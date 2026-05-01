"""Main processing pipeline: fetch → Layer1 → Layer2 → Layer3 → yield RepoRecord."""
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Iterator, Optional

from .adapters.github_api import GitHubAdapter
from .filters.layer1 import apply_layer1
from .filters.layer2 import apply_layer2
from .filters.layer3 import apply_layer3
from .models import RepoRecord

logger = logging.getLogger(__name__)

# GitHub Search API hard cap per query
_API_MAX_PER_QUERY = 1000


def run_pipeline(
    adapter: GitHubAdapter,
    layer1_config: dict,
    layer2_config: dict,
    date_from: str,
    date_to: str,
    max_results: int = 5000,
    layer3_config: Optional[dict] = None,
    skip_urls: Optional[set] = None,
    name_contains: str = "",
    time_from: Optional[str] = None,
    time_to: Optional[str] = None,
    reference_date: Optional[datetime] = None,
) -> Iterator[RepoRecord]:
    """
    Fetch repos and run through all active layers.

    - Date range is split into 1-day chunks (GitHub Search API cap: 1000/query).
    - time_from / time_to ("HH:MM:SS") を指定すると単一の datetime クエリになる。
      例: date_from="2026-04-16", time_from="10:00:00", time_to="10:05:00"
      → created:2026-04-16T10:00:00..2026-04-16T10:05:00
    - Yields every repo (pass and fail) for full funnel visibility.
    """
    # ── クエリスロットのリストを構築 ──────────────────────────────────────────
    if time_from and time_to:
        dt_from = f"{date_from}T{time_from}"
        dt_to   = f"{date_to}T{time_to}"
        slots = [(f"{dt_from}~{dt_to}", f"created:{dt_from}..{dt_to}")]
        ref_default = datetime.fromisoformat(dt_to + "+00:00")
    else:
        days = _split_days(date_from, date_to)
        slots = [(day, f"created:{day}..{day}") for day in days]
        ref_default = None

    seen: set = set()
    total_fetched = 0
    name_filter = f"{name_contains} in:name " if name_contains else ""

    for label, date_range in slots:
        if max_results and total_fetched >= max_results:
            break

        query = f"{name_filter}{date_range} fork:false archived:false is:public"
        per_day_limit = min(
            _API_MAX_PER_QUERY,
            max_results - total_fetched if max_results else _API_MAX_PER_QUERY,
        )
        logger.info("Query [%s]: %s (limit=%d)", label, query, per_day_limit)

        if time_from and time_to:
            ref = reference_date or ref_default
        else:
            ref = reference_date or datetime.fromisoformat(label + "T23:59:59+00:00")

        for raw in adapter.search_repos(query, max_results=per_day_limit):
            repo_id = raw.get("full_name") or f"{raw['owner']['login']}/{raw['name']}"
            if repo_id in seen:
                continue
            seen.add(repo_id)

            repo_url = raw.get("html_url", "")
            if skip_urls and repo_url in skip_urls:
                total_fetched += 1
                continue

            total_fetched += 1
            repo = _raw_to_record(raw)

            # ── Layer1 ──────────────────────────────────────────────────────
            l1_pass, l1_reasons = apply_layer1(repo, layer1_config, reference_date=ref)
            repo.layer1_pass = l1_pass
            repo.layer1_reasons = l1_reasons
            if not l1_pass:
                yield repo
                continue

            # ── Layer2 ──────────────────────────────────────────────────────
            org_age_days = _fetch_org_age(adapter, repo.owner, raw)
            readme = adapter.get_readme(repo.owner, repo.name)
            root_files = adapter.get_root_files(repo.owner, repo.name)
            l2_pass, l2_reasons, score = apply_layer2(
                repo, layer2_config, readme, root_files, org_age_days=org_age_days
            )
            repo.layer2_pass = l2_pass
            repo.layer2_reasons = l2_reasons
            repo.score = score
            repo.site_url = _infer_site_url(repo.homepage, readme, root_files, repo.name)
            if not repo.site_url:
                html_files = [f for f in root_files
                              if any(f.lower().endswith(ext) for ext in _HTML_EXTS)]
                if html_files:
                    html_content = adapter.get_file_content(repo.owner, repo.name, html_files[0])
                    if html_content:
                        repo.site_url = _extract_url_from_html(html_content, repo.name)
            if not l2_pass:
                yield repo
                continue

            # ── Layer3: Japan affinity filter ────────────────────────────────
            homepage = raw.get("homepage") or None
            owner_profile = None
            if layer3_config and layer3_config.get("fetch_owner_profile", True):
                owner_profile = adapter.get_owner_profile(repo.owner)
            l3_result, l3_reasons, l3_score = apply_layer3(
                repo, readme, homepage, owner_profile, layer3_config or {}
            )
            repo.layer3_result  = l3_result
            repo.layer3_score   = l3_score
            repo.layer3_reasons = l3_reasons

            yield repo


_EXCLUDE_URL_PATTERNS = (
    "github.com", "shields.io", "badge", "travis-ci", "circleci",
    "codecov.io", "snyk.io", "gitter.im", "npmjs.com", "pypi.org",
    "cdn.", "fonts.googleapis", "tailwindcss.com", "unpkg.com", "jsdelivr.net",
)

_URL_RE      = re.compile(r'https://[^\s\)\]\"\'<>\\]+')
_OG_URL_RE   = re.compile(r'og:url[^>]*content=["\']([^"\']+)', re.IGNORECASE)
_CANON_RE    = re.compile(r'<link[^>]*rel=["\']canonical["\'][^>]*href=["\']([^"\']+)', re.IGNORECASE)
_VERCEL_RE   = re.compile(r'https://[^\s"\'<>]*\.vercel\.app[^\s"\'<>]*')
_HTML_EXTS   = {".html", ".htm"}


def _infer_site_url(homepage: str, readme: Optional[str], root_files: list, repo_name: str) -> str:
    """優先順: homepage > README URL > vercel.json推定。HTMLフォールバックは別関数。"""
    if homepage:
        return homepage
    if readme:
        for url in _URL_RE.findall(readme):
            url = url.rstrip(".,;)/")
            if any(pat in url for pat in _EXCLUDE_URL_PATTERNS):
                continue
            if url.startswith("https://"):
                return url
    if "vercel.json" in root_files:
        return f"https://{repo_name}.vercel.app"
    return ""


def _extract_url_from_html(html: str, repo_name: str) -> str:
    """HTMLソースからデプロイ先URLを抽出する。og:url > canonical > vercel.app URL の順。"""
    for pattern in (_OG_URL_RE, _CANON_RE):
        m = pattern.search(html)
        if m:
            url = m.group(1).strip()
            if url.startswith("https://") and "github.com" not in url:
                return url
    for url in _VERCEL_RE.findall(html):
        url = url.rstrip(".,;)/\"'")
        if any(pat in url for pat in _EXCLUDE_URL_PATTERNS):
            continue
        return url
    return ""


def _split_days(date_from: str, date_to: str) -> list:
    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    days = []
    current = start
    while current <= end:
        days.append(str(current))
        current += timedelta(days=1)
    return days


def _fetch_org_age(adapter: GitHubAdapter, owner: str, raw: dict) -> Optional[int]:
    if raw.get("owner", {}).get("type") != "Organization":
        return None
    org_data = adapter.get_org(owner)
    created_at = org_data.get("created_at")
    if not created_at:
        return None
    created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - created).days


def _raw_to_record(raw: dict) -> RepoRecord:
    return RepoRecord(
        name=raw["name"],
        owner=raw["owner"]["login"],
        url=raw["html_url"],
        created_at=raw["created_at"],
        language=raw.get("language"),
        stars=raw.get("stargazers_count", 0),
        description=raw.get("description") or "",
        homepage=raw.get("homepage") or "",
    )
