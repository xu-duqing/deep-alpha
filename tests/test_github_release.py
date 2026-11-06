import io
import json
import urllib.request

from deep_alpha.github_release import GitHubReleaseClient


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_get_latest_asset(monkeypatch):
    payload = {"tag_name": "2026-07-20", "assets": [{"name": "qlib_bin.tar.gz", "size": 3, "browser_download_url": "https://example.test/data"}]}
    client = GitHubReleaseClient("owner/repo")
    monkeypatch.setattr(client, "_request", lambda url: Response(json.dumps(payload).encode()))
    asset = client.get_asset(None, "qlib_bin.tar.gz")
    assert asset.release_tag == "2026-07-20"
    assert asset.size == 3


def test_get_assets_uses_one_release_response(monkeypatch):
    payload = {"tag_name": "v2", "assets": [
        {"name": "a", "size": 1, "browser_download_url": "https://e/a"},
        {"name": "b", "size": 2, "browser_download_url": "https://e/b"},
    ]}
    client = GitHubReleaseClient("owner/repo")
    calls = []
    def request(url):
        calls.append(url)
        return Response(json.dumps(payload).encode())
    monkeypatch.setattr(client, "_request", request)
    tag, assets = client.get_assets(None, ("a", "b", "missing"))
    assert tag == "v2"
    assert set(assets) == {"a", "b"}
    assert len(calls) == 1


def test_previous_paired_full_release_is_selected_by_date_not_api_order(monkeypatch):
    full_assets = [{"name": "market"}, {"name": "daily"}]
    payload = [
        {"tag_name": "2026-10-01", "draft": False, "prerelease": False, "assets": full_assets},
        {"tag_name": "2026-10-30", "draft": False, "prerelease": False, "assets": full_assets},
        {"tag_name": "2026-10-31", "draft": False, "prerelease": False, "assets": [{"name": "market"}]},
        {"tag_name": "2026-11-03", "draft": False, "prerelease": False, "assets": full_assets},
    ]
    client = GitHubReleaseClient("owner/repo")
    monkeypatch.setattr(client, "_request", lambda url: Response(json.dumps(payload).encode()))

    assert client.get_previous_paired_full_release_tag("2026-11-02", "market", "daily") == "2026-10-30"


def test_previous_paired_full_release_returns_none_for_non_date_target(monkeypatch):
    client = GitHubReleaseClient("owner/repo")
    monkeypatch.setattr(client, "_request", lambda url: (_ for _ in ()).throw(AssertionError("no request")))
    assert client.get_previous_paired_full_release_tag("latest", "market", "daily") is None


def test_stream_download(monkeypatch, tmp_path):
    client = GitHubReleaseClient("owner/repo")
    requested = []

    def request(url, authenticated=True, range_start=None):
        requested.append((url, range_start))
        return Response(b"abc")

    monkeypatch.setattr(client, "_request", request)
    from deep_alpha.github_release import ReleaseAsset
    destination = tmp_path / "data.tar.gz"
    updates = []
    client.download(
        ReleaseAsset("tag", "data", 3, "https://example.test/data"),
        destination,
        updates.append,
    )
    assert destination.read_bytes() == b"abc"
    assert updates == [3]
    assert requested == [("https://ghfast.top/https://example.test/data", 0)]


def test_download_resumes_after_truncated_chunk(monkeypatch, tmp_path):
    client = GitHubReleaseClient("owner/repo")
    payload = b"a" * 10 + b"b" * 6  # 16 bytes
    calls = []

    def request(url, authenticated=True, range_start=None):
        calls.append(range_start)
        if len(calls) == 1:
            return Response(payload[:10])  # first chunk truncated after 10 bytes
        return Response(payload[range_start:])

    monkeypatch.setattr(client, "_request", request)
    from deep_alpha.github_release import ReleaseAsset
    destination = tmp_path / "data.tar.gz"
    updates = []
    client.download(ReleaseAsset("tag", "data", len(payload), "https://example.test/data"), destination, updates.append)
    assert destination.read_bytes() == payload
    assert calls == [0, 10]
    assert updates[-1] == len(payload)


def test_proxy_download_omits_auth_token(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    client = GitHubReleaseClient("owner/repo")
    sent_headers = []

    class FakeOpener:
        def __init__(self, payload: bytes):
            self.payload = payload

        def __call__(self, request, timeout=0):
            sent_headers.append(dict(request.header_items()))
            return Response(self.payload)

    monkeypatch.setattr(urllib.request, "urlopen", FakeOpener(b"abc"))
    from deep_alpha.github_release import ReleaseAsset
    destination = tmp_path / "data.tar.gz"
    client.download(ReleaseAsset("tag", "data", 3, "https://example.test/data"), destination)
    assert "Authorization" not in sent_headers[0]

    api_headers = client._headers()
    assert api_headers["Authorization"] == "Bearer secret-token"
