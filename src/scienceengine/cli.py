import argparse
import json
import sys
from pathlib import Path

import requests

from . import DomainExtractor, MinerUClient, ModelConfig, ScienceClient, prepare_document
from .errors import ScienceEngineError
from .science import MODULES
from .storage import read_json


def main(argv=None):
    parser = argparse.ArgumentParser(description="OCR and scientific literature extraction")
    commands = parser.add_subparsers(dest="command", required=True)
    ocr = commands.add_parser("ocr", help="Create or resume a MinerU PDF batch")
    ocr.add_argument("input", type=Path)
    ocr.add_argument("--output", required=True, type=Path)
    ocr.add_argument("--model-version", choices=["vlm", "pipeline"], default="vlm")
    ocr.add_argument("--language", default="en")
    ocr.add_argument("--workers", type=int, default=3)
    ocr.add_argument("--poll-interval", type=float, default=15)
    ocr.add_argument("--max-wait", type=float, default=1800)
    prep = commands.add_parser("prepare", help="Adapt one OCR document")
    prep.add_argument("input", type=Path)
    prep.add_argument("--output", required=True, type=Path)
    extract = commands.add_parser("extract", help="Run or resume ScienceEngine modules")
    extract.add_argument("input", type=Path)
    extract.add_argument("--output", required=True, type=Path)
    extract.add_argument("--modules", nargs="+", choices=list(MODULES))
    extract.add_argument("--workers", type=int, default=3)
    extract.add_argument("--timeout", type=float, default=600)
    domain = commands.add_parser("domain", help="Run or resume two-stage domain extraction")
    domain.add_argument("input", type=Path, help="Internal blocks.json")
    domain.add_argument("--config", type=Path, required=True)
    domain.add_argument("--output", type=Path, required=True)
    domain.add_argument("--workers", type=int, default=3)
    domain.add_argument("--timeout", type=float, default=600)
    domain.add_argument("--stage1-tokens", type=int, default=7000)
    domain.add_argument("--stage2-tokens", type=int, default=8192)
    args = parser.parse_args(argv)
    try:
        if args.command == "ocr":
            files = (
                sorted(
                    p for p in args.input.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"
                )
                if args.input.is_dir()
                else [args.input]
            )
            result = MinerUClient().run(
                files,
                args.output,
                model_version=args.model_version,
                language=args.language,
                workers=args.workers,
                poll_interval=args.poll_interval,
                max_wait=args.max_wait,
            )
        elif args.command == "prepare":
            result = prepare_document(args.input, args.output)
        elif args.command == "extract":
            result = ScienceClient(ModelConfig.from_env(), timeout=args.timeout).run(
                args.input, args.output, modules=args.modules, workers=args.workers
            )
        else:
            result = DomainExtractor(
                ModelConfig.from_env(),
                timeout=args.timeout,
                stage1_tokens=args.stage1_tokens,
                stage2_tokens=args.stage2_tokens,
            ).run(args.input, read_json(args.config), args.output, workers=args.workers)
        status = result.get("status", "complete")
        print(json.dumps({"status": status, "output": str(args.output)}, ensure_ascii=False))
        return 1 if status in {"partial", "failed"} else 0
    except requests.RequestException as exc:
        # Signed URLs in requests exception messages are intentionally not printed.
        print(
            f"Network failure ({type(exc).__name__}); inspect run.json and resume.", file=sys.stderr
        )
        return 1
    except KeyError as exc:
        print(f"Missing configuration or response field: {exc}", file=sys.stderr)
        return 2
    except (ScienceEngineError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
