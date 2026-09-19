"""Batch example: OCR -> prepare -> five modules -> optional domain extraction.

Run with: uv run --env-file .env python examples/pipeline.py PDF_DIR --output runs/demo
This performs real API calls; use only documents you intend to submit.
"""

import argparse
import json
from pathlib import Path

from scienceengine import (
    DomainExtractor,
    MinerUClient,
    ModelConfig,
    ScienceClient,
    prepare_document,
)
from scienceengine.storage import read_json, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--domain", type=Path, help="Optional domain configuration JSON")
    args = parser.parse_args()
    files = (
        sorted(p for p in args.input.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")
        if args.input.is_dir()
        else [args.input]
    )
    model = ModelConfig.from_env()
    ocr = MinerUClient().run(files, args.output / "ocr")
    science = ScienceClient(model)
    domain = read_json(args.domain) if args.domain else None
    summary = []
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
        result = science.run(prepared, root / "science")
        entry = {
            "file": original_name,
            "document_id": document_id,
            "ocr": "done",
            "science": result["status"],
        }
        if domain:
            extracted = DomainExtractor(model).run(
                prepared / "blocks.json", domain, root / "domain"
            )
            entry["domain"] = extracted["status"]
        summary.append(entry)
        write_json(args.output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return (
        1
        if any(
            row.get("ocr") != "done"
            or row.get("science") != "complete"
            or row.get("domain", "complete") not in {"complete", "skipped"}
            for row in summary
        )
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
