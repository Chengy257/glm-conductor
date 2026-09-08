#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.commands.host：host-check 子命令（v2.3.1 Wave 2，unit
w2-host-check）。

本模块是 runtime/commands/ 包的首个成员，确立 W3 命令迁出样板：薄函
数 + 显式参数 + 复用 runtime 域模块；零类框架、零 DI、零全局状态；
不 import runtime.cli（依赖方向恒为 cli → commands → 域模块）。

职责：`host-check`（无位置参数；可选 --db <path>，wake-arm 同形）——
ZCode 宿主库 tasks-index.sqlite 只读兼容性探针，供 ZCode 升级后自检
adapter 依赖面是否仍然成立。路径解析与探测事实全部来自
runtime.host.zcode_schedule（discover_zcode_tasks_db /
probe_host_compatibility）；本模块只做调用与单行 JSON 呈现。

绝对零写红线：不 retime、不建任务、不合成写、不改 journal mode、
不改宿主配置（探针只做四类纯读语句——明细见
zcode_schedule.probe_host_compatibility docstring；薄层零 SQL）。
测试与特殊安装经 --db / GLM_CONDUCTOR_ZCODE_DB 显式指向目标库，绝不
隐式触碰真实宿主库之外的任何库。

输出与退出码（cli 契约）：stdout 恒为单行 JSON（ensure_ascii=True，
与 cli._emit 同款契约）；0 = 兼容或干净的不兼容报告（含 DB 缺失 /
不可读——探针不抛探测类异常）；2 = 用法错（cli._dispatch 的
_UsageError，本模块不处理 argv）；意外异常向上抛，由 cli.main 统一
映射退出码 1。
"""

import json
import sys

from runtime.host import zcode_schedule


def host_check(db_path=None) -> int:
    """host-check：只读兼容性探针（零写；恒产出报告）。

    db_path 经 discover_zcode_tasks_db 解析（explicit >
    GLM_CONDUCTOR_ZCODE_DB > 默认 <~>/.zcode/v2/tasks-index.sqlite，
    与 adapter 的 env 覆盖语义一致），探测经
    probe_host_compatibility 汇总后以单行 JSON 写 stdout 并返回 0。
    报告键集见 zcode_schedule.probe_host_compatibility docstring。
    """
    resolved = zcode_schedule.discover_zcode_tasks_db(db_path)
    report = zcode_schedule.probe_host_compatibility(resolved)
    sys.stdout.write(json.dumps(report, ensure_ascii=True) + "\n")
    return 0
