#!/usr/bin/env python3
"""Harness 2.0 验证器（P0 最小版）—— lib/validator.py

P0 范围（必须 AST，不用正则）：
- 调 ast_parser.TypeScriptParser 解析文件；parse_errors 视为 error
- 架构层依赖检查：layer 不在 can_import 白名单 → error
- 禁用导入检查：rules.yaml imports.forbidden_imports → error

后续扩展：
- name-similarity：新文件命名与已有组件 / hook / api 高度相似 → info
- hook-call-misplaced：useXxx() 出现在非组件 / 非 hook 文件，或在分支中 → error
"""

from __future__ import annotations

import difflib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

from lib.ast_parser import TypeScriptParser


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class Issue:
    """验证问题。"""

    rule_id: str
    severity: str        # error | warning | info
    category: str        # parse | architecture | import | type-safety | naming
    message: str
    file: str
    line: Optional[int] = None
    suggestion: Optional[str] = None


# ---------------------------------------------------------------------------
# 验证器
# ---------------------------------------------------------------------------


class CodeValidator:
    """P0 最小验证器。"""

    def __init__(
        self,
        project_dir: Path,
        harness_dir: Optional[Path] = None,
        rules: Optional[Dict] = None,
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.harness_dir = (harness_dir or (self.project_dir / ".harness")).resolve()
        self.rules = rules if rules is not None else self._load_rules()
        self.layers = self._normalize_layers(
            self.rules.get("architecture", {}).get("layers", [])
        )
        self.forbidden_imports = list(
            self.rules.get("imports", {}).get("forbidden_imports", []) or []
        )
        self.checks_cfg = (self.rules.get("checks") or {})
        self.parser = TypeScriptParser()
        # project-context.json：现有组件 / hook / api 的索引（用于命名相似度）
        # 延迟加载：每次 validate_file 时实时读，确保跟最新 scan 一致
        self._context_cache: Optional[Dict] = None

    # -- 公共 API -----------------------------------------------------------

    def validate_file(self, file_path: str) -> List[Issue]:
        """验证一个项目内文件（POSIX 相对路径）。"""
        abs_path = self.project_dir / file_path
        if not abs_path.is_file():
            return [Issue(
                rule_id="file-not-found",
                severity="error",
                category="parse",
                message=f"文件不存在: {file_path}",
                file=file_path,
            )]

        return self._validate_path(file_path, abs_path)

    # -- 内部实现 -----------------------------------------------------------

    def _validate_path(self, rel_path: str, abs_path: Path) -> List[Issue]:
        issues: List[Issue] = []

        # 1) 解析
        try:
            parse_result = self.parser.parse(abs_path)
        except ValueError as exc:
            # 不支持的扩展名：直接放行（验证器不接管 .json/.css 等）
            return []
        except Exception as exc:
            issues.append(Issue(
                rule_id="parse-exception",
                severity="error",
                category="parse",
                message=f"解析异常: {exc}",
                file=rel_path,
            ))
            return issues

        for err in parse_result.parse_errors:
            issues.append(Issue(
                rule_id="parse-error",
                severity="error",
                category="parse",
                message=err,
                file=rel_path,
            ))

        # 2) 架构层依赖检查
        layer = self._classify_layer(rel_path)
        if layer != "unknown":
            allowed = self._allowed_imports_for(layer)
            for imp in parse_result.imports:
                # type-only / 外部包不参与架构检查
                if imp.is_type_only:
                    continue
                target_layer = self._infer_target_layer(imp.source)
                if target_layer is None:  # 外部 / 相对路径未匹配到任何层
                    continue
                if target_layer not in allowed and target_layer != layer:
                    issues.append(Issue(
                        rule_id=f"arch-{layer}-import",
                        severity="error",
                        category="architecture",
                        message=f"{layer} 层不能导入 {target_layer} 层（来源: {imp.source}）",
                        file=rel_path,
                        line=imp.line,
                        suggestion=f"将逻辑下沉到 {layer} 允许的层（{', '.join(allowed) or '无'}）",
                    ))

        # 3) 禁用导入检查
        for imp in parse_result.imports:
            for forbidden in self.forbidden_imports:
                if imp.source == forbidden or imp.source.startswith(forbidden + "/"):
                    issues.append(Issue(
                        rule_id="import-forbidden",
                        severity="error",
                        category="import",
                        message=f"禁止导入: {imp.source}",
                        file=rel_path,
                        line=imp.line,
                        suggestion=self._suggest_for_forbidden(imp.source),
                    ))
                    break

        # 4) Hook 调用规则（React Rules-of-Hooks 项目层叠加版）
        if self._cfg_enabled("hook_call_check", default=True):
            issues.extend(self._check_hook_calls(rel_path, layer, parse_result))

        # 5) 命名相似度（新建 / 改名时与已有 component / hook / api 撞车）
        if self._cfg_enabled("name_similarity", default=True):
            issues.extend(self._check_name_similarity(rel_path, layer, parse_result))

        return issues

    # -- Hook 调用规则 ------------------------------------------------------

    # layer 是否允许出现 useXxx() 调用
    _HOOK_CALL_ALLOWED_LAYERS = frozenset({"component", "hook"})

    def _check_hook_calls(
        self, rel_path: str, layer: str, parse_result
    ) -> List[Issue]:
        cfg = self.checks_cfg.get("hook_call_check") or {}
        excluded = set(cfg.get("excluded_callees") or [])

        issues: List[Issue] = []
        for hc in parse_result.hook_calls:
            if hc.callee in excluded:
                continue
            # 1) 错层调用：service / type / unknown 层都不该调 useXxx()
            if layer not in self._HOOK_CALL_ALLOWED_LAYERS:
                issues.append(Issue(
                    rule_id="hook-call-misplaced",
                    severity="error",
                    category="hook",
                    message=(
                        f"hook 调用 {hc.callee}() 出现在 {layer} 层文件，"
                        f"只允许在 component / hook 层"
                    ),
                    file=rel_path,
                    line=hc.line,
                    suggestion="把这段逻辑挪到 src/hooks/ 自定义 hook 内，再由组件调用。",
                ))
                continue
            # 2) 在合法层但调用位置不合法：顶层 / 普通函数（非组件、非 useXxx）
            if hc.in_function_kind == "top_level":
                issues.append(Issue(
                    rule_id="hook-call-top-level",
                    severity="error",
                    category="hook",
                    message=f"hook 调用 {hc.callee}() 出现在模块顶层，只能在组件 / 自定义 hook 函数体内",
                    file=rel_path,
                    line=hc.line,
                    suggestion="把它包到一个 useXxx 自定义 hook 或组件函数里。",
                ))
                continue
            host = hc.in_function or ""
            if host and not (host[:1].isupper() or host.startswith("use")):
                issues.append(Issue(
                    rule_id="hook-call-in-plain-func",
                    severity="error",
                    category="hook",
                    message=(
                        f"hook 调用 {hc.callee}() 出现在普通函数 {host}() 内，"
                        f"必须由组件（PascalCase）或自定义 hook（use 前缀）持有"
                    ),
                    file=rel_path,
                    line=hc.line,
                    suggestion=f"把 {host} 改名为 use{host[:1].upper()}{host[1:]}，或挪到组件里。",
                ))
                continue
            # 3) 条件 / 循环里调用 hook
            if hc.in_branch:
                issues.append(Issue(
                    rule_id="hook-call-conditional",
                    severity="error",
                    category="hook",
                    message=f"hook 调用 {hc.callee}() 出现在条件 / 循环里，违反 React Rules-of-Hooks",
                    file=rel_path,
                    line=hc.line,
                    suggestion="把判断挪到 hook 内部（在 hook 里写 if，外部无条件调用）。",
                ))
        return issues

    # -- 命名相似度 ---------------------------------------------------------

    def _check_name_similarity(
        self, rel_path: str, layer: str, parse_result
    ) -> List[Issue]:
        cfg = self.checks_cfg.get("name_similarity") or {}
        threshold = float(cfg.get("threshold", 0.8))
        # 用户可关闭
        if threshold <= 0 or threshold >= 1:
            return []

        ctx = self._load_project_context()
        if not ctx:
            return []

        # 当前文件被分到哪类（用 layer 决定看哪份索引）
        if layer == "component":
            existing = ctx.get("components") or []
        elif layer == "hook":
            existing = ctx.get("hooks") or []
        elif layer == "service":
            existing = ctx.get("apis") or []
        else:
            return []

        # 取当前文件主导出名（component/hook：第一个；service：所有）
        my_names = self._extract_primary_export_names(layer, parse_result)
        if not my_names:
            return []

        issues: List[Issue] = []
        for my_name in my_names:
            for existing_item in existing:
                existing_name = (existing_item.get("name") or "").strip()
                existing_file = existing_item.get("file_path") or ""
                if not existing_name:
                    continue
                # 跳过自己（同名同文件 → 不报；同名不同文件 → 走重复实现，severity 提升一档）
                if existing_file == rel_path and existing_name == my_name:
                    continue
                ratio = difflib.SequenceMatcher(None, my_name, existing_name).ratio()
                if ratio < threshold:
                    continue
                same_name = my_name == existing_name
                issues.append(Issue(
                    rule_id="name-similarity",
                    severity="warning" if same_name else "info",
                    category="naming",
                    message=(
                        f"{layer} 命名 {my_name} 与已有 {existing_name} "
                        f"({existing_file}) 相似度 {ratio:.2f}"
                        + ("（同名）" if same_name else "")
                    ),
                    file=rel_path,
                    suggestion=(
                        f"是否要复用已有的 {existing_name}？"
                        if not same_name else
                        f"已存在同名 {layer}，请改名或合并到 {existing_file}"
                    ),
                ))
        return issues

    @staticmethod
    def _extract_primary_export_names(layer: str, parse_result) -> List[str]:
        names: List[str] = []
        if layer == "component":
            for exp in parse_result.exports:
                # 第一个 returns_jsx 或 default 的 export 视为组件主导出
                if exp.returns_jsx or exp.kind == "default":
                    if exp.name and exp.name != "default":
                        names.append(exp.name)
                    break
        elif layer == "hook":
            for exp in parse_result.exports:
                if exp.name and exp.name.startswith("use"):
                    names.append(exp.name)
                    break
        elif layer == "service":
            for exp in parse_result.exports:
                if exp.kind in ("function", "const", "let", "var", "class", "default"):
                    if exp.name and exp.name != "default":
                        names.append(exp.name)
        return names

    def _load_project_context(self) -> Optional[Dict]:
        if self._context_cache is not None:
            return self._context_cache
        f = self.harness_dir / "context" / "project-context.json"
        if not f.exists():
            return None
        try:
            self._context_cache = json.loads(f.read_text(encoding="utf-8"))
            return self._context_cache
        except (json.JSONDecodeError, OSError):
            return None

    def _cfg_enabled(self, name: str, default: bool) -> bool:
        sub = self.checks_cfg.get(name)
        if isinstance(sub, dict) and "enabled" in sub:
            return bool(sub["enabled"])
        return default

    # -- 层级推断 -----------------------------------------------------------

    def _classify_layer(self, rel_posix: str) -> str:
        for layer in self.layers:
            for prefix in layer["paths"]:
                if rel_posix.startswith(prefix):
                    return layer["name"]
        return "unknown"

    def _infer_target_layer(self, source: str) -> Optional[str]:
        """根据 import source 反查目标 layer。

        - @/foo → 把它视作 src/foo，再用 layer.paths 匹配
        - 相对路径 / 外部包 → None（不参与架构检查）
        """
        if not source.startswith("@/"):
            return None
        synthetic = "src/" + source[2:]
        for layer in self.layers:
            for prefix in layer["paths"]:
                # 让 "src/api" 匹配 "src/api/" 前缀
                normalized = prefix.rstrip("/")
                if synthetic == normalized or synthetic.startswith(normalized + "/"):
                    return layer["name"]
        return None

    def _allowed_imports_for(self, layer_name: str) -> List[str]:
        for layer in self.layers:
            if layer["name"] == layer_name:
                return list(layer.get("can_import", []) or [])
        return []

    # -- 建议文案 -----------------------------------------------------------

    @staticmethod
    def _suggest_for_forbidden(source: str) -> str:
        if source.startswith("@/services"):
            return "@/services 路径不存在，请使用 @/api"
        if source.startswith("@/api/mockApi"):
            return "禁止直接耦合 mock 实现，请通过 @/api/<service> 访问"
        return "请改用允许的导入路径（见 rules.yaml imports.allowed_prefixes）"

    # -- 工具 ---------------------------------------------------------------

    def _load_rules(self) -> Dict:
        import yaml
        rules_file = self.harness_dir / "rules.yaml"
        if not rules_file.exists():
            return {}
        return yaml.safe_load(rules_file.read_text(encoding="utf-8")) or {}

    @staticmethod
    def _normalize_layers(layers_cfg: List[Dict]) -> List[Dict]:
        normalized = []
        for layer in layers_cfg or []:
            normalized.append({
                "name": layer.get("name", ""),
                "paths": [p.replace("\\", "/") for p in (layer.get("paths") or [])],
                "can_import": layer.get("can_import") or [],
            })
        return normalized


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------


def validate_file(file_path: str, project_dir: Optional[Path] = None) -> List[Dict]:
    """供 CLI / Hook 使用的便捷入口。"""
    validator = CodeValidator(project_dir or Path.cwd())
    return [asdict(i) for i in validator.validate_file(file_path)]
