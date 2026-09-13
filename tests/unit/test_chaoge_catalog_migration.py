"""The three original surfaces remain verbatim and do not overwrite old keys."""

import ast
import hashlib
from pathlib import Path


def test_source_style_descriptions_are_verbatim() -> None:
    path = Path(__file__).parents[2] / (
        "migrations/versions/a8c3e91d6402_chaoge_original_style_catalog.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    declaration = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "STYLES"
    )
    rows = ast.literal_eval(declaration.value)
    assert [row["key"] for row in rows] == [f"chaoge_v1_{i:02}" for i in range(1, 9)]
    text = "\n".join(
        row[key] for row in rows for key in ("character_tokens", "scene_tokens", "video_tokens")
    )
    # Independently captured from the user's source, not calculated from migration data.
    assert hashlib.sha256(text.encode()).hexdigest() == (
        "6812384de2647cb40fcb29732e9025e3c7b0d64622240989c273d44fff74d980"
    )
    assert all(row["color_grading"] == row["render_mode"] == "" for row in rows)
