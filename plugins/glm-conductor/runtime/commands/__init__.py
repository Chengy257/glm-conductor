#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor runtime.commands 包：CLI 子命令处理函数的分解落点
（v2.3.1 Wave 2 起设）。

cli.py 只保留 argv 分发与 JSON / 退出码映射；新增子命令的处理函数一
律落在本包，样板：
    - 薄函数 + 显式参数（零类框架、零 DI、零全局状态）；
    - 复用现有 runtime 域模块（探测 / 决策逻辑不进本包）；
    - 依赖方向恒为 cli → commands → 域模块（本包不 import runtime.cli）。

v2.4 Phase 3 P3-D/E（Q4）收口：v2.3.1 迁入的三模块（Persistent Wake
Bridge 八子命令 / 全局额度时钟四子命令 / ZCode 宿主库只读兼容性探
针）已随其后端整体删除，本包现无成员模块（空包保留，作后续 CLI 子
命令分解落点）。
"""
