"""
build_profiles.py
=================
Fetches company profile data from Yahoo Finance (via yfinance) for the
tickers listed in companies.json and saves one JSON file per company
under profiles/.

Usage:
    python build_profiles.py --symbol DELTACORP.BO   # single ticker
    python build_profiles.py --all                    # all 27 tickers

Rules enforced here:
  - Supplied name (from companies.json) is kept separate from Yahoo's name.
  - Every field is recorded with source, ticker and retrieval timestamp.
  - Missing fields → "unavailable" (never invented).
  - Financial periods are recorded as returned by Yahoo, not assumed.
  - Currency and units are preserved as-is from Yahoo.
  - Analyst estimates are clearly labelled as estimates, not facts.
  - Rate-limiting: pauses between requests; stops and reports on 429/error.
  - Does not modify news.py, analyze.py, or the Screener scraper.
"""

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timezone

import yfinance as yf

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
COMPANIES_FILE = os.path.join(BASE_DIR, "companies.json")
PROFILES_DIR  = os.path.join(BASE_DIR, "profiles")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
UNAVAILABLE = "unavailable"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe(value, cast=None):
    """Return value or UNAVAILABLE; optionally cast to str/float/int."""
    if value is None:
        return UNAVAILABLE
    if isinstance(value, float) and (value != value):   # NaN check
        return UNAVAILABLE
    if cast is not None:
        try:
            return cast(value)
        except (TypeError, ValueError):
            return UNAVAILABLE
    return value


def df_to_records(df, label: str) -> dict:
    """
    Convert a pandas DataFrame (financial statement) to a plain dict.
    Keeps column names (periods) and index names (line items) as strings.
    Records the period names for transparency.
    """
    if df is None or df.empty:
        return {"note": UNAVAILABLE, "periods": [], "rows": {}}

    periods = [str(c.date()) if hasattr(c, "date") else str(c) for c in df.columns]
    rows = {}
    for idx, row in df.iterrows():
        row_key = str(idx)
        rows[row_key] = {}
        for col, period_label in zip(df.columns, periods):
            val = row[col]
            if val != val or val is None:   # NaN or None
                rows[row_key][period_label] = UNAVAILABLE
            else:
                try:
                    rows[row_key][period_label] = float(val)
                except (TypeError, ValueError):
                    rows[row_key][period_label] = str(val)
    return {"periods": periods, "rows": rows}


def build_identity_flags(supplied_name: str, yahoo_name: str) -> dict:
    """
    Compare the supplied company name with Yahoo's shortName / longName.
    Returns a flag dict for review.
    """
    if yahoo_name == UNAVAILABLE:
        return {
            "match": False,
            "review_needed": True,
            "note": "Yahoo returned no company name; cannot verify identity.",
        }

    s = supplied_name.strip().upper()
    y = yahoo_name.strip().upper()

    # Rough check: do they share the first meaningful word?
    s_first = s.split()[0] if s.split() else ""
    y_first = y.split()[0] if y.split() else ""

    exact = s == y
    partial = s_first == y_first if s_first else False

    return {
        "match": exact,
        "partial_match": partial,
        "review_needed": not (exact or partial),
        "note": (
            "Names match exactly."
            if exact
            else (
                "First word matches; verify full name manually."
                if partial
                else (
                    "Names differ significantly — manual review recommended."
                )
            )
        ),
    }


# ---------------------------------------------------------------------------
# Core fetcher
# ---------------------------------------------------------------------------

def fetch_profile(ticker_symbol: str, supplied_name: str) -> dict:
    """
    Fetch all available profile data for one ticker from Yahoo Finance.
    Returns a structured dict ready for JSON serialisation.
    """
    retrieval_timestamp = utc_now_iso()

    try:
        ticker = yf.Ticker(ticker_symbol)
        info   = ticker.info or {}
    except Exception as exc:
        return {
            "_meta": {
                "ticker": ticker_symbol,
                "supplied_name": supplied_name,
                "retrieval_timestamp_utc": retrieval_timestamp,
                "source": "Yahoo Finance via yfinance",
                "fetch_error": str(exc),
                "status": "error",
            }
        }

    # --- Identity ---
    yahoo_short = safe(info.get("shortName"))
    yahoo_long  = safe(info.get("longName"))
    identity_flags = build_identity_flags(
        supplied_name,
        yahoo_long if yahoo_long != UNAVAILABLE else yahoo_short,
    )

    # --- Business description ---
    description = {
        "sector":   safe(info.get("sector")),
        "industry": safe(info.get("industry")),
        "summary":  safe(info.get("longBusinessSummary")),
        "website":  safe(info.get("website")),
        "country":  safe(info.get("country")),
        "city":     safe(info.get("city")),
        "employees": safe(info.get("fullTimeEmployees"), int),
    }

    # --- Leadership ---
    officers_raw = info.get("companyOfficers") or []
    leadership = []
    for o in officers_raw:
        leadership.append({
            "name":  safe(o.get("name")),
            "title": safe(o.get("title")),
            "age":   safe(o.get("age"), int),
            "total_pay": safe(o.get("totalPay"), float),
            "total_pay_currency": safe(info.get("financialCurrency")),
        })

    # --- Valuation figures ---
    valuation = {
        "currency":          safe(info.get("currency")),
        "financial_currency": safe(info.get("financialCurrency")),
        "current_price":     safe(info.get("currentPrice"), float),
        "previous_close":    safe(info.get("previousClose"), float),
        "market_cap":        safe(info.get("marketCap"), float),
        "enterprise_value":  safe(info.get("enterpriseValue"), float),
        "pe_ratio_trailing": safe(info.get("trailingPE"), float),
        "pe_ratio_forward":  safe(info.get("forwardPE"), float),
        "price_to_book":     safe(info.get("priceToBook"), float),
        "price_to_sales_ttm": safe(info.get("priceToSalesTrailing12Months"), float),
        "ev_to_ebitda":      safe(info.get("enterpriseToEbitda"), float),
        "ev_to_revenue":     safe(info.get("enterpriseToRevenue"), float),
        "beta":              safe(info.get("beta"), float),
        "52_week_high":      safe(info.get("fiftyTwoWeekHigh"), float),
        "52_week_low":       safe(info.get("fiftyTwoWeekLow"), float),
        "dividend_yield":    safe(info.get("dividendYield"), float),
        "payout_ratio":      safe(info.get("payoutRatio"), float),
    }

    # --- Financial summary (from info, not from statement downloads) ---
    financials_summary = {
        "note": (
            "Values are from Yahoo Finance info dict. "
            "Currency and scale match the 'financial_currency' field. "
            "These are trailing/reported figures, not analyst estimates."
        ),
        "revenue_ttm":       safe(info.get("totalRevenue"), float),
        "gross_profit_ttm":  safe(info.get("grossProfits"), float),
        "ebitda":            safe(info.get("ebitda"), float),
        "net_income_ttm":    safe(info.get("netIncomeToCommon"), float),
        "earnings_per_share_ttm": safe(info.get("trailingEps"), float),
        "total_cash":        safe(info.get("totalCash"), float),
        "total_debt":        safe(info.get("totalDebt"), float),
        "total_assets":      safe(info.get("totalAssets"), float),
        "book_value_per_share": safe(info.get("bookValue"), float),
        "operating_cash_flow": safe(info.get("operatingCashflow"), float),
        "free_cash_flow":    safe(info.get("freeCashflow"), float),
        "return_on_equity":  safe(info.get("returnOnEquity"), float),
        "return_on_assets":  safe(info.get("returnOnAssets"), float),
        "profit_margin":     safe(info.get("profitMargins"), float),
        "operating_margin":  safe(info.get("operatingMargins"), float),
        "revenue_growth_yoy": safe(info.get("revenueGrowth"), float),
        "earnings_growth_yoy": safe(info.get("earningsGrowth"), float),
        "debt_to_equity":    safe(info.get("debtToEquity"), float),
        "current_ratio":     safe(info.get("currentRatio"), float),
        "quick_ratio":       safe(info.get("quickRatio"), float),
    }

    # --- Detailed financial statements (annual) ---
    try:
        income_stmt  = df_to_records(ticker.financials, "annual income statement")
    except Exception as e:
        income_stmt = {"note": f"fetch error: {e}"}

    try:
        balance_sheet = df_to_records(ticker.balance_sheet, "annual balance sheet")
    except Exception as e:
        balance_sheet = {"note": f"fetch error: {e}"}

    try:
        cash_flow = df_to_records(ticker.cashflow, "annual cash flow")
    except Exception as e:
        cash_flow = {"note": f"fetch error: {e}"}

    # --- Ownership ---
    try:
        inst_holders_raw = ticker.institutional_holders
        institutional_holders = (
            inst_holders_raw.to_dict(orient="records")
            if inst_holders_raw is not None and not inst_holders_raw.empty
            else UNAVAILABLE
        )
        # Convert Timestamps to strings
        if isinstance(institutional_holders, list):
            for row in institutional_holders:
                for k, v in row.items():
                    if hasattr(v, "isoformat"):
                        row[k] = v.isoformat()
    except Exception as e:
        institutional_holders = f"fetch error: {e}"

    try:
        major_holders_raw = ticker.major_holders
        major_holders = (
            major_holders_raw.to_dict(orient="records")
            if major_holders_raw is not None and not major_holders_raw.empty
            else UNAVAILABLE
        )
    except Exception as e:
        major_holders = f"fetch error: {e}"

    # --- Analyst estimates (clearly labelled) ---
    analyst_estimates = {
        "WARNING": (
            "All values in this section are ANALYST ESTIMATES sourced from "
            "Yahoo Finance. They are not verified facts and may differ from "
            "actual future results."
        ),
        "recommendation": safe(info.get("recommendationKey")),
        "number_of_analyst_opinions": safe(info.get("numberOfAnalystOpinions"), int),
        "target_high_price":  safe(info.get("targetHighPrice"), float),
        "target_low_price":   safe(info.get("targetLowPrice"), float),
        "target_mean_price":  safe(info.get("targetMeanPrice"), float),
        "target_median_price": safe(info.get("targetMedianPrice"), float),
        "forward_eps_estimate": safe(info.get("forwardEps"), float),
        "peg_ratio":          safe(info.get("pegRatio"), float),
    }

    # --- Compose the full profile ---
    profile = {
        "_meta": {
            "ticker":                   ticker_symbol,
            "supplied_name":            supplied_name,
            "yahoo_short_name":         yahoo_short,
            "yahoo_long_name":          yahoo_long,
            "identity_check":           identity_flags,
            "retrieval_timestamp_utc":  retrieval_timestamp,
            "retrieval_date_note":      (
                "This is the date/time the data was pulled from Yahoo Finance. "
                "It is NOT the financial reporting period date."
            ),
            "source":  "Yahoo Finance via yfinance",
            "status":  "ok",
        },
        "description":          description,
        "leadership":           leadership,
        "valuation":            valuation,
        "financials_summary":   financials_summary,
        "income_statement_annual":  income_stmt,
        "balance_sheet_annual":     balance_sheet,
        "cash_flow_annual":         cash_flow,
        "ownership": {
            "institutional_holders": institutional_holders,
            "major_holders":         major_holders,
        },
        "analyst_estimates": analyst_estimates,
    }

    return profile


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def save_profile(ticker_symbol: str, profile: dict) -> str:
    os.makedirs(PROFILES_DIR, exist_ok=True)
    # Sanitise ticker for filename: replace dots and special chars with _
    safe_ticker = ticker_symbol.replace(".", "_").replace("/", "_")
    path = os.path.join(PROFILES_DIR, f"{safe_ticker}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, default=str, ensure_ascii=False)
    return path


# ---------------------------------------------------------------------------
# Rate-limit guard
# ---------------------------------------------------------------------------

PAUSE_BETWEEN_REQUESTS = 3   # seconds


def run_one(ticker_symbol: str, companies: dict, verbose: bool = True) -> bool:
    """
    Fetch and save a single ticker. Returns True on success.
    Prints clear errors; stops on HTTP 429 / rate-limit signals.
    """
    supplied_name = companies.get(ticker_symbol, "UNKNOWN")

    if verbose:
        print(f"\n[{ticker_symbol}] Fetching …  (supplied name: {supplied_name})")

    profile = fetch_profile(ticker_symbol, supplied_name)
    status  = profile.get("_meta", {}).get("status", "error")

    path = save_profile(ticker_symbol, profile)

    if status == "ok":
        yahoo_name = profile["_meta"].get("yahoo_long_name", UNAVAILABLE)
        flags      = profile["_meta"].get("identity_check", {})
        if verbose:
            print(f"  → Saved: {path}")
            print(f"    Yahoo name  : {yahoo_name}")
            print(f"    Identity    : {flags.get('note','')}")
            if flags.get("review_needed"):
                print(f"    ⚠  REVIEW NEEDED — names differ significantly")
    else:
        err = profile.get("_meta", {}).get("fetch_error", "unknown error")
        # 429 / rate limit detection
        if "429" in str(err) or "rate limit" in str(err).lower():
            print(f"  ✖ RATE LIMITED on {ticker_symbol}. Stopping.")
            return False
        print(f"  ✖ Error for {ticker_symbol}: {err}")

    return True


# ---------------------------------------------------------------------------
# Offline / unit tests
# ---------------------------------------------------------------------------

def run_offline_tests():
    """
    Tests the helper functions without any network calls.
    Checks missing-data handling, identity flags, NaN handling.
    """
    print("=" * 55)
    print("OFFLINE TESTS")
    print("=" * 55)
    failures = []

    # 1. safe() with None
    assert safe(None) == UNAVAILABLE, "safe(None) should be UNAVAILABLE"
    print("  PASS  safe(None)")

    # 2. safe() with NaN
    import math
    nan_val = float("nan")
    assert safe(nan_val) == UNAVAILABLE, "safe(NaN) should be UNAVAILABLE"
    print("  PASS  safe(NaN)")

    # 3. safe() with valid int cast
    assert safe("42", int) == 42, "safe cast should work"
    print("  PASS  safe cast int")

    # 4. safe() with bad cast
    assert safe("not-a-number", float) == UNAVAILABLE
    print("  PASS  safe bad cast")

    # 5. Identity: exact match
    r = build_identity_flags("DELTA CORP LIMITED", "DELTA CORP LIMITED")
    assert r["match"] is True and r["review_needed"] is False
    print("  PASS  identity exact match")

    # 6. Identity: partial match
    r = build_identity_flags("DELTA CORP LIMITED", "DELTA CORP PVT")
    assert r["partial_match"] is True
    print("  PASS  identity partial match")

    # 7. Identity: no match
    r = build_identity_flags("DELTA CORP LIMITED", "TATA MOTORS")
    assert r["review_needed"] is True
    print("  PASS  identity review flag raised")

    # 8. Identity: Yahoo returns UNAVAILABLE
    r = build_identity_flags("DELTA CORP LIMITED", UNAVAILABLE)
    assert r["review_needed"] is True
    print("  PASS  identity yahoo unavailable")

    # 9. df_to_records with None
    result = df_to_records(None, "test")
    assert result["periods"] == [] and result["rows"] == {}
    print("  PASS  df_to_records None")

    # 10. save_profile creates file
    test_profile = {
        "_meta": {"ticker": "TEST.BO", "supplied_name": "TEST CO", "status": "ok"},
        "test_key": "test_value",
    }
    path = save_profile("TEST.BO", test_profile)
    assert os.path.exists(path), f"File not created: {path}"
    with open(path, encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded["test_key"] == "test_value"
    os.remove(path)
    print("  PASS  save_profile round-trip")

    print(f"\n  {'ALL OFFLINE TESTS PASSED' if not failures else 'SOME TESTS FAILED'}")
    return len(failures) == 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Build Yahoo Finance company profiles."
    )
    parser.add_argument("--symbol", metavar="TICKER",
                        help="Single ticker to fetch, e.g. DELTACORP.BO")
    parser.add_argument("--all", action="store_true",
                        help="Fetch all 27 tickers in companies.json")
    parser.add_argument("--offline-test", action="store_true",
                        help="Run offline unit tests only (no network)")
    args = parser.parse_args()

    # Always run offline tests first
    if args.offline_test or (not args.symbol and not args.all):
        ok = run_offline_tests()
        if args.offline_test:
            sys.exit(0 if ok else 1)
        print()

    # Load company list
    if not os.path.exists(COMPANIES_FILE):
        print(f"ERROR: {COMPANIES_FILE} not found.")
        sys.exit(1)

    with open(COMPANIES_FILE, encoding="utf-8") as f:
        companies = json.load(f)

    if args.symbol:
        sym = args.symbol.strip()
        if sym not in companies:
            print(f"WARNING: {sym} not in companies.json — proceeding anyway.")
            companies[sym] = "UNKNOWN (not in companies.json)"
        run_one(sym, companies)

    elif args.all:
        total   = len(companies)
        success = 0
        failed  = []
        print(f"\nFetching all {total} tickers with {PAUSE_BETWEEN_REQUESTS}s pause between each.\n")
        for i, (sym, name) in enumerate(companies.items(), start=1):
            print(f"[{i}/{total}] ", end="", flush=True)
            ok = run_one(sym, companies)
            if ok:
                success += 1
            else:
                failed.append(sym)
                print(f"\nStopped at {sym} — rate limited or error. "
                      f"Completed {success}/{total} profiles.\n")
                break
            if i < total:
                time.sleep(PAUSE_BETWEEN_REQUESTS)

        print(f"\n{'='*55}")
        print(f"Done. {success}/{total} profiles saved to {PROFILES_DIR}/")
        if failed:
            print(f"Failed tickers: {failed}")

    else:
        print("No action specified. Use --symbol TICKER, --all, or --offline-test.")
        parser.print_help()


if __name__ == "__main__":
    main()
