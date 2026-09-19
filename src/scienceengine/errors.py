"""Public errors retain service context without exposing configured credentials."""

import requests


class ScienceEngineError(RuntimeError):
    """A service call, input contract, or saved run could not be completed."""


class PollingTimeout(ScienceEngineError):
    """OCR is still running; resume using the same output directory."""


def error_text(exc: Exception, secrets: tuple[str, ...] = ()) -> str:
    text = str(exc)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text[:1500]


def json_response(response: requests.Response, service: str, secrets=()) -> dict:
    if not response.ok:
        message = error_text(RuntimeError(response.text), secrets)
        raise ScienceEngineError(f"{service}: HTTP {response.status_code}: {message}")
    try:
        body = response.json()
    except ValueError as exc:
        raise ScienceEngineError(f"{service}: response is not JSON") from exc
    if not isinstance(body, dict):
        raise ScienceEngineError(f"{service}: expected a JSON object")
    return body
