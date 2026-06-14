import operator, time
from typing import Annotated, Any, TypedDict, Optional, cast
from app.models.schemas import (ClaimSubmission, ExtractionResult, SemanticMapping, RuleVerdict,
                                FinancialBreakdown, Decision, DocumentProblem, TraceEntry,
                                ComponentFailure, VerifierResult)

class ClaimState(TypedDict, total=False):
    submission: ClaimSubmission
    member: dict
    extractions: Annotated[list[ExtractionResult], operator.add]
    problems: list[DocumentProblem]
    semantic: Optional[SemanticMapping]
    rule_verdicts: Annotated[list[RuleVerdict], operator.add]
    financial: Optional[FinancialBreakdown]
    decision: Optional[Decision]
    verifier: Optional[VerifierResult]
    trace: Annotated[list[TraceEntry], operator.add]
    failures: Annotated[list[ComponentFailure], operator.add]

def trace(step: str, agent: str, status: str, summary: str, *, detail: dict | None = None,
          policy_refs: list[str] | None = None, model: str | None = None, started: float | None = None,
          degraded: bool = False, failure_mode: str | None = None,
          input_tokens: int | None = None, output_tokens: int | None = None,
          confidence_delta: float | None = None) -> TraceEntry:
    return TraceEntry(step=step, agent=agent, status=cast(Any, status), summary=summary, detail=detail or {},
                      policy_refs=policy_refs or [], model=model, degraded=degraded,
                      failure_mode=failure_mode, confidence_delta=confidence_delta,
                      input_tokens=input_tokens, output_tokens=output_tokens,
                      duration_ms=int((time.monotonic() - started) * 1000) if started else 0)
