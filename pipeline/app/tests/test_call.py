import unittest

from autoscribe.call import build_call_parts


class CallTests(unittest.TestCase):
    def test_splits_model_content_from_routing_baggage(self):
        source = {
            "identity": "psg.human-only",
            "path": "Content/One.md",
            "blob": "deadbeef",
            "directive": "Tighten this.",
            "content": "Body\n",
        }
        plan = {"id": "plan-id", "steps": []}
        content, baggage = build_call_parts(
            repo="/home/jeremy/Repos/Book.git",
            commit="abc123",
            ref="refs/heads/master",
            plan=plan,
            source=source,
        )
        self.assertEqual(content["input"]["content"], "Body\n")
        self.assertEqual(content["input"]["directive"], "Tighten this.")
        self.assertEqual(content["plan"], plan)
        self.assertNotIn("identity", str(content))
        self.assertEqual(baggage["schema"], "autoscribe.call-baggage.v2")
        self.assertEqual(baggage["source"]["path"], "Content/One.md")
        self.assertEqual(
            baggage["output"],
            {
                "adapter": "git",
                "repo": "Book.git",
                "identity": "psg.human-only",
                "path_hint": "Content/One.md",
                "commit_message": "AutoScribe response: psg.human-only",
            },
        )


if __name__ == "__main__":
    unittest.main()
