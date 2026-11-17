from __future__ import annotations

import asyncio
import shutil
import tempfile
import unittest
from pathlib import Path

from botboy.skills.manager import SkillManager
from botboy.skills.native import forge
from botboy.skills.native.base import NativeSkillContext

ROOT = Path(__file__).resolve().parents[1]

GOOD = """---
name: word-stats
version: 1.2.0
description: Count words and characters in the text passed after the trigger.
triggers: [wordstats]
security_level: BEGINNER
layer:
  id: botboy.skills.word-stats
  security:
    permissions: []
  runtime:
    type: subprocess
    timeout: 5
---

```python
text = payload.get("args", "")
_result = {"words": len(text.split())}
```
"""


def codes(report: forge.LintReport, level: str = "error") -> set:
    return {finding.code for finding in report.findings if finding.level == level}


class ForgeLintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = SkillManager(auto_discover=False)

    def lint(self, text: str) -> forge.LintReport:
        return forge.lint_skill_text(text, "test/SKILL.md", skill_manager=self.manager)

    def test_clean_manifest_passes_and_reports_contract(self) -> None:
        report = self.lint(GOOD)
        self.assertEqual(report.errors, 0, report.render())
        self.assertTrue(report.executable)
        self.assertEqual(report.contract["name"], "word-stats")
        self.assertTrue(report.contract["needs_approval"])
        self.assertFalse(report.contract["allow_filesystem"])

    def test_structural_errors(self) -> None:
        self.assertEqual(codes(self.lint("no frontmatter")), {"frontmatter.missing"})
        self.assertIn("yaml.invalid", codes(self.lint("---\nname: [unclosed\n---\n")))
        self.assertIn("yaml.not_mapping", codes(self.lint("---\n- a\n- b\n---\n")))
        broken = GOOD.replace("description: Count words", "description: Count --- words")
        self.assertIn("frontmatter.dashes", codes(self.lint(broken)))
        missing = GOOD.replace("name: word-stats\n", "").replace("description: Count words and characters in the text passed after the trigger.\n", "")
        self.assertTrue({"name.missing", "description.missing"} <= codes(self.lint(missing)))
        self.assertIn("security_level.invalid", codes(self.lint(GOOD.replace("BEGINNER", "GODMODE"))))

    def test_trigger_reachability(self) -> None:
        self.assertIn("trigger.reserved", codes(self.lint(GOOD.replace("[wordstats]", "[status]"))))
        self.assertIn("trigger.shadowed", codes(self.lint(GOOD.replace("[wordstats]", "[calculate]"))))
        self.assertIn("trigger.shadowed", codes(self.lint(GOOD.replace("[wordstats]", "[expr]"))))
        self.assertIn("trigger.format", codes(self.lint(GOOD.replace("[wordstats]", '["word stats"]'))))

    def test_code_checks_match_the_runtime(self) -> None:
        blocked = GOOD.replace('text = payload.get("args", "")', 'import subprocess\ntext = ""')
        self.assertIn("code.blocked", codes(self.lint(blocked)))
        syntax = GOOD.replace('_result = {"words": len(text.split())}', "_result = (")
        self.assertIn("code.syntax", codes(self.lint(syntax)))
        no_result = GOOD.replace('_result = {"words": len(text.split())}', "x = 1")
        self.assertIn("code.no_result", codes(self.lint(no_result), "warning"))
        no_code = GOOD.split("```python")[0]
        self.assertIn("code.missing", codes(self.lint(no_code), "warning"))

    def test_capabilities_the_sandbox_refuses_are_errors(self) -> None:
        inferred = self.lint(GOOD.replace("[wordstats]", "[filediff]"))
        self.assertIn("runtime.capability_refused", codes(inferred))
        self.assertIn("inferred from a trigger", inferred.render())
        network = self.lint(GOOD.replace("security_level: BEGINNER", "security_level: BEGINNER\nallow_network: true"))
        self.assertIn("runtime.capability_refused", codes(network))
        explicit_off = GOOD.replace("[wordstats]", "[filediff]").replace(
            "security_level: BEGINNER", "security_level: BEGINNER\nallow_filesystem: false")
        self.assertEqual(self.lint(explicit_off).errors, 0)

    def test_runtime_and_ldf_findings(self) -> None:
        docker = self.lint(GOOD.replace("type: subprocess", "type: docker"))
        self.assertIn("runtime.docker", codes(docker))
        builtin = self.lint(GOOD.replace("type: subprocess", "type: builtin\n    entry: os:system"))
        self.assertIn("runtime.builtin", codes(builtin, "info"))
        self.assertIn("ignored", builtin.render())
        perms = self.lint(GOOD.replace("permissions: []", "permissions: [root.everything]"))
        self.assertIn("ldf.permission", codes(perms, "warning"))
        timeout = self.lint(GOOD.replace("timeout: 5", "timeout: soon"))
        self.assertIn("ldf.invalid", codes(timeout))
        policy = self.lint(GOOD.replace("security_level: BEGINNER", "security_level: BEGINNER\nremote_policy: sometimes"))
        self.assertIn("remote_policy.invalid", codes(policy))

    def test_bundled_example_skills_findings(self) -> None:
        examples = ROOT / "examples" / "skills"
        if not examples.is_dir():
            self.skipTest("examples not present")
        git_summary = self.lint((examples / "git-summary" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertTrue({"code.blocked", "runtime.capability_refused"} <= codes(git_summary))
        diff_compare = self.lint((examples / "diff-compare" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("runtime.capability_refused", codes(diff_compare))


class ForgeScaffoldTests(unittest.TestCase):
    def test_scaffold_lints_clean_and_runs_in_the_sandbox(self) -> None:
        manager = SkillManager(auto_discover=False)
        result = asyncio.run(forge.run('forge scaffold word-counter --trigger wc --description "Count words in the text after the trigger"',
                                       NativeSkillContext(skill_manager=manager)))
        self.assertTrue(result["success"], result["output"])
        self.assertEqual(result["data"]["lint"]["errors"], 0)
        self.assertTrue(result["data"]["lint"]["executable"])
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "word-counter"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(result["data"]["skill_md"], encoding="utf-8")
            loaded = SkillManager(skills_dir=tmp)
            self.assertEqual(loaded.load_all_skills(), 1)
            self.assertEqual(loaded.find_skill_for_command("wc hello sandbox world"), "word-counter")
            denied = asyncio.run(loaded.execute_custom("word-counter", "wc a b", approval_context={"granted": False}))
            self.assertFalse(denied["success"])
            ran = asyncio.run(loaded.execute_custom("word-counter", "wc hello sandbox world",
                                                    approval_context={"granted": True}))
            self.assertTrue(ran["success"], ran)
            self.assertEqual(ran["data"]["runtime_used"], "subprocess")
            self.assertEqual(ran["data"]["result"], {"skill": "word-counter", "words": 3, "characters": 19,
                                                     "longest": "sandbox"})

    def test_scaffold_rejects_bad_input(self) -> None:
        for command in ("forge scaffold Bad_Name --trigger x", "forge scaffold ok --trigger status",
                        "forge scaffold ok", "forge scaffold ok --trigger x --level EXPERT",
                        'forge scaffold ok --trigger x --description "a --- b"'):
            with self.subTest(command=command):
                self.assertFalse(asyncio.run(forge.run(command, NativeSkillContext()))["success"])


class ForgeWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        (self.root / "skills" / "good").mkdir(parents=True)
        (self.root / "skills" / "bad").mkdir(parents=True)
        (self.root / "skills" / "good" / "SKILL.md").write_text(GOOD, encoding="utf-8")
        (self.root / "skills" / "bad" / "SKILL.md").write_text(
            GOOD.replace("[wordstats]", "[status]").replace("name: word-stats", "name: bad-stats"), encoding="utf-8")
        self.manager = SkillManager(skills_dir=str(self.root / "skills"))
        self.manager.load_all_skills()
        self.manager.configure_native_context(workspace_root=str(self.root))

    def call(self, command: str) -> dict:
        return asyncio.run(self.manager.execute_builtin("forge", command))

    def test_lint_file_and_directory(self) -> None:
        single = self.call("forge lint skills/good/SKILL.md")
        self.assertTrue(single["success"], single["output"])
        both = self.call("forge lint skills")
        self.assertFalse(both["success"])
        self.assertEqual(both["data"]["errors"], 1)
        self.assertEqual(both["data"]["executable"], 1)
        self.assertIn("trigger.reserved", both["output"])

    def test_audit_covers_discovered_skills(self) -> None:
        audit = self.call("forge audit")
        self.assertEqual({Path(r["source"]).parent.name for r in audit["data"]["reports"]}, {"good", "bad"})
        self.assertEqual(audit["data"]["errors"], 1)

    def test_duplicate_names_are_reported(self) -> None:
        (self.root / "skills" / "copy").mkdir()
        (self.root / "skills" / "copy" / "SKILL.md").write_text(GOOD, encoding="utf-8")
        result = self.call("forge lint skills")
        self.assertIn("name.duplicate", result["output"])
        self.assertEqual(result["data"]["executable"], 0)

    def test_lint_is_confined(self) -> None:
        for command in ("forge lint ../", "forge lint /etc", "forge lint missing", "forge lint"):
            with self.subTest(command=command):
                self.assertFalse(self.call(command)["success"])
        shutil.rmtree(self.root / "skills")
        self.assertIn("No SKILL.md", self.call("forge lint .")["output"])


if __name__ == "__main__":
    unittest.main()
