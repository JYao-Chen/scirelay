"""Adapt MinerU content blocks to SciEngine and internal domain input."""

import shutil
from pathlib import Path

from .errors import SciRelayError
from .storage import read_json, write_json


def prepare_document(ocr_dir: str | Path, output: str | Path) -> dict:
    ocr_dir, output = Path(ocr_dir), Path(output)
    candidates = sorted(ocr_dir.glob("*_content_list.json"))
    if len(candidates) != 1 or not (ocr_dir / "full.md").is_file():
        raise SciRelayError("Expected full.md and exactly one *_content_list.json")
    content = read_json(candidates[0])
    if not isinstance(content, list):
        raise SciRelayError("OCR content list must be an array")
    markdown = (ocr_dir / "full.md").read_text(encoding="utf-8")
    title = next(
        (line[2:].strip() for line in markdown.splitlines() if line.startswith("# ")), ocr_dir.name
    )
    paragraphs, service_rows = [], []
    heading = ""
    for index, block in enumerate(content):
        kind = block.get("type")
        if kind == "table":
            text = "\n".join(
                block.get("table_caption", [])
                + [block.get("table_body", "")]
                + block.get("table_footnote", [])
            )
        elif kind == "image":
            text = "\n".join(block.get("image_caption", []) + block.get("image_footnote", []))
        else:
            text = block.get("text", "")
        if block.get("text_level"):
            heading = text
        if not text.strip():
            continue
        row = {
            "paragraph_idx": index,
            "paragraph": text,
            "head": heading,
            "pagenum": block.get("page_idx"),
            "type": {"table": "body_table_div", "image": "body_figure_div"}.get(kind, "body_div"),
        }
        if block.get("text_level"):
            row["text_level"] = block["text_level"]
        paragraphs.append(row)
        service = dict(row)
        if kind == "table":
            service.update(
                paragraph=block.get("table_caption", []),
                html=block.get("table_body", ""),
                table_footnote=block.get("table_footnote", []),
                path=block.get("img_path", ""),
            )
        elif kind == "image":
            service.update(
                paragraph=block.get("image_caption", []),
                img_footnote=block.get("image_footnote", []),
                path=block.get("img_path", ""),
            )
        service_rows.append(service)
    # Only explicit sections are collected; missing metadata is left empty.
    refs = [
        p["paragraph"]
        for p in paragraphs
        if p["head"].strip().lower() in {"references", "bibliography"}
        and p["paragraph"] != p["head"]
    ]
    abstracts = "\n".join(
        p["paragraph"]
        for p in paragraphs
        if p["head"].strip().lower() == "abstract" and p["paragraph"] != p["head"]
    )
    common = {
        "title": title,
        "authors": [],
        "authorship": [],
        "keywords": [],
        "abstracts": abstracts,
        "references": [
            {"ref_id": f"BIBREF{i}", "ref_idx": i + 1, "ref_text": text}
            for i, text in enumerate(refs)
        ],
    }
    output.mkdir(parents=True, exist_ok=True)
    if (ocr_dir / "full.md").resolve() != (output / "full.md").resolve():
        shutil.copy2(ocr_dir / "full.md", output / "full.md")
    if (ocr_dir / "images").is_dir() and (ocr_dir / "images").resolve() != (
        output / "images"
    ).resolve():
        shutil.copytree(ocr_dir / "images", output / "images", dirs_exist_ok=True)
    write_json(output / "article.json", {**common, "paragraphs": service_rows})
    write_json(output / "blocks.json", {**common, "paragraphs": paragraphs})
    return {
        "article": str(output / "article.json"),
        "blocks": str(output / "blocks.json"),
        "markdown": str(output / "full.md"),
    }
