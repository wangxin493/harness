#!/usr/bin/env python3
"""
统一验证管道
运行所有验证检查并汇总结果
"""

import sys
import subprocess
import json
from pathlib import Path
from typing import Dict, List, Tuple
from dataclasses import dataclass, asdict


@dataclass
class CheckResult:
    """检查结果"""
    name: str
    status: str  # passed, failed, skipped
    exit_code: int
    output: str
    error: str = ""


def run_check(name: str, command: List[str], cwd: Path) -> CheckResult:
    """运行单个检查"""
    print(f"运行: {name}")
    print(f"命令: {' '.join(command)}")
    print("-" * 60)

    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=300,  # 5分钟超时
        )

        if result.returncode == 0:
            print("✓ 通过")
        else:
            print("✗ 失败")

        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr)

        return CheckResult(
            name=name,
            status="passed" if result.returncode == 0 else "failed",
            exit_code=result.returncode,
            output=result.stdout,
            error=result.stderr,
        )

    except subprocess.TimeoutExpired:
        print("✗ 超时 (5分钟)")
        return CheckResult(
            name=name, status="failed", exit_code=124, output="", error="Timeout"
        )
    except FileNotFoundError:
        print("⚠ 跳过 (文件不存在)")
        return CheckResult(
            name=name, status="skipped", exit_code=0, output="", error=""
        )
    except Exception as e:
        print(f"✗ 错误: {e}")
        return CheckResult(
            name=name, status="failed", exit_code=1, output="", error=str(e)
        )


def main():
    """主函数"""
    root_dir = Path(__file__).parent.parent
    scripts_dir = root_dir / "scripts"
    verify_dir = scripts_dir / "verify"

    print("=" * 60)
    print("验证管道 - 开始")
    print(f"工作目录: {root_dir}")
    print("=" * 60)
    print()

    results: List[CheckResult] = []

    # 1. 依赖检查
    results.append(
        run_check(
            "依赖层级检查",
            ["python3", str(scripts_dir / "lint-deps.py")],
            root_dir,
        )
    )
    print()

    # 2. 代码质量检查
    results.append(
        run_check(
            "代码质量检查",
            ["python3", str(scripts_dir / "lint-quality.py")],
            root_dir,
        )
    )
    print()

    # 3. 端到端验证 (如果有脚本)
    if verify_dir.exists():
        for script in sorted(verify_dir.glob("*.py")):
            if script.name.startswith("test_"):
                results.append(
                    run_check(
                        f"验证: {script.name}",
                        ["python3", str(script)],
                        root_dir,
                    )
                )
                print()

    # 4. 汇总结果
    print("=" * 60)
    print("验证管道 - 汇总")
    print("=" * 60)

    total = len(results)
    passed = sum(1 for r in results if r.status == "passed")
    failed = sum(1 for r in results if r.status == "failed")
    skipped = sum(1 for r in results if r.status == "skipped")

    print(f"总检查数: {total}")
    print(f"通过: {passed}")
    print(f"失败: {failed}")
    print(f"跳过: {skipped}")
    print()

    # 详细结果表格
    print("详细结果:")
    print("-" * 60)
    for result in results:
        status_icon = {"passed": "✓", "failed": "✗", "skipped": "⚠"}.get(
            result.status, "?"
        )
        print(f"{status_icon} {result.name:30s} [{result.status.upper()}]")

    print()

    # 保存结果到 JSON
    output_file = root_dir / "harness" / "trace" / f"validate-{timestamp()}.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(
            {
                "timestamp": timestamp(),
                "summary": {
                    "total": total,
                    "passed": passed,
                    "failed": failed,
                    "skipped": skipped,
                },
                "results": [asdict(r) for r in results],
            },
            f,
            indent=2,
        )
    print(f"结果已保存: {output_file}")

    # 返回码
    print()
    if failed > 0:
        print("❌ 验证失败")
        return 1
    elif skipped > 0:
        print("⚠️ 部分检查跳过")
        return 0
    else:
        print("✓ 所有检查通过")
        return 0


def timestamp() -> str:
    """生成时间戳"""
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d-%H%M%S")


if __name__ == "__main__":
    sys.exit(main())
