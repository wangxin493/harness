#!/usr/bin/env python3
"""rules.yaml 共享解析工具。"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple


_PASCAL_RE = re.compile(r"^[A-Z][A-Za-z0-9]*$")
_CAMEL_RE = re.compile(r"^[a-z][A-Za-z0-9]*$")


def validate_name_style(name: str, style: str) -> Optional[str]:
    """校验 name 是否符合 style。style 支持 adopt: 前缀（会被自动剥除）。

    返回 None 表示合规；返回字符串表示违规原因。
    未知 style 返回 None（静默放行）。
    """
    if style.startswith("adopt:"):
        style = style[len("adopt:"):]
    if style == "PascalCase":
        if not _PASCAL_RE.match(name):
            return "必须 PascalCase（首字母大写，只含字母数字）"
    elif style == "camelCase":
        if not _CAMEL_RE.match(name):
            return "必须 camelCase（首字母小写，只含字母数字）"
    elif style == "camelCase-with-use-prefix":
        if not _CAMEL_RE.match(name):
            return "必须 camelCase（首字母小写，只含字母数字）"
        if not (name.startswith("use") and len(name) > 3 and name[3].isupper()):
            return "必须以 use 开头，且 use 后第一个字母大写（例 useTodos）"
    elif style == "camelCase-with-Service-suffix":
        if not _CAMEL_RE.match(name):
            return "必须 camelCase（首字母小写，只含字母数字）"
        if not name.endswith("Service") or name == "Service":
            return "必须以 Service 结尾（例 userService）"
    return None


def parse_import_aliases(scanner_cfg: Dict) -> List[Tuple[str, str]]:
    """统一解析 scanner.import_aliases / import_alias 配置。"""
    aliases: List[Tuple[str, str]] = []
    source_root = (scanner_cfg.get("source_root") or "src").replace("\\", "/")

    multi = scanner_cfg.get("import_aliases") or []
    if isinstance(multi, dict):
        for prefix, target in multi.items():
            _append_alias(aliases, prefix, target)
    elif isinstance(multi, list):
        for item in multi:
            if not isinstance(item, dict):
                continue
            _append_alias(aliases, item.get("prefix"), item.get("target"))

    single = scanner_cfg.get("import_alias")
    if isinstance(single, str) and single:
        # 旧格式：import_alias: "@/"（字符串）
        target = scanner_cfg.get("import_alias_target") or source_root
        _append_alias(aliases, single, target)
    elif isinstance(single, dict):
        # init_resolver 写出的格式：{"prefix": "@/", "target": "src/"}
        _append_alias(aliases, single.get("prefix"), single.get("target"))

    if not aliases:
        _append_alias(aliases, "@/", source_root)

    aliases.sort(key=lambda item: len(item[0]), reverse=True)
    return aliases


def normalize_layers(layers_cfg: List[Dict]) -> List[Dict]:
    """统一规范化 architecture.layers。"""
    normalized = []
    for layer in layers_cfg or []:
        if not isinstance(layer, dict):
            continue
        paths = []
        for path in layer.get("paths") or []:
            if isinstance(path, str) and path:
                paths.append(path.replace("\\", "/").rstrip("/"))
        normalized.append({
            "name": layer.get("name", ""),
            "paths": paths,
            "can_import": list(layer.get("can_import") or [])
            if isinstance(layer.get("can_import"), (list, tuple))
            else ([layer["can_import"]] if isinstance(layer.get("can_import"), str) and layer.get("can_import") else []),
        })
    return normalized


def normalize_sub_layer_convention(convention_cfg) -> Dict[str, str]:
    """规范化 architecture.sub_layer_convention。

    返回 {子目录名: 层名} 的映射，例如：
        {"components": "component", "hooks": "hook", "utils": "util"}
    """
    if not isinstance(convention_cfg, dict):
        return {}
    return {
        str(k).rstrip("/"): str(v)
        for k, v in convention_cfg.items()
        if isinstance(k, str) and isinstance(v, str) and k and v
    }


def classify_layer(
    rel_posix: str,
    layers: List[Dict],
    sub_layer_convention: Optional[Dict[str, str]] = None,
) -> str:
    """按路径前缀匹配文件所属层。

    匹配优先级：
    1. 精确前缀匹配（原逻辑）：路径以某层的 paths 条目开头，直接返回该层。
    2. sub_layer_convention 匹配（新增）：路径包含约定子目录段时，返回对应层。
       例如：配置 {"components": "component"} 后，任意层下的 .../components/...
       文件都会被归为 component 层，无论父目录叫 module/、pages/ 还是 views/。
    """
    normalized_rel = rel_posix.replace("\\", "/")

    # 1. sub_layer_convention 优先：局部子目录约定覆盖父层归类
    #    从路径最深处往上找，越靠近文件的目录段越优先
    if sub_layer_convention:
        parts = normalized_rel.split("/")
        for part in reversed(parts[:-1]):  # 跳过最后一段（文件名本身）
            if part in sub_layer_convention:
                return sub_layer_convention[part]

    # 2. 精确前缀匹配（无 convention 命中时兜底）
    for layer in layers:
        for prefix in layer.get("paths") or []:
            normalized_prefix = prefix.rstrip("/")
            if normalized_rel == normalized_prefix or normalized_rel.startswith(normalized_prefix + "/"):
                return layer.get("name") or "unknown"

    return "unknown"


def _append_alias(aliases: List[Tuple[str, str]], prefix, target) -> None:
    if not isinstance(prefix, str):
        return
    # target 允许是列表（如 tsconfig paths 的 ["src/*"]），取第一项并去掉 "/*" 后缀
    if isinstance(target, list):
        target = target[0] if target else None
    if not isinstance(target, str):
        return
    prefix = prefix.replace("\\", "/").strip()
    # 去掉 tsconfig paths 常见的 "/*" glob 后缀（如 "src/*" → "src"）
    target = re.sub(r"/\*$", "", target.replace("\\", "/").strip())
    if not prefix or not target:
        return
    aliases.append((prefix.rstrip("/") + "/", target.rstrip("/")))
