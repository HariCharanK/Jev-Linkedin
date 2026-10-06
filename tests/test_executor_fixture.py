"""Production Harness adapter contract against intercepted local Chromium fixtures."""
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from jev_linkedin import browser as adapter

FIXTURE = (Path(__file__).parent / "fixtures/people.html").read_text()


@pytest.fixture
def connected_browser(monkeypatch):
    """Replace the Harness transport, retaining Browser construction and every guard."""
    with sync_playwright() as p:
        chromium = p.chromium.launch(headless=True)
        context = chromium.new_context()
        context.route("**/*", lambda route: route.fulfill(body=FIXTURE, content_type="text/html"))
        pages, sessions, calls = {}, {}, []

        def cdp(method, session_id=None, **params):
            calls.append(method)
            if method == "Target.createTarget":
                assert params == {"url": "about:blank", "background": True}
                target = "fixture-target"
                pages[target] = context.new_page()
                return {"targetId": target}
            if method == "Target.attachToTarget":
                session = "fixture-session"
                sessions[session] = context.new_cdp_session(pages[params["targetId"]])
                return {"sessionId": session}
            if method == "Target.closeTarget":
                pages[params["targetId"]].close()
                return {"success": True}
            return sessions[session_id].send(method, params)

        monkeypatch.setattr(adapter, "ensure_daemon", lambda: None)
        monkeypatch.setattr(adapter, "cdp", cdp)
        browser = adapter.Browser("https://fixture.test/people")
        yield browser, pages["fixture-target"], calls
        browser.close()
        assert pages["fixture-target"].is_closed()
        chromium.close()


def test_recycled_profile_refuses_click_before_sending_input(connected_browser):
    browser, page, calls = connected_browser
    observed = browser.observe()
    alice = next(p for p in observed["people"] if p["url"].endswith("/alice"))
    action = next(a for a in observed["actions"] if a["label"] == "Connect" and a["context"] == alice["group"])
    assert browser.fresh(observed, action)
    # Virtual lists can reuse the exact same button node for a different person.
    page.eval_on_selector("#alice a", "e => e.href = 'https://www.linkedin.com/in/recycled'")
    previous_inputs = calls.count("Input.dispatchMouseEvent")
    with pytest.raises(adapter.StalePage, match="changed"):
        browser.act(action, observed)
    assert calls.count("Input.dispatchMouseEvent") == previous_inputs


def test_click_navigation_then_guarded_history_back(connected_browser):
    browser, page, _ = connected_browser
    observed = browser.observe()
    profile_link = next(a for a in observed["actions"] if a.get("href", "") and "/in/alice" in a["href"])
    browser.act(profile_link, observed)
    detail = browser.observe()
    assert detail["url"].startswith("https://www.linkedin.com/in/alice/")
    back = next(a for a in detail["actions"] if a["kind"] == "back")
    assert browser.fresh(detail, back)
    browser.act(back, detail)
    returned = browser.observe()
    assert returned["url"] == "https://fixture.test/people"
    assert page.url == returned["url"]
