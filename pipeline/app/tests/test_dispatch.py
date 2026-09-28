import unittest
from pathlib import Path
import sys
APP=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(APP)); sys.path.insert(0,str(APP/'daemon'))
from daemon.dispatchd import parse_plan_line

class ParsePlanTests(unittest.TestCase):
    def test_label_and_id(self):
        self.assertEqual(parse_plan_line("Subject\n\nPlan: Human Label\t226f2be1-93be-5d90-a686-8307d43f29d2\n"),("Human Label\t226f2be1-93be-5d90-a686-8307d43f29d2","226f2be1-93be-5d90-a686-8307d43f29d2"))
    def test_last_token_is_id(self): self.assertEqual(parse_plan_line("Plan: Any words here ID-123")[1],"ID-123")
    def test_case_sensitive_marker(self): self.assertIsNone(parse_plan_line("plan: Human ID-123"))
    def test_missing_plan(self): self.assertIsNone(parse_plan_line("Subject only"))
    def test_multiple_plan_lines_rejected(self):
        with self.assertRaises(ValueError): parse_plan_line("Plan: A id1\nPlan: B id2")
if __name__=="__main__": unittest.main()
