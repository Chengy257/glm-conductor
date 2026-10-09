#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.3 遗留任务一键归档工具。

用途：
    扫描目标仓库下的 .glm-conductor/tasks/ 目录，复用 runtime.state.detect_legacy_task
    精准识别 v2.3 遗留任务并将其安全移动到 .glm-conductor/archived-tasks/ 目录。

设计纪律：
    - 精准判定：仅归档被 detect_legacy_task 判定为 legacy=True 的 v2.3 遗留任务；
      v2.4+ 活跃/终态任务、损坏目录、非任务目录绝不触碰；
    - 默认 Dry-run：未带 --confirm 时仅扫描并打印候选清单，不进行任何文件移动；
    - 显式确认：指定 --confirm 参数后方执行物理移动（shutil.move）；
    - 纯标准库：仅依赖 Python 标准库与插件内置 runtime.state。

用法：
    # 预览待归档遗留任务（dry-run）
    python scripts/archive_legacy_tasks.py [--repo <path>]

    # 确认执行归档
    python scripts/archive_legacy_tasks.py [--repo <path>] --confirm

    # 机器可读 JSON 输出
    python scripts/archive_legacy_tasks.py [--repo <path>] [--confirm] --json
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

# 将插件根加入 sys.path 以复用 runtime.state
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "glm-conductor"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from runtime import state  # noqa: E402

ARCHIVED_DIRNAME = "archived-tasks"


def scan_and_archive(repo_root, *, confirm=False):
    """扫描并归档目标仓库下的 v2.3 遗留任务。

    返回 dict:
        {
            "ok": True,
            "dry_run": not confirm,
            "repo_root": str(repo_root),
            "legacy_candidates": [{"task_id": str, "markers": list[str], "status": str}],
            "archived": [{"task_id": str, "source": str, "dest": str}],
            "skipped": [{"task_id": str, "reason": str}],
        }
    """
    repo_path = Path(repo_root).resolve()
    tasks_dir = state.tasks_root(repo_path)
    archive_dir = repo_path / state.TASKS_DIRNAME / ARCHIVED_DIRNAME

    result = {
        "ok": True,
        "dry_run": not confirm,
        "repo_root": str(repo_path),
        "legacy_candidates": [],
        "archived": [],
        "skipped": [],
    }

    if not tasks_dir.is_dir():
        return result

    for entry in sorted(tasks_dir.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        task_id = entry.name
        try:
            legacy_info = state.detect_legacy_task(repo_path, task_id)
        except Exception as exc:
            result["skipped"].append({
                "task_id": task_id,
                "reason": "state.json 解析或读取异常: %s" % exc,
            })
            continue

        if legacy_info is None:
            result["skipped"].append({
                "task_id": task_id,
                "reason": "缺少 state.json，非合法任务目录",
            })
            continue

        if not legacy_info.get("legacy", False):
            result["skipped"].append({
                "task_id": task_id,
                "reason": "v2.4+ 正常任务（status=%s）" % legacy_info.get("status"),
            })
            continue

        # 确认为 v2.3 遗留任务
        candidate = {
            "task_id": task_id,
            "status": legacy_info.get("status") or "unknown",
            "markers": legacy_info.get("markers", []),
        }
        result["legacy_candidates"].append(candidate)

        if confirm:
            archive_dir.mkdir(parents=True, exist_ok=True)
            dest_entry = archive_dir / task_id
            if dest_entry.exists():
                result["skipped"].append({
                    "task_id": task_id,
                    "reason": "目标归档目录已存在同名目录: %s" % dest_entry,
                })
                continue
            try:
                shutil.move(str(entry), str(dest_entry))
                result["archived"].append({
                    "task_id": task_id,
                    "source": str(entry),
                    "dest": str(dest_entry),
                })
            except OSError as exc:
                result["skipped"].append({
                    "task_id": task_id,
                    "reason": "移动失败: %s" % exc,
                })

    return result


def main():
    parser = argparse.ArgumentParser(
        description="GLM Conductor v2.3 遗留任务归档工具")
    parser.add_argument(
        "--repo", default=None,
        help="目标 Git 仓库根目录（缺省当前工作目录）")
    parser.add_argument(
        "--confirm", action="store_true",
        help="确认执行物理归档（缺省仅 dry-run 预览）")
    parser.add_argument(
        "--json", action="store_true",
        help="以单行 JSON 格式输出结果")

    args = parser.parse_args()
    repo_root = args.repo or os.environ.get("ZCODE_PROJECT_DIR") or os.getcwd()

    outcome = scan_and_archive(repo_root, confirm=args.confirm)

    if args.json:
        print(json.dumps(outcome, ensure_ascii=False))
        return 0

    mode_str = "[执行模式]" if args.confirm else "[预览模式 (Dry-run)]"
    print("==== GLM Conductor v2.3 遗留任务归档 %s ====" % mode_str)
    print("仓库根路径: %s" % outcome["repo_root"])
    print("发现遗留任务: %d 个" % len(outcome["legacy_candidates"]))

    if outcome["legacy_candidates"]:
        print("\n待归档遗留任务清单:")
        for cand in outcome["legacy_candidates"]:
            markers_str = ", ".join(cand["markers"][:3])
            if len(cand["markers"]) > 3:
                markers_str += " 等共%d项" % len(cand["markers"])
            print("  - %s (status=%s, 标记: %s)"
                  % (cand["task_id"], cand["status"], markers_str))

    if args.confirm:
        print("\n已成功归档任务: %d 个" % len(outcome["archived"]))
        for item in outcome["archived"]:
            print("  - %s -> %s" % (item["task_id"], item["dest"]))
    else:
        if outcome["legacy_candidates"]:
            print("\n提示: 当前为预览模式，未做任何修改。如需执行物理归档，请附加 `--confirm` 参数。")
        else:
            print("\n未发现任何 v2.3 遗留任务，无需归档。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
