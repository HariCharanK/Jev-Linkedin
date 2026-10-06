import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from jev_linkedin.memory import Memory, canonical_profile_url

URL = "https://www.linkedin.com/in/test-person/"


def page(pending=False, following=True, **extra):
    return {"url": URL, "profile": {"url": URL, "pending": pending, "following": following, **extra}}


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite3"
        self.memory = Memory(self.path)

    def tearDown(self):
        self.memory.close()
        self.temp.cleanup()

    def invite(self, before=None):
        return self.memory.begin_action(
            {"kind": "click", "label": "Send", "effect": "invite", "profile_url": URL},
            page() if before is None else before,
        )

    def test_canonicalization_and_foreign_domains(self):
        self.assertEqual(canonical_profile_url("https://uk.linkedin.com/in/Test-Person?trk=x#about"), URL)
        for value in (
            "https://linkedin.com.evil.test/in/test-person",
            "/in/test-person/",
            "https://www.linkedin.com/company/test",
            None,
            "javascript:alert(1)",
        ):
            self.assertIsNone(canonical_profile_url(value))

    def test_duplicate_profile_observations_merge_evidence(self):
        self.memory.record_observation(page())
        self.memory.record_observation({"people": [{"url": URL + "?trk=x", "name": "Test"}]})
        state = self.memory.context()
        self.assertEqual(state["total_profiles"], 1)
        self.assertFalse(state["profiles"][0]["pending"])
        self.assertEqual(state["profiles"][0]["name"], "Test")
        self.assertIn("pending", state["profiles"][0]["evidence"])

    def test_receipt_alone_cannot_verify_invitation(self):
        action = self.invite()
        self.memory.finish_action(action, "executed")
        self.assertEqual(self.memory.invitation_count(), 0)
        self.assertEqual(len(self.memory.unresolved_actions()), 1)

    def test_partial_outcome_then_unfollow(self):
        action = self.invite()
        self.memory.finish_action(action, "verified", page(pending=True))
        self.assertEqual(self.memory.invitation_count(), 1)
        self.assertTrue(self.memory.profile(URL)["invited"])
        self.assertTrue(self.memory.context()["profiles"][0]["invited"])
        self.assertFalse(self.memory.profile(URL)["complete"])
        self.memory.record_observation(page(pending=True, following=False))
        self.assertTrue(self.memory.profile(URL)["complete"])
        self.assertEqual(self.memory.invitation_count(), 1)

    def test_existing_pending_is_not_new_invitation(self):
        action = self.invite(page(pending=True))
        self.memory.finish_action(action, "verified", page(pending=True))
        self.assertEqual(self.memory.invitation_count(), 0)
        self.assertFalse(self.memory.profile(URL)["invited"])

    def test_unknown_current_state_does_not_use_stale_history(self):
        self.memory.record_observation(page(pending=False))
        action = self.invite(page(pending=None))
        self.memory.finish_action(action, "verified", page(pending=True))
        self.assertEqual(self.memory.invitation_count(), 0)

    def test_other_profile_does_not_verify_action(self):
        action = self.invite()
        self.memory.finish_action(action, "uncertain", page(pending=True, url="https://www.linkedin.com/in/other/"))
        self.assertEqual(self.memory.invitation_count(), 0)

    def test_non_invitation_action_does_not_count(self):
        action = self.memory.begin_action({"kind": "scroll", "label": "Down"}, page())
        self.memory.finish_action(action, "verified", page(pending=True))
        self.assertEqual(self.memory.invitation_count(), 0)

    def test_restart_keeps_write_ahead_intent_and_uncertainty(self):
        action = self.invite()
        self.memory.close()
        self.memory = Memory(self.path)
        self.assertEqual(self.memory.unresolved_actions()[0]["id"], action)
        self.memory.finish_action(action, "uncertain", error="connection lost")
        self.memory.close()
        self.memory = Memory(self.path)
        self.assertEqual(self.memory.unresolved_actions()[0]["outcome"], "uncertain")
        self.memory.finish_action(action, "verified", page(pending=True))
        self.assertEqual(self.memory.unresolved_actions(), [])
        self.assertEqual(self.memory.invitation_count(), 1)

    def test_count_is_idempotent_across_restarts(self):
        action = self.invite()
        self.memory.finish_action(action, "verified", page(pending=True))
        self.memory.finish_action(action, "verified", page(pending=True))
        self.memory.close()
        self.memory = Memory(self.path)
        duplicate = self.invite()
        self.memory.finish_action(duplicate, "verified", page(pending=True))
        self.assertEqual(self.memory.invitation_count(), 1)

    def test_context_is_bounded_without_deleting_history(self):
        for index in range(12):
            self.memory.record_observation(page(url=f"https://www.linkedin.com/in/person-{index}/"))
        context = self.memory.context(limit=3)
        self.assertEqual(len(context["profiles"]), 3)
        self.assertEqual(context["omitted_profiles"], 9)
        self.assertIsNotNone(self.memory.profile("https://www.linkedin.com/in/person-0/"))
        self.assertEqual(self.memory.db.execute("SELECT count(*) FROM observations").fetchone()[0], 12)

    def test_stale_actions_resolve_without_count(self):
        action = self.invite()
        self.memory.finish_action(action, "stale")
        self.assertEqual(self.memory.unresolved_actions(), [])
        self.assertEqual(self.memory.invitation_count(), 0)

    def test_old_unfollow_evidence_cannot_complete_a_new_invitation(self):
        action = self.invite(page(pending=False, following=False))
        self.memory.finish_action(action, "verified", page(pending=True, following=None))
        self.assertFalse(self.memory.profile(URL)["complete"])
        self.assertEqual(self.memory.context()["followup_required_count"], 1)
        self.memory.record_observation(page(pending=True, following=False))
        self.assertTrue(self.memory.profile(URL)["complete"])
        self.assertEqual(self.memory.context()["followup_required"], [])

    def test_followup_is_visible_outside_recent_profile_window(self):
        action = self.invite()
        self.memory.finish_action(action, "verified", page(pending=True))
        for index in range(4):
            self.memory.record_observation(page(url=f"https://www.linkedin.com/in/new-{index}/"))
        context = self.memory.context(limit=1)
        self.assertNotEqual(context["profiles"][0]["url"], URL)
        self.assertEqual(context["followup_required"][0]["url"], URL)

    def test_recent_actions_are_bounded_and_chronological(self):
        for index in range(15):
            action = self.memory.begin_action({"kind": "click", "label": f"More {index}"}, page())
            self.memory.finish_action(action, "verified")
        recent = self.memory.context()["recent_actions"]
        self.assertEqual(len(recent), 10)
        self.assertEqual(recent[0]["label"], "More 5")
        self.assertEqual(recent[-1]["label"], "More 14")
        self.assertEqual(recent[-1]["outcome"], "verified")


if __name__ == "__main__":
    unittest.main()
