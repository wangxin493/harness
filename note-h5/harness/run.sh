#!/bin/bash
# Harness 启动脚本
# 使用系统 Python 3.12 运行 harness

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 查找可用的 Python 解释器
PYTHON_BIN=""
for cmd in python3.12 python3; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON_BIN="$cmd"
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "错误: 找不到 Python 解释器"
    echo "请确保已安装 Python 3.12 或 Python 3"
    exit 1
fi

# 切换到 harness 目录
cd "$SCRIPT_DIR"

# 运行 run.py
exec "$PYTHON_BIN" run.py "$@"