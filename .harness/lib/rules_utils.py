#!/usr/bin/env python3
"""rules.yaml 共享解析工具。"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple


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
        target = scanner_cfg.get("import_alias_target") or source_root
        _append_alias(aliases, single, target)

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


def classify_layer(rel_posix: str, layers: List[Dict]) -> str:
    """按路径前缀匹配文件所属层。"""
    normalized_rel = rel_posix.replace("\\", "/")
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
