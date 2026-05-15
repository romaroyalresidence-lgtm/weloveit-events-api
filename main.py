# ============================================================
# WELOVEIT Events API
# main.py v30 - Sports Polish
#
# Provider:
# Ticketmaster + SeatGeek + SerpApi + Sports Expansion Polish
# + PredictHQ + API-Football + Japan local fallback + Eventbrite fallback
#
# Start command su Render:
#   python main.py --serve
#
# Required env vars supported:
#   TICKETMASTER_API_KEY
#   PREDICTHQ_API_KEY
#   PREDICT_API_KEY
#   PREDICTHQ_API_URL
#   PREDICT_API_URL
#   FOOTBALL_API_KEY
#   EVENTBRITE_API_KEY
#   SEATGEEK_CLIENT_ID
#   SEATGEEK_CLIENT_SECRET
#   SERPAPI_API_KEY
#
# ============================================================

import argparse
import os
import re
import math
import asyncio
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, quote_plus, urlparse

import httpx
import uvicorn
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware


VERSION = "ticketmaster-seatgeek-predicthq-football-eventbrite-serpapi-v30-sports-polish"

SERVICE_NAME = "WELOVEIT Events API"

DEFAULT_LIMIT = 50
MAX_LIMIT = 100
HTTP_TIMEOUT = 18.0


# ============================================================
# ENV
# ============================================================

TICKETMASTER_API_KEY = os.getenv("TICKETMASTER_API_KEY", "").strip()

PREDICTHQ_API_KEY = (
    os.getenv("PREDICTHQ_API_KEY", "").strip()
    or os.getenv("PREDICT_API_KEY", "").strip()
)
PREDICTHQ_API_URL = (
    os.getenv("PREDICTHQ_API_URL", "").strip()
    or os.getenv("PREDICT_API_URL", "").strip()
    or "https://api.predicthq.com/v1/events/"
)

FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY", "").strip()
EVENTBRITE_API_KEY = os.getenv("EVENTBRITE_API_KEY", "").strip()

SEATGEEK_CLIENT_ID = os.getenv("SEATGEEK_CLIENT_ID", "").strip()
SEATGEEK_CLIENT_SECRET = os.getenv("SEATGEEK_CLIENT_SECRET", "").strip()

SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "").strip()


# ============================================================
# APP
# ============================================================

app = FastAPI(title=SERVICE_NAME, version=VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "*",
        "https://www.weloveit.it",
        "https://weloveit.it",
        "http://localhost:3000",
        "http://localhost:5173",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# BASIC NORMALIZATION
# ============================================================

COUNTRY_ALIASES = {
    "italia": "IT",
    "italy": "IT",
    "it": "IT",

    "usa": "US",
    "us": "US",
    "united states": "US",
    "united states of america": "US",
    "america": "US",

    "uk": "GB",
    "gb": "GB",
    "great britain": "GB",
    "united kingdom": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "northern ireland": "GB",

    "japan": "JP",
    "giappone": "JP",
    "jp": "JP",

    "spain": "ES",
    "espana": "ES",
    "españa": "ES",
    "es": "ES",

    "canada": "CA",
    "ca": "CA",

    "france": "FR",
    "francia": "FR",
    "fr": "FR",

    "china": "CN",
    "cina": "CN",
    "cn": "CN",

    "germany": "DE",
    "germania": "DE",
    "deutschland": "DE",
    "de": "DE",

    "argentina": "AR",
    "ar": "AR",

    "brazil": "BR",
    "brasil": "BR",
    "brasile": "BR",
    "br": "BR",
}

COUNTRY_NAMES = {
    "IT": "italy",
    "US": "usa",
    "GB": "uk",
    "JP": "japan",
    "ES": "spain",
    "CA": "canada",
    "FR": "france",
    "CN": "china",
    "DE": "germany",
    "AR": "argentina",
    "BR": "brazil",
}

COUNTRY_DISPLAY = {
    "IT": "Italy",
    "US": "US",
    "GB": "Great Britain",
    "JP": "JP",
    "ES": "ES",
    "CA": "Canada",
    "FR": "FR",
    "CN": "CN",
    "DE": "DE",
    "AR": "AR",
    "BR": "BR",
}

CITY_COUNTRY_HINTS = {
    "new york": ("New York", "US", 40.7128, -74.0060),
    "nyc": ("New York", "US", 40.7128, -74.0060),
    "los angeles": ("Los Angeles", "US", 34.0522, -118.2437),
    "las vegas": ("Las Vegas", "US", 36.1699, -115.1398),
    "miami": ("Miami", "US", 25.7617, -80.1918),
    "san francisco": ("San Francisco", "US", 37.7749, -122.4194),
    "chicago": ("Chicago", "US", 41.8781, -87.6298),

    "london": ("London", "GB", 51.5074, -0.1278),
    "manchester": ("Manchester", "GB", 53.4808, -2.2426),
    "birmingham": ("Birmingham", "GB", 52.4862, -1.8904),

    "tokyo": ("Tokyo", "JP", 35.6762, 139.6503),
    "osaka": ("Osaka", "JP", 34.6937, 135.5023),
    "kyoto": ("Kyoto", "JP", 35.0116, 135.7681),
    "nagoya": ("Nagoya", "JP", 35.1815, 136.9066),
    "yokohama": ("Yokohama", "JP", 35.4437, 139.6380),

    "roma": ("Rome", "IT", 41.9028, 12.4964),
    "rome": ("Rome", "IT", 41.9028, 12.4964),
    "milano": ("Milan", "IT", 45.4642, 9.1900),
    "milan": ("Milan", "IT", 45.4642, 9.1900),

    "paris": ("Paris", "FR", 48.8566, 2.3522),
    "berlin": ("Berlin", "DE", 52.5200, 13.4050),
    "madrid": ("Madrid", "ES", 40.4168, -3.7038),
    "barcelona": ("Barcelona", "ES", 41.3874, 2.1686),
    "toronto": ("Toronto", "CA", 43.6532, -79.3832),
    "montreal": ("Montreal", "CA", 45.5017, -73.5673),
    "vancouver": ("Vancouver", "CA", 49.2827, -123.1207),
    "beijing": ("Beijing", "CN", 39.9042, 116.4074),
    "shanghai": ("Shanghai", "CN", 31.2304, 121.4737),
    "buenos aires": ("Buenos Aires", "AR", -34.6037, -58.3816),
    "sao paulo": ("Sao Paulo", "BR", -23.5558, -46.6396),
    "rio de janeiro": ("Rio de Janeiro", "BR", -22.9068, -43.1729),
}

COUNTRY_CAPITAL_FALLBACK = {
    "IT": ("Rome", 41.9028, 12.4964),
    "US": ("New York", 40.7128, -74.0060),
    "GB": ("London", 51.5074, -0.1278),
    "JP": ("Tokyo", 35.6762, 139.6503),
    "ES": ("Madrid", 40.4168, -3.7038),
    "CA": ("Toronto", 43.6532, -79.3832),
    "FR": ("Paris", 48.8566, 2.3522),
    "CN": ("Beijing", 39.9042, 116.4074),
    "DE": ("Berlin", 52.5200, 13.4050),
    "AR": ("Buenos Aires", -34.6037, -58.3816),
    "BR": ("Sao Paulo", -23.5558, -46.6396),
}


def clean_str(value: Any) -> str:
    return str(value or "").strip()


def normalize_country(country: str) -> str:
    c = clean_str(country).lower()
    return COUNTRY_ALIASES.get(c, clean_str(country).upper() if len(clean_str(country)) == 2 else clean_str(country))


def normalize_city(city: str) -> str:
    c = clean_str(city)
    if not c:
        return ""
    key = c.lower()
    if key in CITY_COUNTRY_HINTS:
        return CITY_COUNTRY_HINTS[key][0]
    return c[:1].upper() + c[1:]


def infer_destination(destination: str = "", city: str = "", country: str = "") -> Dict[str, Any]:
    raw = clean_str(city or destination)
    raw_key = raw.lower()
    country_code = normalize_country(country)

    if raw_key in CITY_COUNTRY_HINTS:
        c, cc, lat, lon = CITY_COUNTRY_HINTS[raw_key]
        if not country_code:
            country_code = cc
        return {
            "city": c,
            "country_code": country_code,
            "lat": lat,
            "lon": lon,
            "is_country_search": False,
            "destination": raw,
        }

    # Se la destinazione è un paese, non deve diventare città.
    if raw_key in COUNTRY_ALIASES and not city:
        cc = COUNTRY_ALIASES[raw_key]
        capital, lat, lon = COUNTRY_CAPITAL_FALLBACK.get(cc, ("", None, None))
        return {
            "city": capital,
            "country_code": cc,
            "lat": lat,
            "lon": lon,
            "is_country_search": True,
            "destination": raw,
        }

    if country_code and country_code in COUNTRY_CAPITAL_FALLBACK and not raw:
        capital, lat, lon = COUNTRY_CAPITAL_FALLBACK[country_code]
        return {
            "city": capital,
            "country_code": country_code,
            "lat": lat,
            "lon": lon,
            "is_country_search": True,
            "destination": country_code,
        }

    if raw:
        return {
            "city": normalize_city(raw),
            "country_code": country_code,
            "lat": None,
            "lon": None,
            "is_country_search": False,
            "destination": raw,
        }

    return {
        "city": "",
        "country_code": country_code,
        "lat": None,
        "lon": None,
        "is_country_search": False,
        "destination": raw,
    }


def parse_date_yyyy_mm_dd(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except Exception:
        return None


def safe_iso_date(value: Any) -> Optional[str]:
    d = parse_date_yyyy_mm_dd(str(value)) if value else None
    return d.isoformat() if d else None


def safe_time(value: Any) -> Optional[str]:
    if not value:
        return None
    text = str(value)
    m = re.search(r"(\d{1,2}):(\d{2})", text)
    if not m:
        return None
    h = int(m.group(1))
    minute = int(m.group(2))
    if 0 <= h <= 23 and 0 <= minute <= 59:
        return f"{h:02d}:{minute:02d}:00"
    return None


def month_name_from_date(value: str) -> str:
    d = parse_date_yyyy_mm_dd(value)
    if not d:
        return ""
    return d.strftime("%B %Y")


def destination_country_name(country_code: str) -> str:
    return COUNTRY_NAMES.get(country_code, (country_code or "").lower())


# ============================================================
# CATEGORY MAPS
# ============================================================

def normalize_category(category: str) -> str:
    c = clean_str(category).lower()
    if c in ("", "all", "tutte", "tutti"):
        return ""
    if c in ("concert", "concerts", "music", "musica"):
        return "concert"
    if c in ("sport", "sports", "sporting"):
        return "sport"
    if c in ("motorsport", "f1", "formula 1", "motogp"):
        return "motorsport"
    if c in ("culture", "cultura", "expo", "expos", "conference", "conferences", "show", "shows"):
        return "culture"
    if c in ("festival", "festivals"):
        return "festival"
    return c


TICKETMASTER_CLASSIFICATION = {
    "concert": "music",
    "sport": "sports",
    "motorsport": "sports",
    "culture": "arts",
    "festival": "music",
}

SEATGEEK_TYPES = {
    "concert": "concert",
    "sport": "sports",
    "motorsport": "sports",
    "culture": "",
    "festival": "concert",
}

PREDICTHQ_CATEGORY = {
    "concert": "concerts",
    "sport": "sports",
    "motorsport": "sports",
    "culture": "expos,conferences,performing-arts",
    "festival": "festivals",
}


# ============================================================
# HTTP
# ============================================================

async def fetch_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
) -> Tuple[bool, int, Dict[str, Any], str]:
    try:
        resp = await client.get(url, params=params, headers=headers, timeout=HTTP_TIMEOUT)
        status = resp.status_code
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text[:1000]}
        return 200 <= status < 300, status, data, str(resp.url)
    except Exception as exc:
        return False, 0, {"error": str(exc)}, url


# ============================================================
# EVENT SHAPE
# ============================================================

def make_event(
    *,
    title: str,
    category: str,
    subcategory: str = "",
    start_date: Optional[str],
    start_time: Optional[str] = None,
    city: str,
    country: str,
    venue: str = "",
    source_name: str,
    source_url: Optional[str] = None,
    ticket_url: Optional[str] = None,
    image_url: Optional[str] = None,
    price_min: Optional[float] = None,
    price_max: Optional[float] = None,
    currency: Optional[str] = None,
    status: str = "active",
    extra: Optional[dict] = None,
) -> Dict[str, Any]:
    now = datetime.utcnow().isoformat() + "+00:00"
    e = {
        "title": clean_str(title),
        "category": normalize_category(category) or clean_str(category) or "culture",
        "subcategory": clean_str(subcategory) or clean_str(category) or "General",
        "start_date": start_date,
        "start_time": start_time,
        "city": clean_str(city),
        "country": clean_str(country),
        "venue": clean_str(venue),
        "source_name": clean_str(source_name),
        "source_url": source_url,
        "ticket_url": ticket_url or source_url,
        "image_url": image_url,
        "price_min": price_min,
        "price_max": price_max,
        "currency": currency,
        "is_vip_available": False,
        "status": status,
        "created_at": now,
        "updated_at": now,
    }
    if extra:
        e.update(extra)
    return e


# ============================================================
# TICKETMASTER
# ============================================================

async def fetch_ticketmaster_events(
    client: httpx.AsyncClient,
    city: str,
    country_code: str,
    from_date: str,
    to_date: str,
    category: str,
    limit: int,
) -> List[dict]:
    if not TICKETMASTER_API_KEY:
        return []

    params = {
        "apikey": TICKETMASTER_API_KEY,
        "size": min(limit, 100),
        "sort": "date,asc",
        "startDateTime": f"{from_date}T00:00:00Z",
        "endDateTime": f"{to_date}T23:59:59Z",
    }

    if city:
        params["city"] = city
    if country_code:
        params["countryCode"] = country_code

    segment = TICKETMASTER_CLASSIFICATION.get(category)
    if segment:
        params["classificationName"] = segment

    ok, status, data, _ = await fetch_json(
        client,
        "https://app.ticketmaster.com/discovery/v2/events.json",
        params=params,
    )

    if not ok:
        return []

    events = data.get("_embedded", {}).get("events", []) or []
    output = []

    for item in events:
        dates = item.get("dates", {}) or {}
        start = dates.get("start", {}) or {}
        classifications = item.get("classifications", []) or []
        first_class = classifications[0] if classifications else {}

        venue_obj = {}
        venues = item.get("_embedded", {}).get("venues", []) or []
        if venues:
            venue_obj = venues[0] or {}

        price_ranges = item.get("priceRanges", []) or []
        price_min = None
        price_max = None
        currency = None
        if price_ranges:
            price_min = price_ranges[0].get("min")
            price_max = price_ranges[0].get("max")
            currency = price_ranges[0].get("currency")

        images = item.get("images", []) or []
        image_url = None
        if images:
            # Prefer wider image.
            image_url = sorted(images, key=lambda x: x.get("width", 0), reverse=True)[0].get("url")

        event_city = venue_obj.get("city", {}).get("name") or city
        event_country = (
            venue_obj.get("country", {}).get("countryCode")
            or venue_obj.get("country", {}).get("name")
            or country_code
        )

        output.append(make_event(
            title=item.get("name", ""),
            category="sport" if (first_class.get("segment", {}).get("name", "").lower() == "sports") else category or "culture",
            subcategory=(
                first_class.get("genre", {}).get("name")
                or first_class.get("subGenre", {}).get("name")
                or first_class.get("segment", {}).get("name")
                or category
            ),
            start_date=start.get("localDate"),
            start_time=safe_time(start.get("localTime")),
            city=event_city,
            country=event_country,
            venue=venue_obj.get("name", ""),
            source_name="Ticketmaster",
            source_url=item.get("url"),
            ticket_url=item.get("url"),
            image_url=image_url,
            price_min=price_min,
            price_max=price_max,
            currency=currency,
        ))

    return output


# ============================================================
# SEATGEEK
# ============================================================

async def fetch_seatgeek_events(
    client: httpx.AsyncClient,
    city: str,
    country_code: str,
    lat: Optional[float],
    lon: Optional[float],
    from_date: str,
    to_date: str,
    category: str,
    limit: int,
) -> List[dict]:
    if not SEATGEEK_CLIENT_ID:
        return []

    params = {
        "client_id": SEATGEEK_CLIENT_ID,
        "per_page": min(limit, 100),
        "sort": "datetime_local.asc",
        "datetime_local.gte": f"{from_date}T00:00:00",
        "datetime_local.lte": f"{to_date}T23:59:59",
    }

    sg_type = SEATGEEK_TYPES.get(category)
    if sg_type:
        params["type"] = sg_type

    if lat is not None and lon is not None:
        params["lat"] = lat
        params["lon"] = lon
        params["range"] = "50mi"
    elif city:
        params["venue.city"] = city

    ok, status, data, _ = await fetch_json(
        client,
        "https://api.seatgeek.com/2/events",
        params=params,
    )

    if not ok:
        return []

    output = []

    for item in data.get("events", []) or []:
        venue = item.get("venue", {}) or {}

        output.append(make_event(
            title=item.get("title", ""),
            category="concert" if item.get("type") == "concert" else ("sport" if "sports" in str(item.get("type", "")) else category or item.get("type") or "culture"),
            subcategory=item.get("type") or category or "General",
            start_date=safe_iso_date(item.get("datetime_local")),
            start_time=safe_time(item.get("datetime_local")),
            city=venue.get("city") or city,
            country=venue.get("country") or country_code,
            venue=venue.get("name", ""),
            source_name="SeatGeek",
            source_url=item.get("url"),
            ticket_url=item.get("url"),
            image_url=item.get("performers", [{}])[0].get("image") if item.get("performers") else None,
            price_min=item.get("stats", {}).get("lowest_price"),
            price_max=item.get("stats", {}).get("highest_price"),
            currency=None,
            extra={"seatgeek_id": item.get("id")},
        ))

    return output


# ============================================================
# SERPAPI GOOGLE EVENTS
# ============================================================

def serpapi_query_variants(city: str, country_code: str, from_date: str, to_date: str, category: str) -> List[str]:
    country_name = destination_country_name(country_code)
    start_label = month_name_from_date(from_date)
    end_label = month_name_from_date(to_date)
    year = (parse_date_yyyy_mm_dd(from_date) or date.today()).year

    place = f"{city} {country_name}".strip()

    if category == "concert":
        return [
            f"concerts {place} {start_label} {end_label} {year}",
            f"music events {place} {year}",
            f"live music {place} tickets {year}",
        ]

    if category in ("sport", "motorsport"):
        return [
            f"sports events {place} {year}",
            f"{place} sports tickets {year}",
            f"boxing {city} {year}",
            f"rugby {city} {year}",
            f"NFL {city} {year}",
            f"basketball {city} {year}",
            f"motorsport {country_name} {year}",
        ]

    if category == "culture":
        return [
            f"exhibitions shows {place} {start_label} {end_label} {year}",
            f"culture events {place} {year}",
            f"expos conferences {place} {year}",
        ]

    return [
        f"events {place} {start_label} {end_label} {year}",
        f"concerts festivals {place} {start_label} {end_label} {year}",
        f"sports events {place} {start_label} {end_label} {year}",
        f"exhibitions shows {place} {start_label} {end_label} {year}",
        f"{city} events {country_name} {start_label} {end_label} official tickets",
    ]


def parse_serpapi_date(date_obj: dict, fallback_year: int) -> Tuple[Optional[str], Optional[str]]:
    if not date_obj:
        return None, None

    when = str(date_obj.get("when") or "")
    start_date_text = str(date_obj.get("start_date") or "")

    # Examples:
    # "Sat, Jul 4, 16:00 – 23:59"
    # "Sun 4 Oct, 14:30–17:30"
    # "May 10, 14:30 – May 23, 14:30"
    text = when or start_date_text

    month_map = {
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

    # Month day
    m = re.search(
        r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t|tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(\d{1,2})\b",
        text,
        flags=re.I,
    )
    if not m:
        # Day month, es. "Sun 4 Oct"
        m = re.search(
            r"\b(\d{1,2})\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t|tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b",
            text,
            flags=re.I,
        )
        if m:
            day = int(m.group(1))
            month = month_map.get(m.group(2).lower()[:3])
        else:
            day = None
            month = None
    else:
        month = month_map.get(m.group(1).lower()[:3])
        day = int(m.group(2))

    start_date = None
    if month and day:
        try:
            start_date = date(fallback_year, month, day).isoformat()
        except Exception:
            start_date = None

    start_time = safe_time(text)
    return start_date, start_time


def extract_serpapi_venue_city(address_list: List[str], fallback_city: str) -> Tuple[str, str]:
    if not address_list:
        return "", fallback_city

    first = address_list[0] if len(address_list) > 0 else ""
    second = address_list[1] if len(address_list) > 1 else ""

    venue = clean_str(first).split(",")[0].strip()

    city = fallback_city
    if second:
        city = clean_str(second).split(",")[0].strip() or fallback_city

    return venue, city


async def fetch_serpapi_events(
    client: httpx.AsyncClient,
    city: str,
    country_code: str,
    from_date: str,
    to_date: str,
    category: str,
    limit: int,
) -> List[dict]:
    if not SERPAPI_API_KEY:
        return []

    gl = (country_code or "US").lower()
    fallback_year = (parse_date_yyyy_mm_dd(from_date) or date.today()).year
    queries = serpapi_query_variants(city, country_code, from_date, to_date, category)

    output = []
    seen = set()

    for q in queries[:8]:
        params = {
            "engine": "google_events",
            "q": q,
            "api_key": SERPAPI_API_KEY,
            "hl": "en",
            "gl": gl,
        }
        ok, status, data, _ = await fetch_json(
            client,
            "https://serpapi.com/search.json",
            params=params,
        )
        if not ok:
            continue

        for item in data.get("events_results", []) or []:
            title = clean_str(item.get("title"))
            if not title:
                continue

            date_obj = item.get("date", {}) or {}
            start_date, start_time = parse_serpapi_date(date_obj, fallback_year)
            address = item.get("address", []) or []
            venue, event_city = extract_serpapi_venue_city(address, city)
            link = item.get("link")

            # Filtro località: evita risultati fuori città salvo venue molto vicine/nomi noti.
            city_key = city.lower()
            address_text = " ".join(address).lower()
            if city_key and city_key not in address_text and city_key not in (event_city or "").lower():
                nearby_ok = False
                if city_key == "london" and any(x in address_text for x in ["wembley", "twickenham", "tottenham", "brentford"]):
                    nearby_ok = True
                if city_key == "tokyo" and any(x in address_text for x in ["saitama", "yokohama", "chiba", "minato city"]):
                    nearby_ok = True
                if not nearby_ok:
                    continue

            key = (title.lower(), start_date, venue.lower())
            if key in seen:
                continue
            seen.add(key)

            event_category = category or "culture"
            if category in ("sport", "motorsport") or any(x in title.lower() for x in [
                "nfl", "boxing", "rugby", "football", "basketball", "wwe", "aew", "athletics", "cricket", "padel", "motogp", "f1", "formula 1"
            ]):
                event_category = "sport"

            output.append(make_event(
                title=title,
                category=event_category,
                subcategory=event_category,
                start_date=start_date,
                start_time=start_time,
                city=city,
                country=country_code,
                venue=venue,
                source_name="SerpApi",
                source_url=link,
                ticket_url=link,
                image_url=item.get("thumbnail"),
                extra={"serpapi_query": q},
            ))

            if len(output) >= limit:
                break

        if len(output) >= limit:
            break

    return output


# ============================================================
# SPORTS EXPANSION ENGINE
# ============================================================

SPORTS_EXPANSION_DOMAINS_TRUST = [
    "ticketmaster.",
    "nfl.com",
    "eticketing.co.uk",
    "allianzstadiumtwickenham.com",
    "ovoarena.co.uk",
    "alexandrapalace.com",
    "koobit.com",
    "ents24.com",
    "eventbrite.",
    "universe.com",
    "engagehospitality.co.uk",
    "hospitalityfinder.co.uk",
    "tickettailor.com",
    "todaytix.com",
    "viagogo.",
]

def sports_expansion_queries(city: str, country_code: str, from_date: str, to_date: str) -> List[str]:
    year = (parse_date_yyyy_mm_dd(from_date) or date.today()).year
    city_l = city

    country_name = destination_country_name(country_code)

    base = [
        f"boxing {city_l} {year}",
        f"boxing {city_l} tickets {year}",
        f"boxing events {city_l} {year}",
        f"boxing fights {city_l} {year}",

        f"MMA {city_l} {year}",
        f"MMA tickets {city_l} {year}",
        f"mma events {city_l} {year}",
        f"ufc events {city_l} {year}",

        f"NFL {city_l} {year}",
        f"NFL tickets {city_l} {year}",
        f"NFL games {city_l} {year}",
        f"american football games {city_l} {year}",

        f"basketball {city_l} {year}",
        f"basketball tickets {city_l} {year}",

        f"rugby {city_l} {year}",
        f"rugby tickets {city_l} {year}",

        f"Formula 1 {country_name} {year}",
        f"F1 tickets {country_name} {year}",
        f"MotoGP {country_name} {year}",
        f"MotoGP tickets {country_name} {year}",

        f"WWE {city_l} {year}",
        f"wrestling {city_l} tickets {year}",
    ]

    if city.lower() == "london":
        base = [
            f"NFL London Games {year}",
            f"NFL London Games tickets {year}",
            f"rugby Twickenham {year}",
            f"rugby Twickenham tickets {year}",
            f"Wembley Stadium sport {year}",
            f"Wembley Stadium sport tickets {year}",
            f"boxing London {year}",
            f"boxing London tickets {year}",
            f"Betfred Fight Night London {year}",
            f"York Hall boxing {year}",
            f"basketball London Lions {year}",
            f"London athletics Diamond League {year}",
            f"cricket Lords London {year}",
            f"WWE London {year}",
            f"AEW London Wembley {year}",
        ] + base

    return list(dict.fromkeys(base))[:24]


def sports_expansion_relevance(event: dict, city: str, country_code: str) -> bool:
    title = (event.get("title") or "").lower()
    venue = (event.get("venue") or "").lower()
    url = (event.get("source_url") or event.get("ticket_url") or "").lower()

    bad_terms = [
        "concert",
        "live tour",
        "spotify.com/concert",
        "peter kay live",
        "harry styles",
        "music",
    ]
    if any(x in title or x in url for x in bad_terms):
        return False

    # Eventi sportivi veri o molto probabili.
    good_terms = [
        "nfl",
        "american football",
        "boxing",
        "fight night",
        "rugby",
        "premiership",
        "nations championship",
        "football",
        "fa cup",
        "efl",
        "basketball",
        "wwe",
        "aew",
        "wrestling",
        "athletics",
        "diamond league",
        "cricket",
        "padel",
        "motogp",
        "formula 1",
        "f1",
        "strongman",
        "netball",
    ]

    good_venue_terms = [
        "wembley",
        "twickenham",
        "allianz stadium",
        "tottenham hotspur stadium",
        "york hall",
        "the o2",
        "ovo arena",
        "london stadium",
        "copper box",
        "stonex",
        "stamford bridge",
        "craven cottage",
        "lord",
    ]

    return any(x in title for x in good_terms) or any(x in venue for x in good_venue_terms)


async def fetch_sports_expansion_events(
    client: httpx.AsyncClient,
    city: str,
    country_code: str,
    from_date: str,
    to_date: str,
    limit: int,
) -> List[dict]:
    if not SERPAPI_API_KEY:
        return []

    fallback_year = (parse_date_yyyy_mm_dd(from_date) or date.today()).year
    gl = (country_code or "US").lower()
    queries = sports_expansion_queries(city, country_code, from_date, to_date)

    output = []
    seen = set()

    for q in queries:
        params = {
            "engine": "google_events",
            "q": q,
            "api_key": SERPAPI_API_KEY,
            "hl": "en",
            "gl": gl,
        }
        ok, status, data, _ = await fetch_json(
            client,
            "https://serpapi.com/search.json",
            params=params,
        )
        if not ok:
            continue

        for item in data.get("events_results", []) or []:
            title = clean_str(item.get("title"))
            if not title:
                continue

            date_obj = item.get("date", {}) or {}
            start_date, start_time = parse_serpapi_date(date_obj, fallback_year)
            address = item.get("address", []) or []
            venue, event_city = extract_serpapi_venue_city(address, city)
            link = item.get("link")
            address_text = " ".join(address).lower()

            city_key = city.lower()
            if city_key and city_key not in address_text and city_key not in (event_city or "").lower():
                nearby_ok = False
                if city_key == "london" and any(x in address_text for x in ["wembley", "twickenham", "tottenham", "brentford", "purfleet"]):
                    nearby_ok = True
                if not nearby_ok:
                    continue

            event = make_event(
                title=title,
                category="sport",
                subcategory="Sport",
                start_date=start_date,
                start_time=start_time,
                city=city,
                country=country_code,
                venue=venue,
                source_name="Sports Expansion",
                source_url=link,
                ticket_url=link,
                image_url=item.get("thumbnail"),
                extra={"sports_expansion_query": q},
            )

            if not sports_expansion_relevance(event, city, country_code):
                continue

            key = (title.lower(), start_date, venue.lower())
            if key in seen:
                continue
            seen.add(key)

            output.append(event)

            if len(output) >= limit:
                break

        if len(output) >= limit:
            break

    return output


def sports_official_fallback(city: str, country_code: str, from_date: str, to_date: str, category: str) -> List[dict]:
    if category not in ("sport", "motorsport"):
        return []

    sports = [
        ("Boxing events, schedule and ticket sources", "Boxing", ["BoxRec", "DAZN", "Top Rank", "Matchroom Boxing", "PBC"]),
        ("MMA events, schedule and ticket sources", "MMA", ["UFC", "PFL", "ONE Championship"]),
        ("NFL events, schedule and ticket sources", "NFL", ["NFL", "Ticketmaster", "SeatGeek"]),
        ("MotoGP events, schedule and ticket sources", "MotoGP", ["MotoGP"]),
        ("Formula 1 events, schedule and ticket sources", "Formula 1", ["Formula 1"]),
    ]

    output = []
    for title, subcat, official_sources in sports:
        q = f"{subcat} {city} {country_code} {from_date[:4]} official schedule tickets {' '.join(official_sources)}"
        url = f"https://www.google.com/search?q={quote_plus(q)}"
        output.append(make_event(
            title=title,
            category="motorsport" if subcat in ("MotoGP", "Formula 1") else "sport",
            subcategory=subcat,
            start_date=from_date,
            city=city,
            country=country_code,
            venue=" / ".join(official_sources),
            source_name="Sports Official Fallback",
            source_url=url,
            ticket_url=url,
            status="fallback",
            extra={
                "official_sources": official_sources,
                "ai_score": 62,
                "quality_score": 62,
            },
        ))
    return output


# ============================================================
# PREDICTHQ
# ============================================================

async def fetch_predicthq_events(
    client: httpx.AsyncClient,
    city: str,
    country_code: str,
    from_date: str,
    to_date: str,
    category: str,
    limit: int,
) -> List[dict]:
    if not PREDICTHQ_API_KEY:
        return []

    headers = {
        "Authorization": f"Bearer {PREDICTHQ_API_KEY}",
        "Accept": "application/json",
    }

    params = {
        "limit": min(limit, 100),
        "sort": "start",
        "active.gte": from_date,
        "active.lte": to_date,
    }

    if country_code:
        params["country"] = country_code

    if city:
        params["q"] = city

    phq_cat = PREDICTHQ_CATEGORY.get(category)
    if phq_cat:
        params["category"] = phq_cat

    ok, status, data, _ = await fetch_json(
        client,
        PREDICTHQ_API_URL,
        params=params,
        headers=headers,
    )

    if not ok:
        return []

    results = data.get("results", []) or []
    output = []

    for item in results:
        title = item.get("title", "")
        start = item.get("start") or item.get("start_local")
        labels = item.get("labels") or []
        phq_category = item.get("category") or category or "culture"

        loc_city = city
        entities = item.get("entities") or []
        venue = ""
        for ent in entities:
            if ent.get("type") in ("venue", "location"):
                venue = ent.get("name") or venue

        subcategory = labels[0] if labels else phq_category

        event_category = "sport" if phq_category == "sports" else (
            "concert" if phq_category == "concerts" else category or "culture"
        )

        ticket_q = f"{title} {subcategory} {city} {country_code} {safe_iso_date(start) or from_date} official tickets"
        ticket_url = f"https://www.google.com/search?q={quote_plus(ticket_q)}"

        output.append(make_event(
            title=title,
            category=event_category,
            subcategory=subcategory,
            start_date=safe_iso_date(start),
            start_time=safe_time(start),
            city=loc_city,
            country=country_code,
            venue=venue,
            source_name="PredictHQ",
            source_url=None,
            ticket_url=ticket_url,
            image_url=None,
            extra={
                "rank": item.get("rank"),
                "sport_type": item.get("phq_attendance"),
            },
        ))

    return output


# ============================================================
# API-FOOTBALL LIGHT SUPPORT
# ============================================================

async def fetch_api_football_events(
    client: httpx.AsyncClient,
    city: str,
    country_code: str,
    from_date: str,
    to_date: str,
    category: str,
    limit: int,
) -> List[dict]:
    # Lasciato conservativo: API-Football cambia endpoint/provider a seconda del piano.
    # Se FOOTBALL_API_KEY è presente, usiamo comunque fallback ufficiale via Google search
    # invece di rischiare chiamate errate.
    if not FOOTBALL_API_KEY or category not in ("sport", ""):
        return []

    q = f"football matches {city} {country_code} {from_date[:4]} official tickets"
    url = f"https://www.google.com/search?q={quote_plus(q)}"
    return [
        make_event(
            title="Football matches, fixtures and ticket sources",
            category="sport",
            subcategory="Football",
            start_date=from_date,
            city=city,
            country=country_code,
            venue="Official football fixtures / clubs",
            source_name="API-Football Fallback",
            source_url=url,
            ticket_url=url,
            status="fallback",
            extra={"ai_score": 58, "quality_score": 58},
        )
    ]


# ============================================================
# EVENTBRITE FALLBACK
# ============================================================

def eventbrite_search_url(country_code: str, city: str, query: str = "") -> str:
    country_name = destination_country_name(country_code)
    city_slug = clean_str(city).lower().replace(" ", "-")
    country_slug = clean_str(country_name).lower().replace(" ", "-")

    if country_code == "JP":
        base = f"https://www.eventbrite.com/d/japan--{city_slug}/"
    elif country_code == "GB":
        base = f"https://www.eventbrite.co.uk/d/united-kingdom--{city_slug}/"
    else:
        base = f"https://www.eventbrite.com/d/{country_slug}--{city_slug}/"

    if query:
        return base + "?" + urlencode({"q": query})
    return base


def apply_eventbrite_search_urls(events: List[dict]) -> List[dict]:
    for e in events:
        if not e.get("eventbrite_search_url"):
            e["eventbrite_search_url"] = eventbrite_search_url(
                e.get("country", ""),
                e.get("city", ""),
                e.get("title", ""),
            )
    return events


# ============================================================
# JAPAN LOCAL FALLBACK
# ============================================================

def japan_local_fallback(city: str, country_code: str, from_date: str, to_date: str, category: str) -> List[dict]:
    if country_code != "JP":
        return []

    sources = ["Ticket Pia", "Lawson Ticket", "eplus", "J.League", "NPB"]
    q = f"{city} Japan events {from_date} {to_date} official tickets Pia Lawson eplus"
    url = f"https://www.google.com/search?q={quote_plus(q)}"

    return [
        make_event(
            title="Japan local event ticket sources",
            category="culture" if category != "sport" else "sport",
            subcategory="Local ticket sources",
            start_date=from_date,
            city=city,
            country="JP",
            venue=" / ".join(sources),
            source_name="Japan Local Fallback",
            source_url=url,
            ticket_url=url,
            currency="JPY",
            status="fallback",
            extra={
                "local_sources": sources,
                "ai_score": 60,
                "quality_score": 60,
            },
        )
    ]


# ============================================================
# FILTERS AND SCORING
# ============================================================

def text_key(value: str) -> str:
    value = (value or "").lower().strip()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def is_parking_event(event: dict) -> bool:
    t = text_key(event.get("title", ""))
    sub = text_key(event.get("subcategory", ""))
    return t.startswith("parking ") or " parking " in f" {t} " or sub == "parking"


def is_low_quality_conference(event: dict) -> bool:
    t = text_key(event.get("title", ""))
    sub = text_key(event.get("subcategory", ""))
    if event.get("category") == "sport":
        return False

    bad = [
        "webinar",
        "online conference",
        "virtual conference",
        "training course",
        "workshop online",
    ]
    return any(x in t or x in sub for x in bad)


def base_score(event: dict, requested_category: str, requested_city: str, requested_country: str) -> int:
    score = 70

    src = event.get("source_name", "")
    source_bonus = {
        "Ticketmaster": 24,
        "SeatGeek": 20,
        "Sports Expansion": 12,
        "SerpApi": 10,
        "PredictHQ": 6,
        "Eventbrite": 4,
        "Japan Local Fallback": -8,
        "Sports Official Fallback": -10,
        "API-Football Fallback": -12,
    }
    score += source_bonus.get(src, 0)

    if event.get("image_url"):
        score += 3
    if event.get("ticket_url") and not str(event.get("ticket_url")).startswith("https://www.google.com/search"):
        score += 4
    if event.get("price_min") is not None:
        score += 2

    if requested_category and normalize_category(event.get("category", "")) == requested_category:
        score += 8

    if requested_city and requested_city.lower() in str(event.get("city", "")).lower():
        score += 4

    if requested_country:
        ev_cc = normalize_country(event.get("country", ""))
        if ev_cc == requested_country:
            score += 3

    if is_low_quality_conference(event):
        score -= 18

    if is_parking_event(event):
        score -= 50

    if event.get("status") == "fallback":
        score -= 8

    return max(0, min(99, int(score)))


def score_events(events: List[dict], category: str, city: str, country_code: str) -> List[dict]:
    for e in events:
        score = base_score(e, category, city, country_code)
        if "ai_score" in e:
            try:
                score = min(score, int(e["ai_score"])) if e.get("status") == "fallback" else max(score, int(e["ai_score"]))
            except Exception:
                pass
        e["ai_score"] = score
        e["quality_score"] = score
        e["is_low_quality_conference"] = is_low_quality_conference(e)
    return events


# ============================================================
# V30 SPORTS POLISH
# ============================================================

def _safe_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except Exception:
        return None


def _clean_text_key(value: str) -> str:
    value = (value or "").lower().strip()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _normalize_venue_key(value: str) -> str:
    value = _clean_text_key(value)

    replacements = {
        "allianz stadium twickenham": "allianz stadium",
        "twickenham stadium": "allianz stadium",
        "allianz stadium": "allianz stadium",
        "ovo arena wembley": "ovo arena wembley",
        "wembley stadium": "wembley stadium",
        "the o2": "the o2",
        "o2 arena": "the o2",
        "york hall leisure centre": "york hall",
        "york hall": "york hall",
        "tottenham hotspur stadium": "tottenham hotspur stadium",
        "copper box arena": "copper box arena",
        "queen elizabeth olympic park copper box arena": "copper box arena",
        "london stadium": "london stadium",
        "stonex stadium": "stonex stadium",
        "craven cottage": "craven cottage",
        "stamford bridge": "stamford bridge",
    }

    return replacements.get(value, value)


def _normalize_country_code(country: str) -> str:
    return normalize_country(country)


def _sport_subcategory_from_title(title: str, current: str = "") -> str:
    t = _clean_text_key(title)
    cur = (current or "").strip()

    rules = [
        (
            "NFL",
            [
                "nfl",
                "american football",
                "colts",
                "commanders",
                "eagles",
                "jaguars",
                "union jack classic",
                "kansas v arizona",
                "arizona state",
            ],
        ),
        (
            "Boxing",
            [
                "boxing",
                "fight night",
                "box cup",
                "championship boxing",
                "mayhem in london",
                "final countdown",
                "betfred fight",
                "edge of glory",
            ],
        ),
        ("MMA", ["mma", "ufc", "pfl", "one championship", "cage warriors", "bellator"]),
        (
            "Rugby Union",
            [
                "rugby union",
                "harlequins",
                "saracens",
                "prem final",
                "gallagher prem",
                "nations championship",
                "england v australia",
                "england v japan",
                "england v new zealand",
                "barbarians",
                "twickenham",
                "allianz stadium",
                "gloucester rugby",
                "exeter chiefs",
            ],
        ),
        ("Rugby", ["rugby", "challenge cup"]),
        (
            "Soccer",
            [
                "chelsea",
                "arsenal",
                "west ham",
                "fulham",
                "fa cup",
                "efl",
                "world cup",
                "baller league",
                "manchester city",
                "manchester united",
                "newcastle united",
                "leeds united",
            ],
        ),
        (
            "Basketball",
            [
                "basketball",
                "lions",
                "slb",
                "super league basketball",
                "leicester riders",
                "newcastle eagles",
                "sheffield sharks",
                "bristol flyers",
                "caledonia gladiators",
            ],
        ),
        ("Wrestling", ["wwe", "aew", "wrestling", "smackdown", "raw", "revolution pro wrestling"]),
        ("Track & Field", ["athletics", "diamond league", "track field"]),
        ("Cricket", ["cricket", "lord s", "woakes", "gower", "flower"]),
        ("Padel", ["padel"]),
        ("Strongman", ["strongman"]),
        ("eSports", ["valorant", "esports", "e sports"]),
        ("Motorsport", ["formula 1", "f1", "motogp", "grand prix"]),
        ("Netball", ["netball", "mavericks", "pulse"]),
    ]

    for label, needles in rules:
        if any(n in t for n in needles):
            return label

    bad_generic = {"sport", "sports", "miscellaneous", "men professional", "college", "other", ""}
    if cur.lower() in bad_generic:
        return "Sport"

    return cur


def _domain_of(url: str) -> str:
    try:
        return urlparse(url or "").netloc.lower().replace("www.", "")
    except Exception:
        return ""


def _is_suspicious_title_domain(event: dict) -> bool:
    title = _clean_text_key(event.get("title"))
    url = event.get("source_url") or event.get("ticket_url") or ""
    domain = _domain_of(url)

    if not title or not domain:
        return False

    trusted_generic = [
        "ticketmaster.",
        "ticketweb.",
        "nfl.com",
        "eticketing.co.uk",
        "universe.com",
        "allianzstadiumtwickenham.com",
        "ovoarena.co.uk",
        "alexandrapalace.com",
        "engagehospitality.co.uk",
        "hospitalityfinder.co.uk",
        "koobit.com",
        "ents24.com",
        "eventbrite.",
        "viagogo.",
        "seatgeek.",
        "todaytix.com",
        "tickettailor.com",
    ]

    if any(x in domain for x in trusted_generic):
        return False

    suspicious_pairs = [
        ("chelsea", "southendunited"),
        ("manchester city", "southendunited"),
        ("harry styles", "gigtotem"),
        ("nfl", "spotify"),
        ("american football", "spotify"),
        ("football", "spotify"),
        ("rugby", "flicks.com.au"),
    ]

    return any(a in title and b in domain for a, b in suspicious_pairs)


def _is_low_value_sports_class(event: dict) -> bool:
    title = _clean_text_key(event.get("title"))
    source = (event.get("source_name") or "").lower()
    category = (event.get("category") or "").lower()

    class_words = [
        "class",
        "classes",
        "session",
        "sessions",
        "self defence",
        "self defense",
        "ladies only",
        "women s only",
        "fitness",
        "workout",
        "training",
        "timetable",
        "gym",
    ]

    if category not in ("sport", "sports"):
        return False

    if "sports expansion" not in source and "serpapi" not in source:
        return False

    return any(w in title for w in class_words)


def _event_unique_key(event: dict) -> str:
    title_key = _clean_text_key(event.get("title"))
    venue_key = _normalize_venue_key(event.get("venue"))
    date_key = str(event.get("start_date") or "")

    if venue_key:
        return f"{title_key}|{date_key}|{venue_key}"

    return f"{title_key}|{date_key}"


def _event_soft_key(event: dict) -> str:
    title_key = _clean_text_key(event.get("title"))
    date_key = str(event.get("start_date") or "")

    title_key = title_key.replace("venue premium tickets", "")
    title_key = title_key.replace("register interest", "")
    title_key = title_key.replace("tickets", "")
    title_key = re.sub(r"\s+", " ", title_key).strip()

    return f"{title_key}|{date_key}"


def polish_sports_event(event: dict) -> dict:
    e = dict(event)

    e["country"] = _normalize_country_code(e.get("country"))

    if (e.get("category") or "").lower() in ("sport", "sports", "motorsport"):
        if e.get("category") != "motorsport":
            e["category"] = "sport"
        e["subcategory"] = _sport_subcategory_from_title(
            e.get("title", ""),
            e.get("subcategory", "")
        )

    current_score = int(e.get("ai_score") or e.get("quality_score") or 70)

    if _is_suspicious_title_domain(e):
        new_score = max(0, current_score - 18)
        e["ai_score"] = new_score
        e["quality_score"] = new_score
        e["suspicious_domain_match"] = True

    source_name = (e.get("source_name") or "").lower()
    if source_name in ("sports expansion", "serpapi"):
        new_score = min(int(e.get("ai_score") or 70), 82)
        e["ai_score"] = new_score
        e["quality_score"] = min(int(e.get("quality_score") or new_score), new_score)

    # Non mostrare date passate mascherate da created_at.
    if not e.get("start_date"):
        e["ai_score"] = min(int(e.get("ai_score") or 50), 55)
        e["quality_score"] = min(int(e.get("quality_score") or 50), 55)

    return e


def final_date_gate(events: List[dict], from_date: str = None, to_date: str = None) -> List[dict]:
    start = _safe_date(from_date)
    end = _safe_date(to_date)

    filtered = []

    for e in events:
        d = _safe_date(e.get("start_date"))

        if start and d and d < start:
            continue

        if end and d and d > end:
            continue

        filtered.append(e)

    return filtered


def advanced_dedupe_events(events: List[dict]) -> List[dict]:
    best = {}

    source_priority = {
        "Ticketmaster": 100,
        "SeatGeek": 92,
        "API-Football": 88,
        "Sports Expansion": 75,
        "SerpApi": 70,
        "PredictHQ": 65,
        "Eventbrite": 60,
        "Japan Local Fallback": 30,
        "Sports Official Fallback": 25,
        "API-Football Fallback": 22,
    }

    for e in events:
        key = _event_unique_key(e)
        soft_key = _event_soft_key(e)

        src = e.get("source_name") or ""
        priority = source_priority.get(src, 50)
        score = int(e.get("ai_score") or e.get("quality_score") or 0)

        rank_value = priority * 1000 + score
        candidates = [key, soft_key]

        existing_key = None
        for k in candidates:
            if k in best:
                existing_key = k
                break

        if existing_key is None:
            best[key] = (rank_value, e)
            if soft_key != key:
                best[soft_key] = (rank_value, e)
        else:
            old_rank, old_event = best[existing_key]
            if rank_value > old_rank:
                for k in candidates:
                    best[k] = (rank_value, e)

    seen_ids = set()
    output = []

    for _, event in best.values():
        identity = (
            _clean_text_key(event.get("title")),
            str(event.get("start_date") or ""),
            _normalize_venue_key(event.get("venue")),
            event.get("ticket_url") or event.get("source_url") or "",
        )

        if identity in seen_ids:
            continue

        seen_ids.add(identity)
        output.append(event)

    return output


def sports_polish_v30(events: List[dict], from_date: str = None, to_date: str = None) -> List[dict]:
    polished = []

    for e in events:
        if is_parking_event(e):
            continue

        if _is_low_value_sports_class(e):
            continue

        polished.append(polish_sports_event(e))

    polished = final_date_gate(polished, from_date=from_date, to_date=to_date)
    polished = advanced_dedupe_events(polished)

    polished.sort(
        key=lambda x: (
            -(int(x.get("ai_score") or x.get("quality_score") or 0)),
            str(x.get("start_date") or "9999-99-99"),
            str(x.get("start_time") or "99:99:99"),
        )
    )

    return polished


# ============================================================
# MAIN SEARCH ORCHESTRATION
# ============================================================

async def collect_events(
    destination: str = "",
    city: str = "",
    country: str = "",
    from_date: str = "",
    to_date: str = "",
    category: str = "",
    limit: int = DEFAULT_LIMIT,
) -> Dict[str, Any]:
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    category = normalize_category(category)

    today = date.today().isoformat()
    if not from_date:
        from_date = today
    if not to_date:
        to_date = f"{date.today().year}-12-31"

    dest = infer_destination(destination=destination, city=city, country=country)
    norm_city = dest["city"]
    country_code = dest["country_code"]
    lat = dest["lat"]
    lon = dest["lon"]

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        tasks = [
            fetch_ticketmaster_events(client, norm_city, country_code, from_date, to_date, category, limit),
            fetch_seatgeek_events(client, norm_city, country_code, lat, lon, from_date, to_date, category, limit),
            fetch_serpapi_events(client, norm_city, country_code, from_date, to_date, category, limit),
            fetch_predicthq_events(client, norm_city, country_code, from_date, to_date, category, limit),
        ]

        if category in ("sport", "motorsport"):
            tasks.append(fetch_sports_expansion_events(client, norm_city, country_code, from_date, to_date, limit))
            tasks.append(fetch_api_football_events(client, norm_city, country_code, from_date, to_date, category, limit))

        results = await asyncio.gather(*tasks, return_exceptions=True)

    events: List[dict] = []
    source_counts = {}

    for result in results:
        if isinstance(result, Exception):
            continue
        for e in result or []:
            events.append(e)
            src = e.get("source_name", "Unknown")
            source_counts[src] = source_counts.get(src, 0) + 1

    # Fallback locali/ufficiali: aggiunti solo se pochi risultati o categoria specifica.
    if country_code == "JP" and len(events) < 8:
        jf = japan_local_fallback(norm_city, country_code, from_date, to_date, category)
        events.extend(jf)
        source_counts["Japan Local Fallback"] = source_counts.get("Japan Local Fallback", 0) + len(jf)

    if category in ("sport", "motorsport") and len(events) < 8:
        sf = sports_official_fallback(norm_city, country_code, from_date, to_date, category)
        events.extend(sf)
        source_counts["Sports Official Fallback"] = source_counts.get("Sports Official Fallback", 0) + len(sf)

    events = apply_eventbrite_search_urls(events)
    events = score_events(events, category, norm_city, country_code)
    events = sports_polish_v30(events, from_date=from_date, to_date=to_date)

    limited = events[:limit]

    return {
        "ok": True,
        "service": SERVICE_NAME,
        "provider": "Ticketmaster + SeatGeek + SerpApi + Sports Expansion Polish + PredictHQ + API-Football + Japan local fallback + Eventbrite fallback",
        "version": VERSION,
        "input": {
            "destination": destination,
            "city": city or destination,
            "country": country,
            "from_date": from_date,
            "to_date": to_date,
            "category": category,
            "limit": limit,
        },
        "normalized": {
            "city": norm_city,
            "country_code": country_code,
            "geo": f"{lat},{lon}" if lat is not None and lon is not None else None,
            "is_country_search": dest.get("is_country_search"),
        },
        "features": {
            "country_city_fix": True,
            "parking_filter": True,
            "serpapi_query_expansion": True,
            "japan_local_fallback": True,
            "serpapi_location_filter": True,
            "advanced_source_priority": True,
            "serpapi_category_cleanup": True,
            "sports_expansion_engine": True,
            "sports_official_fallback": True,
            "sports_polish_v30": True,
            "final_date_gate": True,
            "advanced_dedupe": True,
            "sport_subcategory_normalization": True,
            "suspicious_domain_penalty": True,
        },
        "source_counts": source_counts,
        "count": len(limited),
        "events": limited,
    }


# ============================================================
# ROUTES
# ============================================================

@app.get("/")
async def root():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "provider": "Ticketmaster + SeatGeek + SerpApi + Sports Expansion Polish + PredictHQ + API-Football + Japan local fallback + Eventbrite fallback",
        "api_key_present": bool(TICKETMASTER_API_KEY),
        "predict_api_key_present": bool(PREDICTHQ_API_KEY),
        "predict_api_url_present": bool(PREDICTHQ_API_URL),
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
        "japan_local_fallback": True,
        "serpapi_location_filter": True,
        "advanced_source_priority": True,
        "serpapi_category_cleanup": True,
        "sports_expansion_engine": True,
        "sports_official_fallback": True,
        "sports_polish_v30": True,
        "final_date_gate": True,
        "advanced_dedupe": True,
        "sport_subcategory_normalization": True,
        "suspicious_domain_penalty": True,
        "version": VERSION,
    }


@app.get("/health")
async def health():
    return await root()


@app.get("/events")
async def events(
    destination: str = Query("", description="City or country, e.g. London, Tokyo, Japan"),
    city: str = Query("", description="City"),
    country: str = Query("", description="Country code or country name"),
    from_date: str = Query("", description="YYYY-MM-DD"),
    to_date: str = Query("", description="YYYY-MM-DD"),
    category: str = Query("", description="concert, sport, culture, motorsport, festival, empty for all"),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
):
    return await collect_events(
        destination=destination,
        city=city,
        country=country,
        from_date=from_date,
        to_date=to_date,
        category=category,
        limit=limit,
    )


@app.get("/search")
async def search(
    destination: str = Query("", description="City or country, e.g. London, Tokyo, Japan"),
    city: str = Query("", description="City"),
    country: str = Query("", description="Country code or country name"),
    from_date: str = Query("", description="YYYY-MM-DD"),
    to_date: str = Query("", description="YYYY-MM-DD"),
    category: str = Query("", description="concert, sport, culture, motorsport, festival, empty for all"),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
):
    return await collect_events(
        destination=destination,
        city=city,
        country=country,
        from_date=from_date,
        to_date=to_date,
        category=category,
        limit=limit,
    )


@app.get("/debug/serpapi")
async def debug_serpapi(
    city: str = Query("Tokyo"),
    country: str = Query("JP"),
    from_date: str = Query("2026-05-14"),
    to_date: str = Query("2026-07-25"),
    category: str = Query(""),
):
    dest = infer_destination(city=city, country=country)
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        events = await fetch_serpapi_events(
            client,
            dest["city"],
            dest["country_code"],
            from_date,
            to_date,
            normalize_category(category),
            50,
        )
    return {
        "serpapi_api_key_present": bool(SERPAPI_API_KEY),
        "base_url": "https://serpapi.com/search.json",
        "ok": True,
        "input": {
            "city": city,
            "country": country,
            "from_date": from_date,
            "to_date": to_date,
            "category": category,
        },
        "normalized": {
            "city": dest["city"],
            "country_code": dest["country_code"],
        },
        "events_count": len(events),
        "sample": events[:10],
    }


@app.get("/debug/sports-expansion")
async def debug_sports_expansion(
    city: str = Query("London"),
    country: str = Query("GB"),
    from_date: str = Query("2026-01-01"),
    to_date: str = Query("2026-12-31"),
):
    dest = infer_destination(city=city, country=country)
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        events = await fetch_sports_expansion_events(
            client,
            dest["city"],
            dest["country_code"],
            from_date,
            to_date,
            100,
        )

    polished = sports_polish_v30(
        score_events(events, "sport", dest["city"], dest["country_code"]),
        from_date=from_date,
        to_date=to_date,
    )

    return {
        "serpapi_api_key_present": bool(SERPAPI_API_KEY),
        "sports_expansion": True,
        "sports_polish_v30": True,
        "ok": True,
        "input": {
            "city": city,
            "country": country,
            "from_date": from_date,
            "to_date": to_date,
            "category": "sport",
        },
        "normalized": {
            "city": dest["city"],
            "country_code": dest["country_code"],
        },
        "queries": sports_expansion_queries(dest["city"], dest["country_code"], from_date, to_date),
        "total_events_count": len(polished),
        "sample": polished[:30],
    }


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    args = parser.parse_args()

    if args.serve:
        uvicorn.run("main:app", host=args.host, port=args.port)
    else:
        print({
            "status": "ok",
            "service": SERVICE_NAME,
            "version": VERSION,
            "hint": "Run with: python main.py --serve",
        })


if __name__ == "__main__":
    main()
