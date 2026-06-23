"""ast_parser 单元测试。"""

import tempfile
import unittest
from pathlib import Path

from . import _setup  # noqa: F401  → sys.path 注入

from lib.ast_parser import TypeScriptParser  # noqa: E402


def write_temp(content: str, suffix: str = ".tsx") -> Path:
    f = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8")
    f.write(content)
    f.close()
    return Path(f.name)


class TestTypeScriptParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parser = TypeScriptParser()

    def test_unsupported_extension_raises(self):
        with self.assertRaises(ValueError):
            self.parser.parse(Path("/tmp/foo.json"))

    def test_runtime_import(self):
        path = write_temp("import { x } from '@/api/foo';\n", ".ts")
        result = self.parser.parse(path)
        self.assertEqual(len(result.imports), 1)
        imp = result.imports[0]
        self.assertEqual(imp.source, "@/api/foo")
        self.assertFalse(imp.is_type_only)
        self.assertFalse(imp.is_reexport)
        self.assertEqual(imp.line, 1)

    def test_type_only_import(self):
        path = write_temp("import type { Foo } from '@/types';\n", ".ts")
        result = self.parser.parse(path)
        self.assertEqual(len(result.imports), 1)
        self.assertTrue(result.imports[0].is_type_only)

    def test_mixed_type_import_is_runtime(self):
        # `import { type A, B }` 中 B 是运行时；保守按 runtime
        path = write_temp("import { type A, B } from '@/x';\n", ".ts")
        result = self.parser.parse(path)
        self.assertFalse(result.imports[0].is_type_only)

    def test_reexport_from(self):
        path = write_temp("export { x } from '@/y';\n", ".ts")
        result = self.parser.parse(path)
        self.assertEqual(len(result.imports), 1)
        self.assertTrue(result.imports[0].is_reexport)
        self.assertFalse(result.imports[0].is_type_only)

    def test_reexport_type_from(self):
        path = write_temp("export type { Foo } from '@/types';\n", ".ts")
        result = self.parser.parse(path)
        self.assertEqual(len(result.imports), 1)
        self.assertTrue(result.imports[0].is_reexport)
        self.assertTrue(result.imports[0].is_type_only)

    def test_export_function_with_jsx(self):
        path = write_temp(
            "export function Btn() { return <button/>; }\n",
            ".tsx",
        )
        result = self.parser.parse(path)
        self.assertEqual(len(result.exports), 1)
        exp = result.exports[0]
        self.assertEqual(exp.name, "Btn")
        self.assertEqual(exp.kind, "function")
        self.assertTrue(exp.returns_jsx)

    def test_export_const_arrow_jsx(self):
        path = write_temp(
            "export const C: React.FC = () => <div/>;\n",
            ".tsx",
        )
        result = self.parser.parse(path)
        self.assertEqual(result.exports[0].name, "C")
        self.assertTrue(result.exports[0].returns_jsx)

    def test_export_hook_no_jsx(self):
        path = write_temp(
            "export const useFoo = () => ({ a: 1 });\n",
            ".ts",
        )
        result = self.parser.parse(path)
        self.assertFalse(result.exports[0].returns_jsx)

    def test_export_interface_is_type_only(self):
        path = write_temp("export interface I { a: number }\n", ".ts")
        result = self.parser.parse(path)
        self.assertEqual(result.exports[0].kind, "interface")
        self.assertTrue(result.exports[0].is_type_only)

    def test_export_type_alias_is_type_only(self):
        path = write_temp("export type T = string;\n", ".ts")
        result = self.parser.parse(path)
        self.assertEqual(result.exports[0].kind, "type")
        self.assertTrue(result.exports[0].is_type_only)

    def test_export_default_function(self):
        path = write_temp(
            "export default function App() { return <div/>; }\n",
            ".tsx",
        )
        result = self.parser.parse(path)
        # default kind 优先（设计：is_default 覆盖 function）
        kinds = [e.kind for e in result.exports]
        self.assertIn("default", kinds)
        self.assertTrue(any(e.returns_jsx for e in result.exports))

    def test_export_enum_class(self):
        path = write_temp(
            "export enum E { A = 'a' }\nexport class K {}\n",
            ".ts",
        )
        result = self.parser.parse(path)
        kinds = sorted(e.kind for e in result.exports)
        self.assertEqual(kinds, ["class", "enum"])

    def test_parse_errors_recorded(self):
        path = write_temp("export const x =\n", ".ts")  # 故意残缺
        result = self.parser.parse(path)
        # tree-sitter 仍能产出树，但应有 ERROR/MISSING 节点
        self.assertTrue(len(result.parse_errors) > 0)


class TestHookCallExtraction(unittest.TestCase):
    """ast_parser hook_calls 抽取测试。"""

    @classmethod
    def setUpClass(cls):
        cls.parser = TypeScriptParser()

    def test_hook_call_in_component(self):
        path = write_temp(
            "import { useState } from 'react';\n"
            "export function MyComp() {\n"
            "  const [x, setX] = useState(0);\n"
            "  return <div>{x}</div>;\n"
            "}\n",
            ".tsx",
        )
        result = self.parser.parse(path)
        self.assertEqual(len(result.hook_calls), 1)
        hc = result.hook_calls[0]
        self.assertEqual(hc.callee, "useState")
        self.assertEqual(hc.in_function, "MyComp")
        self.assertEqual(hc.in_function_kind, "function")
        self.assertFalse(hc.in_branch)

    def test_hook_call_in_custom_hook_arrow(self):
        path = write_temp(
            "import { useState } from 'react';\n"
            "export const useCounter = () => {\n"
            "  const [x, setX] = useState(0);\n"
            "  return [x, setX] as const;\n"
            "};\n",
            ".ts",
        )
        result = self.parser.parse(path)
        self.assertEqual(len(result.hook_calls), 1)
        hc = result.hook_calls[0]
        self.assertEqual(hc.in_function, "useCounter")
        self.assertEqual(hc.in_function_kind, "arrow")

    def test_hook_call_top_level(self):
        path = write_temp(
            "import { useState } from 'react';\n"
            "const x = useState(0);\n",
            ".ts",
        )
        result = self.parser.parse(path)
        self.assertEqual(len(result.hook_calls), 1)
        self.assertEqual(result.hook_calls[0].in_function_kind, "top_level")

    def test_hook_call_in_plain_function(self):
        path = write_temp(
            "import { useState } from 'react';\n"
            "function helper() {\n"
            "  return useState(0);\n"
            "}\n",
            ".ts",
        )
        result = self.parser.parse(path)
        self.assertEqual(result.hook_calls[0].in_function, "helper")
        self.assertEqual(result.hook_calls[0].in_function_kind, "function")

    def test_hook_call_in_if_branch(self):
        path = write_temp(
            "import { useState } from 'react';\n"
            "export function MyComp(p: { a: boolean }) {\n"
            "  if (p.a) {\n"
            "    const [x] = useState(0);\n"
            "  }\n"
            "  return null;\n"
            "}\n",
            ".tsx",
        )
        result = self.parser.parse(path)
        self.assertEqual(len(result.hook_calls), 1)
        self.assertTrue(result.hook_calls[0].in_branch)

    def test_hook_call_in_for_loop(self):
        path = write_temp(
            "import { useState } from 'react';\n"
            "export function MyComp() {\n"
            "  for (let i = 0; i < 3; i++) {\n"
            "    useState(i);\n"
            "  }\n"
            "  return null;\n"
            "}\n",
            ".tsx",
        )
        result = self.parser.parse(path)
        self.assertTrue(result.hook_calls[0].in_branch)

    def test_hook_call_short_circuit_right(self):
        path = write_temp(
            "import { useState } from 'react';\n"
            "export function C(p: { ok: boolean }) {\n"
            "  const x = p.ok && useState(0);\n"
            "  return null;\n"
            "}\n",
            ".tsx",
        )
        result = self.parser.parse(path)
        self.assertEqual(len(result.hook_calls), 1)
        self.assertTrue(result.hook_calls[0].in_branch)

    def test_non_hook_identifier_excluded(self):
        # useless / user / use（不接大写）不应被识别为 hook
        path = write_temp(
            "function f() { useless(); user(); use(); }\n",
            ".ts",
        )
        result = self.parser.parse(path)
        self.assertEqual(result.hook_calls, [])

    def test_member_use_call_excluded(self):
        # foo.useState() 不识别（成员调用通常不是 React hook 本体）
        path = write_temp(
            "function f() { foo.useState(0); }\n",
            ".ts",
        )
        result = self.parser.parse(path)
        self.assertEqual(result.hook_calls, [])

    def test_multiple_hook_calls(self):
        path = write_temp(
            "import { useState, useEffect } from 'react';\n"
            "export function C() {\n"
            "  const [x] = useState(0);\n"
            "  useEffect(() => {}, []);\n"
            "  return null;\n"
            "}\n",
            ".tsx",
        )
        result = self.parser.parse(path)
        callees = sorted(h.callee for h in result.hook_calls)
        self.assertEqual(callees, ["useEffect", "useState"])

    def test_nested_function_resets_branch(self):
        # 外层 if 内定义一个新函数，新函数体内的 hook 不算"上层分支"
        path = write_temp(
            "import { useState } from 'react';\n"
            "export function C(p: { a: boolean }) {\n"
            "  if (p.a) {\n"
            "    function useInner() { return useState(0); }\n"
            "    useInner();\n"
            "  }\n"
            "  return null;\n"
            "}\n",
            ".tsx",
        )
        result = self.parser.parse(path)
        # 应该至少有 useState 这条；它的 in_function=useInner，in_branch=False
        inner_calls = [h for h in result.hook_calls if h.callee == "useState"]
        self.assertEqual(len(inner_calls), 1)
        self.assertEqual(inner_calls[0].in_function, "useInner")
        self.assertFalse(inner_calls[0].in_branch)


if __name__ == "__main__":
    unittest.main()
