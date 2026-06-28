# 🏗️ Personal Microservices Stack

> 6-service Docker stack behind a single Nginx reverse proxy — system monitoring, blog API, library management, laptop booking, and file conversion.

[![Docker](https://img.shields.io/badge/docker-compose-2496ED?logo=docker)](https://docs.docker.com/compose/)
[![Nginx](https://img.shields.io/badge/nginx-reverse_proxy-009639?logo=nginx)](https://nginx.org/)
[![Python](https://img.shields.io/badge/python-3.11-blue?logo=python)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](./LICENSE)

A production-grade microservices architecture running on a single cloud server with **Docker Compose** orchestration, **Nginx reverse proxy**, **SSL termination**, and **6 independent services** communicating over an internal Docker network.

---

## 📐 Architecture

```
                    ┌──────────────────────────────┐
                    │     Internet (HTTPS :443)     │
                    └──────────────┬───────────────┘
                                   │
                    ┌──────────────▼───────────────┐
                    │    Nginx Reverse Proxy       │
                    │    (personal-site)           │
                    │    - SSL Termination          │
                    │    - Static File Serving      │
                    │    - Route-based Proxying     │
                    └──────┬───────────┬───────────┘
                           │           │
              ┌────────────┼───────────┼────────────┐
              │            │           │            │
    ┌─────────▼──┐ ┌──────▼────┐ ┌───▼──────┐ ┌───▼──────────┐
    │ Dashboard  │ │ Blog API  │ │ Library  │ │File Converter│
    │  :80       │ │  :5000    │ │  :8000   │ │   :80        │
    │ Flask      │ │ Flask     │ │ FastAPI  │ │ Flask+Libr..  │
    └────────────┘ └───────────┘ └──────────┘ └──────────────┘
                                              ┌──────────────┐
                                              │Laptop Booking│
                                              │  :5000       │
                                              │ Flask+SQLite │
                                              └──────────────┘
```

---

## 🚀 Services

| Service | Tech Stack | Port | Description |
|---------|-----------|------|-------------|
| **Nginx Site** | Nginx 1.27 Alpine | 80/443 | Reverse proxy, SSL termination, static files |
| **Dashboard** | Flask + SQLite | 5000 | System metrics (CPU/RAM/Disk), Docker container monitoring, Nginx log analytics |
| **Blog API** | Flask + Markdown | 5000 | Markdown article rendering, full-text search, reading time, JSON-LD |
| **Library API** | FastAPI + SQLAlchemy | 8000 | JWT auth, book management, borrowing system, inventory tracking |
| **Laptop Booking** | Flask + SQLite | 5000 | Booking system with admin panel, phone validation, rate limiting |
| **File Converter** | Flask + LibreOffice | 80 | Document conversion (docx ↔ pdf, image ↔ pdf) |

---

## 📦 Quick Start

### Prerequisites

- Docker & Docker Compose
- SSL certificates (optional — place in `./ssl/`)

### 1. Clone

```bash
git clone https://github.com/Sver0411/microservices-stack.git
cd microservices-stack
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your settings
```

### 3. Create Docker network

```bash
docker network create services-net
```

### 4. Build & Run

```bash
docker compose build
docker compose up -d
```

### 5. Verify

```bash
curl http://localhost/dashboard/
curl http://localhost/blog/api/
curl http://localhost/api/status
```

---

## 📁 Project Structure

```
microservices-stack/
├── docker-compose.yml          # Service orchestration
├── .env.example                # Environment template
├── services/
│   ├── site/
│   │   ├── Dockerfile
│   │   └── nginx.conf          # Reverse proxy rules
│   ├── dashboard/
│   │   ├── Dockerfile
│   │   ├── app.py              # Flask API (6 endpoints)
│   │   ├── index.html          # Dashboard UI
│   │   └── requirements.txt
│   ├── blog-backend/
│   │   ├── Dockerfile
│   │   ├── app.py              # Markdown blog API
│   │   └── requirements.txt
│   ├── library/
│   │   ├── Dockerfile
│   │   ├── app.py              # FastAPI (1378 lines, JWT+RBAC)
│   │   └── requirements.txt
│   ├── laptop-booking/
│   │   ├── Dockerfile
│   │   ├── app.py              # Booking system + admin panel
│   │   └── requirements.txt
│   └── converter/
│       ├── Dockerfile
│       ├── app.py              # LibreOffice wrapper
│       └── requirements.txt
├── .gitignore
└── LICENSE
```

---

## 🔧 Key Features

### Dashboard API (`/api/status`)
- Real-time CPU, memory, disk usage (`/host_proc`)
- Docker container list + health status (Unix socket)
- SSL certificate expiry monitoring
- Nginx access log analytics (PV/UV/status codes)
- Time-series metrics history (SQLite, 8-day retention)

### Blog API (`/blog/api/`)
- Loads Markdown articles with YAML frontmatter
- Full-text search with weighted scoring
- Related articles by tag overlap
- Reading time estimation (Chinese + English + code)
- JSON-LD structured data for SEO

### Library API (`/library/api/`)
- JWT authentication with refresh tokens
- Role-based access control (admin/librarian/reader)
- Complete book lifecycle: catalog → borrow → return → fine
- Reservation queue system
- Rate limiting on login endpoint

### Laptop Booking (`/laptop/api/`)
- Booking creation with phone validation
- Built-in admin panel (password-protected, glass-morphism UI)
- Rate limiting (3 bookings/day/IP)
- CSV export for admin
- Feishu/Lark webhook notifications

### File Converter (`/api/convert`)
- LibreOffice headless conversion
- Supports: docx ↔ pdf, image ↔ pdf
- File size limit: 50MB

---

## 🐳 Docker Networking

All services communicate over an **external Docker network** (`services-net`). The Nginx container acts as the single entry point and proxies requests to backend containers by hostname:

```nginx
# Static hostname (no DNS variable needed for fixed names)
proxy_pass http://personal-dashboard/;

# Runtime DNS resolution (for containers that may restart)
set $lib_api library-api:8000;
proxy_pass http://$lib_api;
```

---

## 🔒 Security

- SSL/TLS termination at Nginx (TLSv1.2+)
- `server_tokens off` — hides Nginx version
- Security headers: HSTS, X-Frame-Options, CSP, Referrer-Policy
- JWT with refresh token rotation (Library API)
- Rate limiting on auth and booking endpoints
- Non-root user in blog-backend container

---

## 🛠️ Tech Stack Overview

| Category | Technology |
|----------|-----------|
| Reverse Proxy | Nginx 1.27 (Alpine) |
| Backend Framework | Flask 3.x, FastAPI 0.115 |
| Database | SQLite (WAL mode) |
| Auth | JWT (PyJWT), bcrypt (passlib) |
| Docs | LibreOffice headless |
| Container | Docker + Docker Compose |
| Monitoring | /proc filesystem, Docker Unix socket |

---

## 📝 License

MIT © 2026

---

## 🤝 Contributing

This is a personal showcase project — feel free to fork, star, or open issues for suggestions!
