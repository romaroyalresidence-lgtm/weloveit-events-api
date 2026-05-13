import os
import json
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.request import urlopen, Request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


PORT = int(os.environ.get("PORT", "8000"))
TICKETMASTER_API_KEY = os.environ.get("TICKETMASTER_API_KEY")
PREDICT_API_KEY = os.environ.get("PREDICT_API_KEY")
PREDICT_API_URL = os.environ.get("PREDICT_API_URL")


CITY_MAP = {
    "roma": "Rome",
    "rome": "Rome",
    "milano": "Milan",
    "milan": "Milan",
    "londra": "London",
    "london": "London",
    "parigi": "Paris",
    "paris": "Paris",
    "new york": "New York",
    "ny": "New York",
    "tokyo": "Tokyo",
    "madrid": "Madrid",
    "barcellona": "Barcelona",
    "barcelona": "Barcelona",
    "berlino": "Berlin",
    "berlin": "Berlin",
    "monaco": "Munich",
    "munich": "Munich",
}


def normalize_country_code(country):
    key = (country or "").strip().lower()

    country_map = {
        "it": "IT",
        "italia": "IT",
        "italy": "IT",

        "us": "US",
        "usa": "US",
        "united states": "US",
        "stati uniti": "US",

        "gb": "GB",
        "uk": "GB",
        "united kingdom": "GB",
        "regno unito": "GB",

        "fr": "FR",
        "france": "FR",
        "francia": "FR",

        "es": "ES",
        "spain": "ES",
        "spagna": "ES",

        "de": "DE",
        "germany": "DE",
        "germania": "DE",

        "jp": "JP",
        "japan": "JP",
        "giappone": "JP",
    }

    return country_map.get(key, country.strip().upper())


def normalize_category(segment):
    if not segment:
        return "event"

    s = segment.lower()

    if "music" in s:
        return "concert"
    if "sports" in s:
        return "sport"
    if "arts" in s or "theatre" in s or "theater" in s:
        return "theatre"
    if "film" in s:
        return "culture"

    return "event"


def clean_text(value):
    return (value or "").strip().lower()


def make_dedupe_key(event):
    return "|".join([
        clean_text(event.get("title")),
        clean_text(event.get("venue")),
        clean_text(event.get("city")),
        clean_text(event.get("start_date")),
    ])


def dedupe_events(events):
    seen = set()
    unique = []

    for event in events:
        key = make_dedupe_key(event)
        if key in seen:
            continue
        seen.add(key)
        unique.append(event)

    return unique


def event_is_in_range(event, from_date="", to_date=""):
    start_date = event.get("start_date")

    if not start_date:
        return False

    if from_date and start_date < from_date:
        return False

    if to_date and start_date > to_date:
        return False

    return True


def get_best_image(images):
    if not images:
        return None

    sorted_images = sorted(
        images,
        key=lambda img: (img.get("width", 0) * img.get("height", 0)),
        reverse=True
    )

    return sorted_images[0].get("url")


def calculate_ai_score(event):
    score = 70

    title = clean_text(event.get("title"))
    venue = clean_text(event.get("venue"))
    category = clean_text(event.get("category"))

    premium_words = [
        "final",
        "grand prix",
        "formula 1",
        "championship",
        "broadway",
        "nba",
        "nhl",
        "nfl",
        "ufc",
        "wimbledon",
        "world cup",
        "derby",
        "concert",
        "festival",
        "musical",
    ]

    iconic_venues = [
        "wembley",
        "madison square garden",
        "royal albert hall",
        "o2 arena",
        "tokyo dome",
        "broadway",
        "stadio olimpico",
        "san siro",
        "camp nou",
        "santiago bernabeu",
    ]

    for word in premium_words:
        if word in title:
            score += 5

    for place in iconic_venues:
        if place in venue:
            score += 5

    if category in ["sport", "concert", "theatre"]:
        score += 5

    return min(score, 100)

def get_predict_score(event):
    if not PREDICT_API_KEY or not PREDICT_API_URL:
        return calculate_ai_score(event)

    payload = json.dumps({
        "title": event.get("title"),
        "category": event.get("category"),
        "subcategory": event.get("subcategory"),
        "city": event.get("city"),
        "country": event.get("country"),
        "venue": event.get("venue"),
        "start_date": event.get("start_date"),
        "price_min": event.get("price_min"),
        "price_max": event.get("price_max"),
        "currency": event.get("currency"),
        "source_name": event.get("source_name"),
    }).encode("utf-8")

    request = Request(
        PREDICT_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {PREDICT_API_KEY}",
            "User-Agent": "WELOVEIT-Events/1.0",
        },
        method="POST"
    )

    try:
        with urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))

        return int(data.get("score", calculate_ai_score(event)))

    except Exception as exc:
        print("Predict API error:", exc)
        return calculate_ai_score(event)

def get_ticketmaster_events(city="", country="", from_date="", to_date="", category="", size=80):
    if not TICKETMASTER_API_KEY:
        return []

    normalized_city = normalize_city(city)
    country_code = normalize_country_code(country)

    params = {
        "apikey": TICKETMASTER_API_KEY,
        "size": size,
        "sort": "date,asc",
    }

    if normalized_city:
        params["city"] = normalized_city
        
    if country_code:
        params["countryCode"] = country_code
    
    if from_date:
        params["startDateTime"] = f"{from_date}T00:00:00Z"

    if to_date:
        params["endDateTime"] = f"{to_date}T23:59:59Z"

    if category == "sport":
        params["classificationName"] = "sports"
        
    elif category == "concert":
        params["classificationName"] = "music"
    elif category == "theatre":
        params["classificationName"] = "arts theatre"
    elif category == "culture":
        params["classificationName"] = "arts"

    url = "https://app.ticketmaster.com/discovery/v2/events.json?" + urlencode(params)
    request = Request(url, headers={"User-Agent": "WELOVEIT-Events/1.0"})

    try:
        with urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print("Ticketmaster error:", exc)
        return []

    raw_events = data.get("_embedded", {}).get("events", [])
    events = []

    for item in raw_events:
        dates = item.get("dates", {}).get("start", {})
        start_date = dates.get("localDate")
        start_time = dates.get("localTime")

        if not start_date:
            continue

        if from_date and start_date < from_date:
            continue

        if to_date and start_date > to_date:
            continue

        venue_data = item.get("_embedded", {}).get("venues", [{}])[0]

        city_name = venue_data.get("city", {}).get("name", "")
        country_name = venue_data.get("country", {}).get("name", "")
        venue_name = venue_data.get("name", "")

        if normalized_city and city_name:
            if clean_text(city_name) != clean_text(normalized_city):
                continue

        classifications = item.get("classifications", [])
        segment = ""
        genre = ""

        if classifications:
            segment = classifications[0].get("segment", {}).get("name", "")
            genre = classifications[0].get("genre", {}).get("name", "")

        mapped_category = normalize_category(segment)

        if category and mapped_category != category:
            continue

        price_min = None
        price_max = None
        currency = None

        price_ranges = item.get("priceRanges", [])
        if price_ranges:
            price_min = price_ranges[0].get("min")
            price_max = price_ranges[0].get("max")
            currency = price_ranges[0].get("currency")

        event = {
            "title": item.get("name", "Unknown event"),
            "category": mapped_category,
            "subcategory": genre or segment or "Live event",
            "start_date": start_date,
            "start_time": start_time,
            "city": city_name,
            "country": country_name,
            "venue": venue_name,
            "source_name": "Ticketmaster",
            "source_url": item.get("url"),
            "ticket_url": item.get("url"),
            "image_url": get_best_image(item.get("images", [])),
            "price_min": price_min,
            "price_max": price_max,
            "currency": currency,
            "is_vip_available": False,
            "status": "active",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        event["ai_score"] = get_predict_score(event)
        events.append(event)

    events = dedupe_events(events)
    events = [event for event in events if event_is_in_range(event, from_date, to_date)]

    events.sort(key=lambda event: (
        event.get("start_date") or "",
        -(event.get("ai_score") or 0),
        event.get("title") or ""
    ))

    return events[:50]


class Handler(BaseHTTPRequestHandler):
    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_json({"status": "ok"})

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path == "/health":
            self.send_json({
                "status": "ok",
                "service": "WELOVEIT Events API",
                "provider": "Ticketmaster",
                "api_key_present": bool(TICKETMASTER_API_KEY),
                "version": "ticketmaster-filtered-deduped-v2"
            })
            return

        if parsed.path == "/events":
           city = query.get("city", query.get("destination", [""]))[0]
           country = query.get("country", query.get("countryCode", [""]))[0]
           from_date = query.get("from_date", [""])[0]
           to_date = query.get("to_date", [""])[0]
           category = query.get("category", [""])[0]

            events = get_ticketmaster_events(
            city=city,
            country=country,
            from_date=from_date,
            to_date=to_date,
            category=category,
            size=80
)

            self.send_json(events)
            return

        self.send_json({"error": "not found"}, status=404)

    def log_message(self, format, *args):
        print(format % args)


def run():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"WELOVEIT Events API running on port {PORT}")
    server.serve_forever()


if __name__ == "__main__":
    run()
