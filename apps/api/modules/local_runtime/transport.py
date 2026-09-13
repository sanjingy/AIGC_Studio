"""Redis 邮箱：请求/结果的**临时关联**，不是第二份执行状态。

这里没有任何"运行中 / 已成功"的工作流状态。执行状态只认 `tasks.status`
（CLAUDE.md 硬规则），本机运行时也不例外——出图照常建 `image.generate` 任务、
照常预扣结算，Redis 只负责把一条请求送到桌面、把结果送回来，键全部带 TTL、
用完即删。

也**没有重投**。连接器崩了就让它超时，用户可以显式重试。自动重投在这条
路径上是有害的：结果不是幂等的，一条请求被跑两遍就是订阅额度被扣两次。

## 为什么这一版几乎全是 Lua

第一版这里是"GET 之后再 SET"、"LLEN 之后再 RPUSH"、"LREM 之后再 DEL"。
每一处都能和另一条协程交叉，而交叉的后果都落在"钱已经花掉了"那一侧：

* 出队与清理交叉 —— 一条已经被判超时的请求仍然被连接器捞去跑一遍，
  用户看到失败，桌面上却在烧订阅额度。
* 深度检查与入队交叉 —— 上限形同虚设。
* 清理的两步之间崩掉 —— 队列里留下一个指向已删载荷的 id。

所以入队、出队、完成、清理**四个动作各自是一个 Lua 脚本**，Redis 单线程
执行脚本，中间插不进任何东西。

## 绝对截止时间

第一版出队时给的是"一份全新的 timeout_seconds"，于是一条在队列里躺了
两分钟的请求，被取走时又获得完整的三分钟——等待方那边早就超时了，
桌面却还在跑。这一版入队时就把 `deadline_ms` 算死，出队时只发**剩余时间**，
剩余不够就直接丢弃，不交给连接器。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal, cast

from apps.api.core.logging import get_logger
from apps.api.core.redis import get_redis

log = get_logger(__name__)

# --- key 前缀 ---
# core/redis.py 的 Keys 里列了 q/cache/events/cb/idem 五个前缀，
# 这里新增第六个 `lrt:`。放在模块内而不是 Keys 里：它们全是本模块的
# 内部实现细节，别的模块不该拼得出这些 key。
_PREFIX = "lrt"

#: 一条请求要什么能力。文本和图片分两条队列：连接器按自己**真实**支持的
#: 那几种来取，claude 那一份不会莫名其妙拿到一条出图请求。
RequestKind = Literal["text", "image"]
KINDS: tuple[RequestKind, ...] = ("text", "image")

#: 队列深度上限。桌面一次只跑一个子进程，堆到这个数说明要么连接器停了、
#: 要么有人在打压测，两种情况都该立刻拒绝而不是继续堆。
MAX_QUEUE_DEPTH = 8

#: 心跳有效期。连接器空闲时约每秒 poll 一次，忙时由 heartbeat 端点续。
HEARTBEAT_TTL_SECONDS = 20

#: 一次 poll 最多跳过多少条已失效的排队项，防止在脏队列上打转。
_POLL_SKIP_LIMIT = 16

#: 出队后到回结果之间，租约的额外宽限（毫秒）。
_LEASE_SLACK_MS = 30_000

#: 剩余时间少于这个数就不再派发：派出去也一定跑不完，只是白烧额度。
_MIN_REMAINING_MS = 5_000

# --- Lua ---
#
# 每个脚本都是一个原子动作。KEYS/ARGV 的顺序在下面各自的 Python 包装里
# 写死，不要在别处直接调这些脚本。

_ENQUEUE_LUA = """
if redis.call('LLEN', KEYS[1]) >= tonumber(ARGV[4]) then return 0 end
redis.call('SET', KEYS[2], ARGV[2], 'EX', tonumber(ARGV[3]))
redis.call('RPUSH', KEYS[1], ARGV[1])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
return 1
"""

_TAKE_LUA = """
local skip = tonumber(ARGV[3])
for i = 1, skip do
  local id = redis.call('LPOP', KEYS[1])
  if not id then return {} end
  local payload = redis.call('GET', ARGV[5] .. id)
  if payload then
    local job = cjson.decode(payload)
    local remaining = tonumber(job['deadline_ms']) - tonumber(ARGV[1])
    if remaining > tonumber(ARGV[7]) then
      local ok = redis.call('SET', ARGV[6] .. id, ARGV[2], 'NX', 'PX',
                            remaining + tonumber(ARGV[4]))
      if ok then
        return {id, payload, tostring(math.floor(remaining))}
      end
    end
  end
end
return {}
"""

# 一次性完成：比对租约 → 删租约与载荷 → 投递结果。三步必须原子，
# 否则"超时清理"与"结果回传"能交叉成：结果写进了一个已经没人等的邮箱，
# 或者同一条请求被回两次、后一次覆盖前一次。
_COMPLETE_LUA = """
local lease = redis.call('GET', KEYS[1])
if not lease then return -1 end
if lease ~= ARGV[1] then return 0 end
redis.call('DEL', KEYS[1])
redis.call('DEL', KEYS[2])
redis.call('RPUSH', KEYS[3], ARGV[2])
redis.call('EXPIRE', KEYS[3], tonumber(ARGV[3]))
return 1
"""

# 清理：把还没出队的排队项摘掉 + 删三个键。两步之间不能被插入一次 take，
# 否则连接器会捞到一条载荷已删的 id。
_CLEANUP_LUA = """
redis.call('LREM', KEYS[1], 0, ARGV[1])
redis.call('DEL', KEYS[2], KEYS[3], KEYS[4])
return 1
"""

#: `_COMPLETE_LUA` 的返回值。
COMPLETE_OK = 1
COMPLETE_LEASE_MISMATCH = 0
COMPLETE_NOT_OUTSTANDING = -1


def _r() -> Any:
    """redis-py 的类型存根同步/异步共用，异步方法被标成
    `Awaitable[T] | T`，直接 await 过不了 mypy strict。
    与 gateway/breaker.py 同样的处理，在一处收敛掉。"""
    return cast(Any, get_redis())


async def _release(client: Any) -> None:
    """把连接还回共享池。

    **必须显式传 `close_connection_pool=False`。** 池是 `core/redis.py` 的
    进程级单例，SSE、熔断器、幂等键全都在用它；让 `aclose()` 自己决定的话，
    某些 redis-py 版本会把整个池一起断掉——那一瞬间所有 SSE 连接同时掉线，
    而且报出来的错跟本模块毫无关系。
    """
    await client.aclose(close_connection_pool=False)


def queue_key(provider: str, kind: str) -> str:
    return f"{_PREFIX}:queue:{provider}:{kind}"


def request_key(request_id: uuid.UUID) -> str:
    return f"{_PREFIX}:req:{request_id}"


def lease_key(request_id: uuid.UUID) -> str:
    return f"{_PREFIX}:lease:{request_id}"


def reply_key(request_id: uuid.UUID) -> str:
    return f"{_PREFIX}:resp:{request_id}"


def heartbeat_key(provider: str) -> str:
    return f"{_PREFIX}:hb:{provider}"


def _req_prefix() -> str:
    return f"{_PREFIX}:req:"


def _lease_prefix() -> str:
    return f"{_PREFIX}:lease:"


@dataclass(frozen=True, slots=True)
class TextJob:
    system: str
    user: str
    schema_name: str
    max_output_tokens: int


@dataclass(frozen=True, slots=True)
class ImageJob:
    prompt: str
    negative_prompt: str
    size: str
    n: int


@dataclass(frozen=True, slots=True)
class LeasedRequest:
    request_id: uuid.UUID
    lease_token: str
    kind: RequestKind
    #: **剩余**秒数，不是原始 timeout。见模块文档「绝对截止时间」。
    timeout_seconds: int
    text: TextJob | None = None
    image: ImageJob | None = None


@dataclass(frozen=True, slots=True)
class Reply:
    text: str | None = None
    model_id: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    error_code: str | None = None
    #: 图片以 base64 在邮箱里走一趟。等待方在 Worker 进程里，
    #: 而结果是从 API 进程回来的，两个进程之间只有 Redis。
    image_base64: str | None = None
    image_mime: str | None = None
    #: 模型改写后的提示词（Codex 的 `revisedPrompt`）。如实带回，
    #: 落进资产 metadata 的 `actual_prompt`——与万相那条链路同一个字段名。
    revised_prompt: str | None = None


@dataclass(frozen=True, slots=True)
class RunnerState:
    provider: str
    kinds: tuple[str, ...]
    #: 连接器自报的可执行文件版本，只用于界面展示与排障。
    version: str | None


async def touch_heartbeat(provider: str, *, kinds: list[str], version: str | None = None) -> None:
    """刷心跳，并记下这台连接器**真实**支持哪几种能力。

    能力由连接器自报而不是服务端写死：服务端猜不到用户装没装 Codex、
    那份 Codex 有没有 image_gen。界面上那句"本机可以出图"必须来自这里，
    否则就是一个假入口。
    """
    payload = json.dumps(
        {"ts": int(time.time()), "kinds": sorted(set(kinds)), "version": version},
        ensure_ascii=False,
    )
    r = _r()
    try:
        await r.setex(heartbeat_key(provider), HEARTBEAT_TTL_SECONDS, payload)
    finally:
        await _release(r)


async def runner_state(provider: str) -> RunnerState | None:
    """连接器还在不在，支持什么。不在返回 None。"""
    r = _r()
    try:
        raw = await r.get(heartbeat_key(provider))
    finally:
        await _release(r)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    kinds = data.get("kinds")
    version = data.get("version")
    return RunnerState(
        provider=provider,
        kinds=tuple(str(k) for k in kinds) if isinstance(kinds, list) else (),
        version=version if isinstance(version, str) and version else None,
    )


async def runner_connected(provider: str) -> bool:
    return (await runner_state(provider)) is not None


async def queue_depth(provider: str, kind: str) -> int:
    r = _r()
    try:
        return int(await r.llen(queue_key(provider, kind)))
    finally:
        await _release(r)


async def enqueue(
    *,
    provider: str,
    kind: RequestKind,
    request_id: uuid.UUID,
    timeout_seconds: int,
    text: TextJob | None = None,
    image: ImageJob | None = None,
) -> bool:
    """把一条请求放进邮箱。**深度检查与入队是一个原子动作。**

    返回 False 表示队列已满（调用方据此报 `local_runtime.busy`）。
    TTL 一律带上：进程崩了也不会留下永久垃圾。
    """
    deadline_ms = int(time.time() * 1000) + timeout_seconds * 1000
    payload: dict[str, Any] = {
        "request_id": str(request_id),
        "kind": kind,
        "provider": provider,
        "deadline_ms": deadline_ms,
    }
    if text is not None:
        payload["text"] = {
            "system": text.system,
            "user": text.user,
            "schema_name": text.schema_name,
            "max_output_tokens": text.max_output_tokens,
        }
    if image is not None:
        payload["image"] = {
            "prompt": image.prompt,
            "negative_prompt": image.negative_prompt,
            "size": image.size,
            "n": image.n,
        }

    ttl = timeout_seconds + _LEASE_SLACK_MS // 1000
    r = _r()
    try:
        script = r.register_script(_ENQUEUE_LUA)
        accepted = await script(
            keys=[queue_key(provider, kind), request_key(request_id)],
            args=[
                str(request_id),
                json.dumps(payload, ensure_ascii=False),
                ttl,
                MAX_QUEUE_DEPTH,
            ],
        )
        return bool(int(accepted))
    finally:
        await _release(r)


def _leased_from(
    raw_id: str, payload_raw: str, remaining_ms: int, lease_token: str
) -> LeasedRequest:
    payload = json.loads(payload_raw)
    raw_kind = str(payload.get("kind", "text"))
    # 收窄到字面量类型：载荷是从 Redis 读回来的，形状不受类型系统保护。
    kind: RequestKind = "image" if raw_kind == "image" else "text"
    text_raw = payload.get("text")
    image_raw = payload.get("image")
    return LeasedRequest(
        request_id=uuid.UUID(raw_id),
        lease_token=lease_token,
        kind=kind if kind in KINDS else "text",
        # 向上取整到秒：截断会让连接器拿到比真实截止时间更早的上限，
        # 边界上刚好跑完的那一次会被自己判超时。
        timeout_seconds=max(1, -(-remaining_ms // 1000)),
        text=(
            TextJob(
                system=str(text_raw.get("system", "")),
                user=str(text_raw.get("user", "")),
                schema_name=str(text_raw.get("schema_name", "")),
                max_output_tokens=int(text_raw.get("max_output_tokens", 0)),
            )
            if isinstance(text_raw, dict)
            else None
        ),
        image=(
            ImageJob(
                prompt=str(image_raw.get("prompt", "")),
                negative_prompt=str(image_raw.get("negative_prompt", "")),
                size=str(image_raw.get("size", "")),
                n=int(image_raw.get("n", 1)),
            )
            if isinstance(image_raw, dict)
            else None
        ),
    )


def _text_of(value: Any) -> str:
    """Lua 回来的可能是 bytes（取决于连接的 decode_responses 配置）。"""
    return value.decode("utf-8") if isinstance(value, bytes | bytearray) else str(value)


async def take(*, provider: str, kind: RequestKind) -> LeasedRequest | None:
    """出队一条并占住租约。**整个动作在一个 Lua 脚本里。**

    出队与"占租约"必须原子：分成两步的话，一条已被清理的请求可能在两步
    之间复活，或者两个连接器同时拿到同一条。返回的是**剩余**时间。
    """
    lease_token = uuid.uuid4().hex + uuid.uuid4().hex
    r = _r()
    try:
        script = r.register_script(_TAKE_LUA)
        result = await script(
            keys=[queue_key(provider, kind)],
            args=[
                int(time.time() * 1000),
                lease_token,
                _POLL_SKIP_LIMIT,
                _LEASE_SLACK_MS,
                _req_prefix(),
                _lease_prefix(),
                _MIN_REMAINING_MS,
            ],
        )
    finally:
        await _release(r)

    if not result or len(result) != 3:
        return None
    raw_id = _text_of(result[0])
    payload_raw = _text_of(result[1])
    remaining = int(_text_of(result[2]))
    return _leased_from(raw_id, payload_raw, remaining, lease_token)


async def is_outstanding(request_id: uuid.UUID) -> bool:
    """这条请求还在等结果吗。

    连接器忙的时候靠它判断"用户是不是已经取消了"——租约在清理时被删，
    所以租约不在 = 没人再等这条了 = 该立刻停下，别继续烧订阅额度。
    """
    r = _r()
    try:
        return bool(await r.exists(lease_key(request_id)))
    finally:
        await _release(r)


async def complete(*, request_id: uuid.UUID, lease_token: str, reply: Reply) -> int:
    """一次性完成。返回 `COMPLETE_*` 之一。

    重复回传、租约不符、已超时清理，三种情况都不会覆盖任何东西——
    这是整条路径上唯一一个"钱已经花掉了"的时刻，写坏了没法回滚。
    """
    body = json.dumps(
        {
            "text": reply.text,
            "model_id": reply.model_id,
            "tokens_in": reply.tokens_in,
            "tokens_out": reply.tokens_out,
            "error_code": reply.error_code,
            "image_base64": reply.image_base64,
            "image_mime": reply.image_mime,
            "revised_prompt": reply.revised_prompt,
        },
        ensure_ascii=False,
    )
    r = _r()
    try:
        script = r.register_script(_COMPLETE_LUA)
        result = await script(
            keys=[lease_key(request_id), request_key(request_id), reply_key(request_id)],
            args=[lease_token, body, _LEASE_SLACK_MS // 1000],
        )
        return int(result)
    finally:
        await _release(r)


async def await_reply(*, request_id: uuid.UUID, timeout_seconds: int) -> Reply | None:
    """阻塞等结果。超时返回 None。

    用 BLPOP 而不是轮询：轮询在超时是分钟级的路径上会产生成千上万次
    无效往返，且延迟还更差。等待方需要**分段**等待时（出图那条要顺便
    看用户有没有取消），由调用方多次调用本函数，每次给一小段超时。
    """
    r = _r()
    try:
        popped = await r.blpop([reply_key(request_id)], timeout=timeout_seconds)
    finally:
        await _release(r)
    if not popped:
        return None
    _key, raw = popped
    data = json.loads(raw)
    return Reply(
        text=data.get("text"),
        model_id=data.get("model_id"),
        tokens_in=data.get("tokens_in"),
        tokens_out=data.get("tokens_out"),
        error_code=data.get("error_code"),
        image_base64=data.get("image_base64"),
        image_mime=data.get("image_mime"),
        revised_prompt=data.get("revised_prompt"),
    )


async def cleanup(*, provider: str, kind: RequestKind, request_id: uuid.UUID) -> None:
    """无论成败都要走一遍：删三个键，并把还没出队的排队项摘掉。

    不删的话，一条超时的请求仍然躺在队列里，连接器下一次 poll 会把它捞
    出来跑一遍——用户已经看到失败了，桌面却还在烧一次订阅额度。
    """
    r = _r()
    try:
        script = r.register_script(_CLEANUP_LUA)
        await script(
            keys=[
                queue_key(provider, kind),
                lease_key(request_id),
                request_key(request_id),
                reply_key(request_id),
            ],
            args=[str(request_id)],
        )
    finally:
        await _release(r)
