"""Codex 原生生图的解析与安全边界。

**全部用固定事件帧，不起 Codex、不花任何订阅额度。**
事件形状取自本机 `codex app-server generate-json-schema` 生成的
`v2/ItemCompletedNotification.json`，`result` 的编码取自 OpenAI 官方
Codex 源码用例（`result: "cG5n"` = base64("png")）。
"""

from __future__ import annotations

import asyncio
import base64
import tempfile
import unittest
from pathlib import Path
from typing import Any

from apps.local_runner import imagegen

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP_BYTES = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 16


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def completed_frame(**overrides: object) -> tuple[str, dict[str, Any]]:
    """一条真实形状的 `item/completed` 通知。"""
    item = {
        "id": "call-image",
        "type": "imageGeneration",
        "status": "completed",
        "result": b64(PNG_BYTES),
        "revisedPrompt": "A tiny blue square",
        "transparentBackground": True,
        "failure": None,
        "savedPath": None,
    }
    item.update(overrides)
    return "item/completed", {
        "completedAtMs": 1_700_000_000_000,
        "threadId": "th_1",
        "turnId": "tu_1",
        "item": item,
    }


class DecodeTest(unittest.TestCase):
    def test_official_fixture_encoding_is_base64(self) -> None:
        """官方用例里 result 是 "cG5n"，也就是 base64("png")。
        这条用例把那个事实钉在这里，将来上游改编码会立刻红。"""
        self.assertEqual(imagegen.decode_image_result("cG5n"), b"png")

    def test_real_png_round_trip(self) -> None:
        self.assertEqual(imagegen.decode_image_result(b64(PNG_BYTES)), PNG_BYTES)

    def test_plain_text_is_rejected(self) -> None:
        """宽松 base64 会把一段普通文字也"解"出字节，然后我们把垃圾存进资产库。"""
        with self.assertRaises(imagegen.ImageGenerationError):
            imagegen.decode_image_result("这不是 base64")
        with self.assertRaises(imagegen.ImageGenerationError):
            imagegen.decode_image_result("not!base64!!")

    def test_empty_result_is_rejected(self) -> None:
        with self.assertRaises(imagegen.ImageGenerationError):
            imagegen.decode_image_result("")

    def test_oversized_result_is_rejected_before_decoding(self) -> None:
        huge = "A" * ((imagegen.MAX_IMAGE_BYTES // 3 + 2) * 4)
        with self.assertRaises(imagegen.ImageGenerationError):
            imagegen.decode_image_result(huge)


class MimeSniffTest(unittest.TestCase):
    def test_known_formats(self) -> None:
        self.assertEqual(imagegen.sniff_image_mime(PNG_BYTES), "image/png")
        self.assertEqual(imagegen.sniff_image_mime(JPEG_BYTES), "image/jpeg")
        self.assertEqual(imagegen.sniff_image_mime(WEBP_BYTES), "image/webp")

    def test_riff_that_is_not_webp_is_refused(self) -> None:
        """RIFF 容器不只有 webp；只看前四字节会把 wav 当成图片。"""
        wav = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"\x00" * 16
        self.assertIsNone(imagegen.sniff_image_mime(wav))

    def test_unknown_bytes_are_refused(self) -> None:
        self.assertIsNone(imagegen.sniff_image_mime(b"hello world"))


class SavedPathTest(unittest.TestCase):
    """`savedPath` 是唯一一处外部输入能指向任意磁盘位置的地方。"""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="imagegen-root-"))
        self.inside = self.root / "generated_images"
        self.inside.mkdir()
        self.good = self.inside / "a.png"
        self.good.write_bytes(PNG_BYTES)

    def test_accepts_a_real_file_inside_an_allowed_root(self) -> None:
        got = imagegen.resolve_saved_path(str(self.good), [self.inside])
        self.assertIsNotNone(got)
        self.assertEqual(got, self.good.resolve())

    def test_refuses_a_path_outside_the_allowed_roots(self) -> None:
        outside = Path(tempfile.mkdtemp(prefix="imagegen-outside-")) / "b.png"
        outside.write_bytes(PNG_BYTES)
        self.assertIsNone(imagegen.resolve_saved_path(str(outside), [self.inside]))

    def test_refuses_dot_dot_escape(self) -> None:
        """`..` 能把路径指到仓库或 .codex/auth.json 旁边，而我们随后要读这个文件。"""
        escaped = str(self.inside / ".." / ".." / "etc" / "passwd")
        self.assertIsNone(imagegen.resolve_saved_path(escaped, [self.inside]))

    def test_refuses_relative_paths(self) -> None:
        self.assertIsNone(imagegen.resolve_saved_path("relative/x.png", [self.inside]))

    def test_refuses_a_path_that_does_not_exist(self) -> None:
        """schema 自己写了 savedPath 不保证存在。"""
        self.assertIsNone(
            imagegen.resolve_saved_path(str(self.inside / "missing.png"), [self.inside])
        )

    def test_refuses_a_directory(self) -> None:
        self.assertIsNone(imagegen.resolve_saved_path(str(self.inside), [self.inside]))

    def test_no_allowed_roots_means_no_path(self) -> None:
        self.assertIsNone(imagegen.resolve_saved_path(str(self.good), []))


class ReadItemTest(unittest.TestCase):
    def test_happy_path(self) -> None:
        _method, params = completed_frame()
        outcome = imagegen.read_image_item(params["item"], allowed_roots=[])
        self.assertEqual(outcome.data, PNG_BYTES)
        self.assertEqual(outcome.mime_type, "image/png")
        self.assertEqual(outcome.revised_prompt, "A tiny blue square")
        self.assertTrue(outcome.transparent_background)
        self.assertIsNone(outcome.saved_path)

    def test_usage_limit_failure_is_its_own_error(self) -> None:
        """额度用尽必须能被单独识别——它要变成一句"会员额度用完了，几点恢复"，
        而不是"生成失败"。"""
        _method, params = completed_frame(
            failure={"type": "usageLimitExceeded", "limitId": "img_daily", "resetsAt": 123}
        )
        with self.assertRaises(imagegen.UsageLimitExceededError) as ctx:
            imagegen.read_image_item(params["item"], allowed_roots=[])
        self.assertEqual(ctx.exception.limit_id, "img_daily")
        self.assertEqual(ctx.exception.resets_at, 123)

    def test_non_image_bytes_are_refused(self) -> None:
        _method, params = completed_frame(result=b64(b"just some text"))
        with self.assertRaises(imagegen.ImageGenerationError):
            imagegen.read_image_item(params["item"], allowed_roots=[])


class FrameRecognitionTest(unittest.TestCase):
    def test_in_progress_item_is_not_a_result(self) -> None:
        """`item/started` 带同一个 item 但 result 是空串；
        当成结果就会拿到零字节。"""
        _m, params = completed_frame(status="in_progress", result="")
        self.assertIsNone(imagegen.is_completed_image_item("item/started", params))
        self.assertIsNone(imagegen.is_completed_image_item("item/completed", params))

    def test_other_item_types_are_ignored(self) -> None:
        _m, params = completed_frame()
        params["item"]["type"] = "agentMessage"
        self.assertIsNone(imagegen.is_completed_image_item("item/completed", params))

    def test_completed_image_is_recognised(self) -> None:
        method, params = completed_frame()
        self.assertIsNotNone(imagegen.is_completed_image_item(method, params))


class WatcherTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_the_image_when_it_arrives(self) -> None:
        watcher = imagegen.TurnWatcher(allowed_roots=[])
        watcher.feed("item/started", completed_frame(status="in_progress", result="")[1])
        watcher.feed(*completed_frame())
        outcome = await watcher.wait_for_image(timeout_seconds=5)
        self.assertEqual(outcome.data, PNG_BYTES)

    async def test_turn_completed_without_an_image_is_its_own_failure(self) -> None:
        """模型可以合法地回一段文字而不画图。对用户是失败，但不是"错误"，
        要单独成一类好让上层决定重试还是报错。"""
        watcher = imagegen.TurnWatcher(allowed_roots=[])
        watcher.feed("turn/completed", {"threadId": "th_1", "turnId": "tu_1"})
        with self.assertRaises(imagegen.NoImageProducedError):
            await watcher.wait_for_image(timeout_seconds=5)

    async def test_error_notification_fails_fast(self) -> None:
        watcher = imagegen.TurnWatcher(allowed_roots=[])
        watcher.feed("error", {"message": "something went wrong"})
        with self.assertRaises(imagegen.ImageGenerationError):
            await watcher.wait_for_image(timeout_seconds=5)

    async def test_timeout_does_not_hang_forever(self) -> None:
        from apps.local_runner.appserver import AppServerTimeoutError

        watcher = imagegen.TurnWatcher(allowed_roots=[])
        with self.assertRaises(AppServerTimeoutError):
            await watcher.wait_for_image(timeout_seconds=0.1)

    async def test_cancellation_propagates(self) -> None:
        """用户取消任务时这一轮必须能被打断，不能留一个永远等下去的协程。"""
        watcher = imagegen.TurnWatcher(allowed_roots=[])
        task = asyncio.create_task(watcher.wait_for_image(timeout_seconds=30))
        await asyncio.sleep(0.05)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task


if __name__ == "__main__":
    unittest.main()
