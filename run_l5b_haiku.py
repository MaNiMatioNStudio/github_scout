#!/usr/bin/env python3
"""
L5b Haiku extractor — 企業名・代表者名をClaude Haikuで正確に抽出

設計原則:
  - 呼ぶ前に最悪コストをチェック (can_afford)
  - max_tokens=200 で出力コストの上限を固定
  - BudgetExceeded は絶対に握りつぶさない
  - リトライなし（コスト二重消費を防ぐため）

GiveUpPolicy（1件あたり最大3ステップ、コストベース）:
  Step1: ルールベース (og:site_name/title/©)          → $0
  Step2: Haiku (LPテキスト全体)                       → ~$0.002
  Step3: Haiku (/company または /about ページ追加取得) → ~$0.002追加
  諦め : manual_needed=True（上限 $0.006/件 or 予算不足）

Usage:
    python run_l5b_haiku.py --source output/filtered_repos_*.jsonl
    python run_l5b_haiku.py --source output/filtered_repos_*.jsonl --repo zeimee-lp
"""
import json
import logging
import os
import re
import sys
from pathlib import Path

import requests
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

MODEL           = "claude-haiku-4-5-20251001"
MAX_OUTPUT      = 200
LP_CHAR_LIMIT   = 8000
PER_REPO_BUDGET = 0.006   # 1件あたりの上限（これを超えたら手動確認）

SYSTEM_PROMPT = """あなたは企業情報抽出の専門家です。
LPのテキストから以下の情報をJSONのみで返してください。余計な説明は不要です。

{
  "company_name": "法人名（例: 株式会社〇〇）。見つからない場合はnull",
  "founders": "代表者名と役職（例: 山田太郎（CEO）/ 鈴木一郎（CTO））。見つからない場合はnull"
}"""

# /company, /about など追加で探索するパス
_COMPANY_PATHS = ["/company", "/about", "/about-us", "/corporate", "/会社概要"]


def parse_args():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--source", required=True)
    p.add_argument("--repo",   help="対象repo名（省略時はlayer5_lp_url設定済みの全件）")
    p.add_argument("--budget", type=float, default=0.008)
    p.add_argument("--yes", "-y", action="store_true")
    return p.parse_args()


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _fetch_text(url: str, char_limit: int = LP_CHAR_LIMIT) -> str:
    from github_scout.filters.layer5 import _fetch_html, _parse_html
    html = _fetch_html(url)
    if not html:
        return ""
    parsed = _parse_html(html)
    return " ".join(parsed._text_parts)[:char_limit]


def _fetch_company_page(base_url: str) -> str:
    """base_url の /company, /about 等を試してテキストを返す。"""
    from urllib.parse import urlparse
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    for path in _COMPANY_PATHS:
        text = _fetch_text(origin + path, char_limit=4000)
        if len(text) > 200:
            logger.info("Company page found: %s%s (%d chars)", origin, path, len(text))
            return text
    return ""


# ── Haiku call ────────────────────────────────────────────────────────────────

def _call_haiku(client: Anthropic, lp_text: str, tracker) -> dict:
    """
    Haikuで企業名・代表者名を抽出する。

    1. can_afford() で事前チェック
    2. API呼び出し (max_tokens固定)
    3. 実績コストを記録
    """
    from github_scout.cost_tracker import BudgetExceeded

    user_content = (
        f"以下はサービスLPのテキストです:\n\n{lp_text}\n\n"
        "会社名と代表者名をJSONで抽出してください。"
    )
    est_input = len(SYSTEM_PROMPT) + len(user_content)  # 保守的: 1文字=1トークン

    worst_case = tracker.estimate_call_cost(MODEL, est_input, MAX_OUTPUT)
    logger.info("最悪コスト見積もり: $%.5f (入力≈%d tokens)", worst_case, est_input)

    if not tracker.can_afford(worst_case):
        raise BudgetExceeded(tracker.spent, tracker.budget)

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_OUTPUT,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )

    actual = tracker.record(MODEL, response.usage.input_tokens, response.usage.output_tokens)
    logger.info("実際のコスト: $%.5f (入力=%d, 出力=%d)", actual, response.usage.input_tokens, response.usage.output_tokens)

    return {"text": response.content[0].text, "cost_usd": actual}


def _parse_response(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text).strip()
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return {}


# ── GiveUpPolicy ──────────────────────────────────────────────────────────────

def _extract_with_giveup_policy(client: Anthropic, rec: dict, tracker) -> dict:
    """
    3ステップのGiveUpPolicyで企業名・代表者名を抽出する。

    Returns dict with: company_name, founders, layer5b_cost_usd, layer5_manual_needed
    """
    from github_scout.cost_tracker import BudgetExceeded
    from github_scout.filters.layer5 import _extract_company, _extract_founder, _parse_html, _fetch_html

    lp_url    = rec["layer5_lp_url"]
    repo_cost = 0.0

    # ── Step1: ルールベース（$0） ─────────────────────────────────────────────
    html = _fetch_html(lp_url)
    if html:
        parsed  = _parse_html(html)
        company = _extract_company(html, parsed)
        founder = _extract_founder(parsed.get_text())
        if company and founder:
            logger.info("Step1 (rule-based) 成功: %s / %s", company, founder)
            return {
                "layer5_company_name":  company,
                "layer5_founder_name":  founder,
                "layer5b_cost_usd":     0.0,
                "layer5_manual_needed": False,
            }

    # ── Step2: Haiku (LPテキスト全体) ─────────────────────────────────────────
    if tracker.remaining < PER_REPO_BUDGET * 0.5:
        logger.info("予算残り不足のためStep2をスキップ: $%.5f", tracker.remaining)
        return _manual_result(repo_cost)

    lp_text = " ".join(_parse_html(html)._text_parts)[:LP_CHAR_LIMIT] if html else ""
    if not lp_text:
        return _manual_result(repo_cost)

    try:
        result2 = _call_haiku(client, lp_text, tracker)
        repo_cost += result2["cost_usd"]
    except BudgetExceeded:
        raise
    except Exception as exc:
        logger.warning("Step2 Haiku失敗: %s", exc)
        return _manual_result(repo_cost)

    parsed2  = _parse_response(result2["text"])
    company2 = parsed2.get("company_name") or ""
    founder2 = parsed2.get("founders") or ""

    if company2 and founder2:
        logger.info("Step2 (Haiku LP) 成功: %s / %s", company2, founder2)
        return {
            "layer5_company_name":  company2,
            "layer5_founder_name":  founder2,
            "layer5b_cost_usd":     round(repo_cost, 6),
            "layer5_manual_needed": False,
        }

    # ── Step3: Haiku (/company /about ページ) ─────────────────────────────────
    if repo_cost >= PER_REPO_BUDGET or tracker.remaining < PER_REPO_BUDGET * 0.5:
        logger.info("コスト上限 or 予算残り不足のためStep3をスキップ")
        # Step2で片方だけ取れた場合はそれを使う
        if company2 or founder2:
            return {
                "layer5_company_name":  company2,
                "layer5_founder_name":  founder2,
                "layer5b_cost_usd":     round(repo_cost, 6),
                "layer5_manual_needed": not (company2 and founder2),
            }
        return _manual_result(repo_cost)

    company_text = _fetch_company_page(lp_url)
    if not company_text:
        return {
            "layer5_company_name":  company2,
            "layer5_founder_name":  founder2,
            "layer5b_cost_usd":     round(repo_cost, 6),
            "layer5_manual_needed": not (company2 and founder2),
        }

    try:
        result3 = _call_haiku(client, company_text, tracker)
        repo_cost += result3["cost_usd"]
    except BudgetExceeded:
        raise
    except Exception as exc:
        logger.warning("Step3 Haiku失敗: %s", exc)
        return {
            "layer5_company_name":  company2,
            "layer5_founder_name":  founder2,
            "layer5b_cost_usd":     round(repo_cost, 6),
            "layer5_manual_needed": not (company2 and founder2),
        }

    parsed3  = _parse_response(result3["text"])
    company3 = parsed3.get("company_name") or company2
    founder3 = parsed3.get("founders") or founder2

    logger.info("Step3 (Haiku company page) 結果: %s / %s", company3, founder3)
    return {
        "layer5_company_name":  company3,
        "layer5_founder_name":  founder3,
        "layer5b_cost_usd":     round(repo_cost, 6),
        "layer5_manual_needed": not (company3 and founder3),
    }


def _manual_result(cost_so_far: float) -> dict:
    return {
        "layer5_company_name":  "",
        "layer5_founder_name":  "",
        "layer5b_cost_usd":     round(cost_so_far, 6),
        "layer5_manual_needed": True,
    }


# ── JSONL update (atomic) ─────────────────────────────────────────────────────

def _update_jsonl(source_path: Path, updates: dict) -> None:
    """updates = {url: {field: value}} で JSONL をアトミックに更新する。"""
    import os
    records = []
    with open(source_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("url") in updates:
                rec.update(updates[rec["url"]])
            records.append(rec)

    tmp_path = source_path.with_suffix(".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        os.replace(str(tmp_path), str(source_path))
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise

    logger.info("JSONL更新完了 (atomic): %s (%d件)", source_path.name, len(records))


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    from github_scout.cost_tracker import BudgetExceeded, CostTracker

    args = parse_args()
    source_path = Path(args.source)
    if not source_path.exists():
        logger.error("ファイルが見つかりません: %s", source_path)
        sys.exit(1)

    all_records = [json.loads(l) for l in source_path.read_text().splitlines() if l.strip()]
    targets = [
        r for r in all_records
        if r.get("layer5_lp_url")
        and (not args.repo or r.get("name") == args.repo)
    ]

    if not targets:
        logger.info("処理対象なし。--repo または layer5_lp_url を確認してください。")
        sys.exit(0)

    tracker = CostTracker(budget=args.budget)

    print(f"\n{'='*58}")
    print(f"  L5b Haiku 企業情報抽出 (GiveUpPolicy 3-step)")
    print(f"  対象     : {len(targets)} 件")
    print(f"  モデル   : {MODEL}  max_tokens={MAX_OUTPUT}")
    print(f"  予算上限 : ${args.budget:.3f}  (1件上限: ${PER_REPO_BUDGET:.3f})")
    for r in targets:
        print(f"  └ {r['name']} → {r['layer5_lp_url']}")
    print(f"{'='*58}")

    if not args.yes:
        ans = input("\n  Continue? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("Aborted.")
            sys.exit(0)
    print()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY が設定されていません")
        sys.exit(1)
    client = Anthropic(api_key=api_key)

    updates = {}
    for rec in targets:
        name = rec["name"]
        logger.info("処理中: %s (%s)", name, rec["layer5_lp_url"])

        try:
            result = _extract_with_giveup_policy(client, rec, tracker)
        except BudgetExceeded as e:
            logger.error("予算超過で中断: 消費=$%.5f / 上限=$%.3f", e.spent, e.limit)
            print(f"\n⚠️  予算上限 ${args.budget:.3f} に達したため中断しました。")
            print(f"   消費済み: ${e.spent:.5f}")
            break
        except Exception as exc:
            logger.warning("抽出エラー (%s): %s", name, exc)
            continue

        updates[rec["url"]] = result

        status = "✅" if not result["layer5_manual_needed"] else "🔍"
        logger.info(
            "  %s 企業=%s  代表=%s  コスト=$%.5f",
            status,
            result["layer5_company_name"] or "不明",
            result["layer5_founder_name"] or "不明",
            result["layer5b_cost_usd"],
        )

    if updates:
        _update_jsonl(source_path, updates)

    manual = sum(1 for v in updates.values() if v.get("layer5_manual_needed"))
    print(f"\n{'='*58}")
    print(f"  完了: {len(updates)} 件更新  (手動確認要: {manual} 件)")
    print(f"  総コスト: ${tracker.spent:.5f} / 上限 ${args.budget:.3f}")
    print(f"{'='*58}\n")


if __name__ == "__main__":
    main()
