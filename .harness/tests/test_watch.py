"""scan --watch 监听器测试。

只测核心契约（不真启 watchdog observer，避免 macOS sandbox + tempfile 的事件
延迟把测试变 flaky）：
- _DebouncedHandler 把多次 submit 合并成一次回调
- _allowed 的过滤范围（src/ + 后缀）
- watch_loop(once=True) 跑一次首扫即返回 0
- 缺 watchdog 依赖时给出可读错误
"""

import shutil
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from . import _setup  # noqa: F401

from lib.watch import (  # noqa: E402
    DEFAULT_DEBOUNCE_SEC,
    _DebouncedHandler,
    _allowed,
    watch_loop,
)
from lib.scanner import IncrementalScanner  # noqa: E402


RULES_YAML = textwrap.dedent("""\
    architecture:
      layers:
        - name: component
          paths: ["src/components/"]
          can_import: ["hook", "service", "type"]
        - name: service
          paths: ["src/api/"]
          can_import: ["type"]
""")


class WatchFixture:
    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="harness-watch-")).resolve()
        (self.root / ".harness").mkdir()
        (self.root / ".harness" / "rules.yaml").write_text(RULES_YAML, encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src" / "api").mkdir()

    def write_ts(self, rel: str, body: str = "export const x = 1;\n") -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        return p

    def scanner(self) -> IncrementalScanner:
        return IncrementalScanner(project_dir=self.root)

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Debounce
# ---------------------------------------------------------------------------


class TestDebouncedHandler(unittest.TestCase):
    def test_multiple_submits_single_callback(self):
        events = []
        ev = threading.Event()

        def cb(paths):
            events.append(set(paths))
            ev.set()

        h = _DebouncedHandler(cb, debounce_sec=0.1)
        h.submit("a")
        h.submit("b")
        h.submit("c")
        # 等回调
        ev.wait(timeout=1.0)
        self.assertEqual(len(events), 1, f"expected 1 callback, got {events}")
        self.assertEqual(events[0], {"a", "b", "c"})

    def test_burst_resets_timer(self):
        events = []
        ev = threading.Event()

        def cb(paths):
            events.append(set(paths))
            ev.set()

        h = _DebouncedHandler(cb, debounce_sec=0.15)
        h.submit("a")
        time.sleep(0.05)
        h.submit("b")  # 应该把 timer 重置，不会立刻 fire
        time.sleep(0.05)
        h.submit("c")
        ev.wait(timeout=1.0)
        self.assertEqual(events[0], {"a", "b", "c"})

    def test_callback_exception_does_not_kill_handler(self):
        # 即使 callback 抛异常，handler 内部的 timer 已 fire 完毕；
        # 关键诉求是不向上层渗透异常
        def cb(paths):
            raise RuntimeError("boom")

        h = _DebouncedHandler(cb, debounce_sec=0.05)
        h.submit("a")
        time.sleep(0.2)  # 等 flush 完成
        # 没 raise 出来即视为通过；再次 submit 仍可继续
        succeeded = []
        h.callback = lambda paths: succeeded.append(paths)
        h.submit("b")
        time.sleep(0.2)
        self.assertTrue(succeeded, "handler 应仍可继续工作")

    def test_cancel_prevents_pending_callback(self):
        events = []
        h = _DebouncedHandler(lambda paths: events.append(paths),
                              debounce_sec=0.5)
        h.submit("a")
        h.cancel()
        time.sleep(0.7)
        self.assertEqual(events, [])


# ---------------------------------------------------------------------------
# _allowed（过滤）
# ---------------------------------------------------------------------------


class TestAllowed(unittest.TestCase):
    def setUp(self):
        self.fx = WatchFixture()
        self.scanner = self.fx.scanner()

    def tearDown(self):
        self.fx.cleanup()

    def test_in_src_with_ts_ext(self):
        p = self.fx.write_ts("src/api/x.ts")
        self.assertTrue(_allowed(p, self.scanner))

    def test_outside_src_rejected(self):
        p = self.fx.root / "tests" / "x.ts"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("export {};", encoding="utf-8")
        self.assertFalse(_allowed(p, self.scanner))

    def test_wrong_extension_rejected(self):
        p = self.fx.write_ts("src/api/notes.md", body="md")
        self.assertFalse(_allowed(p, self.scanner))

    def test_outside_project_rejected(self):
        # 创建一个项目外的 ts 文件
        outside = Path(tempfile.mkdtemp(prefix="harness-outside-")) / "x.ts"
        outside.write_text("export {};", encoding="utf-8")
        try:
            self.assertFalse(_allowed(outside, self.scanner))
        finally:
            shutil.rmtree(outside.parent, ignore_errors=True)


# ---------------------------------------------------------------------------
# watch_loop(once=True)：首扫
# ---------------------------------------------------------------------------


class TestWatchLoopOnce(unittest.TestCase):
    def setUp(self):
        self.fx = WatchFixture()
        self.fx.write_ts("src/api/x.ts")

    def tearDown(self):
        self.fx.cleanup()

    def test_once_runs_initial_scan_and_exits(self):
        rc = watch_loop(self.fx.root, once=True)
        self.assertEqual(rc, 0)
        # 初始扫描应当产出 context/*.json
        ctx = self.fx.root / ".harness" / "context"
        self.assertTrue((ctx / "scan-metadata.json").exists())
        self.assertTrue((ctx / "project-context.json").exists())

    def test_missing_watchdog_returns_4(self):
        # 用 mock 制造 ImportError
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "watchdog.events" or name == "watchdog.observers":
                raise ImportError("simulated")
            return real_import(name, *args, **kwargs)

        with mock.patch.object(builtins, "__import__", side_effect=fake_import):
            rc = watch_loop(self.fx.root, once=True)
        self.assertEqual(rc, 4)


if __name__ == "__main__":
    unittest.main()
