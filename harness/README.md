# AI Harness - 智能开发自动化框架

## 概述

AI Harness 是一个基于 AI Agent 的代码自动生成框架，通过 Planner、Coder、Reviewer 三个 Agent 协作，自动完成需求分析、代码生成、审查修复的完整流程。

## 快速开始

### 运行命令

```bash
# 进入项目目录
cd note-h5

# 执行任务（使用 LLM API）
./harness.sh "你的需求描述"

# 或直接运行
python3.12 harness/run.py "你的需求描述"

# 查看系统状态
python3.12 harness/run.py --status
```

### 工作流程

```
用户需求
    ↓
┌─────────┐
│ Planner │  分析需求，生成执行计划
└─────────┘
    ↓
┌─────────┐
│  Coder  │  根据计划生成代码
└─────────┘
    ↓
┌──────────┐
│ Reviewer │  审查代码，检测问题
└──────────┘
    ↓
   编译验证
    ↓
  成功/失败
```

## 配置说明

### config.yaml

```yaml
# Agent 配置
agents:
  planner:
    max_retries: 3      # Planner 最大重试次数
  coder:
    max_retries: 3      # Coder 最大重试次数
  reviewer:
    max_retries: 2      # Reviewer 最大重试次数

# LLM 配置
llm:
  provider: "deepseek"  # LLM 提供商
  model: "deepseek-coder"  # 模型名称
```

### 环境变量

```bash
# 设置 DeepSeek API Key
export DEEPSEEK_API_KEY=your-api-key

# 或设置阿里百炼 API Key
export DASHSCOPE_API_KEY=your-api-key
```

## 三层架构

Harness 遵循三层架构规范：

| 层级 | 目录 | 职责 |
|------|------|------|
| Component | pages/, components/ | UI 渲染、用户交互 |
| Hook | hooks/ | 业务逻辑、状态管理 |
| Service | api/ | 数据获取、API 调用 |

### 依赖规则

- Component → Hook, Service ✅
- Hook → Service ✅
- Hook → Component ❌
- Service → Hook, Component ❌

## 输出文件

### trace/ 目录

记录完整执行链路，查看方式：

```bash
# 最新执行记录
ls -lt trace/ | head -5

# 查看执行详情
cat trace/trace-{id}.json | python3 -m json.tool
```

### memory/ 目录

保存执行经验教训，供后续执行参考。

### tasks/ 目录

记录任务状态和检查点。

## 常见问题

### Q: 为什么 Reviewer 拒绝了我的代码？

A: Reviewer 按照以下规则审查：
- TypeScript 类型安全（禁止 any）
- React 规范（函数组件、命名导出）
- 三层架构依赖（不能跨层依赖）
- 字段名与 @/types/index.ts 一致

### Q: 如何查看当前任务执行到了哪一步？

A: 查看 trace/trace-{id}.json 中的 agent_sequence 字段，按顺序记录了每个 Agent 的执行。

### Q: 执行失败了怎么办？

A:
1. 查看 trace/trace-{id}.json 的 status 字段
2. 查看 errors 字段了解错误原因
3. 查看 memory/lessons-*.md 学习之前类似问题的解决方案

### Q: 如何让 Harness 记住执行经验？

A: Harness 自动将失败经验保存到 memory/lessons-*.md，下次执行时会自动注入到提示词中。

## 目录结构

```
harness/
├── config.yaml           # 配置文件
├── components_schema.json  # 组件 Schema 约束
├── run.py               # CLI 入口
├── run.sh               # Shell 启动脚本
├── llm_client.py       # LLM 客户端
├── harness.py           # 数据模型
├── core/
│   ├── auto_executor.py  # 自动执行引擎
│   └── orchestrator.py   # 编排器
├── prompts/
│   ├── planner_system.txt   # Planner 提示词
│   ├── coder_system.txt      # Coder 提示词
│   └── reviewer_system.txt    # Reviewer 提示词
├── tools/
│   ├── code_scanner.py   # 代码扫描器
│   ├── validator.py      # 代码验证器
│   └── utils.py          # 工具函数
├── trace/                # 执行追踪（见 trace/README.md）
├── memory/                # 经验教训
└── tasks/                # 任务状态
```