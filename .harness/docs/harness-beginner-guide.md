# Harness 入门指南（从零开始）

> 面向第一次接触 Harness 的人。不需要你提前了解任何背景，从头读即可。

---

## Harness 是什么

Harness 是一个放在你项目里的**代码治理工具**，它帮你和你的 AI 编程助手（Claude / Ducc 等）共同遵守一套工程规则。

你可以把它想象成项目的"规则执行员"：

- **规则从哪来**：你在 `.harness/rules.yaml` 里定义，比如"service 层不能直接 import component 层"、"所有 hook 文件名必须以 `use` 开头"。
- **谁来执行**：Harness CLI（命令行工具）和挂在 Claude/Ducc 上的 hook 脚本。
- **什么时候执行**：每次 Agent 修改了 `.ts` / `.tsx` 文件之后，自动检查有没有违规。违规就拦截，让 Agent 自己改好。

---

## 第一步：搞清楚目录结构

装上 Harness 之后，你的项目会多出一个 `.harness/` 目录：

```
.harness/
├── commands/
│   └── harness           ← 命令行入口，所有 harness 命令从这里跑
├── lib/                  ← Harness 的核心代码（不用改）
├── hooks/                ← 挂在 Claude/Ducc 上的 hook 脚本
├── rules.yaml            ← ⭐ 你的规则定义，唯一需要认真维护的文件
├── context/              ← 扫描缓存（自动生成，不用提交 git）
├── generated/            ← 给 Agent 读的规则说明（自动生成，不用手改）
├── memory/
│   └── lessons/          ← 团队经验教训（可选，用 harness lesson add 添加）
├── .venv/                ← Python 虚拟环境（不用提交 git）
└── docs/                 ← Harness 自身的文档（你现在在看的这份也在里面）
```

**哪些要提交 git，哪些不要：**

| 要提交 ✅ | 不要提交 ❌ |
|---|---|
| `rules.yaml` | `.venv/` |
| `lib/` | `context/` |
| `hooks/` | `generated/`（可选，通常不提交） |
| `docs/` | 临时 probe 输出文件（`/tmp/` 下的） |
| `CLAUDE.md` 里的托管块 | |

---

## 第二步：Harness 有哪些命令

先看全局：

```bash
.harness/commands/harness --help
```

命令分四类，下面逐一解释。

---

### 第一类：接入（第一次用时跑）

#### `harness probe --json`

**作用**：只读地扫一遍你的项目，输出"项目现状 facts"。不改任何文件。

```bash
.harness/commands/harness probe --json
```

输出是一份 JSON，包含：
- `aliases`：tsconfig 里的路径别名（如 `@/` → `src/`）
- `layers`：发现了哪些目录（`components/`、`hooks/`、`api/` 等）
- `naming`：每个目录的命名风格统计（PascalCase 占几成、驼峰占几成）
- `frameworks`：检测到 React / Vue 等
- `notes`：特殊提示，比如 `dual-stack-hint:src/frontend`（说明真正的前端代码在 `src/frontend/` 下，不是 `src/` 根）
- `sub_layer_convention_hint`：在 unknown 层目录里发现的约定子目录（如 `module/**/components/` 里的 `components`），Agent 起草规则时应直接写入 `architecture.sub_layer_convention`

**什么时候用**：接入新项目时，让 Agent 先跑这个，再起草 `rules.yaml`。

---

#### `harness init --rules <file>`

**作用**：把一份写好的 `rules.yaml` 写进 `.harness/rules.yaml`，同时初始化必要的目录结构。

```bash
.harness/commands/harness init --rules /tmp/my-rules.yaml
```

注意：这个命令**只写规则文件**，不装 hook，不跑扫描。做完之后还要跑 `install` 和 `scan`。

**什么时候用**：Agent 根据 `probe --json` 的输出帮你起草好 `rules.yaml` 之后，用这个命令写进去。

---

#### `harness init` / `harness init --yes`

**作用**：旧链路。自动探测 + 交互式协商 + 写盘。

```bash
.harness/commands/harness init           # 有冲突时弹问题让你选
.harness/commands/harness init --yes     # 所有冲突走默认值，不问你
```

**什么时候用**：项目目录结构标准（`src/components`、`src/hooks`、`src/api`、`src/types`），想快速跑完不手动起草 `rules.yaml` 的场景。复杂项目不推荐这个，容易误判。

---

#### `harness setup --agent <agent>`

**作用**：一键接入，等价于 `init --yes` + `install` + `scan` 三步合一。

```bash
.harness/commands/harness setup --agent ducc
# 或
.harness/commands/harness setup --agent claude
# 或
.harness/commands/harness setup --agent baidu-cc
```

**什么时候用**：全新项目、目录结构标准、不想手动分步做的场景。

---

#### `harness install --agent <agent>`

**作用**：把 Harness 的 hook 装进 Claude/Ducc 的配置文件（`.claude/settings.json`），同时在项目根 `CLAUDE.md` 里注入引用块。

```bash
.harness/commands/harness install --agent ducc
```

装完之后，每次 Agent 修改 `.ts` / `.tsx` 文件，就会自动触发 `harness validate`。

幂等：反复跑不会重复装，已有的 hook 会被识别后更新。

---

#### `harness uninstall --agent <agent>`

**作用**：卸载 Harness 的 hook 和 CLAUDE.md 托管块。不影响第三方 hook。

```bash
.harness/commands/harness uninstall --agent ducc
```

---

### 第二类：扫描和生成（有代码变动时跑）

#### `harness scan`

**作用**：扫描 `rules.yaml` 里 `scanner.source_root` 指向的目录，产出：
- `.harness/context/project-context.json`（各文件的元信息）
- `.harness/context/dependency-graph.json`（文件间依赖关系）
- 自动触发 `generate`，刷新 `.harness/generated/claude.md`

```bash
.harness/commands/harness scan           # 增量扫描（只看变动的文件）
.harness/commands/harness scan --full    # 强制全量扫描
.harness/commands/harness scan --json    # 以 JSON 输出扫描摘要
```

**什么时候用**：
- 接入后第一次跑
- 修改了 `rules.yaml`（层定义变了、别名变了）之后
- 想刷新 Agent 读到的规则说明时

> 每次 `scan` 之后会自动触发 `generate`，不用手动跑。

---

#### `harness generate`

**作用**：根据当前 `rules.yaml`、扫描缓存、经验教训、治理模式，重新生成 `.harness/generated/claude.md`（以及 ducc.md、comate.md）。

```bash
.harness/commands/harness generate
```

`generate` 不重新扫描源码，只是把已有的信息重新渲染成 Agent 可读的格式。

**什么时候用**：修改了 `rules.yaml` 或 `memory/lessons/` 但没改源码时，用这个比 `scan` 快。

---

### 第三类：校验（日常会用到）

#### `harness validate <file>`

**作用**：对单个文件跑规则检查，输出有哪些违规。

```bash
.harness/commands/harness validate src/hooks/useData.ts
.harness/commands/harness validate src/components/UserCard.tsx --json
```

会检查：
- **架构层违规**：比如 service 层 import 了 component 层
- **禁用 import**：import 了 `rules.yaml` `imports.forbidden_imports` 里列出的路径
- **hook 调用位置**：React hook 在非 hook 函数里调用（`sub_layer_convention` 配置后，模块内局部 `components/` 里的文件会被正确识别为 component 层，不再误报）
- **命名规范**：文件名不符合该层要求的命名风格

---

#### `harness validate-all [PATH]`

**作用**：批量验证全项目（或指定子目录）里所有已扫描文件，把问题汇总输出。

```bash
.harness/commands/harness validate-all                          # 验全部
.harness/commands/harness validate-all src/frontend/service     # 只验指定子目录
.harness/commands/harness validate-all --json                   # JSON 输出
```

**什么时候用**：接入后想看整体 baseline 有多少违规时。先 `scan` 再 `validate-all`。

---

#### `harness check`

**作用**：跑"需要看全图才能发现"的问题：
- **循环依赖**（比如 A import B、B import A）
- **死代码**（文件有导出但没有任何地方 import 它，entry points 除外）

```bash
.harness/commands/harness check
.harness/commands/harness check --json
.harness/commands/harness check --no-unused    # 只查循环依赖，不查死代码
```

> 需要先跑过 `harness scan` 才有数据。

---

#### `harness doctor`

**作用**：全面体检，检查 Harness 的接入状态是否健康：

- Python 和依赖版本是否 OK
- `rules.yaml` 是否合法、architecture.layers 是否有配置
- 生成的 `generated/claude.md` 是否和当前规则同步（有无漂移）
- hook 是否正确安装在 Agent 配置里
- 上次扫描时间是否过旧

```bash
.harness/commands/harness doctor
```

**什么时候用**：接入后验收、出现奇怪问题排查时。目标是"0 error / 0 warning"。

---

#### `harness status`

**作用**：快速查看当前 Harness 状态：版本、治理模式、上次扫描时间和扫描文件数。

```bash
.harness/commands/harness status
```

---

### 第四类：治理模式和工具

#### `harness mode`

**作用**：查看或切换治理力度，有三档：

| 模式 | 含义 | 适合场景 |
|---|---|---|
| `strict` | 拦截所有 error + warning + info | 新项目、规则已稳定的项目 |
| `relaxed` | 只拦截 error，warning/info 放行 | 老项目过渡期 |
| `off` | 完全关闭拦截（但 validate 仍会打印 ⚠️ 提示） | 接入前整理期、紧急调试 |

```bash
.harness/commands/harness mode            # 查看当前模式
.harness/commands/harness mode strict     # 切到 strict
.harness/commands/harness mode relaxed    # 切到 relaxed
.harness/commands/harness mode off        # 暂停拦截
.harness/commands/harness mode toggle     # 循环切换 strict→relaxed→off→strict
```

> 切换会写进 `.harness/mode-config.json`，建议提交 git 或在 PR 里说明。

---

#### `harness fix <file> --apply`

**作用**：对单个文件自动修复 `import-forbidden` 类型的违规（把废弃路径改成新路径）。

```bash
.harness/commands/harness fix src/components/UserCard.tsx          # dry-run：只打印要做什么
.harness/commands/harness fix src/components/UserCard.tsx --apply  # 真正修改文件
```

注意：
- 只能修复 `import-forbidden` 这一类，不是通用 autofix。
- `--apply` 前会自动做 git stash 备份，非 git 仓库会拒绝执行。
- 默认 dry-run（不加 `--apply` 只打印不动文件）。

**修复的前提**：`rules.yaml` 里 `imports.rewrites` 写了旧路径 → 新路径的映射，`fix` 才知道怎么改。

---

#### `harness new <kind> <name>`

**作用**：按规则生成一个新文件的骨架，文件名、路径、内容模板都符合当前 `rules.yaml` 的规范。

```bash
.harness/commands/harness new component UserCard      # → src/components/UserCard.tsx
.harness/commands/harness new page TodoListPage       # → src/pages/TodoListPage.tsx
.harness/commands/harness new hook useTodos           # → src/hooks/useTodos.ts
.harness/commands/harness new service userService     # → src/api/userService.ts
.harness/commands/harness new type User               # → src/types/User.ts

# 自定义目录（覆盖 rules.yaml 的默认路径）
.harness/commands/harness new component UserCard --path src/widgets/

# 同名文件已存在时强制覆盖
.harness/commands/harness new component UserCard --force
```

执行成功后自动刷新 `generated/claude.md`（相当于帮你跑了 `generate`）。

---

#### `harness lesson add`

**作用**：把一条团队经验写入 `.harness/memory/lessons/`，之后 Agent 修改相关代码时会自动注入这条经验。

```bash
.harness/commands/harness lesson add
# 按提示输入标题、内容即可
```

其他子命令：

```bash
.harness/commands/harness lesson list          # 列出所有本地经验
.harness/commands/harness lesson show <id>     # 查看某条经验全文
.harness/commands/harness lesson remove <id>   # 删除某条经验
.harness/commands/harness lesson match --file <path>  # 看某个文件会召回哪些经验
```

---

#### `harness sync`

**作用**：从团队共享目录（`.harness-shared/lessons/`）把经验同步到本地（latest-wins）。

```bash
.harness/commands/harness sync
```

> 远程 git 同步目前是 P2 占位，只支持本地共享目录。

---

## 第三步：两种接入路径选哪个

**简单判断**：

```
项目目录结构标准？（src/components + src/hooks + src/api + src/types 都有）
├── 是 → 快速路径 B（harness setup 一键搞定）
└── 否 → 推荐路径 A（Agent 起草 rules.yaml）
```

### 路径 A：Agent 起草规则（推荐，适合正式项目或老项目）

```bash
# 1. 只读探测，产出项目 facts
.harness/commands/harness probe --json > /tmp/harness-probe.json

# 2. 把 /tmp/harness-probe.json 给 Agent，让它根据 facts 和项目代码起草 rules.yaml
#    （不确定的层归属和 source_root，Agent 会先问你）
#    Agent 把起草好的内容输出到 /tmp/harness-rules.yaml

# 3. 把起草好的规则写进 .harness/rules.yaml
.harness/commands/harness init --rules /tmp/harness-rules.yaml

# 4. 安装 Agent hook（让 Agent 编辑代码后自动触发验证）
.harness/commands/harness install --agent ducc   # 或 claude / baidu-cc

# 5. 扫描项目，生成 Agent 可读的规则说明
.harness/commands/harness scan

# 6. 体检，确认接入状态正常
.harness/commands/harness doctor
```

### 路径 B：一键 setup（适合结构标准的新项目）

```bash
.harness/commands/harness setup --agent ducc
```

跑完之后**必须**确认：

```bash
.harness/commands/harness scan --json    # 看 total_files 是否 > 0
.harness/commands/harness doctor         # 看有没有 error 或 warning
```

---

## 第四步：老项目特别注意

老项目存量代码多，规则一上来就 strict 大概率出一堆噪音。标准做法：

```bash
# 1. 先关治理
.harness/commands/harness mode off

# 2. 探测 + Agent 起草（不清楚的层归属一定问用户，不要自己猜）
.harness/commands/harness probe --json > /tmp/harness-probe.json
# ... Agent 起草 /tmp/harness-rules.yaml，命名规则用 adopt:<style> ...

# 3. 写盘 + 安装 + 扫描
#    首次 scan 会自动生成 .harness/context/naming-baseline.json
#    把当前所有 adopt:* 命名违规固定为存量快照，之后只报新增
.harness/commands/harness init --rules /tmp/harness-rules.yaml
.harness/commands/harness install --agent ducc
.harness/commands/harness scan

# 4. 看 baseline（有多少违规，性质是什么）
.harness/commands/harness validate-all --json | head -50
.harness/commands/harness check --json

# 5. 规则稳定后渐进收紧
.harness/commands/harness mode relaxed   # 先只拦 error
.harness/commands/harness mode strict    # 确定没噪音了再全拦
```

> 如果后来又调整了 `rules.yaml naming.*`，需要重新固定存量快照：
> ```bash
> harness scan --reset-baseline
> ```

老项目的目标不是一次性零违规，而是：
1. `source_root` 指对了，能扫到文件。
2. 新增代码能被拦截。
3. 存量噪音可控，不影响日常开发。

---

## 快速参考：常见场景命令

| 场景 | 命令 |
|---|---|
| 接入新项目（推荐） | `probe --json` → Agent 起草 → `init --rules` → `install` → `scan` → `doctor` |
| 接入新项目（快速） | `setup --agent ducc` → `scan --json` → `doctor` |
| 修改了 rules.yaml | `harness scan` |
| 只改了 lessons | `harness generate` |
| 检查某个文件 | `harness validate src/xxx.ts` |
| 检查全项目 | `harness validate-all` |
| 检查循环依赖 | `harness check` |
| 接入后体检 | `harness doctor` |
| 查看当前模式 | `harness mode` |
| 临时关闭拦截 | `harness mode off` |
| 新建一个 hook 文件 | `harness new hook useMyHook` |
| 自动修复 import 路径 | `harness fix <file> --apply` |
| 添加团队经验 | `harness lesson add` |
| 看 Harness 状态 | `harness status` |

---

## 最常见的坑

**坑1：`validate-all` 输出 0 条问题，但不是因为代码好**  
原因：治理模式是 `off`。  
解决：`harness mode strict`，再跑一遍。

**坑2：`scan --json` 输出 `total_files: 0`**  
原因：`rules.yaml` 里 `scanner.source_root` 路径不对，指的目录不存在或没有 `.ts`/`.tsx` 文件。  
解决：检查并修改 `scanner.source_root`，然后重跑 `harness scan`。

**坑3：`doctor` 报"generated drift"**  
原因：你改了 `rules.yaml` 或 `lessons/`，但没跑 `generate`。  
解决：`harness generate` 或 `harness scan`。

**坑4：hook 装了但 Agent 写代码没有触发验证**  
原因：Agent 会话在装 hook 之前就开着了，还用的旧配置。  
解决：重启 Agent 会话（新开一个 Claude/Ducc 窗口）。

**坑5：`harness fix <file> --apply` 没有修什么**  
原因：`rules.yaml` 里没有配置 `imports.rewrites`（旧路径 → 新路径的映射）。  
解决：在 `rules.yaml` 的 `imports.rewrites` 里加映射关系。

**坑6：模块内的组件或 hook 被归到 page 层，hook 调用被误拦截**  
原因：`rules.yaml` 的 `architecture.layers` 把 `src/xxx/module/` 整体归为 page 层，`module/**/components/` 和 `module/**/hooks/` 也被前缀匹配到 page。  
解决：在 `rules.yaml` 的 `architecture` 下加 `sub_layer_convention`：
```yaml
architecture:
  sub_layer_convention:
    components: component
    hooks: hook
    utils: util
```
约定目录名优先于路径前缀匹配，局部 `components/` 就会被正确归为 component 层。
