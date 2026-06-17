#!/usr/bin/env bash
# Harness 2.0 PostToolUse Hook —— 接 Claude Code / Comate / Ducc 的 Edit/Write 后置钩子
#
# 协议：
# - stdin: tool_use JSON（含 tool_input.file_path、tool_input.new_string/content）
# - stderr+exit 2: 拦截，附 hookSpecificOutput JSON 给 Agent
# - exit 0: 验证通过
#
# 设计要点（执行清单 P0 #5）：
# - 统一调用 .harness/commands/harness validate <file>，不再直连 lib/validator.py
# - 治理模式 off → 直接放行（mode-config.json）
# - 仅校验 src/ 下的 .ts/.tsx/.d.ts

set -uo pipefail

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-${HARNESS_PROJECT_DIR:-$(pwd)}}"
HARNESS_DIR="$PROJECT_DIR/.harness"
HARNESS_BIN="$HARNESS_DIR/commands/harness"

if [ ! -d "$HARNESS_DIR" ] || [ ! -x "$HARNESS_BIN" ]; then
    # Harness 未初始化：放行
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

# --- 解析 stdin -----------------------------------------------------------
INPUT="$(cat || true)"
if [ -z "$INPUT" ]; then
    exit 0
fi

FILE_PATH=$(printf '%s' "$INPUT" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get('tool_input', {}).get('file_path', ''))
except Exception:
    print('')
" 2>/dev/null || true)

if [ -z "$FILE_PATH" ]; then
    exit 0
fi

# 转项目相对路径
case "$FILE_PATH" in
    "$PROJECT_DIR"/*) REL_PATH="${FILE_PATH#$PROJECT_DIR/}" ;;
    /*)               REL_PATH="$FILE_PATH" ;;  # 不在项目里：直接传给 CLI 让其报错
    *)                REL_PATH="$FILE_PATH" ;;
esac

# 仅 src/*.ts / *.tsx / *.d.ts
case "$REL_PATH" in
    src/*.ts|src/*.tsx|src/*.d.ts|src/**/*.ts|src/**/*.tsx|src/**/*.d.ts) : ;;
    *) exit 0 ;;
esac

# --- 调 CLI 验证 ----------------------------------------------------------
export HARNESS_PROJECT_DIR="$PROJECT_DIR"
RESULT=$("$HARNESS_BIN" validate --json "$REL_PATH" 2>&1)
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    exit 0
fi

# 验证失败 → 输出 hookSpecificOutput，退出码 2
REASON=$(printf '%s' "$RESULT" | python3 -c "
import json, sys
text = sys.stdin.read()
try:
    issues = json.loads(text)
    lines = [f\"[{i['severity'].upper()}] {i.get('file','')}:{i.get('line') or '?'}  {i.get('message','')}\" + (f\"\\n      建议: {i['suggestion']}\" if i.get('suggestion') else '') for i in issues]
    print('\\n'.join(lines) if lines else text)
except Exception:
    print(text)
" 2>/dev/null || printf '%s' "$RESULT")

# 用 python 把 reason 安全编码成 JSON 字符串
python3 -c "
import json, sys
reason = sys.argv[1]
print(json.dumps({
    'hookSpecificOutput': {
        'hookEventName': 'PostToolUse',
        'decision': 'block',
        'reason': reason,
    }
}))
" "$REASON" >&2

exit 2
