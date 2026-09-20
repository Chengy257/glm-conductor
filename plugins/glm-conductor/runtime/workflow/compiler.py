#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.4 原生 Workflow 编译器（Phase 1 工作包 P1-D，单元 U4）。

职责：
    compile_workflow(nodes, task_context)：把静态节点 DAG
    （runtime.work_unit 声明 + runtime.dependency 图语义）确定性地编译
    为面向宿主 dynamic-workflow facade 的 TypeScript Workflow 源码字符串。
    本模块只生成源码、只做静态断言——绝不启动任何 Workflow，也不对生成
    源求值（执行是主会话的事）。

编译管线（顺序固定）：
    1. 节点先过 work_unit.validate_node（逐节点形状）与
       dependency.graph_errors（全图：重复 id / 缺失依赖 / 自依赖 / 环），
       再校验 task_context（至少含 task_ref / goal / repository 三个非空
       字符串）；任一错误聚合抛 WorkflowCompileError；
    2. ownership.plan_stages 规划阶段（组内 scope 两两不相交）：
       ownership 歧义 / 非法模式的冲突以 ownership.OwnershipConflictError
       原样上抛（冲突即抛，绝不猜测）；scope 明确重叠的候选由 U3 侧
       确定性串行（排进后续组），不是错误；
    3. 逐组生成：每组一个 phase 调用（名称「阶段 i: 节点 id 列表」，
       i 从 1 起）+ 一个 await Promise.all（组内并行）；组间是顺序的
       顶层 await 语句；每节点恰好一个 agent 调用（actor 标签为
       node-<节点 id 原文>，persona 用 persona.TEXT_WORKER_PERSONA），
       并在其返回的 actor 上链式调用 .ask<NodeResult>(指令)，ask 指令
       由 persona.build_ask 生成；
    4. 末尾 return 聚合全部节点结果（按节点 id 升序，经 allResults
       结果映射按键取值）。

生成源契约（W0 宿主事实，docs/reviews/ZCODE_3_14_NATIVE_WORKFLOW_RESULTS.md
§2.1 / §3 WF-01/02）：
    - facade 五面无 import 可用：agent(name, persona)、phase(name)、
      Promise.all、顶层 return，以及 ask——但 ask 不是自由函数，而是
      agent 返回的 actor 的方法：观测形态为
      agent("名字", persona).ask<T>(instructions)（生成源只做静态断言，
      宿主编译器是最终裁决）；
    - 结果接口名恰为 NodeResult，字段恰六项：node_id / status
      （"complete" | "partial" | "blocked"）/ changes / local_checks /
      reassessment / gaps（见 NODE_RESULT_FIELDS）；
    - 确定性：无时间戳、无随机数；同一输入两次编译字节全等；
    - Conductor 节点 id 原样出现在 actor 标签、ask 指令与结果映射中；
    - 绝不内嵌凭证或密钥；生成源绝不出现 permit / lease 词汇（U4 禁词
      扫描锚定；persona 与 ask 文本同受此约束）。

依赖：
    仅 Python 3 标准库（json）+ runtime.work_unit / runtime.dependency /
    runtime.ownership / runtime.workflow.persona（均为 Phase 1 新静态面；
    禁止导入任何 v2.3 执行面模块，已全部退役）。

来源：
    docs/roadmap/V2_4_PHASE_1_NATIVE_SEMANTIC_CORE_SPEC.md §5（compiler.py）
    + docs/roadmap/V2_4_PHASE_1_WORKFLOW_EXECUTION_PLAN.md U4。
"""

import json

from runtime import dependency, ownership, work_unit
from runtime.workflow.persona import TEXT_WORKER_PERSONA, build_ask


class WorkflowCompileError(Exception):
    """Workflow 编译的结构性错误（节点形状 / 依赖图 / task_context 非法）。

    ownership 歧义冲突不在此列——那由 ownership.plan_stages 以
    OwnershipConflictError 原样上抛（冲突即抛，本模块不转译、不吞并）。
    """


# NodeResult 接口的恰六字段（顺序即生成源中的字段声明顺序）
NODE_RESULT_FIELDS = (
    "node_id", "status", "changes", "local_checks", "reassessment", "gaps",
)
# status 的三值字面量联合（生成源原样形态）
STATUS_UNION = '"complete" | "partial" | "blocked"'
# task_context 必备键（至少含这三个非空字符串）
TASK_CONTEXT_REQUIRED_KEYS = ("task_ref", "goal", "repository")


# —— 序列化 / 校验小助手 ——

def _ts_string(text: str) -> str:
    """把 Python 字符串确定性地序列化为 TypeScript 字符串字面量。

    json.dumps（ensure_ascii=False）的产物是合法的 TS/JS 双引号字符串
    字面量：引号 / 反斜杠 / 控制字符被转义，其余字符原样保留——多行
    persona 与 ask 文本因此以 \\n 形式内嵌，不破坏生成源结构。
    """
    return json.dumps(text, ensure_ascii=False)


def _comment_text(value) -> str:
    """头部行注释用单行文本：折叠全部空白（换行会破坏 // 行注释）。"""
    return " ".join(str(value).split())


def _task_context_errors(task_context) -> "list[str]":
    """校验 task_context：必须是 dict 且必备键均为非空字符串。"""
    if not isinstance(task_context, dict):
        return ["task_context 必须是 JSON 对象，得到 %s"
                % type(task_context).__name__]
    errors = []
    for key in TASK_CONTEXT_REQUIRED_KEYS:
        value = task_context.get(key)
        if not isinstance(value, str) or value == "":
            errors.append("task_context.%s 必须是非空字符串" % key)
    return errors


def _validate_inputs(nodes, task_context) -> None:
    """编译前校验（节点形状 → 全图 → task_context），错误聚合抛出。"""
    errors = []
    if not isinstance(nodes, list):
        errors.append("nodes 必须是数组，得到 %s" % type(nodes).__name__)
    else:
        for node in nodes:
            errors.extend(work_unit.validate_node(node))
        errors.extend(dependency.graph_errors(nodes))
    errors.extend(_task_context_errors(task_context))
    if errors:
        raise WorkflowCompileError(
            "compile_workflow：输入校验失败（共 %d 项）：%s"
            % (len(errors), "；".join(errors)))


# —— 代码生成 ——

def _emit_stage(stage_number, node_ids, by_id, task_context) -> "list[str]":
    """生成一个阶段的代码：phase 标记 + await Promise.all（组内每节点
    恰一个 agent + ask 调用）。组内节点按 id 升序展开（确定性）。"""
    ordered = sorted(node_ids)
    lines = []
    lines.append("// —— 阶段 %d：%s ——"
                 % (stage_number, "、".join(ordered)))
    lines.append("phase(%s);"
                 % _ts_string("阶段 %d: %s"
                              % (stage_number, ", ".join(ordered))))
    lines.append("await Promise.all([")
    for node_id in ordered:
        actor = "node-%s" % node_id
        instruction = build_ask(by_id[node_id], task_context)
        lines.append("  // 节点 %s（actor 标签 %s；"
                     "结果映射键为节点 id 原文）" % (node_id, actor))
        lines.append("  agent(%s, persona).ask<NodeResult>(%s)"
                     % (_ts_string(actor), _ts_string(instruction)))
        lines.append("    .then((result: NodeResult): void => "
                     "{ allResults[%s] = result; }),"
                     % _ts_string(node_id))
    lines.append("]);")
    return lines


def compile_workflow(nodes, task_context) -> str:
    """把静态节点 DAG 编译为 TypeScript Workflow 源码（返回 str）。

    - 管线与生成源契约见模块 docstring；
    - nodes：静态节点 dict 的数组（runtime.work_unit 形状）；
      task_context：至少含 task_ref / goal / repository 三个非空字符串
      的 dict（多余键忽略，向前兼容）；
    - 确定性：同一输入两次调用返回逐字节相同的字符串；
    - 错误：形状 / 图 / task_context 非法 → WorkflowCompileError（聚合
      中文消息）；ownership 歧义 / 非法 → ownership.OwnershipConflictError
      原样上抛；
    - 空 DAG 允许：生成零阶段脚本，return 聚合为空数组。
    """
    _validate_inputs(nodes, task_context)
    # 冲突即抛：ownership 歧义 / 非法模式在此原样上抛 OwnershipConflictError
    stages = ownership.plan_stages(nodes)
    by_id = {}
    for node in nodes:
        by_id[node["id"]] = node  # id 唯一性已由图校验保证
    all_ids = sorted(by_id)

    lines = []
    lines.append("// " + "=" * 60)
    lines.append("// GLM Conductor v2.4 原生 Workflow 编译产物"
                 "（runtime/workflow，P1-D）")
    lines.append("// task_ref：%s" % _comment_text(task_context["task_ref"]))
    lines.append("// goal：%s" % _comment_text(task_context["goal"]))
    lines.append("// repository：%s"
                 % _comment_text(task_context["repository"]))
    lines.append("// 节点数 %d；阶段数 %d（阶段序列来自 ownership 编译期"
                 "规划：组内并行、组间顺序等待）"
                 % (len(all_ids), len(stages)))
    lines.append("// 本产物为确定性生成：无时间戳、无随机数；"
                 "同一输入两次编译字节全等。")
    lines.append("// Conductor 节点 id 原样出现在 actor 标签、"
                 "ask 指令与结果映射中。")
    lines.append("// " + "=" * 60)
    lines.append("")
    lines.append("// 每节点 ask 的返回契约：结构化实施结果（恰六字段）")
    lines.append("interface NodeResult {")
    lines.append("  node_id: string;")
    lines.append("  status: %s;" % STATUS_UNION)
    lines.append("  changes: string[];")
    lines.append("  local_checks: string[];")
    lines.append("  reassessment: string;")
    lines.append("  gaps: string[];")
    lines.append("}")
    lines.append("")
    lines.append("// 文本工人 persona（唯一权威来源："
                 "runtime/workflow/persona.py）")
    lines.append("const persona: string = %s;"
                 % _ts_string(TEXT_WORKER_PERSONA))
    lines.append("")
    lines.append("// 全部节点结果映射（键 = Conductor 节点 id 原文）")
    lines.append("const allResults: { [nodeId: string]: NodeResult } = {};")
    for stage_number, node_ids in enumerate(stages, start=1):
        lines.append("")
        lines.extend(_emit_stage(stage_number, node_ids, by_id, task_context))
    lines.append("")
    lines.append("// —— 最终聚合返回：全部节点结果按节点 id 升序 ——")
    if all_ids:
        lines.append("return [")
        for node_id in all_ids:
            lines.append("  allResults[%s]," % _ts_string(node_id))
        lines.append("];")
    else:
        lines.append("return [];")
    return "\n".join(lines) + "\n"
