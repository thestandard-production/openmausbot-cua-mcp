"""Command-line interface for OpenMausBot's local administration API."""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, TextIO

from .api import ApiClient, OpenMausBotApiError


class _UsageError(RuntimeError):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _UsageError(message)


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="omb-ctl", description="Read OpenMausBot administration data.")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("status", help="Check API health and compatibility.")

    bots = commands.add_parser("bots", help="List configured bots.")
    bots.add_argument("--include-soul", action="store_true", help="Include full soul text.")
    bots.add_argument("--text", action="store_true", help="Print a compact table.")

    routines = commands.add_parser("routines", help="List routines and recent runs.")
    routines.add_argument("--since-hours", type=float, help="Limit runs to the last N hours.")
    routines.add_argument("--text", action="store_true", help="Print a compact table.")

    commands.add_parser("webhooks", help="List redacted webhooks and delivery attempts.")

    usage = commands.add_parser("usage", help="Show usage for an inclusive date range.")
    usage.add_argument("--from", dest="from_date", required=True, help="Start date, YYYY-MM-DD.")
    usage.add_argument("--to", dest="to_date", required=True, help="End date, YYYY-MM-DD.")
    usage.add_argument("--group-by", help="Optional API grouping dimension.")

    commands.add_parser("models", help="List provider instances and available models.")

    decisions = commands.add_parser("decisions", help="List recent decisions.")
    decisions.add_argument("--limit", type=int, default=50, help="Number of decisions, 1-500.")

    export = commands.add_parser("export", help="Export team data using a paired session.")
    export.add_argument(
        "--format",
        choices=("manifest", "package", "backup"),
        default="manifest",
        help="Export format.",
    )
    return parser


def _print_json(payload: Any, *, stream: TextIO = sys.stdout) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), file=stream)


def _field(payload: dict[str, Any], *path: str) -> str:
    value: Any = payload
    for key in path:
        if not isinstance(value, dict):
            return ""
        value = value.get(key)
    return "" if value is None else str(value)


def _print_bots(payload: dict[str, Any]) -> None:
    print("ID\tNAME\tMODEL\tCWD")
    bots = payload.get("bots")
    if not isinstance(bots, list):
        return
    for bot in bots:
        if not isinstance(bot, dict):
            continue
        print(
            "\t".join(
                (
                    _field(bot, "id"),
                    _field(bot, "name") or _field(bot, "title"),
                    _field(bot, "modelSelection", "model"),
                    _field(bot, "cwd"),
                )
            )
        )


def _print_routines(payload: dict[str, Any]) -> None:
    routines = payload.get("routines")
    runs = payload.get("runs")
    run_count = len(runs) if isinstance(runs, list) else 0
    print(f"ROUTINES\t{len(routines) if isinstance(routines, list) else 0}\tRUNS\t{run_count}")
    print("ID\tNAME\tENABLED\tSCHEDULE")
    if not isinstance(routines, list):
        return
    for routine in routines:
        if not isinstance(routine, dict):
            continue
        print(
            "\t".join(
                (
                    _field(routine, "id"),
                    _field(routine, "name") or _field(routine, "title"),
                    _field(routine, "enabled"),
                    _field(routine, "schedule"),
                )
            )
        )


def _run(client: ApiClient, args: argparse.Namespace) -> tuple[Any, int]:
    if args.command == "status":
        health = client.health()
        compatibility = client.check_compatibility()
        payload = {
            "health": health,
            "version": compatibility["version"],
            "compatibility": compatibility,
        }
        if not compatibility["ok"]:
            return payload, 2
        return payload, 10 if compatibility["warnings"] else 0
    if args.command == "bots":
        return client.list_bots(include_soul=args.include_soul), 0
    if args.command == "routines":
        if args.since_hours is None:
            return client.list_routines(), 0
        if args.since_hours <= 0:
            raise OpenMausBotApiError("--since-hours must be a positive number.")
        to_ms = int(time.time() * 1000)
        from_ms = to_ms - int(args.since_hours * 60 * 60 * 1000)
        return client.list_routines(from_ms=from_ms, to_ms=to_ms), 0
    if args.command == "webhooks":
        return client.list_webhooks(), 0
    if args.command == "usage":
        return client.usage(args.from_date, args.to_date, args.group_by), 0
    if args.command == "models":
        return client.list_models(), 0
    if args.command == "decisions":
        return client.decisions(args.limit), 0
    if args.command == "export":
        return client.export_team(args.format), 0
    raise OpenMausBotApiError(f"Unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return its process exit code."""
    try:
        args = _parser().parse_args(argv)
        payload, exit_code = _run(ApiClient(), args)
    except (_UsageError, OpenMausBotApiError) as exc:
        _print_json({"ok": False, "error": str(exc)}, stream=sys.stderr)
        return 2

    if args.command == "bots" and args.text:
        _print_bots(payload)
    elif args.command == "routines" and args.text:
        _print_routines(payload)
    else:
        _print_json(payload)
    return exit_code
