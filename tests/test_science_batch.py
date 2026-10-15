import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from scienceengine import ModelConfig, ScienceClient, ScienceEngineError
from scienceengine.cli import main


def payload(module):
    if module == "experiment_process":
        return {"result": [{"value": "example"}]}
    return {"extraction_result": [{"extraction_result_list": [{"result_value": "example"}]}]}


@pytest.fixture
def client():
    return ScienceClient(ModelConfig("model", "https://example.invalid/v1", "test-secret"))


def document(root, name):
    path = root / name
    path.mkdir()
    (path / "article.json").write_text("{}")
    (path / "full.md").write_text("# Example")
    return path


def test_deferred_resume_and_optional_failure(tmp_path, monkeypatch, client):
    doc = document(tmp_path, "doc")
    calls = []

    def extract(module, file):
        calls.append(module)
        return payload(module)

    monkeypatch.setattr(client, "extract", extract)
    options = {"modules": ["research_question", "related_work"]}
    out = tmp_path / "run"
    first = client.run(
        doc, out, **options, optional_modules=["related_work"], deferred_modules=["related_work"]
    )
    assert first["status"] == "complete_with_gaps"
    assert first["required_complete"] and not first["all_modules_complete"]
    assert calls == ["research_question"]
    assert first["module_status"]["related_work"] == "deferred"
    calls.clear()
    second = client.run(doc, out, **options)
    assert second["status"] == "complete"
    assert calls == ["related_work"]
    assert len(second["attempts"]) == 2
    assert "test-secret" not in json.dumps(second)

    def fail(module, file):
        raise ScienceEngineError("test-secret deadline")

    monkeypatch.setattr(client, "extract", fail)
    failed = client.run(
        doc, tmp_path / "failed", modules=["related_work"], optional_modules=["related_work"]
    )
    assert failed["status"] == "complete_with_gaps" and failed["errors"]
    assert "test-secret" not in json.dumps(failed)


def test_required_deferred_is_partial(tmp_path, client):
    doc = document(tmp_path, "doc")
    result = client.run(
        doc, tmp_path / "run", modules=["related_work"], deferred_modules=["related_work"]
    )
    assert result["status"] == "partial" and not result["required_complete"]


def test_batch_queue_does_not_starve_fast_modules(tmp_path, monkeypatch, client):
    documents = {f"p{i}": document(tmp_path, f"p{i}") for i in range(3)}
    slow_started, fast_done, release_slow = (threading.Event() for _ in range(3))
    counts = {"slow": 0, "peak": 0, "fast": 0}
    lock = threading.Lock()

    def extract(module, file):
        if module == "related_work":
            with lock:
                counts["slow"] += 1
                counts["peak"] = max(counts["peak"], counts["slow"])
            slow_started.set()
            assert release_slow.wait(5)
            with lock:
                counts["slow"] -= 1
        else:
            with lock:
                counts["fast"] += 1
                if counts["fast"] == 3:
                    fast_done.set()
        return payload(module)

    monkeypatch.setattr(client, "extract", extract)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            client.run_batch,
            documents,
            tmp_path / "batch",
            modules=["related_work", "future_outlook"],
            workers=2,
        )
        try:
            assert slow_started.wait(5)
            assert fast_done.wait(5), "slow module starved other papers"
            assert not future.done()
            summary = json.loads((tmp_path / "batch/summary.json").read_text())
            assert summary["status"] == "running"
        finally:
            release_slow.set()
        result = future.result(timeout=5)
    assert result["status"] == "complete" and counts["peak"] == 1
    assert all(
        (tmp_path / "batch/papers" / p / "extraction_result.json").exists() for p in documents
    )


def test_batch_resume_and_invalid_document(tmp_path, monkeypatch, client):
    docs = {"good": document(tmp_path, "good"), "bad": tmp_path / "missing"}
    calls = []
    monkeypatch.setattr(client, "extract", lambda m, f: (calls.append(m), payload(m))[1])
    out = tmp_path / "batch"
    first = client.run_batch(
        docs, out, optional_modules=["related_work"], deferred_modules=["related_work"]
    )
    assert first["status"] == "partial"
    assert first["papers"]["bad"]["status"] == "failed"
    assert first["papers"]["good"]["status"] == "complete_with_gaps"
    calls.clear()
    second = client.run_batch(docs, out)
    assert calls == ["related_work"]
    assert second["papers"]["good"]["status"] == "complete"
    with pytest.raises(ScienceEngineError, match="changed"):
        client.run_batch({"different": docs["good"]}, out)
    with pytest.raises(ValueError, match="safe"):
        client.run_batch({"../escape": docs["good"]}, tmp_path / "unsafe")


def test_per_module_timeout_and_global_budget(tmp_path, monkeypatch):
    doc = document(tmp_path, "doc")
    client = ScienceClient(
        ModelConfig("m", "https://example.invalid", "secret"),
        module_timeouts={"related_work": 19},
        max_concurrency=1,
    )
    active, peak = 0, 0
    lock = threading.Lock()
    observed = {}

    def post(url, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        module = "related_work" if url.endswith("related-work") else "research_question"
        observed[module] = kwargs["timeout"]
        with lock:
            active -= 1
        return Mock(ok=True, json=lambda: payload(module))

    monkeypatch.setattr("scienceengine.science.requests.post", post)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(client.extract, m, doc / "article.json")
            for m in ["related_work", "research_question"] * 3
        ]
        for future in futures:
            future.result()
    assert observed == {"related_work": (20, 19), "research_question": (20, 600)}
    assert peak == 1


def test_corrupt_cache_is_reported_without_submission(tmp_path, monkeypatch, client):
    doc = document(tmp_path, "doc")
    out = tmp_path / "run"
    monkeypatch.setattr(client, "extract", lambda m, f: payload(m))
    client.run(doc, out, modules=["related_work"])
    (out / "stages/related_work.json").write_text("broken")
    post = Mock()
    monkeypatch.setattr(client, "extract", post)
    result = client.run(doc, out, modules=["related_work"])
    assert result["status"] == "partial" and result["errors"]
    assert result["science_analysis"] == {}
    post.assert_not_called()


def test_cli_batch_manifest_and_resume(tmp_path, monkeypatch):
    document(tmp_path, "doc")
    manifest = tmp_path / "documents.json"
    manifest.write_text(json.dumps({"paper": "doc"}))
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    monkeypatch.setattr(ScienceClient, "extract", lambda self, m, f: payload(m))
    args = [
        "batch",
        str(manifest),
        "--output",
        str(tmp_path / "out"),
        "--module-timeout",
        "related_work=30",
        "--module-limit",
        "related_work=1",
    ]
    assert (
        main(args + ["--optional-modules", "related_work", "--defer-modules", "related_work"]) == 0
    )
    assert main(args) == 0
    assert main(args + ["--module-limit", "related_work=0"]) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout": float("nan")},
        {"module_timeouts": {"related_work": 0}},
        {"module_timeouts": {"unknown": 1}},
        {"max_concurrency": 0},
        {"module_limits": {"related_work": 0}},
    ],
)
def test_invalid_settings(client, kwargs):
    with pytest.raises(ValueError):
        ScienceClient(client.config, **kwargs)


def test_status_does_not_need_credentials(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    (tmp_path / "summary.json").write_text(
        json.dumps(
            {
                "status": "complete_with_gaps",
                "all_modules_complete": False,
            }
        )
    )
    assert main(["status", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["all_modules_complete"] is False


def test_shared_http_budget_blocks_overlapping_direct_calls(tmp_path, monkeypatch, client):
    client = ScienceClient(client.config, max_concurrency=1)
    doc = document(tmp_path, "doc")
    entered, release, second_entered = (threading.Event() for _ in range(3))
    count = 0
    lock = threading.Lock()

    def post(url, **kwargs):
        nonlocal count
        with lock:
            count += 1
            index = count
        if index == 1:
            entered.set()
            assert release.wait(5)
        else:
            second_entered.set()
        return Mock(ok=True, json=lambda: payload("research_question"))

    monkeypatch.setattr("scienceengine.science.requests.post", post)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.extract, "research_question", doc / "article.json")
        assert entered.wait(5)
        second = pool.submit(client.extract, "research_question", doc / "article.json")
        try:
            assert not second_entered.wait(0.1)
        finally:
            release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    assert second_entered.is_set()


@pytest.mark.parametrize(
    "slow_module",
    [
        "research_question",
        "related_work",
        "research_idea",
        "experiment_process",
        "future_outlook",
    ],
)
def test_any_slow_module_is_detected_and_deferred(tmp_path, monkeypatch, client, slow_module):
    from time import monotonic, sleep

    client = ScienceClient(client.config, slow_after=0.03, blocked_action="defer")
    docs = {f"p{i}": document(tmp_path, f"p{i}") for i in range(3)}
    release, fast_done = threading.Event(), threading.Event()
    lock = threading.Lock()
    fast_count = 0
    calls = []

    def extract(module, file):
        nonlocal fast_count
        with lock:
            calls.append(module)
        if module == slow_module:
            assert release.wait(5)
        else:
            with lock:
                fast_count += 1
                if fast_count == 12:
                    fast_done.set()
        return payload(module)

    monkeypatch.setattr(client, "extract", extract)
    out = tmp_path / "batch"
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.run_batch, docs, out, workers=2, optional_modules=[slow_module])
        try:
            assert fast_done.wait(5)
            deadline = monotonic() + 3
            while monotonic() < deadline:
                summary = json.loads((out / "summary.json").read_text())
                deferred = sum(
                    p["module_status"].get(slow_module) == "auto_deferred"
                    for p in summary["papers"].values()
                )
                if deferred == 2:
                    break
                sleep(0.01)
            assert deferred == 2
            assert not future.done(), "in-flight work must not be falsely reported cancelled"
        finally:
            release.set()
        result = future.result(timeout=5)
    assert calls.count(slow_module) == 1
    assert result["status"] == "complete_with_gaps"
    health = result["papers"]["p0"]["scheduler"]["modules"][slow_module]
    assert health["state"] == "blocked" and health["reason"] == "long_running"
    calls.clear()
    second = client.run_batch(docs, out, workers=2)
    assert second["status"] == "complete"
    assert calls == [slow_module, slow_module]


def test_repeated_failures_defer_remaining_requests(tmp_path, monkeypatch, client):
    client = ScienceClient(client.config, failure_threshold=2, blocked_action="defer")
    docs = {f"p{i}": document(tmp_path, f"p{i}") for i in range(5)}
    failed_calls = []

    def extract(module, file):
        if module == "research_idea":
            failed_calls.append(file)
            raise ScienceEngineError("upstream unavailable")
        return payload(module)

    monkeypatch.setattr(client, "extract", extract)
    result = client.run_batch(docs, tmp_path / "batch", workers=2)
    assert len(failed_calls) == 2
    assert result["status"] == "partial"
    assert (
        sum(
            p["module_status"]["research_idea"] == "auto_deferred"
            for p in result["papers"].values()
        )
        == 3
    )
    assert all(
        p["module_status"]["future_outlook"] == "complete" for p in result["papers"].values()
    )
    assert (
        result["papers"]["p0"]["scheduler"]["modules"]["research_idea"]["reason"]
        == "repeated_failure"
    )


def test_slow_module_throttles_without_dropping_required_work(tmp_path, monkeypatch, client):
    from time import sleep

    client = ScienceClient(client.config, slow_after=0.01)
    docs = {f"p{i}": document(tmp_path, f"p{i}") for i in range(3)}

    def extract(module, file):
        if module == "experiment_process":
            sleep(0.025)
        return payload(module)

    monkeypatch.setattr(client, "extract", extract)
    result = client.run_batch(docs, tmp_path / "batch", workers=2)
    assert result["status"] == "complete"
    assert all(
        p["module_status"]["experiment_process"] == "complete" for p in result["papers"].values()
    )
    assert (
        result["papers"]["p0"]["scheduler"]["modules"]["experiment_process"]["state"] == "blocked"
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"slow_after": 0},
        {"slow_after": float("nan")},
        {"failure_threshold": 0},
        {"blocked_action": "ignore"},
    ],
)
def test_invalid_adaptive_settings(client, kwargs):
    with pytest.raises(ValueError):
        ScienceClient(client.config, **kwargs)
