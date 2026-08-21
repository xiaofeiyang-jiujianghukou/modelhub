"""
数据库结构迁移（guarded ALTER TABLE，幂等可重跑）

SQLite 的限制：ADD COLUMN 支持常量默认值，但不支持 UNIQUE/PK/无默认非空列，
也不支持 DROP/MODIFY COLUMN。因此本迁移仅做"加列"，逐列检查存在性。

用法:
    python scripts/migrate_db.py        # 对默认 DB 执行迁移
    python scripts/migrate_db.py --check  # 仅检查是否需迁移
"""

import asyncio
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text  # noqa: E402

from src.database import engine  # noqa: E402

# (表名, [(列名, DDL)]) —— DDL 须为 ADD COLUMN 合法子句
MIGRATIONS = [
    ("providers", [
        ("last_synced_at", "DATETIME"),
        ("last_sync_status", "VARCHAR(20)"),
        ("last_sync_error", "TEXT"),
    ]),
    ("models", [
        ("price_source", "VARCHAR(10) NOT NULL DEFAULT 'default'"),
        ("last_synced_at", "DATETIME"),
        ("synced_from", "VARCHAR(100)"),
        ("price_currency", "VARCHAR(3) NOT NULL DEFAULT 'USD'"),
        ("alias", "VARCHAR(100)"),
    ]),
    ("model_references", [
        ("price_currency", "VARCHAR(3) NOT NULL DEFAULT 'USD'"),
    ]),
    ("request_logs", [
        ("cache_hit_tokens", "INTEGER"),
        ("cache_miss_tokens", "INTEGER"),
    ]),
    ("api_keys", [
        ("key_enc", "TEXT"),
    ]),
]


async def _existing_columns(conn, table: str) -> set[str]:
    """返回表现有列名集合（表不存在返回空集）"""
    is_sqlite = "sqlite" in str(conn.engine.url)
    if is_sqlite:
        rows = (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()
        return {r[1] for r in rows}
    rows = await conn.execute(
        text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
        {"t": table},
    )
    return {r[0] for r in rows}


async def _tables_exist(conn) -> set[str]:
    """返回当前数据库已有表名集合"""
    is_sqlite = "sqlite" in str(conn.engine.url)
    if is_sqlite:
        rows = (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).fetchall()
        return {r[0] for r in rows}
    rows = await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))
    return {r[0] for r in rows}


async def _drop_table_if_exists(conn, table: str) -> bool:
    """删除已废弃的表（如 model_aliases），表不存在时跳过"""
    tables = await _tables_exist(conn)
    if table in tables:
        await conn.execute(text(f"DROP TABLE {table}"))
        return True
    return False


async def _rename_column(conn, table: str, old: str, new: str) -> bool:
    """SQLite 3.25+ 支持 RENAME COLUMN；旧列存在且新列不存在时执行"""
    existing = await _existing_columns(conn, table)
    if old in existing and new not in existing:
        await conn.execute(text(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}"))
        return True
    return False


async def run_db_migrations(engine=engine) -> list[str]:
    """执行待应用迁移，返回已应用的列名列表（幂等）"""
    applied: list[str] = []
    async with engine.begin() as conn:
        tables = await _tables_exist(conn)
        if "models" in tables:
            if await _rename_column(conn, "models", "id", "model"):
                applied.append("models.id->model")
        if await _drop_table_if_exists(conn, "model_aliases"):
            applied.append("model_aliases.dropped")
        for table, columns in MIGRATIONS:
            if table not in tables:
                continue
            existing = await _existing_columns(conn, table)
            for name, ddl in columns:
                if name in existing:
                    continue
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                applied.append(f"{table}.{name}")
    return applied


async def needs_migration(engine=engine) -> bool:
    """检查是否存在待应用迁移"""
    async with engine.begin() as conn:
        tables = await _tables_exist(conn)
        for table, columns in MIGRATIONS:
            if table not in tables:
                continue
            existing = await _existing_columns(conn, table)
            for name, _ddl in columns:
                if name not in existing:
                    return True
    return False


if __name__ == "__main__":
    if "--check" in sys.argv:
        need = asyncio.run(needs_migration())
        print("需要迁移" if need else "无需迁移（结构已是最新）")
    else:
        applied = asyncio.run(run_db_migrations())
        if applied:
            print(f"✅ 已应用 {len(applied)} 列: {', '.join(applied)}")
        else:
            print("✅ 无需迁移（结构已是最新）")
