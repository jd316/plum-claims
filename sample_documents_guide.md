# Sample Documents Guide

## Overview

This guide describes the medical document types your Document Verification and Parsing agents must handle. Use this to build test documents and to design your extraction prompts. Real-world Indian medical documents are messy — your agents need to handle all the variations described here.

---

## Document Types

### 1. Medical Prescription (Doctor's Rx)

**Standard layout:**

```
┌─────────────────────────────────────────────────────┐
│  Dr. Arun Sharma, MBBS, MD (Internal Medicine)      │
│  Reg. No: KA/45678/2015                             │
│  City Medical Centre, 12 MG Road, Bengaluru         │
│  Ph: +91-80-XXXXXXXX                                │
├─────────────────────────────────────────────────────┤
│  Patient: Rajesh Kumar          Date: 01-Nov-2024   │
│  Age: 39 years   Gender: M                          │
│  Chief Complaint: Fever since 3 days, body ache     │
├─────────────────────────────────────────────────────┤
│  Diagnosis: Viral Fever                             │
│                                                     │
│  Rx:                                                │
│  1. Tab Paracetamol 650mg — 1-1-1 x 5 days          │
│  2. Tab Vitamin C 500mg — 0-0-1 x 7 days            │
│                                                     │
│  Investigations: CBC, Dengue NS1                    │
│  Follow-up: After 5 days if no improvement          │
│                                                     │
│                            [Doctor's Signature]     │
│                            [Registration Stamp]     │
└─────────────────────────────────────────────────────┘
```

**Key fields to extract:**
- Doctor name, registration number, specialization
- Patient name, age, gender, date
- Diagnosis (primary and secondary if any)
- Medicines with dosage and duration
- Tests ordered
- Hospital/clinic name and address

**Real-world variations your agent must handle:**
- Handwritten prescriptions (very common in India — may be partially illegible)
- Pre-printed templates with handwritten fill-ins
- Missing or partially visible registration numbers
- Diagnoses written in medical shorthand (HTN = Hypertension, T2DM = Type 2 Diabetes, etc.)
- Regional language names mixed with English medicine names
- Multiple pages
- Rubber stamps over text

---

### 2. Hospital Bill / Clinic Invoice

**Standard layout:**

```
┌─────────────────────────────────────────────────────┐
│  CITY MEDICAL CENTRE                                │
│  12 MG Road, Bengaluru – 560001                     │
│  GSTIN: 29XXXXX1234X1ZX                             │
│  Ph: 080-XXXXXXXX                                   │
├─────────────────────────────────────────────────────┤
│  BILL / RECEIPT                                     │
│  Bill No: CMC/2024/08321    Date: 01-Nov-2024       │
├─────────────────────────────────────────────────────┤
│  Patient Name: Rajesh Kumar                         │
│  Age/Gender: 39 / Male                              │
│  Referring Doctor: Dr. Arun Sharma                  │
├─────────────────────────────────────────────────────┤
│  DESCRIPTION                  QTY    RATE    AMOUNT │
│  Consultation Fee (OPD)        1    1000.00  1000.00│
│  CBC (Complete Blood Count)    1     200.00   200.00│
│  Dengue NS1 Antigen Test       1     300.00   300.00│
│                                                     │
│  Subtotal:                               1500.00    │
│  GST (0% on medical):                       0.00    │
│  Total Amount:                           1500.00    │
├─────────────────────────────────────────────────────┤
│  Payment Mode: Cash / UPI / Card                    │
│  Received by: [Cashier Name]    [Cashier Stamp]     │
└─────────────────────────────────────────────────────┘
```

**Key fields to extract:**
- Hospital name, address, GSTIN (if present)
- Bill number, date
- Patient name, age, gender
- Itemized line items with amounts
- GST amount (if any)
- Total amount

**Real-world variations:**
- No GSTIN on small clinics
- Line items described vaguely ("Medicines" instead of itemized drugs)
- Multiple bills for the same treatment (pharmacy separate from consultation)
- Handwritten bills from small clinics — no printed format
- Amounts written in words and figures (discrepancies possible)
- Cancellation marks or corrections on amounts

---

### 3. Diagnostic / Lab Report

**Standard layout:**

```
┌─────────────────────────────────────────────────────┐
│  PRECISION DIAGNOSTICS PVT LTD                      │
│  NABL Accredited Lab   |   Lab ID: KA-NABL-1234     │
│  45 Jayanagar, Bengaluru   |  Ph: 080-XXXXXXXX      │
├─────────────────────────────────────────────────────┤
│  Patient: Rajesh Kumar                              │
│  Age/Sex: 39 / Male                                 │
│  Ref Doctor: Dr. Arun Sharma                        │
│  Sample Date: 01-Nov-2024   Report Date: 01-Nov-2024│
│  Sample ID: PD-2024-18723                           │
├─────────────────────────────────────────────────────┤
│  TEST NAME          RESULT    UNIT    NORMAL RANGE  │
│  CBC:                                               │
│  Hemoglobin         13.2      g/dL    13.0 – 17.0   │
│  WBC Count          9,800     /μL     4,500 – 11,000│
│  Platelet Count     185,000   /μL    150,000–450,000│
│                                                     │
│  Dengue NS1 Antigen  NEGATIVE           —           │
├─────────────────────────────────────────────────────┤
│  Remarks: WBC count is towards upper normal limit.  │
│  Clinical correlation advised.                      │
│                                                     │
│  Dr. Meena Pillai, MD (Pathology)                   │
│  Reg. No: KA/89012/2018    [Signature & Stamp]      │
└─────────────────────────────────────────────────────┘
```

**Key fields to extract:**
- Lab name, NABL status
- Patient name, age, gender
- Referring doctor
- Sample date and report date
- Each test name, result, unit, normal range
- Pathologist name and registration
- Any remarks/interpretation

---

### 4. Pharmacy Bill

**Standard layout:**

```
┌─────────────────────────────────────────────────────┐
│  HEALTH FIRST PHARMACY                              │
│  Drug Lic. No: KA-BLR-XXXX                          │
│  22 Brigade Road, Bengaluru                         │
├─────────────────────────────────────────────────────┤
│  Bill No: HFP-24-09821    Date: 01-Nov-2024         │
│  Patient: Rajesh Kumar    Dr: Dr. Arun Sharma       │
├─────────────────────────────────────────────────────┤
│  MEDICINE        BATCH   EXP    QTY  MRP    AMT     │
│  Paracetamol 650 A2341  03/26    15  2.50   37.50   │
│  Vitamin C 500   B7821  06/26    10  4.00   40.00   │
│                                                     │
│  Subtotal:                              77.50       │
│  Discount (5%):                         -3.88       │
│  Net Amount:                            73.62       │
├─────────────────────────────────────────────────────┤
│  Pharmacist: R. Sharma   [Stamp]                    │
└─────────────────────────────────────────────────────┘
```

**Key fields to extract:**
- Pharmacy name, drug license number
- Bill number, date
- Patient name, prescribing doctor
- Each medicine with batch, expiry, quantity, MRP, amount
- Discounts if any
- Net amount

---

## Doctor Registration Number Formats

Indian medical registration numbers follow state-specific formats. Your parsing agent must recognize and validate these:

| State | Format | Example |
|-------|--------|---------|
| Karnataka | KA/XXXXX/YYYY | KA/45678/2015 |
| Maharashtra | MH/XXXXX/YYYY | MH/23456/2018 |
| Delhi | DL/XXXXX/YYYY | DL/34567/2016 |
| Tamil Nadu | TN/XXXXX/YYYY | TN/56789/2013 |
| Gujarat | GJ/XXXXX/YYYY | GJ/56789/2014 |
| Andhra Pradesh | AP/XXXXX/YYYY | AP/67890/2017 |
| Uttar Pradesh | UP/XXXXX/YYYY | UP/45678/2016 |
| West Bengal | WB/XXXXX/YYYY | WB/34567/2015 |
| Kerala | KL/XXXXX/YYYY | KL/78901/2012 |
| Ayurveda (national) | AYUR/[STATE]/XXXXX/YYYY | AYUR/KL/2345/2019 |

---

## Common Indian Diagnoses Your Agents Will See

| Category | Common Diagnoses |
|----------|-----------------|
| Infections | Viral Fever, URI, Gastroenteritis, UTI, Dengue, Typhoid |
| Chronic | Hypertension (HTN), Type 2 Diabetes (T2DM), Hypothyroidism |
| Respiratory | Acute Bronchitis, Asthma, COPD exacerbation |
| Musculoskeletal | Lumbar Spondylosis, Cervical Spondylitis, Knee Osteoarthritis |
| Neurological | Migraine, Tension Headache, Vertigo |
| Dental | Dental Caries, Periapical Abscess, Gingivitis |
| Gastrointestinal | GERD, IBS, Peptic Ulcer Disease |

---

## Document Quality Variations to Handle

Your pipeline will need to be tested against all of these:

| Variation | Description | Handling Strategy |
|-----------|-------------|-------------------|
| Handwritten prescription | Fully or partially handwritten Rx | Use vision model with explicit OCR prompts |
| Phone photo of bill | Skewed, low contrast, partial shadows | Pre-process or prompt for best-effort extraction |
| Rubber stamp over text | Registration number or amounts obscured | Flag as LOW confidence field, do not fail entire doc |
| Multilingual doc | Hindi/Tamil/Telugu mixed with English | Extract English fields; flag regional fields as unextracted |
| Partial document | Page cut off or folded | Extract available fields; flag missing fields explicitly |
| Multiple corrections | Amounts crossed out and rewritten | Flag `DOCUMENT_ALTERATION` in fraud check |
| Duplicate stamp | Multiple "ORIGINAL" / "DUPLICATE" stamps | Note in extraction; surface to fraud detection |
| Scanned PDF (multi-page) | 4-5 page detailed bill | Process each page separately; aggregate line items |

---

## How to Create Mock Test Documents

For your own testing, you can generate mock documents using:

**Option 1 — HTML/CSS rendered to image:**
```html
<!-- Build a prescription template in HTML, screenshot it -->
<!-- Use puppeteer or a browser screenshot tool -->
```

**Option 2 — Python (ReportLab or fpdf2):**
```python
from fpdf import FPDF
pdf = FPDF()
pdf.add_page()
pdf.set_font("Helvetica", size=12)
pdf.cell(200, 10, txt="Dr. Arun Sharma", ln=True)
# ... build full document
pdf.output("prescription.pdf")
```

**Option 3 — PIL/Pillow for image-based mocks:**
```python
from PIL import Image, ImageDraw, ImageFont
img = Image.new('RGB', (800, 1000), color='white')
draw = ImageDraw.Draw(img)
draw.text((50, 50), "Dr. Arun Sharma", fill='black')
# ... add text fields
img.save("prescription.jpg")
```

**For blur/noise simulation:**
```python
import cv2
blurred = cv2.GaussianBlur(image, (15, 15), 0)
# or add noise for poor quality simulation
```

