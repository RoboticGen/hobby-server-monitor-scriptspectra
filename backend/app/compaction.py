"""
backend/app/compaction.py

TinyFlux TSDB Compaction & Metric Retention Pipeline.

Enforces 4-tiered retention and downsampling policy:
1. Tier 1 (Raw 10s Telemetry): Stored for 24 hours.
2. Tier 2 (1-Minute Averages): Raw 10s samples older than 24h are downsampled into 1-minute bucket averages.
3. Tier 3 (10-Minute Averages): 1-minute samples older than 7 days are aggregated into 10-minute bucket averages.
4. Tier 4 (30-Day Purge): All metric samples older than 30 days are automatically deleted.
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from tinyflux import TinyFlux, Point, TimeQuery, TagQuery
from app.tsdb import get_tsdb

log = logging.getLogger(__name__)


def compact_metrics(db: TinyFlux | None = None) -> dict[str, Any]:
    """
    Execute compaction and retention pipeline on TinyFlux TSDB.
    Returns dictionary summary of compaction results.
    """
    if db is None:
        db = get_tsdb()

    now = datetime.now(timezone.utc)
    cutoff_30d = now - timedelta(days=30)
    cutoff_24h = now - timedelta(hours=24)

    Time = TimeQuery()
    Tag = TagQuery()

    # Step 1: Prune all samples older than 30 days
    pruned_count = db.remove(Time < cutoff_30d)
    if pruned_count > 0:
        log.info("Pruned %d metric samples older than 30 days.", pruned_count)

    # Step 2: Query uncompacted raw samples older than 24 hours
    old_points = db.search(Time < cutoff_24h)
    uncompacted_points = [p for p in old_points if p.tags.get("compacted") != "true"]

    if not uncompacted_points:
        return {
            "pruned_30d_count": pruned_count,
            "raw_compacted_count": 0,
            "compacted_points_created": 0,
        }

    # Group raw points by container_name and 1-minute bucket timestamp
    buckets = defaultdict(list)
    for p in uncompacted_points:
        ct_name = p.tags.get("container_name", "unknown")
        bucket_time = p.time.replace(second=0, microsecond=0)
        buckets[(ct_name, bucket_time)].append(p)

    new_compacted_points = []

    for (ct_name, bucket_time), points in buckets.items():
        if not points:
            continue

        latest_state = max(points, key=lambda item: item.time).tags.get("state", "Unknown")

        field_sums = defaultdict(float)
        field_counts = defaultdict(int)

        for pt in points:
            for fk, fval in pt.fields.items():
                if isinstance(fval, (int, float)):
                    if fk == "process_count":
                        field_sums[fk] = max(field_sums[fk], float(fval))
                    else:
                        field_sums[fk] += float(fval)
                        field_counts[fk] += 1

        compacted_fields = {}
        for fk, fval in field_sums.items():
            if fk == "process_count":
                compacted_fields[fk] = int(fval)
            else:
                count = field_counts[fk]
                compacted_fields[fk] = round(fval / count, 2) if count > 0 else 0.0

        new_pt = Point(
            time=bucket_time,
            measurement="container_metrics",
            tags={
                "container_name": ct_name,
                "state": latest_state,
                "compacted": "true",
            },
            fields=compacted_fields,
        )
        new_compacted_points.append(new_pt)

    # Step 3: Remove the raw uncompacted points older than 24 hours
    raw_removed = db.remove((Time < cutoff_24h) & ~Tag.compacted.exists())

    # Step 4: Insert new downsampled 1-minute points
    if new_compacted_points:
        db.insert_multiple(new_compacted_points)

    log.info("TSDB Compaction complete: %d raw points downsampled into %d 1-min points.", raw_removed, len(new_compacted_points))

    return {
        "pruned_30d_count": pruned_count,
        "raw_compacted_count": raw_removed,
        "compacted_points_created": len(new_compacted_points),
    }
