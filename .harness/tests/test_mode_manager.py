"""mode_manager 单元测试。"""

import json
import shutil
import tempfile
import textwrap
import unittest
from dataclasses import dataclass
from pathlib import Path

from . import _setup  # noqa: F401

from lib.mode_manager import (  # noqa: E402
    DEFAULT_RULES,
    GovernanceMode,
    ModeManager,
    RuleConfig,
    ValidationContext,
)


# 用一个最小 Issue 替身（与 lib.validator.Issue 字段一致即可）
@dataclass
class _Issue:
    rule_id: str
    severity: str
    category: str
    message: str
    file: str
    line: int = 1
    suggestion: str = ""


class ModeFixture:
    def __init__(self, write_default_yaml: bool = False):
        self.root = Path(tempfile.mkdtemp(prefix="harness-mode-")).resolve()
        self.harness_dir = self.root / ".harness"
        self.harness_dir.mkdir()
        if write_default_yaml:
            self.harness_dir.joinpath("rules.yaml").write_text(
                textwrap.dedent("""\
                governance:
                  default_mode: relaxed
                """),
                encoding="utf-8",
            )

    def manager(self) -> ModeManager:
        return ModeManager(harness_dir=self.harness_dir)

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


# ---------------------------------------------------------------------------
# ModeManager
# ---------------------------------------------------------------------------


class TestModeManager(unittest.TestCase):
    def setUp(self):
        self.fx = ModeFixture()

    def tearDown(self):
        self.fx.cleanup()

    # -- 加载与持久化 -------------------------------------------------------

    def test_default_mode_is_strict(self):
        mm = self.fx.manager()
        self.assertEqual(mm.current_mode, GovernanceMode.STRICT)

    def test_load_from_rules_yaml_governance(self):
        # 没有 mode-config.json 时，从 rules.yaml.governance.default_mode 取
        fx = ModeFixture(write_default_yaml=True)
        try:
            mm = fx.manager()
            self.assertEqual(mm.current_mode, GovernanceMode.RELAXED)
        finally:
            fx.cleanup()

    def test_set_mode_persists(self):
        mm = self.fx.manager()
        mm.set_mode(GovernanceMode.RELAXED)
        # 重新构造一个 manager，应能从磁盘读回
        mm2 = self.fx.manager()
        self.assertEqual(mm2.current_mode, GovernanceMode.RELAXED)
        # 文件内容包含 updated_at
        data = json.loads(mm.config_file.read_text(encoding="utf-8"))
        self.assertEqual(data["mode"], "relaxed")
        self.assertIn("updated_at", data)

    def test_toggle_cycles_strict_relaxed_off_strict(self):
        mm = self.fx.manager()
        self.assertEqual(mm.toggle_mode(), GovernanceMode.RELAXED)
        self.assertEqual(mm.toggle_mode(), GovernanceMode.OFF)
        self.assertEqual(mm.toggle_mode(), GovernanceMode.STRICT)

    def test_load_corrupt_config_falls_back(self):
        mm = self.fx.manager()
        mm.config_file.write_text("not json", encoding="utf-8")
        mm2 = self.fx.manager()
        # 损坏 → 跳到 yaml 链路 → 也没有 → STRICT
        self.assertEqual(mm2.current_mode, GovernanceMode.STRICT)

    # -- 规则匹配 -----------------------------------------------------------

    def test_find_rule_exact_then_wildcard(self):
        mm = self.fx.manager()
        # 精确
        rule = mm.find_rule("import-forbidden")
        self.assertIsNotNone(rule)
        self.assertEqual(rule.category, "import")
        # 通配（validator 实际产出 arch-service-import / arch-hook-import 等）
        rule = mm.find_rule("arch-service-import")
        self.assertIsNotNone(rule)
        self.assertEqual(rule.pattern, "arch-*-import")
        # 命中 naming-* 通配
        rule = mm.find_rule("naming-component")
        self.assertIsNotNone(rule)
        self.assertFalse(rule.enabled_in_relaxed)

    def test_unknown_rule_returns_none(self):
        mm = self.fx.manager()
        self.assertIsNone(mm.find_rule("custom-xyz"))

    # -- 启用判断 -----------------------------------------------------------

    def test_strict_enables_all(self):
        mm = self.fx.manager()
        mm.set_mode(GovernanceMode.STRICT)
        for rule_id in ["arch-service-import", "import-forbidden", "naming-component"]:
            self.assertTrue(mm.is_rule_enabled(rule_id))

    def test_off_disables_all(self):
        mm = self.fx.manager()
        mm.set_mode(GovernanceMode.OFF)
        for rule_id in ["arch-service-import", "import-forbidden"]:
            self.assertFalse(mm.is_rule_enabled(rule_id))
        self.assertFalse(mm.should_validate())

    def test_relaxed_disables_naming_keeps_arch_and_import(self):
        mm = self.fx.manager()
        mm.set_mode(GovernanceMode.RELAXED)
        self.assertTrue(mm.is_rule_enabled("arch-service-import"))
        self.assertTrue(mm.is_rule_enabled("import-forbidden"))
        self.assertFalse(mm.is_rule_enabled("naming-component"))
        # 未知 rule 在 relaxed 下保守启用（避免漏报）
        self.assertTrue(mm.is_rule_enabled("custom-xyz"))

    # -- should_block ------------------------------------------------------

    def test_should_block_per_mode(self):
        mm = self.fx.manager()

        mm.set_mode(GovernanceMode.STRICT)
        self.assertTrue(mm.should_block("error"))
        self.assertTrue(mm.should_block("warning"))
        self.assertTrue(mm.should_block("info"))

        mm.set_mode(GovernanceMode.RELAXED)
        self.assertTrue(mm.should_block("error"))
        self.assertFalse(mm.should_block("warning"))
        self.assertFalse(mm.should_block("info"))

        mm.set_mode(GovernanceMode.OFF)
        self.assertFalse(mm.should_block("error"))
        self.assertFalse(mm.should_block("warning"))

    # -- get_mode_info ------------------------------------------------------

    def test_mode_info_counts(self):
        mm = self.fx.manager()
        mm.set_mode(GovernanceMode.STRICT)
        info = mm.get_mode_info()
        self.assertEqual(info["mode"], "strict")
        self.assertEqual(info["enabled_rules_count"], len(DEFAULT_RULES))
        self.assertGreater(info["error_rules"], 0)
        self.assertIn("config_file", info)


# ---------------------------------------------------------------------------
# ValidationContext
# ---------------------------------------------------------------------------


class TestValidationContext(unittest.TestCase):
    def setUp(self):
        self.fx = ModeFixture()

    def tearDown(self):
        self.fx.cleanup()

    def _issues(self):
        return [
            _Issue("arch-service-import", "error", "architecture",
                   "service 不能 import component", "src/api/x.ts"),
            _Issue("import-forbidden", "error", "import",
                   "禁止导入 @/services", "src/api/x.ts"),
            _Issue("naming-component", "warning", "naming",
                   "组件命名应 PascalCase", "src/components/x.tsx"),
        ]

    def test_strict_passes_all_through(self):
        mm = self.fx.manager()
        mm.set_mode(GovernanceMode.STRICT)
        ctx = ValidationContext(mm)
        out = ctx.filter_issues(self._issues())
        self.assertEqual(len(out), 3)
        # severity 不变
        severities = sorted(i.severity for i in out)
        self.assertEqual(severities, ["error", "error", "warning"])

    def test_off_drops_everything(self):
        mm = self.fx.manager()
        mm.set_mode(GovernanceMode.OFF)
        ctx = ValidationContext(mm)
        self.assertEqual(ctx.filter_issues(self._issues()), [])

    def test_relaxed_drops_naming_keeps_errors(self):
        mm = self.fx.manager()
        mm.set_mode(GovernanceMode.RELAXED)
        ctx = ValidationContext(mm)
        out = ctx.filter_issues(self._issues())
        rule_ids = sorted(i.rule_id for i in out)
        self.assertEqual(rule_ids, ["arch-service-import", "import-forbidden"])
        # 都是 error，未被降级
        self.assertTrue(all(i.severity == "error" for i in out))

    def test_relaxed_downgrades_warning_when_enabled_rule(self):
        # 自定义一条 warning 规则在 relaxed 下也启用 → 应降级为 info
        custom_rules = list(DEFAULT_RULES) + [
            RuleConfig("perf-warn", "perf", "warning", True, False),
        ]
        mm = ModeManager(harness_dir=self.fx.harness_dir, rules=custom_rules)
        mm.set_mode(GovernanceMode.RELAXED)
        ctx = ValidationContext(mm)

        issue = _Issue("perf-warn", "warning", "perf", "慢", "src/x.ts")
        out = ctx.filter_issues([issue])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].severity, "info")
        # 原对象未被原地修改（dataclasses.replace 语义）
        self.assertEqual(issue.severity, "warning")

    def test_should_block_aggregates(self):
        mm = self.fx.manager()
        mm.set_mode(GovernanceMode.RELAXED)
        ctx = ValidationContext(mm)

        only_warnings = [_Issue("naming-component", "warning", "naming", "x", "x.ts")]
        self.assertFalse(ctx.should_block(only_warnings))

        with_error = [_Issue("import-forbidden", "error", "import", "x", "x.ts")]
        self.assertTrue(ctx.should_block(with_error))


if __name__ == "__main__":
    unittest.main()
