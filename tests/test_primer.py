#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.quota.primer 单元测试（v2.2 修正计划 C4，wu-22-C4，分支 B 全量）。

锚定对象：修正计划 §8.1（物化语义）/ §8.2（control-plane call 不属
Task Resume）/ §8.3（Feature-Gate + 三重授权，机械强制）/ §C4 红线
（HTTP 200 与百分比下降都不是证据，必须 quota refresh 二次确认）/
§QC-04..QC-06、QC-12 + Phase0 实验记录 P0-QP-00（端点实测形态）/
P0-QP-03（粒度告警）/ P0-QP-07（幂等设计门——随本文件取证关闭）。

运行：
    cd <repo_root> && python3 -m unittest tests.test_primer -v

覆盖映射（规格报告要求逐条对应）：
    授权闸（AuthorizePrimeGateTest + PolicyConfigTest，14+7 例）：
        三重条件逐条独立验证、缺块/坏块保守拒绝（fail-closed）、词汇
        冻结、execution_policy 落点（validator bool + 容错读缺省 False
        ——结构性关闭）、默认 D5 常量不被波及
    幂等（IdempotencyTest，8 例）：同键重入零网络零事件、记录镜像、
        异键（boundary/identity）隔离、坏账本恢复、失败尝试落账不重发
        （P0-QP-07）、命中跳过 quota refresh
    物化确认（MaterializationTest，9 例）：reset 变化才 materialized、
        200 同 epoch 不是证据、百分比下降不是证据（P0-QP-03）、provider
        翻转不推进 epoch（§10）、基线/确认面缺失不确认、weekly 仍阻塞
        executable=False（QC-05）、provider EXHAUSTED 不可执行、词汇外
        状态保守 False
    多窗（MaterializationTest 内 36-38）：5h+weekly 组合的 weekly 优先
        阻塞语义（复用 epoch.evaluate_epoch，不复制逻辑）
    注入（TransportInjectionTest + BaseUrlTest + StoreTest，20+ 例）：
        transport/fetch_refresh/clock 全注入零真实网络零真实模型调用、
        请求形态冻结（P0-QP-00 §1.2 逐字）、错误只取类型名/状态码
        （§37）、凭证缺失零传输、原子写无 tmp 残留、PermissionError
        有界重试耗尽抛 PrimerStoreError、baseURL allowlist 闸
    single-attempt 加固（SingleAttemptHardeningTest，5 例，RH-01）：
        超时绝不重发（transport 恰 1 次）、歧义超时 post-refresh 确认
        （epoch 推进 → materialized=True / 同 epoch → False）、失败
        记录同键重放幂等（零 transport 零刷新）、非歧义失败（5xx /
        401 / malformed）零重发零确认刷新
    机械授权闸（PrimeAuthorizedTest，RH-02 方案 A）：唯一 public 执行
        API prime_authorized 内嵌三重闸——primer_enabled=false /
        manual / notify / auto_once+source=default → 冻结九键结构化
        拒绝且零 transport 零 primer.json 零 journal 零窗口消费
        （PRIMER-AUTH-01..04）；until_done+user+enabled → 恰一次授权
        尝试（PRIMER-AUTH-05）；任务目录不存在 / state 坏 JSON / 缺
        execution_policy 块 → 保守拒绝零 transport，且私有名
        _prime_once_unchecked 直调仍可执行（绕过必须显式用私有名，
        PRIMER-AUTH-06）；task_id 校验与「未授权零副作用优先」的
        校验顺序
    fail-closed（授权闸各 refuse 例 + 48/49 的 malformed 保守归类 +
        38 的词汇外状态）：授权方向绝不 fail-open
    冻结面（SignatureFreezeTest + 冻结键断言）：_prime_once_unchecked
        冻结签名（RH-02 起私有名）+ prime_authorized 冻结签名、
        返回 8 键、durable 记录 7 键、authorize_prime 3 键

全部离线：scratch 仓库用 tempfile.TemporaryDirectory（绝不触碰仓库内
.glm-conductor/ 真实账本）；凭证 monkeypatch 注入（隔离真实
~/.zcode/v2/config.json 与环境变量）；零真实等待（PermissionError 重试
间隔注入 0 / sleep 注入计数桩）。
"""

import inspect
import json
import os
import socket
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import execution_policy, journal
from runtime.quota import primer
from runtime.quota.provider import ALLOWED_HOSTS

# —— 常量与装置 ——

IDENTITY = "a1b2c3d4e5f60718"
BOUNDARY = "glm:163e156bcbec8e7b"          # 旧 boundary / epoch_id（幂等键之二）
CLOCK_MOMENT = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
CLOCK_ISO = "2026-09-03T12:00:00Z"

RESET_FIVE_OLD = "2026-09-02T21:59:00Z"    # prime 前旧 reset
RESET_FIVE_NEW = "2026-09-03T03:19:22Z"    # 物化后的新 reset（真实漂移样本）
RESET_WEEKLY = "2026-09-07T21:59:00Z"
RESET_WEEKLY_ROLLED = "2026-09-14T21:59:00Z"

DEFAULT_BASE = "https://open.bigmodel.cn/api/anthropic"
MESSAGES_URL = DEFAULT_BASE + "/v1/messages"

# _prime_once_unchecked 冻结签名（wu-22-C4 规格 INTERFACES 原文；
# RH-02 起为私有名，参数表与行为零变化）
PRIME_UNCHECKED_PARAMS = ["repo_root", "boundary_id",
                          "provider_identity_hash",
                          "transport", "clock", "fetch_refresh"]
RESULT_KEYS = ["authorized", "primed", "materialized", "executable",
               "tokens", "latency_ms", "idempotent", "error"]
RECORD_KEYS = ["primed_at", "materialized", "executable",
               "tokens_in", "tokens_out", "latency_ms", "error_kind"]
EVENT_KEYS = ["ts", "event", "boundary_id", "provider_identity_hash",
              "materialized", "executable", "tokens", "latency_ms",
              "idempotent"]


def win(kind, status, remaining, reset_at):
    """§27 形状单窗（used/remaining 百分比参与观察、不参与 epoch 身份）。"""
    used = round(100.0 - remaining, 1)
    return {"kind": kind, "status": status, "used_percent": used,
            "remaining_percent": remaining, "reset_at": reset_at}


def detail(status="AVAILABLE", windows=None):
    """resolve_quota_detail 形状的注入 detail。"""
    return {
        "source": "provider",
        "status": status,
        "snapshot": {"provider": "bigmodel",
                     "fetched_at": "2026-09-02T21:00:00.000Z",
                     "status": status,
                     "windows": windows if windows is not None else []},
        "fetched_at": "2026-09-02T21:00:00.000Z",
    }


def ok_payload():
    """P0-QP-00 实测响应的脱敏最小形（id/model/usage 字段子集）。"""
    return {"id": "msg_test", "model": "glm-5.3-flash",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 19, "output_tokens": 38}}


def recording_transport(script):
    """注入传输层：script 逐项消费（耗尽后重复末项）；返回值形态：
    ("ok", payload_dict) / ("status", code) / ("bytes", code, bytes) /
    ("timeout",) / ("raise", exc)。calls 记录每次请求的 url/headers/
    body/timeout（断言请求形态用）。"""
    calls = []

    def _transport(url, body, headers, timeout):
        index = len(calls)
        calls.append({"url": url, "headers": dict(headers), "body": body,
                      "timeout": timeout})
        item = script[index] if index < len(script) else script[-1]
        kind = item[0]
        if kind == "ok":
            return 200, json.dumps(item[1]).encode("utf-8")
        if kind == "status":
            return item[1], b""
        if kind == "bytes":
            return item[1], item[2]
        if kind == "timeout":
            raise socket.timeout("timed out")
        if kind == "raise":
            raise item[1]
        raise AssertionError("未知脚本项 %r" % (item,))

    _transport.calls = calls
    return _transport


def scripted_fetch(script):
    """注入 fetch_refresh：script 逐项消费（耗尽后重复末项）；Exception
    项抛出（模拟 quota 链路失败）。calls 记录调用次数。"""
    calls = []

    def _fetch(repo_root):
        index = len(calls)
        calls.append(repo_root)
        item = script[index] if index < len(script) else script[-1]
        if isinstance(item, Exception):
            raise item
        return item

    _fetch.calls = calls
    return _fetch


def fixed_clock():
    """注入时钟（仅决定 primed_at）。"""
    return CLOCK_MOMENT


def policy_view(auto_resume="until_done", source="user", primer_enabled=True,
                confirmed_at="2026-09-01T16:15:14+00:00"):
    """execution_policy 形状的 policy_view（authorize_prime 的输入）。"""
    quota_control = {}
    if primer_enabled is not None:
        quota_control["primer_enabled"] = primer_enabled
    return {
        "worker_execution": {"default_mode": "background"},
        "parallelism": {"mode": "standard", "default_workers": 2,
                        "max_workers": 2, "hard_limit": 4},
        "continuity": {"mode": "resumable", "auto_resume": auto_resume,
                       "max_quota_windows": 1},
        "authorization": {"source": source, "confirmed_at": confirmed_at,
                          "scope": "task"},
        "quota_control": quota_control,
    }


class PrimerCase(unittest.TestCase):
    """公共装置：scratch 仓库 + 隔离真实凭证与真实用户配置。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = tmp.name
        # 隔离：baseURL 读不到真实 ~/.zcode/v2/config.json；凭证不读
        # 真实环境（全部测试走 monkeypatch 注入——零真实网络零真实调用）
        patcher = mock.patch.object(
            primer, "_ZCODE_CONFIG_PATH",
            os.path.join(self.repo, "no-such-config.json"))
        patcher.start()
        self.addCleanup(patcher.stop)
        cred = mock.patch.object(
            primer, "resolve_credential",
            return_value=("env", "test-key-abc"))
        cred.start()
        self.addCleanup(cred.stop)

    # —— 断言助手 ——

    def assertResultShape(self, result, where=""):
        """冻结 8 键 + 类型闸（所有返回路径一致）。"""
        self.assertEqual(sorted(result.keys()), sorted(RESULT_KEYS),
                         "返回键漂移 %s" % where)
        self.assertIsInstance(result["authorized"], bool)
        self.assertIsInstance(result["primed"], bool)
        self.assertIsInstance(result["materialized"], bool)
        self.assertIsInstance(result["executable"], bool)
        self.assertIsInstance(result["idempotent"], bool)
        self.assertIsInstance(result["tokens"], dict)
        self.assertEqual(sorted(result["tokens"].keys()),
                         ["input", "output"])
        if result["primed"]:
            self.assertIsNone(result["error"])
        else:
            self.assertIsNotNone(result["error"])
            self.assertIn(result["error"]["kind"], primer.PRIMER_ERROR_KINDS)

    def read_record(self, identity=IDENTITY, boundary=BOUNDARY):
        state = primer.read_primer_state(self.repo)
        self.assertIsNotNone(state)
        return state["primes"][identity][boundary]

    def journal_events(self):
        # v2.2 C5a：window_primed 落控制面 journal（quota/events.jsonl），
        # 不再借用伪任务目录（C4 reviewer P2 正解）
        return journal.read_control_plane_events(self.repo)


# —— §8.3 三重授权闸 ——

class AuthorizePrimeGateTest(PrimerCase):
    """§8.3：三条全过才 authorized；任一不过 → 结构化拒绝。

    fail-closed 不对称：缺块/坏块/非 dict 一律保守拒绝（授权是模型
    调用，绝不 fail-open——观察面方向相反）。零副作用：连 repo_root
    都不入参，结构上不可触盘。
    """

    def test_all_conditions_pass(self):
        verdict = primer.authorize_prime(policy_view())
        self.assertTrue(verdict["authorized"])
        self.assertEqual(
            verdict["checks"], {"primer_enabled": True,
                                "auto_resume_eligible": True,
                                "user_authorized": True})

    def test_enabled_absent_refuses(self):
        # 默认块无 primer_enabled 键 → 结构性关闭（§8.3 默认恒 false）
        verdict = primer.authorize_prime(policy_view(primer_enabled=None))
        self.assertFalse(verdict["authorized"])
        self.assertFalse(verdict["checks"]["primer_enabled"])

    def test_enabled_false_refuses(self):
        verdict = primer.authorize_prime(policy_view(primer_enabled=False))
        self.assertFalse(verdict["authorized"])

    def test_enabled_non_bool_refuses(self):
        # 坏值绝不解释为开启（"true"/1 都是经典 fail-open 陷阱）
        for bad in ("true", 1, ["yes"], {"on": True}):
            verdict = primer.authorize_prime(
                policy_view(primer_enabled=bad))
            self.assertFalse(verdict["authorized"], "坏值 %r 被放行" % (bad,))

    def test_auto_resume_manual_refuses(self):
        verdict = primer.authorize_prime(
            policy_view(auto_resume="manual"))
        self.assertFalse(verdict["authorized"])
        self.assertFalse(verdict["checks"]["auto_resume_eligible"])

    def test_auto_resume_notify_refuses(self):
        # §8.3 原文：manual / notify 永不 prime
        verdict = primer.authorize_prime(
            policy_view(auto_resume="notify"))
        self.assertFalse(verdict["authorized"])

    def test_auto_resume_absent_refuses(self):
        view = policy_view()
        del view["continuity"]["auto_resume"]
        self.assertFalse(primer.authorize_prime(view)["authorized"])

    def test_auto_resume_invalid_word_refuses(self):
        # 词汇外值（如手写 state 的 "always"）→ 保守拒绝，不归一
        verdict = primer.authorize_prime(
            policy_view(auto_resume="always"))
        self.assertFalse(verdict["authorized"])

    def test_source_default_refuses(self):
        verdict = primer.authorize_prime(policy_view(source="default"))
        self.assertFalse(verdict["authorized"])
        self.assertFalse(verdict["checks"]["user_authorized"])

    def test_source_absent_refuses(self):
        view = policy_view()
        del view["authorization"]["source"]
        self.assertFalse(primer.authorize_prime(view)["authorized"])

    def test_policy_none_refuses(self):
        verdict = primer.authorize_prime(None)
        self.assertFalse(verdict["authorized"])
        self.assertFalse(any(verdict["checks"].values()))

    def test_policy_non_dict_refuses(self):
        for bad in ([], "policy", 42):
            self.assertFalse(
                primer.authorize_prime(bad)["authorized"],
                "非 dict policy %r 被放行" % (bad,))

    def test_frozen_keys_and_checks(self):
        verdict = primer.authorize_prime(policy_view())
        self.assertEqual(sorted(verdict.keys()),
                         ["authorized", "checks", "reason"])
        self.assertEqual(sorted(verdict["checks"].keys()),
                         ["auto_resume_eligible", "primer_enabled",
                          "user_authorized"])
        self.assertIsInstance(verdict["reason"], str)

    def test_reason_lists_all_failures(self):
        # 三条全挂 → reason 逐条点名（审计面，非 first-fail 短路）
        view = policy_view(auto_resume="manual", source="default",
                           primer_enabled=False)
        reason = primer.authorize_prime(view)["reason"]
        self.assertIn("primer_enabled", reason)
        self.assertIn("manual", reason)
        self.assertIn("default", reason)


class PolicyConfigTest(PrimerCase):
    """primer_enabled 的 execution_policy 落点：validator bool + 容错读
    缺省 False（§8.3 "primer.enabled" 语义；默认 D5 常量不被波及）。"""

    def test_accessor_default_false(self):
        self.assertFalse(execution_policy.primer_enabled(
            execution_policy.default_execution_policy()))
        self.assertFalse(execution_policy.primer_enabled(None))
        self.assertFalse(execution_policy.primer_enabled({}))

    def test_accessor_true_passthrough(self):
        policy = execution_policy.default_execution_policy()
        policy["quota_control"]["primer_enabled"] = True
        self.assertTrue(execution_policy.primer_enabled(policy))

    def test_accessor_invalid_false(self):
        for bad in ("true", 1, 1.0, [True]):
            policy = execution_policy.default_execution_policy()
            policy["quota_control"]["primer_enabled"] = bad
            self.assertFalse(execution_policy.primer_enabled(policy),
                             "坏值 %r 被解释为开启" % (bad,))

    def test_validator_rejects_non_bool(self):
        policy = execution_policy.default_execution_policy()
        policy["quota_control"]["primer_enabled"] = "yes"
        errors = [e for e in
                  execution_policy.validate_execution_policy(policy)
                  if "primer_enabled" in e]
        self.assertEqual(len(errors), 1)
        self.assertIn("quota_control.primer_enabled", errors[0])
        self.assertIn("bool", errors[0])

    def test_validator_accepts_bools(self):
        for value in (True, False):
            policy = execution_policy.default_execution_policy()
            policy["quota_control"]["primer_enabled"] = value
            self.assertEqual(
                execution_policy.validate_execution_policy(policy), [])

    def test_frozen_defaults_unchanged(self):
        # 本单元不波及 D5 冻结默认（35/20/60 三键逐字不动——既有冻结
        # 测试 test_execution_policy_quota_control 的契约）
        self.assertEqual(
            execution_policy.DEFAULT_QUOTA_CONTROL,
            {"pressure_percent": 35.0, "draining_percent": 20.0,
             "bridge_interval_minutes": 60})
        self.assertEqual(
            execution_policy.default_execution_policy()["quota_control"],
            {"pressure_percent": 35.0, "draining_percent": 20.0,
             "bridge_interval_minutes": 60})

    def test_default_policy_structurally_disabled(self):
        # 默认 policy → authorize_prime 拒绝（结构性关闭的机械落地）
        verdict = primer.authorize_prime(
            execution_policy.default_execution_policy())
        self.assertFalse(verdict["authorized"])
        self.assertFalse(execution_policy.primer_enabled(
            execution_policy.default_execution_policy()))


# —— 幂等（P0-QP-07：同旧 boundary 绝不连发） ——

class IdempotencyTest(PrimerCase):
    """幂等闸：(identity, boundary_id) 键命中 → 零网络零事件的既有结果。

    场景基线：prime 前五小时窗 EXHAUSTED（旧 reset）→ prime 后同窗
    AVAILABLE（新 reset）——物化确认通过的最小双快照。
    """

    PRE = detail("AVAILABLE", [win("five_hour", "EXHAUSTED", 0.0,
                                   RESET_FIVE_OLD)])
    POST = detail("AVAILABLE", [win("five_hour", "AVAILABLE", 100.0,
                                    RESET_FIVE_NEW)])

    def prime(self, transport=None, fetch=None):
        return primer._prime_once_unchecked(
            self.repo, boundary_id=BOUNDARY,
            provider_identity_hash=IDENTITY,
            transport=transport if transport is not None
            else recording_transport([("ok", ok_payload())]),
            clock=fixed_clock,
            fetch_refresh=fetch if fetch is not None
            else scripted_fetch([dict(self.PRE), dict(self.POST)]))

    def test_first_call_executes_and_records(self):
        transport = recording_transport([("ok", ok_payload())])
        result = self.prime(transport=transport)
        self.assertResultShape(result, "首跑")
        self.assertTrue(result["primed"])
        self.assertEqual(len(transport.calls), 1)
        record = self.read_record()
        self.assertEqual(sorted(record.keys()), sorted(RECORD_KEYS))
        self.assertEqual(record["primed_at"], CLOCK_ISO)
        self.assertIsNone(record["error_kind"])
        self.assertEqual(record["tokens_in"], 19)
        self.assertEqual(record["tokens_out"], 38)

    def test_hit_zero_network_zero_events(self):
        transport = recording_transport([("ok", ok_payload())])
        first = self.prime(transport=transport)
        events_after_first = len(self.journal_events())
        second = self.prime(transport=transport)
        # 零新调用、零新事件；结果与首跑一致（idempotent 旗标除外）
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(len(self.journal_events()), events_after_first)
        self.assertTrue(second["idempotent"])
        self.assertFalse(first["idempotent"])
        mirrored = dict(first)
        mirrored["idempotent"] = True
        self.assertEqual(second, mirrored)

    def test_hit_mirrors_record(self):
        self.prime()
        again = self.prime()
        self.assertTrue(again["materialized"])
        self.assertTrue(again["executable"])
        self.assertEqual(again["tokens"], {"input": 19, "output": 38})

    def test_different_boundary_executes_again(self):
        transport = recording_transport([("ok", ok_payload())])
        fetch = scripted_fetch([dict(self.PRE), dict(self.POST)])
        self.prime(transport=transport, fetch=fetch)
        primer._prime_once_unchecked(self.repo, boundary_id="glm:aaaaaaaaaaaaaaaa",
                          provider_identity_hash=IDENTITY,
                          transport=transport, clock=fixed_clock,
                          fetch_refresh=fetch)
        self.assertEqual(len(transport.calls), 2)

    def test_different_identity_executes_again(self):
        transport = recording_transport([("ok", ok_payload())])
        fetch = scripted_fetch([dict(self.PRE), dict(self.POST)])
        self.prime(transport=transport, fetch=fetch)
        primer._prime_once_unchecked(self.repo, boundary_id=BOUNDARY,
                          provider_identity_hash="ffffffffffffffff",
                          transport=transport, clock=fixed_clock,
                          fetch_refresh=fetch)
        self.assertEqual(len(transport.calls), 2)

    def test_corrupt_store_recovers(self):
        # 账本损坏 → 按「无记录」继续执行（代价不对称：重复 prime 害处
        # 小于恢复链卡死）；写回后文件恢复合法
        path = primer.primer_state_path(self.repo)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{corrupted")
        transport = recording_transport([("ok", ok_payload())])
        result = self.prime(transport=transport)
        self.assertTrue(result["primed"])
        with open(path, "r", encoding="utf-8") as handle:
            state = json.load(handle)
        self.assertIn(IDENTITY, state["primes"])

    def test_failed_attempt_durable_and_no_refire(self):
        # 超时（single-attempt 失败，RH-01）→ error_kind 落账；重入命中
        # 失败记录 → 零新调用返回既有失败（P0-QP-07：失败后也不连发，
        # 重试编排归 C5）
        transport = recording_transport([("timeout",)])
        fetch = scripted_fetch([dict(self.PRE)])
        first = self.prime(transport=transport, fetch=fetch)
        self.assertFalse(first["primed"])
        self.assertEqual(first["error"],
                         {"kind": "network", "detail": "socket.timeout"})
        self.assertEqual(self.read_record()["error_kind"], "network")
        second = self.prime(transport=transport, fetch=fetch)
        self.assertEqual(len(transport.calls), 1)  # 首跑仅 1 次尝试
        self.assertTrue(second["idempotent"])
        self.assertFalse(second["primed"])
        self.assertEqual(second["error"],
                         {"kind": "network", "detail": None})

    def test_hit_skips_fetch_refresh(self):
        fetch = scripted_fetch([dict(self.PRE), dict(self.POST)])
        self.prime(fetch=fetch)
        self.assertEqual(len(fetch.calls), 2)
        self.prime(fetch=fetch)
        self.assertEqual(len(fetch.calls), 2)  # 命中路径零 quota 网络


# —— 物化确认（§C4 红线：200 与百分比都不是证据） ——

class MaterializationTest(PrimerCase):
    """物化确认只看 prime 前后窗口身份变化；executable 复用
    epoch.evaluate_epoch（weekly 优先语义，QC-05）。"""

    def prime(self, fetch_script, transport_script=None):
        return primer._prime_once_unchecked(
            self.repo, boundary_id=BOUNDARY,
            provider_identity_hash=IDENTITY,
            transport=recording_transport(
                transport_script or [("ok", ok_payload())]),
            clock=fixed_clock,
            fetch_refresh=scripted_fetch(fetch_script))

    def test_reset_change_confirms_materialized(self):
        # QC-04：prime once → refresh → 新 epoch 确认
        result = self.prime([
            detail("AVAILABLE", [win("five_hour", "EXHAUSTED", 0.0,
                                     RESET_FIVE_OLD)]),
            detail("AVAILABLE", [win("five_hour", "AVAILABLE", 100.0,
                                     RESET_FIVE_NEW)]),
        ])
        self.assertTrue(result["primed"])
        self.assertTrue(result["materialized"])
        self.assertTrue(result["executable"])

    def test_http_200_same_epoch_not_materialized(self):
        # §C4 红线：HTTP 200 + epoch 未变 → materialized=False 且
        # executable=False（绝不因调用成功宣称 AVAILABLE）
        windows = [win("five_hour", "AVAILABLE", 64.0, RESET_FIVE_OLD)]
        result = self.prime([detail("AVAILABLE", windows),
                             detail("AVAILABLE", windows)])
        self.assertTrue(result["primed"])
        self.assertFalse(result["materialized"])
        self.assertFalse(result["executable"])

    def test_percent_drop_not_evidence(self):
        # P0-QP-03 粒度告警：百分比下降 + reset 不变 = 同一 epoch
        result = self.prime([
            detail("AVAILABLE", [win("five_hour", "AVAILABLE", 50.0,
                                     RESET_FIVE_OLD)]),
            detail("AVAILABLE", [win("five_hour", "AVAILABLE", 10.0,
                                     RESET_FIVE_OLD)]),
        ])
        self.assertFalse(result["materialized"])
        self.assertFalse(result["executable"])

    def test_provider_flip_not_epoch(self):
        # §10：provider 状态翻转（EXHAUSTED→AVAILABLE，reset 不变）不
        # 推进 epoch——不是物化
        result = self.prime([
            detail("AVAILABLE", [win("five_hour", "EXHAUSTED", 0.0,
                                     RESET_FIVE_OLD)]),
            detail("AVAILABLE", [win("five_hour", "AVAILABLE", 100.0,
                                     RESET_FIVE_OLD)]),
        ])
        self.assertFalse(result["materialized"])
        self.assertFalse(result["executable"])

    def test_missing_baseline_not_confirmed(self):
        # prime 前快照失败 → 无基线可比 → 不确认（宁可 False 不虚构）；
        # 模型调用本身成功 → primed=True
        result = self.prime([OSError("quota link down"),
                             detail("AVAILABLE",
                                    [win("five_hour", "AVAILABLE", 100.0,
                                         RESET_FIVE_NEW)])])
        self.assertTrue(result["primed"])
        self.assertFalse(result["materialized"])
        self.assertFalse(result["executable"])

    def test_missing_post_refresh_not_confirmed(self):
        result = self.prime([
            detail("AVAILABLE", [win("five_hour", "EXHAUSTED", 0.0,
                                     RESET_FIVE_OLD)]),
            OSError("quota link down"),
        ])
        self.assertTrue(result["primed"])
        self.assertFalse(result["materialized"])
        self.assertFalse(result["executable"])

    def test_weekly_still_blocked_executable_false(self):
        # QC-05：prime 成功 + 5h 窗已恢复，但 weekly 仍 EXHAUSTED →
        # materialized=True、executable=False（多窗任一阻塞即不可执行；
        # 无 ActivationReady 概念产生，归 C5）
        result = self.prime([
            detail("AVAILABLE", [win("five_hour", "EXHAUSTED", 0.0,
                                     RESET_FIVE_OLD),
                                 win("weekly", "EXHAUSTED", 0.0,
                                     RESET_WEEKLY)]),
            detail("AVAILABLE", [win("five_hour", "AVAILABLE", 100.0,
                                     RESET_FIVE_NEW),
                                 win("weekly", "EXHAUSTED", 0.0,
                                     RESET_WEEKLY_ROLLED)]),
        ])
        self.assertTrue(result["materialized"])
        self.assertFalse(result["executable"])

    def test_provider_exhausted_executable_false(self):
        # provider 维度 EXHAUSTED → epoch 复评不可执行（即使窗已滚动）
        result = self.prime([
            detail("EXHAUSTED", [win("five_hour", "EXHAUSTED", 0.0,
                                     RESET_FIVE_OLD)]),
            detail("EXHAUSTED", [win("five_hour", "EXHAUSTED", 0.0,
                                     RESET_FIVE_NEW)]),
        ])
        self.assertTrue(result["materialized"])
        self.assertFalse(result["executable"])

    def test_invalid_post_status_executable_false(self):
        # 注入面给出词汇外 provider 状态 → 保守不可执行（ValueError 兜底）
        result = self.prime([
            detail("AVAILABLE", [win("five_hour", "EXHAUSTED", 0.0,
                                     RESET_FIVE_OLD)]),
            detail("BOGUS", [win("five_hour", "AVAILABLE", 100.0,
                                 RESET_FIVE_NEW)]),
        ])
        self.assertTrue(result["materialized"])
        self.assertFalse(result["executable"])


# —— RH-01：single-attempt 加固（超时绝不重发；歧义超时 post-refresh 确认） ——

class SingleAttemptHardeningTest(PrimerCase):
    """RH-01（v2.2 Release Hardening §3）：一个幂等键下模型调用至多发
    一次。超时 = 「请求可能已到达 provider、结果未知」（ambiguous_
    timeout）——绝不自动重发（重发即是 P0-QP-07 要防的「同旧 boundary
    连发多个 prime」），改以 prime 后强制 refresh 的窗口身份推进做事后
    确认；非歧义失败（5xx / auth / malformed / 非超时 network）零确认面
    直接落账。PRIMER-RH-01..05 逐条对应实施规格 §3.4。
    """

    PRE = detail("AVAILABLE", [win("five_hour", "EXHAUSTED", 0.0,
                                   RESET_FIVE_OLD)])
    POST_NEW = detail("AVAILABLE", [win("five_hour", "AVAILABLE", 100.0,
                                        RESET_FIVE_NEW)])
    POST_SAME = detail("AVAILABLE", [win("five_hour", "EXHAUSTED", 0.0,
                                         RESET_FIVE_OLD)])

    def prime_timeout(self, fetch_script):
        """超时脚本 + 指定 fetch 脚本的一次 prime_once 全注入执行。"""
        transport = recording_transport([("timeout",)])
        fetch = scripted_fetch(fetch_script)
        result = primer._prime_once_unchecked(
            self.repo, boundary_id=BOUNDARY,
            provider_identity_hash=IDENTITY, transport=transport,
            clock=fixed_clock, fetch_refresh=fetch)
        return result, transport, fetch

    def test_rh01_timeout_single_transport_call(self):
        # PRIMER-RH-01：timeout → transport 恰调用 1 次（不再有第二次
        # 模型请求）
        result, transport, _fetch = self.prime_timeout(
            [dict(self.PRE), dict(self.POST_SAME)])
        self.assertEqual(len(transport.calls), 1)
        self.assertFalse(result["primed"])
        self.assertEqual(result["error"],
                         {"kind": "network", "detail": "socket.timeout"})
        self.assertIsInstance(result["latency_ms"], int)

    def test_rh02_timeout_epoch_advanced_confirms_materialized(self):
        # PRIMER-RH-02：timeout + post-refresh epoch 推进 →
        # materialized=True、primed=False（timeout + materialized=True 是
        # 合法组合）、零第二次模型调用；executable 照成功路径规则经
        # evaluate_epoch 复评
        result, transport, fetch = self.prime_timeout(
            [dict(self.PRE), dict(self.POST_NEW)])
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(len(fetch.calls), 2)  # 基线 + 歧义确认刷新
        self.assertFalse(result["primed"])
        self.assertTrue(result["materialized"])
        self.assertTrue(result["executable"])
        self.assertEqual(result["error"],
                         {"kind": "network", "detail": "socket.timeout"})
        record = self.read_record()
        self.assertEqual(record["error_kind"], "network")
        self.assertTrue(record["materialized"])
        self.assertTrue(record["executable"])
        events = self.journal_events()
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["materialized"])
        self.assertTrue(events[0]["executable"])
        self.assertFalse(events[0]["idempotent"])

    def test_rh03_timeout_same_epoch_not_materialized(self):
        # PRIMER-RH-03：timeout + post-refresh 同 epoch →
        # materialized=False、零第二次模型调用
        result, transport, fetch = self.prime_timeout(
            [dict(self.PRE), dict(self.POST_SAME)])
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(len(fetch.calls), 2)
        self.assertFalse(result["primed"])
        self.assertFalse(result["materialized"])
        self.assertFalse(result["executable"])
        self.assertEqual(self.read_record()["error_kind"], "network")

    def test_rh04_timeout_record_replay_idempotent(self):
        # PRIMER-RH-04：timeout 失败落账后同键重放 → idempotent=True、
        # 零 transport、零 quota refresh、返回记录的 materialized 值
        first, transport, fetch = self.prime_timeout(
            [dict(self.PRE), dict(self.POST_NEW)])
        self.assertTrue(first["materialized"])
        calls_after_first = len(transport.calls)
        fetches_after_first = len(fetch.calls)
        second = primer._prime_once_unchecked(
            self.repo, boundary_id=BOUNDARY,
            provider_identity_hash=IDENTITY, transport=transport,
            clock=fixed_clock, fetch_refresh=fetch)
        self.assertTrue(second["idempotent"])
        self.assertEqual(len(transport.calls), calls_after_first)
        self.assertEqual(len(fetch.calls), fetches_after_first)
        self.assertFalse(second["primed"])
        self.assertTrue(second["materialized"])  # 记录值原样镜像
        self.assertEqual(second["error"],
                         {"kind": "network", "detail": None})

    def test_rh05_non_ambiguous_failures_no_resend_no_post_refresh(self):
        # PRIMER-RH-05：HTTP 5xx / 401 auth / malformed → 零重发、零
        # post-refresh（fetch_refresh 恰 1 次 = pre 基线，无第 2 次）
        scripts = [
            ("http_5xx", [("status", 503)]),
            ("auth_401", [("raise", urllib.error.HTTPError(
                MESSAGES_URL, 401, "Unauthorized", None, None))]),
            ("malformed", [("bytes", 200, b"not json")]),
        ]
        for index, (name, script) in enumerate(scripts):
            with self.subTest(case=name):
                transport = recording_transport(script)
                fetch = scripted_fetch([dict(self.PRE)])
                result = primer._prime_once_unchecked(
                    self.repo, boundary_id="glm:rh05%08x" % (index,),
                    provider_identity_hash=IDENTITY, transport=transport,
                    clock=fixed_clock, fetch_refresh=fetch)
                self.assertEqual(len(transport.calls), 1)  # 零重发
                self.assertEqual(len(fetch.calls), 1)      # 零确认刷新
                self.assertFalse(result["primed"])
                self.assertFalse(result["materialized"])
                self.assertFalse(result["executable"])
                self.assertIsNotNone(result["error"])


# —— RH-02：机械授权闸收口至唯一 public 执行 API（方案 A） ——

def _authorized_policy(auto_resume="until_done", source="user",
                       primer_enabled=True):
    """真实 execution_policy 形状的 policy（default_execution_policy()
    为底，仅改三闸词汇——形状参照 runtime/execution_policy.py，它在
    quota 包依赖白名单内可安全 import）。"""
    policy = execution_policy.default_execution_policy()
    policy["quota_control"]["primer_enabled"] = primer_enabled
    policy["continuity"]["auto_resume"] = auto_resume
    policy["authorization"]["source"] = source
    return policy


def _write_task_state(repo, task_id, policy):
    """真实任务布局写入：<repo>/.glm-conductor/tasks/<task_id>/state.json
    （与 task_manager / journal.journal_path 的布局同源）。"""
    task_dir = os.path.join(repo, ".glm-conductor", "tasks", task_id)
    os.makedirs(task_dir, exist_ok=True)
    path = os.path.join(task_dir, "state.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"task_id": task_id, "status": "waiting_quota",
                   "execution_policy": policy}, handle)
    return path


class PrimeAuthorizedTest(PrimerCase):
    """RH-02（v2.2 Release Hardening §4，方案 A）：三重授权闸机械收口
    在唯一 public 执行 API prime_authorized 内——未授权（三闸任一不过、
    任务 / policy 缺失坏块）→ 冻结九键结构化拒绝，零 transport、零
    quota 网络、零 primer.json 写、零 window_primed journal、零窗口
    消费（不制造任何 durable 痕迹）；授权通过才透传 _prime_once_
    unchecked（八键返回）。PRIMER-AUTH-01..06 逐条对应实施规格 §4.5。
    任务 state 一律按真实布局写入测试 tmp repo。
    """

    PRE = detail("AVAILABLE", [win("five_hour", "EXHAUSTED", 0.0,
                                   RESET_FIVE_OLD)])
    POST = detail("AVAILABLE", [win("five_hour", "AVAILABLE", 100.0,
                                    RESET_FIVE_NEW)])

    def prime_authorized(self, task_id, transport=None, fetch=None,
                         boundary=BOUNDARY, policy=None):
        """写任务 state + 全注入调用 prime_authorized 的公共装置。"""
        _write_task_state(self.repo, task_id,
                          policy if policy is not None
                          else _authorized_policy())
        return primer.prime_authorized(
            self.repo, task_id, boundary_id=boundary,
            provider_identity_hash=IDENTITY,
            transport=transport if transport is not None
            else recording_transport([("ok", ok_payload())]),
            clock=fixed_clock,
            fetch_refresh=fetch if fetch is not None
            else scripted_fetch([dict(self.PRE), dict(self.POST)]))

    def assert_denied(self, result, transport, fetch, where=""):
        """冻结九键拒绝形状 + 全零副作用（零 transport 零刷新零落账）。"""
        self.assertEqual(
            sorted(result.keys()),
            sorted(primer.PRIME_AUTHORIZED_RESULT_KEYS),
            "拒绝键漂移 %s" % (where,))
        self.assertFalse(result["authorized"], where)
        self.assertFalse(result["primed"], where)
        self.assertFalse(result["materialized"], where)
        self.assertFalse(result["executable"], where)
        self.assertEqual(result["tokens"],
                         {"input": None, "output": None}, where)
        self.assertIsNone(result["latency_ms"], where)
        self.assertFalse(result["idempotent"], where)
        self.assertIsNone(result["error"], where)
        self.assertIsInstance(result["reason"], str, where)
        self.assertNotEqual(result["reason"], "", where)
        # 零副作用四重：零 transport、零 quota 网络、零 primer.json、
        # 零 window_primed journal（= 零 durable 痕迹、零窗口消费）
        self.assertEqual(len(transport.calls), 0, where)
        self.assertEqual(len(fetch.calls), 0, where)
        self.assertIsNone(primer.read_primer_state(self.repo), where)
        self.assertEqual(self.journal_events(), [], where)

    def test_auth01_primer_disabled_refuses(self):
        # PRIMER-AUTH-01：primer_enabled=false（policy 完整、其余合格）
        # → transport calls == 0、无 primer.json、无 journal
        transport = recording_transport([("ok", ok_payload())])
        fetch = scripted_fetch([dict(self.PRE), dict(self.POST)])
        result = self.prime_authorized(
            "task-auth01", transport=transport, fetch=fetch,
            policy=_authorized_policy(primer_enabled=False))
        self.assert_denied(result, transport, fetch, "AUTH-01")
        self.assertIn("primer_enabled", result["reason"])

    def test_auth02_manual_refuses(self):
        # PRIMER-AUTH-02：auto_resume="manual" → 同上零副作用
        transport = recording_transport([("ok", ok_payload())])
        fetch = scripted_fetch([dict(self.PRE), dict(self.POST)])
        result = self.prime_authorized(
            "task-auth02", transport=transport, fetch=fetch,
            policy=_authorized_policy(auto_resume="manual"))
        self.assert_denied(result, transport, fetch, "AUTH-02")
        self.assertIn("manual", result["reason"])

    def test_auth03_notify_refuses(self):
        # PRIMER-AUTH-03：auto_resume="notify" → 同上零副作用
        transport = recording_transport([("ok", ok_payload())])
        fetch = scripted_fetch([dict(self.PRE), dict(self.POST)])
        result = self.prime_authorized(
            "task-auth03", transport=transport, fetch=fetch,
            policy=_authorized_policy(auto_resume="notify"))
        self.assert_denied(result, transport, fetch, "AUTH-03")
        self.assertIn("notify", result["reason"])

    def test_auth04_auto_once_default_source_refuses(self):
        # PRIMER-AUTH-04：auto_once + source="default" → 同上零副作用
        transport = recording_transport([("ok", ok_payload())])
        fetch = scripted_fetch([dict(self.PRE), dict(self.POST)])
        result = self.prime_authorized(
            "task-auth04", transport=transport, fetch=fetch,
            policy=_authorized_policy(auto_resume="auto_once",
                                      source="default"))
        self.assert_denied(result, transport, fetch, "AUTH-04")
        self.assertIn("default", result["reason"])

    def test_auth05_until_done_user_executes_once(self):
        # PRIMER-AUTH-05：until_done + source="user" + primer_enabled=
        # true → 恰一次授权尝试（transport 恰 1、正常八键返回、
        # durable 落账 + journal 照常）
        transport = recording_transport([("ok", ok_payload())])
        fetch = scripted_fetch([dict(self.PRE), dict(self.POST)])
        result = self.prime_authorized("task-auth05",
                                       transport=transport, fetch=fetch)
        self.assertEqual(sorted(result.keys()), sorted(RESULT_KEYS))
        self.assertTrue(result["authorized"])
        self.assertTrue(result["primed"])
        self.assertTrue(result["materialized"])
        self.assertTrue(result["executable"])
        self.assertEqual(result["tokens"], {"input": 19, "output": 38})
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(len(fetch.calls), 2)  # 基线 + 确认
        self.assertIsNone(self.read_record()["error_kind"])
        events = self.journal_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "window_primed")

    def test_auth06_missing_or_corrupt_task_state_refuses(self):
        # PRIMER-AUTH-06：任务目录不存在 / state.json 坏 JSON / 缺
        # execution_policy 块 → 保守拒绝零 transport；且私有名
        # _prime_once_unchecked 直调仍可执行——绕过必须显式用私有名
        cases = [
            ("missingtask", None),            # 任务目录 / state 不存在
            ("corruptstate", "{corrupted"),   # 坏 JSON
            ("nopolicy", {"task_id": "t",       # 缺 execution_policy 块
                          "status": "waiting_quota"}),
        ]
        transport = recording_transport([("ok", ok_payload())])
        fetch = scripted_fetch([dict(self.PRE), dict(self.POST)])
        for name, payload in cases:
            with self.subTest(case=name):
                if payload is not None:
                    task_dir = os.path.join(self.repo, ".glm-conductor",
                                            "tasks", "task-%s" % name)
                    os.makedirs(task_dir, exist_ok=True)
                    with open(os.path.join(task_dir, "state.json"), "w",
                              encoding="utf-8") as handle:
                        if isinstance(payload, str):
                            handle.write(payload)
                        else:
                            json.dump(payload, handle)
                result = primer.prime_authorized(
                    self.repo, "task-%s" % name, boundary_id=BOUNDARY,
                    provider_identity_hash=IDENTITY, transport=transport,
                    clock=fixed_clock, fetch_refresh=fetch)
                self.assert_denied(result, transport, fetch, name)
        # 测试通道保留：私有名直调不读任务 policy、照常执行——证明
        # prime_authorized 的闸是机械的，「绕过」在调用面显式可见
        result = primer._prime_once_unchecked(
            self.repo, boundary_id=BOUNDARY,
            provider_identity_hash=IDENTITY, transport=transport,
            clock=fixed_clock, fetch_refresh=fetch)
        self.assertTrue(result["primed"])
        self.assertEqual(len(transport.calls), 1)

    def test_task_id_validation_valueerror(self):
        # task_id 非空 str 校验（中文 ValueError，先于一切 I/O）
        transport = recording_transport([("ok", ok_payload())])
        for bad in ("", None, 42, b"task"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    primer.prime_authorized(
                        self.repo, bad, boundary_id=BOUNDARY,
                        provider_identity_hash=IDENTITY,
                        transport=transport)
        self.assertEqual(len(transport.calls), 0)
        self.assertIsNone(primer.read_primer_state(self.repo))

    def test_denial_precedes_param_validation(self):
        # 校验顺序判断（未授权零副作用优先）：policy 拒绝 + 非法
        # boundary_id → 结构化拒绝而非 ValueError（拒绝路径绝不因参数
        # 问题抛错而落不了拒绝返回）
        transport = recording_transport([("ok", ok_payload())])
        fetch = scripted_fetch([dict(self.PRE)])
        result = self.prime_authorized(
            "task-order", transport=transport, fetch=fetch, boundary="",
            policy=_authorized_policy(primer_enabled=False))
        self.assertFalse(result["authorized"])
        self.assertEqual(len(transport.calls), 0)

    def test_authorized_path_keeps_param_valueerror(self):
        # 授权通过后参数校验沿用 _prime_once_unchecked 语义（透传抛错）
        transport = recording_transport([("ok", ok_payload())])
        with self.assertRaises(ValueError):
            self.prime_authorized("task-badparam", transport=transport,
                                  boundary="")
        self.assertEqual(len(transport.calls), 0)  # 校验先于传输


# —— 传输注入（零真实网络零真实模型调用；§37 异常类型名化） ——

class TransportInjectionTest(PrimerCase):

    BASE_FETCH = [detail("AVAILABLE", [win("five_hour", "EXHAUSTED", 0.0,
                                           RESET_FIVE_OLD)]),
                  detail("AVAILABLE", [win("five_hour", "AVAILABLE", 100.0,
                                           RESET_FIVE_NEW)])]

    def prime(self, transport, fetch=None):
        return primer._prime_once_unchecked(
            self.repo, boundary_id=BOUNDARY,
            provider_identity_hash=IDENTITY, transport=transport,
            clock=fixed_clock,
            fetch_refresh=fetch if fetch is not None
            else scripted_fetch([dict(item) for item in self.BASE_FETCH]))

    def test_request_shape_frozen(self):
        # P0-QP-00 §1.2 实测形态逐字：POST <baseURL>/v1/messages，
        # x-api-key + anthropic-version: 2023-06-01，GLM-5.3-Flash /
        # max_tokens 64 / 单条最小消息
        transport = recording_transport([("ok", ok_payload())])
        self.prime(transport)
        self.assertEqual(len(transport.calls), 1)
        call = transport.calls[0]
        self.assertEqual(call["url"], MESSAGES_URL)
        self.assertEqual(call["headers"]["x-api-key"], "test-key-abc")
        self.assertEqual(call["headers"]["anthropic-version"], "2023-06-01")
        self.assertEqual(call["headers"]["content-type"], "application/json")
        body = json.loads(call["body"].decode("utf-8"))
        self.assertEqual(body, {"model": "GLM-5.3-Flash", "max_tokens": 64,
                                "messages": [
                                    {"role": "user",
                                     "content": "Reply with a single "
                                                "word: pong"}]})
        self.assertEqual(call["timeout"], primer.DEFAULT_PRIME_TIMEOUT_SECONDS)

    def test_non_timeout_urlerror_no_retry(self):
        # URLError（非超时）→ 只尝试 1 次（非超时 URLError 不算歧义超时
        # ——RH-01：零重发、零确认刷新，超时用例见
        # SingleAttemptHardeningTest）
        transport = recording_transport(
            [("raise", urllib.error.URLError("getaddrinfo failed"))])
        result = self.prime(transport)
        self.assertEqual(len(transport.calls), 1)
        self.assertFalse(result["primed"])
        self.assertEqual(result["error"],
                         {"kind": "network", "detail": "URLError"})

    def test_httperror_401_auth(self):
        transport = recording_transport(
            [("raise", urllib.error.HTTPError(MESSAGES_URL, 401,
                                              "Unauthorized", None, None))])
        result = self.prime(transport)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(result["error"]["kind"], "auth")
        self.assertEqual(result["error"]["detail"], "HTTPError")

    def test_status_503_unavailable(self):
        transport = recording_transport([("status", 503)])
        result = self.prime(transport)
        self.assertEqual(result["error"]["kind"], "unavailable")
        self.assertEqual(result["error"]["detail"], "HTTP 503")

    def test_status_302_network(self):
        # 3xx → network（重定向禁用纪律的注入面兜底）
        transport = recording_transport([("status", 302)])
        result = self.prime(transport)
        self.assertEqual(result["error"]["kind"], "network")

    def test_non_json_malformed(self):
        transport = recording_transport([("bytes", 200, b"not json")])
        result = self.prime(transport)
        self.assertEqual(result["error"],
                         {"kind": "malformed", "detail": "JSONDecodeError"})

    def test_non_object_json_malformed(self):
        transport = recording_transport([("bytes", 200, b"[1, 2]")])
        result = self.prime(transport)
        self.assertEqual(result["error"]["kind"], "malformed")
        self.assertEqual(result["error"]["detail"], "response_not_object")

    def test_oversized_body_malformed(self):
        transport = recording_transport([("bytes", 200, b"x" * 65537)])
        result = self.prime(transport)
        self.assertEqual(result["error"],
                         {"kind": "malformed",
                          "detail": "bounded_response_exceeded"})

    def test_invalid_status_malformed(self):
        transport = recording_transport([("bytes", "200", b"{}")])
        result = self.prime(transport)
        self.assertEqual(result["error"],
                         {"kind": "malformed", "detail": "invalid_status"})

    def test_unknown_exception_kind(self):
        # 契约外异常 → unknown + 类型名（绝不透传异常文本）
        transport = recording_transport(
            [("raise", RuntimeError("secret http://url body"))])
        result = self.prime(transport)
        self.assertEqual(result["error"],
                         {"kind": "unknown", "detail": "RuntimeError"})

    def test_missing_credential_zero_transport(self):
        # 无凭证 → no-credential 结构化失败，零传输零 quota 网络
        transport = recording_transport([("ok", ok_payload())])
        fetch = scripted_fetch([detail("AVAILABLE", [])])
        with mock.patch.object(primer, "resolve_credential",
                               return_value=(None, None)):
            result = self.prime(transport, fetch=fetch)
        self.assertEqual(len(transport.calls), 0)
        self.assertEqual(len(fetch.calls), 0)
        self.assertFalse(result["primed"])
        self.assertEqual(result["error"],
                         {"kind": "no-credential", "detail": None})
        self.assertEqual(self.read_record()["error_kind"], "no-credential")

    def test_clock_injection_primed_at(self):
        self.prime(recording_transport([("ok", ok_payload())]))
        self.assertEqual(self.read_record()["primed_at"], CLOCK_ISO)

    def test_fetch_called_twice_on_success(self):
        # prime 前基线 + prime 后确认 = 恰 2 次强制刷新
        fetch = scripted_fetch([dict(item) for item in self.BASE_FETCH])
        self.prime(recording_transport([("ok", ok_payload())]), fetch=fetch)
        self.assertEqual(len(fetch.calls), 2)

    def test_invalid_params_valueerror(self):
        transport = recording_transport([("ok", ok_payload())])
        with self.assertRaises(ValueError):
            primer._prime_once_unchecked(self.repo, boundary_id="",
                              provider_identity_hash=IDENTITY,
                              transport=transport)
        with self.assertRaises(ValueError):
            primer._prime_once_unchecked(self.repo, boundary_id=BOUNDARY,
                              provider_identity_hash="", transport=transport)
        for bad in ("transport", 42):
            with self.assertRaises(ValueError):
                primer._prime_once_unchecked(self.repo, boundary_id=BOUNDARY,
                                  provider_identity_hash=IDENTITY,
                                  transport=bad)
            with self.assertRaises(ValueError):
                primer._prime_once_unchecked(self.repo, boundary_id=BOUNDARY,
                                  provider_identity_hash=IDENTITY,
                                  transport=transport, clock=bad)
            with self.assertRaises(ValueError):
                primer._prime_once_unchecked(self.repo, boundary_id=BOUNDARY,
                                  provider_identity_hash=IDENTITY,
                                  transport=transport, fetch_refresh=bad)


class BaseUrlTest(PrimerCase):
    """baseURL 容错读：zcode provider 配置 → allowlist 闸 → 内置默认。"""

    def test_default_when_no_config(self):
        self.assertEqual(primer._resolve_base_url(
            os.path.join(self.repo, "no-such.json")), DEFAULT_BASE)

    def test_from_config(self):
        path = os.path.join(self.repo, "config.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"provider": {"builtin:bigmodel-coding-plan": {
                "options": {"apiKey": "x",
                            "baseURL": DEFAULT_BASE + "/"}}}}, handle)
        self.assertEqual(primer._resolve_base_url(path), DEFAULT_BASE)

    def test_rejects_non_allowlisted_host(self):
        # §37 条款 5：绝不向 allowlist 外 host 发送凭证 → 回退默认
        path = os.path.join(self.repo, "config.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"provider": {"builtin:bigmodel-coding-plan": {
                "options": {"baseURL": "https://evil.example.com/api"}}}},
                handle)
        self.assertEqual(primer._resolve_base_url(path), DEFAULT_BASE)

    def test_rejects_http_scheme(self):
        path = os.path.join(self.repo, "config.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"provider": {"builtin:bigmodel-coding-plan": {
                "options": {"baseURL": "http://open.bigmodel.cn/api"}}}},
                handle)
        self.assertEqual(primer._resolve_base_url(path), DEFAULT_BASE)

    def test_prime_once_uses_configured_base_url(self):
        path = os.path.join(self.repo, "config.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"provider": {"builtin:bigmodel-coding-plan": {
                "options": {"baseURL": DEFAULT_BASE}}}}, handle)
        transport = recording_transport([("ok", ok_payload())])
        with mock.patch.object(primer, "_ZCODE_CONFIG_PATH", path):
            primer._prime_once_unchecked(self.repo, boundary_id=BOUNDARY,
                              provider_identity_hash=IDENTITY,
                              transport=transport, clock=fixed_clock,
                              fetch_refresh=scripted_fetch(
                                  [detail("AVAILABLE", []),
                                   detail("AVAILABLE", [])]))
        self.assertEqual(transport.calls[0]["url"], MESSAGES_URL)

    def test_default_host_in_allowlist(self):
        # 导入期断言的显式锚点：默认 host 恒在 §37 allowlist 内
        self.assertIn("open.bigmodel.cn", ALLOWED_HOSTS)
        self.assertTrue(DEFAULT_BASE.startswith("https://"))
        self.assertTrue(callable(primer.make_default_transport()))


# —— durable 存储 ——

class StoreTest(PrimerCase):

    def test_state_path_layout(self):
        self.assertEqual(
            primer.primer_state_path(self.repo),
            os.path.join(self.repo, ".glm-conductor", "quota",
                         "primer.json"))

    def test_read_missing_returns_none(self):
        self.assertIsNone(primer.read_primer_state(self.repo))

    def test_no_tmp_left_after_write(self):
        primer._prime_once_unchecked(self.repo, boundary_id=BOUNDARY,
                          provider_identity_hash=IDENTITY,
                          transport=recording_transport(
                              [("ok", ok_payload())]),
                          clock=fixed_clock,
                          fetch_refresh=scripted_fetch(
                              [detail("AVAILABLE", []),
                               detail("AVAILABLE", [])]))
        quota_dir = os.path.join(self.repo, ".glm-conductor", "quota")
        self.assertEqual([name for name in os.listdir(quota_dir)
                          if name.endswith(".tmp")], [])

    def test_permission_retry_exhaustion_raises(self):
        # PermissionError 有界重试（默认 3 次 / 0.2s），耗尽 →
        # PrimerStoreError——绝不静默吞（幂等闸失写必须让调用方看见）
        sleeps = []
        with mock.patch("os.replace",
                        side_effect=PermissionError(13, "locked")):
            with self.assertRaises(primer.PrimerStoreError):
                primer._write_primer_state(
                    self.repo, {"schema_version": 1, "primes": {}},
                    sleep=sleeps.append)
        self.assertEqual(len(sleeps),
                         primer.PRIMER_PERMISSION_RETRY_ATTEMPTS - 1)

    def test_result_keys_frozen_all_paths(self):
        fetch_ok = scripted_fetch([detail("AVAILABLE", []),
                                   detail("AVAILABLE", [])])
        paths = [
            # 成功路径
            primer._prime_once_unchecked(self.repo, boundary_id=BOUNDARY,
                              provider_identity_hash=IDENTITY,
                              transport=recording_transport(
                                  [("ok", ok_payload())]),
                              clock=fixed_clock, fetch_refresh=fetch_ok),
            # 幂等命中路径
            primer._prime_once_unchecked(self.repo, boundary_id=BOUNDARY,
                              provider_identity_hash=IDENTITY,
                              transport=recording_transport([]),
                              clock=fixed_clock, fetch_refresh=fetch_ok),
            # 失败路径（传输错误）
            primer._prime_once_unchecked(self.repo, boundary_id="glm:bbbbbbbbbbbbbbbb",
                              provider_identity_hash=IDENTITY,
                              transport=recording_transport([("status",
                                                              500)]),
                              clock=fixed_clock,
                              fetch_refresh=scripted_fetch(
                                  [detail("AVAILABLE", [])])),
            # 失败路径（无凭证）
            primer._prime_once_unchecked(self.repo, boundary_id="glm:cccccccccccccccc",
                              provider_identity_hash=IDENTITY,
                              transport=recording_transport([]),
                              clock=fixed_clock,
                              fetch_refresh=scripted_fetch([])),
        ]
        with mock.patch.object(primer, "resolve_credential",
                               return_value=(None, None)):
            paths.append(primer._prime_once_unchecked(
                self.repo, boundary_id="glm:dddddddddddddddd",
                provider_identity_hash=IDENTITY,
                transport=recording_transport([]), clock=fixed_clock,
                fetch_refresh=scripted_fetch([])))
        for result in paths:
            self.assertEqual(sorted(result.keys()), sorted(RESULT_KEYS))


class SignatureFreezeTest(PrimerCase):
    """冻结签名（wu-22-C4 规格 INTERFACES 原文；RH-02 起执行面二分：
    _prime_once_unchecked 私有名参数表与行为零变化，prime_authorized
    是唯一 public 执行面——repo_root/task_id 位置参 + 其余
    keyword-only；拒绝路径冻结九键 = 八键 + reason）。"""

    def test_prime_unchecked_signature_frozen(self):
        signature = inspect.signature(primer._prime_once_unchecked)
        self.assertEqual(list(signature.parameters),
                         PRIME_UNCHECKED_PARAMS)
        for name in ("transport", "clock", "fetch_refresh"):
            self.assertEqual(signature.parameters[name].default, None)
            self.assertTrue(
                signature.parameters[name].kind
                in (inspect.Parameter.KEYWORD_ONLY,))
        for name in ("boundary_id", "provider_identity_hash"):
            self.assertEqual(signature.parameters[name].default,
                             inspect.Parameter.empty)

    def test_prime_authorized_signature_frozen(self):
        signature = inspect.signature(primer.prime_authorized)
        self.assertEqual(
            list(signature.parameters),
            ["repo_root", "task_id", "boundary_id",
             "provider_identity_hash", "transport", "clock",
             "fetch_refresh"])
        for name in ("transport", "clock", "fetch_refresh"):
            self.assertEqual(signature.parameters[name].default, None)
            self.assertEqual(signature.parameters[name].kind,
                             inspect.Parameter.KEYWORD_ONLY)
        for name in ("task_id", "boundary_id", "provider_identity_hash"):
            self.assertEqual(signature.parameters[name].default,
                             inspect.Parameter.empty)

    def test_prime_authorized_denial_keys_frozen(self):
        # 拒绝路径冻结九键 = PRIMER_RESULT_KEYS 八键 + reason（新
        # public API 的返回形状；PRIMER_RESULT_KEYS 本身零变化）
        self.assertEqual(primer.PRIMER_RESULT_KEYS, tuple(RESULT_KEYS))
        self.assertEqual(
            primer.PRIME_AUTHORIZED_RESULT_KEYS,
            tuple(RESULT_KEYS) + ("reason",))

    def test_authorize_prime_signature(self):
        signature = inspect.signature(primer.authorize_prime)
        self.assertEqual(list(signature.parameters), ["policy_view"])


# —— journal 词汇与事件 ——

class JournalTest(PrimerCase):

    def prime(self, transport_script=None, boundary=BOUNDARY):
        return primer._prime_once_unchecked(
            self.repo, boundary_id=boundary,
            provider_identity_hash=IDENTITY,
            transport=recording_transport(
                transport_script or [("ok", ok_payload())]),
            clock=fixed_clock,
            fetch_refresh=scripted_fetch(
                [detail("AVAILABLE", [win("five_hour", "EXHAUSTED", 0.0,
                                          RESET_FIVE_OLD)]),
                 detail("AVAILABLE", [win("five_hour", "AVAILABLE", 100.0,
                                          RESET_FIVE_NEW)])]))

    def test_event_in_vocabulary(self):
        self.assertIn("window_primed", journal.RECOMMENDED_EVENTS)

    def test_event_fields_after_success(self):
        self.prime()
        events = self.journal_events()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(sorted(event.keys()), sorted(EVENT_KEYS))
        self.assertEqual(event["event"], "window_primed")
        self.assertEqual(event["boundary_id"], BOUNDARY)
        self.assertEqual(event["provider_identity_hash"], IDENTITY)
        self.assertTrue(event["materialized"])
        self.assertTrue(event["executable"])
        self.assertEqual(event["tokens"], {"input": 19, "output": 38})
        self.assertIsInstance(event["latency_ms"], int)
        self.assertFalse(event["idempotent"])

    def test_failure_event(self):
        self.prime(transport_script=[("status", 500)])
        events = self.journal_events()
        self.assertEqual(len(events), 1)
        self.assertFalse(events[0]["materialized"])
        self.assertFalse(events[0]["executable"])

    def test_hit_no_second_event(self):
        # 同键重入零事件（约束 3）
        self.prime()
        self.prime()
        self.assertEqual(len(self.journal_events()), 1)


if __name__ == "__main__":
    unittest.main()
