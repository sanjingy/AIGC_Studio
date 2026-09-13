"""传输策略：HTTPS / 回环例外 / 不跟重定向 / 令牌不外泄。"""

from __future__ import annotations

import email.message
import unittest
import urllib.error
import urllib.request

from apps.local_runner.client import (
    API_PATH,
    BridgeClient,
    RedirectRefusedError,
    UrlPolicyError,
    _NoRedirect,
    normalize_server_url,
)


class UrlPolicyTest(unittest.TestCase):
    def test_https_anywhere_is_allowed(self) -> None:
        self.assertEqual(
            normalize_server_url("https://api.example.com/"),
            "https://api.example.com",
        )

    def test_http_loopback_is_allowed_for_the_ssh_tunnel(self) -> None:
        for url in (
            "http://127.0.0.1:3100",
            "http://localhost:3100/",
            "http://[::1]:3100",
        ):
            self.assertTrue(normalize_server_url(url).startswith("http://"))

    def test_http_to_a_remote_host_is_refused(self) -> None:
        with self.assertRaises(UrlPolicyError):
            normalize_server_url("http://38.76.215.147:3000")

    def test_other_schemes_are_refused(self) -> None:
        for url in ("ftp://x/y", "file:///c:/x", "ws://127.0.0.1:1"):
            with self.assertRaises(UrlPolicyError):
                normalize_server_url(url)

    def test_credentials_query_and_fragment_are_refused(self) -> None:
        for url in (
            "https://user:pw@example.com",
            "https://example.com/?a=1",
            "https://example.com/#x",
        ):
            with self.assertRaises(UrlPolicyError):
                normalize_server_url(url)


def _dummy_request() -> urllib.request.Request:
    """给 `redirect_request` 一个形状正确的入参。

    传 None 也能跑（它第一件事就是把入参丢掉），但类型检查会拦——
    而这两个用例盯的是"拒绝重定向"，不该为了省事在签名上撒谎。
    """
    return urllib.request.Request("https://example.com")


def _dummy_headers() -> email.message.Message:
    """HTTPError 的第四个参数是邮件头对象，不是 dict。"""
    return email.message.Message()


class RedirectTest(unittest.TestCase):
    def test_redirect_handler_refuses_instead_of_following(self) -> None:
        """urllib 默认会跟随重定向，**并带上 Authorization 头**。
        一个被改过的服务端回一个 302 就能把桥接令牌骗到别的域名下。"""
        handler = _NoRedirect()
        with self.assertRaises(RedirectRefusedError):
            handler.redirect_request(
                _dummy_request(), None, 302, "Found", _dummy_headers(), "https://evil.example/steal"
            )

    def test_refusal_message_does_not_echo_the_target(self) -> None:
        handler = _NoRedirect()
        try:
            handler.redirect_request(
                _dummy_request(), None, 307, "x", _dummy_headers(), "https://evil.example/steal"
            )
        except RedirectRefusedError as exc:
            self.assertNotIn("evil.example", str(exc))
        else:  # pragma: no cover
            self.fail("应当拒绝")


class ClientShapeTest(unittest.TestCase):
    def test_base_url_targets_the_pilot_namespace_only(self) -> None:
        client = BridgeClient("http://127.0.0.1:3100", "t" * 40)
        self.assertEqual(client.base, "http://127.0.0.1:3100" + API_PATH)

    def test_token_is_not_in_repr_or_base(self) -> None:
        client = BridgeClient("https://example.com", "super-secret-token-" + "x" * 20)
        self.assertNotIn("super-secret-token", repr(client))
        self.assertNotIn("super-secret-token", client.base)

    def test_http_error_body_is_not_propagated(self) -> None:
        """服务端的错误体不该被当成结果向上抛：调用方只需要状态码。"""
        from apps.local_runner.client import TransportError

        client = BridgeClient("https://example.com", "t" * 40)

        class _Opener:
            def open(self, request: object, timeout: int) -> None:
                del request, timeout
                raise urllib.error.HTTPError(
                    "https://example.com", 409, "conflict", _dummy_headers(), None
                )

        client._opener = _Opener()  # type: ignore[assignment]
        with self.assertRaises(TransportError) as ctx:
            client.poll("codex", kinds=["text", "image"])
        self.assertEqual(ctx.exception.status, 409)


if __name__ == "__main__":
    unittest.main()
