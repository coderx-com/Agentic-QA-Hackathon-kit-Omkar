"""PRD interpretation tests. Run: python -m unittest -v"""
import unittest
from pathlib import Path

from qa_agent.checks import REGISTRY
from qa_agent.llm import LLM
from qa_agent.planner import map_requirements
from qa_agent.prd import parse_prd

PRD = Path(__file__).resolve().parents[2] / "test-website" / "PRD.md"


@unittest.skipUnless(PRD.exists(), "run from the agent/ folder of the hackathon repository")
class PrdTests(unittest.TestCase):
    def setUp(self):
        self.reqs, self.sha = parse_prd(PRD)

    def test_atomic_and_traceable(self):
        self.assertGreaterEqual(len(self.reqs), 50)
        self.assertEqual(len({r.id for r in self.reqs}), len(self.reqs))
        self.assertTrue(all(r.source_line > 0 for r in self.reqs))

    def test_key_statements_extracted(self):
        texts = " ".join(r.text for r in self.reqs).lower()
        for phrase in ("start and end dates are inclusive", "must not exceed the source account",
                       "same-account transfers must be rejected", "clearing filters restores"):
            self.assertIn(phrase, texts)

    def test_every_requirement_is_mapped_or_explained(self):
        map_requirements(self.reqs, LLM(mode="none"))
        for r in self.reqs:
            self.assertTrue(r.check_ids or r.untestable_reason, r.id)
            for c in r.check_ids:
                self.assertIn(c, REGISTRY)


if __name__ == "__main__":
    unittest.main()
