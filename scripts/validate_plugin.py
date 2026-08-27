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
      5. 命名一致性：plugins/、marketplace.json、README.md 中不得出现旧名
         `glm-advisor`；
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
         VISUAL_ACCEPT_REQUEST 不得出现在扫描范围内。

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
# marketplace.json + README.md（与整改规范 5/6 条目声明的范围一致）
SCAN_EXTRA_FILES = (MARKETPLACE_JSON, README)


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
    """检查 5/6/7 的扫描范围：plugins/ 全部文件 + marketplace.json + README.md。"""
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


def check_5_old_name(results):
    """检查 5：扫描范围内不得出现旧名 glm-advisor。"""
    title = "命名一致性（不得出现旧名 glm-advisor）"
    violations = find_in_scope((OLD_PLUGIN_NAME,), case_insensitive=True)
    details = []
    if violations:
        for where, needle, _line in violations:
            details.append("FAIL: %s 出现旧名 `%s`" % (where, needle))
    else:
        details.append("PASS: 扫描范围内（plugins/ + marketplace.json + README.md）无 glm-advisor")
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
        details.append("PASS: 扫描范围内（plugins/ + marketplace.json + README.md）无禁词")
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


CHECKS = (
    check_1_json,
    check_2_agent_frontmatter,
    check_3_skill_frontmatter,
    check_4_agent_refs,
    check_5_old_name,
    check_6_forbidden_words,
    check_7_continuity,
    check_8_visual_protocol,
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
        print("[%d/8] %s -- %s" % (num, title, "PASS" if ok else "FAIL"))
        for detail in details:
            print("       - %s" % detail)
    print("")
    passed = sum(1 for _num, _title, ok, _details in results if ok)
    failed = len(results) - passed
    print("==== 摘要: %d/%d 项通过, %d 项失败 ====" % (passed, len(results), failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
