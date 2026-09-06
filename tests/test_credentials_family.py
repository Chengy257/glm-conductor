#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.quota.credentials discover_families 单元测试（v2.2 M9
wu-22-10，Credential Provider Discovery）。

覆盖：
  - PROVIDER_FAMILIES 白名单与顺序：与 runtime.quota.resolver 的
    _PROVIDER_ORDER 探测装配逐字一致（zai 在前 bigmodel 在后——
    字面量同步锚定，防 credentials ↔ resolver 漂移）；
  - discover_families 返回形状：list 顺序恒白名单序，逐项键冻结
    恰三键 {"family", "available", "mode"}；
  - 可用性三态：env 凭据 → available=True / mode="env"；
    zcode-provider 配置凭据 → mode="zcode-provider"；两路皆无 →
    available=False / mode="unavailable"（词汇对齐 describe_modes）；
  - 缺省 config_path：expanduser(~/.zcode/v2/config.json)（HOME/
    USERPROFILE 注入 tempdir，不触真实用户目录）；
  - 零秘密泄露（§37）：返回值 JSON 序列化后绝不含 key 材料；逐项
    只有家族名 / 布尔 / 模式描述；
  - 零网络零副作用：绝不构造 provider（monkeypatch
    resolver._build_providers 断言未调用）、绝不 print、配置文件
    字节不变、目录零新增文件；
  - v2.2.1 WU-221-B1（缓存绑定）跨账号矩阵：resolver 缓存绑定
    provider 身份（provider_identity_hash）——异身份缓存绝不复用
    （fresh 不放行、stale 不回退）、legacy 无指纹缓存保守等同异身
    份、fetch 失败直落 UNKNOWN 绝不透出异身份快照、同身份快路径
    零变化、新缓存条目携带与共享 identity 模块同口径的指纹。

【零网络 / 零真实凭证纪律】全部测试只读写 tempdir，不发任何网络
请求，不读取真实 ~/.zcode 配置（environ 一律注入隔离）。

运行：
    cd <repo_root> && python3 -m unittest tests.test_credentials_family -v
"""

import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))

from runtime.quota import resolver
from runtime.quota.credentials import (CREDENTIAL_SOURCES,
                                       ENV_VAR, FAMILY_MODE_UNAVAILABLE,
                                       PROVIDER_FAMILIES, describe_modes,
                                       discover_families)
from runtime.quota.identity import compute_provider_identity_hash
from runtime.quota.provider import QuotaProviderError

# 假 key（含 SECRET 标记：零秘密断言复用同一标记）
FAKE_KEY = "sk-test-SECRET"


def standard_payload(key=FAKE_KEY):
    """§36 实测的 ZCode provider 配置标准结构。"""
    return {"provider": {
        "builtin:bigmodel-coding-plan": {"options": {"apiKey": key}}}}


class TempDirFixture(unittest.TestCase):
    """tempdir 基座：配置文件写入 + 目录快照。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def write_config(self, payload_or_text):
        """在 tempdir 写入 .zcode/v2/config.json，返回其路径字符串。"""
        config_path = self.root / ".zcode" / "v2" / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload_or_text, str):
            config_path.write_text(payload_or_text, encoding="utf-8")
        else:
            config_path.write_text(json.dumps(payload_or_text),
                                   encoding="utf-8")
        return str(config_path)

    def snapshot_files(self):
        """tempdir 内全部文件的相对路径集合（新增文件检测用）。"""
        return {str(path.relative_to(self.root))
                for path in self.root.rglob("*") if path.is_file()}


class WhitelistSyncTest(unittest.TestCase):
    """白名单词汇与顺序：与 resolver 探测装配同步（防漂移锚定）。"""

    def test_families_match_resolver_provider_order(self):
        self.assertEqual(
            PROVIDER_FAMILIES,
            tuple(name for name, _cls in resolver._PROVIDER_ORDER))

    def test_frozen_order_zai_first(self):
        self.assertEqual(PROVIDER_FAMILIES, ("zai", "bigmodel"))

    def test_unavailable_mode_in_describe_modes_vocabulary(self):
        # FAMILY_MODE_UNAVAILABLE 与 describe_modes 的 unavailable 键同词汇
        self.assertIn(FAMILY_MODE_UNAVAILABLE, describe_modes())


class DiscoverFamiliesShapeTest(TempDirFixture):
    """返回形状：list 白名单序 + 逐项键冻结恰三键。"""

    def test_shape_and_order_with_env_credential(self):
        entries = discover_families("/nonexistent/cfg.json",
                                    environ={"GLM_CONDUCTOR_QUOTA_API_KEY":
                                             FAKE_KEY})
        self.assertIsInstance(entries, list)
        self.assertEqual([entry["family"] for entry in entries],
                         ["zai", "bigmodel"])
        for entry in entries:
            self.assertEqual(sorted(entry.keys()),
                             ["available", "family", "mode"])
            self.assertIs(entry["available"], True)
            self.assertEqual(entry["mode"], "env")

    def test_env_empty_string_counts_unavailable(self):
        # 空串按未设置处理（与 resolve_credential 同口径）
        entries = discover_families("/nonexistent/cfg.json",
                                    environ={"GLM_CONDUCTOR_QUOTA_API_KEY":
                                             ""})
        for entry in entries:
            self.assertIs(entry["available"], False)
            self.assertEqual(entry["mode"], FAMILY_MODE_UNAVAILABLE)

    def test_missing_config_unavailable(self):
        entries = discover_families(str(self.root / "missing" / "cfg.json"),
                                    environ={})
        self.assertEqual(entries, [
            {"family": "zai", "available": False,
             "mode": FAMILY_MODE_UNAVAILABLE},
            {"family": "bigmodel", "available": False,
             "mode": FAMILY_MODE_UNAVAILABLE}])


class CredentialSourceModesTest(TempDirFixture):
    """三态模式：env / zcode-provider / unavailable。"""

    def test_zcode_provider_config_mode(self):
        config_path = self.write_config(standard_payload())
        entries = discover_families(config_path, environ={})
        for entry in entries:
            self.assertIs(entry["available"], True)
            self.assertEqual(entry["mode"], "zcode-provider")
        self.assertEqual([entry["family"] for entry in entries],
                         list(PROVIDER_FAMILIES))

    def test_env_wins_over_config_for_mode(self):
        # env 优先策略贯通：env 命中即不读配置 → mode 报 "env"
        config_path = self.write_config(standard_payload())
        entries = discover_families(config_path,
                                    environ={"GLM_CONDUCTOR_QUOTA_API_KEY":
                                             "env-key"})
        for entry in entries:
            self.assertEqual(entry["mode"], "env")

    def test_corrupt_config_degrades_silently(self):
        config_path = self.write_config("{not-json")
        entries = discover_families(config_path, environ={})
        for entry in entries:
            self.assertIs(entry["available"], False)
            self.assertEqual(entry["mode"], FAMILY_MODE_UNAVAILABLE)


class DefaultPathTest(TempDirFixture):
    """缺省 config_path：expanduser(~/.zcode/v2/config.json)。"""

    def _patch_home(self):
        patcher = unittest.mock.patch.dict(
            os.environ, {"HOME": str(self.root),
                         "USERPROFILE": str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_default_path_hit(self):
        self._patch_home()
        (self.root / ".zcode" / "v2").mkdir(parents=True, exist_ok=True)
        (self.root / ".zcode" / "v2" / "config.json").write_text(
            json.dumps(standard_payload()), encoding="utf-8")
        entries = discover_families(environ={})
        for entry in entries:
            self.assertIs(entry["available"], True)
            self.assertEqual(entry["mode"], "zcode-provider")

    def test_default_path_miss(self):
        self._patch_home()
        entries = discover_families(environ={})
        for entry in entries:
            self.assertIs(entry["available"], False)


class ZeroSecretDisciplineTest(TempDirFixture):
    """凭证纪律（§37）：零秘密、零 print、零网络、零副作用。"""

    def test_no_key_material_in_result(self):
        config_path = self.write_config(standard_payload())
        results = [
            discover_families(config_path, environ={}),
            discover_families("/nonexistent/cfg.json",
                              environ={"GLM_CONDUCTOR_QUOTA_API_KEY":
                                       FAKE_KEY}),
            discover_families("/nonexistent/cfg.json", environ={}),
        ]
        for entries in results:
            serialized = json.dumps(entries)
            self.assertNotIn(FAKE_KEY, serialized)
            self.assertNotIn("apiKey", serialized)
            self.assertNotIn("sk-", serialized)
            for entry in entries:
                for value in entry.values():
                    # 值只能是家族名 / 布尔 / 模式描述词三类
                    self.assertIsInstance(value, (str, bool))

    def test_never_prints(self):
        config_path = self.write_config(standard_payload())
        with unittest.mock.patch("builtins.print") as mock_print:
            discover_families(config_path, environ={})
            discover_families(str(self.root / "missing.json"), environ={})
        mock_print.assert_not_called()

    def test_never_builds_providers_zero_network(self):
        # 发现是纯材料存在性判定：绝不走 resolver 的 provider 构造 /
        # 抓取路径（零网络的结构性证据，非约定式断言）
        with unittest.mock.patch.object(
                resolver, "_build_providers") as mock_build:
            discover_families("/nonexistent/cfg.json",
                              environ={"GLM_CONDUCTOR_QUOTA_API_KEY":
                                       FAKE_KEY})
            discover_families("/nonexistent/cfg.json", environ={})
        mock_build.assert_not_called()

    def test_config_file_untouched_and_no_new_files(self):
        config_path = self.write_config(standard_payload())
        before_bytes = Path(config_path).read_bytes()
        before_files = self.snapshot_files()
        discover_families(config_path, environ={})
        discover_families(str(self.root / "missing.json"), environ={})
        self.assertEqual(Path(config_path).read_bytes(), before_bytes)
        self.assertEqual(self.snapshot_files(), before_files)


# —— v2.2.1 WU-221-B1（缓存绑定）：resolver 缓存 ↔ provider 身份 ——

NOW_Q = datetime(2026, 8, 31, 5, 0, 0, tzinfo=timezone.utc)
NOW_Q_ISO = "2026-08-31T05:00:00.000Z"

# 两个模拟账号的假 key（互异材料 → 互异身份指纹）
KEY_ACCOUNT_A = "sk-acct-A-000000000000"
KEY_ACCOUNT_B = "sk-acct-B-999999999999"

# §27 形状标准化 snapshot（五小时窗余量 50% → scheduler.evaluate →
# AVAILABLE；与 tests/test_quota_resolver.py 的 GOOD_SNAPSHOT 同形状）
GOOD_SNAPSHOT = {
    "provider": "fake", "plan_generation": "v3", "plan_level": None,
    "fetched_at": "2026-08-31T04:59:59Z", "status": None,
    "windows": [{"kind": "five_hour", "used_percent": 50.0,
                 "remaining_percent": 50.0,
                 "reset_at": "2026-09-01T00:00:00Z"}],
}


class FakeFetchProvider(object):
    """fake quota provider（注入 resolver._build_providers 用）：
    behavior 为 snapshot dict 或异常实例；fetch 调用记入 calls
    （零网络 / 抓取被发起的断言用）。"""

    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = []

    def fetch(self, force=False):
        self.calls.append(force)
        if isinstance(self.behavior, Exception):
            raise self.behavior
        return dict(self.behavior)


def recording_factory(providers):
    """构造 resolver._build_providers 替身：恒返回给定 provider 列表，
    并记录 (api_key, timeout_seconds) 入参（零网络 / 凭证透传断言用；
    与 tests/test_quota_resolver.py 的 fake_factory 同款注入面）。"""
    recorded = []

    def _factory(api_key, timeout_seconds=None):
        recorded.append((api_key, timeout_seconds))
        return list(providers)

    _factory.recorded = recorded
    return _factory


class QuotaCacheIdentityBindingTest(TempDirFixture):
    """v2.2.1 WU-221-B1（缓存绑定）跨账号矩阵（零真实网络）。

    resolver 本地缓存只属于写入它的 provider 身份（缓存唯一新增键
    provider_identity_hash）：fresh / stale 两层复用一律要求缓存指纹
    == 当前身份指纹；legacy 无指纹缓存保守等同异身份；provider 抓取
    失败绝不回退异身份快照（直落 UNKNOWN fail-open）；同身份快路径
    逐字保持。身份切换经 environ 注入（GLM_CONDUCTOR_QUOTA_API_KEY
    配额键，凭证派生走真实 resolve_credential，HOME/USERPROFILE 钉在
    tempdir——配置回退永不触真实用户目录）；provider 经
    resolver._build_providers 工厂注入 fake（零网络，与
    tests/test_quota_resolver.py 同款注入面）。
    """

    def patch_identity(self, api_key):
        """environ 注入身份：配额 env 键 + HOME/USERPROFILE → tempdir。
        连续调用即账号切换（patch.dict 原地覆盖，退出逐层还原）。"""
        patcher = unittest.mock.patch.dict(
            os.environ,
            {ENV_VAR: api_key,
             "HOME": str(self.root), "USERPROFILE": str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def resolve(self, **kwargs):
        kwargs.setdefault("now", NOW_Q)
        return resolver.resolve_quota_status(self.root, **kwargs)

    def resolve_detail(self, **kwargs):
        kwargs.setdefault("now", NOW_Q)
        return resolver.resolve_quota_detail(self.root, **kwargs)

    def write_account_cache(self, stub_factory):
        """当前身份经 stub 抓取成功一次（真实写缓存路径）。"""
        with unittest.mock.patch.object(resolver, "_build_providers",
                                        stub_factory):
            return self.resolve()

    def test_identity_switch_does_not_reuse_account_a_cache(self):
        # A 账号抓取落缓存 → 切到 B → A 的缓存绝不当作 B 的新鲜快照
        # （fresh 层不放行，B 的 provider 抓取被真实发起）
        self.patch_identity(KEY_ACCOUNT_A)
        self.write_account_cache(recording_factory(
            [("fake", FakeFetchProvider(GOOD_SNAPSHOT))]))
        # 两账号材料 → 互异身份指纹（矩阵前提的机械证据）
        self.assertNotEqual(
            compute_provider_identity_hash(
                environ={ENV_VAR: KEY_ACCOUNT_A}),
            compute_provider_identity_hash(
                environ={ENV_VAR: KEY_ACCOUNT_B}))
        self.patch_identity(KEY_ACCOUNT_B)
        reader = recording_factory(
            [("fake", FakeFetchProvider(GOOD_SNAPSHOT))])
        result = self.write_account_cache(reader)
        # B 发起了抓取（同一份凭证解析喂身份指纹与 provider 构造）
        self.assertEqual(reader.recorded, [(KEY_ACCOUNT_B, None)])
        self.assertEqual(result["source"], "provider")  # 非 cache_fresh

    def test_legacy_cache_without_hash_not_fresh_nor_stale_fallback(self):
        # legacy 缓存（无 provider_identity_hash）：保守等同异身份——
        # 不当新鲜（fetch 被发起）、fetch 失败也不作权威 stale 回退
        # → UNKNOWN，绝不透出 legacy 快照
        resolver._save_cache(resolver._cache_path(self.root), {
            "provider": "fake", "fetched_at": NOW_Q_ISO,
            "snapshot": GOOD_SNAPSHOT, "status": "AVAILABLE"})  # 无指纹
        self.patch_identity(KEY_ACCOUNT_A)
        failing = recording_factory([("fake", FakeFetchProvider(
            QuotaProviderError("boom", "network")))])
        with unittest.mock.patch.object(resolver, "_build_providers",
                                        failing):
            detail = self.resolve_detail()
        self.assertEqual(failing.recorded,
                         [(KEY_ACCOUNT_A, None)])  # fetch 被发起：非新鲜
        self.assertEqual(detail["source"], "none")
        self.assertEqual(detail["status"], "UNKNOWN")
        self.assertIsNone(detail["snapshot"])  # 非 legacy 快照
        self.assertIsNone(detail["fetched_at"])

    def test_same_identity_keeps_fresh_cache_fast_path(self):
        # 同身份：新鲜缓存快路径逐字保持——零网络（工厂零调用）+
        # cache_fresh + 缓存值
        self.patch_identity(KEY_ACCOUNT_A)
        self.write_account_cache(recording_factory(
            [("fake", FakeFetchProvider(GOOD_SNAPSHOT))]))
        reader = recording_factory(
            [("fake", FakeFetchProvider(GOOD_SNAPSHOT))])
        with unittest.mock.patch.object(resolver, "_build_providers",
                                        reader):
            again = self.resolve()
        self.assertEqual(reader.recorded, [])  # 零网络：工厂未被调用
        self.assertEqual(again["source"], "cache_fresh")
        self.assertEqual(again["status"], "AVAILABLE")

    def test_identity_switch_fetch_failure_never_falls_back_foreign(self):
        # A 落缓存（新鲜期内 AVAILABLE）→ 切 B → B 抓取失败：绝不回退
        # A 的快照（foreign 缓存不作权威 stale 回退）→ UNKNOWN
        # fail-open
        self.patch_identity(KEY_ACCOUNT_A)
        self.write_account_cache(recording_factory(
            [("fake", FakeFetchProvider(GOOD_SNAPSHOT))]))
        self.patch_identity(KEY_ACCOUNT_B)
        failing = recording_factory([("fake", FakeFetchProvider(
            QuotaProviderError("boom", "network")))])
        with unittest.mock.patch.object(resolver, "_build_providers",
                                        failing):
            detail = self.resolve_detail()
        self.assertEqual(detail["source"], "none")
        self.assertEqual(detail["status"], "UNKNOWN")
        self.assertIsNone(detail["snapshot"])  # A 的快照绝不透出
        self.assertIsNone(detail["fetched_at"])

    def test_written_cache_carries_correct_provider_identity_hash(self):
        # 抓取成功写缓存：唯一新增键 provider_identity_hash 与共享
        # identity 模块对同一身份的产出逐字一致（两处派生口径的防漂
        # 移锚）；既有四键逐字保留；缓存与返回 dict 零凭证材料（§37）
        self.patch_identity(KEY_ACCOUNT_A)
        result = self.write_account_cache(recording_factory(
            [("fake", FakeFetchProvider(GOOD_SNAPSHOT))]))
        with open(resolver._cache_path(self.root), "r",
                  encoding="utf-8") as handle:
            cache = json.load(handle)
        self.assertEqual(
            cache["provider_identity_hash"],
            compute_provider_identity_hash(
                environ={ENV_VAR: KEY_ACCOUNT_A}))
        self.assertEqual(sorted(cache),
                         ["fetched_at", "provider",
                          "provider_identity_hash", "snapshot",
                          "status"])  # 既有四键 + 唯一新增键
        self.assertEqual(cache["status"], "AVAILABLE")
        self.assertEqual(cache["fetched_at"], NOW_Q_ISO)
        # 零秘密：缓存与返回 dict 序列化后绝不含两把假 key
        blob = json.dumps([cache, result], ensure_ascii=False)
        self.assertNotIn(KEY_ACCOUNT_A, blob)
        self.assertNotIn(KEY_ACCOUNT_B, blob)


if __name__ == "__main__":
    unittest.main()
