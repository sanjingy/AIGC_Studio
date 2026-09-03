"""字段级 Patch 的路径与载荷校验。

这一层承担了整条写路径**全部的安全责任**——越界、原型污染、深度炸弹
都在这里挡下。所以逐条打靶，而不是只在集成测试里跑一个"正常改一次"：
正常路径过了不代表边角挡住了，而边角挡不住就是任意字段写入。
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from apps.api.modules.content import patching


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str
    name: str
    count: int = 0


class _Block(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    items: list[_Item]


def _block() -> dict:
    return {
        "title": "标题",
        "items": [
            {"ref": "a", "name": "甲", "count": 1},
            {"ref": "b", "name": "乙", "count": 2},
        ],
    }


# ---------------------------------------------------------------- 路径解析


@pytest.mark.parametrize(
    "path",
    [
        "/title",
        "/items/0/name",
        "/items/1/count",
    ],
)
def test_valid_pointers_parse(path: str) -> None:
    assert patching.parse_pointer(path)


@pytest.mark.parametrize(
    ("path", "why"),
    [
        ("", "空路径"),
        ("/", "只有一个空段"),
        ("title", "没有前导斜杠"),
        ("/items//name", "中间有空段"),
        ("/" + "a/" * 20, "层级过深"),
        ("/" + "x" * 600, "路径过长"),
    ],
)
def test_malformed_pointers_are_rejected(path: str, why: str) -> None:
    with pytest.raises(patching.PathError):
        patching.parse_pointer(path)


@pytest.mark.parametrize(
    "path",
    [
        "/__proto__",
        "/items/0/__proto__",
        "/constructor/prototype",
        "/items/0/_private",
        "/prototype",
    ],
)
def test_prototype_pollution_paths_are_rejected(path: str) -> None:
    """后端是 Python 看着无所谓，但这份 JSON 会原样进前端，
    在那边 `obj[key] = v` 就是真的原型污染。"""
    with pytest.raises(patching.PathError):
        patching.parse_pointer(path)


def test_rfc6901_escapes_are_decoded_in_the_right_order() -> None:
    # 先还原 ~0 会把 "~01" 错还原成 "/"
    assert patching.parse_pointer("/a~01b") == ("a~1b",)
    assert patching.parse_pointer("/a~1b") == ("a/b",)


# ---------------------------------------------------------------- 读写与越界


def test_read_walks_to_the_leaf() -> None:
    tokens = patching.parse_pointer("/items/1/name")
    assert patching.read_at(_block(), tokens) == "乙"


def test_write_replaces_in_place() -> None:
    block = _block()
    patching.write_at(block, patching.parse_pointer("/items/0/name"), "改过的")
    assert block["items"][0]["name"] == "改过的"


@pytest.mark.parametrize(
    "path",
    [
        "/items/9/name",  # 下标越界
        "/items/-/name",  # RFC 6901 的追加语义，我们不支持
        "/items/-1/name",  # 负下标
        "/items/01/name",  # 前导零：同一个元素的第二种写法
        "/items/x/name",  # 数组上用字符串键
        "/nope",  # 顶层不存在的键
        "/items/0/nope",  # 对象里不存在的键
        "/title/0",  # 往标量里继续钻
    ],
)
def test_out_of_range_and_new_keys_are_rejected(path: str) -> None:
    """**只替换已存在的路径。** 新建键会被 extra="forbid" 顶回来，
    而"追加"会让撤销从"写回旧值"变成"删掉一个元素"，是另一套语义。"""
    block = _block()
    tokens = patching.parse_pointer(path)
    with pytest.raises(patching.PathError):
        patching.read_at(block, tokens)
    with pytest.raises(patching.PathError):
        patching.write_at(block, tokens, "x")


def test_write_does_not_create_missing_keys() -> None:
    block = _block()
    with pytest.raises(patching.PathError):
        patching.write_at(block, ("items", "0", "brand_new"), "x")
    assert "brand_new" not in block["items"][0]


# ---------------------------------------------------------------- 载荷


@pytest.mark.parametrize("value", ["文本", 1, 1.5, True, None, [], {}, {"a": [1, {"b": "c"}]}])
def test_plain_json_values_pass(value: object) -> None:
    patching.check_value(value)


def test_pollution_inside_the_value_is_rejected() -> None:
    """路径干净、脏东西全在 value 里，是最容易漏掉的一种。"""
    with pytest.raises(patching.PathError):
        patching.check_value({"__proto__": {"isAdmin": True}})
    with pytest.raises(patching.PathError):
        patching.check_value({"ok": [{"constructor": 1}]})


def test_oversized_value_is_rejected() -> None:
    with pytest.raises(patching.PathError):
        patching.check_value("x" * (patching.MAX_VALUE_BYTES + 1))


def test_deeply_nested_value_is_rejected() -> None:
    deep: object = "leaf"
    for _ in range(patching.MAX_VALUE_DEPTH + 2):
        deep = [deep]
    with pytest.raises(patching.PathError):
        patching.check_value(deep)


def test_non_json_value_is_rejected() -> None:
    with pytest.raises(patching.PathError):
        patching.check_value({1, 2, 3})


# ---------------------------------------------------------------- 整块校验


def test_validate_block_normalizes() -> None:
    block = _block()
    block["items"][0]["count"] = "7"  # 字符串数字
    out = patching.validate_block(_Block, block)
    assert out["items"][0]["count"] == 7


def test_validate_block_rejects_unknown_field() -> None:
    block = _block()
    block["items"][0]["nope"] = 1
    with pytest.raises(patching.SchemaError) as exc:
        patching.validate_block(_Block, block)
    assert exc.value.errors


def test_schema_error_carries_locations() -> None:
    block = _block()
    block["items"][1]["count"] = "不是数字"
    with pytest.raises(patching.SchemaError) as exc:
        patching.validate_block(_Block, block)
    assert exc.value.errors[0]["loc"] == "/items/1/count"
