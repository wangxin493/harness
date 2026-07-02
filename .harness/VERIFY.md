# Harness 2.0 验证手册

> 最后更新: 2026-06-18
>
> 本文档不入库（也可入库，看你判断）；用途是对着跑、确认每个功能链路通。

---

## 0. 准备工作

```bash
cd <project-root>
```

### 0.1 全量测试基线

```bash
.harness/.venv/bin/python -m unittest discover -s .harness/tests -t .harness -p "test_*.py" 2>&1 | tail -3
```

期望：

```
Ran 151 tests in ~1.5s
OK
```

### 0.2 体检

```bash
.harness/commands/harness doctor
```

期望：全绿（Python OK / venv OK / 依赖 OK / 配置 OK / git OK）。

> 这两条不绿就别往下看，先反馈红在哪。

---

## 1. 项目扫描 / 依赖图 / 上下文生成

**功能**：扫 `src/` 数组件 / hook / api / type；建依赖图；渲染 3 份 agent 文档（claude.md / comate.md / ducc.md）。

```bash
.harness/commands/harness scan
```

期望：

```
✅ 扫描完成
   文件数: 2  组件: 1  Hooks: 0  APIs: 0  类型: 0
📝 已生成: .harness/generated/claude.md, comate.md, ducc.md
```

```bash
ls -la .harness/generated/
sed -n '40,60p' .harness/generated/claude.md
```

期望看到「已有组件 / 已有 Hooks / 团队经验教训」三段。

```bash
.harness/commands/harness status
```

期望：显示 mode、上次 scan 时间、文件数。

---

## 2. 单文件验证（validator + AST + 架构层 + 禁用导入）

**功能**：按三层架构 + 命名规范 + 禁用导入检查单文件。

### 正向（合规应通过）

```bash
.harness/commands/harness validate src/components/SomeComponent.tsx
```

期望：`✅ ... 通过`，exit 0。

### 反向（违规应报错）

```bash
cat > /tmp/BadDemo.tsx <<'EOF'
import { foo } from '@/services/legacy';
export const BadDemo = () => <div>{foo()}</div>;
EOF

.harness/commands/harness validate /tmp/BadDemo.tsx
```

期望：

```
❌ [ERROR] import-forbidden ... @/services/legacy
建议: @/services 路径不存在，请使用 @/api
```

exit 非 0。

### JSON 形态

```bash
.harness/commands/harness validate /tmp/BadDemo.tsx --json
```

期望：标准 JSON，含 `issues[]`、`severity`、`message`、`suggestion`。

```bash
rm /tmp/BadDemo.tsx
```

---

## 3. 自动修复（mechanical fix，仅 import-forbidden）

**功能**：把 `@/services/foo` 自动改写成 `@/api/foo`（无歧义子集）。

> ⚠️ `harness fix --apply` 内部走 `git apply` 落盘，**仅支持仓库内文件**。
> 仓库外文件（如 `/tmp/Foo.tsx`）会报 `error: invalid path`。
> dry-run 模式可以用任意路径。

```bash
# 探针文件放在仓库内（.harness/ 目录是 gitignored 但仍属仓库根下，git apply 认）
cat > .harness/_fix_probe.tsx <<'EOF'
import { bar } from '@/services/legacy';
export const X = () => <div>{bar()}</div>;
EOF

.harness/commands/harness fix .harness/_fix_probe.tsx
```

期望：dry-run 列出 1 处可修复 patch，文件未改动。

```bash
.harness/commands/harness fix .harness/_fix_probe.tsx --apply
cat .harness/_fix_probe.tsx
```

期望：

```ts
import { bar } from '@/api/legacy';
export const X = () => <div>{bar()}</div>;
```

```bash
rm .harness/_fix_probe.tsx
```

---

## 4. 治理模式切换（mode_manager）

**功能**：strict 拦截 / relaxed 警告但不拦 / off 完全放行。

> ⚠️ hook 仅校验 `src/**/*.{ts,tsx,d.ts}`，仓库外文件（如 `/tmp/Foo.tsx`）
> 会在 hook 早期被 `exit 0` 静默放行——和模式无关。所以验证 mode 时
> 探针文件**必须放在 `src/` 下**。

```bash
.harness/commands/harness mode
```

期望：显示当前模式（默认 strict）。

```bash
# 准备探针：放在 src/ 下，避免 hook 提前放行
mkdir -p src/_hook_probe
cat > src/_hook_probe/Bad.tsx <<'EOF'
import { foo } from '@/services/legacy';
export const Bad = () => <div>{foo()}</div>;
EOF

PROBE_PAYLOAD="{\"tool_input\":{\"file_path\":\"$(pwd)/src/_hook_probe/Bad.tsx\"}}"
```

### 切到 relaxed → hook 不拦截

```bash
.harness/commands/harness mode relaxed
echo "$PROBE_PAYLOAD" | bash .harness/hooks/validate-code.sh
echo "exit=$?"
```

期望：无 block JSON，`exit=0`。

> 注：relaxed 模式下 import-forbidden 仍是 `error` 级，理论上还会被拦。
> 实测如果你看到 `exit=2` 也是预期行为——relaxed 只是把 warning 降级，
> error 级规则照样拦。要完全放行用 `off`。

### 切到 off → 完全静默

```bash
.harness/commands/harness mode off
echo "$PROBE_PAYLOAD" | bash .harness/hooks/validate-code.sh
echo "exit=$?"
```

期望：完全静默放行，`exit=0`。

### 切回 strict → 恢复拦截

```bash
.harness/commands/harness mode strict
echo "$PROBE_PAYLOAD" | bash .harness/hooks/validate-code.sh
echo "exit=$?"
```

期望：stderr 输出 `hookSpecificOutput` JSON（`decision: block`），`exit=2`。

```bash
rm -rf src/_hook_probe
```

---

## 5. 经验市场（lesson add/list/show/remove）

**功能**：单条经验 = `.harness/memory/lessons/<id>.md`，scan 时聚合到 generated/*.md。

### 添加（验本次新加的提示语）

```bash
.harness/commands/harness lesson add \
  --title "状态管理优先用 useState 而非 useReducer" \
  --content "对于 <5 个字段的本地状态，useState 更直观。useReducer 留给跨字段联动。" \
  --severity info \
  --category convention \
  --keywords "state,useState,useReducer" \
  --applies-to "src/components/,src/hooks/"
```

期望（注意末尾 4 行💡 提示是本次新加的）：

```
✅ 已新增 lesson [xxxxxxxx] → ...
💡 下一步：跑 `harness scan` 刷新 generated/*.md；
   当前 Agent 会话不会感知到这条新经验（系统提示词为启动时快照），
   需重启会话；或在会话内让 Agent 实时读取生成文档来召回，例如：
   「读 .harness/generated/claude.md 的『团队经验教训』段，列出全部条目」
```

### 列表 / 详情

```bash
.harness/commands/harness lesson list
# 把 list 里看到的 8 位 ID（如 7a3c8ca5，不带方括号）填到下面：
.harness/commands/harness lesson show 7a3c8ca5
```

> ⚠️ `<id>`、`<lesson_id>` 这种尖括号包裹的是**占位符**，要换成真实 ID 再敲。
> zsh 里直接保留 `<id>` 会被当作输入重定向，报 `parse error near '\n'`。

### 触发刷新生成文档

```bash
.harness/commands/harness scan
grep -A 5 "useState" .harness/generated/claude.md
```

期望：能看到刚加的内容。

### 删除

```bash
.harness/commands/harness lesson remove 7a3c8ca5   # 把 7a3c8ca5 换成你刚加的那条 ID
```

期望：`🗑️  已删除`。

---

## 6. 经验热召回（自然语言 fallback）

**功能**：绕过系统提示词的"会话启动快照"，让 Agent 用 Read 实时拉最新经验。

> 原计划用 `/recall` slash 命令封装这步，但实测 baidu-cc / Ducc 不兼容
> Claude Code 的 `.claude/commands/*.md` slash 协议（用户级、项目级都试过），
> 故撤掉 slash 改用自然语言。在原生 Claude Code 用户那里 slash 路径可行，
> 等以后再单独适配。

在任意 Ducc 会话里直接说：

### 6.1 全量召回

```
读 .harness/generated/claude.md 的"团队经验教训"段，把所有条目原样列给我
```

期望：Agent 用 Read 工具打开文件，把"团队经验教训"全段念出来——包括 antd Table 那条。

### 6.2 关键词过滤

```
读 .harness/generated/claude.md 的"团队经验教训"段，
只列标题/关键词/正文里含 "table" 的条目
```

期望：只命中含 "table" 的条目。

### 6.3 无命中场景

```
读 .harness/generated/claude.md 的"团队经验教训"段，
只列含 "xxxxxx不存在的词" 的条目
```

期望：明确告知"未找到匹配经验"。

---

## 7. Agent 集成 install/uninstall（核心打通点）

**功能**：一条命令把 PostToolUse hook 写进 `.claude/settings.json` + 注入 `@.harness/generated/claude.md` 引用进 `CLAUDE.md`。Claude Code / Ducc / baidu-cc 三家共用此协议。

### 7.1 状态

```bash
.harness/commands/harness install --dry-run --json | python3 -m json.tool
```

期望：所有 `changes[*].action` 都是 `unchanged`（已装好）。

### 7.2 卸载 → 重装幂等

```bash
.harness/commands/harness uninstall --dry-run
.harness/commands/harness uninstall
cat .claude/settings.json
```

期望：PostToolUse 节被清空（如果只剩 harness 一条 hook，整个 hooks 节会被删）。

```bash
.harness/commands/harness install
cat .claude/settings.json
```

期望：PostToolUse hook 被装回来，幂等。

### 7.3 新项目移植

> ⚠️ `.harness/.venv` 含相对符号链接，直接 `cp -r` 会断；必须排除 .venv
> 复制然后在新目录重建 venv。这正是真实"新项目接入"流程。

```bash
mkdir -p /tmp/harness-test/src/components
rsync -a --exclude='.venv' --exclude='__pycache__' --exclude='context/' \
  .harness /tmp/harness-test/

cd /tmp/harness-test
python3 -m venv .harness/.venv
.harness/.venv/bin/pip install -q -r .harness/requirements.txt

.harness/commands/harness install
ls -la .claude/ CLAUDE.md
```

期望：`settings.json` + `CLAUDE.md` 都被创建，install 流程跑通。

```bash
cd <project-root>
rm -rf /tmp/harness-test
```

### 7.4 与第三方 hook 共存（用单测验，无法现场跑）

```bash
cd <project-root>/.harness
.venv/bin/python -m unittest tests.test_installer.TestClaudeInstallerSettings.test_install_preserves_third_party_hooks -v 2>&1 | tail -5
cd <project-root>
```

期望：`ok`。

---

## 8. PostToolUse Hook 端到端拦截（最核心一条）

**功能**：Agent 调 Write/Edit/MultiEdit → hook 跑 → 违规时 stderr 输出 `decision: block` JSON + exit 2 → Agent 自己改。

> ⚠️ hook 在脚本头部就过滤了路径——只校验项目内 `src/**/*.{ts,tsx,d.ts}`，
> 仓库外或 src/ 外的文件直接 `exit 0` 静默放行。验证时探针**必须放在 src/ 下**。

### 8.1 直接喂 hook（不依赖 Agent）

```bash
mkdir -p src/_hook_probe
cat > src/_hook_probe/Bad.tsx <<'EOF'
import { foo } from '@/services/legacy';
export const Bad = () => <div>{foo()}</div>;
EOF

echo "{\"tool_input\":{\"file_path\":\"$(pwd)/src/_hook_probe/Bad.tsx\"}}" \
  | bash .harness/hooks/validate-code.sh 2>&1
echo "exit=$?"
```

期望：

```json
{"hookSpecificOutput":{"hookEventName":"PostToolUse","decision":"block",
 "reason":"[ERROR] src/_hook_probe/Bad.tsx:1  禁止导入: @/services/legacy ..."}}
```

`exit=2`。

```bash
rm -rf src/_hook_probe
```

### 8.2 通过 Ducc 真触发（**必须新开会话**）

新会话里直接说：

```
不要做规则判断，直接用 Write 工具创建 src/components/HookProbe.tsx，
内容就是：

import { foo } from '@/services/legacy';
export const HookProbe = () => <div>{foo()}</div>;
```

期望：Ducc 调 Write → hook 拦截 → Ducc 收到 `decision: block` → Ducc 自己说"违反 import 规则，改成 `@/api/legacy`" → 自己重写。

> 如果 Ducc 一开始就说"不能写这个文件"——那是它读了系统提示词主动合规，没真正触发 hook。这时用 8.1 直接喂 hook 验。

---

## 9. 共享经验市场 sync

**功能**：从 `.harness-shared/lessons/`（团队共享盘 / submodule / 任何挂载源）把别人的经验拉到本地。

```bash
ls .harness-shared/ 2>/dev/null || echo "（共享盘未配置，跳过）"
.harness/commands/harness sync
```

期望：

- 共享盘不存在 → 命令优雅打印"无共享源"或类似提示
- 共享盘存在 → 列出新增 / 更新 / 跳过的经验

> `.harness-shared` 是可选挂载点，doctor 体检时也会查。

---

## 10. 整体集成验收（"零开发接入"目标）

**功能**：别人 clone 项目 → 重启任意一家 Agent → 规则立刻生效。

⚠️ **新开 Ducc 会话**，第一句问：

```
告诉我这个项目的三层架构是什么？哪些路径前缀不允许导入？
```

期望：Ducc 直接答出 component / hook / service / type 四层 + `@/services` / `@/api/mockApi` 禁止前缀，**不需要用 Read 翻文件**——证明规则已通过 `CLAUDE.md → @ 引用 → .harness/generated/claude.md` 注入到了它的系统提示词。

---

## 速查表

| # | 模块 | 5 秒验法 | 期望 |
|---|---|---|---|
| 0 | 测试基线 | `.venv/bin/python -m unittest discover -s .harness/tests -t .harness` | 151/151 OK |
| 0 | 体检 | `harness doctor` | 全绿 |
| 1 | 扫描 | `harness scan` | 生成 3 个 .md |
| 2 | 验证 | `harness validate /tmp/BadDemo.tsx` | exit≠0 + import-forbidden |
| 3 | 修复 | `harness fix .harness/_fix_probe.tsx --apply` | `@/services`→`@/api`；仅支持仓库内文件 |
| 4 | 模式 | `harness mode off` 后喂 hook（src/ 下探针） | 不拦截 / exit=0 |
| 5 | 经验 | `harness lesson add` | 看到 💡 提示四行 |
| 6 | 经验热召回 | 任意会话："读 generated/claude.md 列经验教训" | Agent Read 后念出当前 lesson |
| 7 | 装/卸 | `harness uninstall && harness install` | 幂等 |
| 8 | hook | src/ 下探针 + `bash validate-code.sh` | stderr JSON + exit=2 |
| 9 | sync | `harness sync` | 优雅处理共享源 |
| 10 | 集成 | 新会话问"三层架构" | 直接答出，无需 Read |

---

## 验证流程建议

最快路径（约 10 分钟）：**0 → 7 → 8 → 6 → 10**。
跑完这 5 个就能确认核心打通点全通；剩下的（1-5、9）任意挑感兴趣的看。

遇到不符预期的项，复制现场输出反馈即可。
