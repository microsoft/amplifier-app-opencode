"""Tests for the static per-model pricing catalog and lookup fallback.

Covers:
  - ``claude-sonnet-5`` (the model id amplifier-agent's anthropic provider
    module actually advertises -- confirmed via
    amplifier_module_provider_anthropic/_cost.py) is present with
    sonnet-tier rates. Prior to this fix it was missing entirely, so
    opencode rendered claude-sonnet-5 sessions as costless
    (see amplifier-support#352).
  - Exact-match entries always win over the family-substring fallback.
  - ``lookup_pricing()`` falls back to a family-tier default (opus /
    sonnet / haiku) for unknown-but-recognizable Anthropic model ids,
    instead of silently returning None (which renders a $0.00 cost
    block in opencode).
  - Non-Anthropic unknown ids still return None -- the fallback is
    scoped to the Anthropic naming convention only.
  - The fallback path logs a WARNING so the approximation is visible
    in logs rather than silent.
"""

from __future__ import annotations

import logging

import pytest

from amplifier_app_opencode.cli import MODEL_PRICING_PER_MILLION, lookup_pricing

# ---------------------------------------------------------------------------
# Change 1: claude-sonnet-5 exact entry
# ---------------------------------------------------------------------------


def test_claude_sonnet_5_exact_lookup_returns_sonnet_tier_rates() -> None:
    """claude-sonnet-5 (the id amplifier-agent actually advertises) must be
    an exact catalog entry with the standard sonnet-tier rates, matching
    claude-sonnet-4-6's pricing."""
    pricing = lookup_pricing("claude-sonnet-5")
    assert pricing == {
        "input": 3.0,
        "output": 15.0,
        "cache_read": 0.3,
        "cache_write": 3.75,
    }


def test_claude_sonnet_5_is_in_static_catalog() -> None:
    """The entry must be a real catalog member, not just reachable via the
    fallback -- proves Change 1 (catalog addition) independent of Change 2
    (fallback)."""
    assert "claude-sonnet-5" in MODEL_PRICING_PER_MILLION


# ---------------------------------------------------------------------------
# Exact-match precedence over fallback
# ---------------------------------------------------------------------------


def test_exact_match_takes_precedence_over_family_fallback() -> None:
    """A known id containing a family keyword must return its own exact
    entry, not the generic family-tier default."""
    pricing = lookup_pricing("claude-haiku-4-5-20251001")
    exact_entry = MODEL_PRICING_PER_MILLION["claude-haiku-4-5-20251001"]
    assert pricing == exact_entry


# ---------------------------------------------------------------------------
# Change 2: family-substring fallback
# ---------------------------------------------------------------------------


def test_unknown_sonnet_family_id_falls_back_to_sonnet_tier(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unrecognized Sonnet-family id (future model not yet in the
    catalog) falls back to sonnet-tier rates instead of None."""
    with caplog.at_level(logging.WARNING):
        pricing = lookup_pricing("claude-sonnet-6-9")
    assert pricing == {
        "input": 3.0,
        "output": 15.0,
        "cache_read": 0.3,
        "cache_write": 3.75,
    }
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_unknown_opus_family_id_falls_back_to_opus_tier(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unrecognized Opus-family id falls back to opus-tier rates."""
    with caplog.at_level(logging.WARNING):
        pricing = lookup_pricing("claude-opus-9-9")
    assert pricing == {
        "input": 5.0,
        "output": 25.0,
        "cache_read": 0.5,
        "cache_write": 6.25,
    }
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_unknown_haiku_family_id_falls_back_to_haiku_tier(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unrecognized Haiku-family id falls back to the rates from the
    existing claude-haiku-4-5-20251001 entry (mirrored, not guessed)."""
    with caplog.at_level(logging.WARNING):
        pricing = lookup_pricing("claude-haiku-9-9-20301231")
    assert pricing == MODEL_PRICING_PER_MILLION["claude-haiku-4-5-20251001"]
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_family_fallback_is_case_insensitive() -> None:
    """Matching is case-insensitive since model ids could arrive in any
    case from an upstream provider."""
    pricing = lookup_pricing("Claude-SONNET-7")
    assert pricing == {
        "input": 3.0,
        "output": 15.0,
        "cache_read": 0.3,
        "cache_write": 3.75,
    }


# ---------------------------------------------------------------------------
# Non-Anthropic unknowns remain None
# ---------------------------------------------------------------------------


def test_non_anthropic_unknown_id_returns_none() -> None:
    """An unknown id with no recognizable Anthropic family keyword still
    returns None -- the fallback must not swallow every miss."""
    assert lookup_pricing("mystery-model-x") is None


def test_unknown_openai_id_returns_none() -> None:
    """A plausible-but-uncataloged OpenAI id (no anthropic family keyword)
    returns None, not a guessed price."""
    assert lookup_pricing("gpt-6-nano") is None
