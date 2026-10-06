"""Small local CLI. Preview is the default; live execution is explicit."""

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from .config import Task


def load_env(path):
    """Read simple KEY=value entries without evaluating shell syntax or printing secrets."""
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if sep and key.strip() in {"TYPESAFE_API_KEY", "TYPESAFE_MODEL", "BU_CDP_WS", "BU_NAME"}:
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Check key and Brave connection configuration without connecting")
    for name in ("observe", "prompt", "preview", "run"):
        command = commands.add_parser(name)
        command.add_argument("--task", required=True, help="Task JSON file")
        command.add_argument("--db", default="data/session.sqlite")
        if name == "prompt":
            command.add_argument("--output", required=True, help="Save the exact prepared JSON body; no API call")
        if name == "run":
            command.add_argument("--execute", action="store_true", help="Execute Jev choices on the live account")
    status = commands.add_parser("status")
    status.add_argument("--db", default="data/session.sqlite")
    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("--db", default="data/session.sqlite")
    reconcile.add_argument("--action-id", required=True)
    reconcile.add_argument("--outcome", choices=["failed", "verified"], required=True)
    reconcile.add_argument("--reason", required=True, help="What you independently checked")
    args = parser.parse_args(argv)
    load_env(args.env_file)
    if args.command == "doctor":
        from .connection import configure_connection

        report = {"api_key_configured": bool(os.environ.get("TYPESAFE_API_KEY"))}
        try:
            report["browser_configuration"] = configure_connection()
            report["browser_connection"] = "not tested"
        except (RuntimeError, ValueError) as exc:
            report["browser_configuration"] = str(exc)
        print(json.dumps(report, indent=2))
        return 0
    from .memory import Memory

    memory = Memory(args.db)
    browser = None
    try:
        if args.command == "status":
            print(json.dumps(memory.context(), indent=2))
            return 0
        if args.command == "reconcile":
            # Manual resolution unblocks recovery but cannot fabricate invitation evidence/counts.
            memory.finish_action(args.action_id, args.outcome, error="Manual review: " + args.reason)
            print("Action reconciled; invitation counts still require observed evidence.")
            return 0
        task = Task.load(args.task)
        execute = args.command == "run" and args.execute
        if args.command == "run" and not execute:
            parser.error("run requires --execute; use preview to choose without executing")
        if args.command not in {"observe", "prompt"} and not os.environ.get("TYPESAFE_API_KEY"):
            print("Set TYPESAFE_API_KEY in the environment or local .env; no browser opened.")
            return 2
        if memory.unresolved_actions():
            print("Unresolved action in this ledger. Use status and inspect before reconcile; no browser opened.")
            return 2
        from .connection import configure_connection

        configure_connection()
        from .browser import Browser

        browser = Browser(task.start_url)
        if args.command == "prompt":
            from .model import build_request

            page = browser.observe(screenshot=False)
            memory.record_observation(page)
            request = build_request(
                page,
                task.goal,
                memory.context_for(page),
                task.text_candidates,
                os.environ.get("TYPESAFE_MODEL", "jev-latest"),
            )
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n")
            print(f"Prepared prompt saved to {output}; no API call or page action performed.")
            return 0
        if args.command == "observe":
            page = browser.observe(screenshot=False)
            memory.record_observation(page)
            print(
                json.dumps(
                    {
                        k: page.get(k)
                        for k in (
                            "url",
                            "title",
                            "profile",
                            "people",
                            "groups",
                            "notices",
                            "truncation",
                            "scroll_regions",
                        )
                    },
                    indent=2,
                )
            )
            return 0
        from .model import JevClient
        from .runner import Runner

        result = Runner(task, browser, JevClient(), memory).run(execute=execute)
        # No raw API request, response, credentials, or full page text in console output.
        summary = asdict(result)
        if result.decision:
            summary["decision"] = {
                k: result.decision.get(k)
                for k in (
                    "choice",
                    "operation",
                    "confidence",
                    "target_confidence",
                    "eligibility",
                    "usage",
                    "latency_ms",
                )
            }
        print(json.dumps(summary, indent=2))
        return 0 if result.status in {"preview", "complete", "stopped"} else 2
    except (ValueError, OSError, RuntimeError, KeyError):
        print("Stopped: configuration, browser connection, or provider error. No automatic mutation retry.")
        return 2
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        memory.close()


if __name__ == "__main__":
    raise SystemExit(main())
