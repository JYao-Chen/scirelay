import json
from unittest.mock import Mock

import pytest

from scirelay import DomainExtractor, ModelConfig, SciRelayError
from scirelay.domain import bind_records, validate_stage1


@pytest.fixture
def data(tmp_path):
    blocks = [
        {
            "paragraph_idx": 0,
            "pagenum": 2,
            "head": "Results",
            "paragraph": "Sample A has value 5 units.",
        }
    ]
    path = tmp_path / "blocks.json"
    path.write_text(json.dumps({"title": "Example", "paragraphs": blocks}))
    domain = {
        "domain_id": "example",
        "version": "1",
        "scope": "test",
        "categories": {
            "measurement": {"fields": {"value": {"label": "Value"}}},
            "empty": {"fields": {"other": {}}},
        },
    }
    stage1 = {
        "paper_metadata": {},
        "screening": {"relevance": "in_scope"},
        "sample_registry": [],
        "knowledge_enrichment_zones": [
            {"category_en": "measurement", "block_ids": [0]},
            {"category_en": "empty", "block_ids": []},
        ],
    }
    record = {
        "field": "value",
        "sample_label": "Sample A",
        "value": "5",
        "unit": "units",
        "conditions": "",
        "source_block_ids": [0],
        "evidence": "Sample A has value 5 units.",
        "derived": False,
    }
    return path, domain, stage1, record, blocks


def extractor():
    return DomainExtractor(ModelConfig("test", "https://example.invalid/v1", "test-key"))


def test_two_stages_receive_only_selected_blocks_and_resume(data, tmp_path, monkeypatch):
    path, domain, stage1, record, blocks = data
    client = extractor()
    calls = []

    def generate(system, payload, tokens, output):
        calls.append(payload)
        if "article" in payload:
            return stage1
        assert payload["blocks"] == blocks
        assert list(payload["fields"]) == ["value"]
        return {"records": [record], "unavailable_fields": [], "limitations": []}

    monkeypatch.setattr(client, "_generate", generate)
    out = tmp_path / "run"
    result = client.run(path, domain, out)
    assert result["status"] == "complete" and len(calls) == 2
    assert result["core_fields"]["measurement"]["records"][0]["source_locations"][0]["page"] == 3
    assert result["core_fields"]["empty"]["records"] == []
    client.run(path, domain, out)
    assert len(calls) == 2
    domain["version"] = "2"
    with pytest.raises(SciRelayError, match="changed"):
        client.run(path, domain, out)


def test_invalid_stage1_recorded_as_failure(data, tmp_path, monkeypatch):
    path, domain, first, _, _ = data
    first["knowledge_enrichment_zones"][0]["block_ids"] = [999]
    client = extractor()
    monkeypatch.setattr(client, "_generate", lambda *args: first)
    result = client.run(path, domain, tmp_path / "run")
    assert result["status"] == "failed"
    assert result["errors"][0]["stage"] == "stage1"
    assert not (tmp_path / "run/stages/stage1.json").exists()


def test_out_of_scope_skips_second_stage(data, tmp_path, monkeypatch):
    path, domain, first, _, _ = data
    first["screening"]["relevance"] = "out_of_scope"
    client = extractor()
    generate = Mock(return_value=first)
    monkeypatch.setattr(client, "_generate", generate)
    assert client.run(path, domain, tmp_path / "run")["status"] == "skipped"
    assert generate.call_count == 1


def test_bad_quote_and_unknown_source_kept_separately(data):
    _, domain, _, record, blocks = data
    record.update(evidence="invented value 99", source_block_ids=[999])
    result = bind_records(
        {"records": [record], "unavailable_fields": [], "limitations": []},
        domain["categories"]["measurement"]["fields"],
        blocks,
    )
    assert result["records"] == []
    assert set(result["flagged_records"][0]["evidence_check"]["issues"]) == {
        "invalid_source_block",
        "quote_not_found",
    }


def test_duplicate_categories_fail(data):
    _, domain, first, _, blocks = data
    first["knowledge_enrichment_zones"][1] = first["knowledge_enrichment_zones"][0]
    with pytest.raises(SciRelayError):
        validate_stage1(first, domain["categories"], {0: blocks[0]})


def test_truncated_response_saved_but_not_accepted(tmp_path, monkeypatch):
    body = {"choices": [{"finish_reason": "length", "message": {"content": "{"}}]}
    monkeypatch.setattr(
        "scirelay.domain.requests.post", lambda *a, **kw: Mock(ok=True, json=lambda: body)
    )
    raw = tmp_path / "raw.json"
    with pytest.raises(SciRelayError, match="truncated"):
        extractor()._generate("prompt", {}, 10, raw)
    assert json.loads(raw.read_text()) == body


def test_empty_model_content_is_a_service_error(tmp_path, monkeypatch):
    body = {"choices": [{"finish_reason": "stop", "message": {"content": None}}]}
    monkeypatch.setattr(
        "scirelay.domain.requests.post", lambda *a, **kw: Mock(ok=True, json=lambda: body)
    )
    with pytest.raises(SciRelayError, match="no text"):
        extractor()._generate("prompt", {}, 10, tmp_path / "raw.json")


def test_failed_category_resumes_without_rerunning_stage1(data, tmp_path, monkeypatch):
    path, domain, first, record, _ = data
    client = extractor()

    def fail_category(system, payload, tokens, output):
        if "article" in payload:
            return first
        raise SciRelayError("temporary model failure")

    monkeypatch.setattr(client, "_generate", fail_category)
    out = tmp_path / "run"
    assert client.run(path, domain, out)["status"] == "partial"
    calls = []

    def recovered(system, payload, tokens, output):
        assert "article" not in payload
        calls.append(payload["category"])
        return {"records": [record], "unavailable_fields": [], "limitations": []}

    monkeypatch.setattr(client, "_generate", recovered)
    assert client.run(path, domain, out)["status"] == "complete"
    assert calls == ["measurement"]
