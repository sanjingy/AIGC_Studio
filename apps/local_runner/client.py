"""与服务端之间的 HTTP。只用 `urllib`，只出站。

两条硬规则：

* **传输必须是 HTTPS，唯一例外是回环地址上的 HTTP。** 那个例外是给现成的
  SSH 隧道用的（`ssh -L 3100:127.0.0.1:3000`）——那一段的加密由 SSH 负责，
  而流量根本没离开本机。除此之外用明文 HTTP 就是把桥接令牌裸奔在网上。
* **不跟随任何重定向。** urllib 默认会跟，并且会把 `Authorization` 头
  一起带到新地址去。一个被改过的服务端只要回一个 302 就能把令牌骗到
  自己的域名下。这里直接拒绝，宁可报错。
"""

from __future__ import annotations

import contextlib
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

#: 允许明文 HTTP 的主机。回环之外一个都不加。
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

#: 回传结果时按体积放大超时的假定上行速率（字节/秒）。
#:
#: 取 50 KB/s（约 400 kbps）是**保守**值，不是测出来的带宽：这条链路常见的
#: 形态是 `ssh -L` 隧道到一台远端服务器，家用上行做不到对称。一张
#: 1024×1024 的 PNG 有 2~3 MB，base64 之后 3~4 MB，按固定 30 秒发必然超时——
#: 而超时的后果是最坏的一种：**用户的订阅额度已经花掉、图已经画出来、
#: 但没人收**（连接器不重投，服务端最终按超时把预扣退掉）。
_RESULT_UPLOAD_BYTES_PER_SECOND = 50_000

#: 回传结果的超时下限。小结果（文本、错误码）用它就够。
_RESULT_MIN_TIMEOUT_SECONDS = 30

#: 回传结果的超时上限。再慢也不能无限等——服务端那边的截止时间才是真的。
_RESULT_MAX_TIMEOUT_SECONDS = 600

API_PATH = "/api/v1/local-runtime"


class UrlPolicyError(Exception):
    """服务端地址不符合传输策略。"""


class RedirectRefusedError(Exception):
    """服务端要求重定向。带着令牌跟过去是不行的。"""


class TransportError(Exception):
    """一次请求没成功。`status` 为 None 表示连都没连上。"""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        del req, fp, msg, headers
        # 连目标地址都不回显：日志会被贴到工单里，而那是攻击者控制的字符串。
        raise RedirectRefusedError(f"服务端返回了 {code} 重定向，已拒绝（不会带着令牌跟过去）")


def normalize_server_url(raw: str) -> str:
    """校验并规范化服务端地址，返回不带尾斜杠的 origin。"""
    parsed = urllib.parse.urlsplit(raw.strip())
    if parsed.scheme not in ("http", "https"):
        raise UrlPolicyError(f"只支持 http/https，收到 {parsed.scheme or '(空)'}")
    if not parsed.hostname:
        raise UrlPolicyError("地址里没有主机名")
    if parsed.username or parsed.password:
        raise UrlPolicyError("地址里不许带用户名密码")
    if parsed.query or parsed.fragment:
        raise UrlPolicyError("地址里不许带查询串或锚点")
    if parsed.scheme == "http" and parsed.hostname.lower() not in _LOOPBACK_HOSTS:
        raise UrlPolicyError(
            f"{parsed.hostname} 上必须用 HTTPS。"
            " 明文 HTTP 只允许回环地址（现成的 SSH 隧道走的就是这一条）。"
        )
    path = parsed.path.rstrip("/")
    netloc = parsed.netloc
    return f"{parsed.scheme}://{netloc}{path}"


class BridgeClient:
    """桥接端点的最小客户端。**令牌只在 Authorization 头里出现。**"""

    def __init__(self, server_url: str, token: str, *, timeout_seconds: int = 30) -> None:
        self._base = normalize_server_url(server_url) + API_PATH
        self._token = token
        self._timeout = timeout_seconds
        self._opener = urllib.request.build_opener(_NoRedirect)

    @property
    def base(self) -> str:
        return self._base

    def _post(self, path: str, body: dict[str, Any], *, timeout: int) -> tuple[int, bytes]:
        url = self._base + path
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        # scheme 已由 normalize_server_url 校验过，只可能是 http(s)。
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {self._token}",
                "User-Agent": "aigc-local-runner/1",
            },
        )
        try:
            with self._opener.open(request, timeout=timeout) as response:
                return int(response.status), response.read()
        except urllib.error.HTTPError as exc:
            # 读掉响应体但不外传：它是服务端的错误对象，交给调用方按状态码判断。
            with contextlib.suppress(OSError):
                exc.read()
            raise TransportError(f"{path} 返回 {exc.code}", status=exc.code) from exc
        except RedirectRefusedError:
            raise
        except urllib.error.URLError as exc:
            raise TransportError(f"{path} 连不上：{exc.reason}") from exc
        except OSError as exc:
            raise TransportError(f"{path} 传输失败：{type(exc).__name__}") from exc

    def poll(
        self, provider: str, *, kinds: list[str], version: str | None = None
    ) -> dict[str, Any] | None:
        """取一条请求。没有活时返回 None（服务端回 204）。

        `kinds` 是这台机器**真实**支持的能力（可执行文件解析得到 +
        Codex 自己报的 image_generation）。服务端据此派活，也据此在网页上
        显示"本机能不能出图"——报一个跑不了的能力等于给用户一个假入口。
        """
        body: dict[str, Any] = {"provider": provider, "kinds": kinds}
        if version:
            body["version"] = version
        status, raw = self._post("/poll", body, timeout=min(self._timeout, 30))
        if status == 204 or not raw.strip():
            return None
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise TransportError("poll 返回了非对象")
        return payload

    def heartbeat(
        self,
        provider: str,
        *,
        kinds: list[str],
        version: str | None = None,
        request_id: str | None = None,
    ) -> bool:
        """跑活时续心跳，并问一句"我手上这条还有人等吗"。

        返回 False 就必须立刻停手：那意味着用户在网页上取消了，或者等待方
        已经超时。继续跑下去只是白烧他自己的订阅额度。

        心跳本身也是必须的：停了心跳，服务端 20 秒后就判定"没有连接器"，
        把同一时刻进来的下一条请求直接拒掉——而我们其实好好地在干活。
        """
        body: dict[str, Any] = {"provider": provider, "kinds": kinds}
        if version:
            body["version"] = version
        if request_id:
            body["request_id"] = request_id
        _status, raw = self._post("/heartbeat", body, timeout=min(self._timeout, 30))
        if not raw.strip():
            return True
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise TransportError("heartbeat 返回了非对象")
        return bool(payload.get("outstanding", True))

    @staticmethod
    def result_timeout(body_bytes: int, *, budget_left_seconds: float | None = None) -> int:
        """回传这么大的结果该等多久。

        两条约束同时成立，取小的那个：

        1. **按体积**：`30 + 体积 / 50KB每秒`，封顶 600 秒。固定 30 秒对
           一张 3 MB 的 base64 图片是不够的（见 `_RESULT_UPLOAD_BYTES_PER_SECOND`）。
        2. **不超过这一轮剩余的绝对预算**：服务端发下来的 `timeout_seconds`
           是**剩余**时间，等结果已经花掉了其中一大截。回传再自顾自地等
           十分钟，只是在服务端早已判超时之后继续占着本机的网络——
           那条请求已经没人收了。

        **注意这里不含任何重发。** 回传失败就让它超时：重发意味着同一条
        请求可能被判两次，而"图已经画出来了"这件事没法回滚。
        """
        by_size = _RESULT_MIN_TIMEOUT_SECONDS + body_bytes // _RESULT_UPLOAD_BYTES_PER_SECOND
        timeout = min(by_size, _RESULT_MAX_TIMEOUT_SECONDS)
        if budget_left_seconds is not None:
            # 至少给 1 秒：给 0 会让 urllib 立刻抛，错误信息指向"超时"，
            # 而真正的原因是"预算已经用完了"。
            timeout = min(timeout, max(1, int(budget_left_seconds)))
        return max(1, timeout)

    def post_result(
        self,
        request_id: str,
        *,
        lease_token: str,
        budget_left_seconds: float | None = None,
        text: str | None = None,
        image_base64: str | None = None,
        image_mime: str | None = None,
        revised_prompt: str | None = None,
        model_id: str | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        error_code: str | None = None,
    ) -> None:
        body: dict[str, Any] = {"lease_token": lease_token}
        if text is not None:
            body["text"] = text
        if image_base64 is not None:
            body["image_base64"] = image_base64
        if image_mime is not None:
            body["image_mime"] = image_mime
        if revised_prompt is not None:
            body["revised_prompt"] = revised_prompt
        if model_id is not None:
            body["model_id"] = model_id
        if tokens_in is not None:
            body["tokens_in"] = tokens_in
        if tokens_out is not None:
            body["tokens_out"] = tokens_out
        if error_code is not None:
            body["error_code"] = error_code
        # request_id 来自服务端，仍然做一次 quote：拼 URL 的地方永远不信任输入。
        quoted = urllib.parse.quote(request_id, safe="")
        # 用 base64 字段的长度当体积估计：它是这个请求体里唯一可能上兆的东西。
        body_bytes = len(image_base64 or "") + len(text or "")
        self._post(
            f"/requests/{quoted}/result",
            body,
            timeout=self.result_timeout(body_bytes, budget_left_seconds=budget_left_seconds),
        )
