"""cache 单元测试。

关键回归：
- key 必须随 mode 变化（P0 #4 修复点）
- 依赖文件内容变化 → 旧 key 失效（P0 #3 增量失效）
- TTL 过期 → 视为 miss
"""

import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from . import _setup  # noqa: F401

from lib.cache import SmartValidationCache  # noqa: E402
from lib.dependency_graph import DependencyGraph  # noqa: E402


class CacheFixture:
    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="harness-cache-"))
        self.cache_dir = self.root / "cache"
        self.cache_dir.mkdir()

    def make_dep_graph(self, graph_data: dict) -> DependencyGraph:
        graph_file = self.root / "dep-graph.json"
        graph_file.write_text(json.dumps({
            "schema_version": 1,
            "updated_at": "2026-06-17T00:00:00",
            "graph": graph_data,
            "reverse_graph": {},
        }))
        return DependencyGraph(graph_file)

    def write_src(self, rel: str, content: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


class TestSmartValidationCache(unittest.TestCase):
    def setUp(self):
        self.fx = CacheFixture()

    def tearDown(self):
        self.fx.cleanup()

    def test_set_then_get(self):
        dg = self.fx.make_dep_graph({"src/a.ts": []})
        cache = SmartValidationCache(self.fx.cache_dir, dg, self.fx.root)
        self.fx.write_src("src/a.ts", "x = 1")
        cache.set("x = 1", "src/a.ts", "strict", {"issues": [], "ok": True})
        result = cache.get("x = 1", "src/a.ts", "strict")
        self.assertEqual(result, {"issues": [], "ok": True})

    def test_mode_changes_key(self):
        """治理模式不同 → 必须 miss（P0 #4 核心修复）。"""
        dg = self.fx.make_dep_graph({"src/a.ts": []})
        cache = SmartValidationCache(self.fx.cache_dir, dg, self.fx.root)
        self.fx.write_src("src/a.ts", "x = 1")
        cache.set("x = 1", "src/a.ts", "strict", {"mode": "strict"})
        self.assertIsNone(cache.get("x = 1", "src/a.ts", "relaxed"))
        self.assertIsNone(cache.get("x = 1", "src/a.ts", "off"))
        self.assertIsNotNone(cache.get("x = 1", "src/a.ts", "strict"))

    def test_dep_change_invalidates(self):
        """依赖文件内容变 → 旧 cache 不应命中（P0 #3 依赖图增量失效）。"""
        dg = self.fx.make_dep_graph({"src/a.ts": ["src/b.ts"]})
        cache = SmartValidationCache(self.fx.cache_dir, dg, self.fx.root)

        self.fx.write_src("src/a.ts", "import b from './b'")
        self.fx.write_src("src/b.ts", "export const b = 1")
        cache.set("import b from './b'", "src/a.ts", "strict", {"v": 1})

        # 不动 a.ts，但改 b.ts
        self.fx.write_src("src/b.ts", "export const b = 2")
        miss = cache.get("import b from './b'", "src/a.ts", "strict")
        self.assertIsNone(miss)

    def test_missing_dep_file_uses_zero_sha(self):
        """依赖图里写了 b.ts 但实际文件不存在 → 不应崩溃。"""
        dg = self.fx.make_dep_graph({"src/a.ts": ["src/b.ts"]})
        cache = SmartValidationCache(self.fx.cache_dir, dg, self.fx.root)
        self.fx.write_src("src/a.ts", "x")
        # 不创建 b.ts
        cache.set("x", "src/a.ts", "strict", {"v": 1})
        self.assertEqual(cache.get("x", "src/a.ts", "strict"), {"v": 1})

    def test_ttl_expires(self):
        dg = self.fx.make_dep_graph({"src/a.ts": []})
        cache = SmartValidationCache(self.fx.cache_dir, dg, self.fx.root, ttl_seconds=0)
        self.fx.write_src("src/a.ts", "x")
        cache.set("x", "src/a.ts", "strict", {"v": 1})
        # ttl=0 → 任何 age > 0 都过期
        time.sleep(0.05)
        self.assertIsNone(cache.get("x", "src/a.ts", "strict"))

    def test_clear_all(self):
        dg = self.fx.make_dep_graph({"src/a.ts": []})
        cache = SmartValidationCache(self.fx.cache_dir, dg, self.fx.root)
        self.fx.write_src("src/a.ts", "x")
        cache.set("x", "src/a.ts", "strict", {"v": 1})
        cache.set("x", "src/a.ts", "relaxed", {"v": 2})
        n = cache.clear_all()
        self.assertEqual(n, 2)
        self.assertIsNone(cache.get("x", "src/a.ts", "strict"))


if __name__ == "__main__":
    unittest.main()
