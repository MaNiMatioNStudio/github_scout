#!/usr/bin/env python3
"""
Layer5 runner — ルールベース LP URL発見 + 日本向け判定 + 企業情報抽出

Claude API不要・$0で動作。
対象: 既存JSONLのL4トスアップ（layer4_pass=True）のみ処理する。
出力: 同じJSONLをL5フィールド付きで上書き更新する。
チェックポイント: output/layer5_checkpoint_{tag}.jsonl に処理済みURLを保存。

Usage:
    python run_l5.py --source output/filtered_repos_2026-03-17_2026-03-18.jsonl
    python run_l5.py --source output/filtered_repos_2026-03-17_2026-03-18.jsonl --resume
"""
import json
import logging
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def parse_args():
    import argparse
    parser = argparse.ArgumentParser(description="Run Layer5 (rule-based) on L4 tossups.")
    parser.add_argument("--source", required=True, help="Path to JSONL with L4 tossups")
    parser.add_argument("--resume", action="store_true", help="Skip already-processed repos")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--config", default="config/filters.yaml")
    return parser.parse_args()


def _load_checkpoint(path: Path) -> dict:
    results = {}
    if not path.exists():
        return results
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rec = json.loads(line)
                    results[rec["url"]] = rec
                except Exception:
                    pass
    return results


def _save_checkpoint(rec: dict, path: Path) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()


def _merge_into_jsonl(source_path: Path, checkpoint: dict) -> None:
    records = []
    with open(source_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("url") in checkpoint:
                rec.update(checkpoint[rec["url"]])
            records.append(rec)
    with open(source_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    logger.info("Merged L5 results into %s (%d records)", source_path, len(records))


def main():
    args = parse_args()
    source_path = Path(args.source)
    if not source_path.exists():
        logger.error("Source file not found: %s", source_path)
        sys.exit(1)

    with open(args.config) as f:
        config = yaml.safe_load(f)
    layer5_config = config.get("layer5", {})

    all_records = [json.loads(l) for l in source_path.read_text().splitlines() if l.strip()]
    tossups = [r for r in all_records if r.get("layer4_pass")]
    logger.info("L4トスアップ: %d件", len(tossups))

    tag = source_path.stem
    checkpoint_path = source_path.parent / f"layer5_checkpoint_{tag}.jsonl"
    checkpoint = _load_checkpoint(checkpoint_path)

    if args.resume:
        pending = [r for r in tossups if r["url"] not in checkpoint]
        logger.info("再開モード: 処理済み=%d件  残り=%d件", len(checkpoint), len(pending))
    else:
        pending = tossups
        if checkpoint:
            logger.info("既存チェックポイント %d件あり（--resume で再利用可）", len(checkpoint))

    if not pending:
        logger.info("処理対象なし。マージのみ実行。")
        _merge_into_jsonl(source_path, checkpoint)
        return

    token = os.getenv("GITHUB_TOKEN")
    from github_scout.adapters.github_api import GitHubAdapter
    adapter = GitHubAdapter(token=token)

    print(f"\n{'='*50}")
    print(f"  Layer5 実行予定: {len(pending)} 件")
    print(f"  Claude API     : 不使用（ルールベース）")
    print(f"  コスト         : $0")
    print(f"  チェックポイント: {checkpoint_path.name}")
    print(f"{'='*50}")

    if not args.yes:
        ans = input("\n  Continue? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("Aborted.")
            sys.exit(0)
    print()

    from github_scout.filters.layer5 import apply_layer5

    for rec in pending:
        class _Repo:
            pass
        repo = _Repo()
        repo.name        = rec["name"]
        repo.owner       = rec["owner"]
        repo.description = rec.get("description", "")
        repo.language    = rec.get("language") or ""

        logger.info("L5処理中: %s/%s", repo.owner, repo.name)

        passed, reasons, confidence, lp_url, company, founder, manual_needed = apply_layer5(
            repo, layer5_config, adapter
        )

        l5_result = {
            "url": rec["url"],
            "layer5_pass":          passed,
            "layer5_reasons":       reasons,
            "layer5_confidence":    confidence,
            "layer5_lp_url":        lp_url or "",
            "layer5_company_name":  company,
            "layer5_founder_name":  founder,
            "layer5_manual_needed": manual_needed,
        }
        checkpoint[rec["url"]] = l5_result
        _save_checkpoint(l5_result, checkpoint_path)

        status = "✅ PASS" if passed else ("🔍 手動" if manual_needed else "❌ FAIL")
        logger.info("  %s  LP=%s  企業=%s  代表=%s",
                    status,
                    lp_url or "未発見",
                    company or "不明",
                    founder or "不明")

    _merge_into_jsonl(source_path, checkpoint)

    passed_count = sum(1 for v in checkpoint.values() if v.get("layer5_pass"))
    manual_count = sum(1 for v in checkpoint.values() if v.get("layer5_manual_needed"))
    logger.info("Layer5 完了: 処理=%d件  通過=%d件  手動確認=%d件",
                len(checkpoint), passed_count, manual_count)


if __name__ == "__main__":
    main()
