"""Jev chooses each action; code journals execution and checks observable constraints."""

import math
import re
import time
from copy import deepcopy
from dataclasses import dataclass

from .config import linkedin_url
from .memory import canonical_profile_url

RESTRICTION = re.compile(
    r"temporarily restricted|account (?:is |has been )?restricted|weekly invitation limit|"
    r"security verification|verify your identity|unusual activity|captcha|security check",
    re.I,
)
SEND = re.compile(r"\bsend\b", re.I)
CONNECT = re.compile(r"^(?:connect|invite)(?:\b|$)", re.I)
FORBIDDEN = re.compile(
    r"^(?:message|follow|withdraw|like|repost|share|comment|post|subscribe|purchase|buy|"
    r"sign out|log out|delete|remove connection|save|report|block)\b",
    re.I,
)


@dataclass
class Result:
    status: str
    reason: str
    steps: int
    invitations: int
    decision: dict | None = None


def identity(page):
    return (page.get("profile") or {}).get("url")


def enrich(action, page):
    action = dict(action)
    if identity(page):
        action["profile_url"] = identity(page)
    if action.get("kind") == "click" and SEND.search(action.get("label", "")):
        action["effect"] = "invite"
    return action


def guard(action, page, decision, task, memory):
    """Reject actions outside the requested scope; never choose a replacement action."""
    if not linkedin_url(page.get("url", "")):
        return "Navigation left LinkedIn; inspect before continuing"
    if action.get("href") and not linkedin_url(action["href"]):
        return "Target leaves LinkedIn"
    label = action.get("label", "").strip().lstrip("+ ")
    profile = page.get("profile") or {}
    is_invite = action.get("effect") == "invite" or bool(CONNECT.search(label))
    is_unfollow = bool(re.match(r"^(following|unfollow)\b", label, re.I))
    if is_invite or is_unfollow:
        group = next((g for g in page.get("groups", []) if g.get("id") == action.get("context")), {})
        related = {canonical_profile_url(u) for u in group.get("profile_urls", [])}
        related.discard(None)
        if related and related != {canonical_profile_url(profile.get("url"))}:
            return "Relationship control belongs to a different profile or ambiguous group"
    unfinished = memory.context().get("followup_required", [])
    if unfinished and (is_invite or action.get("kind") == "back" or action.get("href")):
        return "Finish verifying the invitation and unfollow before leaving this profile"
    if action.get("kind") == "click" and FORBIDDEN.search(label):
        return "Action is outside the connection-without-following task"
    if is_invite:
        if not profile.get("url"):
            return "Invitation actions require a full identified profile"
        if memory.invitation_count() >= task.max_invitations:
            return "Invitation cap reached"
        if profile.get("pending") is not False or profile.get("connected") is not False:
            return "Existing relationship is pending, connected, or not established by observation"
        if decision.get("eligibility") != "eligible":
            return "Jev has not classified the current role and company as eligible"
        confidence = decision.get("eligibility_confidence", 0)
        if not valid_confidence(confidence, task.min_confidence):
            return "Eligibility confidence below threshold"
        if action.get("effect") == "invite" and "without a note" not in label.casefold():
            return "Only Send without a note is supported"
    if is_unfollow:
        known = memory.profile(profile.get("url", "")) if profile.get("url") else None
        if not known or not known.get("invited"):
            return "Unfollow is limited to a profile invited by this ledger"
    if action.get("kind") == "fill":
        if decision.get("text") not in task.text_candidates:
            return "Text must be a configured candidate"
        if page.get("active_dialog") or any(g.get("kind") == "dialog" for g in page.get("groups", [])):
            return "Invitation dialog fields are not filled; note/email requirements must be skipped"
    return None


def valid_confidence(value, threshold):
    return type(value) in (float, int) and math.isfinite(value) and threshold <= value <= 1


class Runner:
    def __init__(self, task, browser, model, memory):
        self.task, self.browser, self.model, self.memory = task, browser, model, memory
        self.profile_evidence = None

    def carry_dialog_evidence(self, page):
        """Carry only this run's immediately preceding same-profile evidence into its dialog."""
        page = deepcopy(page)
        profile = page.get("profile") or {}
        dialog = any(g.get("kind", g.get("type")) == "dialog" for g in page.get("groups", []))
        if not dialog:
            self.profile_evidence = (
                deepcopy(profile) if (profile.get("pending") is False and profile.get("connected") is False) else None
            )
        elif self.profile_evidence and all(
            profile.get(key) and profile.get(key) == self.profile_evidence.get(key) for key in ("url", "name")
        ):
            for key in ("pending", "connected"):
                if profile.get(key) is None:
                    profile[key] = self.profile_evidence[key]
            profile["relationship_evidence_source"] = "same-profile observation immediately before dialog"
        else:
            self.profile_evidence = None
        return page

    def run(self, execute=False):
        from .browser import StalePage

        steps, unchanged = 0, 0
        if self.memory.unresolved_actions():
            return self.result(
                "needs_review", "Unresolved previous action; inspect and reconcile before resuming", steps
            )
        try:
            page = self.browser.observe(screenshot=False)
        except Exception:
            return self.result("needs_review", "Initial browser observation failed", steps)
        for _ in range(self.task.max_steps):
            page = self.carry_dialog_evidence(page)
            self.memory.record_observation(page)
            context = self.memory.context_for(page)
            if self.memory.invitation_count() >= self.task.max_invitations and not context.get(
                "followup_required_count", 0
            ):
                return self.result(
                    "complete", "Invitation cap reached and all invited profiles verified unfollowed", steps
                )
            notices = "\n".join(
                str(n.get("text", "")) if isinstance(n, dict) else str(n) for n in page.get("notices", [])
            )
            if RESTRICTION.search(notices + "\n" + page.get("title", "") + "\n" + page.get("text", "")):
                return self.result("blocked", "Account warning or verification challenge observed", steps)
            if not linkedin_url(page.get("url", "")):
                return self.result("blocked", "Current page is outside LinkedIn", steps)
            try:
                decision = self.model.choose(page, self.task.goal, context, self.task.text_candidates)
            except Exception:
                return self.result("needs_review", "Jev decision failed; no action dispatched", steps)
            if not valid_confidence(decision.get("confidence"), self.task.min_confidence) or (
                decision.get("target_confidence") is not None
                and not valid_confidence(decision["target_confidence"], self.task.min_confidence)
            ):
                return self.result("needs_review", "Decision confidence below threshold", steps, decision)
            choice = decision.get("choice")
            if choice in {"DONE", "BLOCKED"}:
                if context.get("followup_required_count", 0):
                    return self.result(
                        "needs_review", "An invitation still needs unfollow verification", steps, decision
                    )
                return self.result(
                    "stopped", f"Jev selected {choice}; inspect ledger for verified outcomes", steps, decision
                )
            action = next((a for a in page["actions"] if a["id"] == choice), None)
            if action is None:
                return self.result("needs_review", "Decision did not reference an observed action", steps)
            raw_action = action
            action = enrich(action, page)
            reason = guard(action, page, decision, self.task, self.memory)
            if reason:
                return self.result("blocked", reason, steps, decision)
            if not execute:
                return self.result("preview", "Selected one action without executing it", steps, decision)
            action_id = self.memory.begin_action(action, page)
            try:
                self.browser.act(raw_action, page, text=decision.get("text"))
            except StalePage:
                self.memory.finish_action(action_id, "stale")
                try:
                    page = self.browser.observe(screenshot=False)
                except Exception:
                    return self.result("needs_review", "Could not refresh stale page", steps)
                unchanged += 1
                if unchanged >= self.task.no_progress_limit:
                    return self.result("blocked", "Page remained stale repeatedly", steps)
                continue
            except Exception:
                self.memory.finish_action(action_id, "uncertain", error="Browser dispatch outcome unknown")
                return self.result("needs_review", "Action may have executed; it will not be retried", steps)
            steps += 1
            self.memory.finish_action(action_id, "executed")
            try:
                after = self.browser.observe(screenshot=False)
                if action.get("effect") == "invite":
                    deadline = time.monotonic() + 2
                    while (
                        identity(after) == identity(page)
                        and (after.get("profile") or {}).get("pending") is not True
                        and time.monotonic() < deadline
                    ):
                        time.sleep(0.1)
                        after = self.browser.observe(screenshot=False)
            except Exception:
                self.memory.finish_action(action_id, "uncertain", error="Post-action observation failed")
                return self.result("needs_review", "Action executed but resulting page could not be read", steps)
            self.memory.record_observation(after)
            if action.get("effect") == "invite":
                self.profile_evidence = None
                if identity(after) != identity(page) or (after.get("profile") or {}).get("pending") is not True:
                    self.memory.finish_action(action_id, "uncertain", page=after, error="Invitation not verified")
                    return self.result("needs_review", "Invitation outcome not verified; no retry", steps)
            self.memory.finish_action(action_id, "verified", page=after)
            unchanged = unchanged + 1 if after.get("fingerprint") == page.get("fingerprint") else 0
            page = after
            if unchanged >= self.task.no_progress_limit:
                return self.result("blocked", "Repeated actions revealed no page progress", steps)
        return self.result("stopped", "Step budget reached", steps)

    def result(self, status, reason, steps, decision=None):
        return Result(status, reason, steps, self.memory.invitation_count(), decision)
