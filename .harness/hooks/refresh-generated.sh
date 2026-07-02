#!/usr/bin/env bash
# Harness 2.0 SessionStart Hook —— 会话启动 / 恢复 / 清空 / 压缩时刷新 generated/*.md
#
# 目的：
# - lesson 是「写入 → scan/generate 渲染 → CLAUDE.md @-import 注入」三步链路；
#   任何一步漏掉，新经验就不会出现在 Agent 的系统提示里。
# - lesson add/remove 已在 CLI 内自动 generate；这里再加一道兜底：
#   每次会话启动都重新跑一次 scan，保证下游的 .harness/generated/claude.md
#   始终包含 memory/lessons/ 的最新内容（也顺便覆盖 sync 后忘记 scan 的场景）。
#
# 协议：
# - SessionStart 不能阻塞会话；任何错误都吞掉，仅打印到 stderr 提示用户。
# - stdout 会被注入到 Agent 上下文，所以这里只输出 hookSpecificOutput JSON。

set -uo pipefail

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-${HARNESS_PROJECT_DIR:-$(pwd)}}"
PROJECT_DIR="${PROJECT_DIR%/}"

# npm 包化：HARNESS_BIN 优先使用 node_modules/.bin/harness，fallback 全局 PATH
if [ -z "${HARNESS_BIN:-}" ]; then
    _NM_BIN="$PROJECT_DIR/node_modules/.bin/harness"
    if [ -x "$_NM_BIN" ]; then
        HARNESS_BIN="$_NM_BIN"
    else
        HARNESS_BIN="$(command -v harness 2>/dev/null || true)"
    fi
fi

if [ -z "${HARNESS_BIN:-}" ]; then
    # harness 未初始化：什么都不做
    exit 0
fi

export HARNESS_PROJECT_DIR="$PROJECT_DIR"

# 后台跑一次 scan（含 generate）；保留输出用于失败提示。
SCAN_OUTPUT="$("$HARNESS_BIN" scan --json 2>&1)"
SCAN_EXIT=$?

if [ $SCAN_EXIT -ne 0 ]; then
    # 不阻塞会话，仅把错误丢给用户看；同时给 Agent 一个提示。
    printf 'harness scan 失败 (exit=%s)：\n%s\n' "$SCAN_EXIT" "$SCAN_OUTPUT" >&2
    python3 -c "
import json
print(json.dumps({
    'hookSpecificOutput': {
        'hookEventName': 'SessionStart',
        'additionalContext': '⚠️ harness scan 在会话启动时失败，.harness/generated/*.md 可能不是最新；新增的团队经验可能未生效。请手动跑 \`harness scan\` 排查。',
    }
}))
"
    exit 0
fi

# 成功：不必把全量输出塞回上下文，只给一个简短提示，说明规则文件已是最新。
python3 -c "
import json
print(json.dumps({
    'hookSpecificOutput': {
        'hookEventName': 'SessionStart',
        'additionalContext': '✅ harness scan 已在会话启动时刷新 .harness/generated/*.md（规则与团队经验为最新）。',
    }
}))
"
exit 0
