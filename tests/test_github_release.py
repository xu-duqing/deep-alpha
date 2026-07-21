import io
import json

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


def test_stream_download(monkeypatch, tmp_path):
    client = GitHubReleaseClient("owner/repo")
    monkeypatch.setattr(client, "_request", lambda url: Response(b"abc"))
    from deep_alpha.github_release import ReleaseAsset
    destination = tmp_path / "data.tar.gz"
    client.download(ReleaseAsset("tag", "data", 3, "https://example.test/data"), destination)
    assert destination.read_bytes() == b"abc"
