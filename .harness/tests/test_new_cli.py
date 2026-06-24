"""harness new CLI 集成测试。"""

import json
import os
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path

from click.testing import CliRunner

from . import _setup  # noqa: F401

from lib import cli as cli_module  # noqa: E402


RULES_YAML = textwrap.dedent("""\
architecture:
  layers:
    - name: component
      paths: ["src/pages/", "src/components/"]
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
""")


class _NewCliFixture:
    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="harness-new-cli-")).resolve()
        (self.root / ".harness").mkdir()
        (self.root / ".harness" / "rules.yaml").write_text(
            RULES_YAML, encoding="utf-8")

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


class TestNewCli(unittest.TestCase):
    def setUp(self):
        self.fx = _NewCliFixture()
        self.runner = CliRunner()
        self._old_env = os.environ.get("HARNESS_PROJECT_DIR")
        os.environ["HARNESS_PROJECT_DIR"] = str(self.fx.root)

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("HARNESS_PROJECT_DIR", None)
        else:
            os.environ["HARNESS_PROJECT_DIR"] = self._old_env
        self.fx.cleanup()

    # -- 成功路径 --

    def test_create_component_ok(self):
        result = self.runner.invoke(
            cli_module.cli, ["new", "component", "UserCard"])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("已创建", result.output)
        self.assertIn("src/components/UserCard.tsx", result.output)
        self.assertTrue((self.fx.root / "src/components/UserCard.tsx").exists())

    def test_create_hook_ok(self):
        result = self.runner.invoke(
            cli_module.cli, ["new", "hook", "useTodos"])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertTrue((self.fx.root / "src/hooks/useTodos.ts").exists())

    def test_create_service_ok(self):
        result = self.runner.invoke(
            cli_module.cli, ["new", "service", "userService"])
        self.assertEqual(result.exit_code, 0)
        self.assertTrue((self.fx.root / "src/api/userService.ts").exists())

    def test_create_page_ok(self):
        result = self.runner.invoke(
            cli_module.cli, ["new", "page", "TodoListPage"])
        self.assertEqual(result.exit_code, 0)
        self.assertTrue((self.fx.root / "src/pages/TodoListPage.tsx").exists())

    def test_create_type_ok(self):
        result = self.runner.invoke(cli_module.cli, ["new", "type", "User"])
        self.assertEqual(result.exit_code, 0)
        self.assertTrue((self.fx.root / "src/types/User.ts").exists())

    # -- 命名错误 --

    def test_invalid_component_name_exit2(self):
        result = self.runner.invoke(
            cli_module.cli, ["new", "component", "userCard"])
        self.assertEqual(result.exit_code, 2)
        # CliRunner 默认把 err 也合到 output
        self.assertIn("UserCard", result.output)

    def test_invalid_hook_name_exit2(self):
        result = self.runner.invoke(
            cli_module.cli, ["new", "hook", "getTodos"])
        self.assertEqual(result.exit_code, 2)
        self.assertIn("useGetTodos", result.output)

    def test_invalid_service_name_exit2(self):
        result = self.runner.invoke(
            cli_module.cli, ["new", "service", "user"])
        self.assertEqual(result.exit_code, 2)
        self.assertIn("userService", result.output)

    # -- 冲突 --

    def test_existing_file_exit1(self):
        self.runner.invoke(cli_module.cli, ["new", "component", "UserCard"])
        result = self.runner.invoke(
            cli_module.cli, ["new", "component", "UserCard"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("已存在", result.output)

    def test_force_overwrites(self):
        self.runner.invoke(cli_module.cli, ["new", "component", "UserCard"])
        result = self.runner.invoke(
            cli_module.cli, ["new", "component", "UserCard", "--force"])
        self.assertEqual(result.exit_code, 0)

    # -- JSON --

    def test_json_success(self):
        result = self.runner.invoke(
            cli_module.cli, ["new", "type", "User", "--json"])
        self.assertEqual(result.exit_code, 0)
        payload = json.loads(result.output)
        self.assertTrue(payload["written"])
        self.assertEqual(payload["file"], "src/types/User.ts")

    def test_json_invalid_name(self):
        result = self.runner.invoke(
            cli_module.cli, ["new", "hook", "BadName", "--json"])
        self.assertEqual(result.exit_code, 2)
        payload = json.loads(result.output)
        self.assertIn("error", payload)

    # -- 未知 kind 由 click choice 校验 --

    def test_unknown_kind_rejected_by_click(self):
        result = self.runner.invoke(
            cli_module.cli, ["new", "widget", "Foo"])
        self.assertNotEqual(result.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
