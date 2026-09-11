#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2 证据指纹层（normalized diff sha256，v2 工作块 B3.1/B3.2）。

职责：
    把升级指南 §17-§18 的「任何修复使先前审查失效」变成自动强制的
    确定性基础：验证 / 审查证据必须绑定到仓库实际状态，指纹 =
    sha256(归一化改动集表示)。本模块是纯确定性计算层，后续的指纹
    写入 / stale 检测与完成门校验都建立在它之上：
      - normalize_content() / content_digest()：换行归一（CRLF→LF）
        后取 sha256——同一逻辑内容不因换行风格（CRLF/LF）改变指纹；
      - normalize_relpath()：仓库相对路径归一与结构性校验（拒绝
        空串、绝对路径、空段、".." 越根、"." 段等无意义 / 危险形状）；
      - resolve_base()：基线修订号（git rev-parse HEAD；unborn 仓库
        返回 "-"）；
      - compute_fingerprint()：指纹绑定「基线修订 + 相关文件集的
        归一化内容状态」——任何文件增 / 删 / 内容变化 / 文件集变化 /
        基线变化都会改变指纹；只哈希 paths 列出的文件，不哈希 paths
        之外的任何文件（相关范围由调用方控制）；
      - task_fingerprint()：任务级指纹入口（B3.2）——按任务 state 的
        ownership 声明过滤 git 工作区改动文件后计算证据指纹，主会话
        记录验证/审查指纹与 Stop 完成门比对指纹共用此入口；
      - visual_evidence_status()：视觉证据 stale 检测（B3.2，§22）——
        按文件自身原始字节 sha256 与记录值比对，工作区后续变化 →
        证据 stale → 完成被拦（§20）。

归一说明：
      - 换行：只把 b"\\r\\n" 替换为 b"\\n"，不做其他任何处理（不转
        编码、不去 BOM）——Windows 检出与仓库存储的换行差异不属于
        逻辑内容变化；
      - 路径：反斜杠统一为 "/"，循环剥离开头 "./"（可重复），其余
        形状异常一律拒绝（不静默转换、不做猜测式放宽）。

依赖：
    仅 Python 3 标准库（hashlib / pathlib / subprocess），零第三方依赖，
    `python3 -S` 可运行。风格对齐 runtime/ownership.py / runtime/state.py。

来源：
    v2.0 设计（已蒸馏入 docs/architecture.md：证据指纹 / 验证/审查证据
    指纹与 stale 拦截 / 视觉证据）+ v2 升级计划工作块 B3.1、B3.2。
"""

import hashlib
import pathlib
import subprocess


class FingerprintError(Exception):
    """证据指纹层的结构性错误（非 git 仓库、git 调用失败、非法路径、读取失败等）。"""


# —— 内容归一与摘要 ——

def normalize_content(data: bytes) -> bytes:
    """换行归一：把 b"\\r\\n" 全部替换为 b"\\n"（CRLF→LF），其余字节原样。

    只做这一种归一——不做编码转换、不去 BOM、不增删任何字节。
    非 bytes 输入抛 FingerprintError（结构性错误，不静默转换）。
    """
    if not isinstance(data, bytes):
        raise FingerprintError(
            "指纹内容必须是 bytes，得到 %s" % type(data).__name__)
    return data.replace(b"\r\n", b"\n")


def content_digest(data: bytes) -> str:
    """归一内容的 sha256 摘要：normalize_content(data) 的 64 位小写十六进制。"""
    return hashlib.sha256(normalize_content(data)).hexdigest()


# —— 路径归一 ——

def normalize_relpath(path) -> str:
    """仓库相对路径归一与结构性校验。

    归一（仅两步）：
      - 反斜杠统一为 "/"；
      - 循环剥离开头 "./"（可重复，"././a" → "a"）。

    校验（归一后逐条判定，任一命中即抛 FingerprintError，消息含路径原文）：
      - 空串；
      - 以 "/" 开头（POSIX 绝对路径）；
      - 首段为 Windows 盘符形状（如 "C:"）——盘符路径同样视为绝对路径；
      - 含空路径段（连续 "//"、首尾 "/"）；
      - 任一段为 ".."（越出仓库根）；
      - 任一段为 "."（无意义路径段；"." 整路径是其特例）——若放行，
        "a/./b" 与 "a/b" 会指向同一文件却产生不同指纹，破坏确定性。

    非 str 输入抛 FingerprintError（结构性错误，不静默转换）。
    """
    if not isinstance(path, str):
        raise FingerprintError(
            "路径必须是字符串，得到 %s" % type(path).__name__)
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized == "":
        raise FingerprintError("非法指纹路径（归一后为空串）：%r" % path)
    if normalized.startswith("/"):
        raise FingerprintError("非法指纹路径（绝对路径）：%r" % path)
    segments = normalized.split("/")
    first = segments[0]
    if len(first) == 2 and first[0].isalpha() and first[1] == ":":
        raise FingerprintError("非法指纹路径（Windows 盘符绝对路径）：%r" % path)
    for seg in segments:
        if seg == "":
            raise FingerprintError(
                "非法指纹路径（含空路径段，如连续 // 或首尾 /）：%r" % path)
        if seg == "..":
            raise FingerprintError(
                "非法指纹路径（含 .. 越出仓库根）：%r" % path)
        if seg == ".":
            raise FingerprintError(
                "非法指纹路径（含无意义的 . 段）：%r" % path)
    return normalized


# —— 基线修订 ——

def resolve_base(repo_root) -> str:
    """解析基线修订号：git rev-parse HEAD；unborn 仓库（无提交）返回 "-"。

    - HEAD 存在（rc==0）→ 返回 stdout（UTF-8 容错解码）strip 后的修订号；
    - HEAD 不存在但 --git-dir 成功 → unborn 仓库，返回 "-"（基线退化为
      「仓库存在但无修订」，指纹仍可稳定计算）；
    - 两者都失败 → 非 git 仓库，抛 FingerprintError（消息含 returncode
      与 stderr 摘要前 200 字符，格式对齐 ownership.git_touched_files）；
    - git 可执行不存在 / 超时 → FingerprintError。
    subprocess 固定 timeout=3 秒（与 ownership.git_touched_files 一致：
    必须小于 Stop 钩子的 timeoutMs=5000，git 挂起时让结构性错误先于
    整进程击杀触发，降级才可见）。
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(repo_root),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
    except FileNotFoundError as exc:
        raise FingerprintError(
            "git 调用失败：找不到 git 可执行文件（%s）" % exc) from exc
    except subprocess.TimeoutExpired as exc:
        raise FingerprintError(
            "git 调用失败：rev-parse HEAD 超时（>3 秒，repo=%s）"
            % repo_root) from exc
    if proc.returncode == 0:
        return proc.stdout.decode("utf-8", errors="replace").strip()
    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--git-dir"], cwd=str(repo_root),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
    except FileNotFoundError as exc:
        raise FingerprintError(
            "git 调用失败：找不到 git 可执行文件（%s）" % exc) from exc
    except subprocess.TimeoutExpired as exc:
        raise FingerprintError(
            "git 调用失败：rev-parse --git-dir 超时（>3 秒，repo=%s）"
            % repo_root) from exc
    if probe.returncode == 0:
        return "-"
    stderr_summary = probe.stderr.decode("utf-8", errors="replace").strip()
    raise FingerprintError(
        "git 调用失败：rev-parse 返回 returncode=%d（repo=%s）；stderr：%s"
        % (probe.returncode, repo_root, stderr_summary[:200]))


# —— 指纹计算 ——

def compute_fingerprint(repo_root, paths, *, base=None) -> str:
    """计算证据指纹，返回 "sha256:" + 64 位小写十六进制。

    指纹绑定「基线修订 + 相关文件集的归一化内容状态」：
      - 同一逻辑内容不因换行风格（CRLF/LF）改变指纹（content_digest
        先做 CRLF→LF 归一）；
      - 任何文件增 / 删 / 内容变化 / 文件集变化 / 基线变化都会改变指纹；
      - 只读取 paths 列出的文件，不哈希 paths 之外的任何文件
        （相关范围由调用方控制）。

    可选参数 base（关键字专用，缺省 None 时行为与既有调用完全一致）：
      - None → 内部 resolve_base(repo_root) 取基线（非 git 仓库
        FingerprintError 自然上抛）；
      - 非 None → 直接使用（不再调 git rev-parse）——调用方已持有基线
        时省一次 git 子调用（Stop 完成门的 2 次子调用预算），指纹语义
        与缺省调用完全等价；
      - 显式 base 必须是非空 str：其他类型或空串抛 FingerprintError
        （结构性错误，不静默转换，与 normalize_relpath 的输入纪律一致）。

    流程：
      1. paths 逐项 normalize_relpath（非法路径 FingerprintError 自然
         上抛），归一后去重并按 str 排序（结果与输入顺序 / 重复无关）；
      2. 取基线（按上述 base 参数规则）；
      3. 逐路径读工作区文件 <repo_root>/<归一路径>：
           - 不存在 → 标记 "missing"；
           - 存在但是目录 → FingerprintError；
           - 读 bytes 时 OSError → FingerprintError（消息含路径）；
           - 存在 → 标记 "sha256:" + content_digest(字节内容)；
      4. preimage（UTF-8，逐行拼接、行尾 "\\n"）：
           glm-conductor/evidence-fingerprint/1
           base <base>
           file <归一路径>\\0<标记>     # 每个路径一行，按排序后顺序；
                                        # "file " 后单个空格，路径与标记
                                        # 之间单个 NUL 字符
         经 hashlib.sha256 得 hexdigest，返回 "sha256:" + hexdigest。
      paths 为空 → preimage 只有头两行（空改动集也有稳定指纹）。
    """
    normalized_set = set()
    for item in paths:
        normalized_set.add(normalize_relpath(item))
    ordered = sorted(normalized_set)
    if base is None:
        base = resolve_base(repo_root)
    elif not isinstance(base, str) or base == "":
        raise FingerprintError(
            "显式 base 必须是非空字符串，得到 %r" % (base,))
    lines = ["glm-conductor/evidence-fingerprint/1", "base " + base]
    for rel in ordered:
        target = pathlib.Path(repo_root) / rel
        if not target.exists():
            mark = "missing"
        else:
            if target.is_dir():
                raise FingerprintError(
                    "指纹目标路径是目录而非文件：%r（repo=%s）"
                    % (rel, repo_root))
            try:
                with open(target, "rb") as fh:
                    data = fh.read()
            except OSError as exc:
                raise FingerprintError(
                    "读取指纹目标文件失败：%r（repo=%s）：%s"
                    % (rel, repo_root, exc)) from exc
            mark = "sha256:" + content_digest(data)
        lines.append("file " + rel + "\0" + mark)
    preimage = "".join(line + "\n" for line in lines).encode("utf-8")
    return "sha256:" + hashlib.sha256(preimage).hexdigest()


# —— 任务指纹入口与 stale 检测（v2 工作块 B3.2） ——

def task_fingerprint(repo_root, task_state, *, touched=None, base=None) -> str:
    """按任务 ownership 声明计算任务级证据指纹（B3.2）。

    这是主会话记录验证/审查证据指纹与 Stop 完成门比对指纹的同一入口：
    两侧必须调用同一函数，得到的指纹才可比（升级指南 §19/§20——任何
    工作区变化使旧证据指纹失效，完成门据此拦截）。

    可选参数 touched / base（关键字专用，缺省 None 时行为与既有调用
    完全一致）：Stop 完成门传入 touched/base 以复用单次 git 调用
    （每次 Stop 恒为 1 次 status + 1 次 rev-parse，不随任务数增长）；
    主会话记录证据仍用缺省调用（内部自取），两者语义等价。
      - touched 非 None → 跳过 git status，直接用该清单做范围过滤
        （须为 git_touched_files 同口径的路径清单；非 list 抛
        FingerprintError，结构性错误不静默转换；项的类型错误由
        classify_paths 的既有规则抛 OwnershipError）；
      - base 透传 compute_fingerprint（None → 内部 resolve_base；
        非 None 须为非空 str，规则见彼处 docstring）。

    范围规则：
      - 从 task_state 读 ownership.files：非 list 或全非 str 时按空
        处理；只保留非空 str 项作为声明模式；
      - touched = 传入清单，或 git_touched_files(repo_root)（懒导入
        runtime.ownership，避免模块级依赖环）；git 失败时
        OwnershipError 自然上抛，由调用方处理；
      - 声明了 ownership（过滤后非空）→ 范围 = classify_paths(touched,
        patterns) 的 owned_hits（只哈希声明覆盖的改动文件）；
      - 未声明 → 范围 = touched 全部；
      - 指纹 = compute_fingerprint(repo_root, 范围, base=base)。

    task_state 非 dict 抛 FingerprintError（结构性错误，不静默转换）。
    """
    if not isinstance(task_state, dict):
        raise FingerprintError(
            "task_state 必须是 dict，得到 %s" % type(task_state).__name__)
    from runtime import ownership  # 懒导入：避免模块级依赖环
    ownership_state = task_state.get("ownership")
    patterns = []
    if isinstance(ownership_state, dict):
        raw_files = ownership_state.get("files")
        if isinstance(raw_files, list):
            patterns = [item for item in raw_files
                        if isinstance(item, str) and item != ""]
    if touched is None:
        touched = ownership.git_touched_files(repo_root)
    elif not isinstance(touched, list):
        raise FingerprintError(
            "显式 touched 必须是路径清单（list），得到 %s"
            % type(touched).__name__)
    if patterns:
        owned_hits, _ = ownership.classify_paths(touched, patterns)
        scope = owned_hits
    else:
        scope = touched
    return compute_fingerprint(repo_root, scope, base=base)


def visual_evidence_status(repo_root, entries) -> "list[dict]":
    """视觉证据 stale 检测（B3.2，升级指南 §22）。

    entries 是 state.visual_evidence 数组（list of dict，每项含
    path / sha256）；非 list 输入抛 FingerprintError，项非 dict 同样
    抛 FingerprintError（结构性错误，不静默转换）。

    逐项：
      - 路径先 normalize_relpath（非法路径 FingerprintError 上抛）；
      - 读 <repo_root>/<归一路径> 的原始字节取 sha256——不做 CRLF
        归一：截图等二进制证据按文件自身字节哈希（与 content_digest
        的唯一语义差别：后者面向文本逻辑内容，先做 CRLF→LF 归一）；
      - 文件不存在或读取时 OSError → current=None；目标是目录 →
        current=None 且 stale=True（容错优先，不抛）；
      - recorded（entry["sha256"]）非非空 str（缺失 / 空串 / 非 str）
        → 该项直接 stale=True（缺 recorded 视为 stale：证据未绑定
        内容即不可信——否则 recorded 与 current 同时为 None 会被误判
        为新鲜）；
      - stale = recorded 合法时 (current != recorded)，否则恒 True。

    返回 list（顺序与输入一致），每项 {"path": 原始 path,
    "recorded": 记录值, "current": 当前值或 None, "stale": bool}。
    """
    if not isinstance(entries, list):
        raise FingerprintError(
            "visual_evidence 必须是数组，得到 %s" % type(entries).__name__)
    results = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise FingerprintError(
                "visual_evidence 项必须是 JSON 对象，得到 %s"
                % type(entry).__name__)
        path = entry.get("path")
        recorded = entry.get("sha256")
        normalized = normalize_relpath(path)
        target = pathlib.Path(repo_root) / normalized
        current = None
        if target.exists() and not target.is_dir():
            try:
                with open(target, "rb") as fh:
                    current = hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                current = None
        if isinstance(recorded, str) and recorded != "":
            stale = current != recorded
        else:
            # 缺 recorded 视为 stale：证据未绑定内容即不可信（否则
            # recorded 与 current 同时为 None 会被误判为新鲜）
            stale = True
        results.append({
            "path": path,
            "recorded": recorded,
            "current": current,
            "stale": stale,
        })
    return results
