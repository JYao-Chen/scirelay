"""Batch example: OCR -> prepare -> five modules -> optional domain extraction.

Run with: uv run --env-file .env python examples/pipeline.py PDF_DIR --output runs/demo
This performs real API calls; use only documents you intend to submit.
"""

import argparse
import json
from pathlib import Path

from scirelay import (
    MODULES,
    DomainExtractor,
    MinerUClient,
    ModelConfig,
    ScienceClient,
    prepare_document,
)
from scirelay.storage import read_json, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--domain", type=Path, help="Optional domain configuration JSON")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--defer-modules", nargs="+", choices=list(MODULES))
    parser.add_argument("--optional-modules", nargs="+", choices=list(MODULES))
    parser.add_argument("--blocked-action", choices=["throttle", "defer"], default="throttle")
    parser.add_argument("--slow-after", type=float, default=120)
    args = parser.parse_args()
    files = (
        sorted(p for p in args.input.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")
        if args.input.is_dir()
        else [args.input]
    )
    model = ModelConfig.from_env()
    ocr = MinerUClient().run(files, args.output / "ocr")
    science = ScienceClient(model, slow_after=args.slow_after, blocked_action=args.blocked_action)
    domain = read_json(args.domain) if args.domain else None
    summary = []
    documents = {}
    entries = {}
    for original_name, item in ocr["files"].items():
        if item["state"] != "done":
            summary.append(
                {"file": original_name, "ocr": item["state"], "error": item.get("error")}
            )
            write_json(args.output / "summary.json", summary)
            continue
        document_id = Path(item["directory"]).name
        root = args.output / "documents" / document_id
        prepared = root / "input"
        # Preparation is stable across retries; do not rewrite input mtimes each time.
        if not (prepared / "blocks.json").exists():
            prepare_document(args.output / "ocr" / item["directory"], prepared)
        documents[document_id] = prepared
        entry = {"file": original_name, "document_id": document_id, "ocr": "done"}
        entries[document_id] = entry
        summary.append(entry)
    if documents:
        options = {
            "optional_modules": args.optional_modules,
            "deferred_modules": args.defer_modules,
        }
        analysis = science.run_batch(
            documents, args.output / "science", workers=args.workers, **options
        )
        for document_id, prepared in documents.items():
            entry = entries[document_id]
            entry["science"] = analysis["papers"][document_id]["status"]
            if domain:
                extracted = DomainExtractor(model).run(
                    prepared / "blocks.json",
                    domain,
                    args.output / "documents" / document_id / "domain",
                )
                entry["domain"] = extracted["status"]
            write_json(args.output / "summary.json", summary)
    write_json(args.output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return (
        1
        if any(
            row.get("ocr") != "done"
            or row.get("science") not in {"complete", "complete_with_gaps"}
            or row.get("domain", "complete") not in {"complete", "skipped"}
            for row in summary
        )
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
