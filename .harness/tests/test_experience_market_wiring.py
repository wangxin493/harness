"""experience_market 4-key 接通测试(A1):

- enabled: false → adapter 不渲染 lessons (已在 test_adapter 覆盖)
- min_score: rules.yaml 配的值生效 (已在 test_experience_market 覆盖)
- max_lessons: rules.yaml 配的值生效 (已在 test_adapter 覆盖)
- auto_pull: scan 前自动 sync 一次
- auto_push: lesson add 后自动 cp 到 .harness-shared/
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


class _ExpMarketFixture:
    """已经过 setup 的完整项目(rules.yaml / src/ 全有)。"""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="harness-expmkt-")).resolve()
        (self.root / "tsconfig.json").write_text(json.dumps({
            "compilerOptions": {
                "baseUrl": ".",
                "paths": {"@/*": ["src/*"]},
            },
        }), encoding="utf-8")
        (self.root / "package.json").write_text(json.dumps({
            "name": "fake-app",
            "dependencies": {"react": "^18.0.0"},
        }), encoding="utf-8")
        for sub, file_, body in [
            ("components", "Foo.tsx", "export const Foo = () => null;"),
            ("hooks", "useFoo.ts", "export const useFoo = () => 1;"),
            ("api", "userService.ts", "export const userService = () => 1;"),
            ("types", "User.ts", "export type User = {};"),
        ]:
            (self.root / "src" / sub).mkdir(parents=True, exist_ok=True)
            (self.root / "src" / sub / file_).write_text(body, encoding="utf-8")
        # 跑一次 setup,得到完整的 .harness/
        runner = CliRunner()
        old_env = os.environ.get("HARNESS_PROJECT_DIR")
        os.environ["HARNESS_PROJECT_DIR"] = str(self.root)
        try:
            r = runner.invoke(cli_module.cli, ["setup", "--agent", "claude"])
            if r.exit_code != 0:
                raise RuntimeError(f"setup 失败: {r.output}")
        finally:
            if old_env is None:
                os.environ.pop("HARNESS_PROJECT_DIR", None)
            else:
                os.environ["HARNESS_PROJECT_DIR"] = old_env

    def harness_dir(self) -> Path:
        return self.root / ".harness"

    def write_rules_market(self, **market_overrides) -> None:
        """把 experience_market 段写进 rules.yaml,其它保留原值。"""
        rules = self.harness_dir() / "rules.yaml"
        text = rules.read_text(encoding="utf-8")
        # 简单粗暴:在末尾 append 一段 yaml(同一 top-level key 后写覆盖前写)
        lines = ["", "experience_market:"]
        for k, v in market_overrides.items():
            if isinstance(v, bool):
                lines.append(f"  {k}: {'true' if v else 'false'}")
            else:
                lines.append(f"  {k}: {v}")
        rules.write_text(text + "\n".join(lines) + "\n", encoding="utf-8")

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


class _ExpMarketBase(unittest.TestCase):

    def setUp(self) -> None:
        self.fx = _ExpMarketFixture()
        self.runner = CliRunner()
        self._old_env = os.environ.get("HARNESS_PROJECT_DIR")
        os.environ["HARNESS_PROJECT_DIR"] = str(self.fx.root)

    def tearDown(self) -> None:
        if self._old_env is None:
            os.environ.pop("HARNESS_PROJECT_DIR", None)
        else:
            os.environ["HARNESS_PROJECT_DIR"] = self._old_env
        self.fx.cleanup()


class TestAutoPull(_ExpMarketBase):

    def test_scan_calls_sync_when_auto_pull_true(self) -> None:
        """rules.yaml auto_pull: true → scan 前 ExperienceMarket.sync() 被调一次。"""
        self.fx.write_rules_market(auto_pull=True)
        with patch("lib.experience_market.ExperienceMarket.sync") as mock_sync:
            from lib.experience_market import SyncResult
            mock_sync.return_value = SyncResult()
            r = self.runner.invoke(cli_module.cli, ["scan"])
            self.assertEqual(r.exit_code, 0, r.output)
            # 至少调用了一次(可能 lesson_add 内部还会再调,但至少 ≥1)
            self.assertGreaterEqual(mock_sync.call_count, 1,
                                    "auto_pull=true 时 sync 应该被调用")

    def test_scan_skips_sync_when_auto_pull_false(self) -> None:
        self.fx.write_rules_market(auto_pull=False)
        with patch("lib.experience_market.ExperienceMarket.sync") as mock_sync:
            r = self.runner.invoke(cli_module.cli, ["scan"])
            self.assertEqual(r.exit_code, 0, r.output)
            mock_sync.assert_not_called()

    def test_scan_skips_sync_when_market_disabled(self) -> None:
        self.fx.write_rules_market(enabled=False, auto_pull=True)
        with patch("lib.experience_market.ExperienceMarket.sync") as mock_sync:
            r = self.runner.invoke(cli_module.cli, ["scan"])
            self.assertEqual(r.exit_code, 0, r.output)
            mock_sync.assert_not_called()


class TestAutoPush(_ExpMarketBase):

    def test_lesson_add_pushes_to_shared_when_auto_push_true(self) -> None:
        self.fx.write_rules_market(auto_push=True)
        r = self.runner.invoke(cli_module.cli, [
            "lesson", "add",
            "--title", "t", "--content", "body",
            "--id", "L1",
        ])
        self.assertEqual(r.exit_code, 0, r.output)
        shared = self.fx.root / ".harness-shared" / "lessons" / "L1.md"
        self.assertTrue(shared.exists(),
                        f"auto_push=true 应该写到 {shared}")
        self.assertIn("auto_push", r.output)

    def test_lesson_add_skips_push_when_auto_push_false(self) -> None:
        self.fx.write_rules_market(auto_push=False)
        r = self.runner.invoke(cli_module.cli, [
            "lesson", "add",
            "--title", "t", "--content", "body",
            "--id", "L1",
        ])
        self.assertEqual(r.exit_code, 0, r.output)
        shared = self.fx.root / ".harness-shared" / "lessons" / "L1.md"
        self.assertFalse(shared.exists(),
                         "auto_push=false 不应该写到共享盘")


if __name__ == "__main__":
    unittest.main()
