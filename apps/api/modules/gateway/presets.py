"""供应商预设的加载与校验（ADR-039 第 3 条）。

数据在 `adapters/providers/presets.yaml`（随代码发布、容器里挂载可见）；这里只负责
把它读成不可变对象，并在**加载时**把一切不合规的条目拒掉：

- 未知字段（尤其是价格类字段——预设里不许出现价格，那是 `model_pricing` 的事）；
- 白名单外的协议（协议决定能力和适配器，见 `catalog.PROTOCOLS`）；
- 非 https、带凭据、指向私网 / 回环 / 元数据的 Base URL（与用户自填同一道
  `endpoint_url.normalize_base_url`）；
- 重复的 `preset_id`、请求体覆盖里白名单外的键。

坏一条就整份拒绝、进程起不来：带病运行的预设会把错误的 Base URL 拷进用户的连接里，
那比"启动时报错"难排查得多。
"""

from __future__ import annotations

import pathlib
from functools import lru_cache
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from adapters.providers import endpoint_url
from adapters.providers.openai_images import clean_overrides
from apps.api.core.errors import AppError
from apps.api.modules.gateway import catalog

PRESETS_PATH = (
    pathlib.Path(__file__).resolve().parents[4] / "adapters" / "providers" / "presets.yaml"
)

MAX_MODEL_ID_CHARS = 128


def check_model_id(model_id: str) -> str:
    text = (model_id or "").strip()
    if not text or len(text) > MAX_MODEL_ID_CHARS:
        raise ValueError(f"模型 ID 必须是 1–{MAX_MODEL_ID_CHARS} 个字符")
    if any(ch.isspace() for ch in text) or not text.isprintable():
        raise ValueError("模型 ID 不能含空白或控制字符")
    return text


class PresetModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str
    protocol: str
    #: 推理模型。只在厂商文档或本仓库已核实时写 true，缺省 false
    reasoning: bool = False

    @field_validator("model_id")
    @classmethod
    def _model(cls, v: str) -> str:
        return check_model_id(v)

    @field_validator("protocol")
    @classmethod
    def _protocol(cls, v: str) -> str:
        if catalog.protocol_spec(v) is None:
            raise ValueError(f"协议 {v!r} 不在白名单 {sorted(catalog.PROTOCOLS)} 里")
        return v


class Preset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    preset_id: str = Field(pattern=r"^[a-z0-9_]{2,64}$")
    label: str = Field(min_length=1, max_length=64)
    #: 空串 = 地址由用户填（自定义中转）
    base_url: str
    docs_url: str
    key_url: str | None = None
    icon: str = Field(pattern=r"^[a-z0-9_-]{1,32}$")
    models: tuple[PresetModel, ...] = ()
    #: 只有 `openai_images` 用得上；键走 `openai_images.OVERRIDE_KEYS` 白名单
    request_overrides: dict[str, Any] = Field(default_factory=dict)
    #: 这个预设的模板里有哪些协议。显式声明，模型列表为空的自定义中转也要能知道它是出图
    protocols: tuple[str, ...] = ()

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, v: str) -> str:
        if not v:
            return ""
        try:
            return endpoint_url.normalize_base_url(v)
        except AppError as exc:
            raise ValueError(exc.message) from exc

    @field_validator("docs_url", "key_url")
    @classmethod
    def _link(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith("https://"):
            raise ValueError("文档 / 取 Key 链接必须是 https://")
        return v

    @field_validator("request_overrides")
    @classmethod
    def _overrides(cls, v: dict[str, Any]) -> dict[str, Any]:
        return clean_overrides(v)

    @model_validator(mode="after")
    def _derive_protocols(self) -> Preset:
        protocols = tuple(dict.fromkeys(m.protocol for m in self.models))
        if not protocols and not self.protocols:
            raise ValueError(f"预设 {self.preset_id} 没有模型时必须写 protocols")
        for p in self.protocols:
            if catalog.protocol_spec(p) is None:
                raise ValueError(f"协议 {p!r} 不在白名单里")
        if self.request_overrides and "openai_images" not in (protocols or self.protocols):
            raise ValueError("request_overrides 只适用于 openai_images 协议")
        if not self.protocols:
            object.__setattr__(self, "protocols", protocols)
        return self


class PresetFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    presets: tuple[Preset, ...]

    @model_validator(mode="after")
    def _unique(self) -> PresetFile:
        ids = [p.preset_id for p in self.presets]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"preset_id 重复：{dupes}")
        return self


def parse(text: str) -> tuple[Preset, ...]:
    """解析一份预设 YAML。不合规抛 `ValueError`（带全部校验错误）。"""
    raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise ValueError("预设文件顶层必须是 {presets: [...]}")
    try:
        return PresetFile.model_validate(raw).presets
    except ValidationError as exc:
        raise ValueError(f"预设文件校验失败：{exc}") from exc


@lru_cache(maxsize=1)
def presets() -> tuple[Preset, ...]:
    return parse(PRESETS_PATH.read_text(encoding="utf-8"))


def get(preset_id: str) -> Preset | None:
    return next((p for p in presets() if p.preset_id == preset_id), None)
