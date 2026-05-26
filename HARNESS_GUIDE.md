# Harness 使用指南

本项目集成了 Harness 自动化开发系统，通过阿里百炼 qwen-max 模型实现需求到代码的自动化流程。

## 🚀 快速开始

### 方式 1: 命令行（推荐）

```bash
# 在项目根目录
./harness.sh "实现用户登录功能"
```

### 方式 2: VSCode 集成

按 `Cmd+Shift+P` (Mac) 或 `Ctrl+Shift+P` (Windows/Linux)，然后选择：
- `Tasks: Run Task` → `Harness: 执行需求`
- 输入你的需求描述

### 方式 3: npm scripts

```bash
cd note-h5
npm run harness "创建笔记列表组件"
npm run harness:status   # 查看系统状态
npm run harness:tasks    # 查看任务列表
npm run harness:validate # 验证代码
```

### 方式 4: 交互式模式

```bash
python3 harness_cli.py
```

进入交互式模式后，直接输入需求即可：
```
>>> 创建用户设置页面
>>> 实现笔记搜索功能
>>> exit  # 退出
```

### 方式 5: 直接调用 Python

```bash
venv312/bin/python3 note-h5/harness/run.py "你的需求"
```

## 📋 支持的命令

| 命令 | 说明 |
|------|------|
| `"需求描述"` | 执行开发任务 |
| `--status` | 查看系统状态 |
| `--list-tasks` | 查看任务列表 |
| `--validate` | 验证代码质量 |
| `--direct` | 模拟模式（不调用 API） |

## 🎯 工作流程

```
用户需求 → Planner 生成计划 → Coder 编写代码 → Reviewer 审查代码 → 完成
```

## 📁 生成的文件

执行后会生成以下记录：
- `tasks/task-*.json` - 任务记录
- `trace/trace-*.json` - 执行追踪
- `src/` - 生成的代码文件

## 📊 查看执行结果

### 查看最后一次执行（推荐）

```bash
./trace-view.sh
```

**输出示例：**
```
============================================================
📊 任务执行报告
============================================================

任务: 增加置顶笔记的功能
开始时间: 18:54:59
总耗时: 1分15秒

最终状态: ❌ 失败

执行过程:
1. 📋 规划师 - 18:55:11 (耗时: 11秒)
   计划: 拆分成 4 个步骤
      • 创建置顶笔记的 API
      • 创建 usePinnedNotes Hook
      • 更新笔记列表组件以支持置顶功能
      • 审查代码质量

2. 💻 程序员 - 18:55:31 (耗时: 19秒)
   生成了: src/api/pinnedNotes.ts (1426 字符)

3. 💻 程序员 - 18:55:42 (耗时: 11秒)
   生成了: src/hooks/usePinnedNotes.ts (1347 字符)

4. 💻 程序员 - 18:55:58 (耗时: 15秒)
   生成了: src/components/NoteList.tsx (1233 字符)

5. 🔍 审查员 - 18:56:14 (耗时: 16秒)
   审查未通过 ❌ 评分: 70 分
   发现了 5 个问题:
   🔴 [类型安全] 缺少类型导入...
   💡 建议: 添加 `import { Note } from '@/types/note';`
```

**详细解读请查看：** [TRACE_README.md](TRACE_README.md)

## 🔧 配置

API 配置位于 `note-h5/harness/.env`：
```bash
DASHSCOPE_API_KEY=sk-xxx
DASHSCOPE_MODEL=qwen-max
```

## 💡 使用示例

```bash
# 创建组件
./harness.sh "创建一个用户头像组件"

# 实现功能
./harness.sh "实现笔记的增删改查功能"

# 添加页面
./harness.sh "创建用户设置页面"

# 查看状态
./harness.sh --status

# 查看历史任务
./harness.sh --list-tasks
```
