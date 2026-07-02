# 试点报告:fe-salary-adjustment 接入 Harness 2.0

> 日期:2026-06-29  
> 试点项目:`/Users/xiaowangtongzhi/Desktop/harness/fe-salary-adjustment`(`test_harness_add` 分支)  
> 试点目标:**借老项目暴露 harness 2.0 当前的不足**,不是修干净老项目。  
> 试点约束:业务代码一行不改;不 commit / 不 push;不用外部模型扫描。
>
> **[更新 2026-07-01]**  
> C1（双栈 source_root）：probe 已支持 `src/frontend` / `src/backend` 这类前后端双栈提示，并通过 `dual-stack-hint` 暴露给 init / Agent。  
> C2（自定义 layer）：旧 resolver 已支持 `new-layer:<name>`；复杂项目更推荐 `probe --json` + Agent 起草 + `init --rules <file>`。  
> C9（全项目 validate 命令缺失）：已实现 `harness validate-all [PATH] [--json]`；`harness check` 负责循环依赖和死代码这类全图检查。  
> C10（mode=off 静默）：已修复，`harness validate` 在 mode=off 时明确输出 `⚠️ mode=off` 提示。

---

## 1. 试点全流程(7 步)

| # | 步骤 | 命令 / 动作 | 结果 |
|---|---|---|---|
| 1 | 复制 `.harness/` 骨架 | 白名单 `cp` 命令(`commands/` `lib/` `hooks/` `templates/` `requirements.txt` `VERSION`),**不拷**:`rules.yaml` `context/` `mode-config.json` `memory/` `generated/` `.venv/` `README.md` `hooks/.config.sh` | ✅ 干净起步 |
| 2 | 建 venv + 装依赖 | `python3 -m venv .harness/.venv` + `pip install -r requirements.txt` | ✅ Python 3.9.6 + 5 个核心依赖全 OK |
| 3 | 先 `mode off` 兜底 | `.harness/commands/harness mode off` | ✅ 落 `mode-config.json` |
| 4 | `harness init` 探针 | `.harness/commands/harness init --dry-run --json` | ⚠️ 探针走完了,但**产物无法直接用** → 改为手写 rules.yaml |
| 5 | `harness scan` + 逐文件 validate | `.harness/commands/harness scan` + Python 脚本遍历 `project-context.json` 调 `validate --json` | ✅ 156 文件全部解析成功,618 条问题 |
| 6 | `harness check` 全局检查 | `.harness/commands/harness check --json` | ✅ 8 条循环依赖 |
| 7 | `harness doctor` 体检 | `.harness/commands/harness doctor` | ✅ **13 ok / 1 info / 0 warn / 0 error** |

---

## 2. 项目现状摘要(用于解释后续设计选择)

| 维度 | 现状 |
|---|---|
| 分支 / 远端 | `test_harness_add` / `ssh://wangxin125@icode.baidu.com:8235/baidu/erp/fe-salary-adjustment` |
| src 布局 | `src/frontend/`(前端,主目标)+ `src/backend/`(node.js 后端,排除) |
| 实际分层(`src/frontend/` 下) | `module/`(70 ts/tsx)、`components/`(66)、`service/`(19)、`utils/`(2);`layout/` `decorators/` `weirwood/` `Error/` `style/` `_Templates/` 全 0 ts/tsx |
| TS 配置 | TS 4.5,jsx=react,`allowJs: true` + `checkJs: true` |
| tsconfig paths | `frontend/*` `/frontend/*` → `src/frontend/*`;`/@befe` `@befe/erp-comps` `@befe/utils/*` → `src/@befe/*` |
| 已有工具链 | ESLint + fecs + prettier;有 `.comate/`(Comate Agent),无 husky |
| 额外目录 | `_matriks2_/` `_sub_repos_/` `dest/` `tmp/` `link/` `script/` `doc/` 等需 exclude |

---

## 3. 手写 rules.yaml 的关键设计决策

> probe 探针不够用,改为人工根据"目录摸底 + dry-run 输出"手写。

| 字段 | 取值 | 理由 |
|---|---|---|
| `scanner.source_root` | `src/frontend` | 绕开"src 下还有一层"的 probe 缺陷;backend 整个跳出视野 |
| `architecture.layers` | 5 个:`module` / `component` / `service` / `utils` / `type` | 只声明真实有 ts/tsx 的目录;0 文件目录(layout 等)不进 layers |
| `module.can_import` | `[component, service, utils, type]` | 老项目惯用法:业务模块调通用组件 |
| `type.paths` | `[]` 空 | 项目没有独立 types/,但留 layer 占位让其它 layer 的 `can_import: [type]` 能解析(workaround) |
| `naming` | `{}` 空字典 | 试点期不卡命名,等 baseline 出来再选 |
| `imports.allowed_prefixes` | 5 个 probe 探到的别名全留 | 不卡 import,先看 baseline |
| `experience_market.enabled` | `false` | 不污染本地经验池 |
| `checks.unused_exports.enabled` | `false` | 入口太复杂,会出几百条噪音 |
| `governance.default_mode` | `off` | 兜底:mode-config.json 删了也默认不拦 |

完整 rules.yaml 已落在 `<fe-salary-adjustment>/.harness/rules.yaml`(试点产物,不入主仓)。

---

## 4. baseline 数据(strict 模式下 156 文件全量 validate)

```
files validated: 156   parse errors: 0
total issues:    618
  error   143
  warning 188
  info    287

按 rule_id × severity:
  287  info     name-similarity
  188  warning  name-similarity
  142  error    hook-call-misplaced
    1  error    hook-call-in-plain-func

按 layer 分布:
  module    142  (主要是 hook-call-misplaced)
  service   473  (全是 name-similarity)
  component   3

全局 check(harness check):
  8  error    cycle  (components/AuthorizeDialog/* 下 store ↔ hook 互引)
```

### 真伪辨析

| 命中规则 | 数量 | 性质 | 处理建议 |
|---|---|---|---|
| `hook-call-misplaced` (error) | 142 | **真问题**(React 反模式:hook 出现在 module 层文件) | 不动业务,但这是真的债 |
| `hook-call-in-plain-func` (error) | 1 | **真问题** | 同上 |
| `cycle` (error) | 8 | **真问题**(store ↔ hook 循环) | 同上 |
| `name-similarity` service 同名 1.00 (warning) | 多个 | **规则误伤** —— 老项目按业务域拆 `cardApi/authorizeApi/m2-manager-api/...`,各域各自一个 `getButtonList` 是合理的 | 默认应放宽 |
| `name-similarity` 0.8 hooks 相似 (info) | 287 | 多数是噪音 | 默认阈值 0.8 太低 |

**结论**:harness 在老项目上**有真实价值**(151 条真问题),不是"规则太严";但 `name-similarity` 这类规则**对老项目布局不友好**,要调。

---

## 5. harness 自身缺陷清单(10 条)

按"试点暴露的不足,回 note-h5 改"归类,P0/P1/P2 已排序。

### init 阶段(C1–C5)

| # | 缺陷 | 现象 | 影响 | 优先级 |
|---|---|---|---|---|
| C1 | probe 写死 `source_root='src'`,不识别 `src/<frontend>/...` 双栈 | dry-run 把 `src/frontend/` 当"未识别一级目录",问要映射到哪个 layer | 老项目接入**必须手写** source_root | **P0** |
| C2 | probe 只能映射到默认 5 个 layer(component/page/hook/service/type),不能让用户**新建 layer** | `module/` `layout/` 等自定义层只能手写 | 老项目接入**必须手写** layers | **P0** |
| C3 | 0 个 ts/tsx 的子目录 default_choice 是 `skip`(应当是 `ignore`) | `src/backend/`(0 ts/tsx)被建议"先放着" | 用户走 `--yes` 会留个 backend 当历史包袱 | P1 |
| C4 | probe 文件计数偏低(60 vs 实际 157 中 60 是顶层) | dry-run detail 写 "60 个 ts/tsx 文件",用户不知道总量 | 信息误导,不影响功能 | P2 |
| C5 | `imports.allowed_prefixes` 一锅炖采纳 tsconfig 全部 paths,不区分"项目内别名 vs 外部包别名" | 5 个 path 全进 rules.yaml,包括看名字像 npm 包的 `@befe/erp-comps` | 白名单可能过宽 | P2 |

### scan / validate 阶段(C6–C7)

| # | 缺陷 | 现象 | 影响 | 优先级 |
|---|---|---|---|---|
| C6 | `name-similarity` 默认对 service 跨文件同名报 warning,但**老项目按业务域分文件**很常见 | 188 条 warning 全部是同名 service 跨文件 | 噪音污染 baseline | **P0** |
| C7 | `name-similarity` 默认阈值 0.8,触发 287 条 info | 0.8 太低 | 噪音 | P1 |

### rules.yaml 设计(C8)

| # | 缺陷 | 现象 | 影响 | 优先级 |
|---|---|---|---|---|
| C8 | `architecture.layers` 必须有 `type` layer 才能让 `can_import: [type]` 不报错 | 我只能用 `paths: []` 空占位顶 | 设计漏洞:`can_import` 引用了不存在的 layer 应自动忽略或报清楚 | P2 |

### CLI 体验(C9–C10)

| # | 缺陷 | 现象 | 影响 | 优先级 |
|---|---|---|---|---|
| C9 | 没有"逐目录 / 全项目 validate"子命令 | 试点者要自己写 Python 脚本遍历 `project-context.json` 逐文件调 `validate --json` | 想看 baseline 必须写脚本 | **P0** |
| C10 | `harness validate` 在 mode=off 下**静默返回空**,无任何提示"当前 off,所以没规则启用" | 第一次跑 validate 拿到 `total issues: 0`,差点误以为老项目零违规 | **极易误判** | **P0** |

---

## 6. 修复状态（原 P0 方案）

| # | 修复方向 | 状态 |
|---|---|---|
| **C1**（双栈 source_root） | probe 在 `src/` 下检测双栈并提示 `dual-stack-hint`；init 交互时可选对应子目录；Agent 起草链路可直接读 facts 自行决定 | ✅ 已落地 |
| **C2**（自定义 layer） | resolver 的 unknown-dir conflict 已加 `new-layer:<name>` 选项；Agent 起草链路下 Agent 可自定义任意层 | ✅ 已落地 |
| **C6**（name-similarity 老项目不友好） | 需加配置项降低跨文件同名 severity 或支持 per-layer 关闭 | ⬜ 待处理 |
| **C9**（全项目 validate） | 已实现 `harness validate-all [PATH] [--json]`；全图检查（循环依赖 + 死代码）由 `harness check` 承担 | ✅ 已落地 |
| **C10**（off 模式静默） | `harness validate` 在 mode=off 时输出 `⚠️ mode=off` 提示；`--json` 顶层含 `mode` 字段 | ✅ 已落地 |

---

## 7. 试点结论

1. **接入流程能跑通** —— 156 文件 scan/validate/check/doctor 全过,产物准确。
2. **接入门槛主要在 init**:probe 不够智能,老项目接入要**人工介入两次**(白名单复制 + 手写 rules.yaml)。
3. **harness 本身没有崩**,真正的发现是 "**老项目接入瓶颈在 init,不在 hook / scanner / validator**"。
4. **strict 模式下 baseline 有真实价值**:hook-call-misplaced(142)+ hook-call-in-plain-func(1)+ cycle(8)= **151 条真问题**,证明 harness 规则对老项目有意义。
5. **name-similarity 对老项目布局不友好**,要调默认。

---

## 8. 试点产物 / 残留

- `/Users/xiaowangtongzhi/Desktop/harness/fe-salary-adjustment/.harness/` —— 完整 harness 安装,`mode=off`,业务代码零改动
- `/tmp/salary-validate-strict.json` —— 156 文件 strict 模式 validate baseline(618 条 issue)
- `/tmp/salary-check2.json` —— 全局 check baseline(8 条 cycle)
- `/tmp/salary-baseline.json`、`/tmp/salary-check.json` —— off 模式空跑(供对照)

**fe-salary-adjustment 仓库**:无 commit、无 push,工作区是新增 `.harness/`(未 `git add`)。
