"""doctor / upgrade 单元测试。"""

import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from . import _setup  # noqa: F401

from lib.doctor import (  # noqa: E402
    Doctor,
    _version_satisfies,
    plan_upgrade,
)


VALID_RULES_YAML = textwrap.dedent("""\
architecture:
  layers:
    - name: component
      paths: ["src/components/"]
      can_import: ["hook", "service", "type"]
imports:
  forbidden_imports: ["@/services"]
""")


class DoctorFixture:
    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="harness-doctor-")).resolve()
        self.harness_dir = self.root / ".harness"
        self.harness_dir.mkdir()
        # 模拟 harness_root（VERSION + requirements 在这里）
        self.harness_root = self.harness_dir
        (self.harness_root / "VERSION").write_text("2.0.0-test", encoding="utf-8")

    def write_rules(self, content: str = VALID_RULES_YAML):
        (self.harness_dir / "rules.yaml").write_text(content, encoding="utf-8")

    def write_dep_graph(self, valid: bool = True):
        ctx_dir = self.harness_dir / "context"
        ctx_dir.mkdir(parents=True, exist_ok=True)
        path = ctx_dir / "dependency-graph.json"
        if valid:
            path.write_text(json.dumps({
                "schema_version": 1,
                "updated_at": "2026-06-17T20:00:00",
                "graph": {"src/a.ts": [], "src/b.ts": ["src/a.ts"]},
                "reverse_graph": {},
            }), encoding="utf-8")
        else:
            path.write_text("not json", encoding="utf-8")

    def write_mode_config(self, mode: str = "relaxed"):
        (self.harness_dir / "mode-config.json").write_text(
            json.dumps({"mode": mode, "updated_at": "2026-06-17T20:00:00"}),
            encoding="utf-8",
        )

    def write_shared_dir(self, lessons: int = 0):
        shared = self.root / ".harness-shared" / "lessons"
        shared.mkdir(parents=True, exist_ok=True)
        for i in range(lessons):
            (shared / f"l{i}.md").write_text(
                f"---\nid: l{i}\ntitle: t\n---\nbody\n",
                encoding="utf-8",
            )

    def init_git(self):
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=self.root, check=True)

    def doctor(self) -> Doctor:
        return Doctor(project_dir=self.root, harness_root=self.harness_root)

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 单项检查
# ---------------------------------------------------------------------------


class TestDoctorChecks(unittest.TestCase):
    def setUp(self):
        self.fx = DoctorFixture()

    def tearDown(self):
        self.fx.cleanup()

    def _by_name(self, report, name):
        return next((c for c in report.checks if c.name == name), None)

    def test_python_ok(self):
        report = self.fx.doctor().run()
        c = self._by_name(report, "python")
        self.assertEqual(c.severity, "ok")
        self.assertIn(f"{sys.version_info.major}.{sys.version_info.minor}", c.message)

    def test_venv_missing_warns(self):
        # 没建 .venv → warning（不是 error，避免在 CI 之外坏掉）
        report = self.fx.doctor().run()
        c = self._by_name(report, "venv")
        self.assertEqual(c.severity, "warning")
        self.assertIn(".venv", c.message)
        self.assertIn("python3 -m venv", c.suggestion)

    def test_packages_present(self):
        report = self.fx.doctor().run()
        # 只要测试能跑起来，4 个核心包都装了
        for pkg in ("tree-sitter", "tree-sitter-typescript", "click", "PyYAML"):
            c = self._by_name(report, f"pkg:{pkg}")
            self.assertIsNotNone(c, f"missing check for {pkg}")
            self.assertEqual(c.severity, "ok",
                             f"expected ok for {pkg}, got {c.severity}: {c.message}")

    def test_rules_yaml_missing_is_error(self):
        report = self.fx.doctor().run()
        c = self._by_name(report, "rules.yaml")
        self.assertEqual(c.severity, "error")

    def test_rules_yaml_corrupt_is_error(self):
        (self.fx.harness_dir / "rules.yaml").write_text(
            "::: not: yaml: [", encoding="utf-8")
        report = self.fx.doctor().run()
        c = self._by_name(report, "rules.yaml")
        self.assertEqual(c.severity, "error")
        self.assertIn("解析失败", c.message)

    def test_rules_yaml_no_layers_warns(self):
        (self.fx.harness_dir / "rules.yaml").write_text(
            "imports:\n  forbidden_imports: []\n", encoding="utf-8")
        report = self.fx.doctor().run()
        c = self._by_name(report, "rules.yaml")
        self.assertEqual(c.severity, "warning")

    def test_rules_yaml_ok(self):
        self.fx.write_rules()
        report = self.fx.doctor().run()
        c = self._by_name(report, "rules.yaml")
        self.assertEqual(c.severity, "ok")
        self.assertIn("1 个架构层", c.message)

    def test_dep_graph_missing_warns(self):
        report = self.fx.doctor().run()
        c = self._by_name(report, "dependency-graph")
        self.assertEqual(c.severity, "warning")
        self.assertIn("harness scan", c.suggestion)

    def test_dep_graph_corrupt_is_error(self):
        self.fx.write_dep_graph(valid=False)
        report = self.fx.doctor().run()
        c = self._by_name(report, "dependency-graph")
        self.assertEqual(c.severity, "error")

    def test_dep_graph_ok(self):
        self.fx.write_dep_graph(valid=True)
        report = self.fx.doctor().run()
        c = self._by_name(report, "dependency-graph")
        self.assertEqual(c.severity, "ok")
        self.assertIn("2 个节点", c.message)

    def test_mode_config_default_is_info(self):
        report = self.fx.doctor().run()
        c = self._by_name(report, "mode-config")
        self.assertEqual(c.severity, "info")

    def test_mode_config_present_is_ok(self):
        self.fx.write_mode_config("relaxed")
        report = self.fx.doctor().run()
        c = self._by_name(report, "mode-config")
        self.assertEqual(c.severity, "ok")
        self.assertIn("relaxed", c.message)

    def test_mode_config_corrupt_warns(self):
        (self.fx.harness_dir / "mode-config.json").write_text(
            "garbage", encoding="utf-8")
        report = self.fx.doctor().run()
        c = self._by_name(report, "mode-config")
        self.assertEqual(c.severity, "warning")

    def test_git_not_a_repo_is_info_not_error(self):
        report = self.fx.doctor().run()
        c = self._by_name(report, "git")
        self.assertEqual(c.severity, "info")
        self.assertIn("不是 git 仓库", c.message)

    def test_git_repo_ok(self):
        self.fx.init_git()
        report = self.fx.doctor().run()
        c = self._by_name(report, "git")
        self.assertEqual(c.severity, "ok")
        # 默认分支在不同 git 版本可能是 master / main，只断言 prefix
        self.assertIn("git 仓库可用", c.message)

    def test_shared_dir_missing_is_info(self):
        report = self.fx.doctor().run()
        c = self._by_name(report, "shared-lessons")
        self.assertEqual(c.severity, "info")

    def test_shared_dir_present_ok(self):
        self.fx.write_shared_dir(lessons=2)
        report = self.fx.doctor().run()
        c = self._by_name(report, "shared-lessons")
        self.assertEqual(c.severity, "ok")
        self.assertIn("2 条 lesson", c.message)


# ---------------------------------------------------------------------------
# 报告聚合
# ---------------------------------------------------------------------------


class TestDoctorReport(unittest.TestCase):
    def setUp(self):
        self.fx = DoctorFixture()

    def tearDown(self):
        self.fx.cleanup()

    def test_has_error_when_rules_missing(self):
        # 没 rules.yaml → error
        report = self.fx.doctor().run()
        self.assertTrue(report.has_error)
        self.assertEqual(report.summary()["error"] >= 1, True)

    def test_no_error_when_minimal_setup_ok(self):
        # 凑齐：rules + dep_graph
        self.fx.write_rules()
        self.fx.write_dep_graph(valid=True)
        report = self.fx.doctor().run()
        self.assertFalse(report.has_error,
                         f"expected no error, got: "
                         f"{[c for c in report.checks if c.severity == 'error']}")

    def test_to_dict_serializable(self):
        report = self.fx.doctor().run()
        data = report.to_dict()
        # JSON 化无异常
        json.dumps(data, ensure_ascii=False)
        self.assertIn("summary", data)
        self.assertIn("has_error", data)
        self.assertIn("checks", data)


# ---------------------------------------------------------------------------
# 版本约束工具
# ---------------------------------------------------------------------------


class TestVersionSatisfies(unittest.TestCase):
    def test_eq(self):
        self.assertTrue(_version_satisfies("0.21.3", "==0.21.3"))
        self.assertFalse(_version_satisfies("0.21.4", "==0.21.3"))

    def test_range(self):
        self.assertTrue(_version_satisfies("8.1.8", ">=8.1,<9"))
        self.assertTrue(_version_satisfies("8.5.0", ">=8.1,<9"))
        self.assertFalse(_version_satisfies("9.0.0", ">=8.1,<9"))
        self.assertFalse(_version_satisfies("8.0.0", ">=8.1,<9"))

    def test_six(self):
        self.assertTrue(_version_satisfies("6.0.3", ">=6.0,<7.0"))
        self.assertFalse(_version_satisfies("7.0.0", ">=6.0,<7.0"))

    def test_neq(self):
        self.assertTrue(_version_satisfies("1.0.0", "!=2.0.0"))
        self.assertFalse(_version_satisfies("2.0.0", "!=2.0.0"))

    def test_empty_spec(self):
        # 没有约束 → 永远满足
        self.assertTrue(_version_satisfies("0.0.0", ""))


# ---------------------------------------------------------------------------
# Upgrade 占位
# ---------------------------------------------------------------------------


class TestUpgrade(unittest.TestCase):
    def setUp(self):
        self.fx = DoctorFixture()

    def tearDown(self):
        self.fx.cleanup()

    def test_plan_upgrade_returns_version_and_planned(self):
        report = plan_upgrade(self.fx.harness_root)
        self.assertEqual(report.current_version, "2.0.0-test")
        self.assertGreater(len(report.planned), 0)
        # 必含 "P2" 字样表明是占位
        self.assertTrue(any("P2" in p for p in report.planned))

    def test_plan_upgrade_no_version_file(self):
        # 删了 VERSION → 不应崩
        (self.fx.harness_root / "VERSION").unlink()
        report = plan_upgrade(self.fx.harness_root)
        self.assertEqual(report.current_version, "0.0.0")


if __name__ == "__main__":
    unittest.main()
