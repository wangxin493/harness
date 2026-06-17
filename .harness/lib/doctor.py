#!/usr/bin/env python3
"""Harness 2.0 体检 / 升级（P1 #10）—— lib/doctor.py

doctor：环境/配置体检
- Python ≥ 3.9
- .harness/.venv 存在
- tree-sitter / tree-sitter-typescript / click / PyYAML 已装，版本与 requirements.txt 一致
- rules.yaml 存在且能解析
- context/dependency-graph.json 存在（缺则提示先 scan）
- mode-config.json 可读（缺也 OK，按默认 strict）
- git 仓库与共享盘 → info 级提示

upgrade：P1 仅占位
- 打印当前版本与下次实现计划，exit 0
"""

from __future__ import annotations

import importlib
import importlib.metadata as _md
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


# severity ranking：error > warning > info > ok
SEVERITY_ORDER = {"ok": 0, "info": 1, "warning": 2, "error": 3}


@dataclass
class CheckResult:
    """单项体检结果。"""

    name: str
    severity: str   # ok | info | warning | error
    message: str
    suggestion: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DoctorReport:
    """整体体检报告。"""

    checks: List[CheckResult] = field(default_factory=list)

    @property
    def has_error(self) -> bool:
        return any(c.severity == "error" for c in self.checks)

    @property
    def has_warning(self) -> bool:
        return any(c.severity == "warning" for c in self.checks)

    def summary(self) -> Dict[str, int]:
        counts = {"ok": 0, "info": 0, "warning": 0, "error": 0}
        for c in self.checks:
            counts[c.severity] = counts.get(c.severity, 0) + 1
        return counts

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checks": [c.to_dict() for c in self.checks],
            "summary": self.summary(),
            "has_error": self.has_error,
            "has_warning": self.has_warning,
        }


# ---------------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------------


# requirements.txt 中需要校验版本的依赖。key = pip 包名（与 importlib.metadata 一致）
REQUIRED_PACKAGES: Tuple[Tuple[str, str, str], ...] = (
    # (pip_name, requirement_spec, import_check)
    ("tree-sitter",            "==0.21.3", "tree_sitter"),
    ("tree-sitter-typescript", "==0.21.2", "tree_sitter_typescript"),
    ("click",                  ">=8.1,<9", "click"),
    ("PyYAML",                 ">=6.0,<7", "yaml"),
)

MIN_PYTHON = (3, 9)


class Doctor:
    """Harness 体检。"""

    def __init__(self, project_dir: Path, harness_root: Optional[Path] = None) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.harness_dir = self.project_dir / ".harness"
        # harness_root：harness 自身代码所在目录（用于查 VERSION/requirements.txt）
        # 默认就是 project 内的 .harness/，跨项目分发时由调用方覆盖
        self.harness_root = Path(harness_root or self.harness_dir).resolve()

    # -- 公共 API -----------------------------------------------------------

    def run(self) -> DoctorReport:
        report = DoctorReport()
        report.checks.append(self._check_python())
        report.checks.append(self._check_venv())
        report.checks.extend(self._check_packages())
        report.checks.append(self._check_rules_yaml())
        report.checks.append(self._check_dependency_graph())
        report.checks.append(self._check_mode_config())
        report.checks.append(self._check_git_repo())
        report.checks.append(self._check_shared_dir())
        return report

    # -- 单项检查 -----------------------------------------------------------

    def _check_python(self) -> CheckResult:
        v = sys.version_info
        actual = f"{v.major}.{v.minor}.{v.micro}"
        if (v.major, v.minor) < MIN_PYTHON:
            return CheckResult(
                name="python",
                severity="error",
                message=f"Python {actual} 低于最低要求 {'.'.join(map(str, MIN_PYTHON))}",
                suggestion="升级到 Python 3.9 或更高版本",
            )
        return CheckResult(
            name="python",
            severity="ok",
            message=f"Python {actual}",
        )

    def _check_venv(self) -> CheckResult:
        venv = self.harness_root / ".venv"
        py = venv / "bin" / "python3"
        if not venv.exists():
            return CheckResult(
                name="venv",
                severity="warning",
                message=f"虚拟环境不存在: {venv}",
                suggestion=f"运行: python3 -m venv {venv} && {py} -m pip install -r "
                           f"{self.harness_root / 'requirements.txt'}",
            )
        if not py.exists():
            return CheckResult(
                name="venv",
                severity="warning",
                message=f"venv 存在但缺 python3 解释器: {py}",
                suggestion="重建 venv: rm -rf .harness/.venv && python3 -m venv .harness/.venv",
            )
        return CheckResult(
            name="venv",
            severity="ok",
            message=f"venv 就位 ({venv})",
        )

    def _check_packages(self) -> List[CheckResult]:
        results: List[CheckResult] = []
        for pip_name, spec, import_name in REQUIRED_PACKAGES:
            results.append(self._check_one_package(pip_name, spec, import_name))
        return results

    def _check_one_package(
        self, pip_name: str, spec: str, import_name: str
    ) -> CheckResult:
        # 1) 能否 import
        try:
            importlib.import_module(import_name)
        except ImportError as exc:
            return CheckResult(
                name=f"pkg:{pip_name}",
                severity="error",
                message=f"无法导入 {import_name}: {exc}",
                suggestion=f"在 venv 内运行: pip install '{pip_name}{spec}'",
            )

        # 2) 版本是否匹配 requirement
        try:
            actual = _md.version(pip_name)
        except _md.PackageNotFoundError:
            return CheckResult(
                name=f"pkg:{pip_name}",
                severity="warning",
                message=f"{pip_name} 已 import 但元数据缺失",
                suggestion=f"重新安装: pip install --force-reinstall '{pip_name}{spec}'",
            )

        if not _version_satisfies(actual, spec):
            return CheckResult(
                name=f"pkg:{pip_name}",
                severity="warning",
                message=f"{pip_name}=={actual} 不满足 {spec}",
                suggestion=f"对齐版本: pip install '{pip_name}{spec}'",
            )
        return CheckResult(
            name=f"pkg:{pip_name}",
            severity="ok",
            message=f"{pip_name}=={actual} ✓ {spec}",
        )

    def _check_rules_yaml(self) -> CheckResult:
        path = self.harness_dir / "rules.yaml"
        if not path.exists():
            return CheckResult(
                name="rules.yaml",
                severity="error",
                message=f"未找到 {path}",
                suggestion="从仓库初始化默认规则文件，或 cp .harness/rules.yaml.example",
            )
        try:
            import yaml
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            return CheckResult(
                name="rules.yaml",
                severity="error",
                message=f"解析失败: {exc}",
                suggestion="检查 YAML 语法",
            )
        if not data.get("architecture"):
            return CheckResult(
                name="rules.yaml",
                severity="warning",
                message="rules.yaml 解析成功但未配置 architecture.layers",
                suggestion="补全 architecture.layers，否则架构层验证全部跳过",
            )
        layers = (data.get("architecture") or {}).get("layers") or []
        return CheckResult(
            name="rules.yaml",
            severity="ok",
            message=f"已加载 {len(layers)} 个架构层",
        )

    def _check_dependency_graph(self) -> CheckResult:
        path = self.harness_dir / "context" / "dependency-graph.json"
        if not path.exists():
            return CheckResult(
                name="dependency-graph",
                severity="warning",
                message="尚未扫描，依赖图缺失",
                suggestion="运行: harness scan",
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            return CheckResult(
                name="dependency-graph",
                severity="error",
                message=f"依赖图损坏: {exc}",
                suggestion="重建: harness scan --full",
            )
        nodes = len((data.get("graph") or {}))
        return CheckResult(
            name="dependency-graph",
            severity="ok",
            message=f"已加载，{nodes} 个节点 (updated_at={data.get('updated_at') or 'unknown'})",
        )

    def _check_mode_config(self) -> CheckResult:
        path = self.harness_dir / "mode-config.json"
        if not path.exists():
            return CheckResult(
                name="mode-config",
                severity="info",
                message="未持久化模式配置，使用默认 strict",
                suggestion="如需切换: harness mode <strict|relaxed|off>",
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return CheckResult(
                name="mode-config",
                severity="ok",
                message=f"当前模式: {data.get('mode', 'strict')} "
                        f"(updated_at={data.get('updated_at') or 'unknown'})",
            )
        except (json.JSONDecodeError, OSError) as exc:
            return CheckResult(
                name="mode-config",
                severity="warning",
                message=f"mode-config.json 损坏: {exc}",
                suggestion="删除后用 harness mode 重新设置",
            )

    def _check_git_repo(self) -> CheckResult:
        try:
            in_repo = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=self.project_dir,
                capture_output=True, text=True, check=False,
            )
        except FileNotFoundError:
            return CheckResult(
                name="git",
                severity="info",
                message="未安装 git；harness fix --apply 将不可用",
                suggestion="安装 git，或 fix 仅使用 dry-run",
            )
        if in_repo.returncode != 0:
            return CheckResult(
                name="git",
                severity="info",
                message="当前目录不是 git 仓库；harness fix --apply 将被拒绝",
                suggestion="如需 --apply 自动备份能力，请先 git init",
            )

        # 拿分支名 —— 用 symbolic-ref 即使在空仓库（无 commit）也能工作
        branch_proc = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            cwd=self.project_dir,
            capture_output=True, text=True, check=False,
        )
        if branch_proc.returncode == 0:
            branch = (branch_proc.stdout or "").strip() or "(unknown)"
        else:
            # detached HEAD：取短 sha
            sha_proc = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=self.project_dir,
                capture_output=True, text=True, check=False,
            )
            branch = f"detached@{sha_proc.stdout.strip()}" if sha_proc.returncode == 0 else "(detached)"

        return CheckResult(
            name="git",
            severity="ok",
            message=f"git 仓库可用，当前分支: {branch}",
        )

    def _check_shared_dir(self) -> CheckResult:
        path = self.project_dir / ".harness-shared" / "lessons"
        if not path.exists():
            return CheckResult(
                name="shared-lessons",
                severity="info",
                message="未配置团队共享盘 (.harness-shared/lessons/)",
                suggestion="如需团队共享经验，创建该目录或挂载 git submodule",
            )
        try:
            count = sum(1 for _ in path.glob("*.md"))
        except OSError as exc:
            return CheckResult(
                name="shared-lessons",
                severity="warning",
                message=f"共享盘不可读: {exc}",
            )
        return CheckResult(
            name="shared-lessons",
            severity="ok",
            message=f"共享盘就位，{count} 条 lesson",
        )


# ---------------------------------------------------------------------------
# Upgrade（P1 占位）
# ---------------------------------------------------------------------------


@dataclass
class UpgradeReport:
    current_version: str
    planned: List[str]    # 未来 P2 会做的事

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def plan_upgrade(harness_root: Path) -> UpgradeReport:
    """P1 仅返回当前版本与计划项；不执行任何动作。"""
    version_file = Path(harness_root) / "VERSION"
    current = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else "0.0.0"
    return UpgradeReport(
        current_version=current,
        planned=[
            "P2: git pull 自动同步框架代码",
            "P2: pip install -U 自动升级依赖",
            "P2: 数据迁移（schema_version 跨版本兼容）",
            "P2: 远程经验市场（git remote pull/push）",
        ],
    )


# ---------------------------------------------------------------------------
# 工具：版本约束匹配
# ---------------------------------------------------------------------------


_SPEC_PART = re.compile(r"\s*(==|>=|<=|<|>|!=)\s*([0-9][0-9A-Za-z\.\-]*)")


def _version_satisfies(version: str, spec: str) -> bool:
    """轻量级 PEP 440 风格约束匹配。

    支持 spec 形如 "==0.21.3" / ">=8.1,<9" / ">=6.0,<7.0"。
    多个约束用逗号分隔，全部满足才返回 True。
    """
    parts = _SPEC_PART.findall(spec)
    if not parts:
        return True
    actual = _parse_version(version)
    for op, ver in parts:
        target = _parse_version(ver)
        if op == "==" and actual != target:
            return False
        if op == "!=" and actual == target:
            return False
        if op == ">=" and actual < target:
            return False
        if op == "<=" and actual > target:
            return False
        if op == ">"  and actual <= target:
            return False
        if op == "<"  and actual >= target:
            return False
    return True


def _parse_version(v: str) -> Tuple[int, ...]:
    """把 '0.21.3' / '8.1.8' / '6.0' 转成可比较的元组；
    非数字段（如 'rc1'）按 0 处理，够 P1 用。"""
    pieces: List[int] = []
    for token in v.split("."):
        m = re.match(r"^(\d+)", token)
        pieces.append(int(m.group(1)) if m else 0)
    return tuple(pieces)
