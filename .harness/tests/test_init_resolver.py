"""init_resolver 单元测试 —— harness init 冲突协商层。

覆盖点：
- 别名一致 → 静默采纳，写 adopted_notes
- 别名不一致（单个 / 多个）→ 写进 scanner.import_alias / import_aliases
- layer 路径与默认不同 → 自动采纳探测值
- src/ 下未识别目录 → 抛 unknown-dir conflict，含 layer/new-layer/ignore/skip 选项
- apply_user_choices: ignore → 加 exclude_dirs；选 layer → 进对应 paths
- apply_user_choices: new-layer:<name> → 追加新 architecture.layers 条目
- 0 ts/tsx 目录 default_choice = "ignore"（C3）
- 命名风格背离 → 抛 naming conflict；sample_size<3 不打扰
- info_notes 含框架 / 包管理器 / gitignore 建议
- apply_user_choices 缺省项走 default_choice
- 双栈布局 source_root 采纳（C1）
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
        # 应至少给出几个 layer 选项 + 新建 layer + ignore + skip
        self.assertIn("new-layer:utils", keys)
        self.assertIn("ignore", keys)
        self.assertIn("skip", keys)
        self.assertIn("hook", keys)
        self.assertIn("建议", c.detail)
        self.assertIn("util/helper/lib", c.detail)

    def test_unknown_dir_suggests_ignore_for_asset_like_dir(self) -> None:
        report = _make_report(layers=[
            LayerFinding(name="unknown", path="src/assets/", file_count=8,
                         sample_names=["logo", "empty", "icon"]),
        ])
        plan = resolve_init(_default_rules(), report)
        c = next(c for c in plan.conflicts if c.id == "unknown-dir:assets")
        self.assertEqual(c.default_choice, "ignore")
        self.assertIn("建议 ignore", c.detail)
        self.assertIn("资源目录", c.detail)

    def test_unknown_dir_suggests_component_from_pascal_samples(self) -> None:
        report = _make_report(layers=[
            LayerFinding(name="unknown", path="src/widgets/", file_count=3,
                         sample_names=["UserCard", "TeamPanel", "BudgetTable"]),
        ])
        plan = resolve_init(_default_rules(), report)
        c = next(c for c in plan.conflicts if c.id == "unknown-dir:widgets")
        self.assertEqual(c.default_choice, "skip")
        self.assertIn("建议映射为 component", c.detail)

    def test_unknown_dir_suggests_hook_from_use_samples(self) -> None:
        report = _make_report(layers=[
            LayerFinding(name="unknown", path="src/custom/", file_count=3,
                         sample_names=["useFoo", "useBar", "useBaz"]),
        ])
        plan = resolve_init(_default_rules(), report)
        c = next(c for c in plan.conflicts if c.id == "unknown-dir:custom")
        self.assertEqual(c.default_choice, "skip")
        self.assertIn("建议映射为 hook", c.detail)

    def test_apply_ignore_adds_to_exclude_dirs(self) -> None:
        resolver = InitResolver(_default_rules(), self.report)
        plan = resolver.resolve()
        final = resolver.apply_user_choices(plan, {"unknown-dir:utils": "ignore"})
        self.assertIn("src/utils/", final["scanner"]["exclude_dirs"])

    def test_ignore_preserves_dual_stack_full_path(self) -> None:
        """ignore 双栈 unknown-dir 时写完整路径，而不是裸目录名。"""
        report = _make_report(layers=[
            LayerFinding(name="unknown", path="src/frontend/utils/", file_count=2,
                         sample_names=["fmt"]),
        ])
        resolver = InitResolver(_default_rules(), report)
        plan = resolver.resolve()
        final = resolver.apply_user_choices(
            plan, {"unknown-dir:utils": "ignore"})
        excludes = final["scanner"]["exclude_dirs"]
        self.assertIn("src/frontend/utils/", excludes)
        self.assertNotIn("utils", excludes)

    def test_apply_missing_layer_name_creates_layer(self) -> None:
        """防御性兜底:未知 layer choice 不静默丢失。"""
        resolver = InitResolver(_default_rules(), self.report)
        plan = resolver.resolve()
        final = resolver.apply_user_choices(
            plan, {"unknown-dir:utils": "domain"})
        layer = next(L for L in final["architecture"]["layers"]
                     if L["name"] == "domain")
        self.assertIn("src/utils/", layer["paths"])

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

    def test_custom_layer_in_rules_appears_as_choice(self) -> None:
        """B1: default_rules 里加自定义 layer → unknown-dir conflict 选项包含它。"""
        custom_rules = _default_rules()
        custom_rules["architecture"]["layers"].append({
            "name": "widget",
            "paths": ["src/widgets/"],
            "can_import": ["hook", "service", "type"],
        })
        plan = resolve_init(custom_rules, self.report)
        c = next(c for c in plan.conflicts if c.id == "unknown-dir:utils")
        keys = {ch.key for ch in c.choices}
        self.assertIn("widget", keys,
                      "自定义 layer 名应作为 unknown-dir 的可选项")

    def test_apply_to_custom_layer(self) -> None:
        """映射到自定义 layer → paths 被追加。"""
        custom_rules = _default_rules()
        custom_rules["architecture"]["layers"].append({
            "name": "widget",
            "paths": ["src/widgets/"],
            "can_import": [],
        })
        resolver = InitResolver(custom_rules, self.report)
        plan = resolver.resolve()
        final = resolver.apply_user_choices(
            plan, {"unknown-dir:utils": "widget"})
        widget = [L for L in final["architecture"]["layers"]
                  if L["name"] == "widget"][0]
        self.assertIn("src/utils/", widget["paths"])

    def test_new_layer_creates_custom_layer(self) -> None:
        """C2: new-layer:<name> → architecture.layers 追加新自定义层。"""
        resolver = InitResolver(_default_rules(), self.report)
        plan = resolver.resolve()
        final = resolver.apply_user_choices(
            plan, {"unknown-dir:utils": "new-layer:utils"})
        names = [L["name"] for L in final["architecture"]["layers"]]
        self.assertIn("utils", names)
        new_layer = next(L for L in final["architecture"]["layers"]
                         if L["name"] == "utils")
        self.assertIn("src/utils/", new_layer["paths"])
        self.assertEqual(new_layer.get("can_import"), [])

    def test_zero_file_dir_default_ignore(self) -> None:
        """C3: 0 个 ts/tsx 文件的目录默认 ignore，不是 skip。"""
        report = _make_report(layers=[
            LayerFinding(name="unknown", path="src/backend/", file_count=0,
                         sample_names=[]),
        ])
        plan = resolve_init(_default_rules(), report)
        c = next(c for c in plan.conflicts if c.id == "unknown-dir:backend")
        self.assertEqual(c.default_choice, "ignore")


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
        detail_text = "\n".join(ch.detail for ch in c.choices)
        self.assertIn("只在 AI 修改文件时触发", detail_text)
        self.assertIn("存量代码不会被扫描", detail_text)
        self.assertIn("新增代码按新风格约束", detail_text)

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

    def test_dual_stack_source_root_adopted(self) -> None:
        """C1: probe 发现双栈 → source_root 被改成子目录。"""
        report = _make_report(
            notes=["dual-stack-hint:src/frontend"],
            layers=[
                LayerFinding(name="unknown", path="src/frontend/module/",
                             file_count=5, sample_names=[]),
            ],
        )
        plan = resolve_init(_default_rules(), report)
        self.assertEqual(
            plan.rules["scanner"]["source_root"],
            "src/frontend",
        )
        self.assertTrue(any("source_root" in n for n in plan.adopted_notes))


if __name__ == "__main__":
    unittest.main()
