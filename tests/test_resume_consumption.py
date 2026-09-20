#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2.2 C7 resume 消费接线单元测试（wu-22-C7 ①②；修正计划 §C7 / §15.1
消费事务点冻结 / §22.1 / §22.6 / QC-07）。

v2.4 Phase 2（W6，P2-F）：本文件主体（①-⑩ 的 resume 消费接线用例）
以恢复编排（resume_from_quota / _resume_consumption）与其 CLI 退出码
契约为被测面——该编排已随 v2.3 执行面退役删除，相应用例一并移除；
保留的是身份闸直接读者（quota.identity 消费面）的存活锚定。

锚定对象：
  - §15.1：quota_boundary_consumed 唯一合法写入点 = Resume Controller
    的 resume commit point（转态 durable + mark 之后）；同一 epoch 绝
    不二次消费（幂等证据可修正投影）；
  - §22.1：manual / notify 永不消费；授权预闸（auto_resume ∈
    {auto_once, until_done} AND source=="user" AND remaining>0）不过 →
    零调用零副作用；
  - §C7 / 修正计划 C7：消费事件辅助证据只从当前 epoch 快照真实窗口
    派生（D10 形态 "kind:reset_at"，最早可解析窗口，无宽限；无可解析
    窗口跳过消费不虚构 §31）；RH-04 起事件字段记作
    representative_boundary_id（代表窗口身份；RH-04 前旧事件为 legacy
    键 executable_boundary_id，读侧双键兼容；consumption face 返回键
    名 executable_boundary_id 冻结不变）；epoch_id 恒为幂等 / 归属
    权威键；
  - §22.6：迁移先于新形态事件（先 migrate one-shot 再 record）；
  - QC-07 / 退出码契约（v2.2 C7 ②）：消费记账 OSError 自然上抛（证据
    丢失必须可见）→ CLI quota-resume 退出码 3 + {error, guidance}；
    三查竞态 TaskManagerError → consumption face 降级不抛（转态已
    durable）；legacy 未注册任务零消费键。

覆盖映射（规格报告要求逐条对应）：
    ① happy path 恰一次消费 + 事件冻结字段（HappyPathConsumptionTest）
    ② 迁移事件先于首条消费事件的 journal 顺序（同上 + 幂等命中恢复）
    ③ boundary 派生 D10 最早可解析窗 + 不可解析跳过（MultiWindow /
       UnparseableWindow）
    ④ 授权预闸矩阵 manual / notify / 非 user / 预算耗尽 → 零调用零副
       作用 + 中文 reason（PreGateMatrixTest）
    ⑤ 同 epoch 重放（任务已 executing）零二次消费（SameEpochReplayTest）
    ⑥ journal 证据在案的幂等命中 face（idempotent=True 零新建事件）
    ⑦ 三查竞态 TaskManagerError face 降级 / record 与 migrate 的
       OSError 上抛（RaceAndEvidenceLossTest）
    ⑧ legacy 未注册任务无 consumption 键（LegacyUnchangedTest）
    ⑨ CLI：退出码 3 分类 + guidance 面 / TaskManagerError 仍 1 /
       consumption 面透传（CliExitContractTest）
    ⑩ RH-03 write-ahead pending journal 序 + 幸存 pending 经 resume 链
       恢复闭合不双消费（WriteAheadJournalOrderTest，wu-rh03）

fixture：tempfile 仓库 + runtime.state 构造（scratch 任务目录，不碰
真实账本）；resolver / epoch / 记账全注入——零网络零宿主调用。仅
Python 3 标准库（unittest + tempfile），零第三方依赖。

运行：
    cd <repo_root> && python3 -m unittest tests.test_resume_consumption -v
"""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import state
from runtime.continuity import resume as _cr
from runtime.continuity import subscription as _cs
from runtime.quota import epoch as quota_epoch
from runtime.quota import identity as quota_identity
from runtime.quota import resolver as quota_resolver

TID = "resume-consume-1a2b3c"
CONFIRMED_AT = "2026-09-03T00:00:00+00:00"
VERIFY_CMD = "python3 -m unittest tests.test_resume_consumption"

# 真实窗口夹具（§27 形状；epoch 身份由 quota_epoch 从窗口折算——绝不
# 手写指纹，与 tests/test_quota_subscription 同纪律）：
#   WINDOWS_A = 耗尽时刻的 epoch（reset 12:00Z，窗 EXHAUSTED）
#   WINDOWS_B = 窗口滚动后的新 epoch（reset 17:00Z，AVAILABLE →
#               executable=True 的恢复场景；D10 boundary = 最早可解析
#               窗 five_hour:2026-09-01T17:00:00Z）
WINDOWS_A = [{"kind": "five_hour", "status": "EXHAUSTED",
              "used_percent": 100.0, "remaining_percent": 0.0,
              "reset_at": "2026-09-01T12:00:00Z"}]
WINDOWS_B = [{"kind": "five_hour", "status": "AVAILABLE",
              "used_percent": 10.0, "remaining_percent": 90.0,
              "reset_at": "2026-09-01T17:00:00Z"}]
# 多窗：weekly reset 更晚、five_hour 更早——D10 取最早可解析窗
WINDOWS_MULTI = [{"kind": "weekly", "status": "AVAILABLE",
                  "used_percent": 5.0, "remaining_percent": 95.0,
                  "reset_at": "2026-09-05T00:00:00Z"},
                 {"kind": "five_hour", "status": "AVAILABLE",
                  "used_percent": 10.0, "remaining_percent": 90.0,
                  "reset_at": "2026-09-01T17:00:00Z"}]
# 全不可解析：epoch 身份可折算（unknown 占位）但无 D10 boundary（§31）
WINDOWS_OPAQUE = [{"kind": "five_hour", "status": "AVAILABLE",
                   "used_percent": 1.0, "remaining_percent": 99.0,
                   "reset_at": "not-a-timestamp"}]
EPOCH_OF_A = quota_epoch.epoch_id(WINDOWS_A)
EPOCH_OF_B = quota_epoch.epoch_id(WINDOWS_B)
EPOCH_OF_MULTI = quota_epoch.epoch_id(WINDOWS_MULTI)
EPOCH_OF_OPAQUE = quota_epoch.epoch_id(WINDOWS_OPAQUE)
BOUNDARY_OF_B = "five_hour:2026-09-01T17:00:00Z"

# 两个不同的非秘密 16-hex 身份指纹（v2.2.1 WU-221-B2 QuotaIdentity）
HASH_A = "a1b2c3d4e5f60718"
HASH_B = "b2c3d4e5f6071882"

# resume_from_quota 冻结五键（legacy 输出零变化硬锚，同 C5b 口径）
RESUME_FROZEN_KEYS = ["recommended_resume_at", "resumed", "status",
                      "unit_recovery", "wake_budget_remaining"]
# C7 consumption face 冻结键（reason|error 按形态二选一附加）
CONSUMPTION_BASE_KEYS = ["consumed", "consumed_quota_windows", "epoch_id",
                         "executable_boundary_id", "idempotent",
                         "remaining_quota_windows"]


def make_state(**kwargs):
    """构造默认合法的完整状态 dict（solo 路由过全部不变量）。"""
    task_id = kwargs.pop("task_id", TID)
    goal = kwargs.pop("goal", "v2.2 C7 resume consumption 夹具任务")
    return state.new_task_state(task_id, goal, {"mode": "solo"}, **kwargs)


def make_unit(uid, status="waiting_quota"):
    """构造通过 §61 契约校验的最小 work unit dict。"""
    return {"id": uid, "objective": "C7 consumption unit %s" % uid,
            "status": status, "depends_on": [],
            "executor": "flash-implementer",
            "ownership": ["src/%s.py" % uid],
            "verification": [VERIFY_CMD]}


def fake_resolved(status):
    """resolve_quota_status 冻结四键的测试桩。"""
    return {"status": status, "source": "provider",
            "evaluated_at": "2026-09-01T12:00:00.000Z",
            "reason": "fixture"}


def fake_detail(status, windows, snapshot="auto"):
    """resolve_quota_detail 冻结四键的测试桩。"""
    if snapshot == "auto":
        snapshot = {"provider": "fixture",
                    "fetched_at": "2026-09-01T12:00:00.000Z",
                    "status": status, "windows": windows}
    return {"source": "provider", "status": status,
            "snapshot": snapshot,
            "fetched_at": "2026-09-01T12:00:00.000Z"}


def write_identity_cache(repo, *, status="AVAILABLE", windows=None,
                         provider_identity_hash=None):
    """按 resolver 缓存布局落盘 quota-cache.json（可携带身份指纹；
    provider_identity_hash=None 模拟 v2.2 legacy 无指纹缓存）。"""
    payload = {
        "provider": "fixture",
        "fetched_at": "2026-09-01T12:00:00.000Z",
        "status": status,
        "snapshot": {"provider": "fixture",
                     "fetched_at": "2026-09-01T12:00:00.000Z",
                     "status": status, "windows": windows},
    }
    if provider_identity_hash is not None:
        payload["provider_identity_hash"] = provider_identity_hash
    quota_resolver._save_cache(quota_resolver._cache_path(repo), payload)


class DirectReaderIdentityGateTest(unittest.TestCase):
    """B2 面 6（b2-review carryover 收口）：绕过 resolver 层级的
    _load_cache 直接读者补上身份闸——异身份缓存视同无缓存（各走既有
    no-cache 路径），legacy 无指纹缓存保守信任（行为逐字不变）。
    （_execution_phase_decision / wake-plan 直接读者随 v2.3 执行面
    退役，对应用例已移除。）"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = self._tmp.name

    def under_identity(self, identity):
        return mock.patch.object(quota_identity,
                                 "compute_provider_identity_hash",
                                 return_value=identity)

    def test_recommended_resume_foreign_cache_none(self):
        # 恢复建议折算直接读者：异身份缓存 → None 不虚构建议时刻；
        # legacy 缓存照常折算
        write_identity_cache(self.repo, status="EXHAUSTED",
                             windows=WINDOWS_A,
                             provider_identity_hash=HASH_A)
        with self.under_identity(HASH_B):
            self.assertIsNone(
                _cr._evaluation_from_refreshed_cache(self.repo))
        with self.under_identity(HASH_A):
            evaluation = _cr._evaluation_from_refreshed_cache(self.repo)
        self.assertIsNotNone(evaluation)
        self.assertEqual(evaluation["status"], "EXHAUSTED")

    def test_transition_epoch_context_foreign_cache_none(self):
        # 转态点订阅折算直接读者：异身份缓存 → None → 不注册零副作用
        write_identity_cache(self.repo, status="EXHAUSTED",
                             windows=WINDOWS_A,
                             provider_identity_hash=HASH_A)
        with self.under_identity(HASH_B):
            self.assertIsNone(
                _cs._epoch_context_at_transition(self.repo))

    def test_transition_epoch_context_carries_same_identity(self):
        write_identity_cache(self.repo, status="EXHAUSTED",
                             windows=WINDOWS_A,
                             provider_identity_hash=HASH_A)
        with self.under_identity(HASH_A):
            context = _cs._epoch_context_at_transition(self.repo)
        self.assertIsNotNone(context)
        self.assertEqual(context["epoch_id"], EPOCH_OF_A)
        self.assertEqual(context["provider_identity_hash"], HASH_A)

    def test_transition_epoch_context_legacy_cache_no_identity_key(self):
        write_identity_cache(self.repo, status="EXHAUSTED",
                             windows=WINDOWS_A,
                             provider_identity_hash=None)
        with self.under_identity(HASH_A):
            context = _cs._epoch_context_at_transition(self.repo)
        self.assertIsNotNone(context)
        self.assertNotIn("provider_identity_hash", context)


if __name__ == "__main__":
    unittest.main()
