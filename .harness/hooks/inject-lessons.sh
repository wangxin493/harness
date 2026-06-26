#!/usr/bin/env bash
# Harness 2.0 PostToolUse Hook —— 按修改文件动态注入相关 lesson
#
# 作用:Agent 写完 src/**/*.{ts,tsx,d.ts} 后,按 file_path + new_content
# 调 harness lesson match 召回相关经验,通过 hookSpecificOutput.additionalContext
# 推给 Agent,做到「写到 src/api/ 时只看到 api 相关经验」。
#
# 设计:
# - 与 validate-code.sh 是同位面的两个 hook,互不依赖(validate 拦截 → exit 2;
#   本 hook 永远 exit 0,仅注入上下文)
# - fast-path 由 _fast_path.sh 提供(source_root / include_ext / exclude_dirs /
#   mode==off 过滤);experience_market.enabled=0 时直接跳过
# - 治理模式 off / harness 未装 / 命中 0 条 → 静默退出,不污染 Agent 输出
# - 失败永不抛错(stderr 简短提示,exit 0)

# fast-path 引导:source .config.sh + 解 stdin + REL_PATH + bash 过滤 +
# should-validate 二次确认。不命中处 exit 0。
# shellcheck disable=SC1091
. "$(dirname -- "${BASH_SOURCE[0]}")/_fast_path.sh"

# experience_market 关掉 → 直接退(A1 接通点)
# 注:_fast_path.sh 不退此,因为 validate-code.sh 不看 EXPERIENCE_ENABLED;
# 这里在 fast-path 跑完后做最后一道开关。
if [ "${HARNESS_EXPERIENCE_ENABLED:-1}" = "0" ]; then
    exit 0
fi

# --- 解析 stdin 的 content(_fast_path 只取了 file_path,这里再取一次拿 content)---
# 一次解析出 file_path + content(content 取 new_string 或 content,二选一)
PARSED=$(printf '%s' "$INPUT" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    ti = d.get('tool_input', {}) or {}
    fp = ti.get('file_path', '') or ''
    # Write 用 content;Edit/MultiEdit 用 new_string;MultiEdit 还有 edits[].new_string
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

# --- 调 harness lesson match --stdin -----------------------------------
# 把 PARSED 转成 lesson match --stdin 所期望的格式,把绝对路径换成项目相对路径
# (applies_to 配的是 'src/api/' 这种相对路径,所以喂相对路径才能匹配上)
PAYLOAD=$(printf '%s' "$PARSED" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    d['file_path'] = sys.argv[1]
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
header = '📚 与当前修改相关的团队经验(自动注入):'
print(json.dumps({
    'hookSpecificOutput': {
        'hookEventName': 'PostToolUse',
        'additionalContext': header + chr(10) + matched,
    }
}))
" "$MATCHED"

exit 0
