"""init_resolver 单元测试 —— harness init 冲突协商层。

覆盖点：
- 别名一致 → 静默采纳，写 adopted_notes
- 别名不一致（单个 / 多个）→ 写进 scanner.import_alias / import_aliases
- layer 路径与默认不同 → 自动采纳探测值
- src/ 下未识别目录 → 抛 unknown-dir conflict，含 layer/ignore/skip 三类选项
- apply_user_choices: ignore → 加 exclude_dirs；选 layer → 进对应 paths
- 命名风格背离 → 抛 naming conflict；sample_size<3 不打扰
- info_notes 含框架 / 包管理器 / gitignore 建议
- apply_user_choices 缺省项走 default_choice
"""

import copy
import unittest
from typing import Any, Dict

from . import _setup  # noqa: F401

from lib.init_resolver import (  # noqa: E402
    Conflict,
    ConflictChoice,
    InitResolver,
    ProposedPlan,
    resolve_init,
)
from lib.probe import (  # noqa: E402
    AliasFinding,
    LayerFinding,
    NamingFinding,
    ProbeReport,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_rules() -> Dict[str, Any]:
    return {
        "architecture": {
            "layers": [
                {"name": "component", "paths": ["src/pages/", "src/components/"],
                 "can_import": ["hook", "service", "type"]},
                {"name": "hook", "paths": ["src/hooks/"],
                 "can_import": ["service", "type"]},
                {"name": "service", "paths": ["src/api/"],
                 "can_import": ["type"]},
                {"name": "type", "paths": ["src/types/"],
                 "can_import": []},
            ],
        },
        "naming": {
            "component": "PascalCase",
            "hook": "camelCase-with-use-prefix",
            "service": "camelCase-with-Service-suffix",
            "type": "PascalCase",
        },
        "scanner": {
            "source_root": "src",
            "include_extensions": [".ts", ".tsx", ".d.ts"],
            "exclude_dirs": ["node_modules", "dist"],
        },
        "imports": {
            "allowed_prefixes": ["@/types", "@/hooks", "@/api"],
            "forbidden_imports": [],
            "forbidden_suggestions": {},
            "rewrites": {},
        },
    }


def _make_report(**kwargs) -> ProbeReport:
    base = ProbeReport(
        project_dir="/tmp/x",
        has_git=True,
        is_typescript=True,
        has_tsconfig=True,
    )
    for k, v in kwargs.items():
        setattr(base, k, v)
    return base


# ---------------------------------------------------------------------------
# 别名
# ---------------------------------------------------------------------------


class AliasResolutionTests(unittest.TestCase):

    def test_default_alias_silently_adopted(self) -> None:
        # rules 默认 "@/" → "src/"（隐式），探测同上
        report = _make_report(aliases=[
            AliasFinding(prefix="@/", target="src/", source_file="tsconfig.json"),
        ])
        plan = resolve_init(_default_rules(), report)
        self.assertEqual(plan.conflicts, [])
        self.assertTrue(
            any("沿用默认" in n for n in plan.adopted_notes),
            plan.adopted_notes,
        )
        # 不应注入显式别名键（默认已经够用）
        self.assertNotIn("import_alias", plan.rules["scanner"])
        self.assertNotIn("import_aliases", plan.rules["scanner"])

    def test_single_alternative_alias_adopted(self) -> None:
        report = _make_report(aliases=[
            AliasFinding(prefix="~/", target="app/", source_file="tsconfig.json"),
        ])
        plan = resolve_init(_default_rules(), report)
        self.assertEqual(plan.conflicts, [])
        self.assertEqual(
            plan.rules["scanner"]["import_alias"],
            {"prefix": "~/", "target": "app/"},
        )

    def test_multi_alias_adopted_as_list(self) -> None:
        report = _make_report(aliases=[
            AliasFinding(prefix="@/", target="src/", source_file="tsconfig.json"),
            AliasFinding(prefix="~/", target="app/", source_file="tsconfig.json"),
        ])
        plan = resolve_init(_default_rules(), report)
        aliases = plan.rules["scanner"]["import_aliases"]
        self.assertEqual(len(aliases), 2)
        prefixes = {a["prefix"] for a in aliases}
        self.assertEqual(prefixes, {"@/", "~/"})

    def test_explicit_default_diff_from_probe_adopts_probe(self) -> None:
        """rules.yaml 显式配了 import_alias，探测到不同值 → 采纳探测值。

        回归：旧实现把"无显式默认"伪装成 [(@,src)]，导致这一支永远走"等价"
        分支；现在显式默认存在时严格对比。
        """
        rules = _default_rules()
        rules["scanner"]["import_alias"] = {"prefix": "@/", "target": "src/"}
        report = _make_report(aliases=[
            AliasFinding(prefix="~/", target="app/", source_file="tsconfig.json"),
        ])
        plan = resolve_init(rules, report)
        self.assertEqual(plan.conflicts, [])
        self.assertEqual(
            plan.rules["scanner"]["import_alias"],
            {"prefix": "~/", "target": "app/"},
        )


# ---------------------------------------------------------------------------
# Layer 路径
# ---------------------------------------------------------------------------


class LayerPathResolutionTests(unittest.TestCase):

    def test_path_diff_silently_adopted(self) -> None:
        # 默认 component=["src/pages/","src/components/"]，探测只有 src/components/
        report = _make_report(layers=[
            LayerFinding(name="component", path="src/components/", file_count=2,
                         sample_names=["Foo", "Bar"]),
        ])
        plan = resolve_init(_default_rules(), report)
        comp = [L for L in plan.rules["architecture"]["layers"]
                if L["name"] == "component"][0]
        self.assertEqual(comp["paths"], ["src/components/"])
        self.assertTrue(
            any("'component' 路径采用探测结果" in n for n in plan.adopted_notes),
            plan.adopted_notes,
        )

    def test_same_paths_no_note(self) -> None:
        report = _make_report(layers=[
            LayerFinding(name="hook", path="src/hooks/", file_count=1,
                         sample_names=["useFoo"]),
        ])
        plan = resolve_init(_default_rules(), report)
        hook = [L for L in plan.rules["architecture"]["layers"]
                if L["name"] == "hook"][0]
        self.assertEqual(hook["paths"], ["src/hooks/"])
        # 不应记 "采用探测结果" 这种 note
        self.assertFalse(
            any("'hook' 路径" in n for n in plan.adopted_notes),
        )


# ---------------------------------------------------------------------------
# unknown-dir conflict
# ---------------------------------------------------------------------------


class UnknownDirConflictTests(unittest.TestCase):

    def setUp(self) -> None:
        self.report = _make_report(layers=[
            LayerFinding(name="unknown", path="src/utils/", file_count=4,
                         sample_names=["fmt", "delay", "clamp"]),
        ])
        self.plan = resolve_init(_default_rules(), self.report)

    def test_conflict_emitted(self) -> None:
        ids = [c.id for c in self.plan.conflicts]
        self.assertIn("unknown-dir:utils", ids)
        c = next(c for c in self.plan.conflicts if c.id == "unknown-dir:utils")
        self.assertEqual(c.category, "unknown-dir")
        self.assertEqual(c.default_choice, "skip")
        keys = {ch.key for ch in c.choices}
        # 应至少给出几个 layer 选项 + ignore + skip
        self.assertIn("ignore", keys)
        self.assertIn("skip", keys)
        self.assertIn("hook", keys)

    def test_apply_ignore_adds_to_exclude_dirs(self) -> None:
        resolver = InitResolver(_default_rules(), self.report)
        plan = resolver.resolve()
        final = resolver.apply_user_choices(plan, {"unknown-dir:utils": "ignore"})
        self.assertIn("utils", final["scanner"]["exclude_dirs"])

    def test_apply_map_to_layer_adds_path(self) -> None:
        resolver = InitResolver(_default_rules(), self.report)
        plan = resolver.resolve()
        final = resolver.apply_user_choices(
            plan, {"unknown-dir:utils": "service"},
        )
        svc = [L for L in final["architecture"]["layers"]
               if L["name"] == "service"][0]
        self.assertIn("src/utils/", svc["paths"])

    def test_apply_skip_is_noop(self) -> None:
        resolver = InitResolver(_default_rules(), self.report)
        plan = resolver.resolve()
        before = copy.deepcopy(plan.rules)
        final = resolver.apply_user_choices(plan, {"unknown-dir:utils": "skip"})
        self.assertEqual(final["scanner"]["exclude_dirs"],
                         before["scanner"]["exclude_dirs"])
        for layer in final["architecture"]["layers"]:
            self.assertNotIn("src/utils/", layer["paths"])

    def test_missing_answer_uses_default(self) -> None:
        resolver = InitResolver(_default_rules(), self.report)
        plan = resolver.resolve()
        # 不传任何答复 → 走 default_choice (skip) → 等于啥也没做
        final = resolver.apply_user_choices(plan, {})
        self.assertNotIn("utils", final["scanner"]["exclude_dirs"])


# ---------------------------------------------------------------------------
# Naming conflict
# ---------------------------------------------------------------------------


class NamingConflictTests(unittest.TestCase):

    def test_low_sample_skipped(self) -> None:
        # service 默认要 Service 后缀；样本只有 1 个，背离也不报
        report = _make_report(
            layers=[LayerFinding(name="service", path="src/api/",
                                 file_count=1, sample_names=["userApi"])],
            naming=[NamingFinding(
                layer="service", sample_size=1,
                pascal_ratio=0.0, camel_ratio=1.0,
                use_prefix_ratio=0.0, service_suffix_ratio=0.0,
            )],
        )
        plan = resolve_init(_default_rules(), report)
        self.assertFalse(
            any(c.id == "naming:service" for c in plan.conflicts),
        )

    def test_naming_conflict_emitted_when_diverge(self) -> None:
        # service 应该 80% Service 后缀；实际 0% → 报 conflict
        report = _make_report(
            layers=[LayerFinding(name="service", path="src/api/",
                                 file_count=5, sample_names=[])],
            naming=[NamingFinding(
                layer="service", sample_size=5,
                pascal_ratio=0.0, camel_ratio=1.0,
                use_prefix_ratio=0.0, service_suffix_ratio=0.0,
            )],
        )
        plan = resolve_init(_default_rules(), report)
        c = next(c for c in plan.conflicts if c.id == "naming:service")
        keys = {ch.key for ch in c.choices}
        self.assertIn("keep-default", keys)
        self.assertIn("disable", keys)
        # camel_ratio=1.0 → 应建议采纳 camelCase
        self.assertTrue(any(k == "adopt:camelCase" for k in keys), keys)

    def test_apply_disable_clears_naming(self) -> None:
        report = _make_report(
            layers=[LayerFinding(name="service", path="src/api/",
                                 file_count=5, sample_names=[])],
            naming=[NamingFinding(
                layer="service", sample_size=5,
                pascal_ratio=0.0, camel_ratio=1.0,
                use_prefix_ratio=0.0, service_suffix_ratio=0.0,
            )],
        )
        resolver = InitResolver(_default_rules(), report)
        plan = resolver.resolve()
        final = resolver.apply_user_choices(plan, {"naming:service": "disable"})
        self.assertNotIn("service", final["naming"])

    def test_apply_adopt_changes_style(self) -> None:
        report = _make_report(
            layers=[LayerFinding(name="service", path="src/api/",
                                 file_count=5, sample_names=[])],
            naming=[NamingFinding(
                layer="service", sample_size=5,
                pascal_ratio=0.0, camel_ratio=1.0,
                use_prefix_ratio=0.0, service_suffix_ratio=0.0,
            )],
        )
        resolver = InitResolver(_default_rules(), report)
        plan = resolver.resolve()
        final = resolver.apply_user_choices(
            plan, {"naming:service": "adopt:camelCase"},
        )
        self.assertEqual(final["naming"]["service"], "camelCase")


# ---------------------------------------------------------------------------
# Info notes
# ---------------------------------------------------------------------------


class InfoNotesTests(unittest.TestCase):

    def test_framework_and_pm_recorded(self) -> None:
        report = _make_report(
            frameworks=["react"], package_managers=["pnpm"], has_eslint=True,
            gitignore_has_harness=False,
        )
        plan = resolve_init(_default_rules(), report)
        joined = "\n".join(plan.info_notes)
        self.assertIn("react", joined)
        self.assertIn("pnpm", joined)
        self.assertIn("ESLint", joined)
        self.assertIn(".gitignore", joined)

    def test_gitignore_already_set_no_note(self) -> None:
        report = _make_report(gitignore_has_harness=True)
        plan = resolve_init(_default_rules(), report)
        self.assertFalse(any(".gitignore" in n for n in plan.info_notes))


# ---------------------------------------------------------------------------
# 综合
# ---------------------------------------------------------------------------


class FullReportSmokeTests(unittest.TestCase):

    def test_note_h5_like_report(self) -> None:
        # 模拟 note-h5 真实场景：5 个 layer 全到位，别名一致
        report = _make_report(
            aliases=[AliasFinding(prefix="@/", target="src/",
                                  source_file="tsconfig.json")],
            layers=[
                LayerFinding(name="component", path="src/components/",
                             file_count=2, sample_names=["UserTable"]),
                LayerFinding(name="hook", path="src/hooks/",
                             file_count=1, sample_names=["useTodos"]),
                LayerFinding(name="service", path="src/api/",
                             file_count=1, sample_names=["todoService"]),
                LayerFinding(name="type", path="src/types/",
                             file_count=1, sample_names=["todo"]),
                # page 在默认 rules 里是和 component 同层（src/pages/ 也属 component.paths）
                # 这里模拟探测把 src/pages/ 标成独立 page layer 也不报错
            ],
            naming=[
                NamingFinding(layer="component", sample_size=1, pascal_ratio=1.0,
                              camel_ratio=0.0, use_prefix_ratio=0.0,
                              service_suffix_ratio=0.0),
            ],
            frameworks=["react"], package_managers=["npm"],
        )
        plan = resolve_init(_default_rules(), report)
        # 无 unknown，naming 样本小，不应有 conflict
        self.assertEqual(plan.conflicts, [])
        # adopted_notes 至少应提到别名沿用默认
        self.assertTrue(any("别名" in n for n in plan.adopted_notes))


if __name__ == "__main__":
    unittest.main()
