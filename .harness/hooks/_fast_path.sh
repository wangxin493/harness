#!/usr/bin/env bash
# Harness 2.0 PostToolUse Hook —— 公共 fast-path 引导脚本
#
# 用法:被 validate-code.sh / inject-lessons.sh 在头部 `. _fast_path.sh` 引入。
# 完成的事:
#   1. 解出 PROJECT_DIR / HARNESS_DIR / HARNESS_BIN,harness 未装时 exit 0
#   2. source `.config.sh` 拿到 HARNESS_SOURCE_ROOT / INCLUDE_EXT / EXCLUDE_DIRS /
#      MODE / EXPERIENCE_ENABLED 等 fast-path 变量(缺失走兜底默认值)
#   3. 解 stdin 拿到 FILE_PATH,转成 REL_PATH(项目相对)
#   4. 纯 bash 做 source_root / include_ext / exclude_dirs 过滤,不命中 exit 0
#   5. 跑 `harness should-validate` 二次确认(exclude_globs 等复杂规则交 CLI)
#
# 引入完毕后调用方可拿到:
#   - PROJECT_DIR / HARNESS_DIR / HARNESS_BIN
#   - FILE_PATH(原始绝对/相对)+ REL_PATH(项目相对)
#   - INPUT(原始 stdin 文本)
#   - 全部 HARNESS_* 配置变量
# 共用脚本本身只控制 fast-path,不解析 tool_input 的 content/new_string,
# 那部分由 inject-lessons.sh 自己再 parse 一次(它需要 content 喂给 lesson match)。
#
# 设计原则:
# - 任何错误都 exit 0(放行/静默),不污染 Agent 上下文
# - 不依赖 bash 4+ 特性,兼容 macOS bash 3.2

set -uo pipefail

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-${HARNESS_PROJECT_DIR:-$(pwd)}}"
# 去掉 PROJECT_DIR 末尾斜杠,避免 case "$PROJECT_DIR"/* 匹配双斜杠出错
PROJECT_DIR="${PROJECT_DIR%/}"
HARNESS_DIR="$PROJECT_DIR/.harness"

# npm 包化：HARNESS_BIN 优先使用 node_modules/.bin/harness，fallback 全局 PATH
# 仍允许外部通过 HARNESS_BIN 环境变量显式覆盖（installer 注入、测试脚本等）
if [ -z "${HARNESS_BIN:-}" ]; then
    _NM_BIN="$PROJECT_DIR/node_modules/.bin/harness"
    if [ -x "$_NM_BIN" ]; then
        HARNESS_BIN="$_NM_BIN"
    else
        HARNESS_BIN="$(command -v harness 2>/dev/null || true)"
    fi
fi

if [ ! -d "$HARNESS_DIR" ] || [ -z "${HARNESS_BIN:-}" ]; then
    # Harness 未初始化或 CLI 找不到:放行
    exit 0
fi

# --- fast-path 配置 -------------------------------------------------------
# 默认值(.config.sh 缺失时兜底;尽量贴近 rules.yaml 默认)
HARNESS_CONFIG="$HARNESS_DIR/hooks/.config.sh"
HARNESS_SOURCE_ROOT='src'
HARNESS_INCLUDE_EXT=('.ts' '.tsx' '.d.ts')
HARNESS_EXCLUDE_DIRS=('node_modules' 'dist' 'build' '.git' '.harness')
HARNESS_MODE='strict'
HARNESS_EXPERIENCE_ENABLED=1
# shellcheck disable=SC1090
[ -f "$HARNESS_CONFIG" ] && . "$HARNESS_CONFIG"

# --- 治理模式:off → 跳过 -------------------------------------------------
# mode 由 .config.sh 提供;harness mode <x> 会同步刷 .config.sh,无需再 fork
# python3 解析 mode-config.json 兜底。
if [ "${HARNESS_MODE:-strict}" = "off" ]; then
    exit 0
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
    /*)               REL_PATH="$FILE_PATH" ;;  # 绝对路径但不在项目里:交给 CLI 报错
    *)                REL_PATH="$FILE_PATH" ;;
esac

# --- 纯 bash 过滤 ---------------------------------------------------------
# 不在 source_root 下 → 跳过
if [ -n "${HARNESS_SOURCE_ROOT:-}" ]; then
    case "$REL_PATH" in
        "${HARNESS_SOURCE_ROOT}"/*) : ;;
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

# CLI 二次确认(exclude_globs 等复杂规则交给 CLI 判断)
export HARNESS_PROJECT_DIR="$PROJECT_DIR"
if ! "$HARNESS_BIN" should-validate "$REL_PATH" >/dev/null 2>&1; then
    exit 0
fi
