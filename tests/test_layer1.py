"""Tests for Layer1 filter (pure functions, no mocking needed)."""
import pytest
from datetime import datetime, timezone, timedelta

from github_scout.models import RepoRecord
from github_scout.filters.layer1 import apply_layer1

CONFIG = {
    "max_age_days": 7,
    "min_stars": 0,
    "max_stars": 20,
    "allowed_languages": ["Python", "TypeScript", "JavaScript", "Go", "Rust"],
    "exclude_keywords": ["tutorial", "leetcode", "boilerplate"],
}


def _repo(**kwargs) -> RepoRecord:
    """Build a minimal passing repo with optional overrides."""
    defaults = dict(
        name="cool-saas",
        owner="alice",
        url="https://github.com/alice/cool-saas",
        created_at=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        language="Python",
        stars=5,
        description="A great product",
    )
    defaults.update(kwargs)
    return RepoRecord(**defaults)


class TestAge:
    def test_fresh_repo_passes(self):
        repo = _repo(created_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
        passed, reasons = apply_layer1(repo, CONFIG)
        assert passed

    def test_old_repo_fails(self):
        repo = _repo(created_at=(datetime.now(timezone.utc) - timedelta(days=30)).isoformat())
        passed, reasons = apply_layer1(repo, CONFIG)
        assert not passed
        assert any("age" in r for r in reasons)

    def test_boundary_exactly_max_days_passes(self):
        repo = _repo(created_at=(datetime.now(timezone.utc) - timedelta(days=7)).isoformat())
        passed, _ = apply_layer1(repo, CONFIG)
        assert passed


class TestStars:
    def test_zero_stars_passes(self):
        passed, _ = apply_layer1(_repo(stars=0), CONFIG)
        assert passed

    def test_max_stars_passes(self):
        passed, _ = apply_layer1(_repo(stars=20), CONFIG)
        assert passed

    def test_too_many_stars_fails(self):
        passed, reasons = apply_layer1(_repo(stars=100), CONFIG)
        assert not passed
        assert any("stars" in r for r in reasons)


class TestLanguage:
    def test_allowed_language_passes(self):
        for lang in ["Python", "TypeScript", "Go"]:
            passed, _ = apply_layer1(_repo(language=lang), CONFIG)
            assert passed, f"{lang} should pass"

    def test_disallowed_language_fails(self):
        passed, reasons = apply_layer1(_repo(language="Java"), CONFIG)
        assert not passed
        assert any("language" in r for r in reasons)

    def test_none_language_fails_when_list_set(self):
        passed, _ = apply_layer1(_repo(language=None), CONFIG)
        assert not passed

    def test_empty_allowed_list_allows_all(self):
        config = {**CONFIG, "allowed_languages": []}
        passed, _ = apply_layer1(_repo(language="COBOL"), config)
        assert passed


class TestExcludeKeywords:
    def test_keyword_in_name_fails(self):
        passed, reasons = apply_layer1(_repo(name="leetcode-solutions"), CONFIG)
        assert not passed
        assert any("exclude keywords" in r for r in reasons)

    def test_keyword_in_description_fails(self):
        passed, _ = apply_layer1(_repo(description="this is a tutorial project"), CONFIG)
        assert not passed

    def test_case_insensitive_match(self):
        passed, _ = apply_layer1(_repo(name="LeetCode-2024"), CONFIG)
        assert not passed

    def test_no_keyword_passes(self):
        passed, _ = apply_layer1(_repo(name="startup-api", description="SaaS platform"), CONFIG)
        assert passed
