# Changelog

## 1.1.0

v1.0 发布前审计整改（release remediation）：

- **视觉拓扑修正（P0）**：视觉反馈环改为跨多次调用的显式协议——实施者需要截图时返回 `VISUAL_CAPTURE_REQUEST` 并结束本次调用，主会话采集后恢复实施者读图判定，不再假设父子代理存在调用中握手机制（每验收点最多 3 轮）；子代理浏览器/桌面通道经实测确认被 ZCode 策略层禁止（Browser Use / Computer Use 为主会话专用），"主会话采集 + Flash 读图"是唯一受支持的视觉拓扑
- **Continuity 任务隔离（P0）**：checkpoint 从工作区全局单文件改为任务专属 `.glm-conductor/tasks/<continuity-id>/checkpoint.md`；引入稳定的 CONTINUITY_ID（并行长任务互不覆盖、互不删除，"最新 checkpoint"不再是查找策略）；结构化 resume prompt 携带精确 ID 与路径；完成清理只作用于本任务目录与其关联 automation；视觉证据目录同样按任务隔离
- **运行时契约加固（P1）**：同会话投递要求从当前聊天创建绑定会话的定时续作；调度触发本身即存活探针（不引入独立额度检查器）；README 新增"运行时限制"章节；`.glm-conductor/` Git 非侵入政策（优先 `.git/info/exclude`，不自动修改 tracked `.gitignore`）
- **术语一致（P1）**：残留的 v1「升级信号 / 上报升级」措辞统一替换为「重估信号 / ROUTE REASSESSMENT 请求」，闪现实施者 description 同步修正
- **视觉审查者收紧（P1）**：visual-reviewer 定位为以独立视觉验收为主职（代码 diff 仅作上下文，不作为更强的通用代码审查者）；输出格式 `GLM REVIEW` → `VISUAL REVIEW`（含 `RESIDUAL VISUAL RISK` 字段）；文本审查者 glm-reviewer 保持 `GLM REVIEW`
- **市场描述更新（P1）**：marketplace 插件条目描述补全视觉通道与长任务连续性
- **静态校验器与 CI（P2）**：新增 `scripts/validate_plugin.py`（纯标准库 8 项检查：JSON 合法性、agent/skill frontmatter 必填字段、引用 agent 存在、命名一致、v1 禁词、continuity 安全、视觉协议名一致）与 `.github/workflows/validate.yml`
- **发布质量（P2）**：README 声明 Tested with ZCode 3.9.2；「成本优化分工」重定位为「分层执行能力」，成本优势作为次级收益呈现

## 1.0.0

- 项目更名：glm-advisor → **glm-conductor**（系统角色已从"给主模型建议"演化为完整的编排层：route → assign → execute → verify → review → resume）
- Tagline：Selective orchestration for GLM coding agents in ZCode.
- 本地数据目录同步更名：`.glm-conductor/`（checkpoint 与视觉证据）

## 0.3.0

- 新增长任务连续性技能（continuity）：foreground / resumable / idle 三种模式，与路由正交
- CONTINUITY CHECKPOINT 结构化检查点；恢复时 repository 优先于 checkpoint
- 八步恢复流程 + 自包含结构化 resume prompt，映射 ZCode 定时任务与闲时任务
- 显式的额度 API 边界：不虚构 quota 接口、不硬编码 5 小时重置
- 完成后清理机制，防止幽灵唤醒

## 0.2.0

- 新增视觉通道：visual-implementer（GLM-5.3-Flash 多模态实施者）与 visual-reviewer（GLM-5.3-Flash 只读审查者）
- 视觉分工：主会话（纯文本）负责驱动采集截图，Flash 多模态角色负责视觉判定
- 五段式规格新增 VISUAL ACCEPTANCE 扩展；每个验收点最多 3 轮修正，截图不可得即 fail-closed
- 审查者按任务模态选择：文本任务 → glm-reviewer，视觉任务 → visual-reviewer

## 0.1.0

- 初始发布：双轴选择性路由（Delegability × Assurance → solo / delegate / audit / full）
- GLM-5.3 主会话任架构师；flash-implementer（GLM-5.3-Flash）执行有界实施
- 六字段 SELECTIVE ROUTE 声明；基于证据的双向 ROUTE REASSESSMENT
- 五段式实施规格 + IMPLEMENTATION REPORT；无证据的完成声明无效
- 受 DannyMac180/sol-advisor 启发
