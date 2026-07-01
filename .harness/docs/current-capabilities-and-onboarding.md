# Harness 当前功能与新老项目接入逻辑

> 更新时间：2026-07-01  
> 适用范围：Harness 2.0 当前实现（Claude / Ducc / baidu-cc 适配）

---

## 1. Harness 现在解决什么问题

Harness 是一套放在项目内的代码治理框架，目标不是替代业务框架，而是给 Agent 和团队提供统一的工程约束入口。

核心职责：

| 职责 | 说明 |
|---|---|
| 架构规则治理 | 用 `rules.yaml` 定义分层、可导入关系、允许别名、命名规则 |
| Agent 上下文生成 | 根据扫描结果生成 `.harness/generated/*.md`，供 Claude / Ducc / Comate 等 Agent 读取 |
| 编辑后自动校验 | PostToolUse hook 在 Agent 修改代码后自动触发 `harness validate` |
| 经验沉淀 | 本地/团队 lesson 进入生成文档和 hook 注入链路 |
| 接入体检 | `harness doctor` 检查依赖、规则、生成物、hook 配置是否同步 |
| 渐进治理 | `strict / relaxed / off` 三档治理力度，支持老项目低风险接入 |
| 模板新建 | `harness new` 按规则生成 component / page / hook / service / type 文件 |
| 局部自动修复 | `harness fix --apply` 支持 `import-forbidden` 子集修复 |

---

## 2. 当前核心文件

| 文件/目录 | 作用 |
|---|---|
| `.harness/rules.yaml` | 团队共享的治理规则，是核心配置 |
| `.harness/lib/` | Harness CLI、扫描器、验证器、生成器、安装器等实现 |
| `.harness/hooks/` | Agent PostToolUse hook 脚本 |
| `.harness/context/` | 扫描缓存、依赖图、项目上下文 |
| `.harness/generated/` | 生成给不同 Agent 读取的规则说明 |
| `.harness/memory/lessons/` | 本项目经验教训 |
| `CLAUDE.md` | 项目根索引，引用 `.harness/generated/claude.md` |

提交策略：

| 应提交 | 不应提交 |
|---|---|
| `.harness/rules.yaml` | `.harness/.venv/` |
| `.harness/lib/` | `.harness/context/` |
| `.harness/hooks/` | 临时 probe 输出 |
| `.harness/docs/` | 本地缓存文件 |
| `CLAUDE.md` 托管块 | 个人环境产物 |

---

## 3. 当前命令能力

### 3.1 接入与初始化

| 命令 | 作用 |
|---|---|
| `harness probe --json` | 只读探测项目 facts，供 Agent 起草 `rules.yaml` |
| `harness init` | 旧链路：probe -> resolver -> 交互选择 -> 写 `rules.yaml` |
| `harness init --yes` | 旧链路：所有 conflict 走默认值，非交互写盘 |
| `harness init --rules <file>` | 新链路：写入 Agent 起草好的 `rules.yaml` |
| `harness setup --agent <agent>` | 一键执行旧链路 init + install + scan |
| `harness install --agent <agent>` | 安装 Agent hook 和根目录托管块 |
| `harness uninstall --agent <agent>` | 卸载 Agent hook 和托管块 |

### 3.2 扫描与生成

| 命令 | 作用 |
|---|---|
| `harness scan` | 扫描源码，生成 context 和 Agent 文档 |
| `harness scan --full` | 忽略增量缓存，强制全量扫描 |
| `harness generate` | 根据当前 rules/context/lessons/mode 重新生成文档和 hook config |

### 3.3 校验与治理

| 命令 | 作用 |
|---|---|
| `harness validate <file>` | 验证单文件 |
| `harness check` | 全局检查，例如依赖循环/全局架构问题 |
| `harness doctor` | 体检环境、依赖、规则、生成物漂移 |
| `harness mode` | 查看当前治理模式 |
| `harness mode strict` | 严格拦截 |
| `harness mode relaxed` | 放宽治理，适合老项目过渡 |
| `harness mode off` | 暂停治理，适合接入前整理 |

### 3.4 辅助能力

| 命令 | 作用 |
|---|---|
| `harness fix <file> --apply` | 自动修复部分 import 违规 |
| `harness new <kind> <name>` | 按规则生成新文件骨架 |
| `harness lesson add` | 添加本地经验 |
| `harness sync` | 同步团队经验市场 |

---

## 4. rules.yaml 起草职责的新逻辑

当前已经支持两条链路：

| 链路 | 适合场景 | 特点 |
|---|---|---|
| 旧链路：`init/setup` 自动起草 | demo、新项目、目录结构标准 | 快速，但 resolver 仍会做一部分语义推断 |
| 新链路：Agent 起草 | 老项目、复杂项目、需要准确架构判断 | 工具只给 facts，Agent 结合上下文和用户确认起草 |

推荐方向是新链路：

```bash
harness probe --json > /tmp/probe.json
# Agent 读取 /tmp/probe.json、项目结构、用户说明后起草 /tmp/rules.yaml
harness init --rules /tmp/rules.yaml
harness scan
harness doctor
```

关键原则：

- `probe` 只输出事实，不替用户判断项目架构。
- Agent 起草 `rules.yaml` 前，遇到不确定的层归属必须问用户。
- `init --rules` 只负责读取 YAML、写入 `.harness/rules.yaml` 和初始化必要目录。
- 旧 `init` / `setup` 暂时保留，保证已有接入方式不破坏。

---

## 5. 新项目接入逻辑

新项目特点：目录结构清晰、存量代码少、可以尽早上规则。

### 推荐路径 A：Agent 起草链路

```bash
# 1. 只读探测 facts
harness probe --json > /tmp/harness-probe.json

# 2. Agent 根据 facts 起草 rules.yaml
# 输出到 /tmp/harness-rules.yaml

# 3. 写入规则
harness init --rules /tmp/harness-rules.yaml

# 4. 安装 Agent hook
harness install --agent ducc

# 5. 扫描并生成 Agent 文档
harness scan

# 6. 体检
harness doctor
```

适合：正式项目、希望规则一开始就贴合业务架构。

### 快速路径 B：一键 setup

```bash
harness setup --agent ducc
```

适合：实验项目、标准 `src/components + src/hooks + src/api + src/types` 结构、可接受默认推断。

接入后必须确认：

```bash
harness scan --json
harness doctor
```

重点看：

- `total_files` 是否大于 0。
- `architecture.layers` 是否覆盖实际源码目录。
- `scanner.source_root` 是否指向真正前端源码根。
- `.harness/generated/claude.md` 是否已生成。

---

## 6. 老项目接入逻辑

老项目特点：目录历史包袱多、命名不统一、层边界不清晰、不能一上来严格拦截。

推荐步骤：

### 6.1 先关闭治理

```bash
harness mode off
```

目的：先完成接入和 baseline 梳理，不要因为规则未对齐误拦截开发。

### 6.2 只读探测

```bash
harness probe --json > /tmp/harness-probe.json
```

Agent 要重点分析：

| facts | 判断点 |
|---|---|
| `aliases` | tsconfig 别名是否和项目实际 import 一致 |
| `layers` | 哪些目录是业务层，哪些只是资源/工具目录 |
| `naming` | 当前命名风格是否值得治理，还是先关闭某些命名规则 |
| `notes` | 是否有 `dual-stack-hint` 这类 source_root 提示 |
| `frameworks` | React/Vue 等框架决定 hook/page/component 约束方式 |

### 6.3 Agent 起草 rules.yaml

老项目建议保守：

- `scanner.source_root` 只指向前端主源码，不要扫 backend/server。
- 暂时排除 `dist/build/node_modules/.git` 等目录。
- 对历史混乱目录，不确定就先不纳入层，或单独问用户。
- `naming` 可以先关闭或采用现状，避免大面积噪音。
- `mode` 先用 `relaxed`，等 baseline 收敛后再切 `strict`。

### 6.4 写盘 + 扫描 + 体检

```bash
harness init --rules /tmp/harness-rules.yaml
harness scan
harness doctor
```

### 6.5 渐进收紧

```bash
harness mode relaxed
harness check
# 修完明显架构问题后
harness mode strict
```

老项目不要一次性追求零违规。先保证：

1. `source_root` 正确。
2. 新增代码能被约束。
3. 存量噪音不阻塞日常开发。
4. 架构层定义能被团队理解和维护。

---

## 7. setup 与 init --rules 的边界

| 问题 | 结论 |
|---|---|
| `setup` 是否废弃 | 不废弃，保留为快速接入路径 |
| 新项目是否必须用 `probe --json` | 不必须，但正式项目推荐使用 |
| 老项目是否应该用 `setup --interactive` | 可以，但更推荐 Agent 起草链路 |
| `init --rules` 是否会安装 hook | 不会，只写 rules 和必要目录 |
| `init --rules` 后还要做什么 | 运行 `install` 和 `scan` |
| 已有 rules.yaml 会不会被覆盖 | 会提示确认，默认不覆盖 |

---

## 8. 推荐 Agent 操作准则

Agent 在接入项目时应遵守：

1. 先读 `harness probe --json`，不要直接猜规则。
2. 结合项目代码、目录、import 和用户说明起草 `rules.yaml`。
3. 对不确定的层归属、source_root、命名规则，先问用户。
4. 不要把资源目录、构建产物、后端目录纳入前端治理层。
5. 写盘后必须跑 `harness scan` 和 `harness doctor`。
6. 老项目默认从 `mode off/relaxed` 开始，避免误拦截。

---

## 9. 推荐命令速查

```bash
# 只读探测 facts
harness probe --json

# Agent 起草后写入规则
harness init --rules /tmp/harness-rules.yaml

# 快速接入（旧链路）
harness setup --agent ducc

# 安装 hook
harness install --agent ducc

# 扫描 + 生成上下文
harness scan

# 体检
harness doctor

# 切换治理模式
harness mode off
harness mode relaxed
harness mode strict

# 验证单文件
harness validate src/components/Foo.tsx

# 全局检查
harness check
```
