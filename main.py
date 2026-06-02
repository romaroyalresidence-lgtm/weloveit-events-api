import os
import json
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

app = FastAPI(title="WELOVEIT Events API", version="v1-stable")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TICKETMASTER_API_KEY = os.getenv("TICKETMASTER_API_KEY", "").strip()
SEATGEEK_CLIENT_ID = os.getenv("SEATGEEK_CLIENT_ID", "").strip()
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "").strip()

MAX_EVENTS = 70


CITY_COUNTRY = {
    "roma": ("Roma", "IT"),
    "rome": ("Roma", "IT"),
    "milano": ("Milano", "IT"),
    "milan": ("Milano", "IT"),
    "tokyo": ("Tokyo", "JP"),
    "london": ("London", "GB"),
    "londra": ("London", "GB"),
    "paris": ("Paris", "FR"),
    "new york": ("New York", "US"),
    "nyc": ("New York", "US"),
    "singapore": ("Singapore", "SG"),
}


def today_iso() -> str:
    return date.today().isoformat()


def default_to_date() -> str:
    return (date.today() + timedelta(days=180)).isoformat()


def normalize_city(city: str, country: str = "") -> Dict[str, str]:
    raw = (city or "").strip()
    key = raw.lower()

    if key in CITY_COUNTRY:
        c, cc = CITY_COUNTRY[key]
        return {"city": c, "country": country or cc}

    return {
        "city": raw.title() if raw else "Roma",
        "country": country.upper() if country else "",
    }


def safe_get_json(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    query = urllib.parse.urlencode(
        {k: v for k, v in params.items() if v not in [None, ""]},
        doseq=True,
    )

    full_url = url + ("?" + query if query else "")

    req = urllib.request.Request(
        full_url,
        headers={"User-Agent": "WELOVEIT Events/1.0"},
    )

    with urllib.request.urlopen(req, timeout=15) as response:
        raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw or "{}")


def fallback_image(category: str = "") -> str:
    category = (category or "").lower()

    if "sport" in category or "calcio" in category:
        return "https://images.unsplash.com/photo-1461896836934-ffe607ba8211?auto=format&fit=crop&w=900&q=80"

    if "concert" in category or "music" in category or "musica" in category:
        return "https://images.unsplash.com/photo-1501281668745-f7f57925c3b4?auto=format&fit=crop&w=900&q=80"

    return "https://images.unsplash.com/photo-1492684223066-81342ee5ff30?auto=format&fit=crop&w=900&q=80"


def make_event(
    title: str,
    city: str,
    country: str,
    start_date: str,
    venue: str = "",
    category: str = "",
    ticket_url: str = "",
    image_url: str = "",
    source: str = "",
) -> Dict[str, Any]:
    return {
        "title": title or "Evento",
        "city": city,
        "country": country,
        "start_date": start_date,
        "venue": venue or "Luogo da confermare",
        "category": category or "event",
        "ticket_url": ticket_url,
        "image_url": image_url or fallback_image(category),
        "source_name": source or "WELOVEIT",
    }


def ticketmaster_events(city: str, country: str, from_date: str, to_date: str, category: str) -> List[Dict[str, Any]]:
    if not TICKETMASTER_API_KEY:
        return []

    params = {
        "apikey": TICKETMASTER_API_KEY,
        "city": city,
        "countryCode": country,
        "startDateTime": f"{from_date}T00:00:00Z",
        "endDateTime": f"{to_date}T23:59:59Z",
        "size": 80,
        "sort": "date,asc",
    }

    if category:
        cat = category.lower()
        if cat in ["sport", "sports", "calcio", "tennis"]:
          params["segmentName"] = "Sports"
        elif cat in ["concert", "concerti", "musica", "music"]:
          params["segmentName"] = "Music"

    try:
        data = safe_get_json(
            "https://app.ticketmaster.com/discovery/v2/events.json",
            params,
        )
    except Exception:
        return []

    output = []

    for item in data.get("_embedded", {}).get("events", []):
        dates = item.get("dates", {}).get("start", {})
        local_date = dates.get("localDate")

        if not local_date:
            continue

        venue_data = (
            item.get("_embedded", {})
            .get("venues", [{}])[0]
        )

        images = item.get("images", [])
        image_url = ""

        if images:
            images = sorted(images, key=lambda x: x.get("width", 0), reverse=True)
            image_url = images[0].get("url", "")

        output.append(
            make_event(
                title=item.get("name", "Evento"),
                city=venue_data.get("city", {}).get("name", city),
                country=venue_data.get("country", {}).get("countryCode", country),
                start_date=local_date,
                venue=venue_data.get("name", ""),
                category=category or "event",
                ticket_url=item.get("url", ""),
                image_url=image_url,
                source="Ticketmaster",
            )
        )

    return output


def seatgeek_events(city: str, country: str, from_date: str, to_date: str, category: str) -> List[Dict[str, Any]]:
    if not SEATGEEK_CLIENT_ID:
        return []

    params = {
        "client_id": SEATGEEK_CLIENT_ID,
        "venue.city": city,
        "datetime_local.gte": f"{from_date}T00:00:00",
        "datetime_local.lte": f"{to_date}T23:59:59",
        "per_page": 80,
        "sort": "datetime_local.asc",
    }

    try:
        data = safe_get_json("https://api.seatgeek.com/2/events", params)
    except Exception:
        return []

    output = []

    for item in data.get("events", []):
        dt = item.get("datetime_local", "")
        start_date = dt[:10] if dt else ""

        if not start_date:
            continue

        venue = item.get("venue", {})
        performers = item.get("performers", [])
        image_url = performers[0].get("image", "") if performers else ""

        output.append(
            make_event(
                title=item.get("title", "Evento"),
                city=venue.get("city", city),
                country=venue.get("country", country),
                start_date=start_date,
                venue=venue.get("name", ""),
                category=category or item.get("type", "event"),
                ticket_url=item.get("url", ""),
                image_url=image_url,
                source="SeatGeek",
            )
        )

    return output


def demo_events(city: str, country: str, from_date: str, to_date: str, category: str) -> List[Dict[str, Any]]:
    return [
        make_event(
            title=f"Eventi principali a {city}",
            city=city,
            country=country,
            start_date=from_date,
            venue="Centro città",
            category=category or "event",
            ticket_url=f"https://www.google.com/search?q={urllib.parse.quote_plus('eventi ' + city)}",
            source="Google Search",
        )
    ]


def dedupe(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    result = []

    for ev in events:
        key = (
            ev.get("title", "").lower().strip(),
            ev.get("start_date", ""),
            ev.get("venue", "").lower().strip(),
        )

        if key in seen:
            continue

        seen.add(key)
        result.append(ev)

    return result


def filter_dates(events: List[Dict[str, Any]], from_date: str, to_date: str) -> List[Dict[str, Any]]:
    result = []

    for ev in events:
        d = ev.get("start_date", "")

        if not d:
            continue

        if from_date and d < from_date:
            continue

        if to_date and d > to_date:
            continue

        result.append(ev)

    return result


async def build_events_response(request: Optional[Request] = None) -> JSONResponse:
    if request and request.method == "POST":
        try:
            body = await request.json()
        except Exception:
            body = {}
    else:
        body = {}

    query = request.query_params if request else {}

    city_raw = (
        body.get("city")
        or query.get("city")
        or "Roma"
    )

    country_raw = (
        body.get("country")
        or query.get("country")
        or ""
    )

    category = (
        body.get("category")
        or body.get("keyword")
        or query.get("category")
        or query.get("keyword")
        or ""
    ).strip()

    from_date = (
        body.get("from_date")
        or body.get("startDate")
        or query.get("from_date")
        or query.get("startDate")
        or today_iso()
    )

    to_date = (
        body.get("to_date")
        or body.get("endDate")
        or query.get("to_date")
        or query.get("endDate")
        or default_to_date()
    )

    loc = normalize_city(city_raw, country_raw)
    city = loc["city"]
    country = loc["country"]

    events: List[Dict[str, Any]] = []

    events += ticketmaster_events(city, country, from_date, to_date, category)
    events += seatgeek_events(city, country, from_date, to_date, category)

    events = dedupe(events)
    events = filter_dates(events, from_date, to_date)
    events = sorted(events, key=lambda x: x.get("start_date", ""))

    if not events:
        events = demo_events(city, country, from_date, to_date, category)

    return JSONResponse(events[:MAX_EVENTS])


@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "WELOVEIT Events API",
        "endpoints": ["/events", "/api/events", "/health"],
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "ticketmaster": bool(TICKETMASTER_API_KEY),
        "seatgeek": bool(SEATGEEK_CLIENT_ID),
        "serpapi": bool(SERPAPI_API_KEY),
    }


@app.get("/events")
async def get_events(request: Request):
    return await build_events_response(request)


@app.post("/events")
async def post_events(request: Request):
    return await build_events_response(request)


@app.get("/api/events")
async def get_api_events(request: Request):
    return await build_events_response(request)


@app.post("/api/events")
async def post_api_events(request: Request):
    return await build_events_response(request)
