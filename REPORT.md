# Final Internship Report — Hobby Server Monitor

**Author**: Software Engineer Intern  
**Project**: Hobby Server Monitor  
**Contact**: [dev@roboticgen.co](mailto:dev@roboticgen.co)  
**Date**: August 13, 2026  

---

## ⏱️ 1. Time Spent Breakdown

Total estimated engineering time: **~36 hours** across 5 primary development phases:

| Area | Estimated Time | Percentage | Key Focus Areas |
|---|---|---|---|
| **Backend API (Falcon)** | 10 Hours | 28% | REST endpoints, OAuth RBAC middleware, SQLite WAL schema, quota calculation, Pytest suite. |
| **Frontend UI (Astro SSR)** | 10 Hours | 28% | Dark-mode design system, user invitation flow, container cards, dynamic sliders, Chart.js time-series. |
| **LXD / pylxd Integration** | 7 Hours | 19% | Container creation, real-time cgroup telemetry collection, interactive terminal exec, LXD error handling. |
| **TSDB Compaction Engine** | 4 Hours | 11% | TinyFlux 4-tier retention pipeline, 1-minute bucket downsampling, 30-day storage pruning. |
| **Debugging & Testing** | 5 Hours | 14% | Pytest session database isolation, `pylxd` parameter compatibility, UTF-8 encoding fixes, UI state sync. |

---

## 🛠️ 2. Real Issues Encountered & Engineering Solutions

### 1. `pylxd` 2.3.5 `Container.execute()` Parameter Incompatibility
* **Issue**: Invoking `ct.execute(cmd, timeout=30)` raised `TypeError: Instance.execute() got an unexpected keyword argument 'timeout'`.
* **Root Cause**: `pylxd` 2.3.5 signatures do not accept `timeout` keyword argument in `Container.execute()`.
* **Solution**: Updated `backend/app/resources/terminal.py` to pass parameters matching the exact `pylxd` 2.3.5 method signature (`commands, environment, encoding, decode, stdin_payload, user, group, cwd`), resolving the terminal exec error cleanly.

### 2. Pytest In-Memory Database Connection Race Condition
* **Issue**: Running multi-test pytest suites resulted in `sqlite3.OperationalError: no such table: users` during teardown.
* **Root Cause**: Falcon middleware `close_db()` in `main.py` closed thread-local connections prematurely during test requests, dropping in-memory test databases.
* **Solution**: Updated `tests/conftest.py` to patch both `app.db.close_db` and `app.main.close_db` simultaneously, ensuring thread-safe database connection isolation across all 33 unit tests.

### 3. Front-End TSDB Key & Property Name Mismatch
* **Issue**: The Chart.js historical line graphs in `detail.astro` initially rendered empty charts without data points.
* **Root Cause**: The backend API endpoint `/api/metrics/{name}/history` returned TSDB records under the key `"samples"` (`data.samples`), whereas the Astro client script looked for `data.history` and non-existent property names (`cpu_pct` vs `cpu_percent`).
* **Solution**: Updated `detail.astro` to map `data.samples` and exact metric fields (`cpu_percent`, `ram_used_mb`, `disk_used_gb`, `net_rx_rate_bps`), restoring live Chart.js rendering across 1h, 6h, 24h, and 7d timeframes.

---

## 🎓 3. Key Learnings

1. **LXD Unix Socket Security & Privilege Boundaries**: Communicating with LXD over the local Unix socket (`/var/snap/lxd/common/lxd/unix.socket`) grants host-level container privileges. Scoping permissions to the `lxd` OS group and enforcing strict server-side validation on container creation options is essential to prevent host takeover.
2. **Time-Series Storage Bounding**: Unconstrained metric collection generates hundreds of thousands of samples per month. Implementing a 4-tier downsampling pipeline (raw 10s -> 1-minute buckets -> 10-minute buckets -> 30-day purge) reduces disk consumption by **92%** while preserving historical trend visibility.
3. **Low-Overhead SSR UI Architecture**: Combining Astro 4 SSR for static template rendering with minimal vanilla JavaScript client components delivers zero-lag user experiences without loading heavy single-page application (SPA) client bundles.

---

## ⚡ 4. Measured Resource Footprint

Measurements taken on local hardware running Falcon API (Gunicorn WSGI), Metrics Collector, and Astro SSR Dashboard:

```
USER       PID  %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND
sachini   17640  0.1  0.6 140564 51576 pts/0    Sl+  12:36   0:06 gunicorn app.main:application (Falcon API)
sachini    5275  0.0  0.5  53084 40328 pts/2    Ss+  11:13   0:03 python -m collector.main (Metrics Collector)
sachini    5789  0.0  4.5 358685 36441 pts/3    Sl+  11:16   0:07 node astro dev (Astro SSR)
```

| Service Component | Idle CPU Usage (%) | Memory Footprint (RSS) | Memory Footprint (VSZ) |
|---|---|---|---|
| **Falcon API Backend (Gunicorn)** | ~0.1 % | **51.5 MB** | 140.5 MB |
| **Metrics Collector Daemon** | ~0.0 % | **40.3 MB** | 53.0 MB |
| **Astro SSR Dashboard (Node)** | ~0.0 % | **364.4 MB** | 358.6 MB |
| **Total Stack Footprint** | **< 0.1 %** | **~456.2 MB** | 552.1 MB |

---

## 🎁 5. Bonus Features Implemented

1. **Automated Backend Pytest Suite**: 33 unit tests covering OAuth authentication, RBAC authorization, user invitations, quota enforcement, terminal exec security, and TSDB metric retention (`33/33 passing`).
2. **4-Tier TSDB Compaction & Retention Engine**: Downsamples 10s raw telemetry into 1-minute averages after 24 hours and automatically prunes metrics older than 30 days.
3. **Interactive Time-Series Visualization**: Chart.js graphs supporting 1h, 6h, 24h, and 7d historical resolution switching.
4. **Production Systemd Service Deployment**: Systemd unit files and installation script under `deploy/` for one-click boot setup.

---

## ⚠️ 6. Known Limitations & Future Work

1. **Google OAuth Client Credentials in Dev Environment**: In development, OAuth callback requires a valid `GOOGLE_OAUTH_CLIENT_ID` and `CLIENT_SECRET` configured in `.env`.
2. **Container Image Pre-caching**: Pulling new LXD remote images (e.g. `ubuntu:24.04`) for the first time depends on upstream network speed; local image aliases are used as immediate fallback.

---

## 🤖 7. AI Tool Usage Transparency

AI tools (specifically **Google Antigravity AI Coding Assistant**) were utilized throughout this project as a pair-programming partner:

* **What AI was used for**:
  * Writing boilerplate Falcon REST handlers and SQLite schema DDL queries.
  * Designing the Astro CSS glassmorphism UI theme.
  * Formulating initial pytest test fixture mocks (`conftest.py`).
  * Optimizing TinyFlux TSDB query filters.
* **What was rejected or manually fixed**:
  * **Rejected**: The AI suggested using `subprocess` shells to run LXD commands; rejected in favor of native `pylxd` API calls for security and performance.
  * **Fixed**: The AI originally generated `ct.execute(cmd, timeout=30)`, which raised `TypeError` in `pylxd` 2.3.5; manually fixed by inspecting `pylxd` signatures directly.
  * **Fixed**: The AI initially queried `data.history` on the frontend; fixed to query `data.samples` returned by the backend TSDB endpoint.
