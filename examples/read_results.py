"""Read saved results only; no API calls."""

import argparse
import json
from pathlib import Path


def walk_groups(groups):
    for group in groups:
        for item in group.get("extraction_result_list", []):
            yield group.get("extraction_result_label"), item
            yield from walk_groups(item.get("children_extraction_result") or [])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path)
    args = parser.parse_args()
    result = json.loads(args.result.read_text(encoding="utf-8"))
    output = {"paper_id": result["paper_id"], "status": result["status"], "modules": {}}
    for module, body in result.get("science_analysis", {}).items():
        if module == "experiment_process":
            output["modules"][module] = {"object_keys": list(body)}
        else:
            items = list(walk_groups(body.get("extraction_result", [])))
            output["modules"][module] = {
                "items_including_containers": len(items),
                "items_with_value": sum(
                    item.get("result_value") not in (None, "") for _, item in items
                ),
            }
    output["domain_records"] = sum(
        len(group.get("records", [])) + len(group.get("flagged_records", []))
        for group in result.get("core_fields", {}).values()
    )
    output["error_stages"] = [error.get("stage") for error in result.get("errors", [])]
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
