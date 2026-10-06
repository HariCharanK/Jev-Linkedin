"""Guarded Browser Harness executor; adapted from jev-ultrafast (MIT, vendor license)."""

import hashlib
import json
import sys
import time
from pathlib import Path

from browser_harness.admin import ensure_daemon
from browser_harness.helpers import cdp


class StalePage(ValueError):
    """Known stale observation before any input was sent; safe to reobserve."""


class Browser:
    def __init__(self, url):
        self.target = None
        self._initial_navigation = True
        self._after_input = False
        try:
            ensure_daemon()
            self.target = cdp("Target.createTarget", url="about:blank", background=True)["targetId"]
            self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
            self.call("Emulation.setDeviceMetricsOverride", width=1120, height=780, deviceScaleFactor=1, mobile=False)
            self.call("Emulation.setFocusEmulationEnabled", enabled=True)
            self.call("Page.navigate", url=url)
        except Exception:
            self.close()
            raise RuntimeError("Browser connection failed; check Browser Harness setup.") from None

    def call(self, method, **params):
        try:
            return cdp(method, session_id=self.session, **params)
        except Exception:
            raise RuntimeError("Browser command failed; inspect state before retrying.") from None

    def evaluate(self, expression):
        response = self.call("Runtime.evaluate", expression=expression, returnByValue=True)
        if response.get("exceptionDetails"):
            raise RuntimeError("Browser evaluation interrupted; inspect state before retrying.")
        return response.get("result", {}).get("value")

    def observe(self, screenshot=False):
        # Read-only bounded stabilization, never a retry of browser input. A settled
        # snapshot is not proof of an invitation outcome; the runner verifies that.
        source = Path(__file__).with_name("snapshot.js").read_text()
        deadline = time.monotonic() + 2.0
        previous, info = None, None
        if getattr(self, "_after_input", False):
            time.sleep(0.15)
            self._after_input = False
        while time.monotonic() < deadline:
            try:
                ready = self.evaluate("document.readyState")
                current = self.evaluate(source) if ready in {"interactive", "complete"} else None
            except RuntimeError:
                current = None  # Read-only navigation/context interruption.
            if isinstance(current, dict) and not (
                getattr(self, "_initial_navigation", False) and current.get("url") == "about:blank"
            ):
                if previous is not None and current.get("marker") == previous.get("marker"):
                    info = current
                    self._initial_navigation = False
                    break
                previous = current
            else:
                previous = None
            time.sleep(0.1)
        if info is None:
            raise StalePage("Document did not settle within the observation budget.")
        history = self.call("Page.getNavigationHistory")
        index = history.get("currentIndex", 0)
        entries = history.get("entries", [])
        if 0 < index < len(entries) and entries[index - 1].get("url") != "about:blank":
            info["actions"].append(
                {
                    "id": "history_back",
                    "kind": "back",
                    "label": "Back to previous page",
                    "entry_id": entries[index - 1]["id"],
                    "current_entry_id": entries[index]["id"],
                }
            )
        content = {k: info.get(k) for k in ("url", "text", "actions", "scroll")}
        info["fingerprint"] = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
        if screenshot:
            info["screenshot"] = self.call("Page.captureScreenshot", format="jpeg", quality=72)["data"]
        return info

    def fresh(self, page, action=None):
        if action and "node" in action:
            node = action["node"]
            if type(node) is not int or str(node) not in page.get("guards", {}):
                return False
            current = self.evaluate(
                "(() => { const c=window.__jevFast; "
                + f"return c ? [c.pageKey(),c.guard(c.nodes.get({node}))] : null; "
                + "})()"
            )
            return current == [page["page_key"], page["guards"][str(node)]]
        current = self.evaluate("window.__jevFast?.pageKey() ?? null")
        if current != page.get("page_key"):
            return False
        if action and action["kind"] == "back":
            history = self.call("Page.getNavigationHistory")
            i = history.get("currentIndex", 0)
            entries = history.get("entries", [])
            return (
                0 < i < len(entries)
                and entries[i]["id"] == action["current_entry_id"]
                and entries[i - 1]["id"] == action["entry_id"]
            )
        # Full snapshot marker is required for global scroll/wait context.
        state = self.evaluate(Path(__file__).with_name("snapshot.js").read_text())
        return isinstance(state, dict) and state.get("marker") == page.get("marker")

    def act(self, action, page, text=None):
        # Never accept invented parameters even when the id matches an observation.
        if action not in page.get("actions", []):
            raise ValueError("Action was not observed.")
        kind = action.get("kind")
        if kind not in {"wait", "back", "scroll", "click", "fill", "select"}:
            raise ValueError("Unsupported action kind.")
        if kind == "fill" and (not isinstance(text, str) or not text.strip() or len(text) > 2000):
            raise ValueError("A configured text candidate is required.")
        if not self.fresh(page, action):
            raise StalePage("Page changed since decision; observe again.")
        if kind == "wait":
            time.sleep(0.2)
            return {"executed": action["id"]}
        target = None
        if "node" in action:
            # Read-only validation ends before execution begins.
            target = self.evaluate(
                """(action => {
              const e=window.__jevFast?.nodes.get(action.node);
              if (!e?.isConnected || e.matches(':disabled') || e.closest('[aria-disabled="true"],[inert]') ||
                  !e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) return null;
              if (action.kind==='fill' && (e.readOnly || e.getAttribute('aria-readonly')==='true')) return null;
              const r=e.getBoundingClientRect();
              const x=(Math.max(0,r.left)+Math.min(innerWidth,r.right))/2;
              const y=(Math.max(0,r.top)+Math.min(innerHeight,r.bottom))/2;
              if (!r.width || !r.height || x<0 || y<0 || x>=innerWidth || y>=innerHeight) return null;
              if (!e.contains(document.elementFromPoint(x,y))) return null;
              if (action.kind==='select' && (e.tagName!=='SELECT' || ![...e.options].some(o=>o.value===action.value &&
                    !o.disabled && !o.closest('optgroup[disabled]')))) return null;
              return {x,y};
            })("""
                + json.dumps(action)
                + ")"
            )
            if target is None:
                raise StalePage("Observed target is no longer actionable.")
        # ANY failure from here on has an uncertain outcome; never raise StalePage.
        try:
            if kind == "back":
                self.call("Page.navigateToHistoryEntry", entryId=action["entry_id"])
            elif kind == "scroll":
                point = target or {"x": page.get("w", 1120) / 2, "y": page.get("h", 780) * 0.7}
                self.call(
                    "Input.dispatchMouseEvent",
                    type="mouseWheel",
                    x=point["x"],
                    y=point["y"],
                    deltaX=0,
                    deltaY=action["delta"],
                )
            elif kind == "select":
                result = self.evaluate(
                    """(a => { const c=window.__jevFast,e=c?.nodes.get(a.node);
                  if (!e?.isConnected || JSON.stringify(c.pageKey())!==JSON.stringify(a.pageKey) ||
                      JSON.stringify(c.guard(e))!==JSON.stringify(a.guard)) return false;
                  e.value=a.value;e.dispatchEvent(new Event('input',{bubbles:true}));
                  e.dispatchEvent(new Event('change',{bubbles:true}));return true;
                })("""
                    + json.dumps({**action, "pageKey": page["page_key"], "guard": page["guards"][str(action["node"])]})
                    + ")"
                )
                if result is not True:
                    raise RuntimeError("Dropdown action was not confirmed.")
            else:
                for event in ("mousePressed", "mouseReleased"):
                    self.call(
                        "Input.dispatchMouseEvent",
                        type=event,
                        x=target["x"],
                        y=target["y"],
                        button="left",
                        clickCount=1,
                    )
                if kind == "fill":
                    for event in ("keyDown", "keyUp"):
                        params = {
                            "type": event,
                            "key": "a",
                            "code": "KeyA",
                            "modifiers": 4 if sys.platform == "darwin" else 2,
                        }
                        if event == "keyDown":
                            params["commands"] = ["selectAll"]
                        self.call("Input.dispatchKeyEvent", **params)
                    self.call("Input.insertText", text=text)
        except Exception:
            raise RuntimeError("Browser action outcome is uncertain; inspect before retrying.") from None
        self._after_input = True
        return {"executed": action["id"]}

    def close(self):
        if self.target:
            target, self.target = self.target, None
            try:
                cdp("Target.closeTarget", targetId=target)
            except Exception:
                pass
