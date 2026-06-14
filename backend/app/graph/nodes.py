import time
from datetime import date, timedelta
from functools import wraps
from typing import cast
from langgraph.types import Send
from app.config import settings
from app.graph.state import ClaimState, trace
from app.models.schemas import (ExtractionResult, DocumentQuality, RuleVerdict, Decision,
                                ComponentFailure, DocumentProblem, ReasonCode, FinancialBreakdown,
                                LineItem, RuleName)
from app.services.policy_engine import PolicyEngine, get_policy_engine
from app.services.confidence import compute
from app.rules import waiting_period, coverage_exclusion, pre_auth, limits, fraud
from app.rules.base import RuleContext
from app.rules.docgate import check_documents
from app.rules.financial import calculate
from app.rules.aggregator import aggregate
from app.agents.extraction import (extract_document_cached)
from app.agents.semantic_map import map_semantics_with_usage
from app.agents.verifier import verify_with_usage

def pe() -> PolicyEngine:
    # Align with the HTTP layer: one shared, path-keyed PolicyEngine singleton
    # (see app.services.policy_engine.get_policy_engine). Same values, one read.
    return get_policy_engine(settings.policy_path)

RULES = {"waiting_period": waiting_period, "coverage_exclusion": coverage_exclusion,
         "pre_auth": pre_auth, "limits": limits, "fraud_anomaly": fraud}

def resilient(agent_name: str, *, critical: bool = False):
    """Record failures into state and continue. TC011: honor simulate_component_failure
    by injecting a failure into the (non-critical) fraud agent."""
    def deco(fn):
        @wraps(fn)
        def inner(state: ClaimState):
            started = time.monotonic()
            if (state.get("submission") and state["submission"].simulate_component_failure
                    and agent_name == "fraud_anomaly"):
                return {"failures": [ComponentFailure(agent=agent_name, failure_mode="simulated failure")],
                        "rule_verdicts": [RuleVerdict(rule="fraud_anomaly", status="SKIPPED",
                                                      detail="component failed; check skipped", certainty=0.0)],
                        "trace": [trace("adjudicate", agent_name, "ERROR",
                                        "Simulated component failure — skipped, pipeline continues",
                                        started=started, degraded=True, failure_mode="simulated")]}
            try:
                return fn(state)
            except Exception as e:
                out: dict = {"failures": [ComponentFailure(agent=agent_name, failure_mode=str(e)[:200])],
                             "trace": [trace(agent_name, agent_name, "ERROR",
                                             f"{agent_name} failed: {str(e)[:120]} — continuing degraded",
                                             started=started, degraded=True, failure_mode=str(e)[:120])]}
                if agent_name in RULES:
                    out["rule_verdicts"] = [RuleVerdict(rule=cast(RuleName, agent_name), status="SKIPPED",
                                                        detail=f"component error: {str(e)[:80]}", certainty=0.0)]
                if critical:
                    out["problems"] = [DocumentProblem(kind="INTAKE_VIOLATION",
                        message="We hit a technical problem processing your claim. It has been queued "
                                "for manual review — no action needed from you.")]
                return out
        return inner
    return deco

def intake(state: ClaimState):
    s = state["submission"]; started = time.monotonic()
    try:
        member = pe().member(s.member_id)
    except Exception:
        return {"problems": [DocumentProblem(kind="INTAKE_VIOLATION",
                    message=f"Member '{s.member_id}' was not found on policy {s.policy_id}.")],
                "trace": [trace("intake", "intake", "FAIL", f"member {s.member_id} not found", started=started)]}
    try:
        rules = pe().submission_rules()
        problems = []
        if s.claimed_amount < rules["minimum_claim_amount"]:
            problems.append(DocumentProblem(kind="INTAKE_VIOLATION",
                message=f"Claimed amount ₹{s.claimed_amount:,.0f} is below the minimum claimable "
                        f"amount of ₹{rules['minimum_claim_amount']:,.0f}."))
        # Submission deadline (gated OFF by default; see settings.submission_deadline_enabled).
        # Measured against submission_date (or today() when the caller omits it). The eval
        # never enables the flag, so the 2024-dated cases are unaffected.
        if settings.submission_deadline_enabled:
            deadline_days = rules.get("deadline_days_from_treatment")
            as_of = s.submission_date or date.today()
            if deadline_days is not None and (as_of - s.treatment_date).days > deadline_days:
                last_day = s.treatment_date + timedelta(days=deadline_days)
                problems.append(DocumentProblem(kind="INTAKE_VIOLATION",
                    message=f"This claim was submitted on {as_of.isoformat()} for treatment on "
                            f"{s.treatment_date.isoformat()} — {(as_of - s.treatment_date).days} days later. "
                            f"Claims must be submitted within {deadline_days} days of treatment; the deadline "
                            f"for this treatment was {last_day.isoformat()}."))
    except Exception as e:
        # Never crash intake on a rules-lookup glitch: route to manual review safely.
        return {"member": member,
                "problems": [DocumentProblem(kind="INTAKE_VIOLATION",
                    message="We hit a technical problem validating your claim. It has been queued "
                            "for manual review — no action needed from you.")],
                "failures": [ComponentFailure(agent="intake", failure_mode=str(e)[:200])],
                "trace": [trace("intake", "intake", "ERROR",
                                f"intake rules check failed: {str(e)[:120]} — routed to manual review",
                                started=started, degraded=True, failure_mode=str(e)[:120])]}
    return {"member": member, "problems": problems,
            "trace": [trace("intake", "intake", "FAIL" if problems else "PASS",
                            f"member {member['name']} resolved; submission rules "
                            + ("violated" if problems else "satisfied"),
                            policy_refs=["submission_rules"], started=started)]}

def fan_out_extraction(state: ClaimState):
    if state.get("problems"): return "explain"
    return [Send("extract_doc", {"doc": d, "submission": state["submission"]})
            for d in state["submission"].documents]

@resilient("extraction")
def extract_doc(payload: dict):
    started = time.monotonic()
    doc = payload["doc"]
    info: dict = {}
    try:
        r, info = extract_document_cached(doc)
        st = "PASS" if r.quality.readable else "FLAG"
        summary = (f"{doc.file_id} → {r.doc_type}; readable={r.quality.readable}; "
                   f"patient={r.patient_name.value!r} (conf {r.patient_name.confidence:.2f})")
    except Exception as e:
        r = ExtractionResult(file_id=doc.file_id, doc_type="UNKNOWN",
                             quality=DocumentQuality(readable=False, quality_issues=["extraction_failed"],
                                                     overall_confidence=0.0))
        return {"extractions": [r],
                "failures": [ComponentFailure(agent="extraction", failure_mode=str(e)[:200])],
                "trace": [trace("extract", "extraction", "ERROR",
                                f"{doc.file_id} extraction failed — marked unreadable, continuing",
                                started=started, degraded=True, failure_mode=str(e)[:120])]}
    usage = info.get("usage", {})
    out_trace = [trace("extract", "extraction", st, summary, model=settings.gemini_model,
                       detail={"quality_issues": r.quality.quality_issues}, started=started,
                       input_tokens=usage.get("input_tokens"),
                       output_tokens=usage.get("output_tokens"))]
    if info.get("corrected"):
        weak = info.get("weak_fields", [])
        improved = info.get("improved_fields", [])
        pro = info.get("escalated_model")
        out_trace.append(trace(
            "extract", "extraction_self_correction", "INFO",
            f"{doc.file_id}: low confidence on {weak} → re-extracted with {pro}; "
            f"improved {improved}",
            model=pro,
            detail={"weak_fields": weak, "improved_fields": improved},
            started=started, degraded=False))
    return {"extractions": [r], "trace": out_trace}

def docgate(state: ClaimState):
    started = time.monotonic()
    try:
        file_names = {d.file_id: d.file_name for d in state["submission"].documents if d.file_name}
        probs = check_documents(state["extractions"], state["submission"].claim_category,
                                state["member"]["name"], pe(), file_names=file_names)
    except Exception as e:
        # Never crash the gate: hold the claim for manual review rather than 500.
        return {"problems": [DocumentProblem(kind="INTAKE_VIOLATION",
                    message="We hit a technical problem verifying your documents. Your claim has "
                            "been queued for manual review — no action needed from you.")],
                "failures": [ComponentFailure(agent="docgate", failure_mode=str(e)[:200])],
                "trace": [trace("docgate", "doc_verification", "ERROR",
                                f"document verification failed: {str(e)[:120]} — routed to manual review",
                                started=started, degraded=True, failure_mode=str(e)[:120])]}
    return {"problems": probs,
            "trace": [trace("docgate", "doc_verification", "FAIL" if probs else "PASS",
                            probs[0].message if probs else
                            "All required documents present, readable, and belong to the member",
                            policy_refs=[f"document_requirements.{state['submission'].claim_category}"],
                            started=started)]}

def route_after_docgate(state: ClaimState):
    return "explain" if state.get("problems") else "semantic_map"

@resilient("semantic_map")
def semantic_map(state: ClaimState):
    started = time.monotonic()
    m, usage = map_semantics_with_usage(state["submission"].claim_category, state["extractions"], pe())
    return {"semantic": m,
            "trace": [trace("semantic_map", "semantic_map", "PASS",
                            f"waiting_condition={m.waiting_condition!r}, "
                            f"exclusions={m.exclusion_candidates}, conf={m.confidence:.2f}",
                            model=settings.gemini_model, started=started,
                            input_tokens=usage.get("input_tokens"),
                            output_tokens=usage.get("output_tokens"))]}

def supervisor_select(state: ClaimState) -> list[str]:
    """Adaptive routing decision: which rule agents to invoke for THIS claim.

    Consults app.graph.supervisor.select_rules, which skips a rule ONLY when it is
    PROVABLY guaranteed to PASS (so the decision is byte-identical to running all five).
    Honours the `adaptive_routing_enabled` flag: when off, fans out to all five rules
    (original behaviour). Fail-safe: any error falls back to running every rule."""
    from app.graph.supervisor import select_rules, ALL_RULES
    if not settings.adaptive_routing_enabled:
        return list(ALL_RULES)
    try:
        invoked, _ = select_rules(state["submission"], state["member"], pe())
        return invoked
    except Exception:
        # Never let the supervisor change behaviour on error: run everything.
        return list(ALL_RULES)


def supervisor(state: ClaimState):
    """Trace-only node that records the adaptive routing decision (invoked vs skipped +
    reasons) so the adaptive AND provably-safe routing is visible in the trace. It does
    NOT alter rule_verdicts — the actual Sends are emitted by fan_out_rules."""
    started = time.monotonic()
    from app.graph.supervisor import select_rules, ALL_RULES
    if not settings.adaptive_routing_enabled:
        return {"trace": [trace("adjudicate", "supervisor", "INFO",
                    f"Adaptive routing OFF — fanning out to all {len(ALL_RULES)} rule agents.",
                    detail={"invoked": list(ALL_RULES), "skipped": []}, started=started)]}
    try:
        invoked, skipped = select_rules(state["submission"], state["member"], pe())
    except Exception as e:
        return {"trace": [trace("adjudicate", "supervisor", "INFO",
                    f"Supervisor selection failed ({str(e)[:80]}) — running all rules.",
                    detail={"invoked": list(ALL_RULES), "skipped": []}, started=started)]}
    if skipped:
        summary = (f"Adaptive routing: invoked {invoked}; skipped "
                   + "; ".join(f"{s['rule']} ({s['reason']})" for s in skipped))
    else:
        summary = f"Adaptive routing: all {len(invoked)} rules applicable — none skippable."
    return {"trace": [trace("adjudicate", "supervisor", "INFO", summary,
                detail={"invoked": invoked, "skipped": skipped}, started=started)]}


def fan_out_rules(state: ClaimState):
    return [Send("rule_check", {"rule": name, "state": state})
            for name in supervisor_select(state)]

def rule_check(payload: dict):
    name = payload["rule"]; state = payload["state"]
    return _RULE_NODES[name](state)

# Rules whose PASS verdict relies on the semantic mapping (waiting_condition / exclusion_candidates).
# If the LLM semantic step failed, a default empty mapping would let these silently PASS — so we
# convert a would-be PASS into a FLAG (routes the claim to MANUAL_REVIEW) instead of approving.
_SEMANTIC_DEPENDENT = {"waiting_period", "coverage_exclusion"}

def _semantic_failed(state: ClaimState) -> bool:
    return any(f.agent == "semantic_map" for f in state.get("failures", []))

def _make_rule_node(name: str):
    @resilient(name)
    def run(state: ClaimState):
        started = time.monotonic()
        from app.models.schemas import SemanticMapping
        ctx = RuleContext(state["submission"], state["member"], state["extractions"],
                          state.get("semantic") or SemanticMapping(confidence=0.3), pe())
        v = RULES[name].check(ctx)
        if name in _SEMANTIC_DEPENDENT and v.status == "PASS" and _semantic_failed(state):
            v = RuleVerdict(rule=cast(RuleName, name), status="FLAG", reason_code="SEMANTIC_MAPPING_UNAVAILABLE",
                detail=("The semantic mapping step failed, so waiting-period / exclusion checks "
                        "could not be reliably evaluated. Routing to manual review rather than "
                        "approving on an incomplete check."),
                policy_refs=v.policy_refs, certainty=0.0)
        return {"rule_verdicts": [v],
                "trace": [trace("adjudicate", name, v.status, v.detail,
                                policy_refs=v.policy_refs, started=started)]}
    return run
_RULE_NODES = {n: _make_rule_node(n) for n in RULES}

def financial_calc(state: ClaimState):
    started = time.monotonic()
    s = state["submission"]
    try:
        disallowed = [d for v in state["rule_verdicts"] for d in v.disallowed_items]
        items = [i for e in state["extractions"]
                 if e.doc_type in ("HOSPITAL_BILL", "PHARMACY_BILL") for i in e.line_items]
        # Fallback: a real bill whose individual line items weren't extracted should not be
        # treated as ₹0. Use the extracted bill total, else the submitted claimed amount, as a
        # single synthetic line item so the payout is computed on the real amount.
        if not items:
            total = next((e.total_amount.value for e in state["extractions"]
                          if e.doc_type in ("HOSPITAL_BILL", "PHARMACY_BILL")
                          and e.total_amount.value), None)
            fallback = total or (s.claimed_amount if s.claimed_amount and s.claimed_amount > 0 else None)
            if fallback:
                items = [LineItem(description="Claimed amount", amount=float(fallback))]
        hospital = s.hospital_name or next((e.hospital_name.value for e in state["extractions"]
                                            if e.hospital_name.value), None)
        fb = calculate(pe(), s.claim_category, pe().is_network(hospital), items, disallowed)
    except Exception as e:
        # Never crash: produce a zeroed breakdown and a degraded trace; aggregate routes to
        # MANUAL_REVIEW on a zero breakdown with no line items.
        fb = FinancialBreakdown(gross=0.0, approved_amount=0.0,
                                steps=["financial calculation failed"])
        return {"financial": fb,
                "failures": [ComponentFailure(agent="financial", failure_mode=str(e)[:200])],
                "trace": [trace("financial", "financial_calculator", "ERROR",
                                f"financial calculation failed: {str(e)[:120]} — continuing degraded",
                                started=started, degraded=True, failure_mode=str(e)[:120])]}
    return {"financial": fb,
            "trace": [trace("financial", "financial_calculator", "PASS", " | ".join(fb.steps),
                            policy_refs=[f"opd_categories.{s.claim_category.lower()}"], started=started)]}

def decide(state: ClaimState):
    started = time.monotonic()
    try:
        d = aggregate(state["rule_verdicts"], state.get("financial"),
                      pe().fraud_thresholds()["auto_manual_review_above"])
    except Exception as e:
        # Never crash: route to manual review with a valid decision so explain can finish.
        d = Decision(status="MANUAL_REVIEW", approved_amount=0.0,
                     reason_codes=[ReasonCode(code="INTERNAL_FAILURE",
                                              detail=f"Decision aggregation failed: {str(e)[:120]}")],
                     member_message=("We hit a technical problem processing your claim; it has been "
                                     "routed to a human reviewer."),
                     recommendations=["Decision aggregation failed internally; manual review required."])
        return {"decision": d,
                "failures": [ComponentFailure(agent="decide", failure_mode=str(e)[:200])],
                "trace": [trace("decide", "decision_aggregator", "ERROR",
                                f"decision aggregation failed: {str(e)[:120]} — routed to manual review",
                                started=started, degraded=True, failure_mode=str(e)[:120])]}
    return {"decision": d,
            "trace": [trace("decide", "decision_aggregator", "INFO", d.member_message,
                            detail={"decision": d.status,
                                    "reason_codes": [r.code for r in d.reason_codes]}, started=started)]}

@resilient("verifier")
def verifier_node(state: ClaimState):
    started = time.monotonic()
    v, usage = verify_with_usage(cast(Decision, state["decision"]), state["rule_verdicts"])
    return {"verifier": v,
            "trace": [trace("verify", "decision_verifier", v.verdict,
                            f"judge: {v.verdict} (conf {v.confidence:.2f}) — {v.reason}",
                            model=settings.gemini_pro_model, started=started,
                            input_tokens=usage.get("input_tokens"),
                            output_tokens=usage.get("output_tokens"))]}

def explain(state: ClaimState):
    started = time.monotonic()
    if state.get("problems"):
        return {"trace": [trace("explain", "explainer", "FAIL",
                                "Claim stopped before decision: " + state["problems"][0].message,
                                started=started)]}
    d: Decision = cast(Decision, state["decision"])
    verdict_fields = [e for ex in state["extractions"] for f, e in
                      (("patient", ex.patient_name), ("total", ex.total_amount))]
    load_bearing = [f.confidence for f in verdict_fields if f.value is not None]
    extraction_quality = sum(load_bearing) / len(load_bearing) if load_bearing else 0.3
    certs = [v.certainty for v in state["rule_verdicts"] if v.status != "SKIPPED"]
    rule_certainty = sum(certs) / len(certs) if certs else 0.3
    completeness = min(1.0, len(certs) / len(RULES))
    ver = state.get("verifier")
    verifier_agreement = (ver.confidence if ver and ver.verdict == "PASS" else 0.0) if ver else 0.5
    score = compute(extraction_quality, rule_certainty, completeness, verifier_agreement,
                    failures=len(state.get("failures", [])))
    d.confidence, d.confidence_components = score.final, score.components
    if ver and ver.verdict == "FAIL" and d.status in ("APPROVED", "PARTIAL"):
        d.status = "MANUAL_REVIEW"
        d.recommendations.append(f"Independent verifier flagged an inconsistency: {ver.reason}")
    if state.get("failures"):
        d.recommendations.append("One or more components failed during processing; "
                                 "manual review is recommended despite the automated decision.")
    return {"decision": d,
            "trace": [trace("explain", "explainer", "INFO",
                            f"final={d.status} amount=₹{d.approved_amount:,.2f} "
                            f"confidence={d.confidence}", started=started)]}
