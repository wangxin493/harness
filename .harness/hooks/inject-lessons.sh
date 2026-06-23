#!/usr/bin/env bash
# Harness 2.0 PostToolUse Hook —— 按修改文件动态注入相关 lesson
#
# 作用：Agent 写完 src/**/*.{ts,tsx,d.ts} 后，按 file_path + new_content
# 调 harness lesson match 召回相关经验，通过 hookSpecificOutput.additionalContext
# 推给 Agent，做到「写到 src/api/ 时只看到 api 相关经验」。
#
# 设计：
# - 与 validate-code.sh 是同位面的两个 hook，互不依赖（validate 拦截 → exit 2；
#   本 hook 永远 exit 0，仅注入上下文）
# - 治理模式 off / harness 未装 / 命中 0 条 → 静默退出，不污染 Agent 输出
# - 失败永不抛错（stderr 简短提示，exit 0）

set -uo pipefail

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-${HARNESS_PROJECT_DIR:-$(pwd)}}"
HARNESS_DIR="$PROJECT_DIR/.harness"
HARNESS_BIN="$HARNESS_DIR/commands/harness"

if [ ! -d "$HARNESS_DIR" ] || [ ! -x "$HARNESS_BIN" ]; then
    exit 0
fi

# --- 治理模式：off → 跳过 -------------------------------------------------
MODE_CONFIG="$HARNESS_DIR/mode-config.json"
if [ -f "$MODE_CONFIG" ]; then
    MODE=$(python3 -c "
import json, sys
try:
    print(json.load(open(sys.argv[1])).get('mode', 'strict'))
except Exception:
    print('strict')
" "$MODE_CONFIG" 2>/dev/null || echo "strict")
    if [ "$MODE" = "off" ]; then
        exit 0
    fi
fi

# --- 解析 stdin（与 validate-code.sh 同协议）-----------------------------
INPUT="$(cat || true)"
if [ -z "$INPUT" ]; then
    exit 0
fi

# 一次解析出 file_path + content（content 取 new_string 或 content，二选一）
PARSED=$(printf '%s' "$INPUT" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    ti = d.get('tool_input', {}) or {}
    fp = ti.get('file_path', '') or ''
    # Write 用 content；Edit/MultiEdit 用 new_string；MultiEdit 还有 edits[].new_string
    content = ti.get('content') or ti.get('new_string') or ''
    if not content:
        edits = ti.get('edits') or []
        if isinstance(edits, list):
            content = '\n'.join(
                (e or {}).get('new_string', '') for e in edits if isinstance(e, dict)
            )
    print(json.dumps({'file_path': fp, 'content': content}, ensure_ascii=False))
except Exception:
    print('{}')
" 2>/dev/null || echo "{}")

# file_path 空 → 没法匹配 applies_to，直接退
FILE_PATH=$(printf '%s' "$PARSED" | python3 -c "
import json, sys
try:
    print(json.load(sys.stdin).get('file_path', ''))
except Exception:
    print('')
" 2>/dev/null || true)
if [ -z "$FILE_PATH" ]; then
    exit 0
fi

# 只对 src/ 下 .ts/.tsx/.d.ts 注入；其他文件没经验匹配意义
case "$FILE_PATH" in
    "$PROJECT_DIR"/*) REL_PATH="${FILE_PATH#$PROJECT_DIR/}" ;;
    /*)               REL_PATH="$FILE_PATH" ;;
    *)                REL_PATH="$FILE_PATH" ;;
esac
case "$REL_PATH" in
    src/*) ;;
    *) exit 0 ;;
esac
case "$REL_PATH" in
    *.ts|*.tsx|*.d.ts) ;;
    *) exit 0 ;;
esac

# --- 调 harness lesson match --stdin -----------------------------------
export HARNESS_PROJECT_DIR="$PROJECT_DIR"
# 把 PARSED 转成 lesson match --stdin 所期望的格式（已经一致），传相对路径过去
PAYLOAD=$(printf '%s' "$PARSED" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    d['file_path'] = sys.argv[1]  # 用相对路径，applies_to 配的是 'src/api/' 这种
    print(json.dumps(d, ensure_ascii=False))
except Exception:
    print('{}')
" "$REL_PATH" 2>/dev/null || echo "{}")

MATCHED=$(printf '%s' "$PAYLOAD" | "$HARNESS_BIN" lesson match --stdin --format plain --limit 5 2>/dev/null || true)
if [ -z "$MATCHED" ]; then
    exit 0
fi

# 命中 → 输出 hookSpecificOutput JSON 到 stdout
python3 -c "
import json, sys
matched = sys.argv[1]
header = '📚 与当前修改相关的团队经验（自动注入）:'
print(json.dumps({
    'hookSpecificOutput': {
        'hookEventName': 'PostToolUse',
        'additionalContext': header + chr(10) + matched,
    }
}))
" "$MATCHED"

exit 0
