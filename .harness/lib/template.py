#!/usr/bin/env python3
"""Harness 2.0 模板生成器 —— lib/template.py

`harness new <kind> <name>` 一次产出"已经符合所有规则"的新文件骨架：

- 5 种 kind：component / page / hook / service / type
- 目标目录从 rules.yaml architecture.layers 读，按 KindSpec.layer_path_hint
  在层内多路径间挑选；不写死路径
- 命名规则:rules.yaml naming.* (PascalCase / use 前缀 / Service 后缀)
- 模板正文从 .harness/templates/<kind>.<ext>.tmpl 读，用 `{{name}}` 占位
  - 文件不存在 → 用内置最小骨架兜底（保持 P0 行为）
  - rules.yaml templates.kinds.<kind> 可覆盖 extension / template_file /
    default_dir / layer_path_hint 四个字段
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
    template_file: str                              # .harness/ 内相对路径，例 templates/component.tsx.tmpl
    fallback_render: Callable[[str], str]           # 模板文件缺失时的兜底渲染
    # 在层内多路径中选择目标目录的提示词（POSIX 子串，大小写不敏感）：
    # - 非空 → 优先选含该子串的路径（例 "page" 让 page kind 落到 src/pages/）
    # - 空 → 反向：避开任何含 "page" 的路径，避免 component 落到 pages/
    layer_path_hint: str = ""
    # B5: 多子串优先匹配,按列表顺序找首个命中的 layer.paths。
    # 命中任意一条 → 立即返回该路径。
    # 列表为空 → 回退到 layer_path_hint(向后兼容)。
    # 例 page kind: ["page", "screen", "view"] 会让 src/views/ / src/screens/ 都能命中。
    prefer_path_contains: List[str] = field(default_factory=list)


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
# 兜底模板（当 templates/<kind>.<ext>.tmpl 不存在时使用，保持 P0 行为）
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
# KindSpec 默认注册表
# (layer 名与 rules.yaml architecture.layers 对齐;default_dir 仅做回退,
#  真实落盘目录从 layers.paths 取，按 layer_path_hint 在层内多路径中挑选)
# ---------------------------------------------------------------------------

# placeholder; full registry built below

_DEFAULT_KIND_SPECS: Dict[str, KindSpec] = {
    "component": KindSpec(
        kind="component", layer="component",
        default_dir="src/components/", extension=".tsx",
        validate=_validate_pascal, suggest=_suggest_pascal,
        template_file="templates/component.tsx.tmpl",
        fallback_render=_tpl_component,
        layer_path_hint="",          # 避开 page 路径
    ),
    "page": KindSpec(
        kind="page", layer="component",        # page 属 component 层
        default_dir="src/pages/", extension=".tsx",
        validate=_validate_pascal, suggest=_suggest_pascal,
        template_file="templates/page.tsx.tmpl",
        fallback_render=_tpl_page,
        layer_path_hint="page",      # 兼容旧 hint
        # B5: 默认子串列表,识别常见的页面目录命名(pages/screens/views)
        prefer_path_contains=["page", "screen", "view"],
    ),
    "hook": KindSpec(
        kind="hook", layer="hook",
        default_dir="src/hooks/", extension=".ts",
        validate=_validate_use_prefix, suggest=_suggest_use_prefix,
        template_file="templates/hook.ts.tmpl",
        fallback_render=_tpl_hook,
    ),
    "service": KindSpec(
        kind="service", layer="service",
        default_dir="src/api/", extension=".ts",
        validate=_validate_service_suffix, suggest=_suggest_service_suffix,
        template_file="templates/service.ts.tmpl",
        fallback_render=_tpl_service,
    ),
    "type": KindSpec(
        kind="type", layer="type",
        default_dir="src/types/", extension=".ts",
        validate=_validate_pascal, suggest=_suggest_pascal,
        template_file="templates/type.ts.tmpl",
        fallback_render=_tpl_type,
    ),
}


def supported_kinds() -> List[str]:
    return list(_DEFAULT_KIND_SPECS.keys())


def _build_kind_specs(rules: Dict) -> Dict[str, KindSpec]:
    """从 rules.yaml templates.kinds 注入覆盖项；缺省字段沿用 default。

    支持覆盖：extension / template_file / default_dir / layer_path_hint。
    layer / validate / suggest / fallback_render 依然来自内置注册表（决定
    模板属于哪个 kind 的语义）。未知 kind 直接忽略，避免因为配置错误整个
    `harness new` 不能跑。
    """
    overrides = ((rules or {}).get("templates") or {}).get("kinds") or {}
    if not isinstance(overrides, dict):
        return dict(_DEFAULT_KIND_SPECS)

    specs: Dict[str, KindSpec] = {}
    for kind, default in _DEFAULT_KIND_SPECS.items():
        cfg = overrides.get(kind) or {}
        if not isinstance(cfg, dict):
            specs[kind] = default
            continue
        specs[kind] = KindSpec(
            kind=default.kind,
            layer=cfg.get("layer") or default.layer,
            default_dir=str(cfg.get("default_dir") or default.default_dir),
            extension=str(cfg.get("extension") or default.extension),
            validate=default.validate,
            suggest=default.suggest,
            template_file=str(
                cfg.get("template_file") or default.template_file
            ),
            fallback_render=default.fallback_render,
            layer_path_hint=str(
                cfg.get("layer_path_hint")
                if cfg.get("layer_path_hint") is not None
                else default.layer_path_hint
            ),
            prefer_path_contains=_as_str_list(
                cfg.get("prefer_path_contains")
                if cfg.get("prefer_path_contains") is not None
                else default.prefer_path_contains
            ),
        )
    return specs


def _as_str_list(value) -> List[str]:
    """把 yaml 读出来的可能是 None/str/list 的值规范成 List[str]。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return []


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


class TemplateGenerator:
    """模板生成器。"""

    # 占位符：模板里 {{name}} 会被替换成传入的 name
    _PLACEHOLDER = "{{name}}"

    def __init__(self, project_dir: Path, harness_dir: Optional[Path] = None):
        self.project_dir = Path(project_dir).resolve()
        self.harness_dir = (harness_dir or (self.project_dir / ".harness")).resolve()
        self._rules_cache: Optional[Dict] = None
        self._context_cache: Optional[Dict] = None
        # 注意：specs 里包含 rules.yaml 的覆盖，rules 变了需要重建实例
        self._specs: Dict[str, KindSpec] = _build_kind_specs(self._load_rules())

    # -- 公共 API -----------------------------------------------------------

    def generate(
        self,
        kind: str,
        name: str,
        force: bool = False,
        path_override: Optional[str] = None,
    ) -> GenerateResult:
        """生成单个文件骨架。

        - kind: component / page / hook / service / type（含 rules.yaml 覆盖项）
        - name: 导出名 + 文件名(去扩展名)
        - force: 已存在/同名冲突时强制覆盖
        - path_override: 自定义落盘目录(POSIX 相对);默认按 rules.yaml 取层路径
        """
        spec = self._get_spec(kind)

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
        abs_path.write_text(self._render(spec, name), encoding="utf-8")
        result.written = True
        return result

    def validate_name(self, kind: str, name: str) -> Optional[str]:
        """仅校验命名(不查冲突,不落盘)。合法返回 None,否则返回错因。"""
        return self._get_spec(kind).validate(name)

    def suggest_name(self, kind: str, name: str) -> str:
        """给一个建议名(永远返回字符串;输入合法时也会"规范化"一次)。"""
        return self._get_spec(kind).suggest(name)

    # -- 内部 ---------------------------------------------------------------

    def _get_spec(self, kind: str) -> KindSpec:
        if kind not in self._specs:
            raise TemplateError(
                f"未知 kind: {kind}(支持: {', '.join(self._specs.keys())})"
            )
        return self._specs[kind]

    def _render(self, spec: KindSpec, name: str) -> str:
        """读 .harness/templates/<kind>.<ext>.tmpl，把 {{name}} 替换；
        模板缺失走 fallback_render（保持 P0 行为）。"""
        tmpl_path = self.harness_dir / spec.template_file
        if tmpl_path.is_file():
            try:
                tmpl = tmpl_path.read_text(encoding="utf-8")
            except OSError:
                return spec.fallback_render(name)
            return tmpl.replace(self._PLACEHOLDER, name)
        return spec.fallback_render(name)

    def _resolve_target_dir(
        self, spec: KindSpec, override: Optional[str]
    ) -> str:
        """落盘目录解析。

        优先级:
        1) override(用户给的)
        2) rules.yaml architecture.layers 里 spec.layer 的路径,挑选规则:
           a) prefer_path_contains 非空 → 按列表顺序找首个子串命中(B5)
           b) layer_path_hint 非空 → 选含该 hint 的路径
           c) 都空 → 取首个不含 "page" 的路径(避免 component 落到 pages/)
        3) spec.default_dir(兜底)
        """
        if override:
            return override.replace("\\", "/")

        rules = self._load_rules()
        for layer in (rules.get("architecture") or {}).get("layers") or []:
            if layer.get("name") != spec.layer:
                continue
            paths = [p.replace("\\", "/") for p in (layer.get("paths") or [])]

            # B5: prefer_path_contains 多子串顺序匹配
            for needle in spec.prefer_path_contains:
                needle_lc = needle.lower()
                for p in paths:
                    if needle_lc in p.lower():
                        return p

            hint = (spec.layer_path_hint or "").lower()
            if hint:
                for p in paths:
                    if hint in p.lower():
                        return p
                # 没匹配上 hint → 退到 spec.default_dir
                return spec.default_dir
            # hint 为空：取首个不含 "page" 的路径，避免 component 落到 pages/
            for p in paths:
                if "page" in p.lower():
                    continue
                return p
            if paths:
                return paths[0]
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
        }.get(spec.kind)
        if not bucket_key:
            return None
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
