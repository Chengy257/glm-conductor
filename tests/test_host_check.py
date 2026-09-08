#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.commands.host（host-check 子命令）单元测试（v2.3.1 Wave 2，
unit w2-host-check）。

锚定对象：cli.py 的 host-check 分发分支 + runtime/commands/host.py 薄
函数 + zcode_schedule.probe_host_compatibility 只读探针。测试类映射：

    CompatibleDbTest        兼容库：schema compatible + supported +
                            write_probe_performed=="no"；NULL 抽样、
                            env 覆盖发现、explicit > env 优先级
    MissingDbTest           DB 缺失：干净报告（found:false +
                            unsupported）+ 退出码 0（约定一致）
    IncompatibleSchemaTest  automations 表缺失 / 必需列缺失
                            （missing_columns + issues 明细）
    TypeSamplingTest        next_run_at 存储类型抽样（text / real →
                            not ok；空表 → ok）
    RunsTableTest           automation_runs 咨询性标志（缺失容忍 /
                            列缺失 not ok 但不闸 supported）
    ZeroWriteProofTest      零写证明：夹具库 sha256 + mtime 前后不变
                            （test_session_start_advisory.py 手法）+
                            无 -wal / -shm / -journal 副产物
    UsageErrorTest          用法错 → 退出码 2
    MechanicalAnchorTest    host.py 源码机械扫描（薄层零写语句 token）

全部离线：夹具 DB 在 tempfile.TemporaryDirectory 内，且经 --db /
GLM_CONDUCTOR_ZCODE_DB 显式指向（绝不触碰真实 ~/.zcode/v2/ 宿主库）；
建库手法照 tests/test_zcode_schedule.py；CLI 调用照
tests/test_quota_clock_cli.py 先例：cli.main(list) + redirect_stdout
捕获单行 JSON。unittest + unittest.mock，零 pytest。

运行：
    cd <repo_root> && python -X utf8 -m unittest tests.test_host_check -v
"""

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import cli
from runtime.commands import host as host_command
from runtime.host import zcode_schedule

TS = 1750000000000


def make_db(tmpdir, with_automations=True, with_runs=True,
            without_next_run_at=False):
    """建夹具 DB（手法照 tests/test_zcode_schedule.py：sqlite3 建库、
    commit、close）。automations 表含 REQUIRED_AUTOMATIONS_COLUMNS 五
    必需列 + 常用附加列（探针只依赖必需列集与 next_run_at 类型抽样，
    其余列不参与断言语义）；automation_runs 表含 recent_runs 三列。
    返回 db_path。"""
    db_path = os.path.join(tmpdir, zcode_schedule.ZCODE_TASKS_DB_FILENAME)
    conn = sqlite3.connect(db_path)
    if with_automations:
        columns = []
        for name, decl in AUTOMATIONS_COLUMNS:
            if without_next_run_at and name == "next_run_at":
                continue
            columns.append("%s %s" % (name, decl))
        conn.execute("CREATE TABLE automations (%s)" % ", ".join(columns))
    if with_runs:
        conn.execute(
            "CREATE TABLE automation_runs ("
            "run_id TEXT PRIMARY KEY,"
            " automation_id TEXT NOT NULL DEFAULT '',"
            " scheduled_at INTEGER,"
            " session_id TEXT,"
            " outcome TEXT)")
    conn.commit()
    conn.close()
    return db_path


def insert_automation(db_path, automation_id="auto-host-check",
                      next_run_at=TS):
    """插一行健康 recurring 样本（next_run_at 可为 None）。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO automations (automation_id, title, recurring,"
            " enabled, lifecycle_status, next_run_at, run_count)"
            " VALUES (?, 'host-check 夹具', 1, 1, 'active', ?, 2)",
            (automation_id, next_run_at))
        conn.commit()
    finally:
        conn.close()


# automations 表夹具列（探针语义必需列 + 占位附加列）
AUTOMATIONS_COLUMNS = (
    ("automation_id", "TEXT PRIMARY KEY"),
    ("title", "TEXT NOT NULL DEFAULT ''"),
    ("recurring", "INTEGER NOT NULL DEFAULT 0"),
    ("enabled", "INTEGER NOT NULL DEFAULT 1"),
    ("lifecycle_status", "TEXT NOT NULL DEFAULT 'active'"),
    ("next_run_at", "INTEGER"),
    ("run_count", "INTEGER NOT NULL DEFAULT 0"),
)


class HostCheckCase(unittest.TestCase):
    """tempdir 基座：每用例独立 scratch 目录。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.scratch = tmp.name

    def run_host_check(self, argv):
        """跑 cli.main(["host-check", ...])，捕获 stdout，返回
        (exit_code, 解析后的单行 JSON dict)。"""
        buffer = StringIO()
        with redirect_stdout(buffer):
            code = cli.main(list(argv))
        return code, json.loads(buffer.getvalue())

    def file_sha256(self, path):
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()


# —— 兼容库 ——

class CompatibleDbTest(HostCheckCase):

    def test_compatible_db_reports_supported(self):
        """兼容库全绿：schema compatible + supported +
        write_probe_performed=="no" + 报告键集齐备。"""
        db_path = make_db(self.scratch)
        insert_automation(db_path)
        code, payload = self.run_host_check(
            ["host-check", "--db", db_path])
        self.assertEqual(code, 0)
        self.assertEqual(
            sorted(payload.keys()),
            sorted(["backend", "database", "database_found",
                    "database_readable", "schema", "missing_columns",
                    "next_run_at_type_ok", "runs_table_ok", "issues",
                    "write_capability_required", "write_probe_performed",
                    "support_status"]))
        self.assertEqual(payload["backend"], "zcode_sqlite")
        self.assertEqual(payload["database"], db_path)
        self.assertIs(payload["database_found"], True)
        self.assertIs(payload["database_readable"], True)
        self.assertEqual(payload["schema"], "compatible")
        self.assertEqual(payload["missing_columns"], [])
        self.assertIs(payload["next_run_at_type_ok"], True)
        self.assertIs(payload["runs_table_ok"], True)
        self.assertEqual(payload["write_capability_required"],
                         "next_run_at only")
        self.assertEqual(payload["write_probe_performed"], "no")
        self.assertEqual(payload["support_status"], "supported")
        self.assertEqual(payload["issues"], [])

    def test_null_next_run_at_samples_ok(self):
        """NULL 也是合法 next_run_at 存储形态（可空列）：抽样后仍
        supported。"""
        db_path = make_db(self.scratch)
        insert_automation(db_path, next_run_at=None)
        code, payload = self.run_host_check(["host-check", "--db", db_path])
        self.assertEqual(code, 0)
        self.assertIs(payload["next_run_at_type_ok"], True)
        self.assertEqual(payload["support_status"], "supported")

    def test_env_override_discovery(self):
        """GLM_CONDUCTOR_ZCODE_DB 覆盖默认路径（与 adapter env 语义
        一致）；无位置参数形态。"""
        db_path = make_db(self.scratch)
        insert_automation(db_path)
        with mock.patch.dict(
                os.environ,
                {zcode_schedule.ZCODE_DB_ENV_VAR: db_path}):
            code, payload = self.run_host_check(["host-check"])
        self.assertEqual(code, 0)
        self.assertEqual(payload["database"], db_path)
        self.assertIs(payload["database_found"], True)
        self.assertEqual(payload["support_status"], "supported")

    def test_explicit_db_wins_over_env(self):
        """--db 显式路径 > 环境变量（discover 优先级透传）。"""
        env_dir = os.path.join(self.scratch, "envloc")
        explicit_dir = os.path.join(self.scratch, "explicit")
        os.makedirs(env_dir)
        os.makedirs(explicit_dir)
        env_db = make_db(env_dir)
        explicit_db = make_db(explicit_dir)
        with mock.patch.dict(
                os.environ,
                {zcode_schedule.ZCODE_DB_ENV_VAR: env_db}):
            code, payload = self.run_host_check(
                ["host-check", "--db", explicit_db])
        self.assertEqual(code, 0)
        self.assertEqual(payload["database"], explicit_db)


# —— DB 缺失 ——

class MissingDbTest(HostCheckCase):

    def test_missing_db_clean_report_exit_zero(self):
        """DB 缺失 → 干净报告（非崩溃、非 traceback）+ 退出码 0
        （约定：0 = 兼容或干净的不兼容报告）。"""
        absent = os.path.join(self.scratch, "absent",
                              zcode_schedule.ZCODE_TASKS_DB_FILENAME)
        code, payload = self.run_host_check(["host-check", "--db", absent])
        self.assertEqual(code, 0)
        self.assertEqual(payload["database"], absent)
        self.assertIs(payload["database_found"], False)
        self.assertIs(payload["database_readable"], False)
        self.assertEqual(payload["schema"], "incompatible")
        self.assertEqual(payload["support_status"], "unsupported")
        self.assertTrue(any(
            issue.startswith("database not found")
            for issue in payload["issues"]))
        self.assertEqual(payload["write_probe_performed"], "no")

    def test_missing_db_via_env_discovery(self):
        absent = os.path.join(self.scratch, "ghost.sqlite")
        with mock.patch.dict(
                os.environ,
                {zcode_schedule.ZCODE_DB_ENV_VAR: absent}):
            code, payload = self.run_host_check(["host-check"])
        self.assertEqual(code, 0)
        self.assertEqual(payload["database"], absent)
        self.assertIs(payload["database_found"], False)
        self.assertEqual(payload["support_status"], "unsupported")


# —— schema 不兼容 ——

class IncompatibleSchemaTest(HostCheckCase):

    def test_missing_automations_table(self):
        """automations 表缺失 → incompatible + 明细（表级 issue +
        全部必需列按缺失上报）。"""
        db_path = make_db(self.scratch, with_automations=False)
        code, payload = self.run_host_check(["host-check", "--db", db_path])
        self.assertEqual(code, 0)
        self.assertEqual(payload["schema"], "incompatible")
        self.assertEqual(
            payload["missing_columns"],
            sorted(zcode_schedule.REQUIRED_AUTOMATIONS_COLUMNS))
        self.assertIn("required table missing: automations",
                      payload["issues"])
        self.assertIn("required column missing: next_run_at",
                      payload["issues"])
        self.assertIs(payload["next_run_at_type_ok"], False)
        self.assertEqual(payload["support_status"], "unsupported")

    def test_missing_required_column_next_run_at(self):
        """缺 next_run_at 列 → missing_columns 含该列 + issues 明细 +
        unsupported。"""
        db_path = make_db(self.scratch, without_next_run_at=True)
        code, payload = self.run_host_check(["host-check", "--db", db_path])
        self.assertEqual(code, 0)
        self.assertEqual(payload["schema"], "incompatible")
        self.assertIn("next_run_at", payload["missing_columns"])
        self.assertIn("required column missing: next_run_at",
                      payload["issues"])
        self.assertIs(payload["next_run_at_type_ok"], False)
        self.assertEqual(payload["support_status"], "unsupported")

    def test_all_required_columns_missing(self):
        db_path = os.path.join(self.scratch, "bare.sqlite")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE automations (title TEXT)")
        conn.commit()
        conn.close()
        code, payload = self.run_host_check(["host-check", "--db", db_path])
        self.assertEqual(payload["schema"], "incompatible")
        self.assertEqual(
            payload["missing_columns"],
            sorted(zcode_schedule.REQUIRED_AUTOMATIONS_COLUMNS))
        self.assertEqual(payload["support_status"], "unsupported")


# —— next_run_at 类型抽样 ——

class TypeSamplingTest(HostCheckCase):

    def _insert_with_raw_next_run_at(self, db_path, raw_value):
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "INSERT INTO automations (automation_id, recurring,"
                " enabled, lifecycle_status, next_run_at, run_count)"
                " VALUES ('auto-typed', 1, 1, 'active', ?, 0)",
                (raw_value,))
            conn.commit()
        finally:
            conn.close()

    def test_non_integer_stored_type_detected(self):
        """存储类型 text / real → next_run_at_type_ok=False + 明细 +
        unsupported（NUMERIC 亲和性下仍存成非整型的值必然异常）。"""
        for raw, type_name in (("not-a-timestamp", "text"), (1.5, "real")):
            with self.subTest(raw=raw):
                scratch = os.path.join(self.scratch, type_name)
                os.makedirs(scratch)
                db_path = make_db(scratch)
                self._insert_with_raw_next_run_at(db_path, raw)
                code, payload = self.run_host_check(
                    ["host-check", "--db", db_path])
                self.assertEqual(code, 0)
                self.assertIs(payload["next_run_at_type_ok"], False)
                self.assertIn(
                    "next_run_at stored type not integer: %s" % type_name,
                    payload["issues"])
                self.assertEqual(payload["support_status"], "unsupported")

    def test_empty_table_type_ok(self):
        """空表（无抽样样本）不判类型不符：supported。"""
        db_path = make_db(self.scratch)  # 建表但不插行
        code, payload = self.run_host_check(["host-check", "--db", db_path])
        self.assertIs(payload["next_run_at_type_ok"], True)
        self.assertEqual(payload["support_status"], "supported")


# —— automation_runs 咨询性标志 ——

class RunsTableTest(HostCheckCase):

    def test_missing_runs_table_tolerated(self):
        """automation_runs 缺失 → adapter 容忍（recent_runs 记空）：
        runs_table_ok=True + 咨询性 issue，不闸 supported。"""
        db_path = make_db(self.scratch, with_runs=False)
        insert_automation(db_path)
        code, payload = self.run_host_check(["host-check", "--db", db_path])
        self.assertEqual(code, 0)
        self.assertIs(payload["runs_table_ok"], True)
        self.assertIn("optional table missing: automation_runs",
                      payload["issues"])
        self.assertEqual(payload["support_status"], "supported")

    def test_runs_table_bad_columns_not_ok_but_advisory(self):
        """runs 表存在但 recent_runs 三列不可读 → runs_table_ok=False
        + 明细；写能力面不依赖 runs 表，support_status 仍 supported。"""
        db_path = make_db(self.scratch)
        conn = sqlite3.connect(db_path)
        conn.execute("DROP TABLE automation_runs")
        conn.execute(
            "CREATE TABLE automation_runs (run_id TEXT PRIMARY KEY)")
        conn.commit()
        conn.close()
        code, payload = self.run_host_check(["host-check", "--db", db_path])
        self.assertEqual(code, 0)
        self.assertIs(payload["runs_table_ok"], False)
        self.assertTrue(any(
            issue.startswith("automation_runs unreadable")
            for issue in payload["issues"]))
        self.assertEqual(payload["support_status"], "supported")


# —— 零写证明 ——

class ZeroWriteProofTest(HostCheckCase):

    def test_fixture_untouched_by_probe(self):
        """夹具库字节 sha256 + mtime 前后不变（探针全程零写的字节级
        证明），且无 -wal / -shm / -journal 副产物；行值原样。"""
        db_path = make_db(self.scratch)
        insert_automation(db_path)
        before_sha = self.file_sha256(db_path)
        before_mtime = os.path.getmtime(db_path)
        code, payload = self.run_host_check(["host-check", "--db", db_path])
        self.assertEqual(code, 0)
        self.assertEqual(payload["support_status"], "supported")
        self.assertEqual(self.file_sha256(db_path), before_sha)
        self.assertEqual(os.path.getmtime(db_path), before_mtime)
        self.assertFalse(os.path.exists(db_path + "-wal"))
        self.assertFalse(os.path.exists(db_path + "-shm"))
        self.assertFalse(os.path.exists(db_path + "-journal"))
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT next_run_at FROM automations"
                " WHERE automation_id = 'auto-host-check'").fetchone()
        finally:
            conn.close()
        self.assertEqual(row[0], TS)

    def test_missing_db_probe_writes_nothing(self):
        """对缺失路径探测不意外建库（零写红线含「不合成 DB 文件」）。"""
        absent = os.path.join(self.scratch, "never",
                              zcode_schedule.ZCODE_TASKS_DB_FILENAME)
        code, payload = self.run_host_check(["host-check", "--db", absent])
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(absent))
        self.assertFalse(os.path.exists(os.path.dirname(absent)))


# —— 用法错 ——

class UsageErrorTest(HostCheckCase):

    def test_extra_positional_arg_exit_2(self):
        code, payload = self.run_host_check(
            ["host-check", "extra", "--db", "x"])
        self.assertEqual(code, 2)
        self.assertIn("error", payload)

    def test_unknown_optional_flag_exit_2(self):
        code, payload = self.run_host_check(["host-check", "--wrong", "x"])
        self.assertEqual(code, 2)
        self.assertIn("error", payload)

    def test_dangling_db_flag_exit_2(self):
        code, payload = self.run_host_check(["host-check", "--db"])
        self.assertEqual(code, 2)
        self.assertIn("error", payload)

    def test_usage_lists_host_check(self):
        """USAGE 增补行在位（host-check [--db <path>]）。"""
        self.assertIn("host-check [--db <path>]", cli.USAGE)


# —— 机械锚定 ——

class MechanicalAnchorTest(unittest.TestCase):
    """commands/host.py 源码文本机械扫描（大小写不敏感）：薄层零 SQL
    写面——一切变更语句 token 与 PRAGMA 不出现（探测语句收敛在
    zcode_schedule.probe_host_compatibility，该模块由
    test_zcode_schedule.ForbiddenSqlTest 机械锚定）。"""

    FORBIDDEN_TOKENS = ("insert into", "update ", "delete from",
                        "alter table", "drop ", "create ", "pragma")

    def test_host_command_source_write_free(self):
        module_path = Path(host_command.__file__).resolve()
        text = module_path.read_text(encoding="utf-8").lower()
        for token in self.FORBIDDEN_TOKENS:
            self.assertNotIn(
                token, text,
                "commands/host.py 含禁止 token %r（薄层零写面，探测语句"
                "唯一落点是 zcode_schedule.probe_host_compatibility）：%s"
                % (token, module_path))


if __name__ == "__main__":
    unittest.main()
