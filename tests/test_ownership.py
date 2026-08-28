#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime.ownership 单元测试（v2 工作块 B2.1）。

运行：
    python3 -m unittest tests.test_ownership -v

git_touched_files 的用例需要真实 git 仓库 fixture（tempdir 内
git init + config + add/commit，subprocess 调 git）；环境无 git
可执行时自动 skipTest（CI 有 git，正常执行）。
"""

import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "glm-conductor"))
from runtime import ownership

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


# —— normalize_path ——

class TestNormalizePath(unittest.TestCase):

    def test_backslashes_become_slashes(self):
        self.assertEqual(ownership.normalize_path("src\\auth\\x.ts"),
                         "src/auth/x.ts")

    def test_strips_leading_dot_slash(self):
        self.assertEqual(ownership.normalize_path("./src/x.ts"), "src/x.ts")
        self.assertEqual(ownership.normalize_path("././x.ts"), "x.ts")

    def test_keeps_parent_refs(self):
        self.assertEqual(ownership.normalize_path("../x.ts"), "../x.ts")
        self.assertEqual(ownership.normalize_path("a/../b.ts"), "a/../b.ts")

    def test_empty_string_unchanged(self):
        self.assertEqual(ownership.normalize_path(""), "")

    def test_drive_letter_untouched(self):
        # Windows 盘符不做特殊处理，只做同样的反斜杠归一
        self.assertEqual(ownership.normalize_path("C:/x"), "C:/x")
        self.assertEqual(ownership.normalize_path("C:\\x"), "C:/x")

    def test_non_string_raises(self):
        with self.assertRaises(ownership.OwnershipError):
            ownership.normalize_path(123)


# —— compile_pattern（含规格锁定的三个锚定例子的正则级断言） ——

class TestCompilePattern(unittest.TestCase):

    def test_returns_compiled_regex(self):
        self.assertIsInstance(ownership.compile_pattern("src/auth.ts"),
                              re.Pattern)

    def test_dir_prefix_form_matches_self_and_descendants(self):
        # 锚定例 1："src/auth/**" 匹配 "src/auth"（零段）、"src/auth/a.ts"、
        # "src/auth/x/y.ts"（match 语义，不要求 pattern 字符串形态）
        pattern = ownership.compile_pattern("src/auth/**")
        self.assertTrue(pattern.match("src/auth"))
        self.assertTrue(pattern.match("src/auth/a.ts"))
        self.assertTrue(pattern.match("src/auth/x/y.ts"))

    def test_mid_globstar_zero_one_many_segments(self):
        # 锚定例 2："a/**/b" 匹配 "a/b"（零段）、"a/x/b"（一段）、
        # "a/x/y/b"（多段）
        pattern = ownership.compile_pattern("a/**/b")
        self.assertTrue(pattern.match("a/b"))
        self.assertTrue(pattern.match("a/x/b"))
        self.assertTrue(pattern.match("a/x/y/b"))
        # 不越过两侧锚点
        self.assertFalse(pattern.match("a"))
        self.assertFalse(pattern.match("a/b/c"))

    def test_leading_globstar(self):
        # 锚定例 2 的镜像："**/b" 匹配 "b"（零段）、"x/b"、"x/y/b"
        pattern = ownership.compile_pattern("**/b")
        self.assertTrue(pattern.match("b"))
        self.assertTrue(pattern.match("x/b"))
        self.assertTrue(pattern.match("x/y/b"))

    def test_bare_globstar_matches_anything(self):
        pattern = ownership.compile_pattern("**")
        self.assertTrue(pattern.match("a"))
        self.assertTrue(pattern.match("a/b/c"))

    def test_adjacent_globstars_are_legal_and_folded(self):
        # 相邻双星视为冗余合法（允许），等价单个 "**"
        self.assertTrue(ownership.compile_pattern("a/**/**/b").match("a/b"))
        self.assertTrue(
            ownership.compile_pattern("a/**/**/b").match("a/x/y/b"))
        self.assertTrue(ownership.compile_pattern("**/**").match("q/r"))

    def test_star_does_not_cross_slashes(self):
        self.assertTrue(ownership.compile_pattern("*.py").match("x.py"))
        self.assertFalse(ownership.compile_pattern("*.py").match("sub/x.py"))
        self.assertTrue(ownership.compile_pattern("src/*.ts").match("src/a.ts"))
        self.assertFalse(
            ownership.compile_pattern("src/*.ts").match("src/sub/a.ts"))

    def test_question_mark_matches_single_char(self):
        self.assertTrue(ownership.compile_pattern("f?.py").match("f1.py"))
        self.assertFalse(ownership.compile_pattern("f?.py").match("f12.py"))
        self.assertFalse(
            ownership.compile_pattern("f?.py").match("sub/f1.py"))

    def test_literal_chars_are_escaped(self):
        self.assertTrue(ownership.compile_pattern("a.b").match("a.b"))
        self.assertFalse(ownership.compile_pattern("a.b").match("aXb"))
        # "[1]" 是字面量而非字符类
        self.assertTrue(ownership.compile_pattern("a[1].ts").match("a[1].ts"))
        self.assertFalse(ownership.compile_pattern("a[1].ts").match("a1.ts"))

    def test_invalid_patterns_raise_with_pattern_in_message(self):
        for pattern in ("a//b", "/lead", "trail/", ""):
            with self.subTest(pattern=pattern):
                with self.assertRaises(ownership.OwnershipError) as ctx:
                    ownership.compile_pattern(pattern)
                self.assertIn(pattern, str(ctx.exception))

    def test_non_string_pattern_raises(self):
        with self.assertRaises(ownership.OwnershipError):
            ownership.compile_pattern(None)


# —— match_path ——

class TestMatchPath(unittest.TestCase):

    def test_exact_hit_and_miss(self):
        self.assertTrue(ownership.match_path("src/auth.ts", ["src/auth.ts"]))
        self.assertFalse(ownership.match_path("src/other.ts", ["src/auth.ts"]))
        # 全路径锚定：多出字符不命中
        self.assertFalse(
            ownership.match_path("src/auth.tsx", ["src/auth.ts"]))

    def test_directory_prefix_matches_by_segment_not_by_string(self):
        # 关键反例：目录前缀（dir/** 形式）按"段"匹配——
        # 覆盖目录内子文件，但不命中以同前缀字符串开头的兄弟文件
        self.assertTrue(
            ownership.match_path("src/auth/x.ts", ["src/auth/**"]))
        self.assertFalse(
            ownership.match_path("src/authentication.ts", ["src/auth/**"]))
        self.assertFalse(
            ownership.match_path("src/authx/a.ts", ["src/auth/**"]))

    def test_bare_path_is_exact_and_dir_prefix_form(self):
        # 裸路径（不含 glob）= exact file + directory prefix 双语义：
        # "src/auth" 等价 "src/auth/**"（§11 directory prefix 形式），
        # 仍按路径段边界匹配——不命中同前缀字符串开头的兄弟文件
        self.assertTrue(ownership.match_path("src/auth", ["src/auth"]))
        self.assertTrue(ownership.match_path("src/auth/x.ts", ["src/auth"]))
        self.assertTrue(ownership.match_path("src/auth/x/y.ts", ["src/auth"]))
        self.assertFalse(
            ownership.match_path("src/authentication.ts", ["src/auth"]))
        self.assertFalse(
            ownership.match_path("src/authx/a.ts", ["src/auth"]))

    def test_dir_prefix_three_path_shapes(self):
        patterns = ["src/auth/**"]
        self.assertTrue(ownership.match_path("src/auth", patterns))
        self.assertTrue(ownership.match_path("src/auth/a.ts", patterns))
        self.assertTrue(ownership.match_path("src/auth/x/y.ts", patterns))

    def test_mid_globstar_three_depths(self):
        patterns = ["a/**/b"]
        self.assertTrue(ownership.match_path("a/b", patterns))
        self.assertTrue(ownership.match_path("a/x/b", patterns))
        self.assertTrue(ownership.match_path("a/x/y/b", patterns))

    def test_star_pattern_does_not_cross_segments(self):
        self.assertTrue(ownership.match_path("x.py", ["*.py"]))
        self.assertFalse(ownership.match_path("sub/x.py", ["*.py"]))

    def test_question_mark_single_char(self):
        self.assertTrue(ownership.match_path("f1.py", ["f?.py"]))
        self.assertFalse(ownership.match_path("f12.py", ["f?.py"]))

    def test_backslash_path_is_normalized(self):
        # 反斜杠输入与正斜杠等价（path 侧归一）
        self.assertTrue(
            ownership.match_path("src\\auth\\x.ts", ["src/auth/**"]))
        # pattern 侧同样先归一
        self.assertTrue(
            ownership.match_path("src/auth/x.ts", ["src\\auth\\**"]))

    def test_leading_dot_slash_is_stripped(self):
        self.assertTrue(
            ownership.match_path("./src/auth/x.ts", ["src/auth/**"]))
        self.assertTrue(
            ownership.match_path("src/auth", ["./src/auth/**"]))

    def test_any_pattern_hit(self):
        patterns = ["tests/**", "src/auth.ts"]
        self.assertTrue(ownership.match_path("tests/x.py", patterns))
        self.assertTrue(ownership.match_path("src/auth.ts", patterns))
        self.assertFalse(ownership.match_path("src/other.ts", patterns))

    def test_empty_patterns_is_false(self):
        self.assertFalse(ownership.match_path("anything.txt", []))
        self.assertFalse(ownership.match_path("anything.txt", ()))

    def test_invalid_pattern_raises_through(self):
        with self.assertRaises(ownership.OwnershipError):
            ownership.match_path("a/b", ["ok/**", "bad//pattern"])


# —— classify_paths ——

class TestClassifyPaths(unittest.TestCase):

    def test_bucketing_order_and_normalization(self):
        owned, out = ownership.classify_paths(
            ["src/auth/x.ts", "hack.txt", "src\\auth\\deep\\y.ts",
             "tests/t.py", "./src/auth/z.ts"],
            ["src/auth/**", "tests/**"])
        # owned 桶：命中项，保持输入顺序，均为归一后路径
        self.assertEqual(
            owned, ["src/auth/x.ts", "src/auth/deep/y.ts", "tests/t.py",
                    "src/auth/z.ts"])
        # out_of_scope 桶：未命中项，保持输入顺序
        self.assertEqual(out, ["hack.txt"])

    def test_empty_patterns_everything_out_of_scope(self):
        owned, out = ownership.classify_paths(
            ["b.ts", "a.ts"], [])
        self.assertEqual(owned, [])
        self.assertEqual(out, ["b.ts", "a.ts"])

    def test_invalid_owned_pattern_raises(self):
        with self.assertRaises(ownership.OwnershipError):
            ownership.classify_paths(["a.ts"], ["a//b"])


# —— git_touched_files（真实 git 仓库 fixture） ——

@unittest.skipUnless(shutil.which("git"), "环境无 git 可执行，跳过 git fixture 测试")
class TestGitTouchedFiles(unittest.TestCase):
    """tempdir 内建真实 git 仓库，构造 M/A/??/R/D 五类状态。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._force_cleanup)
        self.repo = Path(self._tmp.name)
        run_git(self.repo, "init")
        run_git(self.repo, "config", "user.email", "gate@example.com")
        run_git(self.repo, "config", "user.name", "Ownership Gate")
        # 固定换行行为，避免全局 autocrlf 干扰状态判定
        run_git(self.repo, "config", "core.autocrlf", "false")
        # 初始提交：作为 M / D / R 三类状态的基底
        self.write("base.txt", b"v1\n")
        self.write("del.txt", b"delete me\n")
        self.write("mv-old.txt", b"rename me\n")
        run_git(self.repo, "add", ".")
        run_git(self.repo, "commit", "-m", "init")

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
        target = self.repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def test_five_statuses_exact(self):
        # 1) M：修改已跟踪文件（未暂存）
        self.write("base.txt", b"v2\n")
        # 2) A：新增并暂存
        self.write("staged.txt", b"new\n")
        run_git(self.repo, "add", "staged.txt")
        # 3) ??: 未跟踪
        self.write("untracked.txt", b"?\n")
        # 4) R：git mv 暂存重命名
        run_git(self.repo, "mv", "mv-old.txt", "mv-new.txt")
        # 5) D：删除已跟踪文件（未暂存）
        (self.repo / "del.txt").unlink()

        touched = ownership.git_touched_files(self.repo)
        # 精确断言：六个路径（rename 原路径与新路径双侧都计入）
        self.assertEqual(
            sorted(touched),
            sorted(["base.txt",       # M
                    "staged.txt",     # A
                    "untracked.txt",  # ??
                    "mv-new.txt",     # R 新路径
                    "mv-old.txt",     # R 原路径
                    "del.txt"]))      # D

    def test_rename_both_sides_counted_as_owned(self):
        # rename 双侧与 ownership 的配合：声明只含新路径时，
        # 原路径仍在 touched 里（由 Layer A 决定如何处置）
        run_git(self.repo, "mv", "mv-old.txt", "mv-new.txt")
        owned, out = ownership.classify_paths(
            ownership.git_touched_files(self.repo), ["mv-new.txt"])
        self.assertEqual(owned, ["mv-new.txt"])
        self.assertEqual(out, ["mv-old.txt"])

    def test_spaces_and_non_ascii_filenames(self):
        # -z NUL 分隔 + core.quotepath=false：空格与非 ASCII 文件名原样返回
        self.write("with space.txt", b"x\n")
        self.write("目录/中文 文件.txt", b"x\n")
        touched = ownership.git_touched_files(self.repo)
        self.assertIn("with space.txt", touched)
        self.assertIn("目录/中文 文件.txt", touched)

    def test_runtime_state_dir_exempt(self):
        # .glm-conductor/ 运行时状态目录豁免：编排器自身账本（state.json /
        # events.jsonl / checkpoint）不算用户仓库改动——否则创建 state.json
        # 本身就会被 Layer A 判越界（自指拦截）
        self.write("base.txt", b"v2\n")
        self.write(".glm-conductor/tasks/t-1/state.json", b"{}\n")
        self.write(".glm-conductor/tasks/t-1/events.jsonl", b"\n")
        touched = ownership.git_touched_files(self.repo)
        self.assertEqual(touched, ["base.txt"])

    def test_clean_repo_returns_empty_list(self):
        self.assertEqual(ownership.git_touched_files(self.repo), [])


class TestGitTouchedFilesFailure(unittest.TestCase):

    def test_non_repo_dir_raises_ownership_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ownership.OwnershipError) as ctx:
                ownership.git_touched_files(tmp)
            # 消息含 returncode 与 stderr 摘要
            self.assertIn("returncode", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
