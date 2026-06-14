# backend/tests/test_policy_engine.py
from app.services.policy_engine import PolicyEngine
from tests.conftest import REPO_ROOT

pe = PolicyEngine(str(REPO_ROOT / "policy_terms.json"))

def test_member_lookup():
    m = pe.member("EMP005")
    assert m["name"] == "Vikram Joshi" and m["join_date"] == "2024-09-01"

def test_category_rules():
    c = pe.category_rules("CONSULTATION")
    assert c["sub_limit"] == 2000 and c["copay_percent"] == 10 and c["network_discount_percent"] == 20

def test_waiting_days():
    assert pe.waiting_days("diabetes") == 90
    assert pe.waiting_days("unknown_condition") is None
    assert pe.initial_waiting_days() == 30

def test_exclusions_and_network():
    assert pe.is_excluded_condition("Obesity and weight loss programs")
    assert pe.is_network("Apollo Hospitals") and not pe.is_network("City Clinic, Bengaluru")

def test_doc_requirements_and_limits():
    assert pe.document_requirements("CONSULTATION")["required"] == ["PRESCRIPTION","HOSPITAL_BILL"]
    assert pe.per_claim_limit() == 5000
    assert pe.fraud_thresholds()["same_day_claims_limit"] == 2
    assert pe.submission_rules()["minimum_claim_amount"] == 500

def test_dental_procedures():
    d = pe.category_rules("DENTAL")
    assert "Teeth Whitening" in d["excluded_procedures"]
    assert "Root Canal Treatment" in d["covered_procedures"]
