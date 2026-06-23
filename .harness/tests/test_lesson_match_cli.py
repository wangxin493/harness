"""harness lesson match CLI 测试。

测三件事：
1) 命令行参数解析（--file / --content / --content-file / --stdin 互斥）
2) 输出格式：markdown / plain / --json
3) 与 ExperienceMarket.match_lessons 的集成（真路径 + 真关键词命中）
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from click.testing import CliRunner

from . import _setup  # noqa: F401

from lib import cli as cli_module  # noqa: E402
from lib.experience_market import ExperienceMarket  # noqa: E402


class LessonMatchFixture:
    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="harness-match-cli-")).resolve()
        (self.root / ".harness").mkdir()
        self.market = ExperienceMarket(harness_dir=self.root / ".harness")

    def add(self, lid, title, **kwargs):
        return self.market.add_lesson(title=title, lesson_id=lid, **kwargs)

    def invoke(self, *args, stdin=None):
        runner = CliRunner()
        return runner.invoke(
            cli_module.cli,
            list(args),
            input=stdin,
            env={"HARNESS_PROJECT_DIR": str(self.root)},
        )

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


class TestLessonMatchCli(unittest.TestCase):
    def setUp(self):
        self.fx = LessonMatchFixture()
        self.fx.add("api", "API 经验",
                    content="API 层不要直接调 fetch。",
                    applies_to=["src/api/"], keywords=["fetch"])
        self.fx.add("comp", "组件经验",
                    content="组件命名 PascalCase。",
                    applies_to=["src/components/"], keywords=[])

    def tearDown(self):
        self.fx.cleanup()

    def test_file_path_match_markdown(self):
        result = self.fx.invoke("lesson", "match", "--file", "src/api/x.ts")
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("命中", result.output)
        self.assertIn("API 经验", result.output)
        self.assertNotIn("组件经验", result.output)

    def test_no_match_outputs_empty_marker(self):
        result = self.fx.invoke("lesson", "match", "--file", "src/unknown/x.ts")
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("无命中", result.output)

    def test_plain_format_for_hook_injection(self):
        result = self.fx.invoke(
            "lesson", "match", "--file", "src/api/x.ts", "--format", "plain",
        )
        self.assertEqual(result.exit_code, 0, msg=result.output)
        # plain 格式：开头是 "- [severity] title"
        self.assertRegex(result.output, r"- \[\w+\] API 经验")
        # 没有 markdown 装饰
        self.assertNotIn("🎯", result.output)

    def test_plain_format_no_match_silent(self):
        # plain + 无命中 → 无输出（hook 注入用，不污染上下文）
        result = self.fx.invoke(
            "lesson", "match", "--file", "src/unknown/x.ts", "--format", "plain",
        )
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(result.output.strip(), "")

    def test_json_output(self):
        result = self.fx.invoke(
            "lesson", "match", "--file", "src/api/x.ts", "--json",
        )
        self.assertEqual(result.exit_code, 0, msg=result.output)
        data = json.loads(result.output)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], "api")
        # 完整字段
        self.assertIn("title", data[0])
        self.assertIn("content", data[0])
        self.assertIn("applies_to", data[0])

    def test_content_via_flag(self):
        # content 命中关键词（即便 file_path 不匹配 applies_to）
        result = self.fx.invoke(
            "lesson", "match",
            "--file", "src/components/Foo.tsx",
            "--content", "const r = await fetch('/x');",
            "--json",
        )
        data = json.loads(result.output)
        ids = [d["id"] for d in data]
        # 关键词 fetch 命中 api 这条
        self.assertIn("api", ids)
        # 路径命中 comp 这条
        self.assertIn("comp", ids)

    def test_stdin_payload(self):
        payload = json.dumps({
            "file_path": "src/api/x.ts",
            "content": "fetch users",
        })
        result = self.fx.invoke(
            "lesson", "match", "--stdin", "--json",
            stdin=payload,
        )
        self.assertEqual(result.exit_code, 0, msg=result.output)
        data = json.loads(result.output)
        self.assertEqual(data[0]["id"], "api")

    def test_stdin_invalid_json_errors(self):
        result = self.fx.invoke(
            "lesson", "match", "--stdin",
            stdin="not json",
        )
        self.assertEqual(result.exit_code, 2, msg=result.output)
        self.assertIn("stdin", result.output)

    def test_mutually_exclusive_sources(self):
        result = self.fx.invoke(
            "lesson", "match",
            "--content", "x", "--stdin",
            stdin="{}",
        )
        self.assertEqual(result.exit_code, 2, msg=result.output)
        self.assertIn("三选一", result.output)

    def test_limit_caps_results(self):
        # 多写几条相关的
        for i in range(5):
            self.fx.add(f"k{i}", f"L{i}", content="b",
                        applies_to=["src/api/"])
        result = self.fx.invoke(
            "lesson", "match", "--file", "src/api/x.ts",
            "--limit", "2", "--json",
        )
        data = json.loads(result.output)
        self.assertEqual(len(data), 2)


if __name__ == "__main__":
    unittest.main()
