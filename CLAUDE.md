# Harness 工程说明

当前仓库用于开发和维护 harness 本体，不是接入 harness 的业务项目。

## 关键目录

- `.harness/lib/`：核心实现，包括 CLI、扫描、校验、初始化、修复和 Agent 接入逻辑。
- `.harness/tests/`：回归测试，使用临时项目 fixture，不依赖根目录业务代码。
- `.harness/docs/`：接入指南、设计说明和试点记录。
- `.harness/hooks/`：安装到业务项目的 Agent hook 模板。
- `.harness/templates/`：`harness new` 使用的新文件模板。

## 工作流

- 修改 `.harness/lib/**/*.py` 后，优先运行 `cd .harness && .venv/bin/python -m pytest tests/ -q`。
- 不要为了绕过验证自行执行 `harness mode relaxed/off`；只有用户明确要求调整治理策略时才切换模式。
- 不要把业务 demo 代码重新放回根目录；根目录只保留 harness 工程元信息和文档。
- 当前阶段先保留目录名 `note-h5` 和本地路径不变，npm 包化结构改造后再考虑重命名。
