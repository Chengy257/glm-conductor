# Changelog

## 2.0.0-alpha1

v2「从提示词契约到强制执行契约」——alpha1 强制基座达成（运行时状态层 + Ownership Layer A/B + 执行日志；依据 docs/glm-conductor-v2-upgrade-guide-final.md 与实施计划，Phase 0 运行时验证先行）：

- **任务标识更名**：`CONTINUITY_ID` → `TASK_ID`（v1.x 遗留 checkpoint 读取时归一化，无需重写）；路径占位统一为 `<task-id>`
- **运行时状态层（`runtime/state.py`）**：`state.json` 作为强制状态源——schema 校验（路由五字段 / ownership.files / verification / review / 13 态生命周期词汇）、原子保存、`CONTINUITY_ID` legacy 归一、活动任务发现；创建 state.json 即受完成门跟踪，foreground 普通短任务零干预
- **执行日志（`runtime/journal.py`）**：任务专属 `events.jsonl`，append-only（追加唯一写入口、时间戳由模块管理）、坏行容错读取（撕裂 UTF-8 尾部 / U+2028 行分隔符不丢事件）、尾部查询；无秘密值、无完整 prompt
- **Ownership Gate Layer A（完成门，`hooks/stop_gate.py` + `runtime/ownership.py`）**：Stop 时对声明了 ownership 的活动任务校验「git 改动文件 ⊆ 声明范围」，越界即 block（报文列出精确 out-of-scope 路径与两条出路）；声明形式支持精确文件 / 目录前缀（段级匹配）/ glob（`**` 跨段）；`.glm-conductor/` 运行时目录豁免（防自指拦截）；续行有界——连续两次 block 后放行并报 `ENFORCEMENT GATE EXHAUSTED`（模型义务：向用户报告 blocked，不得声称完成）
- **Ownership Gate Layer B（派发注入，`hooks/pre_tool_use.py`）**：PreToolUse（Agent|Task）在每次子代理派发前注入 ownership 契约提醒（提示级，确定性强制在 Layer A）
- **fail-open 降级可见**：钩子崩溃 / git 不可用时放行 + stderr 报 `ENFORCEMENT DEGRADED` + journal 记 `gate_degraded`——强制层故障不卡死会话，降级绝不静默
- **技能契约增补**：continuity（state.json / events.jsonl 创建时机、事件时点表、恢复读取顺序）、orchestration（route 落盘、ownership.files 同步义务）、新增 `enforcement` 技能（强制层用户侧解释：环境自检 / 报文含义 / 被拦截恢复方法）
- **静态校验器扩展至 14 项**：新增钩子清单完整性（事件合法 / python3 入口 / `${ZCODE_PLUGIN_ROOT}` 脚本存在 / Stop 必声明）与 runtime 状态层标记检查；CI 增加单元测试步骤
- **测试**：146 个单元/集成用例（状态层 63、日志 26、ownership 38、stop_gate 12、pre_tool_use 7，含真实 git fixture 五状态、子进程冒烟与多任务记账回归）
- Ownership 设计依据 Phase 0 修订：子代理工具调用不触发钩子（实测+代码级确证），故强制位于完成边界与派发时点（主会话上下文），写前拦截留待 ZCode 运行时演进（feature request 跟踪）

## 1.1.0

v1.1.0 — v1.0 发布后的审计整改与运行时加固（release hardening）：

- **视觉拓扑修正（P0）**：视觉反馈环改为跨多次调用的显式协议——实施者需要截图时返回 `VISUAL_CAPTURE_REQUEST` 并结束本次调用，主会话采集后恢复实施者读图判定，不再假设父子代理存在调用中握手机制（每验收点最多 3 轮）；子代理浏览器/桌面通道经实测确认被 ZCode 策略层禁止（Browser Use / Computer Use 为主会话专用），"主会话采集 + Flash 读图"是唯一受支持的视觉拓扑
- **Continuity 任务隔离（P0）**：checkpoint 从工作区全局单文件改为任务专属 `.glm-conductor/tasks/<continuity-id>/checkpoint.md`；引入稳定的 CONTINUITY_ID（并行长任务互不覆盖、互不删除，"最新 checkpoint"不再是查找策略）；结构化 resume prompt 携带精确 ID 与路径；完成清理只作用于本任务目录与其关联 automation；视觉证据目录同样按任务隔离
- **运行时契约加固（P1）**：同会话投递要求从当前聊天创建绑定会话的定时续作；调度触发本身即存活探针（不引入独立额度检查器）；README 新增"运行时限制"章节；`.glm-conductor/` Git 非侵入政策（优先 `.git/info/exclude`，不自动修改 tracked `.gitignore`）
- **术语一致（P1）**：残留的 v1「升级信号 / 上报升级」措辞统一替换为「重估信号 / ROUTE REASSESSMENT 请求」，闪现实施者 description 同步修正
- **视觉审查者收紧（P1）**：visual-reviewer 定位为以独立视觉验收为主职（代码 diff 仅作上下文，不作为更强的通用代码审查者）；输出格式 `GLM REVIEW` → `VISUAL REVIEW`（含 `RESIDUAL VISUAL RISK` 字段）；文本审查者 glm-reviewer 保持 `GLM REVIEW`
- **市场描述更新（P1）**：marketplace 插件条目描述补全视觉通道与长任务连续性
- **静态校验器与 CI（P2）**：新增 `scripts/validate_plugin.py`（纯标准库 8 项检查：JSON 合法性、agent/skill frontmatter 必填字段、引用 agent 存在、命名一致、v1 禁词、continuity 安全、视觉协议名一致）与 `.github/workflows/validate.yml`
- **发布质量（P2）**：README 声明 Tested with ZCode 3.9.2；「成本优化分工」重定位为「分层执行能力」，成本优势作为次级收益呈现
- **架构真相源（P0/P1）**：新增权威架构文档 `docs/architecture.md`（与 v1.1 运行时契约一致）；pre-v1.1 架构提案移入 `docs/history/` 并标注 SUPERSEDED，不再作为实现依据
- **CONTINUITY_ID 机械唯一（P1）**：ID 格式改为「语义前缀 + 6~8 位随机十六进制后缀」——并行同时创建的任务不再依赖命名约定防碰撞；生成、持久化、恢复、清理全程使用同一精确 ID
- **视觉新调用规范化（P1）**：视觉反馈环的续作以「携带完整状态的新调用」为规范路径（规格、当前 diff、上一轮 VISUAL_CAPTURE_REQUEST、截图路径、VISUAL_ROUND 轮次号）；子代理 resume 仅为可选优化，协议正确性不依赖 resume
- **校验器扩展（P2）**：权威架构文档纳入静态校验扫描（`docs/history/` 排除）；新增 CONTINUITY_ID 与视觉规范措辞必含检查、plugin.json 与 CHANGELOG 版本一致性检查

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
