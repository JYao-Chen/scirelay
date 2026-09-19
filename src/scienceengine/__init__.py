"""Reusable OCR, document adaptation and literature extraction."""

from .config import ModelConfig
from .document import prepare_document
from .domain import DomainExtractor
from .errors import PollingTimeout, ScienceEngineError
from .ocr import MinerUClient
from .science import MODULES, ScienceClient

__all__ = [
    "ModelConfig",
    "MinerUClient",
    "ScienceClient",
    "DomainExtractor",
    "prepare_document",
    "ScienceEngineError",
    "PollingTimeout",
    "MODULES",
]
