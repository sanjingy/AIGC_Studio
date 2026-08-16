"""应用配置。

所有配置来自环境变量，代码中不出现任何硬编码的密钥、地址或价格。
价格与业务系数不放这里——它们属于 `pricing_rules` / `model_pricing` 表，
需要热更新（ADR-014）。这里只放启动期就必须确定的基础设施配置。
"""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, PostgresDsn, SecretStr, computed_field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    env: Literal["local", "test", "staging", "prod"] = "local"
    debug: bool = False
    log_level: str = "INFO"

    # --- 安全 ---
    secret_key: SecretStr = SecretStr("dev-only-insecure-key-please-change")
    access_token_ttl_seconds: int = 1800
    refresh_token_ttl_seconds: int = 1_209_600

    # --- PostgreSQL ---
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "aigc"
    postgres_password: SecretStr = SecretStr("aigc_dev_password")
    postgres_db: str = "aigc_studio"

    # --- Redis ---
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    # --- 对象存储 ---
    s3_endpoint_url: str = "http://localhost:9000"
    # 预签名 URL 要给浏览器用，容器内地址（minio:9000）浏览器访问不到，
    # 所以对外签名必须用这个地址。踩过这个坑的人都知道。
    s3_public_endpoint_url: str = "http://localhost:9000"
    s3_access_key: SecretStr = SecretStr("aigc_minio")
    s3_secret_key: SecretStr = SecretStr("aigc_minio_password")
    s3_bucket: str = "aigc-assets"
    s3_region: str = "us-east-1"
    s3_presign_ttl_seconds: int = 900
    s3_max_upload_bytes: int = 512 * 1024 * 1024  # 512 MiB
    # 未完成的上传超过这个时长由清理任务回收
    upload_abandon_after_seconds: int = 86_400

    # NoDecode 不可省：pydantic-settings 会在【读取环境变量的源层】就对 list 字段
    # 做 JSON 解析，失败直接抛 SettingsError——那时 field_validator 还没轮到执行。
    # 加上它才能把原始字符串交给下面的校验器处理。
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        # .env 里写的是 `a,b`，不是 JSON 数组。
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("["):
                import json

                return json.loads(s)
            return [item.strip() for item in s.split(",") if item.strip()]
        return v

    # repr=False 不可省：computed_field 默认进 repr()，而连接串里带明文密码。
    # 少了它，任何一次 print(settings) 或异常回显都会把数据库密码写进日志。
    @computed_field(repr=False)  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        return str(
            PostgresDsn.build(
                scheme="postgresql+asyncpg",
                username=self.postgres_user,
                password=self.postgres_password.get_secret_value(),
                host=self.postgres_host,
                port=self.postgres_port,
                path=self.postgres_db,
            )
        )

    # 同上：现在没密码，接了带鉴权的 Redis 就有了。
    @computed_field(repr=False)  # type: ignore[prop-decorator]
    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        return self.env in ("staging", "prod")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    if settings.is_production and "dev-only" in settings.secret_key.get_secret_value():
        raise RuntimeError("生产环境检测到默认 SECRET_KEY，拒绝启动")
    return settings
