"""inject-lessons.sh PostToolUse hook 测试。

测脚本协议：
- 输入 tool_input JSON（stdin）→ 调 `harness lesson match --stdin --format plain --limit 5`
- 命中 → stdout 一行 JSON，含 hookSpecificOutput.additionalContext
- 未命中 / 非 src/ / 非 ts → 静默 exit 0
- 治理模式 off / harness 未装 → 静默 exit 0

由于真跑 harness CLI 比较慢，这里直接走 fixture 起一个最小项目，让 hook
脚本能正常 fork 出 Python 子进程。
"""

import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from . import _setup  # noqa: F401

from lib.experience_market import ExperienceMarket  # noqa: E402


HARNESS_REPO = Path(__file__).resolve().parents[1]
HOOK_SCRIPT = HARNESS_REPO / "hooks" / "inject-lessons.sh"


class InjectHookFixture:
    """搭一个最小项目：.harness/commands/harness + 几条 lesson。"""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="harness-inject-")).resolve()
        # 把整个 .harness 真目录链过来，复用 commands/harness、lib、.venv
        target_harness = self.root / ".harness"
        target_harness.mkdir()
        # 拷贝必要子项（commands / lib / .venv 用 symlink 节省时间）
        for name in ("commands", "lib", ".venv", "VERSION"):
            src = HARNESS_REPO / name
            dst = target_harness / name
            if src.exists():
                if src.is_dir():
                    dst.symlink_to(src)
                else:
                    shutil.copy(src, dst)
        # rules.yaml 必须真存在（doctor/validator 都看它）
        (target_harness / "rules.yaml").write_text(
            "architecture:\n  layers: []\n", encoding="utf-8")
        # 把 lesson 写到独立目录（避免和真项目共用）
        self.market = ExperienceMarket(harness_dir=target_harness)
        # src 目录占位
        (self.root / "src" / "api").mkdir(parents=True, exist_ok=True)

    def add_lesson(self, lid, title, **kwargs):
        return self.market.add_lesson(title=title, lesson_id=lid, **kwargs)

    def run_hook(self, tool_input: dict) -> subprocess.CompletedProcess:
        payload = {"tool_input": tool_input}
        env = os.environ.copy()
        env["CLAUDE_PROJECT_DIR"] = str(self.root)
        env.pop("HARNESS_PROJECT_DIR", None)  # 让 hook 自己决定
        return subprocess.run(
            ["bash", str(HOOK_SCRIPT)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=env,
            timeout=30,
        )

    def set_mode(self, mode: str) -> None:
        (self.root / ".harness" / "mode-config.json").write_text(
            json.dumps({"mode": mode, "updated_at": "2026-06-23T00:00:00"}),
            encoding="utf-8",
        )

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


class TestInjectLessonsHook(unittest.TestCase):
    def setUp(self):
        if not HOOK_SCRIPT.exists():
            self.skipTest(f"hook script missing: {HOOK_SCRIPT}")
        self.fx = InjectHookFixture()
        self.fx.add_lesson(
            "api",
            title="API 经验",
            content="API 层不要直接调 fetch；走 service 封装。",
            applies_to=["src/api/"],
            keywords=["fetch"],
        )

    def tearDown(self):
        self.fx.cleanup()

    def test_path_match_injects_context(self):
        result = self.fx.run_hook({
            "file_path": str(self.fx.root / "src" / "api" / "userService.ts"),
            "content": "export const x = 1;",
        })
        self.assertEqual(result.returncode, 0,
                         msg=f"stdout={result.stdout}\nstderr={result.stderr}")
        self.assertTrue(result.stdout.strip(),
                        f"expected JSON stdout, got empty (stderr={result.stderr})")
        payload = json.loads(result.stdout)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("API 经验", ctx)
        self.assertEqual(payload["hookSpecificOutput"]["hookEventName"], "PostToolUse")

    def test_keyword_in_content_injects(self):
        # file_path 在 components/ 不命中 applies_to，但 content 含 fetch 命中关键词
        result = self.fx.run_hook({
            "file_path": str(self.fx.root / "src" / "components" / "Foo.tsx"),
            "content": "const r = await fetch('/x');",
        })
        # 注意：脚本只对 src/api/ + src/components/ 等 src/ 下 .tsx/.ts 文件注入，
        # 这里 Foo.tsx 在 src/components/ 下，且 content 含 fetch，应该命中
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.stdout.strip())
        payload = json.loads(result.stdout)
        self.assertIn("API 经验",
                      payload["hookSpecificOutput"]["additionalContext"])

    def test_non_src_file_silent(self):
        result = self.fx.run_hook({
            "file_path": str(self.fx.root / "tests" / "x.ts"),
            "content": "fetch users",
        })
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_non_ts_file_silent(self):
        result = self.fx.run_hook({
            "file_path": str(self.fx.root / "src" / "api" / "README.md"),
            "content": "fetch",
        })
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_no_match_silent(self):
        # 没 lesson 匹配这个目录
        result = self.fx.run_hook({
            "file_path": str(self.fx.root / "src" / "unknown" / "x.ts"),
            "content": "完全无关",
        })
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_mode_off_silent(self):
        self.fx.set_mode("off")
        result = self.fx.run_hook({
            "file_path": str(self.fx.root / "src" / "api" / "x.ts"),
            "content": "x",
        })
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_empty_file_path_silent(self):
        result = self.fx.run_hook({"file_path": "", "content": "x"})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_experience_disabled_via_config_silent(self):
        """A1+A2: hooks/.config.sh 设 HARNESS_EXPERIENCE_ENABLED=0 → 直接退。"""
        hooks_dir = self.fx.root / ".harness" / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        (hooks_dir / ".config.sh").write_text(
            "HARNESS_SOURCE_ROOT='src'\n"
            "HARNESS_INCLUDE_EXT=('.ts' '.tsx' '.d.ts')\n"
            "HARNESS_EXCLUDE_DIRS=()\n"
            "HARNESS_MODE='strict'\n"
            "HARNESS_EXPERIENCE_ENABLED=0\n",
            encoding="utf-8",
        )
        result = self.fx.run_hook({
            "file_path": str(self.fx.root / "src" / "api" / "x.ts"),
            "content": "fetch",
        })
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "",
                         "experience disabled 应该静默退出,不应调 CLI")


if __name__ == "__main__":
    unittest.main()
