#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM Conductor Workflow 提交模型选型/预检（v2.4.1 H1 底盘；v2.6.0 WP-M1
用户授权显式选型重基线）。

职责：
    delegate/full 原生 Workflow 启动前的 worker 模型选型与提交契约构造
    （v2.6.0 重基线：worker 必须是用户显式授权的 provider 全限定精确 id
    ——形态合法 +（供给 configured 集合时）精确成员；绝不继承主会话
    文本模型，绝不接受裸别名。Flash 档判据降为**软推荐**候选分类器，
    不再是准入硬门——非 Flash 已配置模型仅因名字/后缀被拒是 no-go 红线
    ，规格 §5.2/§7.2）：
      - validate_worker_model(model_id, configured_model_ids=None)：
        单个 worker 模型 id 预检——非空 str、account: 前缀、
        account:<账户段>/<模型段> 形态（两段均非空、账户段不含 '/'、
        全 id 无空白；角色中性形状校验，v2.6.0 WP-M1 起不再要求
        Flash 后缀——未来陌生名字但已配置的 id 一律合法）；configured
        集合给出时要求精确成员，且集合形状非法即拒；成功原样返回该
        id（归一即恒等），失败抛 WorkflowSubmissionError（携人读原因）；
      - select_worker_model(configured_model_ids, explicit_model_id=None)：
        从主会话自宿主模型发现面提取的已配置 id 集合中选定 worker
        模型——先校验集合形状；显式给出 explicit_model_id 则校验之并
        要求精确成员（显式授权优先，任意已配置精确 id 合法，不限档位
        ）；否则走软推荐便捷路径：按 Flash 档判据收集候选，恰一候选
        自动选定（软推荐，非静默指派——多候选/零候选一律拒绝交回启动
        问答）、零候选拒绝（拒绝静默回退）、多候选歧义拒绝并列出全部
        候选 id——绝不静默取第一个；
      - build_submission_contract(model_id)：构造恰两键的机器可读
        提交契约（subagent_model + model_policy），供 CreateWorkflow
        前的最后预检与诊断输出；无秘密、无时间戳、无会话 id、
        无配额数据。

接口（公共面，自 v2.4.1 起五项不变）：
    常量 FLASH_TIER_SUFFIX + WorkflowSubmissionError + 上述三函数，
    仅此五项公共名。FLASH_TIER_SUFFIX 自 v2.6.0 WP-M1 起语义为软推荐
    候选分类器（自动选型便捷路径的档位判据），不再约束显式授权选择。
    调用方按模块路径导入本模块使用（不经 runtime.workflow 包 __init__
    重导出，包 __init__ 保持不动）。

依赖：
    零导入、零 I/O：纯 Python（3.7 兼容语法）内存计算——本文件不含
    任何 import 语句，零宿主 / 网络 / 文件系统调用；确定性——同输入
    重复调用返回值与错误消息逐字全等，无随机、无时间、无环境因素；
    绝不硬编码任何 account/provider 名（provider 身份一律来自宿主
    配置集合，本模块只认 account: 方案前缀这一形态约定；模型发现归
    宿主 ListModels，本模块绝不替宿主判断模型能力）。

来源：
    docs/roadmap/V2_4_1_CONTINUITY_HOTFIX_IMPLEMENTATION_SPEC.md
    §3.1（原模块语义）/ §3.4（H1 最低测试集）
    + docs/roadmap/POST_V2_5_2_RELIABILITY_AND_MODEL_SELECTION_
    IMPLEMENTATION_SPEC.md §5（WP-M1：v2.5.2 W5 的版本无关 Flash 档
    准入硬门降级为软推荐候选分类；model_policy 从 explicit-flash-
    required 改 user-authorized-explicit-selection；角色中性形状校验
    ——非 Flash 已配置 id 接受；零候选/多候选歧义仍拒绝交回启动问答
    ——问答面在 orchestration §6.0，本模块只负责机械候选收敛）。
"""


# Flash 档软推荐分类器（v2.5.2 W5 引入版本无关判据；v2.6.0 WP-M1 起
# 从准入硬门降级为软推荐候选分类）——worker 模型 id 的模型段（首个
# '/' 之后）恰以 "-Flash" 结尾且长于后缀本身即归为 Flash 档候选：
# GLM-5.3-Flash / GLM-6-Flash / 任一未来版本代际的同档模型均命中，
# 版本无关；非 Flash 档已配置模型合法可用（用户显式授权即选），只是
# 不进自动选型候选集。
FLASH_TIER_SUFFIX = "-Flash"

# account: 方案前缀（形态约定，非具体账户名；具体账户名绝不硬编码）
_ACCOUNT_SCHEME_PREFIX = "account:"
# 提交契约 model_policy 的唯一合法值（规格 §5.3：用户授权显式选型）
_MODEL_POLICY_VALUE = "user-authorized-explicit-selection"


class WorkflowSubmissionError(ValueError):
    """Workflow 提交模型选型/预检失败（id 形状 / 集合形状 / 成员或歧义）。

    继承 ValueError（值语义错误）：调用方按既有 CLI 约定转为校验拒绝
    退出码；对主会话流程这是「不得 CreateWorkflow」的硬闸——拒绝即抛、
    携人读原因，绝不猜测回退、绝不静默降级到别的模型。
    """


def _reject_reason(model_id):
    """纯形状预检：id 合法返回 None；否则返回人读拒绝原因（不查 configured 集合）。

    接受形态恰为：account:<账户段>/<模型段>，两段均非空、账户段不含
    '/'、全 id 无空白字符（角色中性形状校验——v2.6.0 WP-M1 起不再
    要求 Flash 档后缀：非 Flash 已配置模型（含主会话文本模型）合法
    可用，模型能力/档位判断归用户授权与宿主发现面，绝不按名字/后缀
    拒绝）。裸别名（无 account: 前缀）仍一律拒绝。
    """
    if not isinstance(model_id, str):
        return "model_id 必须是非空字符串，实际为 %s" % type(model_id).__name__
    if model_id == "":
        return "model_id 不能是空字符串"
    if not model_id.startswith(_ACCOUNT_SCHEME_PREFIX):
        return ("模型 id 必须以 %r 前缀开头（provider 限定，拒绝裸别名）：%r"
                % (_ACCOUNT_SCHEME_PREFIX, model_id))
    if any(char.isspace() for char in model_id):
        return "模型 id 不允许包含空白字符：%r" % (model_id,)
    account_seg, sep, model_seg = model_id[len(_ACCOUNT_SCHEME_PREFIX):].partition("/")
    if not sep or account_seg == "" or model_seg == "":
        return ("模型 id 必须是 account:<账户段>/<模型段> 形态（两段均非空，"
                "账户段不含 '/'）：%r" % (model_id,))
    return None


def _is_flash_tier_candidate(model_id):
    """软推荐档位分类：形状合法且模型段恰以 FLASH_TIER_SUFFIX 结尾并
    长于后缀本身（裸 "-Flash" 模型段无基础名，不算档位命中）。

    只用于 select_worker_model 的自动选型候选收集（软推荐便捷路径）；
    绝不参与 validate_worker_model 的准入判定——档位不是准入条件。
    """
    reason = _reject_reason(model_id)
    if reason is not None:
        return False
    model_seg = model_id[len(_ACCOUNT_SCHEME_PREFIX):].partition("/")[2]
    return (model_seg.endswith(FLASH_TIER_SUFFIX)
            and len(model_seg) > len(FLASH_TIER_SUFFIX))


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
    中也绝不接受。角色中性：档位（Flash 与否）不参与准入判定——
    已配置的任意精确 id 均合法。
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
    """从已配置模型集合中选定 worker 模型 id（预检通过者）。

    算法（规格 §5.2/§5.3 select_worker_model）：1) 校验集合形状；2)
    显式给出 explicit_model_id 时校验之（形状 + 精确成员）后原样返回
    ——用户显式授权优先，任意已配置精确 id 合法、不限档位；3) 否则
    走软推荐便捷路径：按 Flash 档判据依集合原序收集候选——恰一候选
    自动选定（软推荐，非静默指派）、零候选拒绝（拒绝静默回退）、多
    候选歧义拒绝并在消息中列出全部候选 id（选择交回启动问答）。
    绝不静默取第一个。
    """
    configured = _validated_configured(configured_model_ids)
    flash_candidates = tuple(
        mid for mid in configured if _is_flash_tier_candidate(mid))
    if explicit_model_id is not None:
        return validate_worker_model(explicit_model_id, configured)
    if len(flash_candidates) == 1:
        return flash_candidates[0]
    if not flash_candidates:
        raise WorkflowSubmissionError(
            "configured 集合（共 %d 个模型）中没有任何模型段以 %s 结尾的 "
            "provider 限定 Flash 档软推荐候选，拒绝静默回退——如需选用非 "
            "Flash 已配置模型，请经启动问答复核后显式给出 exact id"
            % (len(configured), FLASH_TIER_SUFFIX))
    listing = ", ".join('"%s"' % mid for mid in flash_candidates)
    raise WorkflowSubmissionError(
        "Flash 档软推荐候选有 %d 个，选择歧义——必须显式给出 exact id，"
        "绝不静默取第一个（选择交回启动问答由用户选定）；全部候选：%s"
        % (len(flash_candidates), listing))


def build_submission_contract(model_id):
    """构造恰两键的机器可读提交契约（CreateWorkflow 前的最终预检产物）。

    返回 {"subagent_model": <预检通过的 id>,
          "model_policy": "user-authorized-explicit-selection"}；
    键序固定、内容确定，无秘密、无时间戳、无会话 id、无配额数据。
    model_policy 语义（规格 §5.3）：worker 模型是用户在启动问答中
    显式授权的精确选择（Flash 档为软推荐而非硬性要求），不是策略层
    硬门结论。model_id 先过 validate_worker_model 预检，非法即抛
    WorkflowSubmissionError。
    """
    validated = validate_worker_model(model_id)
    return {
        "subagent_model": validated,
        "model_policy": _MODEL_POLICY_VALUE,
    }
