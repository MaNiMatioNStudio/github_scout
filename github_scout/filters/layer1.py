"""Layer1: metadata-based rule filter (pure functions, no I/O)."""
from datetime import datetime, timezone
from typing import Optional

from ..models import RepoRecord


def apply_layer1(
    repo: RepoRecord,
    config: dict,
    reference_date: Optional[datetime] = None,
) -> tuple[bool, list[str]]:
    """
    Apply Layer1 metadata filters.

    Args:
        reference_date: Override "now" for time-travel testing. Defaults to UTC now.

    Returns:
        (passed, reasons) where reasons contains human-readable PASS/FAIL lines.
    """
    now = reference_date or datetime.now(timezone.utc)
    reasons: list[str] = []

    # --- Age ---
    max_age_days: int = config.get("max_age_days", 7)
    created = datetime.fromisoformat(repo.created_at.replace("Z", "+00:00"))
    age_days = (now - created).days
    if age_days > max_age_days:
        reasons.append(f"FAIL: age {age_days}d exceeds max {max_age_days}d")
        return False, reasons
    reasons.append(f"PASS: age {age_days}d within {max_age_days}d")

    # --- Stars ---
    min_stars: int = config.get("min_stars", 0)
    max_stars: int = config.get("max_stars", 20)
    if not (min_stars <= repo.stars <= max_stars):
        reasons.append(f"FAIL: stars {repo.stars} not in [{min_stars}, {max_stars}]")
        return False, reasons
    reasons.append(f"PASS: stars {repo.stars} in [{min_stars}, {max_stars}]")

    # --- Language ---
    allowed_languages: list[str] = config.get("allowed_languages", [])
    if allowed_languages and repo.language not in allowed_languages:
        reasons.append(f"FAIL: language '{repo.language}' not in allowed list")
        return False, reasons
    reasons.append(f"PASS: language '{repo.language}'")

    # --- Exclude keywords in name / description ---
    exclude_keywords: list[str] = [kw.lower() for kw in config.get("exclude_keywords", [])]
    combined_text = f"{repo.name} {repo.description or ''}".lower()
    matched = [kw for kw in exclude_keywords if kw in combined_text]
    if matched:
        reasons.append(f"FAIL: exclude keywords matched: {matched}")
        return False, reasons
    reasons.append("PASS: no exclude keywords in name/description")

    return True, reasons
