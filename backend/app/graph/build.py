from typing import Any, cast
from langgraph.graph import StateGraph, START, END
from app.graph.state import ClaimState, trace
from app.graph import nodes
from app.models.schemas import Decision, ReasonCode, ComponentFailure

def build_graph():
    g = StateGraph(ClaimState)
    g.add_node("intake", nodes.intake)
    g.add_node("extract_doc", cast(Any, nodes.extract_doc))  # fan-out node takes a Send payload, not ClaimState
    g.add_node("docgate", nodes.docgate, defer=True)
    g.add_node("semantic_map", nodes.semantic_map)
    g.add_node("supervisor", nodes.supervisor)
    g.add_node("rule_check", cast(Any, nodes.rule_check))  # fan-out node takes a Send payload, not ClaimState
    g.add_node("financial", nodes.financial_calc, defer=True)
    g.add_node("decide", nodes.decide)
    g.add_node("verifier", nodes.verifier_node)
    g.add_node("explain", nodes.explain)

    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", nodes.fan_out_extraction, ["extract_doc", "explain"])
    g.add_edge("extract_doc", "docgate")
    g.add_conditional_edges("docgate", nodes.route_after_docgate, ["semantic_map", "explain"])
    g.add_edge("semantic_map", "supervisor")
    g.add_conditional_edges("supervisor", nodes.fan_out_rules, ["rule_check"])
    g.add_edge("rule_check", "financial")
    g.add_edge("financial", "decide")
    g.add_edge("decide", "verifier")
    g.add_edge("verifier", "explain")
    g.add_edge("explain", END)
    return g.compile()

GRAPH = build_graph()

def _graceful_failure_state(exc: Exception) -> dict:
    """Last-resort final state when the graph itself raises. Never let run_claim crash:
    the assignment requires the system never 500s a request. Routes to MANUAL_REVIEW."""
    decision = Decision(
        status="MANUAL_REVIEW", approved_amount=0.0,
        reason_codes=[ReasonCode(code="INTERNAL_FAILURE",
                                 detail=f"Pipeline error: {str(exc)[:120]}")],
        member_message=("We hit a technical problem processing your claim; it has been "
                        "routed to a human reviewer."),
        recommendations=["The automated pipeline failed internally; a human reviewer must "
                         "process this claim manually."])
    return {
        "decision": decision,
        "trace": [trace("run_claim", "pipeline", "ERROR",
                        f"Unhandled pipeline failure — routed to manual review: {str(exc)[:120]}",
                        degraded=True, failure_mode=str(exc)[:120])],
        "failures": [ComponentFailure(agent="pipeline", failure_mode=str(exc)[:200],
                                      recoverable=False)],
    }

def run_claim(submission) -> dict:
    """Run the full claims pipeline. NEVER raises: an unexpected graph failure is caught and
    converted into a valid degraded MANUAL_REVIEW final state."""
    try:
        return GRAPH.invoke({"submission": submission}, config={"max_concurrency": 4})
    except Exception as e:
        return _graceful_failure_state(e)
