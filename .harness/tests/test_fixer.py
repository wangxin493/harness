"""fixer 单元测试。"""

import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from . import _setup  # noqa: F401

from lib.fixer import Fixer  # noqa: E402


RULES_YAML = textwrap.dedent("""\
architecture:
  layers:
    - name: component
      paths: ["src/components/"]
      can_import: ["hook", "service", "type"]
    - name: hook
      paths: ["src/hooks/"]
      can_import: ["service", "type"]
    - name: service
      paths: ["src/api/"]
      can_import: ["type"]
    - name: type
      paths: ["src/types/"]
      can_import: []
imports:
  forbidden_imports:
    - "@/services"
    - "@/api/mockApi"
  rewrites:
    "@/services": "@/api"
""")


class FixerFixture:
    def __init__(self, init_git: bool = False):
        self.root = Path(tempfile.mkdtemp(prefix="harness-fixer-")).resolve()
        (self.root / ".harness").mkdir()
        (self.root / ".harness" / "rules.yaml").write_text(RULES_YAML, encoding="utf-8")
        (self.root / "src").mkdir()
        if init_git:
            self._git_init()

    def _git_init(self):
        # 仅在临时目录内做本地 git，避免读全局 commit hook / GPG 签名设置
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"],
                       cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "test"],
                       cwd=self.root, check=True)
        subprocess.run(["git", "config", "commit.gpgsign", "false"],
                       cwd=self.root, check=True)

    def write(self, rel: str, content: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def git_commit_all(self, msg: str = "init"):
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", msg],
                       cwd=self.root, check=True)

    def stash_list(self) -> str:
        out = subprocess.run(
            ["git", "stash", "list"],
            cwd=self.root, check=True, capture_output=True, text=True,
        )
        return out.stdout

    def fixer(self) -> Fixer:
        return Fixer(project_dir=self.root)

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


class TestFixer(unittest.TestCase):
    def setUp(self):
        self.fx = FixerFixture()

    def tearDown(self):
        self.fx.cleanup()

    # ------------------------------------------------------------- patch 生成

    def test_import_forbidden_produces_patch(self):
        self.fx.write(
            "src/components/A.tsx",
            "import { x } from '@/services/legacy';\n"
            "export const A = () => <div/>;\n",
        )
        result = self.fx.fixer().fix_file("src/components/A.tsx")
        self.assertEqual(len(result.patches), 1)
        patch = result.patches[0]
        self.assertEqual(patch.file, "src/components/A.tsx")
        # diff 头格式
        self.assertIn("--- a/src/components/A.tsx", patch.diff)
        self.assertIn("+++ b/src/components/A.tsx", patch.diff)
        # 替换语义
        self.assertIn("-import { x } from '@/services/legacy';", patch.diff)
        self.assertIn("+import { x } from '@/api/legacy';", patch.diff)
        self.assertIn("import-forbidden", patch.rule_ids)
        # dry-run 默认未应用
        self.assertFalse(result.applied)
        # 该 issue 已被 patch 覆盖，instructions 中不应再出现 import-forbidden
        self.assertFalse(any(i.rule_id == "import-forbidden"
                             for i in result.instructions))

    def test_multiple_forbidden_imports_in_single_patch(self):
        self.fx.write(
            "src/components/A.tsx",
            "import { x } from '@/services/legacy';\n"
            "import { y } from '@/services/other/util';\n"
            "export const A = () => <div/>;\n",
        )
        result = self.fx.fixer().fix_file("src/components/A.tsx")
        self.assertEqual(len(result.patches), 1)
        diff = result.patches[0].diff
        self.assertIn("+import { x } from '@/api/legacy';", diff)
        self.assertIn("+import { y } from '@/api/other/util';", diff)

    def test_mockapi_forbidden_only_instruction(self):
        # @/api/mockApi 不在可机械替换映射里 → 不出 patch，只出 instruction
        self.fx.write(
            "src/api/itemService.ts",
            "import { y } from '@/api/mockApi/foo';\nexport const x = y;\n",
        )
        result = self.fx.fixer().fix_file("src/api/itemService.ts")
        self.assertEqual(result.patches, [])
        rule_ids = [i.rule_id for i in result.instructions]
        self.assertIn("import-forbidden", rule_ids)

    def test_mockapi_within_services_hunk_still_instruction(self):
        """回归：mockApi 行紧挨 services 行时也必须出 instruction。

        早期实现用 unified_diff hunk 区间（含 n=3 上下文）判断 covered，
        会把 @/api/mockApi 误算成"已被 patch 修复"，于是 instructions 漏掉它。
        现在 covered 改用真实改写过的行号集合，这种相邻情形必须仍出 instruction。
        """
        self.fx.write(
            "src/api/messy.ts",
            "import { a } from '@/services/legacy';\n"
            "import { b } from '@/api/mockApi/foo';\n"
            "export const x = a;\n",
        )
        result = self.fx.fixer().fix_file("src/api/messy.ts")
        # services 这条出 patch
        self.assertEqual(len(result.patches), 1)
        # mockApi 这条仍必须以 instruction 形式暴露
        ins_lines = [(i.rule_id, i.line) for i in result.instructions
                     if i.rule_id == "import-forbidden"]
        self.assertEqual(ins_lines, [("import-forbidden", 2)],
                         f"expected mockApi line 2 in instructions, got {ins_lines}")

    # ----------------------------------------------------------- 架构错误处理

    def test_arch_error_only_instruction_no_patch(self):
        # service 层 import component 层：架构违规，不出 patch
        self.fx.write(
            "src/api/itemService.ts",
            "import { Btn } from '@/components/Btn';\nexport const x = Btn;\n",
        )
        result = self.fx.fixer().fix_file("src/api/itemService.ts")
        self.assertEqual(result.patches, [])
        rule_ids = [i.rule_id for i in result.instructions]
        self.assertIn("arch-service-import", rule_ids)

    # ------------------------------------------------------------- dry-run

    def test_dry_run_does_not_modify_file(self):
        original = (
            "import { x } from '@/services/legacy';\n"
            "export const A = () => <div/>;\n"
        )
        path = self.fx.write("src/components/A.tsx", original)
        result = self.fx.fixer().fix_file("src/components/A.tsx")
        self.assertEqual(len(result.patches), 1)
        self.assertFalse(result.applied)
        # 文件内容没变
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    # -------------------------------------------------------------- --apply

    def test_apply_rejects_non_git_repo(self):
        self.fx.write(
            "src/components/A.tsx",
            "import { x } from '@/services/legacy';\nexport const A = () => <div/>;\n",
        )
        with self.assertRaises(RuntimeError) as ctx:
            self.fx.fixer().fix_file("src/components/A.tsx", apply=True)
        self.assertIn("不是 git 仓库", str(ctx.exception))


class TestFixerWithGit(unittest.TestCase):
    """需要真实 git 的用例独立分组，方便 CI 上按需跳过。"""

    def setUp(self):
        self.fx = FixerFixture(init_git=True)

    def tearDown(self):
        self.fx.cleanup()

    def test_apply_in_clean_repo_changes_file(self):
        original = (
            "import { x } from '@/services/legacy';\n"
            "export const A = () => <div/>;\n"
        )
        path = self.fx.write("src/components/A.tsx", original)
        self.fx.git_commit_all("init")  # 工作树 clean

        result = self.fx.fixer().fix_file("src/components/A.tsx", apply=True)

        self.assertTrue(result.applied)
        # clean 状态下 stash create 返回空，无需 store
        self.assertIsNone(result.stash_ref)
        # 文件内容已被替换
        new_content = path.read_text(encoding="utf-8")
        self.assertIn("@/api/legacy", new_content)
        self.assertNotIn("@/services/legacy", new_content)

    def test_apply_in_dirty_repo_creates_stash_backup(self):
        # 先 commit 一份"基线"，再修改文件让工作树变脏
        path = self.fx.write(
            "src/components/A.tsx",
            "import { x } from '@/clean/path';\nexport const A = () => <div/>;\n",
        )
        self.fx.git_commit_all("baseline")
        path.write_text(
            "import { x } from '@/services/legacy';\nexport const A = () => <div/>;\n",
            encoding="utf-8",
        )

        result = self.fx.fixer().fix_file("src/components/A.tsx", apply=True)

        self.assertTrue(result.applied)
        # 工作树有未提交修改 → stash create 产生备份 sha
        self.assertIsNotNone(result.stash_ref)
        self.assertRegex(result.stash_ref, r"^[0-9a-f]{40}$")
        # stash list 中能看到 harness-fix-backup-* 条目
        self.assertIn("harness-fix-backup-", self.fx.stash_list())
        # 文件最终被替换
        self.assertIn("@/api/legacy", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
