import os
import re
import json
from difflib import SequenceMatcher
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode, urlparse, parse_qs, quote_plus
from urllib.request import urlopen, Request
from urllib.error import HTTPError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


PORT = int(os.environ.get("PORT", "8000"))

TICKETMASTER_API_KEY = os.environ.get("TICKETMASTER_API_KEY")
PREDICT_API_KEY = os.environ.get("PREDICT_API_KEY")
PREDICT_API_URL = os.environ.get("PREDICT_API_URL", "https://api.predicthq.com/v1/events/")

FOOTBALL_API_KEY = os.environ.get("FOOTBALL_API_KEY")
FOOTBALL_API_BASE_URL = "https://v3.football.api-sports.io"

# v14: la chiave resta in /health, ma NON chiamiamo più /events/search/
# perché il debug v13 ha confermato HTTP 404 su quell'endpoint.
EVENTBRITE_API_KEY = os.environ.get("EVENTBRITE_API_KEY")
EVENTBRITE_API_BASE_URL = "https://www.eventbriteapi.com/v3"

SEATGEEK_CLIENT_ID = os.environ.get("SEATGEEK_CLIENT_ID")
SEATGEEK_CLIENT_SECRET = os.environ.get("SEATGEEK_CLIENT_SECRET")
SEATGEEK_API_BASE_URL = "https://api.seatgeek.com/2"


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
    "osaka": "Osaka",
    "kyoto": "Kyoto",
    "yokohama": "Yokohama",
    "madrid": "Madrid",
    "barcellona": "Barcelona",
    "barcelona": "Barcelona",
    "berlino": "Berlin",
    "berlin": "Berlin",
    "monaco": "Munich",
    "munich": "Munich",
    "toronto": "Toronto",
    "vancouver": "Vancouver",
    "montreal": "Montreal",
    "montréal": "Montreal",
    "sao paulo": "São Paulo",
    "san paolo": "São Paulo",
    "rio": "Rio de Janeiro",
    "rio de janeiro": "Rio de Janeiro",
    "buenos aires": "Buenos Aires",
    "shanghai": "Shanghai",
    "pechino": "Beijing",
    "beijing": "Beijing",
}


COUNTRY_NAME_MAP = {
    "IT": "italy",
    "US": "usa",
    "GB": "uk",
    "FR": "france",
    "ES": "spain",
    "DE": "germany",
    "JP": "japan",
    "BR": "brazil",
    "AR": "argentina",
    "CA": "canada",
    "CN": "china",
}


DEFAULT_COUNTRY_CITY = {
    "IT": "Rome",
    "US": "New York",
    "GB": "London",
    "FR": "Paris",
    "ES": "Madrid",
    "DE": "Berlin",
    "JP": "Tokyo",
    "BR": "São Paulo",
    "AR": "Buenos Aires",
    "CA": "Toronto",
    "CN": "Shanghai",
}

COUNTRY_ONLY_ALIASES = {
    "italia": "IT",
    "italy": "IT",
    "stati uniti": "US",
    "usa": "US",
    "us": "US",
    "united states": "US",
    "united states of america": "US",
    "regno unito": "GB",
    "uk": "GB",
    "gb": "GB",
    "united kingdom": "GB",
    "francia": "FR",
    "france": "FR",
    "spagna": "ES",
    "spain": "ES",
    "germania": "DE",
    "germany": "DE",
    "giappone": "JP",
    "japan": "JP",
    "brasile": "BR",
    "brazil": "BR",
    "argentina": "AR",
    "canada": "CA",
    "cina": "CN",
    "china": "CN",
}


PREDICTHQ_CATEGORY_MAP = {
    "concert": "concerts,festivals,performing-arts",
    "sport": "sports",
    "motorsport": "sports",
    "horse_racing": "sports",
    "theatre": "performing-arts",
    "culture": "performing-arts,community,festivals,expos,conferences",
}


SEATGEEK_CITY_GEO = {
    "new york|us": "40.7128,-74.0060",
    "los angeles|us": "34.0522,-118.2437",
    "las vegas|us": "36.1699,-115.1398",
    "miami|us": "25.7617,-80.1918",
    "chicago|us": "41.8781,-87.6298",
    "san francisco|us": "37.7749,-122.4194",
    "london|gb": "51.5074,-0.1278",
    "paris|fr": "48.8566,2.3522",
    "rome|it": "41.9028,12.4964",
    "milan|it": "45.4642,9.1900",
    "madrid|es": "40.4168,-3.7038",
    "barcelona|es": "41.3851,2.1734",
    "berlin|de": "52.5200,13.4050",
    "munich|de": "48.1351,11.5820",
    "tokyo|jp": "35.6762,139.6503",
    "osaka|jp": "34.6937,135.5023",
    "kyoto|jp": "35.0116,135.7681",
    "toronto|ca": "43.6532,-79.3832",
    "vancouver|ca": "49.2827,-123.1207",
    "montreal|ca": "45.5017,-73.5673",
    "são paulo|br": "-23.5505,-46.6333",
    "rio de janeiro|br": "-22.9068,-43.1729",
    "buenos aires|ar": "-34.6037,-58.3816",
    "shanghai|cn": "31.2304,121.4737",
    "beijing|cn": "39.9042,116.4074",
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


RECURRING_LOW_PRIORITY_TITLES = [
    "the great opera arias concert",
    "opera arias concert",
    "stacey kent",
    "mononeon",
    "vince giordano and the nighthawks",
    "live weekly",
]


LOW_QUALITY_TITLE_WORDS = [
    "parking",
    "parking pass",
    "parking lot",
    "parkwhiz",
    "garage",
    "international conference",
    "conference on",
    "academic",
    "science and economics",
    "social science",
    "humanities",
    "proceedings",
    "symposium",
    "congress",
    "seminar",
    "webinar",
    "round table",
    "call for papers",
    "icssh",
    "ic3se",
    "npsa",
    "ipd",
    "mun ",
    "model united nations",
    "dentistry",
    "inclusive peace education",
]


PREMIUM_EXPERIENCE_WORDS = [
    "festival",
    "concert",
    "live",
    "stadium",
    "arena",
    "grand prix",
    "derby",
    "marathon",
    "tennis",
    "theatre",
    "theater",
    "musical",
    "opera",
    "nightlife",
    "food",
    "wine",
    "design",
    "fashion",
    "art",
    "exhibition",
    "expo",
    "show",
    "final",
    "world cup",
    "champions league",
    "serie a",
    "premier league",
    "la liga",
    "nba",
    "nfl",
    "ufc",
    "wwe",
]


PREMIUM_VENUE_WORDS = [
    "stadium",
    "arena",
    "dome",
    "olympic",
    "olimpico",
    "o2",
    "wembley",
    "madison square garden",
    "royal albert hall",
    "tokyo dome",
    "foro italico",
    "san siro",
    "camp nou",
    "santiago bernabeu",
    "parc des princes",
    "allianz arena",
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
        "united states of america": "US",
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
        "ca": "CA",
        "canada": "CA",
        "cn": "CN",
        "china": "CN",
        "cina": "CN",
        "br": "BR",
        "brazil": "BR",
        "brasile": "BR",
        "ar": "AR",
        "argentina": "AR",
    }

    if not key:
        return ""

    return country_map.get(key, country.strip().upper())


def normalize_request_location(city="", country=""):
    """
    v18: se l'utente cerca solo un paese ("giappone", "japan", "canada"),
    lo trasformiamo in una città principale + country code.
    Questo evita query generiche che possono far entrare eventi USA/Canada non richiesti.
    """
    raw_city = clean_text(city)
    country_code = normalize_country_code(country)

    if not country_code and raw_city in COUNTRY_ONLY_ALIASES:
        country_code = COUNTRY_ONLY_ALIASES[raw_city]
        city = DEFAULT_COUNTRY_CITY.get(country_code, "")

    if country_code and not clean_text(city):
        city = DEFAULT_COUNTRY_CITY.get(country_code, "")

    return normalize_city(city), country_code


def event_matches_requested_country(event, requested_country):
    requested_code = normalize_country_code(requested_country)

    if not requested_code:
        return True

    event_country = event.get("country") or ""
    event_country_code = normalize_country_code(event_country)

    if event_country_code == requested_code:
        return True

    return False


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
        "day one",
        "day two",
        "day three",
    ]

    for word in remove_words:
        title = title.replace(word, " ")

    title = re.sub(r"[^a-z0-9\s]", " ", title)
    title = re.sub(r"\s+", " ", title).strip()

    return title


def title_core_for_dedupe(title):
    value = normalize_event_title(title)

    for sep in [" with ", " w ", " at ", " tour ", " live ", " presents "]:
        if sep in value:
            value = value.split(sep)[0].strip()

    # Mantieni le prime parole significative: utile per "Don Toliver..." vs "Don Toliver: Octane Tour".
    words = [w for w in value.split() if len(w) > 1]
    return " ".join(words[:3]).strip()


def similar_text(a, b):
    a = normalize_event_title(a)
    b = normalize_event_title(b)

    if not a or not b:
        return False

    if a == b:
        return True

    if a in b or b in a:
        return True

    core_a = title_core_for_dedupe(a)
    core_b = title_core_for_dedupe(b)

    if core_a and core_b:
        if core_a == core_b:
            return True
        if len(core_a) >= 6 and (core_a in b or core_a in core_b):
            return True
        if len(core_b) >= 6 and (core_b in a or core_b in core_a):
            return True

    ratio = SequenceMatcher(None, a, b).ratio()
    return ratio >= 0.84


def should_drop_low_value_event(event):
    title = clean_text(event.get("title"))
    source = clean_text(event.get("source_name"))

    low_value_words = [
        "parking",
        "parking pass",
        "parking lot",
        "parkwhiz",
        "garage",
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


def is_low_quality_conference(event):
    title = clean_text(event.get("title"))
    subcategory = clean_text(event.get("subcategory"))
    category = clean_text(event.get("category"))
    venue = clean_text(event.get("venue"))

    if category != "culture":
        return False

    looks_like_conference = (
        "conference" in subcategory
        or "conference" in title
        or "congress" in title
        or "symposium" in title
        or "seminar" in title
    )

    if not looks_like_conference:
        return False

    has_premium_signal = any(word in title for word in PREMIUM_EXPERIENCE_WORDS)
    has_premium_venue = any(word in venue for word in PREMIUM_VENUE_WORDS)

    if has_premium_signal or has_premium_venue:
        return False

    if any(word in title for word in LOW_QUALITY_TITLE_WORDS):
        return True

    return True


def calculate_quality_adjustment(event):
    title = clean_text(event.get("title"))
    subcategory = clean_text(event.get("subcategory"))
    category = clean_text(event.get("category"))
    venue = clean_text(event.get("venue"))
    source = clean_text(event.get("source_name"))

    adjustment = 0

    if any(word in title for word in PREMIUM_EXPERIENCE_WORDS):
        adjustment += 12

    if any(word in subcategory for word in ["concert", "festival", "sport", "tennis", "marathon", "theatre", "musical", "expo"]):
        adjustment += 8

    if any(word in venue for word in PREMIUM_VENUE_WORDS):
        adjustment += 8

    if source == "ticketmaster":
        adjustment += 6

    if source == "seatgeek":
        adjustment += 8

    if source == "api-football":
        adjustment += 8

    if source == "predicthq" and category == "sport":
        adjustment += 5

    if is_low_quality_conference(event):
        adjustment -= 28

    if any(word in title for word in LOW_QUALITY_TITLE_WORDS):
        adjustment -= 16

    # Titoli molto lunghi e tecnici tendono a essere meno adatti a un travel engine.
    if len(title) > 85 and category == "culture":
        adjustment -= 6

    # Eventi senza venue sono meno forti, ma non vanno eliminati.
    if not venue:
        adjustment -= 4

    return adjustment


def apply_quality_ranking(event):
    """
    v17: scoring meno saturo.
    Prima v15/v16 portava troppi eventi a 99/100. Qui separiamo segnali forti,
    sorgente, venue, ticket/image e penalità per eventi generici/ricorrenti.
    """
    title = clean_text(event.get("title"))
    venue = clean_text(event.get("venue"))
    category = clean_text(event.get("category"))
    subcategory = clean_text(event.get("subcategory"))
    source = clean_text(event.get("source_name"))

    score = 55

    # Sorgente: preferiamo fonti ticketabili reali.
    if source == "ticketmaster":
        score += 12
    elif source == "seatgeek":
        score += 13
    elif source == "api-football":
        score += 11
    elif source == "predicthq":
        score += 4

    # Prove concrete di acquistabilità/qualità.
    if event.get("ticket_url"):
        score += 7
    if event.get("image_url"):
        score += 5
    if event.get("price_min") is not None:
        score += 3
    if venue:
        score += 4

    # Categoria.
    if category == "sport":
        score += 9
    elif category == "concert":
        score += 8
    elif category == "theatre":
        score += 7
    elif category == "culture":
        score += 1

    # Segnali premium.
    if any(word in title for word in ["festival", "grand prix", "final", "derby", "world cup", "champions league"]):
        score += 10

    if any(word in title for word in ["serie a", "premier league", "la liga", "nba", "nfl", "ufc", "wwe"]):
        score += 8

    if any(word in venue for word in PREMIUM_VENUE_WORDS):
        score += 10

    if any(word in title for word in BIG_MATCH_WORDS):
        score += 5

    # Rank PredictHQ, ma senza farlo dominare.
    try:
        rank = int(event.get("rank") or 0)
        if rank:
            score += min(rank // 20, 5)
    except Exception:
        pass

    # Penalità qualità.
    if is_low_quality_conference(event):
        score -= 28

    if any(word in title for word in LOW_QUALITY_TITLE_WORDS):
        score -= 14

    if not venue:
        score -= 5

    # PredictHQ spesso mette performer come venue: abbassiamo.
    if source == "predicthq" and venue and normalize_event_title(venue) == normalize_event_title(title):
        score -= 9

    # Eventi troppo lunghi/generici.
    if len(title) > 90:
        score -= 5

    if any(word in title for word in ["student innovation showcase", "weekly", "fundraiser", "conference", "seminar", "round table"]):
        score -= 8

    # Repliche note.
    if any(word in normalize_event_title(title) for word in RECURRING_LOW_PRIORITY_TITLES):
        score -= 6

    score = max(35, min(99, int(score)))

    event["ai_score"] = score
    event["quality_score"] = score
    event["is_low_quality_conference"] = is_low_quality_conference(event)

    return event


def filter_low_quality_events(events, category=""):
    """
    v15: non facciamo dump API. Togliamo eventi molto deboli e limitiamo conferenze generiche.
    """
    output = []
    generic_conference_count = 0

    for event in events:
        event = apply_quality_ranking(event)

        event_category = clean_text(event.get("category"))
        title = clean_text(event.get("title"))

        # Quando l'utente cerca sport/concerti/teatro, sii severo.
        if category and event_category != clean_text(category):
            continue

        # Elimina conferenze tecniche deboli, ma tienine poche se la categoria è culture o tutte.
        if event.get("is_low_quality_conference"):
            generic_conference_count += 1

            if generic_conference_count > 3:
                continue

            if event.get("quality_score", 0) < 62:
                continue

        # Elimina titoli manifestamente poco turistici se il punteggio è basso.
        if any(word in title for word in LOW_QUALITY_TITLE_WORDS) and event.get("quality_score", 0) < 68:
            continue

        if event.get("quality_score", 0) < 55:
            continue

        output.append(event)

    return output


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

    if source == "seatgeek":
        score += 9

    if source == "api-football":
        score += 7

    if source == "predicthq":
        score += 2

    if event.get("eventbrite_search_url"):
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


def limit_recurring_events(events, max_per_title_venue=2):
    """
    v14: limita repliche molto simili su date diverse.
    Esempio: "The Great Opera Arias Concert" su molte date -> tiene solo le prime 2.
    """
    counts = {}
    output = []

    for event in events:
        title_key = normalize_event_title(event.get("title"))
        venue_key = clean_text(event.get("venue"))
        city_key = clean_text(event.get("city"))
        category = clean_text(event.get("category"))
        source = clean_text(event.get("source_name"))

        key = f"{title_key}|{venue_key}|{city_key}"

        is_recurring_candidate = (
            category in ["culture", "theatre", "concert"]
            and source in ["predicthq", "ticketmaster", "seatgeek"]
            and (
                any(t in title_key for t in RECURRING_LOW_PRIORITY_TITLES)
                or "day two" in clean_text(event.get("title"))
                or "day three" in clean_text(event.get("title"))
                or "spring pass" in clean_text(event.get("title"))
                or "weekly" in title_key
            )
        )

        if not is_recurring_candidate:
            output.append(event)
            continue

        counts[key] = counts.get(key, 0) + 1
        if counts[key] <= max_per_title_venue:
            output.append(event)

    return output


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


def build_eventbrite_search_url(event=None, city="", country=""):
    """
    v14 fallback Eventbrite: non chiama API, crea link utile di ricerca pubblica.
    """
    if event:
        city = event.get("city") or city
        country = event.get("country") or country
        query = event.get("title") or ""
    else:
        query = ""

    normalized_city = normalize_city(city or "")
    country_code = normalize_country_code(country or "")
    country_slug = COUNTRY_NAME_MAP.get(country_code, (country_code or "events").lower())

    city_slug = clean_text(normalized_city).replace(" ", "-")
    if not city_slug:
        city_slug = "events"

    base = f"https://www.eventbrite.com/d/{country_slug}--{city_slug}/"

    if query:
        return base + "?q=" + quote_plus(query)

    return base


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


def enhance_eventbrite_fallback(event):
    """
    Aggiunge un link Eventbrite pubblico ai risultati PredictHQ/Ticketmaster senza fare chiamate API Eventbrite.
    """
    if event.get("category") in ["culture", "concert", "theatre"]:
        event["eventbrite_search_url"] = build_eventbrite_search_url(event=event)
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

    if source_name == "seatgeek":
        score += 7

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



def normalize_seatgeek_type(category):
    if category == "sport":
        return "sports"
    if category == "concert":
        return "concert"
    if category == "theatre":
        return "theater"
    if category == "culture":
        return ""
    return ""


def get_seatgeek_geo(city="", country=""):
    city_key = get_football_city_key(city, country)
    return SEATGEEK_CITY_GEO.get(city_key, "")


def parse_seatgeek_datetime(value):
    if not value:
        return "", None

    # SeatGeek normalmente ritorna datetime_local tipo 2026-06-12T20:00:00
    start_date = value[:10] if len(value) >= 10 else ""
    start_time = value[11:19] if len(value) >= 19 else None
    return start_date, start_time


def read_http_error_body(exc):
    try:
        return exc.read().decode("utf-8")
    except Exception:
        return ""


def get_seatgeek_events(city="", country="", from_date="", to_date="", category="", size=80):
    if not SEATGEEK_CLIENT_ID:
        return []

    normalized_city = normalize_city(city)
    country_code = normalize_country_code(country)
    geo = get_seatgeek_geo(city, country)

    params = {
        "client_id": SEATGEEK_CLIENT_ID,
        "per_page": min(size, 50),
        "sort": "datetime_local.asc",
    }

    # v17: SeatGeek public events endpoint usa client_id. Non inviamo il secret nella query.
    # Alcuni account ricevono 403 se il secret viene passato come parametro.
    if geo:
        params["lat"] = geo.split(",")[0]
        params["lon"] = geo.split(",")[1]
        params["range"] = "50mi"
    elif normalized_city:
        params["venue.city"] = normalized_city

    sg_type = normalize_seatgeek_type(category)
    if sg_type:
        params["type"] = sg_type

    if from_date:
        params["datetime_local.gte"] = f"{from_date}T00:00:00"

    if to_date:
        params["datetime_local.lte"] = f"{to_date}T23:59:59"

    url = SEATGEEK_API_BASE_URL.rstrip("/") + "/events?" + urlencode(params)
    request = Request(url, headers={"User-Agent": "WELOVEIT-Events/1.0"})

    try:
        with urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        print("SeatGeek HTTP error:", exc.code, read_http_error_body(exc))
        return []
    except Exception as exc:
        print("SeatGeek error:", exc)
        return []

    raw_events = data.get("events", [])
    events = []

    for item in raw_events:
        title = item.get("title") or item.get("short_title") or "Unknown event"
        start_date, start_time = parse_seatgeek_datetime(item.get("datetime_local") or item.get("datetime_utc"))

        if not start_date:
            continue

        venue = item.get("venue") or {}
        city_name = venue.get("city") or normalized_city
        country_name = venue.get("country") or country_code
        venue_name = venue.get("name") or ""

        if normalized_city and city_name:
            if clean_text(normalized_city) not in clean_text(city_name) and clean_text(city_name) not in city_aliases_for(normalized_city):
                continue

        performers = item.get("performers") or []
        image_url = None
        if performers:
            image_url = (
                performers[0].get("image")
                or performers[0].get("images", {}).get("huge")
                or performers[0].get("images", {}).get("large")
            )

        stats = item.get("stats") or {}
        price_min = stats.get("lowest_price")
        price_max = stats.get("highest_price")

        taxonomies = item.get("taxonomies") or []
        taxonomy_names = " ".join([str(t.get("name") or "") for t in taxonomies])
        mapped_category = normalize_category(taxonomy_names or item.get("type") or "")

        # SeatGeek usa "theater"; normalizziamo meglio.
        if "theater" in clean_text(item.get("type")) or "theater" in clean_text(taxonomy_names):
            mapped_category = "theatre"

        if category and mapped_category != category:
            # sport/concert/theatre sono severi; culture può includere theatre/concert.
            if not (category == "culture" and mapped_category in ["culture", "theatre", "concert"]):
                continue

        event = {
            "title": title,
            "category": mapped_category,
            "subcategory": item.get("type") or taxonomy_names or "SeatGeek event",
            "start_date": start_date,
            "start_time": start_time,
            "city": city_name,
            "country": country_name,
            "venue": venue_name,
            "source_name": "SeatGeek",
            "source_url": item.get("url"),
            "ticket_url": item.get("url"),
            "image_url": image_url,
            "price_min": price_min,
            "price_max": price_max,
            "currency": "USD" if country_code == "US" else None,
            "is_vip_available": False,
            "status": "active",
            "seatgeek_score": item.get("score"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        event = enhance_eventbrite_fallback(event)
        event["ai_score"] = calculate_ai_score(event)
        events.append(event)

    return events


def build_debug_seatgeek_payload(city="", country="", from_date="", to_date="", category=""):
    normalized_city = normalize_city(city)
    country_code = normalize_country_code(country)
    geo = get_seatgeek_geo(city, country)

    params = {
        "client_id": "***" if SEATGEEK_CLIENT_ID else "",
        "per_page": 5,
        "sort": "datetime_local.asc",
    }

    if geo:
        params["lat"] = geo.split(",")[0]
        params["lon"] = geo.split(",")[1]
        params["range"] = "50mi"
    elif normalized_city:
        params["venue.city"] = normalized_city

    sg_type = normalize_seatgeek_type(category)
    if sg_type:
        params["type"] = sg_type

    if from_date:
        params["datetime_local.gte"] = f"{from_date}T00:00:00"

    if to_date:
        params["datetime_local.lte"] = f"{to_date}T23:59:59"

    real_params = dict(params)
    if SEATGEEK_CLIENT_ID:
        real_params["client_id"] = SEATGEEK_CLIENT_ID
    # v17: debug con client_id only. Il secret resta su Render ma non viene inviato.
    url = SEATGEEK_API_BASE_URL.rstrip("/") + "/events?" + urlencode(real_params)

    if not SEATGEEK_CLIENT_ID:
        return {
            "seatgeek_client_id_present": False,
            "seatgeek_client_secret_present": bool(SEATGEEK_CLIENT_SECRET),
            "base_url": SEATGEEK_API_BASE_URL,
            "input": {"city": city, "country": country, "from_date": from_date, "to_date": to_date, "category": category},
            "normalized": {"city": normalized_city, "country_code": country_code, "geo": geo},
            "ok": False,
            "error": "missing SEATGEEK_CLIENT_ID",
        }

    request = Request(url, headers={"User-Agent": "WELOVEIT-Events/1.0"})

    try:
        with urlopen(request, timeout=20) as response:
            status_code = response.status
            data = json.loads(response.read().decode("utf-8"))

        sample = []
        for item in data.get("events", [])[:5]:
            venue = item.get("venue") or {}
            sample.append({
                "id": item.get("id"),
                "title": item.get("title"),
                "type": item.get("type"),
                "datetime_local": item.get("datetime_local"),
                "venue": venue.get("name"),
                "city": venue.get("city"),
                "country": venue.get("country"),
                "url": item.get("url"),
            })

        safe_url = SEATGEEK_API_BASE_URL.rstrip("/") + "/events?" + urlencode(params)

        return {
            "seatgeek_client_id_present": bool(SEATGEEK_CLIENT_ID),
            "seatgeek_client_secret_present": bool(SEATGEEK_CLIENT_SECRET),
            "base_url": SEATGEEK_API_BASE_URL,
            "input": {"city": city, "country": country, "from_date": from_date, "to_date": to_date, "category": category},
            "normalized": {"city": normalized_city, "country_code": country_code, "geo": geo},
            "ok": True,
            "status_code": status_code,
            "request_url": safe_url,
            "events_count": len(data.get("events", [])),
            "meta": data.get("meta"),
            "sample": sample,
        }

    except HTTPError as exc:
        safe_url = SEATGEEK_API_BASE_URL.rstrip("/") + "/events?" + urlencode(params)
        return {
            "seatgeek_client_id_present": bool(SEATGEEK_CLIENT_ID),
            "seatgeek_client_secret_present": bool(SEATGEEK_CLIENT_SECRET),
            "seatgeek_auth_mode": "client_id_only",
            "base_url": SEATGEEK_API_BASE_URL,
            "input": {"city": city, "country": country, "from_date": from_date, "to_date": to_date, "category": category},
            "normalized": {"city": normalized_city, "country_code": country_code, "geo": geo},
            "ok": False,
            "status_code": exc.code,
            "error": str(exc),
            "error_body": read_http_error_body(exc),
            "request_url": safe_url,
        }
    except Exception as exc:
        safe_url = SEATGEEK_API_BASE_URL.rstrip("/") + "/events?" + urlencode(params)
        return {
            "seatgeek_client_id_present": bool(SEATGEEK_CLIENT_ID),
            "seatgeek_client_secret_present": bool(SEATGEEK_CLIENT_SECRET),
            "seatgeek_auth_mode": "client_id_only",
            "base_url": SEATGEEK_API_BASE_URL,
            "input": {"city": city, "country": country, "from_date": from_date, "to_date": to_date, "category": category},
            "normalized": {"city": normalized_city, "country_code": country_code, "geo": geo},
            "ok": False,
            "error": str(exc),
            "request_url": safe_url,
        }


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

        event = enhance_eventbrite_fallback(event)
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
        event = enhance_eventbrite_fallback(event)
        event["ticket_url"] = build_ticket_search_url(event)
        event["ai_score"] = calculate_ai_score(event)
        events.append(event)

    return events


def get_eventbrite_events(city="", country="", from_date="", to_date="", category="", size=80):
    """
    v14: Eventbrite API public city search disattivata.
    Motivo: /events/search/ ha restituito HTTP 404 nel debug v13.
    Manteniamo Eventbrite come fallback link tramite eventbrite_search_url.
    """
    return []


def build_debug_eventbrite_payload(city="", country="", from_date="", to_date="", category=""):
    normalized_city = normalize_city(city)
    country_code = normalize_country_code(country)
    location_address = normalized_city
    if country_code:
        location_address = f"{normalized_city}, {country_code}"

    fallback_url = build_eventbrite_search_url(city=normalized_city, country=country_code)

    return {
        "eventbrite_api_key_present": bool(EVENTBRITE_API_KEY),
        "base_url": EVENTBRITE_API_BASE_URL,
        "status": "fallback_only",
        "reason": "Eventbrite /events/search/ returned HTTP 404 in v13 debug; API search disabled in v14.",
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
        "eventbrite_public_search_url": fallback_url,
        "example_eventbrite_query_url": fallback_url + "?q=" + quote_plus("events " + normalized_city),
    }


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
    city, country = normalize_request_location(city, country)
    events = []

    events += get_ticketmaster_events(city, country, from_date, to_date, category, size)
    events += get_seatgeek_events(city, country, from_date, to_date, category, size)
    events += get_predicthq_events(city, country, from_date, to_date, category, size)
    events += get_api_football_events(city, country, from_date, to_date, category, size)
    events += get_eventbrite_events(city, country, from_date, to_date, category, size)

    events = dedupe_events(events)
    events = [event for event in events if event_is_in_range(event, from_date, to_date)]
    events = [event for event in events if event_matches_requested_country(event, country)]
    events = [event for event in events if event_matches_requested_city(event, city)]
    events = [apply_quality_ranking(event) for event in events]
    events = filter_low_quality_events(events, category=category)
    events = limit_recurring_events(events, max_per_title_venue=2)

    # v15: prima qualità, poi data. WELOVEIT deve sembrare un prodotto curato, non un dump cronologico.
    events.sort(key=lambda event: (
        -(event.get("quality_score") or event.get("ai_score") or 0),
        event.get("start_date") or "",
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
                "provider": "Ticketmaster + SeatGeek + PredictHQ + API-Football + Eventbrite fallback",
                "endpoints": {
                    "health": "/health",
                    "events": "/events?city=rome&country=IT",
                    "debug_football": "/debug-football?city=rome&country=IT&from_date=2026-02-01&to_date=2026-04-30",
                    "debug_seatgeek": "/debug-seatgeek?city=new%20york&country=US&category=concert",
                    "debug_eventbrite": "/debug-eventbrite?city=rome&country=IT&category=culture&from_date=2026-02-01&to_date=2026-04-30",
                    "culture_rome": "/events?city=rome&country=IT&category=culture&from_date=2026-02-01&to_date=2026-04-30",
                    "sport_london": "/events?city=london&country=GB&category=sport",
                    "sport_rome": "/events?city=rome&country=IT&category=sport",
                    "concert": "/events?city=new%20york&country=US&category=concert",
                    "tokyo": "/events?city=tokyo&country=JP&from_date=2026-05-14&to_date=2026-07-25",
                    "japan_country_search": "/events?city=giappone&from_date=2026-05-14&to_date=2026-07-25"
                }
            })
            return

        if parsed.path == "/health":
            self.send_json({
                "status": "ok",
                "service": "WELOVEIT Events API",
                "provider": "Ticketmaster + SeatGeek + PredictHQ + API-Football + Eventbrite fallback",
                "api_key_present": bool(TICKETMASTER_API_KEY),
                "predict_api_key_present": bool(PREDICT_API_KEY),
                "predict_api_url_present": bool(PREDICT_API_URL),
                "football_api_key_present": bool(FOOTBALL_API_KEY),
                "eventbrite_api_key_present": bool(EVENTBRITE_API_KEY),
                "seatgeek_client_id_present": bool(SEATGEEK_CLIENT_ID),
                "seatgeek_client_secret_present": bool(SEATGEEK_CLIENT_SECRET),
                "eventbrite_mode": "fallback_only",
                "seatgeek_auth_mode": "client_id_only",
                "country_city_fix": True,
                "parking_filter": True,
                "version": "ticketmaster-seatgeek-predicthq-football-eventbrite-v18-country-city-parking-fix"
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

        if parsed.path == "/debug-seatgeek":
            city = query.get("city", query.get("destination", [""]))[0]
            country = query.get("country", query.get("countryCode", [""]))[0]
            from_date = query.get("from_date", [""])[0]
            to_date = query.get("to_date", [""])[0]
            category = query.get("category", [""])[0]

            payload = build_debug_seatgeek_payload(
                city=city,
                country=country,
                from_date=from_date,
                to_date=to_date,
                category=category
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

            city, country = normalize_request_location(city, country)

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
