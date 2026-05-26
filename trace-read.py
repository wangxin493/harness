#!/usr/bin/env python3
"""
人类可读的 Trace 查看器
将技术化的 trace 记录翻译成易懂的中文说明
"""

import json
import sys
from pathlib import Path
from datetime import datetime

def format_time(iso_time: str) -> str:
    """格式化时间"""
    try:
        dt = datetime.fromisoformat(iso_time.replace('Z', '+00:00'))
        return dt.strftime('%H:%M:%S')
    except:
        return iso_time[:19]

def translate_agent(agent: str) -> str:
    """翻译 Agent 名称"""
    return {
        'planner': '📋 规划师',
        'coder': '💻 程序员',
        'reviewer': '🔍 审查员',
    }.get(agent, agent)

def translate_status(status: str) -> str:
    """翻译状态"""
    return {
        'completed': '✅ 完成',
        'failed': '❌ 失败',
        'success': '✅ 成功',
        'started': '🔄 开始',
        'pending': '⏳ 等待',
        'in_progress': '⏳ 进行中',
    }.get(status, status)

def format_duration(start: str, end: str) -> str:
    """计算持续时间"""
    try:
        start_dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
        end_dt = datetime.fromisoformat(end.replace('Z', '+00:00'))
        duration = (end_dt - start_dt).total_seconds()
        if duration < 1:
            return f"{int(duration * 1000)}毫秒"
        elif duration < 60:
            return f"{int(duration)}秒"
        else:
            mins = int(duration // 60)
            secs = int(duration % 60)
            return f"{mins}分{secs}秒"
    except:
        return "未知"

def explain_issue(issue: dict) -> str:
    """解释问题"""
    severity = issue.get('severity', 'info')
    category = issue.get('category', '')
    message = issue.get('message', '')
    suggestion = issue.get('suggestion', '')

    severity_icon = {
        'error': '🔴',
        'warning': '🟡',
        'info': '🔵',
    }.get(severity, '⚪')

    category_cn = {
        'type-safety': '类型安全',
        'architecture': '架构问题',
        'error-handling': '错误处理',
        'code-quality': '代码质量',
        'performance': '性能问题',
    }.get(category, category)

    result = f"\n{severity_icon} [{category_cn}] {message}\n"

    if suggestion:
        result += f"   💡 建议: {suggestion}\n"

    return result

def show_trace(trace_file: str):
    """展示 trace 记录"""

    with open(trace_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 基本信息
    print("=" * 60)
    print("📊 任务执行报告")
    print("=" * 60)
    print(f"\n任务: {data.get('input', {}).get('description', '未知任务')}")
    print(f"开始时间: {format_time(data.get('timestamp', ''))}")

    # 计算总时长
    if 'ended_at' in data:
        duration = format_duration(data['timestamp'], data['ended_at'])
        print(f"总耗时: {duration}")

    # 最终状态
    status = data.get('status', 'unknown')
    print(f"\n最终状态: {translate_status(status)}")

    if status == 'success':
        print("🎉 太棒了！任务顺利完成！")
    elif status == 'failed':
        print("⚠️  任务失败了，需要修复问题后重试")

    print("\n" + "=" * 60)
    print("📝 执行过程")
    print("=" * 60 + "\n")

    # 执行序列
    sequence = data.get('agent_sequence', [])
    prev_time = data.get('timestamp', '')

    for i, step in enumerate(sequence, 1):
        agent = translate_agent(step.get('agent', ''))
        step_status = translate_status(step.get('status', ''))
        step_time = format_time(step.get('timestamp', ''))

        print(f"{i}. {agent} - {step_time}")

        # 耗时
        if prev_time:
            duration = format_duration(prev_time, step.get('timestamp', ''))
            print(f"   耗时: {duration}")

        # 输出内容
        output = step.get('output', {})

        if step.get('agent') == 'planner':
            steps = output.get('steps', [])
            print(f"   计划: 拆分成 {len(steps)} 个步骤")
            for s in steps:
                print(f"      • {s.get('description', '')}")

        elif step.get('agent') == 'coder':
            file_path = output.get('file_path', '')
            code_len = output.get('code_length', 0)
            print(f"   生成了: {file_path}")
            print(f"   代码量: {code_len} 字符")

        elif step.get('agent') == 'reviewer':
            passed = output.get('passed', False)
            score = output.get('score', 0)
            issues = output.get('issues', [])

            if passed:
                print(f"   审查通过！评分: {score} 分")
                print("   🎉 代码质量符合要求！")
            else:
                print(f"   审查未通过 ❌ 评分: {score} 分")
                print(f"   发现了 {len(issues)} 个问题:")
                for issue in issues:
                    print(explain_issue(issue))

                suggestions = output.get('suggestions', [])
                if suggestions:
                    print(f"\n   💡 优化建议:")
                    for s in suggestions:
                        print(f"      • {s}")

        print()

        prev_time = step.get('timestamp', '')

    print("=" * 60)

def main():
    """主函数"""
    # 找到最新的 trace 文件
    trace_dir = Path('note-h5/trace')
    trace_files = list(trace_dir.glob('trace-*.json'))

    if not trace_files:
        print("还没有执行记录，先运行一次任务吧！")
        print("  ./harness.sh \"你的需求\"")
        return

    # 按时间排序，取最新的
    latest_trace = max(trace_files, key=lambda p: p.stat().st_mtime)

    # 命令行参数
    if len(sys.argv) > 1:
        trace_file = sys.argv[1]
    else:
        trace_file = str(latest_trace)

    if not Path(trace_file).exists():
        print(f"文件不存在: {trace_file}")
        return

    show_trace(trace_file)

if __name__ == '__main__':
    main()
