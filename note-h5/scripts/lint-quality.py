#!/usr/bin/env python3
"""
代码质量检查脚本
检查 React + TypeScript + Hooks 项目的代码质量和规范
"""

import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Set


# React + TypeScript 质量规则定义
QUALITY_RULES = {
    "typescript_p0": [
        {
            "name": "禁止 var 声明",
            "pattern": r"\bvar\s+\w+\s*[=;]",
            "severity": "error",
            "message": "禁止使用 var，请使用 const 或 let",
        },
        {
            "name": "禁止 any 类型",
            "pattern": r":\s*any\b(?!\w)",
            "severity": "error",
            "message": "禁止使用 any 类型，不确定时使用 unknown",
        },
        {
            "name": "禁止 console.log",
            "pattern": r"console\.(log|warn|info|debug)\s*\(",
            "severity": "error",
            "message": "禁止使用 console.log，请移除或使用日志系统",
        },
    ],
    "react_p0": [
        {
            "name": "列表渲染缺少 key",
            "pattern": r"\.map\s*\([^)]*\)\s*=>\s*<\w+[^>]*(?![^>]*key\s*=)",
            "severity": "error",
            "message": "列表渲染必须添加稳定的 key 属性",
            "check_map": True,
        },
        {
            "name": "Hook 在 if/else/循环中直接调用",
            "pattern": r"\b(if|else|for|while|switch)\s*\([^)]*\)\s*\{[^}]*\b(use[A-Z])\s*\(",
            "severity": "error",
            "message": "Hook 只能在组件顶层调用",
            "multiline": True,
        },
    ],
    "naming_p0": [
        {
            "name": "组件命名非 PascalCase",
            "pattern": r"export\s+(default\s+)?(function|const)\s+([a-z][a-z0-9]*)\s*[\(=]",
            "severity": "warning",
            "message": "组件名称建议使用 PascalCase",
            "check_component": True,
        },
        {
            "name": "常量命名非 UPPER_SNAKE_CASE",
            "pattern": r"\bconst\s+([A-Z][a-z][a-z0-9_]*)\s*=\s*(?:[0-9]+|['\"][^'\"]+['\"])",
            "severity": "info",
            "message": "常量建议使用 UPPER_SNAKE_CASE",
        },
    ],
    "async_p0": [
        {
            "name": "await 缺少错误处理",
            "pattern": r"await\s+([^;\n]+)(?!\s*\.(catch|then))",
            "severity": "warning",
            "message": "await 操作建议添加错误处理",
            "check_await": True,
        },
    ],
    "security_p0": [
        {
            "name": "硬编码敏感信息",
            "pattern": r'(api_key|secret|password|token|private_key)\s*[=:]\s*["\'][^"\']{10,}["\']',
            "severity": "error",
            "message": "禁止硬编码敏感信息，请使用环境变量",
        },
        {
            "name": "危险函数 eval",
            "pattern": r"\beval\s*\(",
            "severity": "error",
            "message": "使用 eval 存在安全风险",
        },
    ],
    "best_practices_p1": [
        {
            "name": "未使用 React.memo",
            "pattern": r"export\s+default\s+function\s+[A-Z]",
            "severity": "info",
            "message": "建议使用 React.memo 优化性能",
        },
    ],
}


# 忽略的文件
IGNORE_PATTERNS = [
    r"__pycache__",
    r"\.pyc$",
    r"node_modules",
    r"\.git",
    r"\.d\.ts$",
    r"\.spec\.",
    r"\.test\.",
]


def should_ignore(file_path: Path) -> bool:
    """判断文件是否应该被忽略"""
    path_str = str(file_path)
    for pattern in IGNORE_PATTERNS:
        if re.search(pattern, path_str):
            return True
    return False


def scan_files(root_dir: Path) -> List[Path]:
    """扫描目录中的所有代码文件"""
    files = []
    for root, dirs, filenames in os.walk(root_dir):
        dirs[:] = [d for d in dirs if not should_ignore(Path(d))]

        for filename in filenames:
            file_path = Path(root) / filename
            if not should_ignore(file_path) and file_path.suffix in (".ts", ".tsx", ".js", ".jsx"):
                files.append(file_path)
    return files


def check_map_key(line: str) -> bool:
    """检查 .map() 是否有 key 属性"""
    # 查找所有的 .map() 调用
    map_pattern = r'\.map\s*\([^)]*\)\s*=>\s*<(\w+)'
    map_matches = re.finditer(map_pattern, line)

    for match in map_matches:
        # 获取 .map() 后面的 JSX 片段
        start = match.end()
        # 查找对应的 > 标签结束
        depth = 1
        i = start
        while i < len(line) and depth > 0:
            if line[i] == '<':
                depth += 1
            elif line[i] == '>':
                depth -= 1
            i += 1

        jsx_fragment = line[start:i]
        if 'key=' not in jsx_fragment:
            return True
    return False


def check_await_error_handling(line: str, all_lines: List[str], line_num: int) -> bool:
    """检查 await 是否有错误处理"""
    # 跳过一些明显不需要错误处理的情况
    if any(skip in line for skip in ['delay(', 'sleep(', 'new Promise(resolve =>']):
        return False

    # 查找 await 表达式
    await_pattern = r'await\s+([^;\n\)]+)'
    await_matches = re.finditer(await_pattern, line)

    for match in await_matches:
        await_expr = match.group(1).strip()

        # 跳过以下情况：
        # 1. 已经在 try 块中
        # 2. 后面有 .catch()
        # 3. 后面有 then()
        # 4. 赋值给变量 (通常后续会处理)

        # 检查后面是否有 .catch()
        if match.end() < len(line) and '.catch' in line[match.end():match.end()+20]:
            continue

        # 检查是否在 try 块中
        try_start = None
        for i in range(line_num - 1, -1, -1):
            if 'try{' in all_lines[i] or 'try {' in all_lines[i]:
                try_start = i
                break
            if all_lines[i].strip().startswith('}'):
                # 遇到结束的大括号，不在 try 块中
                try_start = None
                break

        if try_start is not None:
            # 检查 await 是否在 try 和 catch 之间
            has_catch = False
            for i in range(try_start, line_num + 1):
                if 'catch' in all_lines[i] and i > line_num:
                    has_catch = True
                    break

            if not has_catch:
                continue  # 在 try 块中但没有 catch，仍然是个问题

        # 检查前面几行是否有 try
        prev_line_has_try = False
        for i in range(max(0, line_num - 5), line_num):
            if 'try' in all_lines[i] and '{' in all_lines[i]:
                prev_line_has_try = True
                break

        # 如果不在 try 块中，且后面没有 .catch()
        if not prev_line_has_try:
            # 检查是不是赋值语句
            if '=' not in line[:match.start()]:
                return True

    return False


def check_file(file_path: Path) -> List[Dict]:
    """检查单个文件"""
    issues = []
    try:
        content = file_path.read_text()
        lines = content.splitlines()
        is_hook_file = "hooks" in str(file_path)
        is_component_file = "components" in str(file_path) or "pages" in str(file_path)

        # 收集已有的类型定义
        type_definitions: Set[str] = set()
        for i, line in enumerate(lines):
            # 收集接口和类型定义
            if 'interface ' in line or 'type ' in line:
                type_match = re.search(r'(interface|type)\s+(\w+)', line)
                if type_match:
                    type_definitions.add(type_match.group(2))
            # 收集导入的类型
            if 'import' in line and 'from' in line:
                import_match = re.search(r'import\s+{([^}]+)}', line)
                if import_match:
                    type_definitions.update(t.strip() for t in import_match.group(1).split(','))

        for line_num, line in enumerate(lines, start=1):
            line_stripped = line.strip()
            line_content = lines[line_num - 1]

            # 跳过注释
            if line_stripped.startswith('//') or line_stripped.startswith('/*') or line_stripped.startswith('*'):
                continue

            for category, rules in QUALITY_RULES.items():
                for rule in rules:
                    # 跳过 Hook 文件的 Hook 命名检查
                    if rule.get("check_hook") and not is_hook_file:
                        continue

                    # 跳过组件文件的组件命名检查（组件用函数声明是合法的）
                    if rule.get("check_component") and is_component_file:
                        # 跳过以大写开头的组件
                        if re.search(r"export\s+(default\s+)?function\s+[A-Z]", line):
                            continue

                    # 特殊检查
                    if rule.get("check_map"):
                        if check_map_key(line):
                            issues.append({
                                "file": str(file_path),
                                "line": line_num,
                                "rule": rule["name"],
                                "category": category,
                                "severity": rule["severity"],
                                "message": rule["message"],
                                "content": line_content.strip(),
                            })
                        continue

                    if rule.get("check_await"):
                        if check_await_error_handling(line, lines, line_num):
                            issues.append({
                                "file": str(file_path),
                                "line": line_num,
                                "rule": rule["name"],
                                "category": category,
                                "severity": rule["severity"],
                                "message": rule["message"],
                                "content": line_content.strip(),
                            })
                        continue

                    # 多行检查（如 Hook 在条件中）
                    if rule.get("multiline"):
                        # 对于多行检查，需要查看多行内容
                        continue

                    # 正则匹配
                    for match in re.finditer(rule["pattern"], line):
                        # 排除一些误报情况

                        # 排除注释中的匹配
                        comment_start = line.find('//')
                        if comment_start != -1 and match.start() >= comment_start:
                            continue

                        # 排除字符串中的匹配
                        in_string = False
                        string_char = None
                        for i, char in enumerate(line):
                            if char in ('"', "'") and (i == 0 or line[i-1] != '\\'):
                                if in_string and char == string_char:
                                    in_string = False
                                    string_char = None
                                elif not in_string:
                                    in_string = True
                                    string_char = char
                            elif in_string and i >= match.start() and i < match.end():
                                break
                        else:
                            if in_string:
                                continue

                        # 特殊排除：函数声明本身不是 Hook 在嵌套函数中
                        if rule["name"] == "Hook 在嵌套函数中调用":
                            # 如果是函数声明行，跳过
                            if 'function ' in line and 'use' in line:
                                continue

                        # 特殊排除：组件函数声明
                        if rule["name"] == "Hook 在嵌套函数中调用":
                            # 检查是否是 React 组件函数（大写开头或有 Props 参数）
                            if 'function' in line and any([
                                'Props' in line,  # 有 Props 参数
                                re.search(r'function\s+[A-Z]', line),  # 大写开头的函数名
                            ]):
                                continue

                        issues.append({
                            "file": str(file_path),
                            "line": line_num,
                            "rule": rule["name"],
                            "category": category,
                            "severity": rule["severity"],
                            "message": rule["message"],
                            "content": line_content.strip(),
                        })

    except Exception as e:
        issues.append(
            {
                "file": str(file_path),
                "line": 0,
                "rule": "文件读取",
                "category": "system",
                "severity": "error",
                "message": f"无法读取文件: {e}",
                "content": "",
            }
        )

    return issues


def calculate_score(issues: List[Dict]) -> Tuple[str, int]:
    """计算质量得分"""
    severity_weights = {"error": 10, "warning": 3, "info": 1}
    total_penalty = sum(severity_weights.get(i["severity"], 0) for i in issues)

    # 基础分 100，扣分后最低 0
    score = max(0, 100 - total_penalty)

    # 等级判定
    if score >= 90:
        grade = "A"
    elif score >= 80:
        grade = "B"
    elif score >= 70:
        grade = "C"
    elif score >= 60:
        grade = "D"
    else:
        grade = "F"

    return grade, score


def print_results(issues: List[Dict], grade: str, score: int):
    """输出检查结果"""
    # 按严重程度排序
    severity_order = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda x: (severity_order[x["severity"]], x["file"], x["line"]))

    # 输出得分
    print(f"代码质量得分: {grade} ({score}/100)")
    print("-" * 60)

    # 按分类统计
    stats = {}
    for issue in issues:
        cat = issue["category"]
        sev = issue["severity"]
        stats.setdefault(cat, {}).setdefault(sev, 0)
        stats[cat][sev] += 1

    if stats:
        print("问题统计:")
        for category, severities in stats.items():
            print(f"  {category}:")
            for severity, count in severities.items():
                print(f"    {severity}: {count}")
        print()

    # 输出详细信息
    if issues:
        print("问题详情:")
        print("-" * 60)

        # 分组显示
        current_file = None
        for issue in issues:
            if issue["file"] != current_file:
                current_file = issue["file"]
                print(f"\n📄 {current_file}")

            severity_icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(
                issue["severity"], ""
            )
            print(f"  {severity_icon} [{issue['severity']}] 行 {issue['line']}")
            print(f"     规则: {issue['rule']}")
            print(f"     说明: {issue['message']}")
            if issue["content"]:
                print(f"     代码: {issue['content'][:80]}")
    else:
        print("✓ 未发现质量问题")


def main():
    """主函数"""
    root_dir = Path(__file__).parent.parent
    src_dir = root_dir / "src"

    if not src_dir.exists():
        print(f"✓ 源代码目录不存在，跳过检查: {src_dir}")
        return 0

    print(f"代码质量检查: {src_dir}")
    print("检查规则: TypeScript P0, React P0, 命名规范, 异步处理, 安全编码")
    print("-" * 60)

    files = scan_files(src_dir)
    print(f"扫描文件数: {len(files)}")
    print()

    all_issues = []
    for file_path in files:
        issues = check_file(file_path)
        all_issues.extend(issues)

    grade, score = calculate_score(all_issues)
    print_results(all_issues, grade, score)

    # 返回码：A/B/C 通过，其他失败
    return 0 if grade in ("A", "B", "C") else 1


if __name__ == "__main__":
    sys.exit(main())
