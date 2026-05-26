#!/usr/bin/env python3
"""
Harness 主入口 - 支持多LLM API版本

提供命令行接口来使用 AI Harness 框架

使用方式:
    python3 harness/run.py "实现用户登录功能"
    python3 harness/run.py --model deepseek-coder "实现用户注册功能"
"""

import sys
import json
import argparse
import os
import re
import yaml
from pathlib import Path

# 加载 .env 配置
try:
    from dotenv import load_dotenv
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        load_dotenv(env_file)
except ImportError:
    pass  # python-dotenv 未安装时跳过

# 添加 harness 目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from core import AutoExecutor, Orchestrator
from tools.validator import CodeValidator
from llm_client import create_client, AliBailianClient, get_client


def load_config():
    """加载 config.yaml 配置"""
    config_file = Path(__file__).parent / "config.yaml"
    if config_file.exists():
        with open(config_file, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {"llm": {"enabled": False}}


def run_with_llm(
    user_request: str,
    priority: str = "P2",
    model: str = None,
    provider: str = None,
) -> int:
    """
    使用 LLM API 的执行模式

    这个模式会调用 LLM API，实现完整的自动化流程
    """
    print("=" * 70)
    print("AI Harness Engineering - LLM API 模式")
    print("=" * 70)
    print(f"\n任务: {user_request}")
    print(f"优先级: {priority}")

    # 加载配置
    config = load_config()
    llm_config = config.get("llm", {})
    enabled = llm_config.get("enabled", False)

    # 优先级: 参数 > config.yaml > 默认值
    provider = provider or llm_config.get("provider", "alibailian")
    model = model or llm_config.get("model", "")

    print(f"提供商: {provider}")
    print(f"模型: {model}")

    try:
        # 创建 LLM 客户端
        client = create_client(provider=provider, model=model)
        print(f"✓ {provider.capitalize()} API 配置正常\n")

        info = client.get_info()
        print(f"  类型: {info['type']}")
        print(f"  模型: {info['model']}")
        print(f"  API 地址: {info['base_url']}")
        print()

    except Exception as e:
        print(f"\n✗ 错误: {provider.capitalize()} 客户端初始化失败: {e}")
        if provider == "deepseek":
            print(f"\n请设置环境变量 DEEPSEEK_API_KEY:")
            print(f"  export DEEPSEEK_API_KEY=your-api-key")
        elif provider == "alibailian":
            print(f"\n请设置环境变量 DASHSCOPE_API_KEY:")
            print(f"  export DASHSCOPE_API_KEY=your-api-key")
        return 1

    # 创建处理函数
    def planner_handler(user_request: str, system_prompt: str, code_context: dict = None) -> str:
        """Planner 处理函数"""
        print(f"\n[Planner] 正在调用 {provider.capitalize()} 生成执行计划...")

        # 添加现有代码上下文
        if code_context:
            print(f"  [Planner] 代码上下文: {code_context['summary']}")

        try:
            # 添加示例来引导模型返回正确格式
            example_json = {
                "task_description": "示例任务",
                "estimated_steps": 1,
                "steps": [
                    {
                        "step": 1,
                        "agent": "coder",
                        "description": "创建组件",
                        "target_file": "src/components/Example.tsx",
                        "requirements": ["使用函数组件"]
                    },
                    {
                        "step": 2,
                        "agent": "reviewer",
                        "description": "审查代码质量"
                    }
                ]
            }

            # 添加现有代码信息到提示词
            enhanced_prompt = user_request

            if code_context:
                enhanced_prompt += f"\n\n【现有代码结构】\n"
                enhanced_prompt += f"组件数量: {code_context['summary']['total_components']}\n"
                enhanced_prompt += f"Hooks 数量: {code_context['summary']['total_hooks']}\n"
                enhanced_prompt += f"APIs 数量: {code_context['summary']['total_apis']}\n"
                enhanced_prompt += f"类型定义数量: {code_context['summary'].get('total_types', 0)}\n"

                # 添加主要组件列表
                if code_context.get('components'):
                    enhanced_prompt += f"\n现有组件: "
                    components = code_context['components'][:10]  # 最多10个
                    enhanced_prompt += ', '.join([c['name'] for c in components])

                # 添加主要 Hooks 列表
                if code_context.get('hooks'):
                    enhanced_prompt += f"\n现有 Hooks: "
                    hooks = code_context['hooks'][:10]  # 最多10个
                    enhanced_prompt += ', '.join([h['name'] for h in hooks])

                # 添加主要 APIs 列表
                if code_context.get('apis'):
                    enhanced_prompt += f"\n现有 APIs: "
                    apis = code_context['apis'][:10]  # 最多10个
                    enhanced_prompt += ', '.join([a['name'] for a in apis])

                # 添加类型定义列表
                if code_context.get('types'):
                    enhanced_prompt += f"\n现有类型定义: "
                    types = code_context['types'][:15]  # 最多15个
                    enhanced_prompt += ', '.join([t['name'] for t in types])

            enhanced_prompt += f"\n\n⚠️ 重要：必须只返回纯 JSON 格式，不要添加任何额外文本、注释或代码块标记。"
            enhanced_prompt += f"\n\n参考以下格式：\n{json.dumps(example_json, ensure_ascii=False)}"

            # 重试机制：最多重试 3 次
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    # 使用 chat_json 强制 JSON 格式
                    result_dict = client.chat_json(enhanced_prompt, system_prompt, temperature=0.1)
                    result = json.dumps(result_dict, ensure_ascii=False)
                    print(f"[Planner] ✓ 计划生成完成")
                    return result
                except RuntimeError as e:
                    print(f"[Planner] ⚠ 尝试 {attempt + 1}/{max_retries} 失败: {e}")

                    # 最后一次尝试失败，尝试普通 chat
                    if attempt == max_retries - 1:
                        print(f"[Planner] ⚠ JSON 模式全部失败，尝试普通模式")
                        result = client.chat(enhanced_prompt, system_prompt, temperature=0.1)
                        print(f"[Planner] ✓ 计划生成完成（普通模式）")
                        return result

                    # 简化提示词重试
                    if attempt == 1:
                        print(f"[Planner] ⚠ 简化提示词重试...")
                        enhanced_prompt = f"{user_request}\n\n只返回 JSON 格式，参考：\n{json.dumps(example_json, ensure_ascii=False)}"
        except Exception as e:
            print(f"[Planner] ✗ 生成失败: {e}")
            raise

    def coder_handler(user_prompt: str, system_prompt: str, code_context: dict = None) -> str:
        """Coder 处理函数"""
        print(f"\n[Coder] 正在调用 {provider.capitalize()} 生成代码...")

        # 添加现有代码上下文
        if code_context:
            print(f"  [Coder] 代码上下文: {code_context['summary']}")

        try:
            # 添加示例引导
            example_json = {
                "file_path": "src/components/Example.tsx",
                "code": "export function Example() {\n  return <div>Example</div>;\n}",
                "imports": ["react"],
                "explanation": "示例组件"
            }

            # 添加现有代码信息
            enhanced_prompt = user_prompt

            # 【关键修复】读取目标文件的现有内容
            existing_file_content = None
            target_file_match = re.search(r'文件路径:\s*(\S+)', user_prompt)
            if target_file_match:
                target_file = target_file_match.group(1).strip()
                target_path = Path(__file__).parent.parent / target_file
                if target_path.exists():
                    try:
                        existing_file_content = target_path.read_text(encoding="utf-8")
                        print(f"  [Coder] 读取现有文件: {target_file} ({len(existing_file_content)} 字符)")
                    except Exception as e:
                        print(f"  [Coder] 警告: 无法读取现有文件: {e}")

            if code_context:
                enhanced_prompt += f"\n\n【重要：使用现有代码结构】\n"
                enhanced_prompt += f"必须使用现有的 Hooks、APIs 和类型定义，不要创建重复的！\n\n"

                # 添加可用类型定义
                if code_context.get('types'):
                    enhanced_prompt += "现有类型定义（必须使用）:\n"
                    for type_def in code_context['types'][:20]:
                        kind = type_def.get('kind', 'interface')
                        fields = type_def.get('fields', [])
                        if fields:
                            enhanced_prompt += f"- {type_def['name']} ({kind}): {', '.join(fields[:10])}\n"
                        else:
                            enhanced_prompt += f"- {type_def['name']} ({kind})\n"

                # 添加可用 Hooks 和它们的接口
                if code_context.get('hooks'):
                    enhanced_prompt += "\n现有 Hooks 及其接口:\n"
                    for hook in code_context['hooks'][:15]:  # 最多15个
                        enhanced_prompt += f"- {hook['name']}({', '.join(hook.get('parameters', []))})\n"
                        if hook.get('returns'):
                            enhanced_prompt += f"  返回: {hook['returns']}\n"

                # 添加可用 APIs
                if code_context.get('apis'):
                    enhanced_prompt += "\n现有 APIs:\n"
                    for api in code_context['apis'][:15]:  # 最多15个
                        ret_type = api.get('return_type', 'void')
                        async_prefix = "async " if api.get('is_async') else ""
                        enhanced_prompt += f"- {async_prefix}{api['name']}(): {ret_type}\n"

                # 添加可用组件
                if code_context.get('components'):
                    enhanced_prompt += "\n可用组件（可以引用）:\n"
                    for comp in code_context['components'][:10]:
                        enhanced_prompt += f"- {comp['name']}: {comp['file_path']}\n"

            # 【关键修复】添加现有文件内容
            if existing_file_content:
                enhanced_prompt += f"\n\n【重要：现有文件内容 - 请在此基础上修改】\n"
                enhanced_prompt += f"以下是目标文件的现有代码，请在此基础上进行修改：\n\n"
                enhanced_prompt += f"```typescript\n{existing_file_content}\n```\n\n"
                enhanced_prompt += f"⚠️ 关键要求：\n"
                enhanced_prompt += f"1. 保留现有代码中已有的函数、组件、类型定义\n"
                enhanced_prompt += f"2. 只添加新功能，不要删除或重复现有代码\n"
                enhanced_prompt += f"3. 如果需要修改现有代码，请保持其结构并只做必要的修改\n"
                enhanced_prompt += f"4. 确保新增代码与现有代码兼容\n"

            enhanced_prompt += f"\n\n⚠️ 重要：必须只返回纯 JSON 格式，不要添加任何额外文本、注释或代码块标记。"
            enhanced_prompt += f"\n\n参考以下格式：\n{json.dumps(example_json, ensure_ascii=False)}"

            # 重试机制：最多重试 3 次
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    # 使用 chat_json 强制 JSON 格式
                    result_dict = client.chat_json(enhanced_prompt, system_prompt, temperature=0.1)
                    result = json.dumps(result_dict, ensure_ascii=False)
                    print(f"[Coder] ✓ 代码生成完成")
                    return result
                except RuntimeError as e:
                    print(f"[Coder] ⚠ 尝试 {attempt + 1}/{max_retries} 失败: {e}")

                    # 最后一次尝试失败，尝试普通 chat
                    if attempt == max_retries - 1:
                        print(f"[Coder] ⚠ JSON 模式全部失败，尝试普通模式")
                        result = client.chat(enhanced_prompt, system_prompt, temperature=0.1)
                        print(f"[Coder] ✓ 代码生成完成（普通模式）")
                        return result

                    # 简化提示词重试
                    if attempt == 1:
                        print(f"[Coder] ⚠ 简化提示词重试...")
                        # 保留最核心的部分
                        enhanced_prompt = enhanced_prompt[:2000] + f"\n\n⚠️ 只返回 JSON 格式，参考：\n{json.dumps(example_json, ensure_ascii=False)}"
        except Exception as e:
            print(f"[Coder] ✗ 生成失败: {e}")
            raise

    def reviewer_handler(review_content: str, system_prompt: str) -> str:
        """Reviewer 处理函数"""
        print(f"\n[Reviewer] 正在调用 {provider.capitalize()} 审查代码...")
        try:
            # 添加示例引导
            example_json = {
                "passed": True,
                "summary": "代码质量良好",
                "issues": [],
                "score": 95
            }
            enhanced_prompt = f"{review_content}\n\n⚠️ 重要：必须只返回纯 JSON 格式，不要添加任何额外文本、注释或代码块标记。"
            enhanced_prompt += f"\n\n参考以下格式：\n{json.dumps(example_json, ensure_ascii=False)}"

            # 重试机制：最多重试 3 次
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    # 使用 chat_json 强制 JSON 格式
                    result_dict = client.chat_json(enhanced_prompt, system_prompt, temperature=0.1)
                    result = json.dumps(result_dict, ensure_ascii=False)
                    print(f"[Reviewer] ✓ 审查完成")
                    return result
                except RuntimeError as e:
                    print(f"[Reviewer] ⚠ 尝试 {attempt + 1}/{max_retries} 失败: {e}")

                    # 最后一次尝试失败，尝试普通 chat
                    if attempt == max_retries - 1:
                        print(f"[Reviewer] ⚠ JSON 模式全部失败，尝试普通模式")
                        result = client.chat(enhanced_prompt, system_prompt, temperature=0.1)
                        print(f"[Reviewer] ✓ 审查完成（普通模式）")
                        return result

                    # 简化提示词重试
                    if attempt == 1:
                        print(f"[Reviewer] ⚠ 简化提示词重试...")
                        enhanced_prompt = f"{review_content}\n\n只返回 JSON 格式，参考：\n{json.dumps(example_json, ensure_ascii=False)}"
        except Exception as e:
            print(f"[Reviewer] ✗ 审查失败: {e}")
            raise

    # 创建自动执行器并注入处理函数
    executor = AutoExecutor()
    executor.set_handlers(
        planner=planner_handler,
        coder=coder_handler,
        reviewer=reviewer_handler,
    )

    # 执行任务
    result = executor.execute_task(user_request, priority)

    # 显示结果
    print("\n" + "=" * 70)
    print("执行结果")
    print("=" * 70)
    print(f"状态: {result['status']}")
    print(f"任务ID: {result['task_id']}")
    print(f"追踪ID: {result['trace_id']}")

    if result.get("plan"):
        print(f"\n执行计划: {result['plan']['estimated_steps']} 个步骤")

    print(f"\n步骤执行情况:")
    for step in result.get("steps", []):
        if step is None:
            print(f"  ⚠ [未知] 步骤数据缺失")
            continue
        status_icon = "✓" if step.get("success") else "✗"
        retry_info = (
            f" (重试 {step.get('retries', 0)} 次)"
            if step.get("retries", 0) > 0
            else ""
        )
        print(
            f"  {status_icon} [{step.get('agent')}] {step.get('description')}{retry_info}"
        )

    if result.get("errors"):
        print(f"\n错误:")
        for error in result["errors"]:
            print(f"  ✗ {error}")

    # 返回退出码
    return 0 if result["status"] == "completed" else 1


def run_direct(user_request: str, priority: str = "P2") -> None:
    """
    直接模式（模拟数据，用于测试）

    注意：这是一个框架示例，不实际调用 LLM
    """
    print("=" * 70)
    print("AI Harness Engineering - 直接模式（模拟）")
    print("=" * 70)
    print(f"\n任务: {user_request}")
    print(f"优先级: {priority}")
    print(f"\n提示: 本模式使用模拟数据，不会实际生成代码\n")

    # 创建自动执行器
    executor = AutoExecutor()

    # 执行任务
    result = executor.execute_task(user_request, priority)

    # 显示结果
    print("\n" + "=" * 70)
    print("执行结果")
    print("=" * 70)
    print(f"状态: {result['status']}")
    print(f"任务ID: {result['task_id']}")

    if result.get("plan"):
        print(f"\n执行计划: {result['plan']['estimated_steps']} 个步骤")

    print(f"\n步骤执行情况:")
    for step in result.get("steps", []):
        status_icon = "✓" if step.get("success") else "✗"
        print(f"  {status_icon} [{step.get('agent')}] {step.get('description')}")

    if result.get("errors"):
        print(f"\n错误:")
        for error in result["errors"]:
            print(f"  ✗ {error}")

    # 返回退出码
    return 0 if result["status"] == "completed" else 1


def show_status() -> None:
    """显示系统状态"""
    print("=" * 70)
    print("AI Harness Engineering - 系统状态")
    print("=" * 70)

    orchestrator = Orchestrator()

    # 任务统计
    tasks = orchestrator.task_manager.list_tasks()
    print(f"\n任务统计:")
    print(f"  总数: {len(tasks)}")

    status_count = {}
    for task in tasks:
        status = task.status.value
        status_count[status] = status_count.get(status, 0) + 1

    for status, count in status_count.items():
        print(f"  {status}: {count}")

    # 追踪统计
    trace_dir = orchestrator.base_dir / "trace"
    traces = list(trace_dir.glob("trace-*.json"))
    print(f"\n追踪记录: {len(traces)} 条")

    # 经验统计
    memory_dir = orchestrator.base_dir / "memory"
    lessons = list(memory_dir.glob("lessons-*.md"))
    print(f"经验记录: {len(lessons)} 条")

    # 模式统计
    patterns = orchestrator.memory_store.get_patterns()
    print(f"代码模式: {len(patterns)} 个")


def run_validation(file_path: str = None) -> None:
    """运行代码验证"""
    print("=" * 70)
    print("AI Harness Engineering - 代码验证")
    print("=" * 70)

    validator = CodeValidator()

    if file_path:
        print(f"\n验证文件: {file_path}")
    else:
        print(f"\n验证整个项目")

    results = validator.validate_all(file_path)

    print(f"\n总体结果: {'✓ 通过' if results['passed'] else '✗ 未通过'}")
    print(f"总体评分: {results.get('score', 0)}/100")

    print(f"\n检查项:")
    for check_name, check_result in results.get("checks", {}).items():
        icon = "✓" if check_result.get("passed") else "✗"
        print(f"  {icon} {check_name}: ", end="")
        if "score" in check_result:
            print(f"{check_result['score']}/100")
        else:
            print(f"{'通过' if check_result.get('passed') else '未通过'}")

    if results.get("errors"):
        print(f"\n错误:")
        for error in results["errors"]:
            print(f"  ✗ {error}")


def list_tasks(status: str = None) -> None:
    """列出所有任务"""
    print("=" * 70)
    print("AI Harness Engineering - 任务列表")
    print("=" * 70)

    orchestrator = Orchestrator()

    if status:
        print(f"\n筛选状态: {status}")

    tasks = orchestrator.task_manager.list_tasks()

    if not tasks:
        print("\n没有任务")
        return

    print(f"\n共 {len(tasks)} 个任务:\n")

    for task in tasks:
        if status and task.status.value != status:
            continue

        status_icon = {
            "pending": "○",
            "in_progress": "◐",
            "completed": "●",
            "failed": "✗"
        }.get(task.status.value, "?")

        print(f"{status_icon} [{task.priority}] {task.description}")
        print(f"  ID: {task.task_id}")
        print(f"  创建: {task.created_at}")
        print(f"  状态: {task.status.value}")
        print(f"  检查点: {len(task.checkpoints)}")
        print()


def main() -> int:
    """主函数"""
    parser = argparse.ArgumentParser(
        description="AI Harness Engineering - AI 驱动的软件开发自动化平台",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 使用默认 LLM (从 config.yaml 读取)
  python3 harness/run.py "实现用户登录功能"

  # 指定提供商和模型
  python3 harness/run.py --provider deepseek --model deepseek-coder "实现用户注册功能"

  # 直接模式（模拟数据，用于测试）
  python3 harness/run.py --direct "实现用户登录功能"

  # 其他命令
  python3 harness/run.py --status
  python3 harness/run.py --validate
  python3 harness/run.py --list-tasks
        """
    )

    parser.add_argument(
        "task",
        nargs="?",
        help="要执行的任务描述"
    )

    parser.add_argument(
        "-p", "--priority",
        default="P2",
        choices=["P0", "P1", "P2", "P3"],
        help="任务优先级 (默认: P2)"
    )

    parser.add_argument(
        "--status",
        action="store_true",
        help="显示系统状态"
    )

    parser.add_argument(
        "--validate",
        action="store_true",
        help="运行代码验证"
    )

    parser.add_argument(
        "--validate-file",
        metavar="FILE",
        help="验证指定文件"
    )

    parser.add_argument(
        "--list-tasks",
        action="store_true",
        help="列出所有任务"
    )

    parser.add_argument(
        "--list-tasks-by-status",
        metavar="STATUS",
        choices=["pending", "in_progress", "completed", "failed"],
        help="按状态列出任务"
    )

    parser.add_argument(
        "--direct",
        action="store_true",
        help="使用直接模式（模拟数据，不调用 LLM）"
    )

    parser.add_argument(
        "--provider",
        default=None,
        choices=["alibailian", "deepseek"],
        help="LLM 提供商 (默认: 从 config.yaml 读取)"
    )

    parser.add_argument(
        "--model",
        default=None,
        help="LLM 模型名称 (默认: 从 config.yaml 读取)"
    )

    args = parser.parse_args()

    try:
        if args.status:
            show_status()
        elif args.validate:
            run_validation()
        elif args.validate_file:
            run_validation(args.validate_file)
        elif args.list_tasks:
            list_tasks()
        elif args.list_tasks_by_status:
            list_tasks(args.list_tasks_by_status)
        elif args.task:
            if args.direct:
                return run_direct(args.task, args.priority)
            else:
                return run_with_llm(args.task, args.priority, args.model, args.provider)
        else:
            parser.print_help()
            return 0

        return 0

    except KeyboardInterrupt:
        print("\n\n操作已取消")
        return 130
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
