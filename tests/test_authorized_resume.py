#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime 授权续跑测试（v2.1 §14/§22.7，wu-21-11 Authorized Quota Resume）。

v2.4 Phase 2（W6，P2-F）：本文件的被测主体——授权续跑编排
（handle_quota_exhausted / resume_from_quota / record_quota_wake /
quota_wake_prompt 的任务级流水线、订阅接线、恢复对账、recovery 渲染
契约）——已随 v2.3 执行面退役删除，对应用例一并移除（同类面由
tests/test_boundary_consumption / test_resume_consumption 的记账域
存活锚定与 Phase 3 收口承接）。保留的只有不依赖编排体的纯决策与
任务缺失闸锚定（规范落点 continuity.resume / quota.accounting）。
"""



TID = "authorized-resume-1a2b3c"

import sys, unittest
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime.continuity import resume as _cr
from runtime.quota import accounting as _qa


class WakeAuthorizationPureDecisionTest(unittest.TestCase):
    """授权矩阵纯决策（continuity.resume._quota_wake_decision 规范落点）：
    auto_once / until_done 无 user 授权 → 永不要求 wake（纵深防御分支
    ——即使手写 state 绕过了 §5.4 不变量，runtime 也绝不产出 wake 指令）。
    （矩阵的其余分支经 handle_quota_exhausted 编排验证——该编排已随
    v2.3 执行面退役，对应用例移除。）"""

    def test_auto_once_without_user_source_never_requires_wake(self):
        decision = _cr._quota_wake_decision({
            "auto_resume": "auto_once", "source": "default",
            "max_quota_windows": 1, "consumed_quota_windows": 0,
            "remaining": 1})
        self.assertFalse(decision["required"])
        self.assertIsNone(decision["prompt_mode"])
        self.assertFalse(decision["transition_waiting_user"])
        self.assertIn("user", decision["reason"])
        # until_done 同款
        decision = _cr._quota_wake_decision({
            "auto_resume": "until_done", "source": "default",
            "max_quota_windows": 2, "consumed_quota_windows": 0,
            "remaining": 2})
        self.assertFalse(decision["required"])


class WakePromptMissingTaskTest(unittest.TestCase):
    """wake prompt 的任务缺失闸（continuity.resume.quota_wake_prompt
    规范落点）。prompt 内容契约与 record_quota_wake 记账链的其余用例
    以 v2.3 执行面编排为被测面，已随执行面退役移除。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_prompt_missing_task_rejected(self):
        with self.assertRaises(_qa.TaskManagerError):
            _cr.quota_wake_prompt(self.repo, TID)


if __name__ == "__main__":
    unittest.main()
