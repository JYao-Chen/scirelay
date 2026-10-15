import argparse
import json
import sys
from pathlib import Path

import requests

from . import DomainExtractor, MinerUClient, ModelConfig, ScienceClient, prepare_document
from .errors import SciRelayError
from .science import MODULES
from .storage import read_json


def main(argv=None):
    parser = argparse.ArgumentParser(description="OCR and scientific literature extraction")
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status", help="Read batch or document status without API calls")
    status.add_argument("input", type=Path, help="Run output directory")
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
    extract = commands.add_parser("extract", help="Run or resume SciEngine modules")
    extract.add_argument("input", type=Path)
    extract.add_argument("--output", required=True, type=Path)
    extract.add_argument("--modules", nargs="+", choices=list(MODULES))
    extract.add_argument("--workers", type=int, default=3)
    extract.add_argument("--timeout", type=float, default=600)
    batch = commands.add_parser("batch", help="Extract prepared documents with a shared task queue")
    batch.add_argument("input", type=Path, help="JSON mapping paper IDs to prepared directories")
    batch.add_argument("--output", required=True, type=Path)
    batch.add_argument("--modules", nargs="+", choices=list(MODULES))
    batch.add_argument("--workers", type=int, default=10)
    batch.add_argument("--timeout", type=float, default=600)
    for command in (extract, batch):
        command.add_argument("--optional-modules", nargs="+", choices=list(MODULES))
        command.add_argument("--defer-modules", nargs="+", choices=list(MODULES))
        command.add_argument(
            "--module-timeout", action="append", default=[], metavar="MODULE=SECONDS"
        )
        command.add_argument("--module-limit", action="append", default=[], metavar="MODULE=COUNT")
        command.add_argument("--max-concurrency", type=int, default=10)
        command.add_argument("--slow-after", type=float, default=120)
        command.add_argument("--failure-threshold", type=int, default=2)
        command.add_argument("--blocked-action", choices=["throttle", "defer"], default="throttle")
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
        if args.command == "status":
            path = args.input / "summary.json"
            result = read_json(path if path.exists() else args.input / "extraction_result.json")
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return (
                1 if result.get("status") in {"partial", "failed", "processing", "running"} else 0
            )
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
        elif args.command in {"extract", "batch"}:

            def assignments(values, convert):
                result = {}
                for value in values:
                    name, separator, number = value.partition("=")
                    if not separator or name in result:
                        raise ValueError("Expected unique MODULE=VALUE assignments")
                    result[name] = convert(number)
                return result

            client = ScienceClient(
                ModelConfig.from_env(),
                timeout=args.timeout,
                module_timeouts=assignments(args.module_timeout, float),
                module_limits=assignments(args.module_limit, int),
                max_concurrency=args.max_concurrency,
                slow_after=args.slow_after,
                failure_threshold=args.failure_threshold,
                blocked_action=args.blocked_action,
            )
            options = dict(
                modules=args.modules,
                workers=args.workers,
                optional_modules=args.optional_modules,
                deferred_modules=args.defer_modules,
            )
            if args.command == "batch":
                documents = read_json(args.input)
                if not isinstance(documents, dict) or any(
                    not isinstance(v, str) for v in documents.values()
                ):
                    raise ValueError("Batch manifest must be a JSON object")
                # Relative paths are relative to the manifest, not the shell directory.
                documents = {k: args.input.parent / v for k, v in documents.items()}
                result = client.run_batch(documents, args.output, **options)
            else:
                result = client.run(args.input, args.output, **options)
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
    except (SciRelayError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
