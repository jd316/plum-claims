from app.rules.financial import calculate
from app.models.schemas import LineItem
from app.services.policy_engine import PolicyEngine
from tests.conftest import REPO_ROOT

pe = PolicyEngine(str(REPO_ROOT / "policy_terms.json"))

def test_tc004_copay_only():
    fb = calculate(pe, "CONSULTATION", is_network=False,
                   items=[LineItem(description="Consultation Fee", amount=1000),
                          LineItem(description="CBC Test", amount=300),
                          LineItem(description="Dengue NS1 Test", amount=200)],
                   disallowed=[])
    assert fb.approved_amount == 1350 and fb.copay_amount == 150 and fb.network_discount_amount == 0

def test_tc010_network_discount_before_copay():
    fb = calculate(pe, "CONSULTATION", is_network=True,
                   items=[LineItem(description="Consultation Fee", amount=1500),
                          LineItem(description="Medicines", amount=3000)], disallowed=[])
    assert fb.network_discount_amount == 900
    assert fb.post_discount == 3600
    assert fb.copay_amount == 360
    assert fb.approved_amount == 3240
    assert any("discount" in s.lower() for s in fb.steps) and any("co-pay" in s.lower() for s in fb.steps)

def test_tc006_partial_dental():
    fb = calculate(pe, "DENTAL", is_network=False,
                   items=[LineItem(description="Root Canal Treatment", amount=8000),
                          LineItem(description="Teeth Whitening", amount=4000)],
                   disallowed=["Teeth Whitening"])
    assert fb.approved_amount == 8000
    li = {l.description: l for l in fb.line_items}
    assert li["Teeth Whitening"].approved is False and li["Teeth Whitening"].reason
    assert li["Root Canal Treatment"].approved is True

def test_sub_limit_caps_approved_amount():
    fb = calculate(pe, "ALTERNATIVE_MEDICINE", is_network=False,
                   items=[LineItem(description="Panchakarma (10 sessions)", amount=9000)], disallowed=[])
    assert fb.approved_amount == 8000
