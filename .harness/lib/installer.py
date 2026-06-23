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


# Claude Code hook 命令（识别 harness 安装的唯一依据；改了等于无法卸载历史安装）
HOOK_COMMAND = 'bash "$CLAUDE_PROJECT_DIR/.harness/hooks/validate-code.sh"'

# 我们用于识别 hook 是不是 harness 装的子串（兼容用户改过环境变量名的极端情况）
HOOK_COMMAND_FINGERPRINT = ".harness/hooks/validate-code.sh"

# Lesson 动态注入 hook —— 与 validate 是同位面的两个 PostToolUse hook
# validate 拦截违规（exit 2）；inject-lessons 永远 exit 0 但通过 additionalContext
# 给 Agent 推送相关团队经验。两者并存，互不影响。
INJECT_LESSONS_COMMAND = 'bash "$CLAUDE_PROJECT_DIR/.harness/hooks/inject-lessons.sh"'
INJECT_LESSONS_FINGERPRINT = ".harness/hooks/inject-lessons.sh"

# 所有 harness 在 PostToolUse 里装的 hook 指纹（_count / _remove 一并处理）
ALL_POST_TOOL_FINGERPRINTS = (HOOK_COMMAND_FINGERPRINT, INJECT_LESSONS_FINGERPRINT)

# CLAUDE.md 托管块标记
CLAUDE_MD_BEGIN = "<!-- harness:begin (managed; do not edit between markers) -->"
CLAUDE_MD_END = "<!-- harness:end -->"

# CLAUDE.md 托管块内容模板（首尾两个标记之间）
CLAUDE_MD_BLOCK_BODY = """
> 本节由 `harness install` 维护。规则随 `harness scan` 自动刷新。

@.harness/generated/claude.md

**工作流**：

- 修改 `src/**/*.{ts,tsx,d.ts}` 后，PostToolUse hook 会自动跑 `harness validate`，违规会被拦截并把错误塞回我让我自己改
- 治理模式（拦截力度）切换：`harness mode <strict|relaxed|off>`
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

        # 如果 settings.json 卸载完后变成空 hooks/空文件，是否删？保守起见保留文件，
        # 让用户自己决定。仅写回精简后的内容。
        if not dry_run:
            self.settings_file.write_text(
                json.dumps(new_data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        result.add(self.settings_file, "updated",
                   f"已移除 {removed_count} 条 harness hook")

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

        识别两类 PostToolUse hook：validate-code.sh + inject-lessons.sh
        """
        if not isinstance(hook_entry, dict):
            return False
        cmd = hook_entry.get("command")
        if not isinstance(cmd, str):
            return False
        return any(fp in cmd for fp in ALL_POST_TOOL_FINGERPRINTS)

    def _count_harness_hooks(self, settings: Dict[str, Any]) -> int:
        count = 0
        post_tool = ((settings.get("hooks") or {}).get("PostToolUse") or [])
        for group in post_tool:
            for entry in (group.get("hooks") or []):
                if self._is_harness_hook_entry(entry):
                    count += 1
        return count

    def _merge_hook_into_settings(
        self, settings: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], str]:
        """把 harness 的两个 PostToolUse hook（validate + inject-lessons）合并进 settings。

        合并策略：
        1. 先扫一遍 PostToolUse 所有 group，删掉**所有**老 harness hook 条目
           （按 fingerprint 识别，覆盖 validate-code.sh + inject-lessons.sh 两种）
        2. 找一个 matcher 等价于 "Write|Edit|MultiEdit" 的现有 group 把新条目插进去；
           找不到就新建一个 group
        3. 两个 hook 一起插入（顺序：validate 先于 inject-lessons，保持 stderr/stdout 语义独立）
        """
        # 深拷贝避免改入参
        data = json.loads(json.dumps(settings)) if settings else {}
        hooks_node = data.setdefault("hooks", {})
        post_tool = hooks_node.setdefault("PostToolUse", [])

        target_matcher_set = {"Write", "Edit", "MultiEdit"}
        target_matcher_str = "Write|Edit|MultiEdit"

        # --- 1) 移除所有"老的" harness hook（由 fingerprint 识别）-------
        removed_old = 0
        for group in post_tool:
            inner = group.get("hooks") or []
            kept = []
            for entry in inner:
                if self._is_harness_hook_entry(entry):
                    removed_old += 1
                    continue
                kept.append(entry)
            group["hooks"] = kept
        # 清掉因为移除 harness 后变空的 group（仅当 group 整组只剩空 hooks 时）
        post_tool[:] = [g for g in post_tool if (g.get("hooks") or [])]

        # --- 2) 找一个目标 matcher 的 group ----------------------------
        target_group: Optional[Dict[str, Any]] = None
        for group in post_tool:
            matcher = group.get("matcher") or ""
            if isinstance(matcher, str) and set(matcher.split("|")) == target_matcher_set:
                target_group = group
                break

        new_entries = [
            {"type": "command", "command": HOOK_COMMAND, "timeout": 30},
            {"type": "command", "command": INJECT_LESSONS_COMMAND, "timeout": 15},
        ]

        if target_group is not None:
            target_group.setdefault("hooks", []).extend(new_entries)
            detail = f"已合并到现有 PostToolUse(matcher={target_matcher_str}) 组（validate + inject-lessons）"
        else:
            post_tool.append({
                "matcher": target_matcher_str,
                "hooks": new_entries,
            })
            detail = f"已新增 PostToolUse(matcher={target_matcher_str}) 组（validate + inject-lessons）"

        if removed_old > 0:
            detail += f"；清理旧 harness hook {removed_old} 条"

        return data, detail

    def _remove_hook_from_settings(
        self, settings: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], int]:
        """从 settings 里摘掉所有 harness 装的 hook，返回 (新 settings, 移除条数)。"""
        data = json.loads(json.dumps(settings)) if settings else {}
        post_tool = ((data.get("hooks") or {}).get("PostToolUse") or [])

        removed = 0
        for group in post_tool:
            inner = group.get("hooks") or []
            kept = []
            for entry in inner:
                if self._is_harness_hook_entry(entry):
                    removed += 1
                    continue
                kept.append(entry)
            group["hooks"] = kept

        # 清掉空 group
        if "hooks" in data and "PostToolUse" in data["hooks"]:
            data["hooks"]["PostToolUse"] = [
                g for g in post_tool if (g.get("hooks") or [])
            ]
            # 如果 PostToolUse 变成空数组，删掉它（让 settings 干净）
            if not data["hooks"]["PostToolUse"]:
                del data["hooks"]["PostToolUse"]
            # 如果 hooks 空了，也删掉
            if not data["hooks"]:
                del data["hooks"]

        return data, removed

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
