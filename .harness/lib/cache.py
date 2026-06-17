#!/usr/bin/env python3
"""智能验证缓存 —— Harness 2.0 P0 #3 + #4

关键设计点：
- 依赖图**只读**消费 dependency_graph.DependencyGraph（scanner 是唯一写者）
- 缓存 key 包含治理模式（P0 #4，原设计漏掉，会导致 strict→relaxed 后旧缓存被复用）
- 依赖文件 sha1 **按需现算**，不依赖 scan-metadata.json 中的快照（避免"先编辑 A、再编辑依赖 A 的 B"时拿到 A 的旧 sha → 错命中缓存的 bug）
- TTL 默认 24h；超时直接当作 miss（不主动清理，由 clear_all 触发）

cache key 组成：
    mode | file_path | sha1(code) | (sorted deps with current sha1)
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

from lib.dependency_graph import DependencyGraph


class SmartValidationCache:
    """验证结果缓存。"""

    DEFAULT_TTL_SECONDS = 24 * 60 * 60  # 24h

    def __init__(
        self,
        cache_dir: Path,
        dep_graph: DependencyGraph,
        project_dir: Path,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.dep_graph = dep_graph
        self.project_dir = Path(project_dir).resolve()
        self.ttl_seconds = ttl_seconds

    # -- 公共 API -----------------------------------------------------------

    def get(self, code: str, file_path: str, mode: str) -> Optional[Dict[str, Any]]:
        """读缓存。命中返回结果 dict；miss / 过期 / 解析失败均返回 None。"""
        key = self._compute_cache_key(code, file_path, mode)
        cache_file = self.cache_dir / f"{key}.json"
        if not cache_file.exists():
            return None

        # TTL 校验
        try:
            age = time.time() - cache_file.stat().st_mtime
        except OSError:
            return None
        if age > self.ttl_seconds:
            try:
                cache_file.unlink()
            except OSError:
                pass
            return None

        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def set(self, code: str, file_path: str, mode: str, result: Dict[str, Any]) -> str:
        """写缓存；返回 cache key（便于测试/调试）。"""
        key = self._compute_cache_key(code, file_path, mode)
        cache_file = self.cache_dir / f"{key}.json"
        cache_file.write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return key

    def clear_all(self) -> int:
        """清空所有缓存条目；返回删除数量。"""
        count = 0
        for f in self.cache_dir.glob("*.json"):
            try:
                f.unlink()
                count += 1
            except OSError:
                pass
        return count

    # -- key 计算 -----------------------------------------------------------

    def _compute_cache_key(self, code: str, file_path: str, mode: str) -> str:
        """计算缓存 key —— 必须包含 mode + 当前依赖 sha1。"""
        code_sha = hashlib.sha1(code.encode("utf-8")).hexdigest()[:12]

        dep_parts = []
        for dep in self.dep_graph.get_dependencies(file_path):
            dep_sha = self._sha_of_project_file(dep)
            dep_parts.append(f"{dep}:{dep_sha}")

        material = "|".join([
            f"mode={mode}",
            f"file={file_path}",
            f"code={code_sha}",
            f"deps=[{','.join(dep_parts)}]",
        ])
        return hashlib.sha1(material.encode("utf-8")).hexdigest()[:16]

    def _sha_of_project_file(self, rel_path: str) -> str:
        """对项目内某文件按需计算 sha1（短）；不存在/不可读 → '0'。"""
        abs_path = self.project_dir / rel_path
        try:
            with abs_path.open("rb") as f:
                h = hashlib.sha1()
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            return h.hexdigest()[:12]
        except OSError:
            return "0"
