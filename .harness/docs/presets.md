# Harness 接入预设（presets.md）

`harness init` 是探测器 + 协商器，对常见项目形态能自动生成可用的 `rules.yaml`，
但有几类项目探测器猜不准、需要你跑完后手动改两行。

对于结构复杂或存量代码多的项目，推荐先用 `harness probe --json` 输出项目 facts，
再由 Agent 根据 facts 起草 `rules.yaml`，然后通过 `harness init --rules <file>` 写盘。
这样可以避免 resolver 对目录语义做不准确的自动推断。

本文档列出四类典型项目形态 + 推荐配置，作为快速对照参考。

## 出厂默认（基线）

`.harness/rules.yaml` 出厂值假设项目长这样：

```
src/
├── components/   ← component layer
├── pages/        ← 也属 component layer（同层多目录）
├── hooks/        ← hook layer
├── api/          ← service layer
└── types/        ← type layer
```

- 别名：`@/` → `src/`
- 命名：component/type 用 PascalCase；hook 用 `use` 前缀的 camelCase；service 用 `Service` 后缀的 camelCase
- 导入：白名单 `@/types / @/hooks / @/api / @/pages / @/components`；黑名单空

> 三段式 init 会把"探测到的现状"覆盖到这些默认上；下面四类描述的是
> **跑完 init 后的最终 rules.yaml**，不是"默认值"本身。

---

## 1. React + src/ 标准三层（零改动）

**项目特征**：
- React + TS，单包，`src/{components, pages, hooks, api, types}/` 齐全
- `tsconfig.json paths: { "@/*": ["src/*"] }`

**init 后**：直接可用，所有 conflict 走默认即可。
跑 `harness init --yes` 就完事。

---

## 2. Vue + src/views + composables

**项目特征**：
- Vue 3，路由组件落在 `src/views/`，hook 叫 `composables/`，API 叫 `apis/`

**probe 会怎么报**：
- 探测到 `src/views/` → 标 `page` layer（_DIR_LAYER_HINTS 里 `views → page`）
- 探测到 `src/composables/` → 标 `hook` layer
- 探测到 `src/apis/` → 标 `service` layer

**resolve 会怎么决策**：
- 默认 rules 里没 page layer（page 路径合并在 component），所以 `architecture.layers` 不会自动加 page
- 这一条建议手动改：在 `architecture.layers` 里把 `component.paths` 改成 `["src/components/", "src/views/"]`，或者添加一个 page 层并把 `templates.kinds.page.default_dir` 指过去
- composables / apis 路径会自动采纳（写进对应 layer.paths）

**手动补 1 行**：
```yaml
architecture:
  layers:
    - name: component
      paths: ["src/components/", "src/views/"]   # 加一行
      can_import: ["hook", "service", "type"]
```

或者保留 component-only，把 views 当独立 page 层（更接近 Vue 直觉）：
```yaml
architecture:
  layers:
    - name: page              # 新增
      paths: ["src/views/"]
      can_import: ["hook", "service", "type"]
    - name: component
      paths: ["src/components/"]
      can_import: ["hook", "service", "type"]
    # hook / service / type 不变
```

---

## 3. 没有 src/ 的 lib 类项目

**项目特征**：
- npm 包，源码直接在仓库根或 `lib/` 下
- 没有"组件/页面"概念，只有公共函数 / type

**probe 会怎么报**：
- `src/` 不存在 → `layers=[]`，`naming=[]`
- 别名也基本没有

**resolve 后**：
- `architecture.layers` 沿用默认五层，但所有 paths 都指向不存在的目录
- validator 会"一个文件都管不到"，相当于摆设

**手动改三处**：
```yaml
architecture:
  layers:
    - name: lib
      paths: ["lib/", "src/"]            # 改成实际源码根
      can_import: ["type"]
    - name: type
      paths: ["lib/types/", "types/"]
      can_import: []
scanner:
  source_root: "lib"                     # 默认是 "src"
naming:
  lib: camelCase                         # 删掉 component/page/hook/service
  type: PascalCase
```

或者直接 `harness mode off`，把 harness 当文档用，validator 不参与。

---

## 4. Monorepo（apps/web/src + packages/ui）

**项目特征**：
- pnpm / yarn / npm workspaces
- 业务在 `apps/<name>/src/...`，共享库在 `packages/<name>/src/...`

**probe 会怎么报**：
- 探测器只扫**根**目录的 `src/`（不下钻 apps/packages）
- 99% 概率：`layers=[]`，`aliases=[]`，info note 里只有框架 / 包管理器

**推荐做法**：在每个 app/package 单独跑 `harness init`，让每个子项目有
自己的 `.harness/rules.yaml`；或者只在最关键的 app（如 `apps/web/`）下接
入，packages/ 走代码 review 兜底。

如果非要在根目录统一管：
```yaml
scanner:
  source_root: "apps/web/src"            # 锚定到主 app
  exclude_dirs: ["node_modules", "dist", "build", ".git", ".harness",
                 "packages", "apps"]      # 排掉其它子目录
architecture:
  layers:
    - name: component
      paths: ["apps/web/src/components/", "apps/web/src/pages/"]
      can_import: ["hook", "service", "type"]
    # ...
```

但当前不推荐这条路——增量 scan 在多 source_root 下没有作为主路径充分验证。

---

## 通用：探测器猜不到的东西

`init` 完成后请手动审 `.harness/rules.yaml` 这几个键：

| 键 | 为什么需要手动 |
|---|---|
| `imports.forbidden_imports` | 业务语义（例：废弃的 `@/legacy`、不让用的 `@/api/mockApi`） |
| `imports.forbidden_suggestions` | 配合 forbidden_imports，给 Agent 的修复提示 |
| `imports.rewrites` | `harness fix <file> --apply` 的旧前缀→新前缀映射（仅 import-forbidden 子集） |
| `checks.hook_call_check.excluded_callees` | 业务里有同名但非 React hook 的函数（罕见） |
| `checks.unused_exports.entry_points` | SPA / Node 入口；默认覆盖 `src/index.{ts,tsx}` 和 `src/main.{ts,tsx}`，多入口或非常规结构需追加 |

修改完跑 `harness scan` 重新生成 `.harness/generated/claude.md`，
让 Agent 看到新规则。

## 快速诊断

跑完 `init` / 改完 rules，验一下：

```bash
# 看当前规则有没有真的覆盖到代码
harness scan --json | python3 -m json.tool | head -30

# 跑一遍全项目校验，看哪些违规
harness check

# 单文件试拦截（PostToolUse 真实路径）
harness validate src/components/Foo.tsx
```

如果 `scan` 输出里 `total_files=0`，说明 `scanner.source_root` 或
`include_extensions` 没对上；如果 `check` 报一堆 naming-violation，
说明 `init` 时该选 `adopt:<style>` 而你选了 `keep-default`。
把 `rules.yaml naming.*` 改成 `adopt:<style>` 后，再运行
`harness scan --reset-baseline` 固定当前存量命名违规，后续只报新增。
