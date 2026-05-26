# Note-H5

AI 驱动的软件开发自动化平台 - 基于 Ducc (Claude Code) 的 Harness Engineering 框架

## 项目结构

```
note-h5/
├── 请先读我.md                   # 项目开发规范入口（新成员必读）
├── AGENTS.md                    # Agent 导航地图
├── HARNESS_CONFIG.md            # Harness 配置摘要（Ducc 读取）
├── HARNESS_KEYWORDS.md          # 快捷关键词列表
├── DUCC_PROMPT_TEMPLATE.md      # Ducc 对话模板
├── rules.md                     # React + TypeScript 开发规则
├── docs/
│   ├── ARCHITECTURE.md          # 系统架构文档（三层架构）
│   └── PRODUCT_SENSE.md         # 业务上下文
├── scripts/
│   ├── lint-deps.py             # 依赖层级检查
│   ├── lint-quality.py          # 代码质量检查
│   ├── validate.py              # 统一验证管道
│   └── verify/                  # 端到端验证脚本
├── harness/                     # AI Harness 框架
│   ├── core/                    # 核心模块
│   │   ├── orchestrator.py      # 编排器
│   │   └── auto_executor.py     # 自动执行器
│   ├── prompts/                 # Agent 提示词模板
│   ├── tools/                   # 工具函数
│   ├── tasks/                   # 任务存储
│   ├── trace/                   # 执行追踪
│   ├── memory/                  # 经验存储
│   └── run.py                   # 主入口脚本
├── src/                         # 业务代码
│   ├── pages/                   # 页面组件
│   ├── components/              # 可复用组件
│   ├── hooks/                   # 自定义 Hooks
│   └── api/                     # API 服务
└── .eslintrc.js                 # ESLint 配置
```

## 快速开始

### 1. 在 Ducc 中使用 Harness 配置

每次在 Ducc 中开发时，使用以下方式让 Ducc 遵循项目规范：

**方式一：使用关键词**
```
"按照 harness 规范实现用户登录功能"
```

**方式二：指定配置文件**
```
"请按照 HARNESS_CONFIG.md 中的规范实现用户登录功能"
```

**方式三：对话开头加载配置**
```
你正在为 note-h5 项目开发代码。
项目配置位于：HARNESS_CONFIG.md
请始终按照该配置中的规范进行开发。

现在请实现：[你的需求]
```

### 2. 代码验证

```bash
# 运行所有验证检查
python3 scripts/validate.py

# 单独运行依赖检查
python3 scripts/lint-deps.py

# 单独运行质量检查
python3 scripts/lint-quality.py
```

### 3. 开发运行

```bash
# 安装依赖
npm install

# 开发模式
npm run dev

# 构建
npm run build

# 类型检查
npm run type-check

# ESLint 检查
npm run lint
```

## Harness 配置文件

| 文件 | 说明 |
|------|------|
| [请先读我.md](请先读我.md) | **入口文件** - 新成员必读，快速了解项目规范 |
| [HARNESS_CONFIG.md](HARNESS_CONFIG.md) | **配置摘要** - Ducc 对话时读取此文件 |
| [HARNESS_KEYWORDS.md](HARNESS_KEYWORDS.md) | **快捷关键词** - 可用关键词列表 |
| [DUCC_PROMPT_TEMPLATE.md](DUCC_PROMPT_TEMPLATE.md) | **对话模板** - 开场白示例 |
| [harness/prompts/](harness/prompts/) | Agent 提示词 - Planner/Coder/Reviewer 规范 |

## Harness 框架功能

### 已实现

| 功能 | 说明 |
|------|------|
| **任务管理** | 创建、更新、查询任务状态 |
| **执行追踪** | 记录每个 Agent 的输入输出 |
| **经验存储** | 保存经验教训和代码模式 |
| **自动执行** | 完整的 Planner → Coder → Reviewer 流程 |
| **代码验证** | 依赖检查、质量检查、类型检查 |
| **CLI 工具** | 命令行接口，方便使用 |

### 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    Ducc (AI 处理层)                              │
│                                                                 │
│  Agent 逻辑（规划、编码、审查）由 Ducc 直接处理                   │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│              Harness 框架 (基础设施层)                           │
│                                                                 │
│  AutoExecutor → Orchestrator → TaskManager/TraceRecorder        │
│  CodeValidator → lint-deps/lint-quality/validate                │
└─────────────────────────────────────────────────────────────────┘
```

## 三层架构

本项目采用三层架构：

```
Component (pages/, components/) → Hook (hooks/) → Service (api/)
```

- **Component Layer**: UI 渲染、用户交互
- **Hook Layer**: 业务逻辑、状态管理
- **Service Layer**: 数据获取、API 调用

## 文档

- [Agent 导航地图](AGENTS.md) - 了解 Agent 角色和协作流程
- [系统架构](docs/ARCHITECTURE.md) - 理解分层架构和依赖规则
- [开发规则](rules.md) - React + TypeScript 开发规范

## License

MIT