#!/usr/bin/env python3
"""
工具函数模块
提供文件操作、代码分析等辅助功能
"""

import json
import re
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple


def read_file(file_path: Path) -> str:
    """读取文件内容"""
    if not file_path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")
    return file_path.read_text(encoding="utf-8")


def write_file(file_path: Path, content: str) -> None:
    """写入文件内容"""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")


def parse_json_safely(json_str: str) -> Optional[Dict[str, Any]]:
    """安全解析 JSON"""
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return None


def extract_json_from_text(text: str) -> Optional[Dict[str, Any]]:
    """
    从文本中提取 JSON（处理 LLM 输出可能包含额外文本的情况）

    增强版本：支持多种代码块格式，尝试修复截断的 JSON
    """
    if not text or len(text.strip()) == 0:
        return None

    # 1. 尝试直接解析
    result = parse_json_safely(text)
    if result:
        return result

    # 2. 清理常见问题
    cleaned = text.strip()

    # 移除 BOM
    cleaned = cleaned.lstrip('\ufeff')

    # 移除代码块标记前后的非 JSON 内容
    # 例如: "```json\n{...}\n```\n\nSome explanation"
    code_block_patterns = [
        r'```json\s*([\s\S]*?)\s*```',
        r'```JSON\s*([\s\S]*?)\s*```',
        r'```typescript\s*([\s\S]*?)\s*```',
        r'```ts\s*([\s\S]*?)\s*```',
        r'```\s*([\s\S]*?)\s*```',
    ]

    for pattern in code_block_patterns:
        matches = re.findall(pattern, cleaned, re.DOTALL)
        if matches:
            for match in matches:
                # 尝试解析匹配的内容
                result = parse_json_safely(match.strip())
                if result:
                    return result

                # 如果失败，尝试修复
                fixed = _fix_json_string(match.strip())
                result = parse_json_safely(fixed)
                if result:
                    return result

    # 3. 尝试修复常见 JSON 问题
    fixed = _fix_json_string(cleaned)
    result = parse_json_safely(fixed)
    if result:
        return result

    # 4. 尝试提取 {...} 内容（包括嵌套的花括号）
    result = _extract_braces_content(cleaned)
    if result:
        return result

    return None


def _fix_json_string(json_str: str) -> str:
    """修复常见的 JSON 问题"""
    import re

    fixed = json_str

    # 移除尾部逗号
    fixed = re.sub(r',(\s*[}\]])', r'\1', fixed)

    # 移除 JavaScript 注释 //
    fixed = re.sub(r'//.*', '', fixed)

    # 移除 JavaScript 注释 /* */
    fixed = re.sub(r'/\*.*?\*/', '', fixed, flags=re.DOTALL)

    # 移除单引号，改用双引号（简单处理）
    # 注意：这可能引入问题，保守处理
    # fixed = re.sub(r"'([^']*)'", r'"\1"', fixed)

    # 移除尾部的 JavaScript 关键字和符号
    trailing_patterns = [
        r'\n\s*$/',  # 尾部换行
        r'\n\s*```.*$',  # 尾部代码块结束
        r'\n\s*注意:.*$',  # 尾部注意
        r'\n\s*说明:.*$',  # 尾部说明
    ]
    for pattern in trailing_patterns:
        fixed = re.sub(pattern, '', fixed)

    # 尝试补全缺失的闭合括号
    open_braces = fixed.count('{')
    close_braces = fixed.count('}')
    if open_braces > close_braces and open_braces - close_braces < 5:
        fixed = fixed + '}' * (open_braces - close_braces)

    open_brackets = fixed.count('[')
    close_brackets = fixed.count(']')
    if open_brackets > close_brackets and open_brackets - close_brackets < 5:
        fixed = fixed + ']' * (open_brackets - close_brackets)

    return fixed


def _extract_braces_content(text: str) -> Optional[Dict[str, Any]]:
    """提取花括号包裹的 JSON 内容"""
    import re

    # 匹配最外层的 {...}
    brace_count = 0
    start_idx = -1
    for i, char in enumerate(text):
        if char == '{':
            if brace_count == 0:
                start_idx = i
            brace_count += 1
        elif char == '}':
            brace_count -= 1
            if brace_count == 0 and start_idx != -1:
                json_str = text[start_idx:i+1]
                # 尝试修复
                fixed = _fix_json_string(json_str)
                result = parse_json_safely(fixed)
                if result:
                    return result

    # 尝试匹配数组 [...]
    bracket_count = 0
    start_idx = -1
    for i, char in enumerate(text):
        if char == '[':
            if bracket_count == 0:
                start_idx = i
            bracket_count += 1
        elif char == ']':
            bracket_count -= 1
            if bracket_count == 0 and start_idx != -1:
                json_str = text[start_idx:i+1]
                fixed = _fix_json_string(json_str)
                result = parse_json_safely(fixed)
                if result:
                    return result

    return None


def determine_layer(file_path: Path, base_dir: Path) -> Optional[str]:
    """判断文件所属的架构层级"""
    relative_path = file_path.relative_to(base_dir)

    path_str = str(relative_path).lower()

    if any(pattern in path_str for pattern in ["pages", "components", "views"]):
        return "component"
    elif any(pattern in path_str for pattern in ["hooks", "composables"]):
        return "hook"
    elif any(pattern in path_str for pattern in ["api", "services"]):
        return "service"

    return None


def extract_imports(code: str) -> List[str]:
    """提取代码中的导入语句"""
    imports = []
    patterns = [
        r'import\s+{[^}]+}\s+from\s+["\']([^"\']+)["\']',
        r'import\s+\w+(?:\s*,\s*\w+)*\s+from\s+["\']([^"\']+)["\']',
        r'import\s+["\']([^"\']+)["\']',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, code)
        imports.extend(matches)

    return list(set(imports))


def find_component_name(code: str) -> Optional[str]:
    """从 React 代码中提取组件名称"""
    # 匹配 export function/component 语句
    patterns = [
        r'export\s+function\s+(\w+)',
        r'export\s+const\s+(\w+)\s*=',
        r'function\s+(\w+)\(',
    ]

    for pattern in patterns:
        match = re.search(pattern, code)
        if match:
            return match.group(1)

    return None


def count_lines(code: str) -> int:
    """统计代码行数（排除空行和注释）"""
    lines = code.split('\n')
    count = 0
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith('//') and not stripped.startswith('*'):
            count += 1
    return count


def validate_file_path(file_path: str, base_dir: Path) -> Tuple[bool, Optional[str]]:
    """验证文件路径是否在项目范围内"""
    try:
        path = Path(file_path)
        if not path.is_absolute():
            path = base_dir / file_path

        # 检查是否在 base_dir 下
        try:
            path.relative_to(base_dir)
            return True, str(path)
        except ValueError:
            return False, "路径不在项目目录下"
    except Exception as e:
        return False, str(e)


class CodeFile:
    """代码文件封装"""

    def __init__(self, file_path: Path, base_dir: Path):
        self.file_path = file_path
        self.base_dir = base_dir
        self.content = ""
        self.imports: List[str] = []
        self.layer: Optional[str] = None
        self.component_name: Optional[str] = None
        self.lines_count = 0

    def load(self) -> None:
        """加载文件内容"""
        self.content = read_file(self.file_path)
        self.imports = extract_imports(self.content)
        self.layer = determine_layer(self.file_path, self.base_dir)
        self.component_name = find_component_name(self.content)
        self.lines_count = count_lines(self.content)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "file_path": str(self.file_path.relative_to(self.base_dir)),
            "layer": self.layer,
            "component_name": self.component_name,
            "imports": self.imports,
            "lines_count": self.lines_count,
            "size": len(self.content)
        }


def load_config(config_path: Path) -> Dict[str, Any]:
    """加载配置文件"""
    if not config_path.exists():
        return {}

    with open(config_path, "r", encoding="utf-8") as f:
        import yaml
        return yaml.safe_load(f)
