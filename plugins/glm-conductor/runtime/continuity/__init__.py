"""GLM Conductor v2 continuity 子系统（continuity 包，v2.2.1 WU-221-C2 新建）。

长任务连续性域的规范定义落点：Persistent Wake Bridge 的规划 / 记账 /
prompt 生成（wake_bridge；v2.2.1 WU-221-C2 自 runtime.task_manager
行为保持抽取，规范定义见 runtime.continuity.wake_bridge）。依赖方向
冻结：runtime.task_manager 等上层单向 import 本包，本包绝不反向
import 上层（防循环）。本包只承载规划与记账，绝不调用宿主
CronCreate / CronList 等（宿主动作归主会话）。全部离线，仅标准库。
"""
