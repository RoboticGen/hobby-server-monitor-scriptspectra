# Hobby Server Monitor ⚡

> An on-premises Linux server management dashboard and resource monitoring platform built on top of **LXD containers**, **Falcon API**, **Astro SSR**, **SQLite**, and **TinyFlux TSDB**.

Designed for resource efficiency, security, and real-time observability on local Linux hardware.

---

## 📐 Architecture Overview

```
                      ┌─────────────────────────────────────────┐
                      │    Browser Interface (Astro SSR)        │
                      │    - Admin Control Panel                │
                      │    - User Quotas & Containers           │
                      │    - In-Browser Terminal (>_)           │
                      │    - Chart.js Time-Series Telemetry     │
                      └────────────────────┬────────────────────┘
                                           │
                                     HTTP / Cookies
                                           │
                                           ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           Falcon REST API (Port 8000)                           │
│  ┌───────────────────┐  ┌──────────────────┐  ┌──────────────────────────────┐  │
│  │ Google OAuth RBAC │  │ Quota Validator  │  │ Terminal Exec & Audit Log    │  │
│  └───────────────────┘  └──────────────────┘  └──────────────────────────────┘  │
└───────────┬──────────────────────────────┬──────────────────────────────┬───────┘
            │                              │                              │
            ▼                              ▼                              ▼
 ┌─────────────────────┐        ┌─────────────────────┐        ┌─────────────────────┐
 │ SQLite Database WAL │        │   TinyFlux TSDB     │        │ pylxd Unix Socket   │
 │ - users / sessions  │        │ - 4-Tier Metrics    │        │ - LXD Hypervisor    │
 │ - assignments/audit │        │ - 30-Day Retention  │        │ - Container Engine  │
 └─────────────────────┘        └─────────────────────┘        └─────────────────────┘
                                           ▲
                                           │ Polling (Every 10s)
                                           │
                                ┌─────────────────────┐
                                │ Metrics Collector   │
                                │ (collector/main.py) │
                                └─────────────────────┘
```

### Components Summary:
1. **Frontend**: **Astro 4 (SSR mode)** — High-performance, low-footprint server-side rendered dashboard with dynamic vanilla JS client components.
2. **API Backend**: **Falcon 3.x WSGI** — Micro-framework running under Gunicorn, serving RESTful endpoints with strict role-based access control (RBAC).
3. **Hypervisor Integration**: **`pylxd` 2.3.5** — Python client communicating with local LXD Unix socket (`/var/snap/lxd/common/lxd/unix.socket` or `/var/lib/lxd/unix.socket`).
4. **Relational Storage**: **SQLite 3 (WAL Mode)** — Thread-local connection pool storing user profiles, active sessions, container assignments, and audit logs.
5. **Time-Series Metric Engine**: **TinyFlux TSDB** — Lightweight TSDB storing downsampled container telemetry with an automated 4-tier compaction pipeline.

---

## 🛠️ Step-by-Step Installation Guide

Follow these instructions to set up the system on a fresh **Ubuntu 22.04 / 24.04** machine or **WSL2** instance.

### Prerequisites
* Python 3.10+
* Node.js 18+ & `npm`
* LXD 5.x initialized (`lxd init --minimal`)

---

### Step 1: Clone Repository & Create Virtual Environment
```bash
git clone https://github.com/your-username/hobby-server-monitor-scriptspectra.git
cd hobby-server-monitor-scriptspectra

# Create Python virtual environment inside backend/
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd ..
```

---

### Step 2: Set Up Frontend Dependencies
```bash
cd dashboard
npm install
cd ..
```

---

### Step 3: Configure Environment Variables
Copy `.env.example` to `.env` in the root directory:
```bash
cp .env.example .env
```

Edit `.env` and configure your settings:
```ini
GOOGLE_OAUTH_CLIENT_ID=your-google-oauth-client-id.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=your-google-oauth-client-secret
GOOGLE_OAUTH_REDIRECT_URI=http://localhost:8000/auth/google/callback
SESSION_SECRET=a5f8b9... # Generate via: python -c "import secrets; print(secrets.token_hex(32))"
BOOTSTRAP_ADMIN_EMAIL=your-email@gmail.com
```

> **Google OAuth Setup**:
> 1. Go to [Google Cloud Console Credentials](https://console.cloud.google.com/apis/credentials).
> 2. Create an **OAuth 2.0 Client ID** (Web application).
> 3. Add `http://localhost:8000/auth/google/callback` to **Authorized Redirect URIs**.

---

### Step 4: Initialize LXD & Database
Make sure your user belongs to the `lxd` group:
```bash
sudo usermod -aG lxd $USER
newgrp lxd

# Initialize LXD with minimal defaults
sudo lxd init --minimal

# Initialize SQLite database schema
cd backend
.venv/bin/python -m scripts.init_db
cd ..
```

---

### Step 5: Start All Application Services

Open 3 terminal windows or run using Systemd / background tasks:

#### Terminal 1 — Backend API (Port 8000)
```bash
cd backend
.venv/bin/gunicorn app.main:application -w 2 -b 0.0.0.0:8000
```

#### Terminal 2 — Metrics Collector Daemon
```bash
cd backend
.venv/bin/python -m collector.main
```

#### Terminal 3 — Astro Dashboard (Port 4321)
```bash
cd dashboard
npm run dev
```

Open **[http://localhost:4321](http://localhost:4321)** in your browser!

---

## ⚙️ Systemd Production Service Deployment

To install all application services as systemd daemons auto-starting on system boot:

```bash
sudo bash deploy/install_services.sh
```

To manage services:
```bash
sudo systemctl status lxd-monitor-backend.service
sudo systemctl status lxd-monitor-collector.service
sudo systemctl status lxd-monitor-dashboard.service
```

---

## 📊 Data Model & Database Schemas

### SQLite Relational Schema (`data/app.db`)

* **`users`**: User account credentials, Google OAuth profile info, role (`admin` or `user`), and individual resource quotas (`quota_ram_mb`, `quota_cpu_cores`, `quota_disk_gb`).
* **`sessions`**: Secure HMAC-SHA256 signed session tokens, user ID mappings, and expiration timestamps.
* **`containers`**: Registered LXD container instances, creator ID, resource allocation limits, and soft-delete timestamp.
* **`assignments`**: Container-to-user permission assignments with grant/revoke timestamps.
* **`audit_log`**: Security trail recording all container lifecycle events, limit alterations, user invitations, and terminal command executions.

### TinyFlux TSDB Structure (`data/metrics.tinyflux`)

* **Measurement**: `container_metrics`
* **Tags**: `{"container_name": <str>, "state": <str>, "compacted": "true"}`
* **Fields**:
  * `cpu_percent`: float (0.0% – 100.0%)
  * `ram_used_mb`: float
  * `ram_alloc_mb`: float
  * `disk_used_gb`: float
  * `disk_alloc_gb`: float
  * `net_rx_rate_bps`: float
  * `net_tx_rate_bps`: float
  * `process_count`: int

---

## 🔒 Security & Threat Model

1. **Authentication & Authorization**:
   * Google OAuth 2.0 with strict invite enforcement — uninvited Google accounts cannot sign in.
   * Fine-grained Role-Based Access Control (RBAC): Standard `user` accounts can only view assigned containers (`403 Forbidden` on unassigned resource access).
2. **Terminal Exec Safety**:
   * Commands are validated server-side (length <= 512 chars, forbidden binary patterns).
   * All terminal exec commands are recorded in `audit_log` with actor ID, timestamp, and exit status.
3. **LXD Socket Privilege Model**:
   * Backend communicates with LXD over the local Unix socket (`/var/snap/lxd/common/lxd/unix.socket`).
   * Read/write access is restricted to the `lxd` OS group.

---

## 🧪 Running Automated Tests

Run the complete backend pytest suite (33 tests):
```bash
cd backend
.venv/bin/pytest tests/ -v
```
