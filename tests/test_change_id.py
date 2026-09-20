#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.change_id 单元测试（v2.4 Phase 2，单元 W2 / 工作包 P2-B）。

锚定对象：任务级变更标识的五项规格行为——
    TestDeterminism           同树两次计算全等；空相关集同样稳定
    TestTrackedChange         被跟踪路径内容变化 / 删除 / 相关文件集
                              变化 → 标识改变；CRLF↔LF 不变
    TestBookkeepingIgnore     仅 .glm-conductor/ 内变化（内容增删改、
                              反斜杠与 ./ 写法、目录自身路径）不改变
                              标识；与不含记账路径的等价清单全等；
                              形状相近目录（.glm-conductor-notes/）
                              照常参与
    TestBaseAnchoring         显式 base 与缺省（rev-parse HEAD）等价；
                              不同 base 结果不同；钉住 base 后无关提交
                              不影响标识（缺省调用则会变）；非法 base
                              一律拒绝
    TestOrderInsensitivity    顺序 / 重复 / ./ 前缀写法不影响结果
另有归一原语（normalize_content / content_digest / normalize_relpath /
is_bookkeeping_path）与 unborn / 非 git 仓库边界。

全部离线：git fixture 在 tempfile.TemporaryDirectory 内（git init +
config + 初始提交，subprocess 调 git），绝不触碰仓库内 .glm-conductor/
真实账本；OS / 文件原语零 mock；环境无 git 可执行时 git 相关用例自动
skipTest（CI 有 git，正常执行）。纯归一原语用例始终执行。

运行：
    cd <repo_root> && python3 -X utf8 -m unittest tests.test_change_id -v
"""

import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import change_id


def run_git(repo, *args):
    """在 fixture 仓库里执行 git 子命令（测试装置专用，失败即断言错误）。"""
    proc = subprocess.run(
        ["git"] + list(args), cwd=str(repo),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise AssertionError(
            "测试装置 git %s 失败（returncode=%d）：%s"
            % (" ".join(args), proc.returncode,
               proc.stderr.decode("utf-8", errors="replace")))
    return proc.stdout.decode("utf-8", errors="replace")


# 变更标识合法格式："sha256:" + 64 位小写十六进制
_CHANGE_ID_RE = re.compile("^sha256:[0-9a-f]{64}$")


# —— 公共测试基类：tempdir + Windows 安全清理 ——

class TempDirCase(unittest.TestCase):
    """提供临时目录 self.repo 与 Windows 安全清理的测试基类。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._force_cleanup)
        self.repo = Path(self._tmp.name)

    def _force_cleanup(self):
        """解除 git 只读对象后清理临时目录。

        Windows 上 git 松散对象文件带只读属性，TemporaryDirectory.cleanup()
        的 rmtree 会 PermissionError；先遍历 .git 清掉只读位再删除。
        """
        git_dir = os.path.join(self._tmp.name, ".git")
        if os.path.isdir(git_dir):
            for dirpath, _dirnames, filenames in os.walk(git_dir):
                for name in filenames:
                    try:
                        os.chmod(os.path.join(dirpath, name), stat.S_IWRITE)
                    except OSError:
                        pass
        self._tmp.cleanup()

    def write(self, rel, data):
        """写入仓库相对路径文件（bytes 内容），父目录自动创建。"""
        target = self.repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


# —— normalize_content（纯函数，无 git） ——

class TestNormalizeContent(unittest.TestCase):

    def test_crlf_becomes_lf(self):
        self.assertEqual(change_id.normalize_content(b"a\r\nb\r\n"),
                         b"a\nb\n")

    def test_lf_content_unchanged(self):
        self.assertEqual(change_id.normalize_content(b"a\nb\n"), b"a\nb\n")

    def test_content_without_any_cr_unchanged(self):
        self.assertEqual(change_id.normalize_content(b"plain bytes"),
                         b"plain bytes")

    def test_empty_bytes_unchanged(self):
        self.assertEqual(change_id.normalize_content(b""), b"")

    def test_non_bytes_raises(self):
        # str / int / None / bytearray 都不是 bytes，一律拒绝
        for bad in ("text", 123, None, bytearray(b"a\r\n")):
            with self.subTest(value=bad):
                with self.assertRaises(change_id.ChangeIdError):
                    change_id.normalize_content(bad)


# —— content_digest（纯函数，无 git） ——

class TestContentDigest(unittest.TestCase):

    def test_same_content_same_digest(self):
        self.assertEqual(change_id.content_digest(b"hello\n"),
                         change_id.content_digest(b"hello\n"))

    def test_crlf_and_lf_same_digest(self):
        # 换行归一核心用例：CRLF 版与 LF 版的同一文本摘要相等
        self.assertEqual(
            change_id.content_digest(b"line1\r\nline2\r\n"),
            change_id.content_digest(b"line1\nline2\n"))

    def test_different_content_different_digest(self):
        self.assertNotEqual(change_id.content_digest(b"v1\n"),
                            change_id.content_digest(b"v2\n"))

    def test_format_is_64_lowercase_hex(self):
        digest = change_id.content_digest(b"anything")
        self.assertIsInstance(digest, str)
        self.assertEqual(len(digest), 64)
        self.assertRegex(digest, "^[0-9a-f]{64}$")

    def test_non_bytes_raises(self):
        with self.assertRaises(change_id.ChangeIdError):
            change_id.content_digest("text")


# —— normalize_relpath（纯函数，无 git） ——

class TestNormalizeRelpath(unittest.TestCase):

    def test_backslashes_become_slashes(self):
        self.assertEqual(change_id.normalize_relpath("src\\a.ts"),
                         "src/a.ts")

    def test_strips_leading_dot_slash_repeatedly(self):
        self.assertEqual(change_id.normalize_relpath("./a"), "a")
        self.assertEqual(change_id.normalize_relpath("././a"), "a")
        self.assertEqual(change_id.normalize_relpath("./src/a.ts"),
                         "src/a.ts")

    def test_valid_plain_path_unchanged(self):
        self.assertEqual(change_id.normalize_relpath("src/a.ts"),
                         "src/a.ts")

    def test_invalid_paths_raise_with_original_in_message(self):
        # 消息含路径原文（%r）；反斜杠路径 repr 会转义，单独验证抛错即可
        for bad in ("", "/x", "C:/x", "..", "a/../b", ".", "a//b", "a/",
                    "a/./b", "./"):
            with self.subTest(path=bad):
                with self.assertRaises(change_id.ChangeIdError) as ctx:
                    change_id.normalize_relpath(bad)
                self.assertIn(bad, str(ctx.exception))
        for bad in ("C:\\x",):
            with self.subTest(path=bad):
                with self.assertRaises(change_id.ChangeIdError):
                    change_id.normalize_relpath(bad)

    def test_non_string_raises(self):
        with self.assertRaises(change_id.ChangeIdError):
            change_id.normalize_relpath(123)


# —— is_bookkeeping_path（纯函数，无 git） ——

class TestIsBookkeepingPath(unittest.TestCase):

    def test_directory_itself_and_descendants(self):
        self.assertTrue(change_id.is_bookkeeping_path(".glm-conductor"))
        self.assertTrue(
            change_id.is_bookkeeping_path(".glm-conductor/state.json"))
        self.assertTrue(change_id.is_bookkeeping_path(
            ".glm-conductor/tasks/t-1/state.json"))

    def test_non_bookkeeping_paths(self):
        # 豁免按完整首段判定：形状相近目录 / 普通路径都不是记账路径
        for path in ("src/a.txt", ".glm-conductor-notes/a.txt",
                     "x.glm-conductor/a.json", "AGENTS.md"):
            self.assertFalse(change_id.is_bookkeeping_path(path))


# —— git 仓库 fixture：init + 配置 + 初始提交（含 base 文件） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class GitRepoFixture(TempDirCase):
    """git init + 身份配置 + core.autocrlf=false + 初始提交（含 base.txt）。"""

    def setUp(self):
        TempDirCase.setUp(self)
        run_git(self.repo, "init")
        run_git(self.repo, "config", "user.email", "change-id@example.com")
        run_git(self.repo, "config", "user.name", "Change Id Gate")
        # 固定换行行为，避免全局 autocrlf 干扰内容字节
        run_git(self.repo, "config", "core.autocrlf", "false")
        # 初始提交：作为基线（HEAD）与已跟踪文件的基底
        self.write("base.txt", b"base content\n")
        run_git(self.repo, "add", ".")
        run_git(self.repo, "commit", "-m", "init")

    def head(self):
        """当前 HEAD 修订号（测试装置自取，不经被测模块）。"""
        return run_git(self.repo, "rev-parse", "HEAD").strip()


# —— 规格行为 1：同树两次计算全等 ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class TestDeterminism(GitRepoFixture):

    def test_same_tree_twice_equal(self):
        self.write("src/a.txt", b"a\n")
        self.write("docs/b.md", b"b\n")
        first = change_id.compute_change_id(
            self.repo, ["src/a.txt", "docs/b.md"])
        second = change_id.compute_change_id(
            self.repo, ["docs/b.md", "src/a.txt"])
        self.assertEqual(first, second)
        self.assertRegex(first, _CHANGE_ID_RE)

    def test_empty_relevant_paths_stable(self):
        # 空相关集：preimage 只有头两行，同样有稳定合法标识
        first = change_id.compute_change_id(self.repo, [])
        second = change_id.compute_change_id(self.repo, ())
        self.assertEqual(first, second)
        self.assertRegex(first, _CHANGE_ID_RE)


# —— 规格行为 2：被跟踪路径变更后标识改变 ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class TestTrackedChange(GitRepoFixture):

    def test_content_change_changes_id(self):
        self.write("src/a.txt", b"v1 content\n")
        run_git(self.repo, "add", ".")
        before = change_id.compute_change_id(self.repo, ["src/a.txt"])
        self.write("src/a.txt", b"v2 content\n")
        self.assertNotEqual(
            before, change_id.compute_change_id(self.repo, ["src/a.txt"]))

    def test_crlf_lf_same_id(self):
        # 换行归一：LF 版改为 CRLF 版（逻辑行相同）→ 标识不变
        self.write("src/a.txt", b"line1\nline2\n")
        lf = change_id.compute_change_id(self.repo, ["src/a.txt"])
        self.write("src/a.txt", b"line1\r\nline2\r\n")
        self.assertEqual(
            lf, change_id.compute_change_id(self.repo, ["src/a.txt"]))

    def test_delete_marks_missing_and_changes_id(self):
        # 文件删除：同一路径从内容态变为 missing 态 → 标识变化
        self.write("src/a.txt", b"will be deleted\n")
        before = change_id.compute_change_id(self.repo, ["src/a.txt"])
        (self.repo / "src" / "a.txt").unlink()
        self.assertNotEqual(
            before, change_id.compute_change_id(self.repo, ["src/a.txt"]))

    def test_scope_change_changes_id(self):
        # 相关文件集变化：新增文件进 relevant_paths → 标识变化
        self.write("src/a.txt", b"a\n")
        self.write("src/added.txt", b"newly in scope\n")
        before = change_id.compute_change_id(self.repo, ["src/a.txt"])
        after = change_id.compute_change_id(
            self.repo, ["src/a.txt", "src/added.txt"])
        self.assertNotEqual(before, after)


# —— 规格行为 3：仅 .glm-conductor/ 内变更不改变标识 ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class TestBookkeepingIgnore(GitRepoFixture):
    """编排器自身写账本不得使自己过时（规格红线）。"""

    def test_bookkeeping_paths_equivalent_to_absent(self):
        # 相关清单里混入记账路径 == 没混入：结果全等
        self.write("src/a.txt", b"a\n")
        self.assertEqual(
            change_id.compute_change_id(self.repo, ["src/a.txt"]),
            change_id.compute_change_id(
                self.repo,
                ["src/a.txt", ".glm-conductor/state.json",
                 ".glm-conductor/tasks/t-1/state.json"]))

    def test_bookkeeping_content_change_keeps_id(self):
        # 记账文件内容变化 / 删除都不改变标识（真实文件同批参与）
        self.write("src/a.txt", b"a\n")
        paths = ["src/a.txt", ".glm-conductor/state.json"]
        before = change_id.compute_change_id(self.repo, paths)
        self.write(".glm-conductor/state.json", b'{"v": 1}\n')
        self.assertEqual(
            before, change_id.compute_change_id(self.repo, paths))
        self.write(".glm-conductor/state.json", b'{"v": 2, "more": true}\n')
        self.assertEqual(
            before, change_id.compute_change_id(self.repo, paths))
        (self.repo / ".glm-conductor" / "state.json").unlink()
        self.assertEqual(
            before, change_id.compute_change_id(self.repo, paths))

    def test_bookkeeping_normalized_forms_filtered(self):
        # 反斜杠 / ./ 前缀写法归一后同样被剔除；目录自身路径被剔除
        #（即使它真实存在为目录，也不触发目录错误）
        self.write("src/a.txt", b"a\n")
        self.write(".glm-conductor/x.json", b"x\n")
        self.assertEqual(
            change_id.compute_change_id(self.repo, ["src/a.txt"]),
            change_id.compute_change_id(
                self.repo,
                ["src/a.txt", ".glm-conductor\\x.json",
                 "./.glm-conductor/x.json"]))
        self.assertEqual(
            change_id.compute_change_id(self.repo, ["src/a.txt"]),
            change_id.compute_change_id(
                self.repo, ["src/a.txt", ".glm-conductor"]))

    def test_prefix_lookalike_participates(self):
        # 豁免只按完整首段判定：.glm-conductor-notes/ 不是记账目录
        self.write(".glm-conductor-notes/a.txt", b"v1\n")
        paths = [".glm-conductor-notes/a.txt"]
        before = change_id.compute_change_id(self.repo, paths)
        self.write(".glm-conductor-notes/a.txt", b"v2\n")
        self.assertNotEqual(
            before, change_id.compute_change_id(self.repo, paths))


# —— 规格行为 4：base 锚定 ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class TestBaseAnchoring(GitRepoFixture):

    def test_explicit_base_matches_default(self):
        # 显式传入基线（调用方已持有，省一次 rev-parse）与缺省调用全等
        self.write("src/a.txt", b"v2\n")
        self.assertEqual(
            change_id.compute_change_id(
                self.repo, ["src/a.txt"], base=self.head()),
            change_id.compute_change_id(self.repo, ["src/a.txt"]))

    def test_different_base_changes_id(self):
        # 基线变化（HEAD 前进）→ 标识变化
        self.write("src/a.txt", b"v2\n")
        head_before = self.head()
        run_git(self.repo, "commit", "--allow-empty", "-m", "bump")
        self.assertNotEqual(
            change_id.compute_change_id(
                self.repo, ["src/a.txt"], base=head_before),
            change_id.compute_change_id(
                self.repo, ["src/a.txt"], base=self.head()))

    def test_pinned_base_ignores_later_commit(self):
        # base 钉住锚点：之后无关提交不影响标识；缺省调用（内部自取
        # 新 HEAD）则会变——锚定语义的直接证明
        self.write("src/a.txt", b"stable\n")
        head_before = self.head()
        pinned = change_id.compute_change_id(
            self.repo, ["src/a.txt"], base=head_before)
        run_git(self.repo, "commit", "--allow-empty", "-m", "unrelated")
        self.assertEqual(
            pinned,
            change_id.compute_change_id(
                self.repo, ["src/a.txt"], base=head_before))
        self.assertNotEqual(
            pinned, change_id.compute_change_id(self.repo, ["src/a.txt"]))

    def test_invalid_explicit_base_raises(self):
        # 显式 base 必须是非空 str（None = 内部自取；其他类型 / 空串抛
        # ChangeIdError，结构性错误不静默转换）
        self.write("src/a.txt", b"v2\n")
        for bad in (123, b"abc", "", ["abc"], {}):
            with self.subTest(base=bad):
                with self.assertRaises(change_id.ChangeIdError):
                    change_id.compute_change_id(
                        self.repo, ["src/a.txt"], base=bad)


# —— 规格行为 5：路径顺序无关 ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class TestOrderInsensitivity(GitRepoFixture):

    def test_order_and_duplicates_irrelevant(self):
        self.write("src/a.txt", b"a\n")
        self.write("src/b.txt", b"b\n")
        self.write("docs/c.md", b"c\n")
        forward = change_id.compute_change_id(
            self.repo, ["src/a.txt", "src/b.txt", "docs/c.md"])
        backward = change_id.compute_change_id(
            self.repo, ["docs/c.md", "src/b.txt", "src/a.txt"])
        duplicated = change_id.compute_change_id(
            self.repo, ["src/b.txt", "src/a.txt", "src/b.txt",
                        "docs/c.md", "./src/a.txt"])
        self.assertEqual(forward, backward)
        self.assertEqual(forward, duplicated)


# —— 结构性边界（git fixture 内） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class TestStructural(GitRepoFixture):

    def test_directory_target_raises(self):
        (self.repo / "subdir").mkdir()
        with self.assertRaises(change_id.ChangeIdError):
            change_id.compute_change_id(self.repo, ["subdir"])

    def test_illegal_path_raises_through(self):
        with self.assertRaises(change_id.ChangeIdError):
            change_id.compute_change_id(self.repo, ["../escape.txt"])

    def test_non_string_path_item_raises(self):
        with self.assertRaises(change_id.ChangeIdError):
            change_id.compute_change_id(self.repo, [123])

    def test_missing_vs_empty_file_distinct(self):
        # 路径不存在（missing 标记）与空文件（sha256:空串摘要）标识不同
        missing = change_id.compute_change_id(self.repo, ["ghost.txt"])
        self.write("ghost.txt", b"")
        empty = change_id.compute_change_id(self.repo, ["ghost.txt"])
        self.assertNotEqual(missing, empty)


# —— unborn 仓库（git init 后无任何提交） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class TestUnbornRepo(TempDirCase):
    """基线退化为 "-"：标识仍可稳定计算，显式传 "-" 与缺省等价。"""

    def setUp(self):
        TempDirCase.setUp(self)
        run_git(self.repo, "init")
        run_git(self.repo, "config", "user.email", "change-id@example.com")
        run_git(self.repo, "config", "user.name", "Change Id Gate")
        run_git(self.repo, "config", "core.autocrlf", "false")

    def test_resolve_base_returns_dash(self):
        self.assertEqual(change_id.resolve_base(self.repo), "-")

    def test_compute_change_id_works(self):
        self.write("base.txt", b"content before first commit\n")
        cid = change_id.compute_change_id(self.repo, ["base.txt"])
        self.assertRegex(cid, _CHANGE_ID_RE)
        self.assertEqual(
            cid, change_id.compute_change_id(self.repo, ["base.txt"]))

    def test_explicit_base_dash_matches_default(self):
        self.write("base.txt", b"content before first commit\n")
        self.assertEqual(
            change_id.compute_change_id(self.repo, ["base.txt"], base="-"),
            change_id.compute_change_id(self.repo, ["base.txt"]))


# —— 非 git 目录 ——

class TestNonGitDir(TempDirCase):
    """非 git 目录（无 .git）：resolve_base / compute_change_id 均抛错。"""

    def test_resolve_base_raises(self):
        with self.assertRaises(change_id.ChangeIdError) as ctx:
            change_id.resolve_base(self.repo)
        # 消息含 returncode 与 stderr 摘要
        self.assertIn("returncode", str(ctx.exception))

    def test_compute_change_id_raises(self):
        with self.assertRaises(change_id.ChangeIdError):
            change_id.compute_change_id(self.repo, ["whatever.txt"])


if __name__ == "__main__":
    unittest.main()
