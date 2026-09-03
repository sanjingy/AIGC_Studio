"""字段级 Patch 的纯函数层：解析路径、读值、写值、校验整块。

**这一层不碰数据库、不碰租户、不知道 HTTP。** 拆出来是因为它承担了
这条写路径上**全部的安全责任**——路径越界、原型污染、深度炸弹都在这里挡下，
而这类规则只有在能被单元测试逐条打靶时才可信。混进 service 里就只能靠
跑一次真实请求来验证，覆盖不到边角。

路径用 RFC 6901 JSON Pointer，**相对于该 role 的整块产出**：

    /characters/2/hair      角色数组第 3 项的发色
    /shots/0/dialogue       分镜第 1 镜的台词

三条硬规则，改动前先读明白为什么：

1. **只替换已存在的路径。** 不新建键、不追加数组元素（不支持 `-`）。
   产出的形状由 `agents/schemas.py` 定死，新建键必然被 `extra="forbid"`
   顶回来；而"追加"看着无害，却让撤销从"写回旧值"变成"删掉一个元素"，
   是另一套语义和另一套并发风险。要加人物就重跑 Agent。
2. **任何以 `_` 开头的键一律拒绝**，路径里和 value 里都拒。
   我方产出里不存在这种键，所以这条不误伤；而它挡掉的是
   `__proto__` / `constructor` / `prototype` 这类污染载荷——
   后端是 Python 看着无所谓，但这份 JSON 会原样进前端，
   在那边 `obj[key] = v` 就是真的原型污染。
3. **校验用产出自己的 Pydantic schema，不另写一套字段规则。**
   另写一套必然与 schema 漂移，且漂移的方向永远是"校验比 schema 松"。
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ValidationError

# 路径与载荷的硬上限。都不是业务阈值（不进 pricing_rules），
# 是拒绝畸形输入的护栏：给一个 4000 层嵌套的 value 会在 json 序列化、
# 前端渲染、日志脱敏三处各炸一次。
MAX_POINTER_CHARS = 512
MAX_POINTER_DEPTH = 12
MAX_VALUE_BYTES = 20_000
MAX_VALUE_DEPTH = 12

# 显式黑名单。第 2 条规则（`_` 开头）已经盖住了 `__proto__`，
# 但 `constructor` / `prototype` 不以下划线开头，必须单列。
FORBIDDEN_TOKENS = frozenset({"__proto__", "constructor", "prototype"})


class PathError(ValueError):
    """路径本身非法，或指向的位置不存在。"""


def parse_pointer(path: str) -> tuple[str, ...]:
    """把 JSON Pointer 拆成 token 序列，顺带做全部静态校验。

    空路径（指向整块产出）被拒：整块替换不是"字段级编辑"，
    它绕过了逐字段记账，撤销时也无从判断"这一条还是不是我改的那一版"。
    """
    if not isinstance(path, str) or not path:
        raise PathError("路径不能为空")
    if len(path) > MAX_POINTER_CHARS:
        raise PathError(f"路径过长（上限 {MAX_POINTER_CHARS} 字符）")
    if not path.startswith("/"):
        raise PathError("路径必须以 / 开头，形如 /characters/0/name")

    raw = path.split("/")[1:]
    if not raw:
        raise PathError("路径不能指向整块产出，请指到具体字段")
    if len(raw) > MAX_POINTER_DEPTH:
        raise PathError(f"路径层级过深（上限 {MAX_POINTER_DEPTH} 层）")

    tokens: list[str] = []
    for token in raw:
        # RFC 6901 转义：~1 是 /，~0 是 ~。顺序不能反——
        # 先还原 ~0 会把 "~01" 错还原成 "/"。
        decoded = token.replace("~1", "/").replace("~0", "~")
        _check_token(decoded)
        tokens.append(decoded)
    return tuple(tokens)


def _check_token(token: str) -> None:
    if not token:
        raise PathError("路径里有空的一段")
    if token.startswith("_"):
        raise PathError(f"不允许访问私有字段 {token!r}")
    if token.lower() in FORBIDDEN_TOKENS:
        raise PathError(f"不允许访问 {token!r}")


def check_value(value: Any, *, depth: int = 0) -> None:
    """校验待写入的值：类型、体积、嵌套深度，以及内部的键名。

    键名检查要**递归**做：`{"path": "/characters/0", "value": {"__proto__": …}}`
    这种载荷路径本身是干净的，脏东西全在 value 里。
    """
    if depth == 0:
        try:
            size = len(json.dumps(value, ensure_ascii=False).encode())
        except (TypeError, ValueError) as exc:
            raise PathError("值必须是可 JSON 序列化的内容") from exc
        if size > MAX_VALUE_BYTES:
            raise PathError(f"值过大（上限 {MAX_VALUE_BYTES} 字节）")

    if depth > MAX_VALUE_DEPTH:
        raise PathError(f"值的嵌套过深（上限 {MAX_VALUE_DEPTH} 层）")

    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise PathError("对象的键必须是字符串")
            _check_token(key)
            check_value(item, depth=depth + 1)
    elif isinstance(value, list):
        for item in value:
            check_value(item, depth=depth + 1)
    elif not isinstance(value, str | int | float | bool | type(None)):
        raise PathError(f"不支持的值类型：{type(value).__name__}")


def _step(container: Any, token: str, *, where: str) -> Any:
    """按一个 token 往下走一层。走不动就抛，绝不静默创建。"""
    if isinstance(container, dict):
        if token not in container:
            raise PathError(f"{where} 处不存在字段 {token!r}")
        return container[token]
    if isinstance(container, list):
        # 只认纯十进制。`-`（RFC 6901 的"追加"）、`+1`、`01` 一律拒绝：
        # 前者是我们不支持的语义，后两者会让同一个元素有多个路径写法，
        # 而"同一批不能改同一个字段两次"这条检查是按路径字符串去重的。
        if not token.isdigit() or (len(token) > 1 and token.startswith("0")):
            raise PathError(f"{where} 是数组，下标必须是十进制整数，收到 {token!r}")
        index = int(token)
        if index >= len(container):
            raise PathError(f"{where} 的下标 {index} 越界（长度 {len(container)}）")
        return container[index]
    raise PathError(f"{where} 是标量，无法继续往下取 {token!r}")


def read_at(block: Any, tokens: tuple[str, ...]) -> Any:
    cursor: Any = block
    walked = ""
    for token in tokens:
        cursor = _step(cursor, token, where=walked or "/")
        walked = f"{walked}/{token}"
    return cursor


def write_at(block: Any, tokens: tuple[str, ...], value: Any) -> None:
    """原地写。调用方负责先 deepcopy——这里改的就是传进来的那个对象。"""
    parent = read_at(block, tokens[:-1]) if len(tokens) > 1 else block
    last = tokens[-1]
    walked = "/" + "/".join(tokens[:-1]) if len(tokens) > 1 else "/"
    # 先按读的规则走一遍：它会把"键不存在""下标越界""父节点是标量"
    # 全部挡掉。挡不住的写入就是在凭空造字段。
    _step(parent, last, where=walked)
    if isinstance(parent, dict):
        parent[last] = value
    else:
        parent[int(last)] = value


class SchemaError(ValueError):
    """整块产出校验不过。带上 pydantic 的原始 errors 给前端定位。"""

    def __init__(self, exc: ValidationError) -> None:
        self.errors = [
            {
                "loc": "/" + "/".join(str(p) for p in err["loc"]),
                "type": err["type"],
                "msg": err["msg"],
            }
            for err in exc.errors()[:20]
        ]
        super().__init__(f"产出校验不通过，共 {len(exc.errors())} 处")


def validate_block(schema: type[BaseModel], block: dict[str, Any]) -> dict[str, Any]:
    """用产出自己的 schema 重新校验整块，返回规范化后的字典。

    **整块校验，不是只校验被改的那个字段。** 单字段合法但整块非法是常态：
    改掉一个 `character_refs` 里的 ref、让它指向一个已不存在的角色，
    只看字段是一个合法字符串。

    返回值只用来取"被改字段的规范化结果"（见 service），不整块回写——
    整块回写会把 schema 演进带来的默认值补写进那些用户没碰过的字段，
    而那些改动没有对应的变更记录，撤销时补不回去。
    """
    try:
        return schema.model_validate(block).model_dump(mode="json")
    except ValidationError as exc:
        raise SchemaError(exc) from exc
