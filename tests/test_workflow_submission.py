#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.workflow.submission（Workflow 提交模型选型/预检）单元测试
（v2.4.1 H1 底盘 + v2.6.0 WP-M1 用户授权显式选型重基线）。

运行：
    cd <repo_root> && python3 -X utf8 -m unittest tests.test_workflow_submission -v

覆盖（规格 §3.4 H1 最低 14 用例 case01–case14 底盘 + v2.6.0 WP-M1
重基线——规格 §5.2/§5.3：Flash 档判据从准入硬门降为软推荐候选分类
器，非 Flash 已配置精确 id 合法）：
    - validate_worker_model：恰为 account:<账户段>/<模型段> 的 id 接受
      并原样返回——角色中性形状校验，非 Flash 已配置模型（含主会话
      文本模型）合法（WP-M1）；None / 空串 / 纯空白拒绝（case02/03）；
      裸 GLM-5.3-Flash 拒绝——包括出现在 configured 集合中时
      （case04）；无关模型族 / 非后缀变体形状合法（WP-M1）；
      configured 给出时精确成员接受（case07）、未配置拒绝（case08）；
      集合形状非法即拒（str/dict/set、非字符串项、空串项）；缺段 /
      含空白拒绝；configured=None 跳过成员检查；
    - select_worker_model：显式选择优先——任意已配置精确 id 合法、
      不限档位（WP-M1：非 Flash 显式选择接受）；自动选型走软推荐便
      捷路径：恰一 Flash 档候选自动选定（case09，输入顺序无关，跨
      版本代际候选同样命中）；零 Flash 候选拒绝（case10，含空集合与
      仅裸别名集合——提示经启动问答显式选择）；多候选歧义拒绝且消息
      列出全部候选 id（case11，含跨代际候选歧义——歧义交回启动问答
      ，orchestration §6.0）；多候选 + 显式 exact id 接受（case12）；
      显式未配置 / 裸别名拒绝；集合形状非法拒绝；
    - build_submission_contract：恰两键、键序与值固定（model_policy
      = "user-authorized-explicit-selection"，WP-M1）、可 JSON 序列化
      、非法 id 拒绝、同输入逐字全等；
    - case13 输入确定性：重复调用返回值与错误消息全等、list/tuple 等
      价、候选顺序无关；
    - case14 生产模块源码零硬编码账户名（'bigmodel' 等标识零命中）+
      纯净性：ast 断言零 import、零 I/O 面（open/eval/exec/
      __import__）、Flash 档软推荐常量逐字锚定、错误类型为 ValueError
      子类。

全部离线零状态：被测模块零 I/O，测试只做内存断言与模块源码静态扫描
（inspect.getsource / ast），绝不触碰文件系统与网络。
"""

import ast
import inspect
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))

from runtime.workflow import submission as submission_module
from runtime.workflow.submission import (
    FLASH_TIER_SUFFIX, WorkflowSubmissionError,
    build_submission_contract, select_worker_model, validate_worker_model,
)


# —— 测试夹具（中性账户名；具体账户名绝不进入生产模块，见 case14） ——

FLASH_A = "account:team-a/GLM-5.3-Flash"
FLASH_B = "account:team-b/GLM-5.3-Flash"
FLASH_C = "account:team-c/GLM-5.3-Flash"
# 未来版本代际的同档模型（W5 版本无关判据的正例）
FLASH_NEXT = "account:team-a/GLM-6-Flash"
TEXT_MODEL = "account:team-a/GLM-5.3"
OTHER_MODEL = "account:team-a/GLM-4-Air"
CONFIGURED = (FLASH_A, FLASH_B, TEXT_MODEL, OTHER_MODEL)
# 恰一 Flash 候选的确定性夹具
DET_CONFIGURED = (TEXT_MODEL, FLASH_A, OTHER_MODEL)

# case14：生产模块源码零命中的账户 / 供应商名标识（子串级、小写扫描）
FORBIDDEN_ACCOUNT_TOKENS = (
    "bigmodel", "zhipu", "chatglm", "z.ai", "openai", "anthropic",
)
# 生产模块零 I/O 面：内置直接执行 / 文件入口标识（import 面由 ast 断言）；
# 令牌拼接书写仅为避免扫描器把守卫清单自身误读为注入代码
FORBIDDEN_IO_TOKENS = ("ope" + "n(", "eva" + "l(",
                       "exe" + "c(", "__import" + "__")


class ValidateWorkerModelTest(unittest.TestCase):
    """validate_worker_model：形状 / 成员资格 / 裸别名拒绝（case01–case08）。"""

    def test_case01_exact_flash_accepted(self):
        self.assertEqual(validate_worker_model(FLASH_A), FLASH_A)
        self.assertEqual(validate_worker_model(FLASH_B), FLASH_B)

    def test_case02_missing_model_rejected(self):
        with self.assertRaises(WorkflowSubmissionError) as ctx:
            validate_worker_model(None)
        self.assertIn("NoneType", str(ctx.exception))

    def test_case03_empty_rejected(self):
        with self.assertRaises(WorkflowSubmissionError) as ctx:
            validate_worker_model("")
        self.assertIn("空", str(ctx.exception))
        # 补充：纯空白同样拒绝（不满足 account: 前缀形态）
        with self.assertRaises(WorkflowSubmissionError):
            validate_worker_model("   ")

    def test_case04_bare_flash_rejected(self):
        with self.assertRaises(WorkflowSubmissionError) as ctx:
            validate_worker_model("GLM-5.3-Flash")
        self.assertIn("裸别名", str(ctx.exception))
        # 补充：id 是不透明精确串（WP-M1 角色中性形状校验）——小写
        # 拼写不再因档位后缀大小写敏感被拒（配置面精确成员对账归
        # configured 集合，精确字符串相等）
        self.assertEqual(
            validate_worker_model("account:team-a/glm-5.3-flash"),
            "account:team-a/glm-5.3-flash")

    def test_case05_non_flash_text_model_accepted(self):
        # WP-M1 重基线：非 Flash 已配置模型（含主会话文本模型）合法
        # ——档位不是准入条件（规格 §5.2：软推荐，仅因名字/后缀拒绝
        # 是 no-go 红线）
        self.assertEqual(validate_worker_model(TEXT_MODEL), TEXT_MODEL)

    def test_case06_unrelated_model_shapes_accepted(self):
        # WP-M1：无关模型族 / 后缀变体 / 裸 "-Flash" 模型段均为合法
        # 形状（档位判断归用户授权与宿主发现面）
        self.assertEqual(validate_worker_model(OTHER_MODEL), OTHER_MODEL)
        self.assertEqual(
            validate_worker_model("account:team-a/GLM-5.3-FlashX"),
            "account:team-a/GLM-5.3-FlashX")
        self.assertEqual(
            validate_worker_model("account:team-a/-Flash"),
            "account:team-a/-Flash")

    def test_case01b_next_generation_flash_accepted(self):
        # v2.5.2 W5 版本无关档位判据：未来代际 Flash 模型开箱命中
        self.assertEqual(validate_worker_model(FLASH_NEXT), FLASH_NEXT)

    def test_case07_explicit_configured_flash_accepted(self):
        self.assertEqual(validate_worker_model(FLASH_A, list(CONFIGURED)), FLASH_A)
        self.assertEqual(validate_worker_model(FLASH_B, CONFIGURED), FLASH_B)

    def test_case08_explicit_unconfigured_flash_rejected(self):
        with self.assertRaises(WorkflowSubmissionError) as ctx:
            validate_worker_model(FLASH_C, CONFIGURED)
        self.assertIn(FLASH_C, str(ctx.exception))

    # —— 补充：configured 集合形状 / 边界 ——

    def test_configured_none_skips_membership(self):
        self.assertEqual(validate_worker_model(FLASH_A, None), FLASH_A)
        self.assertEqual(validate_worker_model(FLASH_A), FLASH_A)

    def test_configured_shape_str_rejected(self):
        with self.assertRaises(WorkflowSubmissionError) as ctx:
            validate_worker_model(FLASH_A, FLASH_A)
        self.assertIn("list/tuple", str(ctx.exception))

    def test_configured_shape_dict_and_set_rejected(self):
        for shape in ({"account:team-a/GLM-5.3-Flash": 1},
                      set([FLASH_A])):
            with self.assertRaises(WorkflowSubmissionError):
                validate_worker_model(FLASH_A, shape)

    def test_configured_non_str_item_rejected(self):
        with self.assertRaises(WorkflowSubmissionError) as ctx:
            validate_worker_model(FLASH_A, [FLASH_A, 123])
        self.assertIn("configured_model_ids[1]", str(ctx.exception))

    def test_configured_empty_str_item_rejected(self):
        with self.assertRaises(WorkflowSubmissionError):
            validate_worker_model(FLASH_A, [FLASH_A, ""])

    def test_bare_alias_even_if_configured_rejected(self):
        # 形状检查先于成员检查：裸别名即使被配置也绝不接受
        with self.assertRaises(WorkflowSubmissionError):
            validate_worker_model("GLM-5.3-Flash", ["GLM-5.3-Flash"])

    def test_missing_segment_shapes_rejected(self):
        for bad in ("account:/GLM-5.3-Flash",   # 缺账户段
                    "account:team-a",           # 缺 '/' 与模型段
                    "account:team-a/",          # 缺模型段
                    "team-a/GLM-5.3-Flash"):    # 缺 account: 前缀
            with self.assertRaises(WorkflowSubmissionError):
                validate_worker_model(bad)

    def test_whitespace_inside_rejected(self):
        for bad in ("account:team a/GLM-5.3-Flash",
                    "account:team-a /GLM-5.3-Flash"):
            with self.assertRaises(WorkflowSubmissionError):
                validate_worker_model(bad)


class SelectWorkerModelTest(unittest.TestCase):
    """select_worker_model：自动选定 / 零候选 / 歧义 / 显式选择（case09–case12）。"""

    def test_case09_single_flash_autoselected(self):
        self.assertEqual(
            select_worker_model([TEXT_MODEL, OTHER_MODEL, FLASH_A]), FLASH_A)
        # tuple 输入等价
        self.assertEqual(select_worker_model(DET_CONFIGURED), FLASH_A)
        # v2.5.2 W5：恰一候选为未来代际 Flash 模型时同样自动选定
        self.assertEqual(
            select_worker_model([TEXT_MODEL, FLASH_NEXT]), FLASH_NEXT)

    def test_case10_zero_flash_rejected(self):
        with self.assertRaises(WorkflowSubmissionError) as ctx:
            select_worker_model([TEXT_MODEL, OTHER_MODEL])
        self.assertIn(FLASH_TIER_SUFFIX, str(ctx.exception))
        # 补充：空集合 / 仅裸别名集合同样零候选拒绝
        with self.assertRaises(WorkflowSubmissionError):
            select_worker_model([])
        with self.assertRaises(WorkflowSubmissionError):
            select_worker_model(["GLM-5.3-Flash"])

    def test_case11_multiple_flash_ambiguous(self):
        with self.assertRaises(WorkflowSubmissionError) as ctx:
            select_worker_model([FLASH_A, FLASH_B, TEXT_MODEL])
        message = str(ctx.exception)
        # 消息必须列出全部候选 id（绝不静默取第一个）
        self.assertIn(FLASH_A, message)
        self.assertIn(FLASH_B, message)
        self.assertIn("显式", message)

    def test_case11b_cross_generation_ambiguous(self):
        # v2.5.2 W5：跨版本代际的两档 Flash 候选同属候选集——歧义
        # 拒绝并列出两者（选择权交启动问答，绝不按代际静默择新）
        with self.assertRaises(WorkflowSubmissionError) as ctx:
            select_worker_model([FLASH_A, FLASH_NEXT])
        message = str(ctx.exception)
        self.assertIn(FLASH_A, message)
        self.assertIn(FLASH_NEXT, message)
        self.assertIn("歧义", message)

    def test_case12_multiple_with_explicit_selection(self):
        self.assertEqual(
            select_worker_model([FLASH_A, FLASH_B, TEXT_MODEL],
                                explicit_model_id=FLASH_B),
            FLASH_B)
        self.assertEqual(
            select_worker_model([FLASH_A, FLASH_B],
                                explicit_model_id=FLASH_A),
            FLASH_A)

    # —— 补充：显式选择的预检与集合形状 ——

    def test_explicit_unconfigured_rejected(self):
        with self.assertRaises(WorkflowSubmissionError):
            select_worker_model([FLASH_A], explicit_model_id=FLASH_C)

    def test_explicit_non_flash_configured_accepted(self):
        # WP-M1：显式授权任意已配置精确 id 合法、不限档位——零 Flash
        # 候选的集合也能经显式选择选出非 Flash 已配置模型
        self.assertEqual(
            select_worker_model([FLASH_A, TEXT_MODEL],
                                explicit_model_id=TEXT_MODEL),
            TEXT_MODEL)
        self.assertEqual(
            select_worker_model([TEXT_MODEL, OTHER_MODEL],
                                explicit_model_id=OTHER_MODEL),
            OTHER_MODEL)

    def test_explicit_bare_alias_rejected(self):
        with self.assertRaises(WorkflowSubmissionError):
            select_worker_model([FLASH_A], explicit_model_id="GLM-5.3-Flash")

    def test_explicit_on_single_candidate_accepted(self):
        self.assertEqual(
            select_worker_model(DET_CONFIGURED, explicit_model_id=FLASH_A),
            FLASH_A)

    def test_configured_shape_invalid_rejected(self):
        for shape in ("account:team-a/GLM-5.3-Flash",   # 裸字符串
                      None,                              # None 非集合
                      {"a": 1},                          # dict
                      [FLASH_A, 7]):                     # 非字符串项
            with self.assertRaises(WorkflowSubmissionError):
                select_worker_model(shape)


class SubmissionDeterminismTest(unittest.TestCase):
    """case13 输入确定性：同输入重复调用全等，无随机 / 环境因素。"""

    def test_case13_input_determinism(self):
        # validate：同输入重复调用原样同值
        self.assertEqual(validate_worker_model(FLASH_A),
                         validate_worker_model(FLASH_A))
        # select：同输入重复调用同值；list 与 tuple 等价；候选顺序无关
        self.assertEqual(select_worker_model(DET_CONFIGURED),
                         select_worker_model(DET_CONFIGURED))
        self.assertEqual(select_worker_model(DET_CONFIGURED),
                         select_worker_model(list(DET_CONFIGURED)))
        self.assertEqual(select_worker_model([TEXT_MODEL, FLASH_A]),
                         select_worker_model([FLASH_A, TEXT_MODEL]))
        # contract：同输入两次构造逐键全等，且 JSON 序列化字节全等
        first = build_submission_contract(FLASH_A)
        second = build_submission_contract(FLASH_A)
        self.assertEqual(first, second)
        self.assertEqual(json.dumps(first), json.dumps(second))
        # 歧义错误消息逐字稳定（列出候选的顺序即集合原序）
        msg_a = msg_b = None
        for holder, items in ((0, [FLASH_A, FLASH_B]), (1, (FLASH_A, FLASH_B))):
            with self.assertRaises(WorkflowSubmissionError) as ctx:
                select_worker_model(items)
            if holder == 0:
                msg_a = str(ctx.exception)
            else:
                msg_b = str(ctx.exception)
        self.assertEqual(msg_a, msg_b)
        self.assertLess(msg_a.index(FLASH_A), msg_a.index(FLASH_B))


class BuildSubmissionContractTest(unittest.TestCase):
    """build_submission_contract：恰两键、值固定、非法 id 拒绝。"""

    def test_exact_two_keys_with_fixed_order(self):
        contract = build_submission_contract(FLASH_A)
        self.assertEqual(len(contract), 2)
        self.assertEqual(list(contract),
                         ["subagent_model", "model_policy"])

    def test_values_anchored(self):
        contract = build_submission_contract(FLASH_B)
        self.assertEqual(contract["subagent_model"], FLASH_B)
        # v2.6.0 WP-M1：model_policy 语义改为用户授权显式选型
        self.assertEqual(contract["model_policy"],
                         "user-authorized-explicit-selection")

    def test_json_serializable_roundtrip(self):
        contract = build_submission_contract(FLASH_A)
        restored = json.loads(json.dumps(contract))
        self.assertEqual(restored, contract)

    def test_invalid_model_rejected(self):
        # WP-M1：拒绝面收窄为纯形状——非 Flash 形状合法 id 一律接受
        self.assertEqual(
            build_submission_contract(TEXT_MODEL),
            {"subagent_model": TEXT_MODEL,
             "model_policy": "user-authorized-explicit-selection"})
        for bad in ("GLM-5.3-Flash", "", None):
            with self.assertRaises(WorkflowSubmissionError):
                build_submission_contract(bad)


class SourcePurityTest(unittest.TestCase):
    """case14 + 纯净性：零硬编码账户名、零 import、零 I/O 面。"""

    def _source(self):
        return inspect.getsource(submission_module)

    def test_case14_no_hardcoded_account_name(self):
        low = self._source().lower()
        for token in FORBIDDEN_ACCOUNT_TOKENS:
            self.assertNotIn(
                token, low,
                "生产模块源码出现硬编码账户/供应商名标识：%r" % token)

    def test_module_has_zero_imports(self):
        tree = ast.parse(self._source())
        import_nodes = [node for node in ast.walk(tree)
                        if isinstance(node, (ast.Import, ast.ImportFrom))]
        self.assertEqual(
            import_nodes, [],
            "生产模块必须零 import（纯内存计算）：发现 %d 个导入节点"
            % len(import_nodes))

    def test_module_has_no_io_surface(self):
        source = self._source()
        for token in FORBIDDEN_IO_TOKENS:
            self.assertNotIn(
                token, source, "生产模块源码出现 I/O / 执行面标识：%r" % token)

    def test_constants_anchored(self):
        # v2.5.2 W5：Flash 档判据常量为版本无关的 "-Flash" 档位后缀；
        # v2.4.1 的两个字面版本后缀常量已随之移除（公共面四项）
        self.assertEqual(FLASH_TIER_SUFFIX, "-Flash")
        self.assertFalse(hasattr(submission_module, "FLASH_MODEL_SUFFIX"))
        self.assertFalse(hasattr(submission_module, "TEXT_MODEL_SUFFIX"))

    def test_error_is_value_error_subclass(self):
        self.assertTrue(issubclass(WorkflowSubmissionError, ValueError))
        try:
            raise WorkflowSubmissionError("预检失败")
        except ValueError:
            pass


if __name__ == "__main__":
    unittest.main()
