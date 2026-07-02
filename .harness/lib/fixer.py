#!/usr/bin/env python3
"""Harness 2.0 修复器（P1 #7）—— lib/fixer.py

P1 范围（与执行清单一致）：
- 仅对 `rule_id == "import-forbidden"` 出 unified diff patch
  - 内置可机械替换的映射（如 `@/services/X` → `@/api/X`）
  - 无映射的（如 `@/api/mockApi/...`）只出 instruction，不出 patch
- 架构错误（`arch-*-import`）只出 instruction，不出 patch（重构语义机器无法保真）
- 默认 dry-run：把 patch 与 instructions 返回，不动文件
- `--apply`：
  - 非 git 仓库 → 拒绝（exit 3）
  - git 仓库 → 用 `git stash create` + `git stash store` 默默留备份（不动工作树），
    再用 `git apply` 应用 patch；apply 是原子的，失败时工作树不变
- fixer 不动 `.harness/context/`（依赖图唯一生产者是 scanner）

P2 才会做：
- 架构错误的半自动 codemod（基于 jscodeshift / ts-morph）
- 经验市场 lesson 命中后的自动应用
"""

from __future__ import annotations

import datetime as _dt
import difflib
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from lib.validator import CodeValidator, Issue


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class FilePatch:
    """单文件的 unified diff。"""

    file: str            # POSIX 相对路径
    diff: str            # 完整 unified diff（含 --- a/ +++ b/ 头）
    rule_ids: List[str] = field(default_factory=list)  # 这个 patch 修复了哪些 rule


@dataclass
class FixInstruction:
    """无法机械修复的人读指令。"""

    rule_id: str
    file: str
    line: Optional[int]
    message: str
    suggestion: str


@dataclass
class FixResult:
    """fix 结果。"""

    patches: List[FilePatch] = field(default_factory=list)
    instructions: List[FixInstruction] = field(default_factory=list)
    applied: bool = False
    stash_ref: Optional[str] = None  # 备份 stash 的 commit sha（--apply 时）


# ---------------------------------------------------------------------------
# 修复器
# ---------------------------------------------------------------------------


class Fixer:
    """P1 最小修复器：只处理 import-forbidden 中可机械替换的子集。

    可机械替换的 forbidden 映射从 `rules.yaml imports.rewrites` 读取：
        imports:
          rewrites:
            "@/legacy": "@/api"

    key 末尾不带 "/"；命中条件：source == key 或 source.startswith(key + "/")
    rules 缺省 / 不存在时映射为空，fixer 只会出 instruction，不会出 patch。
    """

    def __init__(
        self,
        project_dir: Path,
        validator: Optional[CodeValidator] = None,
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.validator = validator or CodeValidator(project_dir=self.project_dir)
        self._rewrites: Dict[str, str] = self._load_rewrites()

    # -- 公共 API -----------------------------------------------------------

    def fix_file(self, rel_path: str, apply: bool = False) -> FixResult:
        """对单文件生成（并可选应用）修复。

        默认 dry-run：返回 patches + instructions，不动磁盘。
        apply=True：要求当前在 git 仓库，应用前用 stash create/store 留备份。
        """
        result = FixResult()
        issues = self.validator.validate_file(rel_path)
        if not issues:
            return result

        patch, applied_lines = self._build_patch_for_file(rel_path, issues)
        if patch is not None:
            result.patches.append(patch)

        # 所有未被 patch 实际改写的 issue → 出 instruction。
        # 这里的"覆盖判定"用 _rewrite_forbidden_imports 真实改写过的行号集合，
        # 而不是 diff hunk 区间，避免 unified_diff(n=3) 上下文行被误算成 "已修复"。
        for issue in issues:
            if self._is_covered_by_patch(issue, applied_lines):
                continue
            if issue.category == "parse":
                # 解析错误不属于 fixer 职责（让用户自己看 validator 输出）
                continue
            result.instructions.append(FixInstruction(
                rule_id=issue.rule_id,
                file=issue.file,
                line=issue.line,
                message=issue.message,
                suggestion=issue.suggestion or "",
            ))

        if apply and result.patches:
            self._apply_patches(result)

        return result

    # -- patch 生成 ----------------------------------------------------------

    def _build_patch_for_file(
        self, rel_path: str, issues: List[Issue]
    ) -> Tuple[Optional[FilePatch], Set[int]]:
        """对单文件聚合所有 import-forbidden 替换，生成一个 unified diff。

        返回 (FilePatch | None, 实际被改写的 1-indexed 行号集合)。
        行号集合用于 fix_file 决定哪些 issue 已被 patch 真实修复。
        """
        forbidden = [i for i in issues if i.rule_id == "import-forbidden"]
        if not forbidden:
            return None, set()

        abs_path = self.project_dir / rel_path
        try:
            original = abs_path.read_text(encoding="utf-8")
        except OSError:
            return None, set()

        new_text, applied_rules, applied_lines = self._rewrite_forbidden_imports(
            original, forbidden
        )
        if new_text == original or not applied_lines:
            return None, set()  # 无可机械替换的 forbidden（如 mockApi）

        diff = "".join(difflib.unified_diff(
            original.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{rel_path}",
            tofile=f"b/{rel_path}",
            n=3,
        ))
        return (
            FilePatch(file=rel_path, diff=diff, rule_ids=applied_rules),
            set(applied_lines),
        )

    def _rewrite_forbidden_imports(
        self, source: str, forbidden_issues: List[Issue]
    ) -> Tuple[str, List[str], List[int]]:
        """逐行替换可机械修复的 forbidden import。

        策略：只替换被 validator 标记为 import-forbidden 的行号上、
        且原始 import source 命中 _FORBIDDEN_REWRITES 前缀的，避免误伤。
        """
        lines = source.splitlines(keepends=True)
        applied_rules: List[str] = []
        applied_lines: List[int] = []
        targeted = {i.line for i in forbidden_issues if i.line is not None}

        for idx, line in enumerate(lines):
            line_no = idx + 1
            if line_no not in targeted:
                continue
            new_line = self._rewrite_import_line(line)
            if new_line is not None and new_line != line:
                lines[idx] = new_line
                applied_rules.append("import-forbidden")
                applied_lines.append(line_no)

        return "".join(lines), applied_rules, applied_lines

    def _rewrite_import_line(self, line: str) -> Optional[str]:
        """对单行做前缀替换。命中第一个适用的 rewrite 规则就返回。"""
        if not self._rewrites:
            return None
        # 匹配 import/export 行里的字符串字面量（单引号或双引号）
        # 这里不试图解析 AST：fix 只动我们自己生成的字面量片段，不动语法结构。
        pattern = re.compile(r"""(['"])([^'"]+)\1""")
        rewrites = self._rewrites

        def _replace(match: "re.Match[str]") -> str:
            quote = match.group(1)
            src = match.group(2)
            for prefix, replacement in rewrites.items():
                if src == prefix:
                    return f"{quote}{replacement}{quote}"
                if src.startswith(prefix + "/"):
                    rest = src[len(prefix):]
                    return f"{quote}{replacement}{rest}{quote}"
            return match.group(0)

        new_line = pattern.sub(_replace, line)
        return new_line if new_line != line else None

    # -- rules.yaml 装载 -----------------------------------------------------

    def _load_rewrites(self) -> Dict[str, str]:
        """从 .harness/rules.yaml 读 imports.rewrites；缺失/解析失败 → 空映射。"""
        rules_file = self.project_dir / ".harness" / "rules.yaml"
        if not rules_file.exists():
            return {}
        try:
            import yaml  # 延迟导入；测试 fixture 也走这条
            data = yaml.safe_load(rules_file.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}
        imports = data.get("imports") or {}
        rewrites = imports.get("rewrites") or {}
        if not isinstance(rewrites, dict):
            return {}
        # 只保留 str → str 的项，过滤异常配置
        clean: Dict[str, str] = {}
        for k, v in rewrites.items():
            if isinstance(k, str) and isinstance(v, str) and k:
                clean[k.rstrip("/")] = v
        return clean

    # -- patch 覆盖判定 ------------------------------------------------------

    @staticmethod
    def _is_covered_by_patch(issue: Issue, applied_lines: Set[int]) -> bool:
        """issue 是否被 patch 真实改写过。

        只有 import-forbidden 规则才会被 fixer 出 patch，因此其它规则一律返回
        False（让它们走 instruction 路径）。
        """
        if issue.rule_id != "import-forbidden":
            return False
        if issue.line is None:
            return False
        return issue.line in applied_lines

    # -- apply ---------------------------------------------------------------

    def _apply_patches(self, result: FixResult) -> None:
        """非 git 仓库 → 抛 RuntimeError；git 仓库 → stash 备份 + git apply。"""
        if not self._is_git_repo():
            raise RuntimeError(
                "harness fix --apply 拒绝执行：当前目录不是 git 仓库。"
                "请先 git init 或在 git 仓库内运行。"
            )

        result.stash_ref = self._stash_backup()

        for patch in result.patches:
            self._git_apply(patch.diff)

        result.applied = True

    def _is_git_repo(self) -> bool:
        try:
            subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=self.project_dir,
                check=True,
                capture_output=True,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _stash_backup(self) -> Optional[str]:
        """用 git stash create + store 默默留备份（不动工作树）。

        工作树 clean → stash create 输出空 → 不 store，返回 None。
        """
        ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        created = subprocess.run(
            ["git", "stash", "create"],
            cwd=self.project_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        sha = created.stdout.strip()
        if not sha:
            return None  # 工作树干净，无需备份
        subprocess.run(
            ["git", "stash", "store", "-m", f"harness-fix-backup-{ts}", sha],
            cwd=self.project_dir,
            check=True,
            capture_output=True,
        )
        return sha

    def _git_apply(self, diff: str) -> None:
        """git apply 一个 unified diff（原子：失败时工作树不变）。"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".patch", delete=False, encoding="utf-8"
        ) as fp:
            fp.write(diff)
            patch_path = fp.name
        try:
            subprocess.run(
                ["git", "apply", "--whitespace=nowarn", patch_path],
                cwd=self.project_dir,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"git apply 失败: {exc.stderr.strip() or exc.stdout.strip() or exc}"
            ) from exc
        finally:
            Path(patch_path).unlink(missing_ok=True)

