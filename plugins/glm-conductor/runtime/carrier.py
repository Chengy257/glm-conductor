#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.7.0 W1 OS 唤醒载体（L1 floor-on-OS）运行时。

职责（spec docs/roadmap/V2_7_0_IMPLEMENTATION_SPEC.md §3.3/§3.4；
D4 终裁：OS 载体只做 floor 节拍——精度层沿用后台 Bash sleep）：
    三级面，全部零第三方依赖（仅 subprocess / sys / os / shutil /
    platform 标准库，``python3 -S`` 可运行）：

    1. 纯构造面（零执行、零 I/O、可单测——spec §3.6「carrier argv
       纯构造测试（不真建）」的落点）：
         - Windows schtasks：install_argv(task_name, command_line,
           interval_hours) / remove_argv(task_name) 精确 argv 构造；
         - POSIX crontab：install_line（``0 */N * * * <cmd>`` 行）、
           carrier_marker / install_block（marker 行 + 载体行成对——
           crontab 无任务名，任务身份只能靠 marker 行承载）、
           remove_lines（marker 块解析移除）；
         - 唤醒命令构造：wake_command（node+cjs 冻结形态）、
           wake_command_on_path（PATH zcode 同协议形态）、
           resolve_wake_argv（机器级解析入口）；
         - 命令行渲染：render_windows_command_line /
           render_posix_command_line / render_command_line。
    2. 执行面（subprocess 精确 argv——list 形、恒 shell=False）：
       install_carrier / remove_carrier，返回四键
       {ok, argv, returncode, stderr}（stderr 为尾部截断）。
    3. 探针面（纯本地零写零执行副作用）：probe——只 shutil.which +
       路径存在性，绝不建探针任务（spec R3：schtasks 受限环境自然
       降 sleep_only，无硬失败）。

命令行转义规则（本模块的核心纪律，两级都要写明）：
    - **/TR 整串一个 argv，绝不拼 shell**：schtasks 的 /TR 值是
      install_argv 返回 argv 的**单一元素**，由 subprocess 以 list
      形 + shell=False 直接交 Windows 进程创建层（外层引用由
      CreateProcess 命令行规则处理，本模块用 subprocess.list2cmdline
      同款算法逐元素渲染，见 render_windows_command_line）——本模块
      从不组装 shell 字符串、从不 shell=True；
    - **POSIX 单引号转义规则**：crontab 行的命令段由 /bin/sh 解释，
      需要引用的元素用单引号包裹、单引号本身转义为 ``'\\''``（见
      render_posix_command_line）；
    - **Windows 双引号规则**：含空白 / 引号 / 空串的元素用双引号
      包裹、内部双引号按反斜杠计数翻倍转义（MSVCRT argv 惯例，与
      subprocess.list2cmdline 一致）。

唤醒命令形态（spec §3.3，B0 P-1 实证改案）：
    ``node <zcode.cjs> --resume <sess_id> --cwd <repo>
    --workflow-mode alwaysOn -p <prompt>``——workflow-mode 必须
    alwaysOn（headless 下 onDemand 不注册工作流工具）；headless ask
    层自动放行，唤醒轮零权限摩擦（护栏=唤醒提示词紧约束，
    DEFAULT_WAKE_PROMPT 只承担最小指向——恢复上下文由 SessionStart
    钩子注入承载，统一唤醒协议模板归技能层）。

L1 能力探针边界（probe 输出注释，spec §3.4）：
    本探针只判 **L1（os_timer）能力**——zcode CLI 可达性（PATH
    ``zcode`` → 默认装位 zcode.cjs 存在性 → node+cjs 组合）与 OS
    调度器可执行性（Windows schtasks / POSIX crontab，shutil.which
    即可）；**L0 native 的 CronCreate 宿主工具存在性不归本探针**——
    那是 runbook 判定（主会话自查宿主工具面），probe 绝不触碰宿主
    工具面。

依赖：
    仅 Python 3 标准库（subprocess / sys / os / shutil / platform），
    零第三方依赖；3.7 兼容语法。中文 docstring、英文错误消息
    （错误经 CarrierError / ValueError 向 CLI 薄壳透出）。
"""

import os
import platform
import shutil
import subprocess

# ZCode CLI 默认装位（spec §3.4 探测序第二级；Windows 默认安装路径，
# POSIX 上通常不存在——resolve_wake_argv 会自然落 PATH zcode 形态）。
DEFAULT_ZCODE_CJS = "C:/Program Files/ZCode/resources/glm/zcode.cjs"

# 唤醒提示词缺省值（spec §3.3「护栏=唤醒提示词紧约束」的兜底形态；
# 统一唤醒协议模板归 continuity 技能层，CLI --prompt 可显式覆盖）。
DEFAULT_WAKE_PROMPT = (
    "GLM Conductor scheduled wake: this is a headless wake turn. "
    "Read the GLM CONDUCTOR RESUME CONTEXT injected at session start "
    "and follow the continuity skill runbook for this repository; "
    "do not start unrelated work.")

# OS 载体任务名前缀（spec §3.3：task_name=glm-conductor-<task-id>；
# task 层 os_timer 见证 id 的 ``os:schtasks:`` 前缀由调用方按
# 「os:<调度器>:<task_name>」形态在本名外包一层，本模块只管本名）。
TASK_NAME_PREFIX = "glm-conductor-"

# POSIX crontab marker 行模板（crontab 无任务名——载体行必须与
# marker 成对写入，remove_lines 按 marker 定位成对移除）。
CRON_MARKER_TEMPLATE = "# glm-conductor %s"

# subprocess 单步超时（秒）——schtasks / crontab 都是亚秒级工具，
# 30s 已是宽裕上限；超时按执行失败如实报告，绝不挂死调用方。
_SUBPROCESS_TIMEOUT_S = 30

# stderr 尾部截断上限（字符）——执行面只回尾部，避免巨量诊断噪音。
STDERR_TAIL_LIMIT = 2000

# POSIX 渲染的安全字符集（无需引用直出；其余一律单引号包裹）。
_POSIX_SAFE_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789_-+=/:.,@%")


class CarrierError(Exception):
    """载体面环境级失败（如 zcode CLI 不可达）——运行期异常口径，
    由 CLI 薄壳映射为退出码 1（参数值非法走 ValueError，退出码 2）。"""


def is_windows():
    """当前平台是否 Windows（platform.system 口径，与 probe 的
    platform 输出同源；进程内恒定，方便单测经 monkeypatch 分支）。"""
    return platform.system() == "Windows"


# ============================================================
# —— 纯构造面：Windows schtasks argv ——
# ============================================================

def carrier_task_name(task_id):
    """task_id → OS 载体任务名 ``glm-conductor-<task-id>``（纯构造）。

    task_id 非空 str 才合法（空 / 非串 → ValueError，参数值非法
    口径）；task_id 其余形态不在此闸（OS 任务名对字符集宽容，
    任务层身份校验归任务面）。"""
    if not isinstance(task_id, str) or task_id == "":
        raise ValueError(
            "carrier: task_id must be a non-empty string, got %r"
            % (task_id,))
    return TASK_NAME_PREFIX + task_id


def install_argv(task_name, command_line, interval_hours):
    """Windows schtasks 创建 argv（纯构造，绝不执行）。

    恒为 ``["schtasks", "/Create", "/TN", task_name, "/TR",
    command_line, "/SC", "HOURLY", "/MO", str(interval_hours), "/F"]``：
      - /TR 的值是**单一 argv 元素**（整串一个 argv，绝不拼 shell）；
      - /SC HOURLY + /MO N = 每 N 小时触发（floor 节拍；schtasks 对
        HOURLY 的 /MO 自身限 1-23，越界由 OS 报错、执行面如实透出）；
      - /F = 同名强制覆盖（重装幂等语义）。
    interval_hours 经 str() 进 argv（调度器要数字面量；入参校验归
    install_carrier）。"""
    return ["schtasks", "/Create", "/TN", task_name, "/TR", command_line,
            "/SC", "HOURLY", "/MO", str(interval_hours), "/F"]


def remove_argv(task_name):
    """Windows schtasks 删除 argv（纯构造，绝不执行）：
    ``["schtasks", "/Delete", "/TN", task_name, "/F"]``。/F = 无条件
    强制删除（跳过确认）；任务不存在时 OS 返回非零——执行面如实
    报告（OS 删除真实有效，无三态劣化，见 remove_carrier）。"""
    return ["schtasks", "/Delete", "/TN", task_name, "/F"]


# ============================================================
# —— 纯构造面：POSIX crontab 行 ——
# ============================================================

def install_line(command_line, interval_hours):
    """POSIX crontab 载体行（纯构造）：``0 */N * * * <command_line>``。

    每小时第 0 分触发的每 N 小时节拍（crontab 五字段 + 命令段；
    命令段的引用形态由 render_posix_command_line 决定，调用方先渲染
    再传入）。interval_hours 须为 >= 1 的整数（int() 失败 / < 1 →
    ValueError）——crontab 步进语法 ``*/0`` 非法，必须在此闸住。"""
    interval = int(interval_hours)
    if interval < 1:
        raise ValueError(
            "carrier: interval hours must be an integer >= 1, got %r"
            % (interval_hours,))
    return "0 */%d * * * %s" % (interval, command_line)


def carrier_marker(task_name):
    """POSIX 载体 marker 行：``# glm-conductor <task_name>``。

    crontab 无任务名概念——install_block 恒以 marker 行 + 载体行
    **成对**写入，remove_lines 按 marker 定位成对移除；同 crontab
    内其它任务（含其它 conductor 任务的 marker 对）逐字保留。"""
    return CRON_MARKER_TEMPLATE % (task_name,)


def install_block(task_name, command_line, interval_hours):
    """POSIX 安装块（纯构造）：marker 行 + 载体行成对（换行相连）。
    install_carrier 的 POSIX 分支以本块做「移除旧块再追加」的幂等
    重装（与 Windows /F 同语义）。"""
    return carrier_marker(task_name) + "\n" + install_line(command_line,
                                                           interval_hours)


def remove_lines(crontab_text, task_name):
    """POSIX 解析移除（纯构造）：从 crontab 全文摘除 task_name 的
    marker 块——marker 行 + 其紧邻行（非空才成对摘除：install_block
    写出的载体行恒与 marker 紧邻；紧邻为空行即块边界，空行保留、
    不跨行吞并）；marker 位于末尾无跟随行时仅摘 marker 自身。返回
    二元组 ``(new_text, removed_count)``：new_text 为其余行逐字保留
    的全文（原文以换行结尾且仍有余行时补回尾换行——crontab 文件应
    以换行结尾）；removed_count 为摘除的块数。纯字符串处理，零
    I/O。"""
    marker = carrier_marker(task_name)
    lines = crontab_text.splitlines()
    kept = []
    removed = 0
    index = 0
    while index < len(lines):
        line = lines[index]
        if line == marker:
            removed += 1
            following = (lines[index + 1]
                         if index + 1 < len(lines) else None)
            if following is not None and following.strip() != "":
                index += 2  # marker + 载体行成对摘除
            else:
                index += 1  # 孤 marker（手改残留）：仅摘 marker
            continue
        kept.append(line)
        index += 1
    new_text = "\n".join(kept)
    if new_text and crontab_text.endswith("\n"):
        new_text += "\n"
    return new_text, removed


# ============================================================
# —— 纯构造面：唤醒命令与命令行渲染 ——
# ============================================================

def wake_command(zcode_cjs_path, session_id, repo_root, prompt):
    """L1 唤醒命令（冻结形态，spec §3.3 + B0 P-1 实证改案）：
    ``["node", <cjs>, "--resume", <sess>, "--cwd", <repo>,
    "--workflow-mode", "alwaysOn", "-p", <prompt>]``。
    workflow-mode 恒 alwaysOn——headless 下 onDemand 不注册工作流
    工具（B0 实证），唤醒轮必须是 alwaysOn 会话；list 形纯构造，
    绝不拼接执行。"""
    return ["node", zcode_cjs_path, "--resume", session_id, "--cwd",
            repo_root, "--workflow-mode", "alwaysOn", "-p", prompt]


def wake_command_on_path(zcode_exe, session_id, repo_root, prompt):
    """L1 唤醒命令的 PATH zcode 形态（同一唤醒协议，入口换成 PATH
    上已解析的 zcode 可执行文件）：默认装位 zcode.cjs 不存在（典型
    POSIX）而 PATH 有 zcode 时由 resolve_wake_argv 选用。"""
    return [zcode_exe, "--resume", session_id, "--cwd", repo_root,
            "--workflow-mode", "alwaysOn", "-p", prompt]


def resolve_wake_argv(session_id, repo_root, prompt):
    """机器级唤醒命令解析（纯构造，零执行）：默认装位 zcode.cjs
    存在 → wake_command 冻结形态（node+cjs）；否则 PATH 有 zcode →
    wake_command_on_path 形态；两者皆缺 → CarrierError（先跑
    wake-carrier-probe，环境不具备 L1 CLI 能力）。"""
    if os.path.isfile(DEFAULT_ZCODE_CJS):
        return wake_command(DEFAULT_ZCODE_CJS, session_id, repo_root,
                            prompt)
    zcode_exe = shutil.which("zcode")
    if zcode_exe:
        return wake_command_on_path(zcode_exe, session_id, repo_root,
                                    prompt)
    raise CarrierError(
        "zcode CLI not found (no %r on disk and no 'zcode' on PATH); "
        "run wake-carrier-probe first" % (DEFAULT_ZCODE_CJS,))


def render_windows_command_line(argv):
    """argv → Windows 命令行串（纯构造；/TR 值的渲染规则）。

    逐元素按 MSVCRT argv 惯例引用（与 subprocess.list2cmdline 同款
    算法——借 list2cmdline 对单元素列表的输出即为该元素的规范引用
    形）：含空白 / 双引号 / 空串的元素用双引号包裹、内部双引号按
    前置反斜杠计数翻倍转义。产物只作为 install_argv 返回 argv 的
    单一 /TR 元素——**绝不 shell 拼接、绝不 shell=True**。"""
    return " ".join(subprocess.list2cmdline([item]) for item in argv)


def render_posix_command_line(argv):
    """argv → POSIX sh 命令行串（纯构造；crontab 命令段渲染规则）。

    **单引号转义规则**：元素为空或含安全字符集
    （[A-Za-z0-9_-+=/:.,@%]）之外字符时，用单引号包裹整个元素，
    元素内单引号转义为 ``'\\''``（关闭引号 + 转义引号 + 重开引号，
    POSIX sh 标准写法）——crontab 命令段由 /bin/sh 解释，单引号内
    一切字符（含 $ 与反引号）均失去活动性，是最稳引用。"""
    parts = []
    for item in argv:
        if item == "" or any(ch not in _POSIX_SAFE_CHARS for ch in item):
            parts.append("'" + item.replace("'", "'\\''") + "'")
        else:
            parts.append(item)
    return " ".join(parts)


def render_command_line(argv):
    """按当前平台选渲染规则（Windows → 双引号规则；POSIX → 单引号
    规则）——CLI 薄壳把 wake_command 的 list 形产物桥接为
    install_argv / install_block 所需 command_line 串的统一入口。"""
    if is_windows():
        return render_windows_command_line(argv)
    return render_posix_command_line(argv)


# ============================================================
# —— 执行面：subprocess 精确 argv（list 形，恒 shell=False） ——
# ============================================================

def _stderr_tail(text):
    """stderr 尾部截断（None / 空串 → ""；超限只保留尾部
    STDERR_TAIL_LIMIT 字符——执行面诊断噪音上限）。"""
    if not text:
        return ""
    return text.strip()[-STDERR_TAIL_LIMIT:]


def _run_argv(argv, input_text=None):
    """精确 argv 执行单步（list 形、shell=False 恒定——本模块红线
    绝不 shell=True）。返回三元组 (returncode, stdout, stderr)：
    spawn 失败（找不到可执行文件 / 超时 / 其它 OSError）→
    (None, "", 英文原因)——returncode=None 即「进程未跑起来」，
    由调用方如实落入 ok=False。文本模式 + errors="replace"（
    schtasks 在非 UTF-8 控制台代码页下的输出绝不炸解码；stderr
    尾部只是诊断辅助，代码页错配时可能乱码——契约事实是 ok 与
    returncode，二者恒为 ASCII 干净值）。"""
    try:
        proc = subprocess.run(
            argv, input=input_text, capture_output=True, text=True,
            errors="replace", shell=False, timeout=_SUBPROCESS_TIMEOUT_S)
    except FileNotFoundError as exc:
        return None, "", "executable not found: %s" % (exc,)
    except subprocess.TimeoutExpired:
        return None, "", "timed out after %ss: %r" % (
            _SUBPROCESS_TIMEOUT_S, argv)
    except OSError as exc:
        return None, "", "spawn failed: %s" % (exc,)
    return proc.returncode, proc.stdout, proc.stderr


def _validate_install_params(task_name, command_line, interval_hours):
    """install_carrier 入参闸（参数值非法 → ValueError，CLI 映射
    退出码 2）：task_name / command_line 非空 str；interval 为
    >= 1 整数。"""
    if not isinstance(task_name, str) or task_name == "":
        raise ValueError(
            "carrier: task_name must be a non-empty string, got %r"
            % (task_name,))
    if not isinstance(command_line, str) or command_line == "":
        raise ValueError(
            "carrier: command_line must be a non-empty string, got %r"
            % (command_line,))
    try:
        interval = int(interval_hours)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "carrier: interval hours must be an integer >= 1, got %r"
            % (interval_hours,)) from exc
    if interval < 1:
        raise ValueError(
            "carrier: interval hours must be an integer >= 1, got %r"
            % (interval_hours,))
    return interval


def install_carrier(task_name, command_line, interval_hours):
    """安装 OS 载体（执行面）。返回四键
    ``{ok, argv, returncode, stderr}``（stderr 为尾部截断）。

    Windows：schtasks /Create /F 单步（/F 同名强制覆盖=重装幂等）。
    POSIX：读-改-写两步——``crontab -l`` 读全文（失败视为空表：
    crontab 对无 crontab 用户本就返回非零）→ remove_lines 摘同名
    marker 旧块 → 追加新块 → ``crontab -`` 整表写回；结果报告
    **决定性写步**的 argv / returncode / stderr（读步失败不阻断——
    空表合法起步；读步 stderr 仅在写步 stderr 为空时兜底透出）。
    入参非法 → ValueError（CLI 侧映射退出码 2）；OS 拒绝 →
    ok=false 如实透出（R3：受限环境安装失败无硬崩，调用方降级
    sleep_only）。绝不 shell=True。"""
    interval = _validate_install_params(task_name, command_line,
                                        interval_hours)
    if is_windows():
        argv = install_argv(task_name, command_line, interval)
        returncode, _out, err = _run_argv(argv)
        return {"ok": returncode == 0, "argv": argv,
                "returncode": returncode, "stderr": _stderr_tail(err)}
    read_rc, current, read_err = _run_argv(["crontab", "-l"])
    if read_rc != 0:
        current = ""  # 无 crontab（或读失败）→ 空表起步，如实写回
    kept_text, _removed = remove_lines(current or "", task_name)
    new_text = _join_crontab(kept_text,
                             install_block(task_name, command_line,
                                           interval))
    argv = ["crontab", "-"]
    returncode, _out, err = _run_argv(argv, input_text=new_text)
    return {"ok": returncode == 0, "argv": argv,
            "returncode": returncode,
            "stderr": _stderr_tail(err) or _stderr_tail(read_err)}


def remove_carrier(task_name):
    """删除 OS 载体（执行面）。返回四键
    ``{ok, argv, returncode, stderr}``。

    **OS 删除真实有效，无三态劣化**——与原生 Cron 删除（宿主面
    DeleteWorkflowRun/CronCreate 生态的删除尝试可能以「已不存在 /
    未知 / 失败」三态含混回报，需要 runbook 再对账）不同，本面以
    OS 工具的真实退出码为唯一事实：
      - Windows：schtasks /Delete /F——任务不存在时 OS 返回非零，
        ok=false + stderr 尾部如实报告（绝不虚构删除成功；runbook
        对「not exist」尾文自行对账）；
      - POSIX：crontab 无 marker 块可摘（removed == 0）→ 零写
        no-op，ok=true（目标行已不在=删除终态已达成；argv/returncode
        报告读取步）；有块可摘 → ``crontab -`` 整表写回，报告写步。
    task_name 非空 str 才合法（否则 ValueError）。绝不 shell=True。"""
    if not isinstance(task_name, str) or task_name == "":
        raise ValueError(
            "carrier: task_name must be a non-empty string, got %r"
            % (task_name,))
    if is_windows():
        argv = remove_argv(task_name)
        returncode, _out, err = _run_argv(argv)
        return {"ok": returncode == 0, "argv": argv,
                "returncode": returncode, "stderr": _stderr_tail(err)}
    read_rc, current, read_err = _run_argv(["crontab", "-l"])
    new_text, removed = remove_lines(current or "", task_name)
    if removed == 0:
        # 无匹配块：删除终态已达成（零写 no-op），如实 ok=true
        return {"ok": True, "argv": ["crontab", "-l"],
                "returncode": read_rc, "stderr": _stderr_tail(read_err)}
    argv = ["crontab", "-"]
    returncode, _out, err = _run_argv(argv, input_text=_join_crontab(
        new_text, ""))
    return {"ok": returncode == 0, "argv": argv,
            "returncode": returncode,
            "stderr": _stderr_tail(err) or _stderr_tail(read_err)}


def _join_crontab(kept_text, block):
    """拼接 crontab 全文（执行面内部）：余文去尾换行后与 block 恰以
    单换行相连，整体以单换行结尾（crontab 文件以换行结尾惯例；
    block 为空串时只留余文收尾换行——remove 写回路径）。"""
    kept_text = kept_text.rstrip("\n")
    if kept_text and block:
        return kept_text + "\n" + block + "\n"
    if kept_text:
        return kept_text + "\n"
    return block + "\n" if block else ""


# ============================================================
# —— 探针面：纯本地零写零执行副作用 ——
# ============================================================

def _probe_zcode_cli():
    """zcode CLI 探测序（spec §3.4，纯本地零执行）：
      1. PATH ``zcode``（shutil.which）→ via="path"；
      2. 默认装位 ``DEFAULT_ZCODE_CJS`` 存在性 → 事实键 cjs_path；
      3. ``node`` + cjs 组合 → via="node_cjs"。
    available=True 当且仅当存在**可执行组合**（PATH zcode 或
    node+cjs）——cjs 裸存在而无 node 无 zcode 不构成可用（安装面
    resolve_wake_argv 同口径，两面永不矛盾）。"""
    zcode_path = shutil.which("zcode")
    node_path = shutil.which("node")
    cjs_path = DEFAULT_ZCODE_CJS if os.path.isfile(DEFAULT_ZCODE_CJS) \
        else None
    if zcode_path:
        via = "path"
    elif node_path and cjs_path:
        via = "node_cjs"
    else:
        via = "none"
    return {"available": via != "none", "via": via,
            "zcode_path": zcode_path, "node_path": node_path,
            "cjs_path": cjs_path}


def _probe_scheduler():
    """OS 调度器可执行性探针（shutil.which 即可，**绝不建探针任务**
    ——R3 纪律；Windows schtasks / POSIX crontab）。"""
    tool = "schtasks" if is_windows() else "crontab"
    path = shutil.which(tool)
    return {"available": path is not None, "tool": tool, "path": path}


def probe(repo_root=None):
    """L1 能力探针（纯本地：零写盘、零 subprocess 执行）。

    返回四键 ``{platform, zcode_cli, scheduler, recommendation}``：
      - platform：platform.system()（"Windows" / "Linux" / ...）；
      - zcode_cli：_probe_zcode_cli() 的 dict（available / via /
        zcode_path / node_path / cjs_path）；
      - scheduler：_probe_scheduler() 的 dict（available / tool /
        path）；
      - recommendation：zcode CLI 可用**且**调度器可用 →
        ``"os_timer"``（L1 可武装）；任一缺 → ``"sleep_only"``
        （L2：仅后台 sleep 精度层）。

    边界（spec §3.4 输出注释）：本探针只判 **L1（os_timer）能力**；
    **L0 native 的 CronCreate 宿主工具存在性归 runbook 判定**（主
    会话自查宿主工具面），probe 绝不触碰宿主工具面、绝不建探针
    任务。repo_root 仅为 CLI 子命令形态统一而收（探针是机器级事实，
    不读仓库盘面、绝不写盘）。R3：schtasks 受限环境在本探针表现
    为 scheduler.available=false → 自然降 sleep_only，无硬失败。"""
    zcode_cli = _probe_zcode_cli()
    scheduler = _probe_scheduler()
    recommendation = ("os_timer"
                      if zcode_cli["available"] and scheduler["available"]
                      else "sleep_only")
    return {"platform": platform.system(), "zcode_cli": zcode_cli,
            "scheduler": scheduler, "recommendation": recommendation}
