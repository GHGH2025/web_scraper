"""MongoDB connection for Florida Off Market listings.

One shared MongoClient for the long-running scheduler. The daily job is
single-threaded and sequential, so driver pool defaults are used.

Collections:
  raw       — every extracted scrape (upsert by listing_id)
  filtered  — queue for the Python listing server (pending → posted | rejected)
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from typing import Any

from dotenv import load_dotenv
from pymongo import ASCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import ConfigurationError

from scrape_fom import ROOT, log

load_dotenv(ROOT / ".env")

MONGO_URI = (os.getenv("MONGO_URI") or "").strip()
RAW_COLLECTION = "raw"
FILTERED_COLLECTION = "filtered"
LOOKBACK_DAYS = 30
SOURCE = "florida_off_market"

_ADDR_TAIL_RE = re.compile(
    r"^(?P<street>.+?),\s*(?P<city>.+?),\s*(?P<state>[A-Za-z]{2})"
    r"(?:,?\s+(?P<zip>\d{5}(?:-\d{4})?))?\s*$"
)

_client: MongoClient | None = None


def get_client() -> MongoClient:
    global _client
    if not MONGO_URI:
        raise RuntimeError("MONGO_URI must be set in scraper/.env and include a database name")
    if _client is None:
        # Daily sequential job: fail fast if Mongo is down instead of waiting 30s.
        _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
    return _client


def get_db() -> Database:
    try:
        return get_client().get_default_database()
    except ConfigurationError as exc:
        raise RuntimeError(
            "MONGO_URI must include a database name, e.g. mongodb://127.0.0.1:27017/yourdb"
        ) from exc


def get_raw_collection() -> Collection:
    return get_db()[RAW_COLLECTION]


def get_filtered_collection() -> Collection:
    return get_db()[FILTERED_COLLECTION]


def close_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None


def ping() -> None:
    get_client().admin.command("ping")
    log.info(
        "MongoDB reachable db=%s raw=%s filtered=%s",
        get_db().name,
        RAW_COLLECTION,
        FILTERED_COLLECTION,
    )


def ensure_indexes() -> None:
    raw = get_raw_collection()
    raw.create_index([("source", ASCENDING), ("listing_id", ASCENDING)], unique=True, name="uniq_source_listing_id")
    raw.create_index([("updated_at", ASCENDING)], name="raw_updated_at_idx")

    filtered = get_filtered_collection()
    filtered.create_index([("address_norm", ASCENDING), ("status", ASCENDING)], name="addr_norm_status_idx")
    filtered.create_index([("status", ASCENDING), ("parsed_listing_id", ASCENDING)], name="status_parsed_idx")
    filtered.create_index([("listing_id", ASCENDING)], name="filtered_listing_id_idx")
    filtered.create_index([("posted_at", ASCENDING)], name="filtered_posted_at_idx")
    log.info(
        "Ensured indexes on %s.%s and %s.%s",
        get_db().name,
        RAW_COLLECTION,
        get_db().name,
        FILTERED_COLLECTION,
    )


def _now() -> datetime:
    return datetime.utcnow()


def address_norm(address: str) -> str:
    street = (address or "").split(",", 1)[0]
    return " ".join(street.lower().split())


def parse_listing_location(listing: dict[str, Any]) -> dict[str, str | None]:
    """Street/city/state/zip from extract title (FOM H1) plus detail fields."""
    title = (listing.get("address") or listing.get("title") or "").strip()
    city = (listing.get("city") or "").strip() or None
    state = (listing.get("state") or "").strip() or None
    zip_ = (listing.get("zip") or "").strip() or None
    address = title or None

    if title:
        match = _ADDR_TAIL_RE.match(title)
        if match:
            address = match.group("street").strip() or title
            city = city or (match.group("city") or "").strip() or None
            state = state or (match.group("state") or "").strip() or None
            zip_ = zip_ or (match.group("zip") or "").strip() or None

    return {
        "address": address,
        "city": city,
        "state": state,
        "zip": zip_,
        "address_norm": address_norm(address) if address else None,
    }


def _listing_doc(listing: dict[str, Any], now: datetime, source: str) -> dict[str, Any]:
    loc = parse_listing_location(listing)
    doc = dict(listing)
    doc.pop("_id", None)
    doc["source"] = source
    doc["source_website"] = source
    doc["source_listing_id"] = f"{source}:{listing.get('listing_id') or ''}"
    doc["address"] = loc["address"]
    doc["city"] = loc["city"]
    doc["state"] = loc["state"]
    doc["zip"] = loc["zip"]
    doc["address_norm"] = loc["address_norm"]
    doc["last_scraped_at"] = now
    return doc


def upsert_raw_listings(listings: list[dict[str, Any]], *, source: str = SOURCE) -> dict[str, int]:
    now = _now()
    col = get_raw_collection()
    inserted = 0
    modified = 0
    skipped = 0

    for listing in listings:
        listing_id = listing.get("listing_id")
        if not listing_id:
            skipped += 1
            continue
        doc = _listing_doc(listing, now, source)
        result = col.update_one(
            {"listing_id": listing_id, "source": source},
            {
                "$set": {**doc, "updated_at": now},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        if result.upserted_id is not None:
            inserted += 1
        elif result.modified_count:
            modified += 1

    stats = {
        "inserted": inserted,
        "modified": modified,
        "skipped": skipped,
        "total": len(listings),
    }
    log.info(
        "Mongo raw %s.%s inserted=%s modified=%s skipped=%s total=%s",
        get_db().name,
        RAW_COLLECTION,
        inserted,
        modified,
        skipped,
        len(listings),
    )
    return stats


def _filtered_doc(listing: dict[str, Any], loc: dict[str, str | None], raw_id, now: datetime, source: str) -> dict[str, Any]:
    snapshot = dict(listing)
    snapshot.pop("_id", None)
    return {
        "listing_id": listing.get("listing_id"),
        "raw_id": str(raw_id) if raw_id is not None else None,
        "source": source,
        "source_website": source,
        "address": loc["address"],
        "city": loc["city"],
        "state": loc["state"],
        "zip": loc["zip"],
        "county": listing.get("county"),
        "address_norm": loc["address_norm"],
        "title": listing.get("title"),
        "url": listing.get("url"),
        "price": listing.get("price"),
        "price_usd": listing.get("price_usd"),
        "listing": snapshot,
        "status": "pending",
        "parsed_listing_id": None,
        "posted_at": None,
        "reject_reason": None,
        "created_at": now,
        "updated_at": now,
    }


def _recent_address_exists(address: str, address_norm_value: str, since: datetime) -> bool:
    """Check queue and parsed records from every input source for 30-day history."""
    db = get_db()
    if db[FILTERED_COLLECTION].find_one({"address_norm": address_norm_value, "created_at": {"$gte": since}}):
        return True
    exact = re.compile(rf"^{re.escape(address)}$", re.I)
    return bool(
        db["parsed_listings"].find_one(
            {
                "created_at": {"$gte": since},
                "$or": [{"address": exact}, {"complete_info.address": exact}],
            },
            {"_id": 1},
        )
    )


def promote_to_filtered(listings: list[dict[str, Any]], *, source: str = SOURCE) -> dict[str, int]:
    """Queue new web deals only when no same address was created in the last 30 days."""
    now = _now()
    since = now - timedelta(days=LOOKBACK_DAYS)
    raw = get_raw_collection()
    filtered = get_filtered_collection()
    inserted = 0
    skipped_recent_address = 0
    skipped_no_address = 0
    skipped_no_id = 0

    for listing in listings:
        listing_id = listing.get("listing_id")
        if not listing_id:
            skipped_no_id += 1
            continue

        loc = parse_listing_location(listing)
        key = loc["address_norm"]
        if not key:
            skipped_no_address += 1
            continue

        if _recent_address_exists(loc["address"] or "", key, since):
            skipped_recent_address += 1
            continue

        raw_doc = raw.find_one({"listing_id": listing_id, "source": source}, {"_id": 1})
        raw_id = raw_doc["_id"] if raw_doc else None
        filtered.insert_one(_filtered_doc(listing, loc, raw_id, now, source))
        inserted += 1

    stats = {
        "inserted": inserted,
        "skipped_recent_address": skipped_recent_address,
        "skipped_no_address": skipped_no_address,
        "skipped_no_id": skipped_no_id,
        "total": len(listings),
        "lookback_days": LOOKBACK_DAYS,
    }
    log.info(
        "Mongo filtered %s.%s inserted=%s skipped_recent_address=%s "
        "skipped_no_address=%s total=%s",
        get_db().name,
        FILTERED_COLLECTION,
        inserted,
        skipped_recent_address,
        skipped_no_address,
        len(listings),
    )
    return stats
