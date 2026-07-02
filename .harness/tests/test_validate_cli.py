"""validator 截断行为测试（P1 #11）。

validator 本身保持纯函数（已有 test_validator.py 覆盖）；这里只测 CLI 层的截断
逻辑：人读输出最多 N 条 + 提示，--json 永远完整。
"""

import json
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path

from click.testing import CliRunner

from . import _setup  # noqa: F401

from lib import cli as cli_module  # noqa: E402


# 用一个会产出 7 条 issue 的源文件：6 个 forbidden + 1 个 arch（service 层）
SOURCE_WITH_MANY_ISSUES = textwrap.dedent("""\
    import { a } from '@/services/legacy';
    import { b } from '@/services/foo';
    import { c } from '@/services/bar';
    import { d } from '@/api/mockApi/x';
    import { e } from '@/api/mockApi/y';
    import { f } from '@/api/mockApi/z';
    import { Btn } from '@/components/Btn';
    export const x = a;
""")


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
""")


class TruncateFixture:
    def __init__(self, max_issues=None):
        self.root = Path(tempfile.mkdtemp(prefix="harness-trunc-")).resolve()
        (self.root / ".harness").mkdir()
        rules = RULES_YAML
        if max_issues is not None:
            rules += f"\nvalidation:\n  max_issues_per_file: {max_issues}\n"
        (self.root / ".harness" / "rules.yaml").write_text(rules, encoding="utf-8")
        target = self.root / "src" / "api" / "messy.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(SOURCE_WITH_MANY_ISSUES, encoding="utf-8")

    def runner(self):
        return CliRunner()

    def invoke(self, *args):
        runner = self.runner()
        # CLI 通过 HARNESS_PROJECT_DIR 决定 project_dir
        return runner.invoke(
            cli_module.cli,
            list(args),
            env={"HARNESS_PROJECT_DIR": str(self.root)},
        )

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


class TestValidateTruncation(unittest.TestCase):
    def setUp(self):
        self.fx = TruncateFixture()

    def tearDown(self):
        self.fx.cleanup()

    def test_human_output_truncates_at_default_5(self):
        result = self.fx.invoke("validate", "src/api/messy.ts")
        # 退出码 2（有 error）
        self.assertEqual(result.exit_code, 2, msg=result.output)
        out = result.output
        # 总数仍是真实数（>5）
        self.assertRegex(out, r"发现 [6-9]\d* 个问题|发现 [6-9] 个问题")
        # 默认只展示 5 条
        # 行数：一条 issue 至少 1 行，含 suggestion 是 2 行；用 "[ERROR]" 计数
        bracket_count = out.count("[ERROR]")
        self.assertEqual(bracket_count, 5,
                         f"expected 5 issue lines in human output, got {bracket_count}\n{out}")
        # 截断提示
        self.assertIn("已截断", out)
        self.assertIn("--json", out)

    def test_json_output_is_complete_and_not_truncated(self):
        result = self.fx.invoke("validate", "--json", "src/api/messy.ts")
        self.assertEqual(result.exit_code, 2, msg=result.output)
        data = json.loads(result.output)
        self.assertGreater(len(data), 5,
                           f"expected >5 issues in JSON, got {len(data)}")
        # JSON 输出绝不带"已截断"提示
        self.assertNotIn("已截断", result.output)

    def test_few_issues_no_truncation_message(self):
        # 写一个只有 2 条 issue 的文件
        clean_target = self.fx.root / "src" / "api" / "small.ts"
        clean_target.write_text(
            "import { a } from '@/services/legacy';\n"
            "import { Btn } from '@/components/Btn';\n"
            "export const x = a;\n",
            encoding="utf-8",
        )
        result = self.fx.invoke("validate", "src/api/small.ts")
        self.assertEqual(result.exit_code, 2)
        self.assertNotIn("已截断", result.output)
        # 两条都展示
        self.assertEqual(result.output.count("[ERROR]"), 2)

    def test_clean_file_no_truncation(self):
        clean = self.fx.root / "src" / "api" / "clean.ts"
        clean.write_text("export const x = 1;\n", encoding="utf-8")
        result = self.fx.invoke("validate", "src/api/clean.ts")
        self.assertEqual(result.exit_code, 0)
        self.assertIn("验证通过", result.output)
        self.assertNotIn("已截断", result.output)

    def test_mode_off_shows_warning_not_empty_ok(self):
        """C10: mode=off 时人读输出是明确警告，不是"验证通过"。"""
        import json
        root = Path(tempfile.mkdtemp(prefix="harness-off-")).resolve()
        try:
            (root / ".harness").mkdir()
            (root / ".harness" / "mode-config.json").write_text(
                json.dumps({"mode": "off"}), encoding="utf-8")
            (root / ".harness" / "rules.yaml").write_text(
                RULES_YAML, encoding="utf-8")
            (root / "src" / "api").mkdir(parents=True)
            (root / "src" / "api" / "x.ts").write_text("export const x = 1;\n")
            runner = CliRunner()
            result = runner.invoke(
                cli_module.cli, ["validate", "src/api/x.ts"],
                catch_exceptions=False,
                env={"HARNESS_PROJECT_DIR": str(root)},
            )
            self.assertIn("mode=off", result.output)
            self.assertNotIn("验证通过", result.output)
        finally:
            shutil.rmtree(root, ignore_errors=True)


class TestValidateTruncationConfigurable(unittest.TestCase):
    def test_rules_yaml_override_to_3(self):
        fx = TruncateFixture(max_issues=3)
        try:
            result = fx.invoke("validate", "src/api/messy.ts")
            self.assertEqual(result.exit_code, 2, msg=result.output)
            self.assertEqual(result.output.count("[ERROR]"), 3)
            self.assertIn("已截断", result.output)
        finally:
            fx.cleanup()

    def test_rules_yaml_override_to_large_disables_truncation(self):
        fx = TruncateFixture(max_issues=100)
        try:
            result = fx.invoke("validate", "src/api/messy.ts")
            self.assertEqual(result.exit_code, 2, msg=result.output)
            # 全部 7 条都展示
            self.assertEqual(result.output.count("[ERROR]"), 7)
            self.assertNotIn("已截断", result.output)
        finally:
            fx.cleanup()

    def test_invalid_max_issues_falls_back_to_default(self):
        # 配置成 0 / 负数 / 非 int → 回退到默认 5
        for bad in (0, -1, "abc"):
            fx = TruncateFixture(max_issues=bad)
            try:
                result = fx.invoke("validate", "src/api/messy.ts")
                self.assertEqual(result.exit_code, 2)
                self.assertEqual(
                    result.output.count("[ERROR]"), 5,
                    f"bad value {bad!r} should fall back to default 5",
                )
            finally:
                fx.cleanup()


class TestValidateLoaderHelper(unittest.TestCase):
    """直接测 _load_max_issues_per_file 工具函数。"""

    def test_no_rules_yaml_returns_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / ".harness").mkdir()
            self.assertEqual(
                cli_module._load_max_issues_per_file(tmp_path),
                cli_module._DEFAULT_MAX_ISSUES,
            )

    def test_corrupt_yaml_returns_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / ".harness").mkdir()
            (tmp_path / ".harness" / "rules.yaml").write_text(
                "::: not yaml :::", encoding="utf-8")
            self.assertEqual(
                cli_module._load_max_issues_per_file(tmp_path),
                cli_module._DEFAULT_MAX_ISSUES,
            )

    def test_valid_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / ".harness").mkdir()
            (tmp_path / ".harness" / "rules.yaml").write_text(
                "validation:\n  max_issues_per_file: 8\n", encoding="utf-8")
            self.assertEqual(
                cli_module._load_max_issues_per_file(tmp_path), 8)


if __name__ == "__main__":
    unittest.main()
