"""FastAPI 应用：闸机整日清单换交与展期汇总查询。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Path

from . import repository
from .db import get_connection, init_db
from .schemas import ReplaceDayListRequest


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="展会联票分组统计服务",
    version="1.0.0",
    description=(
        "接收闸机整日清单（匿名券号 + 馆号），按展期与日期存入 SQLite。"
        "一张券在同一展期只计一次，归入它去过的全部馆组成的那一组。"
    ),
    lifespan=lifespan,
)

DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.put("/periods/{exhibition_id}/dates/{day}/records")
def replace_day_records(
    payload: ReplaceDayListRequest,
    exhibition_id: str = Path(..., description="展期标识"),
    day: str = Path(..., pattern=DATE_PATTERN, description="日期 YYYY-MM-DD"),
) -> dict:
    """换交某日闸机整日清单（全量替换；可交空清单）。

    清单与券数在同一事务内一同生效；换交失败不改任何数据。
    """
    entries = [(item.ticket_no, item.hall) for item in payload.entries]
    conn = get_connection()
    try:
        result = repository.replace_day_list(conn, exhibition_id, day, entries)
    except Exception as exc:  # 事务内部已回滚，这里转为 500，保证数据不变
        raise HTTPException(status_code=500, detail=f"换交失败，数据未变更: {exc}") from exc
    finally:
        conn.close()
    result["message"] = "换交生效"
    return result


@app.get("/periods/{exhibition_id}/summary")
def get_exhibition_summary(exhibition_id: str) -> dict:
    """查询展期汇总：直接读取已物化的各组券数（换交期间可读到上一版已生效数据）。"""
    conn = get_connection()
    try:
        groups = repository.get_summary(conn, exhibition_id)
    finally:
        conn.close()
    return {"exhibition_id": exhibition_id, "groups": groups}


@app.get("/periods/{exhibition_id}/dates/{day}/records")
def list_day_records(
    exhibition_id: str = Path(...),
    day: str = Path(..., pattern=DATE_PATTERN),
) -> dict:
    """读取某日已生效的清单（核对用）。"""
    conn = get_connection()
    try:
        entries = repository.get_day_records(conn, exhibition_id, day)
    finally:
        conn.close()
    return {"exhibition_id": exhibition_id, "day": day, "entries": entries}


@app.get("/periods/{exhibition_id}/tickets/{ticket_no}")
def get_ticket_group(exhibition_id: str, ticket_no: str) -> dict:
    """查询单张券当前归入的馆组合（核对用）。"""
    conn = get_connection()
    try:
        halls = repository.get_ticket_group(conn, exhibition_id, ticket_no)
    finally:
        conn.close()
    if halls is None:
        raise HTTPException(status_code=404, detail="该展期内没有此券的过闸记录")
    return {"exhibition_id": exhibition_id, "ticket_no": ticket_no, "halls": halls}
