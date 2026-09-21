#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.4 任务级变更标识层（change identity，Phase 2 工作包 P2-B）。

职责：
    v2.4 的唯一新鲜度原语：compute_change_id() 把「任务相关的仓库变更
    状态」确定性压缩为 "sha256:" + 64 位小写十六进制。同一实现必须被
    三处共用（单一事实源，禁止旁路重算）：
      - 主会话记录验证结果（W3 存 validation.change_id）；
      - 记录评审结论（W3 存 review.change_id）；
      - Stop 完成门新鲜度比对（W4：当前 change_id 与记录值相等才算
        新鲜——任何任务相关仓库变化使既有验证 / 评审记录过时）。
    本模块不提供任何 per-unit 指纹 / receipt API（v2.3 的
    task_fingerprint / visual_evidence_status 概念在 v2.4 无对应物）。

语义：
      - 标识绑定「基线修订 + 相关文件集的归一化内容状态」：任何相关
        文件增 / 删 / 内容变化 / 相关文件集变化 / 基线变化都会改变
        标识；
      - 只哈希 relevant_files 列出的**字面文件路径**（不是 ownership
        scope 串）——任务相关范围由调用方解析（按 P2 规格 + AF-01：
        DAG ownership scope 并集 ∩ git 工作区实际改动），本模块不做
        ownership 匹配、不枚举工作区；
      - Conductor 记账文件（`.glm-conductor/` 目录下一切）一律剔除：
        编排器自身写账本不得使自己过时（与 ownership.git_touched_files
        的运行时状态目录豁免同一口径）；
      - relevant_files 顺序与重复不影响结果（归一后去重排序）。

与 runtime/fingerprint.py 的关系：
    规范化与摘要内部借鉴自它（换行归一 CRLF→LF / 仓库相对路径归一与
    结构校验 / rev-parse HEAD 基线解析 / 域分隔 preimage 摘要），按
    Phase 2 计划移植于此——不 import 它、不修改它：它是 v2.3 证据指纹
    层，W6 整体退役；本模块是 v2.4 的独立替代原语，域分隔前缀不同
    （glm-conductor/change-id/1），两者的摘要值不可互换比对。

归一说明：
      - 内容：只把 b"\\r\\n" 替换为 b"\\n"，不做编码转换、不去 BOM
        ——同一逻辑内容不因换行风格（CRLF/LF）改变标识；
      - 路径：反斜杠统一为 "/"，循环剥离开头 "./"，其余形状异常一律
        拒绝（不静默转换、不做猜测式放宽）。

依赖：
    仅 Python 3 标准库（hashlib / pathlib / subprocess），零第三方依赖，
    `python3 -S` 可运行。风格对齐 runtime/ownership.py / runtime/state.py。

来源：
    v2.4 Phase 2 执行计划单元 W2（P2-B）与
    docs/roadmap/V2_4_PHASE_2_STATE_ASSURANCE_RETIREMENT_SPEC.md §3
    （单一任务级 change_id 新鲜度机制）。
"""

import hashlib
import pathlib
import subprocess


class ChangeIdError(Exception):
    """变更标识层的结构性错误（非 git 仓库、git 调用失败、非法路径、读取失败等）。"""


# Conductor 运行时记账目录名（仓库根相对）：其下一切文件不参与变更标识
BOOKKEEPING_DIRNAME = ".glm-conductor"

# 摘要域分隔前缀：v2.4 change_id 专用，与 v2.3 evidence-fingerprint 域隔离
_DOMAIN_TAG = "glm-conductor/change-id/1"


# —— 内容归一与摘要 ——

def normalize_content(data: bytes) -> bytes:
    """换行归一：把 b"\\r\\n" 全部替换为 b"\\n"（CRLF→LF），其余字节原样。

    只做这一种归一——不做编码转换、不去 BOM、不增删任何字节。
    非 bytes 输入抛 ChangeIdError（结构性错误，不静默转换）。
    """
    if not isinstance(data, bytes):
        raise ChangeIdError(
            "变更标识内容必须是 bytes，得到 %s" % type(data).__name__)
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

    校验（归一后逐条判定，任一命中即抛 ChangeIdError，消息含路径原文）：
      - 空串；
      - 以 "/" 开头（POSIX 绝对路径）；
      - 首段为 Windows 盘符形状（如 "C:"）——盘符路径同样视为绝对路径；
      - 含空路径段（连续 "//"、首尾 "/"）；
      - 任一段为 ".."（越出仓库根）；
      - 任一段为 "."（无意义路径段；"." 整路径是其特例）——若放行，
        "a/./b" 与 "a/b" 会指向同一文件却产生不同标识，破坏确定性。

    非 str 输入抛 ChangeIdError（结构性错误，不静默转换）。
    """
    if not isinstance(path, str):
        raise ChangeIdError(
            "路径必须是字符串，得到 %s" % type(path).__name__)
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized == "":
        raise ChangeIdError("非法变更标识路径（归一后为空串）：%r" % path)
    if normalized.startswith("/"):
        raise ChangeIdError("非法变更标识路径（绝对路径）：%r" % path)
    segments = normalized.split("/")
    first = segments[0]
    if len(first) == 2 and first[0].isalpha() and first[1] == ":":
        raise ChangeIdError(
            "非法变更标识路径（Windows 盘符绝对路径）：%r" % path)
    for seg in segments:
        if seg == "":
            raise ChangeIdError(
                "非法变更标识路径（含空路径段，如连续 // 或首尾 /）：%r" % path)
        if seg == "..":
            raise ChangeIdError(
                "非法变更标识路径（含 .. 越出仓库根）：%r" % path)
        if seg == ".":
            raise ChangeIdError(
                "非法变更标识路径（含无意义的 . 段）：%r" % path)
    return normalized


def is_bookkeeping_path(normalized: str) -> bool:
    """归一后的仓库相对路径是否落在 Conductor 记账目录（.glm-conductor/）内。

    口径与 ownership.git_touched_files 的运行时状态目录豁免一致：路径
    恰为目录自身（".glm-conductor"）或以其为完整首段前缀
    （".glm-conductor/..."）都算记账路径；".glm-conductor-notes/..." 之
    类的形状相近目录不算。入参须是 normalize_relpath 的返回值（正斜杠、
    无 "./" 前缀）；本函数只做前缀判定，不做二次归一。
    """
    return (normalized == BOOKKEEPING_DIRNAME
            or normalized.startswith(BOOKKEEPING_DIRNAME + "/"))


# —— 基线修订 ——

def resolve_base(repo_root) -> str:
    """解析基线修订号：git rev-parse HEAD；unborn 仓库（无提交）返回 "-"。

    - HEAD 存在（rc==0）→ 返回 stdout（UTF-8 容错解码）strip 后的修订号；
    - HEAD 不存在但 --git-dir 成功 → unborn 仓库，返回 "-"（基线退化为
      「仓库存在但无修订」，标识仍可稳定计算）；
    - 两者都失败 → 非 git 仓库，抛 ChangeIdError（消息含 returncode
      与 stderr 摘要前 200 字符，格式对齐 ownership.git_touched_files）；
    - git 可执行不存在 / 超时 → ChangeIdError。
    subprocess 固定 timeout=3 秒（与 ownership.git_touched_files 一致：
    必须小于 Stop 钩子的 timeoutMs=5000，git 挂起时让结构性错误先于
    整进程击杀触发，降级才可见）。
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(repo_root),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
    except FileNotFoundError as exc:
        raise ChangeIdError(
            "git 调用失败：找不到 git 可执行文件（%s）" % exc) from exc
    except subprocess.TimeoutExpired as exc:
        raise ChangeIdError(
            "git 调用失败：rev-parse HEAD 超时（>3 秒，repo=%s）"
            % repo_root) from exc
    if proc.returncode == 0:
        return proc.stdout.decode("utf-8", errors="replace").strip()
    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--git-dir"], cwd=str(repo_root),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
    except FileNotFoundError as exc:
        raise ChangeIdError(
            "git 调用失败：找不到 git 可执行文件（%s）" % exc) from exc
    except subprocess.TimeoutExpired as exc:
        raise ChangeIdError(
            "git 调用失败：rev-parse --git-dir 超时（>3 秒，repo=%s）"
            % repo_root) from exc
    if probe.returncode == 0:
        return "-"
    stderr_summary = probe.stderr.decode("utf-8", errors="replace").strip()
    raise ChangeIdError(
        "git 调用失败：rev-parse 返回 returncode=%d（repo=%s）；stderr：%s"
        % (probe.returncode, repo_root, stderr_summary[:200]))


# —— 变更标识计算 ——

def compute_change_id(repo_root, relevant_files, base=None) -> str:
    """计算任务级变更标识，返回 "sha256:" + 64 位小写十六进制。

    标识绑定「基线修订 + 相关文件集的归一化内容状态」：
      - 同一逻辑内容不因换行风格（CRLF/LF）改变标识（content_digest
        先做 CRLF→LF 归一）；
      - 任何相关文件增 / 删 / 内容变化 / 相关文件集变化 / 基线变化都
        会改变标识；
      - 只读取 relevant_files 列出的字面文件路径，不哈希其之外的任何
        文件（入参不是 ownership scope 串，本函数不展开 scope——
        scope → 实际改动文件集的解析由 task.relevant_changed_files
        负责，AF-01 责任分离）；
      - relevant_files 中归一后落在 `.glm-conductor/`（Conductor 记账
        目录）内的路径一律剔除——编排器自身写账本不改变标识；
      - 归一后去重并按 str 排序：结果与输入顺序 / 重复无关。

    参数 base（缺省 None）：
      - None → 内部 resolve_base(repo_root) 取基线（非 git 仓库
        ChangeIdError 自然上抛）；
      - 非 None → 直接使用（不再调 git rev-parse）——调用方已持有基线
        时省一次 git 子调用（Stop 完成门的子调用预算），标识语义与
        缺省调用完全等价；
      - 显式 base 必须是非空 str：其他类型或空串抛 ChangeIdError
        （结构性错误，不静默转换，与 normalize_relpath 的输入纪律一致）。

    流程：
      1. relevant_files 逐项 normalize_relpath（非法路径 ChangeIdError
         自然上抛），归一后剔除记账路径、去重并按 str 排序；
      2. 取基线（按上述 base 参数规则）；
      3. 逐路径读工作区文件 <repo_root>/<归一路径>：
           - 不存在 → 标记 "missing"（删除同样改变标识）；
           - 存在但是目录 → ChangeIdError；
           - 读 bytes 时 OSError → ChangeIdError（消息含路径）；
           - 存在 → 标记 "sha256:" + content_digest(字节内容)；
      4. preimage（UTF-8，逐行拼接、行尾 "\\n"）：
           glm-conductor/change-id/1
           base <base>
           file <归一路径>\\0<标记>     # 每个路径一行，按排序后顺序；
                                        # "file " 后单个空格，路径与标记
                                        # 之间单个 NUL 字符
         经 hashlib.sha256 得 hexdigest，返回 "sha256:" + hexdigest。

    relevant_files 为空 → preimage 只有头两行（空相关集也有稳定标识）。
    本函数是验证记录 / 评审记录 / 完成门新鲜度比对共用的唯一入口
    （消费方 W3 / W4 必须两侧同调此函数，标识才可比）。
    """
    normalized_set = set()
    for item in relevant_files:
        normalized_set.add(normalize_relpath(item))
    # Conductor 记账文件剔除：编排器自身账本不入标识（顺序无关先排序）
    ordered = sorted(
        rel for rel in normalized_set if not is_bookkeeping_path(rel))
    if base is None:
        base = resolve_base(repo_root)
    elif not isinstance(base, str) or base == "":
        raise ChangeIdError(
            "显式 base 必须是非空字符串，得到 %r" % (base,))
    lines = [_DOMAIN_TAG, "base " + base]
    for rel in ordered:
        target = pathlib.Path(repo_root) / rel
        if not target.exists():
            mark = "missing"
        else:
            if target.is_dir():
                raise ChangeIdError(
                    "变更标识目标路径是目录而非文件：%r（repo=%s）"
                    % (rel, repo_root))
            try:
                with open(target, "rb") as fh:
                    data = fh.read()
            except OSError as exc:
                raise ChangeIdError(
                    "读取变更标识目标文件失败：%r（repo=%s）：%s"
                    % (rel, repo_root, exc)) from exc
            mark = "sha256:" + content_digest(data)
        lines.append("file " + rel + "\0" + mark)
    preimage = "".join(line + "\n" for line in lines).encode("utf-8")
    return "sha256:" + hashlib.sha256(preimage).hexdigest()
