"""Assemble chapter Markdown and an offline HTML handbook with embedded SVG."""

import re
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
chapters = sorted(DOCS.glob("[0-9][0-9]-*.md"))
intro = """# ScienceEngine 文献知识抽取模块\n\n完整接口与开发手册 · 更新于 2026-09-19\n\n本手册面向通用 App 开发，介绍外部接口、两阶段领域抽取与前后端集成，单列尚未实现的任务 API 设计。\n\n[TOC]\n\n"""
combined = intro + "\n\n---\n\n".join(p.read_text(encoding="utf-8") for p in chapters)
(DOCS / "开发手册.md").write_text(combined, encoding="utf-8")
body = markdown.markdown(
    combined,
    extensions=["tables", "fenced_code", "toc"],
    extension_configs={"toc": {"toc_depth": 2}},
)


def embed(match):
    path = DOCS / match.group(2)
    if path.suffix == ".svg" and path.exists():
        return (
            '<figure aria-label="'
            + match.group(1)
            + '">'
            + path.read_text()
            + "<figcaption>"
            + match.group(1)
            + "</figcaption></figure>"
        )
    return match.group(0)


body = re.sub(r'<img alt="([^"]*)" src="([^"]*)"\s*/?>', embed, body)
style = """body{font-family:"Noto Sans CJK SC","Microsoft YaHei",sans-serif;background:#f3f5f8;color:#223048;margin:0;line-height:1.85}main{max-width:1120px;margin:30px auto;background:white;padding:50px 65px;box-shadow:0 8px 40px #12244012}h1{font-size:28px;color:#123a60;margin-top:55px;border-bottom:2px solid #dce8f1;padding-bottom:12px}h2{font-size:21px;color:#185986;margin-top:34px}h3{font-size:18px}a{color:#17639a}table{border-collapse:collapse;width:100%;font-size:14px;margin:22px 0}th,td{border:1px solid #dce3eb;padding:9px 12px;text-align:left;overflow-wrap:anywhere}th{background:#ecf3f8}tr:nth-child(even){background:#f9fbfd}pre{background:#142436;color:#e9f2fa;padding:20px;overflow:auto;border-radius:6px;line-height:1.65;font-size:13px}code{font-family:"DejaVu Sans Mono",monospace;font-size:.88em;overflow-wrap:anywhere}.toc{background:#f2f7fc;padding:18px 25px;border-left:4px solid #217aa8}.toc ul{list-style:none;padding-left:20px}figure{margin:28px 0;padding:16px;border:1px solid #dce3eb;overflow:auto}figure svg{max-width:100%;height:auto;display:block;margin:auto}figcaption{text-align:center;color:#61748a;font-size:13px;margin-top:12px}hr{border:0;border-top:1px solid #dce3eb;margin:50px 0}@media(max-width:760px){main{margin:0;padding:20px}table{font-size:12px}th,td{padding:5px}}@media print{body{background:white}main{margin:0;padding:0;box-shadow:none}pre{white-space:pre-wrap;color:black;background:#f5f5f5}h1{break-before:page}figure,tr{break-inside:avoid}a{color:inherit}}"""
page = (
    '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ScienceEngine 接口与开发手册</title><style>'
    + style
    + "</style></head><body><main>"
    + body
    + "</main></body></html>"
)
(DOCS / "开发手册.html").write_text(page, encoding="utf-8")
print(f"Built {len(chapters)} chapters, {len(combined)} Markdown characters")
