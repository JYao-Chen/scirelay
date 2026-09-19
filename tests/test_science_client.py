from unittest.mock import Mock

import pytest

from scienceengine import ModelConfig, ScienceClient, ScienceEngineError
from scienceengine.science import MODULES, validate_payload


@pytest.fixture
def config():
    return ModelConfig("test-model", "https://model.example.invalid/v1", "secret-test-value")


def payload(module):
    if module == "experiment_process":
        return {"protocol_object": {"protocol_name": "Example"}}
    return {"extraction_result": [{"extraction_result_list": [{"result_value": "Example"}]}]}


@pytest.mark.parametrize("module", MODULES)
def test_file_routing_and_form(tmp_path, monkeypatch, config, module):
    endpoint, name, mime = MODULES[module]
    source = tmp_path / name
    source.write_text("{}" if name.endswith(".json") else "# Example")

    def post(url, **kwargs):
        assert url.endswith("/" + endpoint)
        assert kwargs["files"]["file"][2] == mime
        assert kwargs["files"]["file"][1].read()
        assert kwargs["files"]["LLM_MODEL"] == (None, "test-model")
        assert kwargs["files"]["Stream"] == (None, "False")
        assert kwargs["timeout"] == (20, 600)
        return Mock(ok=True, json=lambda: payload(module))

    monkeypatch.setattr("scienceengine.science.requests.post", post)
    assert ScienceClient(config).extract(module, source) == payload(module)


def test_future_rejects_json_before_network(tmp_path, monkeypatch, config):
    post = Mock()
    monkeypatch.setattr("scienceengine.science.requests.post", post)
    with pytest.raises(ValueError, match="requires .md"):
        ScienceClient(config).extract("future_outlook", tmp_path / "article.json")
    post.assert_not_called()


@pytest.mark.parametrize(
    "body",
    [
        [],
        None,
        {},
        {"code": 500},
        {"success": False},
        {"error": "failed"},
        {"_extraction_warnings": ["warn"]},
        {"extraction_result": []},
    ],
)
def test_rejects_business_and_structural_errors(body):
    with pytest.raises(ScienceEngineError):
        validate_payload(body, "research_question")


def test_recursive_container_and_experiment():
    assert validate_payload(
        {
            "extraction_result": [
                {
                    "extraction_result_list": [
                        {"result_value": "", "children_extraction_result": [{}]}
                    ]
                }
            ]
        },
        "related_work",
    )
    assert validate_payload({"result": [{"value": "example"}]}, "experiment_process")
    with pytest.raises(ScienceEngineError):
        validate_payload({"result": []}, "experiment_process")


def test_partial_resume_and_config_change(tmp_path, monkeypatch, config):
    document = tmp_path / "document"
    document.mkdir()
    (document / "article.json").write_text("{}")
    (document / "full.md").write_text("# Example")
    client = ScienceClient(config)
    called = []

    def extract(module, file):
        called.append(module)
        if module == "related_work":
            raise ScienceEngineError("deadline exceeded")
        return payload(module)

    monkeypatch.setattr(client, "extract", extract)
    modules = ["research_question", "related_work"]
    out = tmp_path / "run"
    first = client.run(document, out, modules=modules, workers=1)
    assert first["status"] == "partial"
    assert list(first["science_analysis"]) == ["research_question"]
    called.clear()
    monkeypatch.setattr(
        client, "extract", lambda module, file: (called.append(module), payload(module))[1]
    )
    second = client.run(document, out, modules=modules)
    assert second["status"] == "complete" and second["errors"] == []
    assert called == ["related_work"]
    assert config.api_key not in (out / "run.json").read_text()
    (document / "article.json").write_text('{"changed":true}')
    with pytest.raises(ScienceEngineError, match="changed"):
        client.run(document, out, modules=modules)


def test_secret_redaction(tmp_path, monkeypatch, config):
    file = tmp_path / "full.md"
    file.write_text("# Test")
    response = Mock(ok=False, status_code=500, text="echo " + config.api_key)
    monkeypatch.setattr("scienceengine.science.requests.post", lambda *a, **kw: response)
    with pytest.raises(ScienceEngineError) as error:
        ScienceClient(config).extract("future_outlook", file)
    assert config.api_key not in str(error.value)
    assert config.api_key not in repr(config)
    assert "[REDACTED]" in str(error.value)
