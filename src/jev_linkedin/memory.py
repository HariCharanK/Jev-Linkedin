"""Durable observations and write-ahead actions for a single browser workflow.

An executed click is not evidence of a sent invitation. Invitation counts only
advance on an explicit false -> true pending transition associated with an
invitation intent, and count each profile once for the lifetime of the database.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import uuid4


def canonical_profile_url(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value.strip())
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https") or not (host == "linkedin.com" or host.endswith(".linkedin.com")):
            return None
        match = re.fullmatch(r"/in/([^/]+)/?", parsed.path)
        if not match or unquote(match[1]) in (".", ".."):
            return None
        return "https://www.linkedin.com/in/" + match[1].lower() + "/"
    except ValueError:
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _profiles(page: dict):
    # A detailed profile overrides a result card when both are present.
    for person in page.get("people", []) or []:
        if isinstance(person, dict):
            yield person
    if isinstance(page.get("profile"), dict):
        yield page["profile"]


class Memory:
    def __init__(self, path):
        if str(path) != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
            path = str(Path(path).expanduser())
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS observations (
                id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL,
                page TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS profiles (
                url TEXT PRIMARY KEY, data TEXT NOT NULL,
                observation_id INTEGER NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS actions (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, action TEXT NOT NULL,
                observation_id INTEGER NOT NULL, profile_url TEXT,
                before_state TEXT NOT NULL, outcome TEXT NOT NULL,
                error TEXT, after_observation_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS invitations (
                profile_url TEXT PRIMARY KEY, action_id TEXT NOT NULL,
                observation_id INTEGER NOT NULL, verified_at TEXT NOT NULL
            );
        """)

    def close(self):
        self.db.close()

    def _record(self, page: dict) -> int:
        timestamp = _now()
        observation_id = self.db.execute(
            "INSERT INTO observations(observed_at,page) VALUES (?,?)",
            (timestamp, _json(page)),
        ).lastrowid
        for person in _profiles(page):
            url = canonical_profile_url(person.get("url"))
            if url is None:
                continue
            existing = self.profile(url) or {"url": url}
            evidence = existing.setdefault("evidence", {})
            for key in ("name", "headline", "pending", "following", "connected"):
                value = person.get(key)
                if key in ("pending", "following", "connected"):
                    if not isinstance(value, bool):
                        continue
                elif not isinstance(value, str) or not value:
                    continue
                existing[key] = value
                evidence[key] = observation_id
            for key in ("pending", "following", "connected"):
                existing.setdefault(key, None)
            existing["complete"] = (
                existing["pending"] is True
                and existing["following"] is False
                and evidence.get("pending") == evidence.get("following")
            )
            existing["last_observation_id"] = observation_id
            existing["updated_at"] = timestamp
            self.db.execute(
                "INSERT INTO profiles VALUES (?,?,?,?) ON CONFLICT(url) DO UPDATE SET "
                "data=excluded.data,observation_id=excluded.observation_id,"
                "updated_at=excluded.updated_at",
                (url, _json(existing), observation_id, timestamp),
            )
        return observation_id

    def record_observation(self, page):
        with self.db:
            return self._record(page)

    def profile(self, url):
        row = self.db.execute("SELECT data FROM profiles WHERE url=?", (canonical_profile_url(url),)).fetchone()
        if row is None:
            return None
        result = json.loads(row["data"])
        result["invited"] = (
            self.db.execute("SELECT 1 FROM invitations WHERE profile_url=?", (result["url"],)).fetchone() is not None
        )
        return result

    def begin_action(self, action, page):
        """Commit intent before the caller attempts any browser mutation."""
        current = page.get("profile") or {}
        url = (
            canonical_profile_url(action.get("profile_url"))
            or canonical_profile_url(current.get("url"))
            or canonical_profile_url(action.get("href"))
        )
        # Use current evidence, never infer a pre-action state from old history.
        before = {}
        for person in _profiles(page):
            if url and canonical_profile_url(person.get("url")) == url:
                before = person
        action_id, timestamp = str(uuid4()), _now()
        with self.db:
            observation_id = self._record(page)
            self.db.execute(
                "INSERT INTO actions VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    action_id,
                    timestamp,
                    timestamp,
                    _json(action),
                    observation_id,
                    url,
                    _json(before),
                    "started",
                    None,
                    None,
                ),
            )
        return action_id

    def finish_action(self, action_id, outcome, page=None, error=None):
        if outcome not in {"executed", "verified", "failed", "uncertain", "stale"}:
            raise ValueError(f"Unknown action outcome: {outcome}")
        row = self.db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        if row is None:
            raise KeyError(action_id)
        if row["outcome"] in {"verified", "failed", "stale"} and outcome != row["outcome"]:
            raise ValueError("Cannot overwrite a resolved action")
        with self.db:
            observed = self._record(page) if page is not None else None
            action = json.loads(row["action"])
            before = json.loads(row["before_state"])
            after = {}
            for person in _profiles(page or {}):
                if row["profile_url"] and canonical_profile_url(person.get("url")) == row["profile_url"]:
                    after = person
            label = str(action.get("label", "")).strip().lower()
            invite = action.get("effect") == "invite" or (
                action.get("kind") == "click" and label in {"connect", "send", "send invitation", "send without a note"}
            )
            if (
                outcome in {"executed", "verified", "uncertain"}
                and invite
                and before.get("pending") is False
                and after.get("pending") is True
            ):
                self.db.execute(
                    "INSERT OR IGNORE INTO invitations VALUES (?,?,?,?)",
                    (row["profile_url"], action_id, observed, _now()),
                )
            self.db.execute(
                "UPDATE actions SET updated_at=?,outcome=?,error=?,after_observation_id=? WHERE id=?",
                (_now(), outcome, str(error) if error is not None else None, observed, action_id),
            )

    def unresolved_actions(self):
        result = []
        for row in self.db.execute(
            "SELECT * FROM actions WHERE outcome IN ('started','executed','uncertain') ORDER BY created_at,id"
        ):
            item = dict(row)
            item["action"] = json.loads(item["action"])
            item["before_state"] = json.loads(item["before_state"])
            result.append(item)
        return result

    def invitation_count(self):
        return self.db.execute("SELECT count(*) FROM invitations").fetchone()[0]

    def context(self, limit=30):
        if not isinstance(limit, int) or isinstance(limit, bool) or not 0 <= limit <= 1000:
            raise ValueError("Context limit must be an integer from 0 to 1000")
        total = self.db.execute("SELECT count(*) FROM profiles").fetchone()[0]
        people = [
            self.profile(row[0])
            for row in self.db.execute("SELECT url FROM profiles ORDER BY observation_id DESC,url LIMIT ?", (limit,))
        ]
        unresolved = self.unresolved_actions()
        recent_actions = []
        for row in self.db.execute(
            "SELECT action,outcome,profile_url FROM actions ORDER BY created_at DESC,id DESC LIMIT 10"
        ):
            action = json.loads(row["action"])
            recent_actions.append(
                {
                    "action": action,
                    "label": action.get("label", ""),
                    "outcome": row["outcome"],
                    "profile_url": row["profile_url"],
                }
            )
        recent_actions.reverse()
        followups = [
            self.profile(row[0])
            for row in self.db.execute(
                "SELECT p.url FROM profiles p JOIN invitations i ON i.profile_url=p.url "
                "WHERE json_extract(p.data,'$.complete') != 1 ORDER BY i.verified_at,p.url"
            )
        ]
        # A separate list keeps unfinished invitations visible even after many
        # unrelated result cards displace them from the recent-profile window.
        followup_limit = max(1, limit)
        return {
            "profiles": people,
            "total_profiles": total,
            "omitted_profiles": max(0, total - len(people)),
            "invitation_count": self.invitation_count(),
            "unresolved_actions": unresolved[-limit:] if limit else [],
            "unresolved_count": len(unresolved),
            "recent_actions": recent_actions,
            "followup_required": followups[:followup_limit],
            "followup_required_count": len(followups),
            "omitted_followup_required": max(0, len(followups) - followup_limit),
        }

    def context_for(self, page, limit=30):
        """Include known visible profiles even if displaced from the recent ledger window."""
        context = self.context(limit)
        urls = {canonical_profile_url(p.get("url")) for p in _profiles(page)}
        urls.update(canonical_profile_url(a.get("href")) for a in page.get("actions", []))
        current = {p["url"]: p for p in context["profiles"]}
        for url in urls - {None}:
            profile = self.profile(url)
            if profile:
                current[url] = profile
        context["profiles"] = list(current.values())
        return context
