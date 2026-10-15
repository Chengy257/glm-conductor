#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agents/*.md 模型绑定能力意图测试（unit v232-agent-model-binding，v2.4 AF-02 修订）。

背景（v2.4 AF-02，merge blocker）：v2.4 P1-A 曾把「reviewer 一律删除
frontmatter model 行、继承宿主/会话模型」作为统一规则，以规避未配置
provider 宿主上的 account-connection-unavailable 硬失败（W0 §13.1 实测）。
该统一规则对文本审查成立、对视觉审查不成立：visual-reviewer 契约要求
直接读取截图（多模态），而本项目将普通主会话模型 GLM-5.3 视为纯文本
——继承会「启动成功但丧失契约能力」。统一删除规则已废止，代之以
文本/视觉分治：

  - glm-reviewer（文本审查）：frontmatter 无 model 字段，继承宿主/会话
    模型以保证任何宿主可启动；模型身份不是持久任务状态轴——出现
    model 字段即回归，报文件与字段值；
  - visual-reviewer / visual-implementer（视觉角色）：frontmatter 必须
    是 provider 全限定 account:<套餐标识>/<模型> 且以 /GLM-5.3-Flash
    结尾（主会话已验证的多模态 Flash 绑定，与 visual-implementer 现有
    绑定一致）；绑定不可用的宿主上视觉高保障路线 fail closed（向用户
    报告绑定不可用），绝不静默退回纯文本模型——裸 ID（ZCode 3.12.3+
    会静默回退主会话模型）或以纯文本主模型 /GLM-5.3 结尾均视为回归；
  - visual-reviewer 工具白名单保持只读（fresh-context + 只读隔离仍是
    独立性来源，视觉绑定只恢复能力，不改隔离契约）。

W6（v2.4 Phase 2 P2-F）：flash-implementer 已随 v2.3 执行面退役删除，
不再在本测试覆盖面内——标准实施路由由主会话经原生 Workflow 承担。
"""

import os
import re
import unittest

AGENTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "plugins", "glm-conductor", "agents",
)

# 文本审查者：继承宿主/会话模型（frontmatter 无 model 字段）
TEXT_REVIEWERS = ("glm-reviewer",)
# 视觉角色：必须多模态 GLM-5.3-Flash 绑定（两角色不得再共用继承规则）
VISUAL_ROLES = ("visual-implementer", "visual-reviewer")
FLASH_MODEL_SUFFIX = "/GLM-5.3-Flash"
TEXT_MAIN_MODEL_SUFFIX = "/GLM-5.3"

QUALIFIED_MODEL_RE = re.compile(r"^account:[^/\s]+/\S+$")

# visual-reviewer 只读工具白名单的禁用工具（出现即视为只读契约回归）
VISUAL_REVIEWER = "visual-reviewer"
VISUAL_REVIEWER_FORBIDDEN_TOOLS = ("Write", "Edit", "Bash", "NotebookEdit")


def _parse_frontmatter_field(path, key):
    """从 agents/<name>.md frontmatter 提取单行 `key: value` 字段值（去引号）。

    不引入 YAML 依赖：frontmatter 由 `---` 包裹、字段为单行
    `key: value`，与 scripts/validate_plugin.py 的解析假设一致。
    字段缺失时返回 None——对 glm-reviewer 的 model 字段这是合法态
    （继承宿主/会话模型），是否合法由调用方按角色断言。
    """
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    if not lines or lines[0].strip() != "---":
        raise AssertionError("frontmatter 起始行缺失: %s" % path)
    prefix = key + ":"
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if stripped.startswith(prefix):
            value = stripped[len(prefix):].strip().strip('"').strip("'")
            return value
    return None


class TextReviewerModelBindingTest(unittest.TestCase):
    """glm-reviewer：继承宿主/会话模型是文本审查的既定契约。"""

    def test_text_reviewer_inherits_session_model(self):
        for name in TEXT_REVIEWERS:
            path = os.path.join(AGENTS_DIR, "%s.md" % name)
            self.assertTrue(os.path.isfile(path), "%s.md 缺失" % name)
            value = _parse_frontmatter_field(path, "model")
            self.assertIsNone(
                value,
                "%s 的 frontmatter 含 model 字段（值 %s）：文本审查者的既定"
                "契约是继承宿主/会话模型（模型身份不是持久任务状态轴）；"
                "provider 全限定绑定在未配置该 provider 的宿主上会 "
                "account-connection-unavailable 硬失败" % (path, value))


class VisualRoleMultimodalBindingTest(unittest.TestCase):
    """视觉角色：必须显式绑定已验证的多模态 GLM-5.3-Flash，fail closed。"""

    def test_visual_roles_have_provider_qualified_flash_binding(self):
        for name in VISUAL_ROLES:
            path = os.path.join(AGENTS_DIR, "%s.md" % name)
            self.assertTrue(os.path.isfile(path), "%s.md 缺失" % name)
            value = _parse_frontmatter_field(path, "model")
            self.assertIsNotNone(
                value,
                "%s 的 frontmatter 缺 model 字段：视觉角色必须显式绑定"
                "多模态 GLM-5.3-Flash，继承主会话模型会启动成功但丧失"
                "读图契约能力" % name)
            self.assertRegex(
                value, QUALIFIED_MODEL_RE,
                "%s 的 model 必须是 provider 全限定 account:<套餐>/<模型>；"
                "裸 ID 在 ZCode 3.12.3+ 会静默回退主会话模型" % name)
            self.assertTrue(
                value.endswith(FLASH_MODEL_SUFFIX),
                "%s 的 model 应以 %s 结尾，实际 %s"
                % (name, FLASH_MODEL_SUFFIX, value))

    def test_visual_roles_do_not_resolve_to_text_main_model(self):
        for name in VISUAL_ROLES:
            path = os.path.join(AGENTS_DIR, "%s.md" % name)
            self.assertTrue(os.path.isfile(path), "%s.md 缺失" % name)
            value = _parse_frontmatter_field(path, "model") or ""
            self.assertTrue(
                value.startswith("account:"),
                "%s 的 model=%s 不是 provider 全限定绑定：裸 ID 会静默解析"
                "为纯文本主会话模型" % (name, value or "（缺失）"))
            self.assertFalse(
                value.endswith(TEXT_MAIN_MODEL_SUFFIX),
                "%s 的 model=%s 以 %s 结尾：纯文本主模型不满足视觉角色的"
                "多模态契约" % (name, value, TEXT_MAIN_MODEL_SUFFIX))

    def test_visual_reviewer_tools_remain_read_only(self):
        path = os.path.join(AGENTS_DIR, "%s.md" % VISUAL_REVIEWER)
        self.assertTrue(os.path.isfile(path), "%s.md 缺失" % VISUAL_REVIEWER)
        raw = _parse_frontmatter_field(path, "tools")
        self.assertIsNotNone(raw, "%s 缺少 tools 字段" % VISUAL_REVIEWER)
        tools = [token.strip() for token in raw.split(",") if token.strip()]
        offenders = [t for t in tools if t in VISUAL_REVIEWER_FORBIDDEN_TOOLS]
        self.assertEqual(
            [], offenders,
            "%s 的工具白名单含写入/执行类工具 %s：视觉审查者必须保持"
            "只读白名单" % (VISUAL_REVIEWER, offenders))


if __name__ == "__main__":
    unittest.main()
