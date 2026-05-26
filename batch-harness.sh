#!/bin/bash
# 批量执行 Harness 任务
# 用法: ./batch-harness.sh prd.txt

PRD_FILE="$1"

if [ -z "$PRD_FILE" ]; then
    echo "用法: $0 <prd文件>"
    echo ""
    echo "PRD 文件格式：每行一个需求描述"
    echo "例如："
    echo "  增加置顶笔记的功能"
    echo "  增加笔记归档功能"
    echo "  增加笔记搜索功能"
    exit 1
fi

if [ ! -f "$PRD_FILE" ]; then
    echo "错误: 文件不存在: $PRD_FILE"
    exit 1
fi

echo "============================================================"
echo "🚀 批量执行 Harness 任务"
echo "============================================================"
echo "PRD 文件: $PRD_FILE"
echo ""

TOTAL_TASKS=$(wc -l < "$PRD_FILE" | tr -d ' ')
COMPLETED=0
FAILED=0

# 创建日志目录
LOG_DIR="/tmp/harness-logs-$(date +%s)"
mkdir -p "$LOG_DIR"
echo "📁 日志目录: $LOG_DIR"

TASK_INDEX=0
while IFS= read -r requirement; do
    # 跳过空行和注释
    if [[ -z "$requirement" || "$requirement" =~ ^# ]]; then
        continue
    fi

    TASK_INDEX=$((TASK_INDEX + 1))
    echo ""
    echo "============================================================"
    echo "任务 $TASK_INDEX/$TOTAL_TASKS: $requirement"
    echo "============================================================"

    # 执行 harness（带实时日志）
    LOG_FILE="$LOG_DIR/task-${TASK_INDEX}.log"

    if ./harness.sh "$requirement" 2>&1 | tee "$LOG_FILE"; then
        echo "✅ 成功"
        ((COMPLETED++))
    else
        EXIT_CODE=$?
        echo "❌ 失败 (查看日志: $LOG_FILE, 退出码: $EXIT_CODE)"

        # 显示最后10行日志帮助诊断
        echo ""
        echo "📋 最后10行日志:"
        tail -10 "$LOG_FILE"

        ((FAILED++))
    fi
done < "$PRD_FILE"

echo ""
echo "============================================================"
echo "📊 执行总结"
echo "============================================================"
echo "总任务: $TOTAL_TASKS"
echo "成功: $COMPLETED"
echo "失败: $FAILED"
echo "日志目录: $LOG_DIR"
echo ""

if [ $FAILED -eq 0 ]; then
    echo "🎉 全部完成！"
    exit 0
else
    echo "⚠️  有 $FAILED 个任务失败"
    echo ""
    echo "查看失败任务的日志:"
    echo "  ls -la $LOG_DIR"
    echo "  cat $LOG_DIR/task-<序号>.log"
    exit 1
fi
