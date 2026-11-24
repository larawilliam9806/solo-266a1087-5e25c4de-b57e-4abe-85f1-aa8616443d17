"""SQLite 连接管理与建表。

并发模型
--------
* 开启 WAL 模式：换交（写事务）进行期间，查询仍可读到上一个已提交版本，
  不会被写事务阻塞（快照隔离）。
* 换交使用 ``BEGIN IMMEDIATE`` 立即取得写锁，把校验与写入放进同一个
  原子事务：要么清单与汇总一起生效，要么失败整体回滚、数据不变。
* ``busy_timeout`` 让并发写请求等待对方提交而不是立刻报数据库锁。
"""
from __future__ import annotations

import sqlite3

from . import config

SCHEMA = """
PRAGMA journal_mode=WAL;

-- 闸机原始清单：某展期某日，某券在某馆有一条过闸记录。
-- 同一券同日同馆重复上报只保留一条。
CREATE TABLE IF NOT EXISTS records (
    exhibition_id TEXT NOT NULL,
    day           TEXT NOT NULL,           -- ISO 日期 YYYY-MM-DD
    ticket_no     TEXT NOT NULL,
    hall          TEXT NOT NULL,
    PRIMARY KEY (exhibition_id, day, ticket_no, hall)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_records_lookup
    ON records (exhibition_id, ticket_no, hall);

-- 逐券物化结果：一张券在某展期内去过的全部馆（去重、排序的 JSON 数组）。
-- 每组汇总随换交在同一事务内即时维护。
CREATE TABLE IF NOT EXISTS ticket_halls (
    exhibition_id TEXT NOT NULL,
    ticket_no     TEXT NOT NULL,
    halls_json    TEXT NOT NULL,
    PRIMARY KEY (exhibition_id, ticket_no)
) WITHOUT ROWID;

-- 展期汇总物化视图：各组（馆组合）当前的券数。
CREATE TABLE IF NOT EXISTS group_summary (
    exhibition_id TEXT NOT NULL,
    halls_json    TEXT NOT NULL,
    ticket_count  INTEGER NOT NULL,
    PRIMARY KEY (exhibition_id, halls_json)
) WITHOUT ROWID;
"""


def get_connection() -> sqlite3.Connection:
    """每个请求一条独立连接（SQLite 连接不能跨线程共享）。"""
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(config.DB_PATH),
        timeout=config.BUSY_TIMEOUT_SECONDS,
        isolation_level=None,  # 自动提交模式，事务由代码显式控制
    )
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=%d" % (config.BUSY_TIMEOUT_SECONDS * 1000))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """启动时建表（幂等）。"""
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()
