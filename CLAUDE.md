# 项目说明

<!-- harness:begin (managed; do not edit between markers) -->
> 本节由 `harness install` 维护。规则随 `harness scan` 自动刷新。

@.harness/generated/claude.md

**工作流**：

- 修改 `src/**/*.{ts,tsx,d.ts}` 后，PostToolUse hook 会自动跑 `harness validate`，违规会被拦截并把错误塞回我让我自己改
- 治理模式（拦截力度）切换：`harness mode <strict|relaxed|off>`
- 自动修复（仅 `import-forbidden` 子集）：`harness fix <file> --apply`
- 同步团队经验：`harness sync`
<!-- harness:end -->
