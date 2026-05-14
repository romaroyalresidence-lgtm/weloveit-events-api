import os
import re
import json
from difflib import SequenceMatcher
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.request import urlopen, Request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


PORT = int(os.environ.get("PORT", "8000"))

TICKETMASTER_API_KEY = os.environ.get("TICKETMASTER_API_KEY")
PREDICT_API_KEY = os.environ.get("PREDICT_API_KEY")
PREDICT_API_URL = os.environ.get("PREDICT_API_URL", "https://api.predicthq.com/v1/events/")

FOOTBALL_API_KEY = os.environ.get("FOOTBALL_API_KEY")
FOOTBALL_API_BASE_URL = "https://v3.football.api-sports.io"

EVENTBRITE_API_KEY = os.environ.get("EVENTBRITE_API_KEY")
EVENTBRITE_API_BASE_URL = "https://www.eventbriteapi.com/v3"


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


EVENTBRITE_CATEGORY_MAP = {
    "concert": "103",
    "theatre": "105",
    "sport": "108",
    "motorsport": "108",
    "horse_racing": "108",
    "culture": "",
}


FOOTBALL_CITY_TEAMS = {
    "london|gb": [
        {"id": 42, "name": "Arsenal", "venue": "Emirates Stadium"},
        {"id": 49, "name": "Chelsea", "venue": "Stamford Bridge"},
        {"id": 47, "name": "Tottenham", "venue": "Tottenham Hotspur Stadium"},
        {"id": 48, "name": "West Ham", "venue": "London Stadium"},
        {"id": 36, "name": "Fulham", "venue": "Craven Cottage"},
        {"id": 52, "name": "Crystal Palace", "venue": "Selhurst Park"},
        {"id": 55, "name": "Brentford", "venue": "Gtech Community Stadium"},
    ],
    "rome|it": [
        {"id": 497, "name": "AS Roma", "venue": "Stadio Olimpico"},
        {"id": 487, "name": "Lazio", "venue": "Stadio Olimpico"},
    ],
    "milan|it": [
        {"id": 489, "name": "AC Milan", "venue": "San Siro"},
        {"id": 505, "name": "Inter", "venue": "San Siro"},
    ],
    "madrid|es": [
        {"id": 541, "name": "Real Madrid", "venue": "Santiago Bernabeu"},
        {"id": 530, "name": "Atletico Madrid", "venue": "Civitas Metropolitano"},
        {"id": 728, "name": "Rayo Vallecano", "venue": "Campo de Futbol de Vallecas"},
    ],
    "barcelona|es": [
        {"id": 529, "name": "Barcelona", "venue": "Camp Nou"},
        {"id": 540, "name": "Espanyol", "venue": "RCDE Stadium"},
    ],
    "paris|fr": [
        {"id": 85, "name": "Paris Saint Germain", "venue": "Parc des Princes"},
    ],
    "munich|de": [
        {"id": 157, "name": "Bayern Munich", "venue": "Allianz Arena"},
    ],
    "berlin|de": [
        {"id": 182, "name": "Union Berlin", "venue": "Stadion An der Alten Forsterei"},
        {"id": 159, "name": "Hertha Berlin", "venue": "Olympiastadion Berlin"},
    ],
    "new york|us": [
        {"id": 1608, "name": "New York City FC", "venue": "Yankee Stadium"},
        {"id": 1602, "name": "New York Red Bulls", "venue": "Red Bull Arena"},
    ],
}


FOOTBALL_CITY_LEAGUES = {
    "london|gb": [{"id": 39, "name": "Premier League"}],
    "rome|it": [{"id": 135, "name": "Serie A"}],
    "milan|it": [{"id": 135, "name": "Serie A"}],
    "madrid|es": [{"id": 140, "name": "La Liga"}],
    "barcelona|es": [{"id": 140, "name": "La Liga"}],
    "paris|fr": [{"id": 61, "name": "Ligue 1"}],
    "munich|de": [{"id": 78, "name": "Bundesliga"}],
    "berlin|de": [{"id": 78, "name": "Bundesliga"}],
}


CITY_ALIASES = {
    "rome": ["rome", "roma"],
    "roma": ["rome", "roma"],
    "milan": ["milan", "milano"],
    "milano": ["milan", "milano"],
    "london": ["london", "londra"],
    "londra": ["london", "londra"],
    "paris": ["paris", "parigi"],
    "parigi": ["paris", "parigi"],
    "munich": ["munich", "monaco", "muenchen", "münchen"],
    "monaco": ["munich", "monaco", "muenchen", "münchen"],
    "new york": ["new york", "nyc"],
    "barcelona": ["barcelona", "barcellona"],
    "barcellona": ["barcelona", "barcellona"],
    "berlin": ["berlin", "berlino"],
    "berlino": ["berlin", "berlino"],
}


ROME_FOOTBALL_TEAMS = [
    "lazio",
    "lazio rome",
    "ss lazio",
    "as roma",
    "roma",
]


ITALIAN_FOOTBALL_WORDS = [
    "serie a",
    "serie b",
    "coppa italia",
    "supercoppa",
    "calcio",
    "fc",
    "bc",
    "ac milan",
    "inter",
    "juventus",
    "atalanta",
    "sassuolo",
    "torino",
    "cagliari",
    "bologna",
    "fiorentina",
    "napoli",
    "verona",
    "genoa",
    "lecce",
    "monza",
    "udinese",
    "empoli",
    "parma",
]


BIG_MATCH_WORDS = [
    "ac milan",
    "inter",
    "juventus",
    "napoli",
    "atalanta",
    "roma",
    "lazio",
    "arsenal",
    "chelsea",
    "tottenham",
    "liverpool",
    "manchester",
    "real madrid",
    "barcelona",
    "atletico",
    "psg",
    "bayern",
]


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

    if "football" in s or "soccer" in s:
        return "sport"

    if "sports" in s or "sport" in s:
        return "sport"

    if "arts" in s or "theatre" in s or "theater" in s or "performing" in s:
        return "theatre"

    if "film" in s or "community" in s or "expo" in s or "conference" in s:
        return "culture"

    if "business" in s or "food" in s or "drink" in s or "nightlife" in s:
        return "culture"

    return "event"


def clean_text(value):
    return (value or "").strip().lower()


def city_aliases_for(city):
    city_key = clean_text(city)
    normalized = clean_text(normalize_city(city))
    aliases = set()

    aliases.add(city_key)
    aliases.add(normalized)

    for key in [city_key, normalized]:
        for alias in CITY_ALIASES.get(key, []):
            aliases.add(clean_text(alias))

    return [alias for alias in aliases if alias]


def event_matches_requested_city(event, requested_city):
    if not requested_city:
        return True

    event_city = clean_text(event.get("city"))

    if not event_city:
        return True

    allowed = city_aliases_for(requested_city)

    if event_city in allowed:
        return True

    for alias in allowed:
        if alias and alias in event_city:
            return True

    return False


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
        score += 10

    if event.get("image_url"):
        score += 7

    if event.get("venue"):
        score += 4

    source = clean_text(event.get("source_name"))

    if source == "ticketmaster":
        score += 8

    if source == "eventbrite":
        score += 6

    if source == "api-football":
        score += 7

    if source == "predicthq":
        score += 2

    return score


def dedupe_events(events):
    filtered = []

    for event in events:
        if should_drop_low_value_event(event):
            continue
        filtered.append(event)

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


def infer_sport_subcategory(title, city="", country=""):
    title_clean = clean_text(title)
    city_clean = clean_text(city)
    country_code = clean_text(normalize_country_code(country))

    if "wwe" in title_clean or "wrestling" in title_clean:
        return "Wrestling"

    if "marathon" in title_clean or "half marathon" in title_clean:
        return "Marathon"

    if "internazionali bnl" in title_clean or "tennis" in title_clean or "atp" in title_clean or "wta" in title_clean:
        return "Tennis"

    if "formula 1" in title_clean or "grand prix" in title_clean or "motogp" in title_clean:
        return "Motorsport"

    if "nba" in title_clean or "basketball" in title_clean or "basket" in title_clean:
        return "Basketball"

    if "rugby" in title_clean or "six nations" in title_clean:
        return "Rugby"

    if "serie a" in title_clean:
        return "Serie A"

    if country_code == "it" and any(word in title_clean for word in ITALIAN_FOOTBALL_WORDS):
        return "Serie A"

    if "premier league" in title_clean:
        return "Premier League"

    if "la liga" in title_clean:
        return "La Liga"

    if "bundesliga" in title_clean:
        return "Bundesliga"

    if "ligue 1" in title_clean:
        return "Ligue 1"

    if " vs " in title_clean or " v " in title_clean:
        if city_clean in ["roma", "rome"] and any(team in title_clean for team in ROME_FOOTBALL_TEAMS):
            return "Serie A"
        return "Football"

    return "Sport"


def clean_sport_title(title):
    if not title:
        return title

    value = title.strip()

    replacements = {
        "Lazio Rome": "Lazio",
        "AS Roma Rome": "AS Roma",
        "Roma Rome": "Roma",
        " BC": "",
        " Calcio": "",
    }

    for old, new in replacements.items():
        value = value.replace(old, new)

    value = re.sub(r"\s+", " ", value).strip()

    return value


def infer_sport_venue(title, city="", venue=""):
    title_clean = clean_text(title)
    city_clean = clean_text(city)
    venue_clean = clean_text(venue)

    if "marathon" in title_clean or "half marathon" in title_clean:
        return "Rome city center" if city_clean in ["roma", "rome"] else (venue or "City center")

    if "internazionali bnl" in title_clean or "tennis" in title_clean:
        if city_clean in ["roma", "rome"]:
            return "Foro Italico"
        return venue or ""

    if "wwe" in title_clean or "wrestling" in title_clean:
        if venue_clean and venue_clean not in ["lazio rome", "ss lazio", "as roma"]:
            return venue
        return city or ""

    if city_clean in ["roma", "rome"]:
        if any(team in title_clean for team in ROME_FOOTBALL_TEAMS):
            return "Stadio Olimpico"

    if venue_clean in ["lazio rome", "ss lazio", "as roma", "roma", "rome"]:
        return ""

    return venue


def build_ticket_search_url(event):
    title = str(event.get("title") or "")
    city = str(event.get("city") or "")
    country = str(event.get("country") or "")
    start_date = str(event.get("start_date") or "")
    subcategory = str(event.get("subcategory") or "")

    search_terms = [
        title,
        subcategory,
        city,
        country,
        start_date,
        "official tickets",
    ]

    query = " ".join([term for term in search_terms if term]).strip()

    if not query:
        return None

    return "https://www.google.com/search?" + urlencode({"q": query})


def enhance_predicthq_event(event):
    if clean_text(event.get("source_name")) != "predicthq":
        return event

    if event.get("category") != "sport":
        return event

    title = event.get("title") or ""
    city = event.get("city") or ""
    country = event.get("country") or ""
    venue = event.get("venue") or ""

    cleaned_title = clean_sport_title(title)
    subcategory = infer_sport_subcategory(cleaned_title, city, country)
    cleaned_venue = infer_sport_venue(cleaned_title, city, venue)

    event["title"] = cleaned_title
    event["subcategory"] = subcategory
    event["venue"] = cleaned_venue or venue

    if subcategory in ["Serie A", "Premier League", "La Liga", "Bundesliga", "Ligue 1", "Football"]:
        event["sport_type"] = "Football"
    elif subcategory == "Tennis":
        event["sport_type"] = "Tennis"
    elif subcategory == "Marathon":
        event["sport_type"] = "Running"
    elif subcategory == "Wrestling":
        event["sport_type"] = "Wrestling"
    else:
        event["sport_type"] = "Sport"

    event["ticket_url"] = build_ticket_search_url(event)

    return event


def calculate_ai_score(event):
    score = 60

    title = clean_text(event.get("title"))
    venue = clean_text(event.get("venue"))
    category = clean_text(event.get("category"))
    subcategory = clean_text(event.get("subcategory"))
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
        "arsenal",
        "chelsea",
        "tottenham",
        "liverpool",
        "manchester",
        "roma",
        "lazio",
        "inter",
        "milan",
        "real madrid",
        "barcelona",
        "psg",
        "bayern",
        "internazionali bnl",
        "marathon",
        "wwe",
        "startup",
        "networking",
        "conference",
        "festival",
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
        "emirates stadium",
        "stamford bridge",
        "tottenham hotspur stadium",
        "parc des princes",
        "allianz arena",
        "foro italico",
    ]

    premium_subcategories = [
        "serie a",
        "premier league",
        "la liga",
        "bundesliga",
        "ligue 1",
        "champions league",
        "tennis",
        "motorsport",
        "wrestling",
        "marathon",
        "conference",
        "business",
        "nightlife",
        "food & drink",
        "festival",
    ]

    premium_word_bonus = 0
    for word in premium_words:
        if word in title:
            premium_word_bonus += 3
    score += min(premium_word_bonus, 15)

    venue_bonus = 0
    for place in iconic_venues:
        if place in venue:
            venue_bonus += 5
    score += min(venue_bonus, 8)

    for item in premium_subcategories:
        if item in subcategory:
            score += 8
            break

    if category in ["sport", "motorsport", "horse_racing", "concert", "theatre", "culture"]:
        score += 4

    if source_name == "ticketmaster":
        score += 5

    if source_name == "eventbrite":
        score += 5

    if source_name == "api-football":
        score += 7

    if source_name == "predicthq":
        rank = event.get("rank")
        try:
            if rank:
                score += min(int(rank) // 15, 7)
        except Exception:
            pass

    if any(word in title for word in BIG_MATCH_WORDS):
        score += 4

    if "ac milan" in title or "inter" in title or "juventus" in title or "napoli" in title:
        score += 4

    if "internazionali bnl" in title:
        score += 5

    if "marathon" in title:
        score += 3

    if "final" in title or "derby" in title:
        score += 6

    return min(score, 98)
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

        phq_category = item.get("category", "")
        mapped_category = normalize_category(phq_category)

        if category and mapped_category != category:
            if category == "culture" and mapped_category in ["culture", "theatre", "concert"]:
                pass
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

        event = enhance_predicthq_event(event)
        event["ticket_url"] = build_ticket_search_url(event)
        event["ai_score"] = calculate_ai_score(event)
        events.append(event)

    return events


def map_eventbrite_category(category_name="", subcategory_name=""):
    text = clean_text(f"{category_name} {subcategory_name}")

    if "music" in text or "concert" in text:
        return "concert"

    if "sports" in text or "fitness" in text or "football" in text or "soccer" in text:
        return "sport"

    if "performing" in text or "visual" in text or "arts" in text or "theatre" in text or "theater" in text:
        return "theatre"

    return "culture"


def debug_eventbrite_request(params):
    if not EVENTBRITE_API_KEY:
        return {
            "ok": False,
            "error": "missing EVENTBRITE_API_KEY",
            "request_url": None,
            "params": params,
        }

    url = EVENTBRITE_API_BASE_URL.rstrip("/") + "/events/search/?" + urlencode(params)

    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {EVENTBRITE_API_KEY}",
            "Accept": "application/json",
            "User-Agent": "WELOVEIT-Events/1.0",
        }
    )

    try:
        with urlopen(request, timeout=20) as response:
            status_code = response.status
            data = json.loads(response.read().decode("utf-8"))

        events = data.get("events", [])
        sample = []

        for item in events[:5]:
            venue = item.get("venue") or {}
            address = venue.get("address") or {}
            start = item.get("start") or {}
            category_data = item.get("category") or {}
            subcategory_data = item.get("subcategory") or {}

            sample.append({
                "id": item.get("id"),
                "title": item.get("name", {}).get("text"),
                "url": item.get("url"),
                "start_local": start.get("local"),
                "start_utc": start.get("utc"),
                "status": item.get("status"),
                "is_free": item.get("is_free"),
                "venue_name": venue.get("name"),
                "venue_city": address.get("city"),
                "venue_country": address.get("country"),
                "category": category_data.get("name"),
                "subcategory": subcategory_data.get("name"),
            })

        return {
            "ok": True,
            "status_code": status_code,
            "request_url": url,
            "params": params,
            "events_count": len(events),
            "pagination": data.get("pagination"),
            "sample": sample,
            "raw_error": data.get("error"),
            "raw_status_code": data.get("status_code"),
            "raw_description": data.get("error_description"),
        }

    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "request_url": url,
            "params": params,
        }


def build_debug_eventbrite_payload(city="", country="", from_date="", to_date="", category=""):
    normalized_city = normalize_city(city)
    country_code = normalize_country_code(country)

    location_address = normalized_city
    if country_code:
        location_address = f"{normalized_city}, {country_code}"

    base_params = {
        "location.address": location_address,
        "location.within": "50km",
        "expand": "venue,logo,category,subcategory",
        "sort_by": "date",
        "page_size": 10,
    }

    if from_date:
        base_params["start_date.range_start"] = f"{from_date}T00:00:00Z"

    if to_date:
        base_params["start_date.range_end"] = f"{to_date}T23:59:59Z"

    eventbrite_category_id = EVENTBRITE_CATEGORY_MAP.get(category)
    if eventbrite_category_id:
        base_params["categories"] = eventbrite_category_id

    if category == "culture":
        base_params["q"] = "business food nightlife workshop festival community"

    simple_params = {
        "location.address": location_address,
        "location.within": "50km",
        "page_size": 10,
    }

    if from_date:
        simple_params["start_date.range_start"] = f"{from_date}T00:00:00Z"

    if to_date:
        simple_params["start_date.range_end"] = f"{to_date}T23:59:59Z"

    return {
        "eventbrite_api_key_present": bool(EVENTBRITE_API_KEY),
        "base_url": EVENTBRITE_API_BASE_URL,
        "input": {
            "city": city,
            "country": country,
            "from_date": from_date,
            "to_date": to_date,
            "category": category,
        },
        "normalized": {
            "city": normalized_city,
            "country_code": country_code,
            "location_address": location_address,
        },
        "full_search": debug_eventbrite_request(base_params),
        "simple_search": debug_eventbrite_request(simple_params),
    }


def get_eventbrite_events(city="", country="", from_date="", to_date="", category="", size=80):
    if not EVENTBRITE_API_KEY:
        return []

    normalized_city = normalize_city(city)
    country_code = normalize_country_code(country)

    location_address = normalized_city
    if country_code:
        location_address = f"{normalized_city}, {country_code}"

    params = {
        "location.address": location_address,
        "location.within": "50km",
        "expand": "venue,logo,category,subcategory",
        "sort_by": "date",
        "page_size": min(size, 50),
    }

    if from_date:
        params["start_date.range_start"] = f"{from_date}T00:00:00Z"

    if to_date:
        params["start_date.range_end"] = f"{to_date}T23:59:59Z"

    eventbrite_category_id = EVENTBRITE_CATEGORY_MAP.get(category)
    if eventbrite_category_id:
        params["categories"] = eventbrite_category_id

    if category == "culture":
        params["q"] = "business food nightlife workshop festival community"

    url = EVENTBRITE_API_BASE_URL.rstrip("/") + "/events/search/?" + urlencode(params)

    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {EVENTBRITE_API_KEY}",
            "Accept": "application/json",
            "User-Agent": "WELOVEIT-Events/1.0",
        }
    )

    try:
        with urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print("Eventbrite error:", exc)
        return []

    raw_events = data.get("events", [])
    events = []

    for item in raw_events:
        title = item.get("name", {}).get("text") or "Unknown event"

        start = item.get("start", {})
        start_local = start.get("local") or start.get("utc") or ""
        start_date = start_local[:10] if start_local else ""
        start_time = start_local[11:19] if len(start_local) >= 19 else None

        if not start_date:
            continue

        venue_data = item.get("venue") or {}
        city_name = venue_data.get("address", {}).get("city") or normalized_city
        country_name = venue_data.get("address", {}).get("country") or country_code
        venue_name = venue_data.get("name") or ""

        if city and city_name:
            if not event_matches_requested_city({"city": city_name}, city):
                continue

        category_name = (item.get("category") or {}).get("name") or ""
        subcategory_name = (item.get("subcategory") or {}).get("name") or ""
        mapped_category = map_eventbrite_category(category_name, subcategory_name)

        if category and mapped_category != category:
            if category == "culture" and mapped_category in ["culture", "theatre", "concert"]:
                pass
            else:
                continue

        logo = item.get("logo") or {}
        image_url = logo.get("url") or logo.get("original", {}).get("url")

        is_free = item.get("is_free")
        price_min = 0 if is_free is True else None

        event = {
            "title": title,
            "category": mapped_category,
            "subcategory": subcategory_name or category_name or "Local event",
            "start_date": start_date,
            "start_time": start_time,
            "city": city_name,
            "country": country_name,
            "venue": venue_name,
            "source_name": "Eventbrite",
            "source_url": item.get("url"),
            "ticket_url": item.get("url"),
            "image_url": image_url,
            "price_min": price_min,
            "price_max": None,
            "currency": None,
            "is_vip_available": False,
            "status": item.get("status", "active"),
            "is_free": is_free,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        event["ai_score"] = calculate_ai_score(event)
        events.append(event)

    return events


def get_default_football_dates(from_date="", to_date=""):
    today = datetime.now(timezone.utc).date()
    start = from_date or today.isoformat()
    end = to_date or (today + timedelta(days=180)).isoformat()
    return start, end


def get_football_season(date_string=""):
    try:
        year = int((date_string or "")[:4])
        month = int((date_string or "")[5:7])
        return year if month >= 8 else year - 1
    except Exception:
        today = datetime.now(timezone.utc).date()
        return today.year if today.month >= 8 else today.year - 1


def get_football_city_key(city="", country=""):
    normalized_city = clean_text(normalize_city(city))
    country_code = clean_text(normalize_country_code(country))

    if not normalized_city or not country_code:
        return ""

    return f"{normalized_city}|{country_code}"


def call_api_football(path, params):
    if not FOOTBALL_API_KEY:
        print("API-Football debug: missing FOOTBALL_API_KEY")
        return None

    url = FOOTBALL_API_BASE_URL.rstrip("/") + path + "?" + urlencode(params)

    request = Request(
        url,
        headers={
            "x-apisports-key": FOOTBALL_API_KEY,
            "Accept": "application/json",
            "User-Agent": "WELOVEIT-Events/1.0",
        }
    )

    try:
        with urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data
    except Exception as exc:
        print("API-Football error:", exc)
        return None


def debug_api_football_request(path, params):
    if not FOOTBALL_API_KEY:
        return {
            "ok": False,
            "error": "missing FOOTBALL_API_KEY",
            "request_url": None,
            "params": params,
        }

    url = FOOTBALL_API_BASE_URL.rstrip("/") + path + "?" + urlencode(params)

    request = Request(
        url,
        headers={
            "x-apisports-key": FOOTBALL_API_KEY,
            "Accept": "application/json",
            "User-Agent": "WELOVEIT-Events/1.0",
        }
    )

    try:
        with urlopen(request, timeout=20) as response:
            status_code = response.status
            data = json.loads(response.read().decode("utf-8"))

        results = data.get("response", [])
        sample = []

        for item in results[:5]:
            fixture = item.get("fixture", {})
            league = item.get("league", {})
            teams = item.get("teams", {})
            venue = fixture.get("venue", {})

            sample.append({
                "fixture_id": fixture.get("id"),
                "date": fixture.get("date"),
                "league": league.get("name"),
                "league_id": league.get("id"),
                "season": league.get("season"),
                "home": teams.get("home", {}).get("name"),
                "away": teams.get("away", {}).get("name"),
                "venue_name": venue.get("name"),
                "venue_city": venue.get("city"),
                "status": fixture.get("status", {}).get("long"),
            })

        return {
            "ok": True,
            "status_code": status_code,
            "request_url": url.replace(FOOTBALL_API_KEY or "", "***"),
            "params": params,
            "errors": data.get("errors"),
            "results_count": len(results),
            "sample": sample,
            "paging": data.get("paging"),
        }

    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "request_url": url.replace(FOOTBALL_API_KEY or "", "***"),
            "params": params,
        }


def football_fixture_to_event(item, normalized_city, country_code, requested_city=""):
    fixture = item.get("fixture", {})
    fixture_id = fixture.get("id")

    fixture_date = fixture.get("date", "")
    start_date = fixture_date[:10] if fixture_date else ""
    start_time = fixture_date[11:19] if len(fixture_date) >= 19 else None

    if not start_date:
        return None

    league = item.get("league", {})
    teams_data = item.get("teams", {})
    home = teams_data.get("home", {})
    away = teams_data.get("away", {})
    venue_data = fixture.get("venue", {})

    home_name = home.get("name", "")
    away_name = away.get("name", "")

    if not home_name or not away_name:
        return None

    venue_name = venue_data.get("name") or ""
    venue_city = venue_data.get("city") or normalized_city

    event = {
        "title": f"{home_name} vs {away_name}",
        "category": "sport",
        "subcategory": league.get("name") or "Football",
        "start_date": start_date,
        "start_time": start_time,
        "city": venue_city,
        "country": league.get("country") or country_code,
        "venue": venue_name,
        "source_name": "API-Football",
        "source_url": None,
        "ticket_url": None,
        "image_url": None,
        "price_min": None,
        "price_max": None,
        "currency": None,
        "is_vip_available": False,
        "status": fixture.get("status", {}).get("long") or "scheduled",
        "league": league.get("name") or "Football",
        "home_team": home_name,
        "away_team": away_name,
        "fixture_id": fixture_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    if requested_city and not event_matches_requested_city(event, requested_city):
        return None

    event["ticket_url"] = build_ticket_search_url(event)
    event["ai_score"] = calculate_ai_score(event)

    return event


def get_api_football_events_by_league(city="", country="", from_date="", to_date="", size=80):
    normalized_city = normalize_city(city)
    country_code = normalize_country_code(country)
    city_key = get_football_city_key(city, country)

    leagues = FOOTBALL_CITY_LEAGUES.get(city_key, [])
    football_from, football_to = get_default_football_dates(from_date, to_date)
    football_season = get_football_season(football_from)

    events = []
    seen_fixture_ids = set()

    for league in leagues:
        params = {
            "league": league["id"],
            "season": football_season,
            "from": football_from,
            "to": football_to,
        }

        data = call_api_football("/fixtures", params)
        if not data:
            continue

        for item in data.get("response", []):
            fixture_id = item.get("fixture", {}).get("id")

            if fixture_id and fixture_id in seen_fixture_ids:
                continue

            event = football_fixture_to_event(item, normalized_city, country_code, city)

            if not event:
                continue

            if fixture_id:
                seen_fixture_ids.add(fixture_id)

            event["football_season"] = football_season
            event["football_search_type"] = "league"
            events.append(event)

    return events[:size]


def get_api_football_events_by_team(city="", country="", from_date="", to_date="", size=80):
    normalized_city = normalize_city(city)
    country_code = normalize_country_code(country)
    city_key = get_football_city_key(city, country)

    teams = FOOTBALL_CITY_TEAMS.get(city_key, [])
    football_from, football_to = get_default_football_dates(from_date, to_date)
    football_season = get_football_season(football_from)

    events = []
    seen_fixture_ids = set()

    for team in teams[:8]:
        params = {
            "team": team["id"],
            "season": football_season,
            "from": football_from,
            "to": football_to,
        }

        data = call_api_football("/fixtures", params)
        if not data:
            continue

        for item in data.get("response", []):
            fixture_id = item.get("fixture", {}).get("id")

            if fixture_id and fixture_id in seen_fixture_ids:
                continue

            event = football_fixture_to_event(item, normalized_city, country_code, city)

            if not event:
                continue

            if fixture_id:
                seen_fixture_ids.add(fixture_id)

            if not event.get("venue"):
                event["venue"] = team.get("venue", "")

            event["football_season"] = football_season
            event["football_search_type"] = "team"
            events.append(event)

    return events[:size]


def get_api_football_events(city="", country="", from_date="", to_date="", category="", size=80):
    if not FOOTBALL_API_KEY:
        return []

    if category and category != "sport":
        return []

    events = (
        get_api_football_events_by_league(city, country, from_date, to_date, size)
        + get_api_football_events_by_team(city, country, from_date, to_date, size)
    )

    return dedupe_events(events)[:size]


def build_debug_football_payload(city="", country="", from_date="", to_date=""):
    normalized_city = normalize_city(city)
    country_code = normalize_country_code(country)
    city_key = get_football_city_key(city, country)
    football_from, football_to = get_default_football_dates(from_date, to_date)
    football_season = get_football_season(football_from)

    leagues = FOOTBALL_CITY_LEAGUES.get(city_key, [])
    teams = FOOTBALL_CITY_TEAMS.get(city_key, [])

    league_requests = []
    team_requests = []

    for league in leagues:
        params = {
            "league": league["id"],
            "season": football_season,
            "from": football_from,
            "to": football_to,
        }
        result = debug_api_football_request("/fixtures", params)
        result["league_config"] = league
        league_requests.append(result)

    for team in teams[:8]:
        params = {
            "team": team["id"],
            "season": football_season,
            "from": football_from,
            "to": football_to,
        }
        result = debug_api_football_request("/fixtures", params)
        result["team_config"] = team
        team_requests.append(result)

    return {
        "football_api_key_present": bool(FOOTBALL_API_KEY),
        "base_url": FOOTBALL_API_BASE_URL,
        "input": {
            "city": city,
            "country": country,
            "from_date": from_date,
            "to_date": to_date,
        },
        "normalized": {
            "city": normalized_city,
            "country_code": country_code,
            "city_key": city_key,
            "football_from": football_from,
            "football_to": football_to,
            "football_season": football_season,
        },
        "mapped_leagues": leagues,
        "mapped_teams": teams,
        "league_requests": league_requests,
        "team_requests": team_requests,
    }


def get_all_events(city="", country="", from_date="", to_date="", category="", size=80):
    events = []

    events += get_ticketmaster_events(city, country, from_date, to_date, category, size)
    events += get_predicthq_events(city, country, from_date, to_date, category, size)
    events += get_api_football_events(city, country, from_date, to_date, category, size)
    events += get_eventbrite_events(city, country, from_date, to_date, category, size)

    events = dedupe_events(events)
    events = [event for event in events if event_is_in_range(event, from_date, to_date)]
    events = [event for event in events if event_matches_requested_city(event, city)]

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
                "provider": "Ticketmaster + PredictHQ + API-Football + Eventbrite",
                "endpoints": {
                    "health": "/health",
                    "events": "/events?city=rome&country=IT",
                    "debug_football": "/debug-football?city=rome&country=IT&from_date=2026-02-01&to_date=2026-04-30",
                    "debug_eventbrite": "/debug-eventbrite?city=rome&country=IT&category=culture&from_date=2026-02-01&to_date=2026-04-30",
                    "eventbrite_test": "/events?city=rome&country=IT&category=culture&from_date=2026-02-01&to_date=2026-04-30",
                    "sport_london": "/events?city=london&country=GB&category=sport",
                    "sport_rome": "/events?city=rome&country=IT&category=sport",
                    "concert": "/events?city=new%20york&country=US&category=concert"
                }
            })
            return

        if parsed.path == "/health":
            self.send_json({
                "status": "ok",
                "service": "WELOVEIT Events API",
                "provider": "Ticketmaster + PredictHQ + API-Football + Eventbrite",
                "api_key_present": bool(TICKETMASTER_API_KEY),
                "predict_api_key_present": bool(PREDICT_API_KEY),
                "predict_api_url_present": bool(PREDICT_API_URL),
                "football_api_key_present": bool(FOOTBALL_API_KEY),
                "eventbrite_api_key_present": bool(EVENTBRITE_API_KEY),
                "version": "ticketmaster-predicthq-football-eventbrite-v13-debug-eventbrite"
            })
            return

        if parsed.path == "/debug-football":
            city = query.get("city", query.get("destination", [""]))[0]
            country = query.get("country", query.get("countryCode", [""]))[0]
            from_date = query.get("from_date", [""])[0]
            to_date = query.get("to_date", [""])[0]

            payload = build_debug_football_payload(
                city=city,
                country=country,
                from_date=from_date,
                to_date=to_date
            )

            self.send_json(payload)
            return

        if parsed.path == "/debug-eventbrite":
            city = query.get("city", query.get("destination", [""]))[0]
            country = query.get("country", query.get("countryCode", [""]))[0]
            from_date = query.get("from_date", [""])[0]
            to_date = query.get("to_date", [""])[0]
            category = query.get("category", [""])[0]

            payload = build_debug_eventbrite_payload(
                city=city,
                country=country,
                from_date=from_date,
                to_date=to_date,
                category=category
            )

            self.send_json(payload)
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
