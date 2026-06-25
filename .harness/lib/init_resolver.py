#!/usr/bin/env python3
"""Harness 2.0 init 冲突协商器 —— lib/init_resolver.py

`harness init` 三段式（probe → resolve → apply）的第二段：拿 ProbeReport
对照默认 rules.yaml，决定哪些条目可以静默采纳、哪些得抛 conflict 让用户拍板。

设计原则（与用户确认过）：
- **尊重现状**：探测到的别名 / layer 路径 / 命名风格倾向于覆盖默认。
- **显式提示**：分歧不静默吞掉，进 ProposedPlan.conflicts，由 CLI 交互层展示。
- **业务语义不猜**：forbidden_imports / rewrites 一律留空，让用户后续手动加。

本模块**不**写盘、不读 stdin。CLI 层负责把 conflicts 渲染成提问，
拿到用户答复后再调用 `Resolver.apply_user_choices(...)` 拿到最终 rules dict。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .probe import (
    AliasFinding,
    LayerFinding,
    NamingFinding,
    ProbeReport,
)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class ConflictChoice:
    """单条 conflict 的可选项。"""

    key: str              # 机读 id，CLI 用它回填
    label: str            # 人读标签
    detail: str = ""      # 详细说明（可选）


@dataclass
class Conflict:
    """一条需要用户拍板的分歧。"""

    id: str               # 全局唯一 id，例 "layer-path:page"
    category: str         # alias / layer-path / unknown-dir / naming
    title: str            # 一行说明（给 CLI 渲染成问句）
    detail: str           # 详细背景（多行）
    choices: List[ConflictChoice] = field(default_factory=list)
    default_choice: Optional[str] = None   # 推荐项的 key


@dataclass
class ProposedPlan:
    """resolver 的产出：建议的 rules + 待用户拍板的冲突清单 + 给用户看的说明。"""

    rules: Dict[str, Any]                            # 已经应用了"静默采纳"的 rules
    conflicts: List[Conflict] = field(default_factory=list)
    adopted_notes: List[str] = field(default_factory=list)   # 自动采纳了什么
    info_notes: List[str] = field(default_factory=list)      # 中性提示（不需用户决策）


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


class InitResolver:
    """对照 ProbeReport 和默认 rules，生成 ProposedPlan。

    用法::

        plan = InitResolver(default_rules, probe_report).resolve()
        # CLI 层拿 plan.conflicts 去交互
        final_rules = InitResolver(default_rules, probe_report).apply_user_choices(plan, answers)

    `answers`：`{conflict_id: choice_key}` 形式。
    """

    # 默认 layer 名（probe 用同一套词表，保持一致）
    # B1: 仅作为 _layer_names() 的兜底,实际值优先来自 default_rules.architecture.layers
    _LAYER_NAMES = ("component", "hook", "service", "type", "page")

    # 命名风格关键字 → 探测层指标的最小命中比例
    _NAMING_PRESETS = {
        "PascalCase": ("pascal_ratio", 0.6),
        "camelCase": ("camel_ratio", 0.6),
        "camelCase-with-use-prefix": ("use_prefix_ratio", 0.5),
        "camelCase-with-Service-suffix": ("service_suffix_ratio", 0.5),
    }

    def __init__(self, default_rules: Dict[str, Any], report: ProbeReport) -> None:
        self.default_rules = copy.deepcopy(default_rules)
        self.report = report

    def _layer_names(self) -> List[str]:
        """B1: 从 default_rules 读 layer 名清单,缺失时回 _LAYER_NAMES 兜底。

        让 init_resolver 不再写死"只识别 component/hook/service/type/page",
        rules.yaml 里加 `layer: widget` 后,unknown-dir conflict 自动多出 widget 选项。
        """
        layers = ((self.default_rules or {}).get("architecture") or {}).get("layers") or []
        names = [l.get("name") for l in layers if isinstance(l, dict) and l.get("name")]
        return names if names else list(self._LAYER_NAMES)

    # ---- 主入口 ----------------------------------------------------------

    def resolve(self) -> ProposedPlan:
        plan = ProposedPlan(rules=copy.deepcopy(self.default_rules))
        self._resolve_aliases(plan)
        self._resolve_layers(plan)
        self._resolve_unknown_dirs(plan)
        self._resolve_naming(plan)
        self._record_probe_info(plan)
        return plan

    def apply_user_choices(
        self, plan: ProposedPlan, answers: Dict[str, str],
    ) -> Dict[str, Any]:
        """把 CLI 拿到的 `{conflict_id: choice_key}` 落到 rules 上。

        未给出答复的 conflict 视为采用 default_choice；如果连 default 都没有，
        保留 plan.rules 当前状态不变（resolver 已经填了一份能跑的默认）。
        """
        rules = copy.deepcopy(plan.rules)
        index = {c.id: c for c in plan.conflicts}
        for cid, choice in answers.items():
            c = index.get(cid)
            if not c:
                continue
            self._apply_one(rules, c, choice)
        # 补足没回答的
        for c in plan.conflicts:
            if c.id in answers:
                continue
            if c.default_choice:
                self._apply_one(rules, c, c.default_choice)
        return rules

    # ---- 别名 ------------------------------------------------------------

    # 当 rules.yaml 没显式配 scanner.import_alias(es) 时，scanner 内部会按
    # "@/" → source_root 当兜底。这里的"隐式基线"只用作"探测值是否等于默认"
    # 的对比锚点，不写进 plan.rules（写进去会冒充用户显式配置）。
    _IMPLICIT_DEFAULT_ALIAS = ("@", "src")

    def _resolve_aliases(self, plan: ProposedPlan) -> None:
        """tsconfig.json paths → scanner.import_aliases。

        策略（Q1=A）：探测到的别名与默认完全一致就静默采纳；否则采纳探测值，
        但记一条 adopted_notes 告知用户。多别名场景一律采纳全部。
        """
        scanner = plan.rules.setdefault("scanner", {})
        explicit_default = self._normalize_aliases(
            scanner.get("import_aliases"), scanner.get("import_alias"),
        )
        probed = [
            (a.prefix.rstrip("/"), a.target.rstrip("/"))
            for a in self.report.aliases
        ]
        if not probed:
            return
        # 显式默认存在 → 直接对比；不存在 → 看探测值是否就是隐式基线 @/* → src/*
        if explicit_default:
            equal = self._aliases_equal(probed, explicit_default)
        else:
            equal = (probed == [self._IMPLICIT_DEFAULT_ALIAS])
        if equal:
            plan.adopted_notes.append(
                f"别名沿用默认（{self._fmt_aliases(probed)}）",
            )
            return
        # 不一致 → 采纳探测值
        if len(probed) == 1:
            scanner.pop("import_aliases", None)
            scanner["import_alias"] = {
                "prefix": probed[0][0] + "/",
                "target": probed[0][1] + "/",
            }
        else:
            scanner.pop("import_alias", None)
            scanner["import_aliases"] = [
                {"prefix": p + "/", "target": t + "/"} for p, t in probed
            ]
        plan.adopted_notes.append(
            f"采纳 tsconfig.json 探测到的别名：{self._fmt_aliases(probed)}",
        )

    @staticmethod
    def _normalize_aliases(
        many: Optional[Any], one: Optional[Any],
    ) -> List[tuple]:
        """把 rules.yaml 里的 import_aliases / import_alias 拍平成 [(prefix, target)]。

        没显式配返回空列表（caller 自己判定怎么处理隐式基线）。
        """
        out: List[tuple] = []
        if isinstance(many, list):
            for item in many:
                if isinstance(item, dict):
                    p = str(item.get("prefix") or "").rstrip("/")
                    t = str(item.get("target") or "").rstrip("/")
                    if p and t:
                        out.append((p, t))
        if not out and isinstance(one, dict):
            p = str(one.get("prefix") or "").rstrip("/")
            t = str(one.get("target") or "").rstrip("/")
            if p and t:
                out.append((p, t))
        return out

    @staticmethod
    def _aliases_equal(a: List[tuple], b: List[tuple]) -> bool:
        return sorted(a) == sorted(b)

    @staticmethod
    def _fmt_aliases(items: List[tuple]) -> str:
        return ", ".join(f"{p}/ → {t}/" for p, t in items)

    # ---- layer 路径 ------------------------------------------------------

    def _resolve_layers(self, plan: ProposedPlan) -> None:
        """layer 路径与默认 paths 不一致 → 采纳探测值（Q2=A）。

        例：默认 page=["src/pages/"]，探测到 page→src/views/，
        采纳后 page.paths 变 ["src/views/"]，并写 adopted_notes。
        """
        arch = plan.rules.setdefault("architecture", {})
        layers = arch.setdefault("layers", [])
        # 探测到的各 layer → set of paths
        probed_by_layer: Dict[str, List[str]] = {}
        for L in self.report.layers:
            if L.name == "unknown":
                continue
            probed_by_layer.setdefault(L.name, []).append(L.path)

        for layer_def in layers:
            name = layer_def.get("name")
            if not name or name not in probed_by_layer:
                continue
            default_paths = list(layer_def.get("paths") or [])
            probed_paths = probed_by_layer[name]
            if sorted(default_paths) == sorted(probed_paths):
                continue
            # 不一致 → 采纳探测值
            layer_def["paths"] = probed_paths
            plan.adopted_notes.append(
                f"layer '{name}' 路径采用探测结果：{probed_paths}（默认是 {default_paths}）",
            )

    # ---- unknown 目录 ----------------------------------------------------

    def _resolve_unknown_dirs(self, plan: ProposedPlan) -> None:
        """src/ 下不在常规词表里的目录 → 抛 conflict（Q3=A）。"""
        for L in self.report.layers:
            if L.name != "unknown":
                continue
            dir_name = L.path.rstrip("/").rsplit("/", 1)[-1]
            cid = f"unknown-dir:{dir_name}"
            choices = [
                ConflictChoice(
                    key=name,
                    label=f"映射为 {name}",
                    detail=f"把 {L.path} 加进 layer '{name}' 的 paths",
                )
                for name in self._layer_names()
            ]
            choices.append(ConflictChoice(
                key="ignore",
                label="忽略（加进 scanner.exclude_dirs）",
                detail=f"扫描器会跳过 {L.path}",
            ))
            choices.append(ConflictChoice(
                key="skip",
                label="先放着不处理",
                detail="这次 init 不动这个目录，后续手动改 rules.yaml",
            ))
            plan.conflicts.append(Conflict(
                id=cid,
                category="unknown-dir",
                title=f"src/ 下发现未识别目录：{L.path}（{L.file_count} 个 ts/tsx 文件）",
                detail=(
                    f"示例文件：{', '.join(L.sample_names[:5]) or '（空）'}\n"
                    "请选择如何处理"
                ),
                choices=choices,
                default_choice="skip",
            ))

    # ---- 命名风格 --------------------------------------------------------

    def _resolve_naming(self, plan: ProposedPlan) -> None:
        """对比默认 naming 与探测样本，比例对不上就抛 conflict（Q4=A）。"""
        naming_cfg = plan.rules.setdefault("naming", {})
        # 样本数太小（每层 < 3）就别拿来挑战默认了
        SAMPLE_MIN = 3
        for nf in self.report.naming:
            layer = nf.layer
            if layer == "unknown" or layer not in naming_cfg:
                continue
            if nf.sample_size < SAMPLE_MIN:
                continue
            default_style = naming_cfg.get(layer)
            preset = self._NAMING_PRESETS.get(default_style)
            if not preset:
                continue
            field_name, min_ratio = preset
            actual = getattr(nf, field_name, 0.0)
            if actual >= min_ratio:
                continue   # 默认风格能解释样本，不打扰用户
            # 命名风格与默认背离 → 抛 conflict
            suggested = self._suggest_style(nf)
            choices = [
                ConflictChoice(
                    key="keep-default",
                    label=f"保留默认 '{default_style}'",
                    detail=f"现状仅 {round(actual*100)}% 命中，validator 会报很多 naming-violation",
                ),
            ]
            if suggested and suggested != default_style:
                choices.append(ConflictChoice(
                    key=f"adopt:{suggested}",
                    label=f"采纳现状风格 '{suggested}'",
                    detail="把 naming 配置改成与现有代码一致",
                ))
            choices.append(ConflictChoice(
                key="disable",
                label="不校验该层命名",
                detail=f"把 naming.{layer} 设为空字符串 / 删除",
            ))
            plan.conflicts.append(Conflict(
                id=f"naming:{layer}",
                category="naming",
                title=f"layer '{layer}' 命名与默认风格 '{default_style}' 不匹配",
                detail=(
                    f"探测样本 {nf.sample_size} 个：PascalCase={round(nf.pascal_ratio*100)}%, "
                    f"camelCase={round(nf.camel_ratio*100)}%, "
                    f"use 前缀={round(nf.use_prefix_ratio*100)}%, "
                    f"Service 后缀={round(nf.service_suffix_ratio*100)}%"
                ),
                choices=choices,
                default_choice="keep-default",
            ))

    @staticmethod
    def _suggest_style(nf: NamingFinding) -> Optional[str]:
        """根据探测比例反推一个最合身的命名风格。"""
        # 优先看强约束（带 use 前缀 / Service 后缀）
        if nf.use_prefix_ratio >= 0.5:
            return "camelCase-with-use-prefix"
        if nf.service_suffix_ratio >= 0.5:
            return "camelCase-with-Service-suffix"
        if nf.pascal_ratio >= 0.6:
            return "PascalCase"
        if nf.camel_ratio >= 0.6:
            return "camelCase"
        return None

    # ---- 中性提示（info_notes）------------------------------------------

    def _record_probe_info(self, plan: ProposedPlan) -> None:
        r = self.report
        if r.frameworks:
            plan.info_notes.append(f"探测到框架：{', '.join(r.frameworks)}")
        if r.package_managers:
            plan.info_notes.append(
                f"探测到包管理器：{', '.join(r.package_managers)}",
            )
        if r.has_eslint:
            plan.info_notes.append("检测到 ESLint 配置（与 harness 互不干扰）")
        if not r.gitignore_has_harness:
            plan.info_notes.append(
                "建议把 `.harness/.venv` 加入 .gitignore"
                "（context/ 与 generated/ 是否入库由项目自定）",
            )
        # forbidden_imports / rewrites 留空（Q5=A）
        plan.info_notes.append(
            "forbidden_imports / rewrites 默认留空；如需禁用旧路径，运行后手动编辑 rules.yaml",
        )

    # ---- 用户答复落盘 ----------------------------------------------------

    def _apply_one(
        self, rules: Dict[str, Any], conflict: Conflict, choice: str,
    ) -> None:
        if conflict.category == "unknown-dir":
            self._apply_unknown_dir(rules, conflict, choice)
        elif conflict.category == "naming":
            self._apply_naming(rules, conflict, choice)
        # alias / layer-path 当前是静默采纳，无 conflict 进这里

    @staticmethod
    def _apply_unknown_dir(
        rules: Dict[str, Any], conflict: Conflict, choice: str,
    ) -> None:
        # conflict.id = "unknown-dir:<name>"
        dir_name = conflict.id.split(":", 1)[1]
        dir_path = f"src/{dir_name}/"
        if choice == "skip":
            return
        if choice == "ignore":
            scanner = rules.setdefault("scanner", {})
            excludes = scanner.setdefault("exclude_dirs", [])
            if dir_name not in excludes and dir_path not in excludes:
                excludes.append(dir_name)
            return
        # choice == layer name
        arch = rules.setdefault("architecture", {})
        for layer_def in arch.get("layers") or []:
            if layer_def.get("name") == choice:
                paths = layer_def.setdefault("paths", [])
                if dir_path not in paths:
                    paths.append(dir_path)
                return

    @staticmethod
    def _apply_naming(
        rules: Dict[str, Any], conflict: Conflict, choice: str,
    ) -> None:
        layer = conflict.id.split(":", 1)[1]
        naming = rules.setdefault("naming", {})
        if choice == "keep-default":
            return
        if choice == "disable":
            naming.pop(layer, None)
            return
        if choice.startswith("adopt:"):
            naming[layer] = choice.split(":", 1)[1]


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------


def resolve_init(
    default_rules: Dict[str, Any], report: ProbeReport,
) -> ProposedPlan:
    return InitResolver(default_rules, report).resolve()
