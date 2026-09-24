"""Alembic 环境。

约定：
- 数据库 URL 从 Settings 读，不写进 alembic.ini
- 所有模型必须在 `target_metadata` 之前被 import，否则 autogenerate 会
  把没 import 的表当成"需要 DROP"——这是最容易踩的坑
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from apps.api.core.config import get_settings
from apps.api.core.models import Base

# ---- 模型注册（新增模块时必须在这里加 import）----------------------
# 漏 import 会让 autogenerate 把没注册的表当成"需要 DROP"，
# 生成一个删表的迁移。这是 Alembic 最容易踩的坑。
# 这类 import 只为副作用：把表注册进 Base.metadata。
from apps.api.modules.agent import models as _agent_models  # noqa: F401
from apps.api.modules.asset import models as _asset_models  # noqa: F401
from apps.api.modules.auth import models as _auth_models  # noqa: F401
from apps.api.modules.billing import models as _billing_models  # noqa: F401
from apps.api.modules.consistency import models as _consistency_models  # noqa: F401
from apps.api.modules.content import models as _content_models  # noqa: F401
from apps.api.modules.gateway import models as _gateway_models  # noqa: F401
from apps.api.modules.project import models as _project_models  # noqa: F401
from apps.api.modules.skill import models as _skill_models  # noqa: F401
from apps.api.modules.task import models as _task_models  # noqa: F401

# --------------------------------------------------------------------

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def _render_item(type_: str, obj: object, autogen_context: object) -> object:
    """让 autogenerate 为第三方列类型带上 import。

    默认只会写出 `pgvector.sqlalchemy.vector.VECTOR(...)` 却不加 import，
    生成的迁移一执行就 NameError。这个钩子补上模块导入。
    """
    if type_ == "type" and obj.__class__.__module__.startswith("pgvector"):
        autogen_context.imports.add("import pgvector.sqlalchemy")  # type: ignore[attr-defined]
    return False  # 交回默认渲染


def _include_object(_obj: object, name: str | None, type_: str, *_a: object) -> bool:
    # 扩展自带的表不归 Alembic 管，否则会生成删除它们的迁移
    return not (type_ == "table" and name in {"spatial_ref_sys"})


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
        render_item=_render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
        render_item=_render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
