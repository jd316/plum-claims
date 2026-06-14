"""All policy access goes through here. No policy values are hardcoded anywhere else."""
import json
import re
import threading

from rapidfuzz.distance import JaroWinkler

class PolicyNotFound(Exception): pass
class MemberNotFound(Exception): pass
class UnknownCategory(Exception): pass

# Generic facility words dropped before matching a hospital name against the network
# list, so "Apollo Hospitals Bengaluru" and "Apollo" share the distinctive token "apollo".
_NETWORK_STOPWORDS = {
    "hospital", "hospitals", "clinic", "clinics", "centre", "center", "medical", "medicare",
    "healthcare", "health", "care", "pvt", "private", "ltd", "limited", "the", "and",
    "institute", "institution", "multispeciality", "multispecialty", "speciality",
    "specialty", "super", "general", "nursing", "home", "diagnostics",
}

def _network_tokens(name: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", (name or "").lower())
            if w and w not in _NETWORK_STOPWORDS}

def _token_present(net_tok: str, hosp_tokens: set[str]) -> bool:
    # Exact membership, or a tight fuzzy match (typo tolerance) guarded by a length
    # bound so a short token like "max" can't bleed into "maximus".
    if net_tok in hosp_tokens:
        return True
    return any(abs(len(net_tok) - len(t)) <= 2
               and JaroWinkler.normalized_similarity(net_tok, t) >= 0.90
               for t in hosp_tokens)

_CATEGORY_KEY = {  # claim category -> opd_categories key
    "CONSULTATION": "consultation", "DIAGNOSTIC": "diagnostic", "PHARMACY": "pharmacy",
    "DENTAL": "dental", "VISION": "vision", "ALTERNATIVE_MEDICINE": "alternative_medicine",
}

class PolicyEngine:
    def __init__(self, path: str):
        with open(path) as f:
            self._p = json.load(f)

    @property
    def policy_id(self) -> str: return self._p["policy_id"]

    def members(self) -> list[dict]: return self._p["members"]

    def member(self, member_id: str) -> dict:
        for m in self._p["members"]:
            if m["member_id"] == member_id:
                return m
        raise MemberNotFound(member_id)

    def category_rules(self, category: str) -> dict:
        key = _CATEGORY_KEY.get(category)
        if key is None: raise UnknownCategory(category)
        return self._p["opd_categories"][key]

    def waiting_days(self, condition: str) -> int | None:
        return self._p["waiting_periods"]["specific_conditions"].get(condition)
    def initial_waiting_days(self) -> int:
        return self._p["waiting_periods"]["initial_waiting_period_days"]
    def pre_existing_conditions_days(self) -> int:
        return self._p["waiting_periods"]["pre_existing_conditions_days"]
    def waiting_conditions(self) -> dict:
        return self._p["waiting_periods"]["specific_conditions"]

    def exclusion_conditions(self) -> list[str]: return self._p["exclusions"]["conditions"]
    def is_excluded_condition(self, name: str) -> bool:
        return name in self._p["exclusions"]["conditions"]

    def is_network(self, hospital: str | None) -> bool:
        """True when `hospital` resolves to a network facility. Matches on distinctive
        tokens (facility words like 'Hospital'/'Clinic' stripped) with tight typo
        tolerance, so 'Apollo Hospitals, Bengaluru' matches 'Apollo Hospitals' but a
        short token can't bleed into an unrelated longer name."""
        if not hospital:
            return False
        hosp_tokens = _network_tokens(hospital)
        if not hosp_tokens:
            return False
        for h in self._p["network_hospitals"]:
            net_tokens = _network_tokens(h)
            if net_tokens and all(_token_present(t, hosp_tokens) for t in net_tokens):
                return True
        return False

    def pre_authorization(self) -> dict: return self._p["pre_authorization"]
    def document_requirements(self, category: str) -> dict:
        return self._p["document_requirements"][category]
    def per_claim_limit(self) -> float: return self._p["coverage"]["per_claim_limit"]
    def annual_opd_limit(self) -> float: return self._p["coverage"]["annual_opd_limit"]
    def family_floater(self) -> dict: return self._p["coverage"].get("family_floater", {})
    def policy_holder(self) -> dict: return self._p.get("policy_holder", {})
    def fraud_thresholds(self) -> dict: return self._p["fraud_thresholds"]
    def submission_rules(self) -> dict: return self._p["submission_rules"]


# --------------------------------------------------------------------------- #
# Policy cache — module-level singleton keyed by path.                         #
#                                                                              #
# PolicyEngine just reads + parses a JSON file; that file is read-only at      #
# runtime, so re-reading it on every request (as /api/members and the doc-     #
# requirements endpoint did) is pure waste. `get_policy_engine()` caches one   #
# instance per path so repeated calls return the SAME object with identical    #
# values, but only one file read happens. The graph's `nodes.pe()` already     #
# caches its own singleton; this aligns the HTTP layer with that behaviour.    #
# `invalidate_policy_cache()` drops the cache so a future edit (the policy-as- #
# code studio) forces a fresh read. Purely additive: same values, fewer reads.#
# --------------------------------------------------------------------------- #
_ENGINE_CACHE: dict[str, "PolicyEngine"] = {}
_ENGINE_LOCK = threading.Lock()


def get_policy_engine(path: str) -> "PolicyEngine":
    """Return a cached PolicyEngine for `path`, constructing it once on first use.

    Thread-safe (double-checked locking). The returned instance is shared, so
    callers must treat it as read-only — which all policy access already is."""
    eng = _ENGINE_CACHE.get(path)
    if eng is not None:
        return eng
    with _ENGINE_LOCK:
        eng = _ENGINE_CACHE.get(path)
        if eng is None:
            eng = PolicyEngine(path)
            _ENGINE_CACHE[path] = eng
        return eng


def invalidate_policy_cache(path: str | None = None) -> None:
    """Drop the cached engine for `path` (or all paths if None) so the next
    get_policy_engine() re-reads the policy file. Used after a policy edit."""
    with _ENGINE_LOCK:
        if path is None:
            _ENGINE_CACHE.clear()
        else:
            _ENGINE_CACHE.pop(path, None)
