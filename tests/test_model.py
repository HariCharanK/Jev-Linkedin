import pytest

from jev_linkedin import model


def answer(ids, selected):
    return {"choice": selected, "confidence": 1, "probabilities": {i: float(i == selected) for i in ids}}


def page():
    return {
        "url": "https://fixture.test",
        "title": "Fixture",
        "text": "Search",
        "groups": [{"name": "Alice"}],
        "actions": [{"id": "fill1", "kind": "fill", "node": 1, "label": "Search", "context": "People"}],
    }


def test_jev_selects_supplied_text_and_receives_memory(monkeypatch):
    def post(url, key, body):
        assert body["state"]["memory"]["invitation_count"] == 0
        assert body["state"]["task"] == "search"
        assert body["state"]["groups"] == {}
        q = body["questions"]
        return {
            "answers": {
                "operation": answer(q["operation"]["criteria"], "TYPE_TEXT"),
                "type_text_target": answer(["1"], "1"),
                "text_value": answer(["0", "1"], "1"),
            }
        }

    monkeypatch.setattr(model, "post_json", post)
    result = model.JevClient("dummy").choose(page(), "search", {"visited": ["x"]}, ["Alpha", "Beta"])
    assert result["choice"] == "fill1"
    assert result["text"] == "Beta"


def test_no_text_candidates_excludes_typing(monkeypatch):
    def post(url, key, body):
        q = body["questions"]
        assert "TYPE_TEXT" not in q["operation"]["criteria"]
        assert "text_value" not in q
        return {"answers": {"operation": answer(q["operation"]["criteria"], "BLOCKED")}}

    monkeypatch.setattr(model, "post_json", post)
    assert model.JevClient("dummy").choose(page(), "search", {}, [])["choice"] == "BLOCKED"


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        [],
        {"choice": "x", "confidence": float("nan"), "probabilities": {"x": 1}},
        {"choice": "x", "confidence": 1, "probabilities": {"x": 0.1, "y": 0.9}},
    ],
)
def test_bad_choice_rejected(invalid):
    with pytest.raises(ValueError, match="Invalid"):
        model.validate_choice(invalid, ["x", "y"])


def test_consumed_text_head_is_validated(monkeypatch):
    def post(url, key, body):
        return {
            "answers": {
                "operation": answer(body["questions"]["operation"]["criteria"], "TYPE_TEXT"),
                "type_text_target": answer(["1"], "1"),
                "text_value": answer(["0"], "injected"),
            }
        }

    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError):
        model.JevClient("dummy").choose(page(), "search", {}, ["Alpha"])


def test_profile_requires_validated_eligibility(monkeypatch):
    p = {**page(), "profile": {"url": "https://fixture.test/in/a", "headline": "Engineer"}}

    def post(url, key, body):
        assert "eligibility" in body["questions"]
        return {"answers": {"operation": answer(body["questions"]["operation"]["criteria"], "BLOCKED")}}

    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError):
        model.JevClient("dummy").choose(p, "engineering only", {}, [])
