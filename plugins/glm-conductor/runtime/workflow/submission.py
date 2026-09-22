#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor v2.4.1 Workflow 提交模型选型/预检（continuity hotfix 工作包 H1）。

职责：
    delegate/full 原生 Workflow 启动前的 worker 模型选型与提交契约构造
    （INV-MODEL-01：worker 必须显式选定 provider 限定的 Flash 模型，
    绝不继承主会话文本模型，绝不接受裸别名）：
      - validate_worker_model(model_id, configured_model_ids=None)：
        单个 worker 模型 id 预检——非空 str、account: 前缀、
        account:<账户段>/<模型段> 形态（两段均非空、账户段不含 '/'、
        全 id 无空白）、恰以 FLASH_MODEL_SUFFIX 结尾、绝不由
        TEXT_MODEL_SUFFIX 结尾（主会话文本模型是 v2.4.0 缺陷 A 的
        误继承源，给专门拒绝原因）；configured 集合给出时要求精确
        成员，且集合形状非法即拒；成功原样返回该 id（归一即恒等），
        失败抛 WorkflowSubmissionError（携人读原因）；
      - select_worker_model(configured_model_ids, explicit_model_id=None)：
        从主会话自宿主模型发现面提取的已配置 id 集合中选定唯一
        worker 模型——先校验集合形状，再收集 Flash 候选；显式给出
        explicit_model_id 则校验之并要求精确成员（显式选择优先于
        自动选择）；否则恰一候选自动选定、零候选拒绝、多候选歧义
        拒绝并列出全部候选 id——绝不静默取第一个；
      - build_submission_contract(model_id)：构造恰两键的机器可读
        提交契约（subagent_model + model_policy），供 CreateWorkflow
        前的最后预检与诊断输出；无秘密、无时间戳、无会话 id、
        无配额数据。

接口（公共面冻结，规格 §3.1）：
    两常量 FLASH_MODEL_SUFFIX / TEXT_MODEL_SUFFIX +
    WorkflowSubmissionError + 上述三函数，仅此五项公共名。调用方按
    模块路径导入本模块使用（不经 runtime.workflow 包 __init__ 重导出，
    包 __init__ 保持不动）。

依赖：
    零导入、零 I/O：纯 Python（3.7 兼容语法）内存计算——本文件不含
    任何 import 语句，零宿主 / 网络 / 文件系统调用；确定性——同输入
    重复调用返回值与错误消息逐字全等，无随机、无时间、无环境因素；
    绝不硬编码任何 account/provider 名（provider 身份一律来自宿主
    配置集合，本模块只认 account: 方案前缀这一形态约定）。

来源：
    docs/roadmap/V2_4_1_CONTINUITY_HOTFIX_IMPLEMENTATION_SPEC.md
    §3.1（新模块语义）/ §3.4（H1 最低测试集）
    + docs/roadmap/V2_4_1_CONTINUITY_HOTFIX_PLAN.md I1 / H1。
"""


# Provider 限定 Flash 后缀（INV-MODEL-01：worker 模型 id 恰以此结尾才合法）
FLASH_MODEL_SUFFIX = "/GLM-5.3-Flash"
# 主会话文本模型后缀（以此结尾的 id 绝不可用作 Workflow worker 模型）
TEXT_MODEL_SUFFIX = "/GLM-5.3"

# account: 方案前缀（形态约定，非具体账户名；具体账户名绝不硬编码）
_ACCOUNT_SCHEME_PREFIX = "account:"
# 提交契约 model_policy 的唯一合法值（规格 §3.1 逐字锚定）
_MODEL_POLICY_VALUE = "explicit-flash-required"


class WorkflowSubmissionError(ValueError):
    """Workflow 提交模型选型/预检失败（id 形状 / 集合形状 / 成员或歧义）。

    继承 ValueError（值语义错误）：调用方按既有 CLI 约定转为校验拒绝
    退出码；对主会话流程这是「不得 CreateWorkflow」的硬闸——拒绝即抛、
    携人读原因，绝不猜测回退、绝不静默降级到别的模型。
    """


def _reject_reason(model_id):
    """纯形状预检：id 合法返回 None；否则返回人读拒绝原因（不查 configured 集合）。

    接受形态恰为：account:<账户段>/<模型段>，两段均非空、账户段不含
    '/'、全 id 无空白字符，且恰以 FLASH_MODEL_SUFFIX 结尾。文本模型
    后缀与 Flash 后缀互不为后缀（拒绝集合不因检查次序改变），文本模型
    先判以给出专门的缺陷 A 拒绝原因。
    """
    if not isinstance(model_id, str):
        return "model_id 必须是非空字符串，实际为 %s" % type(model_id).__name__
    if model_id == "":
        return "model_id 不能为空字符串"
    if not model_id.startswith(_ACCOUNT_SCHEME_PREFIX):
        return ("模型 id 必须以 %r 前缀开头（provider 限定，拒绝裸别名）：%r"
                % (_ACCOUNT_SCHEME_PREFIX, model_id))
    if any(char.isspace() for char in model_id):
        return "模型 id 不允许包含空白字符：%r" % (model_id,)
    account_seg, sep, model_seg = model_id[len(_ACCOUNT_SCHEME_PREFIX):].partition("/")
    if not sep or account_seg == "" or model_seg == "":
        return ("模型 id 必须是 account:<账户段>/<模型段> 形态（两段均非空，"
                "账户段不含 '/'）：%r" % (model_id,))
    if model_id.endswith(TEXT_MODEL_SUFFIX):
        return ("模型 id 以文本模型后缀 %s 结尾——主会话文本模型绝不可用作 "
                "Workflow worker 模型：%r" % (TEXT_MODEL_SUFFIX, model_id))
    if not model_id.endswith(FLASH_MODEL_SUFFIX):
        return ("模型 id 未以 provider 限定 Flash 后缀 %s 结尾：%r"
                % (FLASH_MODEL_SUFFIX, model_id))
    return None


def _validated_configured(configured_model_ids):
    """configured 集合形状校验：必须是 list/tuple 且各项均为非空字符串。

    形状非法即抛 WorkflowSubmissionError（字符串、dict、set、None 元素、
    空串元素等一律拒绝）；合法则返回同序元组（保持确定性遍历序）。
    """
    if not isinstance(configured_model_ids, (list, tuple)):
        raise WorkflowSubmissionError(
            "configured_model_ids 必须是 list/tuple 的非空字符串集合，实际为 %s"
            % type(configured_model_ids).__name__)
    for index, item in enumerate(configured_model_ids):
        if not isinstance(item, str) or item == "":
            raise WorkflowSubmissionError(
                "configured_model_ids[%d] 必须是非空字符串，实际为 %r"
                % (index, item))
    return tuple(configured_model_ids)


def validate_worker_model(model_id, configured_model_ids=None):
    """校验单个 worker 模型 id；成功原样返回该 id，失败抛 WorkflowSubmissionError。

    configured_model_ids=None（缺省）表示未提供宿主配置集合，只做 id
    自身形状预检；给出时必须是 list/tuple 的非空字符串，且 model_id
    须为精确成员（精确字符串相等——不做大小写 / 空白归一，集合形状
    非法即拒）。裸别名（无 account: 前缀）即使出现在 configured 集合
    中也绝不接受。
    """
    reason = _reject_reason(model_id)
    if reason is not None:
        raise WorkflowSubmissionError(reason)
    if configured_model_ids is not None:
        configured = _validated_configured(configured_model_ids)
        if model_id not in configured:
            raise WorkflowSubmissionError(
                "模型 id 不在 configured 集合中（须精确成员，共 %d 个已配置"
                "模型）：%r" % (len(configured), model_id))
    return model_id


def select_worker_model(configured_model_ids, explicit_model_id=None):
    """从已配置模型集合中选定唯一 worker 模型 id（预检通过者）。

    算法（规格 §3.1 select_worker_model）：1) 校验集合形状；2) 依集合
    原序收集满足 provider 限定 Flash 形态的候选；3) 显式给出
    explicit_model_id 时校验之并要求精确成员后原样返回（显式选择优先，
    自动歧义不适用）；4) 否则恰一候选自动选定——零候选拒绝（拒绝静默
    回退）、多候选歧义拒绝并在消息中列出全部候选 id。绝不静默取第一个。
    """
    configured = _validated_configured(configured_model_ids)
    flash_candidates = tuple(
        mid for mid in configured
        if _reject_reason(mid) is None and mid.endswith(FLASH_MODEL_SUFFIX))
    if explicit_model_id is not None:
        return validate_worker_model(explicit_model_id, configured)
    if len(flash_candidates) == 1:
        return flash_candidates[0]
    if not flash_candidates:
        raise WorkflowSubmissionError(
            "configured 集合（共 %d 个模型）中没有任何以 %s 结尾的 provider "
            "限定 Flash 候选，拒绝静默回退"
            % (len(configured), FLASH_MODEL_SUFFIX))
    listing = ", ".join('"%s"' % mid for mid in flash_candidates)
    raise WorkflowSubmissionError(
        "Flash 候选有 %d 个，选择歧义——必须显式给出 exact id，绝不静默取"
        "第一个；全部候选：%s" % (len(flash_candidates), listing))


def build_submission_contract(model_id):
    """构造恰两键的机器可读提交契约（CreateWorkflow 前的最终预检产物）。

    返回 {"subagent_model": <预检通过的 id>,
          "model_policy": "explicit-flash-required"}；键序固定、内容确定，
    无秘密、无时间戳、无会话 id、无配额数据。model_id 先过
    validate_worker_model 预检，非法即抛 WorkflowSubmissionError。
    """
    validated = validate_worker_model(model_id)
    return {
        "subagent_model": validated,
        "model_policy": _MODEL_POLICY_VALUE,
    }
