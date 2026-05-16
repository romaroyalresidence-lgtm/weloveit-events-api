# main.py — WELOVEIT Events API v33
# V33 Search Discovery Engine:
# - keeps V31/V32 sport polish logic
# - adds Google-like discovery layer through SerpApi organic search
# - adds country-local ticketing/event sources
# - Rome/Roma parity + city alias expansion
# - Italian Open / Internazionali BNL d'Italia ranking boost
# - hard final date filter after all providers
# - avoids direct Google scraping: uses SerpApi API only
# - no httpx: stdlib urllib + FastAPI

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

VERSION = "ticketmaster-seatgeek-predicthq-football-eventbrite-serpapi-v33-search-discovery"

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
MAX_SEARCH_DISCOVERY_QUERIES = 16
MAX_SEARCH_DISCOVERY_RESULTS_PER_QUERY = 10

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
    "rome": ("Roma", "IT"),
    "roma": ("Roma", "IT"),
    "roma capitale": ("Roma", "IT"),
    "rom": ("Roma", "IT"),

    "milan": ("Milano", "IT"),
    "milano": ("Milano", "IT"),
    "florence": ("Firenze", "IT"),
    "firenze": ("Firenze", "IT"),
    "venice": ("Venezia", "IT"),
    "venezia": ("Venezia", "IT"),
    "naples": ("Napoli", "IT"),
    "napoli": ("Napoli", "IT"),
    "turin": ("Torino", "IT"),
    "torino": ("Torino", "IT"),

    "london": ("London", "GB"),
    "londra": ("London", "GB"),

    "tokyo": ("Tokyo", "JP"),
    "tokio": ("Tokyo", "JP"),

    "new york": ("New York", "US"),
    "nyc": ("New York", "US"),

    "paris": ("Paris", "FR"),
    "parigi": ("Paris", "FR"),

    "madrid": ("Madrid", "ES"),
    "madri": ("Madrid", "ES"),
    "barcelona": ("Barcelona", "ES"),
    "barcellona": ("Barcelona", "ES"),

    "berlin": ("Berlin", "DE"),
    "berlino": ("Berlin", "DE"),

    "toronto": ("Toronto", "CA"),
    "montreal": ("Montreal", "CA"),

    "sao paulo": ("São Paulo", "BR"),
    "san paolo": ("São Paulo", "BR"),
    "rio": ("Rio de Janeiro", "BR"),
    "rio de janeiro": ("Rio de Janeiro", "BR"),

    "buenos aires": ("Buenos Aires", "AR"),

    "beijing": ("Beijing", "CN"),
    "pechino": ("Beijing", "CN"),
    "shanghai": ("Shanghai", "CN"),
}

CITY_SEARCH_ALIASES = {
    "Roma": ["Roma", "Rome"],
    "Milano": ["Milano", "Milan"],
    "Firenze": ["Firenze", "Florence"],
    "Venezia": ["Venezia", "Venice"],
    "Napoli": ["Napoli", "Naples"],
    "Torino": ["Torino", "Turin"],
    "Paris": ["Paris", "Parigi"],
    "Madrid": ["Madrid", "Madri"],
    "Barcelona": ["Barcelona", "Barcellona"],
    "Tokyo": ["Tokyo", "Tokio"],
    "London": ["London", "Londra"],
    "New York": ["New York", "NYC"],
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

COUNTRY_SEARCH_NAMES = {
    "IT": ["Italy", "Italia"],
    "GB": ["United Kingdom", "UK", "Great Britain"],
    "US": ["United States", "USA"],
    "JP": ["Japan", "Giappone"],
    "FR": ["France", "Francia"],
    "ES": ["Spain", "España", "Spagna"],
    "DE": ["Germany", "Deutschland", "Germania"],
    "CA": ["Canada"],
    "BR": ["Brazil", "Brasil", "Brasile"],
    "AR": ["Argentina"],
    "CN": ["China", "Cina"],
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

LOCAL_SEARCH_SITES = {
    "IT": [
        "ticketone.it",
        "ticketmaster.it",
        "dice.fm",
        "ra.co",
        "eventbrite.it",
        "internazionalibnlditalia.com",
        "sport.ticketone.it",
        "vivaticket.com",
        "boxol.it",
        "ciaotickets.com",
        "fansale.it",
    ],
    "FR": [
        "fnacspectacles.com",
        "francebillet.com",
        "ticketmaster.fr",
        "seetickets.com/fr",
        "shotgun.live",
        "dice.fm",
        "accorarena.com",
        "stadefrance.com",
        "parisladefense-arena.com",
    ],
    "ES": [
        "entradas.com",
        "ticketmaster.es",
        "elcorteingles.es/entradas",
        "dice.fm",
        "wegow.com",
        "seetickets.com/es",
        "entradasatualcance.com",
    ],
    "JP": [
        "t.pia.jp",
        "l-tike.com",
        "eplus.jp",
        "rakuten.co.jp/ticket",
        "livepocket.jp",
        "jleague.jp",
        "npb.jp",
        "sumo.or.jp",
    ],
    "US": [
        "ticketmaster.com",
        "seatgeek.com",
        "axs.com",
        "stubhub.com",
        "vividseats.com",
        "jambase.com",
        "bandsintown.com",
        "dice.fm",
    ],
    "GB": [
        "ticketmaster.co.uk",
        "seetickets.com",
        "axs.com/uk",
        "dice.fm",
        "skiddle.com",
        "ents24.com",
        "eventbrite.co.uk",
        "allianzstadiumtwickenham.com",
        "wembleystadium.com",
    ],
    "DE": [
        "eventim.de",
        "ticketmaster.de",
        "reservix.de",
        "dice.fm",
        "ra.co",
    ],
    "CA": [
        "ticketmaster.ca",
        "ticketweb.ca",
        "stubhub.ca",
        "seatgeek.com",
        "dice.fm",
    ],
    "BR": [
        "eventim.com.br",
        "ticketmaster.com.br",
        "sympla.com.br",
        "ingresso.com",
        "ticketsforfun.com.br",
    ],
    "AR": [
        "ticketek.com.ar",
        "entradauno.com",
        "tuentrada.com",
        "passline.com",
    ],
    "CN": [
        "damai.cn",
        "maoyan.com",
        "showstart.com",
        "trip.com",
    ],
}

OFFICIAL_SPORT_SITES = {
    "IT": ["internazionalibnlditalia.com", "legaseriea.it", "figc.it", "asroma.com", "sslazio.it"],
    "FR": ["rolandgarros.com", "psg.fr", "stadefrance.com", "lnr.fr"],
    "ES": ["laliga.com", "realmadrid.com", "fcbarcelona.com", "mutuamadridopen.com"],
    "JP": ["jleague.jp", "npb.jp", "sumo.or.jp", "t.pia.jp"],
    "US": ["nfl.com", "nba.com", "mlb.com", "nhl.com", "ufc.com", "toprank.com"],
    "GB": ["premiershiprugby.com", "thefa.com", "nfl.com", "wembleystadium.com", "allianzstadiumtwickenham.com"],
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
    r"\bmeetup\b",
    r"\bwebinar\b",
]

LOW_QUALITY_SOURCE_PATTERNS = [
    "google.com/maps",
    "maps/vt",
    "pinterest.",
    "facebook.com/share",
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
    "gen": 1, "gennaio": 1,
    "febbraio": 2,
    "marzo": 3,
    "aprile": 4,
    "maggio": 5,
    "giugno": 6,
    "luglio": 7,
    "agosto": 8,
    "set": 9, "settembre": 9,
    "ott": 10, "ottobre": 10,
    "novembre": 11,
    "dic": 12, "dicembre": 12,
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
    s = re.sub(r"[^a-z0-9àèéìòóùäöüßçñãõáíóúâêîôû]+", " ", s)
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

    if not country_code and city_key in {
        "roma", "rome", "milano", "milan", "firenze", "florence",
        "venezia", "venice", "napoli", "naples", "torino", "turin",
    }:
        country_code = "IT"

    return {
        "city": normalized_city,
        "country_code": country_code or raw_country.upper(),
        "country_name": COUNTRY_NAMES.get(
            country_code or raw_country.upper(),
            raw_country.title() if raw_country else "",
        ),
    }


def city_aliases_for_search(city: str) -> List[str]:
    city_clean = clean_text(city)
    aliases = CITY_SEARCH_ALIASES.get(city_clean, [city_clean])
    seen: List[str] = []
    for item in aliases:
        if item and item not in seen:
            seen.append(item)
    return seen


def country_aliases_for_search(country_code: str) -> List[str]:
    return COUNTRY_SEARCH_NAMES.get(country_code, [COUNTRY_NAMES.get(country_code, country_code)])


def is_rome(location: Dict[str, str]) -> bool:
    return slug(location.get("city")) in {"roma", "rome"} and location.get("country_code") == "IT"


def normalize_category(category: str = "") -> str:
    c = slug(category)
    if c in {"sports", "sport", "sporting", "sportivi", "sportivo"}:
        return "sport"
    if c in {"concerts", "concert", "music", "musica", "live"}:
        return "concert"
    if c in {"theatre", "theater", "culture", "cultura", "arts", "arte"}:
        return "culture"
    if c in {"all", "tutte", "tutti", "any", "qualsiasi"}:
        return ""
    return c


def category_from_title(title: str, fallback: str = "") -> Tuple[str, str]:
    s = slug(title)

    if any(k in s for k in ["internazionali", "italian open", "foro italico", "atp rome", "wta rome", "tennis"]):
        return "sport", "Tennis"
    if any(k in s for k in ["rugby", "premiership", "nations championship", "twickenham", "saracens", "harlequins"]):
        return "sport", "Rugby Union"
    if any(k in s for k in ["boxing", "fight night", "boxe", "championship boxing"]):
        return "sport", "Boxing"
    if any(k in s for k in ["nfl", "american football", "colts", "commanders", "jaguars", "eagles"]):
        return "sport", "NFL"
    if any(k in s for k in ["wwe", "aew", "wrestling"]):
        return "sport", "Wrestling"
    if any(k in s for k in ["basketball", "slb", "lions", "nba"]):
        return "sport", "Basketball"
    if any(k in s for k in ["motogp", "moto gp"]):
        return "motorsport", "MotoGP"
    if any(k in s for k in ["formula 1", "f1 grand prix", "grand prix"]):
        return "motorsport", "Formula 1"
    if any(k in s for k in ["football", "soccer", "fa cup", "championship", "serie a", "lazio", "roma v", "as roma", "calcio"]):
        return "sport", "Football"
    if any(k in s for k in ["concert", "live tour", "festival", "dj", "opera", "tenors", "symphony", "concerto"]):
        return "concert", "Concerts"
    if any(k in s for k in ["expo", "fair", "summit", "conference", "mostra", "fiera"]):
        return "culture", "Expos"

    if fallback:
        if fallback == "sport":
            return "sport", "Sport"
        if fallback == "concert":
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
        "UNITED KINGDOM" if target_country == "GB" else "",
    }

    if target_country == "IT" and ev_country in {"ITALY", "IT", "ITALIA"}:
        country_ok = True

    if target in {"roma", "rome"}:
        city_ok = ev_city in {"roma", "rome"} or "roma" in ev_city or "rome" in ev_city
    else:
        city_ok = (ev_city == target) or (target and target in ev_city) or (ev_city and ev_city in target)

    return bool(city_ok and country_ok)


def source_weight(source_name: str) -> int:
    s = slug(source_name)
    if "ticketmaster" in s:
        return 45
    if "seatgeek" in s:
        return 38
    if "search discovery" in s:
        return 33
    if "serpapi" in s or "sports expansion" in s:
        return 30
    if "predicthq" in s:
        return 20
    if "fallback" in s:
        return 5
    return 10


def is_official_or_ticketing_url(url: Any) -> bool:
    u = str(url or "").lower()
    good_domains = [
        "ticketmaster",
        "ticketone",
        "seatgeek",
        "axs.",
        "seetickets",
        "eventim",
        "fnacspectacles",
        "francebillet",
        "entradas",
        "elcorteingles",
        "t.pia.jp",
        "l-tike",
        "eplus.jp",
        "rakuten",
        "vivaticket",
        "boxol",
        "ciaotickets",
        "internazionalibnlditalia",
        "nfl.com",
        "nba.com",
        "mlb.com",
        "nhl.com",
        "ufc.com",
        "jleague",
        "npb.jp",
        "sumo.or.jp",
        "dice.fm",
        "ra.co",
        "wegow",
        "shotgun",
    ]
    return any(x in u for x in good_domains)


def compute_score(ev: Dict[str, Any], location: Dict[str, str], requested_category: str) -> int:
    score = source_weight(ev.get("source_name", ""))
    title = slug(ev.get("title"))
    venue = slug(ev.get("venue"))
    sub = slug(ev.get("subcategory"))
    url = ev.get("ticket_url") or ev.get("source_url")

    if ev.get("image_url"):
        score += 8

    if url and "google.com/search" not in str(url):
        score += 8

    if is_official_or_ticketing_url(url):
        score += 12

    if city_matches(ev, location):
        score += 15

    if requested_category == "sport" and ev.get("category") in {"sport", "motorsport"}:
        score += 15

    if requested_category and requested_category != "sport" and ev.get("category") == requested_category:
        score += 12

    if any(k in title for k in ["final", "open", "championship", "cup", "grand prix", "internazionali"]):
        score += 10

    if "tennis" in sub or any(k in title or k in venue for k in TENNIS_ROMA_KEYWORDS):
        score += 18

    if is_rome(location) and any(k in title or k in venue for k in TENNIS_ROMA_KEYWORDS):
        score += 20

    if title_is_bad(ev.get("title", ""), ev.get("source_url"), ev.get("category", "")):
        score -= 40

    if "predicthq" in slug(ev.get("source_name")) and not ev.get("source_url"):
        score -= 8

    if "search discovery" in slug(ev.get("source_name")):
        if not ev.get("search_discovery_date_confidence"):
            score -= 18
        if not is_official_or_ticketing_url(url):
            score -= 8

    return max(1, min(99, score))


def dedupe_key(ev: Dict[str, Any]) -> str:
    title = slug(ev.get("title"))
    title = re.sub(r"\bvenue premium tickets\b", "", title)
    title = re.sub(r"\bregister interest\b", "", title)
    title = re.sub(r"\btickets?\b", "", title)
    title = re.sub(r"\bbiglietti\b", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    d = ev.get("start_date") or ""
    venue = slug(ev.get("venue"))
    return f"{title[:70]}|{d}|{venue[:40]}"


def merge_events(
    events: List[Dict[str, Any]],
    location: Dict[str, str],
    requested_category: str,
    from_date: str,
    to_date: str,
) -> List[Dict[str, Any]]:
    cleaned: List[Dict[str, Any]] = []

    for ev in events:
        if not ev.get("title") or not ev.get("start_date"):
            continue

        if not is_within_dates(ev, from_date, to_date):
            continue

        if title_is_bad(ev.get("title", ""), ev.get("source_url"), ev.get("category", "")):
            continue

        if location.get("city") and ev.get("status") != "fallback" and not city_matches(ev, location):
            continue

        ev["ai_score"] = compute_score(ev, location, requested_category)
        ev["quality_score"] = ev["ai_score"]
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
                if not ev.get("start_time") and old.get("start_time"):
                    ev["start_time"] = old["start_time"]
            best[k] = ev

    result = list(best.values())
    result.sort(
        key=lambda e: (
            -(e.get("ai_score") or 0),
            e.get("start_date") or "9999-99-99",
            e.get("start_time") or "99:99:99",
        )
    )
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
            image_url=(item.get("performers") or [{}])[0].get("image") if item.get("performers") else None,
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
            extra={"rank": item.get("rank"), "sport_type": item.get("sport_type")},
        ))

    return out


def serpapi_queries(location: Dict[str, str], from_date: str, to_date: str, category: str) -> List[str]:
    city = location["city"]
    country_name = location["country_name"] or location["country_code"]
    year = from_date[:4] if from_date else str(date.today().year)

    if is_rome(location) and category == "sport":
        return [
            "Internazionali BNL d'Italia 2026",
            "Italian Open Rome 2026 tickets",
            "Italian Open 2026 Foro Italico Rome",
            "ATP Rome 2026 Foro Italico",
            "WTA Rome 2026 Foro Italico",
            "tennis Foro Italico Roma maggio 2026",
            "sport events Roma May 2026 tickets",
            "Roma sport eventi maggio 2026 biglietti",
            "Rome sport events May 2026 tickets",
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


def parse_text_date(text: str, from_date: str) -> Tuple[Optional[str], Optional[str], bool]:
    raw = clean_text(text)
    if not raw:
        return None, None, False

    year_default = int((from_date or today_iso())[:4])
    explicit_year_match = re.search(r"\b(20\d{2})\b", raw)
    explicit_year = int(explicit_year_match.group(1)) if explicit_year_match else year_default

    patterns = [
        r"\b(Jan|January|Feb|February|Mar|March|Apr|April|May|Jun|June|Jul|July|Aug|August|Sep|Sept|September|Oct|October|Nov|November|Dec|December|Gen|Gennaio|Febbraio|Marzo|Aprile|Maggio|Giugno|Luglio|Agosto|Set|Settembre|Ott|Ottobre|Novembre|Dic|Dicembre)\s+(\d{1,2})\b",
        r"\b(\d{1,2})\s+(Jan|January|Feb|February|Mar|March|Apr|April|May|Jun|June|Jul|July|Aug|August|Sep|Sept|September|Oct|October|Nov|November|Dec|December|Gen|Gennaio|Febbraio|Marzo|Aprile|Maggio|Giugno|Luglio|Agosto|Set|Settembre|Ott|Ottobre|Novembre|Dic|Dicembre)\b",
        r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](20\d{2}))?\b",
    ]

    day: Optional[int] = None
    month: Optional[int] = None
    year = explicit_year

    m = re.search(patterns[0], raw, re.I)
    if m:
        month = MONTHS.get(m.group(1).lower())
        day = int(m.group(2))
    else:
        m = re.search(patterns[1], raw, re.I)
        if m:
            day = int(m.group(1))
            month = MONTHS.get(m.group(2).lower())
        else:
            m = re.search(patterns[2], raw, re.I)
            if m:
                a = int(m.group(1))
                b = int(m.group(2))
                y = m.group(3)
                if y:
                    year = int(y)
                if a > 12:
                    day, month = a, b
                else:
                    month, day = a, b

    if not day or not month:
        return None, None, False

    try:
        d = date(year, month, day)
    except Exception:
        return None, None, False

    fd = parse_date_safe(from_date)
    if fd and d < fd and not explicit_year_match:
        try:
            d = date(year + 1, month, day)
        except Exception:
            pass

    time_match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", raw)
    t = f"{int(time_match.group(1)):02d}:{time_match.group(2)}:00" if time_match else None

    return d.isoformat(), t, True


def parse_serpapi_date(date_obj: Dict[str, Any], from_date: str, to_date: str) -> Tuple[Optional[str], Optional[str]]:
    raw = clean_text((date_obj or {}).get("when") or (date_obj or {}).get("start_date") or "")
    start = clean_text((date_obj or {}).get("start_date") or "")
    d, t, _ = parse_text_date(raw or start, from_date)
    return d, t


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
    city_aliases = city_aliases_for_search(location["city"])
    country_aliases = country_aliases_for_search(location["country_code"])
    country_code = location["country_code"]
    sites = LOCAL_SEARCH_SITES.get(country_code, [])
    official_sport_sites = OFFICIAL_SPORT_SITES.get(country_code, [])
    year = from_date[:4]
    queries: List[str] = []

    category_terms = {
        "sport": ["sport", "sports", "football", "tennis", "boxing", "basketball", "rugby", "tickets"],
        "concert": ["concerts", "music", "live", "festival", "tickets"],
        "culture": ["events", "exhibition", "theatre", "show", "tickets"],
        "": ["events", "concerts", "sport", "shows", "tickets"],
    }.get(category, ["events", category, "tickets"])

    for c in city_aliases[:2]:
        for country_name in country_aliases[:2]:
            if is_rome(location):
                queries.extend([
                    f"{c} events from {from_date} to {to_date} tickets",
                    f"{c} eventi dal {from_date} al {to_date} biglietti",
                    f"{c} sport eventi maggio {year} biglietti",
                    f"{c} Italian Open {year} Foro Italico tickets",
                    f"{c} Internazionali BNL d'Italia {year} biglietti",
                ])
            else:
                queries.append(f"{' '.join(category_terms[:4])} {c} {country_name} {from_date} {to_date} official tickets")
                queries.append(f"{c} {country_name} events {year} official tickets")

    for site in sites[:8]:
        for c in city_aliases[:2]:
            if category == "sport":
                queries.append(f"site:{site} {c} sport {year} tickets")
            elif category == "concert":
                queries.append(f"site:{site} {c} concert {year} tickets")
            else:
                queries.append(f"site:{site} {c} events {year} tickets")

    if category == "sport":
        for site in official_sport_sites[:5]:
            for c in city_aliases[:2]:
                queries.append(f"site:{site} {c} {year} tickets")

    if is_rome(location):
        queries.extend([
            "site:internazionalibnlditalia.com Internazionali BNL d'Italia 2026 biglietti",
            "site:ticketone.it Roma maggio 2026 sport biglietti",
            "site:ticketmaster.it Roma maggio 2026 sport biglietti",
            "site:sport.ticketone.it Roma tennis 2026 Foro Italico",
        ])

    seen: List[str] = []
    for q in queries:
        q = clean_text(q)
        if q and q not in seen:
            seen.append(q)

    return seen[:MAX_SEARCH_DISCOVERY_QUERIES]


def search_result_city_ok(text: str, location: Dict[str, str]) -> bool:
    s = slug(text)
    city_aliases = [slug(x) for x in city_aliases_for_search(location["city"])]

    if is_rome(location):
        return any(x in s for x in ["roma", "rome", "foro italico", "stadio olimpico"])

    return any(alias and alias in s for alias in city_aliases)


def search_result_country_ok(text: str, location: Dict[str, str]) -> bool:
    s = slug(text)
    code = location.get("country_code", "")
    country_words = [slug(x) for x in country_aliases_for_search(code)]

    if code == "IT" and any(x in s for x in ["italy", "italia"]):
        return True

    return any(w and w in s for w in country_words) or bool(search_result_city_ok(text, location))


def search_discovery_events(location: Dict[str, str], from_date: str, to_date: str, category: str) -> List[Dict[str, Any]]:
    if not SERPAPI_API_KEY:
        return []

    out: List[Dict[str, Any]] = []
    queries = search_discovery_queries(location, from_date, to_date, category)

    for q in queries:
        params = {
            "engine": "google",
            "q": q,
            "api_key": SERPAPI_API_KEY,
            "hl": "en",
            "gl": location["country_code"].lower() if location.get("country_code") else "us",
            "num": MAX_SEARCH_DISCOVERY_RESULTS_PER_QUERY,
        }

        ok, status, data, _ = http_get_json("https://serpapi.com/search.json", params)
        if not ok or status != 200:
            continue

        results = data.get("organic_results") or []
        for item in results[:MAX_SEARCH_DISCOVERY_RESULTS_PER_QUERY]:
            title = clean_text(item.get("title") or "")
            link = item.get("link")
            snippet = clean_text(item.get("snippet") or "")
            source = clean_text(item.get("source") or "")
            displayed_link = clean_text(item.get("displayed_link") or "")
            rich = item.get("rich_snippet") or {}

            if not title or not link:
                continue

            combined = " ".join([
                title,
                snippet,
                source,
                displayed_link,
                json.dumps(rich, ensure_ascii=False)[:800],
            ])

            if title_is_bad(title, link, category):
                continue

            if not search_result_city_ok(combined, location) and not search_result_country_ok(combined, location):
                continue

            d, t, confidence = parse_text_date(combined, from_date)

            if not d:
                fd = parse_date_safe(from_date)
                if fd:
                    d = fd.isoformat()
                    confidence = False
                else:
                    continue

            cat, sub = category_from_title(title + " " + snippet, category)
            venue = ""

            venue_patterns = [
                r"\bat\s+([^|,.;]{3,80})",
                r"\bvenue[:\s]+([^|,.;]{3,80})",
                r"\bpresso\s+([^|,.;]{3,80})",
            ]
            for pat in venue_patterns:
                m = re.search(pat, snippet, re.I)
                if m:
                    venue = clean_text(m.group(1))
                    break

            if is_rome(location) and any(k in slug(title + " " + snippet + " " + link) for k in TENNIS_ROMA_KEYWORDS):
                cat, sub = "sport", "Tennis"
                if not venue:
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
                status="active",
                extra={
                    "search_discovery_query": q,
                    "search_discovery_snippet": snippet[:500],
                    "search_discovery_date_confidence": confidence,
                    "search_discovery_displayed_link": displayed_link,
                },
            ))

        time.sleep(0.05)

    return out


def roma_tennis_override(location: Dict[str, str], from_date: str, to_date: str, category: str) -> List[Dict[str, Any]]:
    if not is_rome(location) or category not in {"", "sport", "tennis"}:
        return []

    ev = make_event(
        title="Internazionali BNL d'Italia 2026 / Italian Open Rome 2026",
        category="sport",
        subcategory="Tennis",
        start_date="2026-05-17",
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
        extra={"official_sources": ["Internazionali BNL d'Italia", "ATP", "WTA"]},
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
            extra={"official_sources": ["Internazionali BNL d'Italia", "AS Roma", "SS Lazio", "CONI", "TicketOne"]},
        )]

    return []


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
        "rome_alias_fix": True,
        "city_alias_expansion": True,
        "rome_roma_parity": True,
        "hard_final_date_filter": True,
        "rome_tennis_override": True,
        "italian_open_ranking_boost": True,
        "search_discovery_engine": True,
        "search_discovery_local_sites": True,
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
        "normalized": {"city": loc["city"], "country_code": loc["country_code"]},
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
    queries = search_discovery_queries(loc, from_date, to_date, cat)
    events = search_discovery_events(loc, from_date, to_date, cat)
    return {
        "ok": True,
        "version": VERSION,
        "serpapi_api_key_present": bool(SERPAPI_API_KEY),
        "search_discovery_engine": True,
        "input": {
            "city": city,
            "country": country,
            "from_date": from_date,
            "to_date": to_date,
            "category": category,
        },
        "normalized": loc,
        "queries": queries,
        "events_count": len(events),
        "sample": events[:20],
    }


@app.get("/events")
def get_events(
    city: str = Query(...),
    country: str = Query(""),
    from_date: str = Query(default_factory=today_iso),
    to_date: str = Query(default_factory=lambda: (date.today() + timedelta(days=30)).isoformat()),
    category: str = Query(""),
) -> JSONResponse:
    loc = normalize_location(city, country)
    cat = normalize_category(category)

    fd = parse_date_safe(from_date) or date.today()
    td = parse_date_safe(to_date) or (fd + timedelta(days=30))
    if td < fd:
        td = fd

    from_iso, to_iso = fd.isoformat(), td.isoformat()

    all_events: List[Dict[str, Any]] = []
    all_events.extend(ticketmaster_events(loc, from_iso, to_iso, cat))
    all_events.extend(seatgeek_events(loc, from_iso, to_iso, cat))
    all_events.extend(roma_tennis_override(loc, from_iso, to_iso, cat))
    all_events.extend(serpapi_events(loc, from_iso, to_iso, cat))
    all_events.extend(search_discovery_events(loc, from_iso, to_iso, cat))
    all_events.extend(predict_events(loc, from_iso, to_iso, cat))
    all_events.extend(local_official_fallback(loc, from_iso, to_iso, cat))

    merged = merge_events(all_events, loc, cat, from_iso, to_iso)

    if is_rome(loc) and cat in {"", "sport", "tennis"}:
        has_tennis = any(
            "tennis" in slug(e.get("subcategory")) or any(k in slug(e.get("title")) for k in TENNIS_ROMA_KEYWORDS)
            for e in merged
        )
        override = roma_tennis_override(loc, from_iso, to_iso, cat)
        if override and not has_tennis:
            override[0]["ai_score"] = 99
            override[0]["quality_score"] = 99
            merged = merge_events(override + merged, loc, cat, from_iso, to_iso)

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
        "search_discovery_queries": search_discovery_queries(loc, from_iso, to_iso, cat),
        "sample": merged[:30],
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
