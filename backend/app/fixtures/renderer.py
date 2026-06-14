"""Render realistic Indian medical document PNGs from test-case JSON.

These are REAL files consumed by the REAL vision pipeline — synthetic fixtures,
not mocks. Layouts follow sample_documents_guide.md.
"""
import os
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 900, 1100
def _font(size: int):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if os.path.exists(p): return ImageFont.truetype(p, size)
    return ImageFont.load_default()

F_H1, F_BODY, F_SMALL = _font(34), _font(24), _font(19)

def _page():
    img = Image.new("RGB", (W, H), "#fdfcf8")
    return img, ImageDraw.Draw(img)

def _header(d, title, sub):
    d.rectangle([0, 0, W, 110], fill="#f0ece2")
    d.text((40, 22), title, font=F_H1, fill="#1a1a1a")
    d.text((40, 66), sub, font=F_SMALL, fill="#444")
    d.line([40, 130, W - 40, 130], fill="#999", width=2)

def _kv(d, y, label, value):
    d.text((40, y), f"{label}:", font=F_BODY, fill="#555")
    d.text((300, y), str(value), font=F_BODY, fill="#111")
    return y + 42

def _items(d, y, items, total):
    d.text((40, y), "DESCRIPTION", font=F_SMALL, fill="#555")
    d.text((680, y), "AMOUNT (Rs.)", font=F_SMALL, fill="#555"); y += 34
    d.line([40, y, W - 40, y], fill="#bbb", width=1); y += 12
    for it in items:
        d.text((40, y), it["description"], font=F_BODY, fill="#111")
        d.text((680, y), f"{it['amount']:.2f}", font=F_BODY, fill="#111"); y += 40
    d.line([40, y, W - 40, y], fill="#bbb", width=1); y += 14
    d.text((480, y), "Total Amount:", font=F_BODY, fill="#111")
    d.text((680, y), f"{total:.2f}", font=F_BODY, fill="#111")
    return y + 50

def _signoff(d, doctor_name: str, reg: str) -> None:
    """Draw a realistic doctor sign-off: a signature stroke with the signed name above
    it, plus a bordered registration stamp — so the synthetic Rx reads like a real one
    (no bracketed placeholder text for the vision model to flag as fake)."""
    name = (doctor_name or "Dr. A. Sharma").split(",")[0].strip()
    d.text((610, H - 182), name, font=F_BODY, fill="#1a3a8f")          # the "signature"
    d.line([600, H - 150, 824, H - 150], fill="#1a3a8f", width=2)       # signature stroke
    d.text((600, H - 144), "Signature", font=F_SMALL, fill="#888")
    d.rectangle([600, H - 116, 880, H - 64], outline="#7a1f1f", width=3)  # rubber stamp
    d.text((614, H - 108), "REGD. MEDICAL PRACTITIONER", font=F_SMALL, fill="#7a1f1f")
    if reg:
        d.text((614, H - 88), f"Reg. No: {reg}", font=F_SMALL, fill="#7a1f1f")

def _render_doc(doc: dict, case_input: dict) -> Image.Image:
    c = doc.get("content", {})
    dtype = doc["actual_type"]
    patient = doc.get("patient_name_on_doc") or c.get("patient_name") or ""
    date = c.get("date") or case_input.get("treatment_date", "")
    img, d = _page()
    if dtype == "PRESCRIPTION":
        _header(d, c.get("doctor_name", "Dr. A. Sharma, MBBS MD"),
                f"Reg. No: {c.get('doctor_registration','')}  |  City Medical Centre, Bengaluru")
        y = 160
        if patient: y = _kv(d, y, "Patient", patient)
        y = _kv(d, y, "Date", date)
        if c.get("diagnosis"): y = _kv(d, y, "Diagnosis", c["diagnosis"])
        if c.get("treatment"): y = _kv(d, y, "Treatment", c["treatment"])
        if c.get("tests_ordered"): y = _kv(d, y, "Investigations", ", ".join(c["tests_ordered"]))
        if c.get("medicines"):
            d.text((40, y + 8), "Rx:", font=F_H1, fill="#111"); y += 64
            for i, m in enumerate(c["medicines"], 1):
                d.text((70, y), f"{i}. {m}", font=F_BODY, fill="#111"); y += 40
        # Realistic signed-off footer: a signature stroke with the doctor's name above
        # it + a bordered registration stamp. NOT literal "[placeholder]" brackets — a
        # capable vision model correctly flags bracketed placeholder text as a fake stamp.
        _signoff(d, c.get("doctor_name", "Dr. A. Sharma"), c.get("doctor_registration", ""))
    elif dtype in ("HOSPITAL_BILL", "PHARMACY_BILL"):
        name = c.get("hospital_name") or c.get("pharmacy_name") or "City Medical Centre"
        _header(d, name.upper(), "Bengaluru – 560001  |  BILL / RECEIPT")
        y = 160
        if patient: y = _kv(d, y, "Patient Name", patient)
        y = _kv(d, y, "Date", date)
        if c.get("doctor_name"): y = _kv(d, y, "Referring Doctor", c["doctor_name"])
        items = c.get("line_items") or [{"description": "Charges", "amount": c.get("total", case_input["claimed_amount"])}]
        total = c.get("total", sum(i["amount"] for i in items))
        y = _items(d, y + 16, items, total)
        d.text((40, H - 100), "Payment Mode: Cash / UPI / Card", font=F_SMALL, fill="#666")
        # Realistic "PAID/RECEIVED" stamp box instead of a "[Cashier Stamp]" placeholder.
        d.rectangle([600, H - 116, 856, H - 64], outline="#1f5f1f", width=3)
        d.text((616, H - 108), "PAID — RECEIVED", font=F_BODY, fill="#1f5f1f")
        d.text((616, H - 84), "Authorised Signatory", font=F_SMALL, fill="#1f5f1f")
    else:  # LAB_REPORT and others
        _header(d, "PRECISION DIAGNOSTICS PVT LTD", "NABL Accredited Lab | Bengaluru")
        y = 160
        if patient: y = _kv(d, y, "Patient", patient)
        y = _kv(d, y, "Report Date", date)
        y = _kv(d, y, "Test", c.get("test_name", "Diagnostic Test"))
        d.text((40, y + 10), "Result: See attached findings. Clinical correlation advised.", font=F_BODY, fill="#111")
        d.text((600, H - 120), "Dr. M. Pillai, MD (Path)", font=F_SMALL, fill="#333")
    if doc.get("quality") == "UNREADABLE":
        img = img.filter(ImageFilter.GaussianBlur(radius=14))
    return img

def render_case_documents(case: dict, out_dir: str) -> dict[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    paths: dict[str, str] = {}
    for doc in case["input"]["documents"]:
        img = _render_doc(doc, case["input"])
        p = os.path.join(out_dir, f"{doc['file_id']}.png")
        img.save(p)
        paths[doc["file_id"]] = p
    return paths
