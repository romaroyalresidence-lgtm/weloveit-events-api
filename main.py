# main.py — WELOVEIT Events API v36
# V36 = V34 funzionante + V35 hardening layer:
# - no httpx: uses stdlib urllib + FastAPI
# - hard final date filter after merge
# - Roma/Rome alias parity
# - Italian Open ranking boost only when event is actually in requested date range
# - Search Discovery does NOT invent dates anymore
# - Search Discovery rejects wrong-year results, e.g. 2027 when searching 2026
# - filters airport-delay, parking, classes, fake/low-quality discovery results
# - source priority + stronger dedupe

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

VERSION = "ticketmaster-seatgeek-predicthq-football-eventbrite-serpapi-v36-v34-core-v35-hardening"

TICKETMASTER_API_KEY = os.getenv("TICKETMASTER_API_KEY", "").strip()
PREDICT_API_KEY = os.getenv("PREDICT_API_KEY", "").strip()
PREDICT_API_URL = os.getenv("PREDICT_API_URL", "").strip()
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY", "").strip()
EVENTBRITE_API_KEY = os.getenv("EVENTBRITE_API_KEY", "").strip()
SEATGEEK_CLIENT_ID = os.getenv("SEATGEEK_CLIENT_ID", "").strip()
SEATGEEK_CLIENT_SECRET = os.getenv("SEATGEEK_CLIENT_SECRET", "").strip()
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "").strip()

DEFAULT_TIMEOUT = 12
MAX_PROVIDER_EVENTS = 80
MAX_FINAL_EVENTS = 70

ITALIAN_OPEN_2026_START = "2026-05-06"
ITALIAN_OPEN_2026_END = "2026-05-18"

# V35/V36 hardening config
MIN_DATE_CONFIDENCE = float(os.getenv("WELOVEIT_MIN_DATE_CONFIDENCE", "0.80"))
DISCOVERY_CACHE_TTL_SECONDS = int(os.getenv("WELOVEIT_DISCOVERY_CACHE_TTL", "900"))
SNAPSHOT_DIR = Path(os.getenv("WELOVEIT_EVENT_SNAPSHOT_DIR", "./snapshots/events"))
ENABLE_EVENTS_CACHE = os.getenv("WELOVEIT_ENABLE_EVENTS_CACHE", "1").strip().lower() not in {"0", "false", "no"}
ENABLE_DIAGNOSTICS_LOG = os.getenv("WELOVEIT_ENABLE_DIAGNOSTICS_LOG", "1").strip().lower() not in {"0", "false", "no"}

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("weloveit.events.v36")
_EVENTS_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]], Dict[str, Any]]] = {}

app = FastAPI(title="WELOVEIT Events API", version=VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

COUNTRY_ALIASES = {
    "italy": "IT", "italia": "IT", "it": "IT",
    "great britain": "GB", "uk": "GB", "united kingdom": "GB", "gb": "GB", "england": "GB",
    "usa": "US", "us": "US", "united states": "US", "america": "US",
    "japan": "JP", "giappone": "JP", "jp": "JP",
    "france": "FR", "francia": "FR", "fr": "FR",
    "spain": "ES", "spagna": "ES", "es": "ES",
    "germany": "DE", "germania": "DE", "de": "DE",
    "canada": "CA", "ca": "CA",
    "brazil": "BR", "brasile": "BR", "br": "BR",
    "argentina": "AR", "ar": "AR",
    "china": "CN", "cina": "CN", "cn": "CN",
}

CITY_ALIASES = {
    "rome": ("Roma", "IT"), "roma": ("Roma", "IT"), "roma capitale": ("Roma", "IT"), "rom": ("Roma", "IT"),
    "milan": ("Milano", "IT"), "milano": ("Milano", "IT"),
    "florence": ("Firenze", "IT"), "firenze": ("Firenze", "IT"),
    "venice": ("Venezia", "IT"), "venezia": ("Venezia", "IT"),
    "naples": ("Napoli", "IT"), "napoli": ("Napoli", "IT"),
    "turin": ("Torino", "IT"), "torino": ("Torino", "IT"),
    "london": ("London", "GB"), "londra": ("London", "GB"),
    "tokyo": ("Tokyo", "JP"),
    "new york": ("New York", "US"), "nyc": ("New York", "US"),
    "paris": ("Paris", "FR"),
    "madrid": ("Madrid", "ES"),
    "barcelona": ("Barcelona", "ES"),
    "berlin": ("Berlin", "DE"),
    "toronto": ("Toronto", "CA"),
    "montreal": ("Montreal", "CA"),
    "sao paulo": ("São Paulo", "BR"), "san paolo": ("São Paulo", "BR"),
    "rio": ("Rio de Janeiro", "BR"), "rio de janeiro": ("Rio de Janeiro", "BR"),
    "buenos aires": ("Buenos Aires", "AR"),
    "beijing": ("Beijing", "CN"), "pechino": ("Beijing", "CN"),
    "shanghai": ("Shanghai", "CN"),
}

COUNTRY_NAMES = {
    "IT": "Italy",
    "GB": "United Kingdom",
    "US": "United States",
    "JP": "Japan",
    "FR": "France",
    "ES": "Spain",
    "DE": "Germany",
    "CA": "Canada",
    "BR": "Brazil",
    "AR": "Argentina",
    "CN": "China",
}

TICKETMASTER_COUNTRY_SEGMENT = {
    "sport": "Sports",
    "sports": "Sports",
    "concert": "Music",
    "concerts": "Music",
    "music": "Music",
    "festival": "Music",
    "culture": "Arts & Theatre",
    "theatre": "Arts & Theatre",
    "family": "Family",
}

SEATGEEK_TYPES = {
    "concert": "concert",
    "concerts": "concert",
    "music": "concert",
    "sport": "sports",
    "sports": "sports",
    "football": "sports",
    "soccer": "sports",
    "basketball": "sports",
    "tennis": "sports",
}

BAD_TITLE_PATTERNS = [
    r"\bparking\b",
    r"\bpark(?:ing)? pass\b",
    r"\bairport\b",
    r"\bdelays?\b",
    r"\bflight\b",
    r"\bweather warning\b",
    r"\btraffic\b",
    r"\bself[- ]?defen[cs]e\b",
    r"\bboxing class\b",
    r"\bclasses\b",
    r"\bsession\b",
    r"\bworkout\b",
    r"\bfitness\b",
    r"\btraining\b",
    r"\bfanpark\b",
    r"\bfan zone\b",
    r"\bfanzone\b",
]

LOW_QUALITY_SOURCE_PATTERNS = [
    "google.com/maps",
    "maps/vt",
]

TENNIS_ROMA_KEYWORDS = [
    "internazionali",
    "bnl",
    "italian open",
    "foro italico",
    "atp rome",
    "wta rome",
    "tennis roma",
    "tennis rome",
]

MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def today_iso() -> str:
    return date.today().isoformat()


def parse_date_safe(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    s = str(value).strip()[:10]
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def parse_dt_safe(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19], fmt)
        except Exception:
            continue
    return None


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def slug(value: Any) -> str:
    s = clean_text(value).lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9àèéìòóùäöüßçñ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def http_get_json(
    url: str,
    params: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[bool, int, Dict[str, Any], str]:
    query = urllib.parse.urlencode(
        {k: v for k, v in params.items() if v is not None and v != ""},
        doseq=True,
    )
    full_url = url + ("?" + query if query else "")
    req = urllib.request.Request(full_url, headers=headers or {"User-Agent": "WELOVEIT/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return True, int(resp.status), json.loads(raw or "{}"), full_url
    except Exception as exc:
        return False, 0, {"error": str(exc)}, full_url


def normalize_location(city: str = "", country: str = "") -> Dict[str, str]:
    raw_city = clean_text(city)
    raw_country = clean_text(country)
    city_key = slug(raw_city)
    country_key = slug(raw_country)

    normalized_city = raw_city.title() if raw_city else ""
    country_code = COUNTRY_ALIASES.get(
        country_key,
        raw_country.upper() if len(raw_country) == 2 else "",
    )

    if city_key in CITY_ALIASES:
        normalized_city, alias_country = CITY_ALIASES[city_key]
        if not country_code:
            country_code = alias_country

    italian_city_keys = {
        "roma", "rome", "milano", "milan", "firenze", "florence",
        "venezia", "venice", "napoli", "naples", "torino", "turin",
    }
    if not country_code and city_key in italian_city_keys:
        country_code = "IT"

    return {
        "city": normalized_city,
        "country_code": country_code or raw_country.upper(),
        "country_name": COUNTRY_NAMES.get(
            country_code or raw_country.upper(),
            raw_country.title() if raw_country else "",
        ),
    }


def is_rome(location: Dict[str, str]) -> bool:
    return slug(location.get("city")) in {"roma", "rome"} and location.get("country_code") == "IT"


def normalize_category(category: str = "") -> str:
    c = slug(category)
    if c in {"sports", "sport", "sporting"}:
        return "sport"
    if c in {"concerts", "concert", "music", "musica"}:
        return "concert"
    if c in {"theatre", "theater", "culture", "cultura", "arts"}:
        return "culture"
    if c in {"tennis"}:
        return "sport"
    if c in {"all", "tutte", "tutti", "any"}:
        return ""
    return c


def category_from_title(title: str, fallback: str = "") -> Tuple[str, str]:
    s = slug(title)

    if any(k in s for k in ["internazionali", "italian open", "foro italico", "atp rome", "wta rome", "tennis"]):
        return "sport", "Tennis"
    if any(k in s for k in ["rugby", "premiership", "nations championship", "twickenham", "saracens", "harlequins"]):
        return "sport", "Rugby Union"
    if any(k in s for k in ["boxing", "fight night", "boxe"]):
        return "sport", "Boxing"
    if any(k in s for k in ["nfl", "american football", "colts", "commanders", "jaguars", "eagles"]):
        return "sport", "NFL"
    if any(k in s for k in ["wwe", "aew", "wrestling"]):
        return "sport", "Wrestling"
    if any(k in s for k in ["basketball", "slb", "lions"]):
        return "sport", "Basketball"
    if any(k in s for k in ["motogp", "moto gp"]):
        return "motorsport", "MotoGP"
    if any(k in s for k in ["formula 1", "f1 grand prix"]):
        return "motorsport", "Formula 1"
    if any(k in s for k in ["football", "soccer", "fa cup", "championship", "serie a", "lazio", "roma v", "as roma"]):
        return "sport", "Football"
    if any(k in s for k in ["concert", "live tour", "festival", "dj", "opera", "tenors", "symphony"]):
        return "concert", "Concerts"
    if any(k in s for k in ["expo", "fair", "summit", "conference"]):
        return "culture", "Expos"

    if fallback:
        if fallback in {"sports", "sport"}:
            return "sport", "Sport"
        if fallback in {"concerts", "concert", "music"}:
            return "concert", "Concerts"
        return fallback, fallback.title()

    return "culture", "Event"


def eventbrite_search_url(city: str, country_code: str, title: str) -> str:
    loc = f"{country_code.lower()}--{slug(city).replace(' ', '-')}" if country_code else slug(city).replace(" ", "-")
    return "https://www.eventbrite.com/d/" + loc + "/?q=" + urllib.parse.quote_plus(title)


def official_google_ticket_url(title: str, city: str, country_code: str, start_date: str, subcategory: str) -> str:
    q = f"{title} {subcategory} {city} {country_code} {start_date} official tickets"
    return "https://www.google.com/search?q=" + urllib.parse.quote_plus(q)


def make_event(
    *,
    title: str,
    category: str = "",
    subcategory: str = "",
    start_date: str,
    start_time: Optional[str] = None,
    city: str,
    country: str,
    venue: str = "",
    source_name: str,
    source_url: Optional[str] = None,
    ticket_url: Optional[str] = None,
    image_url: Optional[str] = None,
    price_min: Any = None,
    price_max: Any = None,
    currency: Optional[str] = None,
    status: str = "active",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not category or not subcategory:
        cat2, sub2 = category_from_title(title, category)
        category = category or cat2
        subcategory = subcategory or sub2

    now = datetime.utcnow().isoformat() + "+00:00"

    ev = {
        "title": clean_text(title),
        "category": category,
        "subcategory": subcategory,
        "start_date": start_date,
        "start_time": start_time,
        "city": clean_text(city),
        "country": clean_text(country),
        "venue": clean_text(venue),
        "source_name": source_name,
        "source_url": source_url,
        "ticket_url": ticket_url or source_url or official_google_ticket_url(title, city, country, start_date, subcategory),
        "image_url": image_url,
        "price_min": price_min,
        "price_max": price_max,
        "currency": currency,
        "is_vip_available": False,
        "status": status,
        "created_at": now,
        "updated_at": now,
        "eventbrite_search_url": eventbrite_search_url(city, country, title),
        "ai_score": 70,
        "quality_score": 70,
        "date_confidence": 0.98,
        "result_year_conflicts": False,
        "is_low_quality_conference": False,
    }

    if extra:
        ev.update(extra)

    return ev


def title_is_bad(title: str, source_url: Optional[str] = None, category: str = "") -> bool:
    s = slug(title)
    u = str(source_url or "").lower()

    if any(p in u for p in LOW_QUALITY_SOURCE_PATTERNS):
        return True

    for pat in BAD_TITLE_PATTERNS:
        if re.search(pat, s, re.I):
            if "fight night" in s or "championship boxing" in s:
                continue
            if category == "sport" and any(k in s for k in ["final", "championship", "cup", "v ", " vs ", "grand prix", "open"]):
                continue
            return True

    too_generic = {
        "home",
        "info",
        "info e biglietti",
        "tickets",
        "biglietti",
        "official website",
        "homepage",
    }
    if s in too_generic:
        return True

    return False


def is_within_dates(ev: Dict[str, Any], from_date: str, to_date: str) -> bool:
    d = parse_date_safe(ev.get("start_date"))
    fd = parse_date_safe(from_date)
    td = parse_date_safe(to_date)

    if not d:
        return False
    if fd and d < fd:
        return False
    if td and d > td:
        return False
    return True


def city_matches(ev: Dict[str, Any], location: Dict[str, str]) -> bool:
    ev_city = slug(ev.get("city"))
    target = slug(location.get("city"))
    ev_country = clean_text(ev.get("country")).upper()
    target_country = location.get("country_code", "").upper()

    country_ok = not target_country or ev_country in {
        target_country,
        COUNTRY_NAMES.get(target_country, "").upper(),
        "GREAT BRITAIN" if target_country == "GB" else "",
    }

    if target_country == "IT" and ev_country in {"ITALY", "IT"}:
        country_ok = True

    if target in {"roma", "rome"}:
        city_ok = ev_city in {"roma", "rome"} or "roma" in ev_city or "rome" in ev_city
    else:
        city_ok = (ev_city == target) or (target and target in ev_city) or (ev_city and ev_city in target)

    return bool(city_ok and country_ok)


def result_year_conflicts(text: str, from_date: str, to_date: str) -> bool:
    years = set(re.findall(r"\b20\d{2}\b", text or ""))
    if not years:
        return False

    fd = parse_date_safe(from_date) or date.today()
    td = parse_date_safe(to_date) or fd
    allowed_years = {str(fd.year), str(td.year)}

    return not bool(years & allowed_years)


def source_weight(source_name: str) -> int:
    s = slug(source_name)
    if "ticketmaster" in s:
        return 45
    if "seatgeek" in s:
        return 38
    if "search discovery" in s:
        return 34
    if "serpapi" in s or "sports expansion" in s:
        return 30
    if "predicthq" in s:
        return 20
    if "fallback" in s:
        return 5
    return 10


def compute_score(ev: Dict[str, Any], location: Dict[str, str], requested_category: str) -> int:
    score = source_weight(ev.get("source_name", ""))

    title = slug(ev.get("title"))
    venue = slug(ev.get("venue"))
    sub = slug(ev.get("subcategory"))

    if ev.get("image_url"):
        score += 8
    if ev.get("ticket_url") and "google.com/search" not in str(ev.get("ticket_url")):
        score += 8
    if city_matches(ev, location):
        score += 15

    if requested_category == "sport" and ev.get("category") in {"sport", "motorsport"}:
        score += 15
    if requested_category and requested_category != "sport" and ev.get("category") == requested_category:
        score += 12

    if any(k in title for k in ["final", "open", "championship", "cup", "grand prix", "internazionali"]):
        score += 10

    if "tennis" in sub or any(k in title or k in venue for k in TENNIS_ROMA_KEYWORDS):
        score += 14

    if is_rome(location) and any(k in title or k in venue for k in TENNIS_ROMA_KEYWORDS):
        score += 10

    if title_is_bad(ev.get("title", ""), ev.get("source_url"), ev.get("category", "")):
        score -= 40

    if "predicthq" in slug(ev.get("source_name")) and not ev.get("source_url"):
        score -= 8

    if ev.get("search_date_confidence") is False:
        score -= 30

    return max(1, min(99, score))


def dedupe_key(ev: Dict[str, Any]) -> str:
    title = slug(ev.get("title"))
    title = re.sub(r"\bvenue premium tickets\b", "", title)
    title = re.sub(r"\bregister interest\b", "", title)
    title = re.sub(r"\bhome\b", "", title)
    title = re.sub(r"\binfo e biglietti\b", "", title)
    title = re.sub(r"\s+", " ", title).strip()

    if any(k in title for k in ["internazionali bnl", "italian open"]):
        title = "internazionali bnl italia italian open rome"

    d = ev.get("start_date") or ""
    venue = slug(ev.get("venue"))

    return f"{title[:70]}|{d}|{venue[:40]}"


def merge_events(
    events: List[Dict[str, Any]],
    location: Dict[str, str],
    requested_category: str,
    from_date: str,
    to_date: str,
    diagnostics: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    V36 merge:
    - keeps V34 source priority + dedupe
    - adds V35 hardening validation
    - records discard reasons
    - refuses fake/low-confidence/wrong-year dates
    """
    cleaned: List[Dict[str, Any]] = []
    discard_reasons: Dict[str, int] = {}

    for ev in events:
        ok, reason = validate_event_v36(ev, location, requested_category, from_date, to_date)
        if not ok:
            discard_reasons[reason] = discard_reasons.get(reason, 0) + 1
            ev["discarded_reason"] = reason
            log_event_v36(ev, accepted=False, reason=reason)
            continue

        ev["discarded_reason"] = None
        ev["ai_score"] = compute_score(ev, location, requested_category)
        ev["quality_score"] = ev["ai_score"]
        log_event_v36(ev, accepted=True, reason="accepted")
        cleaned.append(ev)

    best: Dict[str, Dict[str, Any]] = {}

    for ev in cleaned:
        k = dedupe_key(ev)
        old = best.get(k)

        if not old or compute_score(ev, location, requested_category) > compute_score(old, location, requested_category):
            if old:
                if not ev.get("image_url") and old.get("image_url"):
                    ev["image_url"] = old["image_url"]
                if (not ev.get("ticket_url") or "google.com/search" in str(ev.get("ticket_url"))) and old.get("ticket_url"):
                    ev["ticket_url"] = old["ticket_url"]
                ev["merged_sources"] = sorted(list(set(
                    (old.get("merged_sources") or [old.get("source_name")]) +
                    (ev.get("merged_sources") or [ev.get("source_name")])
                )))
            else:
                ev["merged_sources"] = sorted(list(set(ev.get("merged_sources") or [ev.get("source_name")])))

            best[k] = ev

    result = list(best.values())
    result.sort(
        key=lambda e: (
            -(e.get("ai_score") or 0),
            e.get("start_date") or "9999-99-99",
            e.get("start_time") or "99:99:99",
        )
    )

    if diagnostics is not None:
        diagnostics["discard_reasons"] = discard_reasons
        diagnostics["accepted_before_dedupe"] = len(cleaned)
        diagnostics["deduped_count"] = len(result)

    return result[:MAX_FINAL_EVENTS]



def ticketmaster_events(location: Dict[str, str], from_date: str, to_date: str, category: str) -> List[Dict[str, Any]]:
    if not TICKETMASTER_API_KEY:
        return []

    params = {
        "apikey": TICKETMASTER_API_KEY,
        "city": location["city"],
        "countryCode": location["country_code"],
        "startDateTime": f"{from_date}T00:00:00Z",
        "endDateTime": f"{to_date}T23:59:59Z",
        "size": MAX_PROVIDER_EVENTS,
        "sort": "date,asc",
    }

    segment = TICKETMASTER_COUNTRY_SEGMENT.get(category)
    if segment:
        params["segmentName"] = segment

    ok, status, data, _ = http_get_json("https://app.ticketmaster.com/discovery/v2/events.json", params)
    if not ok or status != 200:
        return []

    out: List[Dict[str, Any]] = []

    for item in data.get("_embedded", {}).get("events", []) or []:
        dates = item.get("dates", {}).get("start", {})
        start_date = dates.get("localDate")
        if not start_date:
            continue

        venue_item = ((item.get("_embedded") or {}).get("venues") or [{}])[0]
        classifications = item.get("classifications") or [{}]
        class0 = classifications[0] if classifications else {}

        segment_name = (class0.get("segment") or {}).get("name") or ""
        genre_name = (class0.get("genre") or {}).get("name") or ""
        title = item.get("name") or ""

        cat, sub = category_from_title(title, "")

        if segment_name.lower() == "sports":
            cat, sub = "sport", genre_name or sub
        elif segment_name.lower() == "music":
            cat, sub = "concert", genre_name or "Concerts"
        elif segment_name:
            cat, sub = "culture", genre_name or segment_name

        images = item.get("images") or []
        image_url = sorted(images, key=lambda x: (x.get("width", 0) or 0), reverse=True)[0].get("url") if images else None

        price_ranges = item.get("priceRanges") or []
        price0 = price_ranges[0] if price_ranges else {}

        out.append(make_event(
            title=title,
            category=cat,
            subcategory=sub,
            start_date=start_date,
            start_time=dates.get("localTime"),
            city=venue_item.get("city", {}).get("name") or location["city"],
            country=venue_item.get("country", {}).get("countryCode") or location["country_code"],
            venue=venue_item.get("name") or "",
            source_name="Ticketmaster",
            source_url=item.get("url"),
            ticket_url=item.get("url"),
            image_url=image_url,
            price_min=price0.get("min"),
            price_max=price0.get("max"),
            currency=price0.get("currency"),
        ))

    return out


def seatgeek_events(location: Dict[str, str], from_date: str, to_date: str, category: str) -> List[Dict[str, Any]]:
    if not SEATGEEK_CLIENT_ID:
        return []

    params = {
        "client_id": SEATGEEK_CLIENT_ID,
        "per_page": min(MAX_PROVIDER_EVENTS, 50),
        "sort": "datetime_local.asc",
        "venue.city": location["city"],
        "datetime_local.gte": f"{from_date}T00:00:00",
        "datetime_local.lte": f"{to_date}T23:59:59",
    }

    sg_type = SEATGEEK_TYPES.get(category)
    if sg_type:
        params["type"] = sg_type

    ok, status, data, _ = http_get_json("https://api.seatgeek.com/2/events", params)
    if not ok or status != 200:
        return []

    out: List[Dict[str, Any]] = []

    for item in data.get("events", []) or []:
        dt = parse_dt_safe(item.get("datetime_local"))
        if not dt:
            continue

        venue = item.get("venue") or {}
        title = item.get("title") or ""

        cat, sub = category_from_title(title, "")
        if item.get("type") == "concert":
            cat, sub = "concert", "Concerts"
        elif "sports" in str(item.get("type", "")):
            cat = "sport"

        performers = item.get("performers") or []
        image_url = performers[0].get("image") if performers else None

        out.append(make_event(
            title=title,
            category=cat,
            subcategory=sub,
            start_date=dt.date().isoformat(),
            start_time=dt.time().isoformat(timespec="seconds") if dt.time() else None,
            city=venue.get("city") or location["city"],
            country=venue.get("country") or location["country_code"],
            venue=venue.get("name") or "",
            source_name="SeatGeek",
            source_url=item.get("url"),
            ticket_url=item.get("url"),
            image_url=image_url,
        ))

    return out


def predict_events(location: Dict[str, str], from_date: str, to_date: str, category: str) -> List[Dict[str, Any]]:
    if not PREDICT_API_KEY and not PREDICT_API_URL:
        return []

    url = PREDICT_API_URL or "https://api.predicthq.com/v1/events/"
    headers = {"User-Agent": "WELOVEIT/1.0"}

    if PREDICT_API_KEY:
        headers["Authorization"] = f"Bearer {PREDICT_API_KEY}"

    phq_category = None
    if category == "sport":
        phq_category = "sports"
    elif category == "concert":
        phq_category = "concerts,festivals"
    elif category == "culture":
        phq_category = "expos,performing-arts,community"

    params = {
        "q": location["city"],
        "country": location["country_code"],
        "active.gte": from_date,
        "active.lte": to_date,
        "limit": MAX_PROVIDER_EVENTS,
    }

    if phq_category:
        params["category"] = phq_category

    ok, status, data, _ = http_get_json(url, params, headers=headers)
    if not ok or status not in {200, 201}:
        return []

    raw = data if isinstance(data, list) else data.get("results") or data.get("events") or []
    out: List[Dict[str, Any]] = []

    for item in raw:
        title = item.get("title") or item.get("name") or ""
        start_dt = parse_dt_safe(item.get("start") or item.get("start_date") or item.get("first_seen"))
        if not start_dt:
            continue

        venue = ""
        for ent in item.get("entities") or []:
            if ent.get("type") in {"venue", "event-group"}:
                venue = ent.get("name") or venue

        cat = item.get("category") or category or ""
        sub = item.get("phq_subcategory") or item.get("subcategory") or ""

        cat2, sub2 = category_from_title(title, cat)
        if cat in {"sports", "sport"}:
            cat2 = "sport"
        elif cat in {"concerts", "concert"}:
            cat2 = "concert"

        out.append(make_event(
            title=title,
            category=cat2,
            subcategory=sub or sub2,
            start_date=start_dt.date().isoformat(),
            start_time=start_dt.time().isoformat(timespec="seconds") if start_dt.time() else None,
            city=item.get("geo", {}).get("address", {}).get("locality") or item.get("city") or location["city"],
            country=item.get("country") or location["country_code"],
            venue=venue or item.get("venue") or "",
            source_name="PredictHQ",
            source_url=item.get("url"),
            ticket_url=item.get("url"),
            image_url=None,
            extra={
                "rank": item.get("rank"),
                "sport_type": item.get("sport_type"),
            },
        ))

    return out


def serpapi_queries(location: Dict[str, str], from_date: str, to_date: str, category: str) -> List[str]:
    city = location["city"]
    country_name = location["country_name"] or location["country_code"]
    year = from_date[:4] if from_date else str(date.today().year)

    if is_rome(location) and category == "sport":
        return [
            f"sport events Roma {year} tickets",
            f"Roma sport eventi {year} biglietti",
            f"tennis Foro Italico Roma {year}",
            f"Internazionali BNL d'Italia {year}",
            f"Italian Open Rome {year} tickets",
            f"ATP Rome {year} Foro Italico",
            f"WTA Rome {year} Foro Italico",
        ]

    if category == "sport":
        return [
            f"sports events {city} {year} tickets",
            f"football {city} {year} tickets",
            f"tennis {city} {year} tickets",
            f"rugby {city} {year} tickets",
            f"boxing {city} {year} tickets",
            f"basketball {city} {year} tickets",
            f"NFL {city} {year}",
            f"MotoGP {city} {year} tickets",
            f"Formula 1 {city} {year} tickets",
        ]

    if category == "concert":
        return [
            f"concerts {city} {country_name} {year}",
            f"music events {city} {country_name} {year}",
            f"live music {city} {year} tickets",
        ]

    return [
        f"events {city} {country_name} from {from_date} to {to_date}",
        f"{city} events {year} official tickets",
        f"concerts festivals exhibitions sport {city} {year}",
    ]


def parse_serpapi_date(date_obj: Dict[str, Any], from_date: str, to_date: str) -> Tuple[Optional[str], Optional[str]]:
    raw = clean_text((date_obj or {}).get("when") or (date_obj or {}).get("start_date") or "")
    start = clean_text((date_obj or {}).get("start_date") or "")
    year = int((from_date or today_iso())[:4])

    text = raw or start

    m = re.search(
        r"\b(Jan|January|Feb|February|Mar|March|Apr|April|May|Jun|June|Jul|July|Aug|August|Sep|Sept|September|Oct|October|Nov|November|Dec|December)\s+(\d{1,2})\b",
        text,
        re.I,
    )
    if m:
        month = MONTHS[m.group(1).lower()]
        day = int(m.group(2))
    else:
        m = re.search(
            r"\b(\d{1,2})\s+(Jan|January|Feb|February|Mar|March|Apr|April|May|Jun|June|Jul|July|Aug|August|Sep|Sept|September|Oct|October|Nov|November|Dec|December)\b",
            text,
            re.I,
        )
        if not m:
            return None, None
        day = int(m.group(1))
        month = MONTHS[m.group(2).lower()]

    d = date(year, month, day)
    fd = parse_date_safe(from_date)

    if fd and d < fd and fd.month > month:
        d = date(year + 1, month, day)

    time_match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text)
    t = f"{int(time_match.group(1)):02d}:{time_match.group(2)}:00" if time_match else None

    return d.isoformat(), t


def serpapi_events(location: Dict[str, str], from_date: str, to_date: str, category: str) -> List[Dict[str, Any]]:
    if not SERPAPI_API_KEY:
        return []

    out: List[Dict[str, Any]] = []

    for q in serpapi_queries(location, from_date, to_date, category):
        params = {
            "engine": "google_events",
            "q": q,
            "api_key": SERPAPI_API_KEY,
            "hl": "en",
            "gl": location["country_code"].lower() if location.get("country_code") else "us",
        }

        ok, status, data, _ = http_get_json("https://serpapi.com/search.json", params)
        if not ok or status != 200:
            continue

        for item in data.get("events_results", []) or []:
            title = item.get("title") or ""

            combined = json.dumps(item, ensure_ascii=False)
            if result_year_conflicts(combined, from_date, to_date):
                continue

            d, t = parse_serpapi_date(item.get("date") or {}, from_date, to_date)
            if not d:
                continue

            address = item.get("address") or []
            venue = ""

            if address:
                venue = clean_text(str(address[0]).split(",")[0])
                full_address = " ".join(address)

                if is_rome(location) and any(k in slug(q) for k in ["italian open", "internazionali", "foro italico", "tennis"]):
                    if not re.search(r"\b(roma|rome|foro italico)\b", full_address, re.I):
                        continue

            cat, sub = category_from_title(title, category)
            source_name = "Sports Expansion" if category == "sport" else "SerpApi"

            out.append(make_event(
                title=title,
                category=cat,
                subcategory=sub,
                start_date=d,
                start_time=t,
                city=location["city"],
                country=location["country_code"],
                venue=venue,
                source_name=source_name,
                source_url=item.get("link"),
                ticket_url=item.get("link"),
                image_url=item.get("thumbnail") if isinstance(item.get("thumbnail"), str) else None,
                extra={"sports_expansion_query": q} if source_name == "Sports Expansion" else {"serpapi_query": q},
            ))

        time.sleep(0.05)

    return out


def search_discovery_queries(location: Dict[str, str], from_date: str, to_date: str, category: str) -> List[str]:
    city = location["city"]
    country_name = location["country_name"] or location["country_code"]
    year = str((parse_date_safe(from_date) or date.today()).year)

    city_variants = [city]
    if is_rome(location):
        city_variants = ["Roma", "Rome"]

    base_terms = []

    if category == "sport":
        base_terms = [
            "official tickets sport events",
            "official tickets football rugby tennis boxing basketball",
            "official schedule tickets",
        ]
    elif category == "concert":
        base_terms = [
            "official tickets concerts",
            "live music tickets",
            "festival tickets",
        ]
    elif category == "culture":
        base_terms = [
            "official tickets exhibitions theatre events",
            "museums expo fair tickets",
        ]
    else:
        base_terms = [
            "official tickets events",
            "concert sport theatre exhibition tickets",
        ]

    if is_rome(location):
        base_terms.extend([
            f"Internazionali BNL d'Italia {year} official tickets",
            f"Italian Open Rome {year} Foro Italico official tickets",
        ])

    queries: List[str] = []
    for c in city_variants:
        for term in base_terms:
            if c.lower() in term.lower():
                queries.append(term)
            else:
                queries.append(f"{term} {c} {country_name} {year}")

    return list(dict.fromkeys(queries))[:8]


def parse_discovery_date_from_text(text: str, from_date: str, to_date: str) -> Tuple[Optional[str], Optional[str], bool]:
    """
    Returns: date ISO, time string, confidence.
    V34 rule: no guessed dates. If no explicit date is found, return None.
    """
    if not text:
        return None, None, False

    fd = parse_date_safe(from_date) or date.today()
    td = parse_date_safe(to_date) or fd

    iso_match = re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", text)
    if iso_match:
        y, m, d = map(int, iso_match.groups())
        try:
            parsed = date(y, m, d)
            return parsed.isoformat(), None, True
        except Exception:
            pass

    euro_match = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](20\d{2})\b", text)
    if euro_match:
        d, m, y = map(int, euro_match.groups())
        try:
            parsed = date(y, m, d)
            return parsed.isoformat(), None, True
        except Exception:
            pass

    month_name = (
        r"Jan|January|Feb|February|Mar|March|Apr|April|May|Jun|June|Jul|July|"
        r"Aug|August|Sep|Sept|September|Oct|October|Nov|November|Dec|December"
    )

    m1 = re.search(rf"\b({month_name})\s+(\d{{1,2}})(?:,?\s+(20\d{{2}}))?\b", text, re.I)
    if m1:
        month = MONTHS[m1.group(1).lower()]
        day = int(m1.group(2))
        year = int(m1.group(3)) if m1.group(3) else fd.year
        try:
            parsed = date(year, month, day)
            if parsed < fd and not m1.group(3):
                parsed = date(fd.year + 1, month, day)
            return parsed.isoformat(), None, True
        except Exception:
            pass

    m2 = re.search(rf"\b(\d{{1,2}})\s+({month_name})(?:\s+(20\d{{2}}))?\b", text, re.I)
    if m2:
        day = int(m2.group(1))
        month = MONTHS[m2.group(2).lower()]
        year = int(m2.group(3)) if m2.group(3) else fd.year
        try:
            parsed = date(year, month, day)
            if parsed < fd and not m2.group(3):
                parsed = date(fd.year + 1, month, day)
            return parsed.isoformat(), None, True
        except Exception:
            pass

    italian_months = {
        "gennaio": 1,
        "febbraio": 2,
        "marzo": 3,
        "aprile": 4,
        "maggio": 5,
        "giugno": 6,
        "luglio": 7,
        "agosto": 8,
        "settembre": 9,
        "ottobre": 10,
        "novembre": 11,
        "dicembre": 12,
    }

    m3 = re.search(
        r"\b(\d{1,2})\s+(gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre)(?:\s+(20\d{2}))?\b",
        text,
        re.I,
    )
    if m3:
        day = int(m3.group(1))
        month = italian_months[m3.group(2).lower()]
        year = int(m3.group(3)) if m3.group(3) else fd.year
        try:
            parsed = date(year, month, day)
            if parsed < fd and not m3.group(3):
                parsed = date(fd.year + 1, month, day)
            return parsed.isoformat(), None, True
        except Exception:
            pass

    return None, None, False


def search_discovery_events(location: Dict[str, str], from_date: str, to_date: str, category: str) -> List[Dict[str, Any]]:
    """
    Uses SerpApi Google Search as a discovery layer.
    V34 important rules:
    - never invent date
    - reject year conflicts
    - reject generic pages
    - only include results inside final requested range
    """
    if not SERPAPI_API_KEY:
        return []

    out: List[Dict[str, Any]] = []

    for q in search_discovery_queries(location, from_date, to_date, category):
        params = {
            "engine": "google",
            "q": q,
            "api_key": SERPAPI_API_KEY,
            "hl": "en",
            "gl": location["country_code"].lower() if location.get("country_code") else "us",
            "num": 10,
        }

        ok, status, data, _ = http_get_json("https://serpapi.com/search.json", params)
        if not ok or status != 200:
            continue

        organic = data.get("organic_results") or []

        for item in organic:
            title = clean_text(item.get("title") or "")
            snippet = clean_text(item.get("snippet") or "")
            link = item.get("link")
            displayed_link = clean_text(item.get("displayed_link") or "")

            if not title or not link:
                continue

            combined = " ".join([title, snippet, displayed_link, q])

            if result_year_conflicts(combined, from_date, to_date):
                continue

            if title_is_bad(title, link, category):
                continue

            d, t, confidence = parse_discovery_date_from_text(combined, from_date, to_date)

            if not d or not confidence:
                continue

            temp_ev = {
                "title": title,
                "start_date": d,
                "city": location["city"],
                "country": location["country_code"],
                "source_url": link,
                "category": category,
            }

            if not is_within_dates(temp_ev, from_date, to_date):
                continue

            if is_rome(location):
                location_text = slug(combined)
                if not any(x in location_text for x in ["roma", "rome", "foro italico", "stadio olimpico"]):
                    continue

            cat, sub = category_from_title(title, category)

            venue = ""
            if is_rome(location) and any(k in slug(combined) for k in TENNIS_ROMA_KEYWORDS):
                venue = "Foro Italico"

            out.append(make_event(
                title=title,
                category=cat,
                subcategory=sub,
                start_date=d,
                start_time=t,
                city=location["city"],
                country=location["country_code"],
                venue=venue,
                source_name="Search Discovery",
                source_url=link,
                ticket_url=link,
                image_url=None,
                extra={
                    "search_discovery_query": q,
                    "search_date_confidence": confidence,
                    "date_confidence": 0.92 if confidence else 0.0,
                },
            ))

        time.sleep(0.05)

    return out


def roma_tennis_override(location: Dict[str, str], from_date: str, to_date: str, category: str) -> List[Dict[str, Any]]:
    if not is_rome(location) or category not in {"", "sport", "tennis"}:
        return []

    requested_from = parse_date_safe(from_date)
    requested_to = parse_date_safe(to_date)
    event_start = parse_date_safe(ITALIAN_OPEN_2026_START)
    event_end = parse_date_safe(ITALIAN_OPEN_2026_END)

    if not event_start or not event_end:
        return []

    if requested_from and requested_from > event_end:
        return []
    if requested_to and requested_to < event_start:
        return []

    display_date = max(requested_from or event_start, event_start).isoformat()

    ev = make_event(
        title="Internazionali BNL d'Italia 2026 / Italian Open Rome 2026",
        category="sport",
        subcategory="Tennis",
        start_date=display_date,
        start_time=None,
        city="Roma",
        country="IT",
        venue="Foro Italico",
        source_name="Sports Official Fallback",
        source_url="https://www.internazionalibnlditalia.com/",
        ticket_url="https://www.internazionalibnlditalia.com/",
        image_url=None,
        currency="EUR",
        status="active",
        extra={
            "official_sources": ["Internazionali BNL d'Italia", "ATP", "WTA"],
            "event_real_start_date": ITALIAN_OPEN_2026_START,
            "event_real_end_date": ITALIAN_OPEN_2026_END,
        },
    )

    return [ev] if is_within_dates(ev, from_date, to_date) else []


def local_official_fallback(location: Dict[str, str], from_date: str, to_date: str, category: str) -> List[Dict[str, Any]]:
    if category == "sport" and is_rome(location):
        url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(
            f"Roma sport events {from_date} {to_date} official tickets Foro Italico Stadio Olimpico"
        )
        return [make_event(
            title="Rome official sport ticket sources",
            category="sport",
            subcategory="Official ticket sources",
            start_date=from_date,
            city="Roma",
            country="IT",
            venue="Foro Italico / Stadio Olimpico / official clubs",
            source_name="Sports Official Fallback",
            source_url=url,
            ticket_url=url,
            status="fallback",
            extra={
                "official_sources": ["Internazionali BNL d'Italia", "AS Roma", "SS Lazio", "CONI", "TicketOne"],
            },
        )]

    return []


# =========================
# V36 CACHE / DIAGNOSTICS FIX
# =========================

def make_events_cache_key(city: str, country: str, from_iso: str, to_iso: str, cat: str) -> str:
    """
    Build stable cache key for /events.
    """
    raw = "|".join([
        str(city or "").strip().lower(),
        str(country or "").strip().lower(),
        str(from_iso or "").strip(),
        str(to_iso or "").strip(),
        str(cat or "").strip().lower(),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_events_cache(cache_key: str) -> Optional[Tuple[List[Dict[str, Any]], Dict[str, Any]]]:
    """
    Read in-memory events cache.
    """
    if not ENABLE_EVENTS_CACHE:
        return None

    item = _EVENTS_CACHE.get(cache_key)
    if not item:
        return None

    created_at, events, diagnostics = item
    if time.time() - created_at > DISCOVERY_CACHE_TTL_SECONDS:
        _EVENTS_CACHE.pop(cache_key, None)
        return None

    cached_diag = dict(diagnostics or {})
    cached_diag["cache"] = "hit"

    return events, cached_diag


def set_events_cache(cache_key: str, events: List[Dict[str, Any]], diagnostics: Dict[str, Any]) -> None:
    """
    Store in-memory events cache.
    """
    if not ENABLE_EVENTS_CACHE:
        return

    _EVENTS_CACHE[cache_key] = (time.time(), events, diagnostics)


def validate_event_v36(
    ev: Dict[str, Any],
    location: Dict[str, str],
    requested_category: str,
    from_date: str,
    to_date: str,
) -> Tuple[bool, str]:
    """
    Final hardening validation before merge.
    """
    title = clean_text(ev.get("title"))
    if not title:
        return False, "missing_title"

    if title_is_bad(title, ev.get("source_url"), ev.get("category", "")):
        return False, "bad_or_low_quality_title"

    if not ev.get("start_date"):
        return False, "missing_start_date"

    if not is_within_dates(ev, from_date, to_date):
        return False, "outside_requested_dates"

    date_confidence = ev.get("date_confidence", 1)
    try:
        date_confidence_float = float(date_confidence if date_confidence is not None else 1)
    except Exception:
        date_confidence_float = 1

    if date_confidence_float < MIN_DATE_CONFIDENCE:
        return False, "low_date_confidence"

    combined = " ".join([
        clean_text(ev.get("title")),
        clean_text(ev.get("venue")),
        clean_text(ev.get("city")),
        clean_text(ev.get("country")),
        clean_text(ev.get("source_url")),
    ])

    if result_year_conflicts(combined, from_date, to_date):
        return False, "wrong_year"

    if location.get("city") and not city_matches(ev, location):
        source_name = slug(ev.get("source_name"))
        if "fallback" not in source_name and "search discovery" not in source_name:
            return False, "wrong_city_or_country"

    if requested_category:
        ev_category = ev.get("category")
        if requested_category == "sport":
            if ev_category not in {"sport", "motorsport"}:
                return False, "wrong_category"
        elif ev_category and ev_category != requested_category:
            return False, "wrong_category"

    return True, "accepted"


def log_event_v36(ev: Dict[str, Any], accepted: bool, reason: str) -> None:
    """
    Optional debug logging for V36 event filtering.
    """
    if not ENABLE_DIAGNOSTICS_LOG:
        return

    logger.debug(
        "event_v36 accepted=%s reason=%s source=%s date=%s title=%s",
        accepted,
        reason,
        ev.get("source_name"),
        ev.get("start_date"),
        ev.get("title"),
    )


def build_events_diagnostics(
    *,
    loc: Dict[str, str],
    category: str,
    from_iso: str,
    to_iso: str,
    provider_counts: Dict[str, int],
    raw_count: int,
    merged_count: int,
    discard_reasons: Dict[str, int],
    cache: str,
) -> Dict[str, Any]:
    """
    Build diagnostics payload for /events?diagnostics=true.
    """
    return {
        "ok": True,
        "version": VERSION,
        "normalized": loc,
        "category": category,
        "from_date": from_iso,
        "to_date": to_iso,
        "provider_counts": provider_counts,
        "raw_count": raw_count,
        "merged_count": merged_count,
        "discard_reasons": discard_reasons,
        "cache": cache,
        "cache_enabled": ENABLE_EVENTS_CACHE,
        "cache_ttl_seconds": DISCOVERY_CACHE_TTL_SECONDS,
        "min_date_confidence": MIN_DATE_CONFIDENCE,
    }


def save_events_snapshot(
    events: List[Dict[str, Any]],
    diagnostics: Dict[str, Any],
    *,
    city: str,
    country: str,
    from_date: str,
    to_date: str,
    category: str,
) -> str:
    """
    Save snapshot JSON file for regression/debug.
    """
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    safe_name = slug(f"{city}-{country}-{from_date}-{to_date}-{category}") or "events"
    safe_name = re.sub(r"[^a-z0-9\-]+", "-", safe_name.lower()).strip("-")
    filename = f"{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}-{safe_name}.json"
    path = SNAPSHOT_DIR / filename

    payload = {
        "created_at": datetime.utcnow().isoformat() + "+00:00",
        "events": events,
        "diagnostics": diagnostics,
    }

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


@app.get("/")
def root() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "WELOVEIT Events API",
        "provider": "Ticketmaster + SeatGeek + SerpApi + Search Discovery + Sports Expansion + PredictHQ + API-Football + Local official fallbacks + Eventbrite fallback",
        "api_key_present": bool(TICKETMASTER_API_KEY),
        "predict_api_key_present": bool(PREDICT_API_KEY),
        "predict_api_url_present": bool(PREDICT_API_URL),
        "football_api_key_present": bool(FOOTBALL_API_KEY),
        "eventbrite_api_key_present": bool(EVENTBRITE_API_KEY),
        "seatgeek_client_id_present": bool(SEATGEEK_CLIENT_ID),
        "seatgeek_client_secret_present": bool(SEATGEEK_CLIENT_SECRET),
        "serpapi_api_key_present": bool(SERPAPI_API_KEY),
        "eventbrite_mode": "fallback_only",
        "seatgeek_auth_mode": "client_id_only",
        "country_city_fix": True,
        "parking_filter": True,
        "serpapi_query_expansion": True,
        "serpapi_location_filter": True,
        "advanced_source_priority": True,
        "serpapi_category_cleanup": True,
        "sports_expansion_engine": True,
        "sports_official_fallback": True,
        "search_discovery_engine": True,
        "search_discovery_no_fake_dates": True,
        "search_discovery_year_guard": True,
        "rome_alias_fix": True,
        "rome_roma_parity": True,
        "hard_final_date_filter": True,
        "rome_tennis_override": True,
        "italian_open_2026_end": ITALIAN_OPEN_2026_END,
        "v35_hardening_integrated": True,
        "v36_cache_enabled": ENABLE_EVENTS_CACHE,
        "v36_min_date_confidence": MIN_DATE_CONFIDENCE,
        "v36_snapshot_dir": str(SNAPSHOT_DIR),
        "version": VERSION,
    }


@app.get("/health")
def health() -> Dict[str, Any]:
    return root()


@app.get("/debug/serpapi")
def debug_serpapi(
    city: str = Query(...),
    country: str = Query(""),
    from_date: str = Query(...),
    to_date: str = Query(...),
    category: str = Query(""),
) -> Dict[str, Any]:
    loc = normalize_location(city, country)
    cat = normalize_category(category)
    events = serpapi_events(loc, from_date, to_date, cat)

    return {
        "serpapi_api_key_present": bool(SERPAPI_API_KEY),
        "sports_expansion": cat == "sport",
        "ok": True,
        "input": {
            "city": city,
            "country": country,
            "from_date": from_date,
            "to_date": to_date,
            "category": category,
        },
        "normalized": {
            "city": loc["city"],
            "country_code": loc["country_code"],
        },
        "queries": serpapi_queries(loc, from_date, to_date, cat),
        "total_events_count": len(events),
        "sample": events[:10],
    }


@app.get("/debug/search-discovery")
def debug_search_discovery(
    city: str = Query(...),
    country: str = Query(""),
    from_date: str = Query(...),
    to_date: str = Query(...),
    category: str = Query(""),
) -> Dict[str, Any]:
    loc = normalize_location(city, country)
    cat = normalize_category(category)
    events = search_discovery_events(loc, from_date, to_date, cat)

    return {
        "ok": True,
        "serpapi_api_key_present": bool(SERPAPI_API_KEY),
        "search_discovery_engine": True,
        "search_discovery_no_fake_dates": True,
        "search_discovery_year_guard": True,
        "input": {
            "city": city,
            "country": country,
            "from_date": from_date,
            "to_date": to_date,
            "category": category,
        },
        "normalized": loc,
        "queries": search_discovery_queries(loc, from_date, to_date, cat),
        "total_events_count": len(events),
        "sample": events[:20],
    }


@app.get("/events")
def get_events(
    city: str = Query(...),
    country: str = Query(""),
    from_date: str = Query(default_factory=today_iso),
    to_date: str = Query(default_factory=lambda: (date.today() + timedelta(days=30)).isoformat()),
    category: str = Query(""),
    diagnostics: bool = Query(False, description="Return events plus V36 diagnostics"),
    use_cache: bool = Query(True, description="Use in-memory V36 cache"),
    write_snapshot: bool = Query(False, description="Write a JSON snapshot for regression tests"),
) -> JSONResponse:
    loc = normalize_location(city, country)
    cat = normalize_category(category)

    fd = parse_date_safe(from_date) or date.today()
    td = parse_date_safe(to_date) or (fd + timedelta(days=30))
    if td < fd:
        td = fd

    from_iso, to_iso = fd.isoformat(), td.isoformat()
    cache_key = make_events_cache_key(city, country, from_iso, to_iso, cat)

    if use_cache:
        cached = get_events_cache(cache_key)
        if cached:
            cached_events, cached_diagnostics = cached
            if diagnostics:
                return JSONResponse({"events": cached_events, "diagnostics": cached_diagnostics})
            return JSONResponse(cached_events)

    all_events: List[Dict[str, Any]] = []
    tm = ticketmaster_events(loc, from_iso, to_iso, cat)
    sg = seatgeek_events(loc, from_iso, to_iso, cat)
    ro = roma_tennis_override(loc, from_iso, to_iso, cat)
    sp = serpapi_events(loc, from_iso, to_iso, cat)
    sd = search_discovery_events(loc, from_iso, to_iso, cat)
    ph = predict_events(loc, from_iso, to_iso, cat)
    fb = local_official_fallback(loc, from_iso, to_iso, cat)

    all_events.extend(tm)
    all_events.extend(sg)
    all_events.extend(ro)
    all_events.extend(sp)
    all_events.extend(sd)
    all_events.extend(ph)
    all_events.extend(fb)

    merge_diag: Dict[str, Any] = {}
    merged = merge_events(all_events, loc, cat, from_iso, to_iso, diagnostics=merge_diag)

    if is_rome(loc) and cat in {"", "sport", "tennis"}:
        has_tennis = any(
            "tennis" in slug(e.get("subcategory"))
            or any(k in slug(e.get("title")) for k in TENNIS_ROMA_KEYWORDS)
            for e in merged
        )
        override = roma_tennis_override(loc, from_iso, to_iso, cat)

        if override and not has_tennis:
            override[0]["ai_score"] = 99
            override[0]["quality_score"] = 99
            merged = merge_events(override + merged, loc, cat, from_iso, to_iso, diagnostics=merge_diag)

    diag = build_events_diagnostics(
        loc=loc,
        category=cat,
        from_iso=from_iso,
        to_iso=to_iso,
        provider_counts={
            "ticketmaster": len(tm),
            "seatgeek": len(sg),
            "rome_tennis_override": len(ro),
            "serpapi_sports_expansion": len(sp),
            "search_discovery": len(sd),
            "predicthq": len(ph),
            "fallback": len(fb),
            "merged": len(merged),
        },
        raw_count=len(all_events),
        merged_count=len(merged),
        discard_reasons=merge_diag.get("discard_reasons", {}),
        cache="miss",
    )
    diag.update({k: v for k, v in merge_diag.items() if k not in diag})

    if write_snapshot:
        diag["snapshot_path"] = save_events_snapshot(
            merged,
            diag,
            city=city,
            country=country,
            from_date=from_iso,
            to_date=to_iso,
            category=cat,
        )

    if use_cache:
        set_events_cache(cache_key, merged, diag)

    if diagnostics:
        return JSONResponse({"events": merged, "diagnostics": diag})

    return JSONResponse(merged)


@app.get("/debug/events")
def debug_events(
    city: str = Query(...),
    country: str = Query(""),
    from_date: str = Query(default_factory=today_iso),
    to_date: str = Query(default_factory=lambda: (date.today() + timedelta(days=30)).isoformat()),
    category: str = Query(""),
) -> Dict[str, Any]:
    loc = normalize_location(city, country)
    cat = normalize_category(category)

    fd = parse_date_safe(from_date) or date.today()
    td = parse_date_safe(to_date) or (fd + timedelta(days=30))
    if td < fd:
        td = fd

    from_iso, to_iso = fd.isoformat(), td.isoformat()

    tm = ticketmaster_events(loc, from_iso, to_iso, cat)
    sg = seatgeek_events(loc, from_iso, to_iso, cat)
    ro = roma_tennis_override(loc, from_iso, to_iso, cat)
    sp = serpapi_events(loc, from_iso, to_iso, cat)
    sd = search_discovery_events(loc, from_iso, to_iso, cat)
    ph = predict_events(loc, from_iso, to_iso, cat)
    fb = local_official_fallback(loc, from_iso, to_iso, cat)

    merged = merge_events(tm + sg + ro + sp + sd + ph + fb, loc, cat, from_iso, to_iso)

    return {
        "ok": True,
        "version": VERSION,
        "input": {
            "city": city,
            "country": country,
            "from_date": from_iso,
            "to_date": to_iso,
            "category": category,
        },
        "normalized": loc,
        "counts": {
            "ticketmaster": len(tm),
            "seatgeek": len(sg),
            "rome_tennis_override": len(ro),
            "serpapi_sports_expansion": len(sp),
            "search_discovery": len(sd),
            "predicthq": len(ph),
            "fallback": len(fb),
            "merged": len(merged),
        },
        "sample": merged[:30],
    }



@app.get("/debug/snapshot")
def debug_snapshot(
    city: str = Query(...),
    country: str = Query(""),
    from_date: str = Query(default_factory=today_iso),
    to_date: str = Query(default_factory=lambda: (date.today() + timedelta(days=30)).isoformat()),
    category: str = Query(""),
) -> Dict[str, Any]:
    response = get_events(
        city=city,
        country=country,
        from_date=from_date,
        to_date=to_date,
        category=category,
        diagnostics=True,
        use_cache=False,
        write_snapshot=True,
    )

    body = json.loads(response.body.decode("utf-8"))
    return {
        "ok": True,
        "version": VERSION,
        "snapshot_path": body.get("diagnostics", {}).get("snapshot_path"),
        "count": len(body.get("events", [])),
        "diagnostics": body.get("diagnostics", {}),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true", help="Run API server")
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    args = parser.parse_args()

    if args.serve:
        import uvicorn
        uvicorn.run("main:app", host=args.host, port=args.port, reload=False)
    else:
        print(json.dumps(root(), indent=2))


if __name__ == "__main__":
    main()
