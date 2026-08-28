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
         visual-reviewer），对应 agents/<agent>.md 必须存在；
      5. 命名一致性：plugins/、marketplace.json、README.md、
         docs/architecture.md 中不得出现旧名 `glm-advisor`；
      6. 禁词：同上范围内不得出现 `升级信号` / `上报升级` / `升级路由`；
      7. Continuity 安全（扫描范围同 5/6）：
         a) quota API 名称（getQuotaRemaining / getQuotaResetTime /
            onQuotaReset）只允许出现在否定式声明行（同行含 `虚构` 或
            `不存在`）；
         b) 先移除允许的任务专属 checkpoint 路径
            （.glm-conductor/tasks/<continuity-id>/checkpoint.md 与
            .glm-conductor/tasks/<id>/checkpoint.md），再检查不得残留
            workspace-global 路径 .glm-conductor/checkpoint.md；
      8. 视觉协议一致性：VISUAL_CAPTURE_REQUEST 与 VISUAL ACCEPTANCE 必须出
         现在 agents/visual-implementer.md，VISUAL_CAPTURE_REQUEST 必须出现
         在 skills/orchestration/references/role-contracts.md；VISUAL REVIEW
         必须出现在 agents/visual-reviewer.md 与 role-contracts.md；
         agents/visual-reviewer.md 中不得出现 `GLM REVIEW`；修正前的旧名
         VISUAL_ACCEPT_REQUEST 不得出现在扫描范围内；
      9. 权威架构文档契约：docs/architecture.md 必须存在；必含
         VISUAL_CAPTURE_REQUEST / CONTINUITY_ID / .glm-conductor/tasks/ /
         ROUTE REASSESSMENT / VISUAL REVIEW / 随机十六进制后缀 /
         solo / delegate / audit / full / foreground / resumable / idle；
         禁含 风险阶梯 / risk ladder / glm-advisor（均大小写不敏感）；
         `最新 checkpoint` / `latest checkpoint` 只允许出现在含否定词
         （不 / 禁止 / never / not）的行；
     10. 连续性标识：CONTINUITY_ID 必含于
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
         version 字段必须等于 CHANGELOG.md 第一个 `## ` 标题行中的版本记号。

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

AGENT_REF_RE = re.compile(r"glm-conductor:([A-Za-z0-9_][A-Za-z0-9_-]*)")
QUOTA_TOKENS = ("getQuotaRemaining", "getQuotaResetTime", "onQuotaReset")
QUOTA_NEGATIONS = ("虚构", "不存在")
# 允许的任务专属 checkpoint 路径：<continuity-id> / <id> 均为单段占位
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
    "CONTINUITY_ID",
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

# —— 检查 10：连续性标识 ——
CONTINUITY_ID_FILES = (CONTINUITY_SKILL, LONG_HORIZON, ARCH_DOC)
HEX_SUFFIX_FILES = (LONG_HORIZON, ARCH_DOC)

# —— 检查 11：视觉规范拓扑 ——
VISUAL_TOPOLOGY_FILES = (VISUAL_IMPLEMENTER, OPERATIONS, ROLE_CONTRACTS)
VISUAL_ROUND_FILES = VISUAL_TOPOLOGY_FILES + (ARCH_DOC,)
VISUAL_TOPOLOGY_REQUIRED = ("规范路径", "完整状态")
VISUAL_WAIT_TOKENS = ("单次调用内等待", "同一调用内等待")
VISUAL_WAIT_NEGATIONS = ("不", "禁止")

# —— 检查 12：CHANGELOG 版本标题（第一个 `^## ` 行） ——
CHANGELOG_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")


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
    """检查 10：连续性标识（CONTINUITY_ID 必含与唯一格式）。"""
    title = "连续性标识（CONTINUITY_ID 必含与唯一格式）"
    details = []
    ok = True

    # CONTINUITY_ID 必含于三个文件；`随机十六进制后缀` 必含于 long-horizon.md
    # 与 docs/architecture.md（「语义前缀+随机后缀」机械唯一格式的落点标记）
    plans = (
        (CONTINUITY_ID_FILES, ("CONTINUITY_ID",)),
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
