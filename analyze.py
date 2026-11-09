import httpx
import trafilatura
import ollama

URL = "https://companyanatomy.com/stock-research-reports/rbl-bank-emirates-nbd-branch-merger-return-test/"

# 1. Download the page
headers = {"User-Agent": "Mozilla/5.0"}
html = httpx.get(URL, headers=headers, follow_redirects=True, timeout=20).text

# 2. Remove ads/menus, keep only the article text
text = trafilatura.extract(html)
if not text:
    raise SystemExit("Could not extract text. This site may need Playwright later.")
text = text[:10000]
print("Extracted characters:", len(text))

# 3. Options for the model (makes runs repeatable)
options = {"temperature": 0, "num_ctx": 12288, "seed": 42}

# --- STEP 1: Extract facts first ---
prompt_a = f"""STRICT RULES: Use ONLY the article below. Do NOT invent anything.
Extract a list of every company, every number, and every date.
For each item, write exactly what it refers to (which deal, which year, which company).
Always write the amount together with its label, for example "₹81.85 crore - India branches FY26 profit".
Never list a year or date without the number it belongs to.
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

# --- STEP 2: Write the report using the list AND the article ---
prompt_b = f"""STRICT RULES: Do NOT invent companies, dates, or numbers.
Do NOT do your own math. If something is missing, write "Not stated in the provided text."
Use the EXTRACTED LIST to keep facts separate. Use the ARTICLE for quotes and meaning.
If the article describes more than one deal, treat them separately. Never move a number from one deal to another.
In section 6, give one exact quote from the ARTICLE for each main claim in sections 1, 4 and 5.
In section 7, first state the thesis in one sentence, then what would prove it wrong.

EXTRACTED LIST:
{reply_a.message.content}

ARTICLE:
{text}

TASK: Write ONLY these 7 sections:
1. Event understanding
2. Transmission of effects
3. Company discovery
4. Company-level consequences
5. Financial significance
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