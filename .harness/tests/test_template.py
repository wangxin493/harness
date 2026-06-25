"""template 单元测试。"""

import json
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path

from . import _setup  # noqa: F401

from lib.template import (  # noqa: E402
    TemplateError,
    TemplateGenerator,
    supported_kinds,
)


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
naming:
  component: PascalCase
  hook: camelCase-with-use-prefix
  service: camelCase-with-Service-suffix
  type: PascalCase
""")


class _Fixture:
    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="harness-template-")).resolve()
        (self.root / ".harness").mkdir()
        (self.root / ".harness" / "rules.yaml").write_text(
            RULES_YAML, encoding="utf-8")

    def gen(self) -> TemplateGenerator:
        return TemplateGenerator(project_dir=self.root)

    def write_context(self, **buckets):
        ctx_dir = self.root / ".harness" / "context"
        ctx_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "components": [], "hooks": [], "apis": [], "types": [],
            "files": [],
        }
        payload.update(buckets)
        (ctx_dir / "project-context.json").write_text(
            json.dumps(payload), encoding="utf-8")

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


# ============================================================================
# 命名校验 + 建议
# ============================================================================


class TestNameValidation(unittest.TestCase):
    def setUp(self):
        self.fx = _Fixture()
        self.gen = self.fx.gen()

    def tearDown(self):
        self.fx.cleanup()

    def test_supported_kinds(self):
        kinds = supported_kinds()
        self.assertEqual(
            set(kinds), {"component", "page", "hook", "service", "type"})

    # -- component --
    def test_component_pascal_ok(self):
        self.assertIsNone(self.gen.validate_name("component", "UserCard"))

    def test_component_lower_rejected(self):
        err = self.gen.validate_name("component", "userCard")
        self.assertIsNotNone(err)
        self.assertIn("PascalCase", err)

    def test_component_suggest(self):
        self.assertEqual(self.gen.suggest_name("component", "userCard"), "UserCard")
        self.assertEqual(self.gen.suggest_name("component", "user_card"), "UserCard")
        self.assertEqual(self.gen.suggest_name("component", "user card"), "UserCard")

    def test_component_empty_rejected(self):
        err = self.gen.validate_name("component", "")
        self.assertIsNotNone(err)

    # -- hook --
    def test_hook_use_prefix_ok(self):
        self.assertIsNone(self.gen.validate_name("hook", "useTodos"))

    def test_hook_no_prefix_rejected(self):
        err = self.gen.validate_name("hook", "getTodos")
        self.assertIsNotNone(err)
        self.assertIn("use", err)

    def test_hook_use_lowercase_after_rejected(self):
        # useTodos vs usetodos:第 4 个字符必须大写
        err = self.gen.validate_name("hook", "usetodos")
        self.assertIsNotNone(err)

    def test_hook_suggest(self):
        self.assertEqual(self.gen.suggest_name("hook", "getTodos"), "useGetTodos")
        self.assertEqual(self.gen.suggest_name("hook", "usetodos"), "useTodos")
        self.assertEqual(self.gen.suggest_name("hook", "todos"), "useTodos")

    # -- service --
    def test_service_suffix_ok(self):
        self.assertIsNone(self.gen.validate_name("service", "userService"))

    def test_service_no_suffix_rejected(self):
        err = self.gen.validate_name("service", "user")
        self.assertIsNotNone(err)
        self.assertIn("Service", err)

    def test_service_uppercase_first_rejected(self):
        err = self.gen.validate_name("service", "UserService")
        self.assertIsNotNone(err)

    def test_service_suggest(self):
        self.assertEqual(self.gen.suggest_name("service", "user"), "userService")
        self.assertEqual(self.gen.suggest_name("service", "userservice"), "userService")
        self.assertEqual(self.gen.suggest_name("service", "UserService"), "userService")

    # -- type --
    def test_type_pascal_ok(self):
        self.assertIsNone(self.gen.validate_name("type", "User"))

    def test_type_camel_rejected(self):
        err = self.gen.validate_name("type", "user")
        self.assertIsNotNone(err)

    # -- page --
    def test_page_pascal_ok(self):
        self.assertIsNone(self.gen.validate_name("page", "TodoListPage"))

    # -- unknown --
    def test_unknown_kind_raises(self):
        with self.assertRaises(TemplateError):
            self.gen.validate_name("widget", "X")


# ============================================================================
# 文件生成
# ============================================================================


class TestGenerate(unittest.TestCase):
    def setUp(self):
        self.fx = _Fixture()
        self.gen = self.fx.gen()

    def tearDown(self):
        self.fx.cleanup()

    def test_component_writes_to_components_dir(self):
        result = self.gen.generate("component", "UserCard")
        self.assertTrue(result.written)
        self.assertEqual(result.file, "src/components/UserCard.tsx")
        body = (self.fx.root / result.file).read_text(encoding="utf-8")
        self.assertIn("export const UserCard", body)
        self.assertIn("UserCardProps", body)
        self.assertIn("import type { FC }", body)

    def test_page_writes_to_pages_dir(self):
        result = self.gen.generate("page", "TodoPage")
        self.assertTrue(result.written)
        self.assertEqual(result.file, "src/pages/TodoPage.tsx")
        body = (self.fx.root / result.file).read_text(encoding="utf-8")
        self.assertIn("export const TodoPage", body)

    def test_hook_writes_to_hooks_dir(self):
        result = self.gen.generate("hook", "useTodos")
        self.assertTrue(result.written)
        self.assertEqual(result.file, "src/hooks/useTodos.ts")
        body = (self.fx.root / result.file).read_text(encoding="utf-8")
        self.assertIn("export const useTodos", body)
        self.assertIn("from 'react'", body)

    def test_service_writes_to_api_dir(self):
        result = self.gen.generate("service", "userService")
        self.assertTrue(result.written)
        self.assertEqual(result.file, "src/api/userService.ts")
        body = (self.fx.root / result.file).read_text(encoding="utf-8")
        self.assertIn("export const userService", body)
        self.assertIn("async", body)

    def test_type_writes_to_types_dir(self):
        result = self.gen.generate("type", "User")
        self.assertTrue(result.written)
        self.assertEqual(result.file, "src/types/User.ts")
        body = (self.fx.root / result.file).read_text(encoding="utf-8")
        self.assertIn("export interface User", body)

    def test_invalid_name_raises_with_suggestion(self):
        with self.assertRaises(TemplateError) as cm:
            self.gen.generate("hook", "getTodos")
        msg = str(cm.exception)
        self.assertIn("useGetTodos", msg)

    def test_existing_file_blocks(self):
        self.gen.generate("component", "UserCard")
        result = self.gen.generate("component", "UserCard")
        self.assertFalse(result.written)
        self.assertIn("已存在", result.skipped_reason or "")

    def test_existing_file_force_overwrites(self):
        first = self.gen.generate("component", "UserCard")
        # 改动 first 的内容,验证 --force 真的覆盖
        Path(self.fx.root / first.file).write_text("MUTATED", encoding="utf-8")
        result = self.gen.generate("component", "UserCard", force=True)
        self.assertTrue(result.written)
        body = (self.fx.root / result.file).read_text(encoding="utf-8")
        self.assertNotIn("MUTATED", body)
        self.assertIn("UserCard", body)

    def test_duplicate_name_in_context_warns(self):
        self.fx.write_context(hooks=[
            {"name": "useTodos", "file_path": "src/hooks/useTodos.ts"},
        ])
        # 同名同路径 → 不算冲突(同一个文件没创建过,只是 context 留着)
        # 这里测同名不同路径:用 --path 指到别处
        result = self.gen.generate("hook", "useTodos", path_override="src/hooks/other/")
        self.assertFalse(result.written)
        self.assertIn("已存在同名", result.skipped_reason or "")

    def test_duplicate_name_force_creates(self):
        self.fx.write_context(hooks=[
            {"name": "useTodos", "file_path": "src/hooks/useTodos.ts"},
        ])
        result = self.gen.generate(
            "hook", "useTodos", path_override="src/hooks/other/", force=True,
        )
        self.assertTrue(result.written)
        self.assertEqual(len(result.warnings), 1)

    def test_path_override(self):
        result = self.gen.generate(
            "component", "UserCard", path_override="src/components/users/",
        )
        self.assertTrue(result.written)
        self.assertEqual(result.file, "src/components/users/UserCard.tsx")

    def test_path_override_normalizes_backslash(self):
        result = self.gen.generate(
            "component", "UserCard", path_override="src\\\\components\\\\users\\\\",
        )
        self.assertTrue(result.written)

    def test_falls_back_to_default_dir_if_no_rules(self):
        # 删 rules.yaml → 走 default_dir
        (self.fx.root / ".harness" / "rules.yaml").unlink()
        gen = TemplateGenerator(project_dir=self.fx.root)
        result = gen.generate("component", "UserCard")
        self.assertTrue(result.written)
        self.assertEqual(result.file, "src/components/UserCard.tsx")

    def test_page_prefers_path_with_screen_substring(self):
        """B5: page kind 默认 prefer_path_contains=[page,screen,view]。

        rules.yaml 只有 src/screens/(没有 src/pages/) → page 自动落到 src/screens/。
        """
        rules = textwrap.dedent("""\
            architecture:
              layers:
                - name: component
                  paths: ["src/screens/", "src/components/"]
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
            naming:
              component: PascalCase
              hook: camelCase-with-use-prefix
              service: camelCase-with-Service-suffix
              type: PascalCase
        """)
        (self.fx.root / ".harness" / "rules.yaml").write_text(
            rules, encoding="utf-8")
        gen = TemplateGenerator(project_dir=self.fx.root)
        result = gen.generate("page", "TodoPage")
        self.assertTrue(result.written)
        # 子串 "screen" 命中 → 落到 src/screens/
        self.assertEqual(result.file, "src/screens/TodoPage.tsx")

    def test_custom_prefer_path_contains_overrides_default(self):
        """B5: rules.yaml templates.kinds.page.prefer_path_contains 自定义覆盖默认。"""
        rules = textwrap.dedent("""\
            architecture:
              layers:
                - name: component
                  paths: ["src/routes/", "src/components/"]
                  can_import: ["hook", "service", "type"]
                - name: hook
                  paths: ["src/hooks/"]
                  can_import: []
                - name: service
                  paths: ["src/api/"]
                  can_import: []
                - name: type
                  paths: ["src/types/"]
                  can_import: []
            templates:
              kinds:
                page:
                  prefer_path_contains: ["route"]
        """)
        (self.fx.root / ".harness" / "rules.yaml").write_text(
            rules, encoding="utf-8")
        gen = TemplateGenerator(project_dir=self.fx.root)
        result = gen.generate("page", "TodoPage")
        self.assertTrue(result.written)
        # 自定义 "route" 命中 src/routes/
        self.assertEqual(result.file, "src/routes/TodoPage.tsx")


if __name__ == "__main__":
    unittest.main()
