# 任务存储目录

此目录用于存储 Harness 系统的任务记录。

## 文件格式

每个任务存储为一个 JSON 文件，命名格式：`task-{task_id}.json`

## 任务数据结构

```json
{
  "task_id": "uuid",
  "status": "pending|in_progress|completed|failed",
  "priority": "P0|P1|P2|P3",
  "agent": "orchestrator",
  "created_at": "ISO时间戳",
  "updated_at": "ISO时间戳",
  "checkpoints": [...],
  "description": "任务描述",
  "input_data": {},
  "output_data": {}
}
```

## 管理

- 任务完成后自动保存
- 可通过 `./harness.sh --list-tasks` 查看所有任务
- 可通过 `./harness.sh --status` 查看系统状态
