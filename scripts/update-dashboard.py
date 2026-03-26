#!/usr/bin/env python3
"""
Update the Soren dashboard with agent run data via GitHub API.

Usage:
  python3 scripts/update-dashboard.py start <agent-key> [--summary "..."]
  python3 scripts/update-dashboard.py finish <agent-key> --status success --duration 102.5 [--summary "..."] [--details "..."] [--errors "err1|err2"]
  python3 scripts/update-dashboard.py add <agent-key> --status success --duration 102.5 [--summary "..."] [--details "..."] [--errors "err1|err2"]

Requires: GITHUB_TOKEN env var
"""

import argparse
import base64
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

REPO = "apeniche-ai/s-dash"
FILE_PATH = "data/runs.json"
API_URL = f"https://api.github.com/repos/{REPO}/contents/{FILE_PATH}"
MAX_RUNS = 500


def get_token():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        try:
            result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
            token = result.stdout.strip()
        except Exception:
            pass
    if not token:
        print("Error: GITHUB_TOKEN not set and gh auth not available", file=sys.stderr)
        sys.exit(1)
    return token


def api_request(url, method="GET", data=None):
    """Use curl for HTTP requests to avoid Python SSL issues on macOS."""
    token = get_token()
    cmd = ["curl", "-sf", "-X", method,
           "-H", f"Authorization: token {token}",
           "-H", "Accept: application/vnd.github.v3+json"]
    if data:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(data)]
    cmd.append(url)

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"API error: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    return json.loads(result.stdout)


def fetch_data():
    """Fetch current runs.json from GitHub."""
    resp = api_request(API_URL)
    sha = resp["sha"]
    content = base64.b64decode(resp["content"]).decode()
    data = json.loads(content)
    return data, sha


def push_data(data, sha, message):
    """Push updated runs.json to GitHub."""
    content_str = json.dumps(data, indent=2, ensure_ascii=False)
    content_b64 = base64.b64encode(content_str.encode()).decode()

    api_request(API_URL, method="PUT", data={
        "message": message,
        "content": content_b64,
        "sha": sha,
        "committer": {
            "name": "Soren Bot",
            "email": "soren@noreply.github.com"
        }
    })


def cmd_start(args):
    """Mark an agent as running."""
    data, sha = fetch_data()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Remove any existing running entry for this agent
    data["runs"] = [r for r in data["runs"] if not (r["agent"] == args.agent and r["status"] == "running")]

    # Insert running entry at the top
    data["runs"].insert(0, {
        "agent": args.agent,
        "timestamp": now,
        "duration_seconds": None,
        "status": "running",
        "summary": args.summary or "Starting up...",
        "details": None,
    })

    data["runs"] = data["runs"][:MAX_RUNS]
    push_data(data, sha, f"agent: {args.agent} started")
    print(f"Dashboard: {args.agent} → running")


def cmd_finish(args):
    """Update a running agent with results."""
    data, sha = fetch_data()

    # Find and update the running entry
    found = False
    for run in data["runs"]:
        if run["agent"] == args.agent and run["status"] == "running":
            run["status"] = args.status
            run["duration_seconds"] = args.duration
            run["summary"] = args.summary or ""
            run["details"] = args.details or None
            if args.errors:
                run["errors"] = [e.strip() for e in args.errors.split("|") if e.strip()]
            if args.cost is not None:
                run["cost_usd"] = args.cost
            if args.tokens is not None:
                run["total_tokens"] = args.tokens
            found = True
            break

    if not found:
        # No running entry found — add a completed entry directly
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = {
            "agent": args.agent,
            "timestamp": now,
            "duration_seconds": args.duration,
            "status": args.status,
            "summary": args.summary or "",
            "details": args.details or None,
        }
        if args.errors:
            entry["errors"] = [e.strip() for e in args.errors.split("|") if e.strip()]
        if args.cost is not None:
            entry["cost_usd"] = args.cost
        if args.tokens is not None:
            entry["total_tokens"] = args.tokens
        data["runs"].insert(0, entry)

    data["runs"] = data["runs"][:MAX_RUNS]
    push_data(data, sha, f"agent: {args.agent} {args.status} ({args.duration}s)")
    print(f"Dashboard: {args.agent} → {args.status}")


def cmd_add(args):
    """Add a completed run directly (no prior running state)."""
    data, sha = fetch_data()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    entry = {
        "agent": args.agent,
        "timestamp": now,
        "duration_seconds": args.duration,
        "status": args.status,
        "summary": args.summary or "",
        "details": args.details or None,
    }
    if args.errors:
        entry["errors"] = [e.strip() for e in args.errors.split("|") if e.strip()]
    if args.cost is not None:
        entry["cost_usd"] = args.cost
    if args.tokens is not None:
        entry["total_tokens"] = args.tokens

    data["runs"].insert(0, entry)
    data["runs"] = data["runs"][:MAX_RUNS]
    push_data(data, sha, f"agent: {args.agent} {args.status} ({args.duration}s)")
    print(f"Dashboard: {args.agent} → {args.status}")


def main():
    parser = argparse.ArgumentParser(description="Update Soren dashboard")
    sub = parser.add_subparsers(dest="command", required=True)

    # start
    p_start = sub.add_parser("start", help="Mark agent as running")
    p_start.add_argument("agent", help="Agent key (e.g. pr-reviewer)")
    p_start.add_argument("--summary", default=None)

    # finish
    p_finish = sub.add_parser("finish", help="Update running agent with results")
    p_finish.add_argument("agent", help="Agent key")
    p_finish.add_argument("--status", required=True, choices=["success", "error"])
    p_finish.add_argument("--duration", required=True, type=float)
    p_finish.add_argument("--summary", default=None)
    p_finish.add_argument("--details", default=None)
    p_finish.add_argument("--errors", default=None, help="Pipe-separated error messages")
    p_finish.add_argument("--cost", default=None, type=float, help="Cost in USD")
    p_finish.add_argument("--tokens", default=None, type=int, help="Total tokens used")

    # add (direct, no running state)
    p_add = sub.add_parser("add", help="Add a completed run directly")
    p_add.add_argument("agent", help="Agent key")
    p_add.add_argument("--status", required=True, choices=["success", "error"])
    p_add.add_argument("--duration", required=True, type=float)
    p_add.add_argument("--summary", default=None)
    p_add.add_argument("--details", default=None)
    p_add.add_argument("--errors", default=None)
    p_add.add_argument("--cost", default=None, type=float)
    p_add.add_argument("--tokens", default=None, type=int)

    args = parser.parse_args()

    if args.command == "start":
        cmd_start(args)
    elif args.command == "finish":
        cmd_finish(args)
    elif args.command == "add":
        cmd_add(args)


if __name__ == "__main__":
    main()
