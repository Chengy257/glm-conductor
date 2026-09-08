#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.host.zcode_schedule 单元测试（v2.3.0 计划 §6，unit v23-w1）。

锚定对象：ZCode Scheduled Task Host Adapter——tasks-index.sqlite 唯一
接触面（生产写操作仅一条 SQL）。测试类映射：

    DiscoverTest            路径解析优先级：explicit > 环境变量 > 默认
    InspectAutomationTest   冻结键集 + recent_runs 倒序 / 上限 20 / 无行
                            None / DB 缺失 / automations 表缺失
    RetimeHappyPathTest     成功 retime（verified + previous 正确）；整
                            行 33 列逐列对比仅 next_run_at 变化
    RetimeGateTest          行数闸（无匹配 / 歧义多行）/ schema 闸（表
                            缺失 / 缺列）/ 时间戳闸（先于 I/O）/
                            recurring 状态闸
    RetimeBusyTest          锁持有者在场：短 busy_timeout 确定型
                            BusyError（fail-fast 有界）；长 busy_timeout
                            等待后成功
    RetimeVerificationTest  读回注入错值 → VerificationError 且已回滚
    ForbiddenSqlTest        禁止语句 token 机械锚定（§6.3 硬安全边界）

全部离线：夹具 DB 均在 tempfile.TemporaryDirectory 内（绝不触碰
~/.zcode/v2/ 真实宿主库）；宿主 schema 按实测冻结形态镜像（automations
33 列 / automation_runs 12 列）；unittest + unittest.mock，零 pytest。

运行：
    cd <repo_root> && python3 -S -m unittest tests.test_zcode_schedule -v
"""

import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime.host import zcode_schedule

AID = "auto-v23-w1"
OLD_TS = 1750000000000
NEW_TS = 1750003600000

# 宿主 automations 表夹具：33 列（2026-09-08 实测冻结形态镜像）。已知
# 关键列与 inspect 键集列逐列对齐；未逐一公开的列以同型占位列补足 33
# 列——被测适配器只依赖 REQUIRED_AUTOMATIONS_COLUMNS 五列与 inspect 键
# 集，占位列不参与断言语义（整行对比测试仅要求列数一致且逐列不变）。
AUTOMATIONS_SCHEMA = (
    ("automation_id", "TEXT"),
    ("title", "TEXT NOT NULL DEFAULT ''"),
    ("recurring", "INTEGER NOT NULL DEFAULT 0"),
    ("enabled", "INTEGER NOT NULL DEFAULT 1"),
    ("lifecycle_status", "TEXT NOT NULL DEFAULT 'active'"),
    ("next_run_at", "INTEGER"),
    ("last_run_at", "INTEGER"),
    ("run_count", "INTEGER NOT NULL DEFAULT 0"),
    ("target_task_id", "TEXT"),
    ("model", "TEXT"),
    ("provider", "TEXT"),
    ("mode", "TEXT"),
    ("thought_level", "TEXT"),
    ("schedule_rule", "TEXT"),
    ("max_runs", "INTEGER"),
    ("dispatch_status", "TEXT"),
    ("last_error", "TEXT"),
    ("prompt", "TEXT"),
    ("workspace_key", "TEXT"),
    ("source", "TEXT"),
    ("origin", "TEXT"),
    ("status", "TEXT"),
    ("priority", "INTEGER NOT NULL DEFAULT 0"),
    ("tags", "TEXT"),
    ("timeout_ms", "INTEGER"),
    ("retry_count", "INTEGER NOT NULL DEFAULT 0"),
    ("created_at", "INTEGER"),
    ("updated_at", "INTEGER"),
    ("deleted_at", "INTEGER"),
    ("last_session_id", "TEXT"),
    ("created_by", "TEXT"),
    ("notes", "TEXT"),
    ("extra_json", "TEXT"),
)
assert len(AUTOMATIONS_SCHEMA) == 33


# —— 夹具助手 ——

def _make_db(tmpdir, with_pk=True):
    """建夹具 DB：33 列 automations（with_pk 时 automation_id 为
    PRIMARY KEY，否则普通列以构造重复行场景）+ 12 列 automation_runs。
    返回 (conn, db_path)；调用方负责 close。"""
    db_path = os.path.join(tmpdir, zcode_schedule.ZCODE_TASKS_DB_FILENAME)
    conn = sqlite3.connect(db_path)
    pk = " PRIMARY KEY" if with_pk else ""
    columns = ", ".join(
        name + " " + decl + (pk if name == "automation_id" else "")
        for name, decl in AUTOMATIONS_SCHEMA)
    conn.execute("CREATE TABLE automations (%s)" % columns)
    conn.execute(
        "CREATE TABLE automation_runs ("
        "run_id TEXT PRIMARY KEY,"
        " automation_id TEXT NOT NULL DEFAULT '',"
        " workspace_key TEXT,"
        " scheduled_at INTEGER,"
        " trigger TEXT,"
        " dispatch_status TEXT,"
        " outcome TEXT,"
        " session_id TEXT,"
        " error TEXT,"
        " attempts INTEGER NOT NULL DEFAULT 0,"
        " created_at INTEGER,"
        " updated_at INTEGER)")
    conn.commit()
    return conn, db_path


def _insert_automation(conn, automation_id=AID, recurring=1,
                       next_run_at=OLD_TS):
    """插一行健康 recurring 样本（title / prompt / model 等占位串）。"""
    conn.execute(
        "INSERT INTO automations (automation_id, title, recurring,"
        " enabled, lifecycle_status, next_run_at, run_count,"
        " target_task_id, model, provider, mode, thought_level,"
        " schedule_rule, prompt, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (automation_id, "占位标题", recurring, 1, "active", next_run_at,
         3, "task-v23-w1", "glm-5.3", "bigmodel", "scheduled", "medium",
         '{"kind": "every_days", "value": 1}', "占位 prompt 正文",
         OLD_TS - 86400000, OLD_TS - 3600000))
    conn.commit()


def _insert_run(conn, automation_id=AID, run_id="run-1",
                scheduled_at=OLD_TS, session_id="sess-1", outcome="ok"):
    """插一条 automation_runs 记录（12 列全给值）。"""
    conn.execute(
        "INSERT INTO automation_runs (run_id, automation_id,"
        " workspace_key, scheduled_at, trigger, dispatch_status, outcome,"
        " session_id, error, attempts, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, automation_id, "ws-v23-w1", scheduled_at, "schedule",
         "dispatched", outcome, session_id, None, 0, scheduled_at,
         scheduled_at))
    conn.commit()


def _full_row(db_path, automation_id=AID):
    """绕过被测 API 直读整行（33 列 tuple；整行对比断言用）。"""
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT * FROM automations WHERE automation_id = ?",
            (automation_id,)).fetchone()
    finally:
        conn.close()


def _next_run_at_of(db_path, automation_id=AID):
    """绕过被测 API 直读 next_run_at 当前值（回滚断言用）。"""
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT next_run_at FROM automations WHERE automation_id = ?",
            (automation_id,)).fetchone()[0]
    finally:
        conn.close()


class ScheduleCase(unittest.TestCase):
    """tempdir 基座：每用例独立 scratch 目录。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.scratch = tmp.name


# —— 路径解析 ——

class DiscoverTest(ScheduleCase):

    def test_discover_priority(self):
        """explicit > 环境变量 > 默认（patch.dict 隔离环境，退出即还原）。"""
        explicit = os.path.join(self.scratch, "explicit.sqlite")
        env_path = os.path.join(self.scratch, "env.sqlite")
        expected_default = os.path.join(
            os.path.expanduser("~"), ".zcode", "v2",
            zcode_schedule.ZCODE_TASKS_DB_FILENAME)
        # 默认分支：环境变量不存在时
        with mock.patch.dict(os.environ):
            os.environ.pop(zcode_schedule.ZCODE_DB_ENV_VAR, None)
            self.assertEqual(zcode_schedule.discover_zcode_tasks_db(),
                             expected_default)
        # 环境变量覆盖默认；explicit 再覆盖环境变量
        with mock.patch.dict(
                os.environ,
                {zcode_schedule.ZCODE_DB_ENV_VAR: env_path}):
            self.assertEqual(zcode_schedule.discover_zcode_tasks_db(),
                             env_path)
            self.assertEqual(
                zcode_schedule.discover_zcode_tasks_db(explicit), explicit)


# —— inspect_automation ——

class InspectAutomationTest(ScheduleCase):

    def test_inspect_row_and_runs(self):
        """冻结键集 + 行值透传 + recent_runs 按 scheduled_at 倒序。"""
        conn, db_path = _make_db(self.scratch)
        _insert_automation(conn, next_run_at=OLD_TS)
        base = OLD_TS - 10000
        for i in (3, 1, 2):  # 故意乱序插入
            _insert_run(conn, run_id="run-%d" % i,
                        scheduled_at=base + i * 1000,
                        session_id="sess-%d" % i,
                        outcome="failed" if i == 2 else "ok")
        conn.close()
        record = zcode_schedule.inspect_automation(db_path, AID)
        self.assertEqual(
            sorted(record.keys()),
            sorted(list(zcode_schedule.INSPECT_AUTOMATION_KEYS)
                   + ["recent_runs"]))
        self.assertEqual(record["automation_id"], AID)
        self.assertEqual(record["title"], "占位标题")
        self.assertEqual(record["recurring"], 1)
        self.assertEqual(record["max_runs"], None)
        self.assertEqual(record["enabled"], 1)
        self.assertEqual(record["lifecycle_status"], "active")
        self.assertEqual(record["next_run_at"], OLD_TS)
        self.assertEqual(record["run_count"], 3)
        self.assertEqual(record["target_task_id"], "task-v23-w1")
        self.assertEqual(record["model"], "glm-5.3")
        self.assertEqual(record["provider"], "bigmodel")
        self.assertEqual(record["mode"], "scheduled")
        self.assertEqual(record["thought_level"], "medium")
        self.assertEqual(record["schedule_rule"],
                         '{"kind": "every_days", "value": 1}')
        self.assertEqual(record["dispatch_status"], None)  # 未插列 → None
        self.assertEqual(record["last_error"], None)
        runs = record["recent_runs"]
        self.assertEqual([r["scheduled_at"] for r in runs],
                         [base + 3000, base + 2000, base + 1000])  # 倒序
        self.assertEqual(runs[0], {"scheduled_at": base + 3000,
                                   "session_id": "sess-3", "outcome": "ok"})
        self.assertEqual(runs[1]["outcome"], "failed")

    def test_inspect_recent_runs_capped_at_20(self):
        """runs 超上限：恰取最近 20 条（scheduled_at 倒序，最新在前）。"""
        conn, db_path = _make_db(self.scratch)
        _insert_automation(conn)
        total = zcode_schedule.RECENT_RUNS_LIMIT + 5
        for i in range(total):
            _insert_run(conn, run_id="run-%02d" % i,
                        scheduled_at=OLD_TS + i * 1000,
                        session_id="sess-%02d" % i)
        conn.close()
        runs = zcode_schedule.inspect_automation(db_path, AID)["recent_runs"]
        self.assertEqual(len(runs), zcode_schedule.RECENT_RUNS_LIMIT)
        self.assertEqual(runs[0]["scheduled_at"],
                         OLD_TS + (total - 1) * 1000)
        self.assertEqual(runs[0]["session_id"], "sess-%02d" % (total - 1))

    def test_inspect_missing_automation_returns_none(self):
        conn, db_path = _make_db(self.scratch)
        _insert_automation(conn)
        conn.close()
        self.assertIsNone(
            zcode_schedule.inspect_automation(db_path, "auto-ghost"))

    def test_inspect_missing_db_raises(self):
        with self.assertRaises(zcode_schedule.ZcodeScheduleDbMissing):
            zcode_schedule.inspect_automation(
                os.path.join(self.scratch, "absent.sqlite"), AID)

    def test_inspect_no_automations_table_raises(self):
        db_path = os.path.join(self.scratch,
                               zcode_schedule.ZCODE_TASKS_DB_FILENAME)
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE automation_runs (run_id TEXT)")
        conn.commit()
        conn.close()
        with self.assertRaises(zcode_schedule.ZcodeScheduleSchemaError):
            zcode_schedule.inspect_automation(db_path, AID)


# —— retime_automation：成功路径 ——

class RetimeHappyPathTest(ScheduleCase):

    def test_valid_retime(self):
        """成功 retime：verified、previous 正确、DB 落新值。"""
        conn, db_path = _make_db(self.scratch)
        _insert_automation(conn, next_run_at=OLD_TS)
        conn.close()
        result = zcode_schedule.retime_automation(db_path, AID, NEW_TS)
        self.assertEqual(result, {"automation_id": AID,
                                  "previous_next_run_at": OLD_TS,
                                  "next_run_at": NEW_TS,
                                  "verified": True})
        self.assertEqual(_next_run_at_of(db_path), NEW_TS)

    def test_only_next_run_at_mutated(self):
        """retime 前后整行 33 列逐列对比：除 next_run_at 外零列变化。"""
        conn, db_path = _make_db(self.scratch)
        _insert_automation(conn, next_run_at=OLD_TS)
        conn.close()
        before = _full_row(db_path)
        self.assertEqual(len(before), 33)
        result = zcode_schedule.retime_automation(db_path, AID, NEW_TS)
        self.assertIs(result["verified"], True)
        after = _full_row(db_path)
        for (name, _decl), old, new in zip(AUTOMATIONS_SCHEMA,
                                           before, after):
            if name == "next_run_at":
                self.assertEqual(new, NEW_TS)
            else:
                self.assertEqual(old, new, "列 %s 被意外改写" % name)


# —— retime_automation：各前置闸 ——

class RetimeGateTest(ScheduleCase):

    def test_unknown_automation(self):
        """0 行匹配 → AutomationNotFound，DB 零副作用。"""
        conn, db_path = _make_db(self.scratch)
        _insert_automation(conn)
        conn.close()
        with self.assertRaises(
                zcode_schedule.ZcodeScheduleAutomationNotFound):
            zcode_schedule.retime_automation(db_path, "auto-ghost", NEW_TS)
        self.assertEqual(_next_run_at_of(db_path), OLD_TS)

    def test_ambiguous_match(self):
        """多于一行匹配（无主键夹具双行同 id）→ AmbiguousMatch。"""
        conn, db_path = _make_db(self.scratch, with_pk=False)
        _insert_automation(conn)
        _insert_automation(conn)
        conn.close()
        with self.assertRaises(zcode_schedule.ZcodeScheduleAmbiguousMatch):
            zcode_schedule.retime_automation(db_path, AID, NEW_TS)
        self.assertEqual(_next_run_at_of(db_path), OLD_TS)

    def test_bad_schema_missing_table(self):
        """automations 表缺失（全空库 / 仅 automation_runs）→ SchemaError
        （PRAGMA 返回空即不满足必需列超集）。"""
        for variant in ("empty", "runs_only"):
            with self.subTest(variant=variant):
                subdir = os.path.join(self.scratch, variant)
                os.makedirs(subdir)
                db_path = os.path.join(subdir, "tasks-index.sqlite")
                conn = sqlite3.connect(db_path)
                if variant == "runs_only":
                    conn.execute(
                        "CREATE TABLE automation_runs (run_id TEXT)")
                conn.commit()
                conn.close()
                with self.assertRaises(
                        zcode_schedule.ZcodeScheduleSchemaError):
                    zcode_schedule.retime_automation(db_path, AID, NEW_TS)

    def test_bad_schema_missing_column(self):
        """automations 缺 next_run_at 列 → SchemaError。"""
        db_path = os.path.join(self.scratch,
                               zcode_schedule.ZCODE_TASKS_DB_FILENAME)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE automations ("
            "automation_id TEXT PRIMARY KEY,"
            " recurring INTEGER NOT NULL DEFAULT 1,"
            " enabled INTEGER NOT NULL DEFAULT 1,"
            " lifecycle_status TEXT NOT NULL DEFAULT 'active')")
        conn.commit()
        conn.close()
        with self.assertRaises(zcode_schedule.ZcodeScheduleSchemaError):
            zcode_schedule.retime_automation(db_path, AID, NEW_TS)

    def test_invalid_timestamp(self):
        """0 / 负数 / bool / 浮点 / None / str / 超上限 → TimestampError；
        参数闸先于任何 I/O（对不存在的 DB 同样先抛）。"""
        db_path = os.path.join(self.scratch,
                               zcode_schedule.ZCODE_TASKS_DB_FILENAME)
        for bad in (0, -1, True, 1.5, None, "123",
                    zcode_schedule.MAX_NEXT_RUN_AT_EPOCH_MS + 1):
            with self.subTest(next_run_at=bad):
                with self.assertRaises(
                        zcode_schedule.ZcodeScheduleTimestampError):
                    zcode_schedule.retime_automation(db_path, AID, bad)
        self.assertFalse(os.path.exists(db_path))

    def test_recurring_mismatch(self):
        """recurring=0 行 + expect_recurring=True → StateError（回滚）；
        expect_recurring=False 时不设该预期，正常写。"""
        conn, db_path = _make_db(self.scratch)
        _insert_automation(conn, recurring=0, next_run_at=OLD_TS)
        conn.close()
        with self.assertRaises(zcode_schedule.ZcodeScheduleStateError):
            zcode_schedule.retime_automation(db_path, AID, NEW_TS)
        self.assertEqual(_next_run_at_of(db_path), OLD_TS)  # 已回滚
        result = zcode_schedule.retime_automation(
            db_path, AID, NEW_TS, expect_recurring=False)
        self.assertIs(result["verified"], True)
        self.assertEqual(_next_run_at_of(db_path), NEW_TS)


# —— retime_automation：忙 / 锁 ——

class RetimeBusyTest(ScheduleCase):

    def hold_exclusive_in_thread(self, db_path, ready, release,
                                 hold_seconds=None):
        """子线程 BEGIN EXCLUSIVE 持锁：ready 后放行主线程；release 事件
        或 hold_seconds 到期后回滚释放（OS 原语零 mock）。"""
        def holder():
            conn = sqlite3.connect(db_path, isolation_level=None)
            try:
                conn.execute("BEGIN EXCLUSIVE")
                ready.set()
                if hold_seconds is None:
                    release.wait(timeout=15)
                else:
                    time.sleep(hold_seconds)
                conn.execute("ROLLBACK")
            finally:
                conn.close()

        thread = threading.Thread(target=holder)
        thread.start()
        self.assertTrue(ready.wait(timeout=10))
        return thread

    def test_busy_timeout_fails_fast(self):
        """锁持有者在场 + busy_timeout_ms=300 → 确定型 BusyError（有界
        fail-fast），DB 未被改。"""
        conn, db_path = _make_db(self.scratch)
        _insert_automation(conn)
        conn.close()
        ready, release = threading.Event(), threading.Event()
        thread = self.hold_exclusive_in_thread(db_path, ready, release)
        try:
            started = time.monotonic()
            with self.assertRaises(zcode_schedule.ZcodeScheduleBusyError):
                zcode_schedule.retime_automation(
                    db_path, AID, NEW_TS, busy_timeout_ms=300)
            elapsed = time.monotonic() - started
        finally:
            release.set()
            thread.join(timeout=15)
        self.assertGreaterEqual(elapsed, 0.2)  # 确实等过 busy 窗口
        self.assertLess(elapsed, 10.0)         # 且 fail-fast 有界
        self.assertEqual(_next_run_at_of(db_path), OLD_TS)

    def test_busy_timeout_waits_then_succeeds(self):
        """锁持有约 1.5s + busy_timeout_ms=8000 → 等待后成功提交。"""
        conn, db_path = _make_db(self.scratch)
        _insert_automation(conn)
        conn.close()
        ready, release = threading.Event(), threading.Event()
        thread = self.hold_exclusive_in_thread(
            db_path, ready, release, hold_seconds=1.5)
        try:
            result = zcode_schedule.retime_automation(
                db_path, AID, NEW_TS, busy_timeout_ms=8000)
        finally:
            release.set()
            thread.join(timeout=15)
        self.assertIs(result["verified"], True)
        self.assertEqual(result["previous_next_run_at"], OLD_TS)
        self.assertEqual(_next_run_at_of(db_path), NEW_TS)


# —— retime_automation：读回验证 ——

class RetimeVerificationTest(ScheduleCase):

    def test_read_back_mismatch_raises(self):
        """读回 SELECT 注入错值 → VerificationError，且事务已回滚（行值
        保持旧值，绝无半写）。"""
        conn, db_path = _make_db(self.scratch)
        _insert_automation(conn, next_run_at=OLD_TS)
        conn.close()

        real_connect = sqlite3.connect
        tampered_value = NEW_TS + 1

        class TamperedCursor(object):

            def __init__(self, cursor, value):
                self._cursor = cursor
                self._value = value

            def fetchone(self):
                row = self._cursor.fetchone()
                if row is None:
                    return None
                return (self._value,)

        class TamperedConn(object):
            """连接代理：仅对读回 SELECT（SELECT next_run_at 开头）返回
            篡改值，其余语句（PRAGMA / BEGIN / COUNT / recurring / 写句 /
            ROLLBACK）透传真实连接。"""

            def __init__(self, conn, value):
                self._conn = conn
                self._value = value

            def execute(self, sql, params=()):
                cursor = self._conn.execute(sql, params)
                if sql.strip().lower().startswith("select next_run_at"):
                    return TamperedCursor(cursor, self._value)
                return cursor

            def close(self):
                self._conn.close()

        def fake_connect(*args, **kwargs):
            return TamperedConn(real_connect(*args, **kwargs),
                                tampered_value)

        with mock.patch("sqlite3.connect", side_effect=fake_connect):
            with self.assertRaises(
                    zcode_schedule.ZcodeScheduleVerificationError):
                zcode_schedule.retime_automation(db_path, AID, NEW_TS)
        self.assertEqual(_next_run_at_of(db_path), OLD_TS)  # 已回滚


# —— §6.3 硬安全边界机械锚定 ——

class ForbiddenSqlTest(unittest.TestCase):
    """zcode_schedule.py 源码文本机械扫描（大小写不敏感）：一切其他
    DML/DDL 语句 token 禁止出现；唯一生产写语句短语恰出现一次（多一处
    即视为引入了第二条写路径）。"""

    FORBIDDEN_TOKENS = ("insert into", "delete from", "alter table",
                        "drop table", "drop ", "create ")
    SOLE_WRITE_PHRASE = "update automations set"

    def test_forbidden_sql_mechanically_absent(self):
        module_path = Path(zcode_schedule.__file__).resolve()
        text = module_path.read_text(encoding="utf-8").lower()
        for token in self.FORBIDDEN_TOKENS:
            self.assertNotIn(
                token, text,
                "zcode_schedule.py 含禁止语句 token %r（v2.3 §6.3 硬边界，"
                "生产写操作只有一条 SQL）：%s" % (token, module_path))
        self.assertEqual(
            text.count(self.SOLE_WRITE_PHRASE), 1,
            "唯一生产写语句短语应恰出现一次，实际 %d 次：%s"
            % (text.count(self.SOLE_WRITE_PHRASE), module_path))


if __name__ == "__main__":
    unittest.main()
