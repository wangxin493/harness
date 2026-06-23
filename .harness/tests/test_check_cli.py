"""harness check CLI 集成测试。"""

import json
import os
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path

from click.testing import CliRunner

from . import _setup  # noqa: F401

from lib import cli as cli_module  # noqa: E402


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
  forbidden_imports: []
checks:
  cycles:
    enabled: true
  unused_exports:
    enabled: true
    entry_points:
      - "src/index.tsx"
      - "src/pages/**"
governance:
  default_mode: strict
""")


class CheckCliFixture:
    """搭一个最小 .harness/context/ —— 不真跑 scanner，写死数据。"""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="harness-check-cli-")).resolve()
        (self.root / ".harness").mkdir()
        (self.root / ".harness" / "rules.yaml").write_text(RULES_YAML, encoding="utf-8")
        self.context = self.root / ".harness" / "context"
        self.context.mkdir()

    def write_graph(self, graph, reverse=None):
        if reverse is None:
            reverse = {n: [] for n in graph}
            for src, dests in graph.items():
                for d in dests:
                    reverse.setdefault(d, []).append(src)
        (self.context / "dependency-graph.json").write_text(json.dumps({
            "graph": graph, "reverse_graph": reverse,
        }), encoding="utf-8")

    def write_context(self, files):
        (self.context / "project-context.json").write_text(json.dumps({
            "files": files, "components": [], "hooks": [], "apis": [], "types": [],
        }), encoding="utf-8")

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


class TestCheckCli(unittest.TestCase):
    def setUp(self):
        self.fx = CheckCliFixture()
        self.runner = CliRunner()
        self._old_env = os.environ.get("HARNESS_PROJECT_DIR")
        os.environ["HARNESS_PROJECT_DIR"] = str(self.fx.root)

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("HARNESS_PROJECT_DIR", None)
        else:
            os.environ["HARNESS_PROJECT_DIR"] = self._old_env
        self.fx.cleanup()

    def test_missing_scan_artifacts_error(self):
        # 删掉 context 文件
        (self.fx.context / "dependency-graph.json").unlink(missing_ok=True)
        result = self.runner.invoke(cli_module.cli, ["check"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("尚未扫描", result.output)

    def test_no_issues_passes(self):
        self.fx.write_graph({"a.ts": [], "b.ts": ["a.ts"]})
        self.fx.write_context([
            {"file_path": "a.ts", "layer": "type", "export_count": 0},
            {"file_path": "b.ts", "layer": "service", "export_count": 0},
        ])
        result = self.runner.invoke(cli_module.cli, ["check"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("全项目检查通过", result.output)

    def test_cycle_detected(self):
        self.fx.write_graph({
            "src/api/a.ts": ["src/api/b.ts"],
            "src/api/b.ts": ["src/api/a.ts"],
        })
        self.fx.write_context([
            {"file_path": "src/api/a.ts", "layer": "service", "export_count": 1},
            {"file_path": "src/api/b.ts", "layer": "service", "export_count": 1},
        ])
        result = self.runner.invoke(cli_module.cli, ["check"])
        # strict mode 下 cycle=error → exit 2
        self.assertEqual(result.exit_code, 2)
        self.assertIn("循环依赖", result.output)

    def test_unused_export_warning(self):
        self.fx.write_graph({"src/api/orphan.ts": []})
        self.fx.write_context([
            {"file_path": "src/api/orphan.ts", "layer": "service", "export_count": 2},
        ])
        result = self.runner.invoke(cli_module.cli, ["check"])
        # 应在输出里看到 orphan.ts + 无引用提示
        self.assertIn("orphan.ts", result.output)
        self.assertIn("无任何引用", result.output)

    def test_json_output(self):
        self.fx.write_graph({
            "src/api/a.ts": ["src/api/b.ts"],
            "src/api/b.ts": ["src/api/a.ts"],
        })
        self.fx.write_context([
            {"file_path": "src/api/a.ts", "layer": "service", "export_count": 1},
            {"file_path": "src/api/b.ts", "layer": "service", "export_count": 1},
        ])
        result = self.runner.invoke(cli_module.cli, ["check", "--json"])
        # exit 2 因为有 cycle；--json 仍打 issues
        payload = json.loads(result.output)
        self.assertTrue(any(i["rule_id"] == "cycle" for i in payload))

    def test_no_cycles_flag_skips_cycle_check(self):
        self.fx.write_graph({
            "src/api/a.ts": ["src/api/b.ts"],
            "src/api/b.ts": ["src/api/a.ts"],
        })
        self.fx.write_context([
            {"file_path": "src/api/a.ts", "layer": "service", "export_count": 1},
            {"file_path": "src/api/b.ts", "layer": "service", "export_count": 1},
        ])
        result = self.runner.invoke(
            cli_module.cli, ["check", "--no-cycles", "--no-unused"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("全项目检查通过", result.output)

    def test_off_mode_skips(self):
        # 写 mode-config.json 切到 off
        (self.fx.root / ".harness" / "mode-config.json").write_text(
            json.dumps({"mode": "off", "updated_at": "2026-06-23T00:00:00"}),
            encoding="utf-8",
        )
        self.fx.write_graph({
            "a.ts": ["b.ts"], "b.ts": ["a.ts"],
        })
        self.fx.write_context([])
        result = self.runner.invoke(cli_module.cli, ["check"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("治理模式为 off", result.output)


if __name__ == "__main__":
    unittest.main()
