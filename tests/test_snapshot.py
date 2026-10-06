"""Exercise the extractor against a real Chromium DOM, never a live service."""
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "src/jev_linkedin/snapshot.js").read_text()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    page = browser.new_page(viewport={"width": 1000, "height": 720})
    page.set_content((ROOT / "tests/fixtures/people.html").read_text())
    yield page
    page.close()


def test_grouping_relationships_and_clipping(page):
    snapshot = page.evaluate(SCRIPT)
    people = {p["url"]: p for p in snapshot["people"]}
    alice = people["https://www.linkedin.com/in/alice"]
    bob = people["https://www.linkedin.com/in/bob"]
    assert "Previously Account Executive" in alice["evidence"]
    assert (alice["pending"], alice["following"]) == (False, False)
    assert (bob["pending"], bob["following"]) == (True, True)
    assert "https://www.linkedin.com/in/dave" not in people
    assert not any(a["label"] == "Invisible" for a in snapshot["actions"])
    groups = {g["id"]: g for g in snapshot["groups"]}
    for action in snapshot["actions"]:
        if action["label"] == "Connect" and action.get("context") == alice["group"]:
            assert groups[action["context"]]["profile_urls"] == [alice["url"]]


def test_nested_scroll_and_recycled_node_guards(page):
    snapshot = page.evaluate(SCRIPT)
    nested = next(r for r in snapshot["scroll_regions"] if r["node"])
    action = next(a for a in snapshot["actions"] if a["id"] == nested["id"] + "_down")
    assert action["delta"] == int(nested["viewport"] * 0.67)
    assert str(action["node"]) in snapshot["guards"]
    target = next(a for a in snapshot["actions"] if a["label"] == "Connect")
    original_guard = snapshot["guards"][str(target["node"])]
    page.eval_on_selector("#alice a", "e => e.href = 'https://www.linkedin.com/in/recycled'")
    guard = page.evaluate("id => window.__jevFast.guard(window.__jevFast.nodes.get(id))", target["node"])
    assert original_guard != guard
    page.eval_on_selector("#results", "e => e.scrollTop = 200")
    assert snapshot["page_key"] != page.evaluate("window.__jevFast.pageKey()")
    after = page.evaluate(SCRIPT)
    assert any(p["url"].endswith("/dave") for p in after["people"])


def test_modal_priority_and_unknown_states(page):
    page.evaluate("""() => {
      const dialog = document.createElement('dialog');
      dialog.innerHTML = '<h2>Send invitation</h2><button>Send without a note</button>';
      document.body.append(dialog); dialog.showModal();
    }""")
    snapshot = page.evaluate(SCRIPT)
    clicks = [a["label"] for a in snapshot["actions"] if a["kind"] == "click"]
    assert clicks == ["Send without a note"]
    assert snapshot["groups"][0]["type"] == "dialog"
    assert not any(a["id"] == "scroll_down" for a in snapshot["actions"])
    page.evaluate("document.querySelector('dialog').remove()")
    page.eval_on_selector_all("#alice button", "es => es.forEach(e => e.remove())")
    person = next(p for p in page.evaluate(SCRIPT)["people"] if p["url"].endswith("/alice"))
    assert person["pending"] is None
    assert person["following"] is None
    assert person["connected"] is None


def test_budget_discloses_omitted_context(page):
    page.set_content("<main><article><a href='https://www.linkedin.com/in/alice'>Alice</a>"
                     "<p style='font-size:1px'>" + "Engineer " * 1800 + "</p>"
                     "<button>Connect</button></article></main>")
    snapshot = page.evaluate(SCRIPT)
    assert snapshot["truncation"]["groups_with_omitted_text"]
    assert len(snapshot["groups"][0]["text"]) <= 2200


def test_profile_menu_dialog_and_verified_outcome(page):
    # All LinkedIn-looking URLs are intercepted; no external request is sent.
    page.route("**/*", lambda route: route.fulfill(body="<html><body></body></html>", content_type="text/html"))
    page.goto("https://www.linkedin.com/in/alice/")
    page.set_content("""<main><section><h1>Alice Engineer</h1><p>Engineer at Example</p>
      <button aria-controls='more-menu'>More</button><button>Follow</button></section></main>
      <div id='more-menu' role='menu' hidden><button role='menuitem'>Connect</button></div>""")
    snapshot = page.evaluate(SCRIPT)
    assert snapshot["profile"]["pending"] is None
    page.eval_on_selector("#more-menu", "e => e.hidden = false")
    snapshot = page.evaluate(SCRIPT)
    assert snapshot["profile"]["pending"] is False
    assert snapshot["profile"]["connected"] is False
    assert snapshot["profile"]["associated_menu_evidence"] == ["Connect"]
    page.evaluate("""() => {
      document.querySelector('#more-menu').hidden = true;
      const dialog = document.createElement('dialog');
      dialog.innerHTML = '<h2>Connect with Alice Engineer</h2><button>Send without a note</button>';
      document.body.append(dialog); dialog.showModal();
    }""")
    snapshot = page.evaluate(SCRIPT)
    # No inference from absence; the runner must retain the verified pre-dialog state.
    assert snapshot["profile"]["pending"] is None
    assert snapshot["groups"][0]["kind"] == "dialog"
    page.evaluate("document.querySelector('dialog').remove()")
    page.eval_on_selector("section", "e => e.insertAdjacentHTML('beforeend', '<button>Pending</button>')")
    snapshot = page.evaluate(SCRIPT)
    assert snapshot["profile"]["pending"] is True
    assert snapshot["profile"]["following"] is False


@pytest.mark.parametrize("wrapper", ["section", "div"])
def test_people_also_viewed_exclusion_preserves_neighbors(page, wrapper):
    page.set_content(f"""<main><section><h1>Profile</h1><p>Engineer at Fleet</p><button>Connect</button></section>
      <{wrapper} id='recommendations'><div><h2>pEoPlE aLsO vIeWeD</h2></div>
        <div style='height:100px;overflow:auto'><article><h3>
          <a href='https://www.linkedin.com/in/recommended'>Unrelated Recommended Person</a></h3>
          <button>Follow recommendation</button><div role='status'>Recommendation status</div>
          <div style='height:300px'>Recommendation details</div></article></div></{wrapper}>
      <section><h2>Experience</h2><p>Current role at Mercor</p><button>Expand experience</button></section></main>""")
    snapshot = page.evaluate(SCRIPT)
    assert snapshot["excluded_profile_urls"] == ["https://www.linkedin.com/in/recommended"]
    for field in ("text", "actions", "groups", "people", "notices", "scroll_regions"):
        assert "recommend" not in str(snapshot[field]).lower()
        assert "people also viewed" not in str(snapshot[field]).lower()
    assert "Current role at Mercor" in snapshot["text"]
    assert any(a["label"] == "Expand experience" for a in snapshot["actions"])
    # DOM pruning is virtual, never a mutation of the actual page.
    assert page.locator("#recommendations").count() == 1


def test_shared_wrapper_and_offscreen_recommendation_heading(page):
    page.set_content("""<main><div><h2 style='margin-top:0'>People also viewed</h2>
      <div style='height:90px'></div><article><a href='https://www.linkedin.com/in/recommended'>Noise</a>
      <button>Noise button</button></article><h2>Experience</h2><p>Engineer at Fleet</p>
      <button>Keep this button</button></div><div style='height:1200px'></div></main>""")
    page.evaluate("scrollTo(0, 80)")
    snapshot = page.evaluate(SCRIPT)
    assert "Noise" not in snapshot["text"]
    assert not any(a["label"] == "Noise button" for a in snapshot["actions"])
    assert any(a["label"] == "Keep this button" for a in snapshot["actions"])
    assert "Engineer at Fleet" in snapshot["text"]
    assert snapshot["excluded_profile_urls"] == ["https://www.linkedin.com/in/recommended"]


def test_company_header_is_not_incidental_employee_profile(page):
    page.route("**/*", lambda route: route.fulfill(body="<html><body></body></html>", content_type="text/html"))
    page.goto("https://www.linkedin.com/company/fleet/")
    page.set_content("""<main><div><section><div><h1>Fleet</h1><p>Software Development</p></div>
      <div><a href='https://www.linkedin.com/in/manoj'>Manoj works here</a></div>
      <button>Follow</button><button>Visit website</button></section>
      <section><h2>People</h2><article><h3><a href='https://www.linkedin.com/in/alice'>Alice</a></h3>
      <p>Engineer at Fleet</p><button>Connect</button></article></section></div></main>""")
    snapshot = page.evaluate(SCRIPT)
    assert all(not p["url"].endswith("/manoj") for p in snapshot["people"])
    assert any(p["url"].endswith("/alice") for p in snapshot["people"])
    company = next(g for g in snapshot["groups"] if g["kind"] == "company")
    assert "Fleet" in company["text"] and "Software Development" in company["text"]
    assert "Visit website" not in company["text"]
    assert "Follow" not in company["text"]
    assert any(a["label"] == "Visit website" for a in snapshot["actions"])


def test_deep_company_h2_header_does_not_capture_posts_or_document_controls(page):
    page.route("**/*", lambda route: route.fulfill(body="<html><body></body></html>", content_type="text/html"))
    page.goto("https://www.linkedin.com/company/fleet-so/posts/")
    employee = "<a href='https://www.linkedin.com/in/manoj'>Manoj works here</a>"
    for _ in range(5):
        employee = f"<div>{employee}</div>"
    header = f"""<div><h2>Fleet AI, Inc.</h2><p>Software Development, 51-200 employees</p>
      {employee}<button>Following</button><a href='/company/fleet-so/people/'>People</a></div>"""
    for _ in range(5):
        header = f"<div>{header}</div>"
    page.set_content(f"""<title>Fleet AI, Inc.: Posts | LinkedIn</title><div><main><div><section><div>
      {header}<div><h2>Feed post</h2><p>Founding engineer's journey.</p>
      <button>Sort by: Top</button><button>Open control menu for post by Fleet AI, Inc.</button></div>
      <section><h2>Employees</h2><article><a href='https://www.linkedin.com/in/alice'>Alice</a>
      <p>Engineer at Fleet</p><button>Connect</button></article></section></div></section></div></main>
      <button aria-label='Document overlay'></button></div>""")
    snapshot = page.evaluate(SCRIPT)
    groups = {g["id"]: g for g in snapshot["groups"]}
    assert any(g["kind"] == "company" and "Fleet AI, Inc." in g["text"] for g in groups.values())
    assert all(not person["url"].endswith("/manoj") for person in snapshot["people"])
    assert any(person["url"].endswith("/alice") for person in snapshot["people"])
    for label in ("Sort by: Top", "Document overlay"):
        action = next(a for a in snapshot["actions"] if a["label"] == label)
        group = groups[action["context"]]
        assert group["kind"] == "context"
        assert "Manoj works here" not in group["text"]
        assert "Software Development" not in group["text"]
