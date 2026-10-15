"""Prepared documents -> fair queue with automatic slow-module isolation.

Manifest paths are relative to the manifest. No OCR or domain model is called.
"""

import argparse
from pathlib import Path

from scienceengine import ModelConfig, ScienceClient
from scienceengine.storage import read_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--fast-first", action="store_true", help="Defer unsent tasks of blocked modules"
    )
    parser.add_argument("--slow-after", type=float, default=120)
    args = parser.parse_args()
    documents = {
        key: args.manifest.parent / value for key, value in read_json(args.manifest).items()
    }
    client = ScienceClient(
        ModelConfig.from_env(),
        max_concurrency=10,
        slow_after=args.slow_after,
        blocked_action="defer" if args.fast_first else "throttle",
    )
    result = client.run_batch(documents, args.output, workers=10)
    print(result["status"])
    return 0 if result["required_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
