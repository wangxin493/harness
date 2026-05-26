#!/usr/bin/env python3
"""
Code Scanner - 代码扫描器

职责：
1. 扫描现有代码文件
2. 识别已实现的功能
3. 检测重复代码
4. 生成代码清单和依赖图
"""

import re
import ast
import json
from pathlib import Path
from typing import Dict, List, Any, Optional, Set
from dataclasses import dataclass, asdict


@dataclass
class CodeElement:
    """代码元素基类"""
    name: str
    file_path: str
    line_number: int
    code_snippet: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Component(CodeElement):
    """React 组件"""
    props_interface: Optional[str] = None
    hooks_used: List[str] = None

    def __post_init__(self):
        if self.hooks_used is None:
            self.hooks_used = []


@dataclass
class Hook(CodeElement):
    """自定义 Hook"""
    parameters: List[str] = None
    returns: List[str] = None
    dependencies: List[str] = None

    def __post_init__(self):
        if self.parameters is None:
            self.parameters = []
        if self.returns is None:
            self.returns = []
        if self.dependencies is None:
            self.dependencies = []


@dataclass
class API(CodeElement):
    """API 函数"""
    return_type: Optional[str] = None
    is_async: bool = False


@dataclass
class Service(CodeElement):
    """Service 类"""
    methods: List[str] = None

    def __post_init__(self):
        if self.methods is None:
            self.methods = []


@dataclass
class TypeDefinition(CodeElement):
    """类型定义（interface, type, enum）"""
    kind: str = "interface"  # interface, type, enum
    exports: List[str] = None  # 导出的类型名称
    fields: List[str] = None  # 字段列表

    def __post_init__(self):
        if self.exports is None:
            self.exports = []
        if self.fields is None:
            self.fields = []


class CodeScanner:
    """代码扫描器"""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.source_dirs = ["src", "lib", "app"]
        self.file_extensions = [".tsx", ".ts", ".jsx", ".js"]

    def scan_all(self) -> Dict[str, Any]:
        """扫描所有代码"""
        result = {
            "components": [],
            "hooks": [],
            "apis": [],
            "types": [],  # 新增：类型定义
            "services": [],
            "dependencies": {},
            "summary": {}
        }

        # 扫描代码文件
        code_files = self._find_code_files()
        print(f"  [Scanner] 找到 {len(code_files)} 个代码文件")

        for file_path in code_files:
            self._scan_file(file_path, result)

        # 构建依赖图
        result["dependencies"] = self._build_dependency_graph(result)

        # 生成摘要
        result["summary"] = self._generate_summary(result)

        return result

    def _find_code_files(self) -> List[Path]:
        """查找所有代码文件"""
        code_files = []

        for source_dir in self.source_dirs:
            dir_path = self.base_dir / source_dir
            if not dir_path.exists():
                continue

            for ext in self.file_extensions:
                code_files.extend(dir_path.rglob(f"*{ext}"))

        return code_files

    def _scan_file(self, file_path: Path, result: Dict[str, Any]):
        """扫描单个文件"""
        try:
            content = file_path.read_text(encoding="utf-8")
            relative_path = str(file_path.relative_to(self.base_dir))

            # 根据文件类型扫描
            if file_path.suffix in [".tsx", ".jsx"]:
                self._scan_react_component(content, relative_path, result)
            elif file_path.suffix in [".ts", ".js"]:
                self._scan_typescript_file(content, relative_path, result)

        except Exception as e:
            print(f"  [Scanner] 警告: 无法读取 {file_path}: {e}")

    def _scan_react_component(self, content: str, file_path: str, result: Dict[str, Any]):
        """扫描 React 组件"""
        # 匹配函数组件 - 简化版，避免嵌套捕获组问题
        # 模式1: const Component = () => {...}
        # 模式2: function Component() {...}
        # 模式3: export const Component = () => {...}
        # 模式4: export function Component() {...}
        component_patterns = [
            r'(?:export\s+)?(?:const|function)\s+(\w+)\s*(?:<(\w+Props)>)?\s*\([^)]*\)\s*(?:=>)?\s*\{?',
            r'(?:export\s+)?(?:const|function)\s+(\w+)\s*(?::\s*(?:React\.)?FC(?:<(\w+Props)>)?)?\s*(?:=>)?\s*\{?'
        ]

        for pattern in component_patterns:
            matches = re.finditer(pattern, content, re.MULTILINE)
            for match in matches:
                # 获取组件名
                component_name = match.group(1)
                # 获取 Props 接口（如果有）
                props_interface = match.group(2) if len(match.groups()) >= 2 and match.group(2) else None

                if not component_name:
                    continue

                # 获取起始行号
                start_pos = match.start()
                line_number = content[:start_pos].count('\n') + 1

                # 提取代码片段（前20行）
                lines = content.split('\n')
                snippet = '\n'.join(lines[max(0, line_number-1):line_number+19])

                # 检测使用的 Hooks
                hooks_used = self._extract_hooks(content)

                component = Component(
                    name=component_name,
                    file_path=file_path,
                    line_number=line_number,
                    code_snippet=snippet,
                    props_interface=props_interface,
                    hooks_used=hooks_used
                )

                result["components"].append(component.to_dict())
                break  # 找到一个匹配就够了

        # 扫描自定义 Hooks
        self._scan_custom_hooks(content, file_path, result)

    def _scan_typescript_file(self, content: str, file_path: str, result: Dict[str, Any]):
        """扫描 TypeScript 文件"""
        # 扫描类型定义（interface, type, enum）
        type_pattern = r'(?:export\s+)?(interface|type|enum)\s+(\w+)(?:\s*<[^>]+>)?\s*\{?'
        type_matches = re.finditer(type_pattern, content, re.MULTILINE)

        for match in type_matches:
            type_kind = match.group(1)  # interface, type, enum
            type_name = match.group(2)

            # 获取起始行号
            start_pos = match.start()
            line_number = content[:start_pos].count('\n') + 1

            # 提取代码片段（前15行）
            lines = content.split('\n')
            snippet = '\n'.join(lines[max(0, line_number-1):line_number+14])

            # 解析字段列表
            fields = self._extract_type_fields(content, match.end())

            type_def = TypeDefinition(
                name=type_name,
                file_path=file_path,
                line_number=line_number,
                code_snippet=snippet,
                kind=type_kind,
                fields=fields
            )

            result["types"].append(type_def.to_dict())

        # 扫描 API 函数 - 只扫描被导出的函数
        # 模式1: export function name() {}
        # 模式2: export async function name() {}
        # 模式3: export const name = () => {}
        # 模式4: export const name = async () => {}
        # 模式5: export const name = function() {}

        api_patterns = [
            # function 关键字形式（必须有 export）
            r'export\s+(?:async\s+)?function\s+(\w+)\s*\([^)]*\)\s*(?::\s*Promise<([^>]+)>)?\s*\{',
            # 箭头函数形式（必须有 export）
            r'export\s+(?:const|let)\s+(\w+)\s*(?::\s*[^=]+)?\s*=\s*(?:async\s+)?(?:\([^)]*\)|(?:[\w_]+))\s*(?::\s*Promise<([^>]+)>)?\s*=>',
        ]

        for pattern in api_patterns:
            matches = re.finditer(pattern, content, re.MULTILINE)

            for match in matches:
                api_name = match.group(1)
                return_type = match.group(2)

                # 检查是否是 async
                is_async = "async" in content[max(0, match.start()-30):match.end()]

                # 获取起始行号
                start_pos = match.start()
                line_number = content[:start_pos].count('\n') + 1

                # 提取代码片段（前10行）
                lines = content.split('\n')
                snippet = '\n'.join(lines[line_number-1:line_number+9])

                api = API(
                    name=api_name,
                    file_path=file_path,
                    line_number=line_number,
                    code_snippet=snippet,
                    return_type=return_type,
                    is_async=is_async
                )

                result["apis"].append(api.to_dict())

    def _extract_type_fields(self, content: str, start_pos: int) -> List[str]:
        """从类型定义中提取字段列表"""
        fields = []

        # 找到匹配的闭合括号
        brace_count = 0
        pos = start_pos
        field_start = start_pos

        while pos < len(content):
            char = content[pos]
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    break
            elif char == '\n' or char == ';':
                # 提取字段行
                field_line = content[field_start:pos].strip()
                if field_line and ':' in field_line and not field_line.startswith('//'):
                    # 提取字段名
                    field_name = field_line.split(':')[0].strip()
                    # 移除可选标记
                    field_name = field_name.rstrip('?').strip()
                    if field_name and not field_name.startswith('//'):
                        fields.append(field_name)
                field_start = pos + 1
            pos += 1

        return fields

    def _scan_custom_hooks(self, content: str, file_path: str, result: Dict[str, Any]):
        """扫描自定义 Hooks"""
        # 改进的正则表达式，更灵活地匹配 Hook 声明
        hook_pattern = r'(?:export\s+)?(?:const|function)\s+(use\w+)\s*(?:<[^>]+>)?\s*\(([^)]*)\)(?:\s*:\s*([^{]+))?\s*(?:=>)?\s*\{?'

        matches = re.finditer(hook_pattern, content, re.MULTILINE)

        for match in matches:
            hook_name = match.group(1)

            # 只匹配以 'use' 开头的函数
            if not hook_name.startswith('use'):
                continue

            # 获取起始行号
            start_pos = match.start()
            line_number = content[:start_pos].count('\n') + 1

            # 提取代码片段（前20行）
            lines = content.split('\n')
            snippet = '\n'.join(lines[line_number-1:line_number+19])

            # 解析参数
            params_str = match.group(2) or ""
            parameters = [p.strip() for p in params_str.split(',') if p.strip()]

            # 解析返回类型
            return_type = match.group(3) or ""

            # 提取依赖
            dependencies = self._extract_dependencies(content, hook_name)

            hook = Hook(
                name=hook_name,
                file_path=file_path,
                line_number=line_number,
                code_snippet=snippet,
                parameters=parameters,
                returns=[return_type] if return_type else [],
                dependencies=dependencies
            )

            result["hooks"].append(hook.to_dict())

    def _extract_hooks(self, content: str) -> List[str]:
        """提取使用的 React Hooks"""
        hook_patterns = [
            r'useState',
            r'useEffect',
            r'useContext',
            r'useMemo',
            r'useCallback',
            r'useRef',
            r'useReducer',
            r'useLayoutEffect'
        ]

        hooks_used = []
        for pattern in hook_patterns:
            if re.search(pattern, content):
                hooks_used.append(pattern)

        return hooks_used

    def _extract_dependencies(self, content: str, hook_name: str) -> List[str]:
        """提取 Hook 的依赖"""
        # 简化版：通过 import 语句分析
        import_pattern = r'import\s+.*\s+from\s+["\']([^"\']+)["\']'

        dependencies = []
        for match in re.finditer(import_pattern, content):
            dep_path = match.group(1)
            # 只记录相对路径的依赖
            if dep_path.startswith('.') or dep_path.startswith('@/'):
                dependencies.append(dep_path)

        return dependencies

    def _build_dependency_graph(self, result: Dict[str, Any]) -> Dict[str, List[str]]:
        """构建依赖图"""
        dependency_graph = {}

        # 收集所有元素
        all_elements = {
            "components": result["components"],
            "hooks": result["hooks"],
            "apis": result["apis"]
        }

        for category, elements in all_elements.items():
            for element in elements:
                name = element["name"]
                file_path = element["file_path"]

                if name not in dependency_graph:
                    dependency_graph[name] = []

                # 分析代码片段中的引用
                content = element.get("code_snippet", "")

                # 查找其他元素的引用
                for other_category, other_elements in all_elements.items():
                    for other_element in other_elements:
                        other_name = other_element["name"]
                        if other_name != name and other_name in content:
                            dependency_graph[name].append(other_name)

        return dependency_graph

    def _generate_summary(self, result: Dict[str, Any]) -> Dict[str, int]:
        """生成摘要"""
        return {
            "total_components": len(result["components"]),
            "total_hooks": len(result["hooks"]),
            "total_apis": len(result["apis"]),
            "total_types": len(result["types"]),
            "total_services": len(result["services"]),
            "total_elements": sum([
                len(result["components"]),
                len(result["hooks"]),
                len(result["apis"]),
                len(result["types"]),
                len(result["services"])
            ])
        }

    def find_duplicates(self, new_code: str, existing_code: Dict[str, Any]) -> List[Dict[str, Any]]:
        """检测重复功能"""
        duplicates = []

        # 提取新代码中的函数名、组件名
        new_elements = self._extract_element_names(new_code)

        # 对比现有代码
        for category, elements in existing_code.items():
            if category == "summary" or category == "dependencies":
                continue

            for element in elements:
                existing_name = element["name"]
                if existing_name in new_elements:
                    duplicates.append({
                        "name": existing_name,
                        "category": category,
                        "existing_file": element["file_path"],
                        "existing_line": element["line_number"]
                    })

        return duplicates

    def _extract_element_names(self, code: str) -> Set[str]:
        """从代码中提取元素名"""
        names = set()

        # 提取组件名
        component_pattern = r'(?:export\s+)?(?:const|function)\s+(\w+)\s*(?:<\w+Props>)?\s*\([^)]*\)\s*:\s*React\.FC'
        names.update(re.findall(component_pattern, code))

        # 提取 Hook 名
        hook_pattern = r'(?:export\s+)?(?:const|function)\s+(use\w+)\s*\('
        names.update(re.findall(hook_pattern, code))

        # 提取函数名
        function_pattern = r'(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\('
        names.update(re.findall(function_pattern, code))

        return names

    def find_similar_functionality(self, description: str) -> List[Dict[str, Any]]:
        """查找类似功能（改进版）"""
        # 实际应用中可以使用 NLP 相似度计算
        # 这里改进为更精确的关键字匹配
        # 注：以下是通用功能关键词，适用于大多数 CRUD 应用

        # 更精确的关键词和模式映射
        keywords = {
            "置顶/Pin": {
                "patterns": ["togglePin", "setPinned", "pinItem", "unpinItem", "isPinned", "pinnedItems"],
                "context": ["置顶", "pin", "pinned"]
            },
            "归档/Archive": {
                "patterns": ["archiveItem", "unarchiveItem", "isArchived", "archivedItems"],
                "context": ["归档", "archive", "archived"]
            },
            "删除/Delete": {
                "patterns": ["deleteItem", "removeItem", "deleteItem"],
                "context": ["删除", "delete", "remove"]
            },
            "编辑/更新": {
                "patterns": ["updateItem", "editItem", "modifyItem", "saveItem"],
                "context": ["编辑", "更新", "edit", "update", "save", "修改"]
            },
            "查询": {
                "patterns": ["getItem", "fetchItem", "findItem", "queryItems", "listItems"],
                "context": ["查询", "获取", "fetch", "get", "query"]
            }
        }

        similar = []

        for keyword, config in keywords.items():
            patterns = config["patterns"]
            context_words = config["context"]

            # 检查描述中是否包含上下文词
            has_context = any(word in description.lower() for word in context_words)
            if not has_context:
                continue

            # 扫描代码中是否有相关函数
            code_files = self._find_code_files()
            for file_path in code_files:
                try:
                    content = file_path.read_text(encoding="utf-8")

                    # 只在函数定义中查找，避免误报
                    for pattern in patterns:
                        # 使用更精确的匹配：函数定义行
                        lines = content.split('\n')
                        for line_num, line in enumerate(lines):
                            if re.search(rf'\b{pattern}\s*\(', line, re.IGNORECASE):
                                # 检查是否有注释（避免匹配注释中的函数名）
                                stripped_line = line.split('//')[0].split('/*')[0]
                                if re.search(rf'\b{pattern}\s*\(', stripped_line, re.IGNORECASE):
                                    similar.append({
                                        "keyword": keyword,
                                        "file": str(file_path.relative_to(self.base_dir)),
                                        "pattern": pattern,
                                        "line": line_num + 1
                                    })
                                    break  # 每个模式只匹配一次
                except Exception:
                    pass

        return similar


if __name__ == "__main__":
    # 测试代码扫描器
    base_dir = Path(__file__).parent.parent.parent
    scanner = CodeScanner(base_dir)
    result = scanner.scan_all()

    print("\n=== 代码扫描结果 ===")
    print(f"组件: {result['summary']['total_components']}")
    print(f"Hooks: {result['summary']['total_hooks']}")
    print(f"APIs: {result['summary']['total_apis']}")
    print(f"总计: {result['summary']['total_elements']}")

    print("\n=== 组件列表 ===")
    for comp in result["components"][:5]:
        print(f"  - {comp['name']} ({comp['file_path']})")

    print("\n=== Hooks 列表 ===")
    for hook in result["hooks"][:5]:
        print(f"  - {hook['name']} ({hook['file_path']})")

    print("\n=== 依赖图 ===")
    for name, deps in list(result["dependencies"].items())[:5]:
        if deps:
            print(f"  {name} -> {', '.join(deps)}")
