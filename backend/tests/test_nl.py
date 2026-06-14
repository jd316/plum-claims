"""Tests for the additive natural-language features:
  1. RAG over the policy (policy_rag) — live answers + deterministic chunk/keyword tests.
  2. NL claim intake (nl_intake) — live structured parse.

Live tests hit Gemini and are gated behind @pytest.mark.live; the deterministic
tests run with no network (chunking + keyword fallback are pure)."""
import pytest

from app.services import policy_rag


# --------------------------------------------------------------------------- #
# Deterministic: chunking + keyword fallback (no embeddings, no network).      #
# --------------------------------------------------------------------------- #

def test_build_chunks_titles_cover_all_sections():
    titles = {c.title for c in policy_rag.build_chunks()}
    # Every coverage category.
    for t in ("Consultation coverage", "Diagnostic coverage", "Pharmacy coverage",
              "Dental coverage", "Vision coverage", "Alternative medicine coverage"):
        assert t in titles, f"missing category chunk: {t}"
    # The structural sections.
    for t in ("Overall coverage limits", "Waiting periods", "Exclusions",
              "Pre-authorization", "Fraud thresholds", "Submission rules",
              "Family floater"):
        assert t in titles, f"missing section chunk: {t}"


def test_chunks_carry_policy_values():
    by_title = {c.title: c.text for c in policy_rag.build_chunks()}
    assert "5,000" in by_title["Overall coverage limits"]      # per-claim limit
    assert "Teeth Whitening" in by_title["Dental coverage"]     # excluded procedure
    assert "MRI" in by_title["Diagnostic coverage"]             # pre-auth test


def test_keyword_fallback_retrieves_right_chunk(monkeypatch):
    # Force the embedding-free path: rebuild with embeddings disabled.
    monkeypatch.setattr(policy_rag, "_embed", lambda *a, **k: None)
    policy_rag.build_index(force=True)

    top = policy_rag.retrieve("what is the per claim limit?", k=1)
    assert top and top[0][0].title == "Overall coverage limits"

    top = policy_rag.retrieve("is teeth whitening covered under dental?", k=2)
    titles = {c.title for c, _ in top}
    assert "Dental coverage" in titles or "Exclusions" in titles

    top = policy_rag.retrieve("how long is the waiting period for cataract?", k=1)
    assert top[0][0].title == "Waiting periods"

    # Reset so a later live test rebuilds with real embeddings.
    policy_rag.build_index(force=True)


# --------------------------------------------------------------------------- #
# Live: RAG answers (≈3 Gemini calls).                                         #
# --------------------------------------------------------------------------- #

@pytest.mark.live
def test_rag_answers_per_claim_limit():
    policy_rag.build_index(force=True)
    out = policy_rag.answer("what is the per-claim limit?")
    print("\n[RAG per-claim limit]", out)
    assert "5,000" in out["answer"] or "5000" in out["answer"]
    assert out["sources"], "expected at least one cited source"


@pytest.mark.live
def test_rag_teeth_whitening_excluded():
    out = policy_rag.answer("is teeth whitening covered?")
    print("\n[RAG teeth whitening]", out)
    low = out["answer"].lower()
    assert "exclud" in low or "not covered" in low or "not specified" in low


@pytest.mark.live
def test_rag_off_policy_declines():
    out = policy_rag.answer("what is the weather today?")
    print("\n[RAG off-policy]", out)
    assert "not specified in the policy" in out["answer"].lower()


# --------------------------------------------------------------------------- #
# Live: NL claim intake (≈2 Gemini calls).                                     #
# --------------------------------------------------------------------------- #

@pytest.mark.live
def test_parse_consultation_apollo():
    from app.agents.nl_intake import parse_claim_text
    d = parse_claim_text("I saw a doctor at Apollo for a fever, bill was ₹1,500")
    print("\n[NL parse consultation]", d)
    assert d["claim_category"] == "CONSULTATION"
    assert d["claimed_amount"] == 1500
    assert d["hospital_name"] and "apollo" in d["hospital_name"].lower()


@pytest.mark.live
def test_parse_dental_root_canal():
    from app.agents.nl_intake import parse_claim_text
    d = parse_claim_text("root canal at the dentist, ₹8000")
    print("\n[NL parse dental]", d)
    assert d["claim_category"] == "DENTAL"
    assert d["claimed_amount"] == 8000
