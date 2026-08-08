"""
backend/app/tsdb.py

TinyFlux Time-Series Database Wrapper for container metrics.

Storage layout:
    Measurement: "container_metrics"
    Tags:        {"container_name": <str>, "state": <str>}
    Fields:      {
                   "cpu_percent": float,
                   "ram_used_mb": float,
                   "ram_alloc_mb": float,
                   "disk_used_gb": float,
                   "disk_alloc_gb": float,
                   "net_rx_bytes": int,
                   "net_tx_bytes": int,
                   "net_rx_rate_bps": float,
                   "net_tx_rate_bps": float,
                   "process_count": int,
                 }
Note: TinyFlux field values must be numeric (int/float or None).
String tags like container_name and state are stored in `tags`.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tinyflux import TinyFlux, Point, TagQuery, TimeQuery
from app.config import settings

log = logging.getLogger(__name__)

_tf_instance: TinyFlux | None = None


def get_tsdb() -> TinyFlux:
    """
    Return the module-level TinyFlux database instance.
    Opens/creates the database file at settings.tinyflux_db_path.
    """
    global _tf_instance
    if _tf_instance is None:
        db_path: Path = settings.tinyflux_db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _tf_instance = TinyFlux(str(db_path))
        log.info("Opened TinyFlux TSDB at %s", db_path)
    return _tf_instance


def write_metric_point(
    container_name: str,
    fields: dict[str, Any],
    state: str = "Unknown",
    timestamp: datetime | None = None
) -> Point:
    """
    Write a single metric sample Point for container_name into TinyFlux.
    `state` string ("Running", "Stopped", etc.) is saved as a tag since TinyFlux fields must be numeric.
    """
    ts = timestamp or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    # Separate numeric fields from string values if any slipped into fields dict
    numeric_fields = {}
    extracted_state = state
    for k, v in fields.items():
        if k == "state":
            extracted_state = str(v)
        elif isinstance(v, (int, float)) or v is None:
            numeric_fields[k] = v

    db = get_tsdb()
    point = Point(
        time=ts,
        measurement="container_metrics",
        tags={
            "container_name": container_name,
            "state": extracted_state,
        },
        fields=numeric_fields,
    )
    db.insert(point)
    return point


def get_latest_metric(container_name: str) -> dict[str, Any] | None:
    """
    Return the most recent metric point dict for container_name, or None if no data exists.
    """
    db = get_tsdb()
    Tag = TagQuery()

    points = db.search(Tag.container_name == container_name)
    if not points:
        return None

    # Get latest by timestamp
    latest_point = max(points, key=lambda p: p.time)

    res = {
        "container_name": container_name,
        "state": latest_point.tags.get("state", "Unknown"),
        "timestamp": latest_point.time.isoformat(),
    }
    res.update(latest_point.fields)
    return res


def get_metric_history(container_name: str, since_dt: datetime | None = None) -> list[dict[str, Any]]:
    """
    Return all metric samples for container_name recorded since since_dt (or all if since_dt is None).
    """
    db = get_tsdb()
    Tag = TagQuery()

    if since_dt is not None:
        if since_dt.tzinfo is None:
            since_dt = since_dt.replace(tzinfo=timezone.utc)
        Time = TimeQuery()
        query = (Tag.container_name == container_name) & (Time >= since_dt)
    else:
        query = Tag.container_name == container_name

    points = db.search(query)
    points.sort(key=lambda p: p.time)

    history = []
    for p in points:
        sample = {
            "container_name": container_name,
            "state": p.tags.get("state", "Unknown"),
            "timestamp": p.time.isoformat(),
        }
        sample.update(p.fields)
        history.append(sample)

    return history
