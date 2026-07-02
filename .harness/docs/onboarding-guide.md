# Harness 2.0 接入指南

> 适用版本：Harness 2.0（`VERSION` 文件以实际为准）  
> 最后更新：2026-07-02  
> 对应 note-h5 主仓分支：`feat-yewu`

---

## 前置条件

| 条件 | 验证命令 |
|---|---|
| Python ≥ 3.9 | `python3 --version` |
| Git 仓库 | 项目根有 `.git/` |
| Claude Code / Ducc / baidu-cc 已安装 | `claude --version` 或 `ducc --version` |

---

## 快速一览（一张图）

```
任意项目（cd 到项目根）
        │
  粘贴 §1 的一行命令
        │
        ├── 新项目：去掉 --interactive，自动跑完
        │
        └── 老项目：加 --interactive，逐步回答冲突问题（见 §2.路径B）
        │
        ▼
  rules.yaml ✅
  CLAUDE.md  ✅
  settings   ✅
  generated/ ✅
```

---

## §1  一键初始化（复制骨架 + 装依赖 + 接入）

`cd` 到目标项目根目录，复制以下**一行命令**直接执行：

```bash
mkdir -p .harness && rsync -a --exclude 'rules.yaml' --exclude 'context/' --exclude 'mode-config.json' --exclude 'memory/' --exclude 'generated/' --exclude '.venv/' --exclude 'README.md' --exclude 'hooks/.config.sh' --exclude 'tests/' --exclude 'docs/' --exclude 'ARCHITECTURE.md' --exclude 'VERIFY.md' /Users/xiaowangtongzhi/Desktop/note-h5/.harness/ .harness/ && chmod +x .harness/commands/harness && python3 -m venv .harness/.venv && .harness/.venv/bin/pip install -r .harness/requirements.txt && .harness/commands/harness setup --agent ducc --interactive
```

这行命令依次做了：

| 步骤 | 说明 |
|---|---|
| `rsync …` | 从 note-h5 主仓复制 `.harness/` 骨架，跳过 `rules.yaml / context/ / generated/ / .venv/` 等项目产物 |
| `chmod +x` | 给 `harness` 命令加执行权限 |
| `python3 -m venv` + `pip install` | 建 `.harness/.venv` 并安装依赖 |
| `harness setup --agent ducc --interactive` | 交互式完成 `init`（生成 rules.yaml）+ `install`（写 CLAUDE.md + settings.json）+ `scan`（生成 generated/） |

> **新项目 / 不想交互**：去掉末尾的 `--interactive`，setup 会用全默认值自动跑完。  
> **使用 claude / baidu-cc**：把 `--agent ducc` 改成 `--agent claude` 或 `--agent baidu-cc`。

---

## §2  生成 rules.yaml + 安装 Agent Hook

### 路径 A：新项目 / src 结构清晰 — Agent 起草链路（推荐）

```bash
# 1. 只读探测 facts
.harness/commands/harness probe --json > /tmp/harness-probe.json

# 2. Agent 根据 facts 起草 /tmp/harness-rules.yaml
#    - probe 输出的 sub_layer_convention_hint 非空时，必须写入 architecture.sub_layer_convention
#    - 对不确定的层归属和 source_root 必须先问用户

# 3. 写入规则
.harness/commands/harness init --rules /tmp/harness-rules.yaml

# 4. 安装 Agent hook
.harness/commands/harness install --agent claude   # 或 ducc / baidu-cc

# 5. 扫描并生成 Agent 文档
.harness/commands/harness scan

# 6. 体检
.harness/commands/harness doctor
```

### 路径 B：新项目 / 结构标准 — 一键 setup

```bash
.harness/commands/harness setup --agent claude
```

`setup` = `init --yes` + `install --agent claude` + `scan`，全自动完成。  
完成后跳到 §4 验证。

### 路径 C：老项目 / 目录混乱（手动三步）

#### C-1  先把模式设为 off，防止未配置时误拦截

```bash
.harness/commands/harness mode off
```

#### C-2  跑探针，Agent 起草 rules.yaml

```bash
.harness/commands/harness probe --json > /tmp/harness-probe.json
# Agent 根据 facts 起草规则（保守原则：source_root 只指向前端主源码，命名先关或用 adopt）
# probe 输出 sub_layer_convention_hint 非空时，写入 architecture.sub_layer_convention
.harness/commands/harness init --rules /tmp/harness-rules.yaml
```

也可走旧链路交互式 init：

```bash
.harness/commands/harness init
```

init 分三阶段：

| 阶段 | 说明 |
|---|---|
| **probe** | 扫描 src/ 目录，探测 TS 别名、文件数、已有层结构 |
| **resolve** | 对探测到的"冲突/歧义"逐项询问（见下方交互说明） |
| **apply** | 根据你的选择写盘 `.harness/rules.yaml` |

**resolve 阶段常见问题及推荐选择**：

| 问题类型 | 推荐选择 | 说明 |
|---|---|---|
| `source_root` 有双层（如 `src/frontend/`） | 选带 `frontend` 的那个 | 让 harness 只看前端代码 |
| unknown 目录（harness 不认识的目录） | 看💡建议选 / 不确定选"先放着" | 带💡的是 harness 自动推测的，可以接受或跳过 |
| asset 类目录（images/ styles/ css/） | 选 ignore | 不需要校验样式/图片 |
| 命名风格冲突 | 优先选 `adopt:<style>` | 让 harness 与现有代码对齐，减少存量噪音；存量不报，新增代码才受约束 |
| 不想引入任何命名约束的层 | 选 `disable` | 该层命名校验完全关闭 |

> 💡 提示：resolver 会根据目录名和目录内文件名自动推测层（如 `utils/` → util 层，`hooks/` → hook 层），屏幕上会显示推测理由，可以直接接受。

#### C-3  安装 Agent Hook + 生成上下文

```bash
# 安装 settings.json hook + CLAUDE.md 托管块
.harness/commands/harness install --agent claude   # 或 ducc / baidu-cc

# 扫描代码，生成 .harness/generated/claude.md
.harness/commands/harness scan
```

---

## §4  审查并调整 rules.yaml

跑完 init / setup 后，必须人工看一遍 `.harness/rules.yaml`，重点关注：

```yaml
scanner:
  source_root: "src"          # ← 确认指向前端源码根，不是 src/backend/
  exclude_dirs:               # ← 把 node_modules/dist/build/.git 等排掉

architecture:
  sub_layer_convention:       # ← probe 的 sub_layer_convention_hint 非空时填入
    components: component     #   防止模块内局部组件/hook 被父层路径误归类
    hooks: hook
    utils: util
  layers:                     # ← 核实每个层的 paths 和 can_import 是否合理

imports:
  allowed_prefixes:           # ← 只留项目内别名，删掉 npm 包别名

naming:                       # ← 老项目建议先全删或全用 adopt 方式，不卡存量
```

### 典型项目形态对照

| 形态 | 关键改动 |
|---|---|
| Vue views + composables | `component.paths` 加 `src/views/`，或新增 page 层 |
| lib 类项目（无 src/） | `source_root: "lib"` + 删掉 component/page/hook 层 |
| Monorepo | 只在主 app 目录下单独 init，不在根目录统一管 |

详见 `.harness/docs/presets.md`。

---

## §5  验证接入状态

```bash
# 1. 体检（13 ok / 0 error 为正常）
.harness/commands/harness doctor

# 2. 看扫描是否覆盖到文件
.harness/commands/harness scan --json | python3 -m json.tool | head -20
# total_files > 0 才说明 source_root 对上了

# 3. 单文件试验证
.harness/commands/harness validate src/components/Foo.tsx

# 4. 批量验证全项目或指定子目录
.harness/commands/harness validate-all

# 5. 全局检查（依赖循环 / 死代码；依赖 scan 产物）
.harness/commands/harness check
```

### 常见异常

| 症状 | 原因 | 处理 |
|---|---|---|
| `total_files: 0` | `source_root` 路径不对 | 改 `rules.yaml scanner.source_root` 后重跑 `harness scan` |
| validate 输出空 + `mode=off` 提示 | 模式为 off | `harness mode strict` 或 `harness mode relaxed` |
| 大量 naming-violation | init 时选了 `keep-default`，但项目实际命名风格不同 | 直接改 `rules.yaml naming.*` 为 `adopt` 的风格，不需要重跑 init |
| validate 报 arch-import 但路径看起来合法 | `allowed_prefixes` 没加该别名 | 在 `rules.yaml imports.allowed_prefixes` 追加，重跑 `harness scan` |

---

## §6  日常工作流（接入后）

```
修改 src/**/*.{ts,tsx}
       │
       ▼
PostToolUse hook 自动触发 validate-code.sh
       │
   ┌───┴────────────────────────────┐
   │ 违规 → block，错误塞回 Agent   │
   │ 合规 → 继续写代码              │
   └────────────────────────────────┘
```

| 需求 | 命令 |
|---|---|
| 查看当前模式 | `harness mode` |
| 切换治理力度 | `harness mode strict \| relaxed \| off` |
| 手动触发规则验证 | `harness validate <file>` |
| 批量验证全项目 | `harness validate-all [PATH]` |
| 重新扫描生成上下文 | `harness scan` |
| 接入状态体检 | `harness doctor` |
| 查看版本/模式/扫描概况 | `harness status` |
| 自动修复导入违规 | `harness fix <file> --apply`（仅 import-forbidden 子集） |
| 新建符合规范的文件 | `harness new <kind> <name>` |
| 同步团队经验教训 | `harness sync` |

---

## §7  卸载

```bash
# 移除 settings.json 里的 harness hook + CLAUDE.md 托管块
.harness/commands/harness uninstall --agent claude

# 如果要彻底清理，删掉整个 .harness/ 目录
rm -rf .harness/
```

---

## §8  注意事项

1. **不要手动改 `.harness/generated/`** — 由 `harness scan` 自动覆盖。
2. **不要提交 `.harness/.venv/` 和 `.harness/context/`** — 加入 `.gitignore`：
   ```
   .harness/.venv/
   .harness/context/
   .harness/memory/
   ```
3. **`rules.yaml` 要提交** — 这是团队共享的规则定义，CI 也会用它。
4. **老项目接入，先用 `mode relaxed` 或 `mode off` 兜底** — 等 baseline 摸清楚再逐步收紧到 strict。
5. **validate 在 `mode=off` 下输出 `⚠️ mode=off` 提示** — 这是正常的，结果为空不代表代码无问题。需要看违规时切 strict 或 relaxed。

---

## 附录：命令速查

```bash
harness --version                        # 查看版本
harness probe --json                     # 只读探测 facts（供 Agent 起草 rules.yaml）
harness init [--yes] [--dry-run] [--json] # 探针 + 交互生成 rules.yaml
harness init --rules <file>             # Agent 起草后直接写入 rules.yaml
harness setup --agent <agent>            # 一键 init + install + scan
harness install --agent <agent>          # 安装 settings.json hook + CLAUDE.md
harness uninstall --agent <agent>        # 卸载
harness scan [--full] [--json]           # 扫描代码，生成 generated/
harness generate                         # 仅重新生成 generated/（不重新扫描）
harness validate <file> [--json]         # 验证单文件
harness validate-all [PATH] [--json]     # 批量验证全项目或指定子目录
harness check [--json]                   # 全局检查（循环依赖 + 死代码）
harness doctor                           # 接入状态体检
harness status                           # 查看版本/模式/最近扫描概况
harness mode [strict|relaxed|off]        # 查看/切换治理模式
harness fix <file> --apply               # 自动修复 import-forbidden 子集（不是通用 autofix）
harness new <kind> <name> [--path <dir>] # 按规范新建文件
harness lesson add                       # 添加团队经验
harness sync                             # 同步经验教训
```
