import sys
import os

# Add the scraper folder to Python's path so it can find the module
sys.path.append(os.path.abspath("screener-scraper"))

import httpx
import trafilatura
import ollama
from screenerScraper import ScreenerScrape

# Now change the current directory to screener-scraper so it finds the tokens folder safely
os.chdir("screener-scraper")

URL = "https://economictimes.indiatimes.com/markets/forex/indian-rupee-breaks-six-day-winning-streak-on-state-banks-dollar-bids/articleshow/135678769.cms"
SCREENER_SYMBOL = "RBLBANK"  # Change this to the stock you want to analyze

# 1. Download the article
headers = {"User-Agent": "Mozilla/5.0"}
html = httpx.get(URL, headers=headers, follow_redirects=True, timeout=20).text
text = trafilatura.extract(html)
if not text:
    raise SystemExit("Could not extract text. This site may need Playwright later.")
text = text[:10000]
print("Extracted characters:", len(text))

# 2. Get Screener data
print(f"Fetching Screener data for {SCREENER_SYMBOL}...")
scraper = ScreenerScrape()
token = scraper.getBSEToken(SCREENER_SYMBOL)
scraper.loadScraper(token, consolidated=True)
screener_data = {
    "P&L": scraper.pnlReport(withAddon=True),
    "Balance Sheet": scraper.balanceSheet(withAddon=True),
    "Ratios": scraper.ratios()
}

# 3. Options for the model
options = {"temperature": 0, "num_ctx": 12288, "seed": 42}

# --- STEP 1: Extract facts first ---
prompt_a = f"""STRICT RULES: Use ONLY the article below. Do NOT invent anything.
Extract a list of every company, every number, and every date.
For each item, write exactly what it refers to.
Always write the amount together with its label.
Format as a simple list, one fact per line.

ARTICLE:
{text}
"""

print("Running Step 1 (Extracting facts)...")
reply_a = ollama.chat(
    model="qwen3:4b",
    messages=[{"role": "user", "content": prompt_a}],
    options=options,
    think=True,
)

print("\n--- STEP 1 OUTPUT: EXTRACTED LIST ---")
print(reply_a.message.content)
print("\n--- STEP 2 OUTPUT: FINAL REPORT ---")

# --- STEP 2: Write the report using the list, article, and Screener data ---
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

reply_b = ollama.chat(
    model="qwen3:4b",
    messages=[{"role": "user", "content": prompt_b}],
    options=options,
    think=True,
)

print(reply_b.message.content)