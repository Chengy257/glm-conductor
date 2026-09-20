#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.4 原生 Workflow 文本工人 persona（Phase 1 工作包 P1-D，单元 U4）。

职责：
    普通文本实施通道的唯一权威 persona 来源：
      - TEXT_WORKER_PERSONA：文本工人的系统级人设与执行纪律，供
        compiler 生成的 Workflow 脚本以 actor persona 形式内嵌
        （W0 事实：宿主 runtime 不消费插件 agent 定义，角色仿真必须
        把 persona 文本复制进脚本——docs/reviews/
        ZCODE_3_14_NATIVE_WORKFLOW_RESULTS.md §3 WF-02）；
      - build_ask(node, task_context)：单节点 ask 指令文本——objective、
        ownership、interfaces、constraints、local_check 与结果契约
        逐节展开，随节点经 ask 下发。

语义来源：
    自 v2.3 实施者代理定义迁移语义：只执行所给有界 objective；
    尊重 ownership / interfaces / constraints；不重新设计架构；模糊处
    返回 reassessment 重估请求而非自行决策；诚实运行可选 local_check；
    返回结构化实施结果。不迁移宿主会话专用的旧词法——本模块文本与
    compiler 生成源零 permit / receipt / lease 词汇（U4 禁词扫描锚定）。

依赖：
    零导入（纯字符串构造），与 runtime 其他模块无导入关系。

来源：
    docs/roadmap/V2_4_PHASE_1_NATIVE_SEMANTIC_CORE_SPEC.md §5（persona.py）
    + docs/roadmap/V2_4_PHASE_1_WORKFLOW_EXECUTION_PLAN.md U4。
"""


# 文本工人 persona（唯一权威来源；compiler 原样内嵌进生成脚本）
TEXT_WORKER_PERSONA = """\
你是 GLM Conductor 原生 Workflow 中的文本实施工人（text worker）。主会话\
已完成规划、任务分解与路由决策，并向你交付一个有界 objective；你的职责\
是精确执行该 objective 本身，而不是重新设计架构。

执行纪律：
- 只执行所交付的有界 objective：objective 之外的架构决策、范围扩张与顺手重构一律不做。
- 严格尊重 ownership 声明：只改动 ownership 覆盖的仓库相对路径，范围外文件一律不碰。
- 遵守声明的 interfaces 与 constraints：不破坏任何要求保持兼容的签名、类型、模式或命令。
- 同一仓库可能有其他代理并行编辑：不回退他人的无关改动，不整理与本 objective 无关的代码。
- 遇到实质性歧义、范围冲突或规格缺漏时不擅自决策：在结果的 reassessment 字段返回明确的重估请求，交回主会话裁决。
- local_check 非空时诚实逐项执行并如实记录结果与失败；local_check 为空时不虚构任何检查。不得伪造输出，不得省略失败。

返回契约：
- 无论完成、部分完成还是受阻，都按 ask 指令给出的结构化实施结果返回；无证据的完成声明无效。"""


def _str_items(value) -> "list[str]":
    """容错读取字符串数组字段：非 list → 空列表；非字符串项转字符串。"""
    if not isinstance(value, list):
        return []
    return [item if isinstance(item, str) else str(item) for item in value]


def _context_value(task_context, key) -> str:
    """容错读取 task_context 键：缺失 / 非字符串 / 空串 → 占位文本。"""
    if not isinstance(task_context, dict):
        return "（未提供）"
    value = task_context.get(key)
    if not isinstance(value, str) or value == "":
        return "（未提供）"
    return value


def _append_section(lines, title, items, empty_note) -> None:
    """追加一个「标题 + 逐条列表 / 空缺占位 + 空行」小节（保持确定性输出）。"""
    lines.append(title)
    if items:
        for item in items:
            lines.append("- %s" % item)
    else:
        lines.append(empty_note)
    lines.append("")


def build_ask(node, task_context) -> str:
    """构造单节点 ask 指令文本（返回多行字符串，内容确定性）。

    逐节展开（节点可选字段缺省时给「未声明」占位，不省略小节——worker
    拿到的指令形状恒定）：
      - 开头点名节点 id 与有界任务口径；
      - objective（只执行此目标，不重新设计架构）；
      - ownership（只能改动的仓库相对路径清单）；
      - interfaces（须保持兼容的接口面）；
      - constraints（额外约束）；
      - local_check（可选本地检查：诚实执行，为空不虚构）；
      - 任务背景（task_ref / goal / repository 回显）；
      - 结果契约：NodeResult 六字段说明（node_id 用节点 id 原文、status
        三值、changes、local_checks、reassessment、gaps）。

    node 必须是静态节点 dict（runtime.work_unit 形状；非 dict 抛
    TypeError）。编译器已先做 validate_node 校验，本函数对其余键只做
    容错读取，不做二次校验。
    """
    if not isinstance(node, dict):
        raise TypeError(
            "build_ask：node 必须是静态节点 dict，得到 %s" % type(node).__name__)
    node_id = node.get("id")
    node_label = (node_id if isinstance(node_id, str) and node_id
                  else "<missing-id>")
    objective = node.get("objective")
    objective_text = (
        objective if isinstance(objective, str) and objective
        else "（本节点未声明 objective——如交付指令确无目标，"
             "按 reassessment 返回重估请求，不要自行猜测目标）")
    lines = []
    lines.append("【节点 %s】下面是本次交付的有界实施任务，"
                 "请按文末结果契约返回结构化实施结果。" % node_label)
    lines.append("")
    lines.append("objective（只执行此目标，不重新设计架构）：")
    lines.append(objective_text)
    lines.append("")
    _append_section(
        lines, "ownership（只能改动这些仓库相对路径）：",
        _str_items(node.get("ownership")),
        "（本节点未声明 ownership——不要改动任何文件）")
    _append_section(
        lines, "interfaces（须保持兼容的接口面）：",
        _str_items(node.get("interfaces")),
        "（本节点未声明接口面）")
    _append_section(
        lines, "constraints（额外约束）：",
        _str_items(node.get("constraints")),
        "（本节点未声明额外约束）")
    _append_section(
        lines,
        "local_check（可选本地检查：诚实逐项执行并如实记录；为空则不虚构检查）：",
        _str_items(node.get("local_check")),
        "（本节点未声明本地检查）")
    lines.append("任务背景：")
    lines.append("- task_ref：%s" % _context_value(task_context, "task_ref"))
    lines.append("- goal：%s" % _context_value(task_context, "goal"))
    lines.append("- repository：%s" % _context_value(task_context, "repository"))
    lines.append("")
    lines.append("结果契约（返回必须严格符合 NodeResult 结构）：")
    lines.append("- node_id：%s（Conductor 节点 id 原文）" % node_label)
    lines.append('- status："complete"（完成且证据齐全）| "partial"（部分完成）'
                 '| "blocked"（受阻）三选一')
    lines.append("- changes：实际改动文件清单（字符串数组，逐条仓库相对路径；"
                 "无改动则空数组）")
    lines.append("- local_checks：实际执行的本地检查及结果（字符串数组；"
                 "未执行则空数组，不得伪造）")
    lines.append("- reassessment：实质性歧义 / 范围冲突 / 规格缺漏时的重估请求"
                 "（无则空字符串；不自行决策、不重新设计架构）")
    lines.append("- gaps：未覆盖项或已知残留（无则空数组）")
    return "\n".join(lines)
