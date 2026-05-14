import os
import re
import json
from difflib import SequenceMatcher
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.request import urlopen, Request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


PORT = int(os.environ.get("PORT", "8000"))
TICKETMASTER_API_KEY = os.environ.get("TICKETMASTER_API_KEY")
PREDICT_API_KEY = os.environ.get("PREDICT_API_KEY")
PREDICT_API_URL = os.environ.get("PREDICT_API_URL", "https://api.predicthq.com/v1/events/")


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


PREDICTHQ_CATEGORY_MAP = {
    "concert": "concerts,festivals,performing-arts",
    "sport": "sports",
    "motorsport": "sports",
    "horse_racing": "sports",
    "theatre": "performing-arts",
    "culture": "performing-arts,community,festivals,expos,conferences",
}


def normalize_city(city):
    key = (city or "").strip().lower()
    return CITY_MAP.get(key, city.strip())


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
        "great britain": "GB",

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

        "br": "BR",
        "brazil": "BR",
        "brasile": "BR",

        "ar": "AR",
        "argentina": "AR",
    }

    if not key:
        return ""

    return country_map.get(key, country.strip().upper())


def normalize_category(segment):
    if not segment:
        return "event"

    s = segment.lower()

    if "music" in s or "concert" in s or "festival" in s:
        return "concert"

    if "motorsport" in s or "motor" in s or "racing" in s or "formula" in s or "grand prix" in s:
        return "motorsport"

    if "horse" in s or "equestrian" in s:
        return "horse_racing"

    if "sports" in s or "sport" in s:
        return "sport"

    if "arts" in s or "theatre" in s or "theater" in s or "performing" in s:
        return "theatre"

    if "film" in s or "community" in s or "expo" in s or "conference" in s:
        return "culture"

    return "event"


def clean_text(value):
    return (value or "").strip().lower()


def normalize_event_title(title):
    title = clean_text(title)

    remove_words = [
        "flexiticket",
        "flex ticket",
        "flex-ticket",
        "standard ticket",
        "skip the line",
        "skip-the-line",
        "entry ticket",
        "entrance ticket",
        "admission ticket",
        "general admission",
        "official ticket",
        "tickets",
        "ticket",
        "vip",
        "experience",
        "tour",
        "guided tour",
        "museum",
        "museo",
    ]

    for word in remove_words:
        title = title.replace(word, " ")

    title = re.sub(r"[^a-z0-9\s]", " ", title)
    title = re.sub(r"\s+", " ", title).strip()

    return title


def similar_text(a, b):
    a = normalize_event_title(a)
    b = normalize_event_title(b)

    if not a or not b:
        return False

    if a == b:
        return True

    if a in b or b in a:
        return True

    ratio = SequenceMatcher(None, a, b).ratio()
    return ratio >= 0.82


def should_drop_low_value_event(event):
    title = clean_text(event.get("title"))
    source = clean_text(event.get("source_name"))

    low_value_words = [
        "flexiticket",
        "flex ticket",
        "flex-ticket",
        "museum flex",
        "standard admission",
        "general admission",
        "skip the line",
        "skip-the-line",
    ]

    for word in low_value_words:
        if word in title:
            return True

    # Riduce eventi turistici/ripetitivi da Ticketmaster
    if source == "ticketmaster":
        museum_words = [
            "museum",
            "museo",
            "exhibition ticket",
            "entry ticket",
            "admission ticket",
        ]

        for word in museum_words:
            if word in title:
                return True

    return False


def event_quality_score(event):
    score = event.get("ai_score") or 0

    if event.get("ticket_url"):
        score += 20

    if event.get("image_url"):
        score += 10

    if event.get("venue"):
        score += 5

    source = clean_text(event.get("source_name"))

    if source == "ticketmaster":
        score += 8

    if source == "predicthq":
        score += 3

    rank = event.get("rank")
    try:
        if rank:
            score += min(int(rank) // 10, 10)
    except Exception:
        pass

    return score


def dedupe_events(events):
    filtered = []

    for event in events:
        if should_drop_low_value_event(event):
            continue
        filtered.append(event)

    # Prima gli eventi migliori, così quando ci sono duplicati teniamo quello più utile
    filtered.sort(key=event_quality_score, reverse=True)

    unique = []

    for event in filtered:
        title = event.get("title")
        venue = clean_text(event.get("venue"))
        city = clean_text(event.get("city"))
        country = clean_text(event.get("country"))
        start_date = clean_text(event.get("start_date"))

        duplicate = False

        for existing in unique:
            same_date = clean_text(existing.get("start_date")) == start_date
            same_city = clean_text(existing.get("city")) == city
            same_country = clean_text(existing.get("country")) == country
            same_venue = clean_text(existing.get("venue")) == venue
            title_similar = similar_text(title, existing.get("title"))

            if same_date and same_city and same_country and title_similar:
                duplicate = True
                break

            if same_date and same_venue and title_similar:
                duplicate = True
                break

        if not duplicate:
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
    source_name = clean_text(event.get("source_name"))

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
        "premier league",
        "champions league",
        "serie a",
        "la liga",
        "j league",
        "npb",
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
        "auditorium parco della musica",
        "maracana",
        "la bombonera",
    ]

    for word in premium_words:
        if word in title:
            score += 5

    for place in iconic_venues:
        if place in venue:
            score += 5

    if category in ["sport", "motorsport", "horse_racing", "concert", "theatre"]:
        score += 5

    if source_name == "predicthq":
        rank = event.get("rank")
        try:
            if rank:
                score += min(int(rank) // 10, 15)
        except Exception:
            pass

    return min(score, 100)


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

    if category in ["sport", "motorsport", "horse_racing"]:
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

        if country_code:
            venue_country_code = venue_data.get("country", {}).get("countryCode", "")
            if venue_country_code and clean_text(venue_country_code) != clean_text(country_code):
                continue

        classifications = item.get("classifications", [])
        segment = ""
        genre = ""
        subgenre = ""

        if classifications:
            segment = classifications[0].get("segment", {}).get("name", "")
            genre = classifications[0].get("genre", {}).get("name", "")
            subgenre = classifications[0].get("subGenre", {}).get("name", "")

        mapped_category = normalize_category(" ".join([segment, genre, subgenre]))

        if category and mapped_category != category:
            # motorsport e horse_racing possono arrivare come sport generico
            if category in ["motorsport", "horse_racing"] and mapped_category == "sport":
                title_check = clean_text(item.get("name", ""))
                genre_check = clean_text(" ".join([segment, genre, subgenre]))

                if category == "motorsport":
                    if not any(word in title_check or word in genre_check for word in ["motor", "racing", "formula", "grand prix"]):
                        continue

                if category == "horse_racing":
                    if not any(word in title_check or word in genre_check for word in ["horse", "equestrian", "racing"]):
                        continue
            else:
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
            "subcategory": subgenre or genre or segment or "Live event",
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

        event["ai_score"] = calculate_ai_score(event)
        events.append(event)

    return events


def get_predicthq_events(city="", country="", from_date="", to_date="", category="", size=80):
    if not PREDICT_API_KEY:
        return []

    normalized_city = normalize_city(city)
    country_code = normalize_country_code(country)

    params = {
        "limit": size,
        "sort": "start",
        "state": "active",
    }

    if normalized_city:
        params["q"] = normalized_city

    if country_code:
        params["country"] = country_code

    if from_date:
        params["start.gte"] = f"{from_date}T00:00:00Z"

    if to_date:
        params["start.lte"] = f"{to_date}T23:59:59Z"

    phq_category = PREDICTHQ_CATEGORY_MAP.get(category)
    if phq_category:
        params["category"] = phq_category

    url = PREDICT_API_URL.rstrip("/") + "/?" + urlencode(params)

    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {PREDICT_API_KEY}",
            "Accept": "application/json",
            "User-Agent": "WELOVEIT-Events/1.0",
        }
    )

    try:
        with urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print("PredictHQ error:", exc)
        return []

    raw_events = data.get("results", [])
    events = []

    for item in raw_events:
        title = item.get("title", "Unknown event")
        start = item.get("start", "")
        start_date = start[:10] if start else ""
        start_time = start[11:19] if len(start) >= 19 else None

        if not start_date:
            continue

        if from_date and start_date < from_date:
            continue

        if to_date and start_date > to_date:
            continue

        phq_category = item.get("category", "")
        mapped_category = normalize_category(phq_category)

        if category and mapped_category != category:
            if category in ["motorsport", "horse_racing"] and mapped_category == "sport":
                title_check = clean_text(title)
                if category == "motorsport":
                    if not any(word in title_check for word in ["motor", "racing", "formula", "grand prix"]):
                        continue
                if category == "horse_racing":
                    if not any(word in title_check for word in ["horse", "equestrian", "racing"]):
                        continue
            else:
                continue

        location = item.get("geo", {}).get("address", {})
        city_name = location.get("locality") or normalized_city
        country_name = location.get("country_code") or country_code

        venue_name = ""
        entities = item.get("entities", [])
        if entities:
            venue_name = entities[0].get("name", "")

        event = {
            "title": title,
            "category": mapped_category,
            "subcategory": phq_category or "Live event",
            "start_date": start_date,
            "start_time": start_time,
            "city": city_name,
            "country": country_name,
            "venue": venue_name,
            "source_name": "PredictHQ",
            "source_url": item.get("url"),
            "ticket_url": None,
            "image_url": None,
            "price_min": None,
            "price_max": None,
            "currency": None,
            "is_vip_available": False,
            "status": item.get("state", "active"),
            "rank": item.get("rank"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        event["ai_score"] = calculate_ai_score(event)
        events.append(event)

    return events


def get_all_events(city="", country="", from_date="", to_date="", category="", size=80):
    ticketmaster_events = get_ticketmaster_events(
        city=city,
        country=country,
        from_date=from_date,
        to_date=to_date,
        category=category,
        size=size
    )

    predicthq_events = get_predicthq_events(
        city=city,
        country=country,
        from_date=from_date,
        to_date=to_date,
        category=category,
        size=size
    )

    events = ticketmaster_events + predicthq_events
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

        if parsed.path == "/":
            self.send_json({
                "service": "WELOVEIT Events API",
                "provider": "Ticketmaster + PredictHQ",
                "endpoints": {
                    "health": "/health",
                    "events": "/events?city=rome&country=IT",
                    "sport": "/events?city=london&country=GB&category=sport",
                    "concert": "/events?city=new%20york&country=US&category=concert"
                }
            })
            return

        if parsed.path == "/health":
            self.send_json({
                "status": "ok",
                "service": "WELOVEIT Events API",
                "provider": "Ticketmaster + PredictHQ",
                "api_key_present": bool(TICKETMASTER_API_KEY),
                "predict_api_key_present": bool(PREDICT_API_KEY),
                "predict_api_url_present": bool(PREDICT_API_URL),
                "version": "ticketmaster-predicthq-advanced-dedupe-v5"
            })
            return

        if parsed.path == "/events":
            city = query.get("city", query.get("destination", [""]))[0]
            country = query.get("country", query.get("countryCode", [""]))[0]
            from_date = query.get("from_date", [""])[0]
            to_date = query.get("to_date", [""])[0]
            category = query.get("category", [""])[0]

            events = get_all_events(
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
