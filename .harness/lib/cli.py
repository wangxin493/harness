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
from typing import List

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
from lib.installer import get_installer  # noqa: E402
from lib.mode_manager import GovernanceMode, ModeManager, ValidationContext  # noqa: E402
from lib.scanner import IncrementalScanner  # noqa: E402
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
def scan_cmd(full: bool, no_generate: bool, as_json: bool) -> None:
    """扫描项目，产出 dep graph + project context；默认随后自动 generate。"""
    project_dir = _resolve_project_dir()
    scanner = IncrementalScanner(project_dir=project_dir)
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
    click.echo("💡 下一步：跑 `harness scan` 刷新 generated/*.md；")
    click.echo("   当前 Agent 会话不会感知到这条新经验（系统提示词为启动时快照），")
    click.echo("   需重启会话；或在会话内让 Agent 实时读取生成文档来召回，例如：")
    click.echo("   「读 .harness/generated/claude.md 的『团队经验教训』段，列出全部条目」")


@lesson_group.command("remove")
@click.argument("lesson_id", type=str)
def lesson_remove(lesson_id: str) -> None:
    """删除本地一条经验。"""
    market = _build_market(_resolve_project_dir())
    if market.remove_lesson(lesson_id):
        click.echo(f"🗑️  已删除 [{lesson_id}]")
    else:
        click.echo(f"❌ 未找到 lesson: {lesson_id}", err=True)
        sys.exit(1)


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


if __name__ == "__main__":
    cli()
