#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agents/*.md 模型绑定格式测试（unit v232-agent-model-binding）。

ZCode 3.12.3（2026-09-16 起安装）重构模型管理：provider 体系由
builtin:bigmodel-coding-plan 切换为 account:<套餐标识>，agent
frontmatter 的裸模型 ID（model: GLM-5.3-Flash）不再可解析，宿主
不报错、静默回退主会话模型（2026-09-17 至 09-18 生产实测 428 次
flash 系派发全部落在旗舰 GLM-5.3 上，含 visual 系失去多模态判定
的结构性风险）。本测试机械锚定 v2.3.2 起的 provider 全限定写法：

  - 四个 agent 的 model 字段必须形如 account:<套餐标识>/<模型>
    （裸 ID 即回归）；
  - 各角色模型后缀对照 role-contracts.md 的「模型」行：
    flash-implementer / visual-implementer / visual-reviewer →
    /GLM-5.3-Flash（实施 + 多模态），glm-reviewer → /GLM-5.3
    （纯文本审查）。
"""

import os
import re
import unittest

AGENTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "plugins", "glm-conductor", "agents",
)

EXPECTED_MODEL_SUFFIX = {
    "flash-implementer": "/GLM-5.3-Flash",
    "visual-implementer": "/GLM-5.3-Flash",
    "visual-reviewer": "/GLM-5.3-Flash",
    "glm-reviewer": "/GLM-5.3",
}

QUALIFIED_MODEL_RE = re.compile(r"^account:[^/\s]+/\S+$")


def _parse_frontmatter_model(path):
    """从 agents/<name>.md frontmatter 提取 model 字段值（去引号）。

    不引入 YAML 依赖：frontmatter 由 `---` 包裹、字段为单行
    `key: value`，与 scripts/validate_plugin.py 的解析假设一致。
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
    raise AssertionError("model 字段缺失: %s" % path)


class AgentModelBindingTest(unittest.TestCase):
    def test_all_agents_use_provider_qualified_model(self):
        for name in sorted(EXPECTED_MODEL_SUFFIX):
            path = os.path.join(AGENTS_DIR, "%s.md" % name)
            self.assertTrue(os.path.isfile(path), "%s.md 缺失" % name)
            value = _parse_frontmatter_model(path)
            self.assertRegex(
                value, QUALIFIED_MODEL_RE,
                "%s 的 model 必须是 provider 全限定 account:<套餐>/<模型>；"
                "裸 ID 在 ZCode 3.12.3+ 会静默回退主会话模型" % name)

    def test_role_model_suffixes(self):
        for name, suffix in sorted(EXPECTED_MODEL_SUFFIX.items()):
            path = os.path.join(AGENTS_DIR, "%s.md" % name)
            value = _parse_frontmatter_model(path)
            self.assertTrue(
                value.endswith(suffix),
                "%s 的 model 应以 %s 结尾，实际 %s" % (name, suffix, value))


if __name__ == "__main__":
    unittest.main()
