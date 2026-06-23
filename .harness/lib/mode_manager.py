#!/usr/bin/env python3
"""Harness 2.0 治理模式（P1 附加项）—— lib/mode_manager.py

实现设计文档 §4.4：
- GovernanceMode = strict / relaxed / off
- ModeManager   持久化在 .harness/mode-config.json
- ValidationContext 把 mode 应用到 issue 流（过滤 + 严重度降级）

与设计文档的两处工程化偏差（已在 §十一 P2 重构清单留痕）：
1. RuleConfig.pattern 支持 fnmatch 通配（如 "arch-*-import"），匹配 validator
   实际产出的动态 rule_id（按 layer 名生成）。设计文档原文是静态枚举，
   会漏掉新增的 layer。
2. mode 加载链：mode-config.json → rules.yaml.governance.default_mode → strict。
   保留环境无 mode-config.json 也能跑的能力。

设计原则：validator 不感知 mode（保持纯函数）；CLI 入口在 validator 之后套
ValidationContext.filter_issues 输出最终视图。cache key 已含 mode（P0 #4），
所以缓存层无需再改。
"""

from __future__ import annotations

import datetime as _dt
import fnmatch
import json
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Mode 枚举
# ---------------------------------------------------------------------------


class GovernanceMode(str, Enum):
    """治理模式。"""

    STRICT = "strict"     # 全量检查，拦截一切
    RELAXED = "relaxed"   # 仅检查严重错误，警告/命名规范降级
    OFF = "off"           # 关闭被动验证，但 scan / generate 仍正常运行


# ---------------------------------------------------------------------------
# 规则配置
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleConfig:
    """单条规则配置。

    pattern 可为精确 rule_id（如 "import-forbidden"）或 fnmatch 通配
    （如 "arch-*-import" / "naming-*"）。
    """

    pattern: str
    category: str            # parse / architecture / import / type-safety / naming
    severity: str            # error / warning / info
    enabled_in_relaxed: bool
    auto_fix: bool


# 默认规则表 —— 与 validator.py 当前/计划产出的 rule_id 对齐。
# 顺序即匹配优先级；先精确匹配，未命中再走通配。
DEFAULT_RULES: List[RuleConfig] = [
    # parse —— 永远启用，relaxed 也保留（语法错本身就是阻断级）
    RuleConfig("parse-error",       "parse",         "error",   True,  False),
    RuleConfig("parse-exception",   "parse",         "error",   True,  False),
    RuleConfig("file-not-found",    "parse",         "error",   True,  False),

    # architecture —— relaxed 仍拦截（架构错是上限红线）
    RuleConfig("arch-*-import",     "architecture",  "error",   True,  False),

    # import —— relaxed 仍拦截，且 import-forbidden 可机械修复（P1 #7）
    RuleConfig("import-forbidden",  "import",        "error",   True,  True),

    # naming —— relaxed 不启用（这是 strict 才管的"风格洁癖")
    # P0/P1 validator 还没产出 naming-* 规则；P2 接入命名规范检查后会用到。
    RuleConfig("naming-*",          "naming",        "warning", False, False),

    # type-safety —— relaxed 仍拦截（类型问题往往是真 bug）
    RuleConfig("type-no-any",       "type-safety",   "error",   True,  False),
    RuleConfig("type-mismatch",     "type-safety",   "error",   True,  False),

    # hook 调用规则（React Rules-of-Hooks 项目层叠加）—— 误调用层面是真 bug
    RuleConfig("hook-call-misplaced",   "hook",      "error",   True,  False),
    RuleConfig("hook-call-top-level",   "hook",      "error",   True,  False),
    RuleConfig("hook-call-in-plain-func","hook",     "error",   True,  False),
    RuleConfig("hook-call-conditional", "hook",      "error",   True,  False),

    # 全项目维度（harness check）
    RuleConfig("cycle",             "cycle",         "error",   True,  False),
    RuleConfig("unused-export",     "unused-export", "warning", False, False),

    # 命名相似度 —— info/warning 提示性，relaxed 不启用
    RuleConfig("name-similarity",   "naming",        "warning", False, False),
]


# ---------------------------------------------------------------------------
# ModeManager
# ---------------------------------------------------------------------------


class ModeManager:
    """治理模式管理器。"""

    def __init__(
        self,
        harness_dir: Path,
        rules: Optional[List[RuleConfig]] = None,
    ) -> None:
        self.harness_dir = Path(harness_dir).resolve()
        self.config_file = self.harness_dir / "mode-config.json"
        self.rules: List[RuleConfig] = list(rules if rules is not None else DEFAULT_RULES)
        self.current_mode: GovernanceMode = self._load_mode()

    # -- 持久化 -------------------------------------------------------------

    def _load_mode(self) -> GovernanceMode:
        """优先级：mode-config.json → rules.yaml.governance.default_mode → strict。"""
        # 1) mode-config.json
        if self.config_file.exists():
            try:
                data = json.loads(self.config_file.read_text(encoding="utf-8"))
                return GovernanceMode(data.get("mode", "strict"))
            except (json.JSONDecodeError, ValueError, OSError):
                pass

        # 2) rules.yaml governance.default_mode
        rules_file = self.harness_dir / "rules.yaml"
        if rules_file.exists():
            try:
                import yaml
                data = yaml.safe_load(rules_file.read_text(encoding="utf-8")) or {}
                default_mode = (data.get("governance") or {}).get("default_mode", "strict")
                return GovernanceMode(default_mode)
            except (Exception,):  # yaml.YAMLError / ValueError / OSError
                pass

        # 3) 最终兜底
        return GovernanceMode.STRICT

    def set_mode(self, mode: GovernanceMode) -> GovernanceMode:
        """持久化模式到 mode-config.json，返回新模式。"""
        self.current_mode = mode
        self.harness_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "mode": mode.value,
            "updated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        }
        self.config_file.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return mode

    def toggle_mode(self) -> GovernanceMode:
        """循环切换：strict → relaxed → off → strict。"""
        cycle = [GovernanceMode.STRICT, GovernanceMode.RELAXED, GovernanceMode.OFF]
        try:
            idx = cycle.index(self.current_mode)
        except ValueError:
            idx = -1
        return self.set_mode(cycle[(idx + 1) % len(cycle)])

    # -- 规则查询 -----------------------------------------------------------

    def find_rule(self, rule_id: str) -> Optional[RuleConfig]:
        """按精确再通配的顺序找首个匹配规则。"""
        # 精确优先
        for rule in self.rules:
            if rule.pattern == rule_id:
                return rule
        # 通配兜底
        for rule in self.rules:
            if "*" in rule.pattern or "?" in rule.pattern:
                if fnmatch.fnmatchcase(rule_id, rule.pattern):
                    return rule
        return None

    def is_rule_enabled(self, rule_id: str) -> bool:
        """当前模式下，该 rule_id 是否参与验证。

        - STRICT  → 所有 rule 都启用
        - OFF     → 全部关闭
        - RELAXED → 仅启用 enabled_in_relaxed=True 的规则；
                    未在规则表中的未知 rule_id 视为"保守启用"（避免漏报）
        """
        if self.current_mode == GovernanceMode.OFF:
            return False
        if self.current_mode == GovernanceMode.STRICT:
            return True
        rule = self.find_rule(rule_id)
        if rule is None:
            return True  # 未知 rule 默认保守启用
        return rule.enabled_in_relaxed

    def get_enabled_rules(self) -> List[RuleConfig]:
        if self.current_mode == GovernanceMode.OFF:
            return []
        if self.current_mode == GovernanceMode.STRICT:
            return list(self.rules)
        return [r for r in self.rules if r.enabled_in_relaxed]

    def should_validate(self) -> bool:
        """整体是否应该跑验证（OFF 模式下可让 hook 直接放行）。"""
        return self.current_mode != GovernanceMode.OFF

    def should_block(self, severity: str) -> bool:
        """根据模式 + 严重度判断是否拦截（hook exit code 用）。"""
        if self.current_mode == GovernanceMode.OFF:
            return False
        if self.current_mode == GovernanceMode.RELAXED:
            return severity == "error"
        return True

    # -- 摘要 ---------------------------------------------------------------

    def get_mode_info(self) -> Dict[str, Any]:
        enabled = self.get_enabled_rules()
        descriptions = {
            GovernanceMode.STRICT:  "全量检查，拦截一切问题",
            GovernanceMode.RELAXED: "仅拦截 error；warning 降级为 info；命名等风格规则关闭",
            GovernanceMode.OFF:     "被动验证关闭；scan / generate 仍运行",
        }
        return {
            "mode": self.current_mode.value,
            "description": descriptions.get(self.current_mode, ""),
            "enabled_rules_count": len(enabled),
            "error_rules": sum(1 for r in enabled if r.severity == "error"),
            "warning_rules": sum(1 for r in enabled if r.severity == "warning"),
            "auto_fix_rules": sum(1 for r in enabled if r.auto_fix),
            "config_file": str(self.config_file),
        }


# ---------------------------------------------------------------------------
# ValidationContext
# ---------------------------------------------------------------------------


class ValidationContext:
    """把 mode 应用到 issue 流。

    输入 / 输出都是 lib.validator.Issue 的列表，调用方无需关心 mode 细节。
    """

    def __init__(self, mode_manager: ModeManager) -> None:
        self.mode_manager = mode_manager

    def filter_issues(self, issues: List[Any]) -> List[Any]:
        """根据当前模式过滤 + 调整严重度。

        - OFF     → 返回空列表
        - STRICT  → 原样透传
        - RELAXED → 丢弃禁用规则；warning 降级为 info；error 保留
        """
        mode = self.mode_manager.current_mode
        if mode == GovernanceMode.OFF:
            return []
        if mode == GovernanceMode.STRICT:
            return list(issues)

        result: List[Any] = []
        for issue in issues:
            if not self.mode_manager.is_rule_enabled(issue.rule_id):
                continue
            if issue.severity == "warning":
                # 用 dataclasses.replace 保留其它字段不变
                issue = replace(issue, severity="info")
            result.append(issue)
        return result

    def should_block(self, issues: List[Any]) -> bool:
        """是否需要拦截（任一 issue 触发 mode_manager.should_block 即返回 True）。"""
        return any(self.mode_manager.should_block(i.severity) for i in issues)
