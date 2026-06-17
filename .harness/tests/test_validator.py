"""validator 单元测试。"""

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


if __name__ == "__main__":
    unittest.main()
