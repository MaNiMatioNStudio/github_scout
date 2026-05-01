"""Layer3: Rule-based Japan affinity scoring (no AI cost)."""
import re
from typing import Optional, Tuple

from ..models import RepoRecord

# ひらがな (U+3040-U+309F) + カタカナ (U+30A0-U+30FF)
_JP_CHAR = re.compile(r"[\u3040-\u309F\u30A0-\u30FF]")

# .jp ドメインを含むURL
_JP_DOMAIN = re.compile(r"https?://[^\s<>\"']*\.jp(?:[/\s<>\"']|$)", re.IGNORECASE)

# locationフィールドに含まれる日本関連キーワード
_JP_LOCATION_KEYWORDS = [
    "japan", "tokyo", "osaka", "kyoto", "nagoya", "yokohama",
    "sapporo", "fukuoka", "kobe", "hiroshima", "sendai",
    "日本", "東京", "大阪", "京都", "名古屋", "横浜", "札幌", "福岡",
]


def _has_jp_char(text: Optional[str]) -> bool:
    return bool(_JP_CHAR.search(text or ""))


def _has_jp_domain(text: Optional[str]) -> bool:
    return bool(_JP_DOMAIN.search(text or ""))


def _is_jp_location(location: Optional[str]) -> bool:
    loc = (location or "").lower()
    return any(kw.lower() in loc for kw in _JP_LOCATION_KEYWORDS)


def apply_layer3(
    repo: RepoRecord,
    readme: Optional[str],
    homepage: Optional[str],
    owner_profile: Optional[dict],
    config: dict,
) -> Tuple[str, list, int]:
    """
    Rule-based Japan affinity scoring.

    Returns:
        (result, reasons, score)
        result:
          "pass"      — score >= pass_threshold  → Japan確定
          "uncertain" — fail_threshold <= score < pass_threshold
          "fail"      — score < fail_threshold   → Japan接点なし
    """
    pass_threshold: int = config.get("pass_threshold", 3)
    fail_threshold: int = config.get("fail_threshold", 1)

    score = 0
    signals: list = []

    # ── Tier 1: 追加APIコストなし ────────────────────────────────────────────

    if _has_jp_char(repo.description):
        score += 3
        signals.append("descriptionに日本語文字 (+3)")

    if _has_jp_char(repo.name):
        score += 2
        signals.append("repo名に日本語文字 (+2)")

    if _has_jp_char(readme):
        score += 3
        signals.append("READMEに日本語文字 (+3)")

    if _has_jp_domain(homepage):
        score += 3
        signals.append(f"homepageに.jpドメイン (+3): {homepage}")

    if _has_jp_domain(readme):
        score += 2
        signals.append("README内に.jpドメインURL (+2)")

    # ── Tier 2: ownerプロフィール（fetch_owner_profile=True 時のみ） ──────────

    if owner_profile:
        location = owner_profile.get("location", "")
        if _is_jp_location(location):
            score += 4
            signals.append(f"ownerのlocationが日本 (+4): {location}")

        blog = owner_profile.get("blog", "")
        if _has_jp_domain(blog):
            score += 3
            signals.append(f"ownerのblogに.jpドメイン (+3): {blog}")

        if _has_jp_char(owner_profile.get("bio")) or _has_jp_char(owner_profile.get("description")):
            score += 3
            signals.append("ownerのbio/descriptionに日本語文字 (+3)")

    # ── 判定 ─────────────────────────────────────────────────────────────────

    if score >= pass_threshold:
        result = "pass"
        summary = f"PASS: Japan score={score} (≥{pass_threshold})"
    elif score < fail_threshold:
        result = "fail"
        summary = f"FAIL: Japan score={score} (<{fail_threshold})"
    else:
        result = "uncertain"
        summary = f"UNCERTAIN: Japan score={score}"

    reasons = [summary] + signals
    return result, reasons, score
