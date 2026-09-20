#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 ownership 路径所有权匹配层（v2 工作块 B2.1）。

职责：
    Ownership Gate Layer A（Stop 完成门）的两个确定性输入来源：
      - 匹配语义：把 state.json ownership.files 声明的路径模式编译为
        正则并判定某路径是否被覆盖；classify_paths() 把 diff 实际改动
        文件分为 owned_hits / out_of_scope 两桶，供「touched ⊆ owned?」
        判定使用（未命中即 block 完成门）；
      - touched 清单：git_touched_files() 列出当前 git 工作区全部改动
        文件（仓库相对路径、正斜杠），作为「实际改动文件」的来源。

三种声明形式（v2.0 设计，已蒸馏入 docs/architecture.md）：
      - exact file:       "src/auth.ts"    只匹配该文件自身；
      - directory prefix: "src/auth/**"    匹配目录自身与全部后代
                          （零段语义使 "src/auth" 自身也命中，目录前缀
                          能力由 "**" 形式承载）；
      - glob:             "*.py" / "a/**/b" / "f?.py"   段级 glob，见下表。

段级 glob 语义表（比 fnmatch 更严格，防止隐式跨段扩张）：
      - "**" 匹配零个或多个路径段（可跨 "/"）："src/auth/**" 也匹配
        "src/auth" 自身；"a/**/b" 匹配 "a/b"、"a/x/b"、"a/x/y/b"；
      - "*"  匹配单个段内任意字符（不跨 "/"）："*.py" 不匹配 "sub/x.py"；
      - "?"  匹配单个字符（不跨 "/"）："f?.py" 匹配 "f1.py"；
      - 其余字符字面量（re.escape）："a[1].ts" 只匹配 "a[1].ts" 自身。

拒绝隐式扩张：
    任何超出声明模式的路径都不算 owned——匹配只做「路径是否命中某条
    声明」，绝不做目录展开或猜测式放宽；未命中即 out_of_scope，
    Layer A 据此拦截完成门（touched ⊆ owned? no → BLOCK）。

v2.4 Phase 1 追加（P1-C 编译期阶段规划）：
    plan_stages() 把已校验静态节点（runtime.work_unit / 
    runtime.dependency）规划为 ownership 两两不相交的并行阶段；
    scopes_overlap() 判定两个 scope 的路径语言是否相交（完备判定，
    两端都可用 glob）；OwnershipConflictError 报告歧义 / 非法模式。
    既有路径语义函数原样保留（Stop 完成门与 lease.py 依赖不变）；
    plan_stages 对 runtime.work_unit / runtime.dependency 采用函数内
    懒导入（先例：runtime/fingerprint.py），保持既有导入区不动。

依赖：
    仅 Python 3 标准库（re / subprocess），零第三方依赖，
    `python3 -S` 可运行。风格对齐 runtime/state.py / runtime/journal.py。

来源：
    v2.0 设计（已蒸馏入 docs/architecture.md：ownership path rules）
    + v2 升级计划工作块 B2.1。
"""

import re
import subprocess


class OwnershipError(Exception):
    """ownership 匹配层的结构性错误（非法模式、git 调用失败等）。"""


# "**" 段的主体占位：零或多段（跨 "/" 的任意段序列，含空串）
_GLOBSTAR = "[^/]*(?:/[^/]*)*"


# —— 路径归一 ——

def normalize_path(path) -> str:
    """路径归一：反斜杠统一为 "/"；去掉开头 "./"；不去掉 "../"（保留相对语义）。

    - 空串原样返回；
    - Windows 盘符路径（如 C:/x）不做特殊处理——本模块只处理仓库相对路径；
    - 非字符串输入抛 OwnershipError（结构性错误，不静默转换）。
    """
    if not isinstance(path, str):
        raise OwnershipError(
            "路径必须是字符串，得到 %s" % type(path).__name__)
    normalized = path.replace("\\", "/")
    # 只剥离开头 "./"（可能重复书写）；"../" 原样保留相对语义
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


# —— 模式编译 ——

def _translate_segment(seg: str) -> str:
    """翻译单个非 "**" 段："*" → "[^/]*"，"?" → "[^/]"，其余字符 re.escape。

    通配符不跨 "/"（[^/...] 保证段级语义），其余字符一律字面量。
    """
    parts = []
    for ch in seg:
        if ch == "*":
            parts.append("[^/]*")
        elif ch == "?":
            parts.append("[^/]")
        else:
            parts.append(re.escape(ch))
    return "".join(parts)


def compile_pattern(pattern) -> "re.Pattern":
    """把 ownership 声明模式编译为正则（match 语义，全路径锚定 ^...$）。

    翻译规则：
      1. pattern 先 normalize_path；
      2. 按 "/" 切段，逐段翻译（见 _translate_segment / _GLOBSTAR）；
      3. 段间用 "/" 连接后整体锚定 ^(...)$；
      4. 段级 "**" 与相邻 "/" 的连接语义（零段可命中）：
           - 末段 "**"（"src/auth/**"）→ 前缀 + 可选组 (?:/_GLOBSTAR)?，
             匹配 "src/auth"（零段）、"src/auth/a.ts"、"src/auth/x/y.ts"；
           - 中段 "**"（"a/**/b"）→ a + (?:/_GLOBSTAR)? + /b，
             匹配 "a/b"（零段）、"a/x/b"、"a/x/y/b"——左分隔符被组吸收，
             右分隔符照常输出；
           - 首段 "**"（"**/b"）→ (?:_GLOBSTAR/)? + b，右分隔符被组吸收；
           - 单独 "**" → _GLOBSTAR 本身，匹配任意路径（含空串）。
         只保证 match 语义正确，不承诺正则字符串形态。
      5. 纯字面量模式（不含 * 与 ? 的裸路径，如 "src/auth.ts" / "src/auth"）
         兼具两种语义：exact 文件匹配 + 目录前缀匹配——编译为
         ^literal(/.*)?$，即 "src/auth" 等价 "src/auth/**"（§11 的
         directory prefix 形式；仍按路径段边界匹配，"src/auth" 不覆盖
         "src/authentication.ts"）。含通配符的模式不享受该前缀语义。
      6. 非法模式抛 OwnershipError（消息含模式原文）：
           - 空路径段：连续 "//"、首 "/"、尾 "/"（含空串模式）；
           - 相邻双星（"**/**"）视为冗余合法——折叠为单个 "**"，允许。

    非字符串输入同样抛 OwnershipError（结构性错误）。
    """
    if not isinstance(pattern, str):
        raise OwnershipError(
            "ownership 模式必须是字符串，得到 %s" % type(pattern).__name__)
    normalized = normalize_path(pattern)
    segments = normalized.split("/")
    for seg in segments:
        if seg == "":
            raise OwnershipError(
                "非法 ownership 模式（含空路径段，如连续 // 或首尾 /）：%r"
                % pattern)
    # 折叠相邻双星："**/**" 冗余合法，语义等价单个 "**"
    folded = []
    for seg in segments:
        if seg == "**" and folded and folded[-1] == "**":
            continue
        folded.append(seg)

    total = len(folded)
    # 纯字面量模式（无 * 与 ?）：exact + 目录前缀双语义（规则 5）。
    # 含通配符的模式不走该分支，严格按声明形状匹配。
    if not any(ch in normalized for ch in "*?"):
        return re.compile("^" + re.escape(normalized) + "(?:/.*)?$")
    parts = []
    for i, seg in enumerate(folded):
        if seg == "**":
            if total == 1:
                # 单独 "**"（含 "**/**" 折叠后）：匹配任意路径
                parts.append(_GLOBSTAR)
            elif i == 0:
                # 首段 "**"：吸收右侧分隔符——(?:任意多段/)?
                parts.append("(?:" + _GLOBSTAR + "/)?")
            else:
                # 中段 / 末段 "**"：吸收左侧分隔符——(?:/任意多段)?
                parts.append("(?:/" + _GLOBSTAR + ")?")
        else:
            # 段间分隔符：当前段是 i>0 的 "**"（上方分支）已随其可选组
            # 吸收左侧分隔符；左邻是首段 "**" 时右侧分隔符已被组尾 "/" 吸收
            if i > 0 and not (i == 1 and folded[0] == "**"):
                parts.append("/")
            parts.append(_translate_segment(seg))
    return re.compile("^" + "".join(parts) + "$")


# —— 匹配 / 分桶 ——

def match_path(path, patterns) -> bool:
    """path 是否被 patterns 中任一模式覆盖。

    - path 先 normalize_path（patterns 由 compile_pattern 自行归一）；
    - patterns 为空 → False；
    - 单个模式非法（OwnershipError）自然向上抛，不静默吞掉。
    """
    normalized = normalize_path(path)
    if not patterns:
        return False
    for pattern in patterns:
        if compile_pattern(pattern).match(normalized):
            return True
    return False


def classify_paths(paths, owned_patterns) -> tuple:
    """把 paths 分为 (owned_hits, out_of_scope) 两个列表。

    - 两个列表均保持输入顺序，元素均为 normalize_path 后的路径；
    - owned = 被 owned_patterns 任一模式命中；out_of_scope = 未命中；
    - 先完成全部模式编译，再分桶——任一声明模式非法时 OwnershipError
      在任何分桶结果产生之前抛出（结构性错误优先）。
    """
    compiled = [compile_pattern(pattern) for pattern in owned_patterns]
    owned_hits = []
    out_of_scope = []
    for path in paths:
        normalized = normalize_path(path)
        if any(regex.match(normalized) for regex in compiled):
            owned_hits.append(normalized)
        else:
            out_of_scope.append(normalized)
    return (owned_hits, out_of_scope)


# —— git touched 清单 ——

def git_touched_files(repo_root) -> "list[str]":
    """列出当前 git 工作区全部改动文件（仓库相对路径、正斜杠）。

    命令（逐字）：git -c core.quotepath=false status --porcelain=v1 -z -uall
      - cwd=repo_root；-z 以 NUL 分隔，规避文件名空格/引号问题；
      - -uall 把未跟踪目录展开到文件级；
      - core.quotepath=false 保证非 ASCII 文件名原样输出。

    解析（porcelain v1 -z 格式）：
      - 按 "\\0" 切分；每条目前两字符为 XY 状态码、第三字符为空格、
        其余为路径；条目为空串跳过；异常格式条目容错跳过；
      - X 或 Y 为 R 或 C（rename/copy）时，紧跟的下一段是原路径
        （-z 格式下 origPath 作为独立 NUL 段跟在该条目之后）——
        原路径与新路径都计入 touched；
      - 返回顺序保持 git 输出顺序。

    运行时状态目录豁免：
      - `.glm-conductor/` 前缀的路径一律剔除（state.json / events.jsonl /
        checkpoint 等编排器自身账本）。Layer A 校验的是「用户仓库的实际
        改动 ⊆ 声明 ownership」，编排器运行时状态不属于用户仓库改动——
        否则创建 state.json 本身就会被判越界（自指拦截）。该目录按
        continuity 契约本就应写入 .git/info/exclude 本地排除，此处豁免
        保证未排除时完成门同样正确。

    失败：git 可执行不存在 / 返回非零（非 git 仓库等）/ 超时 → 抛
    OwnershipError（消息含 returncode 与 stderr 摘要）。
    subprocess 固定 timeout=3 秒——必须小于 Stop 钩子的 timeoutMs=5000：
    git 挂起时让 OwnershipError 先触发（钩子内 fail-open + ENFORCEMENT
    DEGRADED 报告），而不是被运行时整进程击杀（那样降级不可见）。
    """
    command = [
        "git", "-c", "core.quotepath=false",
        "status", "--porcelain=v1", "-z", "-uall",
    ]
    try:
        proc = subprocess.run(
            command, cwd=str(repo_root),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
    except FileNotFoundError as exc:
        raise OwnershipError(
            "git 调用失败：找不到 git 可执行文件（%s）" % exc) from exc
    except subprocess.TimeoutExpired as exc:
        raise OwnershipError(
            "git 调用失败：status 超时（>3 秒，repo=%s）" % repo_root) from exc
    if proc.returncode != 0:
        stderr_summary = proc.stderr.decode("utf-8", errors="replace").strip()
        raise OwnershipError(
            "git 调用失败：status 返回 returncode=%d（repo=%s）；stderr：%s"
            % (proc.returncode, repo_root, stderr_summary[:200]))
    return _parse_porcelain_z(
        proc.stdout.decode("utf-8", errors="replace"))


def _parse_porcelain_z(text: str) -> "list[str]":
    """解析 git status --porcelain=v1 -z 输出，返回 touched 路径列表。

    rename/copy 条目（X 或 Y 为 R/C）后面紧跟一个独立的 NUL 段存放
    原路径，原路径与新路径都计入。`.glm-conductor/` 前缀路径剔除
    （运行时状态目录豁免，见 git_touched_files docstring）。
    """
    touched = []
    entries = text.split("\0")
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if entry == "":
            continue
        # 正常条目：XY + 空格 + 路径（至少 4 字符）；异常格式容错跳过
        if len(entry) < 4 or entry[2] != " ":
            continue
        paths = [entry[3:]]
        status_x, status_y = entry[0], entry[1]
        if status_x in ("R", "C") or status_y in ("R", "C"):
            if index < len(entries):
                origin = entries[index]
                index += 1
                if origin != "":
                    paths.append(origin)
        for path in paths:
            if not (path == ".glm-conductor" or path.startswith(".glm-conductor/")):
                touched.append(path)
    return touched


# —— v2.4 编译期阶段规划（Phase 1 工作包 P1-C；本节纯追加，上方语义不动） ——
#
# 背景（W0 F9）：宿主对同一文件的并发写是 silent last-writer-wins，
# 启动前这道编译期检查是唯一冲突屏障。本节只做确定性规划：
#   - 同一阶段（组）内成员的 ownership scope 两两不相交，可安全并行；
#   - scope 重叠的候选被确定性串行（排进同层级的后续组），绝不猜测；
#   - 歧义或非法 pattern 直接抛 OwnershipConflictError，编译期失败。
# 不引入任何锁、租约、运行时预约（仓库级写守卫是 U5 的独立设施），
# 不新建 per-node 锁——产物是纯静态的阶段列表，供 U4 Workflow 编译器
# 按组展开为 phase / Promise.all。

class OwnershipConflictError(Exception):
    """ownership 编译期冲突 / 歧义错误（规划拒绝，绝不猜测）。

    两个节点的 scope 重叠本身不是错误——重叠候选被确定性串行化；
    本错误只用于两类编译期失败，消息点名涉事节点 id 与出问题的
    scope 原文：
      - pattern 编译失败（非法模式，附带底层 OwnershipError 原文）；
      - pattern 语义上匹配仓库根或一切路径（如 "**" / "*" / 空串），
        所有权声明覆盖一切、意图歧义，以编译期失败取代猜测。
    """


# 段级 NFA 的边统一为三元组 (kind, payload, target)：
#   ("eps", None, target)     ε-转移，不消耗输入；
#   ("seg", matcher, target)  消耗一个完整路径段，段内字符序列须被
#                             matcher（字符级迷你 NFA）接受。
# 字符级迷你 NFA：{"start", "accept", "edges"}；edges: {state: [(guard, target)]}。
# guard 恰好消耗一个段内字符（"*" 的「零或多字符」用自环表达）：
#   ("lit", ch)   字面量字符（与 _translate_segment 的 re.escape 对应）；
#   ("any1",)     任意单字符（"?"，段内不含 "/"）；
#   ("anystar",)  任意段内字符串（"*"，含空串；语义为当前状态自环）。


def _segment_matcher(seg: str) -> dict:
    """把单个模式段翻译为字符级迷你 NFA（语义同 _translate_segment）。"""
    edges = {0: []}
    state = 0
    for ch in seg:
        if ch == "*":
            # 段内任意字符串（含空串）：当前状态自环，不前进
            edges[state].append((("anystar",), state))
        else:
            nxt = state + 1
            edges[nxt] = []
            if ch == "?":
                edges[state].append((("any1",), nxt))
            else:
                edges[state].append((("lit", ch), nxt))
            state = nxt
    return {"start": 0, "accept": state, "edges": edges}


# 「任意单段」匹配器（供 "**" 段自环复用；合法路径段非空，与
# _GLOBSTAR 在合法路径域上等价）
_SEG_ANY = _segment_matcher("*")


def _char_guard_conj(guard_a, guard_b):
    """两个字符级守卫的合取：同时满足的单字符集合；空集返回 None。

    合取在此守卫代数上封闭：anystar 吸收一切；any1 ∧ lit = lit；
    any1 ∧ any1 = any1；lit ∧ lit 仅在字符相等时保留。
    """
    kind_a, kind_b = guard_a[0], guard_b[0]
    if kind_a == "anystar":
        return guard_b
    if kind_b == "anystar":
        return guard_a
    if kind_a == "any1" and kind_b == "any1":
        return ("any1",)
    if kind_a == "any1":
        return guard_b
    if kind_b == "any1":
        return guard_a
    if guard_a[1] == guard_b[1]:
        return guard_a
    return None


def _char_matchers_intersect(matcher_a, matcher_b) -> bool:
    """两个字符级迷你 NFA 是否接受同一字符序列（积自动机 BFS）。"""
    seen = set()
    stack = [(matcher_a["start"], matcher_b["start"])]
    while stack:
        pair = stack.pop()
        if pair in seen:
            continue
        seen.add(pair)
        state_a, state_b = pair
        if state_a == matcher_a["accept"] and state_b == matcher_b["accept"]:
            return True
        for guard_a, target_a in matcher_a["edges"].get(state_a, ()):
            for guard_b, target_b in matcher_b["edges"].get(state_b, ()):
                if _char_guard_conj(guard_a, guard_b) is None:
                    continue
                if (target_a, target_b) not in seen:
                    stack.append((target_a, target_b))
    return False


def _pattern_segment_nfa(pattern) -> dict:
    """把 ownership 模式编译为段级 NFA（整条仓库相对路径 = 段序列）。

    语言语义与 compile_pattern 在合法路径域上完全对齐：
      - 非法模式先经 compile_pattern 校验（OwnershipError 原样上抛）；
      - 纯字面量模式兼具 exact + 目录前缀双语义（编译规则 5）→
        等价于段序列 + 隐式尾随 "**"；
      - "**" 段 = 零或多段（四种位置在段级语言上统一；分隔符吸收
        差异只体现为字符级正则形态，不影响语言）；
      - 歧义模式（编译结果匹配空相对路径 ""，即仓库根本身——如
        "**" / "*" / "**/**" 折叠后）抛 OwnershipConflictError：
        所有权声明覆盖一切，重叠判定失去意义，拒绝猜测。
    """
    compiled = compile_pattern(pattern)
    if compiled.match(""):
        raise OwnershipConflictError(
            "歧义 ownership 模式（语义上匹配仓库根或一切路径）：%r"
            % (pattern,))
    normalized = normalize_path(pattern)
    segments = normalized.split("/")
    # 折叠相邻双星（与 compile_pattern 同规则："**/**" 等价单个 "**"）
    folded = []
    for seg in segments:
        if seg == "**" and folded and folded[-1] == "**":
            continue
        folded.append(seg)
    if not any(ch in normalized for ch in "*?"):
        # 纯字面量：exact + 目录前缀双语义 = 段序列 + 隐式尾随 "**"
        folded = folded + ["**"]
    edges = {0: []}
    counter = [0]

    def new_state() -> int:
        counter[0] += 1
        edges[counter[0]] = []
        return counter[0]

    current = 0
    for seg in folded:
        nxt = new_state()
        if seg == "**":
            # 零段：ε 直达；多段：任意单段自环
            edges[current].append(("eps", None, nxt))
            edges[current].append(("seg", _SEG_ANY, current))
        else:
            edges[current].append(("seg", _segment_matcher(seg), nxt))
        current = nxt
    return {"start": 0, "accept": current, "edges": edges}


def _eps_closure(states, edges) -> set:
    """段级 NFA 的 ε-闭包（states 为状态可迭代集合）。"""
    closure = set(states)
    stack = list(closure)
    while stack:
        state = stack.pop()
        for kind, _payload, target in edges.get(state, ()):
            if kind == "eps" and target not in closure:
                closure.add(target)
                stack.append(target)
    return closure


def _segment_nfas_overlap(nfa_a, nfa_b) -> bool:
    """两个段级 NFA 是否接受同一路径（积自动机 BFS，同步消耗段）。

    对本模块的段级 glob 语言是完备判定而非启发式：返回 False 当且
    仅当不存在任何路径同时被两个模式覆盖。子集积构造保证终止。
    """
    start = (_eps_closure((nfa_a["start"],), nfa_a["edges"]),
             _eps_closure((nfa_b["start"],), nfa_b["edges"]))
    seen = set()
    stack = [start]
    while stack:
        closure_a, closure_b = stack.pop()
        key = (frozenset(closure_a), frozenset(closure_b))
        if key in seen:
            continue
        seen.add(key)
        if nfa_a["accept"] in closure_a and nfa_b["accept"] in closure_b:
            return True
        moves_a = [(payload, target)
                   for state in closure_a
                   for kind, payload, target in nfa_a["edges"].get(state, ())
                   if kind == "seg"]
        moves_b = [(payload, target)
                   for state in closure_b
                   for kind, payload, target in nfa_b["edges"].get(state, ())
                   if kind == "seg"]
        for matcher_a, target_a in moves_a:
            for matcher_b, target_b in moves_b:
                if _char_matchers_intersect(matcher_a, matcher_b):
                    stack.append((
                        _eps_closure((target_a,), nfa_a["edges"]),
                        _eps_closure((target_b,), nfa_b["edges"])))
    return False


def scopes_overlap(scope_a, scope_b) -> bool:
    """两个 ownership scope 的路径语言是否相交（完备判定，不猜测）。

    覆盖：精确同文件、文件对目录前缀、目录对目录前缀、glob 重叠
    （复用 compile_pattern 的段级语义，两端都可用 glob）。按路径段
    边界判定——"src/auth" 不覆盖 "src/authentication.ts"，"*.py"
    不与 "src/**" 相交（前者只命中单段路径）。

    错误：
      - 任一模式非法（空路径段、非字符串等）→ OwnershipError 原样
        上抛（结构性错误，与 match_path / classify_paths 同一约定）；
      - 任一模式歧义（匹配仓库根或一切路径，如 "**" / "*"）→
        OwnershipConflictError（plan_stages 会先行预检并附上节点 id；
        本函数被直接调用时消息只含两个 scope 原文）。
    """
    return _segment_nfas_overlap(
        _pattern_segment_nfa(scope_a), _pattern_segment_nfa(scope_b))


def plan_stages(nodes) -> "list[list[str]]":
    """确定性阶段规划：同阶段成员 ownership 两两不相交（P1-C）。

    算法三句：
      1. 先做静态校验（work_unit.validate_node 逐节点 +
         dependency.graph_errors 全图），任一错误聚合抛 ValueError；
      2. 取 dependency.stage_levels 的确定性层级，层序即阶段大序，
         跨层依赖保持权威（不因 ownership 重叠重排层级）；
      3. 每层内按 id 升序遍历节点，贪心放进第一个与既有成员 scope
         两两无重叠的组，放不进则新开一组——组序列即该层内的
         确定性串行顺序。

    返回 list[list[str]]：每组是一个可并行阶段（成员 scope 两两
    不相交），组间顺序即执行顺序；同一输入两次调用输出全等。

    错误：
      - 节点形状或依赖图不合法 → ValueError（聚合中文错误消息）；
      - 任一 ownership scope 编译失败或歧义（匹配仓库根 / 一切路径）
        → OwnershipConflictError（消息点名节点 id 与 scope 原文；
        预检在任何分组发生之前完成，单节点图同样拦截）。
    """
    # 懒导入：保持本文件既有导入区与模块级依赖不动（先例：
    # runtime/fingerprint.py 对 runtime.ownership 的函数内导入）
    from runtime import dependency, work_unit
    if not isinstance(nodes, list):
        raise ValueError(
            "plan_stages：nodes 必须是数组，得到 %s" % type(nodes).__name__)
    validation = []
    for node in nodes:
        validation.extend(work_unit.validate_node(node))
    validation.extend(dependency.graph_errors(nodes))
    if validation:
        raise ValueError(
            "plan_stages：节点声明校验失败（共 %d 项）：%s"
            % (len(validation), "；".join(validation)))
    by_id = {}
    for node in nodes:
        by_id[node["id"]] = node  # id 唯一性已由 graph_errors 保证
    levels = dependency.stage_levels(nodes)

    # 编译期 scope 预检：按规划顺序逐节点编译全部 scope，非法 / 歧义
    # 在任何分组发生之前失败（fail-fast 于整图；单节点无配对同样拦截）
    for level in levels:
        for node_id in sorted(level):
            for pattern in by_id[node_id]["ownership"]:
                try:
                    _pattern_segment_nfa(pattern)
                except OwnershipConflictError as exc:
                    raise OwnershipConflictError(
                        "节点 %r 的 ownership 模式歧义：%s"
                        % (node_id, exc)) from exc
                except OwnershipError as exc:
                    raise OwnershipConflictError(
                        "节点 %r 的 ownership 模式编译失败：%s"
                        % (node_id, exc)) from exc

    # 层内贪心装箱：按 id 升序，放入第一个无重叠组，否则新开一组
    stages = []
    for level in levels:
        groups = []  # 每组 {"ids": [...], "scopes": [...]}（成员 scope 平铺）
        for node_id in sorted(level):
            scopes = list(by_id[node_id]["ownership"])
            for group in groups:
                if not any(scopes_overlap(scope, existing)
                           for scope in scopes
                           for existing in group["scopes"]):
                    group["ids"].append(node_id)
                    group["scopes"].extend(scopes)
                    break
            else:
                groups.append({"ids": [node_id], "scopes": scopes})
        stages.extend(group["ids"] for group in groups)
    return stages
