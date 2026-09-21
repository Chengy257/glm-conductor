#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hooks/session_start.py 回归测试（v2.4 Phase 3 P3-D 收口版）。

v2.3.1 起挂载的只读额度时钟 advisory 已随其后端在 v2.4 P3-D（Q4）
整体删除——本文件由 advisory 触发面测试改写为删除后的回归锚定：

harness 照 tests/test_recovery.py 先例：子进程直调 session_start.py
（钩子快照不热加载）+ tempfile 仓库 + UTF-8 解码；GLM_CONDUCTOR_HOME
注入 tempfile 临时目录（隔离本机用户级状态——纯隔离惯例，钩子已无
该读取面）。

覆盖：
  - 钩子在任何遗留用户级时钟 state 形态下恒静默（exit 0 且 stdout /
    stderr 均空）：无 state / 陈旧 tick state / 新鲜 tick state /
    损坏 JSON / 不认识的 schema 版本——advisory 面已删除，任何盘面
    都不再产生输出（零噪音红线对删除动作的回归锚定）；
  - active 任务 → 单行 JSON hookSpecificOutput（顶层仅
    hookSpecificOutput / 内层仅两键），additionalContext 与纯
    recovery 渲染逐字节一致——含「遗留时钟 state 同时在盘」形态
    （拼接行为与 advisory 标识头一并成为禁现词汇）；
  - 恢复渲染相关断言全保留（recovery 渲染零噪音回归红线）。

运行：
    cd <repo_root> && python -X utf8 -m unittest \
        tests.test_session_start_advisory -v
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import recovery, state

# 被测钩子脚本：仓库根 plugins/glm-conductor/hooks/session_start.py
SESSION_START = (Path(__file__).resolve().parents[1]
                 / "plugins/glm-conductor/hooks/session_start.py")

TID = "advisory-demo-1a2b3c"
# 矩阵合法的 solo 路由基座（与 test_recovery 同款）
ROUTE = {"mode": "solo", "delegability": "low", "assurance": "standard",
         "executor": "main", "continuity": "foreground"}

# 陈旧 / 新鲜的 tick 时刻（删除前的 state schema 词汇，仅用于构造
# 遗留盘面 fixture；钩子已不读取）
STALE_TICK_MS = 0        # 远古时刻：删除前口径下必判陈旧
FRESH_TICK_MS = 2 ** 62  # 遥远未来时刻：删除前口径下必判新鲜


def save_task(repo, task_id=TID, status="active"):
    """在临时仓库构造一个活动任务状态文件（v2.4 状态层正门构造，
    test_recovery_v24 v24_task 同款：new_task_state + save_state）。"""
    active = state.new_task_state(
        task_id, "advisory 冒烟测试目标", dict(ROUTE),
        repository_root=repo, status=status)
    state.save_state(repo, active)
    return active


def parse_single_line_json(text):
    """把 hook stdout 按单行 JSON 解析并返回 dict（test_recovery 同款）。"""
    stripped = text.strip()
    if "\n" in stripped:
        raise AssertionError("stdout 不是单行 JSON: %r" % text)
    return json.loads(stripped)


class AdvisoryCase(unittest.TestCase):
    """基座：tempfile home（GLM_CONDUCTOR_HOME 注入）+ tempfile repo。"""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="glm-advisory-home-")
        self.addCleanup(shutil.rmtree, self.home, True)
        self.repo = tempfile.mkdtemp(prefix="glm-advisory-repo-")
        self.addCleanup(shutil.rmtree, self.repo, True)

    # —— fixture 与执行装置 ——

    def write_legacy_state(self, *, last_tick_at=STALE_TICK_MS,
                           fallback=60, schema_version=1, raw=None):
        """在注入的临时 home 构造遗留时钟 state 盘面（v2.4 P3-D 前为
        用户级 home 下哈希命名的 JSON 文件；不经任何 runtime 写路径
        ——原写入面已删除，测试直写原始 JSON 构造遗留盘面，路径名仅
        为盘面形态示意；raw 非 None 时原样写入该字符串，构造损坏
        态）。"""
        path = Path(self.home) / "legacy-quota-state" / "deadbeef00ff00ff.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        if raw is not None:
            path.write_text(raw, encoding="utf-8")
            return str(path)
        payload = {
            "schema_version": schema_version,
            "provider_identity_hash": "deadbeef00ff00ff",
            "automation_id": "auto-advisory-1",
            "fallback_interval_minutes": fallback,
            "last_tick_at": last_tick_at,
            "status": "bound",
        }
        with open(str(path), "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        return str(path)

    def run_hook(self, stdin_text="{}"):
        """子进程直调 session_start.py（test_recovery 同款 UTF-8 解码）。"""
        return subprocess.run(
            [sys.executable, str(SESSION_START)],
            input=stdin_text, text=True, capture_output=True,
            encoding="utf-8", errors="replace",
            env=dict(os.environ,
                     ZCODE_PROJECT_DIR=str(self.repo),
                     **{"GLM_CONDUCTOR_HOME": self.home}))

    def assert_silent(self, result):
        """钩子完全安静契约：exit 0 且 stdout / stderr 均空。"""
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")


class SilenceTest(AdvisoryCase):
    """零噪音面：任何遗留时钟 state 盘面 → 输出恒空（advisory 面已删）。"""

    def test_no_legacy_state_silent(self):
        # 无任何遗留 state（unbound）→ stdout 恒空
        self.assert_silent(self.run_hook())

    def test_stale_tick_state_still_silent(self):
        # 陈旧 tick 遗留 state 在盘 → advisory 已删除，仍恒静默
        # （P3-D 删除动作的回归锚定：不得复活任何注入）
        self.write_legacy_state(last_tick_at=STALE_TICK_MS)
        self.assert_silent(self.run_hook())

    def test_fresh_tick_state_silent(self):
        # 新鲜 tick 遗留 state 在盘 → 恒静默
        self.write_legacy_state(last_tick_at=FRESH_TICK_MS)
        self.assert_silent(self.run_hook())

    def test_corrupt_state_json_silent(self):
        # 遗留 state 损坏 / 非法 JSON → 恒静默（fail-open 面保持）
        self.write_legacy_state(raw="{not-json")
        self.assert_silent(self.run_hook())

    def test_unknown_schema_version_silent(self):
        # schema 版本不认识（legacy / 未来版本）→ 恒静默
        self.write_legacy_state(schema_version=2)
        self.assert_silent(self.run_hook())


class ResumeRenderTest(AdvisoryCase):
    """恢复渲染面：输出与纯 recovery 渲染逐字节一致（含遗留 state
    同盘形态；advisory 标识头成为禁现词汇）。"""

    def _hook_context(self):
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

    def test_resume_only_output_byte_identical(self):
        # 有 active 任务、无遗留 state → additionalContext 与纯
        # recovery 渲染逐字节一致（零噪音回归红线，非空路径）
        save_task(self.repo)
        ctx = self._hook_context()
        expected = recovery.render_resume_context(
            recovery.build_recovery_summary(self.repo))
        self.assertEqual(ctx, expected)
        self.assertNotIn("QUOTA CLOCK ADVISORY", ctx)

    def test_resume_with_legacy_state_still_pure_recovery_render(self):
        # active 任务 + 陈旧 tick 遗留 state 同盘 → 输出仍与纯 recovery
        # 渲染逐字节一致（advisory 拼接行为已随 P3-D 删除，不得复活）
        save_task(self.repo)
        self.write_legacy_state(last_tick_at=STALE_TICK_MS)
        ctx = self._hook_context()
        expected = recovery.render_resume_context(
            recovery.build_recovery_summary(self.repo))
        self.assertEqual(ctx, expected)
        self.assertNotIn("QUOTA CLOCK ADVISORY", ctx)
        self.assertIn(TID, ctx)


if __name__ == "__main__":
    unittest.main()
