#!/usr/bin/env python3
"""
Orchestrator - 简化版编排器

职责：
1. 管理任务生命周期（创建、更新、完成）
2. 记录执行追踪
3. 保存经验教训
4. 提供执行计划框架

注意：实际的 Agent 逻辑（AI 决策）由阿里百炼 API 处理
"""

import json
import uuid
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

# 添加父目录到路径，以便导入 harness 模块
sys.path.insert(0, str(Path(__file__).parent.parent))

from harness import TaskManager, TraceRecorder, MemoryStore, Task, CheckpointStatus, TaskStatus


class Orchestrator:
    """简化版 Orchestrator"""

    def __init__(self, base_dir: Optional[Path] = None):
        # orchestrator.py 在 harness/core/ 下，需要往上两级到达 note-h5/
        self.base_dir = base_dir or Path(__file__).parent.parent.parent
        self.task_manager = TaskManager(self.base_dir)
        self.trace_recorder = TraceRecorder(self.base_dir)
        self.memory_store = MemoryStore(self.base_dir)

        # 提示词路径
        self.prompts_dir = self.base_dir / "harness" / "prompts"

        # 缓存最近的经验，避免每次都读取文件
        self._cached_lessons = None
        self._cached_lessons_text = ""

    def _load_recent_lessons(self) -> str:
        """加载最近的 5 条经验教训，拼接为 prompt 可用的文本"""
        if self._cached_lessons_text:
            return self._cached_lessons_text

        memory_dir = self.memory_store.memory_dir
        lesson_files = sorted(
            memory_dir.glob("lessons-*.md"),
            key=lambda f: f.stat().st_mtime,
            reverse=True
        )

        if not lesson_files:
            return ""

        lessons_text = "\n\n## 🔥 历史经验教训（从之前的执行中学习，避免重复错误）\n\n"
        for i, lesson_file in enumerate(lesson_files[:5]):
            content = lesson_file.read_text(encoding="utf-8")
            # 只取关键信息，限制长度
            lines = content.split('\n')
            key_lines = []
            for line in lines:
                if line.startswith('# ') or line.startswith('## '):
                    key_lines.append(line)
                elif line.strip().startswith('- ') or line.strip().startswith('* '):
                    key_lines.append(line)
            if len('\n'.join(key_lines)) > 500:
                key_lines = key_lines[:10]
            lessons_text += f"### 经验 {i+1}\n{''.join(key_lines)}\n\n"

        self._cached_lessons_text = lessons_text
        return lessons_text

    def invalidate_lessons_cache(self):
        """使经验缓存失效（新保存经验后调用）"""
        self._cached_lessons_text = ""

    def create_task(self, description: str, priority: str = "P2") -> Task:
        """创建新任务"""
        task = self.task_manager.create_task(
            agent="orchestrator",
            priority=priority,
            description=description
        )
        return task

    def get_planner_prompt(self) -> str:
        """获取 Planner Agent 系统提示词"""
        prompt_file = self.prompts_dir / "planner_system.txt"
        if prompt_file.exists():
            prompt = prompt_file.read_text(encoding="utf-8")
        else:
            prompt = ""

        # 注入历史经验
        lessons = self._load_recent_lessons()
        if lessons:
            prompt += lessons

        return prompt

    def get_coder_prompt(self) -> str:
        """获取 Coder Agent 系统提示词"""
        prompt_file = self.prompts_dir / "coder_system.txt"
        if prompt_file.exists():
            prompt = prompt_file.read_text(encoding="utf-8")
        else:
            prompt = ""

        # 注入 schema 内容
        schema_file = self.base_dir / "harness" / "components_schema.json"
        if schema_file.exists():
            schema_content = schema_file.read_text(encoding="utf-8")
            if "SCHEMA_PLACEHOLDER" in prompt:
                prompt = prompt.replace("SCHEMA_PLACEHOLDER", schema_content)
            else:
                prompt += "\n\n## 📋 组件 Schema 定义\n\n```json\n" + schema_content + "\n```"

        # 注入历史经验
        lessons = self._load_recent_lessons()
        if lessons:
            prompt += lessons

        return prompt

    def get_reviewer_prompt(self) -> str:
        """获取 Reviewer Agent 系统提示词"""
        prompt_file = self.prompts_dir / "reviewer_system.txt"
        if prompt_file.exists():
            prompt = prompt_file.read_text(encoding="utf-8")
        else:
            prompt = ""

        # 注入历史经验
        lessons = self._load_recent_lessons()
        if lessons:
            prompt += lessons

        return prompt

    def record_planner_result(
        self,
        task_id: str,
        trace_id: str,
        plan: Dict[str, Any]
    ) -> bool:
        """记录 Planner Agent 执行结果"""
        return self.trace_recorder.record_step(
            trace_id=trace_id,
            agent="planner",
            input_data={"task_id": task_id},
            output_data=plan,
            status="completed"
        )

    def record_coder_result(
        self,
        trace_id: str,
        file_path: str,
        code: str,
        success: bool
    ) -> bool:
        """记录 Coder Agent 执行结果"""
        return self.trace_recorder.record_step(
            trace_id=trace_id,
            agent="coder",
            input_data={"file_path": file_path},
            output_data={
                "file_path": file_path,
                "success": success,
                "code_length": len(code)
            },
            status="completed" if success else "failed"
        )

    def record_reviewer_result(
        self,
        trace_id: str,
        review_result: Dict[str, Any]
    ) -> bool:
        """记录 Reviewer Agent 执行结果"""
        return self.trace_recorder.record_step(
            trace_id=trace_id,
            agent="reviewer",
            input_data={},
            output_data=review_result,
            status="completed"
        )

    def save_lesson(
        self,
        title: str,
        content: str,
        category: str = "general"
    ) -> str:
        """保存经验教训"""
        result = self.memory_store.save_lesson(title, content, category)
        self.invalidate_lessons_cache()
        return result

    def save_execution_lessons(
        self,
        task_description: str,
        errors: List[str],
        issues: List[Dict[str, Any]],
        status: str
    ) -> str:
        """将一次执行中的错误和问题总结为经验教训，自动落盘

        Args:
            task_description: 任务描述
            errors: 执行错误列表
            issues: 发现的所有问题列表
            status: 执行状态

        Returns:
            保存的文件名
        """
        if not errors and not issues:
            return ""

        content = f"## 任务\n{task_description}\n\n"
        content += f"## 状态\n{status}\n\n"

        if errors:
            content += "## 错误\n"
            for err in errors:
                content += f"- {err}\n"
            content += "\n"

        if issues:
            # 分组统计
            error_issues = [i for i in issues if i.get('severity') == 'error']
            warning_issues = [i for i in issues if i.get('severity') == 'warning']
            info_issues = [i for i in issues if i.get('severity') == 'info']

            content += f"## 发现 {len(issues)} 个问题 (error: {len(error_issues)}, warning: {len(warning_issues)}, info: {len(info_issues)})\n\n"

            if error_issues:
                content += "### Error 级别\n"
                for issue in error_issues[:10]:
                    content += f"- [{issue.get('category', 'general')}] {issue.get('message', '')}\n"
                    if issue.get('suggestion'):
                        content += f"  建议: {issue['suggestion']}\n"
                content += "\n"

            if warning_issues:
                content += "### Warning 级别\n"
                for issue in warning_issues[:5]:
                    content += f"- {issue.get('message', '')}\n"
                if len(warning_issues) > 5:
                    content += f"- ... 还有 {len(warning_issues) - 5} 个 warning\n"
                content += "\n"

        content += "## 经验总结\n"
        content += "上述问题应在后续执行中避免重复发生。Coder 应特别注意 error 级别的类型匹配和 API 签名一致性问题。\n"

        title = f"执行经验 - {task_description[:50]}"
        category = "execution"
        return self.save_lesson(title, content, category)

    def save_pattern(
        self,
        name: str,
        category: str,
        description: str,
        applicability: List[str],
        implementation: Dict[str, Any]
    ) -> None:
        """保存模式"""
        self.memory_store.save_pattern(name, category, description, applicability, implementation)

    def start_execution(self, task_id: str, description: str) -> str:
        """开始执行任务"""
        trace_id = self.trace_recorder.start_trace(task_id, "orchestrator")
        self.task_manager.update_task_status(task_id, TaskStatus.IN_PROGRESS)
        self.task_manager.add_checkpoint(
            task_id,
            "execution_started",
            CheckpointStatus.PASSED,
            {"description": description}
        )
        return trace_id

    def complete_execution(
        self,
        task_id: str,
        trace_id: str,
        success: bool,
        summary: str = ""
    ) -> bool:
        """完成执行"""
        status = "success" if success else "failed"
        self.trace_recorder.end_trace(trace_id, status)
        self.task_manager.update_task_status(
            task_id,
            TaskStatus.COMPLETED if success else TaskStatus.FAILED
        )
        self.task_manager.add_checkpoint(
            task_id,
            "execution_completed",
            CheckpointStatus.PASSED if success else CheckpointStatus.FAILED,
            {"summary": summary}
        )
        return True

    def get_task_summary(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务摘要"""
        task = self.task_manager.get_task(task_id)
        if not task:
            return None

        return {
            "task_id": task.task_id,
            "status": task.status.value,
            "priority": task.priority,
            "description": task.description,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "checkpoint_count": len(task.checkpoints)
        }


class ExecutionPlan:
    """执行计划数据结构"""

    def __init__(self, plan_data: Dict[str, Any]):
        self.task_description = plan_data.get("task_description", "")
        self.estimated_steps = plan_data.get("estimated_steps", 0)
        self.steps = plan_data.get("steps", [])

    def get_coder_steps(self) -> List[Dict[str, Any]]:
        """获取 Coder Agent 的步骤"""
        return [s for s in self.steps if s.get("agent") == "coder"]

    def get_reviewer_steps(self) -> List[Dict[str, Any]]:
        """获取 Reviewer Agent 的步骤"""
        return [s for s in self.steps if s.get("agent") == "reviewer"]

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "task_description": self.task_description,
            "estimated_steps": self.estimated_steps,
            "steps": self.steps
        }

    @classmethod
    def from_json(cls, json_str: str) -> "ExecutionPlan":
        """从 JSON 字符串创建"""
        plan_data = json.loads(json_str)
        return cls(plan_data)


def load_execution_plan(plan_file: Path) -> Optional[ExecutionPlan]:
    """从文件加载执行计划"""
    if not plan_file.exists():
        return None
    with open(plan_file, "r", encoding="utf-8") as f:
        plan_data = json.load(f)
    return ExecutionPlan(plan_data)
