# AI Engineer Assignment
## Health Insurance Claims Processing System

### About Plum

Plum is India's leading employee health benefits platform, protecting 6,000+ companies and 600,000+ lives. Our AI Pod builds the intelligent systems that power our claims, policy, and member care operations. We process 75,000+ claims annually today and are on a path to 10 million lives by 2030. The only way we get there without linearly scaling our operations team is by building systems that are reliable, explainable, and genuinely intelligent.

This assignment is a real problem we work on.

---

### The Problem

When an employee submits a health insurance claim, they upload a set of medical documents — bills, prescriptions, lab reports — along with some basic details. Someone on our team then reviews those documents against the member's policy to decide whether to approve, partially approve, or reject the claim.

This process is manual today. It is slow, inconsistent, and doesn't scale. Your job is to automate it.

---

### What the System Must Do

The following are non-negotiable behaviors. How you build them is entirely up to you.

**1. Accept a claim submission**
A claim consists of member details, the type of treatment, a claimed amount, and one or more uploaded documents (images or PDFs).

**2. Catch document problems early**
Before any processing happens, the system must verify that the right documents have been uploaded for the claim type. If a member uploads the wrong document — for example, a prescription where a hospital bill is required — the system must stop immediately and tell them exactly what is wrong and what they need to provide instead. A generic error is not acceptable. The message must be specific enough that the member knows precisely what to do next.

**3. Extract structured information**
The system must extract relevant information from the uploaded documents — patient details, diagnosis, treatment, amounts, dates, doctor details. Documents will not be clean. Expect handwritten prescriptions, rubber stamps over text, phone photos of bills, and inconsistent formats.

**4. Make a claim decision**
Using the extracted information and the member's policy terms, the system must produce one of the following decisions: `APPROVED`, `PARTIAL`, `REJECTED`, or `MANUAL_REVIEW`. Every decision must include the approved amount (if any), the reason, and a confidence score.

**5. Make every decision explainable**
For any claim, someone on the operations team must be able to look at the system's output and understand exactly what happened — what was checked, what passed, what failed, and why the final decision was made. If a claim was rejected because of a waiting period, the trace must show that. If confidence dropped because a document was partially unreadable, that must be visible too. Black-box decisions are not acceptable.

**6. Handle failures gracefully**
Individual components of your system will fail — LLM timeouts, parsing errors, bad inputs. The system must not crash. It must continue with whatever it has, reflect the degraded state in the output, and adjust its confidence accordingly.

---

### Policy and Member Data

The file `policy_terms.json` contains the complete policy configuration your system should use: coverage categories, sub-limits, co-pay rules, waiting periods, exclusions, pre-authorization requirements, network hospitals, and the member roster.

Your system must read and apply these rules from the file. Do not hardcode policy logic.

---

### Deliverables

**1. Working System**
A running application with a UI for claim submission and decision review. Provide a deployed URL or clear local setup instructions. Source code on GitHub or GitLab with a clean commit history.

**2. Architecture Document**
Explain the system you built. What are the components, how do they interact, and why did you design it this way? What did you consider and reject? What are the limitations of your current design and how would you address them at 10x the current load? This document is as important as the code.

**3. Component Contracts**
For every significant component in your system, define its interface: what it accepts as input, what it produces as output, and what errors it can raise. These should be precise enough that another engineer could reimplement any single component without reading its code.

**4. Eval Report**
Run all 12 test cases from `test_cases.json` through your system. For each case, show the decision your system produced, the full trace, and whether it matched the expected outcome. Where it didn't match, explain why.

**5. Demo Video** (8–12 minutes)
Cover three things: a claim that gets stopped early due to a document problem (show the error message), a successful end-to-end approval with the full trace visible, and one technical decision you are genuinely proud of and one you would change given more time.

---

### Evaluation Criteria

| Criteria | Weight | What We're Looking For |
|----------|--------|------------------------|
| **System Design** | 30% | Is the architecture well-reasoned? Are components cleanly separated with clear responsibilities? Does it hold up under failure? Would it scale? |
| **Engineering Quality** | 25% | Code clarity, error handling, data modeling, async where it matters, test coverage |
| **Observability** | 20% | Can we reconstruct exactly why any claim got any decision just from the trace? |
| **AI Integration** | 15% | Are LLMs being used thoughtfully? Is output structured and validated? Is failure handled? |
| **Document Verification** | 10% | Does early document detection work? Are error messages specific and actionable? |

---

### Bonus Points
- Multi-agentic architectures will will have bonus points System Design.

---

### Timeline

2-3 days from receipt of this assignment.

Submit your repository link, deployed URL, and eval report. Be prepared for a 60-minute technical review where you will walk us through your architecture and we will ask you to extend it live.

---

### Notes

- Make conscious trade-offs and document them — your judgment about what to cut is part of what we are evaluating.
- Every significant component must have tests. A system with no tests is incomplete.
- Use AI coding tools freely. We expect it.
- If you are stuck for more than two hours on something, make an assumption, document it, and move on.

---

### Resources

- `policy_terms.json` — policy configuration, coverage rules, member roster
- `test_cases.json` — 12 test cases with expected outcomes
- `sample_documents_guide.md` — Indian medical document formats and extraction guidance
