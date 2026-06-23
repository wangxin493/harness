#!/usr/bin/env python3
"""Harness 2.0 文件监听器 —— lib/watch.py

`harness scan --watch` 的常驻进程实现：在前台监听 src/ 下的 .ts/.tsx/.d.ts
文件变化，去抖后增量跑一次 scan（不重新 generate `generated/*.md`，因为那是
rules / lessons / mode 的派生物，开发动作不会触发）。

设计原则（与设计文档一致）：
- watchdog 提供跨平台的 native 事件（macOS FSEvents / Linux inotify），比轮询省 CPU。
- 去抖：bursts of writes（如 git checkout、IDE 保存触发的多次事件）汇聚为
  一次扫描，间隔 DEBOUNCE_SEC。
- 失败不退出：单次 scan 异常仅打印 stderr，loop 继续；只有 KeyboardInterrupt 时退出。
- 静默正常：成功扫描的输出尽量短，避免和 Agent 终端输出抢屏。

不在范围（按 Q5/Q6 的决策）：
- daemon 模式（pidfile / 日志切割） → P2
- 自动 generate → 手动跑 `harness generate` 即可
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Set

from lib.scanner import IncrementalScanner


# 触发 scan 前等多少秒（聚合 bursts）
DEFAULT_DEBOUNCE_SEC = 0.5
# 监听器输出的统一前缀，便于在 terminal 里和 Agent 输出区分
LOG_PREFIX = "[harness watch]"


def _log(msg: str, *, err: bool = False) -> None:
    stream = sys.stderr if err else sys.stdout
    print(f"{LOG_PREFIX} {msg}", file=stream, flush=True)


class _DebouncedHandler:
    """把 watchdog 事件汇聚成「至少多久没新事件后才触发一次」。

    线程安全：watchdog 事件回调来自 observer 线程，trigger 在主线程执行。
    """

    def __init__(
        self,
        callback: Callable[[Set[str]], None],
        debounce_sec: float = DEFAULT_DEBOUNCE_SEC,
    ) -> None:
        self.callback = callback
        self.debounce_sec = debounce_sec
        self._lock = threading.Lock()
        self._pending: Set[str] = set()
        self._timer: Optional[threading.Timer] = None

    def submit(self, path: str) -> None:
        """文件事件入口（来自 observer 线程）。"""
        with self._lock:
            self._pending.add(path)
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce_sec, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _flush(self) -> None:
        """触发一次回调；执行期间新到的事件会重启定时器，等下一次 flush。"""
        with self._lock:
            paths = self._pending
            self._pending = set()
            self._timer = None
        if not paths:
            return
        try:
            self.callback(paths)
        except Exception as exc:  # 永不让异常杀掉 observer 线程
            _log(f"scan 失败: {exc}", err=True)

    def cancel(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


def _allowed(path: Path, scanner: IncrementalScanner) -> bool:
    """事件是否值得触发 scan：限制在 scanner 的扫描范围内。"""
    try:
        rel = path.resolve().relative_to(scanner.project_dir)
    except ValueError:
        return False
    rel_posix = rel.as_posix()
    # 必须在 source_root（默认 src/）下
    if not rel_posix.startswith(scanner.source_root.rstrip("/") + "/"):
        return False
    # 后缀过滤
    if not any(rel_posix.endswith(ext) for ext in scanner.include_exts):
        return False
    return True


def watch_loop(
    project_dir: Path,
    debounce_sec: float = DEFAULT_DEBOUNCE_SEC,
    once: bool = False,
) -> int:
    """启动监听循环。Ctrl-C 退出，返回退出码。

    once=True：只运行一次首扫，不进入事件循环；测试用。
    """
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError as exc:
        _log(f"缺少 watchdog 依赖: {exc}", err=True)
        _log("安装：.harness/.venv/bin/pip install 'watchdog>=4.0,<6.0'", err=True)
        return 4

    project_dir = Path(project_dir).resolve()
    scanner = IncrementalScanner(project_dir=project_dir)
    src_root = project_dir / scanner.source_root

    if not src_root.exists():
        _log(f"源目录不存在: {src_root}（仍会监听其上层目录直到它出现）",
             err=True)

    # 起手扫一次，保证 context/*.json 是最新
    _log(f"首次扫描 {project_dir} ...")
    initial = _do_scan(scanner)
    if initial is not None:
        s = initial.summary()
        _log(f"首扫完成：files={s['total_files']} components={s['total_components']} "
             f"hooks={s['total_hooks']} apis={s['total_apis']} types={s['total_types']}")

    if once:
        return 0

    # ---- 事件循环 -----------------------------------------------------
    handler = _DebouncedHandler(
        callback=lambda paths: _on_change(paths, scanner),
        debounce_sec=debounce_sec,
    )

    class _Handler(FileSystemEventHandler):  # type: ignore[misc]
        def on_any_event(self, event) -> None:  # noqa: D401
            if event.is_directory:
                return
            try:
                p = Path(getattr(event, "src_path", "") or "")
            except Exception:
                return
            if _allowed(p, scanner):
                handler.submit(str(p))
            # rename 事件 dest_path 也要看
            dest = getattr(event, "dest_path", None)
            if dest:
                try:
                    pd = Path(dest)
                except Exception:
                    return
                if _allowed(pd, scanner):
                    handler.submit(str(pd))

    observer = Observer()
    # 监听 source_root 上层（src 父目录）以应对 src/ 整个被删/重建的情况；
    # 实际的事件过滤靠 _allowed。
    watch_root = src_root if src_root.exists() else project_dir
    observer.schedule(_Handler(), str(watch_root), recursive=True)
    observer.start()
    _log(f"开始监听 {watch_root}（debounce={debounce_sec}s）。Ctrl-C 退出。")

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        _log("收到 Ctrl-C，停止监听 ...")
    finally:
        handler.cancel()
        observer.stop()
        observer.join(timeout=5.0)
    return 0


def _on_change(paths: Set[str], scanner: IncrementalScanner) -> None:
    """收到聚合事件后跑一次增量 scan。"""
    rels = []
    for p in paths:
        try:
            rels.append(Path(p).resolve().relative_to(scanner.project_dir).as_posix())
        except ValueError:
            rels.append(p)
    sample = ", ".join(rels[:3]) + (f" (+{len(rels) - 3})" if len(rels) > 3 else "")
    _log(f"变更 {len(rels)} 个文件：{sample}")
    result = _do_scan(scanner)
    if result is None:
        return
    s = result.summary()
    _log(f"扫描完成：files={s['total_files']} components={s['total_components']} "
         f"hooks={s['total_hooks']} apis={s['total_apis']} types={s['total_types']}")


def _do_scan(scanner: IncrementalScanner):
    """跑一次增量 scan；失败仅打印 stderr，返回 None。"""
    try:
        return scanner.scan()
    except Exception as exc:
        _log(f"scan 抛异常: {exc}", err=True)
        return None
