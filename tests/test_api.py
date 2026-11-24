"""功能测试：分组规则、换交、空清单、删日保留、校验失败回滚。"""
from __future__ import annotations

import importlib
import os
import tempfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    tmpdir = tempfile.mkdtemp(prefix="ticket-test-")
    db_path = os.path.join(tmpdir, "test.db")
    monkeypatch.setenv("DB_PATH", db_path)

    import app.config
    import app.db
    import app.main
    importlib.reload(app.config)
    importlib.reload(app.db)
    importlib.reload(app.main)

    with TestClient(app.main.app) as c:
        yield c


@pytest.fixture()
def helpers(client):
    def put(period, day, entries):
        return client.put(
            f"/periods/{period}/dates/{day}/records",
            json={"entries": [{"ticket_no": t, "hall": h} for t, h in entries]},
        )

    def summary(period):
        return client.get(f"/periods/{period}/summary").json()["groups"]

    def group_map(period):
        return {tuple(g["halls"]): g["ticket_count"] for g in summary(period)}

    return put, summary, group_map


def test_single_ticket_single_hall_counts_once(helpers):
    put, _, group_map = helpers
    # 同一张券同一天在甲馆刷了多次，也只计一张、归甲馆组
    r = put("p1", "2026-11-01", [("T1", "甲"), ("T1", "甲"), ("T2", "甲")])
    assert r.status_code == 200
    assert r.json()["unique_rows"] == 2
    assert group_map("p1") == {("甲",): 2}


def test_ticket_grouped_by_union_of_all_halls_across_days(helpers):
    put, _, group_map = helpers
    # T1 两天分别去甲、乙 -> 归入 [甲, 乙] 组，整展期只计一次
    put("p1", "2026-11-01", [("T1", "甲"), ("T2", "甲")])
    put("p1", "2026-11-02", [("T1", "乙"), ("T3", "乙")])
    # 馆组合按馆号规范化排序（中文按 Unicode 码位：乙 < 甲）
    assert group_map("p1") == {
        ("甲",): 1,
        ("乙",): 1,
        ("乙", "甲"): 1,
    }


def test_empty_list_replaces_and_removes_day(client, helpers):
    put, summary, _ = helpers
    put("p1", "2026-11-01", [("T1", "甲")])
    r = put("p1", "2026-11-01", [])  # 可交空清单
    assert r.status_code == 200
    assert summary("p1") == []
    entries = client.get("/periods/p1/dates/2026-11-01/records").json()["entries"]
    assert entries == []


def test_delete_day_keeps_hall_seen_on_another_day(helpers):
    put, _, group_map = helpers
    # T1 两日都去过甲馆；删掉其中一日，甲馆仍计入组合
    put("p1", "2026-11-01", [("T1", "甲"), ("T1", "乙")])
    put("p1", "2026-11-02", [("T1", "甲")])
    assert group_map("p1") == {("乙", "甲"): 1}

    put("p1", "2026-11-01", [])  # 删去 11-01 的记录（含乙馆）
    assert group_map("p1") == {("甲",): 1}


def test_replacement_is_full_not_merge(helpers):
    put, _, group_map = helpers
    put("p1", "2026-11-01", [("T1", "甲"), ("T2", "甲")])
    # 整日换交：新清单只有 T3，T1/T2 当天记录被整体替换
    put("p1", "2026-11-01", [("T3", "乙")])
    assert group_map("p1") == {("乙",): 1}


def test_only_affected_tickets_are_recomputed(helpers):
    put, _, group_map = helpers
    put("p1", "2026-11-01", [("T1", "甲"), ("T2", "乙")])
    put("p1", "2026-11-02", [("T1", "乙")])  # T1 -> [甲,乙]
    # 换交 11-02 为另一张券，只重算 T1 与 T3；T2 不受影响
    put("p1", "2026-11-02", [("T3", "甲")])
    assert group_map("p1") == {("甲",): 2, ("乙",): 1}


def test_periods_are_isolated(helpers):
    put, summary, group_map = helpers
    put("p1", "2026-11-01", [("T1", "甲")])
    put("p2", "2026-11-01", [("T1", "乙")])
    assert group_map("p1") == {("甲",): 1}
    assert group_map("p2") == {("乙",): 1}
    assert summary("unknown") == []


def test_invalid_payload_rejected_without_data_change(client, helpers):
    put, _, group_map = helpers
    put("p1", "2026-11-01", [("T1", "甲")])
    # 空券号 -> 422，且在事务开始前被拦截，数据不变
    r = put("p1", "2026-11-01", [("  ", "甲")])
    assert r.status_code == 422
    assert group_map("p1") == {("甲",): 1}
    # 错误日期格式 -> 422
    r = client.put(
        "/periods/p1/dates/11-01/records",
        json={"entries": []},
    )
    assert r.status_code == 422
    assert group_map("p1") == {("甲",): 1}


def test_duplicate_rows_in_payload_deduped(helpers):
    put, _, group_map = helpers
    r = put("p1", "2026-11-01", [("T1", "甲"), ("T1", "甲"), ("T1", "甲")])
    assert r.json()["unique_rows"] == 1
    assert group_map("p1") == {("甲",): 1}


def test_ticket_disappearing_and_reappearing(helpers):
    put, summary, group_map = helpers
    put("p1", "2026-11-01", [("T1", "甲")])
    put("p1", "2026-11-02", [("T1", "乙")])
    put("p1", "2026-11-01", [])
    put("p1", "2026-11-02", [])
    assert summary("p1") == []
    # 重新出现
    put("p1", "2026-11-03", [("T1", "丙")])
    assert group_map("p1") == {("丙",): 1}
