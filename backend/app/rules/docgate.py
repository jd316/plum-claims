"""Early-exit document gate. Deterministic; runs on extraction results before any adjudication."""
from app.models.schemas import ExtractionResult, DocumentProblem
from app.services.policy_engine import PolicyEngine
from app.services.identity import check_patient_consistency

# Member-friendly names for document types (used in the user-facing messages).
_TYPE_LABEL = {
    "PRESCRIPTION": "prescription", "HOSPITAL_BILL": "hospital bill",
    "PHARMACY_BILL": "pharmacy bill", "LAB_REPORT": "lab report",
    "DIAGNOSTIC_REPORT": "diagnostic report", "DENTAL_REPORT": "dental report",
    "DISCHARGE_SUMMARY": "discharge summary",
}

# A LAB_REPORT and a DIAGNOSTIC_REPORT are the same KIND of evidence — a diagnostic test
# result. An imaging report (e.g. an MRI/CT scan) is legitimately classifiable as either, so
# the vision model can label it either way on different runs. The rules and financial logic
# never distinguish the two, so for DOCUMENT-GATING we treat them as interchangeable: an
# ambiguous classification must not block an otherwise-complete claim. Membership checks below
# compare canonicalised types; user-facing messages still name the type actually uploaded.
_DOCGATE_EQUIVALENTS = {"DIAGNOSTIC_REPORT": "LAB_REPORT"}
def _canon_doc_type(t: str) -> str:
    return _DOCGATE_EQUIVALENTS.get(t, t)

def _unreadable_label(e: ExtractionResult, extractions: list[ExtractionResult],
                      required: list[str], file_names: dict[str, str] | None) -> str:
    """Name an unreadable document the way a member would recognise it: by its type if the
    vision model classified it, else by the required document it must be (a readable sibling
    covers the other required types), else by the original filename, else the file id."""
    if e.doc_type in _TYPE_LABEL:
        return f"your {_TYPE_LABEL[e.doc_type]}"
    readable_types = {x.doc_type for x in extractions if x.quality.readable}
    unmet = [t for t in required if t not in readable_types]
    if len(unmet) == 1:
        return f"your {_TYPE_LABEL.get(unmet[0], unmet[0].lower().replace('_', ' '))}"
    name = (file_names or {}).get(e.file_id)
    return f"the document you uploaded (‘{name}’)" if name else f"document ‘{e.file_id}’"

def check_missing_critical_fields(extractions: list[ExtractionResult],
                                  category: str) -> list[DocumentProblem]:
    """Narrowly-scoped missing-info follow-up. Runs LAST, only on readable docs that
    passed type/patient checks. Asks a SPECIFIC question when a decision-critical field
    is genuinely UNREADABLE (null) and unrecoverable — never guesses, never rejects.

    Deliberately narrow so the 12 cases are untouched:
      • bill (HOSPITAL_BILL/PHARMACY_BILL) with total_amount.value is None AND no
        line_items to sum → can't determine the amount → ask the member for the total.
      • PRESCRIPTION with diagnosis.value is None → ask the condition treated.
    We do NOT gate on patient_name or line_items presence (a bill may legitimately
    have no printed name, e.g. TC009's {total: 4800}-only bill)."""
    problems: list[DocumentProblem] = []
    for e in extractions:
        if not e.quality.readable:
            continue  # readability is handled by the earlier gate; only ask on readable docs
        if e.doc_type in ("HOSPITAL_BILL", "PHARMACY_BILL"):
            if e.total_amount.value is None and not e.line_items:
                label = _TYPE_LABEL.get(e.doc_type, "bill")
                problems.append(DocumentProblem(kind="NEEDS_MEMBER_INPUT", file_id=e.file_id,
                    message=(f"We couldn't read the total amount on your {label}. Please "
                             f"re-upload a clearer copy or reply with the exact bill total (₹) "
                             f"so we can proceed.")))
        elif e.doc_type == "PRESCRIPTION":
            if e.diagnosis.value is None:
                problems.append(DocumentProblem(kind="NEEDS_MEMBER_INPUT", file_id=e.file_id,
                    message=("We couldn't read the diagnosis/condition on your prescription. "
                             "Please re-upload a clearer copy or tell us the condition that "
                             "was treated.")))
    return problems

def check_documents(extractions: list[ExtractionResult], category: str,
                    member_name: str, pe: PolicyEngine,
                    file_names: dict[str, str] | None = None) -> list[DocumentProblem]:
    problems: list[DocumentProblem] = []
    reqs = pe.document_requirements(category)
    # 1) unreadable required docs → ask re-upload (checked first: an unreadable doc can't prove its type)
    for e in extractions:
        if not e.quality.readable:
            label = _unreadable_label(e, extractions, reqs["required"], file_names)
            issues = ", ".join(e.quality.quality_issues) or "unreadable"
            problems.append(DocumentProblem(kind="UNREADABLE_DOCUMENT", file_id=e.file_id,
                message=(f"We could not read {label} ({issues}). Your claim is on hold — please "
                         f"re-upload a clear photo or scan of {label}. "
                         f"The rest of your documents are fine.")))
    if problems:
        return problems
    # 2) required types present? (compare canonicalised types so LAB_REPORT/DIAGNOSTIC_REPORT
    #    are interchangeable — an MRI report classified either way still satisfies the requirement)
    have = [e.doc_type for e in extractions]
    have_canon = {_canon_doc_type(t) for t in have}
    missing = [t for t in reqs["required"] if _canon_doc_type(t) not in have_canon]
    if missing:
        uploaded = ", ".join(sorted(set(have)))
        # Distinguish a genuine WRONG document (a type that has no role in this category —
        # e.g. a dental report for a consultation) from a merely INCOMPLETE-but-correct set
        # (TC001: two prescriptions, both valid types, just missing the bill). A wrong type
        # only blocks when a required doc is also missing; an extra out-of-category doc
        # alongside a complete set is harmless and must not block a processable claim.
        allowed = {_canon_doc_type(t) for t in set(reqs["required"]) | set(reqs.get("optional", []))}
        wrong = sorted({t for t in have if _canon_doc_type(t) not in allowed})
        if wrong:
            wrong_labels = ", ".join(wrong)
            is_are = "is" if len(wrong) == 1 else "are"
            problems.append(DocumentProblem(kind="WRONG_DOCUMENT",
                message=(f"For a {category} claim we need: {', '.join(reqs['required'])}. "
                         f"You uploaded {wrong_labels}, which {is_are} not used for a "
                         f"{category} claim. Please upload the {' and '.join(missing)} "
                         f"and resubmit.")))
            return problems
        problems.append(DocumentProblem(kind="MISSING_DOCUMENT",
            message=(f"For a {category} claim we need: {', '.join(reqs['required'])}. "
                     f"You uploaded: {uploaded}. Missing: {', '.join(missing)}. "
                     f"Please upload the {' and '.join(missing)} and resubmit — "
                     f"a {uploaded.split(',')[0].strip()} alone is not sufficient.")))
        return problems
    # 3) same patient everywhere?
    problems = check_patient_consistency(extractions, member_name)
    if problems:
        return problems
    # 4) decision-critical field missing on an otherwise-valid doc? → ask the member.
    return check_missing_critical_fields(extractions, category)
