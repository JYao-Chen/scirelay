import json

from scienceengine import prepare_document


def test_table_image_and_stable_block_ids(tmp_path):
    source = tmp_path / "ocr"
    source.mkdir()
    (source / "full.md").write_text("# Example")
    blocks = [
        {"type": "text", "text": "", "page_idx": 0},
        {"type": "text", "text": "Results", "text_level": 1, "page_idx": 0},
        {
            "type": "table",
            "table_caption": ["Table 1"],
            "table_body": "<table><tr><td>5</td></tr></table>",
            "page_idx": 1,
            "img_path": "images/table.png",
        },
        {
            "type": "image",
            "image_caption": ["Figure 1"],
            "img_path": "images/figure.png",
            "page_idx": 1,
        },
    ]
    (source / "test_content_list.json").write_text(json.dumps(blocks))
    out = tmp_path / "prepared"
    prepare_document(source, out)
    service = json.loads((out / "article.json").read_text())
    internal = json.loads((out / "blocks.json").read_text())
    assert [p["paragraph_idx"] for p in internal["paragraphs"]] == [1, 2, 3]
    assert service["paragraphs"][1]["paragraph"] == ["Table 1"]
    assert service["paragraphs"][1]["html"] in internal["paragraphs"][1]["paragraph"]
    assert service["paragraphs"][2]["path"] == "images/figure.png"
    assert internal["paragraphs"][1]["pagenum"] == 1
    assert internal["authors"] == []
