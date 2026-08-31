#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.dispatcher 单元测试（v2 工作块 B8.3 + B10.1 租约集成）。

仅 Python 3 标准库（unittest + tempfile），零第三方依赖；被测决策
函数本身零 I/O；唯一触盘的是 B10.1 端到端并发冒烟（tempfile 提供的
临时目录承载租约文件，无 git 需求），不污染真实工作区。

覆盖：
    - patterns_conflict（§66 保守近似）：自身相等 / ** 与其覆盖域内
      文件 / 不相交目录 / 字面量目录前缀 / 纯通配空前缀保守（"*.py"
      与任何模式冲突）/ 单独 "**" / 中段 ** 字面量前缀 / 空侧短路 /
      非法模式抛 OwnershipError（含结构性错误优先于空侧短路）/
      对称性 / 段边界锚定（"src/auth" 不覆盖 "src/authentication.ts"）；
    - plan_dispatch（§64/§65/§67/§78）：并发余量（max_workers 与
      active 占位）/ ownership 冲突（候选间、与 active、active id
      容错）/ 租约闸（他人租约 key 冲突 → lease_conflict、同 owner
      放行、具体文件租约被候选模式覆盖、不相关租约放行、record dict
      容错、ghost active 下租约闸独挑、闸序 ownership 先于租约）/
      quota 四态（AVAILABLE 照常 / PRESSURE 与 UNKNOWN 不再整批挂起
      ——v2.1 wu-21-09 §12：预算收缩职责移交调用方，本层只按传入的
      max_workers 放行 / EXHAUSTED 全转 waiting_quota）/ 闸门顺序
      （PRESSURE 下仍受 ownership 与并发闸约束）/ 依赖层集成
      （未就绪单元不进候选）/ 候选顺序遵循 topo_order / 确定性 /
      入参不可变（deepcopy 比对）/ 非法参数 ValueError / 返回形状 /
      决策组恰好划分候选集 / 三类 deferred reason 全覆盖；
    - max_workers 边界（§82）：1 与 4 合法；0 / 5 / 100 / "2" /
      bool 等非法值 ValueError（消息注明上限与 experimental）；
      缺省 DEFAULT_MAX_WORKERS=2（v2.1 §11.5）；
    - 端到端并发冒烟（B10.1，tempdir）：不相交双单元双派发 → 双租约
      → 第三单元与 A 冲突被租约闸拦下 → A 完成释放 → 重派成功；
      全程 active 记账正确。

运行：
    cd <repo_root> && python3 -m unittest tests.test_dispatcher -v
"""

import copy
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import dependency
from runtime import dispatcher
from runtime import lease
from runtime import ownership


# —— 测试夹具 ——

TID = "dispatch-task-1a2b3c"


def wu(uid, owned=(), deps=(), status="ready", **extra):
    """构造最小可派发 work unit dict（§61 形状；extra 注入可选键）。"""
    unit = {
        "id": uid,
        "objective": "目标 %s" % uid,
        "status": status,
        "depends_on": list(deps),
        "executor": "flash-implementer",
        "ownership": list(owned),
        "verification": ["python3 -m unittest tests.test_dispatcher"],
    }
    unit.update(extra)
    return unit


def reasons(result):
    """从决策 dict 提取 (id, reason) 对列表（断言辅助）。"""
    return [(entry["id"], entry["reason"]) for entry in result["deferred"]]


def topless_pair():
    """互不冲突的两个候选（src/a 与 src/b 两棵子树）。"""
    return [wu("x", ("src/a/**",)), wu("y", ("src/b/**",))]


# —— patterns_conflict（§66 保守近似） ——

class PatternsConflictTest(unittest.TestCase):

    def test_identical_glob_patterns_conflict(self):
        self.assertTrue(dispatcher.patterns_conflict(
            ["src/a/**"], ["src/a/**"]))

    def test_identical_literal_files_conflict(self):
        self.assertTrue(dispatcher.patterns_conflict(
            ["src/auth.ts"], ["src/auth.ts"]))

    def test_globstar_vs_file_under_directory(self):
        self.assertTrue(dispatcher.patterns_conflict(
            ["src/a/**"], ["src/a/x.ts"]))

    def test_disjoint_directories_no_conflict(self):
        self.assertFalse(dispatcher.patterns_conflict(
            ["src/a/**"], ["src/b/**"]))

    def test_literal_directory_prefix_vs_deep_file(self):
        # 纯字面量模式兼具目录前缀语义（compile 的 ^lit(?:/.*)?$）
        self.assertTrue(dispatcher.patterns_conflict(
            ["src/auth"], ["src/auth/deep/x.ts"]))

    def test_pure_wildcard_first_segment_conservative(self):
        # "*.py" 的字面量前缀为空元组 → 与任何模式保守判冲突
        self.assertTrue(dispatcher.patterns_conflict(
            ["*.py"], ["src/**"]))

    def test_alone_globstar_conflicts_with_anything(self):
        # 单独 "**" 前缀同为空元组 → 保守冲突（对称验证）
        self.assertTrue(dispatcher.patterns_conflict(["**"], ["src/a.ts"]))
        self.assertTrue(dispatcher.patterns_conflict(["src/a.ts"], ["**"]))

    def test_disjoint_top_directories_no_conflict(self):
        self.assertFalse(dispatcher.patterns_conflict(
            ["tests/**"], ["src/**"]))

    def test_mid_globstar_literal_prefix(self):
        # "a/**/b" 取 ** 前字面量段 ("a",)，是 ("a", "c", "b") 的前缀
        self.assertTrue(dispatcher.patterns_conflict(
            ["a/**/b"], ["a/c/b"]))

    def test_empty_side_no_conflict(self):
        self.assertFalse(dispatcher.patterns_conflict([], ["src/**"]))
        self.assertFalse(dispatcher.patterns_conflict(["src/**"], []))
        self.assertFalse(dispatcher.patterns_conflict([], []))

    def test_illegal_pattern_raises_ownership_error(self):
        with self.assertRaises(ownership.OwnershipError):
            dispatcher.patterns_conflict(["src//x"], ["src/a"])
        with self.assertRaises(ownership.OwnershipError):
            dispatcher.patterns_conflict(["src/a"], ["a//b"])
        with self.assertRaises(ownership.OwnershipError):
            dispatcher.patterns_conflict([42], ["src/a"])

    def test_illegal_pattern_raises_even_when_other_side_empty(self):
        # 结构性错误优先于空侧短路（与 classify_paths 纪律一致）
        with self.assertRaises(ownership.OwnershipError):
            dispatcher.patterns_conflict([], ["src//x"])

    def test_verdict_is_symmetric(self):
        conflicting = (["src/a/**"], ["src/a/x.ts"])
        disjoint = (["src/a/**"], ["src/b/**"])
        self.assertTrue(
            dispatcher.patterns_conflict(conflicting[0], conflicting[1]))
        self.assertTrue(
            dispatcher.patterns_conflict(conflicting[1], conflicting[0]))
        self.assertFalse(
            dispatcher.patterns_conflict(disjoint[0], disjoint[1]))
        self.assertFalse(
            dispatcher.patterns_conflict(disjoint[1], disjoint[0]))

    def test_segment_boundary_anchor(self):
        # 锚定 compile_pattern 语义：目录前缀不越过段边界
        self.assertFalse(dispatcher.patterns_conflict(
            ["src/auth"], ["src/authentication.ts"]))

    def test_multiple_patterns_any_pair_hits(self):
        self.assertFalse(dispatcher.patterns_conflict(
            ["docs/**", "src/a/**"], ["src/b/**", "lib/**"]))
        self.assertTrue(dispatcher.patterns_conflict(
            ["src/a/**"], ["lib/**", "src/a/x.ts"]))

    def test_accepts_tuples_and_bare_string(self):
        self.assertTrue(dispatcher.patterns_conflict(
            ("src/a/**",), ("src/a/**",)))
        self.assertTrue(dispatcher.patterns_conflict("src/a/**", "src/a/**"))


# —— plan_dispatch：并发余量 ——

class PlanConcurrencyTest(unittest.TestCase):

    def test_default_budget_two_dispatches_both(self):
        # v2.1 §11.5：不传 max_workers 时缺省预算 DEFAULT_MAX_WORKERS=2
        # （并发默认 2）；topo 序 m 先于 z，两个都拿到槽位
        units = [wu("z", ("src/z/**",)), wu("m", ("src/m/**",))]
        self.assertEqual(dispatcher.DEFAULT_MAX_WORKERS, 2)
        result = dispatcher.plan_dispatch(units)
        self.assertEqual(result["dispatch"], ["m", "z"])
        self.assertEqual(result["deferred"], [])

    def test_explicit_budget_one_second_concurrency_deferred(self):
        # 调用方显式收紧到 1：topo 序 m 先于 z，z 落 concurrency
        units = [wu("z", ("src/z/**",)), wu("m", ("src/m/**",))]
        result = dispatcher.plan_dispatch(units, max_workers=1)
        self.assertEqual(result["dispatch"], ["m"])
        self.assertEqual(reasons(result), [("z", "concurrency")])

    def test_two_workers_dispatch_both(self):
        result = dispatcher.plan_dispatch(topless_pair(), max_workers=2)
        self.assertEqual(result["dispatch"], ["x", "y"])
        self.assertEqual(result["deferred"], [])

    def test_active_consumes_concurrency_slots(self):
        units = [wu("run", ("src/run/**",), status="running"),
                 wu("m", ("src/m/**",)), wu("z", ("src/z/**",))]
        result = dispatcher.plan_dispatch(units, max_workers=2,
                                          active=["run"])
        self.assertEqual(result["dispatch"], ["m"])
        self.assertEqual(reasons(result), [("z", "concurrency")])
        self.assertEqual(result["active"], ["run"])

    def test_active_counted_as_given_for_slots(self):
        # len(active) 原样扣减：ghost 不在 units 也占槽（容错锁定）
        result = dispatcher.plan_dispatch([wu("m", ("src/m/**",))],
                                          max_workers=1, active=["ghost"])
        self.assertEqual(reasons(result), [("m", "concurrency")])


# —— plan_dispatch：ownership 冲突闸（§66） ——

class PlanOwnershipTest(unittest.TestCase):

    def test_conflict_between_candidates_second_deferred(self):
        units = [wu("x", ("src/auth/**",)), wu("y", ("src/auth/x.ts",))]
        result = dispatcher.plan_dispatch(units, max_workers=2)
        self.assertEqual(result["dispatch"], ["x"])
        self.assertEqual(reasons(result), [("y", "ownership_conflict")])

    def test_conflict_with_active_unit(self):
        units = [wu("run", ("src/auth/**",), status="running"),
                 wu("y", ("src/auth/x.ts",))]
        result = dispatcher.plan_dispatch(units, max_workers=2,
                                          active=["run"])
        self.assertEqual(result["dispatch"], [])
        self.assertEqual(reasons(result), [("y", "ownership_conflict")])

    def test_non_conflicting_candidates_both_dispatched(self):
        result = dispatcher.plan_dispatch(topless_pair(), max_workers=2)
        self.assertEqual(result["dispatch"], ["x", "y"])

    def test_active_id_not_in_units_ownership_ignored(self):
        # 容错锁定：找不到的 active id 按空 ownership，不参与冲突
        result = dispatcher.plan_dispatch([wu("y", ("src/auth/**",))],
                                          max_workers=2, active=["ghost"])
        self.assertEqual(result["dispatch"], ["y"])

    def test_conflict_judged_against_approved_accumulation(self):
        # 第三个候选与已批准候选冲突（即便与 active 无关）
        units = [wu("x", ("src/auth/**",)),
                 wu("y", ("src/auth/deep/x.ts",)),
                 wu("z", ("src/other/**",))]
        result = dispatcher.plan_dispatch(units, max_workers=3)
        self.assertEqual(result["dispatch"], ["x", "z"])
        self.assertEqual(reasons(result), [("y", "ownership_conflict")])


# —— plan_dispatch：quota 四态（§67） ——

class PlanQuotaTest(unittest.TestCase):

    def three_ready(self):
        return [wu("z", ("src/z/**",)), wu("k", ("src/k/**",)),
                wu("m", ("src/m/**",))]

    def test_available_dispatches_normally(self):
        result = dispatcher.plan_dispatch(self.three_ready(),
                                          max_workers=2)
        self.assertEqual(result["dispatch"], ["k", "m"])
        self.assertEqual(reasons(result), [("z", "concurrency")])

    def test_exhausted_moves_all_ready_to_waiting_quota(self):
        # quota 闸先于并发闸：max_workers=1 也零派发，全转 waiting_quota
        result = dispatcher.plan_dispatch(self.three_ready(),
                                          max_workers=1,
                                          quota_status="EXHAUSTED")
        self.assertEqual(result["waiting_quota"], ["k", "m", "z"])
        self.assertEqual(result["dispatch"], [])
        self.assertEqual(result["deferred"], [])

    def test_unknown_no_longer_suppresses_budget_by_caller(self):
        # v2.1 wu-21-09：UNKNOWN 不再整批挂起（unknown_suppressed 分支
        # 删除）——预算收缩职责在调用方（task_manager 经 §12 表折算
        # UNKNOWN→1）；本层按传入的 max_workers 放行，落选者记
        # concurrency（仍是候选，不是 quota 挂起），waiting_quota 为空
        result = dispatcher.plan_dispatch(self.three_ready(),
                                          max_workers=1,
                                          quota_status="UNKNOWN")
        self.assertEqual(result["dispatch"], ["k"])  # topo 序 k 最先
        self.assertEqual(result["waiting_quota"], [])
        self.assertEqual(reasons(result),
                         [("m", "concurrency"), ("z", "concurrency")])

    def test_unknown_within_budget_dispatches_normally(self):
        # 调用方给足预算时 UNKNOWN 与 AVAILABLE 无差别（本层不解释 quota）
        result = dispatcher.plan_dispatch(self.three_ready(),
                                          max_workers=4,
                                          quota_status="UNKNOWN")
        self.assertEqual(result["dispatch"], ["k", "m", "z"])
        self.assertEqual(result["deferred"], [])


# —— plan_dispatch：PRESSURE / UNKNOWN 预算由调用方（wu-21-09 §12） ——

class PlanPressureTest(unittest.TestCase):
    """PRESSURE 不再整批抑制（pressure_suppressed / small_only 分支
    删除）：本层对 PRESSURE / UNKNOWN 一视同仁，按调用方折算后的
    max_workers 放行——§12 表 PRESSURE→1 的收缩发生在 task_manager
    （tests/test_parallel_activation.py 端到端锚定）。"""

    def small_pair(self):
        return [wu("s", ("src/s/**",), small=True),
                wu("big", ("src/big/**",))]

    def test_pressure_dispatches_normally_within_budget(self):
        # small 键不再有放行语义：带 small 的候选照常按预算派发
        result = dispatcher.plan_dispatch(self.small_pair(),
                                          quota_status="PRESSURE")
        self.assertEqual(result["dispatch"], ["big", "s"])
        self.assertEqual(result["deferred"], [])

    def test_pressure_budget_shrunk_by_caller_shows_concurrency(self):
        # 预算 1（调用方折算 PRESSURE→1 后的形态）：只批 topo 头一个，
        # 落选理由是 concurrency 而非任何 quota 挂起
        result = dispatcher.plan_dispatch(self.small_pair(),
                                          max_workers=1,
                                          quota_status="PRESSURE")
        self.assertEqual(result["dispatch"], ["big"])
        self.assertEqual(reasons(result), [("s", "concurrency")])

    def test_pressure_still_bound_by_ownership_gate(self):
        # 闸门顺序：PRESSURE 下 ownership 闸照常先裁
        units = [wu("run", ("src/auth/**",), status="running"),
                 wu("s", ("src/auth/x.ts",))]
        result = dispatcher.plan_dispatch(
            units, max_workers=2, active=["run"], quota_status="PRESSURE")
        self.assertEqual(reasons(result), [("s", "ownership_conflict")])

    def test_pressure_still_bound_by_concurrency_gate(self):
        # 闸门顺序：PRESSURE 下并发余量闸照常约束（active 占槽）
        units = [wu("run", ("src/run/**",), status="running"),
                 wu("s", ("src/s/**",))]
        result = dispatcher.plan_dispatch(
            units, max_workers=1, active=["run"], quota_status="PRESSURE")
        self.assertEqual(reasons(result), [("s", "concurrency")])


# —— plan_dispatch：依赖层集成 / 顺序 / 确定性 / 纯度 ——

class PlanIntegrationTest(unittest.TestCase):

    def test_unready_units_never_candidates(self):
        # ready 但依赖失败 / waiting_dependency / pending 均不进候选
        units = [
            wu("done", ("src/done/**",), status="completed"),
            wu("bad", ("src/bad/**",), status="failed"),
            wu("b", ("src/b/**",), deps=("bad",)),
            wu("w", ("src/w/**",), deps=("done",),
               status="waiting_dependency"),
            wu("p", ("src/p/**",), status="pending"),
            wu("r", ("src/r/**",)),
        ]
        result = dispatcher.plan_dispatch(units, max_workers=2)
        self.assertEqual(result["dispatch"], ["r"])
        self.assertEqual(result["deferred"], [])
        self.assertEqual(result["waiting_quota"], [])
        # 与依赖层直接对账：候选宇宙就是 ready_units
        self.assertEqual(dependency.ready_units(units), ["r"])

    def test_candidates_follow_topo_order(self):
        # 依赖链 a(completed) → b；独立 c、m。topo：a, b, c, m
        units = [
            wu("m", ("src/m/**",)),
            wu("b", ("src/b/**",), deps=("a",)),
            wu("c", ("src/c/**",)),
            wu("a", ("src/a/**",), status="completed"),
        ]
        result = dispatcher.plan_dispatch(units, max_workers=1)
        # 候选 [b, c, m]：链头 b 先拿槽位（依赖先于被依赖者）
        self.assertEqual(result["dispatch"], ["b"])
        self.assertEqual(reasons(result),
                         [("c", "concurrency"), ("m", "concurrency")])

    def test_decision_groups_partition_candidates(self):
        units = [wu("z", ("src/z/**",)), wu("m", ("src/m/**",)),
                 wu("k", ("src/k/**",))]
        result = dispatcher.plan_dispatch(units, max_workers=1)
        total = (len(result["dispatch"]) + len(result["waiting_quota"])
                 + len(result["deferred"]))
        self.assertEqual(total, 3)  # 每个候选恰好归入其一

    def test_deterministic_same_input_same_output(self):
        units = [wu("z", ("src/z/**",)), wu("x", ("src/auth/**",)),
                 wu("y", ("src/auth/x.ts",)), wu("m", ("src/m/**",))]
        first = dispatcher.plan_dispatch(units, max_workers=2)
        second = dispatcher.plan_dispatch(units, max_workers=2)
        self.assertEqual(first, second)
        # 顺带锚定混合场景：m、x 派发；y 与 x 冲突；z 排队
        self.assertEqual(first["dispatch"], ["m", "x"])
        self.assertEqual(reasons(first),
                         [("y", "ownership_conflict"),
                          ("z", "concurrency")])

    def test_input_units_not_mutated(self):
        units = [wu("m", ("src/m/**",)),
                 wu("run", ("src/run/**",), status="running"),
                 wu("z", ("src/z/**",))]
        snapshot = copy.deepcopy(units)
        for kwargs in ({}, {"max_workers": 2, "active": ["run"]},
                       {"quota_status": "EXHAUSTED"},
                       {"quota_status": "UNKNOWN"},
                       {"quota_status": "PRESSURE"}):
            dispatcher.plan_dispatch(units, **kwargs)
        self.assertEqual(units, snapshot)

    def test_empty_units_all_groups_empty(self):
        self.assertEqual(
            dispatcher.plan_dispatch([]),
            {"dispatch": [], "waiting_quota": [], "deferred": [],
             "max_workers": 2, "active": [], "quota_status": "AVAILABLE"})


# —— plan_dispatch：max_workers 边界（§82 上限 4，experimental） ——

class PlanMaxWorkersBoundTest(unittest.TestCase):

    def test_boundary_one_and_four_are_legal(self):
        # workers=1：只有 x 拿到槽位，y 落 concurrency
        result = dispatcher.plan_dispatch(topless_pair(), max_workers=1)
        self.assertEqual(result["max_workers"], 1)
        self.assertEqual(result["dispatch"], ["x"])
        self.assertEqual(reasons(result), [("y", "concurrency")])
        # workers=4（§82 上界）：双派发
        result = dispatcher.plan_dispatch(
            topless_pair(), max_workers=lease.DEFAULT_MAX_WORKERS_LIMIT)
        self.assertEqual(result["max_workers"], 4)
        self.assertEqual(result["dispatch"], ["x", "y"])
        self.assertEqual(result["deferred"], [])

    def test_out_of_bounds_and_invalid_raise_value_error(self):
        for bad in (0, -1, 5, 100, "2", 1.5, True, None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as ctx:
                    dispatcher.plan_dispatch([], max_workers=bad)
                # 消息注明 §82 上限与 experimental 状态
                self.assertIn("§82", str(ctx.exception))
                self.assertIn("experimental", str(ctx.exception))

    def test_invalid_leases_type_raises_value_error(self):
        for bad in ([], "src/**", 42):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    dispatcher.plan_dispatch([], leases=bad)


# —— plan_dispatch：租约闸（B10.1，§78/§81） ——

class PlanLeaseGateTest(unittest.TestCase):

    def test_foreign_lease_conflicting_key_deferred(self):
        # 候选 ownership 与他人租约 key 冲突 → deferred("lease_conflict")
        result = dispatcher.plan_dispatch(
            [wu("x", ("src/a/**",))], max_workers=2,
            leases={"src/a/**": "unit-other"})
        self.assertEqual(result["dispatch"], [])
        self.assertEqual(reasons(result), [("x", "lease_conflict")])

    def test_same_owner_lease_passes(self):
        # §78 同 owner 放行：候选 id == 租约 owner
        result = dispatcher.plan_dispatch(
            [wu("x", ("src/a/**",))], max_workers=2,
            leases={"src/a/**": "x"})
        self.assertEqual(result["dispatch"], ["x"])
        self.assertEqual(result["deferred"], [])

    def test_specific_file_lease_covered_by_candidate_pattern(self):
        # 租约 key 是具体文件、候选模式覆盖它（src/** vs 租约 src/a.ts）
        # → patterns_conflict 混判冲突
        result = dispatcher.plan_dispatch(
            [wu("x", ("src/**",))], max_workers=2,
            leases={"src/a.ts": "unit-other"})
        self.assertEqual(reasons(result), [("x", "lease_conflict")])

    def test_unrelated_lease_passes(self):
        result = dispatcher.plan_dispatch(
            [wu("x", ("src/a/**",))], max_workers=2,
            leases={"src/b/**": "unit-other"})
        self.assertEqual(result["dispatch"], ["x"])
        self.assertEqual(result["deferred"], [])

    def test_lease_record_dict_value_tolerated(self):
        # 容错：lease_state 直读形状 {path: {"owner", ...}} 可直接传入
        result = dispatcher.plan_dispatch(
            [wu("x", ("src/a/**",))], max_workers=2,
            leases={"src/a/**": {"owner": "unit-other",
                                 "acquired_at": "2026-01-01T00:00:00.000Z"}})
        self.assertEqual(reasons(result), [("x", "lease_conflict")])

    def test_lease_gate_fires_when_active_unit_is_ghost(self):
        # 正交叠加的价值：active id 不在 units（容错按空 ownership，
        # ownership 闸无信息）时，租约闸仍凭落盘租约拦下
        result = dispatcher.plan_dispatch(
            [wu("x", ("src/a/**",))], max_workers=2, active=["ghost"],
            leases={"src/a/**": "ghost"})
        self.assertEqual(reasons(result), [("x", "lease_conflict")])

    def test_gate_order_ownership_before_lease(self):
        # 两闸同拦时先到先裁决：ownership_conflict（闸门 2）先于租约闸
        units = [wu("run", ("src/a/**",), status="running"),
                 wu("x", ("src/a/**",))]
        result = dispatcher.plan_dispatch(
            units, max_workers=2, active=["run"],
            leases={"src/a/**": "run"})
        self.assertEqual(reasons(result), [("x", "ownership_conflict")])


# —— 端到端并发冒烟（B10.1，tempdir，无 git 需求） ——

class EndToEndParallelSmokeTest(unittest.TestCase):
    """租约生命周期 + plan_dispatch 联动闭环（§81）：
    不相交双单元双派发 → 派发前双租约 → 第三单元与 A 冲突被租约闸
    拦下（active 单元已离开待派发清单的常态）→ A 完成释放 → 重派
    成功；全程 active 记账正确。"""

    def test_parallel_dispatch_with_lease_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = tmp
            units = [wu("ua", ("src/a/**",)),
                     wu("ub", ("src/b/**",)),
                     wu("uc", ("src/a/c.ts",))]

            def held_leases():
                return lease.lease_state(root, TID)

            # 第一轮：ownership 不相交（§66）→ 双派发
            # （首批就绪清单只含 ua/ub；uc 稍后才就绪进入待派发清单）
            plan1 = dispatcher.plan_dispatch([units[0], units[1]],
                                             max_workers=2)
            self.assertEqual(plan1["dispatch"], ["ua", "ub"])
            self.assertEqual(plan1["deferred"], [])
            self.assertEqual(plan1["active"], [])
            # Task Manager 派发前获取租约（§78 获取时点），单元入 active
            lease.acquire_lease(root, TID, "ua", ["src/a/**"])
            lease.acquire_lease(root, TID, "ub", ["src/b/**"])
            active = ["ua", "ub"]

            # 第二轮：仅剩 uc 待派发；ua/ub 已不在待派发清单（真实编排
            # 常态）——ownership 闸无信息，租约闸凭落盘租约拦下与 A 冲突
            plan2 = dispatcher.plan_dispatch([units[2]], max_workers=2,
                                             active=active,
                                             leases=held_leases())
            self.assertEqual(plan2["dispatch"], [])
            self.assertEqual(plan2["active"], active)
            self.assertEqual(reasons(plan2), [("uc", "lease_conflict")])

            # A 完成（§78 释放时点：completed）→ 释放租约 + 离开 active
            self.assertEqual(lease.release_lease(root, TID, "ua"),
                             ["src/a/**"])
            active = ["ub"]

            # 第三轮：A 的租约已释放 → uc 重派成功
            plan3 = dispatcher.plan_dispatch([units[2]], max_workers=2,
                                             active=active,
                                             leases=held_leases())
            self.assertEqual(plan3["dispatch"], ["uc"])
            self.assertEqual(plan3["active"], active)
            self.assertEqual(reasons(plan3), [])
            # uc 派发前获取自己的租约成功（与 ub 的 src/b/** 不冲突）
            lease.acquire_lease(root, TID, "uc", ["src/a/c.ts"])
            self.assertEqual(lease.held_by(root, TID, "uc"), ["src/a/c.ts"])
            self.assertEqual(lease.held_by(root, TID, "ub"), ["src/b/**"])
            self.assertEqual(lease.held_by(root, TID, "ua"), [])


# —— plan_dispatch：参数校验 / 返回形状 ——

class PlanContractTest(unittest.TestCase):

    def test_invalid_max_workers_raises_value_error(self):
        for bad in (0, -1, "2", 1.5, True, None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    dispatcher.plan_dispatch([], max_workers=bad)

    def test_invalid_quota_status_raises_value_error(self):
        for bad in ("FOO", "", None, "available"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    dispatcher.plan_dispatch([], quota_status=bad)

    def test_return_shape_and_echo(self):
        units = [wu("m", ("src/m/**",)),
                 wu("run", ("src/run/**",), status="running")]
        result = dispatcher.plan_dispatch(units, max_workers=3,
                                          active=("run",))
        self.assertEqual(set(result),
                         {"dispatch", "waiting_quota", "deferred",
                          "max_workers", "active", "quota_status"})
        self.assertEqual(result["max_workers"], 3)
        self.assertEqual(result["active"], ["run"])  # tuple 入参回传 list
        self.assertEqual(result["quota_status"], "AVAILABLE")

    def test_cycle_raises_value_error_via_topo(self):
        # 环是结构性错误：topo_order 的 ValueError 向上传播
        units = [wu("a", ("src/a/**",), deps=("b",)),
                 wu("b", ("src/b/**",), deps=("a",))]
        with self.assertRaises(ValueError):
            dispatcher.plan_dispatch(units)


# —— 三类 deferred reason 全覆盖（验收锚；v2.1 wu-21-09 起词汇收敛） ——

class DeferReasonCoverageTest(unittest.TestCase):

    def test_all_three_defer_reasons_reachable(self):
        base = [wu("m", ("src/m/**",)), wu("z", ("src/z/**",))]
        pair = [wu("x", ("src/auth/**",)), wu("y", ("src/auth/x.ts",))]
        collected = set()
        # concurrency：余量耗尽
        collected.update(
            r for _, r in reasons(dispatcher.plan_dispatch(
                base, max_workers=1)))
        # lease_conflict：他人租约 key 与候选 ownership 冲突（§78）
        collected.update(
            r for _, r in reasons(dispatcher.plan_dispatch(
                [wu("x", ("src/auth/**",))], max_workers=2,
                leases={"src/auth/**": "unit-other"})))
        # ownership_conflict：候选间冲突
        collected.update(
            r for _, r in reasons(dispatcher.plan_dispatch(pair,
                                                           max_workers=2)))
        # quota 状态不再产生挂起理由（UNKNOWN/PRESSURE 整批挂起分支删除）
        self.assertEqual(len(dispatcher.DEFER_REASONS), 3)
        self.assertEqual(collected, set(dispatcher.DEFER_REASONS))
        self.assertNotIn("unknown_suppressed", dispatcher.DEFER_REASONS)
        self.assertNotIn("pressure_suppressed", dispatcher.DEFER_REASONS)
        self.assertNotIn("small_only", dispatcher.DEFER_REASONS)


if __name__ == "__main__":
    unittest.main()
