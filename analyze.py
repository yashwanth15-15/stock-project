"""
Analyze a financial news article against Screener.in company data using Ollama.
"""

import os
import sys

# Add screener-scraper to path
sys.path.append(os.path.abspath("screener-scraper"))

import httpx
import ollama
from screenerScraper import ScreenerScrape
import trafilatura

# ==============================================================================
# INPUTS (Change your target article URL, stock symbol, or model here)
# ==============================================================================
URL = "https://economictimes.indiatimes.com/markets/forex/indian-rupee-breaks-six-day-winning-streak-on-state-banks-dollar-bids/articleshow/135678769.cms"
SCREENER_SYMBOL = "RBLBANK"  # Screener.in ticker symbol (e.g. "RBLBANK", "UNOMINDA")
MODEL_NAME = "qwen3:4b"  # Local Ollama model

# ==============================================================================
# 1. DOWNLOAD & EXTRACT ARTICLE TEXT
# ==============================================================================
print(f"[1/3] Downloading article:\n      {URL}")
headers = {"User-Agent": "Mozilla/5.0"}
html = httpx.get(URL, headers=headers, follow_redirects=True, timeout=20).text
text = trafilatura.extract(html)
if not text:
    raise SystemExit("Could not extract text. The article may require dynamic rendering.")
text = text[:10000]
print(f"      Extracted {len(text)} characters.")

# ==============================================================================
# 2. FETCH SCREENER.IN FINANCIAL STATEMENTS
# ==============================================================================
print(f"\n[2/3] Fetching Screener data for {SCREENER_SYMBOL}...")
scraper = ScreenerScrape()
scraper.loadScraper(SCREENER_SYMBOL, consolidated=True)

screener_data = {
    "P&L": scraper.pnlReport(withAddon=True),
    "Balance Sheet": scraper.balanceSheet(withAddon=True),
    "Ratios": scraper.ratios(),
}
print("      Screener data retrieved successfully.")

# ==============================================================================
# 3. LLM IMPACT ANALYSIS (2-STEP OLLAMA WORKFLOW)
# ==============================================================================
options = {"temperature": 0, "num_ctx": 12288, "seed": 42}

# --- STEP 1: Extract facts ---
prompt_a = f"""STRICT RULES: Use ONLY the article below. Do NOT invent anything.
Extract a list of every company, every number, and every date.
For each item, write exactly what it refers to.
Always write the amount together with its label.
Format as a simple list, one fact per line.

ARTICLE:
{text}
"""

print("\n[3/3] Running Step 1: Extracting facts...")
reply_a = ollama.chat(
    model=MODEL_NAME,
    messages=[{"role": "user", "content": prompt_a}],
    options=options,
    think=True,
)

print("\n" + "=" * 60)
print("--- STEP 1 OUTPUT: EXTRACTED LIST ---")
print("=" * 60)
print(reply_a.message.content)

# --- STEP 2: Final Report ---
prompt_b = f"""STRICT RULES: Do NOT invent companies, dates, or numbers.
Do NOT do your own math. If something is missing, write "Not stated."
Use the EXTRACTED LIST to keep facts separate. Use the ARTICLE for quotes.
Use the SCREENER DATA to check the article's claims against the company's real history.
If the article describes more than one deal, treat them separately.
In section 6, give one exact quote from the ARTICLE for each main claim.
In section 7, first state the thesis in one sentence, then what would prove it wrong.

EXTRACTED LIST:
{reply_a.message.content}

ARTICLE:
{text}

SCREENER DATA (Real financial history of the company):
{screener_data}

TASK: Write ONLY these 7 sections:
1. Event understanding
2. Transmission of effects
3. Company discovery
4. Company-level consequences
5. Financial significance (use Screener data here to check historical context)
6. Evidence assessment (quote the article)
7. Investment assessment (thesis, then what would invalidate it)
"""

print("\nRunning Step 2: Generating final report...")
reply_b = ollama.chat(
    model=MODEL_NAME,
    messages=[{"role": "user", "content": prompt_b}],
    options=options,
    think=True,
)

print("\n" + "=" * 60)
print("--- STEP 2 OUTPUT: FINAL REPORT ---")
print("=" * 60)
print(reply_b.message.content)
