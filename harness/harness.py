#!/usr/bin/env python3
"""
Harness 核心模块
管理 Agent 任务的执行状态、追踪和记忆存储
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from enum import Enum


class TaskStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class CheckpointStatus(str, Enum):
    """检查点状态枚举"""
    PASSED = "passed"
    FAILED = "failed"


@dataclass
class Checkpoint:
    """检查点数据结构"""
    name: str
    timestamp: str
    status: CheckpointStatus
    data: Optional[Dict[str, Any]] = None

    @classmethod
    def create(cls, name: str, status: CheckpointStatus, data: Optional[Dict[str, Any]] = None):
        return cls(
            name=name,
            timestamp=datetime.now().isoformat(),
            status=status,
            data=data or {}
        )


@dataclass
class Task:
    """任务数据结构"""
    task_id: str
    status: TaskStatus
    priority: str  # P0, P1, P2, P3
    agent: str
    created_at: str
    updated_at: str
    checkpoints: List[Checkpoint]
    description: Optional[str] = None
    input_data: Optional[Dict[str, Any]] = None
    output_data: Optional[Dict[str, Any]] = None

    @classmethod
    def create(cls, agent: str, priority: str = "P2", description: Optional[str] = None):
        now = datetime.now().isoformat()
        return cls(
            task_id=str(uuid.uuid4()),
            status=TaskStatus.PENDING,
            priority=priority,
            agent=agent,
            created_at=now,
            updated_at=now,
            checkpoints=[],
            description=description,
            input_data={},
            output_data={}
        )

    def add_checkpoint(self, name: str, status: CheckpointStatus, data: Optional[Dict[str, Any]] = None):
        """添加检查点"""
        checkpoint = Checkpoint.create(name, status, data)
        self.checkpoints.append(checkpoint)
        self.updated_at = datetime.now().isoformat()
        return checkpoint

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "priority": self.priority,
            "agent": self.agent,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "checkpoints": [
                {
                    "name": cp.name,
                    "timestamp": cp.timestamp,
                    "status": cp.status.value,
                    "data": cp.data
                }
                for cp in self.checkpoints
            ],
            "description": self.description,
            "input_data": self.input_data,
            "output_data": self.output_data
        }


class TaskManager:
    """任务管理器"""

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir or Path(__file__).parent
        self.tasks_dir = self.base_dir / "tasks"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self._tasks: Dict[str, Task] = {}

    def create_task(self, agent: str, priority: str = "P2", description: Optional[str] = None) -> Task:
        """创建新任务"""
        task = Task.create(agent, priority, description)
        self._tasks[task.task_id] = task
        self._save_task(task)
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        """获取任务"""
        if task_id in self._tasks:
            return self._tasks[task_id]
        # 尝试从文件加载
        task_file = self.tasks_dir / f"task-{task_id}.json"
        if task_file.exists():
            return self._load_task(task_file)
        return None

    def update_task_status(self, task_id: str, status: TaskStatus) -> bool:
        """更新任务状态"""
        task = self.get_task(task_id)
        if task:
            task.status = status
            task.updated_at = datetime.now().isoformat()
            self._save_task(task)
            return True
        return False

    def add_checkpoint(self, task_id: str, name: str, status: CheckpointStatus, data: Optional[Dict[str, Any]] = None) -> bool:
        """添加检查点"""
        task = self.get_task(task_id)
        if task:
            task.add_checkpoint(name, status, data)
            self._save_task(task)
            return True
        return False

    def list_tasks(self, status: Optional[TaskStatus] = None) -> List[Task]:
        """列出任务"""
        tasks = []
        for task_file in self.tasks_dir.glob("task-*.json"):
            task = self._load_task(task_file)
            if task and (status is None or task.status == status):
                tasks.append(task)
        return sorted(tasks, key=lambda t: t.created_at, reverse=True)

    def _save_task(self, task: Task) -> None:
        """保存任务到文件"""
        task_file = self.tasks_dir / f"task-{task.task_id}.json"
        with open(task_file, "w", encoding="utf-8") as f:
            json.dump(task.to_dict(), f, indent=2, ensure_ascii=False)

    def _load_task(self, task_file: Path) -> Optional[Task]:
        """从文件加载任务"""
        try:
            with open(task_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            checkpoints = [
                Checkpoint(
                    name=cp["name"],
                    timestamp=cp["timestamp"],
                    status=CheckpointStatus(cp["status"]),
                    data=cp.get("data")
                )
                for cp in data.get("checkpoints", [])
            ]
            return Task(
                task_id=data["task_id"],
                status=TaskStatus(data["status"]),
                priority=data["priority"],
                agent=data["agent"],
                created_at=data["created_at"],
                updated_at=data["updated_at"],
                checkpoints=checkpoints,
                description=data.get("description"),
                input_data=data.get("input_data"),
                output_data=data.get("output_data")
            )
        except Exception:
            return None


class TraceRecorder:
    """执行追踪记录器"""

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir or Path(__file__).parent
        self.trace_dir = self.base_dir / "trace"
        self.trace_dir.mkdir(parents=True, exist_ok=True)

    def start_trace(self, task_id: str, agent: str) -> str:
        """开始追踪"""
        trace_id = str(uuid.uuid4())
        trace_file = self.trace_dir / f"trace-{trace_id}.json"
        trace_data = {
            "trace_id": trace_id,
            "task_id": task_id,
            "timestamp": datetime.now().isoformat(),
            "agent_sequence": [],
            "status": "started"
        }
        with open(trace_file, "w", encoding="utf-8") as f:
            json.dump(trace_data, f, indent=2)
        return trace_id

    def record_step(self, trace_id: str, agent: str, input_data: Dict[str, Any],
                    output_data: Dict[str, Any], status: str) -> bool:
        """记录执行步骤"""
        trace_file = self.trace_dir / f"trace-{trace_id}.json"
        if not trace_file.exists():
            return False

        with open(trace_file, "r", encoding="utf-8") as f:
            trace_data = json.load(f)

        trace_data["agent_sequence"].append({
            "agent": agent,
            "input": input_data,
            "output": output_data,
            "status": status,
            "timestamp": datetime.now().isoformat()
        })

        with open(trace_file, "w", encoding="utf-8") as f:
            json.dump(trace_data, f, indent=2)

        # 同时更新人类可读的 .md 文件
        self._update_trace_md(trace_id, trace_data)

        return True

    def end_trace(self, trace_id: str, status: str) -> bool:
        """结束追踪"""
        trace_file = self.trace_dir / f"trace-{trace_id}.json"
        if not trace_file.exists():
            return False

        with open(trace_file, "r", encoding="utf-8") as f:
            trace_data = json.load(f)

        trace_data["status"] = status
        trace_data["ended_at"] = datetime.now().isoformat()

        with open(trace_file, "w", encoding="utf-8") as f:
            json.dump(trace_data, f, indent=2)

        # 生成最终的人类可读报告
        self._generate_trace_md(trace_id, trace_data)

        return True

    def _update_trace_md(self, trace_id: str, trace_data: Dict[str, Any]) -> None:
        """更新执行中的人类可读文件"""
        md_file = self.trace_dir / f"trace-{trace_id}.md"

        lines = [
            "# 执行追踪报告",
            "",
            "## 基本信息",
            f"- **状态**: {trace_data.get('status', 'unknown')}",
            f"- **时间**: {trace_data.get('timestamp', '')}",
            "",
            "## 执行步骤",
            "",
            "| # | Agent | 描述 | 状态 |",
            "|---|-------|------|------|",
        ]

        for i, step in enumerate(trace_data.get("agent_sequence", []), 1):
            agent = step.get("agent", "")
            desc = step.get("description", "") or self._get_step_description(step)
            status_icon = self._get_step_icon(step)
            lines.append(f"| {i} | {agent} | {desc} | {status_icon} |")

        lines.append("")
        lines.append("> ⚠️ 执行中，请稍后刷新查看最新状态...")

        with open(md_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def _generate_trace_md(self, trace_id: str, trace_data: Dict[str, Any]) -> None:
        """生成最终的人类可读报告"""
        md_file = self.trace_dir / f"trace-{trace_id}.md"

        status = trace_data.get("status", "unknown")
        status_icon = self._status_to_icon(status)
        status_text = {
            "completed": "✅ 成功完成",
            "partial_failure": "⚠️ 部分失败",
            "failed": "❌ 执行失败"
        }.get(status, status)

        lines = [
            f"# 执行追踪报告",
            "",
            "## 基本信息",
            f"- **状态**: {status_text}",
            f"- **开始时间**: {trace_data.get('timestamp', '')}",
            f"- **结束时间**: {trace_data.get('ended_at', '')}",
            "",
            "## 执行步骤",
            "",
            "| # | Agent | 描述 | 状态 |",
            "|---|-------|------|------|",
        ]

        for i, step in enumerate(trace_data.get("agent_sequence", []), 1):
            agent = step.get("agent", "")
            desc = step.get("description", "") or self._get_step_description(step)
            icon = self._get_step_icon(step)
            lines.append(f"| {i} | {agent} | {desc} | {icon} |")

        # 提取错误信息
        errors = self._extract_errors(trace_data)
        if errors:
            lines.extend([
                "",
                "## ❌ 错误详情",
                ""
            ])
            for i, err in enumerate(errors, 1):
                lines.append(f"### {i}. {err.get('file', '未知文件')}")
                lines.append(f"- **错误**: {err.get('message', '')}")
                if err.get('suggestion'):
                    lines.append(f"- **建议**: {err.get('suggestion', '')}")
                lines.append("")

        # 统计生成的文件
        generated_files = self._extract_generated_files(trace_data)
        if generated_files:
            lines.extend([
                "",
                "## 📁 生成的文件",
                ""
            ])
            for file_path in generated_files:
                lines.append(f"- `{file_path}`")

        lines.extend([
            "",
            "---",
            f"_由 AI Harness 自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_"
        ])

        with open(md_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def _get_step_description(self, step: Dict[str, Any]) -> str:
        """从步骤数据中提取描述"""
        output = step.get("output", {})
        if isinstance(output, dict):
            # Planner 输出
            if "task_description" in output:
                return output["task_description"]
            # Coder 输出
            if "file_path" in output:
                return f"生成 {output['file_path']}"
            # Reviewer 输出
            if "summary" in output:
                return output["summary"]
        return ""

    def _get_step_icon(self, step: Dict[str, Any]) -> str:
        """获取步骤图标，根据 Agent 类型和输出结果判断"""
        agent = step.get("agent", "")
        status = step.get("status", "").lower()
        output = step.get("output", {})

        # 先检查状态
        if status == "failed":
            return "❌ 失败"

        # Planner 和 Coder 只看状态
        if agent in ("planner", "coder"):
            return self._status_to_icon(status)

        # Reviewer 需要看 passed 字段
        if agent == "reviewer":
            passed = output.get("passed")
            if passed is True:
                return "✅ 通过"
            elif passed is False:
                return "❌ 不通过"
            # 兼容没有 passed 字段的情况
            return self._status_to_icon(status)

        return self._status_to_icon(status)

    def _status_to_icon(self, status: str) -> str:
        """状态转图标"""
        status = status.lower()  # 统一小写处理
        mapping = {
            "started": "🟡 开始",
            "completed": "✅ 完成",
            "failed": "❌ 失败",
            "passed": "✅ 通过",
            "partial_failure": "⚠️ 部分失败",
            "success": "✅ 成功",
            "true": "✅ 通过",
            "false": "❌ 不通过"
        }
        return mapping.get(status, f"❓ {status}")

    def _extract_errors(self, trace_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """从 trace 中提取错误信息"""
        errors = []
        for step in trace_data.get("agent_sequence", []):
            if step.get("status") == "failed":
                errors.append({
                    "message": "步骤执行失败",
                    "file": step.get("target_file", "")
                })
            output = step.get("output", {})
            if isinstance(output, dict):
                issues = output.get("issues", [])
                for issue in issues:
                    if issue.get("severity") == "error":
                        errors.append({
                            "message": issue.get("message", ""),
                            "file": issue.get("file", ""),
                            "suggestion": issue.get("suggestion", "")
                        })
        return errors

    def _extract_generated_files(self, trace_data: Dict[str, Any]) -> List[str]:
        """提取生成的文件列表"""
        files = []
        seen = set()
        for step in trace_data.get("agent_sequence", []):
            if step.get("agent") == "coder":
                output = step.get("output", {})
                if isinstance(output, dict) and "file_path" in output:
                    fp = output["file_path"]
                    if fp and fp not in seen:
                        seen.add(fp)
                        files.append(fp)
        return files


class MemoryStore:
    """经验记忆存储"""

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir or Path(__file__).parent
        self.memory_dir = self.base_dir / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    def save_lesson(self, title: str, content: str, category: str = "general") -> str:
        """保存经验教训"""
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"lessons-{timestamp}.md"
        file_path = self.memory_dir / filename

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"# {title}\n\n")
            f.write(f"**时间**: {datetime.now().isoformat()}\n")
            f.write(f"**分类**: {category}\n\n")
            f.write("---\n\n")
            f.write(content)

        return filename

    def save_pattern(self, name: str, category: str, description: str,
                     applicability: List[str], implementation: Dict[str, Any]) -> None:
        """保存模式"""
        patterns = {}
        patterns_file = self.memory_dir / "patterns.json"

        if patterns_file.exists():
            with open(patterns_file, "r", encoding="utf-8") as f:
                patterns = json.load(f)

        patterns[name] = {
            "name": name,
            "category": category,
            "description": description,
            "applicability": applicability,
            "implementation": implementation,
            "created_at": datetime.now().isoformat(),
            "usage_count": 0
        }

        with open(patterns_file, "w", encoding="utf-8") as f:
            json.dump(patterns, f, indent=2, ensure_ascii=False)

    def get_patterns(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取模式"""
        patterns_file = self.memory_dir / "patterns.json"
        if not patterns_file.exists():
            return []

        with open(patterns_file, "r", encoding="utf-8") as f:
            patterns = json.load(f)

        if category:
            return [p for p in patterns.values() if p.get("category") == category]
        return list(patterns.values())
