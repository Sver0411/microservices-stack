from flask import Flask, jsonify, send_file, request
import os
import time
import sqlite3
import threading
import json
import re
import socket as sock_mod
import subprocess
import urllib.request
from datetime import datetime
from collections import Counter

app = Flask(__name__)

PROC = "/host_proc" if os.path.exists("/host_proc") else "/proc"
ROOTFS = "/host_root" if os.path.exists("/host_root") else "/"
DOCKER_SOCK = "/var/run/docker.sock"
SSL_CERT_PATH = "/etc/nginx/ssl/fullchain.pem"
NGINX_LOG_PATH = "/var/log/nginx-host/access.log"

# ---------------------------------------------------------------------------
# ── Helpers (unchanged from original) ──
# ---------------------------------------------------------------------------

def read_host_proc(path):
    try:
        with open(os.path.join(PROC, path)) as f:
            return f.read().strip()
    except:
        return ""

def get_cpu_usage():
    stat = read_host_proc("stat")
    if not stat:
        return 0, 0
    parts = stat.split("\n")[0].split()
    if len(parts) < 5:
        return 0, 0
    vals = [int(x) for x in parts[1:8]]
    total = sum(vals)
    idle = vals[3] + vals[4]
    return total, idle

def get_meminfo():
    info = read_host_proc("meminfo")
    mem = {"total": 0, "free": 0, "available": 0, "buffers": 0, "cached": 0}
    for line in info.split("\n"):
        if ":" in line:
            key, val = line.split(":", 1)
            val = val.strip().split()[0]
            try:
                mem[key.strip()] = int(val) * 1024
            except ValueError:
                pass
    mem["total"] = mem.get("MemTotal", 0)
    mem["free"] = mem.get("MemFree", 0)
    mem["available"] = mem.get("MemAvailable", 0)
    mem["buffers"] = mem.get("Buffers", 0)
    mem["cached"] = mem.get("Cached", 0) + mem.get("SReclaimable", 0)
    return mem

def get_disk():
    try:
        s = os.statvfs(ROOTFS)
        total = s.f_frsize * s.f_blocks
        free = s.f_frsize * s.f_bavail
        used = total - free
        return total, used, free
    except:
        return 0, 0, 0

def get_loadavg():
    load = read_host_proc("loadavg")
    if not load:
        return 0, 0, 0
    parts = load.split()[:3]
    return tuple(float(x) for x in parts)

def get_uptime():
    up = read_host_proc("uptime")
    if not up:
        return 0
    return float(up.split()[0])

def get_cpu_count():
    info = read_host_proc("cpuinfo")
    return info.count("processor\t:") or 1

# ---------------------------------------------------------------------------
# ── Metrics History (SQLite time-series) ──
# ---------------------------------------------------------------------------

DB_PATH = "/app/metrics.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL NOT NULL,
        cpu_percent REAL,
        mem_percent REAL,
        disk_percent REAL,
        mem_used INTEGER,
        mem_total INTEGER,
        disk_used INTEGER,
        disk_total INTEGER
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(timestamp)")
    conn.commit()
    conn.close()

def record_metrics_sample():
    """Take one sample and store it in SQLite."""
    try:
        # CPU
        cpu_total, cpu_idle = get_cpu_usage()
        time.sleep(0.1)
        cpu_total2, cpu_idle2 = get_cpu_usage()
        cpu_pct = 0
        dt = cpu_total2 - cpu_total
        di = cpu_idle2 - cpu_idle
        if dt > 0:
            cpu_pct = round((1 - di / dt) * 100, 1)

        # Memory
        mem = get_meminfo()
        mem_used = mem["total"] - mem["available"]
        mem_pct = round(mem_used / mem["total"] * 100, 1) if mem["total"] else 0

        # Disk
        disk_total, disk_used, disk_free = get_disk()
        disk_pct = round(disk_used / disk_total * 100, 1) if disk_total else 0

        now = time.time()
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO metrics (timestamp, cpu_percent, mem_percent, disk_percent, mem_used, mem_total, disk_used, disk_total) VALUES (?,?,?,?,?,?,?,?)",
            (now, cpu_pct, mem_pct, disk_pct, mem_used, mem["total"], disk_used, disk_total)
        )
        conn.commit()
        # Purge data older than 8 days
        cutoff = now - 8 * 86400
        conn.execute("DELETE FROM metrics WHERE timestamp < ?", (cutoff,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"metrics_record error: {e}")

def metrics_recorder():
    init_db()
    record_metrics_sample()  # First sample immediately
    while True:
        time.sleep(60)
        record_metrics_sample()

# ---------------------------------------------------------------------------
# ── Docker Socket Helper ──
# ---------------------------------------------------------------------------

def docker_sock_request(method, path):
    """Make an HTTP request to the Docker Unix socket. Returns parsed JSON."""
    s = sock_mod.socket(sock_mod.AF_UNIX, sock_mod.SOCK_STREAM)
    s.settimeout(5)
    try:
        s.connect(DOCKER_SOCK)
        req = f"{method} {path} HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        s.sendall(req.encode())
        resp = b""
        while True:
            chunk = s.recv(8192)
            if not chunk:
                break
            resp += chunk
    finally:
        s.close()

    if not resp:
        return None
    # Split headers and body
    parts = resp.split(b"\r\n\r\n", 1)
    if len(parts) < 2:
        return None
    body = parts[1]
    # Handle chunked transfer encoding
    headers = parts[0].decode(errors="replace")
    if "chunked" in headers.lower():
        # Simple chunked decoder
        decoded = b""
        idx = 0
        while idx < len(body):
            crlf = body.find(b"\r\n", idx)
            if crlf == -1:
                break
            try:
                chunk_size = int(body[idx:crlf], 16)
            except ValueError:
                break
            if chunk_size == 0:
                break
            decoded += body[crlf + 2:crlf + 2 + chunk_size]
            idx = crlf + 2 + chunk_size + 2
        body = decoded
    try:
        return json.loads(body)
    except:
        return None

def docker_list_containers():
    """List all containers via Docker socket."""
    return docker_sock_request("GET", "/containers/json?all=true")

def docker_inspect(name):
    """Inspect a container by name."""
    return docker_sock_request("GET", f"/containers/{name}/json")

# ---------------------------------------------------------------------------
# ── SSL Certificate Check ──
# ---------------------------------------------------------------------------

def get_ssl_info():
    """Parse SSL certificate and return expiry info."""
    cert_paths = [
        SSL_CERT_PATH,
        "/host_root/etc/nginx/ssl/fullchain.pem",
        "/host_root/opt/services/site/ssl/fullchain.pem",
    ]
    cert_path = None
    for p in cert_paths:
        if os.path.exists(p):
            cert_path = p
            break

    if not cert_path:
        return {"error": "Certificate file not found", "domain": "", "issuer": "", "expiry": "", "days_remaining": -1}

    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", cert_path, "-noout", "-enddate", "-issuer", "-subject"],
            capture_output=True, text=True, timeout=10
        )
        output = result.stdout + result.stderr

        # Parse enddate
        end_match = re.search(r"notAfter=(.+)", output)
        issuer_match = re.search(r"issuer=\s*(.+)", output)
        subject_match = re.search(r"subject=\s*(.+)", output)

        expiry_str = end_match.group(1) if end_match else ""
        issuer = issuer_match.group(1) if issuer_match else ""
        subject = subject_match.group(1) if subject_match else ""

        # Extract CN from subject
        domain = ""
        cn_match = re.search(r"CN\s*=\s*([^,\s]+)", subject)
        if cn_match:
            domain = cn_match.group(1)

        # Calculate days remaining
        days_remaining = -1
        expiry_date = ""
        if expiry_str:
            try:
                expiry_dt = datetime.strptime(expiry_str, "%b %d %H:%M:%S %Y %Z")
                expiry_date = expiry_dt.strftime("%Y-%m-%d")
                days_remaining = (expiry_dt - datetime.now()).days
            except:
                # Try alternate format
                try:
                    expiry_dt = datetime.strptime(expiry_str.strip(), "%b %d %H:%M:%S %Y GMT")
                    expiry_date = expiry_dt.strftime("%Y-%m-%d")
                    days_remaining = (expiry_dt - datetime.now()).days
                except:
                    pass

        return {
            "domain": domain,
            "issuer": issuer.strip(),
            "expiry": expiry_date,
            "days_remaining": days_remaining
        }
    except Exception as e:
        return {"error": str(e), "domain": "", "issuer": "", "expiry": "", "days_remaining": -1}

# ---------------------------------------------------------------------------
# ── Nginx Access Log Stats ──
# ---------------------------------------------------------------------------

def parse_nginx_stats():
    """Parse today's nginx access log from the shared volume."""
    today = datetime.now().strftime("%d/%b/%Y")

    # Try direct log file first, then Docker JSON log fallback
    log_paths = [
        NGINX_LOG_PATH,
        "/host_root/var/log/nginx/access.log",
    ]

    log_path = None
    for p in log_paths:
        if os.path.exists(p):
            log_path = p
            break

    if not log_path:
        return {
            "pv": 0, "uv": 0, "status_codes": {},
            "top_paths": [], "error": "Log file not available"
        }

    pv = 0
    ips = set()
    status_counter = Counter()
    path_counter = Counter()

    try:
        with open(log_path, "r", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                # Standard nginx combined log format:
                # $remote_addr - $remote_user [$time_local] "$request" $status $body_bytes_sent "$http_referer" "$http_user_agent"
                match = re.match(
                    r'^(\S+) \S+ \S+ \[(\d{2}/\w+/\d{4}):\d{2}:\d{2}:\d{2} [+\-]\d{4}\] "(\S+) (\S+)[^"]*" (\d{3})',
                    line
                )
                if not match:
                    continue

                log_date = match.group(2)
                if log_date != today:
                    continue

                ip = match.group(1)
                path = match.group(4)
                status = match.group(5)

                pv += 1
                ips.add(ip)
                status_counter[status] += 1
                path_clean = path.split("?")[0]
                path_counter[path_clean] += 1
    except Exception as e:
        return {
            "pv": pv, "uv": len(ips), "status_codes": dict(status_counter),
            "top_paths": [], "error": str(e)
        }

    top_paths = [{"path": p, "count": c} for p, c in path_counter.most_common(10)]

    return {
        "pv": pv,
        "uv": len(ips),
        "status_codes": dict(status_counter),
        "top_paths": top_paths
    }

# ---------------------------------------------------------------------------
# ── Service Health Checks ──
# ---------------------------------------------------------------------------

SERVICES = [
    ("Nginx /", "http://personal-site/"),
    ("Dashboard", "http://personal-dashboard/"),
    ("Blog", "http://personal-site/blog/"),
    ("Library", "http://personal-site/library/"),
    ("Toolbox", "http://personal-site/toolbox/"),
    ("Laptop", "http://personal-site/laptop/"),
    ("Library API", "http://library-api:8000/"),
]

_service_cache = {}  # name -> {status, response_time_ms, last_checked}

def check_services():
    results = []
    now = datetime.now().isoformat()
    for name, url in SERVICES:
        start = time.time()
        status = "down"
        try:
            req = urllib.request.Request(url, method="HEAD")
            req.add_header("User-Agent", "Dashboard-HealthCheck/1.0")
            resp = urllib.request.urlopen(req, timeout=5)
            if resp.status < 500:
                status = "up"
        except:
            status = "down"
        elapsed = round((time.time() - start) * 1000)
        result = {
            "name": name,
            "status": status,
            "response_time_ms": elapsed,
            "last_checked": now
        }
        results.append(result)
        _service_cache[name] = result
    return results

# ---------------------------------------------------------------------------
# ── Routes (original /api/status preserved exactly) ──
# ---------------------------------------------------------------------------

_prev_cpu = None

@app.route("/api/status")
def api_status():
    global _prev_cpu
    now = datetime.now()

    # CPU (needs two samples for percentage)
    cpu_total, cpu_idle = get_cpu_usage()
    cpu_pct = 0
    if _prev_cpu:
        prev_total, prev_idle = _prev_cpu
        dt = cpu_total - prev_total
        di = cpu_idle - prev_idle
        if dt > 0:
            cpu_pct = round((1 - di / dt) * 100, 1)
    _prev_cpu = (cpu_total, cpu_idle)

    # Memory
    mem = get_meminfo()
    mem_used = mem["total"] - mem["available"]
    mem_pct = round(mem_used / mem["total"] * 100, 1) if mem["total"] else 0

    # Disk
    disk_total, disk_used, disk_free = get_disk()
    disk_pct = round(disk_used / disk_total * 100, 1) if disk_total else 0

    # Load
    load1, load5, load15 = get_loadavg()

    return jsonify({
        "time": now.strftime("%H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "weekday": now.weekday(),
        "uptime": int(get_uptime()),
        "cpu": {"percent": cpu_pct, "cores": get_cpu_count()},
        "memory": {
            "total": mem["total"], "used": mem_used,
            "available": mem["available"], "percent": mem_pct,
        },
        "disk": {
            "total": disk_total, "used": disk_used,
            "free": disk_free, "percent": disk_pct,
        },
        "load": {"1m": load1, "5m": load5, "15m": load15},
    })

# ── NEW: Metrics History ──

@app.route("/api/metrics/history")
def api_metrics_history():
    range_param = request.args.get("range", "1h")
    now = time.time()

    range_map = {
        "1h": 3600,
        "6h": 21600,
        "24h": 86400,
        "7d": 604800,
    }
    seconds = range_map.get(range_param, 3600)
    since = now - seconds

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT timestamp, cpu_percent, mem_percent, disk_percent FROM metrics WHERE timestamp >= ? ORDER BY timestamp ASC",
            (since,)
        ).fetchall()
        conn.close()

        data = []
        for row in rows:
            data.append({
                "t": row["timestamp"],
                "cpu": row["cpu_percent"],
                "mem": row["mem_percent"],
                "disk": row["disk_percent"],
            })
        return jsonify({"range": range_param, "data": data})
    except Exception as e:
        return jsonify({"range": range_param, "data": [], "error": str(e)})

# ── NEW: Docker Containers ──

@app.route("/api/containers")
def api_containers():
    if not os.path.exists(DOCKER_SOCK):
        return jsonify({"error": "Docker socket not available", "containers": []})

    try:
        raw = docker_list_containers()
        if not raw:
            return jsonify({"error": "No response from Docker", "containers": []})

        containers = []
        for c in raw:
            name = (c.get("Names", ["unknown"])[0] or "unknown").lstrip("/")
            state = c.get("State", "unknown")
            status = c.get("Status", "")
            image = c.get("Image", "")

            # Parse ports
            ports = []
            for p in c.get("Ports", []) or []:
                pub = p.get("PublicPort")
                priv = p.get("PrivatePort")
                if pub and priv:
                    ports.append(f"{pub}:{priv}")
                elif priv:
                    ports.append(str(priv))

            # Parse uptime from status string (e.g. "Up 3 days")
            uptime_str = ""
            if state == "running" and "Up" in status:
                uptime_str = status

            containers.append({
                "name": name,
                "status": state,
                "status_text": status,
                "image": image,
                "ports": ports,
                "uptime": uptime_str,
            })

        return jsonify({"containers": containers})
    except Exception as e:
        return jsonify({"error": str(e), "containers": []})

# ── NEW: SSL Certificate ──

@app.route("/api/ssl")
def api_ssl():
    info = get_ssl_info()
    return jsonify(info)

# ── NEW: Nginx Stats ──

@app.route("/api/nginx-stats")
def api_nginx_stats():
    stats = parse_nginx_stats()
    return jsonify(stats)

# ── NEW: Service Health ──

@app.route("/api/services")
def api_services():
    results = check_services()
    return jsonify({"services": results})

# ── Index ──

@app.route("/")
def index():
    return send_file("index.html")

# ---------------------------------------------------------------------------
# ── Main ──
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Start metrics recorder in background
    t = threading.Thread(target=metrics_recorder, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=80)
