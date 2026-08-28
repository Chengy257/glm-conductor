# 运行细节

本文件是 glm-conductor 编排体系的运行细节文档，SKILL.md 与 role-contracts.md 之外的执行机制在此说明。

## 预检与生成

- 按声明的 executor 与审查者核对角色是否存在于 Agent 工具的可用类型列表：
  - delegate/full（标准任务）需要 `glm-conductor:flash-implementer`
  - delegate/full（视觉任务）需要 `glm-conductor:visual-implementer`
  - assurance:high（文本任务）需要 `glm-conductor:glm-reviewer`
  - assurance:high（视觉任务）需要 `glm-conductor:visual-reviewer`
- solo + assurance:standard 无需任何子智能体预检
- 缺失时的 fail-closed 处理：停止该通道，告知用户检查插件安装（Settings → Plugin Management），不得静默替换为其他子智能体类型
- 调用时不得附加模型/思考档位覆盖：各角色定义已固定（GLM-5.3-Flash/high、GLM-5.3/max），任何运行时覆盖都会破坏本体系的双轴假设

## 双轴判断纪律

- 先独立判断 Delegability 与 Assurance，再查路由矩阵；不得从一轴推导另一轴
- 声明后不得无证据变更路由；变更必须走 ROUTE REASSESSMENT 块并附新观察到的证据
- 实施者返回重估信号、审查者给出 rethink 或多项 fix-first，均构成有效重估证据

## 验证证据

主会话的验证义务（不可由工作者报告替代）：

1. 亲自检查完整 diff（git diff 或逐文件读取），核对改动是否越界
2. 重跑 VERIFICATION 指定的全部命令
3. 将每条命令映射到主会话实际观察到的输出，之后才能交给审查者作为 VERIFICATION EVIDENCE

工作者的 IMPLEMENTATION REPORT 仅是声明，不是证据；证据只存在于主会话亲自观察到的 diff 与命令输出中。

视觉任务的补充：主会话可承担驱动与采集（截图落盘），但视觉判定证据来自 Flash 系角色对截图文件的读取结果；主会话不得以文字推测替代视觉判定。

## 视觉通道运行细节

- **采集分工**：主会话用其专属的 Browser/Computer Use 能力启动应用、执行交互、截图落盘。证据目录按任务隔离：`<工作区>/.glm-conductor/tasks/<task-id>/visual-evidence/`（task-id 由主会话按 continuity 技能的 TASK_ID 规则生成——语义前缀+随机后缀；foreground 一次性任务也分配同格式的临时 ID，TASK_ID 即通用运行时任务标识，保证目录不混用；任务结束由用户决定保留或删除）。主会话只采集不判定（纯文本模型）
- **判定分工**：visual-implementer 与 visual-reviewer 用 Read 亲自读取截图后判定；采集而不查看不算观察
- **跨调用握手**：实施者以 `VISUAL_CAPTURE_REQUEST` 结束本次调用来索取证据，主会话采集后发起**新的 visual-implementer 调用**（规范路径）——新调用携带完整状态：五段式规格（含 VISUAL ACCEPTANCE）、当前 diff / 改动状态、上一轮的 VISUAL_CAPTURE_REQUEST、截图路径清单、VISUAL_ROUND 轮次号。仅当 ZCode 运行时已验证稳定的子代理 resume 机制时，才可作为可选优化 resume 原实施者，协议正确性不依赖 resume。不存在子代理在单次调用内等待父会话的通道（协议格式见 agents/visual-implementer.md）
- **修正轮次**：每个 VISUAL ACCEPTANCE 验收点以 VISUAL_ROUND（1|2|3）跟踪，最多 3 轮实施-采集-判定循环；超出即 blocked 交回主会话做 ROUTE REASSESSMENT
- **fail-closed**：主会话无法采集（无浏览器/桌面能力或应用无法启动）时，视觉通道停止，不得改为纯文本验证交付

## 修复与裁决失效流程

fix-first 之后必须按以下顺序执行（两种修正路径）：

1. **audit 路由**：主会话自行修正
2. **full 路由**：由原实施者修正
3. 主会话重跑全部验证命令，确认新证据
4. 换用全新 reviewer 复审（ZCode 子智能体天然新上下文，直接再次调用对应审查者即可）
5. 旧裁决作废，不作为新审查的输入

rethink 裁决：修订架构后重新走对应路由（必要时先做 ROUTE REASSESSMENT），不得报告完成。

## 恢复会话的路由恢复

从 checkpoint 恢复的任务不得沿用旧声明盲目执行，必须依次：

1. 读取本任务 CONTINUITY CHECKPOINT（按 TASK_ID 定位，若有）
2. 检查当前仓库状态与 diff，确认先前变更是否仍在（repository > checkpoint）
3. 检查验证状态与目标完成度
4. 重新输出 SELECTIVE ROUTE 声明（沿用或基于新证据重估），再继续执行

checkpoint 机制与恢复流程详见 `skills/continuity`。

## 与 ZCode 机制的适配说明

- 子智能体内不能再派生子智能体，因此结构天然扁平：主会话是唯一编排者
- 子智能体只能看到主会话启动时已连接的 MCP 服务
- Browser Use 与 Computer Use 为主会话专用：视觉证据的采集由主会话执行，截图落盘后由 Flash 系角色读取判定
- 修改插件文件后需更新 marketplace 并新建会话才能生效

## 故障处理

- **插件角色缺失**：所需子智能体不在可用类型列表时，停止该通道，告知用户检查插件安装，必要时重装后新建会话
- **审查者缺失**（audit/full）：不得降级为无审查交付；保留当前进度并告知用户
- **视觉证据不可得**：视觉通道停止（实施者返回 blocked），不得改用文字推测界面正常，也不得把视觉任务静默改为纯文本验证
- **模型名不符**：可用类型中的定义若显示非预期模型，按 fail-closed 处理并告知用户
- **审查者尝试越权写操作**：立即终止该通道，丢弃其全部输出，改用全新审查者重新审查
