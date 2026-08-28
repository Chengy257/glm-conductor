#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.policy 纯引擎单元测试（v2 工作块 B7.1，§57-§59）。

运行：
    python3 -m unittest tests.test_policy -v

覆盖：
    - classify 矩阵：每条 deny / ask 规则 ≥2 个正例（含规格点名变体）
      + 反例（rm 无标志 / reset --soft / echo hi / git status 等）；
    - 复合命令：ls && rm -rf x 命中 deny；管道 cat a | grep x 不误报；
      deny 与 ask 同时命中取 deny（deny 先于 ask）；
    - 门控顺序（evaluate）：非 Bash 工具 allow；无活动任务 allow；
      deny 在 active_task=True 时无视 assurance；ask 仅 assurance_high；
      ask 命中 + 标准 assurance → allow（reason 注明不升级）；默认放行；
    - 大小写不敏感（RM -RF / Git Push / GIT PUSH --FORCE）；
    - 非 str command → ValueError；tool 严格等于 "Bash"（"bash" 不管理）。

仅 Python 3 标准库（unittest + re 间接），零第三方依赖、零网络、零磁盘。
"""

import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import policy


class DecisionsVocabularyTest(unittest.TestCase):
    """DECISIONS 三值决策词汇表。"""

    def test_decisions_constant_locked(self):
        self.assertEqual(policy.DECISIONS, ("allow", "ask", "deny"))


class DenyRuleMatrixTest(unittest.TestCase):
    """deny 规则正例矩阵：每条规则 ≥2 正例（含规格点名变体）。"""

    DENY_POSITIVES = {
        "rm-destructive": (
            "rm -rf / x",          # 规格点名变体
            "rm -f a b",           # 规格点名变体（仅 f）
            "rm -fr build/",
            "ls && rm -r dist",    # 复合命令尾段
        ),
        "git-reset-hard": (
            "git reset --hard",
            "git reset --hard HEAD~1",
            "git reset  --hard origin/main",   # 多空白
        ),
        "git-clean-force": (
            "git clean -fdx",
            "git clean -f",
            "git clean -ndfq",
        ),
        "git-push-force": (
            "git push origin main --force",    # 规格点名变体
            "git push -f",                     # 规格点名变体
            "git push --force",
            "git push origin -f main",
        ),
    }

    def test_each_deny_rule_positives(self):
        for rule, commands in self.DENY_POSITIVES.items():
            for command in commands:
                with self.subTest(rule=rule, command=command):
                    self.assertEqual(
                        policy.classify_bash(command), ("deny", rule))

    def test_force_with_lease_not_denied_falls_to_ask(self):
        # 表注：--force-with-lease 属可控变体，不拒，归 ask 的 git-push
        self.assertEqual(
            policy.classify_bash("git push --force-with-lease origin main"),
            ("ask", "git-push"))


class AskRuleMatrixTest(unittest.TestCase):
    """ask 规则正例矩阵：每条规则 ≥2 正例（含规格点名变体）。"""

    ASK_POSITIVES = {
        "git-push": (
            "git push",            # 非 force → ask 组命中（规格点名反例位）
            "git push origin main",
            "git push --tags",     # 亦属 release-ops，组内先命中 git-push
        ),
        "schema-migration": (
            "python manage.py migrate",       # 规格点名变体
            "npx prisma migrate deploy",      # 规格点名变体
            "alembic upgrade head",
            "alembic downgrade -1",
            "alembic stamp head",
            "knex migrate:latest",
        ),
        "release-ops": (
            "gh release create v1",           # 规格点名变体
            "npm publish",
            "cargo publish --allow-dirty",
        ),
        "permission-change": (
            "chmod +x run.sh",                # 规格点名变体
            "chown user:group file.txt",
            "icacls report.txt /grant Users:R",
            "attrib +r secret.txt",
        ),
    }

    def test_each_ask_rule_positives(self):
        for rule, commands in self.ASK_POSITIVES.items():
            for command in commands:
                with self.subTest(rule=rule, command=command):
                    self.assertEqual(
                        policy.classify_bash(command), ("ask", rule))


class ClassifyNegativeTest(unittest.TestCase):
    """反例：无害命令不命中任何规则（分类返回 None）。"""

    NEGATIVES = (
        "rm file.txt",            # rm 无 r/f 标志（规格点名反例）
        "git reset --soft HEAD~1",
        "echo hi",
        "git status",
        "git clean -n",           # 预演式 clean，无 f
        "cat a | grep x",         # 管道普通命令（规格点名不误报）
        "ls -la",
        "alembic history",        # 非迁移执行子命令
    )

    def test_benign_commands_return_none(self):
        for command in self.NEGATIVES:
            with self.subTest(command=command):
                self.assertIsNone(policy.classify_bash(command))


class CompoundAndPrecedenceTest(unittest.TestCase):
    """复合命令与 deny 先于 ask 的优先级。"""

    def test_compound_command_hits_deny(self):
        # 规格点名：ls && rm -rf x 命中 deny
        self.assertEqual(
            policy.classify_bash("ls && rm -rf x"),
            ("deny", "rm-destructive"))

    def test_pipe_no_false_positive(self):
        # 规格点名：管道内 cat a | grep x 不误报
        self.assertIsNone(policy.classify_bash("cat a | grep x"))

    def test_deny_beats_ask_on_multi_hit(self):
        # deny 与 ask 同时命中 → 取 deny（deny 先于 ask 扫描）
        self.assertEqual(
            policy.classify_bash("git push -f origin main && chmod +x run.sh"),
            ("deny", "git-push-force"))
        self.assertEqual(
            policy.classify_bash("chmod +x run.sh && git reset --hard"),
            ("deny", "git-reset-hard"))

    def test_quoted_destructive_text_also_matched(self):
        # 已知取舍（beta1 登记）：字符串中引用的破坏性命令文本也命中
        # （保守误报可接受，宁误报不漏报）
        self.assertEqual(
            policy.classify_bash("echo 'rm -rf x'"),
            ("deny", "rm-destructive"))


class CaseInsensitiveTest(unittest.TestCase):
    """大小写不敏感：正则对整条命令 IGNORECASE 搜索。"""

    def test_uppercase_variants(self):
        self.assertEqual(
            policy.classify_bash("RM -RF x"), ("deny", "rm-destructive"))
        self.assertEqual(
            policy.classify_bash("Git Push origin main"),
            ("ask", "git-push"))
        self.assertEqual(
            policy.classify_bash("GIT PUSH --FORCE"),
            ("deny", "git-push-force"))
        self.assertEqual(
            policy.classify_bash("Chmod +x run.sh"),
            ("ask", "permission-change"))


class ClassifyTypeValidationTest(unittest.TestCase):
    """classify_bash：非 str command → ValueError（中文消息）。"""

    def test_non_str_raises_value_error(self):
        for bad in (None, 123, 3.14, b"rm -rf", ["rm -rf"], {"cmd": "rm"}):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    policy.classify_bash(bad)


class EvaluateGatingTest(unittest.TestCase):
    """evaluate 门控顺序：非 Bash → 无活动任务 → deny → ask(仅 high) → 默认。"""

    def test_non_bash_tool_not_in_policy_surface(self):
        verdict = policy.evaluate(
            tool="Write", command="rm -rf /",
            active_task=True, assurance_high=True)
        self.assertEqual(
            verdict,
            {"decision": "allow", "rule": None,
             "reason": "非 Bash 工具不在策略面"})

    def test_tool_name_strictly_bash(self):
        # 严格等于 "Bash"：小写 "bash" 不在策略面（allow）
        verdict = policy.evaluate(
            tool="bash", command="rm -rf /",
            active_task=True, assurance_high=True)
        self.assertEqual(verdict["decision"], "allow")
        self.assertIsNone(verdict["rule"])

    def test_no_active_task_zero_intervention(self):
        # 门控 2 先于分类：deny 规则命中也无活动任务时零干预
        verdict = policy.evaluate(
            tool="Bash", command="git push --force",
            active_task=False, assurance_high=True)
        self.assertEqual(verdict["decision"], "allow")
        self.assertIsNone(verdict["rule"])
        self.assertEqual(verdict["reason"], "无活动任务，策略零干预")

    def test_deny_ignores_assurance_when_active(self):
        # 门控 3：deny 在 active_task=True 时无视 assurance（standard/high 恒拒）
        for high in (False, True):
            with self.subTest(assurance_high=high):
                verdict = policy.evaluate(
                    tool="Bash", command="git reset --hard",
                    active_task=True, assurance_high=high)
                self.assertEqual(verdict["decision"], "deny")
                self.assertEqual(verdict["rule"], "git-reset-hard")
                self.assertIn("git-reset-hard", verdict["reason"])

    def test_ask_only_when_assurance_high(self):
        verdict = policy.evaluate(
            tool="Bash", command="git push origin main",
            active_task=True, assurance_high=True)
        self.assertEqual(verdict["decision"], "ask")
        self.assertEqual(verdict["rule"], "git-push")

    def test_ask_downgraded_for_standard_assurance(self):
        # 门控 4：ask 命中但非 high → allow，reason 注明「标准保障任务不升级」
        verdict = policy.evaluate(
            tool="Bash", command="git push origin main",
            active_task=True, assurance_high=False)
        self.assertEqual(verdict["decision"], "allow")
        self.assertEqual(verdict["rule"], "git-push")
        self.assertIn("标准保障任务不升级", verdict["reason"])

    def test_no_hit_default_allow(self):
        verdict = policy.evaluate(
            tool="Bash", command="ls", active_task=True,
            assurance_high=True)
        self.assertEqual(
            verdict,
            {"decision": "allow", "rule": None, "reason": "默认放行"})

    def test_missing_command_tolerated_allow(self):
        # command 缺失（None）：策略层无文本可分类 → 按未命中放行
        verdict = policy.evaluate(
            tool="Bash", active_task=True, assurance_high=True)
        self.assertEqual(verdict["decision"], "allow")
        self.assertIsNone(verdict["rule"])

    def test_verdict_shape_locked(self):
        # 返回 dict 形状恒为三键：decision / rule / reason
        for verdict in (
                policy.evaluate(tool="Bash", command="ls",
                                active_task=True),
                policy.evaluate(tool="Bash", command="rm -rf /",
                                active_task=True),
                policy.evaluate(tool="Bash", command="git push",
                                active_task=True, assurance_high=True),
                policy.evaluate(tool="Read")):
            self.assertEqual(
                sorted(verdict.keys()), ["decision", "reason", "rule"])


if __name__ == "__main__":
    unittest.main()
