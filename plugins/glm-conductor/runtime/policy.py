#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 Bash 策略引擎（Route-aware Permission Policy，B7.1）。

职责（升级指南 §57-§59）：
    action × resource → allow / ask / deny 的极简表驱动策略：对主会话
    Bash 工具调用的整条命令文本做 IGNORECASE 正则搜索，返回三值决策
    与命中的规则名。规则表是模块常量、逐条可检视，不做复杂 DSL
    （§59 明确禁止 DSL）。

覆盖面：
    - 本层只覆盖 Bash 的主会话调用。Phase 0 实测：PreToolUse 钩子只在
      主会话触发（子代理工具调用不触发），因此策略层管理的是主会话
      Bash 工具调用；角色级 deny（reviewer 等）由 agent 工具白名单
      负责，不在本层。
    - 已知取舍：正则对整条命令文本搜索，字符串中引用的破坏性命令文本
      （如 echo 'rm -rf'）也会被拒——保守误报可接受（beta1 登记），
      宁误报不漏报。
    - 门控前置条件（evaluate）：非 Bash 工具不在策略面；无活动任务时
      策略零干预（对齐 v2「无状态零干预」哲学）。

规则扫描顺序：
    deny 规则全部扫完再扫 ask 规则（deny 先于 ask），因此 deny 与 ask
    同时命中时取 deny；同组内按表声明顺序取首个命中。

deny / ask 语义：
    - deny 规则：活动任务存在时恒拒（无视 assurance）；
    - ask 规则：仅活动任务且 assurance=high 时升级为 ask，standard
      保障任务不升级（allow，reason 注明）。

来源：
    docs/history/v2.0/glm-conductor-v2-upgrade-guide-final.md §57-§59
    （Route-aware Permission Policy）+ 实施计划工作块 B7.1/B7.2。

依赖：
    仅 Python 3.7 标准库（re），零第三方依赖，`python3 -S` 可运行。
"""

import re

# 三值决策词汇（evaluate 返回的 decision 取值域）
DECISIONS = ("allow", "ask", "deny")

# —— 规则表（模块常量；正则对整条命令 IGNORECASE 搜索；deny 先于 ask） ——

# deny：活动任务存在时恒拒（破坏性 / 不可逆操作）
# 表项：(rule 名, 正则, 中文含义)
DENY_RULES = (
    ("rm-destructive",
     r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+",
     "rm 携带 r/f 递归强制标志"),
    ("git-reset-hard",
     r"\bgit\s+reset\s+--hard\b",
     "git reset --hard 硬重置"),
    ("git-clean-force",
     r"\bgit\s+clean\s+-[a-zA-Z]*f",
     "git clean 强制清理"),
    # 精确修正项（相对工作块规格的逐字正则，实证依据见本条注释）：
    # 规格原式为 `\bgit\s+push\b[^|;&]*\b(--force\b|\s-f\b)`。其前导 \b
    # 要求 "--force" 首字符 '-' 之前是词字符，而命令中 "--force" 前恒为
    # 空格（空格与 '-' 均为非词字符，不成界），实测（python3 re，
    # IGNORECASE）规格原式对规格自带正例 "git push origin main --force"
    # 与 "git push --force" 均不命中；且裸 `--force\b` 会把可控变体
    # --force-with-lease（"force" 与 "-with" 之间恰有词边界）误判为强推。
    # 修正：去掉前导 \b + (?!-) 排除 --force-with-lease——强推命中、
    # 可控变体不拒（归 ask 的 git-push），与规格正例矩阵及表注完全一致。
    ("git-push-force",
     r"\bgit\s+push\b[^|;&]*(--force\b(?!-)|\s-f\b)",
     "git push 强制推送"),
)

# ask：仅活动任务且 assurance=high 时升级为 ask，否则 allow
ASK_RULES = (
    ("git-push",
     r"\bgit\s+push\b",
     "git push 推送远端"),
    ("schema-migration",
     r"\b(alembic\s+(upgrade|downgrade|stamp)"
     r"|prisma\s+migrate\b[^|;&]*"
     r"|manage\.py\s+migrate\b"
     r"|knex\s+migrate\b)",
     "数据库模式迁移"),
    ("release-ops",
     r"\b(npm\s+publish\b|cargo\s+publish\b"
     r"|git\s+push\s+--tags\b"
     r"|gh\s+release\s+create\b)",
     "发布操作"),
    ("permission-change",
     r"\b(chmod\b|chown\b|icacls\b|attrib\b)",
     "文件权限变更"),
)

# 扫描组：deny 组在前（deny 先于 ask，多命中取 deny）
_GROUPS = (
    ("deny", DENY_RULES),
    ("ask", ASK_RULES),
)

# 导入期一次性编译（IGNORECASE：正则对整条命令大小写不敏感搜索）
_COMPILED = tuple(
    (decision, tuple(
        (name, re.compile(pattern, re.IGNORECASE))
        for name, pattern, _desc in rules))
    for decision, rules in _GROUPS)


def classify_bash(command) -> "tuple[str, str] | None":
    """对整条命令文本做策略分类，返回 (decision, rule_name) 或 None。

    - command 非 str → ValueError（中文消息）；
    - deny 规则全部扫完再扫 ask 规则（deny 先于 ask，多命中取 deny），
      组内按表声明顺序取首个命中；
    - 无任何命中 → None。
    """
    if not isinstance(command, str):
        raise ValueError(
            "classify_bash：command 必须是字符串，得到 %s"
            % type(command).__name__)
    for decision, rules in _COMPILED:
        for name, pattern in rules:
            if pattern.search(command):
                return (decision, name)
    return None


def evaluate(*, tool, command=None, active_task=False,
             assurance_high=False) -> dict:
    """策略门控评估，返回 {"decision", "rule", "reason"}（reason 中文一句话）。

    门控顺序：
      1. tool 严格等于 "Bash" 才管理；其他工具 → allow（不在策略面）；
      2. active_task=False → allow（无活动任务，策略零干预）；
      3. classify deny 命中 → deny（无视 assurance）；
      4. classify ask 命中且 assurance_high=True → ask；ask 命中但
         standard → allow（reason 注明「标准保障任务不升级」）；
      5. 未命中 → allow（默认放行）。

    容错：command 非 str（含 None）时不进入 classify（策略层无文本可
    分类，按未命中放行；分类层的 ValueError 语义保留给直接调用方）。
    """
    if tool != "Bash":
        return {"decision": "allow", "rule": None,
                "reason": "非 Bash 工具不在策略面"}
    if not active_task:
        return {"decision": "allow", "rule": None,
                "reason": "无活动任务，策略零干预"}
    hit = classify_bash(command) \
        if isinstance(command, str) else None
    if hit is not None:
        decision, rule = hit
        if decision == "deny":
            return {"decision": "deny", "rule": rule,
                    "reason": "命中拒绝规则 %s：活动任务期间破坏性命令恒拒"
                              % rule}
        if assurance_high:
            return {"decision": "ask", "rule": rule,
                    "reason": "命中升级规则 %s：assurance=high 升级为 ask"
                              % rule}
        return {"decision": "allow", "rule": rule,
                "reason": "命中规则 %s：标准保障任务不升级，allow 放行"
                          % rule}
    return {"decision": "allow", "rule": None, "reason": "默认放行"}
