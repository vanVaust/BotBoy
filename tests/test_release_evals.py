from __future__ import annotations

import unittest

from botboy.evals import EvalReplayRunner, default_manifest_path, default_seed_path


class ReleaseEvalSmokeTest(unittest.TestCase):
    def test_default_release_eval_bundle_passes(self) -> None:
        runner = EvalReplayRunner(default_manifest_path(), default_seed_path())
        result = runner.run()
        self.assertEqual(result.failed, 0, result.render())
        self.assertEqual(result.passed, result.total)
        self.assertFalse(result.integrity_issues, result.render())
