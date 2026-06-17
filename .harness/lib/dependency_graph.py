#!/usr/bin/env python3
"""文件依赖图（只读消费者）—— Harness 2.0 P0 #3

约定：
- scanner.py 是依赖图**唯一**生产者，写到 context/dependency-graph.json
- 本模块只读，绝不写盘
- 依赖图中：is_type_only / is_external 的 import 已被 scanner 过滤
- max_depth 参数已预留，P0 默认无限传播；P2 #12 会落实默认 3 + 告警
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional, Set


class DependencyGraph:
    """只读依赖图。"""

    def __init__(self, graph_file: Path) -> None:
        self.graph_file = Path(graph_file)
        self._graph: Dict[str, List[str]] = {}
        self._reverse: Dict[str, List[str]] = {}
        self._loaded_at: Optional[str] = None
        self.reload()

    # -- 加载 ---------------------------------------------------------------

    def reload(self) -> bool:
        """重新加载。文件不存在或解析失败时图为空，返回 False。"""
        if not self.graph_file.exists():
            self._graph = {}
            self._reverse = {}
            self._loaded_at = None
            return False

        try:
            data = json.loads(self.graph_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._graph = {}
            self._reverse = {}
            return False

        self._graph = {k: list(v) for k, v in (data.get("graph") or {}).items()}
        self._reverse = {k: list(v) for k, v in (data.get("reverse_graph") or {}).items()}
        self._loaded_at = data.get("updated_at")
        return True

    # -- 查询 ---------------------------------------------------------------

    def get_dependencies(self, file_path: str) -> List[str]:
        """返回 file_path 直接依赖的文件列表（已排序）。"""
        return list(self._graph.get(file_path, []))

    def get_dependents(self, file_path: str) -> List[str]:
        """返回直接依赖 file_path 的文件列表（一跳）。"""
        return list(self._reverse.get(file_path, []))

    def get_affected_files(
        self,
        changed_file: str,
        max_depth: Optional[int] = None,
    ) -> Set[str]:
        """返回受 changed_file 间接影响的所有文件（反向 BFS，不含自身）。

        max_depth=None 时无限传播；max_depth=N 时只走 N 跳。
        P2 #12 会把默认改为 3 + 超阈告警。
        """
        affected: Set[str] = set()
        if changed_file not in self._reverse:
            return affected

        queue: deque = deque([(changed_file, 0)])
        visited: Set[str] = {changed_file}

        while queue:
            current, depth = queue.popleft()
            if max_depth is not None and depth >= max_depth:
                continue

            for dependent in self._reverse.get(current, []):
                if dependent in visited:
                    continue
                visited.add(dependent)
                affected.add(dependent)
                queue.append((dependent, depth + 1))

        return affected

    # -- 元数据 -------------------------------------------------------------

    @property
    def updated_at(self) -> Optional[str]:
        return self._loaded_at

    @property
    def file_count(self) -> int:
        return len(self._graph)

    def is_loaded(self) -> bool:
        return self._loaded_at is not None

    def files(self) -> List[str]:
        """图中所有已知文件（排序）。"""
        return sorted(self._graph.keys())
