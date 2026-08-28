#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor 插件静态校验器（整改规范 §25/§26）。

用途：
    对本仓库做纯静态一致性检查，不联网、不调用模型、不运行 benchmark。
    检查项编号与整改规范对应：
      1. JSON 解析：marketplace.json 与
         plugins/glm-conductor/.zcode-plugin/plugin.json 均为合法 JSON 且为对象；
      2. Agent frontmatter：plugins/glm-conductor/agents/*.md 必含
         name / description / model / thoughtLevel / tools；
      3. Skill frontmatter：plugins/glm-conductor/skills/*/SKILL.md 必含
         name / description；
      4. 引用 agent 存在：plugins/ 内出现的 `glm-conductor:<agent>` 引用
         （flash-implementer / visual-implementer / glm-reviewer /
         visual-reviewer），对应 agents/<agent>.md 必须存在；`/glm-conductor:
         <name>` 斜杠命令引用形式不算 agent 引用；
      5. 命名一致性：plugins/、marketplace.json、README.md、
         docs/architecture.md 中不得出现旧名 `glm-advisor`；
      6. 禁词：同上范围内不得出现 `升级信号` / `上报升级` / `升级路由`；
      7. Continuity 安全（扫描范围同 5/6）：
         a) quota API 名称（getQuotaRemaining / getQuotaResetTime /
            onQuotaReset）只允许出现在否定式声明行（同行含 `虚构` 或
            `不存在`）；
         b) 先移除允许的任务专属 checkpoint 路径
            （.glm-conductor/tasks/<task-id>/checkpoint.md 与
            .glm-conductor/tasks/<id>/checkpoint.md），再检查不得残留
            workspace-global 路径 .glm-conductor/checkpoint.md；
         c) 「无 quota API」绝对化措辞在 README.md / docs/architecture.md /
            continuity SKILL.md / long-horizon.md 四文件中零命中
            （v2 alpha3 quota 感知上线后的措辞反转防回潮）；
      8. 视觉协议一致性：VISUAL_CAPTURE_REQUEST 与 VISUAL ACCEPTANCE 必须出
         现在 agents/visual-implementer.md，VISUAL_CAPTURE_REQUEST 必须出现
         在 skills/orchestration/references/role-contracts.md；VISUAL REVIEW
         必须出现在 agents/visual-reviewer.md 与 role-contracts.md；
         agents/visual-reviewer.md 中不得出现 `GLM REVIEW`；修正前的旧名
         VISUAL_ACCEPT_REQUEST 不得出现在扫描范围内；
      9. 权威架构文档契约：docs/architecture.md 必须存在；必含
         VISUAL_CAPTURE_REQUEST / TASK_ID / .glm-conductor/tasks/ /
         ROUTE REASSESSMENT / VISUAL REVIEW / 随机十六进制后缀 /
         solo / delegate / audit / full / foreground / resumable / idle；
         禁含 风险阶梯 / risk ladder / glm-advisor（均大小写不敏感）；
         `最新 checkpoint` / `latest checkpoint` 只允许出现在含否定词
         （不 / 禁止 / never / not）的行；
     10. 任务标识：TASK_ID 必含于
         skills/continuity/SKILL.md、
         skills/continuity/references/long-horizon.md 与
         docs/architecture.md；`随机十六进制后缀` 必含于 long-horizon.md
         与 docs/architecture.md（「语义前缀+随机后缀」机械唯一格式）；
     11. 视觉规范拓扑：VISUAL_ROUND 必含于 agents/visual-implementer.md、
         skills/orchestration/references/operations.md、
         skills/orchestration/references/role-contracts.md 与
         docs/architecture.md；`规范路径` 与 `完整状态` 必须同时含于
         前三个文件（各自都要同时含两个标记）；`单次调用内等待` /
         `同一调用内等待` 只允许出现在含否定词（不 / 禁止）的行
         （在上述四个文件范围内检查）；
     12. 版本一致性：plugins/glm-conductor/.zcode-plugin/plugin.json 的
         version 字段必须等于 CHANGELOG.md 第一个 `## ` 标题行中的版本记号；
     13. 钩子清单完整性：plugins/glm-conductor/hooks/hooks.json 存在、合法
         JSON、顶层为对象且含非空 `hooks` 对象；事件键限于 SessionStart /
         UserPromptSubmit / PreToolUse / PostToolUse / PermissionRequest /
         Stop，且必须含 Stop 事件键（v2 完成门要求声明 Stop 钩子，缺失即
         FAIL）；每个事件值为数组，数组每项为对象且含 `hooks` 数组；内层
         hook 对象 type 限于 process / command，type=process 时 command
         必须为 python3，timeoutMs 若存在必须为 >0 的整数，matcher 若存在
         必须为字符串，args 中以 ${ZCODE_PLUGIN_ROOT}/ 开头的路径替换为
         插件根 plugins/glm-conductor 后必须实际存在；
     14. runtime 状态层与技能契约标记：plugins/glm-conductor/runtime/ 下的
         __init__.py / state.py / journal.py / ownership.py、hooks/ 下的
         stop_gate.py / pre_tool_use.py、tests/ 下的 test_state.py /
         test_journal.py / test_ownership.py 均存在且非空；runtime/state.py
         必含 TASK_ID_KEYS / CONTINUITY_ID（legacy 归一证据）/
         TERMINAL_STATUSES，runtime/journal.py 必含 RECOMMENDED_EVENTS /
         events.jsonl，runtime/ownership.py 必含 classify_paths /
         git_touched_files，hooks/stop_gate.py 必含 gate_blocked /
         gate_passed / gate_exhausted（Layer A 记账词汇），
         hooks/pre_tool_use.py 必含 additionalContext（Layer B 注入）；
         skills/continuity/SKILL.md 必含 state.json / events.jsonl /
         task_created / Quota-Aware Scheduling / GLM_CONDUCTOR_QUOTA_API_KEY，
         skills/continuity/references/long-horizon.md 必含
         state.json / events.jsonl，skills/orchestration/SKILL.md 必含
         state.json / route_selected，skills/enforcement/SKILL.md 必含
         ENFORCEMENT DEGRADED / gate_exhausted / Layer A / Layer B；
         runtime/quota/ 下的 parser / provider / _http / zai / bigmodel /
         scheduler / credentials / report 八模块、commands/quota.md 与
         tests/ 下五个 test_quota*.py 均存在且非空，scheduler 必含
         evaluate / plan_resume / PRESSURE / EXHAUSTED，_http 必含
         ALLOWED_HOSTS / malformed，credentials 必含
         GLM_CONDUCTOR_QUOTA_API_KEY / builtin:bigmodel-coding-plan，
         report 必含 --json / unavailable。

    扫描范围说明：检查 5/6/7/8（及 8 内的旧名负向检查）的扫描范围是显式
    列表——plugins/ 全部文件 + marketplace.json + README.md +
    docs/architecture.md。docs/history/ 与 docs/ 下其他文件不在该显式列表
    中，天然不被扫描，无需目录排除逻辑。

用法：
    python3 scripts/validate_plugin.py
    （亦可 `python3 -S scripts/validate_plugin.py`，纯标准库无需 site。）
    脚本从自身位置（<repo>/scripts/）向上定位仓库根，可在任意工作目录运行。
    逐项输出 PASS/FAIL 与摘要：全部通过退出码 0，任一失败退出码 1。

依赖：
    仅 Python 3 标准库（glob / io / json / os / re / sys），零第三方依赖。
    frontmatter 用 `---` 分隔 + `key: value` 行解析，不要求完整 YAML 语义。
"""

import glob
import io
import json
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)

MARKETPLACE_JSON = os.path.join(REPO_ROOT, "marketplace.json")
PLUGIN_JSON = os.path.join(
    REPO_ROOT, "plugins", "glm-conductor", ".zcode-plugin", "plugin.json")
PLUGINS_DIR = os.path.join(REPO_ROOT, "plugins")
AGENTS_DIR = os.path.join(REPO_ROOT, "plugins", "glm-conductor", "agents")
SKILLS_DIR = os.path.join(REPO_ROOT, "plugins", "glm-conductor", "skills")
README = os.path.join(REPO_ROOT, "README.md")
ROLE_CONTRACTS = os.path.join(
    REPO_ROOT, "plugins", "glm-conductor", "skills", "orchestration",
    "references", "role-contracts.md")
VISUAL_IMPLEMENTER = os.path.join(AGENTS_DIR, "visual-implementer.md")
VISUAL_REVIEWER = os.path.join(AGENTS_DIR, "visual-reviewer.md")
ARCH_DOC = os.path.join(REPO_ROOT, "docs", "architecture.md")
CHANGELOG = os.path.join(REPO_ROOT, "CHANGELOG.md")
CONTINUITY_SKILL = os.path.join(SKILLS_DIR, "continuity", "SKILL.md")
LONG_HORIZON = os.path.join(
    SKILLS_DIR, "continuity", "references", "long-horizon.md")
OPERATIONS = os.path.join(
    SKILLS_DIR, "orchestration", "references", "operations.md")

AGENT_REQUIRED_KEYS = ("name", "description", "model", "thoughtLevel", "tools")
SKILL_REQUIRED_KEYS = ("name", "description")

# 整改规范点名的四个契约 agent；与 plugins/ 内实际引用取并集后逐一要求存在
CANONICAL_AGENTS = (
    "flash-implementer", "visual-implementer", "glm-reviewer", "visual-reviewer")

# agent 引用；`/glm-conductor:<name>`（斜杠命令引用，如 /glm-conductor:quota）
# 指向 commands/<name>.md 而非 agents/<name>.md，用负向后顾排除
AGENT_REF_RE = re.compile(r"(?<!/)glm-conductor:([A-Za-z0-9_][A-Za-z0-9_-]*)")
QUOTA_TOKENS = ("getQuotaRemaining", "getQuotaResetTime", "onQuotaReset")
QUOTA_NEGATIONS = ("虚构", "不存在")
# 7c：v2 alpha3 quota 感知上线后的措辞反转——「无 quota API」绝对化措辞
# 在四个权威文档中必须零命中（「无原生 quota API」不含该子串，允许存在）
QUOTA_ABSOLUTE_PHRASE = "无 quota API"
QUOTA_ABSOLUTE_FILES = (README, ARCH_DOC, CONTINUITY_SKILL, LONG_HORIZON)
# 允许的任务专属 checkpoint 路径：<task-id> / <id> 均为单段占位
ALLOWED_CHECKPOINT_RE = re.compile(
    r"\.glm-conductor[/\\]tasks[/\\][^/\\\s\"'`]+[/\\]checkpoint\.md")
GLOBAL_CHECKPOINT_RE = re.compile(r"\.glm-conductor[/\\]checkpoint\.md")

OLD_PLUGIN_NAME = "glm-advisor"
OLD_VISUAL_NAME = "VISUAL_ACCEPT_REQUEST"
FORBIDDEN_WORDS = ("升级信号", "上报升级", "升级路由")

# 检查 5/6/7（及 8 的旧名负向检查）使用的扫描范围：plugins/ 全部文件 +
# marketplace.json + README.md + docs/architecture.md（显式列表；docs/history/
# 与 docs/ 下其他文件不在列表内，天然不被扫描）
SCAN_EXTRA_FILES = (MARKETPLACE_JSON, README, ARCH_DOC)
# 扫描范围的人类可读描述（仅用于 PASS 明细文案，与实际范围保持同步）
SCAN_SCOPE_DESC = (
    "plugins/ + marketplace.json + README.md + docs/architecture.md")

# —— 检查 9：权威架构文档契约（docs/architecture.md） ——
ARCH_REQUIRED_MARKERS = (
    "VISUAL_CAPTURE_REQUEST",
    "TASK_ID",
    ".glm-conductor/tasks/",
    "ROUTE REASSESSMENT",
    "VISUAL REVIEW",
    "随机十六进制后缀",
    "solo / delegate / audit / full",
    "foreground / resumable / idle",
)
ARCH_FORBIDDEN_CI = ("风险阶梯", "risk ladder", "glm-advisor")  # 大小写不敏感
# `最新 checkpoint`/`latest checkpoint` 只允许出现在含否定词的行（英文词大小写不敏感）
CHECKPOINT_REF_TOKENS = ("最新 checkpoint", "latest checkpoint")
CHECKPOINT_REF_NEGATIONS = ("不", "禁止", "never", "not")

# —— 检查 10：任务标识 ——
TASK_ID_FILES = (CONTINUITY_SKILL, LONG_HORIZON, ARCH_DOC)
HEX_SUFFIX_FILES = (LONG_HORIZON, ARCH_DOC)

# —— 检查 11：视觉规范拓扑 ——
VISUAL_TOPOLOGY_FILES = (VISUAL_IMPLEMENTER, OPERATIONS, ROLE_CONTRACTS)
VISUAL_ROUND_FILES = VISUAL_TOPOLOGY_FILES + (ARCH_DOC,)
VISUAL_TOPOLOGY_REQUIRED = ("规范路径", "完整状态")
VISUAL_WAIT_TOKENS = ("单次调用内等待", "同一调用内等待")
VISUAL_WAIT_NEGATIONS = ("不", "禁止")

# —— 检查 12：CHANGELOG 版本标题（第一个 `^## ` 行） ——
CHANGELOG_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")

# —— 检查 13：钩子清单完整性（hooks/hooks.json） ——
HOOKS_MANIFEST = os.path.join(
    REPO_ROOT, "plugins", "glm-conductor", "hooks", "hooks.json")
PLUGIN_ROOT_REL = "plugins/glm-conductor"  # ${ZCODE_PLUGIN_ROOT} 对应的插件根
PLUGIN_ROOT_PREFIX = "${ZCODE_PLUGIN_ROOT}/"
ALLOWED_HOOK_EVENTS = (
    "SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
    "PermissionRequest", "Stop")
ALLOWED_HOOK_TYPES = ("process", "command")
HOOK_COMMAND = "python3"

# —— 检查 14：runtime 状态层与技能契约标记 ——
RUNTIME_DIR = os.path.join(REPO_ROOT, "plugins", "glm-conductor", "runtime")
RUNTIME_INIT = os.path.join(RUNTIME_DIR, "__init__.py")
STATE_PY = os.path.join(RUNTIME_DIR, "state.py")
JOURNAL_PY = os.path.join(RUNTIME_DIR, "journal.py")
OWNERSHIP_PY = os.path.join(RUNTIME_DIR, "ownership.py")
FINGERPRINT_PY = os.path.join(RUNTIME_DIR, "fingerprint.py")
HOOKS_DIR = os.path.join(REPO_ROOT, "plugins", "glm-conductor", "hooks")
STOP_GATE_PY = os.path.join(HOOKS_DIR, "stop_gate.py")
PRE_TOOL_USE_PY = os.path.join(HOOKS_DIR, "pre_tool_use.py")
ENFORCEMENT_SKILL = os.path.join(SKILLS_DIR, "enforcement", "SKILL.md")
TESTS_DIR = os.path.join(REPO_ROOT, "tests")
TEST_STATE = os.path.join(TESTS_DIR, "test_state.py")
TEST_JOURNAL = os.path.join(TESTS_DIR, "test_journal.py")
TEST_OWNERSHIP = os.path.join(TESTS_DIR, "test_ownership.py")
TEST_FINGERPRINT = os.path.join(TESTS_DIR, "test_fingerprint.py")
TEST_STOP_GATE = os.path.join(TESTS_DIR, "test_stop_gate.py")
TEST_PRE_TOOL_USE = os.path.join(TESTS_DIR, "test_pre_tool_use.py")
QUOTA_DIR = os.path.join(RUNTIME_DIR, "quota")
QUOTA_PARSER_PY = os.path.join(QUOTA_DIR, "parser.py")
QUOTA_PROVIDER_PY = os.path.join(QUOTA_DIR, "provider.py")
QUOTA_HTTP_PY = os.path.join(QUOTA_DIR, "_http.py")
QUOTA_ZAI_PY = os.path.join(QUOTA_DIR, "zai.py")
QUOTA_BIGMODEL_PY = os.path.join(QUOTA_DIR, "bigmodel.py")
QUOTA_SCHEDULER_PY = os.path.join(QUOTA_DIR, "scheduler.py")
QUOTA_CREDENTIALS_PY = os.path.join(QUOTA_DIR, "credentials.py")
QUOTA_REPORT_PY = os.path.join(QUOTA_DIR, "report.py")
QUOTA_COMMAND = os.path.join(
    REPO_ROOT, "plugins", "glm-conductor", "commands", "quota.md")
TEST_QUOTA_PARSER = os.path.join(TESTS_DIR, "test_quota_parser.py")
TEST_QUOTA_ADAPTERS = os.path.join(TESTS_DIR, "test_quota_adapters.py")
TEST_QUOTA_SCHEDULER = os.path.join(TESTS_DIR, "test_quota_scheduler.py")
TEST_QUOTA_CREDENTIALS = os.path.join(TESTS_DIR, "test_quota_credentials.py")
TEST_QUOTA_REPORT = os.path.join(TESTS_DIR, "test_quota_report.py")
ORCHESTRATION_SKILL = os.path.join(SKILLS_DIR, "orchestration", "SKILL.md")
RUNTIME_REQUIRED_FILES = (
    RUNTIME_INIT, STATE_PY, JOURNAL_PY, OWNERSHIP_PY, FINGERPRINT_PY,
    QUOTA_PARSER_PY, QUOTA_PROVIDER_PY, QUOTA_HTTP_PY, QUOTA_ZAI_PY,
    QUOTA_BIGMODEL_PY, QUOTA_SCHEDULER_PY, QUOTA_CREDENTIALS_PY,
    QUOTA_REPORT_PY)
LAYER_REQUIRED_FILES = (STOP_GATE_PY, PRE_TOOL_USE_PY)
TEST_REQUIRED_FILES = (
    TEST_STATE, TEST_JOURNAL, TEST_OWNERSHIP, TEST_FINGERPRINT,
    TEST_STOP_GATE, TEST_PRE_TOOL_USE,
    TEST_QUOTA_PARSER, TEST_QUOTA_ADAPTERS, TEST_QUOTA_SCHEDULER,
    TEST_QUOTA_CREDENTIALS, TEST_QUOTA_REPORT)
COMMAND_REQUIRED_FILES = (QUOTA_COMMAND,)
STATE_REQUIRED_MARKERS = (
    "TASK_ID_KEYS", "CONTINUITY_ID", "TERMINAL_STATUSES",
    "record_verification", "record_review", "visual_evidence")
JOURNAL_REQUIRED_MARKERS = ("RECOMMENDED_EVENTS", "events.jsonl")
OWNERSHIP_REQUIRED_MARKERS = ("classify_paths", "git_touched_files")
FINGERPRINT_REQUIRED_MARKERS = (
    "compute_fingerprint", "task_fingerprint", "visual_evidence_status")
STOP_GATE_REQUIRED_MARKERS = (
    "gate_blocked", "gate_passed", "gate_exhausted",
    "evaluate_task", "verification_stale", "review_stale")
PRE_TOOL_USE_REQUIRED_MARKERS = ("additionalContext",)
SKILL_CONTRACT_MARKERS = (
    (CONTINUITY_SKILL,
     ("state.json", "events.jsonl", "task_created", "task_fingerprint",
      "Quota-Aware Scheduling", "GLM_CONDUCTOR_QUOTA_API_KEY")),
    (LONG_HORIZON, ("state.json", "events.jsonl")),
    (ORCHESTRATION_SKILL, ("state.json", "route_selected", "task_fingerprint")),
    (ENFORCEMENT_SKILL,
     ("ENFORCEMENT DEGRADED", "gate_exhausted", "Layer A", "Layer B",
      "verification_stale", "review_stale")),
)


def rel_display(path):
    """仓库根相对路径，统一用 / 分隔（仅用于显示，路径判断仍走 os.path）。"""
    return os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")


def read_text(path):
    """读取 UTF-8 文本；OSError 时返回 None（binary 容错由 errors 处理）。"""
    try:
        with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def iter_plugins_files():
    """plugins/ 下所有文件的绝对路径（排序保证输出稳定）。"""
    for dirpath, dirnames, filenames in os.walk(PLUGINS_DIR):
        dirnames.sort()
        for fname in sorted(filenames):
            yield os.path.join(dirpath, fname)


def scan_scope_files():
    """检查 5/6/7 的扫描范围：plugins/ 全部文件 + SCAN_EXTRA_FILES 显式列表。"""
    for path in iter_plugins_files():
        yield path
    for path in SCAN_EXTRA_FILES:
        yield path


def parse_frontmatter(path):
    """解析 `---` 分隔的 frontmatter，返回 (meta, error)。

    只做 `key: value` 行解析（首个 `:` 切分），不要求完整 YAML 语义、
    不引入 PyYAML。解析失败时返回 (None, 错误说明)。
    """
    text = read_text(path)
    if text is None:
        return None, "无法读取文件"
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, "缺少起始 '---' frontmatter 分隔符"
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return None, "缺少结束 '---' frontmatter 分隔符"
    meta = {}
    for line in lines[1:end_idx]:
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, None


def check_1_json(results):
    """检查 1：两份 JSON 均合法且为对象。"""
    title = "JSON 解析（marketplace.json / plugin.json）"
    details = []
    ok = True
    for path in (MARKETPLACE_JSON, PLUGIN_JSON):
        shown = rel_display(path)
        if not os.path.isfile(path):
            details.append("FAIL: %s 不存在" % shown)
            ok = False
            continue
        try:
            with io.open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (ValueError, OSError) as exc:
            details.append("FAIL: %s 不是合法 JSON: %s" % (shown, exc))
            ok = False
            continue
        if not isinstance(data, dict):
            details.append("FAIL: %s 顶层不是 JSON 对象" % shown)
            ok = False
            continue
        details.append("PASS: %s 为合法 JSON 对象" % shown)
    results.append((1, title, ok, details))


def check_2_agent_frontmatter(results):
    """检查 2：agents/*.md frontmatter 必填字段。"""
    title = "Agent frontmatter 必填字段（agents/*.md）"
    details = []
    ok = True
    agent_files = sorted(glob.glob(os.path.join(AGENTS_DIR, "*.md")))
    if not agent_files:
        details.append("FAIL: agents/ 下未找到任何 .md 文件")
        results.append((2, title, False, details))
        return
    details.append("发现 %d 个 agent 文件" % len(agent_files))
    for path in agent_files:
        shown = rel_display(path)
        meta, error = parse_frontmatter(path)
        if error:
            details.append("FAIL: %s: %s" % (shown, error))
            ok = False
            continue
        missing = [key for key in AGENT_REQUIRED_KEYS if not meta.get(key)]
        if missing:
            details.append("FAIL: %s 缺少必填字段: %s" % (shown, ", ".join(missing)))
            ok = False
        else:
            details.append("PASS: %s（name=%s）" % (shown, meta.get("name")))
    results.append((2, title, ok, details))


def check_3_skill_frontmatter(results):
    """检查 3：skills/*/SKILL.md frontmatter 必填字段。"""
    title = "Skill frontmatter 必填字段（skills/*/SKILL.md）"
    details = []
    ok = True
    skill_files = sorted(glob.glob(os.path.join(SKILLS_DIR, "*", "SKILL.md")))
    if not skill_files:
        details.append("FAIL: skills/ 下未找到任何 SKILL.md")
        results.append((3, title, False, details))
        return
    details.append("发现 %d 个 SKILL.md" % len(skill_files))
    for path in skill_files:
        shown = rel_display(path)
        meta, error = parse_frontmatter(path)
        if error:
            details.append("FAIL: %s: %s" % (shown, error))
            ok = False
            continue
        missing = [key for key in SKILL_REQUIRED_KEYS if not meta.get(key)]
        if missing:
            details.append("FAIL: %s 缺少必填字段: %s" % (shown, ", ".join(missing)))
            ok = False
        else:
            details.append("PASS: %s（name=%s）" % (shown, meta.get("name")))
    results.append((3, title, ok, details))


def check_4_agent_refs(results):
    """检查 4：plugins/ 内 glm-conductor:<agent> 引用对应 agents/<agent>.md 存在。"""
    title = "引用 agent 存在（glm-conductor:<agent> → agents/<agent>.md）"
    details = []
    ok = True
    refs = {}
    for path in iter_plugins_files():
        text = read_text(path)
        if not text:
            continue
        shown = rel_display(path)
        for lineno, line in enumerate(text.splitlines(), 1):
            for match in AGENT_REF_RE.finditer(line):
                refs.setdefault(match.group(1), []).append("%s:%d" % (shown, lineno))
    details.append("plugins/ 内引用到 %d 个 agent: %s"
                   % (len(refs), ", ".join(sorted(refs)) or "（无）"))
    required = sorted(set(refs) | set(CANONICAL_AGENTS))
    for name in required:
        agent_path = os.path.join(AGENTS_DIR, name + ".md")
        if os.path.isfile(agent_path):
            details.append("PASS: agents/%s.md 存在" % name)
        else:
            where = ", ".join(refs.get(name, ["整改规范契约 agent 清单"]))
            details.append("FAIL: agents/%s.md 缺失（被引用/要求于 %s）" % (name, where))
            ok = False
    results.append((4, title, ok, details))


def find_in_scope(needles, case_insensitive=False):
    """在扫描范围内逐行查找禁用内容，返回 [(位置, 命中词, 行内容)]。"""
    violations = []
    for path in scan_scope_files():
        text = read_text(path)
        if not text:
            continue
        shown = rel_display(path)
        for lineno, line in enumerate(text.splitlines(), 1):
            haystack = line.lower() if case_insensitive else line
            for needle in needles:
                probe = needle.lower() if case_insensitive else needle
                if probe in haystack:
                    violations.append(("%s:%d" % (shown, lineno), needle, line.strip()))
    return violations


def tokens_on_non_negated_lines(path, tokens, negations):
    """逐行找出 tokens 命中但行内不含任何否定词的行（写法参考检查 7a）。

    英文否定词按大小写不敏感比较（统一转小写后子串匹配），中文否定词
    原样子串匹配。返回 [(行号, 行内容, 命中词列表)]；文件不可读时返回
    空列表（文件缺失由调用方按 FAIL 处理）。
    """
    hits = []
    text = read_text(path)
    if not text:
        return hits
    neg_probes = tuple(neg.lower() for neg in negations)
    for lineno, line in enumerate(text.splitlines(), 1):
        hit = [token for token in tokens if token in line]
        if not hit:
            continue
        line_probe = line.lower()
        if any(neg in line_probe for neg in neg_probes):
            continue
        hits.append((lineno, line.strip(), hit))
    return hits


def check_5_old_name(results):
    """检查 5：扫描范围内不得出现旧名 glm-advisor。"""
    title = "命名一致性（不得出现旧名 glm-advisor）"
    violations = find_in_scope((OLD_PLUGIN_NAME,), case_insensitive=True)
    details = []
    if violations:
        for where, needle, _line in violations:
            details.append("FAIL: %s 出现旧名 `%s`" % (where, needle))
    else:
        details.append("PASS: 扫描范围内（%s）无 glm-advisor" % SCAN_SCOPE_DESC)
    results.append((5, title, not violations, details))


def check_6_forbidden_words(results):
    """检查 6：扫描范围内不得出现禁词。"""
    title = "禁词（升级信号 / 上报升级 / 升级路由）"
    violations = find_in_scope(FORBIDDEN_WORDS)
    details = []
    if violations:
        for where, needle, _line in violations:
            details.append("FAIL: %s 出现禁词 `%s`" % (where, needle))
    else:
        details.append("PASS: 扫描范围内（%s）无禁词" % SCAN_SCOPE_DESC)
    results.append((6, title, not violations, details))


def check_7_continuity(results):
    """检查 7：Continuity 安全（quota 否定式声明 + 无 workspace-global checkpoint）。"""
    title = "Continuity 安全（quota 否定式声明 / 无 workspace-global checkpoint）"
    details = []
    ok = True

    # 7a) quota API 名称只允许出现在否定式声明行（同行含 虚构 / 不存在）
    for path in scan_scope_files():
        text = read_text(path)
        if not text:
            continue
        shown = rel_display(path)
        for lineno, line in enumerate(text.splitlines(), 1):
            hit = [token for token in QUOTA_TOKENS if token in line]
            if hit and not any(neg in line for neg in QUOTA_NEGATIONS):
                details.append(
                    "FAIL: %s:%d quota API 名称 %s 出现在非否定式声明行"
                    % (shown, lineno, "/".join(hit)))
                ok = False

    # 7b) 先移除允许的任务专属路径，再检查残留 workspace-global 路径
    for path in scan_scope_files():
        text = read_text(path)
        if not text:
            continue
        cleaned = ALLOWED_CHECKPOINT_RE.sub("", text)
        match = GLOBAL_CHECKPOINT_RE.search(cleaned)
        if match:
            lineno = cleaned[:match.start()].count("\n") + 1
            details.append(
                "FAIL: %s:%d 残留 workspace-global checkpoint 路径 "
                ".glm-conductor/checkpoint.md" % (rel_display(path), lineno))
            ok = False

    # 7c) 「无 quota API」绝对化措辞反转（v2 alpha3）：四文档零命中，
    #     命中即 FAIL（行号 + 摘要），全部零命中输出一行 PASS
    absolute_hits = 0
    for path in QUOTA_ABSOLUTE_FILES:
        text = read_text(path)
        shown = rel_display(path)
        if text is None or not os.path.isfile(path):
            details.append("FAIL: %s 不存在或无法读取，无法执行 7c 检查" % shown)
            ok = False
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if QUOTA_ABSOLUTE_PHRASE in line:
                absolute_hits += 1
                details.append(
                    "FAIL: %s:%d 出现「无 quota API」绝对化措辞: %s"
                    % (shown, lineno, line.strip()))
                ok = False
    if absolute_hits == 0:
        details.append(
            "PASS: 四文档（README / architecture / continuity SKILL / "
            "long-horizon）均无「无 quota API」绝对化措辞")

    if ok:
        details.append("PASS: quota API 名称均处于否定式声明行（同行含 虚构/不存在）")
        details.append("PASS: 移除任务专属路径后无 .glm-conductor/checkpoint.md 残留")
    results.append((7, title, ok, details))


def check_8_visual_protocol(results):
    """检查 8：视觉协议一致性（VISUAL_ACCEPT_REQUEST 修正）。"""
    title = "视觉协议一致性（VISUAL_CAPTURE_REQUEST / VISUAL ACCEPTANCE / VISUAL REVIEW）"
    details = []
    ok = True

    def require_contains(path, needle):
        text = read_text(path)
        shown = rel_display(path)
        if text is None or needle not in text:
            details.append("FAIL: %s 缺少 `%s`" % (shown, needle))
            return False
        details.append("PASS: %s 含 `%s`" % (shown, needle))
        return True

    ok = require_contains(VISUAL_IMPLEMENTER, "VISUAL_CAPTURE_REQUEST") and ok
    ok = require_contains(VISUAL_IMPLEMENTER, "VISUAL ACCEPTANCE") and ok
    ok = require_contains(ROLE_CONTRACTS, "VISUAL_CAPTURE_REQUEST") and ok
    ok = require_contains(VISUAL_REVIEWER, "VISUAL REVIEW") and ok
    ok = require_contains(ROLE_CONTRACTS, "VISUAL REVIEW") and ok

    reviewer_text = read_text(VISUAL_REVIEWER) or ""
    if "GLM REVIEW" in reviewer_text:
        details.append("FAIL: %s 出现 `GLM REVIEW`（视觉审查者应只用 VISUAL REVIEW）"
                       % rel_display(VISUAL_REVIEWER))
        ok = False
    else:
        details.append("PASS: visual-reviewer.md 无 `GLM REVIEW`")

    legacy = find_in_scope((OLD_VISUAL_NAME,))
    if legacy:
        for where, _needle, _line in legacy:
            details.append("FAIL: %s 出现修正前旧名 `%s`" % (where, OLD_VISUAL_NAME))
        ok = False
    else:
        details.append("PASS: 扫描范围内无修正前旧名 VISUAL_ACCEPT_REQUEST")

    results.append((8, title, ok, details))


def check_9_arch_doc(results):
    """检查 9：权威架构文档契约（docs/architecture.md）。"""
    title = "权威架构文档契约（docs/architecture.md）"
    details = []
    ok = True

    text = read_text(ARCH_DOC)
    shown = rel_display(ARCH_DOC)
    if text is None or not os.path.isfile(ARCH_DOC):
        details.append("FAIL: %s 不存在或无法读取" % shown)
        results.append((9, title, False, details))
        return

    # 必含标记（子串级）
    for marker in ARCH_REQUIRED_MARKERS:
        if marker in text:
            details.append("PASS: 含必含标记 `%s`" % marker)
        else:
            details.append("FAIL: 缺少必含标记 `%s`" % marker)
            ok = False

    # 禁含标记（出现即 FAIL，大小写不敏感）
    lowered = text.lower()
    for marker in ARCH_FORBIDDEN_CI:
        if marker.lower() in lowered:
            details.append("FAIL: 出现禁含标记 `%s`（大小写不敏感）" % marker)
            ok = False
        else:
            details.append("PASS: 无禁含标记 `%s`" % marker)

    # 条件禁含：`最新 checkpoint`/`latest checkpoint` 只允许出现在否定行
    cond_hits = tokens_on_non_negated_lines(
        ARCH_DOC, CHECKPOINT_REF_TOKENS, CHECKPOINT_REF_NEGATIONS)
    if cond_hits:
        for lineno, line, hit in cond_hits:
            details.append(
                "FAIL: %s:%d `%s` 出现在无否定词（不/禁止/never/not）的行: %s"
                % (shown, lineno, "/".join(hit), line))
            ok = False
    else:
        details.append(
            "PASS: `最新 checkpoint`/`latest checkpoint` 仅出现在含否定词的行")

    results.append((9, title, ok, details))


def check_10_continuity_id(results):
    """检查 10：任务标识（TASK_ID 必含与唯一格式）。"""
    title = "任务标识（TASK_ID 必含与唯一格式）"
    details = []
    ok = True

    # TASK_ID 必含于三个文件；`随机十六进制后缀` 必含于 long-horizon.md
    # 与 docs/architecture.md（「语义前缀+随机后缀」机械唯一格式的落点标记）
    plans = (
        (TASK_ID_FILES, ("TASK_ID",)),
        (HEX_SUFFIX_FILES, ("随机十六进制后缀",)),
    )
    for paths, markers in plans:
        for path in paths:
            for marker in markers:
                shown = rel_display(path)
                text = read_text(path)
                if text is None or not os.path.isfile(path):
                    details.append("FAIL: %s 不存在，无法确认 `%s`" % (shown, marker))
                    ok = False
                elif marker in text:
                    details.append("PASS: %s 含 `%s`" % (shown, marker))
                else:
                    details.append("FAIL: %s 缺少 `%s`" % (shown, marker))
                    ok = False

    results.append((10, title, ok, details))


def check_11_visual_topology(results):
    """检查 11：视觉规范拓扑（新调用规范路径 / VISUAL_ROUND）。"""
    title = "视觉规范拓扑（新调用规范路径 / VISUAL_ROUND）"
    details = []
    ok = True

    # VISUAL_ROUND 必含于四个文件（缺失文件按 FAIL）
    for path in VISUAL_ROUND_FILES:
        shown = rel_display(path)
        text = read_text(path)
        if text is None or not os.path.isfile(path):
            details.append("FAIL: %s 不存在或无法读取，无法确认 `VISUAL_ROUND`" % shown)
            ok = False
        elif "VISUAL_ROUND" in text:
            details.append("PASS: %s 含 `VISUAL_ROUND`" % shown)
        else:
            details.append("FAIL: %s 缺少 `VISUAL_ROUND`" % shown)
            ok = False

    # `规范路径` 与 `完整状态` 必须同时含于三个实现侧文件
    for path in VISUAL_TOPOLOGY_FILES:
        shown = rel_display(path)
        text = read_text(path)
        if text is None or not os.path.isfile(path):
            continue  # 文件缺失已在上一段报告
        for marker in VISUAL_TOPOLOGY_REQUIRED:
            if marker in text:
                details.append("PASS: %s 含 `%s`" % (shown, marker))
            else:
                details.append("FAIL: %s 缺少 `%s`" % (shown, marker))
                ok = False

    # 条件禁含：`单次调用内等待`/`同一调用内等待` 只允许出现在否定行
    cond_hits = []
    for path in VISUAL_ROUND_FILES:
        for lineno, line, hit in tokens_on_non_negated_lines(
                path, VISUAL_WAIT_TOKENS, VISUAL_WAIT_NEGATIONS):
            cond_hits.append((rel_display(path), lineno, line, hit))
    if cond_hits:
        for shown, lineno, line, hit in cond_hits:
            details.append(
                "FAIL: %s:%d `%s` 出现在无否定词（不/禁止）的行: %s"
                % (shown, lineno, "/".join(hit), line))
            ok = False
    else:
        details.append(
            "PASS: `单次调用内等待`/`同一调用内等待` 仅出现在含否定词（不/禁止）的行")

    results.append((11, title, ok, details))


def check_12_version_consistency(results):
    """检查 12：版本一致性（plugin.json ↔ CHANGELOG 最新条目）。"""
    title = "版本一致性（plugin.json ↔ CHANGELOG 最新条目）"
    details = []
    ok = True

    # plugin.json 的 version 字段（重新解析，与检查 1 逻辑一致）
    plugin_version = None
    try:
        with io.open(PLUGIN_JSON, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (ValueError, OSError) as exc:
        data = None
        details.append("FAIL: %s 无法解析: %s" % (rel_display(PLUGIN_JSON), exc))
        ok = False
    if isinstance(data, dict):
        version = data.get("version")
        if isinstance(version, str) and version.strip():
            plugin_version = version.strip()
            details.append("PASS: plugin.json version=%s" % plugin_version)
        else:
            details.append("FAIL: plugin.json 缺少可用的 version 字段")
            ok = False

    # CHANGELOG.md 第一个 `## ` 标题行，取 `## ` 后的版本记号
    changelog_version = None
    text = read_text(CHANGELOG)
    if text is None or not os.path.isfile(CHANGELOG):
        details.append("FAIL: %s 不存在或无法读取" % rel_display(CHANGELOG))
        ok = False
    else:
        for line in text.splitlines():
            match = CHANGELOG_HEADING_RE.match(line)
            if match:
                # 标题可能就是纯版本号；若含附加文字则取首个空白分段
                changelog_version = match.group(1).split()[0]
                break
        if changelog_version:
            details.append("PASS: CHANGELOG 最新条目版本=%s" % changelog_version)
        else:
            details.append("FAIL: CHANGELOG.md 未找到 `## ` 版本标题行")
            ok = False

    if plugin_version is not None and changelog_version is not None:
        if plugin_version == changelog_version:
            details.append("PASS: 版本一致（%s）" % plugin_version)
        else:
            details.append(
                "FAIL: 版本不一致：plugin.json=%s，CHANGELOG=%s"
                % (plugin_version, changelog_version))
            ok = False

    results.append((12, title, ok, details))


def check_13_hooks_manifest(results):
    """检查 13：钩子清单完整性（hooks/hooks.json）。"""
    title = "钩子清单完整性（hooks/hooks.json）"
    details = []
    ok = True
    shown = rel_display(HOOKS_MANIFEST)

    # 13.1 存在、合法 JSON、顶层为对象且含非空 `hooks` 对象（任一不满足即 FAIL 并终止）
    if not os.path.isfile(HOOKS_MANIFEST):
        details.append("FAIL: %s 不存在" % shown)
        results.append((13, title, False, details))
        return
    try:
        with io.open(HOOKS_MANIFEST, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (ValueError, OSError) as exc:
        details.append("FAIL: %s 不是合法 JSON: %s" % (shown, exc))
        results.append((13, title, False, details))
        return
    if not isinstance(data, dict):
        details.append("FAIL: %s 顶层不是 JSON 对象" % shown)
        results.append((13, title, False, details))
        return
    hooks = data.get("hooks")
    if not isinstance(hooks, dict) or not hooks:
        details.append("FAIL: %s 缺少非空的 `hooks` 对象" % shown)
        results.append((13, title, False, details))
        return
    details.append("PASS: %s 为合法 JSON 对象且含非空 `hooks` 对象" % shown)

    # 13.2 事件键 ⊆ 允许事件集合
    unknown_events = sorted(set(hooks) - set(ALLOWED_HOOK_EVENTS))
    if unknown_events:
        details.append(
            "FAIL: 未知事件键: %s（允许: %s）"
            % (", ".join(unknown_events), ", ".join(ALLOWED_HOOK_EVENTS)))
        ok = False
    else:
        details.append("PASS: 事件键均在允许集合内（%s）" % ", ".join(sorted(hooks)))

    # 13.2.1 必须声明 Stop 事件键（v2 完成门的最低声明要求）
    if "Stop" not in hooks:
        details.append(
            "FAIL: 缺少 `Stop` 事件键（v2 完成门要求声明 Stop 钩子）")
        ok = False
    else:
        details.append("PASS: 已声明 `Stop` 事件键（v2 完成门）")

    # 13.3 逐事件 → 逐项 → 逐 hook 校验
    for event_name in sorted(hooks):
        entries = hooks[event_name]
        if not isinstance(entries, list):
            details.append("FAIL: 事件 %s 的值不是数组" % event_name)
            ok = False
            continue
        for entry_idx, entry in enumerate(entries, 1):
            where = "事件 %s 第 %d 项" % (event_name, entry_idx)
            if not isinstance(entry, dict):
                details.append("FAIL: %s 不是对象" % where)
                ok = False
                continue
            # matcher 若存在必须是字符串（顶层项与内层 hook 两级都做类型检查）
            if "matcher" in entry and not isinstance(entry["matcher"], str):
                details.append("FAIL: %s matcher 不是字符串" % where)
                ok = False
            inner_hooks = entry.get("hooks")
            if not isinstance(inner_hooks, list):
                details.append("FAIL: %s 缺少 `hooks` 数组" % where)
                ok = False
                continue
            for hook_idx, hook in enumerate(inner_hooks, 1):
                hook_where = "%s hooks[%d]" % (where, hook_idx)
                hook_ok = True
                if not isinstance(hook, dict):
                    details.append("FAIL: %s 不是对象" % hook_where)
                    ok = False
                    continue

                # type 限于 process / command；type=process 时 command 必须为 python3
                hook_type = hook.get("type")
                if hook_type not in ALLOWED_HOOK_TYPES:
                    details.append(
                        "FAIL: %s type=%r 不在允许集合（%s）"
                        % (hook_where, hook_type, " / ".join(ALLOWED_HOOK_TYPES)))
                    hook_ok = False
                elif hook_type == "process" and hook.get("command") != HOOK_COMMAND:
                    details.append(
                        "FAIL: %s type=process 但 command=%r（必须为 %s）"
                        % (hook_where, hook.get("command"), HOOK_COMMAND))
                    hook_ok = False

                # timeoutMs 若存在必须是 >0 的整数（JSON true 不是整数）
                if "timeoutMs" in hook:
                    timeout = hook["timeoutMs"]
                    if (isinstance(timeout, bool) or not isinstance(timeout, int)
                            or timeout <= 0):
                        details.append(
                            "FAIL: %s timeoutMs=%r 不是 >0 的整数"
                            % (hook_where, timeout))
                        hook_ok = False

                # matcher 若存在必须是字符串
                if "matcher" in hook and not isinstance(hook["matcher"], str):
                    details.append("FAIL: %s matcher 不是字符串" % hook_where)
                    hook_ok = False

                # args 中以 ${ZCODE_PLUGIN_ROOT}/ 开头的字符串：
                # 替换为插件根 plugins/glm-conductor 后必须实际存在
                script_note = ""
                args = hook.get("args")
                if "args" in hook and not isinstance(args, list):
                    details.append("FAIL: %s args 不是数组" % hook_where)
                    hook_ok = False
                elif isinstance(args, list):
                    for arg in args:
                        if not (isinstance(arg, str)
                                and arg.startswith(PLUGIN_ROOT_PREFIX)):
                            continue
                        rel_path = "%s/%s" % (
                            PLUGIN_ROOT_REL, arg[len(PLUGIN_ROOT_PREFIX):])
                        abs_path = os.path.join(REPO_ROOT, *rel_path.split("/"))
                        if os.path.isfile(abs_path):
                            script_note = rel_path
                        else:
                            details.append(
                                "FAIL: %s args 脚本不存在: `%s`（解析为 %s）"
                                % (hook_where, arg, rel_path))
                            hook_ok = False

                if not hook_ok:
                    ok = False
                else:
                    # PASS 明细逐 hook 列出：事件名、command、脚本存在确认
                    exist_note = ("，脚本存在: %s" % script_note) if script_note else ""
                    details.append(
                        "PASS: %s → command=%s%s"
                        % (hook_where, hook.get("command"), exist_note))

    results.append((13, title, ok, details))


def check_14_runtime_state(results):
    """检查 14：runtime 状态层与技能契约标记。"""
    title = "runtime 状态层与技能契约标记"
    details = []
    ok = True

    # 14.1 / 14.2 存在且非空（>0 字节）
    for group_name, paths in (
            ("runtime 状态层", RUNTIME_REQUIRED_FILES),
            ("强制层钩子", LAYER_REQUIRED_FILES),
            ("诊断命令文件", COMMAND_REQUIRED_FILES),
            ("单元测试", TEST_REQUIRED_FILES)):
        for path in paths:
            shown = rel_display(path)
            if not os.path.isfile(path):
                details.append("FAIL: %s 不存在（%s）" % (shown, group_name))
                ok = False
                continue
            size = os.path.getsize(path)
            if size <= 0:
                details.append("FAIL: %s 为空文件（0 字节，%s）" % (shown, group_name))
                ok = False
            else:
                details.append("PASS: %s 存在且非空（%d 字节）" % (shown, size))

    # 14.3 / 14.4 文本标记：runtime 层与技能契约
    marker_plans = (
        (STATE_PY, STATE_REQUIRED_MARKERS),
        (JOURNAL_PY, JOURNAL_REQUIRED_MARKERS),
        (OWNERSHIP_PY, OWNERSHIP_REQUIRED_MARKERS),
        (FINGERPRINT_PY, FINGERPRINT_REQUIRED_MARKERS),
        (STOP_GATE_PY, STOP_GATE_REQUIRED_MARKERS),
        (PRE_TOOL_USE_PY, PRE_TOOL_USE_REQUIRED_MARKERS),
        (QUOTA_SCHEDULER_PY,
         ("evaluate", "plan_resume", "PRESSURE", "EXHAUSTED")),
        (QUOTA_HTTP_PY, ("ALLOWED_HOSTS", "malformed")),
        (QUOTA_CREDENTIALS_PY,
         ("GLM_CONDUCTOR_QUOTA_API_KEY", "builtin:bigmodel-coding-plan")),
        (QUOTA_REPORT_PY, ("--json", "unavailable")),
    ) + SKILL_CONTRACT_MARKERS
    for path, markers in marker_plans:
        shown = rel_display(path)
        text = read_text(path)
        if text is None or not os.path.isfile(path):
            details.append("FAIL: %s 不存在或无法读取，无法确认标记" % shown)
            ok = False
            continue
        for marker in markers:
            if marker in text:
                details.append("PASS: %s 含 `%s`" % (shown, marker))
            else:
                details.append("FAIL: %s 缺少 `%s`" % (shown, marker))
                ok = False

    results.append((14, title, ok, details))


CHECKS = (
    check_1_json,
    check_2_agent_frontmatter,
    check_3_skill_frontmatter,
    check_4_agent_refs,
    check_5_old_name,
    check_6_forbidden_words,
    check_7_continuity,
    check_8_visual_protocol,
    check_9_arch_doc,
    check_10_continuity_id,
    check_11_visual_topology,
    check_12_version_consistency,
    check_13_hooks_manifest,
    check_14_runtime_state,
)


def main():
    # Windows 控制台/管道下避免中文输出触发编码异常
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    results = []
    for check in CHECKS:
        check(results)

    print("==== GLM Conductor 插件静态校验 ====")
    print("仓库根: %s" % REPO_ROOT)
    print("")
    for num, title, ok, details in results:
        print("[%d/%d] %s -- %s" % (num, len(CHECKS), title, "PASS" if ok else "FAIL"))
        for detail in details:
            print("       - %s" % detail)
    print("")
    passed = sum(1 for _num, _title, ok, _details in results if ok)
    failed = len(results) - passed
    print("==== 摘要: %d/%d 项通过, %d 项失败 ====" % (passed, len(results), failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
