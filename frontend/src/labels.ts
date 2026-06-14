// Shared human-friendly labels for backend enums.

import type { ClaimCategory, DocType } from "./types";

export const CATEGORY_LABELS: Record<ClaimCategory, string> = {
  CONSULTATION: "Consultation",
  DIAGNOSTIC: "Diagnostic",
  PHARMACY: "Pharmacy",
  DENTAL: "Dental",
  VISION: "Vision",
  ALTERNATIVE_MEDICINE: "Alternative medicine",
};

export const DOC_TYPE_LABELS: Record<DocType, string> = {
  PRESCRIPTION: "Prescription",
  HOSPITAL_BILL: "Hospital bill",
  PHARMACY_BILL: "Pharmacy bill",
  LAB_REPORT: "Lab report",
  DIAGNOSTIC_REPORT: "Diagnostic report",
  DENTAL_REPORT: "Dental report",
  DISCHARGE_SUMMARY: "Discharge summary",
  UNKNOWN: "Document",
};
