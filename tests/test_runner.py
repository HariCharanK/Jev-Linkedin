from copy import deepcopy

from jev_linkedin.browser import StalePage
from jev_linkedin.config import Task
from jev_linkedin.memory import Memory
from jev_linkedin.runner import Runner

URL = "https://www.linkedin.com/in/test/"


def page(label="More", pending=False, following=False, dialog=False, fingerprint="one"):
    return {
        "url": URL,
        "title": "Person | LinkedIn",
        "text": "Software engineer at Mercor",
        "profile": {
            "url": URL,
            "name": "Person",
            "headline": "Engineer at Mercor",
            "pending": pending,
            "following": following,
            "connected": False if pending is not None else None,
        },
        "actions": [{"id": "e1", "node": 1, "kind": "click", "label": label}],
        "groups": [{"kind": "dialog"}] if dialog else [],
        "notices": [],
        "fingerprint": fingerprint,
    }


class Browser:
    def __init__(self, pages, error=None, observe_error=False):
        self.pages, self.i, self.calls = pages, 0, []
        self.error, self.observe_error = error, observe_error

    def observe(self, screenshot=False):
        if self.calls and self.observe_error:
            raise RuntimeError("observation interrupted")
        return deepcopy(self.pages[min(self.i, len(self.pages) - 1)])

    def act(self, action, state, text=None):
        assert action in state["actions"], "Do not enrich browser-owned observed actions"
        if self.error:
            raise self.error
        self.calls.append(action)
        self.i += 1


class Model:
    def __init__(self, choices=None):
        self.choices = iter(choices or [])
        self.calls = []

    def choose(self, state, goal, memory, text_candidates):
        self.calls.append((state, memory))
        return {
            "choice": next(self.choices, "e1"),
            "confidence": 0.99,
            "target_confidence": 0.99,
            "eligibility": "eligible",
            "eligibility_confidence": 0.99,
        }


def runner(pages, **kw):
    memory = Memory(":memory:")
    browser = Browser(pages, **kw)
    task = Task("Mercor", URL, max_invitations=1, max_steps=10)
    return Runner(task, browser, Model(), memory), browser, memory


def test_preview_never_dispatches():
    run, browser, memory = runner([page()])
    assert run.run().status == "preview"
    assert browser.calls == []
    assert memory.unresolved_actions() == []


def test_full_invitation_then_unfollow_verification():
    run, browser, memory = runner(
        [
            page("Connect"),
            page("Send without a note", pending=None, following=None, dialog=True, fingerprint="dialog"),
            page("Following", pending=True, following=True, fingerprint="sent"),
            page("Unfollow", pending=True, following=True, dialog=True, fingerprint="confirm"),
            page("Follow", pending=True, following=False, fingerprint="done"),
        ]
    )
    result = run.run(execute=True)
    assert result.status == "complete", result
    assert result.invitations == 1
    assert len(browser.calls) == 4
    assert memory.unresolved_actions() == []
    assert memory.profile(URL)["complete"] is True


def test_unknown_dispatch_stops_without_retry_and_persists():
    run, browser, memory = runner([page()], error=RuntimeError("uncertain click"))
    assert run.run(execute=True).status == "needs_review"
    assert len(memory.unresolved_actions()) == 1
    assert run.run(execute=True).status == "needs_review"
    assert len(run.model.calls) == 1


def test_post_dispatch_observation_failure_is_not_retried():
    run, browser, memory = runner([page()], observe_error=True)
    assert run.run(execute=True).status == "needs_review"
    assert len(browser.calls) == 1
    assert memory.unresolved_actions()[0]["outcome"] == "uncertain"


def test_existing_pending_never_sent():
    run, browser, memory = runner([page("Send without a note", pending=True)])
    assert run.run(execute=True).status == "blocked"
    assert not browser.calls
    assert memory.invitation_count() == 0


def test_stale_pre_dispatch_is_bounded():
    run, browser, memory = runner([page()], error=StalePage("stale"))
    assert run.run(execute=True).status == "blocked"
    assert not memory.unresolved_actions()
    assert len(run.model.calls) == run.task.no_progress_limit


def test_no_progress_waits_are_bounded():
    state = page()
    state["actions"] = [{"id": "e1", "kind": "wait", "label": "Wait"}]
    run, browser, _ = runner([state])
    assert run.run(execute=True).status == "blocked"
    assert len(browser.calls) == run.task.no_progress_limit


def test_warning_stops_before_model_call():
    state = page()
    state["notices"] = ["Your account is temporarily restricted"]
    run, browser, _ = runner([state])
    assert run.run(execute=True).status == "blocked"
    assert not run.model.calls


def test_dialog_evidence_never_carried_to_other_person():
    first, second = page("Connect"), page("Send without a note", pending=None, dialog=True)
    second["profile"]["url"] = "https://www.linkedin.com/in/other/"
    second["profile"]["name"] = "Other"
    run, browser, _ = runner([first, second])
    assert run.run(execute=True).status == "blocked"
    assert len(browser.calls) == 1


def test_done_does_not_hide_unfinished_unfollow():
    run, browser, _ = runner(
        [page("Send without a note"), page("Following", pending=True, following=True, fingerprint="sent")]
    )
    run.model = Model(["e1", "DONE"])
    result = run.run(execute=True)
    assert result.status == "needs_review"
    assert len(browser.calls) == 1


def test_recommendation_card_connect_not_attributed_to_current_profile():
    state = page("Connect")
    state["actions"][0]["context"] = "other-card"
    state["groups"] = [{"id": "other-card", "kind": "person", "profile_urls": ["https://www.linkedin.com/in/other/"]}]
    run, browser, _ = runner([state])
    assert run.run(execute=True).status == "blocked"
    assert not browser.calls


def test_plain_body_restriction_stops():
    state = page()
    state["text"] = "Your account is temporarily restricted"
    run, _, _ = runner([state])
    assert run.run(execute=True).status == "blocked"
    assert not run.model.calls
