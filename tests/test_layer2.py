"""Tests for Layer2 filter (pure functions, no mocking needed)."""
import pytest
from datetime import datetime, timezone, timedelta

from github_scout.models import RepoRecord
from github_scout.filters.layer2 import apply_layer2, _match_patterns

CONFIG = {
    "required_files": ["package.json", "requirements.txt", "Dockerfile"],
    "signal_files": ["vite.config.*", "src", "app"],
    "positive_keywords": ["product", "platform", "dashboard", "waitlist"],
    "negative_keywords": ["tutorial", "boilerplate", "portfolio"],
    "min_score": 2.0,
}


def _repo(**kwargs) -> RepoRecord:
    defaults = dict(
        name="cool-saas",
        owner="alice",
        url="https://github.com/alice/cool-saas",
        created_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        language="Python",
        stars=3,
        description="A SaaS product",
    )
    defaults.update(kwargs)
    return RepoRecord(**defaults)


class TestMatchPatterns:
    def test_exact_match(self):
        assert _match_patterns(["package.json", "src"], ["package.json"]) == ["package.json"]

    def test_glob_match(self):
        result = _match_patterns(["vite.config.ts"], ["vite.config.*"])
        assert result == ["vite.config.*"]

    def test_no_match(self):
        assert _match_patterns(["index.ts"], ["package.json"]) == []

    def test_multiple_files_one_pattern_matches_once(self):
        # Pattern should appear only once even if multiple files match
        result = _match_patterns(["vite.config.ts", "vite.config.js"], ["vite.config.*"])
        assert len(result) == 1


class TestLayer2:
    def test_passes_with_readme_and_required_files(self):
        readme = "A product platform with dashboard and waitlist"
        root = ["package.json", "src", "README.md"]
        passed, reasons, score = apply_layer2(_repo(), CONFIG, readme, root)
        assert passed
        assert score >= 2.0

    def test_fails_without_readme_and_no_files(self):
        passed, reasons, score = apply_layer2(_repo(), CONFIG, None, [])
        assert not passed

    def test_negative_keywords_reduce_score(self):
        readme = "This is a portfolio tutorial boilerplate"
        root = ["package.json"]
        passed, reasons, score = apply_layer2(_repo(), CONFIG, readme, root)
        assert any("negative keywords" in r for r in reasons)

    def test_no_readme_does_not_crash(self):
        # Should not raise even with None readme
        passed, reasons, score = apply_layer2(_repo(), CONFIG, None, ["package.json"])
        assert isinstance(passed, bool)

    def test_score_increases_with_more_signals(self):
        readme = "product platform dashboard waitlist"
        root = ["package.json", "Dockerfile", "src", "app"]
        _, _, score_rich = apply_layer2(_repo(), CONFIG, readme, root)

        _, _, score_poor = apply_layer2(_repo(), CONFIG, None, [])
        assert score_rich > score_poor

    def test_min_score_threshold_respected(self):
        config = {**CONFIG, "min_score": 999.0}
        readme = "great product platform dashboard"
        root = ["package.json", "src"]
        passed, _, _ = apply_layer2(_repo(), config, readme, root)
        assert not passed
