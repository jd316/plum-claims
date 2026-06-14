"""Normalized network-hospital matching (PolicyEngine.is_network).

Replaces fragile bidirectional substring matching with distinctive-token matching
plus tight typo tolerance. The financial network discount hinges on this, so a false
positive/negative is a real-money error."""
from app.services.policy_engine import PolicyEngine
from tests.conftest import REPO_ROOT

PE = PolicyEngine(str(REPO_ROOT / "policy_terms.json"))


def test_exact_and_decorated_names_match():
    assert PE.is_network("Apollo Hospitals")
    assert PE.is_network("Apollo Hospitals, Bengaluru")           # trailing location
    assert PE.is_network("Max Super Speciality Hospital")         # facility words stripped
    assert PE.is_network("KOKILABEN DHIRUBHAI AMBANI HOSPITAL")   # case-insensitive multi-token


def test_typo_tolerance():
    assert PE.is_network("Appolo Hospital")   # single-letter typo on the distinctive token


def test_non_network_and_empty():
    assert not PE.is_network("City Clinic, Bengaluru")
    assert not PE.is_network(None)
    assert not PE.is_network("")
    # A short distinctive token must not bleed into an unrelated longer name.
    assert not PE.is_network("Maximus Wellness")  # 'max' must NOT match 'Maximus'


def test_generic_only_name_is_not_network():
    # A name that reduces to only facility stopwords matches nothing.
    assert not PE.is_network("General Hospital")
