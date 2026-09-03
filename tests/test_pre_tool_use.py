#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hooks.pre_tool_use 子进程冒烟测试（v2 Layer B 注入 + Bash 策略门控）。

以子进程方式运行 plugins/glm-conductor/hooks/pre_tool_use.py，验证其
advisory fail-open 契约与 Bash 策略门控契约的可观测行为：

Layer B 注入路径（matcher "Agent|Task"）：
  - 存在声明 ownership.files（非空）的活动任务：stdout 输出单行 JSON
    hookSpecificOutput（hookEventName=="PreToolUse"，additionalContext
    含 task_id、ownership 路径与 Layer A 兜底警告），stderr 恒空，exit 0；
  - 无活动任务 / 活动任务未声明 ownership / 任务已终态：静默放行
    （exit 0，stdout 与 stderr 均空）；
  - stdin 空串 / 非法 JSON：按空对象容错，照常注入并 exit 0；
  - 多任务：逐任务一段，最多列 3 个并注明省略。

Bash 策略门控路径（matcher "Bash"，B7.2）：
  - Bash + deny 规则命令 + 活动任务：stdout 单行 JSON
    permissionDecision=="deny"，reason 含 rule 名与截断命令片段；
  - Bash + ask 规则命令（git push）+ assurance=high 活动任务：
    permissionDecision=="ask"；同命令 + standard 任务 → stdout 空
    （allow 静默，不升级）；
  - Bash + 普通命令（ls）+ 活动任务 → 静默；
  - Bash + deny 命令 + 无活动任务（空仓库）→ 静默（零干预）；
  - tool_input 非 dict / command 缺失 → 容错放行静默；
  - payload {}（tool_name 缺失）→ 按现状走注入路径（兼容选择）。

hooks.json：Stop、PreToolUse（Agent|Task 与 Bash 两条）条目并存。

仅 Python 3 标准库（unittest + subprocess + tempfile），零第三方依赖；
被检仓库目录由 tempfile.TemporaryDirectory 提供，不污染真实工作区；
状态 fixture 经 sys.path 接插件根后用 runtime.state 构造并保存。

运行：
    cd <repo_root> && python3 -m unittest tests.test_pre_tool_use -v
"""

import sys, unittest
import json, os, subprocess, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import dispatch_wave, state, task_manager

# 被测钩子脚本：仓库根 plugins/glm-conductor/hooks/pre_tool_use.py
PRE_TOOL_USE = (Path(__file__).resolve().parents[1]
                / "plugins/glm-conductor/hooks/pre_tool_use.py")

# 被测钩子清单：plugins/glm-conductor/hooks/hooks.json（Stop + PreToolUse）
HOOKS_JSON = (Path(__file__).resolve().parents[1]
              / "plugins/glm-conductor/hooks/hooks.json")

TID = "demo-task-1a2b3c"
# H2 夹具迁移：delegate 路由在规则 R4 下要求非空 ownership/verification，
# 本文件被测的注入 / 策略门控行为与路由模式无关——基座改用矩阵合法的
# solo 路由；ask 升级场景用矩阵合法的 audit 路由表达 assurance=high
ROUTE = {"mode": "solo", "delegability": "low", "assurance": "standard",
         "executor": "main", "continuity": "foreground"}
# Bash 策略 ask 升级场景用的高保障 route（assurance=high；矩阵一致 → audit，
# new_task_state 缺省推导 review.required=true，满足规则 R3）
HIGH_ROUTE = {"mode": "audit", "delegability": "low", "assurance": "high",
              "executor": "main", "continuity": "foreground"}
OWNED = ["plugins/glm-conductor/hooks/pre_tool_use.py",
         "tests/test_pre_tool_use.py"]


def run_hook(stdin_text, project_dir):
    """以子进程运行 pre_tool_use.py，返回 CompletedProcess。

    text=True + 显式 encoding="utf-8"：钩子按运行时契约恒以 UTF-8 字节
    写 stdout/stderr（含中文报文），父进程必须按 UTF-8 解码——不指定
    encoding 时按父进程 locale（如 en-US runner 的 cp1252）严格解码，
    中文字节（如损坏 reason 的 0x8D）会 UnicodeDecodeError（CI Windows
    矩阵实测）；errors="replace" 兜底异常字节。
    ZCODE_PROJECT_DIR 指向被检仓库（钩子据此发现活动任务），其余环境变量
    原样继承。
    """
    return subprocess.run(
        [sys.executable, str(PRE_TOOL_USE)],
        input=stdin_text, text=True, capture_output=True,
        encoding="utf-8", errors="replace",
        env=dict(os.environ, ZCODE_PROJECT_DIR=str(project_dir)))


def save_task(repo, task_id=TID, ownership_files=OWNED, status="executing",
              route=None):
    """在临时仓库构造一个任务状态文件（runtime.state 构造 + 校验原子保存）。

    route 缺省用标准保障 ROUTE；策略 ask 升级用例传 HIGH_ROUTE。
    """
    active = state.new_task_state(
        task_id, "Layer B 注入冒烟测试目标", dict(route or ROUTE),
        ownership_files=ownership_files, status=status)
    if status == "completed":
        # H1 状态转换门（P0-1）：completed 不能经公共 API 首存/直达——
        # 夹具改走合法迁移链 executing→finalizing→完成门内部提交，
        # 被测语义不变（盘上存在终态任务 = 非活动任务 → 不注入）
        for step in ("executing", "finalizing"):
            active["status"] = step
            state.save_state(repo, active)
        state.commit_completion(repo, task_id)
        return
    state.save_state(repo, active)


def parse_single_line_json(text):
    """把 hook stdout 按单行 JSON 解析并返回 dict（多行 / 非法即断言失败）。"""
    stripped = text.strip()
    if "\n" in stripped:
        raise AssertionError("stdout 不是单行 JSON: %r" % text)
    return json.loads(stripped)


def bash_stdin(command):
    """构造 Bash 策略路径的 PreToolUse 事件 stdin（Claude 兼容 JSON）。"""
    return json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": command}})


class OwnershipInjectionTest(unittest.TestCase):
    """存在声明 ownership 的活动任务：注入 additionalContext 后放行。"""

    def test_active_task_with_ownership_injects_reminder(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp)
            result = run_hook(
                '{"tool_name":"Agent","tool_input":{"prompt":"do it"}}', tmp)
            self.assertEqual(result.returncode, 0)
            # advisory 层成功路径也不写 stderr
            self.assertEqual(result.stderr, "")
            payload = parse_single_line_json(result.stdout)
            # Zod 严格校验形态：顶层仅 hookSpecificOutput，内层仅两键
            self.assertEqual(sorted(payload.keys()), ["hookSpecificOutput"])
            out = payload["hookSpecificOutput"]
            self.assertEqual(sorted(out.keys()),
                             ["additionalContext", "hookEventName"])
            self.assertEqual(out["hookEventName"], "PreToolUse")
            ctx = out["additionalContext"]
            self.assertIn("OWNERSHIP REMINDER (Layer B)", ctx)
            self.assertIn(TID, ctx)
            for owned in OWNED:
                self.assertIn(owned, ctx)
            self.assertIn("Layer A", ctx)
            self.assertIn("Layer B", ctx)


class SilentPassTest(unittest.TestCase):
    """无注入对象：静默放行（exit 0，stdout 与 stderr 均空）。"""

    def test_empty_repo_exit0_silent(self):
        # 无活动任务（空 tempdir）：普通派发零干预
        with tempfile.TemporaryDirectory() as tmp:
            result = run_hook("{}", tmp)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "")

    def test_active_task_without_ownership_silent(self):
        # 活动任务存在但 ownership.files 为空 → 不注入
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp, ownership_files=[])
            result = run_hook("{}", tmp)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "")

    def test_terminal_task_not_injected(self):
        # 终态任务（completed）不算活动任务 → 不注入
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp, status="completed")
            result = run_hook("{}", tmp)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "")


class StdinToleranceTest(unittest.TestCase):
    """stdin 容错：空串 / 非法 JSON 一律按空对象处理，照常注入并 exit 0。"""

    def test_empty_and_invalid_json_stdin_exit0(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp)
            for raw in ("", "not-json"):
                with self.subTest(stdin=repr(raw)):
                    result = run_hook(raw, tmp)
                    self.assertEqual(result.returncode, 0)
                    payload = parse_single_line_json(result.stdout)
                    self.assertEqual(
                        payload["hookSpecificOutput"]["hookEventName"],
                        "PreToolUse")


class MaxInjectedTasksTest(unittest.TestCase):
    """多任务：逐任务一段，最多列 3 个并注明省略。"""

    def test_five_tasks_only_three_listed_with_omission_note(self):
        ids = ["task-a-%d" % i for i in range(5)]
        with tempfile.TemporaryDirectory() as tmp:
            for tid in ids:
                save_task(tmp, task_id=tid)
            result = run_hook("{}", tmp)
            self.assertEqual(result.returncode, 0)
            ctx = parse_single_line_json(result.stdout)[
                "hookSpecificOutput"]["additionalContext"]
            # 前 3 个（目录名序）逐任务一段列出
            self.assertEqual(ctx.count("declares ownership:"), 3)
            for tid in ids[:3]:
                self.assertIn(tid, ctx)
            # 其余不出现，且注明省略数量
            for tid in ids[3:]:
                self.assertNotIn(tid, ctx)
            self.assertIn("(2 more active task(s) with declared ownership "
                          "omitted)", ctx)


class HooksManifestTest(unittest.TestCase):
    """hooks.json：PreToolUse 条目增补后与 Stop 条目并存、matcher 正确。"""

    def test_stop_and_pretooluse_coexist(self):
        with open(str(HOOKS_JSON), "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        hooks = manifest["hooks"]
        # Stop 条目仍在，且仍指向 stop_gate.py（逐字节未动的行为佐证）
        self.assertIn("Stop", hooks)
        self.assertIn("stop_gate.py", json.dumps(hooks["Stop"]))
        # PreToolUse 条目存在且 matcher 精确为 Agent|Task
        self.assertIn("PreToolUse", hooks)
        entries = hooks["PreToolUse"]
        self.assertIsInstance(entries, list)
        matchers = [entry.get("matcher") for entry in entries
                    if isinstance(entry, dict)]
        self.assertIn("Agent|Task", matchers)
        # 内层 hook args 指向 pre_tool_use.py
        pre_args = [arg for entry in entries
                    for hook in entry.get("hooks", [])
                    for arg in hook.get("args", [])]
        self.assertTrue(
            any(arg.endswith("pre_tool_use.py") for arg in pre_args),
            "PreToolUse 条目应指向 pre_tool_use.py: %r" % pre_args)

    def test_bash_matcher_entry_appended(self):
        # B7.2：PreToolUse 数组追加 Bash matcher 第二项（Agent|Task 项
        # 不动）；v2.2 C6（wu-22-C6）再追加 CronCreate 第三项——既有
        # 两条的相对序与内容零改动（additive，hooks.json +3 条目之一）
        with open(str(HOOKS_JSON), "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        entries = manifest["hooks"]["PreToolUse"]
        matchers = [entry.get("matcher") for entry in entries
                    if isinstance(entry, dict)]
        self.assertEqual(matchers, ["Agent|Task", "Bash", "CronCreate"])
        hook = entries[matchers.index("Bash")]["hooks"][0]
        self.assertEqual(hook["type"], "process")
        self.assertEqual(hook["command"], "python3")
        self.assertEqual(hook["timeoutMs"], 3000)
        self.assertEqual(
            hook["args"], ["${ZCODE_PLUGIN_ROOT}/hooks/pre_tool_use.py"])


class BashPolicyDenyTest(unittest.TestCase):
    """Bash + deny 规则 + 活动任务：stdout 单行 JSON permissionDecision=deny。"""

    def test_deny_command_with_active_task_emits_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp)
            result = run_hook(bash_stdin("rm -rf ./build"), tmp)
            self.assertEqual(result.returncode, 0)
            # 决策成功路径不写 stderr
            self.assertEqual(result.stderr, "")
            payload = parse_single_line_json(result.stdout)
            # Zod 严格校验形态：顶层仅 hookSpecificOutput，无多余顶层键
            self.assertEqual(sorted(payload.keys()), ["hookSpecificOutput"])
            out = payload["hookSpecificOutput"]
            self.assertEqual(
                sorted(out.keys()),
                ["hookEventName", "permissionDecision",
                 "permissionDecisionReason"])
            self.assertEqual(out["hookEventName"], "PreToolUse")
            self.assertEqual(out["permissionDecision"], "deny")
            reason = out["permissionDecisionReason"]
            self.assertIn("GLM CONDUCTOR POLICY: deny", reason)
            self.assertIn("rm-destructive", reason)
            self.assertIn("rm -rf ./build", reason)

    def test_deny_reason_command_truncated_to_60_chars(self):
        # reason 模板：GLM CONDUCTOR POLICY: <decision> (<rule>).
        # command: <截断 60 字符命令>
        command = "rm -rf " + "x" * 100
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp)
            result = run_hook(bash_stdin(command), tmp)
            out = parse_single_line_json(result.stdout)["hookSpecificOutput"]
            self.assertEqual(
                out["permissionDecisionReason"],
                "GLM CONDUCTOR POLICY: deny (rm-destructive). command: %s"
                % command[:60])

    def test_deny_ignores_assurance_level(self):
        # deny 无视 assurance：high 任务同样 deny（而非升级成 ask）
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp, task_id="demo-task-high1", route=HIGH_ROUTE)
            result = run_hook(bash_stdin("git reset --hard"), tmp)
            out = parse_single_line_json(result.stdout)["hookSpecificOutput"]
            self.assertEqual(out["permissionDecision"], "deny")
            self.assertIn("git-reset-hard", out["permissionDecisionReason"])


class BashPolicyAskTest(unittest.TestCase):
    """Bash + ask 规则（git push）：assurance=high 升级 ask，standard 静默。"""

    def test_git_push_with_high_assurance_task_asks(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp, task_id="demo-task-high2", route=HIGH_ROUTE)
            result = run_hook(bash_stdin("git push origin main"), tmp)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, "")
            out = parse_single_line_json(result.stdout)["hookSpecificOutput"]
            self.assertEqual(out["hookEventName"], "PreToolUse")
            self.assertEqual(out["permissionDecision"], "ask")
            reason = out["permissionDecisionReason"]
            self.assertIn("GLM CONDUCTOR POLICY: ask", reason)
            self.assertIn("git-push", reason)
            self.assertIn("git push origin main", reason)

    def test_git_push_with_standard_assurance_task_silent(self):
        # ask 命中但 assurance=standard → 不升级，allow 完全静默
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp)  # ROUTE：assurance=standard
            result = run_hook(bash_stdin("git push origin main"), tmp)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "")


class BashPolicyAllowSilentTest(unittest.TestCase):
    """策略 allow 路径完全静默（stdout 恒空），含零干预与容错场景。"""

    def test_plain_command_with_active_task_silent(self):
        # 活动任务 + 无规则命中的普通命令 → 静默
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp)
            result = run_hook(bash_stdin("ls"), tmp)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "")

    def test_deny_command_without_active_task_silent(self):
        # 空仓库（无活动任务）→ 策略零干预：deny 规则也不触发，静默
        with tempfile.TemporaryDirectory() as tmp:
            result = run_hook(bash_stdin("rm -rf ./build"), tmp)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "")

    def test_tool_input_not_dict_tolerated_silent(self):
        # tool_input 非 dict → command 容错为 None → 放行静默
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp)
            result = run_hook(
                '{"tool_name":"Bash","tool_input":"oops"}', tmp)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "")


class NonBashDispatchCompatTest(unittest.TestCase):
    """payload {} / tool_name 缺失按现状走注入路径（B7.2 显式兼容选择）。"""

    def test_empty_payload_routes_to_injection_path(self):
        # Bash matcher 场景下 stdin 空 / 形态异常（payload {}）按非 Bash
        # 路径处理：有声明 ownership 的活动任务时照常注入（现状一致）
        with tempfile.TemporaryDirectory() as tmp:
            save_task(tmp)
            result = run_hook("{}", tmp)
            self.assertEqual(result.returncode, 0)
            out = parse_single_line_json(result.stdout)["hookSpecificOutput"]
            self.assertEqual(out["hookEventName"], "PreToolUse")
            self.assertIn("OWNERSHIP REMINDER (Layer B)",
                          out["additionalContext"])

    def test_empty_payload_without_active_task_silent(self):
        # 无活动任务（空仓库）：现状兼容，静默放行
        with tempfile.TemporaryDirectory() as tmp:
            result = run_hook("{}", tmp)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "")


# —— v2.1 M2 dispatch permit 门（wu-21-03，§20.2/§20.3 hook 侧） ——

PT_TID = "permit-gate-test-1a2b3c"
# delegate 路由（矩阵合法：delegability high + assurance standard）
PT_ROUTE = {"mode": "delegate", "delegability": "high",
            "assurance": "standard", "executor": "flash-implementer",
            "continuity": "foreground"}
PT_UNIT = {"id": "wu-1", "objective": "permit gate fixture unit",
           "status": "pending", "depends_on": [],
           "executor": "flash-implementer",
           "ownership": ["src/a.py"],
           "verification": ["python3 -m unittest -h"]}


def pt_save_task(repo):
    """构造带 work unit 的活动任务（executing），返回 state dict。"""
    st = state.new_task_state(
        PT_TID, "permit gate fixture", dict(PT_ROUTE),
        ownership_files=["src/a.py"],
        verification_required=["python3 -m unittest -h"], status="executing")
    st["work_units"] = [dict(PT_UNIT)]
    state.save_state(repo, st)
    return st


def pt_agent_stdin(permit_id=None, subagent_type="flash-implementer",
                   run_in_background=None, where="prompt"):
    """构造 Agent 派发的 PreToolUse 事件 stdin（marker 可选）。"""
    tool_input = {"subagent_type": subagent_type,
                  "description": "fixture dispatch",
                  "prompt": "implement the unit"}
    if permit_id is not None:
        marker = dispatch_wave.marker_for(permit_id)
        if where == "prompt":
            tool_input["prompt"] = "implement %s" % marker
        else:
            tool_input["description"] = "fixture %s" % marker
    if run_in_background is not None:
        tool_input["run_in_background"] = run_in_background
    return json.dumps({"tool_name": "Agent", "tool_input": tool_input})


def pt_decision(result):
    """解析 hook stdout 的 hookSpecificOutput（单行 JSON）."""
    return parse_single_line_json(result.stdout)["hookSpecificOutput"]


class PermitGateDenyTest(unittest.TestCase):
    """§20.2 hook 侧拒绝集：无 marker / 伪造 / 过期 / 重放一律 deny。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        pt_save_task(self.repo)

    def tearDown(self):
        self._tmp.cleanup()

    def _deny_reason(self, result):
        block = pt_decision(result)
        self.assertEqual(block["permissionDecision"], "deny")
        return block["permissionDecisionReason"]

    def test_executor_without_marker_denied(self):
        result = run_hook(pt_agent_stdin(), self.repo)
        reason = self._deny_reason(result)
        self.assertIn("no dispatch marker found", reason)
        self.assertIn("GLM_CONDUCTOR_DISPATCH=", reason)

    def test_fake_permit_denied(self):
        result = run_hook(pt_agent_stdin("dp-000000000000"), self.repo)
        self.assertIn("permit not found", self._deny_reason(result))

    def test_consumed_permit_denied(self):
        from runtime import dispatch_wave as dw
        permit = dw.create_permit(self.repo, PT_TID, "wu-1")
        dw.consume_permit(self.repo, PT_TID, permit["permit_id"])
        result = run_hook(pt_agent_stdin(permit["permit_id"]), self.repo)
        self.assertIn("permit not found", self._deny_reason(result))

    def test_expired_permit_denied(self):
        # TTL=1 秒的 permit：跨过过期点后必须 deny（§20.2）
        permit = dispatch_wave.create_permit(self.repo, PT_TID, "wu-1",
                                             ttl_seconds=1)
        time.sleep(1.2)
        result = run_hook(pt_agent_stdin(permit["permit_id"]), self.repo)
        self.assertIn("permit expired", self._deny_reason(result))

    def test_readonly_type_not_gated(self):
        # 只读类型（Explore）：permit 门不适用——有声明 ownership 的
        # 活动任务时仍走既有注入路径（additionalContext，无 deny）
        st = state.load_state(self.repo, PT_TID)
        st["ownership"]["files"] = ["src/a.py", "src/b.py"]
        state.save_state(self.repo, st)
        result = run_hook(pt_agent_stdin(subagent_type="Explore"), self.repo)
        self.assertEqual(result.returncode, 0)
        block = pt_decision(result)
        self.assertNotIn("permissionDecision", block)
        self.assertIn("additionalContext", block)

    def test_no_active_task_zero_intervention(self):
        with tempfile.TemporaryDirectory() as empty:
            result = run_hook(pt_agent_stdin(), empty)
            self.assertEqual((result.returncode, result.stdout), (0, ""))


class PermitGateAllowTest(unittest.TestCase):
    """§20.3：background 强制改写 / foreground 同步放行。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        pt_save_task(self.repo)

    def tearDown(self):
        self._tmp.cleanup()

    def _prepare_permit(self, mode=None):
        task_manager.refresh_readiness(self.repo, PT_TID)
        if mode is None:
            result = task_manager.prepare_dispatch(
                self.repo, PT_TID, "wu-1", quota_status="AVAILABLE")
            return result["permit"]["permit_id"]
        return dispatch_wave.create_permit(
            self.repo, PT_TID, "wu-1", mode=mode,
            reason="synchronous_dependency"
            if mode == "foreground" else None)["permit_id"]

    def test_background_forces_run_in_background(self):
        permit_id = self._prepare_permit()
        result = run_hook(pt_agent_stdin(permit_id,
                                         run_in_background=None),
                          self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        block = pt_decision(result)
        self.assertNotIn("permissionDecision", block)
        updated = block["updatedInput"]
        self.assertIs(updated["run_in_background"], True)
        # 全量替换：原有字段全部保留
        self.assertEqual(updated["subagent_type"], "flash-implementer")
        self.assertIn("GLM_CONDUCTOR_DISPATCH=", updated["prompt"])
        self.assertIn("join", block["additionalContext"])

    def test_background_already_true_passthrough(self):
        permit_id = self._prepare_permit()
        result = run_hook(pt_agent_stdin(permit_id,
                                         run_in_background=True), self.repo)
        block = pt_decision(result)
        self.assertNotIn("permissionDecision", block)
        self.assertNotIn("updatedInput", block)

    def test_foreground_permit_sync_allowed(self):
        permit_id = self._prepare_permit(mode="foreground")
        result = run_hook(pt_agent_stdin(permit_id,
                                         run_in_background=None),
                          self.repo)
        block = pt_decision(result)
        self.assertNotIn("permissionDecision", block)
        self.assertNotIn("updatedInput", block)

    def test_marker_in_description_located(self):
        permit_id = self._prepare_permit()
        result = run_hook(pt_agent_stdin(permit_id, where="description"),
                          self.repo)
        block = pt_decision(result)
        self.assertNotIn("permissionDecision", block)


if __name__ == "__main__":
    unittest.main()
