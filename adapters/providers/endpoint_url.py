"""用户可控的出网地址怎么校验（05_MODEL_GATEWAY.md §5.2 第 3 条）。

文本能力的 OpenAI 兼容自定义端点是这个仓库**唯一一个**由用户填、由服务端
去请求的地址。不校验就是一个 SSRF 入口：填 `http://169.254.169.254/` 读云
元数据，填 `https://10.0.0.5/` 扫内网，填 `https://user:pw@host/` 让密码
进日志。所以分两道：

1. **保存时的形状校验**（:func:`normalize_base_url`）：只收 `https://`，
   不收 userinfo / fragment / query，主机名不能是 localhost，也不能是
   私网 / 回环 / 链路本地 / 保留段的 IP 字面量。
2. **每次发请求前的解析校验**（:func:`assert_public_host`）：域名解析出来的
   **每一个**地址都必须是公网地址。只在保存时查一次挡不住 DNS rebinding——
   保存那一刻解析到公网，调用那一刻解析到 127.0.0.1。

为什么只收 https、不收 http：仓库文档（§5.2）就是这么定的；而且 http
意味着用户的 Key 以明文走公网。本地起的 http 模型服务本来就落在回环或
私网，第 2 道也会拒它——放开 http 换不来任何一个能用的场景。

不跟随跳转在适配器里做（`follow_redirects=False`）：一次 302 就能把请求
带回内网，前两道全部白做。
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

from apps.api.core.errors import AppError

MAX_URL_CHARS = 512

# 解析函数单独成一个可替换的点：测试里没有 DNS，也不该有。
Resolver = Callable[[str], Awaitable[list[str]]]

_BLOCKED_HOSTNAMES = frozenset({"localhost", "localhost.localdomain", "metadata.google.internal"})


def _invalid(message: str) -> AppError:
    return AppError("common.validation_failed", message=message, detail={"field": "base_url"})


def _ip_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # `is_global` 已经排除了私网、回环、链路本地（含 169.254.169.254）、
    # 保留段和 CGNAT（100.64/10）。IPv4 映射的 IPv6 要拆出来再判一次，
    # 否则 `::ffff:127.0.0.1` 会以"一个 IPv6 地址"的身份溜过去。
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _ip_is_public(ip.ipv4_mapped)
    return bool(ip.is_global) and not ip.is_multicast


def normalize_base_url(raw: str) -> str:
    """校验并规整一个 Base URL。返回去掉末尾 `/` 的版本。

    只做不需要网络的检查；解析后的地址在 :func:`assert_public_host` 里查。
    """
    url = (raw or "").strip()
    if not url:
        raise _invalid("请填写 API 请求地址")
    if len(url) > MAX_URL_CHARS:
        raise _invalid(f"API 请求地址不能超过 {MAX_URL_CHARS} 个字符")
    if any(ch.isspace() for ch in url) or not url.isprintable():
        raise _invalid("API 请求地址里不应包含空白或控制字符")

    parts = urlsplit(url)
    if parts.scheme.lower() != "https":
        raise _invalid("API 请求地址必须以 https:// 开头")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise _invalid("API 请求地址里不能带用户名或密码，Key 请填在 API Key 一栏")
    if parts.fragment or "#" in url:
        raise _invalid("API 请求地址不能带 # 片段")
    if parts.query:
        raise _invalid("API 请求地址不能带查询参数")

    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise _invalid("API 请求地址缺少主机名")
    if host in _BLOCKED_HOSTNAMES or host.endswith(".localhost"):
        raise _invalid("API 请求地址不能指向本机")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and not _ip_is_public(literal):
        raise _invalid("API 请求地址不能指向内网、回环或云元数据地址")

    try:
        port = parts.port
    except ValueError as exc:
        raise _invalid("API 请求地址的端口不合法") from exc
    del port

    path = parts.path.rstrip("/")
    return urlunsplit(("https", parts.netloc.lower(), path, "", ""))


async def _system_resolver(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


_resolver: Resolver = _system_resolver


def set_resolver(resolver: Resolver | None) -> None:
    """测试用。传 None 恢复系统解析。"""
    global _resolver
    _resolver = resolver or _system_resolver


async def assert_public_host(base_url: str) -> None:
    """请求发出前的最后一道：主机解析出来的每个地址都必须是公网地址。

    任何一个不是就整体拒绝——只要有一条记录指向内网，连接时就可能落到它。
    解析失败按"上游不可用"报，不当成用户填错：临时 DNS 故障不该让人去改配置。
    """
    host = (urlsplit(base_url).hostname or "").rstrip(".").lower()
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            resolved = await _resolver(host)
        except OSError as exc:
            raise AppError(
                "provider.unavailable", message=f"无法解析自定义端点的主机名：{exc}"
            ) from exc
        addresses = []
        for text in resolved:
            try:
                addresses.append(ipaddress.ip_address(text.split("%", 1)[0]))
            except ValueError:
                continue
    if not addresses:
        raise AppError("provider.unavailable", message="自定义端点的主机名没有解析出任何地址")
    if not all(_ip_is_public(ip) for ip in addresses):
        raise AppError(
            "provider.params.invalid",
            message="自定义端点解析到了内网、回环或云元数据地址，已拒绝请求",
        )
