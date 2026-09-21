#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.4 静态节点数据层（Phase 1 工作包 P1-B）。

职责：
    Work Unit 在 v2.4 里退化为「静态节点」：只声明编译期事实
    （目标、依赖、所有权、接口面），不再携带任何运行时状态。
    状态 / 转换表 / 重试记账 / 结果历史 / 验证生命周期等运行时
    语义全部移除（v2.4 Phase 2 起执行面退役，节点在编译期
    被规划进 Workflow 拓扑，不存在运行中改状态的通道）。本模块
    是纯数据层（零 I/O、零网络、零第三方依赖）：
      - 构造：new_node() 构造静态节点 dict（可选字段缺省空列表）；
      - 校验：validate_node() 返回中文错误列表（空列表 = 合法），
        错误路径前缀形如 node.<字段名>，聚合全部错误不短路。

静态节点形状（v2.4 Phase 1 语义核心规格 §3）：
      - 必填：id / objective / depends_on / ownership；
      - 可选：interfaces / constraints / local_check（字符串数组，
        缺省空列表，local_check 允许为空——静态声明无「必填非空」
        语义，是否执行归编译后的 Workflow 侧）。

不提供（v2.3 遗留面，随 v2.3 执行面一并退役，W6 已删除）：
    status / WU_TRANSITIONS / transition / attempt / record_attempt /
    result / verification 及任何改写这些字段的兼容 setter——本模块
    的 dict 里根本没有这些键，也不接受向其回填。

依赖：
    零导入（连标准库都不需要）；与 v2.3 遗留实现（已随执行面删除）
    无任何导入关系；Phase 1 新代码（U4 workflow 编译器等）禁止导入
    任何执行面模块。

来源：
    docs/roadmap/V2_4_PHASE_1_NATIVE_SEMANTIC_CORE_SPEC.md §3
    + docs/roadmap/V2_4_PHASE_1_WORKFLOW_EXECUTION_PLAN.md U2。
"""

# —— 键清单常量（形状声明） ——

# 必填键（静态节点声明的事实四元组）
NODE_REQUIRED_KEYS = ("id", "objective", "depends_on", "ownership")
# 可选键（字符串数组，缺省空列表）
NODE_OPTIONAL_KEYS = ("interfaces", "constraints", "local_check")


# —— 内部消息助手（沿用 v2.3 遗留实现的错误消息风格） ——

def _str_list_errors(path, value):
    """校验「字符串数组」：值必须是 list 且每项为非空 str。"""
    if not isinstance(value, list):
        return ["%s 必须是数组" % path]
    errors = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or item == "":
            errors.append("%s[%d] 必须是非空字符串" % (path, index))
    return errors


def _repo_relative_errors(path, value):
    """校验「非空的仓库相对 scope 字符串数组」（ownership 专用）。

    在字符串数组规则之上追加两条：
      - 空数组报错（无文件范围的节点不可编译进 Workflow）；
      - 明显的仓库外路径（绝对路径：首字符 "/" 或 "\\"，或 Windows
        盘符前缀如 "C:/"）报错——ownership scope 必须是仓库相对。
    """
    if not isinstance(value, list):
        return ["%s 必须是数组" % path]
    if not value:
        return ["%s 不能为空数组（无文件范围的节点不可编译）" % path]
    errors = _str_list_errors(path, value)
    for index, item in enumerate(value):
        if isinstance(item, str) and item:
            probe = item.replace("\\", "/")
            if probe.startswith("/") or \
                    (len(probe) >= 2 and probe[1] == ":"
                     and probe[0].isalpha()):
                errors.append(
                    "%s[%d] 必须是仓库相对路径，得到绝对路径 %r"
                    % (path, index, item))
    return errors


def _copy_str_list(name, value):
    """构造期形状校验：list/tuple 且各项非空 str；返回复制的新 list。

    非法抛 TypeError / ValueError（中文消息，与 new_node 前缀一致）；
    入参一律复制为新的 list，不被调用方后续可变操作波及。
    """
    if not isinstance(value, (list, tuple)):
        raise TypeError(
            "new_node：%s 必须是 list 或 tuple，得到 %s"
            % (name, type(value).__name__))
    for item in value:
        if not isinstance(item, str) or item == "":
            raise ValueError(
                "new_node：%s 的各项必须是非空字符串" % name)
    return list(value)


# —— 构造 ——

def new_node(node_id, objective, *, depends_on=(), ownership=(),
             interfaces=None, constraints=None, local_check=None) -> dict:
    """构造静态节点 dict（只构造 + 形状校验，不查语义约束）。

    形状校验（非法抛 ValueError / TypeError，中文消息）：
      - node_id：必须是非空 str（非 str → TypeError，空串 →
        ValueError）；id 在单个 DAG 内的唯一性由图校验
        （runtime.dependency.graph_errors）负责，构造不查全图；
      - objective：允许暂缺或后补（构造从宽），非空约束由
        validate_node 落地；
      - depends_on / ownership / interfaces / constraints / local_check：
        必须是 list 或 tuple（否则 TypeError），各项必须是非空 str
        （否则 ValueError）；入参一律复制为新的 list。

    缺省值：
      - depends_on / ownership 缺省空元组（构造从宽——「非空」是
        编译期语义约束，由 validate_node 落地）；
      - interfaces / constraints / local_check 缺省为新的空列表
        （可选字段缺省空列表，保证返回 dict 形状完整）。

    返回 dict 只含本模块声明的键，无任何运行时字段。
    """
    if not isinstance(node_id, str):
        raise TypeError(
            "new_node：node_id 必须是字符串，得到 %s"
            % type(node_id).__name__)
    if node_id == "":
        raise ValueError("new_node：node_id 必须是非空字符串")
    return {
        "id": node_id,
        "objective": objective,
        "depends_on": _copy_str_list("depends_on", depends_on),
        "ownership": _copy_str_list("ownership", ownership),
        "interfaces": _copy_str_list("interfaces", interfaces or ()),
        "constraints": _copy_str_list("constraints", constraints or ()),
        "local_check": _copy_str_list("local_check", local_check or ()),
    }


# —— 校验 ——

def validate_node(node) -> "list[str]":
    """校验静态节点 dict，返回错误消息列表（中文，node.<字段> 前缀）。

    空列表 = 合法。不抛异常；node 非 dict → ["node 必须是 JSON 对象"]。
    规则（聚合全部错误，不短路）：
      - 必填键齐全（NODE_REQUIRED_KEYS），缺一报「node 缺少必填键 %s」；
      - id：非空字符串；
      - objective：非空字符串；
      - depends_on：字符串数组，且不含自身 id（自依赖归节点级校验；
        跨节点问题——缺失引用 / 重复 id / 环——归图校验 graph_errors）；
      - ownership：非空的仓库相对 scope 字符串列表（空数组或绝对
        路径报错）；
      - 可选数组（interfaces / constraints / local_check）：存在时
        必须是字符串数组（允许空列表，local_check 无「必填非空」
        语义）；
      - 未知键忽略（向前兼容），不报错。
    """
    if not isinstance(node, dict):
        return ["node 必须是 JSON 对象"]
    errors = []
    for key in NODE_REQUIRED_KEYS:
        if key not in node:
            errors.append("node 缺少必填键 %s" % key)
    if "id" in node:
        value = node["id"]
        if not isinstance(value, str) or value == "":
            errors.append("node.id 必须是非空字符串")
    if "objective" in node:
        value = node["objective"]
        if not isinstance(value, str) or value == "":
            errors.append("node.objective 必须是非空字符串")
    node_id = node.get("id")
    self_ref = isinstance(node_id, str) and node_id != ""
    if "depends_on" in node:
        errors.extend(_str_list_errors("node.depends_on",
                                       node["depends_on"]))
        if self_ref and isinstance(node["depends_on"], list) \
                and node_id in node["depends_on"]:
            errors.append("node.depends_on 自依赖：%r 出现在自身依赖中"
                          % node_id)
    if "ownership" in node:
        errors.extend(_repo_relative_errors("node.ownership",
                                            node["ownership"]))
    for key in NODE_OPTIONAL_KEYS:
        if key in node:
            errors.extend(_str_list_errors("node.%s" % key, node[key]))
    return errors
