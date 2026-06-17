"""experience_market 单元测试。"""

import shutil
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from . import _setup  # noqa: F401

from lib.experience_market import (  # noqa: E402
    ExperienceMarket,
    Lesson,
)


class MarketFixture:
    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="harness-market-")).resolve()
        self.harness_dir = self.root / ".harness"
        self.harness_dir.mkdir()

    def market(self) -> ExperienceMarket:
        return ExperienceMarket(harness_dir=self.harness_dir)

    def write_shared_md(self, lesson_id: str, body: str, **frontmatter) -> Path:
        """直接往 .harness-shared/lessons/ 写一个 md 文件，用于测 sync。"""
        shared_dir = self.root / ".harness-shared" / "lessons"
        shared_dir.mkdir(parents=True, exist_ok=True)
        path = shared_dir / f"{lesson_id}.md"
        # 简单 frontmatter 拼装
        fm_lines = [f"id: {lesson_id}"]
        for k, v in frontmatter.items():
            if isinstance(v, list):
                fm_lines.append(f"{k}: [{', '.join(v)}]")
            else:
                fm_lines.append(f"{k}: {v}")
        path.write_text(
            "---\n" + "\n".join(fm_lines) + "\n---\n\n" + body + "\n",
            encoding="utf-8",
        )
        return path

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestCrud(unittest.TestCase):
    def setUp(self):
        self.fx = MarketFixture()

    def tearDown(self):
        self.fx.cleanup()

    def test_add_then_list_and_get(self):
        m = self.fx.market()
        lesson = m.add_lesson(
            title="禁止 @/services",
            content="历史路径，已迁移到 @/api。",
            category="import",
            severity="error",
            keywords=["forbidden", "services"],
            applies_to=["src/api/"],
            lesson_id="t001",
            author="alice",
        )
        self.assertEqual(lesson.id, "t001")
        # 文件落盘
        target = self.fx.harness_dir / "memory" / "lessons" / "t001.md"
        self.assertTrue(target.exists())
        # 列表能读回
        lessons = m.list_lessons()
        self.assertEqual(len(lessons), 1)
        self.assertEqual(lessons[0].id, "t001")
        self.assertEqual(lessons[0].title, "禁止 @/services")
        self.assertEqual(lessons[0].keywords, ["forbidden", "services"])
        self.assertEqual(lessons[0].applies_to, ["src/api/"])
        # get_lesson
        same = m.get_lesson("t001")
        self.assertIsNotNone(same)
        self.assertEqual(same.severity, "error")

    def test_add_auto_id(self):
        m = self.fx.market()
        lesson = m.add_lesson(title="t", content="c")
        self.assertEqual(len(lesson.id), 8)

    def test_add_duplicate_without_overwrite_raises(self):
        m = self.fx.market()
        m.add_lesson(title="a", content="b", lesson_id="dup")
        with self.assertRaises(FileExistsError):
            m.add_lesson(title="x", content="y", lesson_id="dup")

    def test_add_overwrite_replaces(self):
        m = self.fx.market()
        m.add_lesson(title="a", content="b", lesson_id="dup")
        time.sleep(0.01)  # 确保 created_at 字符串递增
        new = m.add_lesson(title="x", content="y", lesson_id="dup", overwrite=True)
        self.assertEqual(new.title, "x")
        loaded = m.get_lesson("dup")
        self.assertEqual(loaded.content, "y")

    def test_remove(self):
        m = self.fx.market()
        m.add_lesson(title="a", content="b", lesson_id="t1")
        self.assertTrue(m.remove_lesson("t1"))
        self.assertFalse(m.remove_lesson("t1"))  # 二次删 → False
        self.assertEqual(m.list_lessons(), [])

    def test_list_skips_corrupt_files(self):
        m = self.fx.market()
        m.add_lesson(title="ok", content="b", lesson_id="good")
        # 写一个坏文件
        bad = self.fx.harness_dir / "memory" / "lessons" / "bad.md"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("no frontmatter here", encoding="utf-8")
        # 写一个 frontmatter 损坏的
        bad2 = self.fx.harness_dir / "memory" / "lessons" / "bad2.md"
        bad2.write_text("---\n: : :\n---\nbody\n", encoding="utf-8")

        lessons = m.list_lessons()
        # 只有 good 被读出
        self.assertEqual([l.id for l in lessons], ["good"])

    def test_special_chars_in_title_roundtrip(self):
        m = self.fx.market()
        m.add_lesson(
            title='含: 冒号 与 "引号" 的标题',
            content="正文",
            lesson_id="sp",
        )
        loaded = m.get_lesson("sp")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.title, '含: 冒号 与 "引号" 的标题')


# ---------------------------------------------------------------------------
# matches / get_relevant_lessons
# ---------------------------------------------------------------------------


class TestRelevance(unittest.TestCase):
    def setUp(self):
        self.fx = MarketFixture()

    def tearDown(self):
        self.fx.cleanup()

    def test_matches_keyword_and_applies_to(self):
        l = Lesson(
            id="x",
            title="t",
            content="c",
            keywords=["forbidden", "services"],
            applies_to=["src/api/"],
            created_at="2026-01-01T00:00:00",
        )
        # 仅 keyword 命中两个 → 0.4
        self.assertAlmostEqual(
            l.matches({"task_description": "use forbidden services"}),
            0.4, places=3,
        )
        # keyword 命中一个 + applies_to 命中 → 0.2 + 0.3 = 0.5
        self.assertAlmostEqual(
            l.matches({
                "task_description": "use services",
                "file_path": "src/api/foo.ts",
            }),
            0.5, places=3,
        )
        # 全部命中（2 keywords + applies_to）→ 0.7
        self.assertAlmostEqual(
            l.matches({
                "task_description": "forbidden services here",
                "file_path": "src/api/foo.ts",
            }),
            0.7, places=3,
        )

    def test_matches_capped_at_one(self):
        l = Lesson(
            id="x", title="t", content="c",
            keywords=["a", "b", "c", "d", "e", "f"],  # 6 个 → 1.2
            created_at="2026-01-01T00:00:00",
        )
        score = l.matches({"task_description": "a b c d e f"})
        self.assertEqual(score, 1.0)

    def test_get_relevant_lessons_filters_and_ranks(self):
        m = self.fx.market()
        m.add_lesson(
            title="禁 services",
            content="...",
            lesson_id="hi",
            keywords=["forbidden", "services"],
            applies_to=["src/api/"],
        )
        m.add_lesson(
            title="无关",
            content="...",
            lesson_id="lo",
            keywords=["foo"],
        )

        ctx = {
            "task_description": "use forbidden services here",
            "file_path": "src/api/itemService.ts",
        }
        out = m.get_relevant_lessons(ctx, limit=5, min_score=0.3)
        # hi 应该排在前面，lo 因低于 min_score 被过滤
        self.assertEqual([x["id"] for x in out], ["hi"])
        self.assertGreater(out[0]["score"], 0.5)

    def test_expired_lessons_excluded(self):
        m = self.fx.market()
        m.add_lesson(
            title="过期", content="...", lesson_id="exp",
            keywords=["match"],
            expires_at="2020-01-01T00:00:00",
        )
        m.add_lesson(
            title="有效", content="...", lesson_id="ok",
            keywords=["match"],
            expires_at="2099-01-01T00:00:00",
        )
        out = m.get_relevant_lessons(
            {"task_description": "match"},
            limit=5, min_score=0.1,
        )
        self.assertEqual([x["id"] for x in out], ["ok"])


# ---------------------------------------------------------------------------
# sync (latest-wins)
# ---------------------------------------------------------------------------


class TestSync(unittest.TestCase):
    def setUp(self):
        self.fx = MarketFixture()

    def tearDown(self):
        self.fx.cleanup()

    def test_sync_pulls_new_lessons(self):
        self.fx.write_shared_md(
            "s1",
            "正文",
            title="共享1",
            category="arch",
            severity="error",
            keywords=["a", "b"],
            author="bob",
            created_at="2026-06-01T10:00:00",
        )
        m = self.fx.market()
        result = m.sync()
        self.assertEqual(result.pulled, ["s1"])
        self.assertEqual(result.overwritten, [])
        # 本地能读到
        loaded = m.get_lesson("s1")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.title, "共享1")

    def test_sync_latest_wins_remote_newer(self):
        m = self.fx.market()
        # 本地先存一份旧的
        m.add_lesson(
            title="本地旧", content="old", lesson_id="L1",
        )
        # 篡改本地 created_at 让它"看起来旧"
        local_md = self.fx.harness_dir / "memory" / "lessons" / "L1.md"
        text = local_md.read_text(encoding="utf-8").replace(
            local_md.read_text(encoding="utf-8").split("created_at: ", 1)[1].split("\n", 1)[0],
            "2024-01-01T00:00:00",
        )
        local_md.write_text(text, encoding="utf-8")

        # 共享盘有更新版
        self.fx.write_shared_md(
            "L1",
            "新版本正文",
            title="共享新", category="general", severity="warning",
            keywords=[], author="bob",
            created_at="2026-06-01T10:00:00",
        )
        result = m.sync()
        self.assertIn("L1", result.overwritten)
        loaded = m.get_lesson("L1")
        self.assertEqual(loaded.title, "共享新")

    def test_sync_latest_wins_local_newer(self):
        m = self.fx.market()
        # 本地是最新
        m.add_lesson(title="本地新", content="new", lesson_id="L2")
        # 共享盘是旧的
        self.fx.write_shared_md(
            "L2",
            "旧正文",
            title="共享旧", category="general", severity="warning",
            keywords=[], author="bob",
            created_at="2024-01-01T00:00:00",
        )
        result = m.sync()
        self.assertEqual(result.pulled, [])
        self.assertEqual(result.overwritten, [])
        self.assertIn("L2", result.kept_local)
        # 本地内容未变
        self.assertEqual(m.get_lesson("L2").title, "本地新")

    def test_sync_skips_corrupt_shared_files(self):
        # 共享盘里写一个坏文件
        shared = self.fx.root / ".harness-shared" / "lessons"
        shared.mkdir(parents=True, exist_ok=True)
        (shared / "bad.md").write_text("garbage", encoding="utf-8")

        m = self.fx.market()
        result = m.sync()
        self.assertIn("bad.md", result.skipped)

    def test_sync_no_shared_dir_returns_empty(self):
        m = self.fx.market()
        result = m.sync()
        self.assertEqual(result.pulled, [])
        self.assertEqual(result.overwritten, [])
        self.assertEqual(result.kept_local, [])

    def test_sync_remote_flag_is_p2_placeholder(self):
        m = self.fx.market()
        result = m.sync(remote=True)
        self.assertTrue(result.remote_skipped)


if __name__ == "__main__":
    unittest.main()
