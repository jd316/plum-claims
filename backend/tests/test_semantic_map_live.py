import pytest
from app.agents.semantic_map import map_semantics
from app.models.schemas import ExtractionResult, StrField, LineItem
from app.services.policy_engine import PolicyEngine
from tests.conftest import REPO_ROOT

pe = PolicyEngine(str(REPO_ROOT / "policy_terms.json"))
pytestmark = pytest.mark.live

def _ex(diag=None, treat=None, items=()):
    return [ExtractionResult(diagnosis=StrField(value=diag, confidence=.9),
                             treatment=StrField(value=treat, confidence=.9),
                             line_items=[LineItem(description=d, amount=a) for d, a in items])]

def test_diabetes_maps_to_waiting_condition():
    m = map_semantics("CONSULTATION", _ex(diag="Type 2 Diabetes Mellitus"), pe)
    assert m.waiting_condition == "diabetes" and m.confidence > 0.5

def test_obesity_maps_to_exclusion():
    m = map_semantics("CONSULTATION", _ex(diag="Morbid Obesity — BMI 37",
                       treat="Bariatric Consultation and Customised Diet Plan"), pe)
    assert any("obesity" in c.lower() for c in m.exclusion_candidates)

def test_viral_fever_maps_clean():
    m = map_semantics("CONSULTATION", _ex(diag="Viral Fever"), pe)
    assert m.waiting_condition is None and m.exclusion_candidates == []
