---
name: quota
description: GLM Coding Plan 额度只读观测（v2.4 终态：两个只读面）。runtime/quota/report.py 出人读/JSON 诊断（额度窗口、状态评估与恢复建议），runtime CLI quota-resolve 出编排决策用的四态解析（AVAILABLE/PRESSURE/EXHAUSTED/UNKNOWN）。用户询问额度余量、5-hour/weekly 窗口、reset 时间、额度压力或派发/恢复前的额度状态时使用。
---

# GLM Coding Plan 额度观测（只读）

v2.4 的额度面收敛为**两个只读观测面**：诊断面（report.py）与解析面（quota-resolve）。两者都不做任务转态、不做拦截决策、零凭证输出。

## 面 1：report.py（人读诊断，`/glm-conductor:quota` 的本体）

用 Bash 运行诊断脚本，把输出**原样**呈现给用户（不转述、不截断、不添加解读性前缀）：

```
python3 ~/.zcode/cli/plugins/cache/glm-conductor/glm-conductor/<version>/runtime/quota/report.py
```

机器可读（供编排决策）加 `--json`：

```
python3 ~/.zcode/cli/plugins/cache/glm-conductor/glm-conductor/<version>/runtime/quota/report.py --json
```

`<version>` 用插件缓存目录下的实际版本号（`ls ~/.zcode/cli/plugins/cache/glm-conductor/glm-conductor/` 可见）。

解读：

- 文本输出：各额度窗口（`5-hour` / `weekly`）的 used/remaining/reset、`status:`（AVAILABLE / PRESSURE / EXHAUSTED / UNKNOWN）与 `plan:`（continue / checkpoint / resume_at / periodic_fallback）恢复建议
- **lite 套餐**：只有 5 小时窗、无周窗——weekly 一行显示 not present；这是正常形态，不是探测失败
- 三种诊断态：正常额度报告 / `quota 探测失败`（每 provider 一行错误分类）/ `unavailable`（未找到凭证，按输出中的配置指引处理）
- 退出码恒 0：本命令是只读诊断面，不是闸门；不要据退出码做拦截决策

## 面 2：quota-resolve（编排四态解析）

供主会话在派发前 / 恢复决策前取额度状态：

```
python3 <插件根>/runtime/cli.py quota-resolve <repo_root> [--force-refresh]
```

- 输出冻结键单行 JSON：`{status, source, evaluated_at, reason}`；status ∈ AVAILABLE / PRESSURE / EXHAUSTED / UNKNOWN
- 解析层级：新鲜缓存（≤300s）→ provider 抓取 → 陈旧缓存 → UNKNOWN——**绝不默认 AVAILABLE、绝不重试网络**；`--force-refresh` 跳过新鲜缓存层强制走 provider（恢复决策前建议使用，不信任休眠期缓存）
- provider 成功时会更新本地额度缓存（观测缓存，非任务状态）；**零任务转态**——它只回答"额度现在什么状态"，状态迁移与恢复决策属于 runtime.task 的生命周期 API（见 continuity 技能）
- UNKNOWN 不是错误：凭证不可得 / provider 不可用时按不可知处理，编排侧继续等待或问用户，不虚构可用性
- 退出码：0 成功 / 2 用法错误 / 1 运行期异常（错误 JSON 只含异常类型名与消息）

## 边界

- 只读观测：两个面都不转态、不拦截；脚本零凭证输出（不打印 key / Authorization / 响应体）
- 凭证来源：环境变量 `GLM_CONDUCTOR_QUOTA_API_KEY`（优先），或已登录 ZCode 的 `~/.zcode/v2/config.json`（provider `builtin:bigmodel-coding-plan`）回退
- 额度观测只作为证据使用：不作为路由轴、不据此绕过授权；恢复决策走 continuity 技能的幂等决策契约
- 无常驻观测进程、无轮询服务——查询只发生在刷新点（任务开始 / 派发前 / 恢复决策前）
- 窗口机制口径：reset_at 时刻窗口恢复 100%（周窗优先）；下一个 reset_at 只在新窗口内发生模型调用时才物化——纯查询绝不推进它
- 若 `python3` 不可用：按 enforcement 技能「环境自检」节处理，不要降级到猜测或跳过
