import io
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from scirelay import MinerUClient, PollingTimeout


@pytest.fixture
def mineru_server():
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("full.md", "# Example")
        z.writestr("sample_content_list.json", "[]")
    state = {"post": 0, "put": 0, "get": 0, "processing": False, "failed": False, "names": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_json(self, data):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"code": 0, "data": data}).encode())

        def do_POST(self):
            state["post"] += 1
            assert self.headers["Authorization"] == "Bearer test"
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["names"] = [f["name"] for f in request["files"]]
            self.send_json(
                {"batch_id": "batch-test", "file_urls": [base + "/upload"] * len(state["names"])}
            )

        def do_PUT(self):
            state["put"] += 1
            assert self.headers.get("Authorization") is None
            assert self.rfile.read(int(self.headers["Content-Length"])).startswith(b"%PDF")
            self.send_response(200)
            self.end_headers()

        def do_GET(self):
            state["get"] += 1
            if self.path == "/archive":
                assert self.headers.get("Authorization") is None
                self.send_response(200)
                self.end_headers()
                self.wfile.write(archive.getvalue())
            else:
                status = (
                    "running" if state["processing"] else "failed" if state["failed"] else "done"
                )
                self.send_json(
                    {
                        "extract_result": [
                            {
                                "file_name": name,
                                "state": status,
                                "full_zip_url": base + "/archive",
                                "err_msg": "test failure" if state["failed"] else "",
                            }
                            for name in state["names"]
                        ]
                    }
                )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    base = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield base, state
    server.shutdown()
    server.server_close()
    thread.join()


def test_real_http_batch_upload_download_and_resume(tmp_path, mineru_server):
    base, calls = mineru_server
    pdf = tmp_path / "document.pdf"
    pdf.write_bytes(b"%PDF test")
    output = tmp_path / "ocr"
    client = MinerUClient("test", base_url=base + "/api/v4")
    result = client.run([pdf], output, poll_interval=0.01, max_wait=2)
    assert result["status"] == "complete"
    assert (output / "papers/doc_0000/full.md").read_text() == "# Example"
    assert calls["post"] == 1 and calls["put"] == 1
    client.run([pdf], output)
    assert calls["post"] == 1 and calls["put"] == 1
    assert "api_key" not in (output / "run.json").read_text()


def test_poll_timeout_resume_same_batch(tmp_path, mineru_server):
    base, calls = mineru_server
    calls["processing"] = True
    pdf = tmp_path / "document.pdf"
    pdf.write_bytes(b"%PDF test")
    client = MinerUClient("test", base_url=base)
    out = tmp_path / "run"
    with pytest.raises(PollingTimeout):
        client.run([pdf], out, poll_interval=0.01, max_wait=0.03)
    calls["processing"] = False
    assert client.run([pdf], out, poll_interval=0.01)["status"] == "complete"
    assert calls["post"] == 1 and calls["put"] == 1


def test_failed_ocr_not_implicitly_resubmitted(tmp_path, mineru_server):
    base, calls = mineru_server
    calls["failed"] = True
    pdf = tmp_path / "document.pdf"
    pdf.write_bytes(b"%PDF test")
    client = MinerUClient("test", base_url=base)
    out = tmp_path / "run"
    assert client.run([pdf], out)["status"] == "partial"
    client.run([pdf], out)
    assert calls["post"] == 1


def test_duplicate_filenames_rejected_before_submission(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    for directory in [a, b]:
        (directory / "same.pdf").write_bytes(b"%PDF")
    with pytest.raises(ValueError, match="unique file names"):
        MinerUClient("test").create_batch([a / "same.pdf", b / "same.pdf"])


def test_non_zip_download_not_promoted(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from scirelay import SciRelayError

    response = MagicMock(ok=True)
    response.__enter__.return_value = response
    response.iter_content.return_value = [b"<html>not a ZIP</html>"]
    monkeypatch.setattr("scirelay.ocr.requests.get", lambda *a, **kw: response)
    target = tmp_path / "document"
    with pytest.raises(SciRelayError, match="ZIP"):
        MinerUClient("test").download("https://example.invalid", target)
    assert not target.exists()
    assert not list(tmp_path.glob("*.zip"))
