#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.task_manager 公共表面契约测试（v2.2.1 WU-221-C2，ST-04 分解
的导入兼容性契约）。

锚定对象（v2.2.1 WU-221-C1/C2 分解后的导入兼容性契约）：
  - 公共表面冻结：runtime.task_manager 的公共名字集（无前导下划线、
    callable 或常量；模块对象既非 callable 也非常量，不入表面）在
    测试时经 dir()/getattr() 实测，断言「冻结清单 ⊆ 实测集合」且
    逐名的 callable/constant 种类不漂移；
  - 表面策略（设计决策，冻结于此 docstring）：**子集断言而非严格
    相等**——冻结清单里的名字未来消失 → 失败并逐名报缺失；新增公共
    名字 → 允许（严格相等会让未来每个新功能都先改本测试）；实测
    集合排序后打印，供人工 review 表面扩张；
  - 编排体留守：resume_from_quota / handle_quota_exhausted 三个事务
    编排体中的公共两员必须仍定义于 runtime.task_manager 本模块
    （__module__ 锚定；_resume_consumption 为私有不入本契约）；
  - 门面 re-export 同一性：ST-04 分解搬到规范落点的公共 API——
    accounting（record_quota_boundary_consumed /
    migrate_quota_window_accounting / TaskManagerError）、
    continuity.wake_bridge（arm_wake_bridge / plan_wake_bridge /
    degrade_continuity / record_bridge_fired / retarget_wake_bridge /
    reconcile_wake_bridge_from_host / universal_wake_prompt /
    write_completion_tombstone）、continuity.subscription
    （register_quota_subscription / evaluate_subscription_eligibility
    / mark_activation_epoch）、continuity.resume（record_quota_wake /
    quota_wake_prompt）——task_manager.<名字> 必须与其规范模块的
    同名对象 **是同一对象**（is 身份断言，非仅可解析）；
  - 导入卫生：runtime.task_manager 在全新解释器中干净导入（冒烟）；
    反向依赖冻结——import runtime.continuity.wake_bridge /
    subscription / resume 与 runtime.quota.accounting（及本单元新增
    的 quota.time_utils / quota.window_math）绝不把
    runtime.task_manager 拉进 sys.modules（子进程实测——同进程
    sys.modules 会被先行测试污染，不可作判据）。

运行：
    cd <repo_root> && python3 -m unittest tests.test_public_surface -v
"""

import os
import subprocess
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
import runtime.continuity.resume as continuity_resume
import runtime.continuity.subscription as continuity_subscription
import runtime.continuity.wake_bridge as continuity_wake_bridge
import runtime.quota.accounting as quota_accounting
import runtime.task_manager as task_manager

# 公共表面冻结清单（v2.2.1 WU-221-C2 时点实测，46 名）：
# (名字, 种类)；种类 ∈ {"callable", "constant"}。
FROZEN_PUBLIC_SURFACE = (
    ("FINISH_OUTCOMES", "constant"),
    ("HARD_WORKER_LIMIT", "constant"),
    ("LEASE_DEFAULT_TTL_SECONDS", "constant"),
    ("PRE_DISPATCH_TASK_STATUSES", "constant"),
    ("PROMOTABLE_STATUSES", "constant"),
    ("QUOTA_RESUME_GRACE_SECONDS", "constant"),
    ("QUOTA_RESUME_STATUSES", "constant"),
    ("QUOTA_SUBSCRIPTION_RESULT_KEYS", "constant"),
    ("QUOTA_WAIT_TASK_STATUSES", "constant"),
    ("QUOTA_WAIT_UNIT_STATUSES", "constant"),
    ("REUSABLE_BRIDGE_STATUSES", "constant"),
    ("UNIT_VERIFICATION_STATUSES", "constant"),
    ("WAKE_BRIDGE_HOST_FACTS", "constant"),
    ("WAKE_PLAN_TASK_STATUSES", "constant"),
    ("WAVE_CLOSED_UNIT_STATUSES", "constant"),
    ("TaskManagerError", "callable"),
    ("abort_dispatch", "callable"),
    ("arm_wake_bridge", "callable"),
    ("commit_dispatch", "callable"),
    ("consumed_quota_windows", "callable"),
    ("default_execution_policy", "callable"),
    ("default_quota_control", "callable"),
    ("degrade_continuity", "callable"),
    ("effective_worker_budget", "callable"),
    ("evaluate_subscription_eligibility", "callable"),
    ("finish_unit", "callable"),
    ("handle_quota_exhausted", "callable"),
    ("mark_activation_epoch", "callable"),
    ("migrate_quota_window_accounting", "callable"),
    ("observe_scheduler_context", "callable"),
    ("plan_wake_bridge", "callable"),
    ("prepare_dispatch", "callable"),
    ("prepare_dispatch_wave", "callable"),
    ("quota_wake_prompt", "callable"),
    ("reconcile_wake_bridge_from_host", "callable"),
    ("record_bridge_fired", "callable"),
    ("record_quota_boundary_consumed", "callable"),
    ("record_quota_wake", "callable"),
    ("record_unit_verification", "callable"),
    ("recover_leases", "callable"),
    ("refresh_readiness", "callable"),
    ("register_quota_subscription", "callable"),
    ("resume_from_quota", "callable"),
    ("retarget_wake_bridge", "callable"),
    ("universal_wake_prompt", "callable"),
    ("write_completion_tombstone", "callable"),
)

# 门面 re-export → 规范落点模块（同一性断言的权威映射；v2.2.1
# WU-221-C1/C2 分解冻结：task_manager → 各规范模块，绝不反向）。
FACADE_REEXPORTS = (
    (quota_accounting, ("TaskManagerError",
                        "migrate_quota_window_accounting",
                        "record_quota_boundary_consumed")),
    (continuity_wake_bridge, ("arm_wake_bridge",
                              "degrade_continuity",
                              "plan_wake_bridge",
                              "record_bridge_fired",
                              "reconcile_wake_bridge_from_host",
                              "retarget_wake_bridge",
                              "universal_wake_prompt",
                              "write_completion_tombstone")),
    (continuity_subscription, ("evaluate_subscription_eligibility",
                               "mark_activation_epoch",
                               "register_quota_subscription")),
    (continuity_resume, ("quota_wake_prompt",
                         "record_quota_wake")),
)

# 编排体（留守 task_manager 本模块的公共两员；_resume_consumption
# 私有，不入公共契约）
ORCHESTRATORS = ("handle_quota_exhausted", "resume_from_quota")

# 导入卫生检查的子进程脚本：逐个导入后断言 task_manager 未被拉入
# sys.modules（反向依赖冻结），全部通过打印 "clean"。
_HYGIENE_MODULES = ("runtime.quota.time_utils",
                    "runtime.quota.window_math",
                    "runtime.quota.accounting",
                    "runtime.continuity.wake_bridge",
                    "runtime.continuity.subscription",
                    "runtime.continuity.resume")
_HYGIENE_CODE = (
    "import sys\n"
    "for name in (%s):\n"
    "    __import__(name)\n"
    "    assert 'runtime.task_manager' not in sys.modules, name\n"
    "print('clean')\n" % (", ".join(repr(m) for m in _HYGIENE_MODULES),)
)
_SMOKE_CODE = "import runtime.task_manager\nprint('imported')\n"

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"


def _measured_public_surface(module):
    """测试时实测模块公共表面：无前导下划线且为 callable / 常量。

    模块对象排除（既非 callable 也非常量）；返回 {名字: 种类}。
    """
    surface = {}
    for name in dir(module):
        if name.startswith("_"):
            continue
        value = getattr(module, name)
        if isinstance(value, types.ModuleType):
            continue
        surface[name] = "callable" if callable(value) else "constant"
    return surface


def _run_python(code):
    """全新解释器执行 code（PYTHONPATH 注入插件根，UTF-8 模式）。"""
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (str(PLUGIN_ROOT)
                         + (os.pathsep + existing if existing else ""))
    env["PYTHONUTF8"] = "1"
    return subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True,
                          env=env, timeout=120)


class TaskManagerPublicSurfaceTest(unittest.TestCase):
    """公共表面冻结：冻结清单 ⊆ 实测集合 + 种类不漂移 + 实测打印。"""

    def test_frozen_names_all_present_with_stable_kind(self):
        measured = _measured_public_surface(task_manager)
        missing = sorted(name for name, _ in FROZEN_PUBLIC_SURFACE
                         if name not in measured)
        self.assertFalse(
            missing,
            "runtime.task_manager 公共名字消失（导入兼容性契约违约，"
            "须评估全部 task_manager.<名字> 解析点后再动）：%s"
            % ", ".join(missing))
        drifted = sorted(
            "%s(冻结=%s, 实测=%s)" % (name, kind, measured.get(name))
            for name, kind in FROZEN_PUBLIC_SURFACE
            if name in measured and measured[name] != kind)
        self.assertFalse(
            drifted,
            "公共名字种类漂移（callable ↔ 常量，违约）：%s"
            % "; ".join(drifted))

    def test_measured_surface_printed_sorted_for_review(self):
        """实测公共表面排序打印（成功路径也输出，供人工 review 扩张）。"""
        measured = _measured_public_surface(task_manager)
        additions = sorted(name for name in measured
                           if name not in dict(FROZEN_PUBLIC_SURFACE))
        print("\n[public-surface] runtime.task_manager 实测公共名字 "
              "(%d)：%s" % (len(measured),
                            ", ".join(sorted(measured))))
        print("[public-surface] 冻结清单之外的新增（允许，review 用）：%s"
              % (", ".join(additions) if additions else "（无）"))


class OrchestratorResidencyTest(unittest.TestCase):
    """事务编排体留守：resume_from_quota / handle_quota_exhausted
    仍定义于 runtime.task_manager 本模块（非 re-export）。"""

    def test_orchestrators_defined_in_task_manager(self):
        for name in ORCHESTRATORS:
            func = getattr(task_manager, name, None)
            self.assertTrue(callable(func),
                            "%s 不在 runtime.task_manager" % name)
            self.assertEqual(
                func.__module__, "runtime.task_manager",
                "%s 的定义体已不在 runtime.task_manager（__module__=%r）"
                "——ST-04 分解冻结编排体留守本模块" % (name, func.__module__))


class FacadeReexportIdentityTest(unittest.TestCase):
    """门面 re-export 同一性：task_manager.<名字> is 规范模块.<名字>。"""

    def test_reexports_are_identical_objects(self):
        mismatches = []
        for canonical, names in FACADE_REEXPORTS:
            for name in names:
                facade = getattr(task_manager, name, None)
                if facade is None:
                    mismatches.append(
                        "%s 不在 runtime.task_manager" % name)
                elif facade is not getattr(canonical, name, None):
                    mismatches.append(
                        "%s 与 %s.%s 非同一对象"
                        % (name, canonical.__name__, name))
        self.assertFalse(
            mismatches,
            "门面 re-export 同一性违约（ST-04 导入兼容性契约）：%s"
            % "; ".join(mismatches))


class ImportHygieneTest(unittest.TestCase):
    """导入卫生：全新解释器冒烟 + 反向依赖冻结（子进程实测）。"""

    def test_task_manager_imports_cleanly_in_fresh_interpreter(self):
        proc = _run_python(_SMOKE_CODE)
        self.assertEqual(
            proc.returncode, 0,
            "runtime.task_manager 全新解释器导入失败（冒烟违约）：\n%s"
            % proc.stderr)
        self.assertIn("imported", proc.stdout)

    def test_decomposition_modules_do_not_pull_task_manager(self):
        proc = _run_python(_HYGIENE_CODE)
        self.assertEqual(
            proc.returncode, 0,
            "导入卫生违约（分解模块反向依赖 task_manager）：\n%s\n%s"
            % (proc.stdout, proc.stderr))
        self.assertIn("clean", proc.stdout)


if __name__ == "__main__":
    unittest.main()
