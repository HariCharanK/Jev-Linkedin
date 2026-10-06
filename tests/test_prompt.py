import json

from jev_linkedin.memory import Memory
from jev_linkedin.model import build_request
from jev_linkedin.prompt import compact_memory


def snapshot():
    return {
        "url": "https://www.linkedin.com/company/fleet-so/posts/",
        "title": "Fleet",
        "text": "Fleet\nSoftware Development\nPeople\nSearch more employees\nLoading…",
        "actions": [
            {
                "id": "e1",
                "node": 1,
                "kind": "click",
                "role": "link",
                "label": "People",
                "href": "https://www.linkedin.com/company/fleet-so/people/",
                "context": "g1",
            },
            {"id": "e2", "node": 2, "kind": "click", "role": "link", "label": "Search more employees", "context": "g1"},
        ],
        "groups": [{"id": "g1", "kind": "company", "text": "Fleet\nSoftware Development\nPeople"}],
        "people": [],
    }


def test_task_and_controls_have_one_authoritative_copy():
    goal = "UNIQUE_TASK_TOKEN"
    body = build_request(snapshot(), goal, {}, [])
    serialized = json.dumps(body)
    assert serialized.count(goal) == 1
    assert serialized.count('"label": "Search more employees"') == 1
    assert body["questions"]["click_target"]["criteria"] == {"1": "Element 1", "2": "Element 2"}
    assert body["state"]["page"]["additional_text"] == "Loading…"
    assert body["state"]["groups"]["g1"]["text"] == "Fleet\nSoftware Development"
    assert "people" not in body["state"]


def test_visible_memory_excludes_old_recommendations_but_keeps_unfinished_work():
    relevant = "https://www.linkedin.com/in/engineer/"
    recommendation = "https://www.linkedin.com/in/unrelated/"
    unfinished = "https://www.linkedin.com/in/unfinished/"
    page = {**snapshot(), "people": [{"url": relevant}], "excluded_profile_urls": [recommendation]}
    memory = {
        "profiles": [
            {"url": relevant, "invited": True, "headline": "Repeat"},
            {"url": recommendation, "headline": "Unrelated"},
        ],
        "recent_actions": [{"profile_url": recommendation, "label": "Unrelated", "outcome": "verified"}],
        "followup_required": [{"url": unfinished, "pending": True}],
        "invitation_count": 1,
    }
    result = compact_memory(memory, page)
    assert result["visible_profiles"] == [{"url": relevant, "invited": True}]
    assert result["unfinished_profiles"] == [{"url": unfinished, "pending": True}]
    assert recommendation not in json.dumps(result)


def test_ledger_fetches_visible_identity_beyond_recent_window():
    memory = Memory(":memory:")
    memory.record_observation({"people": [{"url": "https://www.linkedin.com/in/first/", "pending": True}]})
    memory.record_observation({"people": [{"url": "https://www.linkedin.com/in/last/", "pending": False}]})
    page = {**snapshot(), "people": [{"url": "https://www.linkedin.com/in/first/"}]}
    context = memory.context_for(page, limit=1)
    result = compact_memory(context, page)
    assert result["visible_profiles"][0]["pending"] is True
    memory.close()


def test_dropdown_reference_remains_resolvable():
    page = snapshot()
    page["actions"] = [
        {
            "id": "select",
            "node": 1,
            "kind": "select",
            "role": "combobox",
            "label": "Department → Engineering",
            "value": "eng",
            "current_value": "All",
        }
    ]
    body = build_request(page, "Choose engineering", {}, [])
    assert "1:1" in body["questions"]["select_target"]["criteria"]
    assert body["state"]["elements"]["1"]["options"][0]["index"] == "1:1"


def test_identical_contexts_remap_controls_without_merging_distinct_people():
    page = snapshot()
    page["groups"].append({**page["groups"][0], "id": "g2"})
    page["actions"][1]["context"] = "g2"
    body = build_request(page, "Find engineers", {}, [])
    assert len(body["state"]["groups"]) == 1
    assert body["state"]["elements"]["2"]["context"] == "g1"
    page["groups"][0].update(kind="person", profile_urls=["https://www.linkedin.com/in/a/"])
    page["groups"][1].update(kind="person", profile_urls=["https://www.linkedin.com/in/b/"])
    body = build_request(page, "Find engineers", {}, [])
    assert len(body["state"]["groups"]) == 2
