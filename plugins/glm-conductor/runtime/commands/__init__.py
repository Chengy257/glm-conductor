#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.3.1 runtime.commands 包：CLI 子命令处理函数的分解
落点（v2.3.1 Wave 2，unit w2-host-check 起设）。

cli.py 只保留 argv 分发与 JSON / 退出码映射；新增子命令的处理函数一
律落在本包，样板（Wave 3 迁移既有 wake-* / quota-clock-* 等命令时照
此）：
    - 薄函数 + 显式参数（零类框架、零 DI、零全局状态）；
    - 复用现有 runtime 域模块（探测 / 决策逻辑不进本包）；
    - 依赖方向恒为 cli → commands → 域模块（本包不 import runtime.cli）。

模块清单：
    host    host-check 子命令：ZCode 宿主库只读兼容性探针。
"""
