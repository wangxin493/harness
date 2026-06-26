#!/usr/bin/env bash
# Harness 2.0 PostToolUse Hook —— 接 Claude Code / Comate / Ducc 的 Edit/Write 后置钩子
#
# 协议:
# - stdin: tool_use JSON(含 tool_input.file_path、tool_input.new_string/content)
# - stderr+exit 2: 拦截,附 hookSpecificOutput JSON 给 Agent
# - exit 0: 验证通过
#
# 设计要点:
# - 统一调用 .harness/commands/harness validate <file>,不再直连 lib/validator.py
# - fast-path 由 _fast_path.sh 提供(source_root / include_ext / exclude_dirs /
#   mode==off 过滤),命中不到才进 Python CLI,省去冷启动开销。
# - .config.sh 缺失/过期 → 兜底走原 CLI 路径(向后兼容)。
# - 仅校验 src/ 下的 .ts/.tsx/.d.ts(默认;可被 rules.yaml scanner.* 覆盖)

# fast-path 引导:解 PROJECT_DIR / .config.sh / stdin / REL_PATH,跑 source_root
# / ext / exclude_dirs / should-validate 过滤,不命中处 exit 0。
# shellcheck disable=SC1091
. "$(dirname -- "${BASH_SOURCE[0]}")/_fast_path.sh"

# --- 调 CLI 验证 ----------------------------------------------------------
RESULT=$("$HARNESS_BIN" validate --json "$REL_PATH" 2>&1)
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    exit 0
fi

# 验证失败 → 输出 hookSpecificOutput,退出码 2
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
