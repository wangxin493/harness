"""harness probe CLI 集成测试。"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from click.testing import CliRunner

from . import _setup  # noqa: F401

from lib import cli as cli_module  # noqa: E402


class _ProbeCliFixture:
    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="harness-probe-cli-")).resolve()
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
        for sub, file_, body in [
            ("components", "Foo.tsx", "export const Foo = () => null;"),
            ("hooks", "useFoo.ts", "export const useFoo = () => 1;"),
            ("api", "userService.ts", "export const userService = () => 1;"),
            ("types", "User.ts", "export type User = {};"),
        ]:
            path = self.root / "src" / sub
            path.mkdir(parents=True, exist_ok=True)
            (path / file_).write_text(body, encoding="utf-8")

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


class TestProbeCli(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _ProbeCliFixture()
        self.runner = CliRunner()
        self._old_env = os.environ.get("HARNESS_PROJECT_DIR")
        os.environ["HARNESS_PROJECT_DIR"] = str(self.fx.root)

    def tearDown(self) -> None:
        if self._old_env is None:
            os.environ.pop("HARNESS_PROJECT_DIR", None)
        else:
            os.environ["HARNESS_PROJECT_DIR"] = self._old_env
        self.fx.cleanup()

    def test_probe_json_outputs_raw_facts(self) -> None:
        result = self.runner.invoke(cli_module.cli, ["probe", "--json"])
        self.assertEqual(result.exit_code, 0, result.output)
        data = json.loads(result.output)
        self.assertEqual(data["project_dir"], str(self.fx.root))
        self.assertTrue(data["is_typescript"])
        self.assertTrue(data["has_tsconfig"])
        self.assertIn("react", data["frameworks"])
        self.assertEqual(data["aliases"][0]["prefix"], "@/")
        self.assertTrue(data["layers"])
        self.assertIn("naming", data)
        self.assertIn("notes", data)
        self.assertFalse((self.fx.root / ".harness" / "rules.yaml").exists())

    def test_probe_human_summary(self) -> None:
        result = self.runner.invoke(cli_module.cli, ["probe"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("项目", result.output)
        self.assertIn("harness probe --json", result.output)
        self.assertIn("harness init --rules", result.output)


if __name__ == "__main__":
    unittest.main()
