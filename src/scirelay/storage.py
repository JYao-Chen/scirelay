"""Small file-based run storage. Use one writer per output directory."""

import json
from pathlib import Path
from typing import Any

from .errors import SciRelayError


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def open_run(output: Path, config: dict) -> dict:
    """Reuse only a run whose explicit inputs and non-secret configuration match."""
    path = output / "run.json"
    if path.exists():
        state = read_json(path)
        if state["config"] != config:
            raise SciRelayError("Run configuration or input changed; choose a new output directory")
        return state
    if output.exists() and any(output.iterdir()):
        raise SciRelayError("Output directory is not empty and has no run.json")
    state = {"config": config, "status": "running"}
    write_json(path, state)
    return state


def file_info(path: Path) -> dict:
    path = path.resolve()
    if not path.is_file():
        raise SciRelayError(f"Input file does not exist: {path}")
    stat = path.stat()
    return {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
