"""本地 CLI 试点的配置与作用域判定。

试点的安全性几乎全部压在这两件事上：**默认关**，以及**开了也只对
显式列出的 org + project 生效**。所以这两件事必须有单独的用例挡着。
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from apps.api.core import config
from apps.api.core.config import Settings
from apps.api.modules.local_runtime import service

ORG = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER_ORG = uuid.UUID("22222222-2222-2222-2222-222222222222")
PROJECT = uuid.UUID("33333333-3333-3333-3333-333333333333")
OTHER_PROJECT = uuid.UUID("44444444-4444-4444-4444-444444444444")

TOKEN = "x" * 40


def _enabled(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "env": "local",
        "local_cli_enabled": True,
        "local_cli_org_id": ORG,
        "local_cli_project_ids": [PROJECT],
        "local_cli_provider": "codex",
        "local_cli_token": TOKEN,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class TestDefaults:
    def test_disabled_by_default(self) -> None:
        s = Settings()
        assert s.local_cli_enabled is False
        assert s.local_cli_org_id is None
        assert s.local_cli_project_ids == []

    def test_no_model_id_lives_in_server_config(self) -> None:
        """模型 id 不在服务端配。

        它是连接器启动时的 `--model` 参数——本机那个人才知道自己的订阅里
        有哪几个模型。在服务端再存一份只会多出一份能和现实不一致的真相。
        """
        assert not [f for f in Settings.model_fields if "local_cli" in f and "model" in f]

    def test_token_is_not_reprd(self) -> None:
        """令牌与数据库密码一个级别，打印配置对象时不能泄露。"""
        s = _enabled(local_cli_token="token-that-must-not-appear-anywhere-1234")
        assert "token-that-must-not-appear" not in repr(s)
        assert "token-that-must-not-appear" not in str(s)


class TestRefusesIncompleteConfig:
    """半配置比不配置更危险：端点挂着、令牌是空的、看起来又像"没启用"。"""

    def test_missing_org(self) -> None:
        with pytest.raises(ValidationError, match="LOCAL_CLI_ORG_ID"):
            _enabled(local_cli_org_id=None)

    def test_empty_project_list(self) -> None:
        with pytest.raises(ValidationError, match="LOCAL_CLI_PROJECT_IDS"):
            _enabled(local_cli_project_ids=[])

    def test_short_token(self) -> None:
        with pytest.raises(ValidationError, match="至少 32"):
            _enabled(local_cli_token="too-short")

    def test_prod_is_refused_outright(self) -> None:
        with pytest.raises(ValidationError, match="prod"):
            _enabled(env="prod")

    def test_staging_needs_a_second_explicit_opt_in(self) -> None:
        with pytest.raises(ValidationError, match="LOCAL_CLI_ALLOW_STAGING"):
            _enabled(env="staging")
        # 显式点头之后才允许
        assert _enabled(env="staging", local_cli_allow_staging=True).local_cli_enabled

    def test_timeout_is_bounded(self) -> None:
        with pytest.raises(ValidationError):
            _enabled(local_cli_timeout_seconds=5)
        with pytest.raises(ValidationError):
            _enabled(local_cli_timeout_seconds=100_000)

    def test_image_timeout_is_separately_bounded(self) -> None:
        """出图比文本慢一个量级（模型要先决定调工具、再等图片回来），
        所以它有自己的上限，不共用文本那个。"""
        with pytest.raises(ValidationError):
            _enabled(local_cli_image_timeout_seconds=5)
        with pytest.raises(ValidationError):
            _enabled(local_cli_image_timeout_seconds=100_000)


class TestCrossLayerTimeout:
    """出图的等待预算必须留在 Arq 的 `job_timeout` 之内。

    超了会发生一件**不会报错**的事：Arq 先把 job 杀掉，用户看到"失败"，
    而他自己那台电脑还在画——订阅额度照烧，图画出来也没人接。
    两个数以前分别写死在 `config.py` 和 `worker/main.py` 里，谁都不知道对方。
    """

    def test_an_image_timeout_that_outlives_the_job_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="WORKER_JOB_TIMEOUT_SECONDS"):
            _enabled(local_cli_image_timeout_seconds=1800, worker_job_timeout_seconds=900)

    def test_the_default_pair_leaves_room_for_the_upload(self) -> None:
        s = _enabled()
        assert (
            s.local_cli_image_timeout_seconds + config.LOCAL_IMAGE_WORKER_RESERVE_SECONDS
            <= s.worker_job_timeout_seconds
        )

    def test_a_bigger_job_timeout_allows_a_bigger_image_timeout(self) -> None:
        """要跑更长的生成就把 Worker 的上限一起调大——两个数绑在一起改。"""
        s = _enabled(local_cli_image_timeout_seconds=1500, worker_job_timeout_seconds=1800)
        assert s.local_cli_image_timeout_seconds == 1500

    def test_worker_reads_the_job_timeout_from_settings(self) -> None:
        """`worker/main.py` 必须读配置而不是自己写一个数——
        写死在两个文件里的数迟早对不上，而对不上不会有任何报错。"""
        from worker.main import WorkerSettings

        assert WorkerSettings.job_timeout == Settings().worker_job_timeout_seconds


class TestImageProvider:
    def test_images_only_ever_go_to_codex(self) -> None:
        """本机三个 CLI 里只有 Codex 有原生生图（`codex features list`）。

        Claude 没有；Gemini CLI 的 Imagen/Veo 是 MCP 外接的**付费 API**，
        不是会员权益。所以这个字段的取值集合只有一个元素——
        放开它等于允许配出一条点下去必然失败的路径。
        """
        assert _enabled().local_cli_image_provider == "codex"
        with pytest.raises(ValidationError):
            _enabled(local_cli_image_provider="claude")

    def test_text_and_image_can_be_different_processes(self) -> None:
        """文本走 Claude、图片走 Codex 是**正常配置**，不是异常。

        本用户的 Claude 订阅只有 Opus 且不能生图，Codex 才有 image_gen。
        """
        s = _enabled(local_cli_provider="claude")
        assert s.local_cli_provider == "claude"
        assert s.local_cli_image_provider == "codex"


class TestProjectIdsParsing:
    def test_csv_from_env_style_string(self) -> None:
        """.env 里写的是 `a,b` 而不是 JSON 数组。"""
        s = _enabled(local_cli_project_ids=f"{PROJECT}, {OTHER_PROJECT}")
        assert s.local_cli_project_ids == [PROJECT, OTHER_PROJECT]

    def test_json_array_also_works(self) -> None:
        s = _enabled(local_cli_project_ids=f'["{PROJECT}"]')
        assert s.local_cli_project_ids == [PROJECT]


class TestAppliesTo:
    """命中条件必须是四个 AND，任何一个松了都会让别的租户被卷进来。"""

    def test_false_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(service, "get_settings", Settings)
        assert service.applies_to(ORG, PROJECT) is False

    def test_true_for_the_configured_pair(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(service, "get_settings", _enabled)
        assert service.applies_to(ORG, PROJECT) is True

    def test_false_for_another_org(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(service, "get_settings", _enabled)
        assert service.applies_to(OTHER_ORG, PROJECT) is False

    def test_false_for_a_project_outside_the_whitelist(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(service, "get_settings", _enabled)
        assert service.applies_to(ORG, OTHER_PROJECT) is False

    def test_false_without_a_project(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """不挂项目的调用（资产库里那条独立生成角色档案的路径）走原实现。"""
        monkeypatch.setattr(service, "get_settings", _enabled)
        assert service.applies_to(ORG, None) is False
        assert service.applies_to(None, PROJECT) is False
        assert service.applies_to(None, None) is False


class TestImageAppliesTo:
    """出图与文本共用同一份白名单：试点的范围是"这个项目"，不是"这个能力"。

    分成两份白名单看着更灵活，实际上是两份会慢慢分叉的真相——
    而分叉的那一天，某个项目会在用户不知情的情况下多出一条能出图的路。
    """

    def test_same_scope_as_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(service, "get_settings", _enabled)
        assert service.image_applies_to(ORG, PROJECT) is True
        assert service.image_applies_to(OTHER_ORG, PROJECT) is False
        assert service.image_applies_to(ORG, OTHER_PROJECT) is False
        assert service.image_applies_to(ORG, None) is False

    def test_false_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(service, "get_settings", Settings)
        assert service.image_applies_to(ORG, PROJECT) is False
