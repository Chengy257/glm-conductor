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

## 边界

- 只读诊断：脚本零凭证输出（不打印 key / Authorization / 响应体），不写任何文件
- 凭证来源：环境变量 `GLM_CONDUCTOR_QUOTA_API_KEY`（优先），或已登录 ZCode 的 `~/.zcode/v2/config.json`（provider `builtin:bigmodel-coding-plan`）回退
- 若 `python3` 不可用：按 enforcement 技能「环境自检」节处理，不要降级到猜测或跳过
