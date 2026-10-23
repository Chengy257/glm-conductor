#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.carrier（v2.7.0 W1 OS 唤醒载体）单元测试 + SessionStart 会话见证写。

锚定对象：plugins/glm-conductor/runtime/carrier.py（纯构造面 / 执行面 /
探针面三级）与 plugins/glm-conductor/hooks/session_start.py 的
write_session_witness（v2.7.0 W1 L1 会话见证）——spec
docs/roadmap/V2_7_0_IMPLEMENTATION_SPEC.md §3.3/§3.4/§3.6：

  1. Windows schtasks argv 纯构造（install_argv / remove_argv）：
     精确 argv 形状冻结；/TR 的值是单一 argv 元素（整串单 argv，绝不
     拆 shell 词元——命令串含空白 / 引号也原样占一个槽位）；间隔
     str() 进 argv（/SC HOURLY /MO N /F 幂等重装语义）；
     carrier_task_name 前缀与非空闸。
  2. POSIX crontab 行纯构造：install_line 间隔换算（``0 */N * * *``，
     N < 1 / 非整数 ValueError）；carrier_marker / install_block
     marker+载体行成对；remove_lines 按 marker 成对摘除、其它行逐字
     保留、removed_count 与尾换行语义。
  3. 唤醒命令冻结形态：wake_command / wake_command_on_path 恒含
     ``--workflow-mode alwaysOn``（B0 P-1 实证：headless 下 onDemand
     不注册工作流工具）；resolve_wake_argv 机器级三级解析（默认装位
     cjs → PATH zcode → CarrierError）。
  4. 命令行渲染：render_windows_command_line（list2cmdline 同款双引
     号规则）/ render_posix_command_line（单引号转义规则）/
     render_command_line 平台分派（monkeypatch is_windows 双态）。
  5. probe 假 PATH 阶梯（spec §3.4，monkeypatch shutil.which 与
     os.path.isfile 模拟 CLI 有无 × 调度器有无）：recommendation 两态
     （os_timer / sleep_only）、zcode_cli 三 via 阶梯（path →
     node_cjs → none；cjs 裸存在而无 node 无 zcode 不构成可用）、
     调度器工具名按平台（schtasks / crontab）、probe 恒四键输出且
     绝不执行任何子进程。
  6. 执行面（monkeypatch carrier._run_argv——绝不真跑 schtasks /
     crontab）：四键结果 {ok, argv, returncode, stderr} 契约；Windows
     精确 argv 直传；POSIX 读-改-写两步（空表起步 / 旧块幂等移除 /
     其它任务逐字保留）；remove POSIX 无块零写 no-op ok=true；
     OS 拒绝如实 ok=false 无三态劣化。
  7. SessionStart 会话见证写（hooks/session_start.py）：argv 传
     sess_ 值 → .glm-conductor/session-witness.json 落盘（恰
     session_id / updated_at 两键、UTC ISO、原子替换无 .tmp 残留、
     覆盖语义）；argv 空串 / 裸前缀 "sess_" / 占位残留
     "${CLAUDE_SESSION_ID}" / 非 sess_ 值 → 不写；子进程 main 接线
     （sys.argv[1] → 见证写，恢复注入照常）。

测试红线（spec §3.6）：绝不真执行 schtasks / crontab 安装——执行面
用例一律 monkeypatch carrier._run_argv；探针面是纯本地零写零执行，
直接跑真实现。temp 目录模式照既有测试：账本 / 见证文件全落
tempfile.TemporaryDirectory，绝不触碰真实仓库 .glm-conductor/ 与本机
调度器。仅 Python 3 标准库（unittest + tempfile + importlib），零
第三方依赖。

运行：
    cd <repo_root> && python3 -X utf8 -m unittest tests.test_wake_carrier -v
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))

from runtime import carrier  # noqa: E402

# 被测钩子脚本（hooks/ 非包——按文件路径加载，照 test_session_start_
# advisory 的脚本定位先例）
SESSION_START = (Path(__file__).resolve().parents[1]
                 / "plugins/glm-conductor/hooks/session_start.py")


def _load_session_start_module():
    """按文件路径加载 hooks/session_start.py（钩子快照，模块级 sys.path
    接线随加载执行——只读副作用，零状态残留）。"""
    spec = importlib.util.spec_from_file_location(
        "session_start_under_test_w1", str(SESSION_START))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SESSION_START_MODULE = _load_session_start_module()


# —— 1. Windows schtasks argv 纯构造 ——

class CarrierWindowsArgvTest(unittest.TestCase):
    """install_argv / remove_argv 纯构造：形状冻结、/TR 整串单 argv、
    间隔换算；绝不执行（函数本体即零执行纯构造，spec §3.6 红线的
    构造侧锚）。"""

    def test_install_argv_exact_frozen_shape(self):
        """创建 argv 恒为 schtasks /Create /TN /TR /SC HOURLY /MO /F
        十一键形状。"""
        argv = carrier.install_argv("glm-conductor-t1",
                                    "node z.cjs --resume sess_x", 4)
        self.assertEqual(argv, [
            "schtasks", "/Create", "/TN", "glm-conductor-t1", "/TR",
            "node z.cjs --resume sess_x", "/SC", "HOURLY", "/MO", "4",
            "/F"])

    def test_install_argv_tr_value_is_single_element(self):
        """/TR 的值是单一 argv 元素（整串单 argv）：命令串含空白与
        引号也原样占一个槽位，绝不拆成 shell 词元、绝不拼 shell。"""
        gnarly = ('node "C:/Program Files/z/resources/glm/zcode.cjs"'
                  ' --resume "sess a" -p "wake; rm -rf /"')
        argv = carrier.install_argv("glm-conductor-t2", gnarly, 1)
        self.assertEqual(argv[5], gnarly)      # /TR 值整串一个元素
        self.assertEqual(argv[6], "/SC")       # 后随旗标槽位不变
        self.assertEqual(len(argv), 11)        # 恰十一键，无拆分溢出

    def test_install_argv_interval_str_conversion(self):
        """间隔经 str() 进 argv（调度器要数字面量）：24 → "24"。"""
        argv = carrier.install_argv("glm-conductor-t3", "cmd", 24)
        self.assertEqual(argv[9], "24")
        self.assertEqual(argv[7:10], ["HOURLY", "/MO", "24"])

    def test_remove_argv_exact_frozen_shape(self):
        """删除 argv 恒为 schtasks /Delete /TN /F 四键（/F 无条件强制，
        跳过确认）。"""
        self.assertEqual(
            carrier.remove_argv("glm-conductor-t4"),
            ["schtasks", "/Delete", "/TN", "glm-conductor-t4", "/F"])

    def test_carrier_task_name_prefix_and_validation(self):
        """任务名恒 glm-conductor-<task-id> 前缀；空 / 非串 ValueError。"""
        self.assertEqual(carrier.carrier_task_name("w1-demo"),
                         "glm-conductor-w1-demo")
        for bad in ("", None, 42, b"t1"):
            with self.assertRaises(ValueError):
                carrier.carrier_task_name(bad)


# —— 2. POSIX crontab 行纯构造 ——

class CarrierPosixArgvTest(unittest.TestCase):
    """install_line / carrier_marker / install_block / remove_lines
    纯构造：间隔换算、marker 成对、解析移除语义。"""

    CMD = "node zcode.cjs --resume sess_x --cwd /repo -p wake"

    def test_install_line_interval_step_conversion(self):
        """间隔换算：每 N 小时 → ``0 */N * * * <cmd>``（每小时第 0 分）。"""
        self.assertEqual(carrier.install_line(self.CMD, 4),
                         "0 */4 * * * " + self.CMD)
        self.assertEqual(carrier.install_line(self.CMD, 1),
                         "0 */1 * * * " + self.CMD)
        self.assertEqual(carrier.install_line(self.CMD, 23),
                         "0 */23 * * * " + self.CMD)

    def test_install_line_invalid_interval_refused(self):
        """间隔 < 1 / 非数字面量 → ValueError（crontab */0 非法，
        必须在构造面闸住）。"""
        for bad in (0, -1, "abc"):
            with self.assertRaises(ValueError):
                carrier.install_line(self.CMD, bad)

    def test_marker_and_block_pair(self):
        """marker 行 + 载体行成对：install_block 恰为两行换行相连。"""
        marker = carrier.carrier_marker("glm-conductor-t5")
        self.assertEqual(marker, "# glm-conductor glm-conductor-t5")
        block = carrier.install_block("glm-conductor-t5", self.CMD, 2)
        self.assertEqual(
            block,
            marker + "\n" + "0 */2 * * * " + self.CMD)

    def test_remove_lines_removes_pair_keeps_others(self):
        """remove_lines 成对摘除本任务 marker 块；其它任务行逐字保留；
        removed_count=1；尾换行按原文恢复。"""
        other = "0 12 * * * echo someone-elses-job"
        crontab = (other + "\n"
                   + carrier.install_block("glm-conductor-t6", self.CMD, 3)
                   + "\n" + other + "\n")
        new_text, removed = carrier.remove_lines(crontab,
                                                 "glm-conductor-t6")
        self.assertEqual(removed, 1)
        self.assertEqual(new_text, other + "\n" + other + "\n")

    def test_remove_lines_absent_marker_zero_changes(self):
        """无本任务 marker → removed=0、全文逐字不变（零写 no-op 的
        构造侧前提）。"""
        crontab = "0 * * * * echo keep\n"
        new_text, removed = carrier.remove_lines(crontab,
                                                 "glm-conductor-absent")
        self.assertEqual((new_text, removed), (crontab, 0))

    def test_remove_lines_trailing_marker_takes_line_only(self):
        """末尾孤 marker（手改残留无跟随行）→ 仅摘 marker 自身。"""
        crontab = "0 * * * * echo keep\n# glm-conductor glm-conductor-t7\n"
        new_text, removed = carrier.remove_lines(crontab,
                                                 "glm-conductor-t7")
        self.assertEqual((new_text, removed), ("0 * * * * echo keep\n", 1))

    def test_remove_lines_marker_before_blank_keeps_blank(self):
        """marker 紧邻空行（块边界）→ 只摘 marker、空行保留不跨行吞并。"""
        crontab = ("# glm-conductor glm-conductor-t8\n"
                   "\n0 * * * * echo keep\n")
        new_text, removed = carrier.remove_lines(crontab,
                                                 "glm-conductor-t8")
        self.assertEqual((new_text, removed), ("\n0 * * * * echo keep\n", 1))


# —— 3. 唤醒命令冻结形态 ——

class WakeCommandShapeTest(unittest.TestCase):
    """wake_command / wake_command_on_path / resolve_wake_argv：
    --workflow-mode alwaysOn 冻结形态 + 机器级三级解析。"""

    REPO = "C:/some/repo"
    PROMPT = "wake up"

    def test_wake_command_frozen_form_with_alwayson(self):
        """node+cjs 冻结形态：恰十元素，恒含 ``--workflow-mode
        alwaysOn``（headless 下 onDemand 不注册工作流工具——B0 实证）。"""
        argv = carrier.wake_command("C:/pf/z/resources/glm/zcode.cjs",
                                    "sess_abc", self.REPO, self.PROMPT)
        self.assertEqual(argv, [
            "node", "C:/pf/z/resources/glm/zcode.cjs",
            "--resume", "sess_abc", "--cwd", self.REPO,
            "--workflow-mode", "alwaysOn", "-p", self.PROMPT])
        # 形态锚：workflow-mode 恒 alwaysOn，绝不因调用方变化
        self.assertEqual(argv[6:8], ["--workflow-mode", "alwaysOn"])

    def test_wake_command_on_path_form(self):
        """PATH zcode 形态：入口换成已解析可执行文件，协议五元组同构。"""
        argv = carrier.wake_command_on_path("C:/bin/zcode.exe", "sess_x",
                                            self.REPO, self.PROMPT)
        self.assertEqual(argv, [
            "C:/bin/zcode.exe", "--resume", "sess_x", "--cwd", self.REPO,
            "--workflow-mode", "alwaysOn", "-p", self.PROMPT])

    def test_resolve_prefers_existing_default_cjs(self):
        """默认装位 zcode.cjs 存在 → node+cjs 冻结形态（patch 常量到
        真实临时文件，机器态零依赖）。"""
        with tempfile.TemporaryDirectory() as tmp:
            cjs = Path(tmp) / "zcode.cjs"
            cjs.write_bytes(b"// stub\n")
            with mock.patch.object(carrier, "DEFAULT_ZCODE_CJS", str(cjs)):
                argv = carrier.resolve_wake_argv("sess_r1", self.REPO,
                                                 self.PROMPT)
        self.assertEqual(argv, [
            "node", str(cjs), "--resume", "sess_r1", "--cwd", self.REPO,
            "--workflow-mode", "alwaysOn", "-p", self.PROMPT])

    def test_resolve_falls_back_to_path_zcode(self):
        """默认装位缺失而 PATH 有 zcode → PATH 形态（shutil.which
        monkeypatch，零真实 PATH 依赖）。"""
        with mock.patch.object(carrier, "DEFAULT_ZCODE_CJS",
                               "C:/nonexistent/zcode.cjs"), \
             mock.patch.object(carrier.shutil, "which",
                               return_value="C:/bin/zcode.exe") as fake:
            argv = carrier.resolve_wake_argv("sess_r2", self.REPO,
                                             self.PROMPT)
        fake.assert_called_once_with("zcode")
        self.assertEqual(argv, [
            "C:/bin/zcode.exe", "--resume", "sess_r2", "--cwd", self.REPO,
            "--workflow-mode", "alwaysOn", "-p", self.PROMPT])

    def test_resolve_both_missing_raises_carrier_error(self):
        """两处皆缺 → CarrierError（错误指向 wake-carrier-probe 对账）。"""
        with mock.patch.object(carrier, "DEFAULT_ZCODE_CJS",
                               "C:/nonexistent/zcode.cjs"), \
             mock.patch.object(carrier.shutil, "which", return_value=None):
            with self.assertRaises(carrier.CarrierError) as ctx:
                carrier.resolve_wake_argv("sess_r3", self.REPO,
                                          self.PROMPT)
        self.assertIn("wake-carrier-probe", str(ctx.exception))


# —— 4. 命令行渲染 ——

class RenderCommandLineTest(unittest.TestCase):
    """render_windows_command_line（双引号规则）/ render_posix_command_line
    （单引号转义）/ render_command_line 平台分派。"""

    def test_windows_render_quotes_elements_with_spaces(self):
        """含空白元素双引号包裹（list2cmdline 同款）；无空白直出。"""
        rendered = carrier.render_windows_command_line(
            ["node", "C:/Program Files/z/zcode.cjs", "--resume", "sess a"])
        self.assertEqual(
            rendered,
            'node "C:/Program Files/z/zcode.cjs" --resume "sess a"')

    def test_windows_render_escapes_inner_quotes(self):
        """内部双引号按 MSVCRT 惯例转义（反斜杠计数翻倍）。"""
        rendered = carrier.render_windows_command_line(['say "hi"'])
        self.assertEqual(rendered, '"say \\"hi\\""')

    def test_windows_render_empty_element_becomes_empty_quotes(self):
        """空串元素渲染为 ""（占位不消失）。"""
        self.assertEqual(carrier.render_windows_command_line(["a", ""]),
                         'a ""')

    def test_posix_render_single_quote_escaping(self):
        """单引号转义规则：含空白 / 单引号元素用单引号包裹、内部单
        引号转义 '\\'\\''；安全字符集直出。"""
        rendered = carrier.render_posix_command_line(
            ["node", "a b", "it's", "/repo/x", "--flag"])
        self.assertEqual(rendered,
                         "node 'a b' 'it'\\''s' /repo/x --flag")

    def test_posix_render_escapes_dollar_and_backtick(self):
        """$ 与反引号在单引号内失去活动性（crontab 命令段最稳引用）。"""
        rendered = carrier.render_posix_command_line(["echo $HOME `id`"])
        self.assertEqual(rendered, "'echo $HOME `id`'")

    def test_render_command_line_follows_platform(self):
        """平台分派：is_windows True → 双引号规则；False → 单引号规则
        （monkeypatch，双态本机无关）。"""
        argv = ["run", "a b"]
        with mock.patch.object(carrier, "is_windows", return_value=True):
            self.assertEqual(carrier.render_command_line(argv),
                             'run "a b"')
        with mock.patch.object(carrier, "is_windows", return_value=False):
            self.assertEqual(carrier.render_command_line(argv),
                             "run 'a b'")


# —— 5. probe 假 PATH 阶梯（CLI 有无 × 调度器有无 → recommendation 两态） ——

class ProbeLadderTest(unittest.TestCase):
    """probe 假 PATH 阶梯：monkeypatch shutil.which / os.path.isfile /
    is_windows 模拟机器形态矩阵，recommendation 恒两态（os_timer /
    sleep_only）；probe 纯本地零写零执行（绝不建探针任务）。"""

    FAKE_CJS = "C:/fake/zcode.cjs"

    def run_probe(self, which_map, cjs_exists=False, windows=True):
        """在假机器形态下跑 probe：which_map 映射工具名 → 路径
        （None = 不在 PATH）；cjs_exists 控制默认装位存在性；
        windows 控制调度器工具名分支。"""
        def fake_which(tool):
            return which_map.get(tool)

        with mock.patch.object(carrier.shutil, "which",
                               side_effect=fake_which), \
             mock.patch.object(carrier.os.path, "isfile",
                               return_value=cjs_exists), \
             mock.patch.object(carrier, "DEFAULT_ZCODE_CJS", self.FAKE_CJS), \
             mock.patch.object(carrier, "is_windows", return_value=windows):
            return carrier.probe("C:/some/repo")

    def test_probe_payload_is_exactly_four_keys(self):
        """probe 恒四键 {platform, zcode_cli, scheduler, recommendation}；
        子 dict 键形状冻结。"""
        report = self.run_probe({})
        self.assertEqual(
            sorted(report),
            ["platform", "recommendation", "scheduler", "zcode_cli"])
        self.assertEqual(
            sorted(report["zcode_cli"]),
            ["available", "cjs_path", "node_path", "via", "zcode_path"])
        self.assertEqual(sorted(report["scheduler"]),
                         ["available", "path", "tool"])
        self.assertEqual(report["zcode_cli"]["cjs_path"], None)
        self.assertEqual(report["recommendation"], "sleep_only")

    def test_ladder_cli_on_path_and_scheduler_ready_is_os_timer(self):
        """阶梯一：PATH zcode + 调度器 → os_timer（L1 可武装）。"""
        report = self.run_probe({"zcode": "C:/bin/zcode.exe",
                                 "schtasks": "C:/Windows/schtasks.exe"})
        self.assertEqual(report["recommendation"], "os_timer")
        self.assertTrue(report["zcode_cli"]["available"])
        self.assertEqual(report["zcode_cli"]["via"], "path")
        self.assertEqual(report["zcode_cli"]["zcode_path"],
                         "C:/bin/zcode.exe")
        self.assertTrue(report["scheduler"]["available"])
        self.assertEqual(report["scheduler"]["tool"], "schtasks")

    def test_ladder_node_cjs_combo_and_scheduler_is_os_timer(self):
        """阶梯二：无 PATH zcode 但 node + 默认装位 cjs 齐备 →
        via=node_cjs，照样 os_timer。"""
        report = self.run_probe({"node": "C:/bin/node.exe",
                                 "schtasks": "C:/Windows/schtasks.exe"},
                                cjs_exists=True)
        self.assertEqual(report["recommendation"], "os_timer")
        self.assertEqual(report["zcode_cli"]["via"], "node_cjs")
        self.assertEqual(report["zcode_cli"]["cjs_path"], self.FAKE_CJS)

    def test_ladder_bare_cjs_without_node_is_sleep_only(self):
        """阶梯三：cjs 裸存在而无 node 无 zcode → 不构成可用组合
        （via=none → sleep_only；与 resolve_wake_argv 同口径，两面
        永不矛盾）。"""
        report = self.run_probe({}, cjs_exists=True)
        self.assertEqual(report["zcode_cli"]["via"], "none")
        self.assertFalse(report["zcode_cli"]["available"])
        self.assertEqual(report["recommendation"], "sleep_only")

    def test_ladder_cli_without_scheduler_is_sleep_only(self):
        """阶梯四：CLI 可用而调度器缺席（schtasks 受限环境）→
        sleep_only 自然降级，无硬失败（R3）。"""
        report = self.run_probe({"zcode": "C:/bin/zcode.exe"})
        self.assertFalse(report["scheduler"]["available"])
        self.assertIsNone(report["scheduler"]["path"])
        self.assertEqual(report["recommendation"], "sleep_only")

    def test_ladder_neither_is_sleep_only(self):
        """阶梯五：两皆缺 → sleep_only（探针恒退出成功、恒有答案）。"""
        report = self.run_probe({})
        self.assertEqual(report["recommendation"], "sleep_only")
        self.assertFalse(report["zcode_cli"]["available"])
        self.assertFalse(report["scheduler"]["available"])

    def test_scheduler_tool_name_follows_platform(self):
        """调度器工具名按平台：Windows → schtasks；POSIX → crontab
        （is_windows monkeypatch 双态）。"""
        posix = self.run_probe({"zcode": "/usr/bin/zcode",
                                "crontab": "/usr/bin/crontab"},
                               windows=False)
        self.assertEqual(posix["scheduler"]["tool"], "crontab")
        self.assertTrue(posix["scheduler"]["available"])
        self.assertEqual(posix["recommendation"], "os_timer")
        # 同为 POSIX、crontab 缺席 → 调度器不可用（工具名仍按平台）
        windowsless = self.run_probe({"zcode": "/usr/bin/zcode"},
                                     windows=False)
        self.assertEqual(windowsless["scheduler"]["tool"], "crontab")
        self.assertFalse(windowsless["scheduler"]["available"])
        self.assertEqual(windowsless["recommendation"], "sleep_only")

    def test_probe_never_spawns_processes(self):
        """探针红线：probe 调用路径绝不触碰 subprocess（shutil.which +
        路径存在性之外零副作用——monkeypatch subprocess.run 为哨兵）。"""
        with mock.patch.object(carrier.shutil, "which", return_value=None), \
             mock.patch.object(carrier.subprocess, "run",
                               side_effect=AssertionError(
                                   "probe must never spawn processes")):
            report = carrier.probe("C:/some/repo")
        self.assertEqual(report["recommendation"], "sleep_only")


# —— 6. 执行面（monkeypatch _run_argv——绝不真跑 schtasks / crontab） ——

class CarrierExecutionFaceTest(unittest.TestCase):
    """install_carrier / remove_carrier 执行面：_run_argv 全程
    monkeypatch——四键结果契约、Windows 精确 argv 直传、POSIX 读-改-
    写幂等、remove no-op 语义、OS 拒绝如实透出。测试内绝不执行任何
    OS 调度器命令。"""

    NAME = "glm-conductor-exec-t1"
    CMD = "node zcode.cjs --resume sess_x"
    SCHEDULER_SENTINEL = "tests must never run the OS scheduler"

    def patch_runner(self, results):
        """monkeypatch carrier._run_argv：results 为按调用次序弹出的
        (returncode, stdout, stderr) 三元组列表；记录每次调用 argv 与
        input_text 供断言。"""
        calls = []

        def fake_run(argv, input_text=None):
            calls.append({"argv": list(argv), "input": input_text})
            if not results:
                raise AssertionError(self.SCHEDULER_SENTINEL)
            return results.pop(0)

        patcher = mock.patch.object(carrier, "_run_argv",
                                    side_effect=fake_run)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def test_install_windows_single_step_exact_argv(self):
        """Windows：单步 schtasks /Create——_run_argv 收到 install_argv
        的精确 argv，四键结果逐键透出。"""
        calls = self.patch_runner([(0, "", "")])
        with mock.patch.object(carrier, "is_windows", return_value=True):
            result = carrier.install_carrier(self.NAME, self.CMD, 4)
        self.assertEqual(result, {"ok": True, "argv": [
            "schtasks", "/Create", "/TN", self.NAME, "/TR", self.CMD,
            "/SC", "HOURLY", "/MO", "4", "/F"],
            "returncode": 0, "stderr": ""})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["input"], None)

    def test_install_windows_refusal_reports_ok_false(self):
        """OS 拒绝 → ok=false + returncode/stderr 如实透出（无硬崩，
        调用方降级 sleep_only；stderr 取尾部截断）。"""
        self.patch_runner([(1, "", "ACCESS DENIED" + "x" * 3000)])
        with mock.patch.object(carrier, "is_windows", return_value=True):
            result = carrier.install_carrier(self.NAME, self.CMD, 1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["returncode"], 1)
        self.assertEqual(result["stderr"], ("ACCESS DENIED" + "x" * 3000)
                         [-carrier.STDERR_TAIL_LIMIT:])
        self.assertLessEqual(len(result["stderr"]),
                             carrier.STDERR_TAIL_LIMIT)

    def test_install_posix_empty_table_read_modify_write(self):
        """POSIX 空表起步：读步失败视为空表 → 写步整表恰一 marker 块，
        结果报告决定性写步 argv。"""
        calls = self.patch_runner([(1, "", "no crontab for user"),
                                   (0, "", "")])
        with mock.patch.object(carrier, "is_windows", return_value=False):
            result = carrier.install_carrier(self.NAME, self.CMD, 2)
        self.assertTrue(result["ok"])
        self.assertEqual(result["argv"], ["crontab", "-"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["argv"], ["crontab", "-l"])
        self.assertEqual(calls[1]["argv"], ["crontab", "-"])
        self.assertEqual(
            calls[1]["input"],
            "# glm-conductor %s\n0 */2 * * * %s\n" % (self.NAME, self.CMD))

    def test_install_posix_preserves_other_jobs_and_replaces_own_block(self):
        """POSIX 重装幂等：其它任务行逐字保留、本任务旧块恰被替换
        （marker 成对移除再追加，与 Windows /F 同语义）。"""
        other = "0 12 * * * echo someone-elses-job"
        old_block = carrier.install_block(self.NAME, "old-cmd", 9)
        calls = self.patch_runner([
            (0, other + "\n" + old_block + "\n" + other + "\n", ""),
            (0, "", "")])
        with mock.patch.object(carrier, "is_windows", return_value=False):
            result = carrier.install_carrier(self.NAME, self.CMD, 3)
        self.assertTrue(result["ok"])
        written = calls[1]["input"]
        self.assertEqual(written.count("# glm-conductor " + self.NAME), 1)
        self.assertNotIn("old-cmd", written)
        self.assertEqual(written.count(other), 2, "其它任务行逐字保留")
        self.assertIn("0 */3 * * * " + self.CMD, written)

    def test_remove_windows_reports_real_exit_code(self):
        """Windows 删除：OS 真实退出码即事实——任务不存在 ok=false
        如实报告（无三态劣化）。"""
        calls = self.patch_runner([(1, "", "ERROR: The scheduled task"
                                    ' "x" does not exist')])
        with mock.patch.object(carrier, "is_windows", return_value=True):
            result = carrier.remove_carrier(self.NAME)
        self.assertFalse(result["ok"])
        self.assertEqual(result["argv"],
                         ["schtasks", "/Delete", "/TN", self.NAME, "/F"])
        self.assertEqual(len(calls), 1)

    def test_remove_posix_no_matching_block_zero_write_noop(self):
        """POSIX 无 marker 块可摘 → 零写 no-op ok=true（删除终态已
        达成；argv/returncode 报告读取步，绝不触发写步）。"""
        calls = self.patch_runner([(0, "0 * * * * echo keep\n", "")])
        with mock.patch.object(carrier, "is_windows", return_value=False):
            result = carrier.remove_carrier("glm-conductor-absent")
        self.assertTrue(result["ok"])
        self.assertEqual(result["argv"], ["crontab", "-l"])
        self.assertEqual(len(calls), 1, "零写：只有读取步")

    def test_remove_posix_writes_back_remaining_table(self):
        """POSIX 有块可摘 → 写步整表写回余文（marker 块成对消失）。"""
        other = "0 * * * * echo keep"
        calls = self.patch_runner([
            (0, other + "\n" + carrier.install_block(self.NAME,
                                                     self.CMD, 1) + "\n",
             ""),
            (0, "", "")])
        with mock.patch.object(carrier, "is_windows", return_value=False):
            result = carrier.remove_carrier(self.NAME)
        self.assertTrue(result["ok"])
        self.assertEqual(calls[1]["input"], other + "\n")

    def test_execution_face_exact_list_argv_never_shell(self):
        """红线行为锚：执行面单步以 list 形精确 argv 交 subprocess 且
        恒 shell=False（monkeypatch carrier.subprocess.run 捕获真实
        调用参数——绝不 shell 拼接、绝不 shell=True）。"""
        calls = []

        class _Proc(object):
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(*args, **kwargs):
            calls.append((args, kwargs))
            return _Proc()

        with mock.patch.object(carrier, "is_windows", return_value=True), \
                mock.patch.object(carrier.subprocess, "run",
                                  side_effect=fake_run):
            carrier.install_carrier(self.NAME, self.CMD, 2)
        self.assertEqual(len(calls), 1)
        spawn_args, spawn_kwargs = calls[0]
        self.assertEqual(spawn_args[0], [
            "schtasks", "/Create", "/TN", self.NAME, "/TR", self.CMD,
            "/SC", "HOURLY", "/MO", "2", "/F"])
        self.assertIs(spawn_kwargs.get("shell"), False)

    def test_invalid_params_refused_without_spawning(self):
        """入参闸：task_name / command_line 非空 str、interval >= 1
        整数——ValueError 先于一切 spawn（_run_argv 未打桩即本证）。"""
        with mock.patch.object(carrier, "is_windows", return_value=True):
            for args in (("", self.CMD, 1),
                         (None, self.CMD, 1),
                         (self.NAME, "", 1),
                         (self.NAME, None, 1),
                         (self.NAME, self.CMD, 0),
                         (self.NAME, self.CMD, "abc")):
                with self.assertRaises(ValueError):
                    carrier.install_carrier(*args)
            with self.assertRaises(ValueError):
                carrier.remove_carrier("")
            with self.assertRaises(ValueError):
                carrier.remove_carrier(None)


# —— 7. SessionStart 会话见证写 ——

class SessionWitnessTest(unittest.TestCase):
    """hooks/session_start.py write_session_witness（v2.7.0 W1 L1）：
    argv 传 sess_ 值 → 见证文件落盘；空串 / 占位残留 → 不写。
    ZCODE_PROJECT_DIR 钉在 tempfile 临时目录，绝不触碰真实仓库。"""

    SESSION_ID = "sess_w1-unit-42"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = self._tmp.name
        env_patcher = mock.patch.dict(os.environ,
                                      {"ZCODE_PROJECT_DIR": self.repo})
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

    def witness_path(self):
        return (Path(self.repo) / ".glm-conductor"
                / "session-witness.json")

    def write(self, raw):
        return SESSION_START_MODULE.write_session_witness(raw)

    def test_sess_argv_writes_witness_file(self):
        """argv 传 sess_ 值 → 文件落盘：恰 session_id / updated_at
        两键、值原样、UTC ISO。"""
        self.write(self.SESSION_ID)
        path = self.witness_path()
        self.assertTrue(path.is_file())
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        self.assertEqual(payload["session_id"], self.SESSION_ID)
        self.assertEqual(sorted(payload), ["session_id", "updated_at"])
        # updated_at 是 UTC ISO（含 +00:00 时区标记，可解析回时刻）
        moment = datetime.fromisoformat(payload["updated_at"])
        self.assertEqual(moment.utcoffset().total_seconds(), 0)

    def test_witness_write_is_atomic_no_tmp_leftover(self):
        """原子替换：落盘后目录恰只有 session-witness.json（.tmp 中间
        文件不留残骸）。"""
        self.write(self.SESSION_ID)
        self.assertEqual(
            sorted(os.listdir(Path(self.repo) / ".glm-conductor")),
            ["session-witness.json"])

    def test_rewrite_replaces_previous_session(self):
        """重复写：后值整文件替换前值（os.replace 语义）。"""
        self.write("sess_first-1")
        self.write("sess_second-2")
        with open(self.witness_path(), "r", encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["session_id"], "sess_second-2")

    def test_empty_argv_no_write(self):
        """argv 空串（无 argv[1] 同口径）→ 不写。"""
        self.write("")
        self.assertFalse(self.witness_path().exists())

    def test_bare_prefix_no_write(self):
        """裸前缀 "sess_"（无余量）→ 不写。"""
        self.write("sess_")
        self.assertFalse(self.witness_path().exists())

    def test_placeholder_residue_no_write(self):
        """宿主未展开的占位原文 "${CLAUDE_SESSION_ID}"（含 {} 残留）→
        兜底跳过不写。"""
        self.write("${CLAUDE_SESSION_ID}")
        self.assertFalse(self.witness_path().exists())

    def test_non_sess_value_no_write(self):
        """非 sess_ 前缀值 → 不写（形态闸）。"""
        self.write("abc-123")
        self.assertFalse(self.witness_path().exists())

    def test_non_string_value_no_write(self):
        """非串值（防御形态）→ 不写且不抛（fail-open）。"""
        self.write(None)
        self.assertFalse(self.witness_path().exists())

    def test_main_wiring_via_subprocess(self):
        """子进程 main 接线：argv[1]=sess_ 值 → 见证落盘；占位原文 →
        不写（钩子快照进程，cwd 与 ZCODE_PROJECT_DIR 双钉临时目录，
        恢复注入照常静默——临时仓库无任务）。"""
        env = dict(os.environ)
        env["ZCODE_PROJECT_DIR"] = self.repo
        env.setdefault("SYSTEMROOT", os.environ.get("SYSTEMROOT", ""))
        proc = subprocess.run(
            [sys.executable, str(SESSION_START), "sess_sub-wire-7"],
            cwd=self.repo, env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(self.witness_path(), "r", encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["session_id"], "sess_sub-wire-7")
        # 占位残留形态：不写（同款子进程）
        self.write("")  # 先清盘：删除见证文件
        os.remove(self.witness_path())
        proc = subprocess.run(
            [sys.executable, str(SESSION_START), "${CLAUDE_SESSION_ID}"],
            cwd=self.repo, env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(self.witness_path().exists())


if __name__ == "__main__":
    unittest.main()
