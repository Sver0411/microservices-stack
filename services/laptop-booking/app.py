#!/usr/bin/env python3
"""Laptop booking system — Flask + SQLite backend."""

import csv
import io
import json
import os
import re
import sqlite3
from datetime import date, datetime, timezone

import requests
from flask import Flask, Response, g, jsonify, request

# ── Config ──────────────────────────────────────────────────────────────────
DB_PATH = os.environ.get("DB_PATH", "/data/bookings.db")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "change-me-in-production")
FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")
RATE_LIMIT_PER_DAY = 3

app = Flask(__name__)


# ── Database helpers ────────────────────────────────────────────────────────
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create tables if they don't exist."""
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS bookings (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            phone       TEXT    NOT NULL,
            wechat      TEXT    DEFAULT '',
            dormitory   TEXT    DEFAULT '',
            service_type TEXT   NOT NULL,
            notes       TEXT    DEFAULT '',
            appointment_time TEXT DEFAULT '',
            ip_address  TEXT    DEFAULT '',
            status      TEXT    DEFAULT 'pending',
            created_at  TEXT    DEFAULT (datetime('now', 'localtime')),
            updated_at  TEXT    DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS rate_limits (
            ip_address  TEXT NOT NULL,
            date        TEXT NOT NULL,
            count       INTEGER DEFAULT 1,
            PRIMARY KEY (ip_address, date)
        );
        """
    )
    db.commit()
    db.close()


init_db()


# ── Helpers ─────────────────────────────────────────────────────────────────
def get_client_ip():
    """Extract client IP, preferring X-Forwarded-For."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "127.0.0.1"


def validate_phone(phone: str) -> bool:
    """Phone must be 11 digits starting with 1."""
    return bool(re.fullmatch(r"1\d{10}", phone))


def mask_phone(phone: str) -> str:
    """Mask phone: 138****1234."""
    if len(phone) == 11:
        return phone[:3] + "****" + phone[7:]
    return phone[:3] + "****" + phone[-4:] if len(phone) >= 7 else "***"


def check_admin():
    """Return True if the request carries a valid admin key."""
    key = request.args.get("key", "")
    return key == ADMIN_PASSWORD


def send_feishu_notification(booking):
    """Send a Feishu webhook notification for a new booking."""
    if not FEISHU_WEBHOOK:
        return
    text = (
        f"📋 新的预约\n"
        f"姓名：{booking['name']}\n"
        f"手机：{booking['phone']}\n"
        f"微信：{booking['wechat'] or '未填'}\n"
        f"宿舍：{booking['dormitory'] or '未填'}\n"
        f"服务：{booking['service_type']}\n"
        f"时间：{booking['appointment_time'] or '未指定'}\n"
        f"备注：{booking['notes'] or '无'}\n"
        f"IP：{booking['ip_address']}"
    )
    try:
        requests.post(
            FEISHU_WEBHOOK,
            json={"msg_type": "text", "content": {"text": text}},
            timeout=5,
        )
    except Exception:
        pass  # Non-critical


def check_rate_limit(ip: str) -> bool:
    """Return True if within rate limit (≤3/day), False if exceeded."""
    today = date.today().isoformat()
    db = get_db()
    row = db.execute(
        "SELECT count FROM rate_limits WHERE ip_address=? AND date=?",
        (ip, today),
    ).fetchone()
    if row is None:
        return True  # No record yet — allowed
    return row["count"] < RATE_LIMIT_PER_DAY


def incr_rate_limit(ip: str):
    today = date.today().isoformat()
    db = get_db()
    db.execute(
        "INSERT INTO rate_limits (ip_address, date, count) VALUES (?, ?, 1) "
        "ON CONFLICT(ip_address, date) DO UPDATE SET count=count+1",
        (ip, today),
    )
    db.commit()


# ── API routes ──────────────────────────────────────────────────────────────


@app.route("/api/bookings", methods=["POST"])
def create_booking():
    """Create a new booking."""
    data = request.get_json(silent=True) or {}

    # Required fields
    name = (data.get("name") or data.get("姓名") or "").strip()
    phone = (data.get("phone") or data.get("手机号") or "").strip()
    service_type = (data.get("service_type") or data.get("服务类型") or "").strip()

    if not name:
        return jsonify({"error": "姓名不能为空"}), 400
    if not phone:
        return jsonify({"error": "手机号不能为空"}), 400
    if not validate_phone(phone):
        return jsonify({"error": "手机号格式不正确，请输入11位手机号"}), 400
    if not service_type:
        return jsonify({"error": "请选择服务类型"}), 400

    ip = get_client_ip()

    # Rate limit
    if not check_rate_limit(ip):
        return jsonify({"error": "今日预约次数已达上限（3次），请明天再试"}), 429

    # Optional fields
    wechat = (data.get("wechat") or data.get("微信号") or "").strip()
    dormitory = (data.get("dormitory") or data.get("宿舍楼栋") or "").strip()
    notes = (data.get("notes") or data.get("备注") or "").strip()
    appointment_time = (
        data.get("appointment_time") or data.get("预约时间段") or ""
    ).strip()

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    db = get_db()
    cursor = db.execute(
        """INSERT INTO bookings
           (name, phone, wechat, dormitory, service_type, notes,
            appointment_time, ip_address, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (name, phone, wechat, dormitory, service_type, notes,
         appointment_time, ip, now, now),
    )
    db.commit()
    booking_id = cursor.lastrowid

    booking = dict(db.execute(
        "SELECT * FROM bookings WHERE id=?", (booking_id,)
    ).fetchone())

    incr_rate_limit(ip)
    send_feishu_notification(booking)

    return jsonify({
        "message": "预约成功！我们会尽快联系您。",
        "id": booking_id,
    }), 201


@app.route("/api/bookings", methods=["GET"])
def list_bookings():
    """Admin: list all bookings (requires ?key=password)."""
    if not check_admin():
        return jsonify({"error": "Unauthorized"}), 401

    status_filter = request.args.get("status", "").strip()
    db = get_db()

    if status_filter:
        rows = db.execute(
            "SELECT * FROM bookings WHERE status=? ORDER BY created_at DESC",
            (status_filter,),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM bookings ORDER BY created_at DESC"
        ).fetchall()

    bookings = []
    for r in rows:
        b = dict(r)
        b["phone"] = mask_phone(b["phone"])  # Mask phone in listing
        bookings.append(b)

    return jsonify(bookings)


@app.route("/api/bookings/<int:booking_id>", methods=["PUT"])
def update_booking(booking_id):
    """Admin: update booking status."""
    if not check_admin():
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    new_status = data.get("status", "").strip()

    valid_statuses = {"pending", "contacted", "completed"}
    if new_status not in valid_statuses:
        return jsonify({"error": f"Invalid status. Must be one of: {valid_statuses}"}), 400

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db = get_db()
    db.execute(
        "UPDATE bookings SET status=?, updated_at=? WHERE id=?",
        (new_status, now, booking_id),
    )
    db.commit()

    row = db.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
    if row is None:
        return jsonify({"error": "Booking not found"}), 404

    booking = dict(row)
    booking["phone"] = mask_phone(booking["phone"])
    return jsonify(booking)


@app.route("/api/bookings/export", methods=["GET"])
def export_bookings():
    """Admin: export all bookings as CSV."""
    if not check_admin():
        return jsonify({"error": "Unauthorized"}), 401

    db = get_db()
    rows = db.execute(
        "SELECT * FROM bookings ORDER BY created_at DESC"
    ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID", "姓名", "手机号", "微信号", "宿舍楼栋", "服务类型",
        "备注", "预约时间", "状态", "创建时间", "更新时间"
    ])
    for r in rows:
        writer.writerow([
            r["id"], r["name"], r["phone"], r["wechat"], r["dormitory"],
            r["service_type"], r["notes"], r["appointment_time"],
            r["status"], r["created_at"], r["updated_at"]
        ])

    csv_data = output.getvalue()
    output.close()

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=bookings.csv",
            "Content-Type": "text/csv; charset=utf-8-sig",
        },
    )


# ── Admin page (server-rendered) ────────────────────────────────────────────

ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Booking Admin — Laptop Service</title>
<script src="https://cdn.tailwindcss.com"></script>
<script>tailwind.config={darkMode:'class',theme:{extend:{}}}</script>
<style>
:root{--glass-bg:rgba(255,255,255,0.6);--glass-border:rgba(255,255,255,0.45)}
.dark{--glass-bg:rgba(17,24,39,0.6);--glass-border:rgba(255,255,255,0.06)}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
.glass{background:var(--glass-bg);backdrop-filter:blur(24px) saturate(180%);-webkit-backdrop-filter:blur(24px) saturate(180%);border:1px solid var(--glass-border)}
.glass-strong{background:var(--glass-bg);backdrop-filter:blur(40px) saturate(200%);-webkit-backdrop-filter:blur(40px) saturate(200%);border:1px solid var(--glass-border);box-shadow:0 8px 32px rgba(0,0,0,0.06)}
</style>
</head>
<body class="bg-gradient-to-br from-gray-50 to-blue-50 min-h-screen text-gray-900">

<div id="loginScreen" class="flex items-center justify-center min-h-screen px-4">
  <div class="glass-strong rounded-3xl p-8 w-full max-w-md text-center">
    <div class="text-4xl mb-4">🔐</div>
    <h1 class="text-2xl font-black mb-2">预约管理系统</h1>
    <p class="text-gray-500 mb-6 text-sm">请输入管理密码</p>
    <input id="passwordInput" type="password" placeholder="管理密码" autocomplete="off"
      class="w-full px-4 py-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-white/80 mb-4 text-center text-lg outline-none focus:ring-2 focus:ring-blue-500 transition"
      onkeydown="if(event.key==='Enter')login()">
    <button onclick="login()"
      class="w-full bg-blue-600 text-white py-3 rounded-xl font-bold hover:bg-blue-700 transition shadow-lg shadow-blue-500/25">
      登录
    </button>
    <p id="loginError" class="text-red-500 text-sm mt-3 hidden">密码错误</p>
  </div>
</div>

<div id="adminPanel" class="hidden max-w-6xl mx-auto px-4 py-8">
  <!-- Header -->
  <div class="glass-strong rounded-2xl p-6 mb-6 flex flex-col sm:flex-row items-center justify-between gap-4">
    <div class="flex items-center gap-3">
      <span class="text-3xl">💻</span>
      <div>
        <h1 class="text-xl font-black">预约管理</h1>
        <p class="text-gray-500 text-sm">Laptop Service · Admin Panel</p>
      </div>
    </div>
    <div class="flex items-center gap-3">
      <select id="statusFilter" onchange="loadBookings()"
        class="px-4 py-2 rounded-xl border border-gray-200 bg-white/80 text-sm outline-none">
        <option value="">全部状态</option>
        <option value="pending">待处理</option>
        <option value="contacted">已联系</option>
        <option value="completed">已完成</option>
      </select>
      <button onclick="exportCSV()"
        class="bg-green-600 text-white px-4 py-2 rounded-xl font-bold text-sm hover:bg-green-700 transition shadow-lg shadow-green-500/25">
        📥 导出 CSV
      </button>
      <button onclick="logout()"
        class="bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-200 px-4 py-2 rounded-xl font-bold text-sm hover:bg-gray-300 transition">
        退出
      </button>
    </div>
  </div>

  <!-- Stats -->
  <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6" id="statsRow">
    <div class="glass rounded-2xl p-4 text-center">
      <div class="text-2xl font-black text-blue-600" id="statTotal">0</div>
      <div class="text-xs text-gray-500">总预约</div>
    </div>
    <div class="glass rounded-2xl p-4 text-center">
      <div class="text-2xl font-black text-orange-500" id="statPending">0</div>
      <div class="text-xs text-gray-500">待处理</div>
    </div>
    <div class="glass rounded-2xl p-4 text-center">
      <div class="text-2xl font-black text-yellow-600" id="statContacted">0</div>
      <div class="text-xs text-gray-500">已联系</div>
    </div>
    <div class="glass rounded-2xl p-4 text-center">
      <div class="text-2xl font-black text-green-600" id="statCompleted">0</div>
      <div class="text-xs text-gray-500">已完成</div>
    </div>
  </div>

  <!-- Bookings table -->
  <div class="glass-strong rounded-2xl overflow-x-auto">
    <table class="w-full text-sm">
      <thead>
        <tr class="border-b border-gray-200 dark:border-gray-700 text-gray-500">
          <th class="text-left p-4">ID</th>
          <th class="text-left p-4">姓名</th>
          <th class="text-left p-4">手机</th>
          <th class="text-left p-4 hidden sm:table-cell">微信</th>
          <th class="text-left p-4 hidden md:table-cell">宿舍</th>
          <th class="text-left p-4">服务类型</th>
          <th class="text-left p-4 hidden lg:table-cell">预约时间</th>
          <th class="text-left p-4">状态</th>
          <th class="text-left p-4">操作</th>
        </tr>
      </thead>
      <tbody id="bookingsTable">
        <tr><td colspan="9" class="p-8 text-center text-gray-400">加载中...</td></tr>
      </tbody>
    </table>
  </div>
</div>

<script>
let adminKey = '';

function login() {
  const pw = document.getElementById('passwordInput').value;
  fetch('/laptop/api/bookings?key=' + encodeURIComponent(pw))
    .then(r => {
      if (r.ok) { adminKey = pw; showPanel(); }
      else { document.getElementById('loginError').classList.remove('hidden'); }
    })
    .catch(() => { document.getElementById('loginError').classList.remove('hidden'); });
}

function logout() {
  adminKey = '';
  document.getElementById('loginScreen').classList.remove('hidden');
  document.getElementById('adminPanel').classList.add('hidden');
  document.getElementById('passwordInput').value = '';
}

function showPanel() {
  document.getElementById('loginScreen').classList.add('hidden');
  document.getElementById('adminPanel').classList.remove('hidden');
  loadBookings();
}

function loadBookings() {
  const status = document.getElementById('statusFilter').value;
  let url = '/laptop/api/bookings?key=' + encodeURIComponent(adminKey);
  if (status) url += '&status=' + encodeURIComponent(status);

  fetch(url)
    .then(r => r.json())
    .then(data => {
      if (Array.isArray(data)) {
        renderBookings(data);
      }
    });
}

function renderBookings(bookings) {
  const tbody = document.getElementById('bookingsTable');
  if (!bookings.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="p-8 text-center text-gray-400">暂无预约记录</td></tr>';
  } else {
    tbody.innerHTML = bookings.map(b => {
      const statusColors = {
        pending: 'bg-orange-100 text-orange-700',
        contacted: 'bg-blue-100 text-blue-700',
        completed: 'bg-green-100 text-green-700'
      };
      const statusLabels = { pending: '待处理', contacted: '已联系', completed: '已完成' };
      const sc = statusColors[b.status] || 'bg-gray-100 text-gray-600';
      const sl = statusLabels[b.status] || b.status;
      const appointment = b.appointment_time ? b.appointment_time.replace('T', ' ') : '-';
      const notes = b.notes ? '<br><span class="text-xs text-gray-400">备注: ' + escapeHtml(b.notes) + '</span>' : '';
      return '<tr class="border-b border-gray-100 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-900/50">'
        + '<td class="p-4 text-gray-400">#' + b.id + '</td>'
        + '<td class="p-4 font-semibold">' + escapeHtml(b.name) + '</td>'
        + '<td class="p-4 text-gray-500">' + escapeHtml(b.phone) + '</td>'
        + '<td class="p-4 text-gray-500 hidden sm:table-cell">' + escapeHtml(b.wechat || '-') + '</td>'
        + '<td class="p-4 text-gray-500 hidden md:table-cell">' + escapeHtml(b.dormitory || '-') + '</td>'
        + '<td class="p-4">' + escapeHtml(b.service_type) + notes + '</td>'
        + '<td class="p-4 text-gray-500 hidden lg:table-cell">' + appointment + '</td>'
        + '<td class="p-4"><span class="px-2.5 py-1 rounded-full text-xs font-medium ' + sc + '">' + sl + '</span></td>'
        + '<td class="p-4">'
        + (b.status === 'pending' ? '<button onclick="updateStatus(' + b.id + ',\'contacted\')" class="text-blue-600 hover:text-blue-800 mr-2 text-xs font-bold">标记已联系</button>' : '')
        + (b.status === 'contacted' ? '<button onclick="updateStatus(' + b.id + ',\'completed\')" class="text-green-600 hover:text-green-800 text-xs font-bold">标记已完成</button>' : '')
        + (b.status === 'completed' ? '<span class="text-gray-400 text-xs">已完成</span>' : '')
        + '</td></tr>';
    }).join('');
  }

  // Update stats
  fetch('/laptop/api/bookings?key=' + encodeURIComponent(adminKey))
    .then(r => r.json())
    .then(all => {
      document.getElementById('statTotal').textContent = all.length;
      document.getElementById('statPending').textContent = all.filter(b => b.status === 'pending').length;
      document.getElementById('statContacted').textContent = all.filter(b => b.status === 'contacted').length;
      document.getElementById('statCompleted').textContent = all.filter(b => b.status === 'completed').length;
    });
}

function updateStatus(id, status) {
  fetch('/laptop/api/bookings/' + id + '?key=' + encodeURIComponent(adminKey), {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({status: status})
  }).then(r => r.json()).then(() => loadBookings());
}

function exportCSV() {
  window.open('/laptop/api/bookings/export?key=' + encodeURIComponent(adminKey), '_blank');
}

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}
</script>
</body>
</html>"""


@app.route("/admin")
def admin_page():
    """Serve the admin management page."""
    return ADMIN_HTML


# ── Health check ────────────────────────────────────────────────────────────
@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


# ── Main ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
