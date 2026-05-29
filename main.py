# main.py — WELOVEIT Events API v42
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
import html
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

VERSION = "weloveit-events-v42-real-local-sources-engine"

TICKETMASTER_API_KEY = os.getenv("TICKETMASTER_API_KEY", "").strip()
PREDICT_API_KEY = os.getenv("PREDICT_API_KEY", "").strip()
PREDICT_API_URL = os.getenv("PREDICT_API_URL", "").strip()
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY", "").strip()
EVENTBRITE_API_KEY = os.getenv("EVENTBRITE_API_KEY", "").strip()
EVENTBRITE_API_URL = os.getenv("EVENTBRITE_API_URL", "https://www.eventbriteapi.com/v3/events/search/").strip()
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
logger = logging.getLogger("weloveit.events.v42")
_EVENTS_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]], Dict[str, Any]]] = {}


def make_events_cache_key(city: str, country: str, from_iso: str, to_iso: str, cat: str) -> str:
    return "|".join([
        str(city or "").strip().lower(),
        str(country or "").strip().lower(),
        str(from_iso or "").strip(),
        str(to_iso or "").strip(),
        str(cat or "").strip().lower(),
    ])


def get_events_cache(cache_key: str) -> Optional[Tuple[List[Dict[str, Any]], Dict[str, Any]]]:
    if not ENABLE_EVENTS_CACHE:
        return None

    item = _EVENTS_CACHE.get(cache_key)
    if not item:
        return None

    created_at, events, diagnostics = item

    if time.time() - created_at > DISCOVERY_CACHE_TTL_SECONDS:
        _EVENTS_CACHE.pop(cache_key, None)
        return None

    diag = dict(diagnostics or {})
    diag["cache"] = "hit"

    return events, diag


def set_events_cache(cache_key: str, events: List[Dict[str, Any]], diagnostics: Dict[str, Any]) -> None:
    if not ENABLE_EVENTS_CACHE:
        return

    _EVENTS_CACHE[cache_key] = (
        time.time(),
        list(events or []),
        dict(diagnostics or {}),
    )


def validate_event_v36(
    ev: Dict[str, Any],
    location: Dict[str, str],
    requested_category: str,
    from_date: str,
    to_date: str,
) -> Tuple[bool, str]:
    """
    V41 REAL EVENTS ONLY:
    - accept only real provider events with explicit date
    - reject fake fallback/source cards and synthetic discovery cards
    - ranking may sort/score, but it must never invent events
    """
    title = clean_text(ev.get("title"))
    if not title:
        return False, "missing_title"

    if not ev.get("start_date"):
        return False, "missing_start_date"

    if not is_within_dates(ev, from_date, to_date):
        return False, "outside_requested_dates"

    source_name = slug(ev.get("source_name"))
    status = slug(ev.get("status"))

    # V41: never show synthetic/fallback/source cards as events.
    if (
        status == "fallback"
        or ev.get("v39_source_card")
        or ev.get("source_card")
        or "fallback" in source_name
        or "expanded discovery" in source_name
        or "venue discovery" in source_name
        or source_name in {"v39 expanded discovery", "v39 venue discovery", "japan official fallback", "sports official fallback"}
    ):
        return False, "synthetic_or_fallback_card_rejected"

    if title_is_bad(title, ev.get("source_url"), ev.get("category", "")):
        return False, "bad_or_low_quality_title"

    combined = " ".join([
        title,
        clean_text(ev.get("venue")),
        clean_text(ev.get("source_url")),
        clean_text(ev.get("ticket_url")),
    ])

    if result_year_conflicts(combined, from_date, to_date):
        return False, "wrong_year_conflict"

    # V38: city mismatch becomes a warning, not a hard rejection.
    if not city_matches(ev, location):
        ev["location_warning"] = "soft_city_country_mismatch"

    # V38: low confidence becomes ranking penalty, not deletion.
    confidence = ev.get("date_confidence")
    try:
        if confidence is not None and float(confidence) < MIN_DATE_CONFIDENCE:
            ev["date_confidence_warning"] = "soft_low_date_confidence"
    except Exception:
        pass

    # V38: category mismatch becomes ranking penalty, not deletion.
    if requested_category:
        ev_cat = ev.get("category")
        category_ok = False
        if requested_category == "sport":
            category_ok = ev_cat in {"sport", "motorsport"}
        else:
            category_ok = (not ev_cat) or ev_cat == requested_category
        if not category_ok:
            ev["category_warning"] = "soft_category_mismatch"

    return True, "accepted"

def log_event_v36(ev: Dict[str, Any], accepted: bool, reason: str) -> None:
    if not ENABLE_DIAGNOSTICS_LOG:
        return

    try:
        logger.info(
            "event_v36 accepted=%s reason=%s source=%s date=%s title=%s",
            accepted,
            reason,
            ev.get("source_name"),
            ev.get("start_date"),
            ev.get("title"),
        )
    except Exception:
        pass


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
        "generated_at": datetime.utcnow().isoformat() + "+00:00",
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
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    raw_name = make_events_cache_key(city, country, from_date, to_date, category)
    digest = hashlib.sha1(raw_name.encode("utf-8")).hexdigest()[:12]
    filename = f"events_{digest}_{from_date}_{to_date}.json"
    path = SNAPSHOT_DIR / filename

    payload = {
        "events": events,
        "diagnostics": diagnostics,
    }

    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return str(path)


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

# V37 upgrades: immagini fallback, Japan local discovery, Eventbrite reale, Premium planner, ticket aggregation
FALLBACK_IMAGES = {
    "concert": "https://images.unsplash.com/photo-1501281668745-f7f57925c3b4?auto=format&fit=crop&w=900&q=80",
    "sport": "https://images.unsplash.com/photo-1461896836934-ffe607ba8211?auto=format&fit=crop&w=900&q=80",
    "motorsport": "https://images.unsplash.com/photo-1503736334956-4c8f8e92946d?auto=format&fit=crop&w=900&q=80",
    "culture": "https://images.unsplash.com/photo-1531058020387-3be344556be6?auto=format&fit=crop&w=900&q=80",
    "food": "https://images.unsplash.com/photo-1517248135467-4c7edcad34c4?auto=format&fit=crop&w=900&q=80",
    "nightlife": "https://images.unsplash.com/photo-1492684223066-81342ee5ff30?auto=format&fit=crop&w=900&q=80",
    "default": "https://images.unsplash.com/photo-1492684223066-81342ee5ff30?auto=format&fit=crop&w=900&q=80",
}

JAPAN_LOCAL_DOMAINS = [
    "eplus.jp",
    "t.pia.jp",
    "l-tike.com",
    "ticket.rakuten.co.jp",
    "tokyo-dome.co.jp",
    "jleague.co",
    "npb.jp",
    "sumo.or.jp",
]


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


def fallback_image_for_event(title: str = "", category: str = "", subcategory: str = "", venue: str = "") -> str:
    """Stable hero image fallback when providers do not send an image."""
    s = slug(f"{title} {category} {subcategory} {venue}")

    if any(k in s for k in ["formula 1", "f1", "motogp", "grand prix"]):
        return FALLBACK_IMAGES["motorsport"]
    if any(k in s for k in ["concert", "tour", "festival", "music", "rock", "pop", "jazz", "dj"]):
        return FALLBACK_IMAGES["concert"]
    if any(k in s for k in ["football", "rugby", "tennis", "basketball", "nba", "nfl", "ufc", "boxing", "sport"]):
        return FALLBACK_IMAGES["sport"]
    if any(k in s for k in ["food", "restaurant", "ramen", "wine", "taste"]):
        return FALLBACK_IMAGES["food"]
    if any(k in s for k in ["club", "night", "party", "dance"]):
        return FALLBACK_IMAGES["nightlife"]
    if category in FALLBACK_IMAGES:
        return FALLBACK_IMAGES[category]
    return FALLBACK_IMAGES["default"]


def enrich_event_visuals(ev: Dict[str, Any]) -> Dict[str, Any]:
    if not ev.get("image_url"):
        ev["image_url"] = fallback_image_for_event(
            ev.get("title", ""),
            ev.get("category", ""),
            ev.get("subcategory", ""),
            ev.get("venue", ""),
        )
        ev["image_is_fallback"] = True
    else:
        ev["image_is_fallback"] = False
    return ev


def make_ticket_source(label: str, url: Optional[str], source_type: str = "official") -> Optional[Dict[str, str]]:
    if not url:
        return None
    return {
        "label": clean_text(label) or "Tickets",
        "url": str(url),
        "type": source_type,
    }


def enrich_ticket_sources(ev: Dict[str, Any]) -> Dict[str, Any]:
    sources: List[Dict[str, str]] = []

    primary = make_ticket_source(ev.get("source_name") or "Primary source", ev.get("ticket_url") or ev.get("source_url"), "primary")
    if primary:
        sources.append(primary)

    eventbrite = make_ticket_source("Eventbrite", ev.get("eventbrite_search_url"), "fallback")
    if eventbrite:
        sources.append(eventbrite)

    official = make_ticket_source("Official ticket search", official_google_ticket_url(
        ev.get("title", ""), ev.get("city", ""), ev.get("country", ""), ev.get("start_date", ""), ev.get("subcategory", "")
    ), "official_search")
    if official:
        sources.append(official)

    seen = set()
    unique = []
    for src in sources:
        key = src.get("url")
        if key and key not in seen:
            seen.add(key)
            unique.append(src)

    ev["ticket_sources"] = unique[:5]
    ev["ticket_source_count"] = len(unique[:5])
    return ev


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

    enrich_event_visuals(ev)
    enrich_ticket_sources(ev)
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
    if "eventbrite" in s:
        return 32
    if "search discovery" in s:
        return 34
    if "serpapi" in s or "sports expansion" in s:
        return 30
    if "predicthq" in s:
        return 20
    if "fallback" in s:
        return 5
    return 10


def fame_boost(title: str, venue: str, subcategory: str) -> int:
    s = slug(f"{title} {venue} {subcategory}")

    superstar_keywords = [
        "taylor swift", "beyonce", "beyoncé", "coldplay", "ed sheeran",
        "dua lipa", "bruce springsteen", "madonna", "lady gaga",
        "the weeknd", "billie eilish", "olivia rodrigo", "oasis",
        "metallica", "u2", "rolling stones", "drake", "bad bunny",
        "kendrick lamar", "eminem", "rihanna", "justin bieber",
        "harry styles", "ariana grande", "post malone"
    ]

    major_sport_keywords = [
        "wimbledon", "champions league", "premier league", "nba",
        "nfl", "ufc", "formula 1", "f1", "motogp", "atp", "wta",
        "grand prix", "final", "semi final", "semifinal",
        "italian open", "internazionali bnl", "six nations",
        "rugby world cup", "world cup", "olympics", "us open",
        "roland garros", "super bowl"
    ]

    iconic_venues = [
        "wembley", "o2 arena", "royal albert hall", "madison square garden",
        "stadio olimpico", "foro italico", "san siro", "allianz arena",
        "tokyo dome", "nippon budokan", "saitama super arena",
        "barclays center", "crypto.com arena", "underworld",
        "royal opera house", "apollo", "hyde park"
    ]

    boost = 0

    if any(k in s for k in superstar_keywords):
        boost += 14

    if any(k in s for k in major_sport_keywords):
        boost += 12

    if any(k in s for k in iconic_venues):
        boost += 7

    if any(k in s for k in ["final", "championship", "grand prix", "open", "world tour", "stadium tour"]):
        boost += 6

    return boost


def compute_score(ev: Dict[str, Any], location: Dict[str, str], requested_category: str) -> int:
    score = 55

    title = slug(ev.get("title"))
    venue = slug(ev.get("venue"))
    sub = slug(ev.get("subcategory"))
    source = slug(ev.get("source_name"))

    # Fonte dati: provider più affidabili valgono di più
    if "ticketmaster" in source:
        score += 12
    elif "seatgeek" in source:
        score += 10
    elif "eventbrite" in source:
        score += 9
    elif "search discovery" in source:
        score += 8
    elif "serpapi" in source or "sports expansion" in source:
        score += 7
    elif "predicthq" in source:
        score += 4
    elif "fallback" in source:
        score -= 5

    # Qualità scheda evento
    if ev.get("image_url"):
        score += 4
    if ev.get("image_is_fallback"):
        score -= 2
    if ev.get("ticket_url") and "google.com/search" not in str(ev.get("ticket_url")):
        score += 5
    if ev.get("venue"):
        score += 3
    if ev.get("start_time"):
        score += 2
    if (ev.get("ticket_source_count") or 0) >= 3:
        score += 4
    if city_matches(ev, location):
        score += 8

    # Coerenza categoria richiesta
    if requested_category == "sport" and ev.get("category") in {"sport", "motorsport"}:
        score += 6
    elif requested_category and ev.get("category") == requested_category:
        score += 5

    # Boost fama artista/evento/venue
    score += fame_boost(
        ev.get("title", ""),
        ev.get("venue", ""),
        ev.get("subcategory", "")
    )

    # Boost extra per sport/eventi premium
    if any(k in title for k in ["final", "open", "championship", "cup", "grand prix", "internazionali"]):
        score += 5

    if "tennis" in sub or any(k in title or k in venue for k in TENNIS_ROMA_KEYWORDS):
        score += 6

    if is_rome(location) and any(k in title or k in venue for k in TENNIS_ROMA_KEYWORDS):
        score += 5

    # Penalità eventi deboli o poco utili
    if title_is_bad(ev.get("title", ""), ev.get("source_url"), ev.get("category", "")):
        score -= 35

    if "predicthq" in source and not ev.get("source_url"):
        score -= 8

    if ev.get("search_date_confidence") is False:
        score -= 25

    if len(title) < 8:
        score -= 8

    # Micro-variazione stabile per evitare punteggi tutti uguali
    variation_seed = slug(ev.get("title", "")) + str(ev.get("start_date", "")) + str(ev.get("venue", ""))
    variation = sum(ord(c) for c in variation_seed) % 7
    score += variation

    return max(60, min(99, score))


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



def is_real_ticket_url(url: Any) -> bool:
    u = str(url or '').strip()
    if not u:
        return False
    low = u.lower()
    if low.startswith('https://www.google.com/search'):
        return False
    return low.startswith('http://') or low.startswith('https://')


def official_source_rank(source_name: Any, url: Any) -> int:
    s = slug(source_name)
    u = str(url or '').lower()
    rank = 0
    if any(x in s for x in ['ticketmaster', 'eventbrite', 'seatgeek', 'ticket pia', 'pia', 'lawson', 'eplus', 'rakuten']):
        rank += 40
    if any(x in s for x in ['official', 'club', 'venue']):
        rank += 25
    if any(x in u for x in ['ticketmaster', 'eventbrite', 'seatgeek', 'eplus.jp', 'pia.jp', 'l-tike.com', 'rakuten', 'jleague', 'npb.jp', 'sumo.or.jp']):
        rank += 30
    if 'google.com/search' in u:
        rank -= 25
    return rank


def collect_ticket_links(*items: Dict[str, Any]) -> List[Dict[str, Any]]:
    seen = set()
    links: List[Dict[str, Any]] = []
    for ev in items:
        if not ev:
            continue
        source = ev.get('source_name') or ev.get('source') or 'Fonte evento'
        for key in ['ticket_url', 'source_url', 'url']:
            url = ev.get(key)
            if not is_real_ticket_url(url):
                continue
            if url in seen:
                continue
            seen.add(url)
            links.append({
                'source': source,
                'url': url,
                'official_rank': official_source_rank(source, url),
            })
        for existing in ev.get('ticket_links') or []:
            url = existing.get('url') if isinstance(existing, dict) else None
            if not is_real_ticket_url(url) or url in seen:
                continue
            seen.add(url)
            links.append(existing)
    links.sort(key=lambda x: -(x.get('official_rank') or 0))
    return links[:8]


def apply_smart_ticket_merge(target: Dict[str, Any], *others: Dict[str, Any]) -> Dict[str, Any]:
    all_items = [target] + [x for x in others if x]
    ticket_links = collect_ticket_links(*all_items)
    if ticket_links:
        target['ticket_links'] = ticket_links
        best_link = ticket_links[0]
        target['official_source_url'] = best_link.get('url')
        target['official_source_name'] = best_link.get('source')
        if not is_real_ticket_url(target.get('ticket_url')) or official_source_rank(best_link.get('source'), best_link.get('url')) > official_source_rank(target.get('source_name'), target.get('ticket_url')):
            target['ticket_url'] = best_link.get('url')

    prices = []
    for ev in all_items:
        try:
            p = ev.get('price_min')
            if p is not None and p != '':
                prices.append(float(p))
        except Exception:
            pass
    if prices:
        best_price = min(prices)
        target['best_price'] = best_price
        if target.get('price_min') in {None, ''} or float(target.get('price_min') or best_price) > best_price:
            target['price_min'] = best_price

    merged_sources = []
    for ev in all_items:
        merged_sources += ev.get('merged_sources') or [ev.get('source_name') or ev.get('source') or 'Fonte evento']
    target['merged_sources'] = sorted(list(set([x for x in merged_sources if x])))
    target['multi_provider_merged'] = len(target['merged_sources']) > 1
    return target

def merge_events(
    events: List[Dict[str, Any]],
    location: Dict[str, str],
    requested_category: str,
    from_date: str,
    to_date: str,
    diagnostics: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    V40 intelligent merge:
    - soft validation, not destructive filtering
    - AI anti-duplicate key
    - merges ticket links from multiple providers
    - selects best/most official ticket URL
    - keeps best price when provider prices exist
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

        if old:
            old_score = compute_score(old, location, requested_category)
            new_score = compute_score(ev, location, requested_category)
            winner = ev if new_score >= old_score else old
            loser = old if winner is ev else ev

            if not winner.get("image_url") and loser.get("image_url"):
                winner["image_url"] = loser["image_url"]

            winner = apply_smart_ticket_merge(winner, loser)
            winner["ai_score"] = max(old_score, new_score)
            winner["quality_score"] = winner["ai_score"]
            best[k] = winner
        else:
            best[k] = apply_smart_ticket_merge(ev)

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
        diagnostics["v40_multi_provider_merge"] = True
        diagnostics["v40_best_price"] = True
        diagnostics["v40_official_source_selection"] = True

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


def eventbrite_events(location: Dict[str, str], from_date: str, to_date: str, category: str) -> List[Dict[str, Any]]:
    """Real Eventbrite integration when EVENTBRITE_API_KEY is present."""
    if not EVENTBRITE_API_KEY:
        return []

    headers = {
        "User-Agent": "WELOVEIT/1.0",
        "Authorization": f"Bearer {EVENTBRITE_API_KEY}",
    }

    q = location["city"]
    if category == "sport":
        q += " sport tickets"
    elif category == "concert":
        q += " concert music"
    elif category == "culture":
        q += " exhibition theatre culture"

    params = {
        "q": q,
        "location.address": f"{location['city']} {location.get('country_name') or location.get('country_code')}",
        "start_date.range_start": f"{from_date}T00:00:00Z",
        "start_date.range_end": f"{to_date}T23:59:59Z",
        "expand": "venue,ticket_availability,logo",
        "sort_by": "date",
    }

    ok, status, data, _ = http_get_json(EVENTBRITE_API_URL, params, headers=headers)
    if not ok or status not in {200, 201}:
        return []

    out: List[Dict[str, Any]] = []
    for item in data.get("events", []) or []:
        title = clean_text((item.get("name") or {}).get("text") or item.get("name") or "")
        start_dt = parse_dt_safe((item.get("start") or {}).get("local") or (item.get("start") or {}).get("utc"))
        if not title or not start_dt:
            continue

        venue = item.get("venue") or {}
        venue_name = venue.get("name") or ""
        venue_city = ((venue.get("address") or {}).get("city") or location["city"])
        venue_country = ((venue.get("address") or {}).get("country") or location["country_code"])
        logo = item.get("logo") or {}
        image_url = (logo.get("original") or {}).get("url") or logo.get("url")

        cat, sub = category_from_title(title, category)
        out.append(make_event(
            title=title,
            category=cat,
            subcategory=sub,
            start_date=start_dt.date().isoformat(),
            start_time=start_dt.time().isoformat(timespec="seconds") if start_dt.time() else None,
            city=venue_city,
            country=venue_country,
            venue=venue_name,
            source_name="Eventbrite",
            source_url=item.get("url"),
            ticket_url=item.get("url"),
            image_url=image_url,
            extra={
                "eventbrite_id": item.get("id"),
                "capacity": item.get("capacity"),
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

    if location.get("country_code") == "JP":
        jp_terms = [
            f"site:eplus.jp {city} {year} tickets",
            f"site:t.pia.jp {city} {year} チケット",
            f"site:l-tike.com {city} {year} チケット",
            f"site:ticket.rakuten.co.jp {city} {year} チケット",
            f"Tokyo Dome {year} tickets",
            f"Nippon Budokan {year} tickets",
            f"J League {city} {year} tickets",
            f"NPB baseball {city} {year} tickets",
            f"sumo tournament {city} {year} tickets",
        ]
        base_terms = jp_terms + base_terms

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


def make_source_fallback_event(
    *,
    title: str,
    subcategory: str,
    city: str,
    country: str,
    from_date: str,
    venue: str,
    url: str,
    category: str = "culture",
    source_name: str = "Official Source Fallback",
) -> Dict[str, Any]:
    return make_event(
        title=title,
        category=category or "culture",
        subcategory=subcategory,
        start_date=from_date,
        city=city,
        country=country,
        venue=venue,
        source_name=source_name,
        source_url=url,
        ticket_url=url,
        status="fallback",
        extra={"v38_source_card": True, "date_confidence": 0.99},
    )


def google_search_url(query: str) -> str:
    return "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)


def local_official_fallback(location: Dict[str, str], from_date: str, to_date: str, category: str) -> List[Dict[str, Any]]:
    """
    V38 source cards: used only when real providers return too few events.
    These are not fake events; they are official discovery/ticket source cards.
    """
    city = location.get("city") or ""
    country = location.get("country_code") or ""
    city_slug = slug(city)
    out: List[Dict[str, Any]] = []

    if category == "sport" and is_rome(location):
        out.extend([
            make_source_fallback_event(
                title="Rome official sport ticket sources",
                subcategory="Official sport sources",
                city="Roma",
                country="IT",
                from_date=from_date,
                venue="Foro Italico / Stadio Olimpico / official clubs",
                url=google_search_url(f"Roma sport events {from_date} {to_date} official tickets Foro Italico Stadio Olimpico"),
                category="sport",
                source_name="Sports Official Fallback",
            ),
            make_source_fallback_event(
                title="AS Roma official tickets and fixtures",
                subcategory="Football",
                city="Roma",
                country="IT",
                from_date=from_date,
                venue="Stadio Olimpico",
                url="https://www.asroma.com/en/tickets",
                category="sport",
                source_name="Sports Official Fallback",
            ),
            make_source_fallback_event(
                title="SS Lazio official tickets and fixtures",
                subcategory="Football",
                city="Roma",
                country="IT",
                from_date=from_date,
                venue="Stadio Olimpico",
                url="https://www.sslazio.it/en/tickets",
                category="sport",
                source_name="Sports Official Fallback",
            ),
        ])

    if country == "JP" or city_slug in {"tokyo", "osaka", "kyoto", "yokohama", "himeji", "kobe"}:
        jp_city = city or "Tokyo"
        out.extend([
            make_source_fallback_event(
                title=f"{jp_city} official Japan ticket sources",
                subcategory="Japan local ticket sources",
                city=jp_city,
                country="JP",
                from_date=from_date,
                venue="eplus / Pia / Lawson Ticket / Rakuten Ticket",
                url=google_search_url(f"{jp_city} Japan events {from_date} {to_date} eplus pia lawson rakuten tokyo dome jleague npb sumo official tickets"),
                category=category or "culture",
                source_name="Japan Official Fallback",
            ),
            make_source_fallback_event(
                title=f"{jp_city} concerts and live music official tickets",
                subcategory="Concerts",
                city=jp_city,
                country="JP",
                from_date=from_date,
                venue="Tokyo Dome / Budokan / Zepp / Billboard Live",
                url=google_search_url(f"{jp_city} concerts live music {from_date} {to_date} official tickets eplus pia lawson"),
                category="concert",
                source_name="Japan Official Fallback",
            ),
            make_source_fallback_event(
                title=f"{jp_city} sports fixtures official tickets",
                subcategory="Sports",
                city=jp_city,
                country="JP",
                from_date=from_date,
                venue="J.League / NPB / Sumo / Rugby",
                url=google_search_url(f"{jp_city} sports fixtures tickets J League NPB sumo rugby {from_date} {to_date}"),
                category="sport",
                source_name="Japan Official Fallback",
            ),
            make_source_fallback_event(
                title=f"{jp_city} anime game and pop culture events",
                subcategory="Anime / Pop culture",
                city=jp_city,
                country="JP",
                from_date=from_date,
                venue="Tokyo Big Sight / Makuhari Messe / Anime venues",
                url=google_search_url(f"{jp_city} anime game pop culture events {from_date} {to_date} tickets"),
                category="culture",
                source_name="Japan Official Fallback",
            ),
            make_source_fallback_event(
                title=f"{jp_city} food festivals and nightlife events",
                subcategory="Food / Nightlife",
                city=jp_city,
                country="JP",
                from_date=from_date,
                venue="Local festivals / clubs / food events",
                url=google_search_url(f"{jp_city} food festival nightlife events {from_date} {to_date}"),
                category="culture",
                source_name="Japan Official Fallback",
            ),
        ])

    if country == "FR" or city_slug == "paris":
        out.extend([
            make_source_fallback_event(
                title="Paris official ticket sources",
                subcategory="Paris ticket sources",
                city="Paris",
                country="FR",
                from_date=from_date,
                venue="Ticketmaster France / Fnac Spectacles / See Tickets",
                url=google_search_url(f"Paris events {from_date} {to_date} official tickets Ticketmaster France Fnac Spectacles See Tickets"),
                category=category or "culture",
                source_name="France Official Fallback",
            ),
            make_source_fallback_event(
                title="Paris concerts at Accor Arena and La Défense Arena",
                subcategory="Concerts",
                city="Paris",
                country="FR",
                from_date=from_date,
                venue="Accor Arena / Paris La Défense Arena / Olympia",
                url=google_search_url(f"Paris concerts Accor Arena La Defense Arena Olympia {from_date} {to_date} tickets"),
                category="concert",
                source_name="France Official Fallback",
            ),
            make_source_fallback_event(
                title="Paris sport fixtures and major events",
                subcategory="Sports",
                city="Paris",
                country="FR",
                from_date=from_date,
                venue="Roland-Garros / Parc des Princes / Stade de France",
                url=google_search_url(f"Paris sport events Roland Garros Parc des Princes Stade de France {from_date} {to_date} tickets"),
                category="sport",
                source_name="France Official Fallback",
            ),
            make_source_fallback_event(
                title="Paris exhibitions theatre and culture",
                subcategory="Culture",
                city="Paris",
                country="FR",
                from_date=from_date,
                venue="Museums / theatres / exhibitions",
                url=google_search_url(f"Paris exhibitions theatre culture events {from_date} {to_date} official tickets"),
                category="culture",
                source_name="France Official Fallback",
            ),
        ])

    major_city_generic = {
        "london": ("GB", ["O2 Arena", "Wembley", "Royal Albert Hall", "London theatres"]),
        "new york": ("US", ["Madison Square Garden", "Barclays Center", "Broadway", "MetLife Stadium"]),
        "madrid": ("ES", ["WiZink Center", "Santiago Bernabéu", "Theatres", "Festivals"]),
        "barcelona": ("ES", ["Palau Sant Jordi", "Camp Nou", "Festivals", "Theatres"]),
        "berlin": ("DE", ["Uber Arena", "Olympiastadion", "Clubs", "Museums"]),
    }
    if city_slug in major_city_generic:
        cc, venues = major_city_generic[city_slug]
        out.extend([
            make_source_fallback_event(
                title=f"{city} official event ticket sources",
                subcategory="Official ticket sources",
                city=city,
                country=cc,
                from_date=from_date,
                venue=" / ".join(venues),
                url=google_search_url(f"{city} events {from_date} {to_date} official tickets concerts sports theatre"),
                category=category or "culture",
                source_name="Major City Official Fallback",
            ),
            make_source_fallback_event(
                title=f"{city} concerts sports and theatre discovery",
                subcategory="AI discovery sources",
                city=city,
                country=cc,
                from_date=from_date,
                venue=" / ".join(venues[:3]),
                url=google_search_url(f"best events in {city} {from_date} {to_date} tickets"),
                category=category or "culture",
                source_name="Major City Official Fallback",
            ),
        ])

    # Always dedupe source cards.
    seen: set = set()
    unique: List[Dict[str, Any]] = []
    for ev in out:
        key = dedupe_key(ev)
        if key not in seen:
            seen.add(key)
            unique.append(ev)
    return unique[:40]



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
        "eventbrite_mode": "real_api_plus_fallback" if bool(EVENTBRITE_API_KEY) else "fallback_only",
        "v37_hero_images": True,
        "v37_premium_planner": True,
        "v37_japan_local_sources": True,
        "v37_ticket_source_aggregation": True,
        "v38_soft_ranking_no_overfilter": True,
        "v39_brutal_expansion": True,
        "v40_multi_provider_merge": True,
        "v40_best_price": True,
        "v40_official_source_selection": True,
        "v40_premium_travel_planner": True,
        "v40_japan_deep_sources": True,
        "v39_all_categories_when_tutte": True,
        "v39_major_city_minimum_results": True,
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




# =========================
# V42 LOCAL SOURCES ENGINE — REAL EVENTS ONLY
# =========================
# This layer does NOT invent event cards. It only accepts local-source records
# when a real title + explicit date + source URL can be extracted.

LOCAL_SOURCE_TIMEOUT = int(os.getenv("WELOVEIT_LOCAL_SOURCE_TIMEOUT", "10"))
ENABLE_LOCAL_SOURCES = os.getenv("WELOVEIT_ENABLE_LOCAL_SOURCES", "1").strip().lower() not in {"0", "false", "no"}


def http_get_text(url: str, headers: Optional[Dict[str, str]] = None) -> Tuple[bool, int, str, str]:
    req_headers = headers or {
        "User-Agent": "Mozilla/5.0 (compatible; WELOVEIT-EventsBot/1.0; +https://www.weloveit.it)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
    }
    req = urllib.request.Request(url, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=LOCAL_SOURCE_TIMEOUT) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            return True, int(resp.status), raw.decode(charset, errors="replace"), url
    except Exception as exc:
        logger.info("V42 local source fetch failed url=%s err=%s", url, exc)
        return False, 0, "", url


def absolute_url(base_url: str, maybe_url: Any) -> Optional[str]:
    u = clean_text(maybe_url)
    if not u:
        return None
    return urllib.parse.urljoin(base_url, html.unescape(u))


def strip_html_tags(value: str) -> str:
    value = re.sub(r"<script[\s\S]*?</script>", " ", value or " ", flags=re.I)
    value = re.sub(r"<style[\s\S]*?</style>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return clean_text(html.unescape(value))


def json_loads_lenient(raw: str) -> Optional[Any]:
    try:
        return json.loads(raw)
    except Exception:
        pass
    try:
        cleaned = html.unescape(raw).strip()
        cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
        return json.loads(cleaned)
    except Exception:
        return None


def iter_jsonld_nodes(obj: Any) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []
    if isinstance(obj, dict):
        if "@graph" in obj and isinstance(obj["@graph"], list):
            for x in obj["@graph"]:
                nodes.extend(iter_jsonld_nodes(x))
        nodes.append(obj)
    elif isinstance(obj, list):
        for x in obj:
            nodes.extend(iter_jsonld_nodes(x))
    return nodes


def node_is_event(node: Dict[str, Any]) -> bool:
    t = node.get("@type") or node.get("type") or ""
    if isinstance(t, list):
        return any(str(x).lower() == "event" for x in t)
    return str(t).lower() == "event"


def parse_jsonld_events_from_html(
    html_text: str,
    base_url: str,
    location: Dict[str, str],
    source_name: str,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    scripts = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>([\s\S]*?)</script>',
        html_text or "",
        flags=re.I,
    )
    for raw in scripts:
        data = json_loads_lenient(raw)
        if data is None:
            continue
        for node in iter_jsonld_nodes(data):
            if not node_is_event(node):
                continue
            title = clean_text(node.get("name") or node.get("headline"))
            start_raw = node.get("startDate") or node.get("start_date")
            dt = parse_dt_safe(start_raw) or parse_dt_safe(str(start_raw or "")[:10])
            if not title or not dt:
                continue
            location_node = node.get("location") or {}
            venue = ""
            city = location.get("city") or ""
            country = location.get("country_code") or ""
            if isinstance(location_node, dict):
                venue = clean_text(location_node.get("name"))
                address = location_node.get("address") or {}
                if isinstance(address, dict):
                    city = clean_text(address.get("addressLocality") or city)
                    country = clean_text(address.get("addressCountry") or country)
            url = absolute_url(base_url, node.get("url") or node.get("mainEntityOfPage") or base_url)
            image = node.get("image")
            if isinstance(image, list):
                image = image[0] if image else None
            if isinstance(image, dict):
                image = image.get("url")
            cat, sub = category_from_title(title, "")
            out.append(make_event(
                title=title,
                category=cat,
                subcategory=sub,
                start_date=dt.date().isoformat(),
                start_time=dt.time().isoformat(timespec="seconds") if dt.time() else None,
                city=city or location.get("city") or "",
                country=country or location.get("country_code") or "",
                venue=venue,
                source_name=source_name,
                source_url=url,
                ticket_url=url,
                image_url=absolute_url(base_url, image) if image else None,
                extra={"v42_local_source": True, "date_confidence": 0.99},
            ))
    return out


def parse_date_from_italian_text(text: str, from_date: str, to_date: str) -> Tuple[Optional[str], Optional[str], bool]:
    # Reuse strict discovery parser: it accepts explicit Italian and English dates only.
    return parse_discovery_date_from_text(text, from_date, to_date)


def parse_anchor_local_events(
    html_text: str,
    base_url: str,
    location: Dict[str, str],
    source_name: str,
    from_date: str,
    to_date: str,
) -> List[Dict[str, Any]]:
    """Fallback parser for editorial listing pages. Accepts only anchors/blocks with an explicit date."""
    out: List[Dict[str, Any]] = []
    # Article blocks first.
    blocks = re.findall(r"<(article|li|div)\b[^>]*(?:event|evento|card|item|article)[^>]*>[\s\S]{0,5000}?</\1>", html_text or "", flags=re.I)
    # Python returns only tag if using capturing group; use broader manual block regex.
    blocks2 = re.findall(r"<article\b[\s\S]{0,5000}?</article>|<li\b[\s\S]{0,3500}?</li>", html_text or "", flags=re.I)
    candidates = blocks2[:120]
    if not candidates:
        candidates = re.findall(r"<a\b[^>]+href=[\"'][^\"']+[\"'][^>]*>[\s\S]{0,700}?</a>", html_text or "", flags=re.I)[:180]

    for block in candidates:
        href_match = re.search(r"href=[\"']([^\"']+)[\"']", block, flags=re.I)
        url = absolute_url(base_url, href_match.group(1)) if href_match else base_url
        text = strip_html_tags(block)
        if len(text) < 12:
            continue
        d, t, confidence = parse_date_from_italian_text(text, from_date, to_date)
        if not d or not confidence:
            continue
        # Title extraction: prefer h2/h3/a title/short leading text before date.
        title = ""
        h = re.search(r"<h[1-4][^>]*>([\s\S]{3,220}?)</h[1-4]>", block, flags=re.I)
        if h:
            title = strip_html_tags(h.group(1))
        if not title:
            tm = re.search(r"title=[\"']([^\"']{3,180})[\"']", block, flags=re.I)
            if tm:
                title = clean_text(html.unescape(tm.group(1)))
        if not title:
            title = re.split(r"\b(?:dal|dall|fino|sabato|domenica|lunedì|martedì|mercoledì|giovedì|venerdì|\d{1,2}[/-]\d{1,2})\b", text, maxsplit=1, flags=re.I)[0]
            title = clean_text(title[:140])
        if not title or len(title) < 4:
            continue
        cat, sub = category_from_title(title, "")
        out.append(make_event(
            title=title,
            category=cat,
            subcategory=sub,
            start_date=d,
            start_time=t,
            city=location.get("city") or "",
            country=location.get("country_code") or "",
            venue="",
            source_name=source_name,
            source_url=url,
            ticket_url=url,
            image_url=None,
            extra={"v42_local_source": True, "date_confidence": 0.90},
        ))
    return out


def roma_local_source_urls() -> List[Tuple[str, str]]:
    return [
        ("RomaToday", "https://www.romatoday.it/eventi/"),
        ("Turismo Roma", "https://www.turismoroma.it/it/eventi"),
        ("Wanted in Rome", "https://www.wantedinrome.com/whatson"),
        ("Auditorium Parco della Musica", "https://www.auditorium.com/it/eventi/"),
        ("Teatro dell'Opera di Roma", "https://www.operaroma.it/spettacoli/"),
        ("MAXXI", "https://www.maxxi.art/events/"),
    ]


def paris_local_source_urls() -> List[Tuple[str, str]]:
    return [
        ("Paris Je t'aime", "https://parisjetaime.com/eng/events"),
        ("Sortir à Paris", "https://www.sortiraparis.com/en/what-to-do-in-paris"),
        ("Accor Arena", "https://www.accorarena.com/en/events-and-tickets"),
        ("Philharmonie de Paris", "https://philharmoniedeparis.fr/en/programming"),
    ]


def tokyo_local_source_urls() -> List[Tuple[str, str]]:
    return [
        ("Tokyo Cheapo Events", "https://tokyocheapo.com/events/"),
        ("Tokyo Weekender Events", "https://www.tokyoweekender.com/events/"),
        ("Tokyo Dome", "https://www.tokyo-dome.co.jp/en/tourists/dome/events/"),
        ("Tokyo Big Sight", "https://www.bigsight.jp/english/visitor/event/"),
    ]


def local_source_urls_for_city(location: Dict[str, str]) -> List[Tuple[str, str]]:
    city_key = slug(location.get("city"))
    country = (location.get("country_code") or "").upper()
    if city_key in {"roma", "rome"} or country == "IT" and city_key == "roma":
        return roma_local_source_urls()
    if city_key == "paris":
        return paris_local_source_urls()
    if city_key == "tokyo":
        return tokyo_local_source_urls()
    return []


def local_source_events_v42(location: Dict[str, str], from_date: str, to_date: str, category: str) -> List[Dict[str, Any]]:
    if not ENABLE_LOCAL_SOURCES:
        return []
    urls = local_source_urls_for_city(location)
    if not urls:
        return []
    out: List[Dict[str, Any]] = []
    for source_name, url in urls:
        ok, status, body, final_url = http_get_text(url)
        if not ok or status >= 400 or not body:
            continue
        parsed = parse_jsonld_events_from_html(body, final_url, location, source_name)
        parsed.extend(parse_anchor_local_events(body, final_url, location, source_name, from_date, to_date))
        for ev in parsed:
            if not is_within_dates(ev, from_date, to_date):
                continue
            if category:
                ev_cat = ev.get("category")
                if category == "sport" and ev_cat not in {"sport", "motorsport"}:
                    continue
                if category != "sport" and ev_cat != category:
                    # Keep culture/local events when category is broad only.
                    continue
            ev["source_priority"] = "local_editorial"
            out.append(ev)
        time.sleep(0.05)
    # Deduplicate local results before returning.
    seen = set()
    unique: List[Dict[str, Any]] = []
    for ev in out:
        key = dedupe_key(ev)
        if key in seen:
            continue
        seen.add(key)
        unique.append(ev)
    return unique[:80]


# =========================
# V39 BRUTAL EXPANSION LAYER
# =========================
# Goal: for global cities, never return a useless 1-card result page.
# We keep real provider events first. If APIs are sparse, we add many
# transparent source/discovery cards. These are NOT fake dated events:
# they are official search/ticket entry points for the selected city/date range.

def provider_categories_for_request(category: str) -> List[str]:
    cat = normalize_category(category)
    if cat:
        return [cat]
    # "Tutte": query the main verticals separately instead of one generic call.
    return ["", "concert", "sport", "culture"]


def collect_real_provider_events_v39(
    loc: Dict[str, str],
    from_iso: str,
    to_iso: str,
    cat: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    all_events: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    seen_raw: set = set()

    cats = provider_categories_for_request(cat)

    def add_many(name: str, events: List[Dict[str, Any]]) -> None:
        counts[name] = counts.get(name, 0) + len(events)
        for ev in events:
            key = f"{slug(ev.get('title'))}|{ev.get('start_date')}|{slug(ev.get('venue'))}|{slug(ev.get('source_name'))}"
            if key in seen_raw:
                continue
            seen_raw.add(key)
            all_events.append(ev)

    # Ticketmaster and SeatGeek are relatively structured: expand them by category.
    for c in cats:
        try:
            add_many(f"ticketmaster_{c or 'all'}", ticketmaster_events(loc, from_iso, to_iso, c))
        except Exception as exc:
            logger.warning("V39 ticketmaster category=%s failed: %s", c, exc)
        try:
            add_many(f"seatgeek_{c or 'all'}", seatgeek_events(loc, from_iso, to_iso, c))
        except Exception as exc:
            logger.warning("V39 seatgeek category=%s failed: %s", c, exc)

    # Expensive discovery sources: call once for requested cat, and for sport/concert if category is all.
    discovery_cats = [cat] if cat else ["concert", "sport", "culture"]
    for c in discovery_cats:
        try:
            add_many(f"serpapi_{c or 'all'}", serpapi_events(loc, from_iso, to_iso, c))
        except Exception as exc:
            logger.warning("V39 serpapi category=%s failed: %s", c, exc)
        try:
            add_many(f"search_discovery_{c or 'all'}", search_discovery_events(loc, from_iso, to_iso, c))
        except Exception as exc:
            logger.warning("V39 search discovery category=%s failed: %s", c, exc)

    # Other providers.
    for c in ([cat] if cat else [""]):
        try:
            add_many("predicthq", predict_events(loc, from_iso, to_iso, c))
        except Exception as exc:
            logger.warning("V39 predicthq failed: %s", exc)
        try:
            add_many("eventbrite", eventbrite_events(loc, from_iso, to_iso, c))
        except Exception as exc:
            logger.warning("V39 eventbrite failed: %s", exc)
        try:
            add_many("rome_tennis_override", roma_tennis_override(loc, from_iso, to_iso, c))
        except Exception as exc:
            logger.warning("V39 rome tennis override failed: %s", exc)
        try:
            add_many("local_sources_v42", local_source_events_v42(loc, from_iso, to_iso, c))
        except Exception as exc:
            logger.warning("V42 local sources failed: %s", exc)

    counts["raw_total_unique"] = len(all_events)
    return all_events, counts


def expanded_city_source_cards_v39(
    loc: Dict[str, str],
    from_iso: str,
    to_iso: str,
    cat: str,
    target_min: int = 30,
) -> List[Dict[str, Any]]:
    city = loc.get("city") or ""
    country = loc.get("country_code") or ""
    country_name = loc.get("country_name") or country
    city_key = slug(city)

    verticals = []
    if cat == "concert":
        verticals = ["concerts", "live music", "festivals", "jazz", "clubs"]
    elif cat == "sport":
        verticals = ["sport fixtures", "football", "tennis", "basketball", "rugby", "motorsport"]
    elif cat == "culture":
        verticals = ["exhibitions", "theatre", "museums", "opera", "food festivals", "family events"]
    else:
        verticals = [
            "concerts", "live music", "festivals", "sport fixtures", "football", "tennis",
            "basketball", "rugby", "theatre", "exhibitions", "museums", "opera",
            "food festivals", "nightlife", "family events", "comedy", "dance", "classical music",
        ]

    venue_map = {
        "paris": [
            "Accor Arena", "Paris La Défense Arena", "Olympia", "Zénith Paris",
            "Stade de France", "Parc des Princes", "Roland-Garros", "Philharmonie de Paris",
            "Théâtre Mogador", "Grand Palais", "Louvre", "Bercy",
        ],
        "tokyo": [
            "Tokyo Dome", "Nippon Budokan", "Saitama Super Arena", "Ariake Arena",
            "Tokyo Big Sight", "Makuhari Messe", "Zepp Shinjuku", "Billboard Live Tokyo",
            "National Stadium", "Ryogoku Kokugikan", "Shibuya venues", "Ginza theatres",
        ],
        "london": [
            "O2 Arena", "Wembley Stadium", "Royal Albert Hall", "London Palladium",
            "Tottenham Hotspur Stadium", "Twickenham", "West End theatres", "Alexandra Palace",
        ],
        "new york": [
            "Madison Square Garden", "Barclays Center", "Broadway", "MetLife Stadium",
            "Radio City Music Hall", "Yankee Stadium", "Beacon Theatre", "Lincoln Center",
        ],
        "roma": [
            "Stadio Olimpico", "Foro Italico", "Auditorium Parco della Musica", "Circo Massimo",
            "Teatro dell'Opera", "Palazzo dello Sport", "Ippodromo Capannelle", "MAXXI",
        ],
        "rome": [
            "Stadio Olimpico", "Foro Italico", "Auditorium Parco della Musica", "Circo Massimo",
            "Teatro dell'Opera", "Palazzo dello Sport", "Ippodromo Capannelle", "MAXXI",
        ],
    }
    venues = venue_map.get(city_key, [
        "major arenas", "stadiums", "theatres", "museums", "festival venues", "official ticketing sites"
    ])

    local_sources = {
        "paris": "Ticketmaster France / Fnac Spectacles / See Tickets / France Billet / venue official sites",
        "tokyo": "eplus / Ticket Pia / Lawson Ticket / Rakuten Ticket / venue official sites",
        "london": "Ticketmaster UK / AXS / See Tickets / venue official sites",
        "new york": "Ticketmaster / SeatGeek / AXS / venue official sites",
        "roma": "TicketOne / Ticketmaster Italia / Vivaticket / official club and venue sites",
        "rome": "TicketOne / Ticketmaster Italia / Vivaticket / official club and venue sites",
    }.get(city_key, "Ticketmaster / SeatGeek / Eventbrite / venue official sites")

    cards: List[Dict[str, Any]] = []

    # First: local official source hub cards.
    cards.extend(local_official_fallback(loc, from_iso, to_iso, cat))

    # Then: many vertical + venue search cards.
    for idx, vertical in enumerate(verticals):
        venue = venues[idx % len(venues)]
        title = f"{city} {vertical} - official tickets and events"
        q = f"{city} {country_name} {vertical} {venue} events tickets from {from_iso} to {to_iso} official"
        category_guess, sub_guess = category_from_title(vertical, cat or "culture")
        if "concert" in vertical or "music" in vertical or "festival" in vertical or "jazz" in vertical:
            category_guess, sub_guess = "concert", "Concerts / Music"
        elif any(x in vertical for x in ["sport", "football", "tennis", "basketball", "rugby", "motorsport"]):
            category_guess, sub_guess = "sport", "Sports"
        elif any(x in vertical for x in ["theatre", "opera", "museum", "exhibition"]):
            category_guess, sub_guess = "culture", "Culture"

        cards.append(make_source_fallback_event(
            title=title,
            subcategory=sub_guess,
            city=city,
            country=country,
            from_date=from_iso,
            venue=f"{venue} / {local_sources}",
            url=google_search_url(q),
            category=category_guess,
            source_name="V39 Expanded Discovery",
        ))

    # Add venue-specific cards for major venues.
    for venue in venues:
        q = f"{venue} {city} events tickets {from_iso} {to_iso} official"
        cards.append(make_source_fallback_event(
            title=f"{venue} upcoming events in {city}",
            subcategory="Venue events",
            city=city,
            country=country,
            from_date=from_iso,
            venue=venue,
            url=google_search_url(q),
            category=cat or "culture",
            source_name="V39 Venue Discovery",
        ))

    # Dedupe and cap.
    seen: set = set()
    unique: List[Dict[str, Any]] = []
    for ev in cards:
        key = dedupe_key(ev)
        if key in seen:
            continue
        seen.add(key)
        # Lower score so real events stay above source cards.
        ev["ai_score"] = min(ev.get("ai_score") or 70, 74)
        ev["quality_score"] = ev["ai_score"]
        ev["v39_source_card"] = True
        unique.append(ev)
    return unique[:target_min]


def is_major_city_v39(loc: Dict[str, str]) -> bool:
    return slug(loc.get("city")) in {
        "paris", "tokyo", "london", "new york", "roma", "rome", "madrid",
        "barcelona", "berlin", "milano", "milan", "toronto", "vancouver",
    }



def discovery_sources_for_city_v41(location: Dict[str, str], category: str = "") -> List[Dict[str, str]]:
    """Official/search source links shown in diagnostics only, never as fake events."""
    city = location.get("city") or ""
    country = location.get("country_code") or ""
    city_key = slug(city)

    def src(name: str, url: str, kind: str = "official/search") -> Dict[str, str]:
        return {"name": name, "url": url, "kind": kind}

    common = [
        src("Ticketmaster", google_search_url(f"Ticketmaster {city} events {country} official tickets")),
        src("Eventbrite", eventbrite_search_url(city, country, f"events {city}")),
        src("Bandsintown", google_search_url(f"Bandsintown {city} concerts events")),
        src("Fever", google_search_url(f"Fever {city} events")),
        src("Resident Advisor", google_search_url(f"Resident Advisor {city} events")),
    ]

    city_specific = {
        "paris": [
            src("Fnac Spectacles", google_search_url("Fnac Spectacles Paris concerts sport theatre tickets"), "local ticketing"),
            src("France Billet", google_search_url("France Billet Paris events tickets"), "local ticketing"),
            src("Accor Arena", google_search_url("Accor Arena Paris official events tickets"), "venue official"),
            src("Paris La Défense Arena", google_search_url("Paris La Défense Arena official events tickets"), "venue official"),
        ],
        "tokyo": [
            src("eplus", google_search_url("eplus Tokyo concerts sports anime events tickets"), "Japan ticketing"),
            src("Ticket Pia", google_search_url("Ticket Pia Tokyo events tickets"), "Japan ticketing"),
            src("Lawson Ticket", google_search_url("Lawson Ticket Tokyo events tickets"), "Japan ticketing"),
            src("Rakuten Ticket", google_search_url("Rakuten Ticket Tokyo events"), "Japan ticketing"),
            src("J.League", google_search_url("J.League Tokyo fixtures tickets"), "official sport"),
            src("NPB", google_search_url("NPB Tokyo baseball tickets fixtures"), "official sport"),
            src("Sumo", google_search_url("Tokyo sumo tournament tickets Ryogoku Kokugikan"), "official sport"),
        ],
        "london": [
            src("AXS UK", google_search_url("AXS London events tickets"), "local ticketing"),
            src("See Tickets UK", google_search_url("See Tickets London events"), "local ticketing"),
            src("The O2", google_search_url("The O2 London official events tickets"), "venue official"),
        ],
        "roma": [
            src("TicketOne", google_search_url("TicketOne Roma eventi biglietti"), "local ticketing"),
            src("Vivaticket", google_search_url("Vivaticket Roma eventi biglietti"), "local ticketing"),
            src("Auditorium Parco della Musica", google_search_url("Auditorium Parco della Musica Roma eventi biglietti"), "venue official"),
        ],
        "rome": [
            src("TicketOne", google_search_url("TicketOne Roma eventi biglietti"), "local ticketing"),
            src("Vivaticket", google_search_url("Vivaticket Roma eventi biglietti"), "local ticketing"),
            src("Auditorium Parco della Musica", google_search_url("Auditorium Parco della Musica Roma eventi biglietti"), "venue official"),
        ],
    }

    return (city_specific.get(city_key, []) + common)[:12]


@app.get("/discovery-sources")
def discovery_sources_endpoint(
    city: str = Query(...),
    country: str = Query(""),
    category: str = Query(""),
) -> Dict[str, Any]:
    loc = normalize_location(city, country)
    cat = normalize_category(category)
    return {
        "ok": True,
        "mode": "sources_only_not_events",
        "normalized": loc,
        "sources": discovery_sources_for_city_v41(loc, cat),
    }



@app.get("/debug/local-sources")
def debug_local_sources(
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
    events = local_source_events_v42(loc, fd.isoformat(), td.isoformat(), cat)
    return {
        "ok": True,
        "version": VERSION,
        "enabled": ENABLE_LOCAL_SOURCES,
        "normalized": loc,
        "sources": [{"name": n, "url": u} for n, u in local_source_urls_for_city(loc)],
        "count": len(events),
        "sample": events[:30],
    }


@app.get("/events")
def get_events(
    city: str = Query(...),
    country: str = Query(""),
    from_date: str = Query(default_factory=today_iso),
    to_date: str = Query(default_factory=lambda: (date.today() + timedelta(days=30)).isoformat()),
    category: str = Query(""),
    diagnostics: bool = Query(False, description="Return events plus V41 diagnostics"),
    use_cache: bool = Query(True, description="Use in-memory V41 cache"),
    write_snapshot: bool = Query(False, description="Write a JSON snapshot for regression tests"),
) -> JSONResponse:
    loc = normalize_location(city, country)
    cat = normalize_category(category)

    fd = parse_date_safe(from_date) or date.today()
    td = parse_date_safe(to_date) or (fd + timedelta(days=30))
    if td < fd:
        td = fd

    from_iso, to_iso = fd.isoformat(), td.isoformat()
    cache_key = "v42-real-local-sources|" + make_events_cache_key(city, country, from_iso, to_iso, cat)

    if use_cache:
        cached = get_events_cache(cache_key)
        if cached:
            cached_events, cached_diagnostics = cached
            if diagnostics:
                return JSONResponse({"events": cached_events, "diagnostics": cached_diagnostics})
            return JSONResponse(cached_events)

    # V41: real providers only. No fake source cards, no synthetic fallback events.
    all_events, provider_counts = collect_real_provider_events_v39(loc, from_iso, to_iso, cat)

    # Remove synthetic cards before merge even if older helper functions produced them.
    real_events: List[Dict[str, Any]] = []
    for ev in all_events:
        source = slug(ev.get("source_name"))
        status = slug(ev.get("status"))
        if (
            status == "fallback"
            or ev.get("v39_source_card")
            or ev.get("source_card")
            or "fallback" in source
            or "expanded discovery" in source
            or "venue discovery" in source
        ):
            continue
        real_events.append(ev)

    merge_diag: Dict[str, Any] = {}
    merged = merge_events(real_events, loc, cat, from_iso, to_iso, diagnostics=merge_diag)

    # V41: if sparse, do NOT invent events. Provide source suggestions in diagnostics only.
    discovery_sources = discovery_sources_for_city_v41(loc, cat)

    diag = build_events_diagnostics(
        loc=loc,
        category=cat,
        from_iso=from_iso,
        to_iso=to_iso,
        provider_counts={
            **provider_counts,
            "real_events_after_synthetic_rejection": len(real_events),
            "merged": len(merged),
        },
        raw_count=len(all_events),
        merged_count=len(merged),
        discard_reasons=merge_diag.get("discard_reasons", {}),
        cache="miss",
    )
    diag.update({k: v for k, v in merge_diag.items() if k not in diag})
    diag["v41_mode"] = "real_events_only_no_fake_fallbacks"
    diag["v42_local_sources_engine"] = True
    diag["v42_local_sources_enabled"] = ENABLE_LOCAL_SOURCES
    diag["discovery_sources"] = discovery_sources
    diag["empty_state_message"] = (
        "Non abbiamo ancora trovato abbastanza eventi live verificati per questa ricerca. "
        "Mostrare pochi risultati reali è meglio che inventare eventi."
    )

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
    response = get_events(
        city=city,
        country=country,
        from_date=from_date,
        to_date=to_date,
        category=category,
        diagnostics=True,
        use_cache=False,
        write_snapshot=False,
    )
    body = json.loads(response.body.decode("utf-8"))
    return {
        "ok": True,
        "version": VERSION,
        "mode": "V41 real events only",
        "events_count": len(body.get("events", [])),
        "diagnostics": body.get("diagnostics", {}),
        "sample": body.get("events", [])[:30],
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
