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

三种声明形式（docs/glm-conductor-v2-upgrade-guide-final.md §11）：
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

依赖：
    仅 Python 3 标准库（re / subprocess），零第三方依赖，
    `python3 -S` 可运行。风格对齐 runtime/state.py / runtime/journal.py。

来源：
    docs/glm-conductor-v2-upgrade-guide-final.md §11（ownership path rules）
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
