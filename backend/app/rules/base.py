from dataclasses import dataclass
from app.models.schemas import ClaimSubmission, ExtractionResult, SemanticMapping
from app.services.policy_engine import PolicyEngine

@dataclass
class RuleContext:
    submission: ClaimSubmission
    member: dict
    extractions: list[ExtractionResult]
    semantic: SemanticMapping
    pe: PolicyEngine

    @property
    def line_items(self):
        bills = [e for e in self.extractions if e.doc_type in ("HOSPITAL_BILL", "PHARMACY_BILL")]
        return [i for b in bills for i in b.line_items]
