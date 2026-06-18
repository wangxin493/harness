"""installer 单元测试。"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from . import _setup  # noqa: F401

from lib.installer import (  # noqa: E402
    CLAUDE_MD_BEGIN,
    CLAUDE_MD_END,
    HOOK_COMMAND,
    HOOK_COMMAND_FINGERPRINT,
    ClaudeInstaller,
    get_installer,
)


class _Tmp:
    """临时项目目录夹具。"""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="harness-installer-")).resolve()

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, rel: str, content: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def installer(self) -> ClaudeInstaller:
        return ClaudeInstaller(project_dir=self.root)


class TestClaudeInstallerSettings(unittest.TestCase):
    """settings.json 安装/卸载/合并的核心行为。"""

    def setUp(self) -> None:
        self.tmp = _Tmp()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # ----------------------------------------------------------- 全新安装

    def test_install_creates_settings_when_absent(self) -> None:
        result = self.tmp.installer().install()

        # settings.json 应被创建
        settings_file = self.tmp.root / ".claude" / "settings.json"
        self.assertTrue(settings_file.exists())

        data = json.loads(settings_file.read_text(encoding="utf-8"))
        post = data["hooks"]["PostToolUse"]
        self.assertEqual(len(post), 1)
        self.assertEqual(set(post[0]["matcher"].split("|")),
                         {"Write", "Edit", "MultiEdit"})
        self.assertEqual(post[0]["hooks"][0]["command"], HOOK_COMMAND)

        # changes 列表里 settings 应记 created
        actions = {str(c.path): c.action for c in result.changes}
        self.assertEqual(actions[str(settings_file)], "created")

    # ---------------------------------------------------------- 幂等性

    def test_install_is_idempotent(self) -> None:
        inst = self.tmp.installer()
        inst.install()
        result = inst.install()

        settings_file = self.tmp.root / ".claude" / "settings.json"
        data = json.loads(settings_file.read_text(encoding="utf-8"))
        post = data["hooks"]["PostToolUse"]

        # 仍然只有 1 个 group、1 个 hook 条目
        self.assertEqual(len(post), 1)
        self.assertEqual(len(post[0]["hooks"]), 1)

        # 第二次 install 应该所有 change 都是 unchanged
        actions = {c.action for c in result.changes}
        self.assertEqual(actions, {"unchanged"})

    # --------------------------------------------------- 与第三方 hook 共存

    def test_install_preserves_third_party_hooks(self) -> None:
        # 模拟 baidu-cc 已经装的 hook 集合
        existing = {
            "permissions": {"deny": ["WebSearch"]},
            "hooks": {
                "SessionStart": [
                    {"hooks": [{"type": "command",
                                "command": "~/.baidu-cc/hooks/data-report --session-start",
                                "timeout": 10}]}
                ],
                "PreToolUse": [
                    {"matcher": "Write|Edit",
                     "hooks": [{"type": "command",
                                "command": "~/.baidu-cc/hooks/data-report --pre-tool-use",
                                "timeout": 10}]}
                ],
                "PostToolUse": [
                    {"matcher": "Write|Edit",
                     "hooks": [{"type": "command",
                                "command": "~/.baidu-cc/hooks/data-report --post-tool-use",
                                "timeout": 10}]}
                ],
            },
        }
        self.tmp.write(
            ".claude/settings.json",
            json.dumps(existing, ensure_ascii=False, indent=2),
        )

        self.tmp.installer().install()

        data = json.loads(
            (self.tmp.root / ".claude" / "settings.json").read_text(encoding="utf-8")
        )

        # SessionStart / PreToolUse 原样保留
        self.assertEqual(
            data["hooks"]["SessionStart"][0]["hooks"][0]["command"],
            "~/.baidu-cc/hooks/data-report --session-start",
        )
        self.assertEqual(
            data["hooks"]["PreToolUse"][0]["hooks"][0]["command"],
            "~/.baidu-cc/hooks/data-report --pre-tool-use",
        )

        # PostToolUse：原 group(matcher=Write|Edit)还在；harness 新加了一个 Write|Edit|MultiEdit group
        post = data["hooks"]["PostToolUse"]
        self.assertEqual(len(post), 2)
        commands = {entry["command"] for g in post for entry in g["hooks"]}
        self.assertIn("~/.baidu-cc/hooks/data-report --post-tool-use", commands)
        self.assertIn(HOOK_COMMAND, commands)

        # 其它顶级 key（permissions）也保留
        self.assertEqual(data["permissions"]["deny"], ["WebSearch"])

    # -------------------------------------------------------------- 卸载

    def test_uninstall_only_removes_harness_hook(self) -> None:
        existing = {
            "hooks": {
                "PostToolUse": [
                    {"matcher": "Write|Edit",
                     "hooks": [{"type": "command",
                                "command": "~/.baidu-cc/hooks/data-report --post-tool-use",
                                "timeout": 10}]}
                ],
            },
        }
        self.tmp.write(
            ".claude/settings.json",
            json.dumps(existing, ensure_ascii=False, indent=2),
        )
        inst = self.tmp.installer()
        inst.install()
        inst.uninstall()

        data = json.loads(
            (self.tmp.root / ".claude" / "settings.json").read_text(encoding="utf-8")
        )

        # 第三方 hook 还在
        self.assertEqual(len(data["hooks"]["PostToolUse"]), 1)
        cmd = data["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
        self.assertNotIn(HOOK_COMMAND_FINGERPRINT, cmd)
        self.assertIn("data-report", cmd)

    def test_uninstall_when_nothing_installed_is_noop(self) -> None:
        # settings.json 不存在
        result = self.tmp.installer().uninstall()
        actions = {c.action for c in result.changes}
        self.assertEqual(actions, {"unchanged"})

    def test_install_dry_run_does_not_touch_disk(self) -> None:
        self.tmp.installer().install(dry_run=True)
        self.assertFalse((self.tmp.root / ".claude" / "settings.json").exists())
        self.assertFalse((self.tmp.root / "CLAUDE.md").exists())

    def test_status_reflects_install_state(self) -> None:
        inst = self.tmp.installer()
        s0 = inst.status()
        self.assertFalse(s0["settings_has_hook"])
        self.assertFalse(s0["claude_md_has_block"])

        inst.install()
        s1 = inst.status()
        self.assertTrue(s1["settings_has_hook"])
        self.assertTrue(s1["claude_md_has_block"])

        inst.uninstall()
        s2 = inst.status()
        self.assertFalse(s2["settings_has_hook"])
        self.assertFalse(s2["claude_md_has_block"])

    # --------------------------------------------- 损坏 settings.json 兜底

    def test_install_recovers_from_broken_settings(self) -> None:
        # 写一个非 JSON 的内容
        self.tmp.write(".claude/settings.json", "not-json{{{")
        self.tmp.installer().install()

        data = json.loads(
            (self.tmp.root / ".claude" / "settings.json").read_text(encoding="utf-8")
        )
        # 至少 hook 装上了
        cmds = [
            entry["command"]
            for g in data["hooks"]["PostToolUse"]
            for entry in g["hooks"]
        ]
        self.assertIn(HOOK_COMMAND, cmds)


class TestClaudeInstallerClaudeMd(unittest.TestCase):
    """CLAUDE.md 托管块的合并/替换/卸载。"""

    def setUp(self) -> None:
        self.tmp = _Tmp()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_create_claude_md_when_absent(self) -> None:
        self.tmp.installer().install()
        text = (self.tmp.root / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn(CLAUDE_MD_BEGIN, text)
        self.assertIn(CLAUDE_MD_END, text)
        self.assertIn("@.harness/generated/claude.md", text)

    def test_append_block_when_md_exists_without_block(self) -> None:
        self.tmp.write("CLAUDE.md", "# 我的项目\n\n这是手写的说明。\n")
        self.tmp.installer().install()
        text = (self.tmp.root / "CLAUDE.md").read_text(encoding="utf-8")
        # 原有内容保留
        self.assertIn("这是手写的说明。", text)
        # 块追加
        self.assertIn(CLAUDE_MD_BEGIN, text)
        # 顺序：用户内容在前，托管块在后
        self.assertLess(text.index("这是手写的说明。"), text.index(CLAUDE_MD_BEGIN))

    def test_replace_existing_block(self) -> None:
        # 模拟旧版本的托管块，里面有过时内容
        old = (
            "# 我的项目\n\n用户说明\n\n"
            f"{CLAUDE_MD_BEGIN}\n旧的过时块\n{CLAUDE_MD_END}\n"
            "块后面也有用户内容\n"
        )
        self.tmp.write("CLAUDE.md", old)
        self.tmp.installer().install()
        text = (self.tmp.root / "CLAUDE.md").read_text(encoding="utf-8")

        # 旧内容被替换
        self.assertNotIn("旧的过时块", text)
        self.assertIn("@.harness/generated/claude.md", text)
        # 用户上下两段都还在
        self.assertIn("用户说明", text)
        self.assertIn("块后面也有用户内容", text)

    def test_uninstall_removes_only_block_keeps_user_content(self) -> None:
        self.tmp.write("CLAUDE.md", "# 我的项目\n\n用户说明\n")
        inst = self.tmp.installer()
        inst.install()
        inst.uninstall()
        text = (self.tmp.root / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("用户说明", text)
        self.assertNotIn(CLAUDE_MD_BEGIN, text)

    def test_uninstall_deletes_md_if_only_block_existed(self) -> None:
        # 全新装 → CLAUDE.md 由 installer 创建 → 卸载后只剩空骨架 → 应直接删
        inst = self.tmp.installer()
        inst.install()
        # 把用户内容清空，模拟"用户没改过 CLAUDE.md"
        md_path = self.tmp.root / "CLAUDE.md"
        # 直接覆盖成"只有托管块"的极简内容
        text = md_path.read_text(encoding="utf-8")
        # 切出托管块部分
        begin = text.index(CLAUDE_MD_BEGIN)
        end = text.index(CLAUDE_MD_END) + len(CLAUDE_MD_END) + 1
        only_block = text[begin:end]
        md_path.write_text(only_block, encoding="utf-8")

        inst.uninstall()
        self.assertFalse(md_path.exists(),
                         "CLAUDE.md 整体只剩空白时应被删除")


class TestRegistry(unittest.TestCase):
    def test_get_installer_aliases(self) -> None:
        tmp = _Tmp()
        try:
            inst1 = get_installer("claude", tmp.root)
            inst2 = get_installer("ducc", tmp.root)
            inst3 = get_installer("baidu-cc", tmp.root)
            for inst in (inst1, inst2, inst3):
                self.assertIsInstance(inst, ClaudeInstaller)
        finally:
            tmp.cleanup()

    def test_unknown_agent_raises(self) -> None:
        tmp = _Tmp()
        try:
            with self.assertRaises(ValueError):
                get_installer("cursor", tmp.root)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
