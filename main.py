import os
import json
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import urlopen, Request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


PORT = int(os.environ.get("PORT", "8000"))
TICKETMASTER_API_KEY = os.environ.get("TICKETMASTER_API_KEY")


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


def get_ticketmaster_events(city="", from_date="", to_date="", category="", size=50):
    if not TICKETMASTER_API_KEY:
        return []

    params = {
        "apikey": TICKETMASTER_API_KEY,
        "size": size,
        "sort": "date,asc",
    }

    if city:
        params["city"] = city

    if from_date:
        params["startDateTime"] = f"{from_date}T00:00:00Z"

    if to_date:
        params["endDateTime"] = f"{to_date}T23:59:59Z"

    # filtro categoria Ticketmaster
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

        venue_data = (
            item.get("_embedded", {})
            .get("venues", [{}])[0]
        )

        city_name = venue_data.get("city", {}).get("name", "")
        country_name = venue_data.get("country", {}).get("name", "")
        venue_name = venue_data.get("name", "")

        classifications = item.get("classifications", [])
        segment = ""
        genre = ""

        if classifications:
            segment = classifications[0].get("segment", {}).get("name", "")
            genre = classifications[0].get("genre", {}).get("name", "")

        mapped_category = normalize_category(segment)

        image_url = None
        images = item.get("images", [])
        if images:
            image_url = images[0].get("url")

        price_min = None
        price_max = None
        currency = None

        price_ranges = item.get("priceRanges", [])
        if price_ranges:
            price_min = price_ranges[0].get("min")
            price_max = price_ranges[0].get("max")
            currency = price_ranges[0].get("currency")

        events.append({
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
            "image_url": image_url,
            "price_min": price_min,
            "price_max": price_max,
            "currency": currency,
            "ai_score": 80,
            "is_vip_available": False,
            "status": "active",
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        })

    return events


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
        from urllib.parse import urlparse, parse_qs

        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path == "/health":
            self.send_json({
                "status": "ok",
                "service": "WELOVEIT Events API",
                "provider": "Ticketmaster",
                "api_key_present": bool(TICKETMASTER_API_KEY)
            })
            return

        if parsed.path == "/events":
            city = query.get("city", query.get("destination", [""]))[0]
            from_date = query.get("from_date", [""])[0]
            to_date = query.get("to_date", [""])[0]
            category = query.get("category", [""])[0]

            events = get_ticketmaster_events(
                city=city,
                from_date=from_date,
                to_date=to_date,
                category=category,
                size=50
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
