#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.quota.credentials 单元测试（v2 工作块 B5.4，§36 凭证解析）。

覆盖：
  - env 优先：GLM_CONDUCTOR_QUOTA_API_KEY 非空 str 命中 → ("env", 值)；
    空串 / 非 str 按未设置处理，走回退路径；
  - ZCode provider 配置回退（tempdir JSON 三种命中形态）：
    标准结构命中 → ("zcode-provider", 值)；provider 键缺失 /
    apiKey 空串 → (None, None)；
  - 文件级静默降级：文件不存在 / 非 JSON / 顶层非 dict /
    config_path 是目录 → (None, None)，绝不抛错；
  - 优先级：env 与 config 同时可用 → env 胜；
  - 缺省 config_path：expanduser(~/.zcode/v2/config.json)（HOME/
    USERPROFILE 注入 tempdir 验证，不触真实用户目录）；
  - 凭证纪律锚定（§37）：全程零 print（monkeypatch builtins.print
    断言未调用）、配置文件字节不变、目录无新增文件。

【零网络 / 零真实凭证纪律】本文件全部测试只读写 tempdir，不发任何
网络请求，不读取真实 ~/.zcode 配置（environ 一律注入隔离）。

运行：
    cd <repo_root> && python3 -m unittest tests.test_quota_credentials -v
"""

import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))

from runtime.quota.credentials import (CREDENTIAL_SOURCES, DEFAULT_CONFIG_PATH,
                                       ENV_VAR, PROVIDER_KEY, describe_modes,
                                       resolve_credential)

# 假 key（含 SECRET 标记：纪律断言复用同一标记）
FAKE_KEY = "sk-test-SECRET"


def standard_payload(key=FAKE_KEY):
    """§36 实测的 ZCode provider 配置标准结构。"""
    return {"provider": {PROVIDER_KEY: {"options": {"apiKey": key}}}}


class TempDirFixture(unittest.TestCase):
    """tempdir 基座：配置文件写入 + 目录快照。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def write_config(self, payload_or_text):
        """在 tempdir 写入 .zcode/v2/config.json，返回其路径字符串。"""
        config_path = self.root / ".zcode" / "v2" / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload_or_text, str):
            config_path.write_text(payload_or_text, encoding="utf-8")
        else:
            config_path.write_text(json.dumps(payload_or_text),
                                   encoding="utf-8")
        return str(config_path)

    def snapshot_files(self):
        """tempdir 内全部文件的相对路径集合（新增文件检测用）。"""
        return {str(path.relative_to(self.root))
                for path in self.root.rglob("*") if path.is_file()}


class ConstantsTest(unittest.TestCase):
    """词汇表常量锚定。"""

    def test_constants(self):
        self.assertEqual(ENV_VAR, "GLM_CONDUCTOR_QUOTA_API_KEY")
        self.assertEqual(PROVIDER_KEY, "builtin:bigmodel-coding-plan")
        self.assertEqual(CREDENTIAL_SOURCES, ("env", "zcode-provider"))
        self.assertIsNone(DEFAULT_CONFIG_PATH)

    def test_describe_modes_vocabulary(self):
        modes = describe_modes()
        self.assertTrue(set(modes) >= {"native", "provider-api",
                                       "unavailable"})
        for text in modes.values():
            self.assertIsInstance(text, str)
            self.assertNotEqual(text, "")


class EnvPriorityTest(TempDirFixture):
    """env 优先路径（environ 注入隔离真实环境）。"""

    def test_env_hit(self):
        # config_path 指向不存在的文件：证明 env 命中即返回，不读配置
        missing = str(self.root / "missing" / "config.json")
        self.assertEqual(
            resolve_credential(missing, environ={ENV_VAR: FAKE_KEY}),
            ("env", FAKE_KEY))

    def test_env_empty_string_is_unset(self):
        missing = str(self.root / "missing" / "config.json")
        self.assertEqual(
            resolve_credential(missing, environ={ENV_VAR: ""}),
            (None, None))

    def test_env_missing_falls_back(self):
        missing = str(self.root / "missing" / "config.json")
        self.assertEqual(resolve_credential(missing, environ={}),
                         (None, None))

    def test_env_non_string_is_unset(self):
        missing = str(self.root / "missing" / "config.json")
        self.assertEqual(
            resolve_credential(missing, environ={ENV_VAR: 12345}),
            (None, None))

    def test_env_wins_over_config(self):
        config_path = self.write_config(standard_payload())
        self.assertEqual(
            resolve_credential(config_path, environ={ENV_VAR: "env-key"}),
            ("env", "env-key"))


class ConfigFallbackTest(TempDirFixture):
    """ZCode provider 配置回退路径（tempdir JSON）。"""

    def test_standard_structure_hit(self):
        config_path = self.write_config(standard_payload())
        self.assertEqual(resolve_credential(config_path, environ={}),
                         ("zcode-provider", FAKE_KEY))

    def test_provider_key_missing(self):
        config_path = self.write_config(
            {"provider": {"other:provider": {"options": {"apiKey":
                                                         FAKE_KEY}}}})
        self.assertEqual(resolve_credential(config_path, environ={}),
                         (None, None))

    def test_provider_section_missing(self):
        config_path = self.write_config({})
        self.assertEqual(resolve_credential(config_path, environ={}),
                         (None, None))

    def test_options_missing(self):
        config_path = self.write_config({"provider": {PROVIDER_KEY: {}}})
        self.assertEqual(resolve_credential(config_path, environ={}),
                         (None, None))

    def test_api_key_empty_string(self):
        config_path = self.write_config(standard_payload(key=""))
        self.assertEqual(resolve_credential(config_path, environ={}),
                         (None, None))

    def test_api_key_non_string(self):
        config_path = self.write_config(standard_payload(key=123))
        self.assertEqual(resolve_credential(config_path, environ={}),
                         (None, None))


class SilentDegradationTest(TempDirFixture):
    """文件级失败一律静默 (None, None)：不抛错、不打印。"""

    def test_file_missing(self):
        missing = str(self.root / "missing" / "config.json")
        self.assertEqual(resolve_credential(missing, environ={}),
                         (None, None))

    def test_invalid_json(self):
        config_path = self.write_config("{not-json")
        self.assertEqual(resolve_credential(config_path, environ={}),
                         (None, None))

    def test_top_level_list(self):
        config_path = self.write_config("[]")
        self.assertEqual(resolve_credential(config_path, environ={}),
                         (None, None))

    def test_top_level_string(self):
        config_path = self.write_config('"just a string"')
        self.assertEqual(resolve_credential(config_path, environ={}),
                         (None, None))

    def test_config_path_is_directory(self):
        directory = str(self.root / "as-dir")
        os.makedirs(directory, exist_ok=True)
        self.assertEqual(resolve_credential(directory, environ={}),
                         (None, None))

    def test_result_is_tuple(self):
        missing = str(self.root / "missing" / "config.json")
        result = resolve_credential(missing, environ={})
        self.assertIsInstance(result, tuple)
        self.assertEqual(result, (None, None))


class DefaultPathTest(TempDirFixture):
    """缺省 config_path：expanduser(~/.zcode/v2/config.json)。

    HOME / USERPROFILE 注入 tempdir（ntpath.expanduser 下 HOME 优先），
    不触真实用户目录；environ 一律注入 {} 隔离真实环境变量。
    """

    def _patch_home(self):
        patcher = unittest.mock.patch.dict(
            os.environ, {"HOME": str(self.root),
                         "USERPROFILE": str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_default_path_miss_then_hit(self):
        self._patch_home()
        # 未写配置 → (None, None)
        self.assertEqual(resolve_credential(environ={}), (None, None))
        # 写入标准结构 → 命中
        (self.root / ".zcode" / "v2").mkdir(parents=True, exist_ok=True)
        (self.root / ".zcode" / "v2" / "config.json").write_text(
            json.dumps(standard_payload()), encoding="utf-8")
        self.assertEqual(resolve_credential(environ={}),
                         ("zcode-provider", FAKE_KEY))

    def test_environ_none_uses_os_environ(self):
        # environ 缺省走 os.environ：清空后调用，验证接线（不命中）
        config_path = self.write_config(standard_payload())
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_credential(config_path),
                             ("zcode-provider", FAKE_KEY))
            with unittest.mock.patch.dict(
                    os.environ, {ENV_VAR: "from-os-environ"}):
                self.assertEqual(resolve_credential(config_path),
                                 ("env", "from-os-environ"))


class DisciplineTest(TempDirFixture):
    """凭证纪律锚定（§37）：零 print、配置文件字节不变、零新增文件。"""

    def _patch_home_safely(self):
        patcher = unittest.mock.patch.dict(
            os.environ, {"HOME": str(self.root),
                         "USERPROFILE": str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _all_paths(self, config_path, default_path_result):
        """把命中与各失败路径都跑一遍（在 print mock 内调用）。"""
        missing = str(self.root / "missing" / "config.json")
        resolve_credential(missing, environ={ENV_VAR: FAKE_KEY})
        resolve_credential(missing, environ={})
        resolve_credential(config_path, environ={})
        resolve_credential("{bad-json-path", environ={})
        # 缺省路径（HOME 注入 tempdir，由调用方决定命中形态）
        self.assertEqual(resolve_credential(None, environ={}),
                         default_path_result)

    def test_never_prints(self):
        self._patch_home_safely()
        config_path = self.write_config(standard_payload())
        with unittest.mock.patch("builtins.print") as mock_print:
            self._all_paths(config_path, ("zcode-provider", FAKE_KEY))
        mock_print.assert_not_called()

    def test_never_prints_on_all_failures(self):
        self._patch_home_safely()
        # tempdir HOME 下不写配置：全部路径都走失败/降级形态
        with unittest.mock.patch("builtins.print") as mock_print:
            self._all_paths(str(self.root / "missing" / "config.json"),
                            (None, None))
        mock_print.assert_not_called()

    def test_config_file_untouched(self):
        config_path = self.write_config(standard_payload())
        before = Path(config_path).read_bytes()
        resolve_credential(config_path, environ={})
        self.assertEqual(Path(config_path).read_bytes(), before)

    def test_no_new_files_created(self):
        config_path = self.write_config(standard_payload())
        before = self.snapshot_files()
        resolve_credential(config_path, environ={})
        resolve_credential(str(self.root / "missing.json"), environ={})
        self.assertEqual(self.snapshot_files(), before)


if __name__ == "__main__":
    unittest.main()
