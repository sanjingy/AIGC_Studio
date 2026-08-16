"""MIME 白名单与文件名清洗。

白名单而非黑名单：用户能上传什么必须是显式列举的。
黑名单永远漏，且漏的那个通常就是能执行的那个。
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath

# mime -> 资产类型
ALLOWED_MIME: dict[str, str] = {
    "image/png": "image",
    "image/jpeg": "image",
    "image/webp": "image",
    "image/gif": "image",
    "video/mp4": "video",
    "video/webm": "video",
    "video/quicktime": "video",
    "audio/mpeg": "audio",
    "audio/wav": "audio",
    "audio/x-wav": "audio",
    "audio/mp4": "audio",
    "text/plain": "text",
    "text/markdown": "text",
    "application/json": "workflow",
    "application/pdf": "document",
    "application/epub+zip": "document",
}

_UNSAFE = re.compile(r"[^\w.\-]", re.UNICODE)


def asset_type_for(mime: str) -> str | None:
    return ALLOWED_MIME.get(mime.split(";")[0].strip().lower())


def sanitize_filename(raw: str) -> str:
    """把用户文件名清成可安全用作对象键片段的形式。

    只取 basename——`../../etc/passwd` 这类穿越串在这里被截断。
    对象键本身还带 org_id 和 asset_id 前缀，即便清洗漏了也越不出租户目录。
    """
    name = PurePosixPath(raw.replace("\\", "/")).name
    name = unicodedata.normalize("NFKC", name).strip()
    name = _UNSAFE.sub("_", name)
    name = name.lstrip(".") or "file"
    # 对象键总长有限制，给前缀留出余量
    if len(name.encode()) > 120:
        stem, dot, ext = name.rpartition(".")
        keep = 100 - len(ext)
        name = f"{stem[: max(keep, 1)]}{dot}{ext}" if dot else name[:100]
    return name
