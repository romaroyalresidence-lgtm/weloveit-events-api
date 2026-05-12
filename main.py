"""
WELOVEIT EVENTS - Render-ready zero-dependency backend prototype

Run locally:
    python main.py

Test:
    python main.py --test

Render start command:
    python main.py --serve

Endpoints:
    /health
    /events
    /travel-search?destination=Tokyo&from_date=2026-07-01&to_date=2026-07-10
    /sources
    POST /import
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import threading
import time as sleep_time
import unittest
from dataclasses import asdict, dataclass, field
from datetime import datetime, UTC
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse


HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8000"))
SCAN_INTERVAL_SECONDS = 6 * 60 * 60
SIMILARITY_THRESHOLD = 0.88

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("weloveit-events-api")


class EventCategory(str, Enum):
    SPORT = "sport"
    CONCERT = "concert"
    THEATRE = "theatre"
    FESTIVAL = "festival"
    CULTURE = "culture"
    MOTORSPORT = "motorsport"
    HORSE_RACING = "horse_racing"
    EQUESTRIAN = "equestrian"
    VIP_EXPERIENCE = "vip_experience"
    OTHER = "other"


@dataclass
class Event:
    title: str
    category: str
    start_date: str
    source_name: str
    id: int = 0
    fingerprint: str = ""
    subcategory: Optional[str] = None
    start_time: Optional[str] = None
    end_date: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    venue: Optional[str] = None
    source_url: Optional[str] = None
    ticket_url: Optional[str] = None
    image_url: Optional[str] = None
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    currency: Optional[str] = None
    ai_score: float = 0.0
    is_vip_available: bool = False
    is_family_friendly: bool = False
    status: str = "active"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class EventSource:
    name: str
    category: str
    region: str
    base_url: str
    source_type: str = "official_or_partner"
    scan_frequency_hours: int = 6
    is_active: bool = True


@dataclass
class ImportLog:
    source_name: str
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: Optional[str] = None
    imported_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    error_message: Optional[str] = None


class InMemoryDatabase:
    def __init__(self) -> None:
        self.events: List[Event] = []
        self.sources: List[EventSource] = []
        self.import_logs: List[ImportLog] = []
        self._next_event_id = 1
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self.events = []
            self.sources = []
            self.import_logs = []
            self._next_event_id = 1

    def add_source_once(self, source: EventSource) -> None:
        with self._lock:
            if not any(existing.name == source.name for existing in self.sources):
                self.sources.append(source)

    def upsert_event(self, incoming: Event) -> str:
        with self._lock:
            incoming.fingerprint = make_fingerprint(incoming)
            incoming.ai_score = calculate_ai_score(incoming)

            existing = self.find_by_fingerprint_unlocked(incoming.fingerprint)
            if existing is None:
                existing = self.find_similar_event_unlocked(incoming)

            if existing:
                incoming.id = existing.id
                incoming.created_at = existing.created_at
                incoming.updated_at = datetime.now(UTC).isoformat()
                self.events[self.events.index(existing)] = incoming
                return "updated"

            incoming.id = self._next_event_id
            self._next_event_id += 1
            self.events.append(incoming)
            return "inserted"

    def find_by_fingerprint_unlocked(self, fingerprint: str) -> Optional[Event]:
        return next((event for event in self.events if event.fingerprint == fingerprint), None)

    def find_similar_event_unlocked(self, incoming: Event) -> Optional[Event]:
        for candidate in self.events:
            if candidate.start_date != incoming.start_date:
                continue
            if normalize_text(candidate.city) != normalize_text(incoming.city):
                continue

            title_similarity = text_similarity(incoming.title, candidate.title)
            venue_similarity = text_similarity(incoming.venue or "", candidate.venue or "")
            combined = (title_similarity * 0.75) + (venue_similarity * 0.25)

            if combined >= SIMILARITY_THRESHOLD:
                return candidate
        return None

    def search_events(
        self,
        destination: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        category: Optional[str] = None,
        q: Optional[str] = None,
        limit: int = 100,
    ) -> List[Event]:
        with self._lock:
            results = [event for event in self.events if event.status == "active"]

        if destination:
            needle = normalize_text(destination)
            results = [
                event for event in results
                if needle in normalize_text(event.city)
                or needle in normalize_text(event.country)
                or needle in normalize_text(event.venue)
            ]

        if from_date:
            results = [event for event in results if event.start_date >= from_date]
        if to_date:
            results = [event for event in results if event.start_date <= to_date]
        if category:
            results = [event for event in results if event.category == category]
        if q:
            needle = normalize_text(q)
            results = [
                event for event in results
                if needle in normalize_text(event.title)
                or needle in normalize_text(event.subcategory)
                or needle in normalize_text(event.venue)
            ]

        results.sort(key=lambda event: (-event.ai_score, event.start_date, event.title))
        return results[:limit]

    def add_import_log(self, log: ImportLog) -> None:
        with self._lock:
            self.import_logs.append(log)


DB = InMemoryDatabase()
STOP_WORDS = {"fc", "vs", "v", "official", "tickets", "live", "event", "the"}


def normalize_text(value: Optional[str]) -> str:
    if not value:
        return ""
    cleaned = value.lower().strip()
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in cleaned)
    tokens = [token for token in cleaned.split() if token not in STOP_WORDS]
    return " ".join(tokens)


def text_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize_text(left), normalize_text(right)).ratio()


def make_fingerprint(event: Event) -> str:
    key = "|".join([
        normalize_text(event.title),
        event.start_date,
        normalize_text(event.city),
        normalize_text(event.venue),
        normalize_text(event.category),
    ])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def calculate_ai_score(event: Event) -> float:
    score = 50.0
    title = normalize_text(event.title)
    venue = normalize_text(event.venue)

    premium_keywords = [
        "world cup", "final", "derby", "grand prix", "formula 1", "ufc",
        "championship", "broadway", "olympics", "royal ascot", "all star",
        "champions league", "el clasico", "wimbledon", "super bowl", "concert", "musical"
    ]
    iconic_venues = [
        "tokyo dome", "madison square garden", "wembley", "maracana",
        "monza", "la bombonera", "broadway", "ascot racecourse"
    ]

    for keyword in premium_keywords:
        if keyword in title:
            score += 12
    for venue_name in iconic_venues:
        if venue_name in venue:
            score += 10
    if event.is_vip_available:
        score += 8
    if event.price_min is not None and event.price_min > 300:
        score += 5
    if event.category in {EventCategory.VIP_EXPERIENCE.value, EventCategory.MOTORSPORT.value}:
        score += 5

    return min(score, 100.0)


def event_to_dict(event: Event) -> Dict[str, Any]:
    return asdict(event)


class BaseCollector:
    source_name = "Base Collector"
    def fetch_events(self) -> List[Event]:
        raise NotImplementedError


class DemoSportsCollector(BaseCollector):
    source_name = "Demo Global Sports Feed"
    def fetch_events(self) -> List[Event]:
        return [
            Event("Japan vs Italy Rugby", EventCategory.SPORT.value, "2026-07-04", self.source_name, subcategory="Rugby", start_time="19:00", city="Tokyo", country="Japan", venue="Tokyo National Stadium", source_url="https://www.rugby-japan.jp/", ticket_url="https://www.rugby-japan.jp/", is_vip_available=True),
            Event("FC Tokyo vs Yokohama F. Marinos", EventCategory.SPORT.value, "2026-07-05", "J.League", subcategory="Calcio Giapponese J.League", start_time="18:30", city="Tokyo", country="Japan", venue="Ajinomoto Stadium", source_url="https://www.jleague.co/", ticket_url="https://www.jleague.co/"),
            Event("Yomiuri Giants Baseball Game", EventCategory.SPORT.value, "2026-07-06", "Club official", subcategory="Baseball NPB", start_time="18:00", city="Tokyo", country="Japan", venue="Tokyo Dome", source_url="https://www.giants.jp/", ticket_url="https://www.giants.jp/"),
            Event("NHL Regular Season Game", EventCategory.SPORT.value, "2026-11-10", "NHL Tickets", subcategory="Ice Hockey", start_time="19:30", city="New York", country="USA", venue="Madison Square Garden", source_url="https://www.nhl.com/tickets", ticket_url="https://www.nhl.com/tickets"),
            Event("Royal Ascot Horse Racing", EventCategory.HORSE_RACING.value, "2026-06-16", "Ascot Racecourse", subcategory="Horse Racing", start_time="13:00", city="Ascot", country="United Kingdom", venue="Ascot Racecourse", source_url="https://www.ascot.com/", ticket_url="https://www.ascot.com/", is_vip_available=True),
            Event("Formula 1 Grand Prix Weekend", EventCategory.MOTORSPORT.value, "2026-09-06", "Formula 1 Tickets", subcategory="Formula 1", start_time="15:00", city="Monza", country="Italy", venue="Autodromo Nazionale Monza", source_url="https://tickets.formula1.com/", ticket_url="https://tickets.formula1.com/", is_vip_available=True),
        ]


class DemoLiveEventsCollector(BaseCollector):
    source_name = "Demo Concerts Theatre Shows Feed"
    def fetch_events(self) -> List[Event]:
        return [
            Event("International Pop Concert", EventCategory.CONCERT.value, "2026-07-08", "Live Nation / Venue Official", subcategory="Concerti internazionali", start_time="19:00", city="Tokyo", country="Japan", venue="Tokyo Dome", source_url="https://www.livenation.com/", ticket_url="https://www.livenation.com/"),
            Event("Broadway Musical Night", EventCategory.THEATRE.value, "2026-11-12", "Broadway Official", subcategory="Musical Broadway", start_time="20:00", city="New York", country="USA", venue="Broadway Theatre District", source_url="https://www.broadway.com/", ticket_url="https://www.broadway.com/"),
            Event("Cirque du Soleil Show", EventCategory.CULTURE.value, "2026-11-25", "Cirque du Soleil", subcategory="Live show", start_time="21:00", city="Las Vegas", country="USA", venue="MGM Grand", source_url="https://www.cirquedusoleil.com/", ticket_url="https://www.cirquedusoleil.com/", is_family_friendly=True),
        ]


COLLECTORS: List[BaseCollector] = [DemoSportsCollector(), DemoLiveEventsCollector()]


class ImportEngine:
    def __init__(self, database: InMemoryDatabase) -> None:
        self.database = database

    def run_all_collectors(self) -> Dict[str, int]:
        total_inserted = 0
        total_updated = 0
        total_errors = 0

        for collector in COLLECTORS:
            log = ImportLog(source_name=collector.source_name)
            try:
                for event in collector.fetch_events():
                    result = self.database.upsert_event(event)
                    if result == "inserted":
                        total_inserted += 1
                        log.imported_count += 1
                    elif result == "updated":
                        total_updated += 1
                        log.updated_count += 1
                    else:
                        log.skipped_count += 1
                log.finished_at = datetime.now(UTC).isoformat()
            except Exception as exc:
                total_errors += 1
                log.error_message = str(exc)
                log.finished_at = datetime.now(UTC).isoformat()
                logger.exception("Collector failed: %s", collector.source_name)
            finally:
                self.database.add_import_log(log)

        return {"inserted": total_inserted, "updated": total_updated, "errors": total_errors}


DEFAULT_SOURCES = [
    ("Ticketmaster", "concert", "Global", "https://www.ticketmaster.com/"),
    ("Live Nation", "concert", "Global", "https://www.livenation.com/"),
    ("Broadway", "theatre", "USA", "https://www.broadway.com/"),
    ("West End London", "theatre", "United Kingdom", "https://officiallondontheatre.com/"),
    ("Serie A", "sport", "Italy", "https://www.legaseriea.it/en"),
    ("La Liga", "sport", "Spain", "https://www.laliga.com/en-GB"),
    ("J.League", "sport", "Japan", "https://www.jleague.co/"),
    ("AFA Argentina", "sport", "Argentina", "https://www.afa.com.ar/"),
    ("CBF Brazil", "sport", "Brazil", "https://www.cbf.com.br/"),
    ("NBA", "sport", "USA/Canada", "https://www.nba.com/tickets"),
    ("NHL", "sport", "USA/Canada", "https://www.nhl.com/tickets"),
    ("MLB", "sport", "USA/Canada", "https://www.mlb.com/tickets"),
    ("NFL", "sport", "USA", "https://www.nfl.com/tickets/"),
    ("UFC", "sport", "Global", "https://www.ufc.com/events"),
    ("WWE", "sport", "Global", "https://www.wwe.com/events"),
    ("Formula 1", "motorsport", "Global", "https://tickets.formula1.com/"),
    ("MotoGP", "motorsport", "Global", "https://tickets.motogp.com/"),
    ("ATP Tour", "sport", "Global", "https://www.atptour.com/en/tournaments"),
    ("WTA Tennis", "sport", "Global", "https://www.wtatennis.com/tournaments"),
    ("FEI Equestrian", "equestrian", "Global", "https://www.fei.org/events"),
    ("Ascot Racecourse", "horse_racing", "United Kingdom", "https://www.ascot.com/"),
    ("World Athletics", "sport", "Global", "https://worldathletics.org/competitions"),
]


def seed_sources(database: InMemoryDatabase) -> None:
    for name, category, region, base_url in DEFAULT_SOURCES:
        database.add_source_once(EventSource(name, category, region, base_url))


scheduler_thread: Optional[threading.Thread] = None
scheduler_running = False


def scheduled_import_job() -> None:
    logger.info("Scheduled import started")
    result = ImportEngine(DB).run_all_collectors()
    logger.info("Scheduled import completed: %s", result)


def scheduler_loop() -> None:
    while scheduler_running:
        scheduled_import_job()
        sleep_time.sleep(SCAN_INTERVAL_SECONDS)


def start_scheduler() -> None:
    global scheduler_thread, scheduler_running
    if scheduler_thread and scheduler_thread.is_alive():
        return
    scheduler_running = True
    scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True, name="event-import-scheduler")
    scheduler_thread.start()


class JsonApiHandler(BaseHTTPRequestHandler):
    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self._send_json({"status": "ok"})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path == "/health":
            self._send_json({"status": "ok", "service": "weloveit-events-api"})
            return

        if parsed.path == "/events":
            events = DB.search_events(
                destination=get_first(query, "destination"),
                from_date=get_first(query, "from_date"),
                to_date=get_first(query, "to_date"),
                category=get_first(query, "category"),
                q=get_first(query, "q"),
                limit=int(get_first(query, "limit", "100")),
            )
            self._send_json([event_to_dict(event) for event in events])
            return

        if parsed.path == "/travel-search":
            destination = get_first(query, "destination")
            from_date = get_first(query, "from_date")
            to_date = get_first(query, "to_date")
            if not destination or not from_date or not to_date:
                self._send_json({"error": "Missing required query parameters", "required": ["destination", "from_date", "to_date"]}, status=400)
                return

            events = DB.search_events(
                destination=destination,
                from_date=from_date,
                to_date=to_date,
                category=get_first(query, "category"),
                limit=int(get_first(query, "limit", "100")),
            )
            self._send_json({
                "destination": destination,
                "from_date": from_date,
                "to_date": to_date,
                "total_events": len(events),
                "events": [event_to_dict(event) for event in events],
            })
            return

        if parsed.path == "/sources":
            self._send_json([asdict(source) for source in DB.sources])
            return

        if parsed.path == "/import-logs":
            self._send_json([asdict(log) for log in DB.import_logs])
            return

        self._send_json({"error": "Not found", "path": parsed.path}, status=404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/import":
            result = ImportEngine(DB).run_all_collectors()
            self._send_json({"status": "completed", **result})
            return
        self._send_json({"error": "Not found", "path": parsed.path}, status=404)

    def log_message(self, format: str, *args: Any) -> None:
        logger.info("HTTP %s", format % args)


def get_first(query: Dict[str, List[str]], key: str, default: Optional[str] = None) -> Optional[str]:
    values = query.get(key)
    if not values:
        return default
    return values[0]


_initialized = False


def initialize_app(import_demo_events: bool = True, start_background_scheduler: bool = True) -> None:
    global _initialized
    if _initialized:
        return
    seed_sources(DB)
    if import_demo_events:
        ImportEngine(DB).run_all_collectors()
    if start_background_scheduler:
        start_scheduler()
    _initialized = True


def run_demo() -> None:
    initialize_app(import_demo_events=True, start_background_scheduler=False)
    results = DB.search_events(destination="Tokyo", from_date="2026-07-01", to_date="2026-07-10")
    print(json.dumps({
        "mode": "local_demo",
        "message": "Backend logic executed successfully.",
        "search": {
            "destination": "Tokyo",
            "from_date": "2026-07-01",
            "to_date": "2026-07-10",
            "total_events": len(results),
            "events": [event_to_dict(event) for event in results],
        },
    }, ensure_ascii=False, indent=2))


def run_server() -> None:
    initialize_app(import_demo_events=True, start_background_scheduler=True)
    server = ThreadingHTTPServer((HOST, PORT), JsonApiHandler)
    logger.info("WELOVEIT Events API running on 0.0.0.0:%s", PORT)
    server.serve_forever()


class BackendTests(unittest.TestCase):
    def setUp(self) -> None:
        DB.reset()
        global _initialized
        _initialized = False
        initialize_app(import_demo_events=False, start_background_scheduler=False)

    def test_import_collectors_insert_events(self) -> None:
        result = ImportEngine(DB).run_all_collectors()
        self.assertEqual(result["errors"], 0)
        self.assertGreaterEqual(result["inserted"], 9)
        self.assertEqual(len(DB.events), result["inserted"])

    def test_second_import_updates_instead_of_duplicating(self) -> None:
        first = ImportEngine(DB).run_all_collectors()
        initial_count = len(DB.events)
        second = ImportEngine(DB).run_all_collectors()
        self.assertGreater(first["inserted"], 0)
        self.assertEqual(second["inserted"], 0)
        self.assertGreater(second["updated"], 0)
        self.assertEqual(len(DB.events), initial_count)

    def test_travel_search_tokyo_dates(self) -> None:
        ImportEngine(DB).run_all_collectors()
        results = DB.search_events(destination="Tokyo", from_date="2026-07-01", to_date="2026-07-10")
        titles = {event.title for event in results}
        self.assertIn("Japan vs Italy Rugby", titles)
        self.assertIn("International Pop Concert", titles)
        self.assertIn("FC Tokyo vs Yokohama F. Marinos", titles)

    def test_category_filter(self) -> None:
        ImportEngine(DB).run_all_collectors()
        results = DB.search_events(category=EventCategory.CONCERT.value)
        self.assertTrue(results)
        self.assertTrue(all(event.category == EventCategory.CONCERT.value for event in results))


def run_tests() -> None:
    unittest.main(argv=["ignored"], exit=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WELOVEIT Events backend prototype")
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()

    if args.test:
        run_tests()
    elif args.serve:
        run_server()
    else:
        run_demo()
