"""Layer2: content-based rule filter (pure functions, no I/O)."""
import fnmatch
from typing import Optional

from ..models import RepoRecord


def apply_layer2(
    repo: RepoRecord,
    config: dict,
    readme: Optional[str],
    root_files: list[str],
    org_age_days: Optional[int] = None,
) -> tuple[bool, list[str], float]:
    """
    Apply Layer2 content filters.

    Args:
        org_age_days: Age of the owner org in days. None = unknown / individual account.

    Returns:
        (passed, reasons, score)
    """
    reasons: list[str] = []
    score: float = 0.0

    # --- README existence ---
    if readme is not None:
        reasons.append("PASS: README found")
        score += 1.0
    else:
        reasons.append("INFO: README not found")

    # --- Required / signal files ---
    required_files: list[str] = config.get("required_files", [])
    signal_files: list[str] = config.get("signal_files", [])

    matched_required = _match_patterns(root_files, required_files)
    matched_signal = _match_patterns(root_files, signal_files)

    if matched_required:
        reasons.append(f"PASS: required files found: {matched_required}")
        score += len(matched_required) * 0.5
    else:
        reasons.append("INFO: no required files found")

    if matched_signal:
        reasons.append(f"PASS: signal files found: {matched_signal}")
        score += len(matched_signal) * 0.3

    # --- LP signal (HTML repo with multiple branded HTML files) ---
    lp_bonus: float = config.get("lp_signal_bonus", 1.0)
    if repo.language == "HTML" and _has_lp_signal(root_files):
        reasons.append(f"PASS: LP signal (HTML + branded files) → +{lp_bonus}")
        score += lp_bonus

    # --- New org signal ---
    new_org_max_days: int = config.get("new_org_max_days", 180)
    new_org_bonus: float = config.get("new_org_bonus", 1.0)
    if org_age_days is not None and org_age_days <= new_org_max_days:
        reasons.append(f"PASS: new org ({org_age_days}d old ≤ {new_org_max_days}d) → +{new_org_bonus}")
        score += new_org_bonus
    elif org_age_days is not None:
        reasons.append(f"INFO: existing org ({org_age_days}d old)")

    # --- Description quality ---
    desc_min: int = config.get("description_min_length", 10)
    desc_bonus: float = config.get("description_quality_bonus", 0.5)
    if repo.description and len(repo.description) >= desc_min:
        reasons.append(f"PASS: meaningful description ({len(repo.description)} chars) → +{desc_bonus}")
        score += desc_bonus

    # --- Positive keywords in README + description ---
    search_text = f"{readme or ''} {repo.description or ''}".lower()

    positive_keywords: list[str] = [kw.lower() for kw in config.get("positive_keywords", [])]
    matched_pos = [kw for kw in positive_keywords if kw in search_text]
    if matched_pos:
        reasons.append(f"PASS: positive keywords: {matched_pos}")
        score += len(matched_pos) * 0.5

    # --- Negative keywords ---
    negative_keywords: list[str] = [kw.lower() for kw in config.get("negative_keywords", [])]
    matched_neg = [kw for kw in negative_keywords if kw in search_text]
    if matched_neg:
        reasons.append(f"FAIL: negative keywords: {matched_neg}")
        score -= len(matched_neg) * 1.0

    # --- Score threshold ---
    min_score: float = config.get("min_score", 0.5)
    passed = score >= min_score

    if passed:
        reasons.append(f"PASS: score {score:.2f} >= threshold {min_score}")
    else:
        reasons.append(f"FAIL: score {score:.2f} < threshold {min_score}")

    return passed, reasons, score


def _has_lp_signal(files: list[str]) -> bool:
    """True if this looks like a product landing page (not just any HTML file)."""
    html_files = [f for f in files if f.endswith(".html")]
    # Multiple HTML files, or a named HTML file beyond just index.html
    return len(html_files) >= 2 or any(f != "index.html" for f in html_files)


def _match_patterns(files: list[str], patterns: list[str]) -> list[str]:
    """Return patterns that matched at least one file in `files`."""
    matched: list[str] = []
    for pattern in patterns:
        for f in files:
            if fnmatch.fnmatch(f, pattern) or f == pattern:
                matched.append(pattern)
                break
    return matched
