# Harness 2.0 技术设计规范

## 概述

**目标**：将 harness 框架下沉到每个项目，让任何 Agent（Ducc/Claude Code 等）在编码时都受项目规则的硬约束，产出符合项目规范的代码。

**核心原则**：
- Agent 只负责生成代码
- Harness 负责验证、拦截、积累经验
- 通过 Hook 机制实现强制约束

---

## 一、目录结构

```
项目根目录/
├── .claude/
│   ├── settings.json              # Hook 配置（硬约束入口）
│   └── CLAUDE.md                  # 项目规范（软约束，Agent 会读取）
│
├── .harness/                      # 项目级约束框架
│   ├── rules.yaml                 # 项目规则配置
│   ├── schemas/                   # 接口契约定义
│   │   └── components.json
│   ├── lib/                       # 核心库（Python）
│   │   ├── scanner.py             # 代码扫描器
│   │   ├── validator.py           # Schema 验证器
│   │   ├── dependency_checker.py  # 依赖关系检查
│   │   ├── context.py             # 上下文生成器
│   │   └── memory.py              # 经验管理
│   ├── hooks/                     # Hook 脚本
│   │   ├── validate-code.sh       # PostToolUse 验证
│   │   ├── check-dependencies.sh  # 依赖检查
│   │   └── pre-commit.sh          # Git 提交前验证
│   ├── prompts/                   # 提示词片段（注入 CLAUDE.md）
│   │   ├── architecture.md        # 三层架构规则
│   │   ├── naming.md              # 命名规范
│   │   └── import-rules.md        # 导入规则
│   ├── memory/                    # 经验积累
│   │   └── lessons/
│   ├── context/                   # 扫描结果缓存
│   │   └── project-context.json
│   └── commands/                  # CLI 命令
│       ├── harness-init           # 初始化
│       ├── harness-scan           # 扫描项目
│       ├── harness-validate       # 验证文件
│       └── harness-lesson         # 经验管理
```

---

## 二、核心模块设计

### 2.1 Hook 配置（.claude/settings.json）

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PROJECT_DIR}/.harness/hooks/validate-code.sh",
            "timeout": 30
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PROJECT_DIR}/.harness/hooks/check-dangerous.sh",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

### 2.2 验证脚本（hooks/validate-code.sh）

```bash
#!/bin/bash
# 接收 stdin 的 JSON 输入

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.file_path')
CODE=$(echo "$INPUT" | jq -r '.tool_input.new_string // .tool_input.content')

# 只验证 src/ 目录下的文件
if [[ ! "$FILE_PATH" =~ ^src/ ]]; then
  exit 0
fi

# 调用 Python 验证器
RESULT=$(python3 "${CLAUDE_PROJECT_DIR}/.harness/lib/validator.py" \
  --file "$FILE_PATH" \
  --code "$CODE" \
  --project-dir "${CLAUDE_PROJECT_DIR}" \
  2>&1)

EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
  # 阻止写入，返回错误给 Agent
  jq -n --arg msg "$RESULT" '{
    "hookSpecificOutput": {
      "hookEventName": "PostToolUse",
      "decision": "block",
      "reason": $msg
    }
  }'
  exit 2
fi

exit 0
```

### 2.3 验证器核心（lib/validator.py）

```python
#!/usr/bin/env python3
"""
Harness 验证器 - 核心验证逻辑
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Any, Tuple


class HarnessValidator:
    """项目级代码验证器"""
    
    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        self.harness_dir = project_dir / ".harness"
        self.rules = self._load_rules()
        self.schemas = self._load_schemas()
        self.context = self._load_context()
    
    def _load_rules(self) -> Dict[str, Any]:
        """加载项目规则"""
        rules_file = self.harness_dir / "rules.yaml"
        if not rules_file.exists():
            return self._default_rules()
        # 解析 YAML...
        return {}
    
    def _load_schemas(self) -> Dict[str, Any]:
        """加载组件 Schema"""
        schemas_dir = self.harness_dir / "schemas"
        schemas = {}
        for schema_file in schemas_dir.glob("*.json"):
            schemas.update(json.loads(schema_file.read_text()))
        return schemas
    
    def _load_context(self) -> Dict[str, Any]:
        """加载项目上下文（扫描结果缓存）"""
        context_file = self.harness_dir / "context" / "project-context.json"
        if context_file.exists():
            return json.loads(context_file.read_text())
        return {}
    
    def validate(self, file_path: str, code: str) -> Tuple[bool, List[str]]:
        """
        验证代码是否符合项目规范
        
        Returns:
            (is_valid, errors)
        """
        errors = []
        
        # 1. 三层架构验证
        arch_errors = self._validate_architecture(file_path, code)
        errors.extend(arch_errors)
        
        # 2. 导入验证
        import_errors = self._validate_imports(file_path, code)
        errors.extend(import_errors)
        
        # 3. Schema 验证
        schema_errors = self._validate_schema(file_path, code)
        errors.extend(schema_errors)
        
        # 4. 类型字段验证
        type_errors = self._validate_type_fields(file_path, code)
        errors.extend(type_errors)
        
        # 5. 禁止项验证
        forbidden_errors = self._validate_forbidden_patterns(code)
        errors.extend(forbidden_errors)
        
        return len(errors) == 0, errors
    
    def _validate_architecture(self, file_path: str, code: str) -> List[str]:
        """验证三层架构规则"""
        errors = []
        
        # 判断文件所属层级
        layer = self._get_layer(file_path)
        
        if layer == "service":
            # Service 不能导入 Component 和 Hook
            if re.search(r'from\s+["\']@/(pages|components|hooks)', code):
                errors.append("❌ Service 层不能导入 Component 或 Hook")
        
        elif layer == "hook":
            # Hook 不能导入 Component
            if re.search(r'from\s+["\']@/(pages|components)', code):
                errors.append("❌ Hook 层不能导入 Component")
        
        return errors
    
    def _validate_imports(self, file_path: str, code: str) -> List[str]:
        """验证导入是否有效"""
        errors = []
        
        # 提取所有导入
        imports = re.findall(r'from\s+["\'](@?[/\w]+)["\']', code)
        
        for imp in imports:
            # 检查是否在可用导入列表中
            if imp.startswith("@/"):
                module_path = imp[2:]  # 移除 @/
                
                # 检查是否存在
                possible_paths = [
                    self.project_dir / "src" / f"{module_path}.ts",
                    self.project_dir / "src" / f"{module_path}.tsx",
                    self.project_dir / "src" / module_path / "index.ts",
                ]
                
                if not any(p.exists() for p in possible_paths):
                    # 检查上下文中是否有这个模块
                    if not self._module_exists_in_context(module_path):
                        errors.append(f"❌ 导入不存在的模块: {imp}")
        
        return errors
    
    def _validate_schema(self, file_path: str, code: str) -> List[str]:
        """验证是否符合组件 Schema"""
        errors = []
        
        # 根据文件路径匹配 Schema
        for schema_name, schema in self.schemas.items():
            if schema.get("file") == file_path:
                # 检查必需的 Props
                required_props = schema.get("required_props", [])
                for prop in required_props:
                    prop_name = prop["name"]
                    if prop_name not in code:
                        errors.append(f"⚠️ 缺少必需 Prop: {prop_name}")
        
        return errors
    
    def _validate_type_fields(self, file_path: str, code: str) -> List[str]:
        """验证类型字段是否存在于 @/types 定义中"""
        errors = []
        
        # 获取类型定义中的所有字段
        available_fields = set()
        for type_def in self.context.get("types", []):
            available_fields.update(type_def.get("fields", []))
        
        # 提取代码中使用的字段（简化版，实际需要更精确的解析）
        # 这里只做基础检查，详细检查由 TypeScript 编译器完成
        
        return errors
    
    def _validate_forbidden_patterns(self, code: str) -> List[str]:
        """验证禁止的代码模式"""
        errors = []
        
        forbidden = [
            (r'\bany\b', "禁止使用 any 类型"),
            (r'from\s+["\']@/services/', "@/services 路径不存在，使用 @/api"),
            (r'from\s+["\']@/api/mockApi', "禁止直接导入 mockApi 内部实现"),
            (r'\.overlay\s*=', "antd Dropdown 使用 menu 而非 overlay"),
        ]
        
        for pattern, message in forbidden:
            if re.search(pattern, code):
                errors.append(f"❌ {message}")
        
        return errors
    
    def _get_layer(self, file_path: str) -> str:
        """判断文件所属层级"""
        if "/api/" in file_path:
            return "service"
        elif "/hooks/" in file_path:
            return "hook"
        elif "/pages/" in file_path or "/components/" in file_path:
            return "component"
        return "unknown"
    
    def _module_exists_in_context(self, module_path: str) -> bool:
        """检查模块是否在上下文中存在"""
        # 检查 components, hooks, apis
        for category in ["components", "hooks", "apis"]:
            for item in self.context.get(category, []):
                if module_path in item.get("file_path", ""):
                    return True
        return False
    
    def _default_rules(self) -> Dict[str, Any]:
        """默认规则"""
        return {
            "architecture": {
                "layers": ["component", "hook", "service"],
                "rules": {
                    "service": {"forbidden_imports": ["component", "hook"]},
                    "hook": {"forbidden_imports": ["component"]}
                }
            },
            "naming": {
                "component": "PascalCase",
                "hook": "camelCase with 'use' prefix",
                "service": "camelCase with 'Service' suffix"
            }
        }


def main():
    parser = argparse.ArgumentParser(description="Harness 代码验证器")
    parser.add_argument("--file", required=True, help="文件路径")
    parser.add_argument("--code", required=True, help="代码内容")
    parser.add_argument("--project-dir", required=True, help="项目根目录")
    
    args = parser.parse_args()
    
    validator = HarnessValidator(Path(args.project_dir))
    is_valid, errors = validator.validate(args.file, args.code)
    
    if not is_valid:
        print("\n".join(errors))
        sys.exit(1)
    
    print("✅ 验证通过")
    sys.exit(0)


if __name__ == "__main__":
    main()
```

### 2.4 代码扫描器（lib/scanner.py）

```python
#!/usr/bin/env python3
"""
代码扫描器 - 扫描项目代码，生成上下文
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Any, Set
from dataclasses import dataclass, asdict


@dataclass
class CodeElement:
    """代码元素"""
    name: str
    file_path: str
    line_number: int
    kind: str  # component, hook, api, type


class CodeScanner:
    """代码扫描器"""
    
    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        self.src_dir = project_dir / "src"
    
    def scan(self) -> Dict[str, Any]:
        """扫描项目代码"""
        result = {
            "components": [],
            "hooks": [],
            "apis": [],
            "types": [],
            "imports": {},  # 可用的导入模块
            "summary": {}
        }
        
        if not self.src_dir.exists():
            return result
        
        # 扫描所有 .ts/.tsx 文件
        for file_path in self.src_dir.rglob("*.ts*"):
            self._scan_file(file_path, result)
        
        # 构建导入索引
        result["imports"] = self._build_import_index(result)
        
        # 生成摘要
        result["summary"] = {
            "total_components": len(result["components"]),
            "total_hooks": len(result["hooks"]),
            "total_apis": len(result["apis"]),
            "total_types": len(result["types"])
        }
        
        return result
    
    def _scan_file(self, file_path: Path, result: Dict[str, Any]):
        """扫描单个文件"""
        try:
            content = file_path.read_text(encoding="utf-8")
            relative_path = str(file_path.relative_to(self.project_dir))
            
            # 扫描组件
            self._scan_components(content, relative_path, result)
            
            # 扫描 Hooks
            self._scan_hooks(content, relative_path, result)
            
            # 扫描 API
            self._scan_apis(content, relative_path, result)
            
            # 扫描类型
            self._scan_types(content, relative_path, result)
            
        except Exception as e:
            print(f"警告: 无法扫描 {file_path}: {e}", file=sys.stderr)
    
    def _scan_components(self, content: str, file_path: str, result: Dict[str, Any]):
        """扫描 React 组件"""
        # 匹配函数组件
        pattern = r'(?:export\s+)?(?:const|function)\s+(\w+)\s*(?:<\w+Props>)?\s*[\(=]'
        for match in re.finditer(pattern, content):
            name = match.group(1)
            if name[0].isupper() and not name.startswith("use"):
                line_number = content[:match.start()].count('\n') + 1
                result["components"].append({
                    "name": name,
                    "file_path": file_path,
                    "line_number": line_number
                })
    
    def _scan_hooks(self, content: str, file_path: str, result: Dict[str, Any]):
        """扫描自定义 Hooks"""
        pattern = r'(?:export\s+)?(?:const|function)\s+(use\w+)\s*[\(=]'
        for match in re.finditer(pattern, content):
            name = match.group(1)
            line_number = content[:match.start()].count('\n') + 1
            result["hooks"].append({
                "name": name,
                "file_path": file_path,
                "line_number": line_number
            })
    
    def _scan_apis(self, content: str, file_path: str, result: Dict[str, Any]):
        """扫描 API 服务"""
        # 匹配导出的 Service 对象
        pattern = r'export\s+(?:const|let)\s+(\w+Service)\s*='
        for match in re.finditer(pattern, content):
            name = match.group(1)
            line_number = content[:match.start()].count('\n') + 1
            result["apis"].append({
                "name": name,
                "file_path": file_path,
                "line_number": line_number
            })
    
    def _scan_types(self, content: str, file_path: str, result: Dict[str, Any]):
        """扫描类型定义"""
        pattern = r'(?:export\s+)?(interface|type|enum)\s+(\w+)'
        for match in re.finditer(pattern, content):
            kind = match.group(1)
            name = match.group(2)
            line_number = content[:match.start()].count('\n') + 1
            
            # 提取字段（简化版）
            fields = self._extract_fields(content, match.end())
            
            result["types"].append({
                "name": name,
                "file_path": file_path,
                "line_number": line_number,
                "kind": kind,
                "fields": fields
            })
    
    def _extract_fields(self, content: str, start_pos: int) -> List[str]:
        """提取类型字段"""
        fields = []
        brace_count = 0
        pos = start_pos
        
        while pos < len(content) and brace_count >= 0:
            char = content[pos]
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
            elif char == '\n' and brace_count == 1:
                # 提取字段行
                line_end = content.find('\n', pos + 1)
                line = content[pos:line_end].strip()
                if ':' in line and not line.startswith('//'):
                    field_name = line.split(':')[0].strip().rstrip('?')
                    if field_name:
                        fields.append(field_name)
            pos += 1
        
        return fields
    
    def _build_import_index(self, result: Dict[str, Any]) -> Dict[str, List[str]]:
        """构建可用的导入索引"""
        imports = {
            "types": [],      # @/types 可用的类型
            "hooks": [],      # @/hooks 可用的 Hook
            "apis": [],       # @/api 可用的 Service
            "components": []  # 可用的组件
        }
        
        for t in result["types"]:
            if "types/index.ts" in t["file_path"]:
                imports["types"].append(t["name"])
        
        for h in result["hooks"]:
            imports["hooks"].append(h["name"])
        
        for a in result["apis"]:
            imports["apis"].append(a["name"])
        
        for c in result["components"]:
            imports["components"].append(c["name"])
        
        return imports


def main():
    import sys
    
    if len(sys.argv) < 2:
        print("用法: scanner.py <project_dir>")
        sys.exit(1)
    
    project_dir = Path(sys.argv[1])
    scanner = CodeScanner(project_dir)
    result = scanner.scan()
    
    # 保存到 .harness/context/
    output_dir = project_dir / ".harness" / "context"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "project-context.json"
    
    output_file.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"✅ 上下文已生成: {output_file}")


if __name__ == "__main__":
    main()
```

### 2.5 经验管理（lib/memory.py）

```python
#!/usr/bin/env python3
"""
经验管理 - 记录和查询项目经验
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional


class MemoryStore:
    """经验存储"""
    
    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        self.memory_dir = project_dir / ".harness" / "memory" / "lessons"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
    
    def add_lesson(
        self,
        title: str,
        content: str,
        category: str = "general",
        severity: str = "warning"
    ) -> str:
        """
        添加经验教训
        
        Args:
            title: 标题
            content: 内容
            category: 分类（general, architecture, types, import）
            severity: 严重程度（error, warning, info）
        
        Returns:
            保存的文件名
        """
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"lesson-{timestamp}.md"
        file_path = self.memory_dir / filename
        
        md_content = f"""# {title}

**时间**: {datetime.now().isoformat()}
**分类**: {category}
**严重程度**: {severity}

---

{content}
"""
        
        file_path.write_text(md_content, encoding="utf-8")
        return filename
    
    def get_lessons(
        self,
        category: Optional[str] = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        获取经验教训列表
        
        Args:
            category: 分类过滤
            limit: 最大数量
        
        Returns:
            经验列表
        """
        lessons = []
        
        for lesson_file in sorted(
            self.memory_dir.glob("lesson-*.md"),
            key=lambda f: f.stat().st_mtime,
            reverse=True
        )[:limit]:
            content = lesson_file.read_text(encoding="utf-8")
            
            # 解析 frontmatter
            lines = content.split('\n')
            title = lines[0].replace('# ', '') if lines else ""
            
            lessons.append({
                "file": lesson_file.name,
                "title": title,
                "content": content
            })
        
        return lessons
    
    def get_lessons_prompt(self, limit: int = 5) -> str:
        """
        获取用于注入到提示词的经验内容
        
        Args:
            limit: 最大数量
        
        Returns:
            格式化的经验文本
        """
        lessons = self.get_lessons(limit=limit)
        
        if not lessons:
            return ""
        
        prompt = "\n## 🔥 历史经验教训（避免重复错误）\n\n"
        
        for i, lesson in enumerate(lessons, 1):
            prompt += f"### 经验 {i}: {lesson['title']}\n"
            prompt += lesson["content"][:500] + "\n\n"
        
        return prompt


def main():
    import sys
    
    if len(sys.argv) < 3:
        print("用法: memory.py <project_dir> <command> [args]")
        print("命令:")
        print("  add <title> <content>  - 添加经验")
        print("  list                   - 列出经验")
        sys.exit(1)
    
    project_dir = Path(sys.argv[1])
    command = sys.argv[2]
    
    store = MemoryStore(project_dir)
    
    if command == "add":
        title = sys.argv[3] if len(sys.argv) > 3 else "无标题"
        content = sys.argv[4] if len(sys.argv) > 4 else ""
        filename = store.add_lesson(title, content)
        print(f"✅ 经验已保存: {filename}")
    
    elif command == "list":
        lessons = store.get_lessons()
        for lesson in lessons:
            print(f"- {lesson['title']} ({lesson['file']})")


if __name__ == "__main__":
    main()
```

### 2.6 CLAUDE.md 模板

```markdown
# 项目规范

> 本项目使用 .harness 框架约束代码规范，Agent 必须遵守以下规则。

## ⛔ 强制规则（违反将导致代码被拒绝）

### 三层架构
- **Component 层** (pages/, components/): UI 渲染、用户交互
- **Hook 层** (hooks/): 业务逻辑、状态管理
- **Service 层** (api/): 数据获取、API 调用

**依赖规则**:
- Component → Hook, Service ✅
- Hook → Service ✅
- Hook → Component ❌
- Service → Hook, Component ❌

### 导入规则

**可用的导入**（以 `.harness/context/project-context.json` 为准）:

@/.harness/context/project-context.json

### 禁止行为
- ❌ 使用 `any` 类型
- ❌ 导入 `@/services/` 路径（不存在）
- ❌ 导入 `@/api/mockApi` 内部实现
- ❌ 使用未在 `@/types/index.ts` 中定义的字段

## 📋 经验教训

@/.harness/memory/lessons/

---

**验证方式**: 每次代码写入都会自动执行 `.harness/hooks/validate-code.sh` 验证，不符合规范的代码将被拒绝。
```

---

## 三、规则配置（rules.yaml）

```yaml
# 项目规则配置

# 三层架构规则
architecture:
  layers:
    - name: component
      paths: ["src/pages/", "src/components/"]
      can_import: ["hook", "service", "types"]
    - name: hook
      paths: ["src/hooks/"]
      can_import: ["service", "types"]
    - name: service
      paths: ["src/api/"]
      can_import: ["types"]
  
  forbidden_imports:
    service: ["component", "hook"]
    hook: ["component"]

# 命名规范
naming:
  component: PascalCase        # XxxList, XxxCard
  hook: camelCase with prefix  # useXxx
  service: camelCase with suffix # xxxService
  type: PascalCase             # Xxx, XxxInput

# 类型约束
types:
  source: "src/types/index.ts"
  forbidden_fields: []  # 禁止使用的字段名
  required_fields: []   # 必须包含的字段

# 导入约束
imports:
  allowed_prefixes:
    - "@/types"
    - "@/hooks"
    - "@/api"
    - "@/pages"
    - "@/components"
  
  forbidden_imports:
    - "@/services"      # 路径不存在
    - "@/api/mockApi"   # 内部实现
    - "@/types/xxx.ts"  # 使用 @/types

# 禁止的代码模式
forbidden_patterns:
  - pattern: "\\bany\\b"
    message: "禁止使用 any 类型"
  
  - pattern: "from\\s+[\"']@/services/"
    message: "@/services 路径不存在，使用 @/api"
  
  - pattern: "\\.overlay\\s*="
    message: "antd Dropdown 使用 menu 而非 overlay"

# 经验积累配置
memory:
  auto_save: true
  max_lessons: 50
  categories:
    - architecture
    - types
    - import
    - api
    - general
```

---

## 四、工作流程

### 4.1 Agent 生成代码流程

```
1. Agent 分析需求
   ↓
2. Agent 读取 CLAUDE.md（项目规范）
   ↓
3. Agent 生成代码
   ↓
4. PostToolUse Hook 触发
   ↓
5. validate-code.sh 执行验证
   ├── 三层架构验证
   ├── 导入验证
   ├── Schema 验证
   ├── 类型字段验证
   └── 禁止模式验证
   ↓
6. 验证结果
   ├── 通过 → 代码写入成功
   └── 失败 → 返回错误给 Agent，要求重试
```

### 4.2 经验积累流程

```
1. Agent 发现错误或接收验证失败反馈
   ↓
2. Agent 调用 harness-lesson add "xxx"
   ↓
3. 经验保存到 .harness/memory/lessons/
   ↓
4. 下次对话时，CLAUDE.md 自动注入经验
   ↓
5. Agent 根据历史经验避免重复错误
```

---

## 五、文件清单与优先级

| 文件 | 作用 | 优先级 | 工作量 |
|------|------|--------|--------|
| `.claude/settings.json` | Hook 配置 | P0 | 小 |
| `.claude/CLAUDE.md` | 项目规范注入 | P0 | 小 |
| `.harness/hooks/validate-code.sh` | 验证脚本入口 | P0 | 小 |
| `.harness/lib/validator.py` | 核心验证器 | P0 | 中 |
| `.harness/lib/scanner.py` | 代码扫描器 | P0 | 中 |
| `.harness/rules.yaml` | 规则配置 | P0 | 小 |
| `.harness/schemas/components.json` | 组件契约 | P1 | 小 |
| `.harness/lib/dependency_checker.py` | 依赖检查 | P1 | 中 |
| `.harness/lib/memory.py` | 经验管理 | P1 | 小 |
| `.harness/prompts/*.md` | 提示词片段 | P2 | 小 |
| `.harness/commands/*` | CLI 命令 | P2 | 小 |

---

## 六、扩展能力（可选）

### 6.1 MCP Tool 扩展

为 Agent 提供 MCP 工具，主动查询项目规则：

```python
# .harness/lib/mcp_tools.py

@mcp_tool
def get_available_imports() -> dict:
    """获取项目可用的导入模块列表"""
    
@mcp_tool
def validate_code(file_path: str, code: str) -> dict:
    """验证代码是否符合项目规范"""
    
@mcp_tool
def add_lesson(title: str, content: str) -> str:
    """添加项目经验教训"""
    
@mcp_tool
def get_project_context() -> dict:
    """获取项目代码上下文（组件、Hooks、APIs、类型）"""
```

### 6.2 Git Pre-commit Hook

```bash
# .git/hooks/pre-commit

#!/bin/bash
# 提交前验证所有变更文件

for file in $(git diff --cached --name-only | grep -E '\.(ts|tsx)$'); do
    if [[ "$file" =~ ^src/ ]]; then
        python3 ".harness/lib/validator.py" \
            --file "$file" \
            --code "$(cat "$file")" \
            --project-dir "." || exit 1
    fi
done
```

---

## 七、与现有 harness 的对比

| 特性 | 旧 harness | 新 harness |
|------|-----------|-----------|
| LLM 调用 | 内置 DeepSeek/阿里百炼 | ❌ 删除，由 Agent 负责 |
| 代码审查 | Reviewer Agent | ❌ 删除，由 Agent /code-review 负责 |
| 代码生成 | Coder Agent | ❌ 删除，由 Agent 负责 |
| Hook 机制 | ❌ 无 | ✅ PostToolUse/PreToolUse |
| 验证时机 | 生成后验证 | 写入前拦截 |
| 强制力 | 软约束（提示词） | 硬约束（Hook 拦截） |
| 项目隔离 | 单项目 | 每个项目独立 .harness |
| 经验积累 | ✅ 有 | ✅ 保留并简化 |

---

## 八、实施步骤

1. **Phase 1**: 核心验证能力（P0）
   - 创建 `.harness/` 目录结构
   - 实现 `validator.py` 核心验证器
   - 实现 `scanner.py` 代码扫描器
   - 配置 PostToolUse Hook

2. **Phase 2**: 规则与 Schema（P1）
   - 编写 `rules.yaml` 规则配置
   - 迁移 `components_schema.json`
   - 实现依赖检查器

3. **Phase 3**: 经验与提示词（P1-P2）
   - 实现经验管理模块
   - 编写 CLAUDE.md 模板
   - 编写提示词片段

4. **Phase 4**: 扩展与优化（P2）
   - CLI 命令封装
   - MCP Tool 扩展
   - Git hooks 集成

---

## 九、风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| Hook 超时 | 验证失败，代码被拒绝 | 设置合理 timeout（30s），优化扫描性能 |
| 规则过严 | Agent 无法生成有效代码 | 提供详细的错误信息，引导 Agent 修正 |
| 扫描性能 | 大型项目扫描慢 | 使用增量扫描 + 缓存机制 |
| 规则冲突 | 不同项目规则不同 | 每个项目独立的 .harness，互不干扰 |

---

**文档版本**: 1.0
**最后更新**: 2026-06-17
