#!/usr/bin/env python3
"""
依赖层级检查脚本
确保代码符合三层架构的依赖规则: Service → Hook → Component
"""

import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Optional

# 三层架构定义
LAYERS = {
    "component": {"allowed": ["hook", "service"]},
    "hook": {"allowed": ["service"]},
    "service": {"allowed": []},
}

# 三层架构目录映射
LAYER_PATTERNS = {
    "component": r"(pages|components|views)",
    "hook": r"(hooks|composables)",
    "service": r"(api|services)",
}

# 忽略的文件/目录
IGNORE_PATTERNS = [
    r"__pycache__",
    r"\.pyc$",
    r"node_modules",
    r"\.git",
    r"\.test\.",
    r"tests?",
    r"\.spec\.",
]


def get_layer_from_path(file_path: Path) -> Optional[str]:
    """根据文件路径确定所属层级"""
    path_str = str(file_path)
    for layer, pattern in LAYER_PATTERNS.items():
        if re.search(pattern, path_str):
            return layer
    return None


def should_ignore(file_path: Path) -> bool:
    """判断文件是否应该被忽略"""
    path_str = str(file_path)
    for pattern in IGNORE_PATTERNS:
        if re.search(pattern, path_str):
            return True
    return False


def extract_imports(file_path: Path) -> List[str]:
    """从文件中提取导入语句"""
    if file_path.suffix == ".ts" or file_path.suffix == ".tsx":
        return extract_typescript_imports(file_path)
    elif file_path.suffix in (".js", ".jsx"):
        return extract_javascript_imports(file_path)
    return []


def extract_typescript_imports(file_path: Path) -> List[str]:
    """从 TypeScript 文件中提取导入"""
    imports = []
    try:
        content = file_path.read_text()
        patterns = [
            r'from\s+["\']([^"\']+)["\']',          # import ... from '...'
            r'import\s*\([^)]+\)\s+from\s+["\']([^"\']+)["\']',  # import { ... } from '...'
        ]
        for pattern in patterns:
            matches = re.findall(pattern, content)
            for match in matches:
                if isinstance(match, tuple):
                    imports.append(match[-1])  # 取最后一个匹配的路径
                else:
                    imports.append(match)
    except Exception:
        pass
    return list(set(imports))  # 去重


def extract_javascript_imports(file_path: Path) -> List[str]:
    """从 JavaScript 文件中提取导入"""
    imports = []
    try:
        content = file_path.read_text()
        patterns = [
            r'from\s+["\']([^"\']+)["\']',
            r'require\(["\']([^"\']+)["\']\)',
        ]
        for pattern in patterns:
            imports.extend(re.findall(pattern, content))
    except Exception:
        pass
    return list(set(imports))


def check_dependency_rule(
    source_layer: str, target_layer: str
) -> Tuple[bool, str]:
    """检查依赖规则是否被违反"""
    if source_layer == target_layer:
        return True, ""

    allowed = LAYERS.get(source_layer, {}).get("allowed", [])
    if target_layer in allowed:
        return True, ""

    # 特殊规则: Component 允许直接依赖 Service（简单场景）
    if source_layer == "component" and target_layer == "service":
        return True, "warning: 推荐通过 Hook 层"

    return False, f"{source_layer} cannot depend on {target_layer}"


def scan_directory(root_dir: Path) -> List[Tuple[Path, List[str]]]:
    """扫描目录中的所有代码文件"""
    files = []
    for root, dirs, filenames in os.walk(root_dir):
        # 过滤忽略的目录
        dirs[:] = [d for d in dirs if not should_ignore(Path(d))]

        for filename in filenames:
            file_path = Path(root) / filename
            if not should_ignore(file_path) and file_path.suffix in (
                ".ts",
                ".tsx",
                ".js",
                ".jsx",
            ):
                imports = extract_imports(file_path)
                if imports:
                    files.append((file_path, imports))
    return files


def main():
    """主函数"""
    root_dir = Path(__file__).parent.parent
    src_dir = root_dir / "src"

    if not src_dir.exists():
        print(f"✓ 源代码目录不存在，跳过检查: {src_dir}")
        return 0

    print(f"扫描目录: {src_dir}")
    print("-" * 60)
    print("三层架构依赖规则检查: Component → Hook → Service")
    print("=" * 60)

    violations = []
    warnings = []
    files = scan_directory(src_dir)

    for file_path, imports in files:
        source_layer = get_layer_from_path(file_path)
        if not source_layer:
            continue

        for imp in imports:
            # 跳过外部库导入
            if not imp.startswith("@/") and not imp.startswith("."):
                continue

            # 处理相对路径
            if imp.startswith("."):
                imp_path = file_path.parent / imp
                imp_path = imp_path.resolve().relative_to(src_dir)
                imp_str = str(imp_path)
            else:
                # 处理 @/ 别名
                imp_str = imp.replace("@/", "")

            # 从导入路径推断目标层级
            target_layer = get_layer_from_path(Path(imp_str))
            if not target_layer:
                continue

            valid, error = check_dependency_rule(source_layer, target_layer)
            if error == "warning: 推荐通过 Hook 层":
                warnings.append({
                    "file": str(file_path.relative_to(root_dir)),
                    "import": imp,
                    "message": error,
                })
            elif not valid:
                violations.append({
                    "file": str(file_path.relative_to(root_dir)),
                    "import": imp,
                    "error": error,
                })

    # 输出警告
    if warnings:
        print("\n⚠️  建议 (P1):")
        print("-" * 60)
        for w in warnings:
            print(f"  {w['file']}")
            print(f"    导入: {w['import']}")
            print(f"    说明: {w['message']}")
            print()

    # 输出错误
    if violations:
        print("\n❌ 发现依赖违规:")
        print("-" * 60)
        for v in violations:
            print(f"  {v['file']}")
            print(f"    导入: {v['import']}")
            print(f"    错误: {v['error']}")
            print()
        print("依赖规则:")
        print("  ✓ Component 可以依赖 Hook, Service")
        print("  ✓ Hook 可以依赖 Service")
        print("  ✗ Hook 不能依赖 Component")
        print("  ✗ Service 不能依赖 Component, Hook")
        return 1
    else:
        print("\n✓ 所有依赖规则检查通过")
        return 0


if __name__ == "__main__":
    sys.exit(main())
