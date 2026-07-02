# Harness 2.0 最终设计方案

> **[历史文档 / ARCHIVED]**  
> 本文件是 Harness 2.0 的早期架构设计草稿，包含 Phase 1-3 的整体方向和部分接口提案。  
> **当前实际命令清单、CLI 接口和配置格式以 `lib/cli.py`、`harness --help` 和  
> `.harness/docs/current-capabilities-and-onboarding.md` 为准，本文中的草稿内容  
> （如 §五 CLI 草稿、§六 validator API、AutoFixer 全能力描述等）可能与实现不符。**

---

## 一、核心决策

| 决策点 | 结论 |
|--------|------|
| 兼容性 | 直接切 2.0，归档旧版 harness/ |
| Agent 支持 | DUCC + Comate + Claude Code，通过适配层支持 |
| 验证器实现 | tree-sitter + TypeScript Compiler API |
| 缓存策略 | 依赖图增量失效（方案二） |
| 文档策略 | CLAUDE.md 做索引，规则集中在 .harness/ |

---

## 二、Phase 1 核心能力

| 能力 | 说明 | 优先级 |
|------|------|--------|
| **自动修复引擎** | 每条规则配套 fixer，输出补丁或指令 | P0 |
| **经验市场** | Git 共享目录 + 推送机制，团队协同 | P0 |
| **智能缓存** | 依赖图增量失效，精准缓存失效 | P0 |
| **分级治理** | strict / relaxed / off 三档切换 | P0 |

---

## 三、目录结构

```
项目根目录/
├── .claude/                      # Claude Code 适配
│   ├── settings.json             # Hook 配置
│   └── CLAUDE.md                 # 规则索引
│
├── .comate/                      # Comate 适配
│   └── .comaterc                 # Comate 配置
│
├── .ducc/                        # Ducc 适配
│   └── .duccrc                   # Ducc 配置
│
├── .harness/                     # 核心框架（与 Agent 无关）
│   ├── rules.yaml                # 规则配置
│   ├── schemas/                  # 接口契约定义
│   │   └── components.json
│   │
│   ├── lib/                      # 核心库（Python）
│   │   ├── scanner.py            # 增量扫描器
│   │   ├── validator.py          # 验证器（tree-sitter + tsc）
│   │   ├── fixer.py              # 自动修复引擎 ⭐
│   │   ├── cache.py              # 智能缓存（依赖图）
│   │   ├── dependency_graph.py   # 文件依赖图
│   │   ├── git_tracker.py        # Git 变更追踪
│   │   ├── memory.py             # 经验管理
│   │   ├── experience_market.py  # 经验市场 ⭐
│   │   ├── mode_manager.py       # 分级治理 ⭐
│   │   └── adapter.py            # Agent 适配器
│   │
│   ├── fixers/                   # 修复脚本库 ⭐
│   │   ├── architecture.ts       # 架构违规修复
│   │   ├── naming.ts             # 命名规范修复
│   │   └── import.ts             # 导入路径修复
│   │
│   ├── hooks/                    # Hook 脚本
│   │   └── validate-code.sh      # PostToolUse 验证
│   │
│   ├── prompts/                  # 提示词片段
│   │   ├── architecture.md
│   │   ├── naming.md
│   │   └── import-rules.md
│   │
│   ├── memory/                   # 经验积累
│   │   ├── lessons/              # 本地经验
│   │   └── shared/               # 团队共享经验 ⭐
│   │
│   ├── context/                  # 扫描结果缓存
│   │   ├── project-context.json
│   │   ├── scan-metadata.json
│   │   ├── dependency-graph.json # 依赖关系图
│   │   └── validation-cache/     # 验证结果缓存
│   │
│   ├── generated/                # 生成的适配文件
│   │   ├── claude.md
│   │   ├── comate.md
│   │   └── ducc.md
│   │
│   └── commands/                 # CLI 命令
│       └── harness               # 统一入口
│
├── .harness-shared/              # 团队共享经验仓库 ⭐
│   └── lessons/
│
├── _archived_harness_1.x/        # 归档的旧版本
│
└── src/                          # 源码目录
```

---

## 四、核心模块设计

### 4.1 自动修复引擎（lib/fixer.py）

```python
#!/usr/bin/env python3
"""
自动修复引擎
为每条规则提供 fixer，输出补丁或修复指令
"""

import json
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass


@dataclass
class FixSuggestion:
    """修复建议"""
    rule_id: str
    severity: str
    description: str
    fix_type: str  # "patch" | "instruction" | "refactor"
    patch: Optional[str] = None        # unified diff 格式
    instruction: Optional[str] = None  # 自然语言指令
    target_file: Optional[str] = None
    line_range: Optional[tuple] = None


class AutoFixer:
    """自动修复引擎"""
    
    def __init__(self, harness_dir: Path):
        self.harness_dir = harness_dir
        self.fixers_dir = harness_dir / "fixers"
        self.rules = self._load_rules()
    
    def analyze_issue(self, issue: Dict[str, Any], code: str, file_path: str) -> List[FixSuggestion]:
        """
        分析问题并生成修复建议
        
        Args:
            issue: 验证发现的问题
            code: 文件内容
            file_path: 文件路径
        
        Returns:
            修复建议列表
        """
        suggestions = []
        category = issue.get("category", "")
        
        # 根据问题类别调用对应的 fixer
        if category == "architecture":
            suggestions.extend(self._fix_architecture(issue, code, file_path))
        elif category == "import":
            suggestions.extend(self._fix_import(issue, code, file_path))
        elif category == "naming":
            suggestions.extend(self._fix_naming(issue, code, file_path))
        elif category == "type-safety":
            suggestions.extend(self._fix_type_safety(issue, code, file_path))
        
        return suggestions
    
    def _fix_architecture(self, issue: Dict, code: str, file_path: str) -> List[FixSuggestion]:
        """修复架构层违规"""
        suggestions = []
        message = issue.get("message", "")
        
        # 示例：Service 层导入了 Component
        if "Service 层不能导入" in message:
            # 提取违规导入
            import re
            matches = re.findall(r'from\s+["\'](@/(pages|components)/[^"\']+)["\']', code)
            
            for import_path, _ in matches:
                suggestion = FixSuggestion(
                    rule_id="arch-service-import",
                    severity="error",
                    description=f"Service 层违规导入: {import_path}",
                    fix_type="instruction",
                    instruction=f"""
检测到 Service 层直接导入了 Component ({import_path})。

修复方案：
1. 在 hooks/ 中创建对应的 Hook 封装业务逻辑
2. Service 只负责数据获取，不依赖 UI 层
3. Component 通过 Hook 调用 Service

示例重构：
```typescript
// ❌ 当前（违规）
// src/api/itemService.ts
import {{ ItemList }} from '@/components/ItemList';

// ✅ 修复后
// src/hooks/useItems.ts
import {{ itemService }} from '@/api/itemService';
export function useItems() {{
  const [items, setItems] = useState([]);
  // 业务逻辑...
  return {{ items, fetchItems: itemService.fetchItems }};
}}

// src/components/ItemList.tsx
import {{ useItems }} from '@/hooks/useItems';
```
""",
                    target_file=file_path
                )
                suggestions.append(suggestion)
        
        return suggestions
    
    def _fix_import(self, issue: Dict, code: str, file_path: str) -> List[FixSuggestion]:
        """修复导入路径问题"""
        suggestions = []
        message = issue.get("message", "")
        
        # 示例：@/services 路径不存在
        if "@/services" in message:
            # 生成替换 patch
            old_import = "@/services"
            new_import = "@/api"
            
            suggestion = FixSuggestion(
                rule_id="import-path-invalid",
                severity="error",
                description=f"导入路径不存在: {old_import}",
                fix_type="patch",
                patch=self._generate_replace_patch(code, old_import, new_import),
                instruction=f"将 {old_import} 替换为 {new_import}",
                target_file=file_path
            )
            suggestions.append(suggestion)
        
        return suggestions
    
    def _fix_naming(self, issue: Dict, code: str, file_path: str) -> List[FixSuggestion]:
        """修复命名规范问题"""
        suggestions = []
        # TODO: 实现命名修复逻辑
        return suggestions
    
    def _fix_type_safety(self, issue: Dict, code: str, file_path: str) -> List[FixSuggestion]:
        """修复类型安全问题"""
        suggestions = []
        
        if "禁止使用 any" in issue.get("message", ""):
            suggestion = FixSuggestion(
                rule_id="type-no-any",
                severity="error",
                description="禁止使用 any 类型",
                fix_type="instruction",
                instruction="""
检测到使用了 any 类型。

修复方案：
1. 如果类型未知，使用 unknown 并进行类型守卫
2. 如果是第三方库无类型，创建 .d.ts 声明文件
3. 如果是复杂对象，定义具体 interface

示例：
```typescript
// ❌ 当前
function process(data: any) { ... }

// ✅ 修复
interface ProcessData {
  id: string;
  name: string;
}
function process(data: ProcessData) { ... }

// 或使用 unknown
function process(data: unknown) {
  if (typeof data === 'object' && data !== null) {
    // 安全访问
  }
}
```
""",
                target_file=file_path
            )
            suggestions.append(suggestion)
        
        return suggestions
    
    def _generate_replace_patch(self, code: str, old: str, new: str) -> str:
        """生成替换补丁（unified diff 格式）"""
        import difflib
        
        lines_old = code.split('\n')
        lines_new = code.replace(old, new).split('\n')
        
        diff = difflib.unified_diff(
            lines_old,
            lines_new,
            fromfile='original',
            tofile='fixed',
            lineterm=''
        )
        
        return '\n'.join(diff)
    
    def apply_patch(self, file_path: str, patch: str) -> bool:
        """应用补丁到文件"""
        try:
            result = subprocess.run(
                ["patch", "-p0"],
                input=patch,
                capture_output=True,
                text=True,
                cwd=self.harness_dir.parent
            )
            return result.returncode == 0
        except Exception:
            return False
    
    def _load_rules(self) -> Dict[str, Any]:
        """加载规则配置"""
        import yaml
        rules_file = self.harness_dir / "rules.yaml"
        if rules_file.exists():
            return yaml.safe_load(rules_file.read_text())
        return {}


class FixResult:
    """修复结果"""
    
    def __init__(self, suggestions: List[FixSuggestion], auto_applied: bool = False):
        self.suggestions = suggestions
        self.auto_applied = auto_applied
    
    def to_agent_message(self) -> str:
        """转换为 Agent 可理解的消息"""
        if not self.suggestions:
            return "无需修复"
        
        lines = ["检测到以下问题，已生成修复建议：\n"]
        
        for i, sug in enumerate(self.suggestions, 1):
            lines.append(f"### 问题 {i}: {sug.description}")
            lines.append(f"- 规则: {sug.rule_id}")
            lines.append(f"- 严重程度: {sug.severity}")
            lines.append(f"- 修复方式: {sug.fix_type}")
            
            if sug.instruction:
                lines.append(f"\n**修复方案**:\n{sug.instruction}")
            
            if sug.patch:
                lines.append(f"\n**补丁**:\n```diff\n{sug.patch}\n```")
            
            lines.append("")
        
        return '\n'.join(lines)
```

---

### 4.2 经验市场（lib/experience_market.py）

```python
#!/usr/bin/env python3
"""
经验市场
团队共享经验，从同一个坑只跌倒一次
"""

import json
import subprocess
import shutil
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime
from dataclasses import dataclass


@dataclass
class Lesson:
    """经验教训"""
    id: str
    title: str
    content: str
    category: str
    severity: str
    keywords: List[str]
    author: str
    created_at: str
    expires_at: Optional[str] = None
    applies_to: List[str] = None  # 适用文件路径模式
    
    def matches(self, context: Dict[str, Any]) -> float:
        """计算与当前上下文的匹配度"""
        score = 0.0
        
        # 关键词匹配
        context_text = context.get("task_description", "") + " " + context.get("file_path", "")
        for keyword in self.keywords:
            if keyword.lower() in context_text.lower():
                score += 0.2
        
        # 文件路径匹配
        if self.applies_to:
            file_path = context.get("file_path", "")
            for pattern in self.applies_to:
                if pattern in file_path:
                    score += 0.3
                    break
        
        return min(score, 1.0)


class ExperienceMarket:
    """经验市场"""
    
    def __init__(self, harness_dir: Path):
        self.harness_dir = harness_dir
        self.local_lessons_dir = harness_dir / "memory" / "lessons"
        self.shared_lessons_dir = harness_dir.parent / ".harness-shared" / "lessons"
        self.config = self._load_config()
    
    def _load_config(self) -> Dict[str, Any]:
        """加载配置"""
        config_file = self.harness_dir / "memory" / "market-config.json"
        if config_file.exists():
            return json.loads(config_file.read_text())
        return {
            "enabled": True,
            "auto_pull": True,
            "auto_push": False,
            "max_lessons": 10,
            "min_score": 0.3
        }
    
    def sync_from_shared(self) -> int:
        """从共享仓库同步经验"""
        if not self.config.get("auto_pull", True):
            return 0
        
        if not self.shared_lessons_dir.exists():
            return 0
        
        count = 0
        for lesson_file in self.shared_lessons_dir.glob("*.md"):
            # 检查是否已存在
            local_copy = self.local_lessons_dir / f"shared_{lesson_file.name}"
            if not local_copy.exists():
                shutil.copy(lesson_file, local_copy)
                count += 1
        
        return count
    
    def publish_lesson(self, lesson: Lesson) -> bool:
        """发布经验到共享仓库"""
        if not self.config.get("auto_push", False):
            return False
        
        if not self.shared_lessons_dir.exists():
            self.shared_lessons_dir.mkdir(parents=True, exist_ok=True)
        
        # 生成文件内容
        content = f"""---
id: {lesson.id}
title: {lesson.title}
category: {lesson.category}
severity: {lesson.severity}
keywords: {', '.join(lesson.keywords)}
author: {lesson.author}
created_at: {lesson.created_at}
expires_at: {lesson.expires_at or 'never'}
applies_to: {lesson.applies_to or []}
---

{lesson.content}
"""
        
        # 写入文件
        lesson_file = self.shared_lessons_dir / f"{lesson.id}.md"
        lesson_file.write_text(content)
        
        return True
    
    def get_relevant_lessons(
        self,
        context: Dict[str, Any],
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        获取与当前上下文相关的经验
        
        Args:
            context: 包含 task_description, file_path, keywords 等
            limit: 最大数量
        
        Returns:
            相关经验列表，按匹配度排序
        """
        all_lessons = []
        
        # 收集所有经验（本地 + 共享）
        for lesson_file in self.local_lessons_dir.glob("*.md"):
            lesson = self._parse_lesson_file(lesson_file)
            if lesson:
                all_lessons.append(lesson)
        
        # 计算匹配度并排序
        scored_lessons = []
        for lesson in all_lessons:
            score = lesson.matches(context)
            if score >= self.config.get("min_score", 0.3):
                scored_lessons.append((score, lesson))
        
        scored_lessons.sort(key=lambda x: x[0], reverse=True)
        
        # 返回 top N
        return [
            {
                "score": score,
                "id": lesson.id,
                "title": lesson.title,
                "content": lesson.content[:500],  # 截断
                "category": lesson.category,
                "severity": lesson.severity
            }
            for score, lesson in scored_lessons[:limit]
        ]
    
    def _parse_lesson_file(self, file_path: Path) -> Optional[Lesson]:
        """解析经验文件"""
        try:
            content = file_path.read_text(encoding="utf-8")
            
            # 解析 frontmatter
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    frontmatter = parts[1].strip()
                    body = parts[2].strip()
                    
                    # 解析 YAML frontmatter
                    import yaml
                    meta = yaml.safe_load(frontmatter)
                    
                    return Lesson(
                        id=meta.get("id", file_path.stem),
                        title=meta.get("title", ""),
                        content=body,
                        category=meta.get("category", "general"),
                        severity=meta.get("severity", "warning"),
                        keywords=meta.get("keywords", []),
                        author=meta.get("author", "unknown"),
                        created_at=meta.get("created_at", ""),
                        expires_at=meta.get("expires_at"),
                        applies_to=meta.get("applies_to", [])
                    )
        except Exception as e:
            print(f"解析经验文件失败: {file_path}: {e}")
        
        return None
    
    def create_lesson(
        self,
        title: str,
        content: str,
        category: str = "general",
        severity: str = "warning",
        keywords: List[str] = None,
        applies_to: List[str] = None,
        publish: bool = False
    ) -> Lesson:
        """创建新经验"""
        import uuid
        
        lesson = Lesson(
            id=str(uuid.uuid4())[:8],
            title=title,
            content=content,
            category=category,
            severity=severity,
            keywords=keywords or [],
            author=self._get_author(),
            created_at=datetime.now().isoformat(),
            applies_to=applies_to
        )
        
        # 保存到本地
        self._save_lesson(lesson)
        
        # 可选：发布到共享
        if publish:
            self.publish_lesson(lesson)
        
        return lesson
    
    def _save_lesson(self, lesson: Lesson):
        """保存经验到本地"""
        self.local_lessons_dir.mkdir(parents=True, exist_ok=True)
        
        content = f"""---
id: {lesson.id}
title: {lesson.title}
category: {lesson.category}
severity: {lesson.severity}
keywords: {', '.join(lesson.keywords)}
author: {lesson.author}
created_at: {lesson.created_at}
applies_to: {lesson.applies_to or []}
---

{lesson.content}
"""
        
        lesson_file = self.local_lessons_dir / f"{lesson.id}.md"
        lesson_file.write_text(content)
    
    def _get_author(self) -> str:
        """获取当前作者"""
        try:
            result = subprocess.run(
                ["git", "config", "user.name"],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except:
            pass
        return "unknown"


class ExperienceInjector:
    """经验注入器"""
    
    def __init__(self, market: ExperienceMarket):
        self.market = market
    
    def inject_to_prompt(
        self,
        prompt: str,
        context: Dict[str, Any],
        max_chars: int = 1000
    ) -> str:
        """
        将相关经验注入到提示词中
        
        Args:
            prompt: 原始提示词
            context: 当前上下文
            max_chars: 最大字符数
        
        Returns:
            增强后的提示词
        """
        lessons = self.market.get_relevant_lessons(context)
        
        if not lessons:
            return prompt
        
        # 构建经验注入文本
        injection = "\n\n## 🔥 相关经验教训\n\n"
        injection += "> 以下是从团队经验库中检索的相关教训，请避免重复犯错。\n\n"
        
        current_chars = 0
        for lesson in lessons:
            lesson_text = f"### {lesson['title']}\n"
            lesson_text += f"- 匹配度: {lesson['score']:.0%}\n"
            lesson_text += f"- 类别: {lesson['category']}\n"
            lesson_text += f"\n{lesson['content']}\n\n"
            
            if current_chars + len(lesson_text) > max_chars:
                break
            
            injection += lesson_text
            current_chars += len(lesson_text)
        
        return prompt + injection
```

---

### 4.3 智能缓存（lib/cache.py）

```python
#!/usr/bin/env python3
"""
智能验证缓存
基于依赖图的增量缓存失效
"""

import json
import hashlib
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Set
from collections import defaultdict


class DependencyGraph:
    """文件依赖图"""
    
    def __init__(self, context_file: Path):
        self.context_file = context_file
        self.graph_file = context_file.parent / "dependency-graph.json"
        
        # file -> imports
        self.graph: Dict[str, Set[str]] = defaultdict(set)
        # file -> depended_by (反向依赖)
        self.reverse_graph: Dict[str, Set[str]] = defaultdict(set)
        
        self._load_or_build()
    
    def _load_or_build(self):
        """加载或构建依赖图"""
        if self.graph_file.exists():
            self._load_graph()
        else:
            self._build_graph()
    
    def _load_graph(self):
        """从文件加载依赖图"""
        try:
            data = json.loads(self.graph_file.read_text())
            for file, imports in data.get("graph", {}).items():
                self.graph[file] = set(imports)
            for file, dependents in data.get("reverse_graph", {}).items():
                self.reverse_graph[file] = set(dependents)
        except Exception:
            self._build_graph()
    
    def _build_graph(self):
        """从项目代码构建依赖图"""
        import re
        
        if not self.context_file.exists():
            return
        
        context = json.loads(self.context_file.read_text())
        
        # 从所有代码元素中提取导入关系
        for element in context.get("components", []) + \
                       context.get("hooks", []) + \
                       context.get("apis", []) + \
                       context.get("types", []):
            file_path = element.get("file_path", "")
            
            # 读取文件提取导入
            full_path = self.context_file.parent.parent.parent / file_path
            if full_path.exists():
                try:
                    content = full_path.read_text(encoding="utf-8")
                    imports = re.findall(r'from\s+["\'](@?[/\w]+)["\']', content)
                    
                    for imp in imports:
                        if imp.startswith("@/"):
                            # 转换为文件路径
                            target_file = self._resolve_import(imp)
                            if target_file:
                                self.graph[file_path].add(target_file)
                                self.reverse_graph[target_file].add(file_path)
                except Exception:
                    pass
        
        self._save_graph()
    
    def _resolve_import(self, import_path: str) -> Optional[str]:
        """将 @/xxx 转换为文件路径"""
        rel_path = import_path[2:]  # 移除 @/
        
        # 尝试多种可能的文件路径
        base = self.context_file.parent.parent.parent / "src"
        candidates = [
            base / f"{rel_path}.ts",
            base / f"{rel_path}.tsx",
            base / rel_path / "index.ts",
            base / rel_path / "index.tsx",
        ]
        
        for candidate in candidates:
            if candidate.exists():
                return str(candidate.relative_to(self.context_file.parent.parent.parent))
        
        return None
    
    def _save_graph(self):
        """保存依赖图"""
        self.graph_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "graph": {k: list(v) for k, v in self.graph.items()},
            "reverse_graph": {k: list(v) for k, v in self.reverse_graph.items()},
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        self.graph_file.write_text(json.dumps(data, indent=2))
    
    def get_affected_files(self, changed_file: str) -> Set[str]:
        """
        获取受影响的所有文件（传递性）
        
        当一个文件变化时，所有依赖它的文件都需要重新验证
        """
        affected = set()
        queue = [changed_file]
        visited = set()
        
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            
            # 找到所有依赖当前文件的其他文件
            dependents = self.reverse_graph.get(current, set())
            
            for dependent in dependents:
                affected.add(dependent)
                queue.append(dependent)
        
        return affected
    
    def get_dependencies(self, file_path: str) -> Set[str]:
        """获取文件的所有依赖"""
        return self.graph.get(file_path, set())
    
    def update(self, file_path: str, imports: Set[str]):
        """更新文件的依赖关系"""
        # 移除旧依赖
        old_imports = self.graph.get(file_path, set())
        for old_imp in old_imports:
            if old_imp in self.reverse_graph:
                self.reverse_graph[old_imp].discard(file_path)
        
        # 添加新依赖
        self.graph[file_path] = imports
        for imp in imports:
            self.reverse_graph[imp].add(file_path)
        
        self._save_graph()


class SmartValidationCache:
    """智能验证缓存"""
    
    def __init__(self, cache_dir: Path, dep_graph: DependencyGraph):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.dep_graph = dep_graph
        
        # 文件内容哈希
        self.file_hashes: Dict[str, str] = {}
        self.hash_file = cache_dir / "file-hashes.json"
        self._load_file_hashes()
    
    def _load_file_hashes(self):
        """加载文件哈希"""
        if self.hash_file.exists():
            self.file_hashes = json.loads(self.hash_file.read_text())
    
    def _save_file_hashes(self):
        """保存文件哈希"""
        self.hash_file.write_text(json.dumps(self.file_hashes, indent=2))
    
    def _compute_cache_key(self, code: str, file_path: str) -> str:
        """计算缓存 key：包含依赖的哈希"""
        # 获取直接依赖
        imports = self.dep_graph.get_dependencies(file_path)
        
        # 计算依赖哈希
        dep_hashes = []
        for imp in sorted(imports):
            dep_hash = self.file_hashes.get(imp, "0")
            dep_hashes.append(f"{imp}:{dep_hash}")
        
        # 组合：自身代码 + 依赖哈希
        content = f"{file_path}:{hashlib.md5(code.encode()).hexdigest()[:8]}:" + \
                  ",".join(dep_hashes)
        
        return hashlib.md5(content.encode()).hexdigest()[:12]
    
    def get(self, code: str, file_path: str) -> Optional[Dict[str, Any]]:
        """获取缓存"""
        cache_key = self._compute_cache_key(code, file_path)
        cache_file = self.cache_dir / f"{cache_key}.json"
        
        if not cache_file.exists():
            return None
        
        # 检查 TTL（24 小时）
        age = time.time() - cache_file.stat().st_mtime
        if age > 86400:
            cache_file.unlink()
            return None
        
        try:
            return json.loads(cache_file.read_text())
        except:
            return None
    
    def set(self, code: str, file_path: str, result: Dict[str, Any]):
        """保存缓存"""
        cache_key = self._compute_cache_key(code, file_path)
        cache_file = self.cache_dir / f"{cache_key}.json"
        cache_file.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        
        # 更新文件哈希
        self.file_hashes[file_path] = hashlib.md5(code.encode()).hexdigest()[:8]
        self._save_file_hashes()
    
    def invalidate_file(self, file_path: str):
        """使指定文件的缓存失效"""
        # 不直接删除缓存文件，而是更新哈希让下次重新计算
        if file_path in self.file_hashes:
            del self.file_hashes[file_path]
            self._save_file_hashes()
    
    def invalidate_affected(self, changed_file: str):
        """使受影响的所有文件缓存失效"""
        affected = self.dep_graph.get_affected_files(changed_file)
        
        for file_path in affected:
            self.invalidate_file(file_path)
        
        # 也使变更文件本身失效
        self.invalidate_file(changed_file)
        
        return len(affected) + 1  # 返回受影响文件数
    
    def clear_all(self):
        """清空所有缓存"""
        for cache_file in self.cache_dir.glob("*.json"):
            if cache_file.name != "file-hashes.json":
                cache_file.unlink()
        self.file_hashes.clear()
        self._save_file_hashes()
```

---

### 4.4 分级治理模式（lib/mode_manager.py）

```python
#!/usr/bin/env python3
"""
分级治理模式
支持 strict / relaxed / off 三档切换
"""

import json
from pathlib import Path
from typing import Dict, Any, List
from enum import Enum
from dataclasses import dataclass


class GovernanceMode(str, Enum):
    """治理模式"""
    STRICT = "strict"       # 全量检查，拦截一切
    RELAXED = "relaxed"     # 仅检查严重错误，警告命名规范
    OFF = "off"             # 关闭被动验证，但扫描上下文仍然运行


@dataclass
class RuleConfig:
    """规则配置"""
    id: str
    category: str
    severity: str           # error / warning / info
    enabled_in_relaxed: bool  # relaxed 模式下是否启用
    auto_fix: bool          # 是否自动修复


class ModeManager:
    """治理模式管理器"""
    
    # 默认规则配置
    DEFAULT_RULES = [
        # 架构规则
        RuleConfig("arch-service-import", "architecture", "error", False, True),
        RuleConfig("arch-hook-import", "architecture", "error", False, True),
        RuleConfig("arch-layer-violation", "architecture", "error", True, False),
        
        # 导入规则
        RuleConfig("import-path-invalid", "import", "error", True, True),
        RuleConfig("import-not-exist", "import", "error", True, False),
        RuleConfig("import-mock-internal", "import", "error", True, False),
        
        # 命名规则
        RuleConfig("naming-component", "naming", "warning", False, False),
        RuleConfig("naming-hook", "naming", "warning", False, False),
        RuleConfig("naming-service", "naming", "warning", False, False),
        
        # 类型安全
        RuleConfig("type-no-any", "type-safety", "error", True, True),
        RuleConfig("type-mismatch", "type-safety", "error", True, False),
    ]
    
    def __init__(self, harness_dir: Path):
        self.harness_dir = harness_dir
        self.config_file = harness_dir / "mode-config.json"
        self.current_mode = self._load_mode()
        self.rules = {r.id: r for r in self.DEFAULT_RULES}
    
    def _load_mode(self) -> GovernanceMode:
        """加载当前模式"""
        if self.config_file.exists():
            try:
                data = json.loads(self.config_file.read_text())
                return GovernanceMode(data.get("mode", "strict"))
            except:
                pass
        return GovernanceMode.STRICT
    
    def set_mode(self, mode: GovernanceMode):
        """设置治理模式"""
        self.current_mode = mode
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.config_file.write_text(json.dumps({
            "mode": mode.value,
            "updated_at": self._get_timestamp()
        }, indent=2))
    
    def _get_timestamp(self) -> str:
        import datetime
        return datetime.datetime.now().isoformat()
    
    def get_enabled_rules(self) -> List[RuleConfig]:
        """获取当前模式下启用的规则"""
        if self.current_mode == GovernanceMode.OFF:
            return []
        
        if self.current_mode == GovernanceMode.RELAXED:
            return [r for r in self.rules.values() if r.enabled_in_relaxed]
        
        # STRICT 模式：所有规则都启用
        return list(self.rules.values())
    
    def should_validate(self) -> bool:
        """是否应该执行验证"""
        return self.current_mode != GovernanceMode.OFF
    
    def should_block(self, severity: str) -> bool:
        """是否应该拦截（根据模式和问题严重程度）"""
        if self.current_mode == GovernanceMode.OFF:
            return False
        
        if self.current_mode == GovernanceMode.RELAXED:
            # relaxed 模式只拦截 error
            return severity == "error"
        
        # strict 模式拦截所有
        return True
    
    def get_mode_info(self) -> Dict[str, Any]:
        """获取当前模式信息"""
        enabled_rules = self.get_enabled_rules()
        
        return {
            "mode": self.current_mode.value,
            "enabled_rules_count": len(enabled_rules),
            "error_rules": len([r for r in enabled_rules if r.severity == "error"]),
            "warning_rules": len([r for r in enabled_rules if r.severity == "warning"]),
            "auto_fix_rules": len([r for r in enabled_rules if r.auto_fix]),
            "description": {
                GovernanceMode.STRICT: "全量检查，拦截一切问题",
                GovernanceMode.RELAXED: "仅检查严重错误，警告命名规范",
                GovernanceMode.OFF: "关闭验证，但扫描上下文仍然运行"
            }.get(self.current_mode, "")
        }
    
    def toggle_mode(self) -> GovernanceMode:
        """切换模式（循环：strict -> relaxed -> off -> strict）"""
        cycle = [
            GovernanceMode.STRICT,
            GovernanceMode.RELAXED,
            GovernanceMode.OFF
        ]
        
        current_index = cycle.index(self.current_mode)
        next_index = (current_index + 1) % len(cycle)
        next_mode = cycle[next_index]
        
        self.set_mode(next_mode)
        return next_mode


class ValidationContext:
    """验证上下文"""
    
    def __init__(self, mode_manager: ModeManager):
        self.mode_manager = mode_manager
    
    def filter_issues(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """根据当前模式过滤问题"""
        enabled_rules = {r.id for r in self.mode_manager.get_enabled_rules()}
        
        filtered = []
        for issue in issues:
            rule_id = issue.get("rule_id", "")
            
            if rule_id in enabled_rules:
                # 根据模式调整严重程度
                if self.mode_manager.current_mode == GovernanceMode.RELAXED:
                    # relaxed 模式下，warning 降级为 info
                    if issue.get("severity") == "warning":
                        issue = {**issue, "severity": "info"}
                
                filtered.append(issue)
        
        return filtered
    
    def should_block(self, issues: List[Dict[str, Any]]) -> bool:
        """根据问题和模式决定是否拦截"""
        for issue in issues:
            if self.mode_manager.should_block(issue.get("severity", "error")):
                return True
        return False
```

---

### 4.5 Agent 适配器（lib/adapter.py）

```python
#!/usr/bin/env python3
"""
Agent 适配器
支持 DUCC / Comate / Claude Code
"""

import json
from pathlib import Path
from typing import Dict, Any, Optional
from abc import ABC, abstractmethod


class AgentAdapter(ABC):
    """Agent 适配器基类"""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Agent 名称"""
        pass
    
    @abstractmethod
    def generate_rules(self, rules: Dict, context: Dict) -> str:
        """生成当前 Agent 可用的规则文件"""
        pass
    
    @abstractmethod
    def get_hook_config(self) -> Optional[Dict]:
        """获取 Hook 配置"""
        pass
    
    def get_generated_file_path(self) -> Path:
        """获取生成的规则文件路径"""
        return Path(f".harness/generated/{self.name}.md")


class ClaudeAdapter(AgentAdapter):
    """Claude Code 适配器"""
    
    @property
    def name(self) -> str:
        return "claude"
    
    def generate_rules(self, rules: Dict, context: Dict) -> str:
        """生成带 @ 引用的 CLAUDE.md"""
        content = f"""# 项目规范

> 本项目使用 .harness 框架约束代码规范

## ⛔ 强制规则

@/.harness/rules.yaml

## 📋 组件 Schema

@/.harness/schemas/components.json

## 📁 可用导入

@/.harness/context/project-context.json

## 🔥 经验教训

@/.harness/memory/lessons/
"""
        return content
    
    def get_hook_config(self) -> Optional[Dict]:
        """获取 Claude Code Hook 配置"""
        return {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "Edit|Write",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "${CLAUDE_PROJECT_DIR}/.harness/hooks/validate-code.sh",
                                "timeout": 60
                            }
                        ]
                    }
                ]
            }
        }


class ComateAdapter(AgentAdapter):
    """Comate 适配器"""
    
    @property
    def name(self) -> str:
        return "comate"
    
    def generate_rules(self, rules: Dict, context: Dict) -> str:
        """生成纯 Markdown 格式（不支持 @ 引用）"""
        content = f"""# 项目规范

> 本项目使用 .harness 框架约束代码规范

## ⛔ 强制规则

### 三层架构

| 层级 | 目录 | 职责 | 可导入 |
|------|------|------|--------|
| Component | pages/, components/ | UI 渲染 | hook, service, types |
| Hook | hooks/ | 业务逻辑 | service, types |
| Service | api/ | 数据获取 | types |

### 导入规则

- ✅ 可用路径: @/types, @/hooks, @/api, @/pages, @/components
- ❌ 禁止路径: @/services, @/api/mockApi

### 命名规范

- Component: PascalCase
- Hook: camelCase with use prefix
- Service: camelCase with Service suffix

## 📁 可用类型

{self._format_types(context)}

## 📁 可用 API

{self._format_apis(context)}
"""
        return content
    
    def _format_types(self, context: Dict) -> str:
        types = context.get("types", [])
        if not types:
            return "暂无"
        
        lines = []
        for t in types[:20]:
            lines.append(f"- {t.get('name', '')} ({t.get('file_path', '')})")
        return '\n'.join(lines)
    
    def _format_apis(self, context: Dict) -> str:
        apis = context.get("apis", [])
        if not apis:
            return "暂无"
        
        lines = []
        for api in apis[:20]:
            lines.append(f"- {api.get('name', '')} ({api.get('file_path', '')})")
        return '\n'.join(lines)
    
    def get_hook_config(self) -> Optional[Dict]:
        """Comate 暂不支持 Hook"""
        return None


class DuccAdapter(AgentAdapter):
    """Ducc 适配器"""
    
    @property
    def name(self) -> str:
        return "ducc"
    
    def generate_rules(self, rules: Dict, context: Dict) -> str:
        """生成 Ducc 可用的规则文件"""
        # Ducc 支持 Markdown + 特定格式
        content = f"""# 项目规范

> 本项目使用 .harness 框架约束代码规范

## ⛔ 强制规则

{self._format_rules(rules)}

## 📁 可用导入

{self._format_context(context)}
"""
        return content
    
    def _format_rules(self, rules: Dict) -> str:
        lines = []
        
        # 架构规则
        arch = rules.get("architecture", {})
        for layer in arch.get("layers", []):
            name = layer.get("name", "")
            paths = ", ".join(layer.get("paths", []))
            can_import = ", ".join(layer.get("can_import", []))
            lines.append(f"- **{name} 层** ({paths}): 可导入 {can_import}")
        
        return '\n'.join(lines)
    
    def _format_context(self, context: Dict) -> str:
        lines = ["### 类型定义", ""]
        for t in context.get("types", [])[:10]:
            lines.append(f"- {t.get('name', '')}")
        
        lines.extend(["", "### Hooks", ""])
        for h in context.get("hooks", [])[:10]:
            lines.append(f"- {h.get('name', '')}")
        
        lines.extend(["", "### APIs", ""])
        for a in context.get("apis", [])[:10]:
            lines.append(f"- {a.get('name', '')}")
        
        return '\n'.join(lines)
    
    def get_hook_config(self) -> Optional[Dict]:
        """Ducc 的 Hook 配置"""
        # 根据实际 Ducc 的机制配置
        return None


class AdapterManager:
    """适配器管理器"""
    
    ADAPTERS = {
        "claude": ClaudeAdapter,
        "comate": ComateAdapter,
        "ducc": DuccAdapter,
    }
    
    def __init__(self, harness_dir: Path):
        self.harness_dir = harness_dir
        self.adapters = {name: cls() for name, cls in self.ADAPTERS.items()}
    
    def get_adapter(self, name: str) -> Optional[AgentAdapter]:
        """获取适配器"""
        return self.adapters.get(name)
    
    def generate_all(self, rules: Dict, context: Dict):
        """生成所有 Agent 的规则文件"""
        generated_dir = self.harness_dir / "generated"
        generated_dir.mkdir(parents=True, exist_ok=True)
        
        for name, adapter in self.adapters.items():
            content = adapter.generate_rules(rules, context)
            file_path = generated_dir / f"{name}.md"
            file_path.write_text(content, encoding="utf-8")
            print(f"✅ 生成: {file_path}")
    
    def get_hook_configs(self) -> Dict[str, Dict]:
        """获取所有支持 Hook 的 Agent 配置"""
        configs = {}
        for name, adapter in self.adapters.items():
            config = adapter.get_hook_config()
            if config:
                configs[name] = config
        return configs
```

---

### 4.6 验证器（lib/validator.py）

```python
#!/usr/bin/env python3
"""
代码验证器
tree-sitter + TypeScript Compiler API
"""

import re
import json
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass


@dataclass
class Issue:
    """问题"""
    rule_id: str
    severity: str        # error / warning / info
    category: str        # architecture / import / naming / type-safety
    message: str
    file: str
    line: Optional[int] = None
    suggestion: Optional[str] = None


class CodeValidator:
    """代码验证器"""
    
    def __init__(self, project_dir: Path, harness_dir: Path):
        self.project_dir = project_dir
        self.harness_dir = harness_dir
        self.rules = self._load_rules()
        self.context = self._load_context()
    
    def validate(self, file_path: str, code: str) -> List[Issue]:
        """
        验证代码
        
        Args:
            file_path: 文件路径
            code: 代码内容
        
        Returns:
            问题列表
        """
        issues = []
        
        # 1. 快速检查（正则表达式）
        issues.extend(self._quick_check(file_path, code))
        
        # 2. 结构检查（AST）
        issues.extend(self._structure_check(file_path, code))
        
        # 3. 类型检查（TypeScript Compiler）
        issues.extend(self._type_check(file_path))
        
        return issues
    
    def _quick_check(self, file_path: str, code: str) -> List[Issue]:
        """快速检查：正则表达式"""
        issues = []
        
        # 禁止 any 类型
        if re.search(r'\bany\b', code):
            issues.append(Issue(
                rule_id="type-no-any",
                severity="error",
                category="type-safety",
                message="禁止使用 any 类型",
                file=file_path,
                suggestion="使用具体类型或 unknown"
            ))
        
        # 导入路径检查
        if re.search(r'from\s+["\']@/services/', code):
            issues.append(Issue(
                rule_id="import-path-invalid",
                severity="error",
                category="import",
                message="@/services 路径不存在，使用 @/api",
                file=file_path,
                suggestion="修改导入路径为 @/api"
            ))
        
        # 禁止导入 mockApi 内部实现
        if re.search(r'from\s+["\']@/api/mockApi', code):
            issues.append(Issue(
                rule_id="import-mock-internal",
                severity="error",
                category="import",
                message="禁止直接导入 mockApi 内部实现",
                file=file_path,
                suggestion="使用 @/api/xxxService"
            ))
        
        return issues
    
    def _structure_check(self, file_path: str, code: str) -> List[Issue]:
        """结构检查：架构层依赖"""
        issues = []
        
        layer = self._get_layer(file_path)
        
        if layer == "service":
            # Service 层不能导入 Component 和 Hook
            if re.search(r'from\s+["\']@/(pages|components|hooks)', code):
                issues.append(Issue(
                    rule_id="arch-service-import",
                    severity="error",
                    category="architecture",
                    message="Service 层不能导入 Component 或 Hook",
                    file=file_path,
                    suggestion="将逻辑移到 Hook 层或重构依赖"
                ))
        
        elif layer == "hook":
            # Hook 层不能导入 Component
            if re.search(r'from\s+["\']@/(pages|components)', code):
                issues.append(Issue(
                    rule_id="arch-hook-import",
                    severity="error",
                    category="architecture",
                    message="Hook 层不能导入 Component",
                    file=file_path,
                    suggestion="组件应该在 pages/components 中实现"
                ))
        
        return issues
    
    def _type_check(self, file_path: str) -> List[Issue]:
        """类型检查：调用 TypeScript Compiler"""
        issues = []
        
        try:
            result = subprocess.run(
                ["npx", "tsc", "--noEmit", "--pretty", "false"],
                cwd=self.project_dir,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                # 解析错误输出
                for line in result.stdout.split('\n'):
                    if file_path in line and 'error' in line.lower():
                        # 提取错误信息
                        match = re.match(r'(.+?)\((\d+),(\d+)\):\s*(error\s+TS\d+:.*)', line)
                        if match:
                            issues.append(Issue(
                                rule_id="type-mismatch",
                                severity="error",
                                category="type-safety",
                                message=match.group(4),
                                file=match.group(1),
                                line=int(match.group(2)),
                                suggestion="修复 TypeScript 类型错误"
                            ))
        
        except subprocess.TimeoutExpired:
            issues.append(Issue(
                rule_id="type-check-timeout",
                severity="warning",
                category="performance",
                message="类型检查超时，跳过",
                file=file_path
            ))
        
        return issues
    
    def _get_layer(self, file_path: str) -> str:
        """判断文件所属层级"""
        if '/api/' in file_path:
            return 'service'
        elif '/hooks/' in file_path:
            return 'hook'
        elif '/pages/' in file_path or '/components/' in file_path:
            return 'component'
        return 'unknown'
    
    def _load_rules(self) -> Dict[str, Any]:
        """加载规则配置"""
        import yaml
        rules_file = self.harness_dir / "rules.yaml"
        if rules_file.exists():
            return yaml.safe_load(rules_file.read_text())
        return {}
    
    def _load_context(self) -> Dict[str, Any]:
        """加载项目上下文"""
        context_file = self.harness_dir / "context" / "project-context.json"
        if context_file.exists():
            return json.loads(context_file.read_text())
        return {}


def validate_file(file_path: str, code: str, project_dir: Path = None) -> List[Dict]:
    """验证文件的便捷函数"""
    project_dir = project_dir or Path.cwd()
    harness_dir = project_dir / ".harness"
    
    validator = CodeValidator(project_dir, harness_dir)
    issues = validator.validate(file_path, code)
    
    return [
        {
            "rule_id": i.rule_id,
            "severity": i.severity,
            "category": i.category,
            "message": i.message,
            "file": i.file,
            "line": i.line,
            "suggestion": i.suggestion
        }
        for i in issues
    ]
```

---

## 五、CLI 命令

### 统一入口（commands/harness）

```bash
#!/usr/bin/env python3
"""
Harness CLI 统一入口
"""

import click
import json
from pathlib import Path


@click.group()
def cli():
    """Harness 2.0 - 代码治理框架"""
    pass


@cli.command()
def init():
    """初始化 Harness"""
    click.echo("🚀 初始化 Harness 2.0...")
    
    # 创建目录结构
    dirs = [
        ".harness/lib",
        ".harness/hooks",
        ".harness/prompts",
        ".harness/memory/lessons",
        ".harness/memory/shared",
        ".harness/context/validation-cache",
        ".harness/schemas",
        ".harness/fixers",
        ".harness/generated",
        ".harness-shared/lessons",
    ]
    
    for d in dirs:
        Path(d).mkdir(parents=True, exist_ok=True)
    
    click.echo("✅ 目录结构已创建")
    
    # 生成默认配置
    # TODO: 复制模板文件
    
    click.echo("✅ 初始化完成")


@cli.command()
@click.option('--full', is_flag=True, help='强制全量扫描')
def scan(full: bool):
    """扫描项目代码"""
    click.echo("🔍 扫描项目代码...")
    
    from lib.scanner import IncrementalScanner
    
    scanner = IncrementalScanner(Path.cwd())
    result = scanner.scan(force_full=full)
    
    click.echo(f"✅ 扫描完成:")
    click.echo(f"   组件: {result['summary'].get('total_components', 0)}")
    click.echo(f"   Hooks: {result['summary'].get('total_hooks', 0)}")
    click.echo(f"   APIs: {result['summary'].get('total_apis', 0)}")
    click.echo(f"   类型: {result['summary'].get('total_types', 0)}")


@cli.command()
@click.argument('file_path')
def validate(file_path: str):
    """验证指定文件"""
    click.echo(f"🔍 验证文件: {file_path}")
    
    from lib.validator import validate_file
    
    full_path = Path.cwd() / file_path
    if not full_path.exists():
        click.echo(f"❌ 文件不存在: {file_path}")
        return
    
    code = full_path.read_text(encoding="utf-8")
    issues = validate_file(file_path, code)
    
    if issues:
        click.echo(f"❌ 发现 {len(issues)} 个问题:")
        for issue in issues:
            severity = issue['severity'].upper()
            click.echo(f"   [{severity}] {issue['message']}")
            if issue.get('suggestion'):
                click.echo(f"      建议: {issue['suggestion']}")
    else:
        click.echo("✅ 验证通过")


@cli.command()
@click.argument('mode', type=click.Choice(['strict', 'relaxed', 'off']))
def mode(mode: str):
    """设置治理模式"""
    from lib.mode_manager import GovernanceMode, ModeManager
    
    manager = ModeManager(Path.cwd() / ".harness")
    manager.set_mode(GovernanceMode(mode))
    
    click.echo(f"✅ 治理模式已切换为: {mode}")


@cli.command()
def status():
    """查看当前状态"""
    from lib.mode_manager import ModeManager
    
    manager = ModeManager(Path.cwd() / ".harness")
    info = manager.get_mode_info()
    
    click.echo("📊 Harness 状态:")
    click.echo(f"   模式: {info['mode']}")
    click.echo(f"   启用规则: {info['enabled_rules_count']}")
    click.echo(f"   描述: {info['description']}")


@cli.command()
@click.option('--publish', is_flag=True, help='发布到团队共享')
@click.argument('title')
@click.argument('content')
def lesson(title: str, content: str, publish: bool):
    """记录经验教训"""
    from lib.experience_market import ExperienceMarket
    
    market = ExperienceMarket(Path.cwd() / ".harness")
    lesson = market.create_lesson(
        title=title,
        content=content,
        publish=publish
    )
    
    click.echo(f"✅ 经验已保存: {lesson.id}")
    if publish:
        click.echo("✅ 已发布到团队共享")


@cli.command()
def sync():
    """同步团队共享经验"""
    from lib.experience_market import ExperienceMarket
    
    market = ExperienceMarket(Path.cwd() / ".harness")
    count = market.sync_from_shared()
    
    click.echo(f"✅ 同步完成，新增 {count} 条经验")


@cli.command()
@click.argument('agent', type=click.Choice(['claude', 'comate', 'ducc', 'all']))
def generate(agent: str):
    """生成 Agent 规则文件"""
    from lib.adapter import AdapterManager
    import yaml
    
    harness_dir = Path.cwd() / ".harness"
    
    # 加载规则和上下文
    rules_file = harness_dir / "rules.yaml"
    context_file = harness_dir / "context" / "project-context.json"
    
    rules = yaml.safe_load(rules_file.read_text()) if rules_file.exists() else {}
    context = json.loads(context_file.read_text()) if context_file.exists() else {}
    
    manager = AdapterManager(harness_dir)
    
    if agent == "all":
        manager.generate_all(rules, context)
    else:
        adapter = manager.get_adapter(agent)
        if adapter:
            content = adapter.generate_rules(rules, context)
            file_path = harness_dir / "generated" / f"{agent}.md"
            file_path.write_text(content)
            click.echo(f"✅ 生成: {file_path}")


if __name__ == '__main__':
    cli()
```

---

## 六、Hook 脚本

### validate-code.sh

```bash
#!/bin/bash
# PostToolUse Hook - 代码验证
# 接收 stdin 的 JSON 输入

set -e

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"
HARNESS_DIR="$PROJECT_DIR/.harness"

# 检查 Harness 是否初始化
if [[ ! -d "$HARNESS_DIR" ]]; then
    echo "Harness 未初始化，跳过验证"
    exit 0
fi

# 检查治理模式
MODE_CONFIG="$HARNESS_DIR/mode-config.json"
if [[ -f "$MODE_CONFIG" ]]; then
    MODE=$(python3 -c "import json; print(json.load(open('$MODE_CONFIG')).get('mode', 'strict'))")
    if [[ "$MODE" == "off" ]]; then
        echo "治理模式为 off，跳过验证"
        exit 0
    fi
fi

# 解析输入
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('file_path',''))")
CODE=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('new_string','') or d.get('tool_input',{}).get('content',''))")

if [[ -z "$FILE_PATH" || -z "$CODE" ]]; then
    echo "无法解析输入，跳过验证"
    exit 0
fi

# 只验证 src/ 目录下的文件
if [[ ! "$FILE_PATH" =~ ^src/ ]]; then
    exit 0
fi

# 调用验证器
RESULT=$(python3 "$HARNESS_DIR/lib/validator.py" --file "$FILE_PATH" --code "$CODE" --project-dir "$PROJECT_DIR" 2>&1)
EXIT_CODE=$?

if [[ $EXIT_CODE -ne 0 ]]; then
    # 验证失败，返回错误给 Agent
    echo "{\"hookSpecificOutput\":{\"hookEventName\":\"PostToolUse\",\"decision\":\"block\",\"reason\":\"$RESULT\"}}" >&2
    exit 2
fi

# 验证通过
exit 0
```

---

## 七、配置文件

### rules.yaml

```yaml
# Harness 2.0 规则配置

# 验证配置
validation:
  timeout:
    quick_check: 10s
    full_check: 60s
  
  dynamic_timeout:
    enabled: true
    rules:
      - max_lines: 100
        timeout: 30s
      - max_lines: 500
        timeout: 60s
      - max_lines: null
        timeout: 120s

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

# 命名规范
naming:
  component: PascalCase
  hook: camelCase with prefix use
  service: camelCase with suffix Service
  type: PascalCase

# 导入规则
imports:
  allowed_prefixes:
    - "@/types"
    - "@/hooks"
    - "@/api"
    - "@/pages"
    - "@/components"
  
  forbidden_imports:
    - "@/services"
    - "@/api/mockApi"

# 禁止的代码模式
forbidden_patterns:
  - pattern: "\\bany\\b"
    message: "禁止使用 any 类型"
  
  - pattern: "from\\s+[\"']@/services/"
    message: "@/services 路径不存在，使用 @/api"

# 经验市场配置
experience_market:
  enabled: true
  auto_pull: true
  auto_push: false
  max_lessons: 10
  min_score: 0.3

# 治理模式配置
governance:
  default_mode: strict
  
  rules:
    # 架构规则
    arch-service-import:
      severity: error
      auto_fix: true
      enabled_in_relaxed: false
    
    arch-hook-import:
      severity: error
      auto_fix: true
      enabled_in_relaxed: false
    
    # 导入规则
    import-path-invalid:
      severity: error
      auto_fix: true
      enabled_in_relaxed: true
    
    # 类型规则
    type-no-any:
      severity: error
      auto_fix: false
      enabled_in_relaxed: true
```

---

## 八、实施步骤

### Phase 1: 核心能力（Week 1-2）

| 任务 | 时间 | 产出 |
|------|------|------|
| 目录结构初始化 | Day 1 | 完整目录结构 |
| 验证器实现 | Day 2-3 | validator.py |
| 智能缓存实现 | Day 4-5 | cache.py + dependency_graph.py |
| 自动修复引擎 | Day 6-7 | fixer.py + fixers/ |
| 经验市场实现 | Day 8-9 | experience_market.py |
| 分级治理实现 | Day 10 | mode_manager.py |
| Agent 适配器 | Day 11-12 | adapter.py |
| CLI 命令实现 | Day 13-14 | harness CLI |

### Phase 2: 集成测试（Week 3）

| 任务 | 时间 | 产出 |
|------|------|------|
| 单元测试 | Day 1-2 | 测试覆盖率 > 80% |
| 集成测试 | Day 3-4 | E2E 测试用例 |
| 性能测试 | Day 5 | 性能报告 |
| 文档完善 | Day 6-7 | 使用文档 |

### Phase 3: 优化扩展（Week 4+）

- 规则效能分析
- 规则可执行化
- Git Pre-commit 集成
- MCP Tool 扩展

---

## 九、验收标准

| 功能 | 验收标准 |
|------|----------|
| 增量扫描 | 只有变更文件被重新扫描 |
| 智能缓存 | 依赖变化时相关缓存自动失效 |
| 自动修复 | 每条错误规则都有 fixer 输出修复建议 |
| 经验市场 | 支持本地 + 团队共享经验 |
| 分级治理 | strict/relaxed/off 三档可切换 |
| Agent 支持 | 支持 Claude / Comate / Ducc |
| Hook 验证 | 不符合规范的代码被拦截 |

---

## 十、后续优化（Phase 2+）

- [ ] 规则可执行化（TypeScript 编写规则）
- [ ] 规则效能分析
- [ ] 完整性校验
- [ ] Git Pre-commit 集成
- [ ] MCP Tool 扩展

---

## 十一、跨项目分发与依赖隔离（Phase 2 重构项）

> **背景**：Phase 1 期间 `.harness/lib/` 与项目同仓库，每个接入项目需在 `.harness/.venv/` 安装 tree-sitter 等依赖（侵入性高、版本易漂移）。Phase 2 需把 lib/ 抽离为独立可分发包，让接入项目里的 `.harness/` 只保留**配置 + 上下文 + hooks**。

### 11.1 分发方案选型（Phase 2 决策）

| 方案 | 接入项目要做什么 | 适用场景 |
|---|---|---|
| **A. 项目内 venv**（Phase 1 当前用） | `.harness/.venv` + `pip install -r requirements.txt` | 单项目开发期 |
| **B. uv + PEP 723 单文件脚本** | 全局装一次 uv；项目零 Python 依赖 | 内部多项目、Python 友好团队 |
| **C. pipx / uv tool install** | 全局装 `harness` CLI | 工具型分发 |
| **D. 独立二进制**（PyInstaller/Nuitka） | 下载 `harness` 二进制扔进 PATH | 非 Python 团队 |
| **E. Docker 化**（见原 P2 #13） | 装 docker | CI、隔离要求高 |

### 11.2 重构清单（Phase 2 必做，避免遗漏）

- [ ] **代码物理拆分**：`.harness/lib/` → 独立仓库或 PyPI 包（暂定名 `harness-cli`）
- [ ] **接入项目结构瘦身**：项目内只保留 `.harness/{rules.yaml, schemas/, context/, memory/, hooks/, generated/, mode-config.json, VERSION}`，去掉 `lib/` 与 `.venv/`
- [ ] **Hook 调用方式改造**：`hooks/validate-code.sh` 由 `python3 .harness/lib/...` 改为 `harness validate ...` 或 `uv run --from harness-cli@x.y.z harness validate ...`
- [ ] **VERSION 语义升级**：分项目 schema 版本（`.harness/VERSION`）与 CLI 版本（`harness --version`）；`harness doctor` 校验两者兼容
- [ ] **`harness upgrade` 命令**（已在 P1 #10）：升级独立分发的 CLI 而非项目内 lib/
- [ ] **打包元数据**：补 `pyproject.toml` + `[project.scripts] harness = "harness.cli:cli"`
- [ ] **跨平台二进制构建**（仅当走方案 D）：mac arm64 / mac x86_64 / linux x86_64 / win x86_64 四份产物，CI 统一构建

### 11.3 Phase 1 期间的"为重构留余地"约定

P0 实现期就遵守的约定，避免 Phase 2 重写：

1. `lib/` 内部 **不假设** `.harness/lib/...` 的固定相对路径；用 `Path(__file__).parent` 推导
2. CLI 入口 (`commands/harness`) **不假设** 必须从项目根启动；通过参数 / 环境变量接收 `project_dir` 与 `harness_dir`
3. 所有依赖**只声明在 `.harness/requirements.txt`**，不掺杂到项目根 `package.json` 或别处
4. 模块间不互相 import 项目相对路径，统一走 `from lib.xxx import ...`，便于将来改成 `from harness.xxx import ...`
