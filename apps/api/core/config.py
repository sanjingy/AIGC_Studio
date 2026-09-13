"""应用配置。

所有配置来自环境变量，代码中不出现任何硬编码的密钥、地址或价格。
价格与业务系数不放这里——它们属于 `pricing_rules` / `model_pricing` 表，
需要热更新（ADR-014）。这里只放启动期就必须确定的基础设施配置。
"""

import uuid
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import (
    Field,
    PostgresDsn,
    SecretStr,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

#: 本机出图那条路径上，等结果之外还要留给收尾的时间（秒）。
#:
#: 一个 Arq job 里发生的不只是"等图"：还有配额预检、把字节写进对象存储、
#: 登记资产、结算。等待预算 + 这段预留必须留在 `job_timeout` 之内，
#: 否则 Arq 会在图刚回来、还没写进资产库的时候把整个 job 杀掉。
LOCAL_IMAGE_WORKER_RESERVE_SECONDS = 120


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

    # 凭证密文字段（provider_credentials.key_encrypted）的对称密钥，
    # base64 编码的 32 字节。与 SECRET_KEY 分开：两者的轮换节奏不同——
    # 换 SECRET_KEY 只是让人重新登录，换这把钥匙要重新加密整张表。
    # 留空时 apps/api/core/crypto.py 从 SECRET_KEY 派生（仅限非生产）。
    credential_encryption_key: SecretStr = SecretStr("")

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

    # --- Worker ---
    # 单个 job 的上限（Arq 的 `job_timeout`）。**从这里读而不是写死在
    # `worker/main.py`**：本机出图要在一个 job 里等几分钟，两个数必须能
    # 互相校验，写死在两个文件里的数迟早会对不上（见 `_check_local_cli`）。
    worker_job_timeout_seconds: int = Field(default=900, ge=60, le=7200)

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

    # --- 上游 Provider 凭据 ---
    # 用 SecretStr：打印配置对象时不会泄露。绝不进代码、不进日志、不进库。
    deepseek_api_key: SecretStr = SecretStr("")
    ark_api_key: SecretStr = SecretStr("")
    dashscope_api_key: SecretStr = SecretStr("")

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

    # --- 本机运行时（试点，默认关闭）---
    #
    # 用户的 Codex / Claude CLI 登录留在他自己的桌面上，服务端不持有、不复制
    # 任何 Provider 登录态。桌面连接器主动**出站**拉取请求、在本地跑官方
    # CLI、把结果回传。服务端不监听桌面、不开远程 shell。
    #
    # 默认必须是关的：这条路径绕开了 Gateway 的 failover，
    # 开着而没人看管等于给一条没有成本可见性的旁路。
    local_cli_enabled: bool = False
    # 只对这一个 org 生效。留空 = 未配置，`local_cli_enabled=true` 时拒绝启动。
    local_cli_org_id: uuid.UUID | None = None
    # 显式项目白名单，不许留空、不许写 "*"。试点的范围必须能被一眼看完。
    local_cli_project_ids: Annotated[list[uuid.UUID], NoDecode] = Field(default_factory=list)
    # **文本**走哪个本机 CLI。图像固定走 Codex（见下一条），两者可以是
    # 不同的进程：本用户的 Claude 订阅只有 Opus 且不能生图，Codex 才有
    # 原生 image_gen。默认按用户实际持有的那一份定：文本 = claude。
    local_cli_provider: Literal["codex", "claude"] = "claude"
    # 模型 id **不在服务端配**：它是连接器启动时的 `--model` 参数，
    # 由本机那个人自己决定（他才知道自己的订阅里有哪几个模型）。
    # 在这里再加一个字段只会多出一份能和现实不一致的真相。
    # **图像**走哪个本机 CLI。只有 codex：本机三个 CLI 里只有它有原生生图
    # （`codex features list` → image_generation stable true），
    # Claude 没有（逐条核过 `--help`）。Gemini **本轮未验证、未接入**：
    # 本机没装 gemini，一次实测都没做过，所以这里不对它的能力或计费形态
    # 下任何结论——没验过就不接入，仅此而已。见 15_LOCAL_RUNTIME.md §8-7。
    # 写成一个字面量枚举而不是 bool，是为了将来多一家时不用改调用点。
    local_cli_image_provider: Literal["codex"] = "codex"
    # 桥接令牌：只授权 poll / heartbeat / result 三个端点，
    # 不是登录态，也换不出登录态。
    local_cli_token: SecretStr = SecretStr("")
    local_cli_timeout_seconds: int = Field(default=180, ge=30, le=900)
    # 出图比文本慢一个量级（模型要先决定调工具、再等图片回来），
    # 所以单独一个上限，不共用文本那个。
    #
    # **它必须留在 Arq 的 `job_timeout` 之内**（见下面的 `_check_local_cli`）：
    # 出图这条是在 Worker 的一个 job 里等结果的，等超过 job_timeout，
    # Arq 会先把 job 干掉——用户看到"失败"，而他自己那台电脑还在画，
    # 订阅额度照烧，图画出来也没人接。
    local_cli_image_timeout_seconds: int = Field(default=420, ge=60, le=1800)
    # staging 想开必须再显式点一次头；prod 无论如何都不许开。
    local_cli_allow_staging: bool = False

    @field_validator("local_cli_org_id", mode="before")
    @classmethod
    def _blank_uuid_is_none(cls, v: object) -> object:
        # `.env.example` 里这一行是 `LOCAL_CLI_ORG_ID=`（留空 = 没配）。
        # 不转成 None 的话，空串会被当成一个非法 UUID，整个进程起不来——
        # 而它明明是"这个功能没开"的正常写法。
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("local_cli_project_ids", mode="before")
    @classmethod
    def _split_uuid_csv(cls, v: object) -> object:
        # 与 cors_origins 同样的理由：.env 里写的是 `a,b` 而不是 JSON 数组，
        # 而 NoDecode 之后原始字符串才轮得到这里。
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("["):
                import json

                return json.loads(s)
            return [item.strip() for item in s.split(",") if item.strip()]
        return v

    @model_validator(mode="after")
    def _check_local_cli(self) -> "Settings":
        """开着但配不全 = 拒绝启动。

        半配置的试点比不配置更危险：`applies_to` 会因为缺 org/project 而
        永远返回 False，运行时看起来"没启用"，但桥接端点已经挂着一个
        空令牌在那里等人来试。宁可启动就炸。
        """
        if not self.local_cli_enabled:
            return self
        if self.env == "prod":
            raise ValueError("LOCAL_CLI_ENABLED 不允许在 prod 打开：试点不进生产")
        if self.env == "staging" and not self.local_cli_allow_staging:
            raise ValueError("staging 要开本地 CLI 试点必须同时设 LOCAL_CLI_ALLOW_STAGING=true")
        if self.local_cli_org_id is None:
            raise ValueError("LOCAL_CLI_ENABLED=true 时必须配 LOCAL_CLI_ORG_ID")
        if not self.local_cli_project_ids:
            raise ValueError("LOCAL_CLI_ENABLED=true 时必须配非空的 LOCAL_CLI_PROJECT_IDS")
        if len(self.local_cli_token.get_secret_value()) < 32:
            raise ValueError("LOCAL_CLI_TOKEN 至少 32 个随机字符")
        # 跨层超时：出图是在一个 Arq job 里等结果的。等待预算 + 收尾预留
        # 必须留在 `job_timeout` 之内，否则 Arq 先把 job 杀掉——
        # 用户看到失败，而他那台电脑还在画，订阅额度照烧、图也没人接。
        budget = self.local_cli_image_timeout_seconds + LOCAL_IMAGE_WORKER_RESERVE_SECONDS
        if budget > self.worker_job_timeout_seconds:
            raise ValueError(
                f"LOCAL_CLI_IMAGE_TIMEOUT_SECONDS({self.local_cli_image_timeout_seconds})"
                f" + 收尾预留({LOCAL_IMAGE_WORKER_RESERVE_SECONDS}) 超过了"
                f" WORKER_JOB_TIMEOUT_SECONDS({self.worker_job_timeout_seconds})；"
                "调大 Worker 的 job 上限，或调小本机出图上限"
            )
        return self

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
