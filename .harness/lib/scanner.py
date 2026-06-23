#!/usr/bin/env python3
"""增量扫描器 —— Harness 2.0 P0 #1

职责：
1. 遍历 src/，按 rules.yaml 的 scanner 配置过滤；
2. 对每个 TS/TSX/.d.ts 文件用 ast_parser 提取 imports/exports；
3. 基于 mtime + sha1 增量决定哪些文件需要重新解析；
4. 产出三份数据：
   - context/project-context.json   组件/Hook/API/类型 索引
   - context/dependency-graph.json  文件依赖图（scanner 是唯一数据源）
   - context/scan-metadata.json     文件指纹（mtime + sha1 + parsed_ok）

对外约定：
- type-only import 不进依赖图（is_type_only=True 的 import 跳过）
- 外部包（react、lodash 等非 @/ 与非相对路径）记入 imports，但不进依赖图
- src/ 直接子文件（layer=unknown）保留 imports/exports 但不分类
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from lib.ast_parser import ParseResult, TypeScriptParser


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class FileRecord:
    """单文件扫描结果。"""

    file_path: str             # POSIX 风格、相对项目根
    layer: str                 # component | hook | service | type | unknown
    sha1: str
    mtime: float
    parsed_ok: bool
    imports: List[Dict]        # [{ source, resolved, is_type_only, is_reexport, is_external, line }]
    exports: List[Dict]        # ast_parser ExportItem 序列化
    hook_calls: List[Dict] = field(default_factory=list)   # ast_parser HookCall 序列化
    parse_errors: List[str] = field(default_factory=list)


@dataclass
class ScanResult:
    """完整扫描结果。"""

    files: Dict[str, FileRecord] = field(default_factory=dict)
    components: List[Dict] = field(default_factory=list)
    hooks: List[Dict] = field(default_factory=list)
    apis: List[Dict] = field(default_factory=list)
    types: List[Dict] = field(default_factory=list)

    def summary(self) -> Dict[str, int]:
        return {
            "total_files": len(self.files),
            "total_components": len(self.components),
            "total_hooks": len(self.hooks),
            "total_apis": len(self.apis),
            "total_types": len(self.types),
        }


# ---------------------------------------------------------------------------
# 增量扫描器
# ---------------------------------------------------------------------------


class IncrementalScanner:
    """增量扫描器。

    使用方式::

        scanner = IncrementalScanner(project_dir=Path.cwd())
        result = scanner.scan(force_full=False)
    """

    SCAN_METADATA_VERSION = 2

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
        self.scanner_cfg = self.rules.get("scanner", {}) or {}
        self.layers = self._normalize_layers(self.rules.get("architecture", {}).get("layers", []))

        self.source_root = self.scanner_cfg.get("source_root", "src")
        self.include_exts = tuple(self.scanner_cfg.get("include_extensions", [".ts", ".tsx", ".d.ts"]))
        self.exclude_globs = list(self.scanner_cfg.get("exclude_globs", []))
        self.exclude_dirs = set(self.scanner_cfg.get("exclude_dirs", []))

        self.parser = TypeScriptParser()

    # -- 公共入口 -----------------------------------------------------------

    def scan(self, force_full: bool = False) -> ScanResult:
        """执行扫描。

        force_full=True 时强制重新解析所有文件（无视 metadata 缓存）。
        """
        prev_meta = {} if force_full else self._load_scan_metadata()
        prev_files: Dict[str, Dict] = prev_meta.get("files", {}) if prev_meta else {}

        result = ScanResult()
        current_paths = self._discover_files()

        # 1) 解析每个文件（增量）
        for rel_path, abs_path in current_paths:
            stat = abs_path.stat()
            mtime = stat.st_mtime
            prev = prev_files.get(rel_path)

            # 增量判定：mtime 一致才看 sha1，否则直接重算
            sha1 = self._compute_sha1(abs_path)
            need_parse = (
                force_full
                or prev is None
                or prev.get("sha1") != sha1
                or prev.get("schema_version") != self.SCAN_METADATA_VERSION
            )

            if not need_parse and "record" in prev:
                # 复用 metadata 内嵌的 record（无解析开销）
                # 注意：layer 是 path × rules 的派生量，不入缓存——rules.yaml 改了
                # 但文件 mtime/sha1 不变时，旧的 layer 会过期。这里**总是现算**。
                cached = prev["record"]
                record = FileRecord(
                    file_path=rel_path,
                    layer=self._classify_layer(rel_path),
                    sha1=sha1,
                    mtime=mtime,
                    parsed_ok=cached.get("parsed_ok", True),
                    imports=cached.get("imports", []),
                    exports=cached.get("exports", []),
                    hook_calls=cached.get("hook_calls", []),
                    parse_errors=cached.get("parse_errors", []),
                )
            else:
                record = self._scan_file(rel_path, abs_path, sha1, mtime)

            result.files[rel_path] = record
            self._classify_into_index(record, result)

        # 2) 产出依赖图（scanner 是唯一数据源）
        graph, reverse_graph = self._build_dependency_graph(result.files)

        # 3) 落盘
        self._write_dependency_graph(graph, reverse_graph)
        self._write_project_context(result)
        self._write_scan_metadata(result.files)

        return result

    # -- 文件发现 -----------------------------------------------------------

    def _discover_files(self) -> List[Tuple[str, Path]]:
        """遍历 source_root，返回 [(rel_posix, abs_path)]，已应用排除规则。"""
        src = self.project_dir / self.source_root
        if not src.is_dir():
            return []

        results: List[Tuple[str, Path]] = []
        for path in src.rglob("*"):
            # 排除目录命中
            if any(part in self.exclude_dirs for part in path.parts):
                continue
            if not path.is_file():
                continue
            if not self._has_allowed_extension(path):
                continue

            rel = path.relative_to(self.project_dir).as_posix()
            if self._is_globally_excluded(rel):
                continue
            results.append((rel, path))

        results.sort(key=lambda x: x[0])
        return results

    def _has_allowed_extension(self, path: Path) -> bool:
        # path.suffixes 把 .d.ts 拆成 ['.d', '.ts']
        name = path.name.lower()
        return any(name.endswith(ext) for ext in self.include_exts)

    def _is_globally_excluded(self, rel_posix: str) -> bool:
        return any(fnmatch.fnmatch(rel_posix, pat) for pat in self.exclude_globs)

    # -- 单文件解析 ---------------------------------------------------------

    def _scan_file(
        self,
        rel_path: str,
        abs_path: Path,
        sha1: str,
        mtime: float,
    ) -> FileRecord:
        """解析单文件并组装 FileRecord。"""
        layer = self._classify_layer(rel_path)

        try:
            parse_result = self.parser.parse(abs_path)
            parsed_ok = True
            parse_errors = list(parse_result.parse_errors)
            imports_raw = parse_result.imports
            exports_raw = parse_result.exports
            hook_calls_raw = parse_result.hook_calls
        except Exception as exc:
            parsed_ok = False
            parse_errors = [f"parser_exception: {exc}"]
            imports_raw = []
            exports_raw = []
            hook_calls_raw = []

        # 序列化 imports，附加 resolved + is_external
        imports: List[Dict] = []
        for imp in imports_raw:
            resolved, is_external = self._resolve_import(imp.source, abs_path)
            imports.append({
                "source": imp.source,
                "resolved": resolved,
                "is_type_only": imp.is_type_only,
                "is_reexport": imp.is_reexport,
                "is_external": is_external,
                "line": imp.line,
            })

        exports: List[Dict] = [asdict(e) for e in exports_raw]
        hook_calls: List[Dict] = [asdict(h) for h in hook_calls_raw]

        return FileRecord(
            file_path=rel_path,
            layer=layer,
            sha1=sha1,
            mtime=mtime,
            parsed_ok=parsed_ok,
            imports=imports,
            exports=exports,
            hook_calls=hook_calls,
            parse_errors=parse_errors,
        )

    # -- 路径 → 层级 --------------------------------------------------------

    def _classify_layer(self, rel_posix: str) -> str:
        """按 rules.yaml architecture.layers 顺序匹配；未命中 → unknown。"""
        for layer in self.layers:
            for prefix in layer["paths"]:
                if rel_posix.startswith(prefix):
                    return layer["name"]
        return "unknown"

    @staticmethod
    def _normalize_layers(layers_cfg: List[Dict]) -> List[Dict]:
        normalized = []
        for layer in layers_cfg or []:
            name = layer.get("name", "")
            paths = layer.get("paths", []) or []
            # 统一 POSIX 前缀
            paths = [p.replace("\\", "/") for p in paths]
            normalized.append({"name": name, "paths": paths,
                                "can_import": layer.get("can_import", [])})
        return normalized

    # -- 分类入索引（component / hook / api / type）------------------------

    def _classify_into_index(self, record: FileRecord, result: ScanResult) -> None:
        """根据 layer + export 形态把文件归入 components/hooks/apis/types。

        规则：
        - layer=component & 任一 export returns_jsx=True → 组件（取首个 jsx export）
        - layer=hook      & 任一 export name 以 use 开头 → hook
        - layer=service                                     → api
        - layer=type 或 export kind 含 interface/type/enum  → type
        - 其它（含 layer=unknown）：仅放入 files，不入分类
        """
        layer = record.layer

        if layer == "component":
            for exp in record.exports:
                if exp.get("returns_jsx") or exp.get("kind") == "default":
                    result.components.append({
                        "name": exp.get("name") or "default",
                        "file_path": record.file_path,
                        "line": exp.get("line"),
                        "kind": exp.get("kind"),
                    })
                    break

        if layer == "hook":
            for exp in record.exports:
                name = exp.get("name", "")
                if name.startswith("use"):
                    result.hooks.append({
                        "name": name,
                        "file_path": record.file_path,
                        "line": exp.get("line"),
                    })
                    break

        if layer == "service":
            # API 文件可能有多个具名导出；全部记入
            for exp in record.exports:
                if exp.get("kind") in ("function", "const", "let", "var", "class", "default"):
                    result.apis.append({
                        "name": exp.get("name") or "default",
                        "file_path": record.file_path,
                        "line": exp.get("line"),
                    })

        # 类型：layer=type 直接全收；其他层级里的 interface/type/enum 也收（共用）
        for exp in record.exports:
            kind = exp.get("kind")
            if kind in ("interface", "type", "enum") or (
                layer == "type" and kind in ("const", "class")
            ):
                result.types.append({
                    "name": exp.get("name", ""),
                    "file_path": record.file_path,
                    "line": exp.get("line"),
                    "kind": kind,
                })

    # -- 模块解析 -----------------------------------------------------------

    def _resolve_import(
        self,
        source: str,
        from_file_abs: Path,
    ) -> Tuple[Optional[str], bool]:
        """把 import source 解析成项目内文件路径。

        返回 (resolved_rel_posix or None, is_external)。
        - 外部包（不以 @/ 或 ./ 或 ../ 开头）→ (None, True)
        - @/foo → src/foo.{ts,tsx,d.ts} 或 src/foo/index.{ts,tsx,d.ts}
        - ./foo / ../foo → 相对 from_file 解析
        """
        if not source:
            return None, True

        if source.startswith("@/"):
            rel_under_src = source[2:]
            base = self.project_dir / self.source_root / rel_under_src
            return self._probe_module_candidates(base), False

        if source.startswith("./") or source.startswith("../"):
            base = (from_file_abs.parent / source).resolve()
            return self._probe_module_candidates(base), False

        # 裸名 / 三方包
        return None, True

    # 与 TS resolver 对齐的探测顺序
    _CANDIDATE_SUFFIXES = (".ts", ".tsx", ".d.ts")
    _INDEX_SUFFIXES = ("index.ts", "index.tsx", "index.d.ts")

    def _probe_module_candidates(self, base: Path) -> Optional[str]:
        """按 .ts → .tsx → .d.ts → /index.ts → /index.tsx → /index.d.ts 顺序探测。"""
        # 1) base + ext —— 用字符串拼接避免 with_suffix 在 .d 已存在时丢后缀
        for ext in self._CANDIDATE_SUFFIXES:
            candidate = Path(str(base) + ext)
            if candidate.is_file():
                return self._to_project_rel(candidate)

        # 2) base/index.*
        if base.is_dir():
            for idx in self._INDEX_SUFFIXES:
                candidate = base / idx
                if candidate.is_file():
                    return self._to_project_rel(candidate)

        return None

    def _to_project_rel(self, abs_path: Path) -> Optional[str]:
        """把绝对路径转成项目根的 POSIX 相对路径；越界返回 None。"""
        try:
            return abs_path.resolve().relative_to(self.project_dir).as_posix()
        except ValueError:
            return None

    # -- 依赖图 -------------------------------------------------------------

    def _build_dependency_graph(
        self,
        files: Dict[str, FileRecord],
    ) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
        """根据 files 的 imports 构图。

        约定：
        - is_type_only=True 的 import 不进图
        - is_external=True 的 import 不进图
        - resolved 为 None（未能解析到本地文件）不进图
        """
        graph: Dict[str, Set[str]] = {f: set() for f in files}
        reverse: Dict[str, Set[str]] = {f: set() for f in files}

        for file_path, record in files.items():
            for imp in record.imports:
                if imp.get("is_type_only") or imp.get("is_external"):
                    continue
                resolved = imp.get("resolved")
                if not resolved:
                    continue
                graph[file_path].add(resolved)
                reverse.setdefault(resolved, set()).add(file_path)

        # 转为 list + 排序，保证落盘稳定
        graph_out = {k: sorted(v) for k, v in graph.items()}
        reverse_out = {k: sorted(v) for k, v in reverse.items()}
        return graph_out, reverse_out

    # -- 持久化 -------------------------------------------------------------

    def _write_dependency_graph(
        self,
        graph: Dict[str, List[str]],
        reverse_graph: Dict[str, List[str]],
    ) -> None:
        self.context_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "schema_version": 1,
            "updated_at": _now_iso(),
            "graph": graph,
            "reverse_graph": reverse_graph,
        }
        (self.context_dir / "dependency-graph.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _write_project_context(self, result: ScanResult) -> None:
        self.context_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "schema_version": 1,
            "updated_at": _now_iso(),
            "summary": result.summary(),
            "components": result.components,
            "hooks": result.hooks,
            "apis": result.apis,
            "types": result.types,
            # files: 仅保留摘要字段，详细 imports/exports 已在 dependency-graph + scan-metadata
            "files": [
                {
                    "file_path": r.file_path,
                    "layer": r.layer,
                    "parsed_ok": r.parsed_ok,
                    "export_count": len(r.exports),
                    "import_count": len(r.imports),
                }
                for r in result.files.values()
            ],
        }
        (self.context_dir / "project-context.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _write_scan_metadata(self, files: Dict[str, FileRecord]) -> None:
        self.context_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "schema_version": self.SCAN_METADATA_VERSION,
            "updated_at": _now_iso(),
            "files": {
                f.file_path: {
                    "sha1": f.sha1,
                    "mtime": f.mtime,
                    "schema_version": self.SCAN_METADATA_VERSION,
                    "record": {
                        "layer": f.layer,
                        "parsed_ok": f.parsed_ok,
                        "imports": f.imports,
                        "exports": f.exports,
                        "hook_calls": f.hook_calls,
                        "parse_errors": f.parse_errors,
                    },
                }
                for f in files.values()
            },
        }
        (self.context_dir / "scan-metadata.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _load_scan_metadata(self) -> Dict:
        meta_file = self.context_dir / "scan-metadata.json"
        if not meta_file.exists():
            return {}
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if data.get("schema_version") != self.SCAN_METADATA_VERSION:
            return {}
        return data

    # -- 工具 ---------------------------------------------------------------

    def _load_rules(self) -> Dict:
        import yaml  # 延迟 import

        rules_file = self.harness_dir / "rules.yaml"
        if not rules_file.exists():
            return {}
        return yaml.safe_load(rules_file.read_text(encoding="utf-8")) or {}

    @staticmethod
    def _compute_sha1(abs_path: Path) -> str:
        h = hashlib.sha1()
        with abs_path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")
