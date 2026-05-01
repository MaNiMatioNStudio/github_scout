#!/usr/bin/env python3
"""
タイムスリップ検証: zeimee-lp が Layer1〜Layer4 パイプラインを通過するか？

シミュレーション条件:
  - zeimee-lp が作成された翌日（2026-03-18）にこのシステムを実行した想定
  - repoデータは tests/fixtures/zeimee_lp.json の固定値を使用
  - orgデータは GitHub API からライブ取得（GITHUB_TOKEN 推奨）
  - Layer3/4 は Claude API を実際に呼び出す（ANTHROPIC_API_KEY 必須）

Usage:
    cd ~/github_scout
    source .venv/bin/activate
    python tests/test_zeimee_pipeline.py
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from dotenv import load_dotenv

load_dotenv()

import anthropic

from github_scout.adapters.github_api import GitHubAdapter
from github_scout.filters.layer1 import apply_layer1
from github_scout.filters.layer2 import apply_layer2
from github_scout.filters.layer3 import apply_layer3
from github_scout.filters.layer4 import apply_layer4
from github_scout.models import RepoRecord

# ──────────────────────────────────────────────
# Setup
# ──────────────────────────────────────────────

FIXTURE = Path(__file__).parent / "fixtures" / "zeimee_lp.json"
FILTER_CONFIG = Path(__file__).parent.parent / "config" / "filters.yaml"
PREFERENCES = Path(__file__).parent.parent / "config" / "preferences.yaml"

SEP = "─" * 60
BOLD_SEP = "═" * 60


def load_configs():
    with open(FILTER_CONFIG) as f:
        filters = yaml.safe_load(f)
    with open(PREFERENCES) as f:
        prefs = yaml.safe_load(f)
    return filters, prefs


def fetch_org_age(owner: str) -> tuple:
    """Return (age_in_days, created_at_str) or (None, '') on failure."""
    token = os.getenv("GITHUB_TOKEN")
    adapter = GitHubAdapter(token=token)
    org_data = adapter.get_org(owner)
    if not org_data or "created_at" not in org_data:
        return None, ""
    created = datetime.fromisoformat(org_data["created_at"].replace("Z", "+00:00"))
    age_days = (datetime.now(timezone.utc) - created).days
    return age_days, org_data["created_at"]


# ──────────────────────────────────────────────
# Display helpers
# ──────────────────────────────────────────────

def print_header(title: str):
    print(f"\n{BOLD_SEP}")
    print(f"  {title}")
    print(BOLD_SEP)


def print_section(title: str):
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)


def print_reasons(reasons: list[str]):
    for r in reasons:
        icon = "✓" if r.startswith("PASS") else ("✗" if r.startswith("FAIL") else "·")
        print(f"  {icon} {r}")


def verdict_icon(passed: bool) -> str:
    return "✅ PASS" if passed else "❌ FAIL"


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    filters, prefs = load_configs()
    layer1_cfg = filters["layer1"]
    layer2_cfg = filters["layer2"]
    layer3_cfg = filters.get("layer3", {})
    layer4_cfg = filters.get("layer4", {})

    # Load fixture
    with open(FIXTURE) as f:
        fixture = json.load(f)

    repo_data = fixture["repo"]
    sim = fixture["simulation"]
    reference_date = datetime.fromisoformat(sim["reference_date"].replace("Z", "+00:00"))

    repo = RepoRecord(
        name=repo_data["name"],
        owner=repo_data["owner"],
        url=repo_data["url"],
        created_at=repo_data["created_at"],
        language=repo_data["language"],
        stars=repo_data["stars"],
        description=repo_data["description"],
    )
    readme = fixture.get("readme")
    root_files = fixture.get("root_files", [])

    # ── Header ──────────────────────────────
    print_header(f"PIPELINE TRACE: {repo.owner}/{repo.name}")
    print(f"\n  Repo         : {repo.name}")
    print(f"  Owner        : {repo.owner}")
    print(f"  URL          : {repo.url}")
    print(f"  Created      : {repo.created_at}")
    print(f"  Language     : {repo.language}")
    print(f"  Stars        : {repo.stars}")
    print(f"  Description  : {repo.description}")
    print(f"  Root files   : {root_files}")
    print(f"  README       : {'あり' if readme else 'なし'}")
    print(f"\n  ⏱ シミュレーション日時: {reference_date.date()} ({sim['note']})")

    # ── Fetch org age ────────────────────────
    print_section("ORG データ取得 (GitHub API)")
    print(f"  オーナー {repo.owner} の情報を取得中...")
    org_age_days, org_created_at = fetch_org_age(repo.owner)
    if org_age_days is not None:
        print(f"  · org 作成日 : {org_created_at}")
        print(f"  · org 年齢   : {org_age_days}日")
    else:
        print("  · org データ取得失敗（個人アカウントまたは API エラー）")

    # ── Layer 1 ──────────────────────────────
    print_section("LAYER 1: メタデータフィルタ")
    l1_pass, l1_reasons = apply_layer1(repo, layer1_cfg, reference_date=reference_date)
    repo.layer1_pass = l1_pass
    repo.layer1_reasons = l1_reasons
    print_reasons(l1_reasons)
    print(f"\n  結果: {verdict_icon(l1_pass)}")

    if not l1_pass:
        print("\n  ⚠ Layer1 で除外されました。パイプライン終了。")
        print_final_verdict(repo)
        return

    # ── Layer 2 ──────────────────────────────
    print_section("LAYER 2: コンテンツスコアリング")
    l2_pass, l2_reasons, score = apply_layer2(
        repo, layer2_cfg, readme, root_files, org_age_days=org_age_days
    )
    repo.layer2_pass = l2_pass
    repo.layer2_reasons = l2_reasons
    repo.score = score
    print_reasons(l2_reasons)
    print(f"\n  スコア: {score:.2f}  (閾値: {layer2_cfg.get('min_score', 0.5)})")
    print(f"  結果: {verdict_icon(l2_pass)}")

    if not l2_pass:
        print("\n  ⚠ Layer2 で除外されました。パイプライン終了。")
        print_final_verdict(repo)
        return

    # ── Layer 3 ──────────────────────────────
    print_section("LAYER 3: スタートアップ判定 [Haiku]")
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("  ⚠ ANTHROPIC_API_KEY が未設定です。Layer3/4 をスキップします。")
        print_final_verdict(repo)
        return

    client = anthropic.Anthropic(api_key=api_key)
    print("  Claude Haiku に問い合わせ中...")

    l3_pass, l3_reasons, l3_conf = apply_layer3(repo, layer3_cfg, client)
    repo.layer3_pass = l3_pass
    repo.layer3_reasons = l3_reasons
    repo.layer3_confidence = l3_conf
    print_reasons(l3_reasons)
    print(f"\n  結果: {verdict_icon(l3_pass)}")

    if not l3_pass:
        print("\n  ⚠ Layer3 で除外されました。パイプライン終了。")
        print_final_verdict(repo)
        return

    # ── Layer 4 ──────────────────────────────
    print_section("LAYER 4: VC 品質評価 [Sonnet]")
    print("  Claude Sonnet に問い合わせ中...")

    l4_pass, l4_reasons, l4_conf, l4_action = apply_layer4(
        repo, layer4_cfg, prefs, client
    )
    repo.layer4_pass = l4_pass
    repo.layer4_reasons = l4_reasons
    repo.layer4_confidence = l4_conf
    repo.layer4_suggested_action = l4_action
    print_reasons(l4_reasons)
    print(f"\n  結果: {verdict_icon(l4_pass)}")

    # ── Final verdict ─────────────────────────
    print_final_verdict(repo)


def print_final_verdict(repo: RepoRecord):
    surfaced = repo.layer1_pass and repo.layer2_pass and repo.layer3_pass and repo.layer4_pass
    print(f"\n{BOLD_SEP}")
    if surfaced:
        print(f"  🎯 VERDICT: zeimee はあなたにトスアップされます ✅")
        if repo.layer4_suggested_action:
            print(f"  📌 推奨アクション: {repo.layer4_suggested_action}")
    else:
        failed_at = (
            "Layer1" if not repo.layer1_pass
            else "Layer2" if not repo.layer2_pass
            else "Layer3" if not repo.layer3_pass
            else "Layer4"
        )
        print(f"  ❌ VERDICT: zeimee は {failed_at} で除外されます")
    print(BOLD_SEP)

    # サマリーテーブル
    print("\n  フィルタサマリー:")
    print(f"    Layer1 (メタデータ) : {verdict_icon(repo.layer1_pass)}")
    print(f"    Layer2 (スコア {repo.score:.2f})  : {verdict_icon(repo.layer2_pass)}")
    print(f"    Layer3 (Haiku)      : {verdict_icon(repo.layer3_pass)}  (確信度: {repo.layer3_confidence:.2f})")
    print(f"    Layer4 (Sonnet)     : {verdict_icon(repo.layer4_pass)}  (確信度: {repo.layer4_confidence:.2f})")
    print()


if __name__ == "__main__":
    main()
