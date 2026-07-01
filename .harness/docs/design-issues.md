# Harness 2.0 架构偏差记录

> 本文件记录经全量代码扫描后识别的**架构/设计层面**偏差，不包含 bug 修复和性能优化。  
> 每条按优先级排序，包含：问题描述 / 当前行为 / 应有行为 / 改法方向。  
> 状态：⬜ 待处理 / 🔄 进行中 / ✅ 已完成

---

## P0 — 必须先对齐，否则治理结果不可信

### D4 · `import_aliases` 格式分叉（scanner vs validator）

**状态：** ✅ 已完成（2026-07-01）

**问题：**  
同一份 `rules.yaml` 的 `import_aliases` 字段，`scanner.py` 和 `validator.py` 用不同格式读取，两条代码路径对同一规则解读不同，架构治理核心数据有分叉。

**修复：** 新建 `lib/rules_utils.py`，提取 `parse_import_aliases(scanner_cfg)` 兼容新旧两种格式（dict 和 list of `{prefix, target}`），scanner 和 validator 统一使用。

---

### D5 · `_classify_layer` 逻辑重复（scanner vs validator）

**状态：** ✅ 已完成（2026-07-01）

**问题：**  
`IncrementalScanner` 和 `CodeValidator` 各自维护一份 `_classify_layer` 和 `_normalize_layers`，两份实现完全独立，改一处必须同步改两处。

**修复：** `lib/rules_utils.py` 提取 `normalize_layers()` 和 `classify_layer()`，scanner 和 validator 删除各自副本，统一调用。

---

## P1 — 影响接入效果和工具可信度

### D1 · 语义判断混在工具层（init_resolver）

**状态：** ✅ 已完成（2026-07-01）

**问题：**  
`init_resolver._suggest_layer_for_dir` 用关键词表 + 文件命名统计推断"这个目录应该是什么层"，并把推断结果作为 `default_choice` 影响 resolver 的机械决策。语义判断不应在工具层完成。

**修复：** `default_choice` 不再由 `suggested_key` 驱动（资源类目录除外，因"资源目录 ignore"是机械判断而非语义判断）。推测信息仍展示在 `detail` 里供用户参考，但工具不替用户做选择。

---

### D2 · `rules.yaml` 起草职责错位

**状态：** ✅ 已完成（2026-07-01，加法迁移）

**问题：**  
`harness init` 承担了"理解项目架构 → 起草 rules.yaml"的职责。项目架构理解属于语义判断，应由 Agent 完成，工具只应执行规则。

**应有行为：**  
- `harness probe --json` 输出项目 facts（目录结构、别名、命名统计）
- Agent 读取 facts，结合项目上下文，起草 `rules.yaml`
- `harness init` 退化为"校验 + 写盘"

**修复：** 采用加法迁移，不破坏旧 `init/setup` 行为：新增 `harness probe --json` 暴露原始项目 facts；新增 `harness init --rules <file>` 接收 Agent 起草好的规则并写入 `.harness/rules.yaml`。旧 resolver-driven `init` 保留为兼容路径，后续可再评估是否标记为 legacy。

---

### D6 · `setup` 非交互模式静默覆盖 `rules.yaml`

**状态：** ✅ 已完成（2026-07-01）

**问题：**  
非交互模式下已有 `rules.yaml` 时，`setup` 静默覆盖已有配置，用户手动修改的规则会丢失。

**修复：** `setup_cmd` 新增 `--force` option。非交互模式下若 `rules.yaml` 已存在且未传 `--force`，报错退出并提示"已有接入项目请用 `harness scan` 刷新"。测试用例同步更新为验证新行为。

---

### D3 · `doctor` 对 `layers: []` 误报健康

**状态：** ✅ 已完成（2026-07-01）

**问题：**  
`doctor._check_rules_yaml` 对 `layers: []` 返回 ok（✅ 已加载 0 个架构层），0 层等于架构治理完全未激活，不应显示绿色。

**修复：** `layers` 为空时返回 `severity="warning"`，提示"architecture.layers 为空，架构治理未激活"。

---

## P2 — 一致性问题，不影响核心功能

### D7 · `harness new` 不触发 generate

**状态：** ✅ 已完成（2026-07-01）

**问题：**  
`new_cmd` 建完文件后不调 `generate_all()`，与 `lesson add` / `mode` 切换行为不一致，导致 `generated/claude.md` 不自动更新。

**修复：** `new_cmd` 成功输出后追加 `Generator.generate_all()` 调用，generate 失败时静默处理（不影响 new 的成功状态）。

---

## 变更记录

| 日期 | 操作 |
|---|---|
| 2026-07-01 | 初始版本，基于全量代码扫描（7 个核心 lib 文件）识别 D1–D7 共 7 条设计偏差 |
| 2026-07-01 | D4/D5/D3/D6/D1/D7 落地完成；D2 先标记为待处理（大方向，暂不改代码） |
| 2026-07-01 | D2 加法迁移落地：新增 `probe --json` + `init --rules <file>`，Agent 起草链路打通，旧 init/setup 保留兼容 |
