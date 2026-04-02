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
import time
from datetime import datetime, timezone

REPO = "apeniche-ai/s-dash"
FILE_PATH = "data/runs.json"
API_URL = f"https://api.github.com/repos/{REPO}/contents/{FILE_PATH}"
MAX_RUNS = 500
MAX_RETRIES = 3


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
    """Use curl for HTTP requests to avoid Python SSL issues on macOS.
    Returns (parsed_json, http_status_code). Raises on curl-level failure."""
    token = get_token()
    cmd = ["curl", "-sS", "-w", "\n%{http_code}", "-X", method,
           "-H", f"Authorization: token {token}",
           "-H", "Accept: application/vnd.github.v3+json"]
    if data:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(data)]
    cmd.append(url)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"curl exit {result.returncode}: {result.stderr.strip()}")

    # Parse response body and HTTP status code
    lines = result.stdout.rsplit("\n", 1)
    body = lines[0] if len(lines) > 1 else result.stdout
    try:
        status_code = int(lines[1]) if len(lines) > 1 else 0
    except ValueError:
        status_code = 0

    if status_code >= 400:
        raise RuntimeError(f"HTTP {status_code}: {body[:500]}")

    return json.loads(body)


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


def with_retry(modify_fn, commit_message):
    """Fetch-modify-push with retry on 409 Conflict (SHA mismatch).
    modify_fn receives (data) and mutates it in place."""
    for attempt in range(MAX_RETRIES):
        try:
            data, sha = fetch_data()
            modify_fn(data)
            data["runs"] = data["runs"][:MAX_RUNS]
            push_data(data, sha, commit_message)
            return
        except RuntimeError as e:
            if "HTTP 409" in str(e) and attempt < MAX_RETRIES - 1:
                wait = 1 + attempt
                print(f"SHA conflict, retrying in {wait}s (attempt {attempt + 1}/{MAX_RETRIES})...", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
    raise RuntimeError(f"Failed after {MAX_RETRIES} retries")


def cmd_start(args):
    """Mark an agent as running."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def modify(data):
        data["runs"] = [r for r in data["runs"] if not (r["agent"] == args.agent and r["status"] == "running")]
        data["runs"].insert(0, {
            "agent": args.agent,
            "timestamp": now,
            "duration_seconds": None,
            "status": "running",
            "summary": args.summary or "Starting up...",
            "details": None,
        })

    with_retry(modify, f"agent: {args.agent} started")
    print(f"Dashboard: {args.agent} → running")


def cmd_finish(args):
    """Update a running agent with results."""
    def modify(data):
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

    with_retry(modify, f"agent: {args.agent} {args.status} ({args.duration}s)")
    print(f"Dashboard: {args.agent} → {args.status}")


def cmd_sync_agents(args):
    """Sync the agents registry from a JSON payload."""
    agents = json.loads(args.agents_json)

    def modify(data):
        data["agents"] = agents

    with_retry(modify, "sync: update agent registry")
    print(f"Dashboard: synced {len(agents)} agents")


def cmd_add(args):
    """Add a completed run directly (no prior running state)."""
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

    def modify(data):
        data["runs"].insert(0, entry)

    with_retry(modify, f"agent: {args.agent} {args.status} ({args.duration}s)")
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

    # sync-agents
    p_sync = sub.add_parser("sync-agents", help="Sync agent registry from JSON")
    p_sync.add_argument("--agents", dest="agents_json", required=True, help="JSON string of agents object")

    args = parser.parse_args()

    try:
        if args.command == "start":
            cmd_start(args)
        elif args.command == "finish":
            cmd_finish(args)
        elif args.command == "add":
            cmd_add(args)
        elif args.command == "sync-agents":
            cmd_sync_agents(args)
    except RuntimeError as e:
        print(f"Dashboard update failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
