#!/usr/bin/env python3
"""Harness 2.0 项目探测器 —— lib/probe.py

`harness init` 三段式（probe → resolve → apply）的第一段：纯只读地扫一遍
项目根，收集"现状"，给 init_resolver 拿去对照默认规则、生成冲突清单。

探测内容：
- tsconfig.json compilerOptions.paths      → 现有路径别名（例 "@/*" → "src/*"）
- package.json (dependencies/devDependencies/peerDependencies)
                                            → 框架猜测（react / vue / angular / svelte）
- src/ 顶层目录                              → 现有架构层猜测（components/pages/...）
- 命名样本                                   → 推测命名风格（PascalCase / camelCase 比例）
- .eslintrc / eslint.config.*                → 是否已有 lint
- .gitignore                                 → 是否已含 .harness/.venv / .harness/context

输出：ProbeReport（只读 dataclass），任何持久化都由 resolver/cli 负责。
失败默认值：一律是"无证据"（None / 空集合），让 resolver 安全回退到默认。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class AliasFinding:
    """tsconfig.json paths 里的一条别名映射。"""

    prefix: str            # "@/"
    target: str            # "src/"，已规整为相对项目根的 POSIX
    source_file: str       # 出自哪个配置文件（POSIX 相对）


@dataclass
class LayerFinding:
    """src/ 下识别到的疑似架构层目录。"""

    name: str              # component / hook / service / type / page / unknown
    path: str              # POSIX 相对（含末尾 "/"）
    file_count: int = 0
    sample_names: List[str] = field(default_factory=list)


@dataclass
class NamingFinding:
    """对某 layer 的命名风格统计。"""

    layer: str
    sample_size: int
    pascal_ratio: float    # 0.0 ~ 1.0
    camel_ratio: float
    use_prefix_ratio: float
    service_suffix_ratio: float


@dataclass
class ProbeReport:
    """探测报告（只读）。"""

    project_dir: str
    has_git: bool
    is_typescript: bool
    has_tsconfig: bool

    aliases: List[AliasFinding] = field(default_factory=list)
    frameworks: List[str] = field(default_factory=list)   # react / vue / ...
    package_managers: List[str] = field(default_factory=list)   # npm / pnpm / yarn / bun
    layers: List[LayerFinding] = field(default_factory=list)
    naming: List[NamingFinding] = field(default_factory=list)
    has_eslint: bool = False
    gitignore_has_harness: bool = False

    # 不能机械决策的提示，留给 resolver 给用户看
    notes: List[str] = field(default_factory=list)

    # 在 unknown 层目录内检测到的约定子目录名 → 推荐层名。
    # 例：{"components": "component", "hooks": "hook", "utils": "util"}
    # 供 Agent 起草 rules.yaml 时自动写入 architecture.sub_layer_convention。
    sub_layer_convention_hint: Dict[str, str] = field(default_factory=dict)


def probe_report_to_dict(report: ProbeReport) -> Dict:
    """把 ProbeReport 转成 CLI JSON 友好的 dict。"""
    return asdict(report)


# ---------------------------------------------------------------------------
# 探测器
# ---------------------------------------------------------------------------


class ProjectProbe:
    """项目探测器（只读）。

    用法::

        report = ProjectProbe(project_dir).run()
    """

    # 默认按子目录名猜 layer 的映射；resolver 会把它和 rules.yaml 默认值做 diff
    _DIR_LAYER_HINTS: Dict[str, str] = {
        "components": "component",
        "pages": "page",
        "views": "page",
        "hooks": "hook",
        "composables": "hook",
        "api": "service",
        "apis": "service",
        "service": "service",
        "services": "service",
        "types": "type",
        "models": "type",
        "interfaces": "type",
        "utils": "util",
        "util": "util",
        "utilities": "util",
        "helpers": "util",
        "decorators": "util",
    }

    # init 探测阶段按运行时代码文件统计；.d.ts 不参与命名采样。
    _SOURCE_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx")

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = Path(project_dir).resolve()

    def run(self) -> ProbeReport:
        report = ProbeReport(
            project_dir=str(self.project_dir),
            has_git=(self.project_dir / ".git").exists(),
            is_typescript=False,
            has_tsconfig=False,
        )
        self._probe_tsconfig(report)
        self._probe_package_json(report)
        self._probe_src_layers(report)
        self._probe_naming(report)
        self._probe_eslint(report)
        self._probe_gitignore(report)
        return report

    # -- tsconfig ----------------------------------------------------------

    def _probe_tsconfig(self, report: ProbeReport) -> None:
        candidates = [
            self.project_dir / "tsconfig.json",
            self.project_dir / "tsconfig.base.json",
            self.project_dir / "tsconfig.app.json",
        ]
        for f in candidates:
            if not f.exists():
                continue
            report.has_tsconfig = True
            report.is_typescript = True
            data = _read_json_with_comments(f)
            if not data:
                report.notes.append(f"读取 {f.name} 失败（JSON 解析异常），跳过 paths 探测")
                continue
            paths = (
                (data.get("compilerOptions") or {}).get("paths") or {}
            )
            base_url = (data.get("compilerOptions") or {}).get("baseUrl") or "."
            for alias, targets in paths.items():
                if not isinstance(targets, list) or not targets:
                    continue
                target = targets[0]
                if not isinstance(target, str):
                    continue
                prefix = alias.rstrip("*").rstrip("/")
                if not prefix:
                    continue
                resolved = self._normalize_alias_target(
                    base_url, target.rstrip("*").rstrip("/"),
                )
                if not resolved:
                    continue
                report.aliases.append(AliasFinding(
                    prefix=prefix + "/",
                    target=resolved.rstrip("/") + "/",
                    source_file=f.name,
                ))

    def _normalize_alias_target(self, base_url: str, target: str) -> Optional[str]:
        """把 tsconfig paths 的 target 规整为相对项目根的 POSIX 路径。"""
        if not target:
            return None
        base = (self.project_dir / base_url).resolve()
        resolved = (base / target).resolve()
        try:
            return resolved.relative_to(self.project_dir).as_posix()
        except ValueError:
            return None

    # -- package.json -------------------------------------------------------

    def _probe_package_json(self, report: ProbeReport) -> None:
        f = self.project_dir / "package.json"
        if not f.exists():
            return
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            report.notes.append("读取 package.json 失败，跳过框架/PM 探测")
            return

        deps: Dict[str, str] = {}
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            d = data.get(key) or {}
            if isinstance(d, dict):
                deps.update({k: str(v) for k, v in d.items()})

        # 框架命中（顺序无关，依赖名匹配即可）
        framework_signals = {
            "react": ("react",),
            "vue": ("vue",),
            "svelte": ("svelte",),
            "angular": ("@angular/core",),
            "solid": ("solid-js",),
            "preact": ("preact",),
            "qwik": ("@builder.io/qwik",),
        }
        for name, signals in framework_signals.items():
            if any(sig in deps for sig in signals):
                report.frameworks.append(name)

        if "typescript" in deps and not report.is_typescript:
            report.is_typescript = True

        # 包管理器（lockfile 优先；没有就看 packageManager 字段）
        lockfiles = [
            ("pnpm-lock.yaml", "pnpm"),
            ("yarn.lock", "yarn"),
            ("bun.lockb", "bun"),
            ("package-lock.json", "npm"),
        ]
        for fname, pm in lockfiles:
            if (self.project_dir / fname).exists():
                report.package_managers.append(pm)
        pm_field = data.get("packageManager")
        if isinstance(pm_field, str):
            head = pm_field.split("@", 1)[0]
            if head and head not in report.package_managers:
                report.package_managers.append(head)

    # -- src/ 层级 ----------------------------------------------------------

    def _probe_src_layers(self, report: ProbeReport) -> None:
        src = self.project_dir / "src"
        if not src.is_dir():
            return

        # C1: 检测双栈布局 —— src/ 下若有且仅有一个子目录含有源文件
        # (其余子目录全为 0)，则该子目录更可能是真正的 source_root，
        # 记录为 source_root_hint 供 resolver 询问用户。
        first_level = [c for c in sorted(src.iterdir()) if c.is_dir()]
        non_empty = []
        for child in first_level:
            cnt = self._count_source_files(child)
            if cnt > 0:
                non_empty.append((child, cnt))
        # C1: 双栈检测 —— src/ 下有 2+ 个子目录且源文件只集中在其中 1 个
        # 仅在多目录情况下触发，避免单目录 scaffold 误下钻。
        scan_root = src
        path_prefix = "src"
        if len(first_level) >= 2 and len(non_empty) == 1:
            scan_root = non_empty[0][0]
            path_prefix = f"src/{scan_root.name}"
            report.notes.append(f"dual-stack-hint:{path_prefix}")
        elif len(first_level) >= 2:
            preferred_frontend = self._preferred_frontend_root(first_level)
            if preferred_frontend is not None:
                scan_root = preferred_frontend
                path_prefix = f"src/{scan_root.name}"
                report.notes.append(f"dual-stack-hint:{path_prefix}")

        # 按推断出的 source_root 扫一级子目录。
        for child in sorted(scan_root.iterdir()):
            if not child.is_dir():
                continue
            layer = self._DIR_LAYER_HINTS.get(child.name.lower())
            if not layer:
                # 不在常规命名表里 → 标 unknown，让 resolver 询问用户
                layer = "unknown"
            # file_count 用真实总数；sample 只采前 N 个供命名风格统计
            real_count = self._count_source_files(child)
            files = self._collect_source_files(child, limit=self._NAMING_SAMPLE_LIMIT)
            samples = [Path(f).stem for f in files]
            report.layers.append(LayerFinding(
                name=layer,
                path=f"{path_prefix}/{child.name}/",
                file_count=real_count,
                sample_names=samples[:10],
            ))
            # 对 unknown 层目录额外扫一层子目录，收集约定子目录名作为
            # sub_layer_convention hint，供 Agent 起草 rules.yaml 时参考。
            if layer == "unknown":
                self._probe_sub_layer_convention(child, report)

    # 探测命名时采样的最多文件数
    _NAMING_SAMPLE_LIMIT = 60

    def _probe_sub_layer_convention(self, unknown_dir: Path, report: ProbeReport) -> None:
        """在 unknown 层目录内递归搜索约定子目录名，更新 sub_layer_convention_hint。

        只看 _DIR_LAYER_HINTS 里已有的子目录名（components/hooks/utils 等），
        不进行额外推断，保持纯"观察事实"语义。
        """
        try:
            for child in unknown_dir.rglob("*"):
                if not child.is_dir():
                    continue
                name_lower = child.name.lower()
                layer = self._DIR_LAYER_HINTS.get(name_lower)
                if layer and name_lower not in report.sub_layer_convention_hint:
                    report.sub_layer_convention_hint[child.name] = layer
        except PermissionError:
            pass

    @classmethod
    def _preferred_frontend_root(cls, first_level: List[Path]) -> Optional[Path]:
        by_name = {p.name.lower(): p for p in first_level}
        frontend = by_name.get("frontend") or by_name.get("client") or by_name.get("web")
        backend = by_name.get("backend") or by_name.get("server")
        if frontend is None or backend is None:
            return None
        if cls._count_source_files(frontend) <= 0:
            return None
        return frontend

    @classmethod
    def _count_source_files(cls, folder: Path) -> int:
        return sum(
            1 for p in folder.rglob("*")
            if p.is_file() and p.suffix in cls._SOURCE_EXTENSIONS
        )

    @classmethod
    def _collect_source_files(cls, folder: Path, limit: int) -> List[str]:
        out: List[str] = []
        for p in folder.rglob("*"):
            if p.is_file() and p.suffix in cls._SOURCE_EXTENSIONS:
                out.append(p.as_posix())
                if len(out) >= limit:
                    break
        return out

    # -- 命名风格统计 ------------------------------------------------------

    _PASCAL_RE = re.compile(r"^[A-Z][A-Za-z0-9]*$")
    _CAMEL_RE = re.compile(r"^[a-z][A-Za-z0-9]*$")

    def _probe_naming(self, report: ProbeReport) -> None:
        for layer in report.layers:
            samples = layer.sample_names
            if not samples:
                continue
            n = len(samples)
            pascal = sum(1 for s in samples if self._PASCAL_RE.match(s))
            camel = sum(1 for s in samples if self._CAMEL_RE.match(s))
            use_pref = sum(
                1 for s in samples
                if self._CAMEL_RE.match(s) and s.startswith("use") and
                len(s) > 3 and s[3].isupper()
            )
            svc_suffix = sum(
                1 for s in samples
                if self._CAMEL_RE.match(s) and s.endswith("Service") and s != "Service"
            )
            report.naming.append(NamingFinding(
                layer=layer.name,
                sample_size=n,
                pascal_ratio=round(pascal / n, 3),
                camel_ratio=round(camel / n, 3),
                use_prefix_ratio=round(use_pref / n, 3),
                service_suffix_ratio=round(svc_suffix / n, 3),
            ))

    # -- eslint -------------------------------------------------------------

    def _probe_eslint(self, report: ProbeReport) -> None:
        for name in (
            ".eslintrc", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json",
            ".eslintrc.yml", ".eslintrc.yaml",
            "eslint.config.js", "eslint.config.mjs", "eslint.config.cjs",
        ):
            if (self.project_dir / name).exists():
                report.has_eslint = True
                return
        pkg = self.project_dir / "package.json"
        if pkg.exists():
            try:
                data = json.loads(pkg.read_text(encoding="utf-8"))
                if isinstance(data.get("eslintConfig"), dict):
                    report.has_eslint = True
            except Exception:
                pass

    # -- .gitignore ---------------------------------------------------------

    def _probe_gitignore(self, report: ProbeReport) -> None:
        f = self.project_dir / ".gitignore"
        if not f.exists():
            return
        try:
            lines = [
                ln.strip() for ln in f.read_text(encoding="utf-8").splitlines()
            ]
        except Exception:
            return
        targets = {".harness/.venv", ".harness/context", ".harness/generated"}
        report.gitignore_has_harness = any(t in lines for t in targets)


# ---------------------------------------------------------------------------
# JSON-with-comments 读取（tsconfig 允许 // 和 /* */）
# ---------------------------------------------------------------------------


_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def _strip_jsonc_comments(raw: str) -> str:
    """剥离 // 和 /* */ 注释，但保留字符串字面量内的相同字符。

    用纯状态机扫一遍：跟踪是否在字符串内、是否处于反斜杠转义中，
    确保 "@/*" 这种合法 JSON 字符串里的 /* 不会被误判为注释起点。
    """
    out: List[str] = []
    i = 0
    n = len(raw)
    in_string = False
    escape = False
    while i < n:
        ch = raw[i]
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        # 不在字符串里：注释 / 字符串起点判断
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n:
            nxt = raw[i + 1]
            if nxt == "/":
                # 行注释：吃到换行（但保留换行符以维持行号）
                j = raw.find("\n", i + 2)
                if j == -1:
                    return "".join(out)
                i = j
                continue
            if nxt == "*":
                # 块注释：吃到 */
                j = raw.find("*/", i + 2)
                if j == -1:
                    return "".join(out)
                i = j + 2
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _read_json_with_comments(path: Path) -> Optional[Dict]:
    """读 tsconfig.json：容忍 // / /* */ 注释和尾随逗号。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    cleaned = _strip_jsonc_comments(raw)
    cleaned = _TRAILING_COMMA_RE.sub(r"\1", cleaned)
    try:
        data = json.loads(cleaned)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------


def probe_project(project_dir: Path) -> ProbeReport:
    return ProjectProbe(project_dir).run()
