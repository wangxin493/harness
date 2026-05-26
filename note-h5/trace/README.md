# 执行追踪目录

此目录用于存储 Harness 系统的执行追踪记录。

## 文件格式

每次执行生成一个 JSON 文件，命名格式：`trace-{trace_id}.json`

## 追踪数据结构

```json
{
  "trace_id": "uuid",
  "task_id": "uuid",
  "timestamp": "ISO时间戳",
  "agent_sequence": [
    {
      "agent": "planner|coder|reviewer",
      "input": {},
      "output": {},
      "status": "completed|failed",
      "timestamp": "ISO时间戳"
    }
  ],
  "status": "started|success|failed",
  "ended_at": "ISO时间戳"
}
```

## 用途

- 记录完整的执行流程
- 用于调试和问题排查
- 分析 Agent 协作效率

## 管理

- 每次执行自动创建
- 可通过日志查看执行详情
- 定期清理历史追踪记录
