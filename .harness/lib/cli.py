#!/usr/bin/env python3
"""Harness 2.0 CLI —— P0 最小版

仅实现执行清单要求的命令：
- harness scan [--full]            扫描项目代码（生成 dep graph + project context）
- harness validate <file>          验证单文件（AST + 架构 + 禁用导入）

P1 会扩展：mode / status / lesson / sync / generate / fix / doctor / upgrade
（设计文档 §五、执行清单 P1 #9–#11）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 让 `lib.xxx` 能被 import：CLI 通过 commands/harness wrapper 启动时
# .harness/ 已加入 sys.path；这里兜底一下，便于直接 `python lib/cli.py`。
_HARNESS_DIR = Path(__file__).resolve().parent.parent
if str(_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_HARNESS_DIR))

import click  # noqa: E402

from lib.scanner import IncrementalScanner  # noqa: E402
from lib.validator import CodeValidator  # noqa: E402


def _resolve_project_dir() -> Path:
    """允许通过 HARNESS_PROJECT_DIR 覆盖（hook 使用），默认 cwd。"""
    env = os.environ.get("HARNESS_PROJECT_DIR")
    if env:
        return Path(env).resolve()
    return Path.cwd().resolve()


@click.group()
@click.version_option(version=(_HARNESS_DIR / "VERSION").read_text(encoding="utf-8").strip()
                      if (_HARNESS_DIR / "VERSION").exists() else "0.0.0",
                      prog_name="harness")
def cli() -> None:
    """Harness 2.0 — 代码治理框架"""


# -- scan ------------------------------------------------------------------


@cli.command("scan")
@click.option("--full", is_flag=True, help="强制全量扫描（忽略增量缓存）")
@click.option("--json", "as_json", is_flag=True, help="以 JSON 输出 summary")
def scan_cmd(full: bool, as_json: bool) -> None:
    """扫描项目，产出 dep graph + project context。"""
    project_dir = _resolve_project_dir()
    scanner = IncrementalScanner(project_dir=project_dir)
    result = scanner.scan(force_full=full)
    summary = result.summary()

    if as_json:
        click.echo(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    click.echo("✅ 扫描完成")
    click.echo(f"   文件数: {summary['total_files']}")
    click.echo(f"   组件:   {summary['total_components']}")
    click.echo(f"   Hooks:  {summary['total_hooks']}")
    click.echo(f"   APIs:   {summary['total_apis']}")
    click.echo(f"   类型:   {summary['total_types']}")


# -- validate --------------------------------------------------------------


@cli.command("validate")
@click.argument("file_path", type=str)
@click.option("--json", "as_json", is_flag=True, help="以 JSON 输出问题列表")
def validate_cmd(file_path: str, as_json: bool) -> None:
    """验证单个文件（AST + 架构层 + 禁用导入）。"""
    project_dir = _resolve_project_dir()
    validator = CodeValidator(project_dir=project_dir)
    issues = validator.validate_file(file_path)

    if as_json:
        click.echo(json.dumps(
            [_issue_to_dict(i) for i in issues],
            ensure_ascii=False,
            indent=2,
        ))
    else:
        if not issues:
            click.echo(f"✅ {file_path} 验证通过")
        else:
            click.echo(f"❌ {file_path} 发现 {len(issues)} 个问题:")
            for issue in issues:
                line = f":{issue.line}" if issue.line else ""
                click.echo(f"   [{issue.severity.upper()}] {file_path}{line}  {issue.message}")
                if issue.suggestion:
                    click.echo(f"      建议: {issue.suggestion}")

    # 非零退出码：有 error 级问题
    if any(i.severity == "error" for i in issues):
        sys.exit(2)


def _issue_to_dict(issue) -> dict:
    return {
        "rule_id": issue.rule_id,
        "severity": issue.severity,
        "category": issue.category,
        "message": issue.message,
        "file": issue.file,
        "line": issue.line,
        "suggestion": issue.suggestion,
    }


if __name__ == "__main__":
    cli()
