"""文件名清洗。

用户文件名会成为对象键的一部分，必须挡住路径穿越。
对象键本身还有 org_id/asset_id 前缀兜底，但不能因此就不清洗——
纵深防御的意思是每一层都要自己站得住。
"""

from __future__ import annotations

import pytest

from apps.api.modules.asset.mime import asset_type_for, sanitize_filename


@pytest.mark.parametrize(
    "raw",
    [
        "../../../etc/passwd",
        "..\\..\\windows\\system32\\cmd.exe",
        "/absolute/path/file.png",
        "....//....//x.png",
    ],
)
def test_path_traversal_is_stripped(raw: str) -> None:
    out = sanitize_filename(raw)
    assert "/" not in out
    assert "\\" not in out
    assert not out.startswith(".")


def test_keeps_readable_name() -> None:
    assert sanitize_filename("shot_024.png") == "shot_024.png"


def test_chinese_filename_survives() -> None:
    # 中文产品，中文文件名是常态，不能被清成一串下划线
    out = sanitize_filename("第01集-分镜.png")
    assert "第01集" in out
    assert out.endswith(".png")


def test_spaces_and_specials_replaced() -> None:
    out = sanitize_filename("my file (1)*.png")
    assert " " not in out
    assert "*" not in out
    assert out.endswith(".png")


def test_empty_name_gets_fallback() -> None:
    assert sanitize_filename("...") == "file"
    assert sanitize_filename("/") == "file"


def test_long_name_is_truncated_keeping_extension() -> None:
    out = sanitize_filename("x" * 400 + ".png")
    assert len(out.encode()) <= 120
    assert out.endswith(".png")


def test_mime_allowlist() -> None:
    assert asset_type_for("image/png") == "image"
    assert asset_type_for("video/mp4") == "video"
    assert asset_type_for("IMAGE/PNG") == "image"
    assert asset_type_for("image/png; charset=binary") == "image"
    # 不在白名单里的一律拒绝
    assert asset_type_for("application/x-sh") is None
    assert asset_type_for("text/html") is None
    assert asset_type_for("application/octet-stream") is None
