# Harness

Harness 是一个面向前端项目的代码治理框架，提供项目探测、分层架构校验、导入边界校验、命名规范、Agent Hook 接入和增量扫描能力。

当前仓库保留原本的本地路径 `note-h5`，但内容已作为 harness 工程维护，不再承载业务 demo 应用。

## 目录结构

```text
.harness/
├── lib/          # 核心 Python 实现：probe / init / scan / validate / doctor / fix 等
├── tests/        # harness 回归测试
├── docs/         # 接入指南、设计记录和试点记录
├── hooks/        # Agent PostToolUse 等 hook 模板
├── templates/    # harness new 使用的新文件模板
├── commands/     # 当前本地 CLI 入口
├── rules.yaml    # harness 自身示例规则
└── requirements.txt
```

## 本地开发

```bash
cd .harness
.venv/bin/python -m pytest tests/ -q
```

根目录也提供等价脚本：

```bash
npm test
```

## 当前接入方式

在 npm 包化完成前，接入业务项目仍使用复制 `.harness/` 骨架的方式，详见 `.harness/docs/onboarding-guide.md`。

后续目标是发布为 npm 包：核心能力由 `@befe/harness` 提供，业务项目只保留 `.harness/rules.yaml`、`.harness/context/`、`.harness/generated/` 和 Agent Hook 配置。
