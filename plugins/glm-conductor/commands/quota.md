---
name: quota
description: GLM Coding Plan 额度只读诊断：运行 runtime/quota/report.py 查询当前额度窗口（5 小时 / 周窗）、状态评估与恢复建议，支持文本与 --json 双输出。用户询问额度余量、5-hour/weekly 窗口、reset 时间、额度压力或是否该 checkpoint 时使用。
---

# GLM Coding Plan 额度诊断（只读）

用 Bash 运行诊断脚本，把输出**原样**呈现给用户（不转述、不截断、不添加解读性前缀）：

```
python3 ~/.zcode/cli/plugins/cache/glm-conductor/glm-conductor/<version>/runtime/quota/report.py
```

机器可读（供编排决策）加 `--json`：

```
python3 ~/.zcode/cli/plugins/cache/glm-conductor/glm-conductor/<version>/runtime/quota/report.py --json
```

`<version>` 用插件缓存目录下的实际版本号（`ls ~/.zcode/cli/plugins/cache/glm-conductor/glm-conductor/` 可见）。

## 解读

- 文本输出：各额度窗口（`5-hour` / `weekly`）的 used/remaining/reset、`status:`（AVAILABLE / PRESSURE / EXHAUSTED / UNKNOWN）与 `plan:`（continue / checkpoint / resume_at / periodic_fallback）恢复建议
- 三种诊断态：正常额度报告 / `quota 探测失败`（每 provider 一行错误分类）/ `unavailable`（未找到凭证，按输出中的配置指引处理）
- 退出码恒 0：本命令是只读诊断面，不是闸门；不要据退出码做拦截决策
- 窗口机制口径（v2.2 实测）：reset_at 时刻窗口恢复 100%（周窗优先）；下一个 reset_at 只在新窗口内发生模型调用时才物化——纯查询绝不推进它；会话空闲会推迟窗口起点

## 延伸的 runtime 额度/连续性命令（v2.2）

诊断之外的动作面走 runtime CLI（`python3 <插件根>/runtime/cli.py <子命令>`）：

- `quota-resolve <repo> [--force-refresh]` / `quota-observe <repo> [task]` / `quota-phase <repo> <task>`：额度状态 / 自适应观测 / execution phase（NORMAL / PRESSURE / DRAINING / BLOCKED）与连续性义务
- `quota-exhausted <repo> <task>`：额度耗尽确定性转态（waiting_quota + 订阅自动注册 + 唤醒裁决）
- `quota-resume <repo> <task> [status]`：恢复首步（§15.1 commit point 消费窗口预算；退出码 0 成功 / 1 拒绝 / 2 参数 / **3 durable-but-degraded——转态可能已落盘，幂等重跑安全，先查任务 journal 再决定**）
- `wake-plan` / `wake-status` / `wake-reconcile` / `transport-status` / `quota-watcher start|status|stop|once`：persistent wake bridge 裁决与状态、宿主事实对账、activation transport 事实面、可选常驻额度 watcher

边界：runtime 自身零宿主 `Cron*` 调用——宿主事实由会话侧供给（如 `wake-reconcile` 显式传入 CronList 观测结论）；宿主 CronCreate/CronDelete 由主会话执行，桥接清理是会话侧单次尝试动作（绝不重试）；`wake-record` 是 v2.1 legacy 入口（deprecated），窗口预算只在 resume commit point 消费。

## 边界

- 只读诊断：脚本零凭证输出（不打印 key / Authorization / 响应体），不写任何文件
- 凭证来源：环境变量 `GLM_CONDUCTOR_QUOTA_API_KEY`（优先），或已登录 ZCode 的 `~/.zcode/v2/config.json`（provider `builtin:bigmodel-coding-plan`）回退
- 若 `python3` 不可用：按 enforcement 技能「环境自检」节处理，不要降级到猜测或跳过
