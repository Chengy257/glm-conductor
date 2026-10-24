#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hooks/permission_request_workflow.py 全判定链回归 + v2.7.0 W2
auto_approve_workflow state 键 / task.create_task 透传测试。

v2.7.0 W2（CreateWorkflow 任务级免确认，interview Q4 授权）测试面：

  1. 钩子判定链（harness 照 tests/test_session_start_advisory.py 先例：
     子进程直调 permission_request_workflow.py + tempfile 仓库 +
     UTF-8 解码；ZCODE_PROJECT_DIR 注入 tempfile 临时仓库，
     GLM_CONDUCTOR_HOME 注入临时目录隔离本机用户级状态——纯隔离
     惯例，本钩子无该读取面）：
       - 缺省弃权：无授权任务（无任务盘面 / 任务在盘未落授权键 /
         auto_approve_workflow=False）→ stdout 空且 exit 0（默认
         人工确认行为不变）；
       - 放行 JSON 逐字节断言：非终态授权任务 + path 型提交（gen/
         与 .zcode/workflow-drafts/ 两个许可目录）→ stdout 恰为
         接口定义的单行紧凑 JSON（黄金串逐字节比对，不 import 实现
         常量——实现漂移即测试红）；json.loads 后同时断言三层键
         形状闭合（无 permissionUpdates / updatedPermissions /
         updatedInput 等多余键——decision-only 零持久化红线）；
       - inline 无 path 弃权；越界路径弃权（temp 仓库外 + 仓库内
         非许可目录）；终态任务带旗标弃权（completed / cancelled /
         failed 逐态在盘——钩子对终态自然失权）；
       - 状态文件损坏跳过（损坏 + 授权共存 → 仍放行）；非法 stdin
         （空串 / 非 JSON / 合法 JSON 非 dict）弃权 exit 0；
         hookEventName 或 toolName 不符弃权。
  2. state / 任务层：new_task_state 缺省无键、True 落键（is True）、
     False 不落键（「未授权即无键」语义，与 worker_model pin 同款）；
     validate_state 对 True / False 合法、对 1 / 0 / "true" / None /
     [] 等非 bool 恰报 STATE_AUTO_APPROVE_INVALID；
     task.create_task(auto_approve_workflow=True) 落盘键、缺省建任务
     盘上无键。

v2.8.0 W-A1（诊断装备化）扩容面——stderr 原因码矩阵：

  3. 弃权契约从「stderr 空」升级为「stderr 恰一行
     PERMISSION_HOOK abstain: reason=<code> detail=…」（决策行为零
     变更：stdout 空 + exit 0 不变）；放行契约增 stderr 恰一行
     PERMISSION_HOOK allow: task=<id> path=<归一后> path_raw=<归一前>
     repo_root=<root> repo_root_source=<源>。原因码恰六值对六类弃权
     分支：bad_payload / bad_event / bad_tool / no_path /
     path_outside / no_authorized_task；path_outside 与放行行的
     detail 必含归一前/归一后 path、repo_root 及其来源（H2/H3 判别
     装备），no_authorized_task 行含 repo_root 及来源（H4 判别）。
     另增 cwd 回退口径用例（剥离 ZCODE_PROJECT_DIR 后 repo_root
     取 cwd、来源标注 cwd——H2 口径复现面）。

任务盘面 fixture 一律走 v2.4 状态层正门（new_task_state + save_state，
test_session_start_advisory.save_task 同款）或直写损坏文件构造盘面；
tempfile 隔离，绝不触碰真实仓库 .glm-conductor/。仅 Python 3 标准库。

运行：
    cd <repo_root> && python3 -X utf8 -m unittest tests.test_auto_approve_workflow -v
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# 模块引导照 tests/test_cli_v24.py 同款：仓库根下 runtime 入 path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import messages, state, task  # noqa: E402

# 被测钩子脚本：仓库根 plugins/glm-conductor/hooks/permission_request_workflow.py
HOOK_PATH = (Path(__file__).resolve().parents[1]
             / "plugins/glm-conductor/hooks/permission_request_workflow.py")

# 放行单行 JSON 黄金形态（接口定义：紧凑分隔符 + 尾随换行；硬编码
# 基线逐字节比对，绝不 import 实现常量——实现漂移即测试红）
ALLOW_GOLDEN = (
    '{"hookSpecificOutput":{"hookEventName":"PermissionRequest",'
    '"decision":{"behavior":"allow","message":"glm-conductor '
    'task-level auto-approve (interview-authorized, path-type '
    'submission)"}},"suppressOutput":true}')

TID = "auto-approve-hook-1a2b3c"
TID_UNFLAGGED = "auto-approve-hook-2b3c4d"
TID_FALSE = "auto-approve-hook-3c4d5e"
TID_DONE = "auto-approve-hook-4d5e6f"
TID_CANCELLED = "auto-approve-hook-5e6f7a"
TID_FAILED = "auto-approve-hook-6f7a8b"
TID_CORRUPT = "auto-approve-corrupt-7a8b9c"
TID_AUTHORIZED = "auto-approve-live-8b9c0d"

# 矩阵合法的 solo 路由基座（test_session_start_advisory 同款精简形）
ROUTE = {"mode": "solo", "assurance": "standard"}


class HookCase(unittest.TestCase):
    """钩子判定链基座：tempfile 仓库（ZCODE_PROJECT_DIR 注入）+
    tempfile home（GLM_CONDUCTOR_HOME 隔离注入）。"""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="glm-autoapprove-home-")
        self.addCleanup(shutil.rmtree, self.home, True)
        self.repo = tempfile.mkdtemp(prefix="glm-autoapprove-repo-")
        self.addCleanup(shutil.rmtree, self.repo, True)

    # —— fixture 与执行装置 ——

    def save_task(self, task_id=TID, status="active", auto_approve=False):
        """正门构造任务盘面（new_task_state + save_state，
        test_session_start_advisory.save_task 同款；auto_approve
        透传 W2 授权键；首存终态经转换门放行）。"""
        active = state.new_task_state(
            task_id, "auto-approve 钩子冒烟测试目标", dict(ROUTE),
            repository_root=self.repo, status=status,
            auto_approve_workflow=auto_approve)
        state.save_state(self.repo, active)
        return active

    def write_corrupt_task(self, task_id=TID_CORRUPT):
        """绕过 runtime 直写损坏 state.json（构造「损坏 + 授权共存」
        盘面——损坏不是授权证据，也不是阻断理由）。"""
        path = state.state_path(self.repo, task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not-json", encoding="utf-8")
        return str(path)

    def run_hook(self, stdin_text, with_project_dir=True):
        """子进程直调钩子（test_session_start_advisory.run_hook 同款
        UTF-8 解码；ZCODE_PROJECT_DIR 指向临时仓库；with_project_dir=
        False 剥离 ZCODE_PROJECT_DIR——repo_root 回退 cwd 口径用例）。"""
        env = dict(os.environ,
                   GLM_CONDUCTOR_HOME=self.home)
        if with_project_dir:
            env["ZCODE_PROJECT_DIR"] = str(self.repo)
        else:
            env.pop("ZCODE_PROJECT_DIR", None)
        return subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input=stdin_text, text=True, capture_output=True,
            encoding="utf-8", errors="replace", env=env)

    def event(self, path=None, event_name="PermissionRequest",
              tool_name="CreateWorkflow"):
        """构造 PermissionRequest 事件 payload（path=None 即 inline
        script 提交形态——toolInput 无 path 键）。"""
        tool_input = {} if path is None else {"path": path}
        return json.dumps({"hookEventName": event_name,
                           "toolName": tool_name,
                           "toolInput": tool_input})

    def gen_path(self, name="compiled.mjs"):
        """许可目录一：仓库 gen/ 之下的脚本路径。"""
        return os.path.join(self.repo, "gen", name)

    def drafts_path(self, name="compiled.mjs"):
        """许可目录二：仓库 .zcode/workflow-drafts/ 之下的脚本路径。"""
        return os.path.join(self.repo, ".zcode", "workflow-drafts", name)

    def assert_abstain(self, result, reason, detail_parts=()):
        """弃权契约（v2.8.0 W-A1 升级面）：exit 0 + stdout 空 +
        stderr 恰一行 `PERMISSION_HOOK abstain: reason=<code>
        detail=…`——决策行为不变（默认人工确认），观测面必报原因码；
        detail_parts 为 detail 必含的子串（H2/H3/H4 判别装备锚）。"""
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr.count("\n"), 1,
            "弃权 stderr 应恰一行: %r" % (result.stderr,))
        self.assertTrue(
            result.stderr.startswith(
                "PERMISSION_HOOK abstain: reason=%s detail=" % (reason,)),
            "reason 码不符: %r" % (result.stderr,))
        for part in detail_parts:
            self.assertIn(part, result.stderr)

    def assert_allow(self, result, task_id=TID, raw_path=None):
        """放行契约：exit 0 + stdout 恰为接口定义的单行 JSON——先逐字节
        比对黄金串（含尾随换行），再 json.loads 断言三层键形状闭合且无
        permissionUpdates 等多余键；v2.8.0 W-A1 起 stderr 另恰一行
        `PERMISSION_HOOK allow: task=<id> path=<归一后> path_raw=<归一前>
        repo_root=<root> repo_root_source=<源>`（观测面四元组，H2/H3
        判别装备锚）。"""
        self.assertEqual(result.returncode, 0, result.stderr)
        # 逐字节：单行紧凑 JSON + 尾随换行（实现漂移即红）
        self.assertEqual(result.stdout, ALLOW_GOLDEN + "\n")
        payload = json.loads(result.stdout)
        # 顶层仅两键：decision-only——无 permissionUpdates /
        # updatedPermissions / updatedInput 等持久化面词汇
        self.assertEqual(sorted(payload.keys()),
                         ["hookSpecificOutput", "suppressOutput"])
        self.assertIs(payload["suppressOutput"], True)
        for forbidden in ("permissionUpdates", "updatedPermissions",
                          "updatedInput"):
            self.assertNotIn(forbidden, payload)
        block = payload["hookSpecificOutput"]
        self.assertEqual(sorted(block.keys()),
                         ["decision", "hookEventName"])
        self.assertEqual(block["hookEventName"], "PermissionRequest")
        decision = block["decision"]
        self.assertEqual(sorted(decision.keys()), ["behavior", "message"])
        self.assertEqual(decision["behavior"], "allow")
        # 观测行（恰一行 allow：task / 归一后 path / 归一前 path /
        # repo_root / repo_root_source 四元组齐备）
        self.assertEqual(result.stderr.count("\n"), 1,
                         "放行 stderr 应恰一行: %r" % (result.stderr,))
        expected_parts = ["PERMISSION_HOOK allow: ",
                          "task=%s" % (task_id,),
                          "path=%s" % (os.path.abspath(raw_path),),
                          "path_raw=%s" % (raw_path,),
                          "repo_root=%s" % (self.repo,),
                          "repo_root_source=ZCODE_PROJECT_DIR"]
        for part in expected_parts:
            self.assertIn(part, result.stderr)
        return payload


class HookDecisionChainTest(HookCase):
    """判定链五步逐闸锚定：任一不过即弃权（stderr 原因码矩阵，v2.8.0
    W-A1），全过才放行（stderr allow 观测行）。"""

    # —— 判定链 (5) 缺省弃权：无授权任务 ——

    def test_no_task_at_all_abstains(self):
        # 无任何任务盘面（无 .glm-conductor/tasks/）→ 弃权 no_authorized_task
        self.assert_abstain(
            self.run_hook(self.event(self.gen_path())),
            "no_authorized_task")

    def test_task_without_flag_abstains(self):
        # 任务在盘但未落授权键（缺省建任务）→ 弃权 no_authorized_task
        self.save_task(TID_UNFLAGGED)
        self.assert_abstain(
            self.run_hook(self.event(self.gen_path())),
            "no_authorized_task")

    def test_task_with_explicit_false_abstains(self):
        # auto_approve_workflow=False（显式未授权）→ 弃权 no_authorized_task
        self.save_task(TID_FALSE, auto_approve=False)
        self.assert_abstain(
            self.run_hook(self.event(self.gen_path())),
            "no_authorized_task")

    # —— 判定链 (5) 放行：非终态授权任务 + 许可目录 path ——

    def test_authorized_task_gen_path_allows_byte_exact(self):
        # active 授权任务 + gen/ path → 放行（逐字节黄金串 + 形状闭合
        # + stderr allow 观测行四元组）
        self.save_task(TID, auto_approve=True)
        self.assert_allow(
            self.run_hook(self.event(self.gen_path())),
            task_id=TID, raw_path=self.gen_path())

    def test_authorized_task_drafts_path_allows(self):
        # .zcode/workflow-drafts/ 是第二个许可目录 → 同样放行
        self.save_task(TID, auto_approve=True)
        self.assert_allow(
            self.run_hook(self.event(self.drafts_path())),
            task_id=TID, raw_path=self.drafts_path())

    # —— 判定链 (3) inline 无 path 弃权 ——

    def test_inline_without_path_abstains(self):
        # 授权任务在盘但 toolInput 无 path（inline 提交）→ 弃权 no_path
        self.save_task(TID, auto_approve=True)
        self.assert_abstain(
            self.run_hook(self.event(path=None)), "no_path")

    # —— 判定链 (4) 越界路径弃权（detail 四元组 = H2/H3 判别装备）——

    def test_path_outside_temp_repo_abstains(self):
        # temp 仓库之外（父目录下兄弟路径）→ 弃权 path_outside；detail
        # 必含归一前 path / 归一后 path / repo_root / 来源标注
        self.save_task(TID, auto_approve=True)
        outside = os.path.join(os.path.dirname(self.repo),
                               "elsewhere", "compiled.mjs")
        self.assert_abstain(
            self.run_hook(self.event(outside)), "path_outside",
            detail_parts=("path_raw=%r" % (outside,),
                          "path_norm=%r" % (os.path.abspath(outside),),
                          "repo_root=%r" % (self.repo,),
                          "repo_root_source=ZCODE_PROJECT_DIR"))

    def test_path_inside_repo_outside_permitted_dirs_abstains(self):
        # 仓库之内但不在 gen/ 或 .zcode/workflow-drafts/ 之下 → 弃权
        self.save_task(TID, auto_approve=True)
        inside = os.path.join(self.repo, "src", "compiled.mjs")
        self.assert_abstain(
            self.run_hook(self.event(inside)), "path_outside",
            detail_parts=("path_norm=%r" % (inside,),
                          "repo_root=%r" % (self.repo,)))

    def test_relative_path_normalizes_against_hook_cwd(self):
        # 相对 path 按 hook 进程 cwd 归一（ZCODE_PROJECT_DIR 之外的
        # 基）→ 与注入 repo_root 不一致即弃权——H3 相对 path 归一基
        # 口径的复现面（detail 同时报归一前相对形与归一后绝对形）
        self.save_task(TID, auto_approve=True)
        relative = "gen" + os.sep + "compiled.mjs"
        self.assert_abstain(
            self.run_hook(self.event(relative)),
            "path_outside",
            detail_parts=("path_raw=%r" % (relative,),
                          "path_norm=%r" % (os.path.abspath(relative),),
                          "repo_root=%r" % (self.repo,),
                          "repo_root_source=ZCODE_PROJECT_DIR"))

    def test_cwd_fallback_source_is_reported(self):
        # 剥离 ZCODE_PROJECT_DIR → repo_root 回退 cwd 且来源标注 cwd
        #（H2 口径失配的复现面：root 取 cwd 时授权盘面扫不到）
        self.save_task(TID, auto_approve=True)
        self.assert_abstain(
            self.run_hook(self.event(self.gen_path()),
                          with_project_dir=False),
            "path_outside",
            detail_parts=("repo_root_source=cwd",))

    # —— 判定链 (5) 终态任务自然失权 ——

    def test_terminal_tasks_with_flag_abstain(self):
        # completed / cancelled / failed 逐态带旗标在盘 → 全部弃权
        #（任一状态被误判非终态即翻转放行，测试即红）
        self.save_task(TID_DONE, status="completed", auto_approve=True)
        self.save_task(TID_CANCELLED, status="cancelled",
                       auto_approve=True)
        self.save_task(TID_FAILED, status="failed", auto_approve=True)
        self.assert_abstain(
            self.run_hook(self.event(self.gen_path())),
            "no_authorized_task",
            detail_parts=("repo_root=%r" % (self.repo,),))

    # —— 判定链 (5) 状态文件损坏跳过 ——

    def test_corrupt_state_skipped_authorized_still_allows(self):
        # 一个损坏 state.json + 一个授权任务共存 → 损坏跳过、仍放行
        #（观测行报告的是真正命中授权的任务 id）
        self.write_corrupt_task()
        self.save_task(TID_AUTHORIZED, auto_approve=True)
        self.assert_allow(
            self.run_hook(self.event(self.gen_path())),
            task_id=TID_AUTHORIZED, raw_path=self.gen_path())

    # —— 判定链 (1) 非法 stdin ——

    def test_invalid_stdin_abstains(self):
        # 空串 / 非 JSON / 合法 JSON 但非 dict → 一律弃权 bad_payload
        self.save_task(TID, auto_approve=True)
        for raw in ("", "not-json{", "[1, 2]"):
            self.assert_abstain(self.run_hook(raw), "bad_payload")

    # —— 判定链 (2) 事件形状不符 ——

    def test_event_name_mismatch_abstains(self):
        # hookEventName 非 PermissionRequest（matcher 外事件）→ bad_event
        self.save_task(TID, auto_approve=True)
        self.assert_abstain(
            self.run_hook(
                self.event(self.gen_path(), event_name="PreToolUse")),
            "bad_event")

    def test_tool_name_mismatch_abstains(self):
        # toolName 非 CreateWorkflow → bad_tool
        self.save_task(TID, auto_approve=True)
        self.assert_abstain(
            self.run_hook(
                self.event(self.gen_path(), tool_name="Bash")),
            "bad_tool")

    def test_empty_payload_abstains(self):
        # 空事件 dict（键全缺）→ 弃权（首闸是 hookEventName 形状）
        self.save_task(TID, auto_approve=True)
        self.assert_abstain(self.run_hook("{}"), "bad_event")


class AutoApproveStateKeyTest(unittest.TestCase):
    """v2.7.0 W2 state 键形状：构造落键语义 + validate_state 收口。"""

    TID = "auto-approve-state-1a2b3c"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(
            prefix="glm-autoapprove-state-")
        self.addCleanup(self._tmp.cleanup)
        self.repo = self._tmp.name

    def new_state(self, **kwargs):
        """缺省基座 state（ solo 路由，temp 仓库根绑定）。"""
        return state.new_task_state(
            self.TID, "auto-approve state 键测试目标", dict(ROUTE),
            repository_root=self.repo, **kwargs)

    # —— new_task_state 落键语义（未授权即无键） ——

    def test_new_task_state_default_has_no_key(self):
        st = self.new_state()
        self.assertNotIn("auto_approve_workflow", st)

    def test_new_task_state_true_writes_literal_true(self):
        st = self.new_state(auto_approve_workflow=True)
        self.assertIn("auto_approve_workflow", st)
        self.assertIs(st["auto_approve_workflow"], True)
        self.assertEqual(state.validate_state(st), [])

    def test_new_task_state_false_writes_no_key(self):
        st = self.new_state(auto_approve_workflow=False)
        self.assertNotIn("auto_approve_workflow", st)

    # —— validate_state 形状收口 ——

    def test_validate_state_accepts_true_and_false(self):
        for value in (True, False):
            st = self.new_state()
            st["auto_approve_workflow"] = value
            self.assertEqual(state.validate_state(st), [])

    def test_validate_state_rejects_non_bool(self):
        # 1 / 0 / "true" / None / [] 等非 bool → 恰一条
        # STATE_AUTO_APPROVE_INVALID（基座合法 → 错误唯一可归因）
        for bad in (1, 0, "true", None, []):
            st = self.new_state()
            st["auto_approve_workflow"] = bad
            self.assertEqual(
                state.validate_state(st),
                [messages.STATE_AUTO_APPROVE_INVALID],
                "auto_approve_workflow=%r 应被拒绝" % (bad,))


class CreateTaskPassthroughTest(unittest.TestCase):
    """task.create_task 的 auto_approve_workflow 透传落盘面。"""

    TID = "auto-approve-create-1a2b3c"
    TID_DEFAULT = "auto-approve-create-2b3c4d"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(
            prefix="glm-autoapprove-create-")
        self.addCleanup(self._tmp.cleanup)
        self.repo = self._tmp.name

    def test_create_task_true_persists_key(self):
        task.create_task(self.repo, self.TID, "透传落盘测试目标",
                         dict(ROUTE), auto_approve_workflow=True)
        st = state.load_state(self.repo, self.TID)
        self.assertIsNotNone(st)
        self.assertIs(st["auto_approve_workflow"], True)

    def test_create_task_default_persists_no_key(self):
        task.create_task(self.repo, self.TID_DEFAULT, "缺省无键测试目标",
                         dict(ROUTE))
        st = state.load_state(self.repo, self.TID_DEFAULT)
        self.assertIsNotNone(st)
        self.assertNotIn("auto_approve_workflow", st)


if __name__ == "__main__":
    unittest.main()
