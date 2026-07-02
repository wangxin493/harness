"""probe.py 单元测试 —— harness init 探测层。

覆盖点：
- tsconfig.json paths 别名探测（含带 // /* */ 注释 + 尾随逗号的 JSONC）
- 字符串字面量内的 /* 不被当作块注释起点（典型坑："@/*"）
- package.json 框架/包管理器探测
- src/ 顶层 layer 推断 + unknown 标记
- 命名风格统计（PascalCase / camelCase / use 前缀 / Service 后缀）
- .eslintrc / package.json eslintConfig 探测
- .gitignore 是否含 .harness/* 条目
- 无 src/、无 tsconfig、无 package.json 的退化场景
"""

import json
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path

from . import _setup  # noqa: F401

from lib.probe import (  # noqa: E402
    ProjectProbe,
    _strip_jsonc_comments,
    probe_project,
)


class ProbeFixture:
    """临时项目工厂。"""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="harness-probe-"))

    def write(self, rel: str, content: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


class StripJsoncCommentsTests(unittest.TestCase):
    """关键：注释剥离必须尊重字符串字面量。"""

    def test_line_comment(self) -> None:
        raw = '{\n  // hello\n  "a": 1\n}'
        out = _strip_jsonc_comments(raw)
        self.assertEqual(json.loads(out), {"a": 1})

    def test_block_comment(self) -> None:
        raw = '{ /* hello */ "a": 1 }'
        self.assertEqual(json.loads(_strip_jsonc_comments(raw)), {"a": 1})

    def test_multiline_block_comment(self) -> None:
        raw = '{\n/* one\ntwo */\n"a": 1\n}'
        self.assertEqual(json.loads(_strip_jsonc_comments(raw)), {"a": 1})

    def test_slash_star_inside_string_literal(self) -> None:
        # "@/*" 这种合法字符串里的 /* 不能触发块注释
        raw = '{"paths": {"@/*": ["src/*"]}}'
        out = _strip_jsonc_comments(raw)
        self.assertEqual(json.loads(out), {"paths": {"@/*": ["src/*"]}})

    def test_double_slash_inside_string(self) -> None:
        raw = '{"url": "http://example.com"}'
        self.assertEqual(
            json.loads(_strip_jsonc_comments(raw)),
            {"url": "http://example.com"},
        )

    def test_escaped_quote_inside_string(self) -> None:
        raw = r'{"s": "a\"b // not comment"}'
        out = _strip_jsonc_comments(raw)
        self.assertEqual(json.loads(out), {"s": 'a"b // not comment'})

    def test_unterminated_block_comment_safe(self) -> None:
        # 不应抛异常；返回截断结果即可
        raw = '{ /* unterminated'
        out = _strip_jsonc_comments(raw)
        self.assertTrue(out.startswith("{"))


class TsconfigProbeTests(unittest.TestCase):

    def setUp(self) -> None:
        self.fx = ProbeFixture()

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_basic_alias(self) -> None:
        self.fx.write("tsconfig.json", json.dumps({
            "compilerOptions": {
                "baseUrl": ".",
                "paths": {"@/*": ["src/*"]},
            },
        }))
        report = ProjectProbe(self.fx.root).run()
        self.assertTrue(report.is_typescript)
        self.assertTrue(report.has_tsconfig)
        self.assertEqual(len(report.aliases), 1)
        a = report.aliases[0]
        self.assertEqual(a.prefix, "@/")
        self.assertEqual(a.target, "src/")
        self.assertEqual(a.source_file, "tsconfig.json")

    def test_jsonc_alias_with_comments(self) -> None:
        # 带注释 + 尾随逗号；同时包含 "@/*"（容易把 /* 误判为注释起点）
        raw = textwrap.dedent("""\
            {
              // 项目根 tsconfig
              "compilerOptions": {
                "baseUrl": ".",
                /* paths 配置 */
                "paths": {
                  "@/*": ["src/*"],
                },
              },
            }
        """)
        self.fx.write("tsconfig.json", raw)
        report = ProjectProbe(self.fx.root).run()
        self.assertEqual(len(report.aliases), 1)
        self.assertEqual(report.aliases[0].prefix, "@/")
        self.assertEqual(report.aliases[0].target, "src/")

    def test_baseurl_offset(self) -> None:
        # baseUrl = "./app" 时，"~/*" → "app/lib/*"
        self.fx.write("tsconfig.json", json.dumps({
            "compilerOptions": {
                "baseUrl": "./app",
                "paths": {"~/*": ["lib/*"]},
            },
        }))
        report = ProjectProbe(self.fx.root).run()
        self.assertEqual(len(report.aliases), 1)
        self.assertEqual(report.aliases[0].prefix, "~/")
        self.assertEqual(report.aliases[0].target, "app/lib/")

    def test_no_tsconfig(self) -> None:
        report = ProjectProbe(self.fx.root).run()
        self.assertFalse(report.has_tsconfig)
        self.assertEqual(report.aliases, [])

    def test_malformed_tsconfig_records_note(self) -> None:
        self.fx.write("tsconfig.json", "{ this is not json")
        report = ProjectProbe(self.fx.root).run()
        self.assertTrue(report.has_tsconfig)
        self.assertTrue(
            any("tsconfig.json" in n for n in report.notes),
            f"expected a note about tsconfig parse failure; notes={report.notes}",
        )


class PackageJsonProbeTests(unittest.TestCase):

    def setUp(self) -> None:
        self.fx = ProbeFixture()

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_react_detected(self) -> None:
        self.fx.write("package.json", json.dumps({
            "dependencies": {"react": "^18.0.0", "react-dom": "^18.0.0"},
        }))
        report = ProjectProbe(self.fx.root).run()
        self.assertIn("react", report.frameworks)

    def test_vue_detected(self) -> None:
        self.fx.write("package.json", json.dumps({
            "dependencies": {"vue": "^3.4.0"},
        }))
        report = ProjectProbe(self.fx.root).run()
        self.assertIn("vue", report.frameworks)

    def test_typescript_devdep_marks_ts(self) -> None:
        self.fx.write("package.json", json.dumps({
            "devDependencies": {"typescript": "^5.0.0"},
        }))
        report = ProjectProbe(self.fx.root).run()
        self.assertTrue(report.is_typescript)

    def test_lockfile_pnpm(self) -> None:
        self.fx.write("package.json", "{}")
        self.fx.write("pnpm-lock.yaml", "lockfileVersion: 6.0\n")
        report = ProjectProbe(self.fx.root).run()
        self.assertIn("pnpm", report.package_managers)

    def test_lockfile_npm(self) -> None:
        self.fx.write("package.json", "{}")
        self.fx.write("package-lock.json", "{}")
        report = ProjectProbe(self.fx.root).run()
        self.assertIn("npm", report.package_managers)

    def test_package_manager_field(self) -> None:
        self.fx.write("package.json", json.dumps({
            "packageManager": "pnpm@8.10.0",
        }))
        report = ProjectProbe(self.fx.root).run()
        self.assertIn("pnpm", report.package_managers)

    def test_malformed_package_json(self) -> None:
        self.fx.write("package.json", "{not json")
        report = ProjectProbe(self.fx.root).run()
        self.assertTrue(any("package.json" in n for n in report.notes))


class SrcLayersProbeTests(unittest.TestCase):

    def setUp(self) -> None:
        self.fx = ProbeFixture()

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_layer_dirs_detected(self) -> None:
        self.fx.write("src/components/Foo.tsx", "export const Foo = () => null;")
        self.fx.write("src/hooks/useFoo.ts", "export const useFoo = () => 1;")
        self.fx.write("src/api/fooService.ts", "export const fooService = () => 1;")
        self.fx.write("src/types/foo.ts", "export type Foo = number;")
        self.fx.write("src/pages/HomePage.tsx", "export default function() {return null}")
        report = ProjectProbe(self.fx.root).run()
        names = {L.name for L in report.layers}
        self.assertEqual(
            names,
            {"component", "hook", "service", "type", "page"},
        )

    def test_js_jsx_files_count_as_source_files(self) -> None:
        self.fx.write("src/components/Foo.jsx", "export const Foo = () => <div/>;")
        self.fx.write("src/api/fooService.js", "export const fooService = () => 1;")
        report = ProjectProbe(self.fx.root).run()
        comp = next(L for L in report.layers if L.path == "src/components/")
        svc = next(L for L in report.layers if L.path == "src/api/")
        self.assertEqual(comp.file_count, 1)
        self.assertEqual(svc.file_count, 1)
        self.assertIn("Foo", comp.sample_names)
        self.assertIn("fooService", svc.sample_names)

    def test_file_count_not_limited_by_naming_sample_limit(self) -> None:
        for i in range(65):
            self.fx.write(f"src/components/C{i}.tsx", "export const X = () => null;")
        report = ProjectProbe(self.fx.root).run()
        comp = next(L for L in report.layers if L.name == "component")
        self.assertEqual(comp.file_count, 65)
        self.assertLessEqual(len(comp.sample_names), 10)

    def test_unknown_layer_marked(self) -> None:
        self.fx.write("src/weird/whatever.ts", "export const x = 1;")
        report = ProjectProbe(self.fx.root).run()
        self.assertTrue(any(L.name == "unknown" for L in report.layers))

    def test_dual_stack_source_root_hint(self) -> None:
        self.fx.write("src/frontend/module/Foo.ts", "export const x = 1;")
        self.fx.write("src/backend/server.js", "module.exports = {};")
        report = ProjectProbe(self.fx.root).run()
        self.assertIn("dual-stack-hint:src/frontend", report.notes)
        self.assertTrue(
            any(L.path == "src/frontend/module/" and L.name == "unknown"
                for L in report.layers),
        )
        self.assertFalse(any(L.path == "src/backend/" for L in report.layers))

    def test_no_src(self) -> None:
        report = ProjectProbe(self.fx.root).run()
        self.assertEqual(report.layers, [])

    def test_singular_service_dir_detected(self) -> None:
        self.fx.write("src/service/userApi.ts", "export const userApi = () => 1;")
        self.fx.write("src/components/Foo.tsx", "export const Foo = () => null;")
        report = ProjectProbe(self.fx.root).run()
        self.assertTrue(
            any(L.path == "src/service/" and L.name == "service"
                for L in report.layers),
        )

    def test_alternate_layer_names(self) -> None:
        # views / composables / apis / models 应该被识别成对应 layer
        self.fx.write("src/views/HomeView.tsx", "export default () => null;")
        self.fx.write("src/composables/useThing.ts", "export const useThing = () => 1;")
        self.fx.write("src/apis/userApi.ts", "export const userApi = () => 1;")
        self.fx.write("src/models/User.ts", "export type User = {};")
        report = ProjectProbe(self.fx.root).run()
        kinds = {L.name for L in report.layers}
        self.assertEqual(kinds, {"page", "hook", "service", "type"})

    def test_sub_layer_convention_hint_populated_for_unknown_dir(self) -> None:
        # module/ 是 unknown 层，内部有 components/ 和 hooks/ 子目录
        self.fx.write("src/module/UserModule/components/UserCard.tsx",
                      "export const UserCard = () => null;")
        self.fx.write("src/module/UserModule/hooks/useUser.ts",
                      "export const useUser = () => null;")
        self.fx.write("src/module/UserModule/utils/format.ts",
                      "export const format = () => null;")
        report = ProjectProbe(self.fx.root).run()
        hint = report.sub_layer_convention_hint
        self.assertEqual(hint.get("components"), "component")
        self.assertEqual(hint.get("hooks"), "hook")
        self.assertEqual(hint.get("utils"), "util")

    def test_sub_layer_convention_hint_empty_when_no_unknown(self) -> None:
        # 全部一级目录都有明确层名，无 unknown → hint 为空
        self.fx.write("src/components/Foo.tsx", "export const Foo = () => null;")
        self.fx.write("src/hooks/useFoo.ts", "export const useFoo = () => null;")
        report = ProjectProbe(self.fx.root).run()
        self.assertEqual(report.sub_layer_convention_hint, {})

    def test_sub_layer_convention_hint_not_duplicate(self) -> None:
        # 多个 unknown 目录里都有 components/，hint 只记录一次
        self.fx.write("src/moduleA/Foo/components/A.tsx", "export const A = () => null;")
        self.fx.write("src/moduleB/Bar/components/B.tsx", "export const B = () => null;")
        report = ProjectProbe(self.fx.root).run()
        hint = report.sub_layer_convention_hint
        # components 出现多次但 hint 里只有一条
        self.assertEqual(list(hint.keys()).count("components"), 1)


class NamingProbeTests(unittest.TestCase):

    def setUp(self) -> None:
        self.fx = ProbeFixture()

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_component_pascal(self) -> None:
        for n in ("Foo", "Bar", "BazQux"):
            self.fx.write(f"src/components/{n}.tsx", "export const X = () => null;")
        report = ProjectProbe(self.fx.root).run()
        comp_naming = [n for n in report.naming if n.layer == "component"][0]
        self.assertGreaterEqual(comp_naming.pascal_ratio, 0.99)

    def test_hook_use_prefix(self) -> None:
        for n in ("useFoo", "useBar", "useBaz"):
            self.fx.write(f"src/hooks/{n}.ts", "export const x = () => 1;")
        report = ProjectProbe(self.fx.root).run()
        hook_naming = [n for n in report.naming if n.layer == "hook"][0]
        self.assertGreaterEqual(hook_naming.use_prefix_ratio, 0.99)
        self.assertGreaterEqual(hook_naming.camel_ratio, 0.99)

    def test_service_suffix(self) -> None:
        for n in ("fooService", "barService"):
            self.fx.write(f"src/api/{n}.ts", "export const x = () => 1;")
        report = ProjectProbe(self.fx.root).run()
        svc_naming = [n for n in report.naming if n.layer == "service"][0]
        self.assertGreaterEqual(svc_naming.service_suffix_ratio, 0.99)


class EslintAndGitignoreTests(unittest.TestCase):

    def setUp(self) -> None:
        self.fx = ProbeFixture()

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_eslintrc_file(self) -> None:
        self.fx.write(".eslintrc.json", "{}")
        report = ProjectProbe(self.fx.root).run()
        self.assertTrue(report.has_eslint)

    def test_flat_eslint_config(self) -> None:
        self.fx.write("eslint.config.mjs", "export default [];")
        report = ProjectProbe(self.fx.root).run()
        self.assertTrue(report.has_eslint)

    def test_eslint_in_package_json(self) -> None:
        self.fx.write("package.json", json.dumps({"eslintConfig": {}}))
        report = ProjectProbe(self.fx.root).run()
        self.assertTrue(report.has_eslint)

    def test_no_eslint(self) -> None:
        report = ProjectProbe(self.fx.root).run()
        self.assertFalse(report.has_eslint)

    def test_gitignore_has_harness(self) -> None:
        self.fx.write(".gitignore", ".harness/.venv\n.harness/context\n")
        report = ProjectProbe(self.fx.root).run()
        self.assertTrue(report.gitignore_has_harness)

    def test_gitignore_missing_harness(self) -> None:
        self.fx.write(".gitignore", "node_modules\n")
        report = ProjectProbe(self.fx.root).run()
        self.assertFalse(report.gitignore_has_harness)


class ProbeProjectConvenienceTests(unittest.TestCase):
    """probe_project() 便捷函数应该等价于 ProjectProbe(...).run()。"""

    def setUp(self) -> None:
        self.fx = ProbeFixture()

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_returns_report(self) -> None:
        self.fx.write("package.json", json.dumps({
            "dependencies": {"react": "^18.0.0"},
        }))
        r = probe_project(self.fx.root)
        self.assertIn("react", r.frameworks)
        self.assertEqual(r.project_dir, str(self.fx.root.resolve()))


if __name__ == "__main__":
    unittest.main()
