"""harness setup CLI 集成测试（B6: one-command-setup）。

覆盖：
- --agent 必填；缺失 → click 报错（exit 2）
- --agent 非法值 → click 报错
- 全新项目跑 setup --agent claude → init/install/scan 三段全成功
- --json 输出聚合三段结果
- fail-fast：install 阶段失败时 scan 不应再跑
- 重跑 setup（rules.yaml 已存在）→ 默认非交互覆盖，不卡住
- --interactive 模式：rules.yaml 已存在时回车默认 No → 退出 1
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from . import _setup  # noqa: F401

from lib import cli as cli_module  # noqa: E402


class _SetupFixture:
    """完整 React+TS 项目骨架（不含已有的 .harness 目录）。"""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="harness-setup-cli-")).resolve()
        (self.root / "tsconfig.json").write_text(json.dumps({
            "compilerOptions": {
                "baseUrl": ".",
                "paths": {"@/*": ["src/*"]},
            },
        }), encoding="utf-8")
        (self.root / "package.json").write_text(json.dumps({
            "name": "fake-app",
            "dependencies": {"react": "^18.0.0"},
            "devDependencies": {"typescript": "^5"},
        }), encoding="utf-8")
        # 5 layer 全到位，避免抛 conflict
        for sub, file_, body in [
            ("components", "Foo.tsx", "export const Foo = () => null;"),
            ("hooks", "useFoo.ts", "export const useFoo = () => 1;"),
            ("api", "userService.ts", "export const userService = () => 1;"),
            ("types", "User.ts", "export type User = {};"),
        ]:
            (self.root / "src" / sub).mkdir(parents=True, exist_ok=True)
            (self.root / "src" / sub / file_).write_text(body, encoding="utf-8")

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


class _SetupCliBase(unittest.TestCase):

    def setUp(self) -> None:
        self.fx = _SetupFixture()
        self.runner = CliRunner()
        self._old_env = os.environ.get("HARNESS_PROJECT_DIR")
        os.environ["HARNESS_PROJECT_DIR"] = str(self.fx.root)

    def tearDown(self) -> None:
        if self._old_env is None:
            os.environ.pop("HARNESS_PROJECT_DIR", None)
        else:
            os.environ["HARNESS_PROJECT_DIR"] = self._old_env
        self.fx.cleanup()


class TestSetupArgValidation(_SetupCliBase):

    def test_agent_required(self) -> None:
        result = self.runner.invoke(cli_module.cli, ["setup"])
        self.assertNotEqual(result.exit_code, 0)
        # click 把 required option 缺失的错误写到 stderr
        self.assertIn("--agent", result.output)

    def test_agent_invalid_value(self) -> None:
        result = self.runner.invoke(
            cli_module.cli, ["setup", "--agent", "vscode"],
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("vscode", result.output)


class TestSetupHappyPath(_SetupCliBase):

    def test_setup_runs_three_stages(self) -> None:
        result = self.runner.invoke(
            cli_module.cli, ["setup", "--agent", "claude"],
        )
        self.assertEqual(result.exit_code, 0, result.output)
        # 三个阶段标题都应在输出
        self.assertIn("[1/3] init", result.output)
        self.assertIn("[2/3] install", result.output)
        self.assertIn("[3/3] scan", result.output)
        # 末尾总结
        self.assertIn("Harness 接入完成", result.output)
        # 关键产物
        rules = self.fx.root / ".harness" / "rules.yaml"
        mode = self.fx.root / ".harness" / "mode-config.json"
        claude_md = self.fx.root / ".harness" / "generated" / "claude.md"
        self.assertTrue(rules.exists(), "rules.yaml 应该被 init 写出")
        self.assertTrue(mode.exists(), "mode-config.json 应该被 init 写出")
        self.assertTrue(claude_md.exists(), "scan 应该自动 generate")

    def test_setup_json_aggregates_stages(self) -> None:
        result = self.runner.invoke(
            cli_module.cli, ["setup", "--agent", "claude", "--json"],
        )
        self.assertEqual(result.exit_code, 0, result.output)
        # 输出应是合法 JSON
        data = json.loads(result.output)
        self.assertTrue(data["ok"])
        self.assertEqual(data["agent"], "claude")
        self.assertEqual(len(data["stages"]), 3)
        self.assertEqual(
            [s["stage"] for s in data["stages"]],
            ["init", "install", "scan"],
        )
        for s in data["stages"]:
            self.assertTrue(s["ok"], f"{s['stage']} 应该成功")


class TestSetupFailFast(_SetupCliBase):

    def test_install_failure_skips_scan(self) -> None:
        """install 阶段抛异常 → scan 不应被调用，退出 1。"""
        original_get_installer = cli_module.get_installer

        def boom(*args, **kwargs):
            raise RuntimeError("install boom")

        with patch.object(cli_module, "get_installer", boom):
            with patch.object(
                cli_module, "IncrementalScanner",
                side_effect=AssertionError("scan 不应被调用"),
            ):
                result = self.runner.invoke(
                    cli_module.cli, ["setup", "--agent", "claude"],
                )

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("install", result.output)
        self.assertIn("boom", result.output)
        # init 既然已经跑过，rules.yaml 应该有
        self.assertTrue(
            (self.fx.root / ".harness" / "rules.yaml").exists(),
        )

        # cleanup（防止 patch 泄露）
        self.assertIs(cli_module.get_installer, original_get_installer)

    def test_install_failure_json_output(self) -> None:
        with patch.object(
            cli_module, "get_installer",
            side_effect=RuntimeError("install boom"),
        ):
            result = self.runner.invoke(
                cli_module.cli, ["setup", "--agent", "claude", "--json"],
            )
        self.assertEqual(result.exit_code, 1, result.output)
        data = json.loads(result.output)
        self.assertFalse(data["ok"])
        self.assertEqual(data["failed_stage"], "install")
        # init 应该 ok，install 应该 fail
        stages = {s["stage"]: s for s in data["stages"]}
        self.assertTrue(stages["init"]["ok"])
        self.assertFalse(stages["install"]["ok"])
        self.assertNotIn("scan", stages)


class TestSetupReentrancy(_SetupCliBase):

    def test_rerun_setup_requires_force(self) -> None:
        """rules.yaml 已存在时,非交互模式默认拒绝覆盖，避免误删人工配置。"""
        # 第一次 setup
        first = self.runner.invoke(
            cli_module.cli, ["setup", "--agent", "claude"],
        )
        self.assertEqual(first.exit_code, 0, first.output)

        # 第二次 setup（rules.yaml 已存在），不加 --force 应阻止覆盖
        second = self.runner.invoke(
            cli_module.cli, ["setup", "--agent", "claude"],
        )
        self.assertEqual(second.exit_code, 1, second.output)
        self.assertIn("需加 --force", second.output)

    def test_rerun_setup_with_force_overwrites(self) -> None:
        """rules.yaml 已存在时,显式 --force 才允许非交互覆盖。"""
        first = self.runner.invoke(
            cli_module.cli, ["setup", "--agent", "claude"],
        )
        self.assertEqual(first.exit_code, 0, first.output)

        second = self.runner.invoke(
            cli_module.cli, ["setup", "--agent", "claude", "--force"],
        )
        self.assertEqual(second.exit_code, 0, second.output)
        self.assertIn("Harness 接入完成", second.output)


class TestSetupInteractive(_SetupCliBase):

    def test_interactive_overwrite_declined_exits(self) -> None:
        """--interactive + rules.yaml 已存在 + 用户回车默认 No → 退出 1。"""
        # 先建占位 rules.yaml
        (self.fx.root / ".harness").mkdir(parents=True, exist_ok=True)
        rules_path = self.fx.root / ".harness" / "rules.yaml"
        rules_path.write_text("# placeholder\nfoo: bar\n", encoding="utf-8")

        result = self.runner.invoke(
            cli_module.cli,
            ["setup", "--agent", "claude", "--interactive"],
            input="\n",
        )
        # 用户拒绝覆盖 → 退出
        self.assertNotEqual(result.exit_code, 0)
        # rules.yaml 应保持原样
        self.assertIn("foo: bar", rules_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
