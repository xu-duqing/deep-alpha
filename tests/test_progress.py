import io

from deep_alpha.progress import DownloadProgress


class Stream(io.StringIO):
    def isatty(self):
        return False


def test_download_progress_reports_percent_and_size():
    stream = Stream()
    progress = DownloadProgress(1024, stream=stream, width=10)
    progress.update(512)
    progress.update(1024)
    output = stream.getvalue()
    assert "50%" in output
    assert "100%" in output
    assert "512 B/1.0 KiB" in output


def test_non_interactive_progress_is_throttled():
    stream = Stream()
    progress = DownloadProgress(100, stream=stream, width=10)
    for downloaded in range(1, 101):
        progress.update(downloaded)
    assert len(stream.getvalue().splitlines()) == 11
