#!/usr/bin/env python3
"""
Harness 交互式客户端

提供交互式界面来使用 Harness 系统
"""

import sys
import os
from pathlib import Path

# 添加路径
harness_dir = Path(__file__).parent.parent / "note-h5" / "harness"
sys.path.insert(0, str(harness_dir))
os.chdir(str(Path(__file__).parent.parent / "note-h5"))

from core import AutoExecutor
from llm_client import get_client

def run_harness_interactive():
    """交互式运行 Harness"""
    print("=" * 70)
    print("Harness 交互式模式")
    print("=" * 70)
    print("\n请输入你的需求（输入 'exit' 退出）:\n")

    # 创建客户端
    try:
        client = get_client()
        print(f"✓ 已连接到: {client.get_info()['model']}")
    except Exception as e:
        print(f"✗ 连接失败: {e}")
        return

    # 创建执行器
    executor = AutoExecutor()

    # 设置处理函数
    executor.set_handlers(
        planner=lambda u, s: client.chat(u, s, temperature=0.1),
        coder=lambda u, s: client.chat(u, s, temperature=0.1),
        reviewer=lambda c, s: client.chat(c, s, temperature=0.1),
    )

    while True:
        try:
            user_input = input(">>> ").strip()

            if not user_input:
                continue

            if user_input.lower() in ["exit", "quit", "q"]:
                print("\n再见！")
                break

            if user_input == "--status":
                from harness import TaskManager, TraceRecorder
                tm = TaskManager(Path(harness_dir).parent)
                tasks = tm.list_tasks()
                print(f"\n任务统计: {len(tasks)} 个")
                continue

            # 执行任务
            print(f"\n执行: {user_input}\n")
            result = executor.execute_task(user_input, "P2")

            status = result["status"]
            if status == "completed":
                print(f"\n✓ 任务完成！")
            else:
                print(f"\n✗ 任务失败")
                for error in result.get("errors", []):
                    print(f"  - {error}")

            print()

        except KeyboardInterrupt:
            print("\n\n操作已取消")
            break
        except Exception as e:
            print(f"\n错误: {e}")

if __name__ == "__main__":
    run_harness_interactive()
