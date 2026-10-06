"""Compact model context while keeping execution evidence in the original snapshot."""

import re

from .memory import canonical_profile_url


def normalized(value):
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def lines_without(text, represented):
    result, seen = [], set()
    for line in str(text or "").splitlines():
        key = normalized(line)
        if key and key not in represented and key not in seen:
            result.append(line.strip())
            seen.add(key)
    return "\n".join(result)


def compact_memory(memory, page):
    visible = {canonical_profile_url(p.get("url")) for p in page.get("people", [])}
    visible.add(canonical_profile_url((page.get("profile") or {}).get("url")))
    visible.update(canonical_profile_url(a.get("href")) for a in page.get("actions", []))
    visible.discard(None)
    excluded = {canonical_profile_url(u) for u in page.get("excluded_profile_urls", [])} - visible
    keys = ("url", "pending", "following", "connected", "complete", "invited")

    def status(profile):
        return {k: profile[k] for k in keys if k in profile}

    people = [status(p) for p in memory.get("profiles", []) if canonical_profile_url(p.get("url")) in visible]
    followups = [
        status(p) for p in memory.get("followup_required", []) if canonical_profile_url(p.get("url")) not in excluded
    ]
    recent = []
    for item in memory.get("recent_actions", [])[-10:]:
        if canonical_profile_url(item.get("profile_url")) in excluded:
            continue
        action = item.get("action") or {}
        recent.append(
            {
                "action": item.get("label", action.get("label", "")),
                "outcome": item.get("outcome"),
                "profile_url": item.get("profile_url"),
            }
        )
    result = {
        "invitation_count": memory.get("invitation_count", 0),
        "visible_profiles": people,
        "unfinished_profiles": followups,
        "recent_actions": recent,
    }
    if memory.get("unresolved_count"):
        result["unresolved_action_count"] = memory["unresolved_count"]
    omitted = memory.get("omitted_followup_required", 0)
    if omitted:
        result["omitted_unfinished_profiles"] = omitted
    return result


def compact_state(page, goal, memory, elements):
    """One control table, one goal, descriptive groups, and only residual page text."""
    labels = {normalized(e.get("label", "")) for e in elements}
    profile = {
        k: v
        for k, v in (page.get("profile") or {}).items()
        if k
        in {
            "url",
            "name",
            "headline",
            "pending",
            "following",
            "connected",
            "relationship_evidence_source",
            "omitted_characters",
        }
    }
    represented = set(labels)
    represented.update(
        normalized(line) for key in ("name", "headline") for line in str(profile.get(key, "")).splitlines()
    )
    groups, group_aliases, seen_groups = {}, {}, {}
    for group in page.get("groups", []):
        gid = group.get("id")
        if not gid:
            continue
        # Preserve per-person descriptions even when another person shares a role.
        text = lines_without(group.get("text", ""), labels)
        group_data = {"kind": group.get("kind", group.get("type", "context"))}
        if text:
            group_data["text"] = text
            represented.update(normalized(line) for line in text.splitlines())
        if group.get("omitted_characters"):
            group_data["omitted_characters"] = group["omitted_characters"]
        if len(group_data) == 1 and group_data["kind"] == "context":
            group_aliases[gid] = None
            continue
        # Merge identical contexts only when their profile identities also agree.
        signature = (group_data["kind"], text, tuple(sorted(group.get("profile_urls", []))))
        if signature in seen_groups:
            group_aliases[gid] = seen_groups[signature]
        else:
            groups[gid] = group_data
            seen_groups[signature] = gid
            group_aliases[gid] = gid
    controls = {}
    for element in elements:
        item = {k: v for k, v in element.items() if k not in {"index", "id"} and v not in ("", None)}
        if "context" in item:
            context = group_aliases.get(item["context"])
            if context:
                item["context"] = context
            else:
                item.pop("context")
        if "options" in item:
            item["options"] = [{"index": o["index"], "label": o["label"]} for o in item["options"]]
        controls[element["index"]] = item
    notices = page.get("notices", [])
    represented.update(normalized(n) for n in notices if isinstance(n, str))
    state = {
        "task": goal,
        "page": {"url": page["url"], "title": page["title"]},
        "elements": controls,
        "groups": groups,
        "memory": compact_memory(memory, page),
    }
    residual = lines_without(page.get("text", ""), represented)
    if residual:
        state["page"]["additional_text"] = residual
    if profile:
        state["profile"] = profile
    if notices:
        state["notices"] = notices
    if page.get("truncation"):
        state["truncation"] = page["truncation"]
    if page.get("scroll_regions"):
        state["scroll_regions"] = [
            {k: r[k] for k in ("id", "y", "height", "viewport") if k in r} for r in page["scroll_regions"]
        ]
    if page.get("excluded_sections"):
        state["excluded_sections"] = page["excluded_sections"]
    return state
