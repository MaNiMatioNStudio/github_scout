#!/usr/bin/env python3
"""GitHub Startup Scout — CLI entry point."""
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Tuple

import yaml
from dotenv import load_dotenv

from github_scout.adapters.github_api import GitHubAdapter
from github_scout.models import RepoRecord
from github_scout.output import append_jsonl, write_csv_from_jsonl
from github_scout.pipeline import run_pipeline
from github_scout.window_checkpoint import WindowCheckpoint

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def parse_args():
    import argparse

    today    = datetime.now(timezone.utc).date()
    week_ago = today - timedelta(days=7)

    parser = argparse.ArgumentParser(description="Fetch GitHub repos and filter startup candidates.")
    parser.add_argument("--date-from", default=str(week_ago))
    parser.add_argument("--date-to",   default=str(today))
    parser.add_argument("--time-from", default="", help="開始時刻 HH:MM:SS (UTC)。指定時は分単位クエリ。")
    parser.add_argument("--time-to",   default="", help="終了時刻 HH:MM:SS (UTC)。指定時は分単位クエリ。")
    parser.add_argument("--max-results", type=int, default=5000)
    parser.add_argument("--config",      default="config/filters.yaml")
    parser.add_argument("--output-dir",  default="output")
    parser.add_argument("--passed-only", action="store_true")
    parser.add_argument("--name-contains", default="")
    parser.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip all confirmation prompts.",
    )
    parser.add_argument(
        "--window-minutes", type=int, default=0,
        help="時間窓分割モード。30 を指定すると30分ごとにクエリを発行する。0=従来の日単位。",
    )
    return parser.parse_args()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _generate_windows(
    date_from: str, date_to: str, window_minutes: int = 30
) -> Iterator[Tuple[str, str, str, str]]:
    """Yield (label, date_str, time_from, time_to) for each time window.

    label     : "2026-03-24T00:00"  (チェックポイントのキー)
    date_str  : "2026-03-24"
    time_from : "00:00:00"
    time_to   : "00:29:59"
    """
    delta = timedelta(minutes=window_minutes)
    end_date = date.fromisoformat(date_to)
    current = datetime(
        *date.fromisoformat(date_from).timetuple()[:3], 0, 0, 0
    )
    end_dt = datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59)

    while current <= end_dt:
        window_end = min(current + delta - timedelta(seconds=1), end_dt)
        date_str  = current.strftime("%Y-%m-%d")
        time_from = current.strftime("%H:%M:%S")
        time_to   = window_end.strftime("%H:%M:%S")
        label     = f"{date_str}T{current.strftime('%H:%M')}"
        yield label, date_str, time_from, time_to
        current += delta


def _count_total_repos(adapter: GitHubAdapter, date_from: str, date_to: str,
                       name_contains: str = "",
                       time_from: str = "", time_to: str = "") -> int:
    from datetime import date, timedelta
    name_filter = f"{name_contains} in:name " if name_contains else ""
    if time_from and time_to:
        q = (f"{name_filter}created:{date_from}T{time_from}..{date_to}T{time_to} "
             f"fork:false archived:false is:public")
        return adapter.count_repos(q)
    start   = date.fromisoformat(date_from)
    end     = date.fromisoformat(date_to)
    total   = 0
    current = start
    while current <= end:
        day = str(current)
        q = f"{name_filter}created:{day}..{day} fork:false archived:false is:public"
        total += adapter.count_repos(q)
        current += timedelta(days=1)
    return total


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()

    with open(args.config) as f:
        config = yaml.safe_load(f)

    layer1_config = config.get("layer1", {})
    layer2_config = config.get("layer2", {})
    layer3_config = config.get("layer3", {})

    token = os.getenv("GITHUB_TOKEN")
    if not token:
        logger.warning("GITHUB_TOKEN not set — rate limits will be strict")
    adapter = GitHubAdapter(token=token)

    # ── Paths ────────────────────────────────────────────────────────────────
    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)
    if args.time_from and args.time_to:
        time_tag = f"T{args.time_from.replace(':','')}-{args.time_to.replace(':','')}"
        tag = f"{args.date_from}{time_tag}"
    elif args.date_from == args.date_to:
        tag = args.date_from
    else:
        tag = f"{args.date_from}_{args.date_to}"
    jsonl_path = output_dir / f"filtered_repos_{tag}.jsonl"
    csv_path   = output_dir / f"filtered_repos_{tag}.csv"

    # ── Run pipeline with streaming write ────────────────────────────────────
    all_count    = 0
    passed_count = 0

    if args.window_minutes > 0:
        # ── 時間窓モード ────────────────────────────────────────────────────
        cp_path = output_dir / f"wcheckpoint_{tag}_w{args.window_minutes}.json"
        checkpoint = WindowCheckpoint(cp_path)

        windows = list(_generate_windows(args.date_from, args.date_to, args.window_minutes))
        total_windows = len(windows)
        skipped = checkpoint.count()
        logger.info(
            "Window mode: %d windows total, %d already done, %d remaining",
            total_windows, skipped, total_windows - skipped,
        )

        # 再開時は追記モード、新規は上書き
        open_mode = "a" if checkpoint.count() > 0 else "w"
        with open(jsonl_path, open_mode, encoding="utf-8") as jsonl_f:
            for idx, (label, date_str, time_from, time_to) in enumerate(windows, 1):
                if checkpoint.is_done(label):
                    continue

                logger.info(
                    "[%d/%d] Window %s (%s-%s)",
                    idx, total_windows, label, time_from, time_to,
                )
                for repo in run_pipeline(
                    adapter=adapter,
                    layer1_config=layer1_config,
                    layer2_config=layer2_config,
                    date_from=date_str,
                    date_to=date_str,
                    max_results=1000,
                    layer3_config=layer3_config,
                    name_contains=args.name_contains,
                    time_from=time_from,
                    time_to=time_to,
                ):
                    all_count += 1
                    is_passed = (
                        repo.layer1_pass
                        and repo.layer2_pass
                        and repo.layer3_result == "pass"
                    )
                    if is_passed:
                        passed_count += 1
                    if not args.passed_only or is_passed:
                        append_jsonl(repo, jsonl_f)

                    if all_count % 50 == 0:
                        logger.info("processed=%d  passed=%d", all_count, passed_count)

                checkpoint.mark_done(label)
    else:
        # ── 従来の日単位モード ───────────────────────────────────────────────
        with open(jsonl_path, "w", encoding="utf-8") as jsonl_f:
            for repo in run_pipeline(
                adapter=adapter,
                layer1_config=layer1_config,
                layer2_config=layer2_config,
                date_from=args.date_from,
                date_to=args.date_to,
                max_results=args.max_results,
                layer3_config=layer3_config,
                name_contains=args.name_contains,
                time_from=args.time_from or None,
                time_to=args.time_to or None,
            ):
                all_count += 1
                is_passed = repo.layer1_pass and repo.layer2_pass and repo.layer3_result == "pass"
                if is_passed:
                    passed_count += 1

                if not args.passed_only or is_passed:
                    append_jsonl(repo, jsonl_f)

                if all_count % 50 == 0:
                    logger.info("processed=%d  passed=%d", all_count, passed_count)

    try:
        write_csv_from_jsonl(jsonl_path, csv_path)
    except Exception as exc:
        logger.warning("CSV generation failed: %s", exc)

    logger.info("Done. processed=%d  passed=%d  output=%s", all_count, passed_count, jsonl_path)


if __name__ == "__main__":
    main()
