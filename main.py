from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TICKETMASTER_API_KEY = os.getenv("TICKETMASTER_API_KEY")

@app.get("/")
def home():
    return {
        "status": "online",
        "service": "WELOVEIT Events API"
    }

@app.get("/events")
def get_events(
    city: str = "Tokyo",
    keyword: str = "",
    size: int = 12
):
    url = "https://app.ticketmaster.com/discovery/v2/events.json"

    params = {
        "apikey": TICKETMASTER_API_KEY,
        "city": city,
        "size": size,
        "keyword": keyword,
        "sort": "date,asc"
    }

    response = requests.get(url, params=params)

    data = response.json()

    events = []

    embedded = data.get("_embedded", {})
    raw_events = embedded.get("events", [])

    for e in raw_events:
        events.append({
            "title": e.get("name"),
            "date": e.get("dates", {})
                     .get("start", {})
                     .get("localDate"),
            "time": e.get("dates", {})
                     .get("start", {})
                     .get("localTime"),
            "url": e.get("url"),
            "image": e.get("images", [{}])[0].get("url"),
            "venue": e.get("_embedded", {})
                      .get("venues", [{}])[0]
                      .get("name"),
            "city": city
        })

    return {
        "success": True,
        "count": len(events),
        "events": events
    }
