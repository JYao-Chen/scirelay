"""Five-module ScienceEngine client and resumable per-document extraction."""

import math
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from threading import BoundedSemaphore
from time import monotonic

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
    def __init__(
        self,
        config: ModelConfig,
        *,
        base_url="https://sciengine.las.ac.cn",
        timeout=600,
        module_timeouts=None,
        max_concurrency=10,
        module_limits=None,
        slow_after=120,
        failure_threshold=2,
        blocked_action="throttle",
    ):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive")
        self.config = config
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.module_timeouts = dict(module_timeouts or {})
        limits = dict(module_limits or {})
        if set(self.module_timeouts) - MODULES.keys() or set(limits) - MODULES.keys():
            raise ValueError("Unknown module in timeout or concurrency configuration")
        if any(not math.isfinite(v) or v <= 0 for v in self.module_timeouts.values()):
            raise ValueError("Module timeouts must be positive and finite")
        if type(max_concurrency) is not int or max_concurrency < 1:
            raise ValueError("max_concurrency must be a positive integer")
        if any(type(v) is not int or v < 1 for v in limits.values()):
            raise ValueError("Module limits must be positive integers")
        if not math.isfinite(slow_after) or slow_after <= 0:
            raise ValueError("slow_after must be positive and finite")
        if type(failure_threshold) is not int or failure_threshold < 1:
            raise ValueError("failure_threshold must be a positive integer")
        if blocked_action not in {"throttle", "defer"}:
            raise ValueError("blocked_action must be throttle or defer")
        self.slow_after = slow_after
        self.failure_threshold = failure_threshold
        self.blocked_action = blocked_action
        self.max_concurrency = max_concurrency
        self.module_limits = limits
        self._slots = BoundedSemaphore(max_concurrency)
        self._module_slots = {m: BoundedSemaphore(n) for m, n in limits.items()}

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
            # Acquire module capacity before global capacity so a throttled module
            # cannot consume the shared request budget while waiting.
            with ExitStack() as stack:
                if module in self._module_slots:
                    stack.enter_context(self._module_slots[module])
                stack.enter_context(self._slots)
                stream = stack.enter_context(file.open("rb"))
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
                    timeout=(20, self.module_timeouts.get(module, self.timeout)),
                )
            body = json_response(response, "ScienceEngine", (self.config.api_key,))
            return validate_payload(body, module)
        except (requests.RequestException, ScienceEngineError) as exc:
            raise ScienceEngineError(error_text(exc, (self.config.api_key,))) from None

    def _prepare(self, document_dir, output, selected, optional, deferred):
        document_dir, output = Path(document_dir), Path(output)
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
        previous = read_json(result_path) if result_path.exists() else {}
        result = {
            "paper_id": document_dir.name,
            "science_analysis": {},
            "errors": [],
            "status": "processing",
            "provenance": {
                **self.config.public(),
                "service": self.base_url,
            },
            "policy": {"optional_modules": sorted(optional), "deferred_modules": sorted(deferred)},
            "module_status": {},
            "attempts": previous.get("attempts", []),
        }
        pending = []
        for module in selected:
            cache = output / "stages" / f"{module}.json"
            if cache.exists():
                try:
                    result["science_analysis"][module] = validate_payload(read_json(cache), module)
                    result["module_status"][module] = "cached"
                    continue
                except (ScienceEngineError, ValueError, OSError) as exc:
                    # A corrupt cache is not silently accepted or automatically resubmitted.
                    result["module_status"][module] = "failed"
                    result["errors"].append(
                        {"stage": module, "error": error_text(exc, (self.config.api_key,))}
                    )
                    continue
            if module in deferred:
                result["module_status"][module] = "deferred"
            else:
                result["module_status"][module] = "pending"
                pending.append(module)
        state["status"] = "running"
        write_json(output / "run.json", state)
        write_json(result_path, result)
        return {
            "document": document_dir,
            "output": output,
            "state": state,
            "result": result,
            "selected": selected,
            "optional": optional,
            "pending": pending,
        }

    @staticmethod
    def _selection(modules, optional_modules, deferred_modules):
        selected = list(MODULES) if modules is None else list(modules)
        if not selected or len(set(selected)) != len(selected) or set(selected) - MODULES.keys():
            raise ValueError("Select a nonempty unique list of supported module keys")
        optional, deferred = set(optional_modules or ()), set(deferred_modules or ())
        if (optional | deferred) - set(selected):
            raise ValueError("Optional and deferred modules must belong to selected modules")
        return sorted(selected), optional, deferred

    def _attempt(self, module, file):
        started = datetime.now(timezone.utc).isoformat()
        try:
            body = validate_payload(self.extract(module, file), module)
            error = None
        except (ScienceEngineError, ValueError, OSError) as exc:
            body, error = None, error_text(exc, (self.config.api_key,))
        return body, {
            "module": module,
            "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "status": "failed" if error else "complete",
            "error": error,
            "timeout": self.module_timeouts.get(module, self.timeout),
        }

    @staticmethod
    def _finish(context):
        result = context["result"]
        missing = set(context["selected"]) - result["science_analysis"].keys()
        result["missing_modules"] = sorted(missing)
        result["required_complete"] = not (missing - context["optional"])
        result["all_modules_complete"] = not missing
        result["status"] = (
            "complete"
            if not missing
            else "complete_with_gaps"
            if result["required_complete"]
            else "partial"
        )
        write_json(context["output"] / "extraction_result.json", result)
        context["state"]["status"] = result["status"]
        write_json(context["output"] / "run.json", context["state"])
        return result

    def _execute(self, contexts, workers, on_update=None):
        pending = {m: deque(c for c in contexts if m in c["pending"]) for m in MODULES}
        selected = set().union(*(set(c["selected"]) for c in contexts))
        capacity = min(workers, self.max_concurrency)
        # Reserve a fair share for each module rather than assuming one named
        # endpoint is slow. One slow module cannot occupy every worker.
        fair_limit = max(1, math.ceil(capacity / max(1, len(selected))))
        health = {
            m: {"state": "healthy", "consecutive_failures": 0, "reason": None} for m in selected
        }
        scheduler = {
            "slow_after": self.slow_after,
            "failure_threshold": self.failure_threshold,
            "blocked_action": self.blocked_action,
            "fair_module_limit": fair_limit,
            "modules": health,
        }
        for context in contexts:
            context["result"]["scheduler"] = scheduler
            if not context["pending"]:
                self._finish(context)
        active_counts = {m: 0 for m in MODULES}
        rotation = deque(MODULES)

        def mark_blocked(module, reason):
            if health[module]["state"] == "blocked":
                return
            health[module].update(state="blocked", reason=reason)
            if self.blocked_action == "defer":
                while pending[module]:
                    context = pending[module].popleft()
                    context["result"]["module_status"][module] = "auto_deferred"
            # Persist the observation even if no request has finished yet.
            for context in contexts:
                write_json(context["output"] / "extraction_result.json", context["result"])
            if on_update:
                on_update()

        with ThreadPoolExecutor(max_workers=workers) as pool:
            active = {}
            while any(pending.values()) or active:
                now = monotonic()
                for context, module, submitted in list(active.values()):
                    if now - submitted >= self.slow_after:
                        mark_blocked(module, "long_running")
                # Round robin: one task per module per pass, across all papers.
                while len(active) < capacity:
                    chosen = None
                    for _ in range(len(rotation)):
                        module = rotation[0]
                        rotation.rotate(-1)
                        if not pending[module]:
                            continue
                        limit = min(fair_limit, self.module_limits.get(module, capacity))
                        if health[module]["state"] == "blocked":
                            limit = min(limit, 1)
                        if active_counts[module] < limit:
                            chosen = module
                            break
                    if chosen is None:
                        break
                    module = chosen
                    context = pending[module].popleft()
                    context["result"]["module_status"][module] = "running"
                    write_json(context["output"] / "extraction_result.json", context["result"])
                    future = pool.submit(
                        self._attempt, module, context["document"] / MODULES[module][1]
                    )
                    active[future] = (context, module, monotonic())
                    active_counts[module] += 1
                if not active:
                    break
                done, _ = wait(
                    active, timeout=min(0.25, self.slow_after), return_when=FIRST_COMPLETED
                )
                for future in done:
                    context, module, submitted = active.pop(future)
                    active_counts[module] -= 1
                    body, attempt = future.result()
                    elapsed = monotonic() - submitted
                    attempt["elapsed_seconds"] = elapsed
                    # Also detect a slow request that finished between scheduler polls.
                    if elapsed >= self.slow_after:
                        mark_blocked(module, "long_running")
                    result = context["result"]
                    result["attempts"].append(attempt)
                    if body is not None:
                        health[module]["consecutive_failures"] = 0
                        write_json(context["output"] / "stages" / f"{module}.json", body)
                        result["science_analysis"][module] = body
                        result["module_status"][module] = "complete"
                    else:
                        health[module]["consecutive_failures"] += 1
                        if health[module]["consecutive_failures"] >= self.failure_threshold:
                            mark_blocked(module, "repeated_failure")
                        result["module_status"][module] = "failed"
                        result["errors"].append({"stage": module, "error": attempt["error"]})
                    write_json(context["output"] / "extraction_result.json", result)
                for context in contexts:
                    result = context["result"]
                    if result["status"] == "processing" and all(
                        v not in {"pending", "running"} for v in result["module_status"].values()
                    ):
                        self._finish(context)
                if done and on_update:
                    on_update()
        return [self._finish(c) for c in contexts]

    def run(
        self,
        document_dir,
        output,
        *,
        modules=None,
        workers=3,
        optional_modules=None,
        deferred_modules=None,
    ):
        """Run one document; resume deferred/failed modules without repeating successes.

        Keep modules fixed for a run. Optional/deferred policy and timeouts may change
        on resume; model, service and input identity may not.
        """
        if type(workers) is not int or workers < 1:
            raise ValueError("workers must be a positive integer")
        selected, optional, deferred = self._selection(modules, optional_modules, deferred_modules)
        context = self._prepare(document_dir, output, selected, optional, deferred)
        return self._execute([context], workers)[0]

    def run_batch(
        self,
        documents,
        output,
        *,
        modules=None,
        workers=10,
        optional_modules=None,
        deferred_modules=None,
    ):
        """Extract a mapping of stable paper IDs to prepared document directories.

        One coordinator owns output writes; one bounded executor handles all papers.
        Individual invalid inputs are recorded without blocking valid documents.
        """
        if type(workers) is not int or workers < 1:
            raise ValueError("workers must be a positive integer")
        selected, optional, deferred = self._selection(modules, optional_modules, deferred_modules)
        if not isinstance(documents, dict) or not documents:
            raise ValueError("documents must be a nonempty mapping of paper IDs to directories")
        if any(
            not isinstance(k, str)
            or not k
            or k in {".", ".."}
            or any(
                ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_- ."
                for ch in k
            )
            or k.endswith((" ", "."))
            for k in documents
        ):
            raise ValueError("Paper IDs must be safe single directory names")
        if any(not isinstance(v, (str, Path)) for v in documents.values()):
            raise ValueError("Document directories must be paths")
        output = Path(output)
        state = open_run(
            output,
            {
                "kind": "science_batch",
                "model": self.config.public(),
                "service": self.base_url,
                "modules": selected,
                "documents": {k: str(Path(v).resolve()) for k, v in documents.items()},
            },
        )
        contexts, failures = [], {}
        for paper_id, document in documents.items():
            try:
                context = self._prepare(
                    document, output / "papers" / paper_id, selected, optional, deferred
                )
                context["result"]["paper_id"] = paper_id
                contexts.append(context)
            except (ScienceEngineError, ValueError, OSError) as exc:
                failures[paper_id] = {
                    "status": "failed",
                    "error": error_text(exc, (self.config.api_key,)),
                }

        def snapshot(final=False):
            papers = dict(failures)
            for c in contexts:
                r = c["result"]
                missing = set(selected) - r["science_analysis"].keys()
                papers[r["paper_id"]] = {
                    "status": r["status"],
                    "required_complete": not (missing - optional),
                    "all_modules_complete": not missing,
                    "missing_modules": sorted(missing),
                    "module_status": dict(r["module_status"]),
                    "scheduler": r.get("scheduler", {}),
                    "errors": list(r["errors"]),
                }
            required = not failures and all(p["required_complete"] for p in papers.values())
            complete = not failures and all(p["all_modules_complete"] for p in papers.values())
            summary = {
                "status": "running"
                if not final
                else "complete"
                if complete
                else "complete_with_gaps"
                if required
                else "partial",
                "papers": papers,
                "papers_total": len(documents),
                "required_complete": required,
                "all_modules_complete": complete,
            }
            write_json(output / "summary.json", summary)
            return summary

        snapshot()
        self._execute(contexts, workers, snapshot)
        result = snapshot(final=True)
        state["status"] = result["status"]
        write_json(output / "run.json", state)
        return result
