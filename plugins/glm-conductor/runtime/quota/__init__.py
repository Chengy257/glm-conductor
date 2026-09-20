"""GLM Conductor v2.4 quota 观测核（quota 包，v2 工作块 B5.1；v2.4 Phase 3 P3-A/P3-D 收缩）。

纯观测层：标准化解析与评估（parser：§27 snapshot / §28 四态 /
§29-§33 恢复规划纯函数）+ provider 抽象边界（provider / _http /
zai / bigmodel）+ 凭证解析（credentials）+ 运行时解析（resolver）
+ 只读诊断面（report）。全部离线可测；不含任何执行相决策语义
（v2.4 P3-A 起 control / observer / watcher / watcher_store / epoch /
primer / accounting / scheduler 控制面模块已删除；P3-D 起 clock /
clock_store / identity 亦随控制面收口删除——身份指纹派生单一真相源
收敛至 resolver._provider_identity_hash）。
"""
