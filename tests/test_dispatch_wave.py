#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.dispatch_wave 单元测试（v2.1 M2 前半，WU-21-02，计划 §6.3/§6.6/§20.2）。

仅 Python 3 标准库（unittest + tempfile + subprocess），零第三方依赖；
permit 文件全部落盘在 tempfile.TemporaryDirectory 提供的临时目录
（真实 create/consume/invalidate 路径，非 mock），不污染真实工作区。
CLI 用例经 cli.main 列表注入 + redirect_stdout 捕获，另设一条真实
子进程路径（显式 encoding="utf-8"，CI 教训）。

覆盖（§20.2 数据层全集 + 冻结形状锚定）：
    - create_permit：冻结字段逐键锚定（permit_id="dp-"+12 hex、mode、
      reason、created_at/expires_at=now+ttl、consumed=False）/ now 注入
      / foreground 带 reason 合法 / 文件真实落盘（tmp 不残留）；
    - create 校验：mode 枚举 / foreground 缺 reason / reason 枚举 /
      background 带 reason 拒绝 / ttl 非正数（含 None 与 bool）/
      task_id / unit_id 非空——全部 ValueError 中文消息含字段名；
    - validate_permit 校验链（§20.2）：fake permit denied / valid
      allowed / expired（now 注入，边界：恰好相等即过期——RB-21-04
      用户锁定决策）/ wrong unit / wrong task（内容 task_id 与请求
      任务不符的兜底闸）/ wrong wave / wrong mode / 校验顺序
      （expired 先于 unit）/ consumed 后按 not found 拒绝；RB-21-04
      integrity 链（permit_id 形状闸 / payload.permit_id 一致性 /
      mode 枚举 / reason invariant / created_at / expires_at 可解析
      与先后 / consumed=true 载荷——全部 fail-closed deny）；
    - replay 防护（R3）：consume → True；validate → "permit not
      found"；二次 consume → False；audit 轨迹（.consumed.json 保留
      原内容、活跃文件消失）；
    - invalidate：abort 面作废（.invalidated.json 轨迹）+ 重复失效
      False + validate 拒绝；
    - load / list：缺失 None / 损坏文件 None（fail-closed）/ 非 dict
      None / list 排序 + 排除 consumed/invalidated/.tmp / TTL 过期后
      list 仍可见但 validate 拒绝；
    - marker：构造冻结格式 / 单行定位 / 多行文本定位 / 行尾无空白 /
      无 marker None / 空文本 None / 非 str None / 空 permit_id 抛；
    - 纯原语层边界：create/consume 不写 journal、不动 state（零
      state/journal 依赖由模块结构锁定，这里断言无副作用文件产生）；
    - CLI：permits 空列表形态 / 列表 / permit-show / permit-consume
      成功与重放拒绝 / 退出码 0/2/1 / 真实子进程 utf-8 烟雾。

运行：
    cd <repo_root> && python3 -m unittest tests.test_dispatch_wave -v
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import cli, dispatch_wave, journal, state

TID = "dw-task-1a2b3c"
OTHER_TASK = "dw-task-other99"

NOW = "2026-08-30T12:00:00.000Z"
LATER = "2026-08-30T12:10:00.000Z"      # NOW + 600s（默认 TTL 内）
AFTER_TTL = "2026-08-30T12:30:00.000Z"  # NOW + 1800s（恰好默认 TTL 到点）
FAR_LATER = "2026-08-30T13:00:00.000Z"  # 一小时后（已过期）

CLI_PATH = (Path(__file__).resolve().parents[1] / "plugins" /
            "glm-conductor" / "runtime" / "cli.py")


def run_cli(*args):
    """调用 cli.main 并捕获 stdout，返回 (退出码, 解析后 JSON)。"""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = cli.main(list(args))
    return code, json.loads(buffer.getvalue())


def permits_dir(root, task_id=TID):
    return Path(root) / ".glm-conductor" / "tasks" / task_id / "permits"


def raw_permit_file(root, permit_id, task_id=TID, **overrides):
    """按冻结形状手工落一张 permit 文件（wrong-task 等构造专用）。"""
    permit = {
        "permit_id": permit_id, "task_id": task_id, "unit_id": "u1",
        "wave_id": None, "mode": "background", "reason": None,
        "created_at": NOW,
        "expires_at": "2026-08-30T12:30:00.000Z", "consumed": False}
    permit.update(overrides)
    path = permits_dir(root, task_id) / ("%s.json" % permit_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(permit, fh, ensure_ascii=False, indent=2, sort_keys=True)
    return permit


class DispatchWaveTestBase(unittest.TestCase):
    """临时目录夹具：每个用例独立的 repo_root（零 git / 零 state 需求）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name


# —— create_permit：冻结形状 / now 注入 / 落盘事实 ——

class CreatePermitTest(DispatchWaveTestBase):

    def test_frozen_shape_fields_exact(self):
        # §6.3 冻结字段逐键锚定：恰为九键、值形状精确
        permit = dispatch_wave.create_permit(
            self.root, TID, "u1", now=NOW)
        self.assertEqual(
            sorted(permit),
            ["consumed", "created_at", "expires_at", "mode", "permit_id",
             "reason", "task_id", "unit_id", "wave_id"])
        self.assertEqual(permit["task_id"], TID)
        self.assertEqual(permit["unit_id"], "u1")
        self.assertIsNone(permit["wave_id"])
        self.assertEqual(permit["mode"], "background")
        self.assertIsNone(permit["reason"])  # background 恒 null
        self.assertFalse(permit["consumed"])
        # permit_id 形状："dp-" + 12 hex（os.urandom）
        self.assertRegex(permit["permit_id"],
                         r"^dp-[0-9a-f]{12}$")
        # 时间形状：ISO-8601 UTC 毫秒（now 注入原样起算）
        self.assertEqual(permit["created_at"], NOW)
        self.assertEqual(permit["expires_at"], AFTER_TTL)

    def test_unique_ids_across_creates(self):
        first = dispatch_wave.create_permit(self.root, TID, "u1", now=NOW)
        second = dispatch_wave.create_permit(self.root, TID, "u1", now=NOW)
        self.assertNotEqual(first["permit_id"], second["permit_id"])

    def test_foreground_permit_with_reason(self):
        for reason in dispatch_wave.FOREGROUND_REASONS:
            with self.subTest(reason=reason):
                permit = dispatch_wave.create_permit(
                    self.root, TID, "u1", mode="foreground", reason=reason,
                    now=NOW)
                self.assertEqual(permit["mode"], "foreground")
                self.assertEqual(permit["reason"], reason)

    def test_file_persisted_atomic_shape(self):
        permit = dispatch_wave.create_permit(self.root, TID, "u1", now=NOW)
        path = permits_dir(self.root) / ("%s.json" % permit["permit_id"])
        self.assertTrue(path.is_file())
        # tmp 不残留（os.replace 成功后必须清场）
        self.assertEqual(
            [p.name for p in permits_dir(self.root).iterdir()],
            [path.name])
        with open(path, "r", encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), permit)

    def test_wave_id_and_ttl_passthrough(self):
        permit = dispatch_wave.create_permit(
            self.root, TID, "u1", wave_id="wave-2", ttl_seconds=60,
            now=NOW)
        self.assertEqual(permit["wave_id"], "wave-2")
        self.assertEqual(permit["expires_at"], "2026-08-30T12:01:00.000Z")


class CreatePermitValidationTest(DispatchWaveTestBase):
    """create_permit 非法输入 → ValueError（中文消息含字段名），零写入。"""

    def _assert_value_error_with(self, field, *args, **kwargs):
        before = sorted(os.listdir(self.root)) if os.path.isdir(self.root) \
            else []
        with self.assertRaises(ValueError) as ctx:
            dispatch_wave.create_permit(self.root, *args, **kwargs)
        self.assertIn(field, str(ctx.exception))
        # 失败零写入：任务目录下连 permits/ 都不该出现
        after = sorted(os.listdir(self.root)) if os.path.isdir(self.root) \
            else []
        self.assertEqual(after, before)

    def test_empty_or_illegal_task_id(self):
        for bad in ("", None, 42):
            with self.subTest(bad=bad):
                self._assert_value_error_with(
                    "task_id", bad, "u1", now=NOW)

    def test_empty_or_illegal_unit_id(self):
        for bad in ("", None, 42):
            with self.subTest(bad=bad):
                self._assert_value_error_with(
                    "unit_id", TID, bad, now=NOW)

    def test_mode_out_of_vocabulary(self):
        for bad in ("sync", "BACKGROUND", None, 1):
            with self.subTest(bad=bad):
                self._assert_value_error_with(
                    "mode", TID, "u1", mode=bad, now=NOW)

    def test_foreground_without_reason_rejected(self):
        # §20.3 foreground_without_reason_rejected 的数据层锚点
        self._assert_value_error_with(
            "reason", TID, "u1", mode="foreground", now=NOW)

    def test_foreground_reason_out_of_vocabulary(self):
        for bad in ("because", "", "SYNC_DEPENDENCY", 42):
            with self.subTest(bad=bad):
                self._assert_value_error_with(
                    "reason", TID, "u1", mode="foreground", reason=bad,
                    now=NOW)

    def test_background_with_reason_rejected(self):
        # background 时 reason 恒 null：传了也拒（字段不变量不静默归一）
        self._assert_value_error_with(
            "reason", TID, "u1", mode="background",
            reason="synchronous_dependency", now=NOW)

    def test_ttl_must_be_positive_number(self):
        for bad in (0, -1, None, True, "60"):
            with self.subTest(bad=bad):
                self._assert_value_error_with(
                    "ttl_seconds", TID, "u1", ttl_seconds=bad, now=NOW)


# —— validate_permit：§20.2 校验链 ——

class ValidatePermitTest(DispatchWaveTestBase):

    def setUp(self):
        super().setUp()
        self.permit = dispatch_wave.create_permit(
            self.root, TID, "u1", wave_id="wave-1", now=NOW)
        self.pid = self.permit["permit_id"]

    def test_valid_permit_allowed(self):
        # §20.2 valid_permit_allowed（数据层）
        self.assertEqual(
            dispatch_wave.validate_permit(self.root, TID, self.pid,
                                          now=LATER),
            (True, "ok"))
        # 全维校验也过
        self.assertEqual(
            dispatch_wave.validate_permit(
                self.root, TID, self.pid, unit_id="u1", wave_id="wave-1",
                mode="background", now=LATER),
            (True, "ok"))

    def test_fake_permit_denied(self):
        # §20.2 fake_permit_denied：手写不存在的 permit 必须 reject
        for fake in ("dp-000000000000", "totally-made-up", "",
                     "../escape", None):
            with self.subTest(fake=fake):
                self.assertEqual(
                    dispatch_wave.validate_permit(self.root, TID, fake,
                                                  now=LATER),
                    (False, "permit not found"))

    def test_expired_permit_denied_with_now_injection(self):
        # §20.2 expired_permit_denied（now 注入，不真睡 TTL）
        self.assertEqual(
            dispatch_wave.validate_permit(self.root, TID, self.pid,
                                          now=FAR_LATER),
            (False, "permit expired"))

    def test_expiry_boundary_exact_hit_expired(self):
        # RB-21-04 用户锁定决策（语义翻转，非回退掩盖）：expires_at
        # <= now 即过期——恰好到达过期时刻即失效，严格大于才有效
        # （authorization fail-closed 方向：更严是唯一允许方向）
        self.assertEqual(
            dispatch_wave.validate_permit(self.root, TID, self.pid,
                                          now=AFTER_TTL),
            (False, "permit expired"))

    def test_expiry_just_before_boundary_still_valid(self):
        # 新边界的另一侧锚定：expires_at 之前一刻仍有效
        # （12:29:59.999 < expires_at 12:30:00.000 → ok）
        self.assertEqual(
            dispatch_wave.validate_permit(
                self.root, TID, self.pid, now="2026-08-30T12:29:59.999Z"),
            (True, "ok"))

    # —— RB-21-04 integrity 链（§5.4 命名回归集；malformed 构造走
    # _rewrite_payload：只动被测字段，其余保持 §6.3 冻结形状） ——

    def _rewrite_payload(self, **overrides):
        """把 setUp 签发的活跃 permit 按字段覆盖后原位重写落盘。"""
        permit = dict(self.permit)
        permit.update(overrides)
        path = permits_dir(self.root) / ("%s.json" % self.pid)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(permit, fh, ensure_ascii=False, indent=2,
                      sort_keys=True)
        return permit

    def test_malformed_expires_at_denied(self):
        # 损坏 expires_at 绝不按未过期放行（fail-closed 反转旧口径）
        for bad in ("not-a-timestamp", "", 42, None):
            with self.subTest(bad=bad):
                self._rewrite_payload(expires_at=bad)
                self.assertEqual(
                    dispatch_wave.validate_permit(self.root, TID, self.pid,
                                                  now=LATER),
                    (False, "permit malformed"))

    def test_malformed_created_at_denied(self):
        for bad in ("not-a-timestamp", "", 42, None):
            with self.subTest(bad=bad):
                self._rewrite_payload(created_at=bad)
                self.assertEqual(
                    dispatch_wave.validate_permit(self.root, TID, self.pid,
                                                  now=LATER),
                    (False, "permit malformed"))

    def test_expires_before_created_denied(self):
        # 时间倒挂（expires_at < created_at）的伪造 permit → malformed
        self._rewrite_payload(created_at=LATER, expires_at=NOW)
        self.assertEqual(
            dispatch_wave.validate_permit(self.root, TID, self.pid,
                                          now=LATER),
            (False, "permit malformed"))

    def test_invalid_mode_in_file_denied(self):
        # 校验端复核创建端不变量：文件内 mode 不在 PERMIT_MODES 即拒
        for bad in ("sync", "BACKGROUND", "", 42, None):
            with self.subTest(bad=bad):
                self._rewrite_payload(mode=bad)
                self.assertEqual(
                    dispatch_wave.validate_permit(self.root, TID, self.pid,
                                                  now=LATER),
                    (False, "permit mode mismatch"))

    def test_background_reason_in_file_denied(self):
        # background permit 的 reason 恒 null：文件内写了 reason 即拒
        for bad in ("synchronous_dependency", "because", ""):
            with self.subTest(bad=bad):
                self._rewrite_payload(reason=bad)
                self.assertEqual(
                    dispatch_wave.validate_permit(self.root, TID, self.pid,
                                                  now=LATER),
                    (False, "permit reason mismatch"))

    def test_foreground_reason_out_of_vocabulary_in_file_denied(self):
        # foreground permit 的 reason 不在 §6.6 冻结词汇 → 拒
        self._rewrite_payload(mode="foreground", reason="because")
        self.assertEqual(
            dispatch_wave.validate_permit(self.root, TID, self.pid,
                                          now=LATER),
            (False, "permit reason mismatch"))

    def test_permit_id_payload_mismatch_denied(self):
        # payload.permit_id ≠ 请求 permit_id → id mismatch（文件名主体
        # 与请求一致由 load 按 permit_id 定位结构性保证，此处闭合三角）
        self._rewrite_payload(permit_id="dp-ffffffffffff")
        self.assertEqual(
            dispatch_wave.validate_permit(self.root, TID, self.pid,
                                          now=LATER),
            (False, "permit id mismatch"))

    def test_consumed_true_payload_denied(self):
        # 活跃 .json 内写 "consumed": true：载荷消费标记从被忽略到拒绝
        self._rewrite_payload(consumed=True)
        self.assertEqual(
            dispatch_wave.validate_permit(self.root, TID, self.pid,
                                          now=LATER),
            (False, "permit consumed"))

    def test_missing_consumed_field_malformed_denied(self):
        # consumed 缺失 / 非 bool：载荷形状不完整 → malformed
        for bad in (None, "false", 1):
            with self.subTest(bad=bad):
                self._rewrite_payload(consumed=bad)
                self.assertEqual(
                    dispatch_wave.validate_permit(self.root, TID, self.pid,
                                                  now=LATER),
                    (False, "permit malformed"))

    def test_non_dp_shape_id_denied_as_not_found(self):
        # permit_id 形状闸：非 "dp-"+12hex 的请求 id 哪文件存在也按
        # 不存在 deny（无形状闸时此文件可全链通过拿到 ok——闸的真实缺口）
        raw_permit_file(self.root, "totally-made-up")
        self.assertEqual(
            dispatch_wave.validate_permit(self.root, TID, "totally-made-up",
                                          now=LATER),
            (False, "permit not found"))

    def test_malformed_unit_or_wave_type_denied(self):
        # unit_id / wave_id 类型完整性：非 str 的载荷形状 → malformed
        self._rewrite_payload(unit_id=42)
        self.assertEqual(
            dispatch_wave.validate_permit(self.root, TID, self.pid,
                                          now=LATER),
            (False, "permit malformed"))
        self._rewrite_payload(wave_id=42)
        self.assertEqual(
            dispatch_wave.validate_permit(self.root, TID, self.pid,
                                          now=LATER),
            (False, "permit malformed"))

    def test_wrong_unit_denied(self):
        # §20.2 permit_wrong_unit_denied
        self.assertEqual(
            dispatch_wave.validate_permit(self.root, TID, self.pid,
                                          unit_id="u2", now=LATER),
            (False, "permit unit mismatch"))

    def test_wrong_wave_denied(self):
        self.assertEqual(
            dispatch_wave.validate_permit(self.root, TID, self.pid,
                                          wave_id="wave-9", now=LATER),
            (False, "permit wave mismatch"))

    def test_wrong_mode_denied(self):
        self.assertEqual(
            dispatch_wave.validate_permit(self.root, TID, self.pid,
                                          mode="foreground", now=LATER),
            (False, "permit mode mismatch"))

    def test_validation_order_expired_beats_unit(self):
        # 冻结顺序：task > expired > unit > wave > mode——过期与 wrong
        # unit 同时命中时报 expired
        self.assertEqual(
            dispatch_wave.validate_permit(self.root, TID, self.pid,
                                          unit_id="u2", now=FAR_LATER),
            (False, "permit expired"))
        # unit 先于 wave / mode
        self.assertEqual(
            dispatch_wave.validate_permit(
                self.root, TID, self.pid, unit_id="u2", wave_id="wave-9",
                mode="foreground", now=LATER),
            (False, "permit unit mismatch"))
        self.assertEqual(
            dispatch_wave.validate_permit(
                self.root, TID, self.pid, wave_id="wave-9",
                mode="foreground", now=LATER),
            (False, "permit wave mismatch"))

    def test_consumed_permit_denied_as_not_found(self):
        # §20.2 permit_replay_denied 的校验面：消费后 validate 拒绝
        self.assertTrue(
            dispatch_wave.consume_permit(self.root, TID, self.pid))
        self.assertEqual(
            dispatch_wave.validate_permit(self.root, TID, self.pid,
                                          now=LATER),
            (False, "permit not found"))

    def test_wrong_task_content_mismatch_denied(self):
        # 文件内容 task_id 与请求任务不符（手工挪动 / 伪造文件的兜底闸）：
        # load 按目录定位找得到文件，validate 在内容闸拒绝
        permit = raw_permit_file(self.root, "dp-bbbbbbbbbbbb")
        permit["task_id"] = OTHER_TASK  # 模拟跨任务挪动后的脏内容
        path = permits_dir(self.root) / "dp-bbbbbbbbbbbb.json"
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(permit, fh, ensure_ascii=False, indent=2,
                      sort_keys=True)
        self.assertEqual(
            dispatch_wave.validate_permit(self.root, TID, "dp-bbbbbbbbbbbb",
                                          now=LATER),
            (False, "permit task mismatch"))


# —— replay 防护 / 审计轨迹（§6.3 R3 锁定） ——

class ConsumeReplayTest(DispatchWaveTestBase):

    def setUp(self):
        super().setUp()
        self.permit = dispatch_wave.create_permit(
            self.root, TID, "u1", now=NOW)
        self.pid = self.permit["permit_id"]

    def test_consume_once_true_then_replay_false(self):
        self.assertTrue(
            dispatch_wave.consume_permit(self.root, TID, self.pid))
        # 二次消费：文件已改名 → False（重放被机械拒绝，无需文件锁）
        self.assertFalse(
            dispatch_wave.consume_permit(self.root, TID, self.pid))
        self.assertFalse(
            dispatch_wave.consume_permit(self.root, TID, self.pid))

    def test_consumed_audit_trail_preserved(self):
        dispatch_wave.consume_permit(self.root, TID, self.pid)
        # 活跃文件消失；.consumed.json 保留原内容（消费态由文件名承载）
        active = permits_dir(self.root) / ("%s.json" % self.pid)
        trail = permits_dir(self.root) / ("%s.consumed.json" % self.pid)
        self.assertFalse(active.exists())
        self.assertTrue(trail.is_file())
        with open(trail, "r", encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), self.permit)
        self.assertEqual(
            [p.name for p in permits_dir(self.root).iterdir()],
            [trail.name])

    def test_consume_unknown_or_illegal_id_false(self):
        self.assertFalse(
            dispatch_wave.consume_permit(self.root, TID, "dp-000000000000"))
        self.assertFalse(dispatch_wave.consume_permit(self.root, TID, ""))
        self.assertFalse(
            dispatch_wave.consume_permit(self.root, TID, "../escape"))
        self.assertFalse(dispatch_wave.consume_permit(self.root, TID, None))


class InvalidatePermitTest(DispatchWaveTestBase):

    def setUp(self):
        super().setUp()
        self.permit = dispatch_wave.create_permit(
            self.root, TID, "u1", now=NOW)
        self.pid = self.permit["permit_id"]

    def test_invalidate_once_true_then_false(self):
        self.assertTrue(
            dispatch_wave.invalidate_permit(self.root, TID, self.pid))
        self.assertFalse(
            dispatch_wave.invalidate_permit(self.root, TID, self.pid))

    def test_invalidated_audit_trail_and_validate_denied(self):
        dispatch_wave.invalidate_permit(self.root, TID, self.pid)
        trail = permits_dir(self.root) / ("%s.invalidated.json" % self.pid)
        self.assertTrue(trail.is_file())
        self.assertFalse(
            (permits_dir(self.root) / ("%s.json" % self.pid)).exists())
        self.assertEqual(
            dispatch_wave.validate_permit(self.root, TID, self.pid,
                                          now=LATER),
            (False, "permit not found"))
        # 已失效的也不能再消费（原文件名处无文件）
        self.assertFalse(
            dispatch_wave.consume_permit(self.root, TID, self.pid))


# —— load / list ——

class LoadPermitTest(DispatchWaveTestBase):

    def test_missing_returns_none(self):
        self.assertIsNone(
            dispatch_wave.load_permit(self.root, TID, "dp-000000000000"))
        # 目录都不存在同样 None
        self.assertIsNone(
            dispatch_wave.load_permit(self.root, TID, "dp-111111111111"))

    def test_round_trip_fields(self):
        permit = dispatch_wave.create_permit(
            self.root, TID, "u1", wave_id="w", now=NOW)
        self.assertEqual(
            dispatch_wave.load_permit(self.root, TID,
                                      permit["permit_id"]), permit)

    def test_corrupt_or_non_dict_file_fail_closed(self):
        permits_dir(self.root).mkdir(parents=True)
        corrupt = permits_dir(self.root) / "dp-cccccccccccc.json"
        corrupt.write_text('{"permit_id": "dp-cccccccccccc"',  # 半截 JSON
                           encoding="utf-8")
        non_dict = permits_dir(self.root) / "dp-dddddddddddd.json"
        non_dict.write_text("[1, 2, 3]", encoding="utf-8")
        self.assertIsNone(
            dispatch_wave.load_permit(self.root, TID, "dp-cccccccccccc"))
        self.assertIsNone(
            dispatch_wave.load_permit(self.root, TID, "dp-dddddddddddd"))
        # 校验同样按 not found deny（绝不因脏文件放行）
        self.assertEqual(
            dispatch_wave.validate_permit(self.root, TID,
                                          "dp-cccccccccccc", now=LATER),
            (False, "permit not found"))


class ListPermitsTest(DispatchWaveTestBase):

    def test_empty_before_any_permit(self):
        self.assertEqual(dispatch_wave.list_permits(self.root, TID), [])

    def test_sorted_by_permit_id(self):
        ids = []
        for _ in range(5):
            ids.append(dispatch_wave.create_permit(
                self.root, TID, "u1", now=NOW)["permit_id"])
        self.assertEqual(
            [p["permit_id"] for p in
             dispatch_wave.list_permits(self.root, TID)], sorted(ids))

    def test_excludes_consumed_invalidated_and_tmp(self):
        keep = dispatch_wave.create_permit(self.root, TID, "u1", now=NOW)
        consumed = dispatch_wave.create_permit(self.root, TID, "u1", now=NOW)
        invalidated = dispatch_wave.create_permit(
            self.root, TID, "u2", now=NOW)
        self.assertTrue(dispatch_wave.consume_permit(
            self.root, TID, consumed["permit_id"]))
        self.assertTrue(dispatch_wave.invalidate_permit(
            self.root, TID, invalidated["permit_id"]))
        # .tmp 残影也不列
        permits_dir(self.root).mkdir(parents=True, exist_ok=True)
        (permits_dir(self.root) / "dp-eeeeeeeeeeee.json.tmp").write_text(
            "{}", encoding="utf-8")
        self.assertEqual(
            [p["permit_id"] for p in
             dispatch_wave.list_permits(self.root, TID)],
            [keep["permit_id"]])

    def test_expired_still_listed_but_validate_denied(self):
        # TTL 过期不删文件：list 仍可见（审计面），validate 拒绝（门面）
        expired = dispatch_wave.create_permit(
            self.root, TID, "u1", ttl_seconds=1, now=NOW)
        self.assertEqual(
            [p["permit_id"] for p in
             dispatch_wave.list_permits(self.root, TID)],
            [expired["permit_id"]])
        self.assertEqual(
            dispatch_wave.validate_permit(
                self.root, TID, expired["permit_id"], now=FAR_LATER),
            (False, "permit expired"))


# —— marker（§6.4 冻结格式） ——

class MarkerTest(unittest.TestCase):

    PID = "dp-abc123def456"

    def test_marker_for_frozen_format(self):
        self.assertEqual(
            dispatch_wave.marker_for(self.PID),
            "GLM_CONDUCTOR_DISPATCH=" + self.PID)

    def test_marker_for_empty_id_raises(self):
        for bad in ("", None, 42):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    dispatch_wave.marker_for(bad)

    def test_parse_single_line(self):
        text = "请实施。%s 请遵守 ownership。" % dispatch_wave.marker_for(
            self.PID)
        self.assertEqual(dispatch_wave.parse_marker(text), self.PID)

    def test_parse_locates_within_multiline_text(self):
        text = ("任务说明：\n"
                "第一步做 X。\n"
                "%s\n"
                "第二步做 Y。\n") % dispatch_wave.marker_for(self.PID)
        self.assertEqual(dispatch_wave.parse_marker(text), self.PID)

    def test_parse_marker_at_end_of_text(self):
        self.assertEqual(
            dispatch_wave.parse_marker("前文\n" + dispatch_wave.marker_for(
                self.PID)),
            self.PID)

    def test_no_marker_returns_none(self):
        self.assertIsNone(
            dispatch_wave.parse_marker("没有任何 marker 的普通 prompt"))
        # 残缺前缀（缺 "=" 与 id）匹配不上 MARKER_PREFIX → None
        self.assertIsNone(
            dispatch_wave.parse_marker("GLM_CONDUCTOR_DISPATCH"))

    def test_empty_or_non_text_returns_none(self):
        for bad in ("", None, 42, b"GLM_CONDUCTOR_DISPATCH=dp-abc123def456"):
            with self.subTest(bad=bad):
                self.assertIsNone(dispatch_wave.parse_marker(bad))

    def test_first_marker_wins(self):
        text = "%s\n%s" % (dispatch_wave.marker_for(self.PID),
                           dispatch_wave.marker_for("dp-ffffffffffff"))
        self.assertEqual(dispatch_wave.parse_marker(text), self.PID)

    def test_round_trip_with_validate(self):
        # 主会话流程形态：marker → parse → validate 通过
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        permit = dispatch_wave.create_permit(root, TID, "u1", now=NOW)
        text = "dispatch now %s" % dispatch_wave.marker_for(
            permit["permit_id"])
        pid = dispatch_wave.parse_marker(text)
        self.assertEqual(
            dispatch_wave.validate_permit(root, TID, pid, unit_id="u1",
                                          now=LATER),
            (True, "ok"))


# —— 纯原语层边界：零 journal 写入、零 state 写入 ——

class PrimitiveLayerBoundaryTest(DispatchWaveTestBase):

    def test_permit_operations_write_no_journal_or_state(self):
        # 分工锚定：journal / state 归 task_manager 接线层，本层零写入
        permit = dispatch_wave.create_permit(self.root, TID, "u1", now=NOW)
        dispatch_wave.consume_permit(self.root, TID, permit["permit_id"])
        dispatch_wave.create_permit(self.root, TID, "u2", now=NOW)
        dispatch_wave.invalidate_permit(
            self.root, TID,
            dispatch_wave.list_permits(self.root, TID)[0]["permit_id"])
        self.assertEqual(journal.read_events(self.root, TID), [])
        self.assertIsNone(state.load_state(self.root, TID))
        # 任务目录下只有 permits/，没有 events.jsonl / state.json /
        # leases.json
        task_dir = Path(self.root) / ".glm-conductor" / "tasks" / TID
        self.assertEqual(sorted(p.name for p in task_dir.iterdir()),
                         ["permits"])


# —— CLI：permits / permit-show / permit-consume ——

class PermitCliTest(DispatchWaveTestBase):

    def setUp(self):
        super().setUp()
        # CLI 任务存在闸要求 state.json 在（防拼错 task_id 得到空列表假象）
        st = state.new_task_state(
            TID, "permit CLI 测试目标",
            {"mode": "delegate", "delegability": "high",
             "assurance": "standard", "executor": "flash-implementer",
             "continuity": "foreground"},
            ownership_files=("src/**",),
            verification_required=("python3 -m unittest tests.test_dw",))
        state.save_state(self.root, st)

    def test_permits_empty_list_shape(self):
        code, payload = run_cli("permits", self.root, TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload, {"task_id": TID, "permits": []})

    def test_permits_lists_created_permit(self):
        permit = dispatch_wave.create_permit(self.root, TID, "u1", now=NOW)
        code, payload = run_cli("permits", self.root, TID)
        self.assertEqual(code, 0)
        self.assertEqual(payload["permits"], [permit])

    def test_permit_show_existing_and_missing(self):
        permit = dispatch_wave.create_permit(self.root, TID, "u1", now=NOW)
        code, payload = run_cli("permit-show", self.root, TID,
                                permit["permit_id"])
        self.assertEqual(code, 0)
        self.assertEqual(payload, permit)
        code, payload = run_cli("permit-show", self.root, TID,
                                "dp-000000000000")
        self.assertEqual(code, 1)
        self.assertIn("error", payload)
        # 已消费同样按缺失报（exit 1）
        dispatch_wave.consume_permit(self.root, TID, permit["permit_id"])
        code, payload = run_cli("permit-show", self.root, TID,
                                permit["permit_id"])
        self.assertEqual(code, 1)

    def test_permit_consume_success_then_replay_exit_1(self):
        permit = dispatch_wave.create_permit(self.root, TID, "u1", now=NOW)
        code, payload = run_cli("permit-consume", self.root, TID,
                                permit["permit_id"])
        self.assertEqual(code, 0)
        self.assertEqual(payload, {"task_id": TID,
                                   "permit_id": permit["permit_id"],
                                   "consumed": True})
        # 重放：exit 1 + 落盘轨迹仍在（审计可见）
        code, payload = run_cli("permit-consume", self.root, TID,
                                permit["permit_id"])
        self.assertEqual(code, 1)
        self.assertIn("error", payload)
        self.assertTrue((permits_dir(self.root) /
                         ("%s.consumed.json" % permit["permit_id"]))
                        .is_file())

    def test_usage_and_task_missing_exit_codes(self):
        # 用法错误 → 2
        code, payload = run_cli("permits", self.root)
        self.assertEqual(code, 2)
        code, payload = run_cli("permit-show", self.root, TID)
        self.assertEqual(code, 2)
        code, payload = run_cli("permit-consume", self.root, TID, "a", "b")
        self.assertEqual(code, 2)
        # 任务不存在 → 1
        code, payload = run_cli("permits", self.root, "dw-task-notta")
        self.assertEqual(code, 1)

    def test_subprocess_real_cli_utf8(self):
        # 真实子进程路径（显式 encoding="utf-8"，CI 教训）
        proc = subprocess.run(
            [sys.executable, str(CLI_PATH), "permits", self.root, TID],
            capture_output=True, encoding="utf-8", check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload, {"task_id": TID, "permits": []})


if __name__ == "__main__":
    unittest.main()
