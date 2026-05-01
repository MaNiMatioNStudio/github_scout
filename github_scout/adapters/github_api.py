import base64
import logging
import time
from typing import Iterator, Optional

import requests

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://api.github.com/search/repositories"
_REPOS_URL = "https://api.github.com/repos"


class GitHubAdapter:
    def __init__(self, token: Optional[str] = None):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def search_repos(
        self,
        query: str,
        max_results: int = 5000,
        per_page: int = 100,
    ) -> Iterator[dict]:
        """Yield raw repo dicts from GitHub Search API with pagination."""
        page = 1
        total_fetched = 0

        while total_fetched < max_results:
            batch_size = min(per_page, max_results - total_fetched)
            data = self._get(
                _SEARCH_URL,
                params={
                    "q": query,
                    "sort": "updated",
                    "order": "desc",
                    "per_page": batch_size,
                    "page": page,
                },
            )

            items = data.get("items", [])
            if not items:
                break

            for item in items:
                yield item
                total_fetched += 1
                if total_fetched >= max_results:
                    return

            if len(items) < batch_size:
                break

            page += 1
            # GitHub Search API: 30 req/min authenticated → ~2s per page is safe
            time.sleep(2)

    def get_readme(self, owner: str, repo: str, max_bytes: int = 5000) -> Optional[str]:
        """Return first `max_bytes` characters of README, or None on failure."""
        try:
            data = self._get(f"{_REPOS_URL}/{owner}/{repo}/readme")
            content = data.get("content", "")
            if content:
                decoded = base64.b64decode(content).decode("utf-8", errors="ignore")
                return decoded[:max_bytes]
        except Exception as exc:
            logger.warning("README fetch failed for %s/%s: %s", owner, repo, exc)
        return None

    def get_org(self, org_name: str) -> dict:
        """Return organization metadata (created_at, type, etc.)."""
        try:
            return self._get(f"https://api.github.com/orgs/{org_name}")
        except Exception as exc:
            logger.warning("Org fetch failed for %s: %s", org_name, exc)
        return {}

    def get_owner_profile(self, owner: str) -> dict:
        """Return owner's location/blog/bio for Japan affinity detection."""
        for endpoint in (
            f"https://api.github.com/orgs/{owner}",
            f"https://api.github.com/users/{owner}",
        ):
            try:
                data = self._get(endpoint)
                if data:
                    return {
                        "location":    data.get("location")    or "",
                        "blog":        data.get("blog")        or "",
                        "bio":         data.get("bio")         or "",
                        "description": data.get("description") or "",
                    }
            except Exception:
                pass
        return {}

    def get_owner_repos(self, owner: str, max_repos: int = 50) -> list:
        """Return list of repo dicts for an owner (user or org)."""
        for endpoint in (f"https://api.github.com/orgs/{owner}/repos",
                         f"https://api.github.com/users/{owner}/repos"):
            try:
                data = self._get(endpoint, params={"per_page": max_repos, "sort": "updated"})
                if isinstance(data, list) and data:
                    return data
            except Exception:
                pass
        return []

    def get_repo_homepage(self, owner: str, repo: str) -> Optional[str]:
        """Return the homepage URL set on the repo, or None."""
        try:
            data = self._get(f"{_REPOS_URL}/{owner}/{repo}")
            return data.get("homepage") or None
        except Exception as exc:
            logger.warning("get_repo_homepage failed for %s/%s: %s", owner, repo, exc)
        return None

    def count_repos(self, query: str) -> int:
        """Return GitHub's total_count for a search query (fetches 1 result only)."""
        try:
            data = self._get(_SEARCH_URL, params={"q": query, "per_page": 1, "page": 1})
            return int(data.get("total_count", 0))
        except Exception as exc:
            logger.warning("count_repos failed: %s", exc)
            return 0

    def get_file_content(self, owner: str, repo: str, path: str) -> Optional[str]:
        """Return decoded text content of a file in the repo, or None on failure."""
        import base64
        try:
            data = self._get(f"{_REPOS_URL}/{owner}/{repo}/contents/{path}")
            encoded = data.get("content", "")
            if encoded:
                return base64.b64decode(encoded).decode("utf-8", errors="ignore")
        except Exception as exc:
            logger.warning("get_file_content failed for %s/%s/%s: %s", owner, repo, path, exc)
        return None

    def get_root_files(self, owner: str, repo: str) -> list[str]:
        """Return list of file/directory names at the repo root."""
        try:
            items = self._get(f"{_REPOS_URL}/{owner}/{repo}/contents")
            if isinstance(items, list):
                return [item["name"] for item in items]
        except Exception as exc:
            logger.warning("Contents fetch failed for %s/%s: %s", owner, repo, exc)
        return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, url: str, params: Optional[dict] = None, retries: int = 3) -> dict:
        for attempt in range(retries):
            try:
                resp = self.session.get(url, params=params, timeout=15)

                if resp.status_code == 403:
                    # Rate-limited: wait until reset
                    reset_at = int(resp.headers.get("X-RateLimit-Reset", 0))
                    wait = max(reset_at - time.time(), 0) + 2
                    logger.warning("Rate limited. Waiting %.0fs…", wait)
                    time.sleep(wait)
                    continue

                if resp.status_code == 404:
                    return {}

                if resp.status_code == 422:
                    # Search API returns 422 when paginating beyond 1000 results
                    logger.warning("422 from GitHub (beyond 1000 result limit): %s", url)
                    return {}

                resp.raise_for_status()
                return resp.json()

            except requests.exceptions.RequestException as exc:
                if attempt == retries - 1:
                    raise
                wait = 2 ** (attempt + 1)
                logger.warning("Request error (%s), retrying in %ds…", exc, wait)
                time.sleep(wait)

        return {}
