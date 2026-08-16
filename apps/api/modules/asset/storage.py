"""对象存储访问。

两个客户端不是冗余，是必须的：
- **签名客户端**用对外地址（localhost:9000 / CDN 域名）。预签名 URL 的
  签名把 host 算进去了，用容器内地址签出来的 URL 浏览器根本连不上。
- **内部客户端**用容器内地址（minio:9000），供 HEAD / DELETE 等服务端操作。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aioboto3
from botocore.config import Config
from botocore.exceptions import ClientError

from apps.api.core.config import get_settings
from apps.api.core.logging import get_logger

log = get_logger(__name__)

_session = aioboto3.Session()
# 预签名用 SigV4，MinIO 与 S3 都认
_CONFIG = Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"})


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    size_bytes: int
    etag: str
    content_type: str


def _client(*, public: bool) -> Any:
    s = get_settings()
    return _session.client(
        "s3",
        endpoint_url=s.s3_public_endpoint_url if public else s.s3_endpoint_url,
        aws_access_key_id=s.s3_access_key.get_secret_value(),
        aws_secret_access_key=s.s3_secret_key.get_secret_value(),
        region_name=s.s3_region,
        config=_CONFIG,
    )


async def presign_put(*, key: str, content_type: str) -> str:
    """签发上传 URL。客户端直传对象存储，不经过 API 进程。

    大文件走 API 代理会把进程打死——一个 500MB 的视频素材就够了。
    """
    s = get_settings()
    async with _client(public=True) as s3:
        url: str = await s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": s.s3_bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=s.s3_presign_ttl_seconds,
        )
    return url


async def presign_get(*, key: str, download_name: str | None = None) -> str:
    s = get_settings()
    params: dict[str, Any] = {"Bucket": s.s3_bucket, "Key": key}
    if download_name:
        params["ResponseContentDisposition"] = f'attachment; filename="{download_name}"'
    async with _client(public=True) as s3:
        url: str = await s3.generate_presigned_url(
            "get_object", Params=params, ExpiresIn=s.s3_presign_ttl_seconds
        )
    return url


async def head_object(*, key: str) -> ObjectInfo | None:
    """对象不存在返回 None，其余异常照抛。"""
    s = get_settings()
    async with _client(public=False) as s3:
        try:
            resp = await s3.head_object(Bucket=s.s3_bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
    return ObjectInfo(
        size_bytes=int(resp["ContentLength"]),
        etag=str(resp["ETag"]).strip('"'),
        content_type=str(resp.get("ContentType", "application/octet-stream")),
    )


async def delete_object(*, key: str) -> None:
    s = get_settings()
    async with _client(public=False) as s3:
        try:
            await s3.delete_object(Bucket=s.s3_bucket, Key=key)
        except ClientError as exc:
            # 删不掉不该让业务失败，记日志人工兜底
            log.warning("storage.delete_failed", key=key, error=str(exc))
