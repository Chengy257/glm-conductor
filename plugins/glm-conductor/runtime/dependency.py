#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 Work Unit 依赖图层（v2 工作块 B8.2）。

职责：
    Work Unit 依赖构成小型有向无环图（升级指南 §63：环非法、
    全部依赖 completed 才可 ready）。本模块提供纯函数层的图校验
    与就绪推导（零 I/O、零网络、零第三方依赖）：
      - 校验：graph_errors() 报重复 id / depends_on 引用不存在的 id /
        自依赖 / 环（DFS 检测，报出全部环成员）/ 非法 depends_on 项；
      - 就绪推导：deps_satisfied() / ready_units() 实现 §63 规则 3
        与 §64 就绪集推导的确定性基础（dispatcher B8.3 再叠加
        executor 可用性 / lease / 并发槽 / 配额等策略过滤）；
      - 影响分析：downstream() 计算直接 + 间接依赖某单元的传递闭包
        （恢复对账 B8.4 / 完成传播用）；
      - 排序：topo_order() 输出全图确定性拓扑序（Kahn 队列按 id
        最小堆入列，同层按 id 排序）。

容错读键：
    units 为 work unit dict 列表（§61 形状，或任意含 id / depends_on /
    status 的 dict）；本模块只依赖这三个键，均用 dict.get 容错读取，
    §61 形状之外的键一律忽略。形状级错误（缺 id、depends_on 项非
    字符串等）由 graph_errors 报告；推导函数对垃圾输入保持确定性
    （如缺失引用按不满足处理、非 dict 项跳过）。

依赖：
    仅 Python 3 标准库（heapq），零第三方依赖，`python3 -S` 可运行。
    不导入 runtime 其他模块（依赖图只关心 §62 词汇中的 "completed"
    一个状态字面量；与 work_unit.py / state.py 均无循环导入）。

来源：
    v2.0 设计（已蒸馏入 docs/architecture.md：dependency graph / ready
    queue / resume semantics）+ 工作块 B8.2。
"""

import heapq

# 依赖满足的唯一目标状态（§63 规则 3：全部依赖 completed 才可 ready）
_COMPLETED = "completed"


# —— 内部读键 / 邻接构建（容错，保持确定性） ——

def _dep_list(unit) -> "list[str]":
    """容错读取 unit 的 depends_on：保序去重，剔除非 str / 空串项。

    形状级问题（depends_on 非 list、项非字符串）由 graph_errors
    负责；推导函数只消费本函数的净化结果，保证确定性行为。
    """
    if not isinstance(unit, dict):
        return []
    deps = unit.get("depends_on")
    if not isinstance(deps, list):
        return []
    seen = set()
    ordered = []
    for dep in deps:
        if isinstance(dep, str) and dep != "" and dep not in seen:
            seen.add(dep)
            ordered.append(dep)
    return ordered


def _units_by_id(units) -> dict:
    """构建 id → unit 映射（重复 id 首次出现者优先，非 dict 项跳过）。"""
    by_id = {}
    if not isinstance(units, list):
        return by_id
    for unit in units:
        if not isinstance(unit, dict):
            continue
        uid = unit.get("id")
        if isinstance(uid, str) and uid != "" and uid not in by_id:
            by_id[uid] = unit
    return by_id


def _cycle_members(units) -> "list[str]":
    """DFS 检测全部参与环的 unit id，返回按 id 排序的列表（无环 → 空）。

    三色标记（WHITE 未访 / GRAY 在栈 / BLACK 已完成）；遇 GRAY 回边时，
    当前 DFS 路径上从该节点到栈顶的片段即一个环的全部成员。
    只报真正参与环的节点——依赖环但其自身不在环上的下游单元不报
    （环的裁决归 graph_errors，下游派发抑制由 deps_satisfied 自然达成）。
    迭代实现（显式栈），深链不受递归深度限制。
    """
    adjacency = {}
    for unit in (units if isinstance(units, list) else []):
        if not isinstance(unit, dict):
            continue
        uid = unit.get("id")
        if isinstance(uid, str) and uid != "" and uid not in adjacency:
            adjacency[uid] = _dep_list(unit)
    white, gray, black = 0, 1, 2
    color = dict.fromkeys(adjacency, white)
    members = set()
    for start in sorted(adjacency):
        if color[start] != white:
            continue
        color[start] = gray
        path = [start]
        stack = [iter(adjacency[start])]
        while stack:
            advanced = False
            for nxt in stack[-1]:
                if nxt not in color:
                    continue  # 引用不存在的 id：不在图中，跳过
                shade = color[nxt]
                if shade == gray:
                    members.update(path[path.index(nxt):])
                elif shade == white:
                    color[nxt] = gray
                    path.append(nxt)
                    stack.append(iter(adjacency[nxt]))
                    advanced = True
                    break
                # black：已完成节点，继续下一个邻居
            if not advanced:
                color[path.pop()] = black
                stack.pop()
    return sorted(members)


# —— 图校验 ——

def graph_errors(units) -> "list[str]":
    """校验依赖图，返回错误消息列表（中文，含单元 id / 下标路径）。

    空列表 = 合法图。检查项（聚合全部，不短路）：
      - units 非数组 / 项非 JSON 对象 / id 缺失或非空字符串；
      - 重复 id（报告两次出现的下标）；
      - depends_on 非 list / 项非空字符串；
      - 自依赖（id 出现在自身 depends_on 中）；
      - depends_on 引用不存在的 id（§63 规则 1：依赖必须引用同一
        父任务内的 Work Unit）；
      - 环（§63 规则 2；消息按 id 排序列出全部环成员）。
    """
    if not isinstance(units, list):
        return ["units 必须是数组"]
    errors = []
    first_index = {}  # id -> 首次出现的下标
    for index, unit in enumerate(units):
        if not isinstance(unit, dict):
            errors.append("units[%d] 必须是 JSON 对象" % index)
            continue
        uid = unit.get("id")
        if not isinstance(uid, str) or uid == "":
            errors.append("units[%d].id 必须是非空字符串" % index)
            continue
        if uid in first_index:
            errors.append("重复 id：%r（units[%d] 与 units[%d]）"
                          % (uid, first_index[uid], index))
            continue
        first_index[uid] = index
        deps = unit.get("depends_on")
        if not isinstance(deps, list):
            errors.append("units[%d].depends_on 必须是数组" % index)
            continue
        for j, dep in enumerate(deps):
            if not isinstance(dep, str) or dep == "":
                errors.append("units[%d].depends_on[%d] 必须是非空字符串"
                              % (index, j))
        if uid in deps:
            errors.append("units[%d]（id=%r）自依赖" % (index, uid))
    # 未知引用（§63 规则 1）：只查每个 id 的首次出现处
    for uid, index in first_index.items():
        for dep in _dep_list(units[index]):
            if dep not in first_index:
                errors.append("units[%d]（id=%r）depends_on 引用不存在的 id：%r"
                              % (index, uid, dep))
    cycle = _cycle_members(units)
    if cycle:
        errors.append("依赖图存在环（环成员按 id 排序：%s）" % ", ".join(cycle))
    return errors


# —— 就绪推导（§63 规则 3 / §64） ——

def deps_satisfied(unit, units_by_id) -> bool:
    """unit 的全部依赖是否满足：每个依赖都存在且 status == completed。

    任一依赖引用缺失 → 不满足（§63 规则 1）；failed / blocked /
    pending 等任何非 completed 状态 → 不满足（§63 规则 4：失败或
    阻断的依赖抑制下游派发，直到父会话裁决）。无依赖 → True。
    """
    if not isinstance(unit, dict):
        return False
    for dep in _dep_list(unit):
        target = units_by_id.get(dep) if isinstance(units_by_id, dict) else None
        if not isinstance(target, dict) or target.get("status") != _COMPLETED:
            return False
    return True


def ready_units(units) -> "list[str]":
    """就绪集推导（§64）：status == ready 且全部依赖满足的 id 列表。

    输出保持输入顺序（确定性基础）；waiting_dependency 即便依赖已
    全部满足也不入列（状态未提升）、status 为 ready 但依赖未满足的
    同样不入列。executor 可用性 / lease / 并发槽 / 配额 / 任务级阻断
    等策略过滤归 dispatcher（B8.3），不在本函数。
    """
    by_id = _units_by_id(units)
    ready = []
    for unit in (units if isinstance(units, list) else []):
        if not isinstance(unit, dict):
            continue
        if unit.get("status") == "ready" and deps_satisfied(unit, by_id):
            ready.append(unit.get("id"))
    return ready


# —— 影响分析 / 排序 ——

def downstream(unit_id, units) -> "list[str]":
    """直接 + 间接依赖 unit_id 的单元 id 列表（传递闭包，拓扑序输出）。

    - 不含 unit_id 自身；unit_id 不存在（或图中无此 id）→ 空列表；
    - 输出为全图确定性拓扑序（topo_order）过滤后的子序列——
      同层按 id 排序；图含环时 topo_order 抛 ValueError（与
      topo_order 同一语义）。
    """
    by_id = _units_by_id(units)
    if unit_id not in by_id:
        return []
    dependents = {}  # dep_id -> 直接依赖它的 id 列表（输入顺序）
    for unit in (units if isinstance(units, list) else []):
        if not isinstance(unit, dict):
            continue
        uid = unit.get("id")
        if not isinstance(uid, str) or uid == "":
            continue
        for dep in _dep_list(unit):
            dependents.setdefault(dep, []).append(uid)
    # BFS 传递闭包（visited 含起点，天然挡掉环回边，保证不含自身）
    visited = {unit_id}
    queue = [unit_id]
    index = 0
    while index < len(queue):
        current = queue[index]
        index += 1
        for dependent in dependents.get(current, ()):
            if dependent not in visited:
                visited.add(dependent)
                queue.append(dependent)
    visited.discard(unit_id)
    return [uid for uid in topo_order(units) if uid in visited]


def topo_order(units) -> "list[str]":
    """全图确定性拓扑序（Kahn 算法，就绪队列按 id 最小堆出列）。

    - 依赖必须先于被依赖者出现；同层（可并行层）按 id 升序；
    - 同一图不同输入顺序 → 相同输出（确定性契约，供恢复对账
      与调度快照对比）；
    - depends_on 引用不存在的 id：按不存在处理（不计入入度，
      图校验归 graph_errors）；
    - 图有环 → ValueError（消息含按 id 排序的全部环成员；
      与 graph_errors 复用同一环检测实现 _cycle_members）。
    """
    by_id = _units_by_id(units)
    cycle = _cycle_members(units)
    if cycle:
        raise ValueError(
            "依赖图存在环，无法拓扑排序（环成员按 id 排序：%s）"
            % ", ".join(cycle))
    indegree = dict.fromkeys(by_id, 0)
    dependents = {}
    for uid, unit in by_id.items():
        for dep in _dep_list(unit):
            if dep in by_id:
                indegree[uid] += 1
                dependents.setdefault(dep, []).append(uid)
    available = [uid for uid, degree in indegree.items() if degree == 0]
    heapq.heapify(available)
    order = []
    while available:
        uid = heapq.heappop(available)
        order.append(uid)
        for dependent in dependents.get(uid, ()):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heapq.heappush(available, dependent)
    return order
