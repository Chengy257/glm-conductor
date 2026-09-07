#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.quota.resolver 单元测试（v2.1 §13 Runtime Quota，wu-21-10）。

仅 Python 3 标准库（unittest + tempfile + unittest.mock），零第三方
依赖；缓存文件与任务账本全部落在 tempfile.TemporaryDirectory 提供的
scratch 目录（resolver 不触指纹、不需要 git；纪律：绝不触碰仓库内
.glm-conductor/ 真实账本）。全部测试零真实网络：凭证经
resolver.resolve_credential 名字 monkeypatch 注入（或注入
(None, None) 模拟无凭证），provider 经 resolver._build_providers
工厂 monkeypatch 注入 fake provider（注入面与 report.py 同款）。

覆盖（规格测试清单 12 项；第 12 项 = 既有 test_task_manager /
test_wave_dispatch 缺省调用显式化后全绿，由全量 unittest 提供）：
    1. fresh cache 命中：source=cache_fresh、provider 工厂未被调用
       （零网络）；
    2. 缓存过期 + provider 成功：source=provider、status 来自
       scheduler.evaluate、缓存被刷新（fetched_at/snapshot/status
       更新）、timeout_seconds 透传工厂；
    3. 缓存过期 + provider 异常：source=cache_stale、status=缓存值、
       reason 含「过期」、缓存文件不被覆写；
    4. 无缓存 + 无凭证：source=none、status=UNKNOWN；
    5. 无缓存 + provider 异常：none/UNKNOWN；两个 provider 各试一轮
       （绝不重试网络）；
    6. 缓存损坏 JSON：视为无缓存 → none/UNKNOWN；
    7. force_refresh=True：fresh 缓存也走 provider；
    8. credential safety：返回 dict 与缓存文件序列化后不含凭证值与
       key/token 字样；
    9. task_manager 集成：resolver 返回 EXHAUSTED → prepare_dispatch
       抛 waiting_quota 口径 TaskManagerError + journal 有
       quota_resolved 事件（status/source/evaluated_at 三键）；
    10. task_manager 集成：显式 quota_status="AVAILABLE" → 零解析
        （resolver mock 未被调用）零 quota_resolved 事件；
    11. prepare_dispatch_wave：resolver UNKNOWN → worker_budget=1、
        单单元 wave、wave 记录 quota_status 记解析后实际值。

运行：
    cd <repo_root> && python3 -m unittest tests.test_quota_resolver -v
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import journal, lease, state, task_manager, work_unit
from runtime.quota import resolver
from runtime.quota.provider import QuotaProviderError

NOW = datetime(2026, 8, 31, 5, 0, 0, tzinfo=timezone.utc)
NOW_ISO = "2026-08-31T05:00:00.000Z"
OLD_ISO = "2026-08-31T04:53:20.000Z"  # NOW - 400s（超出 300 秒新鲜期）

# §27 形状标准化 snapshot（provider fake 产出；五小时窗余量 50% →
# scheduler.evaluate → AVAILABLE）
GOOD_SNAPSHOT = {
    "provider": "fake", "plan_generation": "v3", "plan_level": None,
    "fetched_at": "2026-08-31T04:59:59Z", "status": None,
    "windows": [{"kind": "five_hour", "used_percent": 50.0,
                 "remaining_percent": 50.0,
                 "reset_at": "2026-09-01T00:00:00Z"}],
}
# 已耗尽 snapshot（used 100 → EXHAUSTED）
EXHAUSTED_SNAPSHOT = {
    "provider": "fake", "plan_generation": "v3", "plan_level": None,
    "fetched_at": "2026-08-31T04:59:59Z", "status": None,
    "windows": [{"kind": "five_hour", "used_percent": 100.0,
                 "remaining_percent": 0.0,
                 "reset_at": "2026-09-01T00:00:00Z"}],
}


class FakeProvider(object):
    """fake quota provider：behavior 为 snapshot dict 或异常实例；
    fetch 调用记入 calls（断言「零网络 / 不重试」用）。"""

    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = []

    def fetch(self, force=False):
        self.calls.append(force)
        if isinstance(self.behavior, Exception):
            raise self.behavior
        return dict(self.behavior)


def fake_factory(providers):
    """构造 _build_providers 替身：固定返回 [(name, FakeProvider)]，
    并记录 (api_key, timeout_seconds) 入参（透传断言用）。"""
    recorded = []

    def _factory(api_key, timeout_seconds=None):
        recorded.append((api_key, timeout_seconds))
        return list(providers)

    _factory.recorded = recorded
    return _factory


class ResolverTestBase(unittest.TestCase):
    """scratch 目录夹具 + 统一的凭证 / provider 注入助手。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name

    # —— 缓存文件助手（真实路径 = resolver._cache_path，不自造路径） ——

    def write_cache(self, status, fetched_at=OLD_ISO, snapshot=None):
        # v2.2.1 WU-221-B1: planted cache now carries the identity hash (same-identity fixture; legacy-reuse assertions superseded by the binding ruling)
        payload = {"provider": "fake", "fetched_at": fetched_at,
                   "snapshot": (GOOD_SNAPSHOT if snapshot is None
                                else snapshot),
                   "status": status,
                   "provider_identity_hash":
                       resolver._provider_identity_hash("test-key-value")}
        os.makedirs(os.path.dirname(resolver._cache_path(self.root)),
                    exist_ok=True)
        with open(resolver._cache_path(self.root), "w",
                  encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2,
                      sort_keys=True)
        return payload

    def read_cache(self):
        with open(resolver._cache_path(self.root), "r",
                  encoding="utf-8") as handle:
            return json.load(handle)

    def cache_exists(self):
        return os.path.exists(resolver._cache_path(self.root))

    # —— 注入助手 ——

    def patch_credential(self, key="test-key-value"):
        """注入凭证解析：(None, None) 模拟无凭证，否则 ("env", key)。"""
        if key is None:
            return mock.patch.object(resolver, "resolve_credential",
                                     return_value=(None, None))
        return mock.patch.object(resolver, "resolve_credential",
                                 return_value=("env", key))

    def resolve(self, **kwargs):
        kwargs.setdefault("now", NOW)
        return resolver.resolve_quota_status(self.root, **kwargs)


# —— 1/2/7. 缓存层级与 provider 抓取 ——

class CacheHierarchyTest(ResolverTestBase):

    def test_fresh_cache_hit_skips_provider(self):
        # 1. fresh cache 命中：cache_fresh + 缓存值；provider 工厂零调用
        self.write_cache("PRESSURE", fetched_at=NOW_ISO)
        factory = fake_factory([("fake", FakeProvider(GOOD_SNAPSHOT))])
        with self.patch_credential(), \
                mock.patch.object(resolver, "_build_providers", factory):
            result = self.resolve()
        self.assertEqual(result["status"], "PRESSURE")
        self.assertEqual(result["source"], "cache_fresh")
        self.assertEqual(result["evaluated_at"], NOW_ISO)
        self.assertEqual(set(result), {"status", "source", "evaluated_at",
                                       "reason"})  # 键冻结
        self.assertEqual(factory.recorded, [])  # 零网络：工厂未被调用

    def test_expired_cache_provider_success_refreshes_cache(self):
        # 2. 缓存过期 + provider 成功：provider 结果 + 缓存刷新 + 超时透传
        self.write_cache("PRESSURE", fetched_at=OLD_ISO)
        provider = FakeProvider(GOOD_SNAPSHOT)
        factory = fake_factory([("fake", provider)])
        with self.patch_credential(), \
                mock.patch.object(resolver, "_build_providers", factory):
            result = self.resolve()
        self.assertEqual(result["status"], "AVAILABLE")  # evaluate 的产出
        self.assertEqual(result["source"], "provider")
        self.assertEqual(provider.calls, [True])  # 一轮探测（force 口径）
        self.assertEqual(factory.recorded, [("test-key-value", None)])
        cache = self.read_cache()
        # v2.2.1 WU-221-B1: planted cache now carries the identity hash (same-identity fixture; legacy-reuse assertions superseded by the binding ruling)
        self.assertEqual(set(cache), {"provider", "fetched_at", "snapshot",
                                      "status",
                                      "provider_identity_hash"})  # 缓存形状冻结
        self.assertEqual(cache["status"], "AVAILABLE")
        self.assertEqual(cache["fetched_at"], NOW_ISO)  # 刷新为本次时刻
        self.assertEqual(cache["snapshot"], GOOD_SNAPSHOT)
        self.assertEqual(cache["provider"], "fake")

    def test_timeout_seconds_passthrough(self):
        # 2 补充：timeout_seconds 透传 provider 工厂（None → 工厂默认）
        self.write_cache("PRESSURE", fetched_at=OLD_ISO)
        factory = fake_factory([("fake", FakeProvider(GOOD_SNAPSHOT))])
        with self.patch_credential(), \
                mock.patch.object(resolver, "_build_providers", factory):
            self.resolve(timeout_seconds=2.5)
        self.assertEqual(factory.recorded, [("test-key-value", 2.5)])

    def test_force_refresh_bypasses_fresh_cache(self):
        # 7. force_refresh=True：fresh 缓存也走 provider 并刷新缓存
        self.write_cache("PRESSURE", fetched_at=NOW_ISO)  # 本来是新鲜的
        provider = FakeProvider(GOOD_SNAPSHOT)
        factory = fake_factory([("fake", provider)])
        with self.patch_credential(), \
                mock.patch.object(resolver, "_build_providers", factory):
            result = self.resolve(force_refresh=True)
        self.assertEqual(result["source"], "provider")
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual(provider.calls, [True])
        self.assertEqual(self.read_cache()["status"], "AVAILABLE")

    def test_result_status_vocabulary_always_valid(self):
        # 层级输出 status 恒在四态词汇内（fail-open 也只能产出 UNKNOWN）
        with self.patch_credential(key=None):
            result = self.resolve()
        self.assertIn(result["status"],
                      ("AVAILABLE", "PRESSURE", "EXHAUSTED", "UNKNOWN"))
        self.assertIn(result["source"], resolver.QUOTA_SOURCES)


# —— 3/4/5/6. 降级链：stale cache → UNKNOWN ——

class FallbackChainTest(ResolverTestBase):

    def test_expired_cache_provider_failure_falls_back_to_stale(self):
        # 3. 缓存过期 + provider 异常：cache_stale + 缓存值 + reason 含过期
        original = self.write_cache("PRESSURE", fetched_at=OLD_ISO)
        factory = fake_factory([("fake", FakeProvider(
            QuotaProviderError("boom", "network")))])
        with self.patch_credential(), \
                mock.patch.object(resolver, "_build_providers", factory):
            result = self.resolve()
        self.assertEqual(result["status"], "PRESSURE")  # 缓存值
        self.assertEqual(result["source"], "cache_stale")
        self.assertIn("过期", result["reason"])
        self.assertIn(OLD_ISO, result["reason"])  # 注明数据过期时间
        # 异常细节不透传：只有 kind 分类，无异常文本
        self.assertNotIn("boom", result["reason"])
        self.assertIn("network", result["reason"])
        # 缓存文件不被覆写（失败路径零写入）
        self.assertEqual(self.read_cache(), original)

    def test_no_cache_no_credential_yields_unknown(self):
        # 4. 无缓存 + 无凭证：none/UNKNOWN（fail-open，不虚构）
        with self.patch_credential(key=None):
            result = self.resolve()
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["source"], "none")
        self.assertEqual(result["evaluated_at"], NOW_ISO)
        self.assertEqual(self.cache_exists(), False)  # 无凭证不写缓存

    def test_no_cache_provider_failure_yields_unknown_without_retry(self):
        # 5. 无缓存 + provider 异常：none/UNKNOWN；两个 provider 各试
        #    一轮（zai → bigmodel 装配序）——绝不重试网络
        bad_zai = FakeProvider(QuotaProviderError("x", "unavailable"))
        bad_bigmodel = FakeProvider(QuotaProviderError("y", "auth"))
        factory = fake_factory([("zai", bad_zai), ("bigmodel", bad_bigmodel)])
        with self.patch_credential(), \
                mock.patch.object(resolver, "_build_providers", factory):
            result = self.resolve()
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["source"], "none")
        self.assertEqual(bad_zai.calls, [True])
        self.assertEqual(bad_bigmodel.calls, [True])  # 各一轮，无二次尝试

    def test_corrupt_cache_treated_as_missing(self):
        # 6. 缓存损坏 JSON → 视为无缓存（无凭证 → none/UNKNOWN）
        os.makedirs(os.path.dirname(resolver._cache_path(self.root)),
                    exist_ok=True)
        with open(resolver._cache_path(self.root), "w",
                  encoding="utf-8", newline="\n") as handle:
            handle.write("{broken json")
        with self.patch_credential(key=None):
            result = self.resolve()
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["source"], "none")

    def test_provider_success_with_no_cache_writes_fresh_cache(self):
        # 层级 2 的无缓存入口：抓取成功即建缓存（下次命中 fresh）
        provider = FakeProvider(EXHAUSTED_SNAPSHOT)
        factory = fake_factory([("fake", provider)])
        with self.patch_credential(), \
                mock.patch.object(resolver, "_build_providers", factory):
            result = self.resolve()
        self.assertEqual(result["status"], "EXHAUSTED")
        self.assertEqual(result["source"], "provider")
        self.assertEqual(self.read_cache()["status"], "EXHAUSTED")


# —— 8. credential safety ——

class CredentialSafetyTest(ResolverTestBase):

    def test_result_and_cache_file_never_contain_credentials(self):
        # 8. 返回 dict 与缓存文件序列化后不含凭证值与 key/token 字样
        self.write_cache("AVAILABLE", fetched_at=NOW_ISO)
        factory = fake_factory([("fake", FakeProvider(GOOD_SNAPSHOT))])
        with self.patch_credential(key="SECRET-CREDENTIAL-MATERIAL"), \
                mock.patch.object(resolver, "_build_providers", factory):
            result = self.resolve(force_refresh=True)
        result_blob = json.dumps(result, ensure_ascii=False)
        cache_blob = json.dumps(self.read_cache(), ensure_ascii=False)
        for blob in (result_blob, cache_blob):
            self.assertNotIn("SECRET-CREDENTIAL-MATERIAL", blob)
            self.assertNotIn("test-key-value", blob)
            self.assertNotIn("key", blob.lower())
            self.assertNotIn("token", blob.lower())
        # provider 工厂收到了凭证（内部只用于 Authorization 头），
        # 但凭证绝不进入返回值 / 缓存
        self.assertEqual(factory.recorded[0][0], "SECRET-CREDENTIAL-MATERIAL")

    def test_failure_reasons_carry_only_kind_or_type_name(self):
        # 契约外异常只取类型名，绝不透传异常文本（可能携带外部输入）
        class WeirdError(Exception):
            pass

        self.write_cache("AVAILABLE", fetched_at=OLD_ISO)
        provider = FakeProvider(None)

        def explode(force=False):
            raise WeirdError("secret detail /path?q=1")

        provider.fetch = explode
        factory = fake_factory([("fake", provider)])
        with self.patch_credential(), \
                mock.patch.object(resolver, "_build_providers", factory):
            result = self.resolve()
        self.assertEqual(result["source"], "cache_stale")
        self.assertIn("WeirdError", result["reason"])
        self.assertNotIn("secret detail", result["reason"])


# —— 9/10/11. task_manager 接线集成 ——

TID = "resolver-task-1a2b3c"
VERIFY_CMD = "python3 -m unittest tests.test_quota_resolver"
ROUTE = {"mode": "delegate", "delegability": "high",
         "assurance": "standard", "executor": "flash-implementer",
         "continuity": "foreground"}


def wu(uid, owned, deps=()):
    """构造 §61 形状的 ready 单元（真实 new_work_unit）。"""
    return work_unit.new_work_unit(
        uid, "resolver 目标 %s" % uid, executor="flash-implementer",
        ownership=list(owned), verification=[VERIFY_CMD],
        depends_on=list(deps), status="ready")


def make_task(root, units, *, max_workers=2):
    """落盘一个 scratch 任务 state（真实 new_task_state + save）。"""
    st = state.new_task_state(
        TID, "resolver 集成测试目标", dict(ROUTE),
        ownership_files=("src/**",),
        verification_required=(VERIFY_CMD,), status="executing")
    st["work_units"] = list(units)
    st["dispatch"] = {"max_workers": max_workers, "active": []}
    state.save_state(root, st)
    return st


def events(root, name):
    return [e for e in journal.read_events(root, TID)
            if e.get("event") == name]


class TaskManagerWiringTest(ResolverTestBase):

    def _resolved(self, status, source="provider"):
        return {"status": status, "source": source,
                "evaluated_at": NOW_ISO, "reason": "测试注入"}

    def test_prepare_dispatch_resolves_and_journals_quota_resolved(self):
        # 9. resolver EXHAUSTED → waiting_quota 口径拒绝 + quota_resolved
        make_task(self.root, [wu("u1", ("src/a/**",))])
        with mock.patch.object(resolver, "resolve_quota_status",
                               return_value=self._resolved("EXHAUSTED")):
            with self.assertRaises(task_manager.TaskManagerError) as ctx:
                task_manager.prepare_dispatch(self.root, TID, "u1")
        self.assertIn("quota EXHAUSTED", str(ctx.exception))
        # 解析事实入账（三键冻结），拒绝本身零其他事件
        resolved_events = events(self.root, "quota_resolved")
        self.assertEqual(len(resolved_events), 1)
        self.assertEqual(resolved_events[0]["status"], "EXHAUSTED")
        self.assertEqual(resolved_events[0]["source"], "provider")
        self.assertEqual(resolved_events[0]["evaluated_at"], NOW_ISO)
        # 决策未批准：零租约、零 permit、零派发事件
        self.assertEqual(lease.lease_state(self.root, TID), {})
        self.assertEqual(events(self.root, "dispatch_prepared"), [])
        self.assertEqual(
            [e["event"] for e in journal.read_events(self.root, TID)],
            ["quota_resolved"])

    def test_explicit_quota_status_skips_resolution(self):
        # 10. 显式 quota_status="AVAILABLE" → 零解析、零事件（直通）
        make_task(self.root, [wu("u1", ("src/a/**",))])
        with mock.patch.object(resolver, "resolve_quota_status") as fake:
            plan = task_manager.prepare_dispatch(self.root, TID, "u1",
                                                 quota_status="AVAILABLE")
        fake.assert_not_called()  # 零解析
        self.assertEqual(events(self.root, "quota_resolved"), [])  # 零事件
        self.assertEqual(plan["quota_status"], "AVAILABLE")
        self.assertEqual(plan["dispatch"], ["u1"])
        # 事件序不受影响（wu-21-10 之前的行为逐字保持）
        self.assertEqual(
            [e["event"] for e in journal.read_events(self.root, TID)],
            ["dispatch_prepared", "dispatch_permit_created"])

    def test_default_none_triggers_resolution_and_enters_plan(self):
        # 9 补充：None 缺省触发解析，解析值进入决策链（UNKNOWN → 预算 1
        # 不挂起，wu-21-09 语义）
        make_task(self.root, [wu("u1", ("src/a/**",))])
        with mock.patch.object(resolver, "resolve_quota_status",
                               return_value=self._resolved(
                                   "UNKNOWN", source="none")) as fake:
            plan = task_manager.prepare_dispatch(self.root, TID, "u1")
        fake.assert_called_once_with(self.root)
        self.assertEqual(plan["quota_status"], "UNKNOWN")
        self.assertEqual(plan["max_workers"], 1)  # §12：UNKNOWN → 预算 1
        self.assertEqual(plan["dispatch"], ["u1"])
        prepared = events(self.root, "dispatch_prepared")[0]
        self.assertEqual(prepared["effective_max_workers"], 1)

    def test_wave_resolves_unknown_to_budget_one(self):
        # 11. wave + resolver UNKNOWN → worker_budget=1、单单元 wave、
        #     wave 记录 quota_status 记解析后实际值
        make_task(self.root, [wu("u1", ("src/a/**",)),
                              wu("u2", ("src/b/**",))])
        with mock.patch.object(resolver, "resolve_quota_status",
                               return_value=self._resolved(
                                   "UNKNOWN", source="cache_stale")):
            result = task_manager.prepare_dispatch_wave(self.root, TID)
        self.assertEqual(result["units"], ["u1"])  # 预算 1 只批 1 个
        self.assertEqual(result["worker_budget"], 1)
        self.assertEqual(result["deferred"],
                         [{"id": "u2", "reason": "concurrency"}])
        self.assertEqual(result["waiting_quota"], [])
        st = state.load_state(self.root, TID)
        wave = st["dispatch"]["waves"][0]
        self.assertEqual(wave["quota_status"], "UNKNOWN")  # 解析后实际值
        self.assertEqual(wave["worker_budget"], 1)
        prepared = events(self.root, "dispatch_wave_prepared")[0]
        self.assertEqual(prepared["worker_budget"], 1)
        resolved_events = events(self.root, "quota_resolved")
        self.assertEqual(len(resolved_events), 1)
        self.assertEqual(resolved_events[0]["source"], "cache_stale")
        # 只有被批成员持有租约
        self.assertEqual(set(lease.lease_state(self.root, TID)),
                         {"src/a/**"})

    def test_wave_explicit_status_passthrough_zero_events(self):
        # 10 补充（wave 面）：显式声明优先——零解析零事件，记录显式值
        make_task(self.root, [wu("u1", ("src/a/**",))])
        with mock.patch.object(resolver, "resolve_quota_status") as fake:
            result = task_manager.prepare_dispatch_wave(
                self.root, TID, quota_status="PRESSURE")
        fake.assert_not_called()
        self.assertEqual(events(self.root, "quota_resolved"), [])
        self.assertEqual(
            state.load_state(self.root, TID)["dispatch"]["waves"][0]
            ["quota_status"], "PRESSURE")
        self.assertEqual(result["units"], ["u1"])


if __name__ == "__main__":
    unittest.main()
