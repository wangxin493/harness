#!/usr/bin/env python3
"""Harness 2.0 Agent 集成安装器 —— lib/installer.py

把 harness 与 Agent 的 "最后一米" 接通：

- ClaudeInstaller  覆盖 Claude Code / Ducc / baidu-cc（三者共用同一套 hook 协议）
  · 写/合并 <project>/.claude/settings.json，注册 PostToolUse → validate-code.sh
  · 写/合并 <project>/CLAUDE.md，注入 @.harness/generated/claude.md 引用块

设计原则：
- 幂等：重复 install 不会产生重复 hook 条目；不会重复追加 CLAUDE.md 块
- 不破坏：合并 settings.json 时其它人的 hook（如 baidu-cc 自带 data-report）原样保留
- 可识别：harness 装的 hook 用 command 字符串里的相对路径识别（不依赖 schema 私货字段）
- 可卸载：uninstall 反向只摘 harness 自己的部分

约定 hook command 形态（不可改，识别就靠它）：
    bash "$CLAUDE_PROJECT_DIR/.harness/hooks/validate-code.sh"
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------


# Claude Code hook 命令。
# 注意：命令必须在 shell 层解析项目根，不能直接依赖 "$CLAUDE_PROJECT_DIR/..."，
# 否则 Ducc / baidu-cc 等兼容环境未注入 CLAUDE_PROJECT_DIR 时脚本启动前就失败。
_PROJECT_DIR_EXPR = 'PROJECT_DIR="${CLAUDE_PROJECT_DIR:-${HARNESS_PROJECT_DIR:-$PWD}}"'
HOOK_COMMAND = _PROJECT_DIR_EXPR + '; bash "$PROJECT_DIR/.harness/hooks/validate-code.sh"'

# 我们用于识别 hook 是不是 harness 装的子串（兼容历史命令和用户改过环境变量名的情况）
HOOK_COMMAND_FINGERPRINT = ".harness/hooks/validate-code.sh"

# Lesson 动态注入 hook —— 与 validate 是同位面的两个 PostToolUse hook
# validate 拦截违规（exit 2）；inject-lessons 永远 exit 0 但通过 additionalContext
# 给 Agent 推送相关团队经验。两者并存，互不影响。
INJECT_LESSONS_COMMAND = _PROJECT_DIR_EXPR + '; bash "$PROJECT_DIR/.harness/hooks/inject-lessons.sh"'
INJECT_LESSONS_FINGERPRINT = ".harness/hooks/inject-lessons.sh"

# SessionStart 刷新 generated/*.md，确保 lessons/sync 后 Agent 上下文及时更新。
REFRESH_GENERATED_COMMAND = _PROJECT_DIR_EXPR + '; bash "$PROJECT_DIR/.harness/hooks/refresh-generated.sh"'
REFRESH_GENERATED_FINGERPRINT = ".harness/hooks/refresh-generated.sh"

# 所有 harness hook 指纹（_count / _remove 一并处理）
ALL_HOOK_FINGERPRINTS = (
    HOOK_COMMAND_FINGERPRINT,
    INJECT_LESSONS_FINGERPRINT,
    REFRESH_GENERATED_FINGERPRINT,
)

# CLAUDE.md 托管块标记
CLAUDE_MD_BEGIN = "<!-- harness:begin (managed; do not edit between markers) -->"
CLAUDE_MD_END = "<!-- harness:end -->"

# CLAUDE.md 托管块内容模板（首尾两个标记之间）
CLAUDE_MD_BLOCK_BODY = """
> 本节由 `harness install` 维护。规则随 `harness scan` 自动刷新。

## Harness 接入说明

本项目已接入 Harness 代码治理。Agent 写代码时应优先阅读并遵守：

@.harness/generated/claude.md

## 生成物职责

- `.harness/rules.yaml`：项目规则源，定义分层、命名、导入边界和扫描范围
- `.harness/generated/claude.md`：给 Claude Code 的当前项目规范与索引摘要
- `.harness/context/`：扫描生成的项目索引、依赖图和增量缓存
- `.claude/settings.json`：注册 PostToolUse hook，在写文件后触发校验和经验注入

## 日常工作流

- 修改 `src/**/*.{ts,tsx,d.ts}` 后，PostToolUse hook 会自动跑 `harness validate`，违规会被拦截并把错误塞回我让我自己改
- 老项目默认只做增量治理：不主动修存量；改到老文件时按校验结果顺手修
- 治理模式（拦截力度）切换：`harness mode <strict|relaxed|off>`
- 重新生成 Agent 上下文：`harness scan`
- 体检接入状态：`harness doctor`
- 自动修复（仅 `import-forbidden` 子集）：`harness fix <file> --apply`
- 同步团队经验：`harness sync`
"""


# ---------------------------------------------------------------------------
# 结果数据结构
# ---------------------------------------------------------------------------


@dataclass
class FileChange:
    """单个文件的安装/卸载操作记录。"""

    path: Path
    action: str        # created | updated | unchanged | removed
    detail: str = ""   # 简短人读说明


@dataclass
class InstallResult:
    """install / uninstall 的整体结果。"""

    agent: str                       # claude
    dry_run: bool
    changes: List[FileChange] = field(default_factory=list)

    def add(self, path: Path, action: str, detail: str = "") -> None:
        self.changes.append(FileChange(path=path, action=action, detail=detail))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent": self.agent,
            "dry_run": self.dry_run,
            "changes": [
                {
                    "path": str(c.path),
                    "action": c.action,
                    "detail": c.detail,
                }
                for c in self.changes
            ],
        }


# ---------------------------------------------------------------------------
# Claude Installer
# ---------------------------------------------------------------------------


class ClaudeInstaller:
    """Claude Code / Ducc / baidu-cc 集成安装器（三者共用同一套 hook 协议）。"""

    AGENT_NAME = "claude"

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.settings_file = self.project_dir / ".claude" / "settings.json"
        self.claude_md = self.project_dir / "CLAUDE.md"

    # -- 公共 API ----------------------------------------------------------

    def install(self, dry_run: bool = False) -> InstallResult:
        result = InstallResult(agent=self.AGENT_NAME, dry_run=dry_run)
        self._install_settings(result, dry_run=dry_run)
        self._install_claude_md(result, dry_run=dry_run)
        return result

    def uninstall(self, dry_run: bool = False) -> InstallResult:
        result = InstallResult(agent=self.AGENT_NAME, dry_run=dry_run)
        self._uninstall_settings(result, dry_run=dry_run)
        self._uninstall_claude_md(result, dry_run=dry_run)
        return result

    def status(self) -> Dict[str, Any]:
        """检查当前安装状态（不动文件）。"""
        return {
            "agent": self.AGENT_NAME,
            "settings_file": str(self.settings_file),
            "settings_exists": self.settings_file.exists(),
            "settings_has_hook": self._settings_has_hook(),
            "claude_md": str(self.claude_md),
            "claude_md_exists": self.claude_md.exists(),
            "claude_md_has_block": self._claude_md_has_block(),
        }

    # -- settings.json：安装 ----------------------------------------------

    def _install_settings(self, result: InstallResult, dry_run: bool) -> None:
        existing, was_present = self._load_settings()
        new_data, action_detail = self._merge_hook_into_settings(existing)

        if existing == new_data and was_present:
            result.add(self.settings_file, "unchanged",
                       "PostToolUse hook 已存在，无需修改")
            return

        action = "updated" if was_present else "created"
        if not dry_run:
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)
            self.settings_file.write_text(
                json.dumps(new_data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        result.add(self.settings_file, action, action_detail)

    def _uninstall_settings(self, result: InstallResult, dry_run: bool) -> None:
        if not self.settings_file.exists():
            result.add(self.settings_file, "unchanged", "settings.json 不存在")
            return

        existing, _ = self._load_settings()
        new_data, removed_count = self._remove_hook_from_settings(existing)

        if removed_count == 0:
            result.add(self.settings_file, "unchanged",
                       "settings.json 中没有 harness 相关 hook")
            return

        if not dry_run:
            self.settings_file.write_text(
                json.dumps(new_data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        result.add(self.settings_file, "updated",
                   f"已移除 {removed_count} 条 harness hook（含 PostToolUse + SessionStart）")

    def _settings_has_hook(self) -> bool:
        if not self.settings_file.exists():
            return False
        existing, _ = self._load_settings()
        return self._count_harness_hooks(existing) > 0

    # -- settings.json：解析与合并 ---------------------------------------

    def _load_settings(self) -> Tuple[Dict[str, Any], bool]:
        """读取 settings.json；不存在或损坏 → 返回空 dict + was_present=False。"""
        if not self.settings_file.exists():
            return {}, False
        try:
            data = json.loads(self.settings_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {}, False
            return data, True
        except (json.JSONDecodeError, OSError):
            return {}, False

    @staticmethod
    def _is_harness_hook_entry(hook_entry: Any) -> bool:
        """判断一个 hook 条目（hooks[].hooks[].command 的最内层）是否 harness 装的。

        识别三类 hook：validate-code.sh / inject-lessons.sh / refresh-generated.sh
        """
        if not isinstance(hook_entry, dict):
            return False
        cmd = hook_entry.get("command")
        if not isinstance(cmd, str):
            return False
        return any(fp in cmd for fp in ALL_HOOK_FINGERPRINTS)

    def _count_harness_hooks(self, settings: Dict[str, Any]) -> int:
        count = 0
        for groups in (settings.get("hooks") or {}).values():
            if not isinstance(groups, list):
                continue
            for group in groups:
                for entry in (group.get("hooks") or []):
                    if self._is_harness_hook_entry(entry):
                        count += 1
        return count

    def _merge_hook_into_settings(
        self, settings: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], str]:
        """把 harness 的 PostToolUse + SessionStart hook 合并进 settings。"""
        data = json.loads(json.dumps(settings)) if settings else {}
        hooks_node = data.setdefault("hooks", {})
        post_tool = hooks_node.setdefault("PostToolUse", [])
        session_start = hooks_node.setdefault("SessionStart", [])

        target_matcher_set = {"Write", "Edit", "MultiEdit"}
        target_matcher_str = "Write|Edit|MultiEdit"

        removed_old = self._remove_harness_entries_from_groups(post_tool)
        post_tool[:] = [g for g in post_tool if (g.get("hooks") or [])]
        removed_old += self._remove_harness_entries_from_groups(session_start)
        session_start[:] = [g for g in session_start if (g.get("hooks") or [])]

        target_group: Optional[Dict[str, Any]] = None
        for group in post_tool:
            matcher = group.get("matcher") or ""
            if isinstance(matcher, str) and set(matcher.split("|")) == target_matcher_set:
                target_group = group
                break

        post_entries = [
            {"type": "command", "command": HOOK_COMMAND, "timeout": 30},
            {"type": "command", "command": INJECT_LESSONS_COMMAND, "timeout": 15},
        ]

        if target_group is not None:
            target_group.setdefault("hooks", []).extend(post_entries)
            detail = f"已合并到现有 PostToolUse(matcher={target_matcher_str}) 组（validate + inject-lessons）"
        else:
            post_tool.append({
                "matcher": target_matcher_str,
                "hooks": post_entries,
            })
            detail = f"已新增 PostToolUse(matcher={target_matcher_str}) 组（validate + inject-lessons）"

        session_start.append({
            "hooks": [{
                "type": "command",
                "command": REFRESH_GENERATED_COMMAND,
                "timeout": 20,
            }],
        })
        detail += "；已注册 SessionStart refresh-generated"

        if removed_old > 0:
            detail += f"；清理旧 harness hook {removed_old} 条"

        return data, detail

    def _remove_hook_from_settings(
        self, settings: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], int]:
        """从 settings 里摘掉所有 harness 装的 hook，返回 (新 settings, 移除条数)。"""
        data = json.loads(json.dumps(settings)) if settings else {}
        hooks_node = data.get("hooks") or {}

        removed = 0
        for event_name, groups in list(hooks_node.items()):
            if not isinstance(groups, list):
                continue
            removed += self._remove_harness_entries_from_groups(groups)
            hooks_node[event_name] = [g for g in groups if (g.get("hooks") or [])]
            if not hooks_node[event_name]:
                del hooks_node[event_name]

        if "hooks" in data and not data["hooks"]:
            del data["hooks"]

        return data, removed

    def _remove_harness_entries_from_groups(self, groups: List[Dict[str, Any]]) -> int:
        removed = 0
        for group in groups:
            inner = group.get("hooks") or []
            kept = []
            for entry in inner:
                if self._is_harness_hook_entry(entry):
                    removed += 1
                    continue
                kept.append(entry)
            group["hooks"] = kept
        return removed

    # -- CLAUDE.md：安装/卸载 ---------------------------------------------

    def _install_claude_md(self, result: InstallResult, dry_run: bool) -> None:
        existing = self._read_claude_md()
        new_text, was_present, block_present = self._merge_block_into_claude_md(existing)

        if existing == new_text:
            result.add(self.claude_md, "unchanged", "CLAUDE.md 托管块已是最新")
            return

        action = "updated" if was_present else "created"
        detail = "替换托管块" if block_present else "追加托管块"
        if not dry_run:
            self.claude_md.write_text(new_text, encoding="utf-8")
        result.add(self.claude_md, action, detail)

    def _uninstall_claude_md(self, result: InstallResult, dry_run: bool) -> None:
        if not self.claude_md.exists():
            result.add(self.claude_md, "unchanged", "CLAUDE.md 不存在")
            return

        existing = self._read_claude_md()
        new_text = self._remove_block_from_claude_md(existing)

        if existing == new_text:
            result.add(self.claude_md, "unchanged", "CLAUDE.md 中没有托管块")
            return

        # 卸载后若 CLAUDE.md 整体只剩空白，干脆删掉文件
        if new_text.strip() == "":
            if not dry_run:
                self.claude_md.unlink()
            result.add(self.claude_md, "removed", "卸载后 CLAUDE.md 为空 → 删除")
            return

        if not dry_run:
            self.claude_md.write_text(new_text, encoding="utf-8")
        result.add(self.claude_md, "updated", "已移除 harness 托管块")

    def _claude_md_has_block(self) -> bool:
        if not self.claude_md.exists():
            return False
        return CLAUDE_MD_BEGIN in self._read_claude_md()

    def _read_claude_md(self) -> str:
        if not self.claude_md.exists():
            return ""
        try:
            return self.claude_md.read_text(encoding="utf-8")
        except OSError:
            return ""

    @staticmethod
    def _build_managed_block() -> str:
        return f"{CLAUDE_MD_BEGIN}\n{CLAUDE_MD_BLOCK_BODY.strip()}\n{CLAUDE_MD_END}\n"

    def _merge_block_into_claude_md(
        self, existing: str
    ) -> Tuple[str, bool, bool]:
        """返回 (新文本, 文件原本是否存在, 托管块原本是否存在)。"""
        was_present = bool(existing)
        block = self._build_managed_block()

        if not existing:
            # 新建：上来一段 H1 + 托管块
            new_text = "# 项目说明\n\n" + block
            return new_text, False, False

        if CLAUDE_MD_BEGIN in existing and CLAUDE_MD_END in existing:
            # 替换托管块
            pattern = re.compile(
                re.escape(CLAUDE_MD_BEGIN) + r".*?" + re.escape(CLAUDE_MD_END) + r"\n?",
                flags=re.DOTALL,
            )
            new_text = pattern.sub(block, existing, count=1)
            return new_text, True, True

        # 追加：在末尾插
        sep = "" if existing.endswith("\n") else "\n"
        new_text = existing + sep + "\n" + block
        return new_text, True, False

    @staticmethod
    def _remove_block_from_claude_md(existing: str) -> str:
        if CLAUDE_MD_BEGIN not in existing:
            return existing
        pattern = re.compile(
            r"\n*" + re.escape(CLAUDE_MD_BEGIN) + r".*?"
            + re.escape(CLAUDE_MD_END) + r"\n?",
            flags=re.DOTALL,
        )
        return pattern.sub("", existing, count=1)


# ---------------------------------------------------------------------------
# Agent 工厂
# ---------------------------------------------------------------------------


_INSTALLER_REGISTRY = {
    "claude": ClaudeInstaller,
    # Ducc / baidu-cc 共用 Claude Code 协议：通过 alias 复用同一个 installer
    "ducc": ClaudeInstaller,
    "baidu-cc": ClaudeInstaller,
}


def get_installer(agent: str, project_dir: Path) -> ClaudeInstaller:
    cls = _INSTALLER_REGISTRY.get(agent)
    if cls is None:
        raise ValueError(
            f"未知 agent: {agent}；当前支持 {sorted(set(_INSTALLER_REGISTRY))}"
        )
    return cls(project_dir=project_dir)
