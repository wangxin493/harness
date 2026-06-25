#!/usr/bin/env bash
# Harness 2.0 PostToolUse Hook —— 接 Claude Code / Comate / Ducc 的 Edit/Write 后置钩子
#
# 协议：
# - stdin: tool_use JSON（含 tool_input.file_path、tool_input.new_string/content）
# - stderr+exit 2: 拦截，附 hookSpecificOutput JSON 给 Agent
# - exit 0: 验证通过
#
# 设计要点：
# - 统一调用 .harness/commands/harness validate <file>，不再直连 lib/validator.py
# - fast-path（A2）：source hooks/.config.sh 后用纯 bash 做扩展名 / source_root /
#   mode==off 过滤；命中不到才进 Python CLI，省去冷启动开销。
# - .config.sh 缺失 / 过期 → 兜底走原 CLI 路径（向后兼容）。
# - 仅校验 src/ 下的 .ts/.tsx/.d.ts（默认；可被 rules.yaml scanner.* 覆盖）

set -uo pipefail

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-${HARNESS_PROJECT_DIR:-$(pwd)}}"
HARNESS_DIR="$PROJECT_DIR/.harness"
HARNESS_BIN="$HARNESS_DIR/commands/harness"

if [ ! -d "$HARNESS_DIR" ] || [ ! -x "$HARNESS_BIN" ]; then
    # Harness 未初始化：放行
    exit 0
fi

# --- fast-path 配置：harness generate 写出来的 .config.sh ------------------
HARNESS_CONFIG="$HARNESS_DIR/hooks/.config.sh"
# 默认值（.config.sh 缺失时兜底；尽量贴近 rules.yaml 默认）
HARNESS_SOURCE_ROOT='src'
HARNESS_INCLUDE_EXT=('.ts' '.tsx' '.d.ts')
HARNESS_EXCLUDE_DIRS=('node_modules' 'dist' 'build' '.git' '.harness')
HARNESS_EXCLUDE_GLOBS=()
HARNESS_MODE='strict'
HARNESS_EXPERIENCE_ENABLED=1
# shellcheck disable=SC1090
[ -f "$HARNESS_CONFIG" ] && . "$HARNESS_CONFIG"

# --- 治理模式：off → 跳过（先读 .config.sh，再读 mode-config.json 兜底）---
if [ "${HARNESS_MODE:-strict}" = "off" ]; then
    exit 0
fi
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

# --- fast-path：纯 bash 过滤 ----------------------------------------------
# 不在 source_root 下 → 跳过
if [ -n "${HARNESS_SOURCE_ROOT:-}" ]; then
    case "$REL_PATH" in
        "${HARNESS_SOURCE_ROOT}"/*) : ;;  # 命中
        *) exit 0 ;;
    esac
fi

# 不在 include_extensions 之列 → 跳过
_ext_ok=0
for _ext in "${HARNESS_INCLUDE_EXT[@]:-}"; do
    [ -z "$_ext" ] && continue
    case "$REL_PATH" in
        *"$_ext") _ext_ok=1; break ;;
    esac
done
[ $_ext_ok -eq 0 ] && exit 0

# 命中 exclude_dirs → 跳过
for _xd in "${HARNESS_EXCLUDE_DIRS[@]:-}"; do
    [ -z "$_xd" ] && continue
    case "$REL_PATH" in
        "$_xd"/*|*/"$_xd"/*) exit 0 ;;
    esac
done

# CLI 二次确认（exclude_globs 等复杂规则交给 CLI 判断）
export HARNESS_PROJECT_DIR="$PROJECT_DIR"
if ! "$HARNESS_BIN" should-validate "$REL_PATH" >/dev/null 2>&1; then
    exit 0
fi

# --- 调 CLI 验证 ----------------------------------------------------------
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
