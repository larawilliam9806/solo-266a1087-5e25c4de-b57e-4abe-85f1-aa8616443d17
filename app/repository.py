"""数据访问与“整日清单换交”的事务逻辑。"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from .schemas import decode_halls, encode_halls


def _decrement_group(conn: sqlite3.Connection, exhibition_id: str, halls_json: str) -> None:
    conn.execute(
        """
        UPDATE group_summary
           SET ticket_count = ticket_count - 1
         WHERE exhibition_id = ? AND halls_json = ?
        """,
        (exhibition_id, halls_json),
    )
    conn.execute(
        """
        DELETE FROM group_summary
         WHERE exhibition_id = ? AND halls_json = ? AND ticket_count <= 0
        """,
        (exhibition_id, halls_json),
    )


def _increment_group(conn: sqlite3.Connection, exhibition_id: str, halls_json: str) -> None:
    conn.execute(
        """
        INSERT INTO group_summary (exhibition_id, halls_json, ticket_count)
        VALUES (?, ?, 1)
        ON CONFLICT(exhibition_id, halls_json)
        DO UPDATE SET ticket_count = ticket_count + 1
        """,
        (exhibition_id, halls_json),
    )


def _rebuild_ticket(conn: sqlite3.Connection, exhibition_id: str, ticket_no: str) -> None:
    """根据 records 全量重算单张券的馆组合，并同步汇总表。

    只处理一张券，因此换交某日时只需重算当天新旧清单涉及的券，
    与其他日期、其他券无关。
    """
    old_row = conn.execute(
        "SELECT halls_json FROM ticket_halls WHERE exhibition_id = ? AND ticket_no = ?",
        (exhibition_id, ticket_no),
    ).fetchone()
    if old_row is not None:
        _decrement_group(conn, exhibition_id, old_row["halls_json"])

    halls = [
        row["hall"]
        for row in conn.execute(
            """
            SELECT DISTINCT hall
              FROM records
             WHERE exhibition_id = ? AND ticket_no = ?
             ORDER BY hall
            """,
            (exhibition_id, ticket_no),
        )
    ]

    if halls:
        new_json = encode_halls(halls)
        conn.execute(
            """
            INSERT INTO ticket_halls (exhibition_id, ticket_no, halls_json)
            VALUES (?, ?, ?)
            ON CONFLICT(exhibition_id, ticket_no)
            DO UPDATE SET halls_json = excluded.halls_json
            """,
            (exhibition_id, ticket_no, new_json),
        )
        _increment_group(conn, exhibition_id, new_json)
    else:
        # 该券在本展期已无任何过闸记录，移除其物化行。
        conn.execute(
            "DELETE FROM ticket_halls WHERE exhibition_id = ? AND ticket_no = ?",
            (exhibition_id, ticket_no),
        )


def replace_day_list(
    conn: sqlite3.Connection,
    exhibition_id: str,
    day: str,
    entries: Iterable[tuple[str, str]],
) -> dict:
    """换交某展期某日的闸机整日清单（全量替换，允许空清单）。

    整个过程在一个 ``BEGIN IMMEDIATE`` 事务内完成：
    * 清单（records）与券数（ticket_halls / group_summary）一同提交生效；
    * 任何步骤抛错都整体回滚，已生效数据保持不变；
    * 立即取得写锁，并发换交（即使是不同日期）串行化，全部生效不丢更新。
    """
    # 入参去重：同一券同馆在同一清单里重复出现只算一条。
    new_rows = {(ticket_no, hall) for ticket_no, hall in entries}
    new_tickets = {ticket_no for ticket_no, _ in new_rows}

    conn.execute("BEGIN IMMEDIATE")
    try:
        old_rows = conn.execute(
            "SELECT ticket_no, hall FROM records WHERE exhibition_id = ? AND day = ?",
            (exhibition_id, day),
        ).fetchall()
        old_tickets = {row["ticket_no"] for row in old_rows}

        # 只核算当天新旧清单涉及的券。
        affected_tickets = old_tickets | new_tickets

        conn.execute(
            "DELETE FROM records WHERE exhibition_id = ? AND day = ?",
            (exhibition_id, day),
        )
        if new_rows:
            conn.executemany(
                "INSERT OR IGNORE INTO records (exhibition_id, day, ticket_no, hall)"
                " VALUES (?, ?, ?, ?)",
                [(exhibition_id, day, ticket_no, hall) for ticket_no, hall in new_rows],
            )

        for ticket_no in sorted(affected_tickets):
            _rebuild_ticket(conn, exhibition_id, ticket_no)

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {
        "exhibition_id": exhibition_id,
        "day": day,
        "unique_rows": len(new_rows),
        "affected_tickets": len(affected_tickets),
    }


def get_summary(conn: sqlite3.Connection, exhibition_id: str) -> list[dict]:
    """直接读取汇总表，返回当前有券的各组及券数。"""
    rows = conn.execute(
        """
        SELECT halls_json, ticket_count
          FROM group_summary
         WHERE exhibition_id = ? AND ticket_count > 0
         ORDER BY halls_json
        """,
        (exhibition_id,),
    ).fetchall()
    return [
        {"halls": decode_halls(row["halls_json"]), "ticket_count": row["ticket_count"]}
        for row in rows
    ]


def get_day_records(conn: sqlite3.Connection, exhibition_id: str, day: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT ticket_no, hall
          FROM records
         WHERE exhibition_id = ? AND day = ?
         ORDER BY ticket_no, hall
        """,
        (exhibition_id, day),
    ).fetchall()
    return [{"ticket_no": row["ticket_no"], "hall": row["hall"]} for row in rows]


def get_ticket_group(conn: sqlite3.Connection, exhibition_id: str, ticket_no: str) -> list[str] | None:
    row = conn.execute(
        "SELECT halls_json FROM ticket_halls WHERE exhibition_id = ? AND ticket_no = ?",
        (exhibition_id, ticket_no),
    ).fetchone()
    return decode_halls(row["halls_json"]) if row else None
