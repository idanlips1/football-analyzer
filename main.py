"""Query REPL — acts as a thin client to the Azure Backend API to generate highlights."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, cast

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000").rstrip("/")


def _prompt(msg: str, default: str = "") -> str:
    try:
        value = input(msg).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return value or default


def _display_game_list(matches: list[dict]) -> None:
    print()
    for i, m in enumerate(matches, 1):
        if "home_team" in m:
            label = (
                f"{m['home_team']} vs {m['away_team']}  |  "
                f"{m.get('competition', '')}  |  {m.get('season_label', '')}"
            )
        else:
            label = m["match_id"]
        print(f"  [{i}] {label}")
    print()


def _get_matches() -> list[dict[str, Any]]:
    url = f"{API_BASE_URL}/api/v1/matches"
    try:
        with urllib.request.urlopen(url) as response:  # nosec B310
            data = json.loads(response.read().decode())
            return cast(list[dict[str, Any]], data.get("matches", []))
    except Exception as e:
        print(f"Error fetching matches from API: {e}", file=sys.stderr)
        return []


def _submit_job(match_id: str, query: str) -> dict[str, Any] | None:
    url = f"{API_BASE_URL}/api/v1/jobs"
    payload = {"match_id": match_id, "highlights_query": query}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as response:  # nosec B310
            return cast(dict[str, Any], json.loads(response.read().decode()))
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  Error submitting job: {e.code} - {body}", file=sys.stderr)
    except Exception as e:
        print(f"  Error submitting job: {e}", file=sys.stderr)
    return None


def _poll_job(poll_url: str, job_id: str) -> None:
    url = f"{API_BASE_URL}{poll_url}"
    print("\n  Job queued. Waiting for worker to process on Azure...")
    while True:
        try:
            with urllib.request.urlopen(url) as response:  # nosec B310
                job_data = json.loads(response.read().decode())
                status = job_data.get("status")

                if status == "completed":
                    result = job_data.get("result", {})
                    watch_url = f"{API_BASE_URL}/watch/{job_id}"
                    print(f"\n  Watch here:  {watch_url}")
                    print(f"  Download:    {result.get('download_url')}")
                    print(
                        f"  Duration: {result.get('duration_seconds', 0)}s | "
                        f"Clips: {result.get('clip_count', 0)}\n"
                    )
                    return
                elif status == "failed":
                    print(f"\n  Job failed: {job_data.get('error')}\n", file=sys.stderr)
                    return
                else:
                    stage = job_data.get("progress", "processing")
                    sys.stdout.write(f"\r  Status: {status} ({stage})".ljust(50))
                    sys.stdout.flush()
        except Exception as e:
            print(f"\n  Error polling job: {e}", file=sys.stderr)
            return
        time.sleep(3)


def _game_repl(match: dict) -> None:
    """Inner REPL for a chosen game. Returns when user types 'back'."""
    if "home_team" in match:
        header = f"{match['home_team']} vs {match['away_team']} — {match.get('season_label', '')}"
    else:
        header = match["match_id"]
    print(f"\n  {header}")
    print("  Type your highlights request, 'back' to pick another game, or 'quit'.\n")

    while True:
        raw = _prompt("> ")
        if raw.lower() in ("quit", "exit", "q"):
            print("Bye!")
            sys.exit(0)
        if raw.lower() == "back":
            return
        if not raw:
            continue

        job_info = _submit_job(match["match_id"], raw)
        if job_info:
            job_id = job_info.get("job_id", "")
            if "status" in job_info and job_info["status"] == "completed":
                print("\n  Job instantly found in cache!")
                result = job_info.get("result", {})
                watch_url = f"{API_BASE_URL}/watch/{job_id}"
                print(f"  Watch here:  {watch_url}")
                print(f"  Download:    {result.get('download_url')}\n")
            else:
                _poll_job(job_info["poll_url"], job_id)


def run() -> None:
    """Main query REPL."""
    print("\n  Football Highlights Client")
    print("  " + "-" * 34)

    while True:
        matches = _get_matches()
        if not matches:
            print("\n  No games found from API! Or API is unreachable.\n")
            time.sleep(2)
            continue

        _display_game_list(matches)
        pick = _prompt(f"  Pick a game [1-{len(matches)}] or 'quit': ")
        if pick.lower() in ("quit", "exit", "q"):
            print("Bye!")
            break
        try:
            idx = int(pick) - 1
            if 0 <= idx < len(matches):
                _game_repl(matches[idx])
            else:
                print("  Invalid choice.")
        except ValueError:
            print("  Please enter a number.")


if __name__ == "__main__":
    run()
