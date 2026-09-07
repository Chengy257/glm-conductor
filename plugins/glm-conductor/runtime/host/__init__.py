"""GLM Conductor v2.3.0 宿主（host）适配包（v2.3 计划 §6，unit v23-w1）。

与宿主 ZCode 内部库的唯一接触面：zcode_schedule（tasks-index.sqlite 的
只读检查 + 唯一生产写动态重定时）。纯库：零 journal / 零 log，零 quota /
domain 逻辑耦合（不 import runtime.quota 等——未来替换为官方 API
adapter 时算法层不动）。
"""
