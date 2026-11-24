"""并发测试：真实 uvicorn 进程 + 多线程同时换交/查询。

覆盖需求：
* 并发换交不同日期，即使涉及同一张券也都要生效；
* 换交期间仍可查询已生效的汇总，且每张券只计一次、汇总始终自洽；
* 换交失败不改数据。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="ticket-conc-")
    db_path = os.path.join(tmpdir, "conc.db")
    env = {**os.environ, "DB_PATH": db_path}
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "app.main:app",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--workers", "1",
        ],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            r = httpx.get(base + "/health", timeout=2)
            if r.status_code == 200:
                break
        except httpx.TransportError:
            time.sleep(0.3)
    else:
        proc.terminate()
        out = proc.stdout.read() if proc.stdout else ""
        pytest.fail(f"server did not start:\n{out}")

    yield base

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _put(base, period, day, entries):
    return httpx.put(
        f"{base}/periods/{period}/dates/{day}/records",
        json={"entries": [{"ticket_no": t, "hall": h} for t, h in entries]},
        timeout=60,
    )


def _summary(base, period):
    r = httpx.get(f"{base}/periods/{period}/summary", timeout=30)
    r.raise_for_status()
    return r.json()["groups"]


def _assert_consistent(base, period, expected_total):
    groups = _summary(base, period)
    # 每张券只计一次：各组券数之和等于券总数；不存在 0 券的组
    assert sum(g["ticket_count"] for g in groups) == expected_total
    assert all(g["ticket_count"] > 0 for g in groups)
    halls_sets = [tuple(g["halls"]) for g in groups]
    assert len(halls_sets) == len(set(halls_sets))
    return groups


def test_concurrent_replacements_different_dates_all_apply(server):
    base, period = server, "conc1"
    days = [f"2026-11-{d:02d}" for d in range(1, 9)]  # 8 个日期并发换交

    def payload_for(day_index: int):
        day = days[day_index]
        # 每张清单都含公共券 T0（涉及同一张券），再加当天专属券
        entries = [("T0", "甲" if day_index % 2 == 0 else "乙")]
        entries += [(f"T{day_index}-{k}", "甲" if k % 2 else "乙") for k in range(20)]
        return day, entries

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda i: _put(base, period, *payload_for(i)), range(8)))

    assert all(r.status_code == 200 for r in results), [r.text for r in results]

    # 8 天全部生效：专属券只在当天出现、各归单馆组；
    # 只有公共券 T0 在偶数天去甲、奇数天去乙，归入 [乙,甲] 组且整展期只计一次
    groups = _assert_consistent(base, period, expected_total=1 + 8 * 20)
    gmap = {tuple(g["halls"]): g["ticket_count"] for g in groups}
    assert gmap[("乙", "甲")] == 1
    assert gmap[("甲",)] == 8 * 10
    assert gmap[("乙",)] == 8 * 10

    # 每日清单都可完整读回
    for i, day in enumerate(days):
        got = httpx.get(f"{base}/periods/{period}/dates/{day}/records", timeout=30).json()["entries"]
        assert len(got) == 21  # T0 + 20 张专属券

    # 公共券 T0 的馆组合是各日并集（甲、乙），而不是被最后一次换交覆盖
    t0 = httpx.get(f"{base}/periods/{period}/tickets/T0", timeout=30)
    assert t0.status_code == 200
    assert t0.json()["halls"] == ["乙", "甲"]


def test_concurrent_replacement_and_reads_never_break_consistency(server):
    base, period = server, "conc2"
    _put(base, period, "2026-11-01", [(f"T{i}", "甲") for i in range(50)])

    stop = False

    def reader():
        seen_nonnegative = True
        while not stop:
            groups = _summary(base, period)
            seen_nonnegative &= all(g["ticket_count"] > 0 for g in groups)
        return seen_nonnegative

    def writer(day_index):
        day = f"2026-12-{day_index:02d}"
        entries = [(f"W{day_index}-{k}", "乙") for k in range(25)]
        r = _put(base, period, day, entries)
        return r.status_code

    with ThreadPoolExecutor(max_workers=6) as pool:
        reader_fut = pool.submit(reader)
        writer_futs = [pool.submit(writer, i) for i in range(1, 6)]
        codes = [f.result() for f in writer_futs]
        stop = True
        assert reader_fut.result() is True

    assert codes == [200] * 5
    groups = _assert_consistent(base, period, expected_total=50 + 5 * 25)
    gmap = {tuple(g["halls"]): g["ticket_count"] for g in groups}
    assert gmap[("甲",)] == 50
    assert gmap[("乙",)] == 125


def test_concurrent_replacement_same_date_last_commit_wins(server):
    base, period = server, "conc3"
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = [
            pool.submit(_put, base, period, "2026-11-01", [(f"V{i}-{k}", "甲") for k in range(10)])
            for i in range(4)
        ]
        results = [f.result() for f in futs]
    assert all(r.status_code == 200 for r in results)

    got = httpx.get(f"{base}/periods/{period}/dates/2026-11-01/records", timeout=30).json()["entries"]
    # 同一日期的并发全量换交串行提交，最终恰好保留某一份完整清单（10 条），不会混合
    assert len(got) == 10
    _assert_consistent(base, period, expected_total=10)


def test_failed_replacement_does_not_change_data(server, tmp_path):
    base, period = server, "conc4"
    _put(base, period, "2026-11-01", [("X1", "甲"), ("X2", "乙")])
    before = _summary(base, period)

    # 请求体本身合法（通过 Pydantic 校验），但日期格式非法：路由不匹配，
    # 不接触数据库，原有汇总保持不变
    r = httpx.put(
        f"{base}/periods/{period}/dates/bad-date/records",
        json={"entries": [{"ticket_no": "Z", "hall": "丙"}]},
        timeout=30,
    )
    assert r.status_code == 422
    assert _summary(base, period) == before

    # 空清单换交正常生效，再确认汇总随之变化且仍自洽
    r = _put(base, period, "2026-11-01", [])
    assert r.status_code == 200
    assert _summary(base, period) == []
