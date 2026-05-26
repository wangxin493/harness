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
        return True


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
