"""scanner 单元测试。"""

import json
import shutil
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from . import _setup  # noqa: F401

from lib.scanner import IncrementalScanner  # noqa: E402


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
scanner:
  source_root: "src"
  include_extensions: [".ts", ".tsx", ".d.ts"]
  exclude_globs: ["**/*.test.ts", "**/*.test.tsx"]
  exclude_dirs: ["node_modules", "dist"]
""")


class ScannerFixture:
    """临时项目工厂。"""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="harness-scanner-"))
        self.harness_dir = self.root / ".harness"
        (self.harness_dir / "context").mkdir(parents=True)
        (self.harness_dir / "rules.yaml").write_text(RULES_YAML, encoding="utf-8")
        (self.root / "src").mkdir()

    def write(self, rel: str, content: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def scan(self, force_full: bool = False, reset_baseline: bool = False):
        scanner = IncrementalScanner(project_dir=self.root)
        return scanner.scan(force_full=force_full, reset_baseline=reset_baseline)

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


class TestIncrementalScanner(unittest.TestCase):
    def setUp(self):
        self.fx = ScannerFixture()

    def tearDown(self):
        self.fx.cleanup()

    # ------------------------------------------------------------------ 分类

    def test_classify_layers(self):
        self.fx.write("src/components/Btn.tsx", "export const Btn = () => <div/>;\n")
        self.fx.write("src/hooks/useFoo.ts", "export const useFoo = () => 1;\n")
        self.fx.write("src/api/itemService.ts", "export const itemService = {};\n")
        self.fx.write("src/types/index.ts", "export interface T { a: number }\n")
        self.fx.write("src/index.tsx", "export const App = () => <div/>;\n")  # unknown layer

        result = self.fx.scan(force_full=True)

        layers = {r.file_path: r.layer for r in result.files.values()}
        self.assertEqual(layers["src/components/Btn.tsx"], "component")
        self.assertEqual(layers["src/hooks/useFoo.ts"], "hook")
        self.assertEqual(layers["src/api/itemService.ts"], "service")
        self.assertEqual(layers["src/types/index.ts"], "type")
        self.assertEqual(layers["src/index.tsx"], "unknown")

        self.assertEqual([c["name"] for c in result.components], ["Btn"])
        self.assertEqual([h["name"] for h in result.hooks], ["useFoo"])
        self.assertEqual([a["name"] for a in result.apis], ["itemService"])
        self.assertEqual([t["name"] for t in result.types], ["T"])

    # ----------------------------------------------------------- 依赖图产出

    def test_dependency_graph_resolution(self):
        self.fx.write(
            "src/components/Btn.tsx",
            "import { useFoo } from '@/hooks/useFoo';\n"
            "export const Btn = () => { useFoo(); return <div/>; };\n",
        )
        self.fx.write("src/hooks/useFoo.ts", "export const useFoo = () => 1;\n")

        result = self.fx.scan(force_full=True)

        graph_file = self.fx.harness_dir / "context" / "dependency-graph.json"
        data = json.loads(graph_file.read_text(encoding="utf-8"))
        self.assertEqual(
            data["graph"]["src/components/Btn.tsx"],
            ["src/hooks/useFoo.ts"],
        )
        self.assertEqual(
            data["reverse_graph"]["src/hooks/useFoo.ts"],
            ["src/components/Btn.tsx"],
        )

    def test_external_imports_excluded_from_graph(self):
        self.fx.write(
            "src/components/A.tsx",
            "import React from 'react';\nexport const A = () => <div/>;\n",
        )
        result = self.fx.scan(force_full=True)
        # imports 中应记录 react，但带 is_external=True；图里不应出现
        rec = result.files["src/components/A.tsx"]
        externals = [i for i in rec.imports if i["is_external"]]
        self.assertEqual([i["source"] for i in externals], ["react"])

        graph_file = self.fx.harness_dir / "context" / "dependency-graph.json"
        data = json.loads(graph_file.read_text(encoding="utf-8"))
        self.assertEqual(data["graph"]["src/components/A.tsx"], [])

    def test_type_only_imports_excluded_from_graph(self):
        self.fx.write("src/types/T.ts", "export type T = string;\n")
        self.fx.write(
            "src/components/A.tsx",
            "import type { T } from '@/types/T';\n"
            "export const A = () => <div/>;\n",
        )
        self.fx.scan(force_full=True)

        data = json.loads(
            (self.fx.harness_dir / "context" / "dependency-graph.json").read_text()
        )
        # type-only import 不进图
        self.assertEqual(data["graph"]["src/components/A.tsx"], [])

    # ------------------------------------------------------------- 增量扫描

    def test_incremental_skips_unchanged(self):
        self.fx.write(
            "src/api/itemService.ts",
            "export const itemService = {};\n",
        )
        self.fx.scan(force_full=True)

        # 第二次扫：未修改 → 走增量分支，不应抛
        # 验证 metadata 中 sha 与第一次一致
        meta1 = json.loads(
            (self.fx.harness_dir / "context" / "scan-metadata.json").read_text()
        )
        sha_before = meta1["files"]["src/api/itemService.ts"]["sha1"]

        self.fx.scan(force_full=False)

        meta2 = json.loads(
            (self.fx.harness_dir / "context" / "scan-metadata.json").read_text()
        )
        self.assertEqual(meta2["files"]["src/api/itemService.ts"]["sha1"], sha_before)

    def test_incremental_detects_change(self):
        f = self.fx.write(
            "src/api/itemService.ts",
            "export const itemService = { a: 1 };\n",
        )
        self.fx.scan(force_full=True)

        meta1 = json.loads(
            (self.fx.harness_dir / "context" / "scan-metadata.json").read_text()
        )
        sha_before = meta1["files"]["src/api/itemService.ts"]["sha1"]

        time.sleep(0.05)  # 让 mtime 变
        f.write_text("export const itemService = { a: 2 };\n", encoding="utf-8")
        self.fx.scan(force_full=False)

        meta2 = json.loads(
            (self.fx.harness_dir / "context" / "scan-metadata.json").read_text()
        )
        self.assertNotEqual(meta2["files"]["src/api/itemService.ts"]["sha1"], sha_before)

    # ------------------------------------------------------------- 排除规则

    def test_excludes_test_files(self):
        self.fx.write("src/api/itemService.ts", "export const x = 1;\n")
        self.fx.write("src/api/itemService.test.ts", "export const y = 1;\n")
        self.fx.write("src/api/__tests__/foo.ts", "export const z = 1;\n")

        result = self.fx.scan(force_full=True)
        files = set(result.files.keys())
        self.assertIn("src/api/itemService.ts", files)
        self.assertNotIn("src/api/itemService.test.ts", files)

    def test_excludes_node_modules(self):
        self.fx.write("src/api/x.ts", "export const x = 1;\n")
        # node_modules 不在 src/ 下，理论上 source_root=src 就不会扫到；
        # 但 src/ 下也有人放过临时 node_modules：
        self.fx.write("src/node_modules/pkg/index.ts", "export const z = 1;\n")
        result = self.fx.scan(force_full=True)
        files = set(result.files.keys())
        self.assertNotIn("src/node_modules/pkg/index.ts", files)

    def test_js_jsx_files_scanned_when_configured(self):
        """rules.yaml include_extensions 含 .js/.jsx 时，JS 文件被扫描和分层。"""
        js_rules = RULES_YAML.replace(
            'include_extensions: [".ts", ".tsx", ".d.ts"]',
            'include_extensions: [".ts", ".tsx", ".d.ts", ".js", ".jsx"]',
        )
        (self.fx.harness_dir / "rules.yaml").write_text(js_rules, encoding="utf-8")
        self.fx.write("src/components/Card.jsx", "export const Card = () => <div/>;")
        self.fx.write("src/api/userService.js", "export const userService = {};")

        result = self.fx.scan(force_full=True)
        files = set(result.files.keys())
        self.assertIn("src/components/Card.jsx", files)
        self.assertIn("src/api/userService.js", files)
        self.assertEqual(result.files["src/components/Card.jsx"].layer, "component")
        self.assertEqual(result.files["src/api/userService.js"].layer, "service")

    def test_js_files_not_scanned_when_not_configured(self):
        """.js 不在 include_extensions 时不被扫描（回退 TS-only 场景）。"""
        self.fx.write("src/components/Card.jsx", "export const Card = () => <div/>;")
        self.fx.write("src/components/Btn.tsx", "export const Btn = () => <div/>;")
        result = self.fx.scan(force_full=True)
        files = set(result.files.keys())
        self.assertNotIn("src/components/Card.jsx", files)
        self.assertIn("src/components/Btn.tsx", files)

    def test_scan_writes_reuse_index_summary(self):
        (self.fx.root / "package.json").write_text(json.dumps({
            "dependencies": {"lodash": "^4.17.21", "react": "^18.0.0"},
            "devDependencies": {"date-fns": "^3.0.0", "typescript": "^5.0.0"},
        }), encoding="utf-8")
        self.fx.write("src/utils/debounce.ts", "export const debounce = () => null;\n")
        self.fx.write("src/utils/format.ts", "export const format = () => null;\n")

        self.fx.scan(force_full=True)

        data = json.loads(
            (self.fx.harness_dir / "context" / "project-context.json").read_text(encoding="utf-8")
        )
        reuse = data["reuse_index"]
        self.assertEqual(reuse["packages"], ["date-fns", "lodash"])
        self.assertEqual(reuse["utility_dirs"][0]["path"], "src/utils/")
        self.assertEqual(reuse["utility_dirs"][0]["file_count"], 2)
        self.assertGreaterEqual(reuse["utility_dirs"][0]["export_count"], 2)

    def test_reuse_index_can_be_disabled(self):
        rules = RULES_YAML + textwrap.dedent("""\
        reuse_index:
          enabled: false
        """)
        (self.fx.harness_dir / "rules.yaml").write_text(rules, encoding="utf-8")
        (self.fx.root / "package.json").write_text(json.dumps({
            "dependencies": {"lodash": "^4.17.21"},
        }), encoding="utf-8")
        self.fx.write("src/utils/debounce.ts", "export const debounce = () => null;\n")

        self.fx.scan(force_full=True)

        data = json.loads(
            (self.fx.harness_dir / "context" / "project-context.json").read_text(encoding="utf-8")
        )
        self.assertEqual(data["reuse_index"], {})

    def test_scan_generates_naming_baseline_for_adopt_rules(self):
        rules = RULES_YAML + textwrap.dedent("""\
        naming:
          component: adopt:PascalCase
        """)
        (self.fx.harness_dir / "rules.yaml").write_text(rules, encoding="utf-8")
        self.fx.write("src/components/userTable.tsx", "export const userTable = () => <div/>;\n")

        self.fx.scan(force_full=True)
        baseline = json.loads(
            (self.fx.harness_dir / "context" / "naming-baseline.json").read_text(encoding="utf-8")
        )
        self.assertEqual(baseline["schema_version"], 1)
        self.assertEqual(baseline["issues"], [{
            "file": "src/components/userTable.tsx",
            "rule_id": "naming-component",
            "name": "userTable",
        }])

    def test_scan_does_not_overwrite_existing_naming_baseline(self):
        rules = RULES_YAML + textwrap.dedent("""\
        naming:
          component: adopt:PascalCase
        """)
        (self.fx.harness_dir / "rules.yaml").write_text(rules, encoding="utf-8")
        baseline_file = self.fx.harness_dir / "context" / "naming-baseline.json"
        baseline_file.write_text(json.dumps({
            "schema_version": 1,
            "updated_at": "old",
            "issues": [{"file": "old.tsx", "rule_id": "naming-component", "name": "old"}],
        }), encoding="utf-8")
        self.fx.write("src/components/userTable.tsx", "export const userTable = () => <div/>;\n")

        self.fx.scan(force_full=True)
        baseline = json.loads(baseline_file.read_text(encoding="utf-8"))
        self.assertEqual(baseline["issues"], [{
            "file": "old.tsx",
            "rule_id": "naming-component",
            "name": "old",
        }])

    def test_scan_reset_baseline_overwrites_existing_naming_baseline(self):
        rules = RULES_YAML + textwrap.dedent("""\
        naming:
          component: adopt:PascalCase
        """)
        (self.fx.harness_dir / "rules.yaml").write_text(rules, encoding="utf-8")
        baseline_file = self.fx.harness_dir / "context" / "naming-baseline.json"
        baseline_file.write_text(json.dumps({
            "schema_version": 1,
            "updated_at": "old",
            "issues": [{"file": "old.tsx", "rule_id": "naming-component", "name": "old"}],
        }), encoding="utf-8")
        self.fx.write("src/components/userTable.tsx", "export const userTable = () => <div/>;\n")

        self.fx.scan(force_full=True, reset_baseline=True)
        baseline = json.loads(baseline_file.read_text(encoding="utf-8"))
        self.assertEqual(baseline["issues"], [{
            "file": "src/components/userTable.tsx",
            "rule_id": "naming-component",
            "name": "userTable",
        }])

    def test_layer_recomputed_when_rules_change_without_file_change(self):
        """rules.yaml 调整 layers 后，即使文件 mtime/sha1 不变，layer 也必须按新规则重算。"""
        # 第一次：layers 留空
        empty_rules = textwrap.dedent("""\
            architecture:
              layers: []
            scanner:
              source_root: "src"
              include_extensions: [".ts", ".tsx", ".d.ts"]
              exclude_globs: []
              exclude_dirs: []
            """)
        (self.fx.harness_dir / "rules.yaml").write_text(empty_rules, encoding="utf-8")

        self.fx.write("src/components/Btn.tsx", "export const Btn = () => <div/>;\n")
        self.fx.write("src/api/itemService.ts", "export const itemService = {};\n")

        result1 = self.fx.scan()
        layers1 = {r.file_path: r.layer for r in result1.files.values()}
        self.assertEqual(layers1["src/components/Btn.tsx"], "unknown")
        self.assertEqual(layers1["src/api/itemService.ts"], "unknown")

        # 第二次：补上 layers，文件不动
        (self.fx.harness_dir / "rules.yaml").write_text(RULES_YAML, encoding="utf-8")
        result2 = self.fx.scan()  # 注意：force_full=False，走增量路径
        layers2 = {r.file_path: r.layer for r in result2.files.values()}
        # 关键：layer 必须按新规则重算
        self.assertEqual(layers2["src/components/Btn.tsx"], "component")
        self.assertEqual(layers2["src/api/itemService.ts"], "service")
        # 同时 components/apis 索引也要跟着更新
        self.assertEqual([c["name"] for c in result2.components], ["Btn"])
        self.assertEqual([a["name"] for a in result2.apis], ["itemService"])


if __name__ == "__main__":
    unittest.main()
