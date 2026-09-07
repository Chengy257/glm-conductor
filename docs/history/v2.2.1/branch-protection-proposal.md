# main 分支保护配置方案（v2.2.1 WU-221-F3）

> **状态：仅方案交付，未应用。** 应用须用户当次明确批准（D8 裁决）；批准后由主会话经 `gh api` 一次性落地，命令逐条列于文末。

## 设计依据

本仓库大量改动由编码智能体执行——机械化的分支保护与项目的「运行时强制层」哲学同构：规则化门禁优先于纪律自觉。

## 推荐规则（main 分支）

| 规则 | 建议值 | 理由 |
| --- | --- | --- |
| 要求 Pull Request | 是（1 个 approving review 可选；当前单人仓库可置 0） | 保持「PR → 四路 CI → merge」的既有发布链形态 |
| 必需状态检查 | `Static plugin validation (ubuntu-latest, Python 3.8)` 等 4 腿全部 required | validator + ruff + smoke + unittest 全绿才可合并 |
| 严格必需检查 | 是（branch up-to-date before merging） | 避免检查跑在旧 head 上 |
| 禁止 force push | 是（含 administrators） | 已发布历史不可重写（§6 git 纪律） |
| 禁止删除/改名的分支 | main（含 administrators） | 稳定真相源不可移除 |
| owner/admin 旁路 | 保留 GitHub 默认（admin 可 bypass，用于紧急恢复） | 应急通道留白，不做完全锁死 |

## 应用命令（批准后执行，逐条）

```bash
# 1) 规则主体（PR 必须 + 4 腿 required + strict + 禁 force + 禁删除）
gh api -X PUT repos/Chengy257/glm-conductor/branches/main/protection   -f required_status_checks[strict]=true   -f required_status_checks[contexts][]='Static plugin validation (ubuntu-latest, Python 3.8)'   -f required_status_checks[contexts][]='Static plugin validation (ubuntu-latest, Python 3.13)'   -f required_status_checks[contexts][]='Static plugin validation (windows-latest, Python 3.8)'   -f required_status_checks[contexts][]='Static plugin validation (windows-latest, Python 3.13)'   -f enforce_admins=true   -F required_pull_request_reviews=null   -F restrictions=null
# 2) 如需 review 门槛，将 required_pull_request_reviews 换为：
#   -F required_pull_request_reviews[required_approving_review_count]=0
```

注：`enforce_admins=true` 同时对管理员禁 force push；若用户希望保留 admin 应急旁路，改设 `enforce_admins=false` 并在批准时说明。

## 回滚

应用后如需撤销：`gh api -X DELETE repos/Chengy257/glm-conductor/branches/main/protection`（管理面操作，不影响任何提交历史）。
