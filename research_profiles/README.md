# Company Research Profiles (Step 2)

This directory contains research profiles for selected listed equities under research in `D:\stock-project`. These profiles synthesize verified factual data from official company filings, regulatory submissions, corporate websites, and public announcements to document business models, revenue segments, partners/customers/suppliers, key dependencies, and material risks.

---

## 1. Directory Structure

```
research_profiles/
├── README.md               # Documentation of schema, field definitions, and methodology
├── DELTACORP_BO.json       # Research profile for Delta Corp Limited (DELTACORP.BO)
├── PPAP_BO.json            # Research profile for PPAP Automotive Limited (PPAP.BO)
└── MATRIMONY_BO.json       # Research profile for Matrimony.com Limited (MATRIMONY.BO)
```

---

## 2. Core Principles & Evidence Rules

1. **Separation of Verified Facts vs. Hypotheses**:
   - Factual assertions require a direct citation to a listed `source_id` accompanied by an exact excerpt (up to 25 words).
   - Plausible links or event transmission mechanisms that lack explicit documentary proof are strictly designated as `HYPOTHESIS`.
2. **Strict Identity Verification**:
   - The user-supplied name is compared against the entity's registered legal and exchange name.
   - Yahoo Finance profiles are referenced solely to corroborate official websites, never as primary factual evidence for business relationships.
3. **No Inferred Relationships**:
   - Customers, suppliers, and partners are recorded only when explicitly identified in an official disclosure or verified public announcement.
   - Missing names are marked `NOT DISCLOSED IN REVIEWED SOURCES`; they do not imply the company lacks trading partners.
4. **Temporal Precision**:
   - **Reporting Period**: The actual financial or regulatory period covered by the document (e.g., `FY 2023-24`, `Q2 FY 2024-25`).
   - **Publication Date**: The date the document was made public.
   - **Retrieval Date**: The calendar date when the source was accessed and evaluated. These dates must never be conflated.

---

## 3. Schema & Field Definitions

Each profile JSON file adheres to the following top-level structure:

| Field | Description |
|---|---|
| `_meta` | Identity verification, supplied name, verified name, ticker, retrieval timestamp, and methodology note. |
| `sources` | Array of source documents reviewed, each assigned a unique `source_id` (e.g., `S1`, `S2`). |
| `products_and_services` | Detailed breakdown of products, service lines, and the core business model. |
| `revenue_segments` | Segmental revenue splits (operating lines and geographies) as disclosed in financial statements. |
| `operating_geographies` | Geographic footprint of production, assets, and service delivery. |
| `named_customers` | Verifiably identified OEM or corporate clients (or marked not disclosed for B2C). |
| `named_suppliers_and_partners` | Explicitly disclosed vendors, technology collaborators, joint venture partners, and platforms. |
| `business_dependencies` | Critical inputs, regulatory licenses, technological platforms, and logistical requirements. |
| `announced_plans` | Formally announced capex projects, strategic investments, or restructuring initiatives with dates. |
| `material_risks` | Sourced regulatory disputes, tax demands, supply chain vulnerabilities, or market risks. |
| `event_pathways` | Cause-and-effect pathways evaluating how external shocks propagate to company performance. |
| `gaps_and_limitations` | Explicit categorization of unresolved research questions and unreviewed areas. |

---

## 4. Evidence-Status Labels for Event Pathways

Each entry under `event_pathways` must carry one of the following two statuses:

* **`SUPPORTED`**: The transmission mechanism or cause-and-effect relationship is explicitly corroborated by an official source (e.g., a tax demand directly threatening liquidity, or an OEM vehicle model rollout impacting component delivery).
* **`HYPOTHESIS`**: The relationship is economically or logically plausible, but the company's disclosed documents do not provide direct empirical proof.
  * *Requirement*: Every `HYPOTHESIS` entry must specify `evidence_needed` outlining the exact data or disclosures required for verification.

---

## 5. Classification of Gaps and Limitations

To maintain research integrity and prevent false assumptions, missing information is categorized into three standardized tiers:

* **`NOT DISCLOSED IN REVIEWED SOURCES`**: The relevant official documents (such as Annual Reports, Investor Presentations, or BSE filings) were consulted, but the company did not disclose the specific detail (e.g., proprietary raw material suppliers, individual B2C consumer names).
* **`NOT CHECKED`**: The specific domain, document type, or historical filing was outside the initial scope of review or has not yet been analyzed.
* **`SOURCE UNAVAILABLE`**: An identified reference document or URL could not be retrieved, was paywalled, or was blocked during the research phase.
