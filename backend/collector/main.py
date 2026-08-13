"""
backend/collector/main.py

Standalone LXD Metrics Collector Process.
Runs independently of Falcon API server (its own systemd unit / terminal process).

Every POLL_INTERVAL seconds:
1. Queries LXD for all container states using lxd_client.lxd_safe()
2. Computes CPU %, RAM used, Disk used, Net Rx/Tx rates (bps), and process count
3. Writes Point samples to TinyFlux TSDB (tsdb.py)
4. Executes hourly metric compaction & 30-day retention pruning (compaction.py)

Gracefully handles stopped containers and LXD daemon restarts/unavailability.
"""

import sys
import time
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

# Ensure backend root is on sys.path when executed directly
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.config import settings
from app.lxd_client import get_client, lxd_safe
from app.tsdb import write_metric_point

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (collector) %(message)s"
)
log = logging.getLogger("collector")

# Cache previous network counters & timestamps for rate calculation (bps)
_prev_net_cache: dict[str, dict] = {}


def extract_container_metrics(container) -> tuple[str, dict]:
    """
    Extract metric fields safely from a pylxd Container object.
    None-guards every field to handle stopped/frozen/unreachable states without crashing.
    Returns: (state_string, numeric_fields_dict)
    """
    state_obj, state_err = lxd_safe(lambda: container.state())

    if state_err or not state_obj:
        return "Unknown", {
            "cpu_percent": 0.0,
            "ram_used_mb": 0.0,
            "ram_alloc_mb": 512.0,
            "disk_used_gb": 0.0,
            "disk_alloc_gb": 5.0,
            "net_rx_bytes": 0,
            "net_tx_bytes": 0,
            "net_rx_rate_bps": 0.0,
            "net_tx_rate_bps": 0.0,
            "process_count": 0,
        }

    status_str = getattr(state_obj, "status", "Unknown")

    # 1. Memory usage
    ram_used_bytes = 0
    if hasattr(state_obj, "memory") and state_obj.memory:
        ram_used_bytes = state_obj.memory.get("usage", 0) or 0
    ram_used_mb = round(ram_used_bytes / (1024 * 1024), 2)

    # Configured limits
    config = getattr(container, "config", {}) or {}
    ram_alloc_raw = config.get("limits.memory", "512MB")
    ram_alloc_mb = 512.0
    if ram_alloc_raw.endswith("MB"):
        try: ram_alloc_mb = float(ram_alloc_raw.replace("MB", ""))
        except ValueError: pass
    elif ram_alloc_raw.endswith("GB"):
        try: ram_alloc_mb = float(ram_alloc_raw.replace("GB", "")) * 1024
        except ValueError: pass

    # 2. CPU Usage
    cpu_percent = 0.0
    if hasattr(state_obj, "cpu") and state_obj.cpu:
        cpu_nsec = state_obj.cpu.get("usage", 0) or 0
        cpu_percent = round(min(100.0, (cpu_nsec / 1e9) % 100), 2) if cpu_nsec else 0.0

    # 3. Disk usage
    disk_used_bytes = 0
    if hasattr(state_obj, "disk") and state_obj.disk:
        root_disk = state_obj.disk.get("root", {}) or {}
        disk_used_bytes = root_disk.get("usage", 0) or 0
    disk_used_gb = round(disk_used_bytes / (1024 * 1024 * 1024), 2)
    disk_alloc_gb = 5.0

    # 4. Processes
    process_count = getattr(state_obj, "processes", 0) or 0

    # 5. Network Rx/Tx Counters & Rates
    net_rx_bytes = 0
    net_tx_bytes = 0
    if hasattr(state_obj, "network") and state_obj.network:
        for iface, if_data in state_obj.network.items():
            if iface == "lo":
                continue
            counters = if_data.get("counters", {}) or {}
            net_rx_bytes += counters.get("bytes_received", 0) or 0
            net_tx_bytes += counters.get("bytes_sent", 0) or 0

    # Calculate Network Rates (bps) using cache
    now = datetime.now(timezone.utc)
    net_rx_rate_bps = 0.0
    net_tx_rate_bps = 0.0

    name = container.name
    if name in _prev_net_cache:
        prev = _prev_net_cache[name]
        dt_seconds = (now - prev["time"]).total_seconds()
        if dt_seconds > 0:
            rx_delta = max(0, net_rx_bytes - prev["rx_bytes"])
            tx_delta = max(0, net_tx_bytes - prev["tx_bytes"])
            net_rx_rate_bps = round((rx_delta * 8) / dt_seconds, 2)
            net_tx_rate_bps = round((tx_delta * 8) / dt_seconds, 2)

    # Update cache
    _prev_net_cache[name] = {
        "time": now,
        "rx_bytes": net_rx_bytes,
        "tx_bytes": net_tx_bytes,
    }

    numeric_metrics = {
        "cpu_percent": cpu_percent,
        "ram_used_mb": ram_used_mb,
        "ram_alloc_mb": ram_alloc_mb,
        "disk_used_gb": disk_used_gb,
        "disk_alloc_gb": disk_alloc_gb,
        "net_rx_bytes": net_rx_bytes,
        "net_tx_bytes": net_tx_bytes,
        "net_rx_rate_bps": net_rx_rate_bps,
        "net_tx_rate_bps": net_tx_rate_bps,
        "process_count": process_count,
    }

    return status_str, numeric_metrics


def collect_cycle() -> int:
    """
    Perform one polling iteration across all LXD containers.
    Returns number of points written to TSDB.
    """
    client = get_client()
    containers, err = lxd_safe(lambda: client.containers.all())

    if err:
        log.warning("LXD connection unavailable during collection cycle: %s", err)
        return 0

    points_written = 0
    now = datetime.now(timezone.utc)

    for ct in containers:
        status_str, metrics = extract_container_metrics(ct)
        write_metric_point(ct.name, metrics, state=status_str, timestamp=now)
        points_written += 1

    return points_written


def run_collector_loop(stop_event: threading.Event | None = None) -> None:
    """Main collector polling loop."""
    interval = settings.COLLECTOR_POLL_INTERVAL_SECONDS
    log.info("Starting LXD Metrics Collector (Polling every %d seconds)...", interval)

    stop = stop_event or threading.Event()
    cycle_counter = 0

    while not stop.is_set():
        try:
            count = collect_cycle()
            log.debug("Collection cycle completed: %d points written.", count)

            # Run TSDB compaction once every 360 cycles (~1 hour)
            cycle_counter += 1
            if cycle_counter % 360 == 0:
                try:
                    from app.compaction import compact_metrics
                    compact_summary = compact_metrics()
                    log.info("Hourly TSDB compaction summary: %s", compact_summary)
                except Exception as comp_err:
                    log.error("Error during TSDB metric compaction: %s", comp_err)
        except Exception as exc:
            log.error("Unexpected error in collector cycle: %s", exc, exc_info=True)

        stop.wait(interval)

    log.info("Metrics Collector shut down gracefully.")


if __name__ == "__main__":
    run_collector_loop()
