"""Standalone single-module example, using the installed package implementation."""

import argparse
import json
from pathlib import Path

from scienceengine import ModelConfig, ScienceClient
from scienceengine.science import MODULES


def main():
    endpoints = {spec[0]: module for module, spec in MODULES.items()}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", choices=endpoints, required=True)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()
    result = ScienceClient(ModelConfig.from_env(), timeout=args.timeout).extract(
        endpoints[args.module], args.file
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(args.output)


if __name__ == "__main__":
    main()
