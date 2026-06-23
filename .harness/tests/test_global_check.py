"""global_check 单元测试。"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from . import _setup  # noqa: F401

from lib.global_check import (  # noqa: E402
    GlobalChecker,
    _rotate_min,
    _tarjan_scc,
)


class TestTarjanSCC(unittest.TestCase):
    """Tarjan 算法核心。"""

    def test_no_cycles(self):
        graph = {"a": ["b"], "b": ["c"], "c": []}
        sccs = _tarjan_scc(graph)
        # 每个节点单独一个 SCC
        self.assertEqual(sorted([s[0] for s in sccs]), ["a", "b", "c"])
        self.assertTrue(all(len(s) == 1 for s in sccs))

    def test_simple_cycle(self):
        graph = {"a": ["b"], "b": ["a"], "c": []}
        sccs = _tarjan_scc(graph)
        big = [s for s in sccs if len(s) > 1]
        self.assertEqual(len(big), 1)
        self.assertEqual(set(big[0]), {"a", "b"})

    def test_three_node_cycle(self):
        graph = {"a": ["b"], "b": ["c"], "c": ["a"]}
        sccs = _tarjan_scc(graph)
        big = [s for s in sccs if len(s) > 1]
        self.assertEqual(set(big[0]), {"a", "b", "c"})

    def test_self_loop(self):
        graph = {"a": ["a"], "b": []}
        sccs = _tarjan_scc(graph)
        # 自环节点单独一个 SCC（size=1，但 a→a），不会被 size>1 过滤
        self.assertTrue(any(s == ["a"] for s in sccs))

    def test_multiple_disjoint_cycles(self):
        graph = {
            "a": ["b"], "b": ["a"],
            "c": ["d"], "d": ["c"],
            "e": [],
        }
        sccs = _tarjan_scc(graph)
        big = sorted([sorted(s) for s in sccs if len(s) > 1])
        self.assertEqual(big, [["a", "b"], ["c", "d"]])

    def test_rotate_min(self):
        self.assertEqual(_rotate_min(["c", "a", "b"]), ["a", "b", "c"])
        self.assertEqual(_rotate_min(["a"]), ["a"])
        self.assertEqual(_rotate_min([]), [])


class _Fixture:
    """搭一个最小项目：只写 dependency-graph.json + project-context.json，
    不真跑 scanner —— global_check 只读这两个文件。"""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="harness-globalcheck-")).resolve()
        self.harness = self.root / ".harness"
        self.context = self.harness / "context"
        self.context.mkdir(parents=True)
        # 最小 rules.yaml（GlobalChecker 用 yaml 加载）
        (self.harness / "rules.yaml").write_text(
            "checks: {cycles: {enabled: true}, unused_exports: {enabled: true}}\n",
            encoding="utf-8",
        )

    def write_graph(self, graph: dict, reverse: dict = None):
        if reverse is None:
            reverse = {n: [] for n in graph}
            for src, dests in graph.items():
                for d in dests:
                    reverse.setdefault(d, []).append(src)
        (self.context / "dependency-graph.json").write_text(json.dumps({
            "schema_version": 1,
            "graph": graph,
            "reverse_graph": reverse,
        }, indent=2), encoding="utf-8")

    def write_context(self, files: list, **extras):
        data = {
            "schema_version": 1,
            "summary": {},
            "components": [],
            "hooks": [],
            "apis": [],
            "types": [],
            "files": files,
        }
        data.update(extras)
        (self.context / "project-context.json").write_text(
            json.dumps(data, indent=2), encoding="utf-8")

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


class TestDetectCycles(unittest.TestCase):
    def setUp(self):
        self.fx = _Fixture()

    def tearDown(self):
        self.fx.cleanup()

    def test_no_cycle_no_issue(self):
        self.fx.write_graph({"a": ["b"], "b": [], "c": []})
        self.fx.write_context([])
        result = GlobalChecker(self.fx.root).run()
        cycles = [i for i in result.issues if i.rule_id == "cycle"]
        self.assertEqual(cycles, [])

    def test_simple_cycle_reported(self):
        self.fx.write_graph({"a": ["b"], "b": ["a"]})
        self.fx.write_context([])
        result = GlobalChecker(self.fx.root).run()
        cycles = [i for i in result.issues if i.rule_id == "cycle"]
        self.assertEqual(len(cycles), 1)
        self.assertIn("a", cycles[0].extra["ring"])
        self.assertIn("b", cycles[0].extra["ring"])
        # 环路径首尾相接
        ring = cycles[0].extra["ring"]
        self.assertEqual(ring[0], ring[-1])

    def test_three_node_cycle(self):
        self.fx.write_graph({"a": ["b"], "b": ["c"], "c": ["a"]})
        self.fx.write_context([])
        result = GlobalChecker(self.fx.root).run()
        cycles = [i for i in result.issues if i.rule_id == "cycle"]
        self.assertEqual(len(cycles), 1)
        self.assertEqual(len(cycles[0].extra["ring"]), 4)  # 3 + 闭环

    def test_self_loop_cycle(self):
        self.fx.write_graph({"a": ["a"]})
        self.fx.write_context([])
        result = GlobalChecker(self.fx.root).run()
        cycles = [i for i in result.issues if i.rule_id == "cycle"]
        self.assertEqual(len(cycles), 1)

    def test_disabled_skips(self):
        # 自环但 cycles 关闭 → 无 issue
        self.fx.write_graph({"a": ["a"]})
        self.fx.write_context([])
        result = GlobalChecker(self.fx.root).run(enable_cycles=False)
        cycles = [i for i in result.issues if i.rule_id == "cycle"]
        self.assertEqual(cycles, [])


class TestDetectUnusedExports(unittest.TestCase):
    def setUp(self):
        self.fx = _Fixture()

    def tearDown(self):
        self.fx.cleanup()

    def test_unused_export_reported(self):
        # foo 有导出但无人引用 → 报；bar 引用 baz → 不报
        self.fx.write_graph({
            "src/api/foo.ts": [],
            "src/api/baz.ts": [],
            "src/components/Bar.tsx": ["src/api/baz.ts"],
        })
        self.fx.write_context([
            {"file_path": "src/api/foo.ts", "layer": "service", "export_count": 2},
            {"file_path": "src/api/baz.ts", "layer": "service", "export_count": 1},
            {"file_path": "src/components/Bar.tsx", "layer": "component", "export_count": 1},
        ])
        result = GlobalChecker(self.fx.root).run()
        unused = [i for i in result.issues if i.rule_id == "unused-export"]
        files = [i.file for i in unused]
        self.assertIn("src/api/foo.ts", files)
        self.assertNotIn("src/api/baz.ts", files)

    def test_entry_point_exempt(self):
        self.fx.write_graph({
            "src/index.tsx": [],
            "src/pages/Home.tsx": [],
            "src/api/orphan.ts": [],
        })
        self.fx.write_context([
            {"file_path": "src/index.tsx", "layer": "unknown", "export_count": 1},
            {"file_path": "src/pages/Home.tsx", "layer": "component", "export_count": 1},
            {"file_path": "src/api/orphan.ts", "layer": "service", "export_count": 1},
        ])
        result = GlobalChecker(self.fx.root).run()
        unused_files = [i.file for i in result.issues if i.rule_id == "unused-export"]
        self.assertNotIn("src/index.tsx", unused_files)
        self.assertNotIn("src/pages/Home.tsx", unused_files)
        self.assertIn("src/api/orphan.ts", unused_files)

    def test_type_layer_exempt(self):
        self.fx.write_graph({
            "src/types/User.ts": [],
            "src/api/foo.ts": [],
        })
        self.fx.write_context([
            {"file_path": "src/types/User.ts", "layer": "type", "export_count": 1},
            {"file_path": "src/api/foo.ts", "layer": "service", "export_count": 1},
        ])
        result = GlobalChecker(self.fx.root).run()
        unused_files = [i.file for i in result.issues if i.rule_id == "unused-export"]
        self.assertNotIn("src/types/User.ts", unused_files)
        self.assertIn("src/api/foo.ts", unused_files)

    def test_no_exports_no_report(self):
        # 文件没 export 也不报（可能是 side-effect import）
        self.fx.write_graph({"src/api/foo.ts": []})
        self.fx.write_context([
            {"file_path": "src/api/foo.ts", "layer": "service", "export_count": 0},
        ])
        result = GlobalChecker(self.fx.root).run()
        self.assertEqual([i for i in result.issues if i.rule_id == "unused-export"], [])

    def test_disabled_skips(self):
        self.fx.write_graph({"src/api/foo.ts": []})
        self.fx.write_context([
            {"file_path": "src/api/foo.ts", "layer": "service", "export_count": 1},
        ])
        result = GlobalChecker(self.fx.root).run(enable_unused=False)
        self.assertEqual([i for i in result.issues if i.rule_id == "unused-export"], [])


class TestMissingArtifacts(unittest.TestCase):
    def test_missing_graph_returns_empty(self):
        fx = _Fixture()
        try:
            # 不写任何 context 文件
            result = GlobalChecker(fx.root).run()
            self.assertEqual(result.issues, [])
        finally:
            fx.cleanup()


if __name__ == "__main__":
    unittest.main()
