"""Financial metadata enrichment and constraint-aware endpoint matching.

This module intentionally lives in Attas: time semantics, asset classes,
regions, and financial data types are application concepts rather than generic
multi-agent infrastructure.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, Mapping, Sequence


FINANCIAL_DIMENSIONS_VERSION = "attas-financial-dimensions-v11"
ASSET_CLASS_TAG_PREFIX = "asset_class:"
ASSET_CLASS_MATCH_WEIGHT = 10.0

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_SPACE_RE = re.compile(r"\s+")
_DESCRIPTION_BREAKS = (
    " API Parameters ",
    " :Parameters: ",
    " Parameters ",
    " Examples (",
    " Example (",
    " Language-specific guides ",
    " Sample ticker ",
    " Downloadable CSV file:",
)

_STOP_WORDS = {
    "a",
    "about",
    "and",
    "available",
    "best",
    "can",
    "compare",
    "data",
    "do",
    "for",
    "from",
    "get",
    "i",
    "in",
    "is",
    "it",
    "me",
    "my",
    "need",
    "of",
    "or",
    "please",
    "source",
    "sources",
    "that",
    "the",
    "to",
    "use",
    "what",
    "which",
    "with",
}

_TOKEN_EXPANSIONS = {
    "adjusted": {"adjust", "dividend", "split"},
    "company": {"business", "profile", "overview"},
    "equities": {"equity", "stock", "ticker", "security"},
    "equity": {"stock", "ticker", "security"},
    "fundamental": {"overview", "income", "balance", "cashflow", "earnings"},
    "fundamentals": {"overview", "income", "balance", "cashflow", "earnings"},
    "facts": {"companyfacts", "companyconcept", "xbrl"},
    "filing": {"filings", "submission", "submissions", "form", "edgar"},
    "filings": {"filing", "submission", "submissions", "form", "edgar"},
    "global": {"international", "exchange", "region", "country"},
    "intraday": {"interval", "minute", "hour", "realtime"},
    "metadata": {"submission", "submissions", "form", "filing"},
    "price": {"prices", "close", "ohlc", "history", "historical"},
    "prices": {"price", "close", "ohlc", "history", "historical"},
    "stock": {"equity", "ticker", "security"},
    "stocks": {"equity", "ticker", "security"},
    "shareholder": {"ownership", "holder", "holders", "holding", "holdings", "institutional", "major"},
    "shareholders": {"ownership", "holder", "holders", "holding", "holdings", "institutional", "major"},
    "stockholder": {"ownership", "holder", "holders", "holding", "holdings", "institutional", "major"},
    "stockholders": {"ownership", "holder", "holders", "holding", "holdings", "institutional", "major"},
    "volume": {"trading", "shares"},
}

_FREQUENCY_PATTERNS = {
    "tick": (r"\btick(?:s)?\b",),
    "intraday": (r"\bintraday\b", r"\bminute(?:ly)?\b", r"\bhourly\b"),
    "daily": (r"\bdaily\b", r"\bend[ -]of[ -]day\b", r"\beod\b"),
    "weekly": (r"\bweekly\b",),
    "monthly": (r"\bmonthly\b",),
    "quarterly": (r"\bquarterly\b",),
    "annual": (r"\bannual(?:ly)?\b", r"\byearly\b"),
    "event_driven": (r"\bevent[ -]driven\b", r"\bcalendar\b", r"\bfiling(?:s)?\b"),
}

_TIMELINESS_PATTERNS = {
    "realtime": (r"\breal[ -]?time\b", r"\blive\b", r"\bstream(?:ing)?\b"),
    "delayed": (r"\bdelayed\b",),
    "historical": (r"\bhistorical\b", r"\bhistory\b",),
    "snapshot": (r"\bsnapshot\b", r"\bquote\b"),
}

_ASSET_CLASS_PATTERNS = {
    "equity": (
        r"\bequit(?:y|ies)\b",
        r"\bstocks?\b",
        r"\bshares?\b",
        r"\blisted[ -]compan(?:y|ies)\b",
        r"\b(?:stock|securities) exchange\b",
        r"\b(?:asx|bvb|hose|hnx|jse|kospi|kosdaq|krx|ngx|nzx|qse|tadawul|twse)\b",
    ),
    "etf": (r"\betfs?\b", r"\bexchange traded funds?\b"),
    "fund": (r"\bmutual funds?\b", r"\bfunds?\b"),
    "index": (r"\bind(?:ex|ices)\b",),
    "option": (r"\boptions?\b", r"\bput[ -]call\b"),
    "fx": (
        r"\bfx\b",
        r"\bforex\b",
        r"\bforeign exchange\b",
        r"\bcurrency pairs?\b",
        r"\bexchange[ -]rates?\b",
    ),
    "crypto": (
        r"\bcrypto(?:currency|currencies)?\b",
        r"\bdigital currenc(?:y|ies)\b",
        r"\bbitcoin\b",
        r"\bether(?:eum)?\b",
    ),
    "commodity": (r"\bcommodit(?:y|ies)\b", r"\bcrude oil\b", r"\bnatural gas\b", r"\bgold\b", r"\bsilver\b"),
    "fixed_income": (
        r"\bfixed income\b",
        r"\bbonds?\b",
        r"\btreasur(?:y|ies)\b",
        r"\byields?\b",
        r"\b(?:sovereign|external|government|public)[ -]debt\b",
        r"\bdebt securit(?:y|ies)\b",
    ),
    # "future" is ordinary prose in descriptions such as "future peaks".
    # Require the plural instrument name or an explicit future contract.
    "futures": (r"\bfutures\b", r"\bfuture contracts?\b"),
    "macro": (r"\bmacro(?:economic)?\b", r"\beconomic indicators?\b", r"\bgdp\b", r"\bcpi\b", r"\binflation\b", r"\bunemployment\b"),
}

_CURRENCY_SYMBOLS = {
    "AED", "AUD", "BDT", "BRL", "CAD", "CHF", "CNY", "EUR", "GBP", "HKD",
    "IDR", "INR", "ISK", "JPY", "KES", "KRW", "MXN", "NGN", "NOK", "NZD",
    "PGK", "PKR", "QAR", "RON", "RUB", "SAR", "SEK", "SGD", "TRY", "TWD",
    "USD", "VND", "ZAR",
}
_CRYPTO_SYMBOLS = {
    "ADA", "AVAX", "BNB", "BTC", "DOGE", "DOT", "ETH", "LINK", "LTC",
    "MATIC", "SHIB", "SOL", "TRX", "USDC", "USDT", "XRP",
}
_COMMODITY_SYMBOLS = {"BRENT", "WTI", "XAG", "XAU"}
_NON_TICKER_WORDS = {
    *_STOP_WORDS,
    "adjusted", "alpha", "annual", "api", "apple", "asia", "ask", "cad",
    "china", "close", "cost", "cpi", "crypto", "daily", "eod", "eps", "equity",
    "etf", "europe", "exchange", "forex", "fund", "fx", "gdp", "global", "gold",
    "basel", "historical", "history", "hourly", "india", "intraday", "ipo", "japan",
    "latest", "live", "london", "macd", "macro", "market", "monthly", "news",
    "ohlc", "ohlcv", "option", "price", "prices", "quote", "rate", "rates",
    "realtime", "region", "rsi", "security", "silver", "stock", "stocks",
    "adx", "asx", "bvb", "dfm", "dse", "hnx", "hose", "jse", "kosdaq", "kospi",
    "krx", "ngx", "nse", "nzx", "png", "pse", "psx", "qse", "sgx", "tadawul",
    "twse", "symbol", "ticker", "united", "us", "usa", "vantage", "volume", "weekly",
}
_CONTEXTUAL_TICKER_EVIDENCE_RE = re.compile(
    r"\b(?:adjusted|company|dividend|earnings?|equities|equity|fundamentals?|history|"
    r"historical|holders?|market data|ohlc|options?|prices?|quotes?|shares?|splits?|"
    r"stocks?|symbols?|tickers?|volume)\b",
    re.IGNORECASE,
)
_ROMAN_NUMERAL_RE = re.compile(r"^[IVXLCDM]+$")
_INSTRUMENT_PAIR_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z]{3,5})\s*[/_-]\s*([A-Za-z]{3,5})(?![A-Za-z0-9])"
)
_LABELED_TICKER_RE = re.compile(
    r"(?:\$|\b(?:ticker|symbol|nasdaq|nyse|amex)\s*[:=]?\s*)([A-Za-z][A-Za-z0-9]{0,5}(?:[.-][A-Za-z0-9]{1,2})?)\b",
    re.IGNORECASE,
)
_CONTEXTUAL_TICKER_RE = re.compile(
    r"\b(?:for|of|on)\s+([A-Za-z][A-Za-z0-9]{1,5}(?:[.-][A-Za-z0-9]{1,2})?)\b",
    re.IGNORECASE,
)
_UPPERCASE_TICKER_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2,5}(?:[.-][A-Z0-9]{1,2})?)(?![A-Za-z0-9])")

_REGION_PATTERNS = {
    "global": (r"\bglobal\b", r"\bworldwide\b", r"\binternational\b"),
    "united_states": (
        r"\bunited states\b",
        r"\bu\.?s\.?\b",
        r"\bus market\b",
        r"\bnyse\b",
        r"\bnasdaq\b(?!\s+iceland\b)",
    ),
    "united_kingdom": (r"\bunited kingdom\b", r"\bu\.?k\.?\b", r"\blondon\b"),
    "europe": (r"\beurope(?:an)?\b", r"\beuro[ -]?area\b", r"\beurozone\b"),
    "canada": (r"\bcanada\b", r"\bcanadian\b", r"\btoronto\b"),
    "china": (r"\bchina\b", r"\bshanghai\b", r"\bshenzhen\b"),
    "india": (r"\bindia\b", r"\bbse\b", r"\bnse\b"),
    "japan": (r"\bjapan(?:ese)?\b", r"\btokyo\b"),
    "australia": (r"\baustralia(?:n)?\b", r"\basx\b"),
    "hong_kong": (r"\bhong kong\b", r"\bhkex\b"),
    "singapore": (r"\bsingapore\b", r"\bsgx\b"),
    "asia_pacific": (r"\basia[ -]pacific\b", r"\bapac\b"),
    "new_zealand": (r"\bnew zealand\b", r"\bnzx\b"),
    "south_korea": (r"\bsouth korea\b", r"\bkorea(?:n)?\b", r"\bkrx\b", r"\bkospi\b", r"\bkosdaq\b"),
    "taiwan": (r"\btaiwan\b", r"\btwse\b", r"\btpex\b"),
    "malaysia": (r"\bmalaysia(?:n)?\b", r"\bbursa malaysia\b", r"\bklse\b"),
    "thailand": (r"\bthailand\b", r"\bthai\b", r"\bset index\b"),
    "indonesia": (r"\bindonesia(?:n)?\b", r"\bindonesia stock exchange\b", r"\bidx\b"),
    "philippines": (r"\bphilippines?\b", r"\bphilippine stock exchange\b", r"\bpse\b"),
    "vietnam": (r"\bvietnam(?:ese)?\b", r"\bhose\b", r"\bhnx\b"),
    "south_africa": (r"\bsouth africa(?:n)?\b", r"\bjse\b"),
    "brazil": (r"\bbrazil(?:ian)?\b", r"\bb3 exchange\b", r"\bbovespa\b"),
    "mexico": (r"\bmexico\b", r"\bmexican\b", r"\bbmv\b"),
    "chile": (r"\bchile(?:an)?\b", r"\bsantiago stock exchange\b"),
    "poland": (r"\bpoland\b", r"\bpolish\b", r"\bwarsaw stock exchange\b", r"\bgpw\b"),
    "czech_republic": (r"\bczech(?:ia| republic)?\b", r"\bprague stock exchange\b"),
    "hungary": (r"\bhungar(?:y|ian)\b", r"\bbudapest stock exchange\b"),
    "romania": (r"\bromania(?:n)?\b", r"\bbucharest stock exchange\b", r"\bbvb\b"),
    "greece": (r"\bgreece\b", r"\bgreek\b", r"\bathens exchange\b", r"\bathex\b"),
    "iceland": (r"\biceland(?:ic)?\b", r"\bnasdaq iceland\b"),
    "turkey": (r"\bturkey\b", r"\bturkish\b", r"\bborsa istanbul\b", r"\bbist\b"),
    "israel": (r"\bisrael(?:i)?\b", r"\btel aviv stock exchange\b", r"\btase\b"),
    "saudi_arabia": (r"\bsaudi arabia(?:n)?\b", r"\bsaudi\b", r"\btadawul\b"),
    "united_arab_emirates": (r"\bunited arab emirates\b", r"\bu\.?a\.?e\.?\b", r"\badx\b", r"\bdfm\b"),
    "qatar": (r"\bqatar(?:i)?\b", r"\bqse\b"),
    "kenya": (r"\bkenya(?:n)?\b", r"\bnairobi securities exchange\b"),
    "nigeria": (r"\bnigeria(?:n)?\b", r"\bnigerian exchange\b", r"\bngx\b"),
    "pakistan": (r"\bpakistan(?:i)?\b", r"\bpakistan stock exchange\b", r"\bpsx\b"),
    "bangladesh": (r"\bbangladesh(?:i)?\b", r"\bdhaka stock exchange\b", r"\bdse\b"),
    "sri_lanka": (r"\bsri lanka(?:n)?\b", r"\bcolombo stock exchange\b", r"\bcse\b"),
    "fiji": (r"\bfiji(?:an)?\b",),
    "papua_new_guinea": (r"\bpapua new guinea\b", r"\bpng\b"),
}

_DATA_TYPE_PATTERNS = {
    "price": (r"\bprices?\b", r"\bclose\b", r"\bmarket value\b", r"\bexchange[ -]rates?\b"),
    "adjusted_price": (r"\badjusted (?:close|prices?|ohlc)\b", r"\badjusted\b"),
    "ohlcv": (r"\bohlcv?\b", r"\bcandles?\b", r"\bprice bars?\b"),
    "volume": (r"\bvolume\b", r"\btrading volume\b"),
    "quote": (r"\bquotes?\b",),
    "bid_ask": (
        r"\bbid[ -](?:and|&)[ -]?ask\b",
        r"\bbid ask\b",
        r"\bbid\b[^.!?]{0,40}\bask\b",
    ),
    "corporate_actions": (r"\bcorporate actions?\b",),
    "dividends": (r"\bdividends?\b",),
    "splits": (r"\bsplits?\b",),
    "fundamentals": (r"\bfundamentals?\b", r"\bcompany overview\b"),
    "financial_statements": (r"\bfinancial statements?\b", r"\bincome statements?\b", r"\bbalance sheets?\b", r"\bcash flows?\b"),
    "earnings": (r"\bearnings?\b", r"\beps\b"),
    "estimates": (r"\bestimates?\b", r"\bforecast(?:s)?\b"),
    "holdings": (r"\bholdings?\b",),
    "fund_holdings": (r"\betfs? holdings?\b", r"\bfund holdings?\b", r"\bportfolio holdings?\b", r"\bconstituents?\b"),
    "fund_fees": (r"\bexpense ratios?\b", r"\bmanagement fees?\b", r"\bfund fees?\b", r"\bnet expense\b"),
    "sector_exposure": (r"\bsector exposures?\b", r"\bsector allocations?\b", r"\bsector weights?\b"),
    "ownership": (
        r"\bownership\b",
        r"\binstitutional (?:holders?|holdings?)\b",
        r"\binsider holders?\b",
        r"\bsecurity ownership\b",
        r"\b(?:share|stock)holders?\b",
        r"\bbeneficial owners?\b",
        r"\bmajor holders?\b",
    ),
    "news": (r"\bnews\b",),
    "sentiment": (r"\bsentiment\b",),
    "transcript": (r"\btranscripts?\b", r"\bearnings calls?\b"),
    "technical_indicator": (r"\btechnical indicators?\b", r"\brsi\b", r"\bmacd\b", r"\bmoving averages?\b"),
    "economic_indicator": (r"\beconomic indicators?\b", r"\bgdp\b", r"\bcpi\b", r"\binflation\b", r"\bunemployment\b", r"\bpayroll\b"),
    "fixed_income_yield": (
        r"\btreasury yields?\b",
        r"\bbond yields?\b",
        r"\byield curves?\b",
        r"\bfixed income rates?\b",
    ),
    "yield_curve": (r"\byield curves?\b", r"\bpar yields?\b", r"\bpar yield curves?\b"),
    "auction_results": (r"\bauction results?\b", r"\btreasury auctions?\b", r"\bauction dates?\b"),
    "filings": (
        r"\bfilings?\b",
        r"\b10[ -]?[kq]\b",
        r"\bform 13f\b",
        r"\bsubmissions?\b",
    ),
    "form_13f": (r"\bform 13f\b", r"\b13f filings?\b", r"\b13f holdings?\b"),
    "xbrl": (r"\bxbrl\b", r"\bcompany facts?\b", r"\bcompanyfacts\b", r"\btaxonom(?:y|ies)\b"),
    "insider_transactions": (r"\binsider transactions?\b", r"\binsider trades?\b"),
    "credit": (r"\bbank credit\b", r"\bprivate credit\b", r"\bcredit series\b"),
    "external_debt": (
        r"\bexternal[ -]debt\b",
        r"\bexternal debt stocks?\b",
        r"\bpublic and publicly guaranteed debt\b",
    ),
    "policy_rate": (r"\bpolicy[ -]rates?\b", r"\bcentral bank rates?\b"),
    "debt_service_ratio": (r"\bdebt[ -]service[ -]ratios?\b", r"\bdebt service ratios?\b"),
    "market_cap": (r"\bmarket cap(?:italization)?\b", r"\bmarket_cap\b"),
    "supply": (r"\bcirculating supply\b", r"\btotal supply\b", r"\bmax supply\b"),
    "options": (r"\boptions?\b", r"\bput[ -]call\b", r"\boption chains?\b"),
    "implied_volatility": (r"\bimplied volatility\b", r"\bimplied_volatility\b", r"\biv surface\b"),
    "option_greeks": (r"\bgreeks?\b", r"\bdelta\b[^.!?]{0,80}\bgamma\b"),
    "open_interest": (r"\bopen interest\b", r"\bopen_interest\b"),
    "settlement": (r"\bsettlement prices?\b", r"\bsettle(?:ment)?\b"),
    "contract_reference": (r"\bcontract expir(?:y|ation)\b", r"\bcontract metadata\b", r"\bfutures contracts?\b"),
    "term_structure": (r"\bterm[ -]structure\b", r"\bforward curves?\b", r"\bfutures curves?\b"),
    "calendar": (r"\bcalendars?\b", r"\bipo calendar\b", r"\bearnings calendar\b"),
    # Specialized discovery intents must not collapse into generic symbol
    # lookup: mapping identifiers and enumerating exchanges are distinct jobs.
    "identifier_mapping": (
        r"\bidentifier mappings?\b",
        r"\bcross[ -]identifier\b",
        r"\bisin\b[^.!?]{0,80}\bfigi\b",
        r"\bfigi\b[^.!?]{0,80}\bisin\b",
    ),
    "exchange_reference": (
        r"\bcrypto(?:currency)? exchanges?\b",
        r"\bexchange(?:s)?(?:\s*(?:,|and|&))?\s*symbols?\b",
        r"\bsymbols?(?:\s*(?:,|and|&))?\s*exchange(?:s)?\b",
        r"\bexchange coverage\b",
    ),
    "reference": (
        r"\breference\b",
        r"\bsearch endpoint\b",
        r"\bsymbol(?:s)?(?: search| mapping| coverage)?\b",
        r"\bidentifier(?:s| mapping)?\b",
        r"\bisin\b",
        r"\bfigi\b",
        r"\bprofiles?\b",
    ),
}

_DATA_TYPE_COMPATIBILITY = {
    "price": {"price", "adjusted_price", "ohlcv", "quote", "bid_ask"},
    "adjusted_price": {"adjusted_price"},
    "ohlcv": {"ohlcv"},
    "volume": {"volume", "ohlcv"},
    "quote": {"quote", "bid_ask"},
    "bid_ask": {"bid_ask"},
    "fundamentals": {"fundamentals", "financial_statements", "earnings", "estimates"},
    "financial_statements": {"financial_statements"},
    "corporate_actions": {"corporate_actions", "dividends", "splits"},
    "holdings": {"holdings", "fund_holdings", "ownership"},
    "ownership": {"ownership"},
    "fund_holdings": {"fund_holdings"},
    "fund_fees": {"fund_fees"},
    "sector_exposure": {"sector_exposure"},
    "fixed_income_yield": {"fixed_income_yield"},
    "yield_curve": {"yield_curve"},
    "auction_results": {"auction_results"},
    "filings": {"filings"},
    "form_13f": {"form_13f"},
    "xbrl": {"xbrl"},
    "insider_transactions": {"insider_transactions"},
    "credit": {"credit"},
    "external_debt": {"external_debt"},
    "policy_rate": {"policy_rate"},
    "debt_service_ratio": {"debt_service_ratio"},
    "market_cap": {"market_cap"},
    "supply": {"supply"},
    "options": {"options"},
    "implied_volatility": {"implied_volatility"},
    "option_greeks": {"option_greeks"},
    "open_interest": {"open_interest"},
    "settlement": {"settlement"},
    "contract_reference": {"contract_reference"},
    "term_structure": {"term_structure"},
    "identifier_mapping": {"identifier_mapping"},
    "exchange_reference": {"exchange_reference"},
}

_EXPLICIT_VALUE_ALIASES = {
    "adjusted_close": "adjusted_price",
    "adjusted_prices": "adjusted_price",
    "bid_ask_quotes": "bid_ask",
    "cryptocurrency": "crypto",
    "cryptocurrencies": "crypto",
    "end_of_day": "daily",
    "financial_statement": "financial_statements",
    "foreign_exchange": "fx",
    "hk": "hong_kong",
    "apac": "asia_pacific",
    "prices": "price",
    "real_time": "realtime",
    "technical_indicators": "technical_indicator",
    "uk": "united_kingdom",
    "us": "united_states",
    "usa": "united_states",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized(value: Any) -> str:
    return " ".join(_TOKEN_RE.findall(_text(value).lower()))


def _matches(text: str, patterns: Sequence[str]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _infer_values(text: str, patterns: Mapping[str, Sequence[str]]) -> set[str]:
    return {name for name, choices in patterns.items() if _matches(text, choices)}


def _asset_class_tags(values: Sequence[Any] | set[str]) -> list[str]:
    return [f"{ASSET_CLASS_TAG_PREFIX}{value}" for value in sorted({_normalized(item).replace(" ", "_") for item in values if _normalized(item)})]


def _instrument_symbol_asset_classes(value: Any) -> set[str]:
    """Infer an asset class from explicit instrument notation or ticker context."""
    text = _text(value)
    inferred: set[str] = set()
    classified_symbols: set[str] = set()

    for match in _INSTRUMENT_PAIR_RE.finditer(text):
        base, quote = (match.group(1).upper(), match.group(2).upper())
        classified_symbols.update((base, quote))
        if base in _COMMODITY_SYMBOLS or quote in _COMMODITY_SYMBOLS:
            inferred.add("commodity")
        elif base in _CRYPTO_SYMBOLS or quote in _CRYPTO_SYMBOLS:
            inferred.add("crypto")
        elif base in _CURRENCY_SYMBOLS and quote in _CURRENCY_SYMBOLS:
            inferred.add("fx")

    candidates: list[tuple[str, bool, bool]] = []
    candidates.extend((match.group(1), True, False) for match in _LABELED_TICKER_RE.finditer(text))
    candidates.extend((match.group(1), False, True) for match in _CONTEXTUAL_TICKER_RE.finditer(text))
    candidates.extend((match.group(1), False, False) for match in _UPPERCASE_TICKER_RE.finditer(text))
    contextual_ticker_evidence = bool(_CONTEXTUAL_TICKER_EVIDENCE_RE.search(text))
    standalone_currency_context = bool(
        re.search(
            r"\b(?:currency|exchange[ -]rates?|foreign exchange|forex|fx|spot rates?)\b",
            text,
            re.IGNORECASE,
        )
    )
    for raw_symbol, explicitly_labeled, contextual in candidates:
        symbol = raw_symbol.upper().strip("$.,:;!?")
        base_symbol = re.split(r"[.-]", symbol, maxsplit=1)[0]
        if symbol in classified_symbols or base_symbol in classified_symbols:
            continue
        if _ROMAN_NUMERAL_RE.fullmatch(base_symbol):
            continue
        if base_symbol in _CRYPTO_SYMBOLS:
            inferred.add("crypto")
            continue
        if base_symbol in _COMMODITY_SYMBOLS:
            inferred.add("commodity")
            continue
        if base_symbol in _CURRENCY_SYMBOLS:
            # A denomination such as "JPY duration" or "prices in USD" is not
            # itself an FX data request. Currency pairs are classified above;
            # standalone codes require explicit currency-market context.
            if explicitly_labeled or standalone_currency_context:
                inferred.add("fx")
            continue
        if base_symbol.lower() in _NON_TICKER_WORDS:
            continue
        if contextual and not contextual_ticker_evidence:
            continue
        if explicitly_labeled or len(base_symbol) >= 2:
            inferred.add("equity")
    return inferred


def _derivative_underlying_asset_classes(text: str, derivative: str) -> set[str]:
    """Identify only assets syntactically attached to an option/futures leg.

    A multi-asset request such as "equity prices, oil futures, and QAR/USD FX"
    must retain equity and FX as independent primary asset classes. The older
    set-based rule incorrectly converted every non-derivative class into an
    underlying whenever the word ``futures`` appeared anywhere in the query.
    """
    normalized = _normalized(text)
    aliases = {
        "equity": r"(?:equity|equities|stocks?|shares?)",
        "etf": r"(?:etfs?|exchange traded funds?)",
        "index": r"(?:index|indices)",
        "fx": r"(?:fx|forex|foreign exchange|currenc(?:y|ies))",
        "crypto": r"(?:crypto(?:currenc(?:y|ies))?|bitcoin|ethereum)",
        "commodity": r"(?:commodit(?:y|ies)|crude oil|natural gas|gold|silver|wti|brent)",
        "fixed_income": r"(?:fixed income|bonds?|treasur(?:y|ies)|debt securities)",
    }
    matches: set[str] = set()
    for asset_class, alias in aliases.items():
        patterns = (
            rf"\b{alias}(?:[- ]\w+){{0,2}}[- ]{derivative}\b",
            rf"\b{derivative}\s+(?:on|for|of)\s+{alias}\b",
        )
        if any(re.search(pattern, normalized) for pattern in patterns):
            matches.add(asset_class)
    return matches


def concise_financial_description(value: Any, *, limit: int = 700) -> str:
    """Keep the vendor's capability summary while dropping examples and code."""
    text = f" {_SPACE_RE.sub(' ', _text(value))} "
    cut = len(text)
    lowered = text.lower()
    for marker in _DESCRIPTION_BREAKS:
        position = lowered.find(marker.lower())
        if position >= 0:
            cut = min(cut, position)
    concise = text[:cut].strip(" .")
    if len(concise) > limit:
        concise = concise[:limit].rsplit(" ", 1)[0].rstrip(" .")
    return f"{concise}." if concise else ""


def _identity_text(dataset: Mapping[str, Any]) -> str:
    return _normalized(
        " ".join(
            (
                _text(dataset.get("dataset_id")),
                _text(dataset.get("name")),
                _text(dataset.get("operation")),
                _text(dataset.get("category")),
                " ".join(
                    _text(item)
                    for item in dataset.get("tags") or []
                    if not _text(item).lower().startswith(ASSET_CLASS_TAG_PREFIX)
                ),
            )
        )
    )


def _field_schema_text(dataset: Mapping[str, Any]) -> str:
    """Return field-level evidence without serializing entire response examples."""
    parts: list[str] = []
    for field in dataset.get("fields") or []:
        if not isinstance(field, Mapping):
            continue
        # Inferred canonical fields are downstream products of these
        # dimensions. Feeding their definitions back into inference can create
        # capabilities that the provider never documented (for example, the
        # word "auction" inside a generic yield definition).
        if field.get("inferred"):
            continue
        parts.extend(
            _text(field.get(key))
            for key in ("name", "vendor_name", "canonical_name", "path", "provider_definition", "description")
            if _text(field.get(key))
        )

    def visit_schema(node: Any, *, depth: int = 0) -> None:
        if depth > 5 or len(parts) >= 1_200 or not isinstance(node, Mapping):
            return
        properties = node.get("properties") if isinstance(node.get("properties"), Mapping) else {}
        for name, definition in properties.items():
            parts.append(_text(name))
            if isinstance(definition, Mapping):
                if _text(definition.get("description")):
                    parts.append(_text(definition.get("description")))
                visit_schema(definition, depth=depth + 1)
        if isinstance(node.get("items"), Mapping):
            visit_schema(node["items"], depth=depth + 1)

    if not parts:
        schema = dataset.get("data_schema") or dataset.get("output_schema") or dataset.get("response_schema")
        visit_schema(schema)
    return _normalized(" ".join(parts))


def _source_id(dataset: Mapping[str, Any], source_id: str = "") -> str:
    return _text(source_id or dataset.get("source") or dataset.get("provider")).lower().replace("-", "_")


def infer_financial_dimensions(dataset: Mapping[str, Any], *, source_id: str = "") -> Dict[str, Any]:
    """Infer normalized finance dimensions from stable endpoint identity first."""
    identity = _identity_text(dataset)
    field_schema = _field_schema_text(dataset)
    summary = _normalized(concise_financial_description(dataset.get("description")))
    combined = f"{identity} {summary}".strip()
    typed_evidence = f"{identity} {field_schema}".strip()
    source = _source_id(dataset, source_id)

    frequencies = _infer_values(identity, _FREQUENCY_PATTERNS)
    # A documented frequency argument is capability evidence even when the
    # endpoint's short description omits it. Lookback periods (e.g. period=1y)
    # are intentionally excluded: they do not imply annual observations.
    input_schema = dataset.get('input_schema') or {}
    properties = (input_schema.get('properties') or {}) if isinstance(input_schema, Mapping) else {}
    if not isinstance(properties, Mapping):
        properties = {}
    for name in ('freq', 'frequency'):
        declaration = properties.get(name) or {}
        if isinstance(declaration, Mapping):
            values = [declaration.get('default'), *(declaration.get('enum') or [])]
            frequencies.update(_infer_values(' '.join(v for v in values if isinstance(v, str)), _FREQUENCY_PATTERNS))
    timeliness = _infer_values(identity, _TIMELINESS_PATTERNS)
    asset_classes = _infer_values(combined, _ASSET_CLASS_PATTERNS)
    regions = _infer_values(summary, _REGION_PATTERNS)
    data_types = _infer_values(typed_evidence, _DATA_TYPE_PATTERNS)
    underlying_asset_classes: set[str] = set()

    # Specialized access semantics must come from endpoint identity or an
    # explicit provider rule. Merely returning ISIN/FIGI/exchange fields does
    # not prove that an endpoint performs mapping or enumerates exchanges.
    specialized_types = {"identifier_mapping", "exchange_reference"}
    data_types.difference_update(specialized_types)
    data_types.update(
        _infer_values(
            identity,
            {key: _DATA_TYPE_PATTERNS[key] for key in specialized_types},
        )
    )

    operation = _normalized(dataset.get("operation") or dataset.get("dataset_id"))
    category = _normalized(dataset.get("category") or " ".join(_text(item) for item in dataset.get("tags") or []))

    # Record derivative underlyings separately from the primary instrument.
    # Generic futures routes therefore cannot claim crude/commodity coverage
    # unless the endpoint identity or summary actually documents it.
    if "option" in asset_classes:
        underlying_asset_classes.update(_derivative_underlying_asset_classes(combined, "options?"))
    if "futures" in asset_classes:
        underlying_asset_classes.update(_derivative_underlying_asset_classes(combined, "futures"))

    if source in {"alpha_vantage", "alphavantage"} or "alpha vantage" in combined:
        if "time series stock" in category:
            asset_classes.add("equity")
            regions.add("global")
            # Alpha Vantage documents this catalog as global, with US exchange
            # coverage explicitly represented by ordinary US symbols.
            regions.add("united_states")
        if "foreign exchange" in category or operation.startswith("alpha vantage fx") or " currency exchange rate" in f" {operation}":
            asset_classes.add("fx")
            regions.add("global")
        if "digital crypto" in category or "digital currency" in operation or "crypto intraday" in operation:
            asset_classes.add("crypto")
            regions.add("global")
        if "commodities" in category:
            asset_classes.add("commodity")
            regions.add("global")
            data_types.add("price")
        if "economic indicators" in category:
            # "Federal funds rate" is a macro policy rate, not an investment fund.
            asset_classes.discard("fund")
            asset_classes.add("macro")
            regions.add("united_states")
            data_types.add("economic_indicator")
        if "index data" in category:
            asset_classes.add("index")
            data_types.add("price")
        if "options data" in category:
            asset_classes.update({"option", "equity"})
            underlying_asset_classes.add("equity")
            regions.add("united_states")
            data_types.add("options")
        if "technical indicators" in category:
            asset_classes.add("equity")
            regions.update({"global", "united_states"})
            data_types.add("technical_indicator")
            frequencies.update({"intraday", "daily", "weekly", "monthly"})
        if "fundamental data" in category:
            asset_classes.add("equity")
            regions.update({"global", "united_states"})

        if "time series" in operation or operation.startswith("alpha vantage fx") or "digital currency" in operation or "crypto intraday" in operation:
            data_types.update({"price", "ohlcv", "volume"})
        if " adjusted" in f" {operation}":
            data_types.update({"adjusted_price", "dividends"})
            if "daily adjusted" in operation:
                data_types.add("splits")
        if "global quote" in operation or "bulk quotes" in operation:
            data_types.update({"price", "quote"})
            timeliness.add("snapshot")
        if "bid ask" in operation:
            data_types.update({"quote", "bid_ask"})
        if "realtime" in operation:
            timeliness.add("realtime")
        if "historical" in operation:
            timeliness.add("historical")
        if "top gainers losers" in operation:
            asset_classes.add("equity")
            regions.discard("global")
            regions.add("united_states")
            data_types.update({"price", "volume"})
        if "news sentiment" in operation:
            asset_classes.update({"equity", "fx", "crypto"})
            regions.add("global")
            data_types.update({"news", "sentiment"})
        if "earnings call transcript" in operation:
            asset_classes.add("equity")
            regions.add("global")
            data_types.update({"earnings", "transcript"})
        if "overview" in operation:
            data_types.update({"fundamentals", "reference"})
        if any(value in operation for value in ("income statement", "balance sheet", "cash flow")):
            data_types.update({"fundamentals", "financial_statements"})
        if "earnings" in operation:
            data_types.add("earnings")
        if "transcript" in operation:
            asset_classes.add("equity")
            data_types.update({"earnings", "transcript"})
        if "estimate" in operation:
            data_types.add("estimates")
        if "institutional holdings" in operation:
            data_types.update({"holdings", "ownership"})
            data_types.add("form_13f")
        if "etf profile" in operation:
            asset_classes.add("etf")
            data_types.update({"holdings", "fund_holdings"})
        if "expense" in typed_evidence:
            data_types.add("fund_fees")
        if "sector" in typed_evidence and "etf" in combined:
            data_types.add("sector_exposure")
        if "dividends" in operation:
            data_types.update({"corporate_actions", "dividends"})
        if "splits" in operation:
            data_types.update({"corporate_actions", "splits"})
        if "calendar" in operation:
            frequencies.add("event_driven")
            data_types.add("calendar")

    if source == "finnhub" or operation.startswith("finnhub"):
        regions.add("global")
        if category in {"stock fundamentals", "stock estimates", "stock price", "technical analysis", "global filings search"}:
            asset_classes.add("equity")
        if category == "forex":
            asset_classes.add("fx")
        if category == "crypto":
            asset_classes.add("crypto")
        if category == "bonds":
            asset_classes.add("fixed_income")
        if category == "economic":
            asset_classes.discard("fund")
            asset_classes.add("macro")
            data_types.add("economic_indicator")
        if category == "etfs indices":
            asset_classes.update({"etf", "index"})
        if category == "mutual funds":
            asset_classes.add("fund")
        if "candle" in operation or "tick" in operation:
            data_types.update({"price", "ohlcv", "volume"})
            timeliness.add("historical")
        if "quote" in operation or "bidask" in operation or "nbbo" in operation:
            data_types.update({"price", "quote"})
            timeliness.add("snapshot")
        if "news" in operation or "press release" in operation:
            data_types.add("news")
        if "sentiment" in operation:
            data_types.add("sentiment")
        if "financial" in operation:
            data_types.update({"fundamentals", "financial_statements"})
        if "estimate" in operation or "price target" in operation:
            data_types.add("estimates")
        if "earnings" in operation:
            data_types.add("earnings")
        if "transcript" in operation:
            asset_classes.add("equity")
            data_types.update({"earnings", "transcript"})
        if "holding" in operation or "ownership" in operation or "constituents" in operation:
            data_types.add("holdings")
        if "ownership" in operation and "institutional" in typed_evidence:
            data_types.add("form_13f")
        if "etf" in combined and "expense ratio" in typed_evidence:
            data_types.add("fund_fees")
        if "etf" in combined and any(marker in typed_evidence for marker in ("sector exposure", "sector allocation", "sector")):
            data_types.add("sector_exposure")
        if "filing" in operation:
            data_types.add("filings")
        if "insider" in operation and any(marker in operation for marker in ("transaction", "filing")):
            data_types.update({"ownership", "insider_transactions"})
        if "dividend" in operation:
            data_types.update({"corporate_actions", "dividends"})
        if "split" in operation:
            data_types.update({"corporate_actions", "splits"})
        if "calendar" in operation or "filing" in operation:
            frequencies.add("event_driven")
        if any(marker in operation for marker in ("symbol", "exchange")):
            data_types.add("reference")
        if category == "crypto" and any(marker in operation for marker in ("symbol", "exchange")):
            data_types.add("exchange_reference")

    if source == "twelve_data":
        regions.add("global")
        if "time series" in operation:
            asset_classes.update({"equity", "etf", "fund", "fx", "crypto", "index"})
            data_types.update({"price", "ohlcv", "volume"})
        elif not asset_classes:
            asset_classes.add("equity")

    if source == "massive":
        regions.add("global")
        if " stocks " in f" {operation} ":
            asset_classes.add("equity")
        if " options " in f" {operation} ":
            asset_classes.update({"option", "equity"})
        if " futures " in f" {operation} ":
            asset_classes.add("futures")
        if " indices " in f" {operation} ":
            asset_classes.add("index")
        if " forex " in f" {operation} ":
            asset_classes.add("fx")
        if " crypto " in f" {operation} ":
            asset_classes.add("crypto")
        if " economy " in f" {operation} ":
            asset_classes.add("macro")
        if "aggregates" in operation:
            data_types.update({"price", "ohlcv", "volume"})
            timeliness.add("historical")
        if "forex" in operation and "quote" in operation:
            data_types.update({"price", "quote", "bid_ask"})
        if " options " in f" {operation} ":
            data_types.add("options")
        if "option chain" in operation or "contract snapshot" in operation:
            data_types.update({
                "options", "bid_ask", "implied_volatility", "option_greeks",
                "open_interest", "volume",
            })
        if " futures " in f" {operation} ":
            data_types.add("contract_reference")
        if "futures aggregates" in operation:
            data_types.update({"price", "ohlcv", "volume", "settlement"})
        if "futures" in operation and "contracts snapshot" in operation:
            data_types.update({"price", "open_interest", "contract_reference", "term_structure"})
        if any(marker in operation for marker in ("futures contracts", "futures products")):
            data_types.update({"contract_reference", "term_structure"})

    if source == "financial_modeling_prep":
        regions.add("global")
        if not asset_classes and any(
            marker in operation
            for marker in ("statement", "profile", "quote", "stock", "earning", "dividend", "market cap")
        ):
            asset_classes.add("equity")
        if "historical price full" in operation and not any(
            marker in operation for marker in ("stock dividend", "stock split")
        ):
            asset_classes.add("index")

    if source == "treasury_fiscal_data":
        regions.add("united_states")
        asset_classes.update({"macro", "fixed_income"})
        if any(marker in operation for marker in ("yield curve", "par yield", "avg interest rates")):
            data_types.add("fixed_income_yield")
        if any(marker in operation for marker in ("yield curve", "par yield")):
            data_types.add("yield_curve")
        if "auction" in operation:
            data_types.update({"auction_results", "fixed_income_yield"})
        if "exchange" in operation or "currency" in operation:
            asset_classes.discard("fixed_income")
            asset_classes.add("fx")

    if source in {"world_bank", "imf", "bis", "oecd"}:
        regions.add("global")
        asset_classes.add("macro")
        if any(marker in operation for marker in ("debt", "credit", "security")):
            asset_classes.add("fixed_income")

    if source == "world_bank" and any(
        marker in operation
        for marker in (
            "dp dod decd",
            "external debt",
            "dt dod",
            "dt dsd",
        )
    ):
        # World Bank indicator codes and labels are endpoint-specific factual
        # evidence. Generic SDMX/data routes must not inherit this tag.
        asset_classes.add("fixed_income")
        data_types.add("external_debt")

    if source == "bis":
        asset_classes.add("fixed_income")
        if operation.startswith("data ") or "bis get data" in identity:
            data_types.update({"credit", "policy_rate", "debt_service_ratio", "economic_indicator"})
        else:
            data_types.difference_update({"credit", "policy_rate", "debt_service_ratio"})
            data_types.add("reference")

    if source == "imf":
        data_types.update({"credit", "policy_rate", "economic_indicator"})

    if source in {"world_bank", "oecd"}:
        data_types.update({"credit", "economic_indicator"})

    if source == "ecb":
        regions.add("europe")
        asset_classes.update({"macro", "fixed_income"})
        # ECB's SDMX data service is a generic route over official dataflows.
        # These tags describe its documented catalog scope; the exact flow key
        # still needs verification and is called out by answer synthesis.
        data_types.update({
            "credit",
            "policy_rate",
            "economic_indicator",
            "fixed_income_yield",
            "yield_curve",
        })
        if any(marker in operation for marker in ("exchange", "fx", "currency")):
            asset_classes.add("fx")

    if source in {"coingecko", "coinmarketcap"}:
        regions.add("global")
        asset_classes.add("crypto")
        if source == "coingecko" and "nft" in operation:
            asset_classes.discard("crypto")
            asset_classes.add("nft")
        if "derivative" in operation:
            asset_classes.add("futures")
        if any(marker in operation for marker in ("ohlc", "k line", "market chart", "historical")):
            data_types.update({"price", "ohlcv", "volume"})
        if "market cap" in typed_evidence or "market cap" in operation:
            data_types.add("market_cap")
        if "supply" in typed_evidence or "supply" in operation:
            data_types.add("supply")
        if source == "coingecko" and any(marker in operation for marker in ("coins markets", "market chart")):
            data_types.update({"price", "volume", "market_cap"})
        if source == "coingecko" and "coins markets" in operation:
            data_types.add("supply")
        if source == "coinmarketcap" and any(
            marker in operation
            for marker in ("cryptocurrency listings", "cryptocurrency quotes")
        ):
            data_types.update({"price", "quote", "volume", "market_cap", "supply"})
        if any(marker in operation for marker in ("search", " map", "exchange", "market pairs")):
            data_types.add("reference")
        if any(marker in operation for marker in ("exchange", "market pairs")):
            data_types.add("exchange_reference")

    if source == "openfigi":
        regions.add("global")
        asset_classes.update({"equity", "fixed_income", "option", "futures"})
        data_types.update({"reference", "identifier_mapping"})

    if source == "intrinio":
        regions.add("global")
        if not asset_classes:
            asset_classes.add("equity")
        if "option" in operation:
            asset_classes.add("option")
        if "historical stock data" in operation:
            asset_classes.add("equity")
            frequencies.add("daily")
            timeliness.add("historical")
            data_types.update({"price", "ohlcv", "volume"})
        if "profile" in operation:
            data_types.update({"fundamentals", "reference"})
        if "etf" in combined:
            asset_classes.add("etf")
            if "holding" in operation:
                data_types.update({"holdings", "fund_holdings"})
            if "expense ratio" in typed_evidence:
                data_types.add("fund_fees")
            if "sector" in typed_evidence:
                data_types.add("sector_exposure")
            if "holding" not in operation:
                data_types.discard("holdings")
                data_types.discard("fund_holdings")
        if "filing" in operation:
            data_types.add("filings")
        if "insider transaction" in operation:
            data_types.update({"ownership", "insider_transactions"})
        if "institutional holding" in operation:
            data_types.update({"ownership", "form_13f"})
        if "option" in operation:
            data_types.add("options")
        if "greek" in operation or any(marker in typed_evidence for marker in ("delta gamma", "implied volatility")):
            data_types.update({"option_greeks", "implied_volatility"})
        if "open interest" in typed_evidence or "option aggregates" in operation:
            data_types.add("open_interest")

    if source == "aisa_one":
        regions.add("global")
        asset_classes.add("equity")
        if "interest-rate" in operation or "/macro/" in operation:
            asset_classes.add("macro")
        if "price" in operation:
            data_types.update({"price", "ohlcv", "volume"})
        if any(marker in operation for marker in ("financials", "financial-metrics", "company/facts")):
            data_types.update({"fundamentals", "financial_statements"})
        if "filing" in operation:
            data_types.add("filings")
        if "institutional ownership" in operation:
            data_types.update({"ownership", "form_13f"})
        if any(marker in operation for marker in ("earnings", "insider", "ownership", "analyst")):
            frequencies.add("event_driven")

    if source == "eodhd":
        regions.add("global")
        if not asset_classes and any(marker in operation for marker in ("historical data", "fundamental", "exchange", "dividend")):
            asset_classes.add("equity")

    if source == "tiingo":
        regions.add("global")
        if any(marker in operation for marker in ("end of day", "equity realtime", "iex", "fundamentals", "dividends", "splits")):
            asset_classes.add("equity")
        if "fund and etf" in operation:
            asset_classes.update({"fund", "etf"})
        if "forex" in operation:
            asset_classes.add("fx")
        if "crypto" in operation:
            asset_classes.add("crypto")

    if source == "nasdaq_data_link":
        regions.add("global")
        if not asset_classes:
            asset_classes.update({"equity", "index", "macro"})

    if source == "sec_edgar":
        asset_classes.add("equity")
        regions.add("united_states")
        if "submission" in operation or "filing" in operation:
            data_types.add("filings")
        if any(marker in operation for marker in ("xbrl", "companyfacts", "companyconcept", "frames")):
            data_types.update({"xbrl", "fundamentals", "financial_statements"})
        frequencies.add("event_driven")

    if source == "fred":
        asset_classes.discard("fund")
        asset_classes.add("macro")
        regions.add("united_states")
        data_types.add("economic_indicator")

    if source == "yfinance" or operation.startswith("yfinance"):
        regions.add("global")
        if operation in {
            "yfinance download",
            "yfinance ticker history",
            "yfinance tickers history",
            "yfinance tickers download",
        }:
            asset_classes.update({"equity", "etf", "fund", "index", "fx", "crypto"})
            frequencies.update({"intraday", "daily", "weekly", "monthly"})
            timeliness.add("historical")
            data_types.update({"price", "adjusted_price", "ohlcv", "volume"})
        elif "ticker" in operation:
            asset_classes.update({"equity", "etf", "fund", "index"})
        if "option" in operation:
            asset_classes.update({"option", "equity"})
            data_types.add("options")
        if "option chain" in operation:
            data_types.update({
                "bid_ask", "implied_volatility", "open_interest", "options", "volume",
            })
        if any(value in operation for value in ("balance sheet", "balancesheet", "income stmt", "incomestmt", "cash flow", "cashflow", "financials")):
            data_types.update({"fundamentals", "financial_statements"})
        if "earnings" in operation or "eps" in operation:
            data_types.add("earnings")
        if "estimate" in operation or "price targets" in operation:
            data_types.add("estimates")
        if "dividends" in operation:
            data_types.update({"corporate_actions", "dividends"})
        if "splits" in operation or "actions" in operation:
            data_types.update({"corporate_actions", "splits"})
        if "news" in operation:
            data_types.add("news")
        if "holder" in operation:
            data_types.update({"holdings", "ownership"})
        if "funds data" in operation:
            data_types.update({"holdings", "fund_holdings"})
        if "info" in operation:
            data_types.update({"fundamentals", "reference"})
        if "live" in operation or "websocket" in operation:
            timeliness.add("realtime")
            data_types.update({"price", "quote"})
        if "calendar" in operation:
            frequencies.add("event_driven")
            data_types.add("calendar")
        if "lookup stock" in operation:
            asset_classes.add("equity")
            data_types.add("reference")
        if "lookup etf" in operation:
            asset_classes.add("etf")
            data_types.add("reference")
        if "lookup currency" in operation:
            asset_classes.add("fx")
            data_types.add("reference")
        if "lookup cryptocurrency" in operation:
            asset_classes.add("crypto")
            data_types.add("reference")

    # Description-derived values fill gaps, but identity-derived rules above
    # remain authoritative for endpoints that merely mention alternatives.
    if not frequencies:
        frequencies.update(_infer_values(summary, _FREQUENCY_PATTERNS))
    if not timeliness:
        timeliness.update(_infer_values(summary, _TIMELINESS_PATTERNS))
    if not data_types:
        data_types.update(_infer_values(summary, _DATA_TYPE_PATTERNS))

    if "ohlcv" in data_types:
        data_types.update({"price", "volume"})
    if "adjusted_price" in data_types:
        data_types.add("price")
    if "bid_ask" in data_types:
        data_types.update({"price", "quote"})
    if "dividends" in data_types or "splits" in data_types:
        data_types.add("corporate_actions")
    if "holdings" in data_types and asset_classes.intersection({"etf", "fund"}):
        data_types.add("fund_holdings")
    if "filings" in data_types and "xbrl" in data_types:
        data_types.add("fundamentals")

    # An option endpoint describes the derivative itself. Equity references in
    # its documentation identify the underlying and must not let it satisfy a
    # cash-equity quote request.
    derivative_identity = f"{operation} {category}"
    if "option" in asset_classes and "option" in derivative_identity:
        asset_classes = {"option"}

    return {
        "version": FINANCIAL_DIMENSIONS_VERSION,
        "time": {
            "frequencies": sorted(frequencies),
            "timeliness": sorted(timeliness),
        },
        "asset_classes": sorted(asset_classes),
        "asset_class_tags": _asset_class_tags(asset_classes),
        "underlying_asset_classes": sorted(underlying_asset_classes),
        "regions": sorted(regions),
        "data_types": sorted(data_types),
    }


def _explicit_values(request: Mapping[str, Any], singular: str, plural: str) -> set[str]:
    values: list[Any] = []
    singular_value = request.get(singular)
    if singular_value:
        values.append(singular_value)
    plural_value = request.get(plural)
    if isinstance(plural_value, Sequence) and not isinstance(plural_value, (str, bytes)):
        values.extend(plural_value)
    elif plural_value:
        values.append(plural_value)
    normalized = {_normalized(value).replace(" ", "_") for value in values if _normalized(value)}
    return {_EXPLICIT_VALUE_ALIASES.get(value, value) for value in normalized}


def financial_request_text(request: Mapping[str, Any]) -> str:
    preferences = request.get("preferences") if isinstance(request.get("preferences"), Mapping) else {}
    fields = request.get("fields") if isinstance(request.get("fields"), Sequence) and not isinstance(request.get("fields"), (str, bytes)) else []
    time = request.get("time") if isinstance(request.get("time"), Mapping) else {}
    return " ".join(
        value
        for value in (
            _text(request.get("query")),
            _text(request.get("use_case")),
            _text(request.get("asset_class")),
            " ".join(_text(item) for item in request.get("asset_classes") or []),
            _text(request.get("region")),
            " ".join(_text(item) for item in request.get("regions") or []),
            " ".join(_text(item) for item in request.get("data_types") or []),
            " ".join(_text(field) for field in fields),
            " ".join(f"{key} {_text(value)}" for key, value in preferences.items()),
            " ".join(f"{key} {_text(value)}" for key, value in time.items()),
        )
        if value
    )


def infer_financial_requirements(request: Mapping[str, Any]) -> Dict[str, Any]:
    request_text = financial_request_text(request)
    text = _normalized(request_text)
    frequencies = _infer_values(text, _FREQUENCY_PATTERNS)
    timeliness = _infer_values(text, _TIMELINESS_PATTERNS)
    asset_classes = _infer_values(text, _ASSET_CLASS_PATTERNS)
    asset_classes.update(_instrument_symbol_asset_classes(request_text))
    if re.search(r"\b(?:stock|equity|market) (?:index|indices)\b", text):
        asset_classes.discard("equity")
    regions = _infer_values(text, _REGION_PATTERNS)
    data_types = _infer_values(text, _DATA_TYPE_PATTERNS)

    time = request.get("time") if isinstance(request.get("time"), Mapping) else {}
    frequencies.update(_explicit_values(time, "frequency", "frequencies"))
    timeliness.update(_explicit_values(time, "timeliness", "timeliness_modes"))
    asset_classes.update(_explicit_values(request, "asset_class", "asset_classes"))
    regions.update(_explicit_values(request, "region", "regions"))
    data_types.update(_explicit_values(request, "data_type", "data_types"))

    # Generic nouns inside compound product terms are not additional asset
    # classes: an ETF is a fund structurally, but "ETF holdings" asks for the
    # ETF asset class, not a second mutual-fund leg.
    if "etf" in asset_classes and not re.search(r"\bmutual funds?\b", text):
        asset_classes.discard("fund")

    if data_types.intersection({"ownership", "insider_transactions"}) or re.search(r"\bform 13f\b", text):
        asset_classes.add("equity")
    if data_types.intersection({"credit", "external_debt", "policy_rate", "debt_service_ratio"}) or "macroprudential" in text:
        asset_classes.add("macro")

    underlying_asset_classes: set[str] = set()
    if "option" in asset_classes:
        attached = _derivative_underlying_asset_classes(request_text, "options?")
        underlying_asset_classes.update(attached)
        asset_classes.difference_update(attached)
    if "futures" in asset_classes:
        attached = _derivative_underlying_asset_classes(request_text, "futures?")
        underlying_asset_classes.update(attached)
        asset_classes.difference_update(attached)

    if "ohlcv" in data_types:
        data_types.update({"price", "volume"})
    if "adjusted_price" in data_types:
        data_types.add("price")
    if "bid_ask" in data_types:
        data_types.add("quote")
    return {
        "time": {"frequencies": sorted(frequencies), "timeliness": sorted(timeliness)},
        "asset_classes": sorted(asset_classes),
        "asset_class_tags": _asset_class_tags(asset_classes),
        "underlying_asset_classes": sorted(underlying_asset_classes),
        "regions": sorted(regions),
        "data_types": sorted(data_types),
    }


def _compatible_data_type(requested: str, available: set[str]) -> bool:
    return bool(_DATA_TYPE_COMPATIBILITY.get(requested, {requested}) & available)


def evaluate_financial_fit(dataset: Mapping[str, Any], request: Mapping[str, Any]) -> Dict[str, Any]:
    profile = dataset.get("financial_dimensions") if isinstance(dataset.get("financial_dimensions"), Mapping) else infer_financial_dimensions(dataset)
    requirements = infer_financial_requirements(request)
    matched: list[str] = []
    missing: list[str] = []
    conflicts: list[str] = []
    earned = 0.0
    possible = 0.0

    requested_frequencies = set(requirements["time"]["frequencies"])
    available_frequencies = set((profile.get("time") or {}).get("frequencies") or [])
    for value in sorted(requested_frequencies):
        possible += 3.0
        if value in available_frequencies:
            earned += 3.0
            matched.append(f"time.frequency:{value}")
        elif available_frequencies:
            conflicts.append(f"time.frequency:{value}")
        else:
            missing.append(f"time.frequency:{value}")

    requested_timeliness = set(requirements["time"]["timeliness"])
    available_timeliness = set((profile.get("time") or {}).get("timeliness") or [])
    for value in sorted(requested_timeliness):
        possible += 2.0
        if value in available_timeliness:
            earned += 2.0
            matched.append(f"time.timeliness:{value}")
        elif available_timeliness and value in {"realtime", "historical"}:
            conflicts.append(f"time.timeliness:{value}")
        else:
            missing.append(f"time.timeliness:{value}")

    requested_assets = set(requirements["asset_classes"])
    requested_underlyings = set(requirements.get("underlying_asset_classes") or [])
    available_assets = set(profile.get("asset_classes") or [])
    required_asset_tags = set(requirements.get("asset_class_tags") or _asset_class_tags(requested_assets))
    endpoint_asset_tags = set(profile.get("asset_class_tags") or _asset_class_tags(available_assets))
    matched_asset_tags: set[str] = set()
    conflicting_asset_tags: set[str] = set()
    asset_class_compatible = True
    evidence_role = "direct"
    proxy_underlying_overlap: set[str] = set()
    if requested_assets:
        possible += ASSET_CLASS_MATCH_WEIGHT
        overlap = requested_assets & available_assets
        if overlap:
            earned += ASSET_CLASS_MATCH_WEIGHT * (len(overlap) / len(requested_assets))
            matched.extend(f"asset_class:{value}" for value in sorted(overlap))
            matched_asset_tags.update(_asset_class_tags(overlap))
            missing.extend(
                f"asset_class:{value}"
                for value in sorted(requested_assets - overlap)
            )
        elif (
            requested_assets.intersection({"option", "futures"})
            and requested_underlyings
            and available_assets.intersection(requested_underlyings)
        ):
            # Cash/reference data for a derivative's underlying is useful
            # supporting evidence, but it must never be promoted to a direct
            # option/futures match. Keep it retrievable as an explicit proxy.
            evidence_role = "underlying_proxy"
            proxy_underlying_overlap = available_assets & requested_underlyings
            missing.extend(
                f"asset_class:{value}"
                for value in sorted(requested_assets)
            )
        elif available_assets:
            conflicts.append(f"asset_class:{'/'.join(sorted(requested_assets))}")
            conflicting_asset_tags.update(required_asset_tags)
            asset_class_compatible = False
        else:
            # Asset class is a hard routing tag. An untagged endpoint cannot be
            # assumed to satisfy an explicitly asset-scoped request.
            conflicts.append(f"asset_class:{'/'.join(sorted(requested_assets))}")
            conflicting_asset_tags.update(required_asset_tags)
            asset_class_compatible = False

    requested_regions = set(requirements["regions"])
    available_regions = set(profile.get("regions") or [])
    region_match_mode = "unconstrained"
    region_verification_required = False
    unverified: list[str] = []
    if requested_regions:
        possible += 2.0
        compatible_regions = requested_regions & available_regions
        if (
            "global" in available_regions
            and requested_regions != {"global"}
            and not requested_regions.issubset(available_regions - {"global"})
        ):
            compatible_regions = set(requested_regions)
            region_match_mode = "global_claim"
            region_verification_required = True
            unverified.extend(f"region:{value}" for value in sorted(requested_regions))
        elif compatible_regions:
            region_match_mode = "explicit"
        if compatible_regions:
            # A provider's generic "global" label is useful discovery
            # evidence, but it is weaker than documented exchange/country
            # coverage and must never yield a 100% regional fit by itself.
            earned += 1.0 if region_verification_required else 2.0
            matched.extend(f"region:{value}" for value in sorted(compatible_regions))
        elif available_regions:
            conflicts.append(f"region:{'/'.join(sorted(requested_regions))}")
        else:
            missing.append(f"region:{'/'.join(sorted(requested_regions))}")

    available_underlyings = set(profile.get("underlying_asset_classes") or [])
    proxy_dimensions: list[str] = []
    for value in sorted(requested_underlyings):
        possible += 3.0
        if value in available_underlyings:
            earned += 3.0
            matched.append(f"underlying_asset_class:{value}")
        elif value in proxy_underlying_overlap:
            # A spot/reference endpoint substantiates the underlying market,
            # not the derivative contract. Half credit keeps it discoverable
            # without making source-level coverage appear complete.
            earned += 1.5
            proxy_dimensions.append(f"underlying_asset_class:{value}")
            missing.append(f"underlying_asset_class:{value}")
        else:
            missing.append(f"underlying_asset_class:{value}")

    requested_types = set(requirements["data_types"])
    available_types = set(profile.get("data_types") or [])
    matched_type_count = 0
    for value in sorted(requested_types):
        possible += 2.0
        if _compatible_data_type(value, available_types):
            earned += 2.0
            matched.append(f"data_type:{value}")
            matched_type_count += 1
        else:
            missing.append(f"data_type:{value}")
    # Composite financial requests are normally fulfilled by several routes.
    # Missing one requested data family is therefore a coverage gap, not a hard
    # contradiction. It becomes incompatible only when it cannot substantiate
    # any requested data family; an untyped generic route is not evidence.
    if requested_types and matched_type_count == 0:
        conflicts.append(f"data_type:any_of:{'/'.join(sorted(requested_types))}")

    confidence = 1.0 if possible == 0 else max(0.0, min(1.0, earned / possible))
    if conflicts:
        level = "mismatch"
    elif possible == 0:
        level = "unconstrained"
    elif not missing and confidence >= 0.999:
        level = "exact"
    elif confidence >= 0.7:
        level = "strong"
    else:
        level = "partial"
    return {
        "level": level,
        "confidence": round(confidence, 4),
        "matched_dimensions": matched,
        "missing_dimensions": missing,
        "conflicting_dimensions": conflicts,
        "unverified_dimensions": unverified,
        "evidence_role": evidence_role,
        "proxy_dimensions": proxy_dimensions,
        "required_asset_class_tags": sorted(required_asset_tags),
        "endpoint_asset_class_tags": sorted(endpoint_asset_tags),
        "matched_asset_class_tags": sorted(matched_asset_tags),
        "conflicting_asset_class_tags": sorted(conflicting_asset_tags),
        "asset_class_compatible": asset_class_compatible,
        "region_match_mode": region_match_mode,
        "region_verification_required": region_verification_required,
        "requirements": requirements,
        "dimension_score": int(round(earned * 10)),
    }


def financial_lexical_score(dataset: Mapping[str, Any], request: Mapping[str, Any]) -> int:
    query = financial_request_text(request)
    original = {
        token
        for token in _TOKEN_RE.findall(query.lower())
        if len(token) > 1 and token not in _STOP_WORDS
    }
    expanded = set(original)
    for token in original:
        expanded.update(_TOKEN_EXPANSIONS.get(token, set()))

    dataset_id = _normalized(dataset.get("dataset_id"))
    name = _normalized(dataset.get("name"))
    description = _normalized(concise_financial_description(dataset.get("description"), limit=1_000))
    fields = _normalized(" ".join(
        f"{_text(field.get('name'))} {_text(field.get('description'))}"
        for field in dataset.get("fields") or []
        if isinstance(field, Mapping)
    ))
    dimensions = _normalized(dataset.get("financial_dimensions"))
    dataset_id_tokens = set(_TOKEN_RE.findall(dataset_id))
    name_tokens = set(_TOKEN_RE.findall(name))
    description_tokens = set(_TOKEN_RE.findall(description))
    field_tokens = set(_TOKEN_RE.findall(fields))
    dimension_tokens = set(_TOKEN_RE.findall(dimensions))
    score = 0
    for token in original:
        if token in dataset_id_tokens:
            score += 16
        if token in name_tokens:
            score += 12
        if token in field_tokens:
            score += 9
        if token in description_tokens:
            score += 6
        if token in dimension_tokens:
            score += 5
    for token in expanded - original:
        if token in dataset_id_tokens or token in name_tokens:
            score += 4
        elif token in field_tokens or token in description_tokens or token in dimension_tokens:
            score += 2
    normalized_query = _normalized(query)
    if len(normalized_query) > 5 and normalized_query in " ".join((dataset_id, name, description, fields, dimensions)):
        score += 24
    identity_types = _infer_values(" ".join((dataset_id, name)), _DATA_TYPE_PATTERNS)
    requested_types = set(infer_financial_requirements(request)["data_types"])
    score += 8 * len(identity_types & requested_types)
    if dataset.get("executable"):
        score += 1
    return score


def _inferred_fields(dimensions: Mapping[str, Any]) -> list[Dict[str, Any]]:
    data_types = set(dimensions.get("data_types") or [])
    names: list[tuple[str, str]] = []
    if "ohlcv" in data_types:
        names.extend((
            ("timestamp", "string"),
            ("open", "number"),
            ("high", "number"),
            ("low", "number"),
            ("close", "number"),
            ("volume", "number"),
        ))
    if "adjusted_price" in data_types:
        names.append(("adjusted_close", "number"))
    if "dividends" in data_types:
        names.append(("dividend_amount", "number"))
    if "splits" in data_types:
        names.append(("split_coefficient", "number"))
    if "fund_holdings" in data_types:
        names.extend((("symbol", "string"), ("weight", "number"), ("as_of_date", "string")))
    if "fund_fees" in data_types:
        names.append(("expense_ratio", "number"))
    if "sector_exposure" in data_types:
        names.extend((("sector", "string"), ("weight", "number")))
    if "fixed_income_yield" in data_types:
        names.extend((("maturity", "string"), ("yield", "number"), ("record_date", "string")))
    if "yield_curve" in data_types:
        names.extend((("maturity", "string"), ("yield", "number"), ("record_date", "string")))
    if "auction_results" in data_types:
        names.extend((("auction_date", "string"), ("security_term", "string"), ("high_yield", "number")))
    if "filings" in data_types:
        names.extend((("cik", "string"), ("form", "string"), ("filing_date", "string")))
    if "form_13f" in data_types:
        names.extend((("cik", "string"), ("form", "string"), ("filing_date", "string"), ("owner", "string")))
    if "xbrl" in data_types:
        names.extend((("taxonomy", "string"), ("concept", "string"), ("value", "number"), ("period", "string")))
    if "insider_transactions" in data_types:
        names.extend((("owner", "string"), ("transaction_date", "string")))
    if data_types.intersection({"credit", "external_debt", "policy_rate", "debt_service_ratio", "economic_indicator"}):
        names.extend((("country", "string"), ("period", "string"), ("value", "number")))
    if "policy_rate" in data_types:
        names.append(("rate", "number"))
    if "debt_service_ratio" in data_types:
        names.append(("ratio", "number"))
    if "market_cap" in data_types:
        names.append(("market_cap", "number"))
    if "supply" in data_types:
        names.append(("circulating_supply", "number"))
    if "options" in data_types:
        names.extend(
            (
                ("strike", "number"),
                ("expiration", "string"),
                ("bid", "number"),
                ("ask", "number"),
                ("volume", "number"),
            )
        )
    if "implied_volatility" in data_types:
        names.append(("implied_volatility", "number"))
    if "option_greeks" in data_types:
        names.extend((("delta", "number"), ("gamma", "number")))
    if "open_interest" in data_types:
        names.append(("open_interest", "number"))
    if "settlement" in data_types:
        names.append(("settlement_price", "number"))
    if "contract_reference" in data_types:
        names.extend((("contract", "string"), ("expiration", "string")))
    if "identifier_mapping" in data_types:
        names.extend((("identifier", "string"), ("isin", "string"), ("figi", "string"), ("symbol", "string")))
    if "exchange_reference" in data_types:
        names.extend((("exchange", "string"), ("symbol", "string")))
    unique: list[Dict[str, Any]] = []
    seen: set[str] = set()
    for name, field_type in names:
        if name in seen:
            continue
        seen.add(name)
        unique.append({
            "name": name,
            "type": field_type,
            "description": "Field inferred from the documented financial endpoint capability.",
            "required": False,
            "schema": {"type": field_type},
            "inferred": True,
        })
    return unique


def _normalized_cost(dataset: Mapping[str, Any]) -> Dict[str, Any]:
    cost = copy.deepcopy(dict(dataset.get("cost") or {}))
    operation = _normalized(dataset.get("operation") or dataset.get("dataset_id"))
    source = _source_id(dataset)
    if source in {"alpha_vantage", "alphavantage"} and operation.endswith("time series daily"):
        cost.update({
            "model": "tiered",
            "amount": 0,
            "currency": _text(cost.get("currency") or "USD"),
            "unit": _text(cost.get("unit") or "request"),
            "provider_cost": "Compact daily history supports free keys; full history requires a premium plan.",
        })
        notes = [_text(item) for item in cost.get("notes") or [] if _text(item)]
        cost["notes"] = list(dict.fromkeys([*notes, "Entitlement depends on outputsize."]))
    return cost


def enrich_financial_dataset(dataset: Mapping[str, Any], *, source_id: str = "") -> Dict[str, Any]:
    enriched = copy.deepcopy(dict(dataset))
    dimensions = infer_financial_dimensions(enriched, source_id=source_id)
    enriched["description"] = concise_financial_description(enriched.get("description")) or _text(enriched.get("description"))
    enriched["financial_dimensions"] = dimensions
    existing_tags = [
        _text(item)
        for item in enriched.get("tags") or []
        if _text(item)
        and not _text(item).lower().startswith(ASSET_CLASS_TAG_PREFIX)
    ]
    enriched["tags"] = list(dict.fromkeys([*existing_tags, *dimensions["asset_class_tags"]]))
    enriched["cost"] = _normalized_cost(enriched)
    existing_fields = [copy.deepcopy(dict(item)) for item in enriched.get("fields") or [] if isinstance(item, Mapping)]
    existing_names = {
        _normalized(item.get("canonical_name") or item.get("name")).replace(" ", "_")
        for item in existing_fields
    }
    inferred_fields = [
        item
        for item in _inferred_fields(dimensions)
        if _normalized(item.get("name")).replace(" ", "_") not in existing_names
    ]
    enriched["fields"] = [*existing_fields, *inferred_fields]
    coverage = copy.deepcopy(dict(enriched.get("coverage") or {}))
    coverage.update({
        "asset_classes": dimensions["asset_classes"],
        "regions": dimensions["regions"],
        "data_types": dimensions["data_types"],
        "frequencies": dimensions["time"]["frequencies"],
    })
    enriched["coverage"] = coverage
    freshness = copy.deepcopy(dict(enriched.get("freshness") or {}))
    freshness.update({
        "frequencies": dimensions["time"]["frequencies"],
        "timeliness": dimensions["time"]["timeliness"],
    })
    enriched["freshness"] = freshness
    return enriched


def rank_financial_datasets(
    datasets: Sequence[Mapping[str, Any]],
    request: Mapping[str, Any],
    *,
    limit: int,
) -> list[Dict[str, Any]]:
    """Return constraint-compatible datasets, falling back only when needed."""
    query = financial_request_text(request)
    requirements = infer_financial_requirements(request)
    requested_types = set(requirements.get("data_types") or [])
    requested_type_keys = {f"data_type:{value}" for value in requested_types}
    requested_asset_keys = {
        f"asset_class:{value}" for value in requirements.get("asset_classes") or []
    }
    requested_underlying_keys = {
        f"underlying_asset_class:{value}"
        for value in requirements.get("underlying_asset_classes") or []
    }
    requested_coverage_keys = requested_type_keys | requested_asset_keys | requested_underlying_keys
    independent_type_families = {
        data_type
        for data_type in requested_types
        if not (
            data_type in {"price", "volume"} and "ohlcv" in requested_types
            or data_type in {"price", "quote"} and "bid_ask" in requested_types
        )
    }
    composite_request = len(independent_type_families) > 1 or len(requested_asset_keys) > 1
    ranked: list[tuple[int, float, int, str, Dict[str, Any]]] = []
    for raw in datasets:
        if not isinstance(raw, Mapping):
            continue
        dataset = enrich_financial_dataset(raw, source_id=_source_id(raw))
        dataset_id = _text(dataset.get("dataset_id")).lower()
        if _source_id(dataset) == "yfinance" and (
            dataset_id in {"yfinance.websocket", "yfinance.asyncwebsocket"}
            or dataset_id.endswith((".close", ".subscribe", ".unsubscribe"))
        ):
            continue
        lexical = financial_lexical_score(dataset, request)
        fit = evaluate_financial_fit(dataset, request)
        factual = lexical + int(fit["dimension_score"])
        fit["lexical_score"] = lexical
        fit["factual_score"] = factual
        dataset["financial_match"] = fit
        item = (
            factual,
            float(fit["confidence"]),
            int(bool(dataset.get("executable"))),
            _text(dataset.get("dataset_id")),
            dataset,
        )
        if not fit["asset_class_compatible"]:
            continue
        if query and lexical <= 0 and not fit["matched_dimensions"]:
            continue
        if not fit["conflicting_dimensions"] and (
            fit["level"] in {"exact", "strong", "unconstrained"}
            or float(fit["confidence"]) >= 0.5
        ):
            ranked.append(item)

    exact = [item for item in ranked if item[4]["financial_match"]["level"] == "exact"]
    strong = [item for item in ranked if item[4]["financial_match"]["level"] == "strong"]
    partial = [item for item in ranked if item[4]["financial_match"]["level"] in {"partial", "unconstrained"}]
    candidates = exact or ([*strong, *partial] if composite_request else (strong or partial))
    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]), reverse=True)
    if candidates and not composite_request:
        factual_floor = candidates[0][0] - 15
        candidates = [item for item in candidates if item[0] >= factual_floor]
    bounded = max(1, int(limit or 1))
    if not composite_request:
        return [item[4] for item in candidates[:bounded]]

    # Preserve complementary routes instead of returning several variants of
    # the same highest-scoring endpoint while other requested data families go
    # uncovered.
    selected: list[tuple[int, float, int, str, Dict[str, Any]]] = []
    covered: set[str] = set()
    for item in candidates:
        matched = set(item[4]["financial_match"].get("matched_dimensions") or [])
        new_coverage = (matched & requested_coverage_keys) - covered
        if not new_coverage:
            continue
        selected.append(item)
        covered.update(new_coverage)
        if len(selected) >= bounded or covered >= requested_coverage_keys:
            break
    for item in candidates:
        if len(selected) >= bounded:
            break
        if item not in selected:
            selected.append(item)
    return [item[4] for item in selected]


__all__ = [
    "FINANCIAL_DIMENSIONS_VERSION",
    "concise_financial_description",
    "enrich_financial_dataset",
    "evaluate_financial_fit",
    "financial_lexical_score",
    "financial_request_text",
    "infer_financial_dimensions",
    "infer_financial_requirements",
    "rank_financial_datasets",
]
