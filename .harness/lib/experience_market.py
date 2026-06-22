#!/usr/bin/env python3
"""Harness 2.0 经验市场（P1 #8）—— lib/experience_market.py

P1 范围（与执行清单一致）：
- 本地存储：.harness/memory/lessons/<id>.md（Markdown + YAML frontmatter，沿用设计文档 §4.2）
- 共享目录：<project>/.harness-shared/lessons/<id>.md（团队 git 共享盘）
  注：设计文档 §三 原写的是 .harness/memory/shared/，落地时迁到顶层 .harness-shared/
  方便挂载 git submodule / NFS；二者只能存在其一，本实现一律读 .harness-shared/。
- 冲突策略：lesson id 相同 → latest-wins（按 created_at 取最新一条）
- P1 仅做本地 + 共享盘 sync，远程 git pull/push 留 P2（CLI 用 --remote 占位）
- prompt 注入由 adapter.Generator 在 scan 时渲染 generated/*.md 完成（静态注入）

设计原则：
- ExperienceMarket 是纯数据层（CRUD + 检索 + sync），不感知 mode / 不打日志副作用
- 解析失败的 lesson 文件 → 跳过（不抛），保证 list 在脏盘下也可用
- created_at 作为字符串按字典序比较即可（统一 ISO 8601，前缀年月日时分秒已严格递增）
"""

from __future__ import annotations

import datetime as _dt
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class Lesson:
    """单条经验。"""

    id: str
    title: str
    content: str
    category: str = "general"
    severity: str = "warning"
    keywords: List[str] = field(default_factory=list)
    author: str = "unknown"
    created_at: str = ""              # ISO 8601
    expires_at: Optional[str] = None  # ISO 8601 或 None
    applies_to: List[str] = field(default_factory=list)  # 文件路径模式（子串匹配）

    # -- 上下文匹配评分 ----------------------------------------------------

    def matches(self, context: Dict[str, Any]) -> float:
        """计算与上下文的匹配度（0.0 ~ 1.0）。

        - keyword 命中 task_description / file_path：每个 +0.2
        - applies_to 命中 file_path：+0.3（只算一次，命中即停）
        """
        score = 0.0
        haystack = (
            context.get("task_description", "")
            + " "
            + context.get("file_path", "")
        ).lower()
        for kw in self.keywords:
            if kw and kw.lower() in haystack:
                score += 0.2

        if self.applies_to:
            file_path = context.get("file_path", "")
            for pattern in self.applies_to:
                if pattern and pattern in file_path:
                    score += 0.3
                    break

        return min(score, 1.0)


@dataclass
class SyncResult:
    """sync 调用的统计。"""

    pulled: List[str] = field(default_factory=list)        # 新拉到本地的 id
    overwritten: List[str] = field(default_factory=list)   # latest-wins 覆盖的本地 id
    kept_local: List[str] = field(default_factory=list)    # 本地更新，保持不动
    skipped: List[str] = field(default_factory=list)       # 解析失败被跳过的文件名
    remote_skipped: bool = False                            # --remote 占位（P2）


# ---------------------------------------------------------------------------
# 经验市场
# ---------------------------------------------------------------------------


class ExperienceMarket:
    """本地 + 共享盘两层存储的经验库。"""

    DEFAULT_MIN_SCORE = 0.3

    def __init__(
        self,
        harness_dir: Path,
        shared_dir: Optional[Path] = None,
    ) -> None:
        self.harness_dir = Path(harness_dir).resolve()
        # local: .harness/memory/lessons/
        self.local_dir = self.harness_dir / "memory" / "lessons"
        # shared: <project>/.harness-shared/lessons/（与 .harness/ 同级）
        if shared_dir is not None:
            self.shared_dir = Path(shared_dir).resolve()
        else:
            self.shared_dir = self.harness_dir.parent / ".harness-shared" / "lessons"

    # -- 公共 API：CRUD ------------------------------------------------------

    def list_lessons(self) -> List[Lesson]:
        """列出本地所有 lesson（解析失败的跳过）。"""
        lessons: List[Lesson] = []
        if not self.local_dir.exists():
            return lessons
        for md_file in sorted(self.local_dir.glob("*.md")):
            lesson = self._parse_lesson_file(md_file)
            if lesson is not None:
                lessons.append(lesson)
        return lessons

    def get_lesson(self, lesson_id: str) -> Optional[Lesson]:
        path = self.local_dir / f"{lesson_id}.md"
        if not path.exists():
            return None
        return self._parse_lesson_file(path)

    def add_lesson(
        self,
        title: str,
        content: str,
        category: str = "general",
        severity: str = "warning",
        keywords: Optional[List[str]] = None,
        applies_to: Optional[List[str]] = None,
        lesson_id: Optional[str] = None,
        author: Optional[str] = None,
        expires_at: Optional[str] = None,
        overwrite: bool = False,
    ) -> Lesson:
        """新增 lesson 到本地。

        lesson_id 为 None → 自动生成 UUID4 前 8 位。
        若指定 id 已存在且 overwrite=False → 抛 FileExistsError。
        """
        lid = lesson_id or uuid.uuid4().hex[:8]
        target = self.local_dir / f"{lid}.md"
        if target.exists() and not overwrite:
            raise FileExistsError(
                f"lesson {lid} 已存在 ({target})；指定 --id 时若需覆盖请先 remove"
            )

        lesson = Lesson(
            id=lid,
            title=title,
            content=content,
            category=category,
            severity=severity,
            keywords=list(keywords or []),
            author=author or self._detect_author(),
            created_at=_now_iso(),
            expires_at=expires_at,
            applies_to=list(applies_to or []),
        )
        self._write_lesson(target, lesson)
        return lesson

    def remove_lesson(self, lesson_id: str) -> bool:
        path = self.local_dir / f"{lesson_id}.md"
        if not path.exists():
            return False
        path.unlink()
        return True

    # -- 公共 API：sync ------------------------------------------------------

    def sync(self, remote: bool = False) -> SyncResult:
        """从 shared_dir 同步到 local_dir。

        - 本地不存在该 id → 拷贝
        - 本地存在：按 created_at 比较，较新者保留（latest-wins）
        - remote=True 在 P1 仅占位；P2 才走 git pull/push
        """
        result = SyncResult()
        if remote:
            result.remote_skipped = True  # P2 才实现

        if not self.shared_dir.exists():
            return result

        self.local_dir.mkdir(parents=True, exist_ok=True)

        for shared_md in sorted(self.shared_dir.glob("*.md")):
            shared_lesson = self._parse_lesson_file(shared_md)
            if shared_lesson is None:
                result.skipped.append(shared_md.name)
                continue

            local_md = self.local_dir / f"{shared_lesson.id}.md"
            if not local_md.exists():
                shutil.copyfile(shared_md, local_md)
                result.pulled.append(shared_lesson.id)
                continue

            local_lesson = self._parse_lesson_file(local_md)
            if local_lesson is None:
                # 本地损坏 → 用 shared 覆盖
                shutil.copyfile(shared_md, local_md)
                result.overwritten.append(shared_lesson.id)
                continue

            if self._is_newer(shared_lesson.created_at, local_lesson.created_at):
                shutil.copyfile(shared_md, local_md)
                result.overwritten.append(shared_lesson.id)
            else:
                result.kept_local.append(shared_lesson.id)

        return result

    # -- 公共 API：检索 ------------------------------------------------------

    def get_relevant_lessons(
        self,
        context: Dict[str, Any],
        limit: int = 5,
        min_score: float = DEFAULT_MIN_SCORE,
    ) -> List[Dict[str, Any]]:
        """按 context 召回相关 lesson，按 score 降序。

        返回 dict 列表（含 score），便于直接 JSON 序列化给 prompt 注入器使用。
        """
        scored: List[tuple] = []
        for lesson in self.list_lessons():
            if self._is_expired(lesson):
                continue
            score = lesson.matches(context)
            if score >= min_score:
                scored.append((score, lesson))
        scored.sort(key=lambda x: x[0], reverse=True)

        return [
            {
                "score": round(score, 3),
                **{k: v for k, v in asdict(lesson).items() if k != "content"},
                # 内容截断 500 字符，避免 prompt 超长（Injector 自己再二次截断）
                "content": (lesson.content[:500] + "…") if len(lesson.content) > 500
                            else lesson.content,
            }
            for score, lesson in scored[:limit]
        ]

    # -- 内部：解析 / 序列化 -------------------------------------------------

    def _parse_lesson_file(self, path: Path) -> Optional[Lesson]:
        """读取 Markdown + YAML frontmatter。失败返回 None（不抛）。"""
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        if not text.startswith("---"):
            return None

        parts = text.split("---", 2)
        if len(parts) < 3:
            return None

        try:
            import yaml
            meta = yaml.safe_load(parts[1]) or {}
        except Exception:
            return None
        if not isinstance(meta, dict):
            return None

        body = parts[2].lstrip("\n").rstrip()

        try:
            return Lesson(
                id=str(meta.get("id") or path.stem),
                title=str(meta.get("title") or ""),
                content=body,
                category=str(meta.get("category") or "general"),
                severity=str(meta.get("severity") or "warning"),
                keywords=_as_str_list(meta.get("keywords")),
                author=str(meta.get("author") or "unknown"),
                created_at=str(meta.get("created_at") or ""),
                expires_at=_optional_str(meta.get("expires_at")),
                applies_to=_as_str_list(meta.get("applies_to")),
            )
        except Exception:
            return None

    def _write_lesson(self, path: Path, lesson: Lesson) -> None:
        """写 Markdown + frontmatter。手写 YAML 而不是 yaml.dump，因为
        - 字段顺序固定，diff 友好
        - 不引入 yaml.dump 的字符串引号风格不确定性
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "---",
            f"id: {lesson.id}",
            f"title: {_yaml_quote(lesson.title)}",
            f"category: {lesson.category}",
            f"severity: {lesson.severity}",
            f"keywords: {_yaml_list(lesson.keywords)}",
            f"author: {_yaml_quote(lesson.author)}",
            f"created_at: {lesson.created_at}",
            f"expires_at: {lesson.expires_at if lesson.expires_at else 'null'}",
            f"applies_to: {_yaml_list(lesson.applies_to)}",
            "---",
            "",
            lesson.content.rstrip(),
            "",
        ]
        path.write_text("\n".join(lines), encoding="utf-8")

    # -- 内部：杂项 ----------------------------------------------------------

    @staticmethod
    def _is_newer(a: str, b: str) -> bool:
        """ISO 8601 字符串字典序比较；空串视为最旧。"""
        if not a:
            return False
        if not b:
            return True
        return a > b

    @staticmethod
    def _is_expired(lesson: Lesson) -> bool:
        if not lesson.expires_at:
            return False
        try:
            exp = _dt.datetime.fromisoformat(lesson.expires_at)
        except ValueError:
            return False
        return exp < _dt.datetime.now()

    @staticmethod
    def _detect_author() -> str:
        try:
            r = subprocess.run(
                ["git", "config", "user.name"],
                capture_output=True, text=True, check=False,
            )
            name = (r.stdout or "").strip()
            return name or "unknown"
        except FileNotFoundError:
            return "unknown"


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _as_str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        # 允许 frontmatter 里写成 "k1, k2"
        return [s.strip() for s in value.split(",") if s.strip()]
    if isinstance(value, list):
        return [str(v) for v in value]
    return []


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in ("null", "none", "never"):
        return None
    return s


def _yaml_quote(value: str) -> str:
    """如果包含特殊字符就加双引号；纯 ASCII 简单字符直接裸写。"""
    if value == "":
        return '""'
    if any(c in value for c in ":#\"'\n[]{},&*!|>%@`"):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _yaml_list(items: List[str]) -> str:
    if not items:
        return "[]"
    return "[" + ", ".join(_yaml_quote(s) for s in items) + "]"
