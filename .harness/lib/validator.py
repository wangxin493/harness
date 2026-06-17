#!/usr/bin/env python3
"""Harness 2.0 验证器（P0 最小版）—— lib/validator.py

P0 范围（必须 AST，不用正则）：
- 调 ast_parser.TypeScriptParser 解析文件；parse_errors 视为 error
- 架构层依赖检查：layer 不在 can_import 白名单 → error
- 禁用导入检查：rules.yaml imports.forbidden_imports → error

P1 会在此基础上扩展：tsc 集成、命名规范、type-no-any、错误截断等
（执行清单 #11、设计文档 4.6 节中 _quick_check / _type_check 的扩展）。
"""

from __future__ import annotations

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
        self.parser = TypeScriptParser()

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

        return issues

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
