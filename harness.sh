#!/bin/bash
# Harness 快捷执行脚本

# 设置项目路径
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$(which python3.12 2>/dev/null || echo python3)"

# 检查参数
if [ -z "$1" ]; then
    echo "用法: ./harness.sh \"你的需求描述\""
    echo ""
    echo "示例:"
    echo "  ./harness.sh \"实现用户登录功能\""
    echo "  ./harness.sh \"创建笔记列表组件\""
    echo ""
    echo "其他命令:"
    echo "  ./harness.sh --status      # 查看系统状态"
    echo "  ./harness.sh --list-tasks  # 查看任务列表"
    echo "  ./harness.sh --validate    # 验证代码"
    exit 1
fi

# 执行 Harness
"$PYTHON_BIN" "$PROJECT_DIR/note-h5/harness/run.py" "$@"
