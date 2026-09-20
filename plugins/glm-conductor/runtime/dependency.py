#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.4 纯 DAG 依赖图层（Phase 1 工作包 P1-B）。

职责：
    静态节点（runtime.work_unit）的依赖构成有向无环图，本模块提供
    纯函数层的图校验与确定性排序（零 I/O、零网络、零第三方依赖），
    是编译期设施——供 U4 workflow 编译器把 DAG 编译为 Workflow
    拓扑，不再服务运行时就绪推导：
      - 校验：graph_errors() 报重复 id / 缺失依赖（depends_on 引用
        不存在的 id）/ 自依赖 / 环（DFS 检测，报出全部环成员），
        聚合全部错误不短路；
      - 排序：topo_order() 输出全图确定性拓扑序（Kahn 就绪集按 id
        最小堆出列，同层按 id 升序；同一图不同输入顺序 → 相同输出）；
      - 分层：stage_levels() 返回确定性层级列表（第 i 层 = 全部依赖
        都落在更早层级的节点集合，层内按 id 排序，供代码生成按层
        展开）；
      - 影响分析：downstream() 计算直接 + 间接依赖某节点的传递闭包
        （编译器可用；不用也不影响其余接口）。

不提供（v2.3 遗留面，随 v2.3 执行面一并退役，W6 已删除）：
    ready_units / deps_satisfied 等基于 status 的运行时就绪函数——
    静态节点没有 status，「依赖 completed 才可 ready」的运行时推导
    随执行面语义一起退役，本模块不做任何状态读取。

容错读键：
    nodes 为静态节点 dict 列表（或任意含 id / depends_on 的 dict）；
    本模块只依赖这两个键，均用 dict.get 容错读取，其余键一律忽略。
    形状级错误（缺 id、depends_on 项非字符串等）由 graph_errors
    报告；排序 / 分层函数对垃圾输入保持确定性（缺失引用不计入
    依赖、非 dict 项跳过）。

依赖：
    仅 Python 3 标准库（heapq），零第三方依赖，`python3 -S` 可运行。
    不导入 runtime 其他模块（与 runtime.work_unit.py 无导入关系，
    无循环导入）。

来源：
    docs/roadmap/V2_4_PHASE_1_NATIVE_SEMANTIC_CORE_SPEC.md §3
    + docs/roadmap/V2_4_PHASE_1_WORKFLOW_EXECUTION_PLAN.md U2
    （算法沿用 v2.3 遗留实现的 Kahn / DFS 实现并裁剪状态语义）。
"""

import heapq


# —— 内部读键 / 邻接构建（容错，保持确定性） ——

def _dep_list(node) -> "list[str]":
    """容错读取 node 的 depends_on：保序去重，剔除非 str / 空串项。

    形状级问题（depends_on 非 list、项非字符串）由 graph_errors
    负责；排序 / 分层函数只消费本函数的净化结果，保证确定性。
    """
    if not isinstance(node, dict):
        return []
    deps = node.get("depends_on")
    if not isinstance(deps, list):
        return []
    seen = set()
    ordered = []
    for dep in deps:
        if isinstance(dep, str) and dep != "" and dep not in seen:
            seen.add(dep)
            ordered.append(dep)
    return ordered


def _nodes_by_id(nodes) -> dict:
    """构建 id → node 映射（重复 id 首次出现者优先，非 dict 项跳过）。"""
    by_id = {}
    if not isinstance(nodes, list):
        return by_id
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        if isinstance(node_id, str) and node_id != "" \
                and node_id not in by_id:
            by_id[node_id] = node
    return by_id


def _cycle_members(nodes) -> "list[str]":
    """DFS 检测全部参与环的节点 id，返回按 id 排序的列表（无环 → 空）。

    三色标记（WHITE 未访 / GRAY 在栈 / BLACK 已完成）；遇 GRAY 回边时，
    当前 DFS 路径上从该节点到栈顶的片段即一个环的全部成员。
    只报真正参与环的节点——依赖环上节点但其自身不在环上的下游节点
    不报（环的裁决归 graph_errors）。迭代实现（显式栈），深链不受
    递归深度限制。
    """
    adjacency = {}
    for node in (nodes if isinstance(nodes, list) else []):
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        if isinstance(node_id, str) and node_id != "" \
                and node_id not in adjacency:
            adjacency[node_id] = _dep_list(node)
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

def graph_errors(nodes) -> "list[str]":
    """校验依赖图，返回错误消息列表（中文，含节点 id / 下标路径）。

    空列表 = 合法图。检查项（聚合全部，不短路）：
      - nodes 非数组 / 项非 JSON 对象 / id 缺失或非空字符串；
      - 重复 id（报告两次出现的下标）——同一 DAG 内 id 必须唯一；
      - depends_on 非 list / 项非空字符串；
      - 自依赖（id 出现在自身 depends_on 中）；
      - 缺失依赖：depends_on 引用不存在的 id（依赖必须引用同一
        DAG 内的节点）；
      - 环（消息按 id 排序列出全部环成员）。
    """
    if not isinstance(nodes, list):
        return ["nodes 必须是数组"]
    errors = []
    first_index = {}  # id -> 首次出现的下标
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append("nodes[%d] 必须是 JSON 对象" % index)
            continue
        node_id = node.get("id")
        if not isinstance(node_id, str) or node_id == "":
            errors.append("nodes[%d].id 必须是非空字符串" % index)
            continue
        if node_id in first_index:
            errors.append("重复 id：%r（nodes[%d] 与 nodes[%d]）"
                          % (node_id, first_index[node_id], index))
            continue
        first_index[node_id] = index
        deps = node.get("depends_on")
        if not isinstance(deps, list):
            errors.append("nodes[%d].depends_on 必须是数组" % index)
            continue
        for j, dep in enumerate(deps):
            if not isinstance(dep, str) or dep == "":
                errors.append("nodes[%d].depends_on[%d] 必须是非空字符串"
                              % (index, j))
        if node_id in deps:
            errors.append("nodes[%d]（id=%r）自依赖" % (index, node_id))
    # 缺失依赖：只查每个 id 的首次出现处
    for node_id, index in first_index.items():
        for dep in _dep_list(nodes[index]):
            if dep not in first_index:
                errors.append(
                    "nodes[%d]（id=%r）depends_on 引用不存在的 id：%r"
                    % (index, node_id, dep))
    cycle = _cycle_members(nodes)
    if cycle:
        errors.append("依赖图存在环（环成员按 id 排序：%s）"
                      % ", ".join(cycle))
    return errors


# —— 确定性排序 / 分层 ——

def topo_order(nodes) -> "list[str]":
    """全图确定性拓扑序（Kahn 算法，就绪集内按 id 最小堆出列）。

    - 依赖必须先于被依赖者出现；同层（可并行层）按 id 升序；
    - 同一图不同输入顺序 → 相同输出（确定性契约，供编译快照对比）；
    - depends_on 引用不存在的 id：按不存在处理（不计入入度，
      图校验归 graph_errors）；
    - 图有环 → ValueError（消息含按 id 排序的全部环成员；
      与 graph_errors 复用同一环检测实现 _cycle_members）。
    """
    by_id = _nodes_by_id(nodes)
    cycle = _cycle_members(nodes)
    if cycle:
        raise ValueError(
            "依赖图存在环，无法拓扑排序（环成员按 id 排序：%s）"
            % ", ".join(cycle))
    indegree = dict.fromkeys(by_id, 0)
    dependents = {}
    for node_id, node in by_id.items():
        for dep in _dep_list(node):
            if dep in by_id:
                indegree[node_id] += 1
                dependents.setdefault(dep, []).append(node_id)
    ready = [node_id for node_id, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    order = []
    while ready:
        node_id = heapq.heappop(ready)
        order.append(node_id)
        for dependent in dependents.get(node_id, ()):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heapq.heappush(ready, dependent)
    return order


def stage_levels(nodes) -> "list[list[str]]":
    """确定性层级列表：第 i 层节点的依赖全部落在更早层级。

    - 第 0 层 = 无依赖的节点；第 i 层 = 依赖全部已入列（更早层级）
      的节点集合，层内按 id 升序；层数 = 图的最长链长度，可并行
      的节点必然同层——供代码生成按层展开（同层节点可并行编译）；
    - 同一图不同输入顺序 → 相同输出（确定性契约）；
    - depends_on 引用不存在的 id：按不存在处理（不阻塞入层，
      图校验归 graph_errors）；
    - 图有环 → ValueError（与 topo_order 同一语义，环上节点永远
      无法满足「依赖全部入层」）。
    """
    order = topo_order(nodes)
    by_id = _nodes_by_id(nodes)
    level_of = {}  # id -> 所处层级
    levels = []
    for node_id in order:
        depth = 0
        for dep in _dep_list(by_id[node_id]):
            if dep in level_of:
                depth = max(depth, level_of[dep] + 1)
        level_of[node_id] = depth
        if depth == len(levels):
            levels.append([])
        levels[depth].append(node_id)
    return levels


# —— 影响分析 ——

def downstream(node_id, nodes) -> "list[str]":
    """直接 + 间接依赖 node_id 的节点 id 列表（传递闭包，拓扑序输出）。

    - 不含 node_id 自身；node_id 不存在（或图中无此 id）→ 空列表；
    - 输出为全图确定性拓扑序（topo_order）过滤后的子序列——
      同层按 id 排序；图含环时 topo_order 抛 ValueError（与
      topo_order 同一语义）。
    """
    by_id = _nodes_by_id(nodes)
    if node_id not in by_id:
        return []
    dependents = {}  # dep_id -> 直接依赖它的 id 列表（输入顺序）
    for node in (nodes if isinstance(nodes, list) else []):
        if not isinstance(node, dict):
            continue
        current_id = node.get("id")
        if not isinstance(current_id, str) or current_id == "":
            continue
        for dep in _dep_list(node):
            dependents.setdefault(dep, []).append(current_id)
    # BFS 传递闭包（visited 含起点，天然挡掉环回边，保证不含自身）
    visited = {node_id}
    queue = [node_id]
    index = 0
    while index < len(queue):
        current = queue[index]
        index += 1
        for dependent in dependents.get(current, ()):
            if dependent not in visited:
                visited.add(dependent)
                queue.append(dependent)
    visited.discard(node_id)
    return [uid for uid in topo_order(nodes) if uid in visited]
