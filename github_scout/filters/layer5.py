"""Layer5: LP/サービスURL発見 (L5a) + 日本向け判定・企業情報抽出 (L5b)

完全ルールベース — Claude API不要、$0で動作。
手動確認が必要なケースは layer5_manual_needed フラグで通知。

L5a URL発見の優先順位:
  1. GitHub homepage フィールド
  2. description 内URL
  3. README 内URL
  4. HTMLファイル本文からURL抽出（HTML repoの場合）
  5. GitHub Pages URL推測 + 到達確認
  6. オーナーの他repo横断（backend/api repoの場合、対応するfrontend/landingを探す）
  → 全て失敗 → 手動確認フラグ

L5b ルールベース抽出:
  - 日本語文字（ひらがな/カタカナ/漢字）の比率で日本向け判定
  - og:site_name / title タグ / ©フッターから企業名
  - 代表取締役/CEO/Founder パターンから代表者名
"""
import base64
import logging
import re
from html.parser import HTMLParser
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_URL_RE  = re.compile(r'https?://[^\s\)\]\>\"\'`]+')
_JP_RE   = re.compile(r'[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff]')
_MAX_PAGE_CHARS = 6000

# URLとして無効なドメインパターン
_NOISE_DOMAINS = (
    "github.com", "shields.io", "travis-ci", "badge",
    "cdn.", "cdnjs.", "unpkg.com", "jsdelivr.net",
    "tailwindcss.com", "googleapis.com", "gstatic.com",
    "fontawesome.com", "bootstrapcdn.com", "jquery.com",
    "w3.org", "schema.org", "openstreetmap.org",
    "gravatar.com", "wp.com", "wordpress.com",
    "twitter.com/intent", "facebook.com/sharer",
    "linkedin.com/share", "t.co",
)

# repo名にこれらが含まれていたらbackend系と判断
_BACKEND_KEYWORDS = ("backend", "api", "server", "service", "microservice", "worker")
# frontend/landing系のキーワード
_FRONTEND_KEYWORDS = ("frontend", "front", "lp", "landing", "web", "app", "client", "ui")


# ── HTML parser ──────────────────────────────────────────────────────────────

class _HtmlParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._text_parts = []
        self._skip = False
        self.meta = {}   # property/name → content
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        if tag in ("script", "style", "noscript"):
            self._skip = True
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            key = attrs_d.get("property") or attrs_d.get("name", "")
            val = attrs_d.get("content", "")
            if key and val:
                self.meta[key.lower()] = val

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = False
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title and not self.title:
            self.title = data.strip()
        if not self._skip:
            s = data.strip()
            if s:
                self._text_parts.append(s)

    def get_text(self) -> str:
        return " ".join(self._text_parts)[:_MAX_PAGE_CHARS]


def _parse_html(html: str):
    p = _HtmlParser()
    try:
        p.feed(html)
    except Exception:
        pass
    return p


# ── URL discovery helpers ─────────────────────────────────────────────────────

def _urls_from_text(text: str, strict: bool = False) -> list:
    """URLを抽出。strict=Trueの場合はCDN/ライブラリ等を除外。"""
    urls = _URL_RE.findall(text)
    if strict:
        urls = [u for u in urls if not any(nd in u.lower() for nd in _NOISE_DOMAINS)]
    else:
        urls = [u for u in urls if "github.com" not in u and "shields.io" not in u]
    return urls


def _http_reachable(url: str, timeout: int = 6) -> bool:
    try:
        r = requests.head(url, timeout=timeout, allow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0"})
        return r.status_code < 400
    except Exception:
        return False


def _fetch_html(url: str, timeout: int = 10) -> Optional[str]:
    try:
        r = requests.get(url, timeout=timeout, allow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0 (compatible; StartupScout/1.0)"})
        r.raise_for_status()
        return r.text
    except Exception as exc:
        logger.debug("Fetch failed %s: %s", url, exc)
        return None


def _extract_urls_from_html_file(adapter, owner: str, repo: str) -> Optional[str]:
    """HTMLリポジトリのルートHTMLファイルからURLを抽出する。"""
    root_files = adapter.get_root_files(owner, repo)
    html_files = [f for f in root_files
                  if f.lower().endswith((".html", ".htm"))
                  and "landing" in f.lower() or "index" in f.lower()]
    # index.html を優先
    html_files.sort(key=lambda f: (0 if "index" in f.lower() else 1))

    for filename in html_files[:2]:
        try:
            data = adapter._get(f"https://api.github.com/repos/{owner}/{repo}/contents/{filename}")
            content = data.get("content", "")
            if content:
                raw = base64.b64decode(content).decode("utf-8", errors="ignore")
                urls = _urls_from_text(raw, strict=True)
                if urls:
                    logger.info("L5a: URL in HTML file (%s) → %s", filename, urls[0])
                    return urls[0]
        except Exception as exc:
            logger.debug("HTML file fetch failed %s/%s/%s: %s", owner, repo, filename, exc)
    return None


def _try_github_pages(owner: str, repo: str) -> Optional[str]:
    """GitHub Pages URL を推測して到達確認する。"""
    candidate = f"https://{owner.lower()}.github.io/{repo}/"
    if _http_reachable(candidate):
        logger.info("L5a: GitHub Pages reachable → %s", candidate)
        return candidate
    return None


def _find_sibling_repo_url(adapter, owner: str, repo_name: str) -> Optional[str]:
    """backend/api repoの場合、オーナーの他repoからfrontend/landingを探す。"""
    name_lower = repo_name.lower()
    if not any(kw in name_lower for kw in _BACKEND_KEYWORDS):
        return None

    # 基底名を取得（例: contractsense-backend → contractsense）
    base = name_lower
    for kw in _BACKEND_KEYWORDS:
        base = base.replace(f"-{kw}", "").replace(f"_{kw}", "")

    repos = adapter.get_owner_repos(owner)
    for r in repos:
        rname = r.get("name", "").lower()
        if rname == repo_name.lower():
            continue
        # 基底名が含まれ、かつfrontend/landing系のキーワードがある
        if base in rname and any(kw in rname for kw in _FRONTEND_KEYWORDS):
            hp = r.get("homepage")
            if hp and hp.startswith("http"):
                logger.info("L5a: sibling repo homepage (%s) → %s", r["name"], hp)
                return hp
            # GitHub Pages試行
            pages_url = _try_github_pages(owner, r["name"])
            if pages_url:
                return pages_url

    return None


# ── L5a: URL発見（メイン） ────────────────────────────────────────────────────

def discover_lp_url(owner: str, name: str, description: str,
                    language: str, adapter) -> tuple:
    """
    Returns (url_or_None, method_used)
    method_used: "homepage" | "description" | "readme" | "html_file" |
                 "github_pages" | "sibling_repo" | None
    """
    # ① GitHub homepage
    homepage = adapter.get_repo_homepage(owner, name)
    if homepage and homepage.startswith("http"):
        return homepage, "homepage"

    # ② description
    if description:
        urls = _urls_from_text(description, strict=True)
        if urls:
            return urls[0], "description"

    # ③ README
    readme = adapter.get_readme(owner, name) or ""
    if readme:
        urls = _urls_from_text(readme, strict=True)
        if urls:
            return urls[0], "readme"

    # ④ HTMLファイル本文（HTML repoのみ）
    if language and language.lower() == "html":
        url = _extract_urls_from_html_file(adapter, owner, name)
        if url:
            return url, "html_file"

    # ⑤ GitHub Pages
    url = _try_github_pages(owner, name)
    if url:
        return url, "github_pages"

    # ⑥ sibling repo（backend/api repoの場合）
    url = _find_sibling_repo_url(adapter, owner, name)
    if url:
        return url, "sibling_repo"

    return None, None


# ── L5b: ルールベース抽出 ─────────────────────────────────────────────────────

def _judge_japan(text: str, repo_description: str = "") -> tuple:
    """日本語文字比率で日本向け判定。Returns (is_japan: bool, confidence: float)"""
    combined = text + " " + repo_description
    jp = len(_JP_RE.findall(combined))
    total = max(len(combined.replace(" ", "")), 1)
    ratio = jp / total

    if ratio >= 0.15:
        return True, min(0.95, 0.70 + ratio)
    elif ratio >= 0.05:
        return True, 0.60 + ratio * 2
    elif ratio >= 0.01:
        return True, 0.50
    else:
        return False, max(0.10, 0.90 - ratio * 20)


def _extract_company(html: str, parsed: _HtmlParser) -> str:
    """og:site_name → og:title → title → © から企業名を抽出。"""
    # og:site_name
    val = parsed.meta.get("og:site_name", "")
    if val and len(val) < 60:
        return val.strip()

    # og:title / title → 最初のセグメント
    for key in ("og:title", "twitter:title"):
        val = parsed.meta.get(key, "")
        if val:
            seg = re.split(r'[|｜\-–—]', val)[0].strip()
            if 1 < len(seg) < 40:
                return seg

    if parsed.title:
        seg = re.split(r'[|｜\-–—]', parsed.title)[0].strip()
        if 1 < len(seg) < 40:
            return seg

    # © copyright
    m = re.search(r'[©&copy;]\s*(?:\d{4}[-–]\d{4}|\d{4})?\s*([^\n<,]{2,40})', html)
    if m:
        return m.group(1).strip()

    return ""


def _extract_founder(text: str) -> str:
    """代表者名を正規表現で抽出。"""
    patterns = [
        r'代表取締役(?:社長|CEO)?\s*[：:]\s*([\w　・\-]{2,20})',
        r'代表者\s*[：:]\s*([\w　・\-]{2,20})',
        r'代表\s*[：:]\s*([\w　・\-]{2,20})',
        r'創業者\s*[：:]\s*([\w　・\-]{2,20})',
        r'CEO\s*[：:\s]\s*([A-Za-z\u3040-\u9fff][A-Za-z\u3040-\u9fff\s]{1,20})',
        r'Founder\s*[：:\s]\s*([A-Za-z\u3040-\u9fff][A-Za-z\u3040-\u9fff\s]{1,20})',
        r'Founded by\s+([A-Za-z][A-Za-z\s]{1,25})',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""


# ── apply_layer5: メインエントリ ─────────────────────────────────────────────

def apply_layer5(repo, config: dict, adapter) -> tuple:
    """
    ルールベースのL5a + L5b。Claude API不要。

    Returns:
        (passed, reasons, confidence, lp_url, company_name, founder_name, manual_needed)
    """
    language = getattr(repo, "language", None) or ""

    # ── L5a ─────────────────────────────────────────────────────────────────
    lp_url, method = discover_lp_url(
        repo.owner, repo.name, repo.description, language, adapter
    )

    if not lp_url:
        reasons = [
            "URL: 未発見",
            "SKIP: homepage/README/HTMLファイル/GitHubPages/sibling repo — 全て該当なし",
            "ACTION: 手動でサービスURLを確認してください",
        ]
        return False, reasons, 0.0, "", "", "", True  # manual_needed=True

    logger.info("L5a: URL found via [%s] → %s", method, lp_url)

    # ── Fetch page ───────────────────────────────────────────────────────────
    html = _fetch_html(lp_url)
    if not html:
        reasons = [
            f"URL ({method}): {lp_url}",
            "FAIL: ページ取得失敗（タイムアウトまたは非公開）",
            "ACTION: 手動でURLを確認してください",
        ]
        return False, reasons, 0.0, lp_url, "", "", True

    parsed  = _parse_html(html)
    text    = parsed.get_text()

    # ── L5b: Japan判定 ───────────────────────────────────────────────────────
    is_japan, confidence = _judge_japan(text, repo.description)

    # ── L5b: 企業名・代表者 ──────────────────────────────────────────────────
    company = _extract_company(html, parsed)
    founder = _extract_founder(text)

    passed = is_japan and confidence >= config.get("confidence_threshold", 0.5)

    reasons = [
        f"URL ({method}): {lp_url}",
        f"{'PASS' if is_japan else 'FAIL'}: is_japan_facing={is_japan} (confidence={confidence:.2f})",
    ]
    if company:
        reasons.append(f"Company: {company}")
    if founder:
        reasons.append(f"Founder: {founder}")
    if not company and not founder:
        reasons.append("INFO: 企業名・代表者名はページから自動抽出できませんでした（手動確認推奨）")

    return passed, reasons, confidence, lp_url, company, founder, False
