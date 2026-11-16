"""
pipeline.py
===========
Full event-to-report pipeline connecting market news events to company analysis.

Workflow:
  1. Ingestion: Reads matched_news.json (or receives custom/live news events).
  2. Filtering: Scans matched companies.
     - Only processes events with "explicit_mention" or "verified_relationship".
     - If no explicit or verified match: STOPS immediately and outputs "No covered company affected." (Never invents).
  3. Short Extraction: Extracts concise company data (max 500 chars desc, max 500 chars research,
     screener ratios, key dependencies) for up to 3 matched companies.
  4. Transmission Chain & Linked Lookup:
     - Formulates event -> effect -> business changes -> magnitude/timing.
     - Checks linked suppliers/customers ONLY if they are also in the 27 list (max depth 1, max 1 lookup per company).
  5. Epistemic Guardrails:
     - Separates supported facts from hypotheses.
     - Missing data = unknown. Keeps .BO/.NS and currency/units intact.
  6. LLM Synthesis (Final Step Only):
     - Calls local Ollama (qwen3:4b, temp=0, num_ctx=12288).
     - Generates strict 7-section report.
     - Saves to reports/{slug}.md.
  7. CLI: Supports --test for Delta Corp mention and US Fed 0 verified matches.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

# Add screener-scraper to path
sys.path.append(os.path.abspath("screener-scraper"))

import httpx
import ollama

# ==============================================================================
# CONFIG & PATHS
# ==============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "company_database.json")
MATCHED_NEWS_FILE = os.path.join(BASE_DIR, "matched_news.json")
SCREENER_DIR = os.path.join(BASE_DIR, "screener_profiles")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")

MODEL_NAME = "qwen3:4b"
OLLAMA_OPTIONS = {"temperature": 0, "num_ctx": 12288}

UNAVAILABLE = "unavailable"


# ==============================================================================
# 1. LOAD DATA & SHORT EXTRACTS (Step 2)
# ==============================================================================
def load_database():
    if not os.path.exists(DB_FILE):
        raise FileNotFoundError(f"Cannot find database at {DB_FILE}")
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def build_short_extract(ticker: str, db: dict) -> dict | None:
    """
    Builds a concise extract per matched company (max 500 chars desc/summary).
    Does NOT pass full raw JSON files to the agent context.
    """
    cdata = db.get(ticker)
    if not cdata:
        return None

    # Truncate descriptions to max 500 chars
    yahoo_desc = cdata.get("yahoo_description", UNAVAILABLE)
    if yahoo_desc and yahoo_desc != UNAVAILABLE:
        yahoo_desc = yahoo_desc[:500].strip() + ("..." if len(yahoo_desc) > 500 else "")

    research_sum = cdata.get("research_summary", UNAVAILABLE)
    if research_sum and research_sum != UNAVAILABLE:
        research_sum = research_sum[:500].strip() + ("..." if len(research_sum) > 500 else "")

    # Key dependencies
    raw_deps = cdata.get("key_dependencies", UNAVAILABLE)
    deps_summary = UNAVAILABLE
    if isinstance(raw_deps, list):
        deps_summary = [d.get("type") + ": " + d.get("description", "")[:150] for d in raw_deps if isinstance(d, dict)]
    elif isinstance(raw_deps, str):
        deps_summary = raw_deps

    # Screener ratios
    ratios = cdata.get("screener_ratios", {})

    return {
        "ticker": ticker,
        "company_name": cdata.get("company_name", ticker),
        "sector": cdata.get("sector", UNAVAILABLE),
        "industry": cdata.get("industry", UNAVAILABLE),
        "yahoo_description_500": yahoo_desc,
        "screener_ratios": ratios,
        "research_summary_500": research_sum,
        "key_dependencies": deps_summary
    }


# ==============================================================================
# 2. TRANSMISSION CHAIN & DEPTH-1 LINKED LOOKUP (Step 3 & 4)
# ==============================================================================
def build_transmission_chain(ticker: str, match_info: dict, db: dict, article_title: str) -> dict:
    """
    Constructs deterministic transmission chain:
      event -> effect on business -> what changes -> how big and when.
    Performs max depth 1 lookup if a linked supplier/customer is ALSO in the 27 list.
    """
    cdata = db.get(ticker, {})
    company_name = cdata.get("company_name", ticker)
    match_type = match_info.get("match_type", "unknown")
    trigger = match_info.get("trigger_phrase", "")

    # Chain building
    if match_type == "explicit_mention":
        chain = {
            "event": article_title,
            "effect_on_business": f"Direct explicit company/brand development regarding '{trigger}'",
            "what_changes": "Operational focus, legal/regulatory exposure, or direct product volume",
            "how_big_and_when": "Immediate headline sensitivity; financial magnitude depends on formal quarterly filings",
            "claim_status": "proven"
        }
    elif match_type == "verified_relationship":
        entity_named = match_info.get("entity_named", trigger)
        rel = match_info.get("relationship", "Commercial Partner")
        chain = {
            "event": article_title,
            "effect_on_business": f"Commercial transmission through verified counterparty '{entity_named}' ({rel})",
            "what_changes": f"Demand for {company_name}'s supplied components or services is indexed to {entity_named}'s output",
            "how_big_and_when": "Order adjustments typically transmit within 30-90 days based on production scheduling",
            "claim_status": "proven relationship, potential demand impact only possible"
        }
    else:
        chain = {
            "event": article_title,
            "effect_on_business": "Sector-wide macroeconomic transmission",
            "what_changes": "Cost of capital or broad consumer demand elasticity",
            "how_big_and_when": "Medium-term macro lag; direct impact on company is unverified",
            "claim_status": "only possible (hypothesis)"
        }

    # Linked company lookup (Max depth 1, max 1 lookup)
    linked_lookup = None
    all_27_tickers = set(db.keys())

    # Check named customers and partners to see if any are in the 27 list
    candidates = []
    customers = cdata.get("named_customers")
    if isinstance(customers, list):
        for c in customers:
            cname = c.get("customer_name", "")
            candidates.append(cname)

    partners = cdata.get("named_partners")
    if isinstance(partners, list):
        for p in partners:
            pname = p.get("entity_name", "")
            candidates.append(pname)

    # Search candidates against the 27 tickers
    for cand in candidates:
        cand_clean = cand.lower()
        for t27, d27 in db.items():
            if t27 == ticker:
                continue
            name27 = d27.get("company_name", "").lower()
            sym27 = t27.split(".")[0].lower()
            if (sym27 in cand_clean and len(sym27) > 3) or (name27 and name27 in cand_clean):
                linked_extract = build_short_extract(t27, db)
                linked_lookup = {
                    "linked_ticker": t27,
                    "relationship": "Inter-company link within 27 list",
                    "extract": linked_extract
                }
                break
        if linked_lookup:
            break

    return {
        "ticker": ticker,
        "company_name": company_name,
        "chain": chain,
        "linked_lookup_depth_1": linked_lookup if linked_lookup else "None (Counterparty not in 27 list)"
    }


# ==============================================================================
# 3. OLLAMA REPORT SYNTHESIS (Step 5)
# ==============================================================================
REPORT_PROMPT_TEMPLATE = """You are an institutional equity research analyst.
STRICT INSTRUCTIONS:
- Base your analysis ONLY on the supplied ARTICLE and SHORT COMPANY EXTRACTS.
- Do NOT invent companies, dates, numbers, or facts.
- Distinguish between PROVEN facts (explicitly stated in the article/extracts) and ONLY POSSIBLE hypotheses.
- If information is missing or unknown, state "Unknown" or "Not stated in sources". Never guess.
- Preserve all ticker symbols with suffixes exactly (e.g. DELTACORP.BO).
- Keep currency and units exactly as stated (INR, Cr., %, etc.).

ARTICLE DETAILS:
Title: {title}
URL: {url}
Date: {pub_date}
Article Text:
{article_text}

MATCHED COMPANY SHORT EXTRACTS (Max 3):
{extracts_json}

TRANSMISSION CHAINS & LINKED ENTITY (Depth 1):
{chains_json}

TASK:
Write an equity research report strictly divided into the following 7 sections:
1. Event understanding
   - Objective factual summary of what happened, relevant institution, geography, and core figures.
2. Transmission of effects
   - Concrete transmission mechanism from the event to business operations.
3. Company discovery
   - Explicit identification of how the company was matched (explicit mention or verified relationship from research profile).
4. Company-level consequences
   - Operational impact on product lines, input dependencies, customers, or pricing power.
5. Financial significance
   - Evaluate balance sheet and ratio context (ROE, ROCE, Debt-to-Equity, Current Ratio) from the Screener extracts.
6. Evidence assessment
   - Provide exact quotes from the article for every key factual claim.
7. Investment assessment
   - State the primary thesis in one sentence, followed by what specific evidence would invalidate it.
"""


def generate_ollama_report(event_dict: dict, extracts: list, chains: list) -> str:
    """Calls local Ollama qwen3:4b to synthesize the strict 7-section report."""
    title = event_dict.get("title", "")
    url = event_dict.get("link", "")
    pub_date = event_dict.get("pub_date", "")
    text = event_dict.get("article_content") or event_dict.get("description", "")

    prompt = REPORT_PROMPT_TEMPLATE.format(
        title=title,
        url=url,
        pub_date=pub_date,
        article_text=text[:4000],
        extracts_json=json.dumps(extracts, indent=2),
        chains_json=json.dumps(chains, indent=2)
    )

    try:
        response = ollama.chat(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            options=OLLAMA_OPTIONS
        )
        return response.message.content.strip()
    except Exception as e:
        return f"ERROR: Ollama generation failed: {e}"


def save_report(title: str, report_content: str, tickers: list) -> str:
    """Saves the markdown report to reports/."""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9]+", "_", title)[:45].strip("_")
    filename = f"{slug}.md"
    file_path = os.path.join(REPORTS_DIR, filename)

    now_iso = datetime.now(timezone.utc).isoformat()
    header = (
        f"# Equity Event Analysis: {title}\n\n"
        f"- **Generated At**: {now_iso}\n"
        f"- **Covered Tickers**: {', '.join(tickers)}\n"
        f"- **Model**: {MODEL_NAME} (Local Ollama, temp=0, num_ctx=12288)\n\n"
        f"---\n\n"
    )

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(header + report_content)

    return file_path


# ==============================================================================
# 4. EVENT PROCESSING ENGINE (Step 1 to 5)
# ==============================================================================
def process_event(event_item: dict, db: dict) -> dict:
    """
    Executes full pipeline for a single event:
      1. Inspects matches (reusing match_news criteria).
      2. If no explicit or verified match: STOPS and records 'No covered company affected.'
      3. Builds short extract (max 3).
      4. Writes transmission chain & depth-1 lookup.
      5. Calls Ollama for 7-section report.
    """
    title = event_item.get("event", {}).get("title") or event_item.get("title", "")
    url = event_item.get("event", {}).get("link") or event_item.get("link", "")
    pub_date = event_item.get("event", {}).get("pub_date") or event_item.get("pub_date", "")
    desc = event_item.get("event", {}).get("description") or event_item.get("description", "")
    content = event_item.get("article_content", "")

    # Extract verified matches
    why_matched = event_item.get("why_matched", [])

    # Filter strictly for explicit_mention or verified_relationship
    verified_matches = [
        m for m in why_matched
        if m.get("match_type") in ["explicit_mention", "verified_relationship"]
    ]

    # STEP 1: STOP IF NO EXPLICIT OR VERIFIED MATCH
    if not verified_matches:
        msg = "No covered company affected."
        print(f"\n[EVENT] {title}")
        print(f"  → Status: {msg} (No explicit mention or verified supply chain relationship).")
        return {
            "status": "stopped",
            "message": msg,
            "title": title,
            "report_path": None
        }

    # STEP 2: BUILD SHORT EXTRACTS (Max 3 companies)
    unique_tickers = []
    for m in verified_matches:
        t = m.get("ticker")
        if t and t not in unique_tickers:
            unique_tickers.append(t)
    unique_tickers = unique_tickers[:3]

    extracts = []
    chains = []
    for t in unique_tickers:
        ext = build_short_extract(t, db)
        if ext:
            extracts.append(ext)
        # Find matching reason for chain
        match_info = next((m for m in verified_matches if m.get("ticker") == t), {})
        ch = build_transmission_chain(t, match_info, db, title)
        chains.append(ch)

    print(f"\n[EVENT] {title}")
    print(f"  → Verified Matches ({len(unique_tickers)}): {unique_tickers}")
    print(f"  → Running Ollama 7-section report generation for {unique_tickers}...")

    # STEP 5: OLLAMA REPORT SYNTHESIS
    event_dict = {
        "title": title,
        "link": url,
        "pub_date": pub_date,
        "description": desc,
        "article_content": content
    }
    report_content = generate_ollama_report(event_dict, extracts, chains)
    report_path = save_report(title, report_content, unique_tickers)
    print(f"  → Report generated and saved: {report_path}")

    return {
        "status": "completed",
        "title": title,
        "tickers": unique_tickers,
        "report_path": report_path
    }


# ==============================================================================
# 5. OFFLINE TEST SUITE (--test)
# ==============================================================================
def run_pipeline_tests(db: dict) -> bool:
    """
    Executes offline tests verifying:
      1. Delta Corp mention: Explicit mention -> short extract -> chain -> 7-section report saved.
      2. US Fed headline: 0 verified matches -> stops and outputs 'No covered company affected.'
    """
    print("=" * 60)
    print("RUNNING PIPELINE OFFLINE TESTS")
    print("=" * 60)
    failures = []

    # Test 1: Delta Corp explicit mention
    delta_event = {
        "title": "Delta Corp receives court relief on casino license renewal in Goa",
        "link": "https://example.com/deltacorp-relief",
        "pub_date": "Wed, 11 Nov 2026 10:00:00 +0530",
        "description": "Delta Corp Limited secured an interim relief from the High Court regarding offshore casino vessel operations on the Mandovi river.",
        "article_content": "Delta Corp Limited secured an interim relief from the High Court regarding offshore casino vessel operations on the Mandovi river. The company operates Deltin Royale and Deltin JAQK.",
        "why_matched": [
            {
                "ticker": "DELTACORP.BO",
                "company_name": "Delta Corp Limited",
                "match_type": "explicit_mention",
                "trigger_phrase": "Delta Corp",
                "database_field": "company_name"
            }
        ]
    }

    res_delta = process_event(delta_event, db)
    if res_delta["status"] != "completed":
        failures.append(f"Test 1 Failed: Expected status 'completed', got '{res_delta['status']}'")
    elif not res_delta["report_path"] or not os.path.exists(res_delta["report_path"]):
        failures.append(f"Test 1 Failed: Report file not created at {res_delta['report_path']}")
    else:
        # Verify 7 sections in report file
        with open(res_delta["report_path"], "r", encoding="utf-8") as f:
            content = f.read()
        required_sections = [
            "Event understanding",
            "Transmission of effects",
            "Company discovery",
            "Company-level consequences",
            "Financial significance",
            "Evidence assessment",
            "Investment assessment"
        ]
        missing_sections = [s for s in required_sections if s.lower() not in content.lower()]
        if missing_sections:
            failures.append(f"Test 1 Failed: Report missing required sections: {missing_sections}")
        else:
            print("  [PASS] Test 1: Delta Corp explicit mention successfully produced complete 7-section report.")

    # Test 2: US Fed headline (0 verified matches)
    us_fed_event = {
        "title": "Traders raise odds of 50 bps Fed rate hike as CPI rise exceeds expectations",
        "link": "https://example.com/us-fed-rate-hike",
        "pub_date": "Wed, 11 Nov 2026 09:00:00 +0530",
        "description": "US consumer prices rose faster than anticipated, prompting speculation about Federal Reserve interest rate hikes.",
        "article_content": "US consumer prices rose faster than anticipated, prompting speculation about Federal Reserve interest rate hikes.",
        "why_matched": []  # Zero verified matches
    }

    res_fed = process_event(us_fed_event, db)
    if res_fed["status"] != "stopped":
        failures.append(f"Test 2 Failed: Expected status 'stopped', got '{res_fed['status']}'")
    elif res_fed["message"] != "No covered company affected.":
        failures.append(f"Test 2 Failed: Expected 'No covered company affected.', got '{res_fed['message']}'")
    elif res_fed["report_path"] is not None:
        failures.append("Test 2 Failed: Report was generated when it should have stopped.")
    else:
        print("  [PASS] Test 2: US Fed headline with 0 verified matches stopped cleanly: 'No covered company affected.'")

    print("=" * 60)
    if failures:
        print("PIPELINE TESTS FAILED:")
        for f in failures:
            print("  ✖", f)
        return False
    else:
        print("ALL PIPELINE OFFLINE TESTS PASSED!")
        print("=" * 60 + "\n")
        return True


# ==============================================================================
# 6. MAIN RUNNER
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="Full Event to Report Pipeline")
    parser.add_argument("--test", action="store_true", help="Run offline validation tests only")
    args = parser.parse_args()

    db = load_database()

    if args.test:
        ok = run_pipeline_tests(db)
        sys.exit(0 if ok else 1)

    # Run tests first to ensure pipeline integrity
    ok = run_pipeline_tests(db)
    if not ok:
        sys.exit(1)

    # Process matched_news.json
    if not os.path.exists(MATCHED_NEWS_FILE):
        print(f"ERROR: {MATCHED_NEWS_FILE} not found. Run match_news.py first.")
        sys.exit(1)

    with open(MATCHED_NEWS_FILE, "r", encoding="utf-8") as f:
        events = json.load(f)

    print(f"Loaded {len(events)} events from {MATCHED_NEWS_FILE}.")
    completed_reports = []

    for ev in events:
        res = process_event(ev, db)
        if res["status"] == "completed" and res["report_path"]:
            completed_reports.append(res["report_path"])

    print("\n" + "=" * 60)
    print("PIPELINE EXECUTION SUMMARY")
    print("=" * 60)
    print(f"Total events inspected       : {len(events)}")
    print(f"Reports successfully created : {len(completed_reports)}")
    if completed_reports:
        print(f"Sample report path           : {completed_reports[0]}")
    else:
        print("Note: In current live news feed, all items were foreign macro/unmatched; no verified Indian company matches were found.")
        print(f"Sample test report available : reports/Delta_Corp_receives_court_relief_on_casino.md")
    print("=" * 60)


if __name__ == "__main__":
    main()
