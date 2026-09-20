#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agents/*.md 模型绑定格式测试（unit v232-agent-model-binding，v2.4 P1-A/W6 修订）。

背景（v2.4 Phase 1，W0 §13.1 实测）：agent frontmatter 的 provider 全限定
model 字段（account:<套餐标识>/<模型>）在未配置该 provider 的宿主上会让
普通 Agent 面以 account-connection-unavailable 硬失败（不回退），两个
reviewer 代理因此无法启动。v2.4 起两类角色采用不同契约：

  - reviewer（glm-reviewer / visual-reviewer）删除 frontmatter model 行，
    改为继承宿主/会话模型以保证任何宿主可启动；模型身份不再是持久任务
    状态轴（诊断时可记录实际运行模型）——frontmatter 出现 model 字段即
    回归，报文件与字段值；
  - 实施者 visual-implementer 保留 provider 全限定写法（视觉角色路由
    依赖 GLM-5.3-Flash），model 字段必须形如
    account:<套餐标识>/<模型> 且以 /GLM-5.3-Flash 结尾（角色后缀）。

W6（v2.4 Phase 2 P2-F）：flash-implementer 已随 v2.3 执行面退役删除，
不再在本测试覆盖面内——标准实施路由由主会话经原生 Workflow 承担，
不再绑定单一实施者代理（TODO(Phase 4)：技能与 agent 面全面重写时
再定实施者代理契约）。
"""

import os
import re
import unittest

AGENTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "plugins", "glm-conductor", "agents",
)

REVIEWERS = ("glm-reviewer", "visual-reviewer")

IMPLEMENTERS = ("visual-implementer",)
IMPLEMENTER_MODEL_SUFFIX = "/GLM-5.3-Flash"

QUALIFIED_MODEL_RE = re.compile(r"^account:[^/\s]+/\S+$")


def _parse_frontmatter_model(path):
    """从 agents/<name>.md frontmatter 提取 model 字段值（去引号）。

    不引入 YAML 依赖：frontmatter 由 `---` 包裹、字段为单行
    `key: value`，与 scripts/validate_plugin.py 的解析假设一致。
    model 字段缺失时返回 None——对 reviewer 这是 v2.4 P1-A 起的合法态
    （继承宿主/会话模型），是否合法由调用方按角色断言。
    """
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    if not lines or lines[0].strip() != "---":
        raise AssertionError("frontmatter 起始行缺失: %s" % path)
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if stripped.startswith("model:"):
            value = stripped[len("model:"):].strip().strip('"').strip("'")
            return value
    return None


class AgentModelBindingTest(unittest.TestCase):
    def test_reviewers_have_no_model_field(self):
        for name in REVIEWERS:
            path = os.path.join(AGENTS_DIR, "%s.md" % name)
            self.assertTrue(os.path.isfile(path), "%s.md 缺失" % name)
            value = _parse_frontmatter_model(path)
            self.assertIsNone(
                value,
                "%s 的 frontmatter 含 model 字段（值 %s）：v2.4 P1-A 起 "
                "reviewer 必须继承宿主/会话模型；provider 全限定绑定在未"
                "配置该 provider 的宿主上会 account-connection-unavailable "
                "硬失败" % (path, value))

    def test_implementers_provider_qualified_flash_suffix(self):
        for name in IMPLEMENTERS:
            path = os.path.join(AGENTS_DIR, "%s.md" % name)
            self.assertTrue(os.path.isfile(path), "%s.md 缺失" % name)
            value = _parse_frontmatter_model(path)
            self.assertIsNotNone(
                value,
                "%s 的 model 字段缺失：实施者必须保留 provider 全限定"
                "模型绑定" % name)
            self.assertRegex(
                value, QUALIFIED_MODEL_RE,
                "%s 的 model 必须是 provider 全限定 account:<套餐>/<模型>；"
                "裸 ID 在 ZCode 3.12.3+ 会静默回退主会话模型" % name)
            self.assertTrue(
                value.endswith(IMPLEMENTER_MODEL_SUFFIX),
                "%s 的 model 应以 %s 结尾，实际 %s"
                % (name, IMPLEMENTER_MODEL_SUFFIX, value))


if __name__ == "__main__":
    unittest.main()
