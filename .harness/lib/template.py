#!/usr/bin/env python3
"""Harness 2.0 模板生成器 —— lib/template.py

`harness new <kind> <name>` 一次产出"已经符合所有规则"的新文件骨架：

- 5 种 kind：component / page / hook / service / type
- 目标目录从 rules.yaml architecture.layers 读，首个匹配前缀；不写死路径
- 命名规则:rules.yaml naming.* (PascalCase / use 前缀 / Service 后缀)
- 冲突保护:
  - 目标文件已存在 → 拒绝（除非 --force）
  - project-context.json 命中同名 → 警告（除非 --force）

不做的事:
- 不动 PostToolUse hook;新文件落盘后由 hook 跑 validate 兜底
- 不更新 project-context.json（那是 scanner 的领地;下一次 scan 自动捡到）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class KindSpec:
    """一种 kind 的元信息(路径/扩展名/命名/模板)。"""

    kind: str                                       # component | page | hook | service | type
    layer: str                                      # 对应 rules.yaml 层名
    default_dir: str                                # 缺省落盘目录(POSIX 相对)
    extension: str                                  # .tsx / .ts
    validate: Callable[[str], Optional[str]]        # 返回 None=合法,否则返回错因
    suggest: Callable[[str], str]                   # 错名 → 建议名
    render: Callable[[str], str]                    # name → 模板正文


@dataclass
class GenerateResult:
    """new 子命令结果。"""

    file: str = ""                                  # POSIX 相对路径
    kind: str = ""
    name: str = ""
    written: bool = False
    skipped_reason: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


class TemplateError(RuntimeError):
    """命名 / 冲突 / 配置类问题;CLI 据此映射 exit code。"""


# ---------------------------------------------------------------------------
# 命名校验 + 建议
# ---------------------------------------------------------------------------


_PASCAL = re.compile(r"^[A-Z][A-Za-z0-9]*$")
_CAMEL = re.compile(r"^[a-z][A-Za-z0-9]*$")


def _validate_pascal(name: str) -> Optional[str]:
    if not name:
        return "名字不能为空"
    if not _PASCAL.match(name):
        return "必须 PascalCase(首字母大写,只含字母数字)"
    return None


def _suggest_pascal(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", " ", name or "").strip()
    parts = [p for p in cleaned.split(" ") if p]
    if not parts:
        return "MyComponent"
    return "".join(p[:1].upper() + p[1:] for p in parts)


def _validate_use_prefix(name: str) -> Optional[str]:
    if not name:
        return "名字不能为空"
    if not _CAMEL.match(name):
        return "必须 camelCase(首字母小写,只含字母数字)"
    if not (name.startswith("use") and len(name) > 3 and name[3].isupper()):
        return "必须以 use 开头,且 use 后第一个字母大写(例 useTodos)"
    return None


def _suggest_use_prefix(name: str) -> str:
    raw = name or ""
    # 已经是 use 开头但格式错 → 修剩下部分
    if raw.lower().startswith("use") and len(raw) > 3:
        rest = raw[3:]
        rest = _suggest_pascal(rest)
        return "use" + rest
    body = _suggest_pascal(raw)
    if not body:
        return "useSomething"
    return "use" + body


def _validate_service_suffix(name: str) -> Optional[str]:
    if not name:
        return "名字不能为空"
    if not _CAMEL.match(name):
        return "必须 camelCase(首字母小写,只含字母数字)"
    if not name.endswith("Service") or name == "Service":
        return "必须以 Service 结尾(例 userService)"
    return None


def _suggest_service_suffix(name: str) -> str:
    raw = name or ""
    # 截掉已有的 Service 后缀(可能大小写错)
    stem = re.sub(r"[Ss]ervice$", "", raw).strip()
    if not stem:
        return "somethingService"
    # 首字母小写,其余按 PascalCase 拆,再拼回 camelCase
    parts = re.sub(r"[^A-Za-z0-9]+", " ", stem).split()
    if not parts:
        return "somethingService"
    head = parts[0][:1].lower() + parts[0][1:]
    rest = "".join(p[:1].upper() + p[1:] for p in parts[1:])
    return f"{head}{rest}Service"


# ---------------------------------------------------------------------------
# 模板正文(返回字符串;不写盘)
# ---------------------------------------------------------------------------


def _tpl_component(name: str) -> str:
    return (
        "import type { FC } from 'react';\n"
        "\n"
        f"export interface {name}Props {{\n"
        "  // TODO: 定义 props\n"
        "}\n"
        "\n"
        f"export const {name}: FC<{name}Props> = (props) => {{\n"
        f"  return <div>{name}</div>;\n"
        "};\n"
    )


def _tpl_page(name: str) -> str:
    return (
        "import type { FC } from 'react';\n"
        "\n"
        f"export const {name}: FC = () => {{\n"
        f"  return <div>{name}</div>;\n"
        "};\n"
    )


def _tpl_hook(name: str) -> str:
    return (
        "import { useState } from 'react';\n"
        "\n"
        f"export const {name} = () => {{\n"
        "  const [items, setItems] = useState<unknown[]>([]);\n"
        "  // TODO: 业务逻辑\n"
        "  return { items, setItems };\n"
        "};\n"
    )


def _tpl_service(name: str) -> str:
    return (
        "// import type { } from '@/types';\n"
        "\n"
        f"export const {name} = async () => {{\n"
        "  // TODO: API 调用\n"
        "};\n"
    )


def _tpl_type(name: str) -> str:
    return (
        f"export interface {name} {{\n"
        "  // TODO: 定义字段\n"
        "  id: string;\n"
        "}\n"
    )


# ---------------------------------------------------------------------------
# KindSpec 注册表(layer 名与 rules.yaml 对齐;default_dir 仅做回退,真实从 rules 取)
# ---------------------------------------------------------------------------


_KIND_SPECS: Dict[str, KindSpec] = {
    "component": KindSpec(
        kind="component", layer="component",
        default_dir="src/components/", extension=".tsx",
        validate=_validate_pascal, suggest=_suggest_pascal, render=_tpl_component,
    ),
    "page": KindSpec(
        kind="page", layer="component",        # page 属 component 层
        default_dir="src/pages/", extension=".tsx",
        validate=_validate_pascal, suggest=_suggest_pascal, render=_tpl_page,
    ),
    "hook": KindSpec(
        kind="hook", layer="hook",
        default_dir="src/hooks/", extension=".ts",
        validate=_validate_use_prefix, suggest=_suggest_use_prefix, render=_tpl_hook,
    ),
    "service": KindSpec(
        kind="service", layer="service",
        default_dir="src/api/", extension=".ts",
        validate=_validate_service_suffix, suggest=_suggest_service_suffix,
        render=_tpl_service,
    ),
    "type": KindSpec(
        kind="type", layer="type",
        default_dir="src/types/", extension=".ts",
        validate=_validate_pascal, suggest=_suggest_pascal, render=_tpl_type,
    ),
}


def supported_kinds() -> List[str]:
    return list(_KIND_SPECS.keys())


def _get_spec(kind: str) -> KindSpec:
    if kind not in _KIND_SPECS:
        raise TemplateError(
            f"未知 kind: {kind}(支持: {', '.join(supported_kinds())})"
        )
    return _KIND_SPECS[kind]


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


class TemplateGenerator:
    """模板生成器。"""

    def __init__(self, project_dir: Path, harness_dir: Optional[Path] = None):
        self.project_dir = Path(project_dir).resolve()
        self.harness_dir = (harness_dir or (self.project_dir / ".harness")).resolve()
        self._rules_cache: Optional[Dict] = None
        self._context_cache: Optional[Dict] = None

    # -- 公共 API -----------------------------------------------------------

    def generate(
        self,
        kind: str,
        name: str,
        force: bool = False,
        path_override: Optional[str] = None,
    ) -> GenerateResult:
        """生成单个文件骨架。

        - kind: component / page / hook / service / type
        - name: 导出名 + 文件名(去扩展名)
        - force: 已存在/同名冲突时强制覆盖
        - path_override: 自定义落盘目录(POSIX 相对);默认按 rules.yaml 取层首路径
        """
        spec = _get_spec(kind)

        err = spec.validate(name)
        if err:
            suggested = spec.suggest(name)
            raise TemplateError(
                f"{kind} 命名不合规:{err}。建议:{suggested}"
            )

        # 落盘目录
        target_dir = self._resolve_target_dir(spec, path_override)
        rel_path = f"{target_dir.rstrip('/')}/{name}{spec.extension}"
        abs_path = self.project_dir / rel_path

        result = GenerateResult(file=rel_path, kind=kind, name=name)

        # 冲突 1:目标文件已存在
        if abs_path.exists() and not force:
            result.skipped_reason = (
                f"文件已存在:{rel_path}(加 --force 覆盖)"
            )
            return result

        # 冲突 2:project-context.json 命中同名(警告而非阻断,除非 --force off)
        dup = self._find_duplicate_name(spec, name)
        if dup and dup != rel_path:
            warning = f"已存在同名 {kind}:{dup}(加 --force 仍可创建另一个)"
            if not force:
                result.skipped_reason = warning
                return result
            result.warnings.append(warning)

        # 落盘
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(spec.render(name), encoding="utf-8")
        result.written = True
        return result

    def validate_name(self, kind: str, name: str) -> Optional[str]:
        """仅校验命名(不查冲突,不落盘)。合法返回 None,否则返回错因。"""
        return _get_spec(kind).validate(name)

    def suggest_name(self, kind: str, name: str) -> str:
        """给一个建议名(永远返回字符串;输入合法时也会"规范化"一次)。"""
        return _get_spec(kind).suggest(name)

    # -- 内部 ---------------------------------------------------------------

    def _resolve_target_dir(
        self, spec: KindSpec, override: Optional[str]
    ) -> str:
        """落盘目录解析。

        优先级:
        1) override(用户给的)
        2) rules.yaml architecture.layers 里 spec.layer 的首路径
        3) spec.default_dir(兜底)
        """
        if override:
            return override.replace("\\", "/")

        rules = self._load_rules()
        for layer in (rules.get("architecture") or {}).get("layers") or []:
            if layer.get("name") != spec.layer:
                continue
            paths = layer.get("paths") or []
            for p in paths:
                # page 特殊:在 component 层的 paths 里挑包含 'page' 的
                if spec.kind == "page":
                    if "page" in p.lower():
                        return p.replace("\\", "/")
                else:
                    # 其它 kind 取层首路径,但避开 page 路径(component kind 不该落到 pages/)
                    if spec.kind == "component" and "page" in p.lower():
                        continue
                    return p.replace("\\", "/")
        return spec.default_dir

    def _find_duplicate_name(self, spec: KindSpec, name: str) -> Optional[str]:
        """在 project-context.json 里找同名条目;返回其文件路径或 None。"""
        ctx = self._load_context()
        if not ctx:
            return None
        bucket_key = {
            "component": "components",
            "page": "components",
            "hook": "hooks",
            "service": "apis",
            "type": "types",
        }[spec.kind]
        for item in ctx.get(bucket_key) or []:
            if (item.get("name") or "") == name:
                return item.get("file_path") or None
        return None

    def _load_rules(self) -> Dict:
        if self._rules_cache is not None:
            return self._rules_cache
        f = self.harness_dir / "rules.yaml"
        if not f.exists():
            self._rules_cache = {}
            return self._rules_cache
        try:
            import yaml
            self._rules_cache = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except Exception:
            self._rules_cache = {}
        return self._rules_cache

    def _load_context(self) -> Dict:
        if self._context_cache is not None:
            return self._context_cache
        f = self.harness_dir / "context" / "project-context.json"
        if not f.exists():
            self._context_cache = {}
            return self._context_cache
        try:
            self._context_cache = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._context_cache = {}
        return self._context_cache
