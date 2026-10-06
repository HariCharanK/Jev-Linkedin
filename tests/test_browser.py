from unittest.mock import Mock

import pytest

from jev_linkedin.browser import Browser, StalePage


def browser():
    b = Browser.__new__(Browser)
    b.target = "owned"
    b.session = "session"
    b.fresh = Mock(return_value=True)
    b.evaluate = Mock(return_value={"x": 40, "y": 50})
    b.call = Mock(return_value={})
    return b


def test_stale_blocks_input():
    b = browser()
    b.fresh.return_value = False
    a = {"id": "c1", "kind": "click", "node": 1}
    with pytest.raises(StalePage):
        b.act(a, {"actions": [a]})
    b.call.assert_not_called()


def test_mouse_release_failure_is_uncertain_not_stale():
    b = browser()
    b.call.side_effect = [{}, StalePage("secret payload")]
    a = {"id": "c1", "kind": "click", "node": 1}
    with pytest.raises(RuntimeError, match="uncertain") as error:
        b.act(a, {"actions": [a]})
    assert "secret" not in str(error.value)
    assert b.call.call_count == 2


def test_nested_scroll_uses_live_container_point():
    b = browser()
    a = {"id": "s1", "kind": "scroll", "node": 2, "delta": 400, "rect": {"x": 1}}
    b.act(a, {"actions": [a]})
    b.call.assert_called_once_with("Input.dispatchMouseEvent", type="mouseWheel", x=40, y=50, deltaX=0, deltaY=400)


def test_rejects_modified_observed_action():
    b = browser()
    a = {"id": "c1", "kind": "click", "node": 1}
    with pytest.raises(ValueError, match="not observed"):
        b.act({**a, "node": 2}, {"actions": [a]})
    b.call.assert_not_called()


def test_fill_guard_checks_node():
    b = browser()
    b.evaluate.return_value = ["key", "changed"]
    assert not Browser.fresh(b, {"page_key": "key", "guards": {"1": "original"}}, {"kind": "fill", "node": 1})


def test_back_requires_matching_history():
    b = browser()
    b.evaluate.return_value = "key"
    b.call.return_value = {"currentIndex": 1, "entries": [{"id": 1}, {"id": 3}]}
    assert not Browser.fresh(b, {"page_key": "key"}, {"kind": "back", "entry_id": 1, "current_entry_id": 2})


def test_cdp_errors_do_not_leak_payload(monkeypatch):
    b = browser()

    def fail(*args, **kwargs):
        raise RuntimeError("private CDP payload")

    monkeypatch.setattr("jev_linkedin.browser.cdp", fail)
    with pytest.raises(RuntimeError) as error:
        Browser.call(b, "Runtime.evaluate", expression="private")
    assert "private" not in str(error.value)


def test_observe_waits_for_ready_and_stable_snapshot(monkeypatch):
    b = browser()
    b._initial_navigation = True
    snapshot = {"url": "https://fixture.test", "actions": [], "marker": "stable"}
    b.evaluate.side_effect = [
        "loading",
        "complete",
        {**snapshot, "marker": "loading"},
        "complete",
        snapshot,
        "complete",
        snapshot,
    ]
    b.call.return_value = {"currentIndex": 0, "entries": []}
    monkeypatch.setattr("jev_linkedin.browser.time.sleep", lambda duration: None)
    assert b.observe()["marker"] == "stable"
    assert b._initial_navigation is False


def test_production_executor_against_local_chromium():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        chromium = p.chromium.launch(headless=True)
        page = chromium.new_page(viewport={"width": 1000, "height": 720})
        page.set_content("""<main><h1>Local fixture</h1>
          <input aria-label="Search"><button onclick="this.textContent='Done'">Do it</button>
          <select aria-label="Choice"><option value="a">Alpha</option><option value="b">Beta</option></select>
          <div aria-label="Nested results" style="height:120px;overflow:auto;width:400px">
            <div style="height:1000px">More fixture content</div></div></main>""")
        session = page.context.new_cdp_session(page)
        b = Browser.__new__(Browser)
        b.call = lambda method, **params: session.send(method, params)
        state = b.observe()
        fill = next(a for a in state["actions"] if a["kind"] == "fill")
        b.act(fill, state, text="Example company")
        assert page.locator("input").input_value() == "Example company"
        state = b.observe()
        select = next(a for a in state["actions"] if a["kind"] == "select" and a["value"] == "b")
        b.act(select, state)
        assert page.locator("select").input_value() == "b"
        state = b.observe()
        scroll = next(a for a in state["actions"] if a["kind"] == "scroll" and a.get("node"))
        b.act(scroll, state)
        b.observe()
        assert page.locator('[aria-label="Nested results"]').evaluate("e=>e.scrollTop") > 0
        state = b.observe()
        click = next(a for a in state["actions"] if a["label"] == "Do it")
        b.act(click, state)
        assert page.locator("button").inner_text() == "Done"
        with pytest.raises(StalePage):
            b.act(click, state)
        chromium.close()
