"""MinerU v4 batch upload, polling, download and resumable local results."""

import os
import shutil
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from .errors import PollingTimeout, SciRelayError, json_response
from .storage import file_info, open_run, write_json


class MinerUClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url="https://mineru.net/api/v4",
        timeout=60,
        upload_timeout=600,
    ):
        self.api_key = api_key or os.environ["MINERU_API_KEY"]
        if not self.api_key.strip() or timeout <= 0 or upload_timeout <= 0:
            raise ValueError("Nonempty API key and positive timeouts required")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.upload_timeout = upload_timeout

    @property
    def headers(self):
        return {"Authorization": "Bearer " + self.api_key}

    def _payload(self, response):
        body = json_response(response, "MinerU", (self.api_key,))
        if body.get("code") != 0 or not isinstance(body.get("data"), dict):
            message = str(body.get("msg", "missing data")).replace(self.api_key, "[REDACTED]")
            raise SciRelayError(f"MinerU business error: {message}")
        return body["data"]

    def create_batch(self, files: list[Path], *, model_version="vlm", language="en") -> dict:
        names = [p.name for p in files]
        if not 1 <= len(files) <= 200 or len(set(names)) != len(names):
            raise ValueError("Provide 1–200 PDFs with unique file names per batch")
        if model_version not in {"vlm", "pipeline"}:
            raise ValueError("This client supports vlm or pipeline PDF OCR")
        for path in files:
            if path.suffix.lower() != ".pdf" or not path.is_file() or not path.stat().st_size:
                raise ValueError(f"Not a nonempty PDF file: {path}")
        payload = {
            "files": [
                {"name": p.name, "data_id": f"doc_{i:04d}", "is_ocr": True}
                for i, p in enumerate(files)
            ],
            "model_version": model_version,
            "enable_formula": True,
            "enable_table": True,
            "language": language,
        }
        data = self._payload(
            requests.post(
                self.base_url + "/file-urls/batch",
                headers=self.headers,
                json=payload,
                timeout=(20, self.timeout),
            )
        )
        if not data.get("batch_id") or len(data.get("file_urls", [])) != len(files):
            raise SciRelayError("MinerU returned an incomplete upload URL list")
        return data

    def get_batch(self, batch_id: str, *, timeout=None) -> list[dict]:
        data = self._payload(
            requests.get(
                self.base_url + f"/extract-results/batch/{batch_id}",
                headers=self.headers,
                timeout=(20, self.timeout if timeout is None else timeout),
            )
        )
        if not isinstance(data.get("extract_result"), list):
            raise SciRelayError("MinerU response has no extract_result array")
        return data["extract_result"]

    def upload(self, path: Path, url: str) -> None:
        with path.open("rb") as source:
            response = requests.put(url, data=source, timeout=(20, self.upload_timeout))
        if not response.ok:
            raise SciRelayError(f"MinerU file upload: HTTP {response.status_code}")

    def download(self, url: str, target: Path) -> None:
        """Promote a complete unpacked document only after ZIP and Markdown checks."""
        target.parent.mkdir(parents=True, exist_ok=True)
        archive = target.with_suffix(".download.zip")
        staging = target.with_name(target.name + ".unpacking")
        try:
            with requests.get(url, stream=True, timeout=(20, self.upload_timeout)) as response:
                if not response.ok:
                    raise SciRelayError(f"MinerU result download: HTTP {response.status_code}")
                with archive.open("wb") as f:
                    for chunk in response.iter_content(1024 * 1024):
                        f.write(chunk)
            if staging.exists():
                shutil.rmtree(staging)
            staging.mkdir()
            with zipfile.ZipFile(archive) as z:
                if z.testzip():
                    raise SciRelayError("Invalid OCR ZIP member")
                for member in z.namelist():
                    if not (staging / member).resolve().is_relative_to(staging.resolve()):
                        raise SciRelayError("OCR ZIP member is outside the result directory")
                z.extractall(staging)
            markdown = list(staging.rglob("full.md"))
            if len(markdown) != 1 or not markdown[0].stat().st_size:
                raise SciRelayError("OCR ZIP must contain one nonempty full.md")
            root = markdown[0].parent
            if target.exists():
                raise SciRelayError(f"Unregistered result directory already exists: {target}")
            root.rename(target)
        except zipfile.BadZipFile as exc:
            raise SciRelayError("OCR download is not a complete ZIP") from exc
        finally:
            archive.unlink(missing_ok=True)
            if staging.exists():
                shutil.rmtree(staging)

    def run(
        self,
        files,
        output,
        *,
        model_version="vlm",
        language="en",
        workers=3,
        poll_interval=15,
        max_wait=1800,
    ) -> dict:
        """Resume the same batch via run.json; no hidden paid re-submission on failure."""
        if workers < 1 or poll_interval <= 0 or max_wait <= 0:
            raise ValueError("workers, poll_interval and max_wait must be positive")
        files = sorted(Path(p).resolve() for p in files)
        output = Path(output)
        config = {
            "kind": "mineru",
            "base_url": self.base_url,
            "model_version": model_version,
            "language": language,
            "files": [file_info(p) for p in files],
        }
        state = open_run(output, config)
        if "batch_id" not in state:
            data = self.create_batch(files, model_version=model_version, language=language)
            state.update(
                batch_id=data["batch_id"],
                files={
                    p.name: {
                        "upload_url": url,
                        "uploaded": False,
                        "state": "pending",
                        "downloaded": False,
                        "directory": f"papers/doc_{i:04d}",
                        "original_name": p.name,
                    }
                    for i, (p, url) in enumerate(zip(files, data["file_urls"], strict=True))
                },
            )
            write_json(output / "run.json", state)
        # Duplicate uploads after a crash are possible; successful persisted uploads are skipped.
        with ThreadPoolExecutor(max_workers=workers) as pool:
            jobs = {
                pool.submit(self.upload, p, state["files"][p.name]["upload_url"]): p.name
                for p in files
                if not state["files"][p.name]["uploaded"]
            }
            upload_errors = []
            for future in as_completed(jobs):
                name = jobs[future]
                try:
                    future.result()
                    state["files"][name]["uploaded"] = True
                    state["files"][name].pop("upload_error", None)
                except requests.RequestException:
                    state["files"][name]["upload_error"] = "Network upload failed; resume the run"
                    upload_errors.append(name)
                except SciRelayError as exc:
                    state["files"][name]["upload_error"] = str(exc)
                    upload_errors.append(name)
                write_json(output / "run.json", state)
            if upload_errors:
                raise SciRelayError("Upload failed for: " + ", ".join(upload_errors))
        deadline = time.monotonic() + max_wait
        while True:
            incomplete = [
                r
                for r in state["files"].values()
                if r["state"] not in {"done", "failed"}
                or (r["state"] == "done" and not r["downloaded"])
            ]
            if incomplete:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PollingTimeout(
                        "OCR wait deadline reached; resume the same output directory"
                    )
                rows = self.get_batch(state["batch_id"], timeout=min(self.timeout, remaining))
                for row in rows:
                    if row.get("file_name") in state["files"]:
                        saved = state["files"][row["file_name"]]
                        saved.update(state=row["state"], error=row.get("err_msg", ""))
                        if row.get("full_zip_url"):
                            saved["download_url"] = row["full_zip_url"]
                write_json(output / "run.json", state)
            for row in state["files"].values():
                if row["state"] == "done":
                    target = output / row["directory"]
                    if row["downloaded"]:
                        if not (target / "full.md").is_file():
                            raise SciRelayError(
                                "Saved OCR output is missing; restore it or use a new run"
                            )
                    elif (target / "full.md").is_file():
                        row["downloaded"] = True  # previous download finished before state save
                    else:
                        self.download(row["download_url"], target)
                        row["downloaded"] = True
                    write_json(output / "run.json", state)
            if all(r["state"] in {"done", "failed"} for r in state["files"].values()):
                state["status"] = (
                    "partial"
                    if any(r["state"] == "failed" for r in state["files"].values())
                    else "complete"
                )
                write_json(output / "run.json", state)
                return state
            time.sleep(min(poll_interval, max(0, deadline - time.monotonic())))
