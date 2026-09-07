#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""插件装载冒烟检查（v2.2.1 CI 加固 D2）。

用途：
    静态校验与单元测试之外的第三道轻量闸：回答一个问题——"插件包能否
    被装载"。覆盖四件事：
      1. marketplace.json / .zcode-plugin/plugin.json 可解析且必需字段齐备；
      2. 目录结构完整：skills/*/SKILL.md 存在；agents/ 存在时须全为 .md
         且非空；hooks/hooks.json 可解析且每个 process 钩子指向的脚本
         文件真实存在（${ZCODE_PLUGIN_ROOT} 展开）；
      3. runtime 包在跑批 Python 下可真实 import（runtime.task_manager）；
      4. 该模块至少一个纯函数行为符合冻结语义（_is_worker_cap）。
    零网络、零模型调用、零状态写副作用；stdlib-only，Python 3.7 兼容。

边界：
    与 scripts/validate_plugin.py 互补——校验器做全量静态一致性（15 项
    检查），本脚本只做装载面冒烟，不重复其逐项断言。

退出码：
    0 = 全部通过；1 = 任一检查失败（逐条打印 PASS/FAIL）。
"""

import io
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "glm-conductor"
MARKETPLACE_JSON = REPO_ROOT / "marketplace.json"
PLUGIN_JSON = PLUGIN_ROOT / ".zcode-plugin" / "plugin.json"
HOOKS_JSON = PLUGIN_ROOT / "hooks" / "hooks.json"

FAILURES = []


def check(ok, message):
    print(("PASS  " if ok else "FAIL  ") + message)
    if not ok:
        FAILURES.append(message)
    return bool(ok)


def load_json(path, label):
    try:
        with io.open(str(path), "r", encoding="utf-8") as fh:
            return json.load(fh), None
    except (OSError, ValueError) as exc:
        return None, "%s: %s" % (label, exc)


def smoke_manifests():
    marketplace, err = load_json(MARKETPLACE_JSON, "marketplace.json")
    if not check(marketplace is not None, "marketplace.json 可解析 (%s)" % (err or "ok")):
        return
    check(isinstance(marketplace.get("name"), str) and marketplace["name"],
          "marketplace.name 为非空字符串")
    plugins = marketplace.get("plugins")
    check(isinstance(plugins, list) and plugins, "marketplace.plugins 为非空数组")
    for entry in plugins or []:
        if not check(isinstance(entry, dict) and entry.get("name") and entry.get("source"),
                     "marketplace 插件项 name/source 齐备 (%s)" % entry.get("name")):
            continue
        source = REPO_ROOT / str(entry["source"]).lstrip("./")
        check(source.is_dir(), "插件源目录存在: %s" % entry["source"])

    plugin, err = load_json(PLUGIN_JSON, "plugin.json")
    if not check(plugin is not None, ".zcode-plugin/plugin.json 可解析 (%s)" % (err or "ok")):
        return
    for key in ("name", "version", "description"):
        check(isinstance(plugin.get(key), str) and plugin[key],
              "plugin.json.%s 为非空字符串" % key)
    skills_dir = PLUGIN_ROOT / str(plugin.get("skills", "skills/")).lstrip("./")
    check(skills_dir.is_dir(), "plugin.json.skills 指向的目录存在")


def smoke_structure():
    skills_dir = PLUGIN_ROOT / "skills"
    skill_dirs = sorted(p for p in skills_dir.iterdir() if p.is_dir())
    check(bool(skill_dirs), "skills/ 至少含一个 skill 目录")
    for skill_dir in skill_dirs:
        skill_md = skill_dir / "SKILL.md"
        check(skill_md.is_file() and skill_md.stat().st_size > 0,
              "skills/%s/SKILL.md 存在且非空" % skill_dir.name)

    agents_dir = PLUGIN_ROOT / "agents"
    if agents_dir.is_dir():
        agent_files = sorted(p for p in agents_dir.iterdir())
        md_files = [p for p in agent_files if p.suffix == ".md" and p.is_file()]
        check(bool(md_files), "agents/ 存在且至少含一个 agent .md")
        check(len(md_files) == len(agent_files), "agents/ 下全部条目均为 .md 文件")

    hooks, err = load_json(HOOKS_JSON, "hooks.json")
    if not check(hooks is not None, "hooks/hooks.json 可解析 (%s)" % (err or "ok")):
        return
    hooks_map = hooks.get("hooks") if isinstance(hooks, dict) else None
    if not check(isinstance(hooks_map, dict) and hooks_map, "hooks.json 含非空 hooks 对象"):
        return
    for event, groups in sorted(hooks_map.items()):
        ok_event = isinstance(groups, list) and bool(groups)
        check(ok_event, "hooks.%s 为非空数组" % event)
        for group in groups or []:
            for hook in group.get("hooks", []) if isinstance(group, dict) else []:
                if hook.get("type") != "process":
                    continue
                script = "/".join(hook.get("args", []))
                script_path = PLUGIN_ROOT / script.replace(
                    "${ZCODE_PLUGIN_ROOT}/", "")
                check(script_path.is_file(), "钩子脚本存在: %s" % script)


def smoke_runtime_import():
    sys.path.insert(0, str(PLUGIN_ROOT))
    try:
        import runtime.task_manager as task_manager
    except Exception as exc:  # 装载失败必须显式 FAIL，不允许静默
        check(False, "runtime.task_manager 可 import (%s)" % exc)
        return
    check(True, "runtime.task_manager 可 import")
    check(task_manager._is_worker_cap(4) is True,
          "task_manager._is_worker_cap(4) is True（纯函数装载后可用）")
    check(task_manager._is_worker_cap(True) is False,
          "task_manager._is_worker_cap(True) is False（bool 不充当槽位数）")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    print("==== GLM Conductor 插件装载冒烟 ====")
    smoke_manifests()
    smoke_structure()
    smoke_runtime_import()
    print("")
    print("==== 摘要: %d 项失败 ====" % len(FAILURES))
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())
