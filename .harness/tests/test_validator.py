"""validator 单元测试。"""

import json
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path

from . import _setup  # noqa: F401

from lib.validator import CodeValidator  # noqa: E402


RULES_YAML = textwrap.dedent("""\
architecture:
  layers:
    - name: component
      paths: ["src/components/"]
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
imports:
  forbidden_imports:
    - "@/services"
    - "@/api/mockApi"
  forbidden_suggestions:
    "@/services": "@/services 路径不存在，请使用 @/api"
    "@/api/mockApi": "禁止直接耦合 mock 实现，请通过 @/api/<service> 访问"
checks:
  hook_call_check:
    enabled: true
  name_similarity:
    enabled: true
    threshold: 0.8
""")


class ValidatorFixture:
    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="harness-validator-"))
        (self.root / ".harness").mkdir()
        (self.root / ".harness" / "rules.yaml").write_text(RULES_YAML, encoding="utf-8")
        (self.root / "src").mkdir()

    def write(self, rel: str, content: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def validator(self) -> CodeValidator:
        return CodeValidator(project_dir=self.root)

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


class TestCodeValidator(unittest.TestCase):
    def setUp(self):
        self.fx = ValidatorFixture()

    def tearDown(self):
        self.fx.cleanup()

    # ------------------------------------------------------------- 架构检查

    def test_service_cannot_import_component(self):
        self.fx.write(
            "src/api/itemService.ts",
            "import { Btn } from '@/components/Btn';\nexport const x = Btn;\n",
        )
        issues = self.fx.validator().validate_file("src/api/itemService.ts")
        rules = [i.rule_id for i in issues]
        self.assertIn("arch-service-import", rules)
        # 严重程度
        arch_issue = next(i for i in issues if i.rule_id == "arch-service-import")
        self.assertEqual(arch_issue.severity, "error")
        self.assertEqual(arch_issue.line, 1)

    def test_hook_cannot_import_component(self):
        self.fx.write(
            "src/hooks/useFoo.ts",
            "import { Btn } from '@/components/Btn';\nexport const useFoo = () => Btn;\n",
        )
        issues = self.fx.validator().validate_file("src/hooks/useFoo.ts")
        self.assertTrue(any(i.rule_id == "arch-hook-import" for i in issues))

    def test_component_can_import_hook(self):
        self.fx.write(
            "src/components/Btn.tsx",
            "import { useFoo } from '@/hooks/useFoo';\n"
            "export const Btn = () => { useFoo(); return <div/>; };\n",
        )
        issues = self.fx.validator().validate_file("src/components/Btn.tsx")
        # 不应有架构层错误
        arch_errs = [i for i in issues if i.category == "architecture"]
        self.assertEqual(arch_errs, [])

    def test_type_only_import_skipped_in_arch(self):
        # service 层 import type 自 components → 不算违规（运行时无依赖）
        self.fx.write(
            "src/api/itemService.ts",
            "import type { BtnProps } from '@/components/Btn';\n"
            "export const x: BtnProps = {} as any;\n",
        )
        issues = self.fx.validator().validate_file("src/api/itemService.ts")
        arch_errs = [i for i in issues if i.category == "architecture"]
        self.assertEqual(arch_errs, [])

    # ------------------------------------------------------------- 禁用导入

    def test_forbidden_import_services(self):
        self.fx.write(
            "src/components/A.tsx",
            "import { x } from '@/services/legacy';\n"
            "export const A = () => <div/>;\n",
        )
        issues = self.fx.validator().validate_file("src/components/A.tsx")
        forbidden = [i for i in issues if i.rule_id == "import-forbidden"]
        self.assertEqual(len(forbidden), 1)
        self.assertIn("@/services", forbidden[0].suggestion)

    def test_forbidden_import_mockapi(self):
        self.fx.write(
            "src/api/itemService.ts",
            "import { y } from '@/api/mockApi/foo';\n"
            "export const x = y;\n",
        )
        issues = self.fx.validator().validate_file("src/api/itemService.ts")
        self.assertTrue(any(i.rule_id == "import-forbidden" for i in issues))

    # --------------------------------------------------------------- 边界

    def test_file_not_found(self):
        issues = self.fx.validator().validate_file("src/missing.ts")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].rule_id, "file-not-found")

    def test_unknown_layer_no_arch_error(self):
        self.fx.write(
            "src/index.tsx",
            "import { Btn } from '@/components/Btn';\nexport const X = () => <Btn/>;\n",
        )
        issues = self.fx.validator().validate_file("src/index.tsx")
        # unknown layer：架构检查跳过；只剩一个 file-not-found 不在此处；不应有 arch-*
        arch_errs = [i for i in issues if i.category == "architecture"]
        self.assertEqual(arch_errs, [])

    def test_clean_file_no_issues(self):
        self.fx.write(
            "src/components/Btn.tsx",
            "import React from 'react';\nexport const Btn = () => <div/>;\n",
        )
        issues = self.fx.validator().validate_file("src/components/Btn.tsx")
        self.assertEqual(issues, [])


# ============================================================================
# Hook 调用规则
# ============================================================================


class TestHookCallCheck(unittest.TestCase):
    def setUp(self):
        self.fx = ValidatorFixture()

    def tearDown(self):
        self.fx.cleanup()

    def test_hook_call_in_service_layer_blocked(self):
        # service 层文件不能调用 useXxx
        self.fx.write(
            "src/api/userService.ts",
            "import { useState } from 'react';\n"
            "export function fetchUser() { return useState(0); }\n",
        )
        issues = self.fx.validator().validate_file("src/api/userService.ts")
        misplaced = [i for i in issues if i.rule_id == "hook-call-misplaced"]
        self.assertEqual(len(misplaced), 1)
        self.assertEqual(misplaced[0].severity, "error")

    def test_hook_call_in_component_ok(self):
        self.fx.write(
            "src/components/Foo.tsx",
            "import { useState } from 'react';\n"
            "export function Foo() {\n"
            "  const [x] = useState(0);\n"
            "  return <div>{x}</div>;\n"
            "}\n",
        )
        issues = self.fx.validator().validate_file("src/components/Foo.tsx")
        hook_issues = [i for i in issues if i.category == "hook"]
        self.assertEqual(hook_issues, [])

    def test_hook_call_in_custom_hook_ok(self):
        self.fx.write(
            "src/hooks/useFoo.ts",
            "import { useState } from 'react';\n"
            "export const useFoo = () => useState(0);\n",
        )
        issues = self.fx.validator().validate_file("src/hooks/useFoo.ts")
        hook_issues = [i for i in issues if i.category == "hook"]
        self.assertEqual(hook_issues, [])

    def test_hook_call_top_level_blocked(self):
        self.fx.write(
            "src/components/Foo.tsx",
            "import { useState } from 'react';\n"
            "const x = useState(0);\n"
            "export const Foo = () => <div/>;\n",
        )
        issues = self.fx.validator().validate_file("src/components/Foo.tsx")
        top = [i for i in issues if i.rule_id == "hook-call-top-level"]
        self.assertEqual(len(top), 1)

    def test_hook_call_in_plain_function_blocked(self):
        # 在 component 文件里有个普通函数 helper 调用 useState
        self.fx.write(
            "src/components/Foo.tsx",
            "import { useState } from 'react';\n"
            "function helper() { return useState(0); }\n"
            "export const Foo = () => { helper(); return <div/>; };\n",
        )
        issues = self.fx.validator().validate_file("src/components/Foo.tsx")
        plain = [i for i in issues if i.rule_id == "hook-call-in-plain-func"]
        self.assertEqual(len(plain), 1)

    def test_hook_call_conditional_blocked(self):
        self.fx.write(
            "src/components/Foo.tsx",
            "import { useState } from 'react';\n"
            "export function Foo(p: { ok: boolean }) {\n"
            "  if (p.ok) { const [x] = useState(0); }\n"
            "  return <div/>;\n"
            "}\n",
        )
        issues = self.fx.validator().validate_file("src/components/Foo.tsx")
        cond = [i for i in issues if i.rule_id == "hook-call-conditional"]
        self.assertEqual(len(cond), 1)

    def test_excluded_callee_not_blocked(self):
        # 写一个临时 rules.yaml 把 useRouter 放进 excluded_callees
        rules = textwrap.dedent("""\
        architecture:
          layers:
            - name: service
              paths: ["src/api/"]
              can_import: ["type"]
        imports:
          forbidden_imports: []
        checks:
          hook_call_check:
            enabled: true
            excluded_callees: ["useRouter"]
        """)
        (self.fx.root / ".harness" / "rules.yaml").write_text(rules, encoding="utf-8")
        self.fx.write(
            "src/api/foo.ts",
            "function f() { return useRouter(); }\n",
        )
        issues = self.fx.validator().validate_file("src/api/foo.ts")
        hook_issues = [i for i in issues if i.category == "hook"]
        self.assertEqual(hook_issues, [])

    def test_disabled_check_skips(self):
        rules = textwrap.dedent("""\
        architecture:
          layers:
            - name: service
              paths: ["src/api/"]
              can_import: ["type"]
        imports:
          forbidden_imports: []
        checks:
          hook_call_check:
            enabled: false
        """)
        (self.fx.root / ".harness" / "rules.yaml").write_text(rules, encoding="utf-8")
        self.fx.write(
            "src/api/foo.ts",
            "import { useState } from 'react';\n"
            "function f() { return useState(0); }\n",
        )
        issues = self.fx.validator().validate_file("src/api/foo.ts")
        hook_issues = [i for i in issues if i.category == "hook"]
        self.assertEqual(hook_issues, [])


# ============================================================================
# 命名相似度
# ============================================================================


class TestNameSimilarity(unittest.TestCase):
    def setUp(self):
        self.fx = ValidatorFixture()
        # 模拟 scan 产物：已有 UserTable / fetchUserService / useTodos
        ctx_dir = self.fx.root / ".harness" / "context"
        ctx_dir.mkdir(parents=True, exist_ok=True)
        (ctx_dir / "project-context.json").write_text(json.dumps({
            "schema_version": 1,
            "components": [
                {"name": "UserTable", "file_path": "src/components/UserTable.tsx"},
            ],
            "hooks": [
                {"name": "useTodos", "file_path": "src/hooks/useTodos.ts"},
            ],
            "apis": [
                {"name": "fetchUserService", "file_path": "src/api/userService.ts"},
            ],
            "types": [],
            "files": [],
        }), encoding="utf-8")

    def tearDown(self):
        self.fx.cleanup()

    def test_similar_component_name_warning(self):
        self.fx.write(
            "src/components/UserTables.tsx",
            "export const UserTables = () => <div/>;\n",
        )
        issues = self.fx.validator().validate_file("src/components/UserTables.tsx")
        sim = [i for i in issues if i.rule_id == "name-similarity"]
        self.assertEqual(len(sim), 1)
        self.assertIn("UserTable", sim[0].message)

    def test_same_name_different_file_warning(self):
        # 同名（UserTable）但在不同文件 → severity=warning
        self.fx.write(
            "src/components/UserTable2.tsx",
            "export const UserTable = () => <div/>;\n",
        )
        issues = self.fx.validator().validate_file("src/components/UserTable2.tsx")
        sim = [i for i in issues if i.rule_id == "name-similarity"]
        self.assertEqual(len(sim), 1)
        self.assertEqual(sim[0].severity, "warning")
        self.assertIn("同名", sim[0].message)

    def test_dissimilar_name_no_warning(self):
        self.fx.write(
            "src/components/Dashboard.tsx",
            "export const Dashboard = () => <div/>;\n",
        )
        issues = self.fx.validator().validate_file("src/components/Dashboard.tsx")
        sim = [i for i in issues if i.rule_id == "name-similarity"]
        self.assertEqual(sim, [])

    def test_self_no_false_positive(self):
        # 已在 context 里的文件再次校验自己 → 不报相似度
        self.fx.write(
            "src/components/UserTable.tsx",
            "export const UserTable = () => <div/>;\n",
        )
        issues = self.fx.validator().validate_file("src/components/UserTable.tsx")
        sim = [i for i in issues if i.rule_id == "name-similarity"]
        self.assertEqual(sim, [])

    def test_hook_similarity(self):
        self.fx.write(
            "src/hooks/useTodo.ts",  # 与 useTodos 仅差一个 s
            "export const useTodo = () => 1;\n",
        )
        issues = self.fx.validator().validate_file("src/hooks/useTodo.ts")
        sim = [i for i in issues if i.rule_id == "name-similarity"]
        self.assertEqual(len(sim), 1)

    def test_service_same_name_cross_file_is_info(self):
        # C6: service 层同名跨文件默认 info，不是 warning
        ctx_dir = self.fx.root / ".harness" / "context"
        ctx_dir.mkdir(parents=True, exist_ok=True)
        (ctx_dir / "project-context.json").write_text(json.dumps({
            "schema_version": 1,
            "components": [],
            "hooks": [],
            "apis": [
                {"name": "getButtonList",
                 "file_path": "src/api/approvalApi.ts"},
            ],
            "types": [],
            "files": [],
        }), encoding="utf-8")
        self.fx.write(
            "src/api/salaryApi.ts",
            "export const getButtonList = async () => {};\n",
        )
        issues = self.fx.validator().validate_file("src/api/salaryApi.ts")
        sim = [i for i in issues if i.rule_id == "name-similarity"]
        self.assertEqual(len(sim), 1)
        self.assertEqual(sim[0].severity, "info",
                         "service 层同名默认应为 info（C6 降噪）")

    def test_service_same_name_severity_overridable(self):
        # C6: rules.yaml 覆盖 same_name_severity.service=warning 时恢复 warning
        rules_path = self.fx.root / ".harness" / "rules.yaml"
        content = rules_path.read_text(encoding="utf-8")
        content += (
            "\n  name_similarity:\n"
            "    threshold: 0.8\n"
            "    same_name_severity:\n"
            "      service: warning\n"
        )
        rules_path.write_text(content, encoding="utf-8")
        ctx_dir = self.fx.root / ".harness" / "context"
        ctx_dir.mkdir(parents=True, exist_ok=True)
        (ctx_dir / "project-context.json").write_text(json.dumps({
            "schema_version": 1,
            "components": [],
            "hooks": [],
            "apis": [
                {"name": "getButtonList",
                 "file_path": "src/api/approvalApi.ts"},
            ],
            "types": [],
            "files": [],
        }), encoding="utf-8")
        self.fx.write(
            "src/api/salaryApi.ts",
            "export const getButtonList = async () => {};\n",
        )
        issues = self.fx.validator().validate_file("src/api/salaryApi.ts")
        sim = [i for i in issues if i.rule_id == "name-similarity"]
        self.assertEqual(len(sim), 1)
        self.assertEqual(sim[0].severity, "warning")

    def test_threshold_one_only_matches_exact_name(self):
        # threshold=1.0 不应关闭检查；应只报完全同名（Bug #8 修复验证）
        rules_path = self.fx.root / ".harness" / "rules.yaml"
        content = rules_path.read_text(encoding="utf-8")
        content += (
            "\n  name_similarity:\n"
            "    threshold: 1.0\n"
        )
        rules_path.write_text(content, encoding="utf-8")
        self.fx.write(
            "src/components/UserTable2.tsx",
            "export const UserTable = () => <div/>;\n",
        )
        issues = self.fx.validator().validate_file("src/components/UserTable2.tsx")
        sim = [i for i in issues if i.rule_id == "name-similarity"]
        # 完全同名应有 1 条；相似但不同名的不报
        self.assertEqual(len(sim), 1)
        self.assertIn("同名", sim[0].message)

    def test_threshold_zero_disables_check(self):
        # threshold=0 关闭
        rules = (self.fx.root / ".harness" / "rules.yaml").read_text(encoding="utf-8")
        rules = rules.replace("threshold: 0.8", "threshold: 0")
        (self.fx.root / ".harness" / "rules.yaml").write_text(rules, encoding="utf-8")
        self.fx.write(
            "src/components/UserTables.tsx",
            "export const UserTables = () => <div/>;\n",
        )
        issues = self.fx.validator().validate_file("src/components/UserTables.tsx")
        sim = [i for i in issues if i.rule_id == "name-similarity"]
        self.assertEqual(sim, [])

    def test_component_export_name_continues_past_anonymous_default(self):
        # Bug #10：匿名 default 不应阻止继续搜索后续命名 export
        from types import SimpleNamespace
        names = CodeValidator._extract_primary_export_names(
            "component",
            SimpleNamespace(exports=[
                SimpleNamespace(returns_jsx=False, kind="default", name="default"),
                SimpleNamespace(returns_jsx=True, kind="function", name="NamedCard"),
            ])
        )
        self.assertEqual(names, ["NamedCard"])



    def test_no_context_no_check(self):
        # 把 project-context.json 删了 → 无 issue（不报错）
        (self.fx.root / ".harness" / "context" / "project-context.json").unlink()
        self.fx.write(
            "src/components/UserTables.tsx",
            "export const UserTables = () => <div/>;\n",
        )
        issues = self.fx.validator().validate_file("src/components/UserTables.tsx")
        sim = [i for i in issues if i.rule_id == "name-similarity"]
        self.assertEqual(sim, [])



# ============================================================================
# P0: import alias 架构检查
# ============================================================================

RULES_YAML_ALIAS = textwrap.dedent("""\
architecture:
  layers:
    - name: component
      paths: ["src/frontend/components/"]
      can_import: ["hook", "service", "type"]
    - name: hook
      paths: ["src/frontend/hooks/"]
      can_import: ["service", "type"]
    - name: service
      paths: ["src/frontend/service/"]
      can_import: ["type"]
    - name: type
      paths: ["src/frontend/types/"]
      can_import: []
imports:
  forbidden_imports: []
scanner:
  source_root: src/frontend
  import_aliases:
    - prefix: "frontend/"
      target: "src/frontend/"
    - prefix: "/frontend/"
      target: "src/frontend/"
checks:
  hook_call_check:
    enabled: false
  name_similarity:
    enabled: false
  naming:
    enabled: false
""")


class TestImportAlias(unittest.TestCase):
    def setUp(self):
        self.fx = ValidatorFixture()
        (self.fx.root / ".harness" / "rules.yaml").write_text(
            RULES_YAML_ALIAS, encoding="utf-8"
        )

    def tearDown(self):
        self.fx.cleanup()

    def test_frontend_alias_service_imports_hook_blocked(self):
        """frontend/service/X 用 frontend/hooks/ 别名导入 hook → arch violation。"""
        self.fx.write(
            "src/frontend/service/itemService.ts",
            "import { useItem } from 'frontend/hooks/useItem';\n"
            "export const itemService = useItem;\n",
        )
        issues = self.fx.validator().validate_file(
            "src/frontend/service/itemService.ts"
        )
        arch = [i for i in issues if i.category == "architecture"]
        self.assertEqual(len(arch), 1, arch)
        self.assertEqual(arch[0].rule_id, "arch-service-import")

    def test_frontend_alias_component_imports_hook_allowed(self):
        """frontend/components/X 用 frontend/hooks/ 别名导入 hook → OK。"""
        self.fx.write(
            "src/frontend/components/Btn.tsx",
            "import { useItem } from 'frontend/hooks/useItem';\n"
            "export const Btn = () => null;\n",
        )
        issues = self.fx.validator().validate_file(
            "src/frontend/components/Btn.tsx"
        )
        arch = [i for i in issues if i.category == "architecture"]
        self.assertEqual(arch, [])

    def test_at_slash_still_works_without_alias_in_config(self):
        """@/ 默认映射在没有显式 alias 时仍生效。"""
        rules = textwrap.dedent("""\
        architecture:
          layers:
            - name: service
              paths: ["src/api/"]
              can_import: ["type"]
            - name: hook
              paths: ["src/hooks/"]
              can_import: ["type"]
        imports:
          forbidden_imports: []
        checks:
          hook_call_check:
            enabled: false
          name_similarity:
            enabled: false
          naming:
            enabled: false
        """)
        (self.fx.root / ".harness" / "rules.yaml").write_text(rules, encoding="utf-8")
        self.fx.write(
            "src/api/itemService.ts",
            "import { useFoo } from '@/hooks/useFoo';\n"
            "export const x = useFoo;\n",
        )
        issues = self.fx.validator().validate_file("src/api/itemService.ts")
        arch = [i for i in issues if i.category == "architecture"]
        self.assertEqual(len(arch), 1)
        self.assertEqual(arch[0].rule_id, "arch-service-import")

    def test_longer_alias_prefix_takes_priority(self):
        """alias 前缀较长的优先命中（/frontend/ 比 frontend/ 不受顺序影响）。"""
        self.fx.write(
            "src/frontend/service/foo.ts",
            "import { Bar } from '/frontend/hooks/useBar';\n"
            "export const foo = Bar;\n",
        )
        issues = self.fx.validator().validate_file(
            "src/frontend/service/foo.ts"
        )
        arch = [i for i in issues if i.category == "architecture"]
        self.assertEqual(len(arch), 1)
        self.assertEqual(arch[0].rule_id, "arch-service-import")

    def test_at_slash_uses_source_root_fallback(self):
        """未显式配置 alias 时，@/ 应映射到 scanner.source_root 而不是硬编码 src。"""
        rules = textwrap.dedent("""\
        architecture:
          layers:
            - name: service
              paths: ["src/frontend/service/"]
              can_import: ["type"]
            - name: hook
              paths: ["src/frontend/hooks/"]
              can_import: ["type"]
        imports:
          forbidden_imports: []
        scanner:
          source_root: src/frontend
        checks:
          hook_call_check:
            enabled: false
          name_similarity:
            enabled: false
          naming:
            enabled: false
        """)
        (self.fx.root / ".harness" / "rules.yaml").write_text(rules, encoding="utf-8")
        self.fx.write(
            "src/frontend/service/itemService.ts",
            "import { useFoo } from '@/hooks/useFoo';\n"
            "export const x = useFoo;\n",
        )
        issues = self.fx.validator().validate_file("src/frontend/service/itemService.ts")
        arch = [i for i in issues if i.category == "architecture"]
        self.assertEqual(len(arch), 1)
        self.assertEqual(arch[0].rule_id, "arch-service-import")

    def test_alias_target_list_format_supported(self):
        """兼容 tsconfig paths 常见的 list target 格式。"""
        rules = textwrap.dedent("""\
        architecture:
          layers:
            - name: service
              paths: ["src/api/"]
              can_import: ["type"]
            - name: hook
              paths: ["src/hooks/"]
              can_import: ["type"]
        imports:
          forbidden_imports: []
        scanner:
          import_aliases:
            "@/": ["src/*"]
        checks:
          hook_call_check:
            enabled: false
          name_similarity:
            enabled: false
          naming:
            enabled: false
        """)
        (self.fx.root / ".harness" / "rules.yaml").write_text(rules, encoding="utf-8")
        self.fx.write(
            "src/api/itemService.ts",
            "import { useFoo } from '@/hooks/useFoo';\n"
            "export const x = useFoo;\n",
        )
        issues = self.fx.validator().validate_file("src/api/itemService.ts")
        arch = [i for i in issues if i.category == "architecture"]
        self.assertEqual(len(arch), 1)
        self.assertEqual(arch[0].rule_id, "arch-service-import")


# ============================================================================
# P1: naming-violation 运行时检查
# ============================================================================

RULES_YAML_NAMING = textwrap.dedent("""\
architecture:
  layers:
    - name: component
      paths: ["src/components/"]
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
imports:
  forbidden_imports: []
naming:
  component: PascalCase
  hook: camelCase-with-use-prefix
  service: camelCase-with-Service-suffix
  type: PascalCase
checks:
  hook_call_check:
    enabled: false
  name_similarity:
    enabled: false
""")


class TestNamingViolation(unittest.TestCase):
    def setUp(self):
        self.fx = ValidatorFixture()
        (self.fx.root / ".harness" / "rules.yaml").write_text(
            RULES_YAML_NAMING, encoding="utf-8"
        )

    def tearDown(self):
        self.fx.cleanup()

    def test_component_camelCase_name_warning(self):
        self.fx.write(
            "src/components/userTable.tsx",
            "export const userTable = () => <div/>;\n",
        )
        issues = self.fx.validator().validate_file("src/components/userTable.tsx")
        naming = [i for i in issues if i.rule_id == "naming-component"]
        self.assertEqual(len(naming), 1)
        self.assertEqual(naming[0].severity, "warning")
        self.assertIn("PascalCase", naming[0].message)

    def test_component_pascal_name_ok(self):
        self.fx.write(
            "src/components/UserTable.tsx",
            "export const UserTable = () => <div/>;\n",
        )
        issues = self.fx.validator().validate_file("src/components/UserTable.tsx")
        naming = [i for i in issues if i.category == "naming"]
        self.assertEqual(naming, [])

    def test_hook_missing_use_prefix_warning(self):
        self.fx.write(
            "src/hooks/getTodos.ts",
            "export const getTodos = () => [];\n",
        )
        issues = self.fx.validator().validate_file("src/hooks/getTodos.ts")
        naming = [i for i in issues if i.rule_id == "naming-hook"]
        self.assertEqual(len(naming), 1)
        self.assertIn("use", naming[0].message)

    def test_hook_valid_use_prefix_ok(self):
        self.fx.write(
            "src/hooks/useTodos.ts",
            "export const useTodos = () => [];\n",
        )
        issues = self.fx.validator().validate_file("src/hooks/useTodos.ts")
        naming = [i for i in issues if i.category == "naming"]
        self.assertEqual(naming, [])

    def test_service_missing_suffix_warning(self):
        self.fx.write(
            "src/api/userApi.ts",
            "export const fetchUser = async () => {};\n",
        )
        issues = self.fx.validator().validate_file("src/api/userApi.ts")
        naming = [i for i in issues if i.rule_id == "naming-service"]
        self.assertEqual(len(naming), 1)
        self.assertIn("Service", naming[0].message)

    def test_service_valid_suffix_ok(self):
        self.fx.write(
            "src/api/userService.ts",
            "export const fetchUserService = async () => {};\n",
        )
        issues = self.fx.validator().validate_file("src/api/userService.ts")
        naming = [i for i in issues if i.category == "naming"]
        self.assertEqual(naming, [])

    def test_naming_disabled_via_cfg(self):
        rules = (self.fx.root / ".harness" / "rules.yaml").read_text(encoding="utf-8")
        rules += "\n  naming:\n    enabled: false\n"
        (self.fx.root / ".harness" / "rules.yaml").write_text(rules, encoding="utf-8")
        self.fx.write(
            "src/components/badName.tsx",
            "export const badName = () => <div/>;\n",
        )
        issues = self.fx.validator().validate_file("src/components/badName.tsx")
        naming = [i for i in issues if i.category == "naming"]
        self.assertEqual(naming, [])

    def test_unknown_layer_no_naming_check(self):
        self.fx.write(
            "src/utils/helper.ts",
            "export const helper = () => {};\n",
        )
        issues = self.fx.validator().validate_file("src/utils/helper.ts")
        naming = [i for i in issues if i.category == "naming"]
        self.assertEqual(naming, [])


if __name__ == "__main__":
    unittest.main()
