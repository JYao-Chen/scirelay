"""Five-module ScienceEngine client and resumable per-document extraction."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from .config import ModelConfig
from .errors import ScienceEngineError, error_text, json_response
from .storage import file_info, open_run, read_json, write_json

MODULES = {
    "research_question": ("question", "article.json", "application/json"),
    "related_work": ("related-work", "article.json", "application/json"),
    "research_idea": ("innovation", "article.json", "application/json"),
    "experiment_process": ("experiment", "full.md", "text/markdown"),
    "future_outlook": ("future", "full.md", "text/markdown"),
}
EXPERIMENT_KEYS = (
    "protocol_object",
    "protocol_focus_object",
    "reagent_object",
    "equipment_object",
    "synthesis_operation_object",
    "characterization_methods",
    "evaluation_methods",
    "result",
)


def validate_payload(body: dict, module: str) -> dict:
    if not isinstance(body, dict):
        raise ScienceEngineError("Expected a JSON object")
    if body.get("success") is False or body.get("code") not in (None, 0, 200, "0", "200"):
        raise ScienceEngineError(
            "Upstream business failure: " + str(body.get("msg", body.get("message", "")))
        )
    if body.get("error") and not any(k in body for k in ("data", "result", "content")):
        raise ScienceEngineError("Upstream error: " + str(body["error"]))
    if body.get("_extraction_warnings"):
        raise ScienceEngineError(
            "Upstream extraction warning: " + str(body["_extraction_warnings"])
        )
    if module == "experiment_process":
        if not any(body.get(key) for key in EXPERIMENT_KEYS):
            raise ScienceEngineError("Empty experiment response")
    else:
        groups = body.get("extraction_result")
        if not isinstance(groups, list) or not any(
            isinstance(g, dict)
            and isinstance(g.get("extraction_result_list"), list)
            and g["extraction_result_list"]
            for g in groups
        ):
            raise ScienceEngineError("Missing or empty extraction_result groups")
    return body


class ScienceClient:
    def __init__(self, config: ModelConfig, *, base_url="https://sciengine.las.ac.cn", timeout=600):
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.config = config
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def extract(self, module: str, file: str | Path) -> dict:
        if module not in MODULES:
            raise ValueError(f"Unknown module: {module}; choose from {list(MODULES)}")
        endpoint, expected, mime = MODULES[module]
        file = Path(file)
        if file.suffix.lower() != Path(expected).suffix:
            raise ValueError(f"{module} requires {Path(expected).suffix} content")
        if not file.is_file() or not file.stat().st_size:
            raise ValueError("Input file is missing or empty")
        try:
            with file.open("rb") as stream:
                response = requests.post(
                    self.base_url + "/" + endpoint,
                    files={
                        "LLM_MODEL": (None, self.config.model),
                        "LLM_API_KEY": (None, self.config.api_key),
                        "LLM_BASE_URL": (None, self.config.base_url),
                        "Stream": (None, "False"),
                        "extra_input": (None, ""),
                        "file": (file.name, stream, mime),
                    },
                    timeout=(20, self.timeout),
                )
            body = json_response(response, "ScienceEngine", (self.config.api_key,))
            return validate_payload(body, module)
        except (requests.RequestException, ScienceEngineError) as exc:
            raise ScienceEngineError(error_text(exc, (self.config.api_key,))) from None

    def run(self, document_dir, output, *, modules=None, workers=3) -> dict:
        selected = list(MODULES) if modules is None else list(modules)
        if (
            not selected
            or len(set(selected)) != len(selected)
            or any(m not in MODULES for m in selected)
        ):
            raise ValueError("Select a nonempty unique list of supported module keys")
        if workers < 1:
            raise ValueError("workers must be positive")
        document_dir, output = Path(document_dir), Path(output)
        selected = sorted(selected)
        inputs = {m: file_info(document_dir / MODULES[m][1]) for m in selected}
        state = open_run(
            output,
            {
                "kind": "science",
                "model": self.config.public(),
                "service": self.base_url,
                "modules": selected,
                "inputs": inputs,
            },
        )
        result_path = output / "extraction_result.json"
        result = (
            read_json(result_path)
            if result_path.exists()
            else {
                "paper_id": document_dir.name,
                "science_analysis": {},
                "errors": [],
                "status": "processing",
                "provenance": {**self.config.public(), "service": self.base_url},
            }
        )
        errors = {e["stage"]: e for e in result["errors"]}
        pending = []
        for module in selected:
            cache = output / "stages" / f"{module}.json"
            if cache.exists():
                result["science_analysis"][module] = validate_payload(read_json(cache), module)
                errors.pop(module, None)
            else:
                pending.append(module)
        write_json(result_path, result)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            jobs = {pool.submit(self.extract, m, document_dir / MODULES[m][1]): m for m in pending}
            for future in as_completed(jobs):
                module = jobs[future]
                try:
                    body = future.result()
                    write_json(output / "stages" / f"{module}.json", body)
                    result["science_analysis"][module] = body
                    errors.pop(module, None)
                except (ScienceEngineError, ValueError, OSError) as exc:
                    errors[module] = {
                        "stage": module,
                        "error": error_text(exc, (self.config.api_key,)),
                    }
                result["errors"] = list(errors.values())
                write_json(result_path, result)
        result["errors"] = list(errors.values())
        result["status"] = (
            "complete"
            if not errors and all(m in result["science_analysis"] for m in selected)
            else "partial"
        )
        write_json(result_path, result)
        state["status"] = result["status"]
        write_json(output / "run.json", state)
        return result
