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
from typing import Any, Dict, List, Optional

# 让 `lib.xxx` 能被 import：CLI 通过 commands/harness wrapper 启动时
# .harness/ 已加入 sys.path；这里兜底一下，便于直接 `python lib/cli.py`。
_HARNESS_DIR = Path(__file__).resolve().parent.parent
if str(_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_HARNESS_DIR))

import click  # noqa: E402

from lib.adapter import Generator  # noqa: E402
from lib.doctor import Doctor, plan_upgrade  # noqa: E402
from lib.experience_market import ExperienceMarket  # noqa: E402
from lib.fixer import Fixer  # noqa: E402
from lib.global_check import GlobalChecker  # noqa: E402
from lib.init_resolver import InitResolver  # noqa: E402
from lib.installer import get_installer  # noqa: E402
from lib.mode_manager import GovernanceMode, ModeManager, ValidationContext  # noqa: E402
from lib.probe import probe_project  # noqa: E402
from lib.scanner import IncrementalScanner  # noqa: E402
from lib.template import (  # noqa: E402
    TemplateError,
    TemplateGenerator,
    supported_kinds,
)
from lib.validator import CodeValidator  # noqa: E402


# 当前支持的 agent 集合（installer 注册表的对外白名单）。
# claude / ducc / baidu-cc 共用 Claude Code 协议，复用同一 installer。
_SUPPORTED_AGENTS = ["claude", "ducc", "baidu-cc"]


def _resolve_project_dir() -> Path:
    """允许通过 HARNESS_PROJECT_DIR 覆盖（hook 使用），默认 cwd。"""
    env = os.environ.get("HARNESS_PROJECT_DIR")
    if env:
        return Path(env).resolve()
    return Path.cwd().resolve()


def _build_mode_manager(project_dir: Path) -> ModeManager:
    """从 .harness/ 加载当前治理模式。"""
    return ModeManager(harness_dir=project_dir / ".harness")


@click.group()
@click.version_option(version=(_HARNESS_DIR / "VERSION").read_text(encoding="utf-8").strip()
                      if (_HARNESS_DIR / "VERSION").exists() else "0.0.0",
                      prog_name="harness")
def cli() -> None:
    """Harness 2.0 — 代码治理框架"""


# -- scan ------------------------------------------------------------------


@cli.command("scan")
@click.option("--full", is_flag=True, help="强制全量扫描（忽略增量缓存）")
@click.option("--no-generate", is_flag=True,
              help="扫描后跳过自动生成 .harness/generated/*.md")
@click.option("--json", "as_json", is_flag=True, help="以 JSON 输出 summary")
@click.option("--watch", is_flag=True,
              help="常驻进程：监听 src/ 文件变化，去抖后增量 scan（不自动 generate）")
@click.option("--debounce", type=float, default=0.5, show_default=True,
              help="--watch 下，事件聚合到这个秒数才触发一次扫描")
def scan_cmd(full: bool, no_generate: bool, as_json: bool,
             watch: bool, debounce: float) -> None:
    """扫描项目，产出 dep graph + project context；默认随后自动 generate。

    --watch 模式：前台常驻，监听 src/ 下的 .ts/.tsx/.d.ts；不调用 generate
    （那是 rules/lessons/mode 的派生物，开发动作不会触发它）。
    """
    project_dir = _resolve_project_dir()

    if watch:
        if as_json:
            click.echo("⚠️  --watch 与 --json 互斥，已忽略 --json", err=True)
        if not no_generate:
            click.echo("ℹ️  --watch 下不会自动 generate；如需刷新规则文件请单独跑 `harness generate`",
                       err=True)
        from lib.watch import watch_loop
        sys.exit(watch_loop(project_dir, debounce_sec=debounce))

    scanner = IncrementalScanner(project_dir=project_dir)

    # A1 接通点:experience_market.auto_pull → scan 前自动 sync
    market_cfg = _load_experience_config(project_dir)
    if market_cfg.get("enabled", True) and market_cfg.get("auto_pull", True):
        try:
            _build_market(project_dir).sync(remote=False)
        except Exception as exc:
            if not as_json:
                click.echo(f"⚠️  auto_pull 失败,继续 scan: {exc}", err=True)

    result = scanner.scan(force_full=full)
    summary = result.summary()

    generated_files: List[str] = []
    if not no_generate:
        try:
            gen_result = _build_generator(project_dir).generate_all()
            generated_files = gen_result.written
        except Exception as exc:  # generate 失败不应阻断 scan
            if not as_json:
                click.echo(f"⚠️  scan 完成但 generate 失败: {exc}", err=True)

    if as_json:
        payload = dict(summary)
        payload["generated"] = generated_files
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    click.echo("✅ 扫描完成")
    click.echo(f"   文件数: {summary['total_files']}")
    click.echo(f"   组件:   {summary['total_components']}")
    click.echo(f"   Hooks:  {summary['total_hooks']}")
    click.echo(f"   APIs:   {summary['total_apis']}")
    click.echo(f"   类型:   {summary['total_types']}")
    if generated_files:
        click.echo(f"📝 已生成: {', '.join(generated_files)}")
    elif no_generate:
        click.echo("   （已跳过 generate；如需手动生成: harness generate）")


# -- validate --------------------------------------------------------------


# P1 #11 截断阈值默认值；rules.yaml 中 `validation.max_issues_per_file` 可覆盖
_DEFAULT_MAX_ISSUES = 5


def _load_max_issues_per_file(project_dir: Path) -> int:
    """从 rules.yaml 的 validation.max_issues_per_file 读截断阈值，缺失返回默认值。"""
    rules_file = project_dir / ".harness" / "rules.yaml"
    if not rules_file.exists():
        return _DEFAULT_MAX_ISSUES
    try:
        import yaml
        data = yaml.safe_load(rules_file.read_text(encoding="utf-8")) or {}
    except Exception:
        return _DEFAULT_MAX_ISSUES
    raw = (data.get("validation") or {}).get("max_issues_per_file")
    if not isinstance(raw, int) or raw <= 0:
        return _DEFAULT_MAX_ISSUES
    return raw


@cli.command("validate")
@click.argument("file_path", type=str)
@click.option("--json", "as_json", is_flag=True, help="以 JSON 输出问题列表（不截断）")
def validate_cmd(file_path: str, as_json: bool) -> None:
    """验证单个文件（AST + 架构层 + 禁用导入）。

    人读输出最多展示 N 条 issue（N 由 rules.yaml 的
    validation.max_issues_per_file 控制，默认 5），其余截断并提示
    `harness validate <file> --json` 查看完整列表。
    --json 输出始终完整。
    """
    project_dir = _resolve_project_dir()
    mode_manager = _build_mode_manager(project_dir)

    # OFF 模式：直接放行（hook 也会先于此短路，这里再保一层）
    if not mode_manager.should_validate():
        if as_json:
            click.echo(json.dumps([], ensure_ascii=False))
        else:
            click.echo(f"⏸️  治理模式为 off，跳过验证: {file_path}")
        return

    validator = CodeValidator(project_dir=project_dir)
    raw_issues = validator.validate_file(file_path)
    issues = ValidationContext(mode_manager).filter_issues(raw_issues)

    if as_json:
        # JSON：永远完整，不截断（CI / Agent / Hook 消费方需要完整列表）
        click.echo(json.dumps(
            [_issue_to_dict(i) for i in issues],
            ensure_ascii=False,
            indent=2,
        ))
    else:
        if not issues:
            click.echo(f"✅ {file_path} 验证通过 (mode={mode_manager.current_mode.value})")
        else:
            total = len(issues)
            limit = _load_max_issues_per_file(project_dir)
            shown = issues[:limit]
            click.echo(f"❌ {file_path} 发现 {total} 个问题 (mode={mode_manager.current_mode.value}):")
            for issue in shown:
                line = f":{issue.line}" if issue.line else ""
                click.echo(f"   [{issue.severity.upper()}] {file_path}{line}  {issue.message}")
                if issue.suggestion:
                    click.echo(f"      建议: {issue.suggestion}")
            if total > limit:
                click.echo(
                    f"   … 还有 {total - limit} 条已截断；"
                    f"完整列表: harness validate {file_path} --json"
                )

    # 非零退出码：根据 mode 决定（relaxed 仅 error 拦截，strict 拦截一切非 info）
    if any(mode_manager.should_block(i.severity) for i in issues):
        sys.exit(2)


@cli.command("should-validate")
@click.argument("file_path", type=str)
def should_validate_cmd(file_path: str) -> None:
    """判断指定文件是否应当走 harness validate（供 hook shell 调用）。

    退出码：
      0  → 应该校验（hook 接着调 `harness validate`）
      1  → 跳过（不在 scanner.source_root 内 / 扩展名不在 include_extensions
           / 命中 exclude_globs / 治理模式为 off）

    刻意不打印任何东西：hook 用 exit code 决策，输出会污染 hookSpecificOutput。
    """
    project_dir = _resolve_project_dir()
    if _file_should_validate(project_dir, file_path):
        sys.exit(0)
    sys.exit(1)


def _file_should_validate(project_dir: Path, file_path: str) -> bool:
    """判断文件是否在 rules.yaml scanner 约束的范围内（hook 用）。

    流程：
    1) 治理模式 off → False
    2) 转项目相对路径（绝对路径剥前缀；不在项目内一律 False）
    3) 命中 scanner.exclude_dirs / exclude_globs → False
    4) 不在 scanner.source_root 下 → False
    5) 后缀不在 scanner.include_extensions 内 → False
    """
    try:
        manager = _build_mode_manager(project_dir)
        if not manager.should_validate():
            return False
    except Exception:
        # mode-config 读取异常时不影响放行判断，按"参与校验"处理
        pass

    rules = _load_rules_safe(project_dir)
    scanner_cfg = (rules.get("scanner") or {}) if isinstance(rules, dict) else {}
    source_root = (scanner_cfg.get("source_root") or "src").rstrip("/")
    include_exts = tuple(scanner_cfg.get("include_extensions")
                          or [".ts", ".tsx", ".d.ts"])
    exclude_globs = list(scanner_cfg.get("exclude_globs") or [])
    exclude_dirs = set(scanner_cfg.get("exclude_dirs") or [])

    # 1) 绝对路径剥前缀；不在项目内 → False
    p = Path(file_path)
    if p.is_absolute():
        try:
            rel = p.resolve().relative_to(project_dir).as_posix()
        except ValueError:
            return False
    else:
        rel = file_path.replace("\\", "/")

    # 2) source_root 前缀
    src_prefix = source_root + "/"
    if not (rel == source_root or rel.startswith(src_prefix)):
        return False

    # 3) exclude_dirs（任一路径分量命中即排除）
    parts = rel.split("/")
    if any(part in exclude_dirs for part in parts):
        return False

    # 4) exclude_globs
    import fnmatch
    if any(fnmatch.fnmatch(rel, pat) for pat in exclude_globs):
        return False

    # 5) 扩展名（支持组合扩展名 .d.ts）
    name = rel.rsplit("/", 1)[-1].lower()
    if not any(name.endswith(ext.lower()) for ext in include_exts):
        return False

    return True


def _load_rules_safe(project_dir: Path) -> dict:
    rules_file = project_dir / ".harness" / "rules.yaml"
    if not rules_file.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(rules_file.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


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


# -- fix -------------------------------------------------------------------


@cli.command("fix")
@click.argument("file_path", type=str)
@click.option("--apply", "do_apply", is_flag=True,
              help="应用 patch 到工作树（默认 dry-run，仅打印 diff）")
@click.option("--json", "as_json", is_flag=True, help="以 JSON 输出 patches/instructions")
def fix_cmd(file_path: str, do_apply: bool, as_json: bool) -> None:
    """修复单文件可机械修复的问题（P1：仅 import-forbidden）。

    默认 dry-run：打印 unified diff 与人读 instructions，不动文件。
    --apply：应用 patch；非 git 仓库会被拒绝；apply 前会用 git stash 留备份。
    """
    project_dir = _resolve_project_dir()
    fixer = Fixer(project_dir=project_dir)
    try:
        result = fixer.fix_file(file_path, apply=do_apply)
    except RuntimeError as exc:
        click.echo(f"❌ {exc}", err=True)
        sys.exit(3)

    if as_json:
        click.echo(json.dumps({
            "patches": [
                {"file": p.file, "diff": p.diff, "rule_ids": p.rule_ids}
                for p in result.patches
            ],
            "instructions": [
                {
                    "rule_id": i.rule_id,
                    "file": i.file,
                    "line": i.line,
                    "message": i.message,
                    "suggestion": i.suggestion,
                }
                for i in result.instructions
            ],
            "applied": result.applied,
            "stash_ref": result.stash_ref,
        }, ensure_ascii=False, indent=2))
        return

    if not result.patches and not result.instructions:
        click.echo(f"✅ {file_path} 无可修复问题")
        return

    if result.patches:
        if result.applied:
            click.echo(f"✅ 已应用 {len(result.patches)} 个 patch（备份 stash: {result.stash_ref or '工作树原本干净'}）")
        else:
            click.echo(f"📝 dry-run：以下 patch 未应用（加 --apply 落盘）")
        for patch in result.patches:
            click.echo(patch.diff, nl=False)
            if not patch.diff.endswith("\n"):
                click.echo("")

    if result.instructions:
        click.echo(f"ℹ️  {len(result.instructions)} 个问题需手工处理：")
        for ins in result.instructions:
            line = f":{ins.line}" if ins.line else ""
            click.echo(f"   [{ins.rule_id}] {ins.file}{line}  {ins.message}")
            if ins.suggestion:
                click.echo(f"      建议: {ins.suggestion}")


# -- new (模板生成) --------------------------------------------------------


@cli.command("new")
@click.argument("kind", type=click.Choice(supported_kinds()))
@click.argument("name", type=str)
@click.option("--force", is_flag=True,
              help="目标文件已存在或同名冲突时,仍强制创建/覆盖")
@click.option("--path", "path_override", default=None,
              help="自定义落盘目录(POSIX 相对,默认从 rules.yaml 取)")
@click.option("--json", "as_json", is_flag=True, help="以 JSON 输出结果")
def new_cmd(kind: str, name: str, force: bool,
            path_override: Optional[str], as_json: bool) -> None:
    """生成已符合规则的新文件骨架:component / page / hook / service / type。

    示例:
      harness new component UserCard      → src/components/UserCard.tsx
      harness new page TodoListPage       → src/pages/TodoListPage.tsx
      harness new hook useTodos           → src/hooks/useTodos.ts
      harness new service userService     → src/api/userService.ts
      harness new type User               → src/types/User.ts

    冲突保护:目标文件已存在或 project-context.json 命中同名 → 拒绝,
    用 --force 强制创建。命名错误会直接给建议名,不落盘。
    """
    project_dir = _resolve_project_dir()
    gen = TemplateGenerator(project_dir=project_dir)

    try:
        result = gen.generate(
            kind=kind, name=name, force=force, path_override=path_override,
        )
    except TemplateError as exc:
        if as_json:
            click.echo(json.dumps(
                {"error": str(exc)}, ensure_ascii=False, indent=2,
            ))
        else:
            click.echo(f"❌ {exc}", err=True)
        sys.exit(2)

    if as_json:
        click.echo(json.dumps({
            "file": result.file,
            "kind": result.kind,
            "name": result.name,
            "written": result.written,
            "skipped_reason": result.skipped_reason,
            "warnings": result.warnings,
        }, ensure_ascii=False, indent=2))
        if not result.written:
            sys.exit(1)
        return

    if not result.written:
        click.echo(f"⏸️  未创建:{result.skipped_reason}", err=True)
        sys.exit(1)

    click.echo(f"✅ 已创建 {kind}:{result.file}")
    for w in result.warnings:
        click.echo(f"   ⚠️  {w}")
    click.echo("   下一步:打开文件填写 TODO;保存时 PostToolUse hook 会校验。")


# -- check (全项目维度) -----------------------------------------------------


@cli.command("check")
@click.option("--cycles/--no-cycles", default=True, show_default=True,
              help="是否检查循环依赖")
@click.option("--unused/--no-unused", default=True, show_default=True,
              help="是否检查死代码（导出但无引用）")
@click.option("--json", "as_json", is_flag=True, help="以 JSON 输出问题列表")
def check_cmd(cycles: bool, unused: bool, as_json: bool) -> None:
    """跑全项目维度的检查（依赖 scan 产物）。

    单文件维度由 `harness validate` 负责；本命令只跑需要看全图的检查：
      - cycle           : 文件级循环依赖（Tarjan）
      - unused-export   : 导出但无人引用，entry_points 豁免

    退出码：发现 error → 2；只有 warning/info → 0。被 mode_manager 过滤后再判定。
    """
    project_dir = _resolve_project_dir()
    mode_manager = _build_mode_manager(project_dir)

    if not mode_manager.should_validate():
        if as_json:
            click.echo(json.dumps([], ensure_ascii=False))
        else:
            click.echo("⏸️  治理模式为 off，跳过全项目检查")
        return

    checker = GlobalChecker(project_dir=project_dir)
    raw = checker.run(enable_cycles=cycles, enable_unused=unused)

    # 没有 scan 产物时给出可执行提示
    graph_file = project_dir / ".harness" / "context" / "dependency-graph.json"
    if not graph_file.exists():
        click.echo("⚠️  尚未扫描，先跑 `harness scan` 再 check。", err=True)
        sys.exit(1)

    issues = ValidationContext(mode_manager).filter_issues(raw.issues)

    if as_json:
        click.echo(json.dumps(
            [_global_issue_to_dict(i) for i in issues],
            ensure_ascii=False, indent=2,
        ))
    else:
        if not issues:
            click.echo(f"✅ 全项目检查通过 (mode={mode_manager.current_mode.value})")
        else:
            click.echo(f"❌ 共 {len(issues)} 个问题 (mode={mode_manager.current_mode.value}):")
            for i in issues:
                click.echo(f"   [{i.severity.upper()}] {i.file}  {i.message}")
                if i.suggestion:
                    click.echo(f"      建议: {i.suggestion}")

    if any(mode_manager.should_block(i.severity) for i in issues):
        sys.exit(2)


def _global_issue_to_dict(issue) -> dict:
    return {
        "rule_id": issue.rule_id,
        "severity": issue.severity,
        "category": issue.category,
        "message": issue.message,
        "file": issue.file,
        "line": issue.line,
        "suggestion": issue.suggestion,
        "extra": issue.extra,
    }


# -- mode / status ---------------------------------------------------------


@cli.command("mode")
@click.argument("target", type=click.Choice(["strict", "relaxed", "off", "toggle", "show"]),
                required=False, default="show")
@click.option("--json", "as_json", is_flag=True, help="以 JSON 输出当前模式信息")
def mode_cmd(target: str, as_json: bool) -> None:
    """查看或切换治理模式。

    用法：
      harness mode               # 显示当前模式（同 mode show）
      harness mode strict        # 切到 strict
      harness mode relaxed       # 切到 relaxed
      harness mode off           # 切到 off
      harness mode toggle        # 循环切换 strict→relaxed→off→strict
    """
    project_dir = _resolve_project_dir()
    mm = _build_mode_manager(project_dir)

    if target == "show":
        info = mm.get_mode_info()
    elif target == "toggle":
        mm.toggle_mode()
        info = mm.get_mode_info()
    else:
        mm.set_mode(GovernanceMode(target))
        info = mm.get_mode_info()

    if as_json:
        click.echo(json.dumps(info, ensure_ascii=False, indent=2))
        return

    click.echo(f"🎚️  治理模式: {info['mode']}")
    click.echo(f"   {info['description']}")
    click.echo(f"   启用规则: {info['enabled_rules_count']} "
               f"(error={info['error_rules']} / warning={info['warning_rules']} / "
               f"auto-fix={info['auto_fix_rules']})")
    click.echo(f"   配置文件: {info['config_file']}")


@cli.command("status")
@click.option("--json", "as_json", is_flag=True, help="以 JSON 输出全局状态")
def status_cmd(as_json: bool) -> None:
    """显示 Harness 全局状态：版本 / 模式 / 上次扫描概况。"""
    project_dir = _resolve_project_dir()
    harness_dir = project_dir / ".harness"
    mm = _build_mode_manager(project_dir)

    version_file = _HARNESS_DIR / "VERSION"
    version = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else "0.0.0"

    # 上次 scan 概况：读 scan-metadata.json（不存在就标记 never_scanned）
    scan_meta_file = harness_dir / "context" / "scan-metadata.json"
    scan_info: dict
    if scan_meta_file.exists():
        try:
            data = json.loads(scan_meta_file.read_text(encoding="utf-8"))
            scan_info = {
                "updated_at": data.get("updated_at"),
                "files_tracked": len(data.get("files", {})),
            }
        except (json.JSONDecodeError, OSError):
            scan_info = {"error": "scan-metadata.json 损坏"}
    else:
        scan_info = {"never_scanned": True}

    payload = {
        "version": version,
        "project_dir": str(project_dir),
        "harness_dir": str(harness_dir),
        "mode": mm.get_mode_info(),
        "scan": scan_info,
    }

    if as_json:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    click.echo(f"📦 Harness {version}")
    click.echo(f"   项目: {project_dir}")
    click.echo(f"   模式: {payload['mode']['mode']} — {payload['mode']['description']}")
    click.echo(f"   规则: {payload['mode']['enabled_rules_count']} 启用")
    if scan_info.get("never_scanned"):
        click.echo(f"   扫描: 尚未扫描（运行 harness scan）")
    elif scan_info.get("error"):
        click.echo(f"   扫描: ⚠️  {scan_info['error']}")
    else:
        click.echo(f"   扫描: {scan_info['files_tracked']} 文件 "
                   f"(updated_at={scan_info.get('updated_at') or 'unknown'})")


# -- lesson / sync ---------------------------------------------------------


def _build_market(project_dir: Path) -> ExperienceMarket:
    return ExperienceMarket(harness_dir=project_dir / ".harness")


def _load_experience_config(project_dir: Path) -> Dict[str, Any]:
    """读 rules.yaml `experience_market.*` 配置;缺失走出厂默认。"""
    defaults = {
        "enabled": True,
        "auto_pull": True,
        "auto_push": False,
        "max_lessons": 10,
        "min_score": 0.3,
    }
    rules_file = project_dir / ".harness" / "rules.yaml"
    if not rules_file.exists():
        return defaults
    try:
        import yaml
        data = yaml.safe_load(rules_file.read_text(encoding="utf-8")) or {}
    except Exception:
        return defaults
    market_cfg = (data.get("experience_market") or {})
    if not isinstance(market_cfg, dict):
        return defaults
    out = dict(defaults)
    out.update({k: v for k, v in market_cfg.items() if k in defaults})
    return out


def _build_generator(project_dir: Path) -> Generator:
    version = (
        (_HARNESS_DIR / "VERSION").read_text(encoding="utf-8").strip()
        if (_HARNESS_DIR / "VERSION").exists() else "0.0.0"
    )
    return Generator(harness_dir=project_dir / ".harness", version=version)


@cli.group("lesson")
def lesson_group() -> None:
    """经验市场（本地 .harness/memory/lessons/，共享 .harness-shared/lessons/）。"""


@lesson_group.command("list")
@click.option("--json", "as_json", is_flag=True, help="以 JSON 输出")
def lesson_list(as_json: bool) -> None:
    """列出本地所有经验。"""
    market = _build_market(_resolve_project_dir())
    lessons = market.list_lessons()

    if as_json:
        from dataclasses import asdict
        click.echo(json.dumps(
            [asdict(l) for l in lessons],
            ensure_ascii=False,
            indent=2,
        ))
        return

    if not lessons:
        click.echo("（本地无经验；harness sync 可从 .harness-shared/ 拉取）")
        return
    click.echo(f"📚 共 {len(lessons)} 条经验：")
    for l in lessons:
        kw = (",".join(l.keywords)) if l.keywords else "-"
        click.echo(f"   [{l.id}] [{l.severity}] {l.title}")
        click.echo(f"          category={l.category}  keywords={kw}  by {l.author} @ {l.created_at}")


@lesson_group.command("show")
@click.argument("lesson_id", type=str)
@click.option("--json", "as_json", is_flag=True, help="以 JSON 输出")
def lesson_show(lesson_id: str, as_json: bool) -> None:
    """查看一条经验的完整内容。"""
    market = _build_market(_resolve_project_dir())
    lesson = market.get_lesson(lesson_id)
    if lesson is None:
        click.echo(f"❌ 未找到 lesson: {lesson_id}", err=True)
        sys.exit(1)

    if as_json:
        from dataclasses import asdict
        click.echo(json.dumps(asdict(lesson), ensure_ascii=False, indent=2))
        return

    click.echo(f"📖 [{lesson.id}] {lesson.title}")
    click.echo(f"   category={lesson.category}  severity={lesson.severity}")
    click.echo(f"   keywords={lesson.keywords}")
    click.echo(f"   applies_to={lesson.applies_to}")
    click.echo(f"   by {lesson.author} @ {lesson.created_at}"
               + (f"  (expires {lesson.expires_at})" if lesson.expires_at else ""))
    click.echo("")
    click.echo(lesson.content)


@lesson_group.command("add")
@click.option("--title", required=True, help="标题（必填）")
@click.option("--content", required=True, help="正文（建议 Markdown）")
@click.option("--category", default="general", show_default=True)
@click.option("--severity",
              type=click.Choice(["error", "warning", "info"]),
              default="warning", show_default=True)
@click.option("--keywords", default="", help="逗号分隔关键词（如 'forbidden,services'）")
@click.option("--applies-to", "applies_to", default="",
              help="逗号分隔的路径模式（子串匹配，如 'src/api/,src/services/'）")
@click.option("--id", "lesson_id", default=None, help="指定 ID（默认自动生成 8 位）")
@click.option("--author", default=None, help="作者（默认取 git config user.name）")
@click.option("--expires-at", default=None, help="过期时间 ISO 8601（如 2026-12-31T00:00:00）")
@click.option("--overwrite", is_flag=True, help="若 ID 已存在，允许覆盖")
@click.option("--json", "as_json", is_flag=True, help="以 JSON 输出新增记录")
def lesson_add(title, content, category, severity, keywords, applies_to,
               lesson_id, author, expires_at, overwrite, as_json) -> None:
    """新增一条经验到本地 .harness/memory/lessons/<id>.md。"""
    market = _build_market(_resolve_project_dir())
    kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
    apply_list = [p.strip() for p in applies_to.split(",") if p.strip()]
    try:
        lesson = market.add_lesson(
            title=title,
            content=content,
            category=category,
            severity=severity,
            keywords=kw_list,
            applies_to=apply_list,
            lesson_id=lesson_id,
            author=author,
            expires_at=expires_at,
            overwrite=overwrite,
        )
    except FileExistsError as exc:
        click.echo(f"❌ {exc}", err=True)
        sys.exit(1)

    if as_json:
        from dataclasses import asdict
        click.echo(json.dumps(asdict(lesson), ensure_ascii=False, indent=2))
        return
    click.echo(f"✅ 已新增 lesson [{lesson.id}] → "
               f"{market.local_dir / (lesson.id + '.md')}")

    # A1 接通点:experience_market.auto_push → 推到 .harness-shared/lessons/
    project_dir = _resolve_project_dir()
    market_cfg = _load_experience_config(project_dir)
    if market_cfg.get("enabled", True) and market_cfg.get("auto_push", False):
        try:
            import shutil as _shutil
            market.shared_dir.mkdir(parents=True, exist_ok=True)
            src = market.local_dir / f"{lesson.id}.md"
            dst = market.shared_dir / f"{lesson.id}.md"
            _shutil.copy2(src, dst)
            click.echo(f"📤 auto_push → {dst}")
        except Exception as exc:
            click.echo(f"⚠️  auto_push 失败(本地已写入,跳过共享盘): {exc}", err=True)

    # 自动刷新 generated/*.md：generator 直接读 memory/lessons/，不依赖 scan 结果，
    # 失败不阻断（和 scan_cmd 内部逻辑保持一致）。
    generated_files: List[str] = []
    try:
        gen_result = _build_generator(project_dir).generate_all()
        generated_files = gen_result.written
    except Exception as exc:
        click.echo(f"⚠️  lesson 已写入，但自动 generate 失败: {exc}", err=True)
        click.echo("   请手动跑 `harness scan` 或 `harness generate` 刷新规则文件。", err=True)

    if generated_files:
        click.echo(f"📝 已自动刷新: {', '.join(generated_files)}")
    click.echo("💡 当前 Agent 会话不会感知到这条新经验（系统提示词为启动时快照），")
    click.echo("   需重启会话；或在会话内让 Agent 实时读取生成文档来召回，例如：")
    click.echo("   「读 .harness/generated/claude.md 的『团队经验教训』段，列出全部条目」")


@lesson_group.command("remove")
@click.argument("lesson_id", type=str)
def lesson_remove(lesson_id: str) -> None:
    """删除本地一条经验。"""
    market = _build_market(_resolve_project_dir())
    if market.remove_lesson(lesson_id):
        click.echo(f"🗑️  已删除 [{lesson_id}]")
        # 同步刷新 generated/*.md，否则被删的经验仍会留在规则文件里。
        try:
            gen_result = _build_generator(_resolve_project_dir()).generate_all()
            if gen_result.written:
                click.echo(f"📝 已自动刷新: {', '.join(gen_result.written)}")
        except Exception as exc:
            click.echo(f"⚠️  删除成功但自动 generate 失败: {exc}", err=True)
            click.echo("   请手动跑 `harness scan` 或 `harness generate` 刷新规则文件。", err=True)
    else:
        click.echo(f"❌ 未找到 lesson: {lesson_id}", err=True)
        sys.exit(1)


@lesson_group.command("match")
@click.option("--file", "file_path", default="",
              help="目标文件路径（相对项目根或绝对均可；用于 applies_to 匹配）")
@click.option("--content-file", "content_file", default=None,
              help="读这个文件作为待匹配内容（用于关键词命中）；与 --content 互斥")
@click.option("--content", "content", default=None,
              help="直接传入内容字符串；与 --content-file 互斥")
@click.option("--stdin", "from_stdin", is_flag=True,
              help="从 stdin 读 JSON（PostToolUse hook 用），结构: {file_path, content}")
@click.option("--limit", default=5, show_default=True, type=int,
              help="最多返回多少条")
@click.option("--json", "as_json", is_flag=True,
              help="以 JSON 输出 [{id,title,severity,applies_to,keywords,content},...]")
@click.option("--format", "fmt", default="markdown", show_default=True,
              type=click.Choice(["markdown", "plain"]),
              help="人读输出格式；--json 时忽略本选项")
def lesson_match(file_path, content_file, content, from_stdin, limit, as_json, fmt) -> None:
    """按 file_path + content 召回相关经验（PostToolUse hook 注入用）。

    匹配语义：
      - applies_to 子串命中 file_path → 路径相关（+2）
      - keywords 任一作为子串出现在 content 或 file_path → 关键词相关（每条 +1，大小写不敏感）
      - applies_to 与 keywords 都为空的 lesson → 全局，无条件命中（最低优先级）

    返回按 score 降序，同分按 created_at 倒序（新优先）。
    """
    # 互斥校验
    sources = [bool(content_file), bool(content), bool(from_stdin)]
    if sum(sources) > 1:
        click.echo("❌ --content / --content-file / --stdin 三选一", err=True)
        sys.exit(2)

    actual_content = ""
    actual_file = file_path

    if from_stdin:
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except json.JSONDecodeError as exc:
            click.echo(f"❌ 读取 stdin JSON 失败: {exc}", err=True)
            sys.exit(2)
        actual_file = payload.get("file_path") or actual_file
        actual_content = payload.get("content") or ""
    elif content is not None:
        actual_content = content
    elif content_file:
        try:
            actual_content = Path(content_file).read_text(encoding="utf-8")
        except OSError as exc:
            click.echo(f"❌ 读 {content_file} 失败: {exc}", err=True)
            sys.exit(2)

    market = _build_market(_resolve_project_dir())
    lessons = market.match_lessons(
        file_path=actual_file, content=actual_content, limit=limit,
    )

    if as_json:
        from dataclasses import asdict
        click.echo(json.dumps(
            [asdict(l) for l in lessons],
            ensure_ascii=False,
            indent=2,
        ))
        return

    if not lessons:
        if fmt == "plain":
            return  # hook stdout 静默
        click.echo("（无命中经验）")
        return

    if fmt == "plain":
        # 给 hook 注入用：title + 简短正文，无 emoji
        for l in lessons:
            click.echo(f"- [{l.severity}] {l.title}")
            body = (l.content or "").strip()
            if len(body) > 280:
                body = body[:280] + "…"
            for line in body.splitlines():
                click.echo(f"  {line}")
        return

    # markdown 人读
    click.echo(f"🎯 命中 {len(lessons)} 条经验:")
    for l in lessons:
        kw = ",".join(l.keywords) if l.keywords else "-"
        applies = ",".join(l.applies_to) if l.applies_to else "-"
        click.echo(f"   [{l.id}] [{l.severity}] {l.title}")
        click.echo(f"          applies_to={applies}  keywords={kw}")


@cli.command("sync")
@click.option("--remote", is_flag=True,
              help="（P2 占位）从远程 git 仓库 pull/push；P1 仅打印告警后跳过")
@click.option("--json", "as_json", is_flag=True, help="以 JSON 输出 sync 结果")
def sync_cmd(remote: bool, as_json: bool) -> None:
    """从 .harness-shared/lessons/ 同步到本地（latest-wins）。"""
    market = _build_market(_resolve_project_dir())
    result = market.sync(remote=remote)

    if as_json:
        click.echo(json.dumps({
            "pulled": result.pulled,
            "overwritten": result.overwritten,
            "kept_local": result.kept_local,
            "skipped": result.skipped,
            "remote_skipped": result.remote_skipped,
            "shared_dir": str(market.shared_dir),
        }, ensure_ascii=False, indent=2))
        return

    click.echo(f"🔁 sync 完成（共享盘: {market.shared_dir}）")
    click.echo(f"   新拉取:   {len(result.pulled)}  {result.pulled or ''}")
    click.echo(f"   覆盖:     {len(result.overwritten)}  {result.overwritten or ''}")
    click.echo(f"   保留本地: {len(result.kept_local)}  {result.kept_local or ''}")
    if result.skipped:
        click.echo(f"   ⚠️ 解析失败跳过: {result.skipped}")
    if result.remote_skipped:
        click.echo("   ⚠️ --remote 暂未实现（P2 任务）；本次仅同步本地共享盘")


# -- generate --------------------------------------------------------------


@cli.command("generate")
@click.option("--json", "as_json", is_flag=True, help="以 JSON 输出生成清单")
def generate_cmd(as_json: bool) -> None:
    """生成 .harness/generated/{claude,comate,ducc}.md（每次 scan 后会自动调用）。"""
    project_dir = _resolve_project_dir()
    try:
        result = _build_generator(project_dir).generate_all()
    except Exception as exc:
        click.echo(f"❌ generate 失败: {exc}", err=True)
        sys.exit(1)

    if as_json:
        click.echo(json.dumps({"generated": result.written}, ensure_ascii=False, indent=2))
        return

    if not result.written:
        click.echo("（未生成任何文件）")
        return
    click.echo(f"📝 已生成 {len(result.written)} 个 Agent 规则文件：")
    for f in result.written:
        click.echo(f"   - {f}")


# -- doctor / upgrade ------------------------------------------------------


_SEVERITY_BADGES = {
    "ok":      "✅",
    "info":    "ℹ️ ",
    "warning": "⚠️ ",
    "error":   "❌",
}


@cli.command("doctor")
@click.option("--json", "as_json", is_flag=True, help="以 JSON 输出体检报告")
def doctor_cmd(as_json: bool) -> None:
    """体检：Python / venv / 依赖 / 配置 / 依赖图 / git / 共享盘。

    退出码：有 error → 1；否则 0（warning 不阻断）。
    """
    project_dir = _resolve_project_dir()
    doctor = Doctor(project_dir=project_dir, harness_root=_HARNESS_DIR)
    report = doctor.run()

    if as_json:
        click.echo(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        sys.exit(1 if report.has_error else 0)

    click.echo("🩺 Harness 体检")
    for c in report.checks:
        badge = _SEVERITY_BADGES.get(c.severity, "•")
        click.echo(f"   {badge} [{c.name}] {c.message}")
        if c.suggestion and c.severity in ("warning", "error"):
            click.echo(f"        建议: {c.suggestion}")

    s = report.summary()
    click.echo("")
    if report.has_error:
        click.echo(f"❌ 体检未通过：error={s['error']} warning={s['warning']} "
                   f"info={s['info']} ok={s['ok']}")
        sys.exit(1)
    elif report.has_warning:
        click.echo(f"⚠️  体检通过但有提醒：warning={s['warning']} info={s['info']} ok={s['ok']}")
    else:
        click.echo(f"✅ 体检全部通过 ({s['ok']} ok / {s['info']} info)")


@cli.command("upgrade")
@click.option("--json", "as_json", is_flag=True, help="以 JSON 输出升级计划")
def upgrade_cmd(as_json: bool) -> None:
    """（P1 占位）打印当前版本与 P2 升级计划，不执行任何动作。"""
    report = plan_upgrade(_HARNESS_DIR)

    if as_json:
        click.echo(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return

    click.echo(f"📦 当前版本: {report.current_version}")
    click.echo("ℹ️  升级能力仍在 P2 路线图中，本次不执行任何动作。")
    click.echo("    计划：")
    for item in report.planned:
        click.echo(f"     - {item}")


# -- install / uninstall ---------------------------------------------------


_ACTION_BADGES = {
    "created":   "🆕",
    "updated":   "✏️ ",
    "unchanged": "⏸️ ",
    "removed":   "🗑️ ",
}


def _print_install_changes(result, project_dir: Path) -> None:
    for c in result.changes:
        rel = _format_relative(c.path, project_dir)
        badge = _ACTION_BADGES.get(c.action, "•")
        suffix = f" — {c.detail}" if c.detail else ""
        click.echo(f"   {badge} [{c.action}] {rel}{suffix}")


def _format_relative(path: Path, project_dir: Path) -> str:
    try:
        return str(Path(path).relative_to(project_dir))
    except ValueError:
        return str(path)


@cli.command("install")
@click.option("--agent", "agent", default="claude", show_default=True,
              type=click.Choice(_SUPPORTED_AGENTS),
              help="目标 Agent（claude/ducc/baidu-cc 共用同一套 hook 协议）")
@click.option("--dry-run", is_flag=True,
              help="仅打印将要做的改动，不写盘")
@click.option("--json", "as_json", is_flag=True, help="以 JSON 输出操作记录")
def install_cmd(agent: str, dry_run: bool, as_json: bool) -> None:
    """把 harness 接到 Agent：注册 PostToolUse hook + 注入 CLAUDE.md 引用。

    幂等：可反复执行；已有的 harness hook / CLAUDE.md 托管块会被识别后更新。
    不破坏：第三方已注册的其它 hook（如 baidu-cc 自带的 data-report）原样保留。
    """
    project_dir = _resolve_project_dir()
    installer = get_installer(agent, project_dir)
    result = installer.install(dry_run=dry_run)

    if as_json:
        click.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return

    title = f"🔌 Harness install ({agent})" + ("  [dry-run]" if dry_run else "")
    click.echo(title)
    _print_install_changes(result, project_dir)

    if dry_run:
        click.echo("\nℹ️  dry-run；未写盘。去掉 --dry-run 真正落盘。")
    else:
        click.echo("\n✅ 安装完成。")
        click.echo("   下一步：")
        click.echo("   1) 跑 `harness scan` 生成 .harness/generated/claude.md")
        click.echo("   2) 重启 / 重新打开 Agent 让其重新读 .claude/settings.json")
        click.echo("   3) 让 Agent 编辑 src/ 下的 .ts/.tsx 文件，违规会被自动拦截")


@cli.command("uninstall")
@click.option("--agent", "agent", default="claude", show_default=True,
              type=click.Choice(_SUPPORTED_AGENTS),
              help="目标 Agent")
@click.option("--dry-run", is_flag=True, help="仅打印将要做的改动，不写盘")
@click.option("--json", "as_json", is_flag=True, help="以 JSON 输出操作记录")
def uninstall_cmd(agent: str, dry_run: bool, as_json: bool) -> None:
    """卸载 harness 与 Agent 的对接（仅移除 harness 自己装的部分）。"""
    project_dir = _resolve_project_dir()
    installer = get_installer(agent, project_dir)
    result = installer.uninstall(dry_run=dry_run)

    if as_json:
        click.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return

    title = f"🔌 Harness uninstall ({agent})" + ("  [dry-run]" if dry_run else "")
    click.echo(title)
    _print_install_changes(result, project_dir)

    if dry_run:
        click.echo("\nℹ️  dry-run；未写盘。去掉 --dry-run 真正落盘。")
    else:
        click.echo("\n✅ 卸载完成（其它 Agent 自带的 hook 已原样保留）。")


# -- init ------------------------------------------------------------------


_DEFAULT_RULES_PATH = _HARNESS_DIR / "rules.yaml"


def _load_default_rules_for_init() -> dict:
    """读 .harness/rules.yaml 作为 init 的基线默认。

    与 _load_rules_safe 不同：这里读的是 harness 安装目录里出厂的那份，
    不是用户项目里被改过的那份。init 是要"为新项目"生成一份 rules。
    """
    if not _DEFAULT_RULES_PATH.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(_DEFAULT_RULES_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _render_plan_human(plan, project_dir: Path) -> str:
    """渲染 ProposedPlan 给人看（init --dry-run / 交互前预览）。"""
    lines: List[str] = []
    lines.append(f"📂 项目: {project_dir}")
    lines.append("")
    if plan.adopted_notes:
        lines.append("✅ 自动采纳：")
        for n in plan.adopted_notes:
            lines.append(f"   • {n}")
        lines.append("")
    if plan.info_notes:
        lines.append("ℹ️  探测提示：")
        for n in plan.info_notes:
            lines.append(f"   • {n}")
        lines.append("")
    if plan.conflicts:
        lines.append(f"❓ 需要决策（{len(plan.conflicts)} 项）：")
        for c in plan.conflicts:
            lines.append(f"   [{c.id}] {c.title}")
            for ch in c.choices:
                mark = " (默认)" if ch.key == c.default_choice else ""
                lines.append(f"      - {ch.key}: {ch.label}{mark}")
        lines.append("")
    else:
        lines.append("✨ 无需决策的冲突。")
        lines.append("")
    return "\n".join(lines)


def _prompt_for_choice(conflict) -> str:
    """交互式让用户从 conflict.choices 里挑一个，返回 choice.key。"""
    click.echo()
    click.echo(f"❓ {conflict.title}")
    if conflict.detail:
        for ln in conflict.detail.splitlines():
            click.echo(f"   {ln}")
    keys: List[str] = []
    for idx, ch in enumerate(conflict.choices, 1):
        mark = " ★" if ch.key == conflict.default_choice else ""
        click.echo(f"   [{idx}] {ch.label}{mark}")
        if ch.detail:
            click.echo(f"        {ch.detail}")
        keys.append(ch.key)
    default_idx = "1"
    if conflict.default_choice and conflict.default_choice in keys:
        default_idx = str(keys.index(conflict.default_choice) + 1)
    while True:
        raw = click.prompt(
            "   选择编号", default=default_idx, show_default=True, type=str,
        ).strip()
        try:
            n = int(raw)
            if 1 <= n <= len(keys):
                return keys[n - 1]
        except ValueError:
            pass
        click.echo(f"   ⚠️ 请输入 1-{len(keys)} 之间的数字")


def _write_init_outputs(
    project_dir: Path, final_rules: dict, plan,
) -> List[Path]:
    """落盘 rules.yaml + 初始化必要的目录。返回写了/动了的文件列表。"""
    import yaml
    written: List[Path] = []
    harness_dir = project_dir / ".harness"
    harness_dir.mkdir(parents=True, exist_ok=True)
    rules_path = harness_dir / "rules.yaml"
    rules_path.write_text(
        yaml.safe_dump(final_rules, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    written.append(rules_path)
    # 必要的空目录
    for sub in ("context", "generated", "memory/lessons"):
        d = harness_dir / sub
        d.mkdir(parents=True, exist_ok=True)
        keep = d / ".gitkeep"
        if not keep.exists():
            keep.write_text("", encoding="utf-8")
    # mode-config.json
    mode_file = harness_dir / "mode-config.json"
    if not mode_file.exists():
        mode_file.write_text(
            json.dumps({"mode": "strict"}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(mode_file)
    return written


@cli.command("init")
@click.option("--dry-run", is_flag=True,
              help="只跑探测 + 协商，打印 plan，不写盘")
@click.option("--yes", "-y", "auto_yes", is_flag=True,
              help="所有 conflict 走 default_choice，不交互")
@click.option("--json", "as_json", is_flag=True, help="以 JSON 输出 plan")
def init_cmd(dry_run: bool, auto_yes: bool, as_json: bool) -> None:
    """探测项目现状并生成 .harness/rules.yaml。

    三段式:
      1) probe   — 只读扫一遍项目根（tsconfig / package.json / src/）
      2) resolve — 对照出厂默认 rules，自动采纳能采纳的、抛 conflict
      3) apply   — 交互回答（或 --yes 走默认）后写盘
    """
    project_dir = _resolve_project_dir()
    report = probe_project(project_dir)
    default_rules = _load_default_rules_for_init()
    resolver = InitResolver(default_rules, report)
    plan = resolver.resolve()

    if as_json:
        click.echo(json.dumps({
            "project_dir": str(project_dir),
            "adopted_notes": plan.adopted_notes,
            "info_notes": plan.info_notes,
            "conflicts": [
                {
                    "id": c.id,
                    "category": c.category,
                    "title": c.title,
                    "detail": c.detail,
                    "default_choice": c.default_choice,
                    "choices": [
                        {"key": ch.key, "label": ch.label, "detail": ch.detail}
                        for ch in c.choices
                    ],
                }
                for c in plan.conflicts
            ],
            "dry_run": dry_run,
        }, ensure_ascii=False, indent=2))
        return

    click.echo(_render_plan_human(plan, project_dir))

    if dry_run:
        click.echo("ℹ️  dry-run；未写盘。去掉 --dry-run 真正落盘。")
        return

    # 交互或 --yes
    answers: dict = {}
    if plan.conflicts and not auto_yes:
        click.echo("请逐条选择：")
        for c in plan.conflicts:
            answers[c.id] = _prompt_for_choice(c)

    final_rules = resolver.apply_user_choices(plan, answers)
    rules_path = project_dir / ".harness" / "rules.yaml"
    if rules_path.exists():
        if not click.confirm(
            f"⚠️ {rules_path.relative_to(project_dir)} 已存在，覆盖？",
            default=False,
        ):
            click.echo("已取消，未写盘。")
            return

    written = _write_init_outputs(project_dir, final_rules, plan)
    click.echo("\n✅ init 完成。落盘:")
    for p in written:
        try:
            rel = p.relative_to(project_dir)
        except ValueError:
            rel = p
        click.echo(f"   • {rel}")
    click.echo("\n下一步：")
    click.echo("   1) `harness install --agent <claude|ducc|baidu-cc>` 接通 hook")
    click.echo("   2) `harness scan` 生成 .harness/generated/claude.md")


# -- setup (one-command bootstrap) ----------------------------------------


@cli.command("setup")
@click.option("--agent", "agent", required=True,
              type=click.Choice(_SUPPORTED_AGENTS),
              help="目标 Agent；必填，没有默认值（不同 Agent 安装位置不同）")
@click.option("--interactive", is_flag=True,
              help="init 阶段走交互模式（默认 --yes 不交互）")
@click.option("--json", "as_json", is_flag=True,
              help="以 JSON 输出每阶段结果（聚合）")
def setup_cmd(agent: str, interactive: bool, as_json: bool) -> None:
    """一条命令搞定接入：init + install + scan（fail-fast，任一阶段失败即退出）。

    等价于：
      harness init --yes        # --interactive 时去掉 --yes
      harness install --agent <agent>
      harness scan

    适合"刚 cp 完 .harness/，立即就要能用"的场景；不想交互就这条命令一把梭。
    init 已经探测过的项目重跑也安全（rules.yaml 已存在会确认覆盖）。
    """
    project_dir = _resolve_project_dir()
    stages: List[Dict[str, Any]] = []

    def _stage(title: str) -> None:
        if not as_json:
            click.echo(f"\n━━━ {title} ━━━")

    # ---- 1) init ------------------------------------------------------------
    _stage("[1/3] init — 探测 + 生成 rules.yaml")
    try:
        report = probe_project(project_dir)
        default_rules = _load_default_rules_for_init()
        resolver = InitResolver(default_rules, report)
        plan = resolver.resolve()

        if not as_json:
            click.echo(_render_plan_human(plan, project_dir))

        answers: dict = {}
        if plan.conflicts and interactive:
            click.echo("请逐条选择：")
            for c in plan.conflicts:
                answers[c.id] = _prompt_for_choice(c)
        # 非 interactive：answers 留空，apply 会回落 default_choice

        rules_path = project_dir / ".harness" / "rules.yaml"
        if rules_path.exists() and not as_json and not interactive:
            # 一键模式默认覆盖（用户跑 setup 就是接受所有默认）；
            # 但保留交互模式的二次确认体验
            pass
        elif rules_path.exists() and interactive:
            if not click.confirm(
                f"⚠️ {rules_path.relative_to(project_dir)} 已存在，覆盖？",
                default=False,
            ):
                if as_json:
                    click.echo(json.dumps(
                        {"stage": "init", "skipped": "user-cancelled"},
                        ensure_ascii=False, indent=2,
                    ))
                else:
                    click.echo("已取消 setup（init 阶段）。")
                sys.exit(1)

        final_rules = resolver.apply_user_choices(plan, answers)
        written = _write_init_outputs(project_dir, final_rules, plan)
        stages.append({
            "stage": "init",
            "ok": True,
            "written": [str(p.relative_to(project_dir))
                        if _is_under(p, project_dir) else str(p)
                        for p in written],
            "conflicts": [c.id for c in plan.conflicts],
            "interactive": interactive,
        })
        if not as_json:
            click.echo("✅ init 完成")
    except Exception as exc:  # noqa: BLE001
        _emit_setup_fail(stages, "init", exc, as_json)
        sys.exit(1)

    # ---- 2) install ---------------------------------------------------------
    _stage(f"[2/3] install — 注册 PostToolUse hook ({agent})")
    try:
        installer = get_installer(agent, project_dir)
        result = installer.install(dry_run=False)
        stages.append({
            "stage": "install",
            "ok": True,
            "agent": agent,
            "changes": result.to_dict(),
        })
        if not as_json:
            _print_install_changes(result, project_dir)
            click.echo("✅ install 完成")
    except Exception as exc:  # noqa: BLE001
        _emit_setup_fail(stages, "install", exc, as_json)
        sys.exit(1)

    # ---- 3) scan ------------------------------------------------------------
    _stage("[3/3] scan — 生成 generated/*.md")
    try:
        scanner = IncrementalScanner(project_dir=project_dir)
        scan_result = scanner.scan(force_full=False)
        summary = scan_result.summary()
        try:
            gen_result = _build_generator(project_dir).generate_all()
            generated_files = gen_result.written
        except Exception as gen_exc:
            generated_files = []
            if not as_json:
                click.echo(f"⚠️  scan 完成但 generate 失败: {gen_exc}",
                           err=True)
        stages.append({
            "stage": "scan",
            "ok": True,
            "summary": dict(summary),
            "generated": generated_files,
        })
        if not as_json:
            click.echo(f"   文件数: {summary['total_files']}")
            click.echo(f"   组件:   {summary['total_components']}")
            click.echo(f"   Hooks:  {summary['total_hooks']}")
            click.echo(f"   APIs:   {summary['total_apis']}")
            if generated_files:
                click.echo(f"📝 已生成: {', '.join(generated_files)}")
            click.echo("✅ scan 完成")
    except Exception as exc:  # noqa: BLE001
        _emit_setup_fail(stages, "scan", exc, as_json)
        sys.exit(1)

    # ---- 总结 ---------------------------------------------------------------
    if as_json:
        click.echo(json.dumps(
            {"ok": True, "agent": agent, "stages": stages},
            ensure_ascii=False, indent=2,
        ))
        return

    click.echo("\n🎉 Harness 接入完成！")
    click.echo("   下一步：")
    click.echo(f"   • 重启 / 重开 {agent} 让其重新读 hook 配置")
    click.echo("   • 让 Agent 编辑 src/ 下文件，违规会被自动拦截")
    click.echo("   • 改规则：编辑 .harness/rules.yaml 后跑 `harness generate`")


def _is_under(p: Path, root: Path) -> bool:
    try:
        p.relative_to(root)
        return True
    except ValueError:
        return False


def _emit_setup_fail(
    stages: List[Dict[str, Any]], stage: str, exc: BaseException,
    as_json: bool,
) -> None:
    """setup fail-fast：在 stages 尾部加失败记录，按格式输出。"""
    stages.append({"stage": stage, "ok": False, "error": str(exc)})
    if as_json:
        click.echo(json.dumps(
            {"ok": False, "failed_stage": stage, "stages": stages},
            ensure_ascii=False, indent=2,
        ))
    else:
        click.echo(f"\n❌ setup 在 [{stage}] 阶段失败：{exc}", err=True)


if __name__ == "__main__":
    cli()