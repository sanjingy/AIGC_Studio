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
# 目前还没有业务模型，S2 起逐步加入，形如：
#     from apps.api.modules.auth import models as _auth_models
# 这类 import 只为副作用（把表注册进 metadata），需要标注忽略未使用告警。
# --------------------------------------------------------------------

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


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
