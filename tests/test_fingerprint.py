#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.fingerprint 单元测试（v2 工作块 B3.1 / B3.2）。

运行：
    python3 -m unittest tests.test_fingerprint -v

resolve_base / compute_fingerprint / task_fingerprint 的用例需要真实
git 仓库 fixture（tempdir 内 git init + config + add/commit，subprocess
调 git）；环境无 git 可执行时自动 skipTest（CI 有 git，正常执行）。
visual_evidence_status 不依赖 git（纯文件读取），始终执行。
"""

import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import fingerprint
from runtime import ownership

import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile


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


# 指纹合法格式："sha256:" + 64 位小写十六进制
_FINGERPRINT_RE = re.compile("^sha256:[0-9a-f]{64}$")


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
        self.assertEqual(fingerprint.normalize_content(b"a\r\nb\r\n"),
                         b"a\nb\n")

    def test_lf_content_unchanged(self):
        self.assertEqual(fingerprint.normalize_content(b"a\nb\n"), b"a\nb\n")

    def test_content_without_any_cr_unchanged(self):
        self.assertEqual(fingerprint.normalize_content(b"plain bytes"),
                         b"plain bytes")

    def test_empty_bytes_unchanged(self):
        self.assertEqual(fingerprint.normalize_content(b""), b"")

    def test_non_bytes_raises(self):
        # str / int / None / bytearray 都不是 bytes，一律拒绝
        for bad in ("text", 123, None, bytearray(b"a\r\n")):
            with self.subTest(value=bad):
                with self.assertRaises(fingerprint.FingerprintError):
                    fingerprint.normalize_content(bad)


# —— content_digest（纯函数，无 git） ——

class TestContentDigest(unittest.TestCase):

    def test_same_content_same_digest(self):
        self.assertEqual(fingerprint.content_digest(b"hello\n"),
                         fingerprint.content_digest(b"hello\n"))

    def test_crlf_and_lf_same_digest(self):
        # 换行归一核心用例：CRLF 版与 LF 版的同一文本摘要相等
        self.assertEqual(
            fingerprint.content_digest(b"line1\r\nline2\r\n"),
            fingerprint.content_digest(b"line1\nline2\n"))

    def test_different_content_different_digest(self):
        self.assertNotEqual(fingerprint.content_digest(b"v1\n"),
                            fingerprint.content_digest(b"v2\n"))

    def test_format_is_64_lowercase_hex(self):
        digest = fingerprint.content_digest(b"anything")
        self.assertIsInstance(digest, str)
        self.assertEqual(len(digest), 64)
        self.assertRegex(digest, "^[0-9a-f]{64}$")

    def test_non_bytes_raises(self):
        with self.assertRaises(fingerprint.FingerprintError):
            fingerprint.content_digest("text")


# —— normalize_relpath（纯函数，无 git） ——

class TestNormalizeRelpath(unittest.TestCase):

    def test_backslashes_become_slashes(self):
        self.assertEqual(fingerprint.normalize_relpath("src\\a.ts"),
                         "src/a.ts")

    def test_strips_leading_dot_slash_repeatedly(self):
        self.assertEqual(fingerprint.normalize_relpath("./a"), "a")
        self.assertEqual(fingerprint.normalize_relpath("././a"), "a")
        self.assertEqual(fingerprint.normalize_relpath("./src/a.ts"),
                         "src/a.ts")

    def test_valid_plain_path_unchanged(self):
        self.assertEqual(fingerprint.normalize_relpath("src/a.ts"),
                         "src/a.ts")

    def test_invalid_paths_raise_with_original_in_message(self):
        # 消息含路径原文（%r）；反斜杠路径 repr 会转义，单独验证抛错即可
        for bad in ("", "/x", "C:/x", "..", "a/../b", ".", "a//b", "a/",
                    "a/./b", "./"):
            with self.subTest(path=bad):
                with self.assertRaises(fingerprint.FingerprintError) as ctx:
                    fingerprint.normalize_relpath(bad)
                self.assertIn(bad, str(ctx.exception))
        for bad in ("C:\\x",):
            with self.subTest(path=bad):
                with self.assertRaises(fingerprint.FingerprintError):
                    fingerprint.normalize_relpath(bad)

    def test_non_string_raises(self):
        with self.assertRaises(fingerprint.FingerprintError):
            fingerprint.normalize_relpath(123)


# —— git 仓库 fixture：init + 配置 + 初始提交（含 base 文件） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class GitRepoFixture(TempDirCase):
    """git init + 身份配置 + core.autocrlf=false + 初始提交（含 base.txt）。"""

    def setUp(self):
        TempDirCase.setUp(self)
        run_git(self.repo, "init")
        run_git(self.repo, "config", "user.email", "fingerprint@example.com")
        run_git(self.repo, "config", "user.name", "Fingerprint Gate")
        # 固定换行行为，避免全局 autocrlf 干扰内容字节
        run_git(self.repo, "config", "core.autocrlf", "false")
        # 初始提交：作为基线（HEAD）与已跟踪文件的基底
        self.write("base.txt", b"base content\n")
        run_git(self.repo, "add", ".")
        run_git(self.repo, "commit", "-m", "init")


# —— resolve_base / compute_fingerprint（真实 git 仓库 fixture） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class TestComputeFingerprint(GitRepoFixture):

    def test_basic_flow_returns_valid_format(self):
        # 修改已跟踪文件 + 新增未跟踪文件 → 合法指纹
        self.write("base.txt", b"modified content\n")
        self.write("untracked.txt", b"new file\n")
        fp = fingerprint.compute_fingerprint(
            self.repo, ["base.txt", "untracked.txt"])
        self.assertRegex(fp, _FINGERPRINT_RE)

    def test_deterministic_across_repeat_calls(self):
        self.write("base.txt", b"v2\n")
        fp1 = fingerprint.compute_fingerprint(self.repo, ["base.txt"])
        fp2 = fingerprint.compute_fingerprint(self.repo, ["base.txt"])
        self.assertEqual(fp1, fp2)

    def test_order_insensitive(self):
        # paths 传逆序列表结果相同（排序不敏感）
        self.write("base.txt", b"v2\n")
        self.write("b.txt", b"b\n")
        fp1 = fingerprint.compute_fingerprint(
            self.repo, ["b.txt", "base.txt"])
        fp2 = fingerprint.compute_fingerprint(
            self.repo, ["base.txt", "b.txt"])
        self.assertEqual(fp1, fp2)

    def test_duplicate_paths_dedup(self):
        # 重复路径（含 ./ 前缀写法）归一去重后结果相同
        self.write("base.txt", b"v2\n")
        fp1 = fingerprint.compute_fingerprint(self.repo, ["base.txt"])
        fp2 = fingerprint.compute_fingerprint(
            self.repo, ["base.txt", "base.txt", "./base.txt"])
        self.assertEqual(fp1, fp2)

    def test_crlf_lf_same_fingerprint(self):
        # 换行归一：LF 版改为 CRLF 版（逻辑行相同）→ 指纹不变
        self.write("base.txt", b"line1\nline2\n")
        fp_lf = fingerprint.compute_fingerprint(self.repo, ["base.txt"])
        self.write("base.txt", b"line1\r\nline2\r\n")
        fp_crlf = fingerprint.compute_fingerprint(self.repo, ["base.txt"])
        self.assertEqual(fp_lf, fp_crlf)

    def test_content_change_changes_fingerprint(self):
        self.write("base.txt", b"v1 content\n")
        fp1 = fingerprint.compute_fingerprint(self.repo, ["base.txt"])
        self.write("base.txt", b"v2 content\n")
        fp2 = fingerprint.compute_fingerprint(self.repo, ["base.txt"])
        self.assertNotEqual(fp1, fp2)

    def test_deleted_file_marks_missing_and_changes_fingerprint(self):
        # 文件删除：同一路径从内容态变为 missing 态 → 指纹变化
        self.write("extra.txt", b"will be deleted\n")
        fp1 = fingerprint.compute_fingerprint(self.repo, ["extra.txt"])
        (self.repo / "extra.txt").unlink()
        fp2 = fingerprint.compute_fingerprint(self.repo, ["extra.txt"])
        self.assertNotEqual(fp1, fp2)

    def test_adding_new_path_changes_fingerprint(self):
        # 文件集变化：新增文件进 paths → 指纹变化
        self.write("added.txt", b"newly in scope\n")
        fp1 = fingerprint.compute_fingerprint(self.repo, ["base.txt"])
        fp2 = fingerprint.compute_fingerprint(
            self.repo, ["base.txt", "added.txt"])
        self.assertNotEqual(fp1, fp2)

    def test_missing_vs_empty_file_distinct(self):
        # 路径不存在（missing 标记）与空文件（sha256:空串摘要）指纹不同
        fp_missing = fingerprint.compute_fingerprint(self.repo, ["ghost.txt"])
        self.write("ghost.txt", b"")
        fp_empty = fingerprint.compute_fingerprint(self.repo, ["ghost.txt"])
        self.assertNotEqual(fp_missing, fp_empty)

    def test_baseline_commit_changes_fingerprint(self):
        # 基线参与：新增一次 commit（HEAD 变化、文件内容不变）→ 指纹变化
        self.write("base.txt", b"stable content\n")
        fp1 = fingerprint.compute_fingerprint(self.repo, ["base.txt"])
        run_git(self.repo, "commit", "--allow-empty", "-m", "bump")
        fp2 = fingerprint.compute_fingerprint(self.repo, ["base.txt"])
        self.assertNotEqual(fp1, fp2)

    def test_directory_path_raises(self):
        (self.repo / "subdir").mkdir()
        with self.assertRaises(fingerprint.FingerprintError):
            fingerprint.compute_fingerprint(self.repo, ["subdir"])

    def test_empty_paths_returns_valid_fingerprint(self):
        # 空 paths：preimage 只有 header+base 两行，仍有稳定合法指纹
        fp = fingerprint.compute_fingerprint(self.repo, [])
        self.assertRegex(fp, _FINGERPRINT_RE)
        self.assertEqual(
            fp, fingerprint.compute_fingerprint(self.repo, ()))

    def test_illegal_path_raises_through(self):
        with self.assertRaises(fingerprint.FingerprintError):
            fingerprint.compute_fingerprint(self.repo, ["../escape.txt"])


# —— task_fingerprint（真实 git 仓库 fixture，B3.2） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class TestTaskFingerprint(GitRepoFixture):
    """task_fingerprint：按 ownership 声明过滤 touched 后计算指纹。"""

    def test_declared_ownership_hashes_only_owned_hits(self):
        # 声明 ownership → 越界文件内容变化 → 指纹不变；owned 文件变化 → 变
        self.write("src/owned.py", b"owned v1\n")
        self.write("other/out-of-scope.txt", b"out v1\n")
        st = {"ownership": {"files": ["src/**"]}}
        fp1 = fingerprint.task_fingerprint(self.repo, st)
        self.write("other/out-of-scope.txt", b"out v2\n")
        fp2 = fingerprint.task_fingerprint(self.repo, st)
        self.assertEqual(fp1, fp2)
        self.write("src/owned.py", b"owned v2\n")
        fp3 = fingerprint.task_fingerprint(self.repo, st)
        self.assertNotEqual(fp1, fp3)

    def test_no_ownership_hashes_all_touched(self):
        # 未声明 ownership → 任意 touched 文件变化 → 指纹变
        self.write("src/owned.py", b"owned v1\n")
        self.write("other/out.txt", b"out v1\n")
        st = {"ownership": {"files": []}}  # 空声明 = 未声明
        fp1 = fingerprint.task_fingerprint(self.repo, st)
        self.write("other/out.txt", b"out v2\n")
        fp2 = fingerprint.task_fingerprint(self.repo, st)
        self.assertNotEqual(fp1, fp2)
        self.write("src/owned.py", b"owned v2\n")
        fp3 = fingerprint.task_fingerprint(self.repo, st)
        self.assertNotEqual(fp2, fp3)

    def test_matches_compute_fingerprint_over_owned_scope(self):
        # 一致性：声明 ownership 时 = 对 owned_hits 手动调 compute_fingerprint
        self.write("src/owned.py", b"owned v1\n")
        self.write("other/out.txt", b"out\n")
        st = {"ownership": {"files": ["src/**"]}}
        self.assertEqual(
            fingerprint.task_fingerprint(self.repo, st),
            fingerprint.compute_fingerprint(self.repo, ["src/owned.py"]))

    def test_matches_compute_fingerprint_over_all_touched(self):
        # 一致性：未声明 ownership 时 = 对全部 touched 手动调 compute_fingerprint
        self.write("src/owned.py", b"owned v1\n")
        self.write("other/out.txt", b"out v1\n")
        st = {"ownership": {"files": []}}
        self.assertEqual(
            fingerprint.task_fingerprint(self.repo, st),
            fingerprint.compute_fingerprint(
                self.repo, ["src/owned.py", "other/out.txt"]))

    def test_malformed_ownership_files_treated_as_empty(self):
        # files 非 list 或全非 str 项 → 按空处理（范围 = touched 全部）
        self.write("a.txt", b"a\n")
        expected = fingerprint.compute_fingerprint(self.repo, ["a.txt"])
        for bad_files in (None, "src/**", [123, ""], {"src/**"}):
            with self.subTest(files=bad_files):
                st = {"ownership": {"files": bad_files}}
                self.assertEqual(
                    fingerprint.task_fingerprint(self.repo, st), expected)

    def test_ownership_key_missing_treated_as_undeclared(self):
        self.write("a.txt", b"a\n")
        self.assertEqual(
            fingerprint.task_fingerprint(self.repo, {}),
            fingerprint.compute_fingerprint(self.repo, ["a.txt"]))

    def test_non_dict_task_state_raises(self):
        # 结构性错误：task_state 非 dict → FingerprintError（不静默转换）
        for bad in (None, "state", ["ownership"], 42):
            with self.subTest(task_state=bad):
                with self.assertRaises(fingerprint.FingerprintError):
                    fingerprint.task_fingerprint(self.repo, bad)

    def test_fingerprint_format_valid(self):
        self.write("src/owned.py", b"owned v1\n")
        fp = fingerprint.task_fingerprint(
            self.repo, {"ownership": {"files": ["src/**"]}})
        self.assertRegex(fp, _FINGERPRINT_RE)


@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class TestTaskFingerprintNonGit(TempDirCase):
    """非 git 目录：task_fingerprint 让 OwnershipError 自然上抛。"""

    def test_ownership_declared_raises_ownership_error(self):
        st = {"ownership": {"files": ["src/**"]}}
        with self.assertRaises(ownership.OwnershipError):
            fingerprint.task_fingerprint(self.repo, st)

    def test_ownership_undeclared_raises_ownership_error(self):
        # git_touched_files 在范围判定之前调用，未声明 ownership 同样上抛
        with self.assertRaises(ownership.OwnershipError):
            fingerprint.task_fingerprint(self.repo, {})


# —— visual_evidence_status（纯文件读取，不依赖 git，B3.2） ——

class TestVisualEvidenceStatus(TempDirCase):

    def test_non_list_entries_raise(self):
        for bad in (None, "x", {"path": "a", "sha256": "ab"}, 42):
            with self.subTest(entries=bad):
                with self.assertRaises(fingerprint.FingerprintError):
                    fingerprint.visual_evidence_status(self.repo, bad)

    def test_non_dict_item_raises(self):
        with self.assertRaises(fingerprint.FingerprintError):
            fingerprint.visual_evidence_status(
                self.repo, ["docs/shot.png"])

    def test_illegal_path_raises(self):
        entries = [{"path": "../escape.png", "sha256": "ab" * 32}]
        with self.assertRaises(fingerprint.FingerprintError):
            fingerprint.visual_evidence_status(self.repo, entries)

    def test_fresh_entry_not_stale(self):
        payload = b"\x89PNG fake bytes\n"
        self.write("docs/shot.png", payload)
        digest = hashlib.sha256(payload).hexdigest()
        result = fingerprint.visual_evidence_status(
            self.repo, [{"path": "docs/shot.png", "sha256": digest}])
        self.assertEqual(len(result), 1)
        item = result[0]
        self.assertEqual(item["path"], "docs/shot.png")
        self.assertEqual(item["recorded"], digest)
        self.assertEqual(item["current"], digest)
        self.assertFalse(item["stale"])

    def test_content_change_stale(self):
        self.write("docs/shot.png", b"v1 bytes\n")
        recorded = hashlib.sha256(b"v1 bytes\n").hexdigest()
        self.write("docs/shot.png", b"v2 bytes\n")
        result = fingerprint.visual_evidence_status(
            self.repo, [{"path": "docs/shot.png", "sha256": recorded}])
        self.assertTrue(result[0]["stale"])
        self.assertEqual(
            result[0]["current"], hashlib.sha256(b"v2 bytes\n").hexdigest())

    def test_deleted_file_current_none_and_stale(self):
        self.write("docs/shot.png", b"bytes\n")
        recorded = hashlib.sha256(b"bytes\n").hexdigest()
        (self.repo / "docs" / "shot.png").unlink()
        result = fingerprint.visual_evidence_status(
            self.repo, [{"path": "docs/shot.png", "sha256": recorded}])
        self.assertIsNone(result[0]["current"])
        self.assertTrue(result[0]["stale"])

    def test_directory_path_current_none_and_stale(self):
        # 容错优先：目标是目录不抛，该项记 current=None、stale=True
        (self.repo / "docs").mkdir(parents=True)
        result = fingerprint.visual_evidence_status(
            self.repo, [{"path": "docs", "sha256": "ab" * 32}])
        self.assertIsNone(result[0]["current"])
        self.assertTrue(result[0]["stale"])

    def test_binary_bytes_not_normalized(self):
        # raw 语义证明：含 \r\n 的文件按原始字节哈希命中（stale=False），
        # 而 content_digest（CRLF→LF 归一）的值 ≠ recorded
        payload = b"\x00\x01binary\r\nwith crlf\r\n\x00"
        self.write("docs/raw.bin", payload)
        raw_digest = hashlib.sha256(payload).hexdigest()
        result = fingerprint.visual_evidence_status(
            self.repo, [{"path": "docs/raw.bin", "sha256": raw_digest}])
        self.assertFalse(result[0]["stale"])
        self.assertNotEqual(raw_digest, fingerprint.content_digest(payload))

    def test_backslash_path_normalized_but_original_returned(self):
        # 读取用归一路径，返回值保留原始 path
        self.write("docs/shot.png", b"bytes\n")
        digest = hashlib.sha256(b"bytes\n").hexdigest()
        result = fingerprint.visual_evidence_status(
            self.repo, [{"path": "docs\\shot.png", "sha256": digest}])
        self.assertFalse(result[0]["stale"])
        self.assertEqual(result[0]["path"], "docs\\shot.png")

    def test_multiple_entries_order_preserved(self):
        self.write("a.png", b"a\n")
        self.write("b.png", b"b\n")
        entries = [
            {"path": "b.png", "sha256": hashlib.sha256(b"b\n").hexdigest()},
            {"path": "a.png", "sha256": hashlib.sha256(b"old\n").hexdigest()},
        ]
        result = fingerprint.visual_evidence_status(self.repo, entries)
        self.assertEqual([item["path"] for item in result], ["b.png", "a.png"])
        self.assertFalse(result[0]["stale"])
        self.assertTrue(result[1]["stale"])


@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class TestUnbornRepo(TempDirCase):
    """git init 后无任何提交（unborn 仓库，无 HEAD）。"""

    def setUp(self):
        TempDirCase.setUp(self)
        run_git(self.repo, "init")
        run_git(self.repo, "config", "user.email", "fingerprint@example.com")
        run_git(self.repo, "config", "user.name", "Fingerprint Gate")
        run_git(self.repo, "config", "core.autocrlf", "false")

    def test_resolve_base_returns_dash(self):
        self.assertEqual(fingerprint.resolve_base(self.repo), "-")

    def test_compute_fingerprint_works(self):
        self.write("base.txt", b"content before first commit\n")
        fp = fingerprint.compute_fingerprint(self.repo, ["base.txt"])
        self.assertRegex(fp, _FINGERPRINT_RE)
        self.assertEqual(
            fp, fingerprint.compute_fingerprint(self.repo, ["base.txt"]))


class TestNonGitDir(TempDirCase):
    """非 git 目录（无 .git）：resolve_base / compute_fingerprint 均抛错。"""

    def test_resolve_base_raises(self):
        with self.assertRaises(fingerprint.FingerprintError) as ctx:
            fingerprint.resolve_base(self.repo)
        # 消息含 returncode 与 stderr 摘要
        self.assertIn("returncode", str(ctx.exception))

    def test_compute_fingerprint_raises(self):
        with self.assertRaises(fingerprint.FingerprintError):
            fingerprint.compute_fingerprint(self.repo, ["whatever.txt"])


if __name__ == "__main__":
    unittest.main()
