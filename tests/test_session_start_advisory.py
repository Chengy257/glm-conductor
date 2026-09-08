#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hooks/session_start.py 的 v2.3.1 Quota Clock advisory 测试（unit
w1-session-advisory）。

harness 照 tests/test_recovery.py 先例：子进程直调 session_start.py
（钩子快照不热加载）+ tempfile 仓库 + UTF-8 解码；clock state 经
GLM_CONDUCTOR_HOME 注入 tempfile 临时目录（手法同
tests/test_quota_clock_cli.py），identity 经 GLM_CONDUCTOR_QUOTA_API_KEY
固定（env 优先于 ~/.zcode/v2/config.json 回退 → 指纹确定可预期，父
进程用同一 compute_provider_identity_hash 算出同一 hash）。

覆盖（VERIFICATION 清单）：
  - 无 clock state（unbound）→ stdout 恒空（不提示未绑定者）；
  - 新鲜 tick（未超 2×fallback 阈值）→ stdout 恒空（零噪音红线）；
  - 陈旧 tick → 单行 JSON hookSpecificOutput（顶层仅
    hookSpecificOutput / 内层仅两键），advisory 含标识头 / 陈旧事实 /
    Flash 专用会话 replace 指引 / 「未执行自动会话迁移」声明 /
    quota-clock-status 指路 / not_mechanically_verifiable；
  - last_tick_at 缺失（None / 键缺省）→ 视为陈旧；
  - last_tick_at 非数值 → 视为陈旧；fallback_interval_minutes 非法
    → 按 60 折算（90 分钟前的 tick 折算后不陈旧）；
  - state 文件 advisory 前后 sha256 不变（零写证明）；
  - state 损坏 / 非法 JSON / schema 版本不认识 → fail-open 完全静默；
  - active 任务 resume context 与 advisory 共存：resume 在前、空行
    分隔、advisory 在后；仅 resume（无 clock state）时
    additionalContext 与纯 recovery 渲染逐字节一致（零噪音回归红线）。

运行：
    cd <repo_root> && python -X utf8 -m unittest \
        tests.test_session_start_advisory -v
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import recovery, state
from runtime.quota import clock, clock_store
from runtime.quota.credentials import ENV_VAR as CREDENTIAL_ENV_VAR
from runtime.quota.identity import compute_provider_identity_hash

# 被测钩子脚本：仓库根 plugins/glm-conductor/hooks/session_start.py
SESSION_START = (Path(__file__).resolve().parents[1]
                 / "plugins/glm-conductor/hooks/session_start.py")

TID = "advisory-demo-1a2b3c"
# 矩阵合法的 solo 路由基座（与 test_recovery 同款）
ROUTE = {"mode": "solo", "delegability": "low", "assurance": "standard",
         "executor": "main", "continuity": "foreground"}

# 固定凭证（env 优先 → 子进程 identity 指纹确定，绝不触碰真实
# ~/.zcode/v2/config.json）；期望指纹用同一函数在父进程算出
FIXED_KEY = "w1-advisory-fixed-credential"
with mock.patch.dict(os.environ, {CREDENTIAL_ENV_VAR: FIXED_KEY}):
    EXPECTED_HASH = compute_provider_identity_hash()

# 陈旧 / 新鲜的 tick 时刻（相对测试进程 now 构造，远离 120 分钟阈值，
# 抵御子进程启动耗时的时间差）
NOW_MS = int(time.time() * 1000)
FRESH_TICK_MS = NOW_MS - 10 * 60 * 1000          # 10 分钟前：新鲜
STALE_TICK_MS = NOW_MS - 4 * 60 * 60 * 1000      # 4 小时前：陈旧
NEAR_TICK_MS = NOW_MS - 90 * 60 * 1000           # 90 分钟前：折算后仍新鲜


def make_unit(uid, status):
    """构造一个通过 §61 契约校验的最小 work unit dict（test_recovery 同款）。"""
    return {"id": uid, "objective": "fixture unit %s" % uid,
            "status": status, "depends_on": [], "executor": "main",
            "ownership": ["src/a.py"],
            "verification": ["python3 -m unittest -h"]}


def save_task(repo, task_id=TID, status="executing", units=()):
    """在临时仓库构造一个活动任务状态文件（test_recovery 同款构造）。"""
    active = state.new_task_state(
        task_id, "advisory 冒烟测试目标", dict(ROUTE),
        ownership_files=["src/a.py"],
        verification_required=["python3 -m unittest -h"], status=status)
    active["work_units"] = list(units)
    state.save_state(repo, active)
    return active


def parse_single_line_json(text):
    """把 hook stdout 按单行 JSON 解析并返回 dict（test_recovery 同款）。"""
    stripped = text.strip()
    if "\n" in stripped:
        raise AssertionError("stdout 不是单行 JSON: %r" % text)
    return json.loads(stripped)


class AdvisoryCase(unittest.TestCase):
    """基座：tempfile home（GLM_CONDUCTOR_HOME 注入）+ tempfile repo，
    子进程 env 另注入固定凭证（identity 指纹确定）。"""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="glm-advisory-home-")
        self.addCleanup(shutil.rmtree, self.home, True)
        self.repo = tempfile.mkdtemp(prefix="glm-advisory-repo-")
        self.addCleanup(shutil.rmtree, self.repo, True)

    # —— fixture 与执行装置 ——

    def write_state(self, *, last_tick_at=STALE_TICK_MS,
                    fallback=60, schema_version=1, raw=None):
        """在注入的临时 home 写 clock state fixture（不经 runtime 写
        路径——本单元锚定的红线正是 advisory 只读，测试自行构造文件；
        raw 非 None 时原样写入该字符串，构造损坏态）。返回 state 路径。"""
        path = Path(clock_store.clock_state_path(EXPECTED_HASH,
                                                 home=self.home))
        path.parent.mkdir(parents=True, exist_ok=True)
        if raw is not None:
            path.write_text(raw, encoding="utf-8")
            return str(path)
        payload = {
            "schema_version": schema_version,
            "provider_identity_hash": EXPECTED_HASH,
            "automation_id": "auto-advisory-1",
            "owner_repository": "X:/mock/repo",
            "zcode_db_path": "X:/mock/tasks-index.sqlite",
            "runtime_path": "X:/mock/runtime",
            "automation_model": "GLM-5.3-Flash",
            "fallback_interval_minutes": fallback,
            "grace_seconds": 120,
            "retry_delay_seconds": 300,
            "last_tick_at": last_tick_at,
            "last_reset_at": None,
            "next_target_at": None,
            "last_retime_at": None,
            "status": "bound",
        }
        with open(str(path), "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        return str(path)

    def state_sha256(self, path):
        """state 文件字节 sha256（零写证明用）。"""
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    def run_hook(self, stdin_text="{}"):
        """子进程直调 session_start.py（test_recovery 同款 UTF-8 解码）。"""
        return subprocess.run(
            [sys.executable, str(SESSION_START)],
            input=stdin_text, text=True, capture_output=True,
            encoding="utf-8", errors="replace",
            env=dict(os.environ,
                     ZCODE_PROJECT_DIR=str(self.repo),
                     **{clock_store.GLM_CONDUCTOR_HOME_ENV: self.home,
                        CREDENTIAL_ENV_VAR: FIXED_KEY}))

    def assert_silent(self, result):
        """钩子完全安静契约：exit 0 且 stdout / stderr 均空。"""
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")


class SilenceTest(AdvisoryCase):
    """零噪音面：unbound / 新鲜 tick / 损坏态 → 输出与现状逐字节一致。"""

    def test_no_clock_state_silent(self):
        # 无 clock state（unbound）→ 不提示未绑定者，stdout 恒空
        self.assert_silent(self.run_hook())

    def test_fresh_tick_silent(self):
        # 新鲜 tick（未超 2×60 分钟阈值）→ 零噪音红线
        self.write_state(last_tick_at=FRESH_TICK_MS)
        self.assert_silent(self.run_hook())

    def test_corrupt_state_json_fail_open_silent(self):
        # state 损坏 / 非法 JSON → fail-open 完全静默
        self.write_state(raw="{not-json")
        self.assert_silent(self.run_hook())

    def test_unknown_schema_version_silent(self):
        # schema 版本不认识（legacy / 未来版本）→ load 为 None → 静默
        self.write_state(schema_version=2)
        self.assert_silent(self.run_hook())


class StaleAdvisoryTest(AdvisoryCase):
    """陈旧 tick → advisory 注入（单行 JSON 契约 + 文案要素齐备）。"""

    def _advisory_context(self):
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        payload = parse_single_line_json(result.stdout)
        # Zod 严格校验形态：顶层仅 hookSpecificOutput，内层仅两键
        self.assertEqual(sorted(payload.keys()), ["hookSpecificOutput"])
        block = payload["hookSpecificOutput"]
        self.assertEqual(sorted(block.keys()),
                         ["additionalContext", "hookEventName"])
        self.assertEqual(block["hookEventName"], "SessionStart")
        return block["additionalContext"]

    def test_stale_tick_emits_advisory_with_all_elements(self):
        self.write_state(last_tick_at=STALE_TICK_MS)
        ctx = self._advisory_context()
        # 标识头 + 陈旧事实（automation_id 与阈值口径可见）
        self.assertTrue(ctx.startswith("GLM CONDUCTOR QUOTA CLOCK ADVISORY"))
        self.assertIn("auto-advisory-1", ctx)
        self.assertIn("last_tick_at", ctx)
        self.assertIn("120 minutes", ctx)  # 2 × fallback 60 = 120
        # 恢复指引：专用低成本 Flash 会话显式 replace
        self.assertIn("quota-clock-bind <new_automation_id>", ctx)
        self.assertIn("Flash", ctx)
        # 「未执行自动会话迁移」声明（clock 常量逐字）
        self.assertIn(clock.NO_AUTO_MIGRATION_DECLARATION, ctx)
        # 会话专用性不可机械验证（clock 常量逐字）
        self.assertIn(clock.DEDICATED_SESSION_UNVERIFIABLE, ctx)
        # 权威诊断指路
        self.assertIn("quota-clock-status", ctx)

    def test_missing_last_tick_at_treated_stale(self):
        # last_tick_at 缺失（None）→ 与 status 口径一致视为陈旧
        self.write_state(last_tick_at=None)
        ctx = self._advisory_context()
        self.assertIn("GLM CONDUCTOR QUOTA CLOCK ADVISORY", ctx)

    def test_non_numeric_last_tick_at_treated_stale(self):
        # last_tick_at 非数值（bool 拒绝口径外的垃圾值）→ 视为陈旧
        self.write_state(last_tick_at="garbage")
        ctx = self._advisory_context()
        self.assertIn("GLM CONDUCTOR QUOTA CLOCK ADVISORY", ctx)

    def test_invalid_fallback_folded_to_60(self):
        # fallback_interval_minutes 非法 → 按 60 折算（同 status 容错）：
        # 4h 前（>2h）陈旧；90min 前（<2h，若误折 30 会误报）不陈旧
        self.write_state(last_tick_at=STALE_TICK_MS, fallback="bogus")
        self.assertIn("GLM CONDUCTOR QUOTA CLOCK ADVISORY",
                      self._advisory_context())
        self.write_state(last_tick_at=NEAR_TICK_MS, fallback="bogus")
        self.assert_silent(self.run_hook())


class ReadOnlyTest(AdvisoryCase):
    """零写证明：advisory 路径前后 state 文件字节不变。"""

    def test_state_file_unchanged_across_advisory(self):
        path = self.write_state(last_tick_at=STALE_TICK_MS)
        before = self.state_sha256(path)
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        # advisory 确实触发（证明读路径真实发生）
        self.assertIn("GLM CONDUCTOR QUOTA CLOCK ADVISORY", result.stdout)
        self.assertEqual(self.state_sha256(path), before)
        # 目录内无新增文件（无 temp/锁残留）
        self.assertEqual(
            sorted(os.listdir(os.path.dirname(path))),
            [os.path.basename(path)])


class CoexistenceTest(AdvisoryCase):
    """与既有 resume context 共存（resume 在前、空行分隔、advisory 在后）
    与仅 resume 时的逐字节回归红线。"""

    def test_resume_context_and_advisory_coexist(self):
        save_task(self.repo, units=[make_unit("wu-run", "running")])
        self.write_state(last_tick_at=STALE_TICK_MS)
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        ctx = parse_single_line_json(result.stdout)[
            "hookSpecificOutput"]["additionalContext"]
        # resume 在前、advisory 在后、空行分隔
        self.assertTrue(ctx.startswith("GLM CONDUCTOR RESUME CONTEXT"))
        self.assertIn("\n\nGLM CONDUCTOR QUOTA CLOCK ADVISORY", ctx)
        self.assertIn(TID, ctx)
        self.assertIn("quota-clock-status", ctx)
        self.assertLess(ctx.index("GLM CONDUCTOR RESUME CONTEXT"),
                        ctx.index("GLM CONDUCTOR QUOTA CLOCK ADVISORY"))

    def test_resume_only_output_byte_identical(self):
        # 有 active 任务、无 clock state → additionalContext 与纯
        # recovery 渲染逐字节一致（零噪音回归红线，非空路径）
        save_task(self.repo, units=[make_unit("wu-run", "running")])
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        ctx = parse_single_line_json(result.stdout)[
            "hookSpecificOutput"]["additionalContext"]
        expected = recovery.render_resume_context(
            recovery.build_recovery_summary(self.repo))
        self.assertEqual(ctx, expected)
        self.assertNotIn("QUOTA CLOCK ADVISORY", ctx)


if __name__ == "__main__":
    unittest.main()
