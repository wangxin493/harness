#!/bin/bash
# Harness 执行入口
# 支持两种用法：
#   1. ./harness.sh "需求描述字符串"
#   2. ./harness.sh <prd文件路径>   ← 自动读取文件内容

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HARNESS_DIR="$SCRIPT_DIR/harness"

if [ -z "$1" ]; then
    echo "用法:"
    echo "  ./harness.sh \"需求描述\""
    echo "  ./harness.sh example-prd.txt"
    exit 1
fi

# 智能识别：第一个参数如果是文件路径且文件存在，则读取其内容
if [ -f "$1" ]; then
    echo "[harness.sh] 检测到文件参数: $1"
    TASK_DESCRIPTION=$(cat "$1")
    echo "[harness.sh] 已读取 $(echo "$TASK_DESCRIPTION" | wc -l) 行需求"
elif [ -f "$SCRIPT_DIR/$1" ]; then
    # 相对路径尝试
    echo "[harness.sh] 检测到文件参数: $SCRIPT_DIR/$1"
    TASK_DESCRIPTION=$(cat "$SCRIPT_DIR/$1")
    echo "[harness.sh] 已读取 $(echo "$TASK_DESCRIPTION" | wc -l) 行需求"
else
    TASK_DESCRIPTION="$1"
fi

# 查找 Python
PYTHON_BIN=""
for cmd in python3.12 python3; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON_BIN="$cmd"
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "错误: 找不到 Python 解释器"
    exit 1
fi

cd "$HARNESS_DIR"
exec "$PYTHON_BIN" run.py "$TASK_DESCRIPTION"
