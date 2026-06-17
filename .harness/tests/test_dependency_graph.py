"""dependency_graph 单元测试。"""

import json
import tempfile
import unittest
from pathlib import Path

from . import _setup  # noqa: F401

from lib.dependency_graph import DependencyGraph  # noqa: E402


def write_graph(graph: dict, reverse: dict) -> Path:
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump({
        "schema_version": 1,
        "updated_at": "2026-06-17T19:00:00",
        "graph": graph,
        "reverse_graph": reverse,
    }, f)
    f.close()
    return Path(f.name)


class TestDependencyGraph(unittest.TestCase):
    def test_load_missing_file(self):
        dg = DependencyGraph(Path("/tmp/nonexistent-graph-xyz.json"))
        self.assertFalse(dg.is_loaded())
        self.assertEqual(dg.file_count, 0)

    def test_load_corrupt_file(self):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        f.write("{not valid json")
        f.close()
        dg = DependencyGraph(Path(f.name))
        self.assertEqual(dg.file_count, 0)

    def test_get_dependencies(self):
        f = write_graph(
            graph={"a.ts": ["b.ts", "c.ts"], "b.ts": [], "c.ts": []},
            reverse={"b.ts": ["a.ts"], "c.ts": ["a.ts"], "a.ts": []},
        )
        dg = DependencyGraph(f)
        self.assertEqual(set(dg.get_dependencies("a.ts")), {"b.ts", "c.ts"})
        self.assertEqual(dg.get_dependencies("b.ts"), [])
        self.assertEqual(dg.get_dependencies("nonexistent.ts"), [])

    def test_get_affected_transitive(self):
        # a → b → c → d ; e → c
        f = write_graph(
            graph={"a.ts": ["b.ts"], "b.ts": ["c.ts"], "c.ts": ["d.ts"], "d.ts": [],
                   "e.ts": ["c.ts"]},
            reverse={"b.ts": ["a.ts"], "c.ts": ["b.ts", "e.ts"], "d.ts": ["c.ts"],
                     "a.ts": [], "e.ts": []},
        )
        dg = DependencyGraph(f)
        # 改 d.ts → 影响 c.ts, b.ts, a.ts, e.ts
        affected = dg.get_affected_files("d.ts")
        self.assertEqual(affected, {"c.ts", "b.ts", "a.ts", "e.ts"})

    def test_max_depth_limits_propagation(self):
        f = write_graph(
            graph={"a.ts": ["b.ts"], "b.ts": ["c.ts"], "c.ts": ["d.ts"], "d.ts": []},
            reverse={"b.ts": ["a.ts"], "c.ts": ["b.ts"], "d.ts": ["c.ts"], "a.ts": []},
        )
        dg = DependencyGraph(f)
        # 改 d.ts，max_depth=1 → 只到 c.ts
        self.assertEqual(dg.get_affected_files("d.ts", max_depth=1), {"c.ts"})
        # max_depth=2 → 到 b.ts
        self.assertEqual(dg.get_affected_files("d.ts", max_depth=2), {"c.ts", "b.ts"})
        # max_depth=None → 全传
        self.assertEqual(dg.get_affected_files("d.ts"), {"c.ts", "b.ts", "a.ts"})

    def test_reload(self):
        f = write_graph(graph={"a.ts": []}, reverse={"a.ts": []})
        dg = DependencyGraph(f)
        self.assertEqual(dg.file_count, 1)

        # 写入新内容并 reload
        f.write_text(json.dumps({
            "schema_version": 1,
            "updated_at": "2026-06-17T20:00:00",
            "graph": {"a.ts": [], "b.ts": []},
            "reverse_graph": {"a.ts": [], "b.ts": []},
        }), encoding="utf-8")
        dg.reload()
        self.assertEqual(dg.file_count, 2)


if __name__ == "__main__":
    unittest.main()
