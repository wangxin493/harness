#!/usr/bin/env python3
"""Harness 2.0 全项目级检查 —— lib/global_check.py

`harness check` 子命令的实现。需要看全图的检查放这里：

- detect_cycles      : 文件级循环依赖（Tarjan SCC）
- detect_unused      : 死代码（导出但无引用），entry_points 豁免

数据源：scanner 已落盘的
- `.harness/context/dependency-graph.json`   (graph + reverse_graph)
- `.harness/context/project-context.json`    (components/hooks/apis/types/files)

不重复扫描；运行前若没扫过，调用方应先跑 scan。
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class GlobalIssue:
    """全项目级检查问题。结构与 validator.Issue 兼容（可被 mode_manager 过滤）。"""

    rule_id: str
    severity: str          # error | warning | info
    category: str          # cycle | unused-export | ...
    message: str
    file: str              # POSIX 相对路径；环类问题填环里第一个文件
    line: Optional[int] = None
    suggestion: Optional[str] = None
    extra: Dict = field(default_factory=dict)  # 携带详细信息（如完整环路径、symbol 名）


@dataclass
class GlobalCheckResult:
    issues: List[GlobalIssue] = field(default_factory=list)

    def summary(self) -> Dict[str, int]:
        c = {"error": 0, "warning": 0, "info": 0}
        for i in self.issues:
            c[i.severity] = c.get(i.severity, 0) + 1
        c["total"] = len(self.issues)
        return c


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------


class GlobalChecker:
    """读取 scan 产物，跑全项目级规则。"""

    def __init__(
        self,
        project_dir: Path,
        harness_dir: Optional[Path] = None,
        rules: Optional[Dict] = None,
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.harness_dir = (harness_dir or (self.project_dir / ".harness")).resolve()
        self.context_dir = self.harness_dir / "context"
        self.rules = rules if rules is not None else self._load_rules()
        self.checks_cfg = (self.rules.get("checks") or {})

    # -- 入口 ---------------------------------------------------------------

    def run(
        self,
        enable_cycles: bool = True,
        enable_unused: bool = True,
    ) -> GlobalCheckResult:
        """跑所有启用的检查；缺数据源时返回空结果（不报错，让 CLI 提示先 scan）。"""
        result = GlobalCheckResult()

        graph_data = self._load_graph()
        ctx_data = self._load_context()
        if graph_data is None or ctx_data is None:
            return result

        if enable_cycles and self._cfg_enabled("cycles", default=True):
            result.issues.extend(self.detect_cycles(graph_data["graph"]))

        if enable_unused and self._cfg_enabled("unused_exports", default=True):
            result.issues.extend(self.detect_unused_exports(
                graph=graph_data["graph"],
                reverse=graph_data.get("reverse_graph", {}),
                context=ctx_data,
            ))

        return result

    # -- 循环依赖（Tarjan）--------------------------------------------------

    def detect_cycles(self, graph: Dict[str, List[str]]) -> List[GlobalIssue]:
        """文件级循环依赖：跑 Tarjan SCC，size>1 的强连通分量都是环。

        自环（A → A）也报：scanner 不会产出自环（import 自己），但保险起见放进来。
        """
        sccs = _tarjan_scc(graph)
        issues: List[GlobalIssue] = []
        for scc in sccs:
            if len(scc) <= 1:
                # 仍要检查自环（只有 1 个节点但它依赖自己）
                node = scc[0]
                if node in graph and node in graph[node]:
                    issues.append(self._make_cycle_issue([node, node]))
                continue
            # 把环按字典序最小为起点旋转，便于稳定输出
            ordered = _rotate_min(scc)
            issues.append(self._make_cycle_issue(ordered + [ordered[0]]))
        # 多个环按起点字典序稳定排序
        issues.sort(key=lambda i: (i.file, i.message))
        return issues

    def _make_cycle_issue(self, ring: List[str]) -> GlobalIssue:
        path_str = " → ".join(ring)
        head = ring[0]
        return GlobalIssue(
            rule_id="cycle",
            severity="error",
            category="cycle",
            message=f"循环依赖（{len(ring) - 1} 个文件）: {path_str}",
            file=head,
            suggestion="抽取共同依赖到下层（type / service / hook）打破环；或合并强耦合的文件。",
            extra={"ring": ring},
        )

    # -- 死代码（unused exports）-------------------------------------------

    def detect_unused_exports(
        self,
        graph: Dict[str, List[str]],
        reverse: Dict[str, List[str]],
        context: Dict,
    ) -> List[GlobalIssue]:
        """文件级"导出但无人引用"。

        粒度：文件级（不是符号级）—— scanner 的依赖图是文件级的。
        豁免：rules.yaml `checks.unused_exports.entry_points` 的 fnmatch 模式
              （默认含 src/index.tsx, src/pages/**）。
        类型文件：可选豁免（type-only 文件常被 type-only import，依赖图里不进；
                  默认豁免，避免大量误报）。
        """
        cfg = self.checks_cfg.get("unused_exports", {}) or {}
        entry_patterns = cfg.get("entry_points") or [
            "src/index.ts", "src/index.tsx", "src/pages/**", "src/**/index.ts",
        ]
        exempt_type_layer = cfg.get("exempt_type_layer", True)

        issues: List[GlobalIssue] = []
        files = context.get("files", [])
        # 转 file_path -> file 摘要
        file_index = {f["file_path"]: f for f in files}

        for file_path, summary in file_index.items():
            if summary.get("export_count", 0) == 0:
                continue
            if self._is_entry_point(file_path, entry_patterns):
                continue
            if exempt_type_layer and summary.get("layer") == "type":
                continue
            importers = reverse.get(file_path) or []
            if importers:
                continue
            issues.append(GlobalIssue(
                rule_id="unused-export",
                severity="warning",
                category="unused-export",
                message=f"文件有导出但无任何引用（{summary.get('export_count')} 个 export）",
                file=file_path,
                suggestion="删除该文件，或加到 rules.yaml `checks.unused_exports.entry_points`。",
                extra={"layer": summary.get("layer")},
            ))

        issues.sort(key=lambda i: i.file)
        return issues

    @staticmethod
    def _is_entry_point(rel_posix: str, patterns: List[str]) -> bool:
        for pat in patterns:
            if fnmatch.fnmatch(rel_posix, pat):
                return True
        return False

    # -- 配置工具 -----------------------------------------------------------

    def _cfg_enabled(self, name: str, default: bool) -> bool:
        sub = self.checks_cfg.get(name)
        if isinstance(sub, dict) and "enabled" in sub:
            return bool(sub["enabled"])
        return default

    def _load_rules(self) -> Dict:
        import yaml
        rules_file = self.harness_dir / "rules.yaml"
        if not rules_file.exists():
            return {}
        return yaml.safe_load(rules_file.read_text(encoding="utf-8")) or {}

    def _load_graph(self) -> Optional[Dict]:
        f = self.context_dir / "dependency-graph.json"
        if not f.exists():
            return None
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _load_context(self) -> Optional[Dict]:
        f = self.context_dir / "project-context.json"
        if not f.exists():
            return None
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None


# ---------------------------------------------------------------------------
# Tarjan SCC（迭代版，避免大项目栈溢出）
# ---------------------------------------------------------------------------


def _tarjan_scc(graph: Dict[str, List[str]]) -> List[List[str]]:
    """迭代实现的 Tarjan，返回 SCC 列表（每个 SCC 是节点 list）。

    保留 SCC 内部的发现顺序，便于把环路径以稳定顺序展示。
    """
    index_counter = [0]
    stack: List[str] = []
    on_stack: Set[str] = set()
    indexes: Dict[str, int] = {}
    lowlinks: Dict[str, int] = {}
    sccs: List[List[str]] = []

    # iterative dfs
    nodes = list(graph.keys())

    for start in nodes:
        if start in indexes:
            continue
        # 工作栈：(node, neighbor_iterator, called_after_child)
        work: List[Tuple[str, int]] = [(start, 0)]
        # 初始化
        indexes[start] = index_counter[0]
        lowlinks[start] = index_counter[0]
        index_counter[0] += 1
        stack.append(start)
        on_stack.add(start)

        while work:
            node, i = work[-1]
            neighbors = graph.get(node, [])
            if i < len(neighbors):
                work[-1] = (node, i + 1)
                w = neighbors[i]
                if w not in indexes:
                    indexes[w] = index_counter[0]
                    lowlinks[w] = index_counter[0]
                    index_counter[0] += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, 0))
                elif w in on_stack:
                    if indexes[w] < lowlinks[node]:
                        lowlinks[node] = indexes[w]
            else:
                # post-visit：把 child 的 lowlink 回灌给 parent
                work.pop()
                if work:
                    parent = work[-1][0]
                    if lowlinks[node] < lowlinks[parent]:
                        lowlinks[parent] = lowlinks[node]
                if lowlinks[node] == indexes[node]:
                    component: List[str] = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        component.append(w)
                        if w == node:
                            break
                    component.reverse()  # 入栈顺序
                    sccs.append(component)

    return sccs


def _rotate_min(seq: List[str]) -> List[str]:
    """把序列旋转到字典序最小的元素为起点，便于稳定展示。"""
    if not seq:
        return seq
    pivot = min(range(len(seq)), key=lambda i: seq[i])
    return seq[pivot:] + seq[:pivot]


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------


def run_global_checks(project_dir: Path) -> GlobalCheckResult:
    return GlobalChecker(project_dir=project_dir).run()
