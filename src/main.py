from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
import logging
import sys
import time
from typing import Any

from src.config import ALARM_OFFSETS_MINUTES, EVENTS_FILE, GEMINI_API_KEY, KST
from src.crawler import fetch_event_detail, fetch_matching_events
from src.notifier import build_alarm_message, send_telegram_message, send_test_notification
from src.parser import extract_event_schedules

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")


def load_state() -> dict[str, dict[str, Any]]:
    """Load persisted events state from `data/events.json`."""
    if not EVENTS_FILE.exists():
        return {}
    try:
        content = EVENTS_FILE.read_text(encoding="utf-8").strip()
        if not content:
            return {}
        data = json.loads(content)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.error("Failed reading %s: %s", EVENTS_FILE, exc)
        return {}


def save_state(state: dict[str, dict[str, Any]]) -> None:
    """Persist events state to `data/events.json`."""
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True)
    EVENTS_FILE.write_text(serialized + "\n", encoding="utf-8")


def sync_events(
    state: dict[str, dict[str, Any]],
    force_reparse: bool = False,
) -> bool:
    """
    Crawl Lotte Cinema for active '무비싸다구' events, parse any newly discovered
    events (or previously unparsed events), and update `state`.
    Returns True if `state` was modified.
    """
    modified = False
    matching_items = fetch_matching_events()

    for item in matching_items:
        eid = str(item.get("EventID", "")).strip()
        if not eid:
            continue

        existing = state.get(eid)
        needs_parse = (
            force_reparse
            or existing is None
            or not existing.get("schedules")
        )

        if not needs_parse:
            continue

        logger.info("Fetching details & extracting schedule for event: %s (%s)", item.get("EventName"), eid)
        detail = fetch_event_detail(eid, list_item=item)
        extracted = extract_event_schedules(detail)

        # Preserve notification flags if re-parsing an existing event
        if existing and existing.get("schedules"):
            old_by_time = {
                s.get("open_time_kst"): s for s in existing.get("schedules", [])
            }
            for sched in extracted.get("schedules", []):
                prev = old_by_time.get(sched.get("open_time_kst"))
                if prev:
                    for offset in ALARM_OFFSETS_MINUTES:
                        flag = offset["flag"]
                        sched[flag] = bool(prev.get(flag, False))

        state[eid] = extracted
        modified = True
        logger.info(
            "Saved event %s with %d schedule(s): %s",
            eid,
            len(extracted.get("schedules", [])),
            [s.get("open_time_kst") for s in extracted.get("schedules", [])],
        )

    if modified:
        save_state(state)

    return modified


def send_today_events_notification(
    state: dict[str, dict[str, Any]],
    dry_run: bool = False,
) -> bool:
    """
    Crawl Lotte Cinema and immediately send Telegram notification(s) for all
    '무비싸다구' events that opened today (in KST) to verify end-to-end crawling
    and Telegram delivery. Falls back to the latest discovered event if none opened today.
    """
    sync_events(state, force_reparse=False)
    today_str = datetime.now(KST).strftime("%Y-%m-%d")

    today_matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    all_matches: list[tuple[dict[str, Any], dict[str, Any]]] = []

    for eid, event_record in sorted(state.items()):
        schedules = event_record.get("schedules") or []
        for sched in schedules:
            open_iso = str(sched.get("open_time_kst") or "")
            all_matches.append((event_record, sched))
            if open_iso.startswith(today_str) or str(event_record.get("progress_start_date", "")).startswith(today_str):
                today_matches.append((event_record, sched))

    targets = today_matches if today_matches else all_matches[-1:]
    if not targets:
        logger.warning("No '무비싸다구' events found on Lotte Cinema; sending fallback test message.")
        return send_test_notification(dry_run=dry_run)

    all_ok = True
    for event_record, sched in targets:
        text, markup = build_alarm_message(
            event_record,
            sched,
            minutes_before=5,
        )
        ok = send_telegram_message(text, reply_markup=markup, dry_run=dry_run)
        if not ok:
            all_ok = False

    return all_ok


def process_upcoming_alarms(
    state: dict[str, dict[str, Any]],
    lookahead_minutes: int = 30,
    grace_past_minutes: int = 20,
    wait_for_exact_time: bool = True,
    dry_run: bool = False,
) -> int:
    """
    Check all tracked event schedules for upcoming T-60m (1 hour before) and
    T-5m (5 minutes before) alarms.
    If an alarm is within `lookahead_minutes`, optionally sleeps until the exact
    target timestamp, sends the Telegram message, and updates `state`.
    Returns the number of notifications sent.
    """
    now = datetime.now(KST)
    due_tasks: list[dict[str, Any]] = []

    for eid, event_record in state.items():
        schedules = event_record.get("schedules") or []
        for idx, sched in enumerate(schedules):
            open_time_str = sched.get("open_time_kst")
            if not open_time_str:
                continue
            try:
                open_dt = datetime.fromisoformat(open_time_str).astimezone(KST)
            except ValueError:
                continue

            if open_dt <= now:
                continue

            for offset in ALARM_OFFSETS_MINUTES:
                minutes_before = offset["minutes"]
                flag = offset["flag"]

                if sched.get(flag):
                    continue

                target_dt = open_dt - timedelta(minutes=minutes_before)
                diff_seconds = (target_dt - now).total_seconds()

                if minutes_before >= 60 and (open_dt - now).total_seconds() < 15 * 60:
                    continue

                if -grace_past_minutes * 60 <= diff_seconds <= lookahead_minutes * 60:
                    due_tasks.append(
                        {
                            "target_dt": target_dt,
                            "open_dt": open_dt,
                            "minutes_before": minutes_before,
                            "flag": flag,
                            "event_id": eid,
                            "event_record": event_record,
                            "schedule_item": sched,
                            "schedule_index": idx,
                        }
                    )

    if not due_tasks:
        logger.info("No upcoming alarms within the next %d minutes (now=%s).", lookahead_minutes, now.isoformat())
        return 0

    due_tasks.sort(key=lambda t: t["target_dt"])
    sent_count = 0

    for task in due_tasks:
        target_dt: datetime = task["target_dt"]
        current_now = datetime.now(KST)
        wait_seconds = (target_dt - current_now).total_seconds()

        if wait_for_exact_time and wait_seconds > 0:
            logger.info(
                "Upcoming %d-min alarm for '%s' at %s. Sleeping %.1f seconds until exact alarm time...",
                task["minutes_before"],
                task["event_record"].get("title"),
                target_dt.isoformat(),
                wait_seconds,
            )
            time.sleep(wait_seconds)

        text, markup = build_alarm_message(
            task["event_record"],
            task["schedule_item"],
            minutes_before=task["minutes_before"],
        )
        ok = send_telegram_message(text, reply_markup=markup, dry_run=dry_run)
        if ok:
            task["schedule_item"][task["flag"]] = True
            task["schedule_item"][f"{task['flag']}_sent_at"] = (
                datetime.now(KST).replace(microsecond=0).isoformat()
            )
            save_state(state)
            sent_count += 1

    return sent_count


def inspect_single_event(event_id: str) -> None:
    """Utility to inspect crawling and schedule extraction for any Lotte Cinema EventID."""
    detail = fetch_event_detail(event_id)
    extracted = extract_event_schedules(detail)
    print(json.dumps(extracted, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lotte Cinema '무비싸다구' Telegram Alarm Bot (1h & 5m before)"
    )
    parser.add_argument(
        "--mode",
        choices=["all", "crawl", "alarm"],
        default="all",
        help="Execution mode: 'crawl' (discover events), 'alarm' (check & send alarms), or 'all' (both).",
    )
    parser.add_argument(
        "--lookahead-minutes",
        type=int,
        default=30,
        help="Minutes ahead to look for upcoming alarms and sleep until exact trigger time (default: 30).",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Send any alarm inside the lookahead window immediately without sleeping until exact target time.",
    )
    parser.add_argument(
        "--force-reparse",
        action="store_true",
        help="Force re-fetching and re-parsing all matching events.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log Telegram messages instead of sending them.",
    )
    parser.add_argument(
        "--test-telegram",
        action="store_true",
        help="Crawl Lotte Cinema and send today's real '무비싸다구' event(s) to Telegram for testing.",
    )
    parser.add_argument(
        "--send-today",
        action="store_true",
        help="Crawl Lotte Cinema and send today's '무비싸다구' event(s) to Telegram.",
    )
    parser.add_argument(
        "--inspect-event",
        type=str,
        default="",
        help="Inspect detail fetching and schedule parsing for a specific EventID.",
    )

    args = parser.parse_args()
    state = load_state()

    if args.test_telegram or args.send_today:
        ok = send_today_events_notification(state, dry_run=args.dry_run)
        return 0 if ok else 1

    if args.inspect_event:
        inspect_single_event(args.inspect_event)
        return 0

    if args.mode in ("all", "crawl"):
        sync_events(state, force_reparse=args.force_reparse)

    if args.mode in ("all", "alarm"):
        process_upcoming_alarms(
            state,
            lookahead_minutes=args.lookahead_minutes,
            wait_for_exact_time=not args.no_wait,
            dry_run=args.dry_run,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
