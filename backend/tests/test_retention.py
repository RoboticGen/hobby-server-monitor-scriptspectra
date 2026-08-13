"""
backend/tests/test_retention.py

Unit tests for Phase 9 — Metric Retention, Compaction & Bounded Storage.

Tests cover:
  - Downsampling raw 10s samples (>24h old) into 1-minute bucket averages
  - Automatic pruning of samples >30 days old
  - Correct tag & field calculations during compaction
"""

import tempfile
from datetime import datetime, timedelta, timezone
import pytest
from tinyflux import TinyFlux, Point, TimeQuery, TagQuery

from app.compaction import compact_metrics
from app.tsdb import write_metric_point


@pytest.fixture
def temp_tsdb():
    """Create a temporary TinyFlux database instance for isolated testing."""
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        tmp_path = tmp.name
    db = TinyFlux(tmp_path)
    yield db


def test_compact_raw_samples_to_1min_buckets(temp_tsdb):
    """Raw 10s samples older than 24h should be downsampled into 1-minute bucket averages."""
    now = datetime.now(timezone.utc)
    base_time = (now - timedelta(hours=25)).replace(second=0, microsecond=0)

    # Insert 6 raw samples across 1 minute (every 10 seconds)
    for i in range(6):
        sample_time = base_time + timedelta(seconds=i * 10)
        point = Point(
            time=sample_time,
            measurement="container_metrics",
            tags={"container_name": "retention-box-01", "state": "Running"},
            fields={
                "cpu_percent": 10.0 + i,
                "ram_used_mb": 100.0 + (i * 10),
                "ram_alloc_mb": 512.0,
                "disk_used_gb": 1.0,
                "disk_alloc_gb": 5.0,
                "net_rx_rate_bps": 1000.0,
                "net_tx_rate_bps": 500.0,
                "process_count": 10 + i,
            }
        )
        temp_tsdb.insert(point)

    assert len(temp_tsdb.all()) == 6

    # Execute compaction
    summary = compact_metrics(temp_tsdb)

    assert summary["raw_compacted_count"] == 6
    assert summary["compacted_points_created"] == 1

    # Verify only 1 downsampled point remains
    all_points = temp_tsdb.all()
    assert len(all_points) == 1

    compacted_pt = all_points[0]
    assert compacted_pt.tags["container_name"] == "retention-box-01"
    assert compacted_pt.tags["compacted"] == "true"
    # Average cpu_percent = sum(10..15)/6 = 12.5
    assert compacted_pt.fields["cpu_percent"] == 12.5
    # Max process count = 15
    assert compacted_pt.fields["process_count"] == 15


def test_prune_30d_old_samples(temp_tsdb):
    """Metric samples older than 30 days must be pruned completely."""
    now = datetime.now(timezone.utc)
    old_time = now - timedelta(days=35)

    point = Point(
        time=old_time,
        measurement="container_metrics",
        tags={"container_name": "expired-box", "state": "Stopped"},
        fields={"cpu_percent": 0.0, "ram_used_mb": 0.0}
    )
    temp_tsdb.insert(point)

    assert len(temp_tsdb.all()) == 1

    # Execute compaction
    summary = compact_metrics(temp_tsdb)

    assert summary["pruned_30d_count"] == 1
    assert len(temp_tsdb.all()) == 0

