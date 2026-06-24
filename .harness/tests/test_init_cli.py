"""harness init CLI 集成测试。

覆盖 cli.init 子命令的端到端行为：
- --dry-run 不写盘
- --json 输出结构化 plan
- --yes 走 default_choice，写出 rules.yaml + mode-config.json + 必要目录
- 交互模式（input=...）能把用户选择落进 rules.yaml
- 重写时遇到已有 rules.yaml 询问确认（默认 No）
- HARNESS_PROJECT_DIR 环境变量正确生效
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from click.testing import CliRunner

from . import _setup  # noqa: F401

from lib import cli as cli_module  # noqa: E402


class _InitFixture:
    """临时项目工厂：写 tsconfig + package.json + 一个 src 结构。"""

    def __init__(self, with_unknown_dir: bool = False,
                 with_service_naming_diverge: bool = False) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="harness-init-cli-")).resolve()
        # tsconfig.json 含 "@/*": ["src/*"]（最容易踩 JSONC 坑）
        (self.root / "tsconfig.json").write_text(json.dumps({
            "compilerOptions": {
                "baseUrl": ".",
                "paths": {"@/*": ["src/*"]},
            },
        }), encoding="utf-8")
        (self.root / "package.json").write_text(json.dumps({
            "name": "fake",
            "dependencies": {"react": "^18.0.0"},
            "devDependencies": {"typescript": "^5"},
        }), encoding="utf-8")
        # 默认 5 layer
        (self.root / "src" / "components").mkdir(parents=True)
        (self.root / "src" / "components" / "Foo.tsx").write_text(
            "export const Foo = () => null;", encoding="utf-8")
        (self.root / "src" / "hooks").mkdir(parents=True)
        (self.root / "src" / "hooks" / "useFoo.ts").write_text(
            "export const useFoo = () => 1;", encoding="utf-8")
        (self.root / "src" / "api").mkdir(parents=True)
        api_dir = self.root / "src" / "api"
        if with_service_naming_diverge:
            # 5 个文件全部不带 Service 后缀
            for n in ("userApi", "todoApi", "orderApi", "billApi", "noteApi"):
                (api_dir / f"{n}.ts").write_text(
                    f"export const {n} = () => 1;", encoding="utf-8")
        else:
            (api_dir / "userService.ts").write_text(
                "export const userService = () => 1;", encoding="utf-8")
        (self.root / "src" / "types").mkdir(parents=True)
        (self.root / "src" / "types" / "User.ts").write_text(
            "export type User = {};", encoding="utf-8")
        if with_unknown_dir:
            (self.root / "src" / "utils").mkdir(parents=True)
            (self.root / "src" / "utils" / "fmt.ts").write_text(
                "export const fmt = () => 1;", encoding="utf-8")

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


class _InitCliBase(unittest.TestCase):

    def setUp(self) -> None:
        self.fx = self._make_fixture()
        self.runner = CliRunner()
        self._old_env = os.environ.get("HARNESS_PROJECT_DIR")
        os.environ["HARNESS_PROJECT_DIR"] = str(self.fx.root)

    def tearDown(self) -> None:
        if self._old_env is None:
            os.environ.pop("HARNESS_PROJECT_DIR", None)
        else:
            os.environ["HARNESS_PROJECT_DIR"] = self._old_env
        self.fx.cleanup()

    def _make_fixture(self) -> _InitFixture:
        return _InitFixture()


class TestInitDryRun(_InitCliBase):

    def test_dry_run_does_not_write(self) -> None:
        result = self.runner.invoke(cli_module.cli, ["init", "--dry-run"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("dry-run", result.output)
        # 不应该有 .harness 目录或 rules.yaml
        self.assertFalse((self.fx.root / ".harness" / "rules.yaml").exists())

    def test_dry_run_json(self) -> None:
        result = self.runner.invoke(
            cli_module.cli, ["init", "--dry-run", "--json"])
        self.assertEqual(result.exit_code, 0, result.output)
        data = json.loads(result.output)
        self.assertEqual(data["project_dir"], str(self.fx.root))
        self.assertIn("adopted_notes", data)
        self.assertIn("conflicts", data)
        self.assertTrue(data["dry_run"])


class TestInitYesWrites(_InitCliBase):

    def test_yes_writes_rules_and_mode(self) -> None:
        result = self.runner.invoke(cli_module.cli, ["init", "--yes"])
        self.assertEqual(result.exit_code, 0, result.output)
        rules_path = self.fx.root / ".harness" / "rules.yaml"
        mode_path = self.fx.root / ".harness" / "mode-config.json"
        self.assertTrue(rules_path.exists())
        self.assertTrue(mode_path.exists())
        # mode-config.json 应是 strict
        self.assertEqual(
            json.loads(mode_path.read_text(encoding="utf-8")),
            {"mode": "strict"},
        )
        # 必要目录
        self.assertTrue(
            (self.fx.root / ".harness" / "context").is_dir(),
        )
        self.assertTrue(
            (self.fx.root / ".harness" / "memory" / "lessons").is_dir(),
        )

    def test_yes_rules_yaml_parseable(self) -> None:
        import yaml
        self.runner.invoke(cli_module.cli, ["init", "--yes"])
        data = yaml.safe_load(
            (self.fx.root / ".harness" / "rules.yaml").read_text("utf-8"),
        )
        self.assertIn("architecture", data)
        self.assertIn("naming", data)
        self.assertIn("scanner", data)


class TestInitWithUnknownDir(_InitCliBase):

    def _make_fixture(self) -> _InitFixture:
        return _InitFixture(with_unknown_dir=True)

    def test_yes_uses_default_skip_for_unknown_dir(self) -> None:
        # default_choice = "skip"，所以 utils 既不应进 exclude_dirs 也不进任何 paths
        result = self.runner.invoke(cli_module.cli, ["init", "--yes"])
        self.assertEqual(result.exit_code, 0, result.output)
        import yaml
        data = yaml.safe_load(
            (self.fx.root / ".harness" / "rules.yaml").read_text("utf-8"),
        )
        self.assertNotIn("utils",
                         data.get("scanner", {}).get("exclude_dirs", []))
        for layer in data["architecture"]["layers"]:
            self.assertNotIn("src/utils/", layer.get("paths", []))

    def test_interactive_map_unknown_dir_to_layer(self) -> None:
        # 交互模式：unknown-dir 选 "3"（service），其它走默认回车
        # CliRunner.input 把字符按提示顺序喂进去
        # 顺序：unknown-dir:utils → choice [3] = service
        result = self.runner.invoke(
            cli_module.cli, ["init"], input="3\n",
        )
        self.assertEqual(result.exit_code, 0, result.output)
        import yaml
        data = yaml.safe_load(
            (self.fx.root / ".harness" / "rules.yaml").read_text("utf-8"),
        )
        svc = next(L for L in data["architecture"]["layers"]
                   if L["name"] == "service")
        self.assertIn("src/utils/", svc["paths"])


class TestInitNamingConflict(_InitCliBase):

    def _make_fixture(self) -> _InitFixture:
        return _InitFixture(with_service_naming_diverge=True)

    def test_yes_keeps_default_naming(self) -> None:
        # default_choice = "keep-default"
        self.runner.invoke(cli_module.cli, ["init", "--yes"])
        import yaml
        data = yaml.safe_load(
            (self.fx.root / ".harness" / "rules.yaml").read_text("utf-8"),
        )
        self.assertEqual(
            data["naming"]["service"], "camelCase-with-Service-suffix",
        )

    def test_interactive_adopt_camelcase(self) -> None:
        # adopt:camelCase 对应 choice [2]
        result = self.runner.invoke(
            cli_module.cli, ["init"], input="2\n",
        )
        self.assertEqual(result.exit_code, 0, result.output)
        import yaml
        data = yaml.safe_load(
            (self.fx.root / ".harness" / "rules.yaml").read_text("utf-8"),
        )
        self.assertEqual(data["naming"]["service"], "camelCase")


class TestInitOverwriteConfirm(_InitCliBase):

    def test_existing_rules_overwrite_declined(self) -> None:
        # 先建一份占位 rules.yaml
        (self.fx.root / ".harness").mkdir(parents=True, exist_ok=True)
        rules_path = self.fx.root / ".harness" / "rules.yaml"
        rules_path.write_text("# placeholder\nfoo: bar\n", encoding="utf-8")
        # 走 --yes，最后会问"覆盖？"，回车=No
        result = self.runner.invoke(
            cli_module.cli, ["init", "--yes"], input="\n",
        )
        self.assertEqual(result.exit_code, 0, result.output)
        # 没被覆盖
        self.assertIn("foo: bar", rules_path.read_text(encoding="utf-8"))
        self.assertIn("已取消", result.output)

    def test_existing_rules_overwrite_yes(self) -> None:
        (self.fx.root / ".harness").mkdir(parents=True, exist_ok=True)
        rules_path = self.fx.root / ".harness" / "rules.yaml"
        rules_path.write_text("# placeholder\nfoo: bar\n", encoding="utf-8")
        result = self.runner.invoke(
            cli_module.cli, ["init", "--yes"], input="y\n",
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn("foo: bar",
                         rules_path.read_text(encoding="utf-8"))
        self.assertIn("architecture",
                      rules_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
