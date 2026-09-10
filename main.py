import os
import sqlite3
import secrets
import hmac
import json
from datetime import datetime, timedelta
from functools import wraps
from flask import (
    Flask, request, redirect, url_for, session, render_template_string, jsonify
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "123456")

DB_DIR = "/opt/render/project/src" if os.path.exists("/opt/render/project/src") else "."
DB_NAME = os.path.join(DB_DIR, "salon.db")

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                category_type TEXT NOT NULL,
                sub_category TEXT NOT NULL,
                price REAL NOT NULL,
                duration INTEGER DEFAULT 30,
                credit_value REAL DEFAULT 0.0
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stylists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                title TEXT NOT NULL
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT UNIQUE NOT NULL,
                token TEXT UNIQUE NOT NULL,
                credits REAL DEFAULT 0.0
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_no TEXT UNIQUE NOT NULL,
                customer_id INTEGER NOT NULL,
                total_amount REAL NOT NULL,
                payment_details TEXT NOT NULL,
                remark TEXT DEFAULT '',
                status TEXT DEFAULT 'NORMAL',
                created_at TEXT NOT NULL,
                FOREIGN KEY(customer_id) REFERENCES customers(id)
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                item_name TEXT NOT NULL,
                price REAL NOT NULL,
                qty INTEGER DEFAULT 1,
                FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                service_id INTEGER NOT NULL,
                stylist TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                status TEXT DEFAULT 'CONFIRMED',
                FOREIGN KEY(customer_id) REFERENCES customers(id),
                FOREIGN KEY(service_id) REFERENCES services(id)
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS holidays (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date_str TEXT UNIQUE NOT NULL,
                reason TEXT
            );
        """)
        
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('open_time', '10:00')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('close_time', '20:00')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('closed_weekdays', '1')")
        
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM services")
        if cursor.fetchone()[0] == 0:
            sample_services = [
                ("Senior Director Cut", "Services", "Cut", 120.0, 45, 0.0),
                ("Botanical Oil Hair Coloring", "Services", "Coloring", 380.0, 90, 0.0),
                ("Top-up RM 1000 Free RM 200", "Packages", "Package", 1000.0, 0, 1200.0),
            ]
            cursor.executemany("""
                INSERT INTO services (name, category_type, sub_category, price, duration, credit_value)
                VALUES (?, ?, ?, ?, ?, ?)
            """, sample_services)
            
        cursor.execute("SELECT COUNT(*) FROM stylists")
        if cursor.fetchone()[0] == 0:
            sample_stylists = [
                ("Alex", "Director"),
                ("David", "Senior Stylist"),
                ("Emma", "Stylist")
            ]
            cursor.executemany("INSERT INTO stylists (name, title) VALUES (?, ?)", sample_stylists)

init_db()

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated

def get_setting(key, default):
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

def get_lang():
    return session.get("lang", "zh")

LANGS = {
    "zh": {
        "title": "Dew Hair Salon 管理后台",
        "pos": "POS 收银",
        "app_mgmt": "预约管理",
        "cust_mgmt": "会员与历史记录",
        "orders": "订单历史",
        "settings": "营业、项目与员工",
        "reports": "90天报表",
        "logout": "退出",
        "switch_lang": "English",
        "save": "保存",
        "delete": "删除"
    },
    "en": {
        "title": "Dew Hair Salon Admin",
        "pos": "POS",
        "app_mgmt": "Appointments",
        "cust_mgmt": "Members & History",
        "orders": "Orders",
        "settings": "Settings & Staff",
        "reports": "90-Day Reports",
        "logout": "Logout",
        "switch_lang": "中文",
        "save": "Save",
        "delete": "Delete"
    }
}

def get_layout():
    lang = get_lang()
    t = LANGS[lang]
    other_lang = "en" if lang == "zh" else "zh"
    other_lang_label = LANGS[other_lang]["switch_lang"]
    return f"""
<!DOCTYPE html>
<html lang="{lang}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{t['title']}</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 min-h-screen">
    <nav class="bg-indigo-600 text-white p-4 shadow-md">
        <div class="container mx-auto flex justify-between items-center">
            <h1 class="text-xl font-bold">{t['title']}</h1>
            <div class="flex items-center space-x-2 text-sm font-bold">
                <a href="/admin/pos" class="hover:bg-indigo-700 px-2 py-1 rounded">{t['pos']}</a>
                <a href="/admin/appointments" class="hover:bg-indigo-700 px-2 py-1 rounded">{t['app_mgmt']}</a>
                <a href="/admin/customers" class="hover:bg-indigo-700 px-2 py-1 rounded">{t['cust_mgmt']}</a>
                <a href="/admin/orders" class="hover:bg-indigo-700 px-2 py-1 rounded">{t['orders']}</a>
                <a href="/admin" class="hover:bg-indigo-700 px-2 py-1 rounded">{t['settings']}</a>
                <a href="/admin/reports" class="hover:bg-indigo-700 px-2 py-1 rounded">{t['reports']}</a>
                <a href="/admin/lang/toggle" class="bg-indigo-800 px-2.5 py-1 rounded hover:bg-indigo-900 border border-indigo-400">{other_lang_label}</a>
                <a href="/admin/logout" class="bg-red-500 px-2 py-1 rounded hover:bg-red-600">{t['logout']}</a>
            </div>
        </div>
    </nav>
    <main class="container mx-auto p-6">
        {{% block content %}}{{% endblock %}}
    </main>
</body>
</html>
"""

@app.route("/admin/lang/toggle")
def toggle_lang():
    current = session.get("lang", "zh")
    session["lang"] = "en" if current == "zh" else "zh"
    return redirect(request.referrer or url_for("admin_dashboard"))

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        if hmac.compare_digest(password, ADMIN_PASSWORD):
            session["is_admin"] = True
            return redirect(url_for("admin_dashboard"))
        error = "Password error, default: 123456"
    return render_template_string("""
        <div style="max-width:400px;margin:100px auto;padding:20px;border:1px solid #ccc;text-align:center;font-family:sans-serif;border-radius:8px;">
            <h2>Dew Hair Salon Admin</h2>
            {% if error %}<p style="color:red;">{{ error }}</p>{% endif %}
            <form method="POST">
                <input type="password" name="password" placeholder="Password (Default: 123456)" style="width:100%;padding:10px;margin:10px 0;box-sizing:border-box;" required>
                <button style="width:100%;padding:10px;background:#4f46e5;color:white;border:none;border-radius:4px;font-weight:bold;cursor:pointer;">Login</button>
            </form>
        </div>
    """, error=error)

@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("admin_login"))

@app.route("/admin")
@admin_required
def admin_dashboard():
    lang = get_lang()
    open_time = get_setting("open_time", "10:00")
    close_time = get_setting("close_time", "20:00")
    closed_wd = get_setting("closed_weekdays", "1")
    
    with get_db() as conn:
        services = conn.execute("SELECT * FROM services ORDER BY category_type, sub_category").fetchall()
        stylists = conn.execute("SELECT * FROM stylists ORDER BY id DESC").fetchall()
        holidays = conn.execute("SELECT * FROM holidays ORDER BY date_str DESC").fetchall()

    content_zh = """
    <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div class="md:col-span-2 space-y-6">
            <div class="bg-white p-6 rounded shadow">
                <h2 class="text-xl font-bold mb-4">营业与休息日设置</h2>
                <form action="/admin/settings/update" method="POST" class="grid grid-cols-2 md:grid-cols-4 gap-4 items-end mb-4">
                    <div><label class="block text-sm font-medium">开门时间</label><input type="time" name="open_time" value="{{ open_time }}" class="w-full border rounded p-2" required></div>
                    <div><label class="block text-sm font-medium">关门时间</label><input type="time" name="close_time" value="{{ close_time }}" class="w-full border rounded p-2" required></div>
                    <div><label class="block text-sm font-medium">每周固定休息日</label>
                        <select name="closed_weekdays" class="w-full border rounded p-2">
                            <option value="0" {% if closed_wd == '0' %}selected{% endif %}>周日休息</option>
                            <option value="1" {% if closed_wd == '1' %}selected{% endif %}>周一休息</option>
                            <option value="2" {% if closed_wd == '2' %}selected{% endif %}>周二休息</option>
                            <option value="-1" {% if closed_wd == '-1' %}selected{% endif %}>无固定休息日</option>
                        </select>
                    </div>
                    <div><button class="w-full bg-indigo-600 text-white font-bold py-2 rounded hover:bg-indigo-700">保存设置</button></div>
                </form>
                <hr class="my-4">
                <h3 class="font-bold mb-2">添加特定临时休息日</h3>
                <form action="/admin/holiday/add" method="POST" class="flex gap-2">
                    <input type="date" name="date_str" class="border rounded p-2" required>
                    <input type="text" name="reason" placeholder="休息原因" class="border rounded p-2 flex-grow">
                    <button class="bg-red-500 text-white px-4 py-2 rounded font-bold hover:bg-red-600">添加闭店日</button>
                </form>
                <div class="mt-4 flex flex-wrap gap-2">
                    {% for h in holidays %}
                    <span class="bg-red-50 text-red-700 px-3 py-1 rounded border border-red-200 text-sm flex items-center gap-2">
                        {{ h.date_str }} ({{ h.reason }})
                        <a href="/admin/holiday/delete/{{ h.id }}" class="font-bold hover:text-red-900">×</a>
                    </span>
                    {% endfor %}
                </div>
            </div>

            <div class="bg-white p-6 rounded shadow">
                <h2 class="text-xl font-bold mb-4">发型师团队管理</h2>
                <table class="w-full text-left">
                    <thead><tr class="border-b"><th class="p-2">姓名</th><th class="p-2">头衔</th><th class="p-2">操作</th></tr></thead>
                    <tbody>
                        {% for st in stylists %}
                        <tr class="border-b"><td class="p-2 font-bold text-indigo-600">{{ st.name }}</td><td class="p-2">{{ st.title }}</td><td class="p-2"><a href="/admin/stylist/delete/{{ st.id }}" onclick="return confirm('确定删除？')" class="text-red-500 font-bold">删除</a></td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>

            <div class="bg-white p-6 rounded shadow">
                <h2 class="text-xl font-bold mb-4">项目与套餐管理</h2>
                <table class="w-full text-left">
                    <thead><tr class="border-b"><th class="p-2">分类</th><th class="p-2">名称</th><th class="p-2">售价</th><th class="p-2">赠送Credit</th><th class="p-2">操作</th></tr></thead>
                    <tbody>
                        {% for item in services %}
                        <tr class="border-b">
                            <td class="p-2 font-bold text-indigo-600">{{ item.category_type }}</td>
                            <td class="p-2">{{ item.name }}</td>
                            <td class="p-2 font-bold text-red-600">RM {{ "%.2f"|format(item.price) }}</td>
                            <td class="p-2 font-bold text-green-600">{% if item.category_type == 'Packages' %}RM {{ "%.2f"|format(item.credit_value) }}{% else %}-{% endif %}</td>
                            <td class="p-2"><a href="/admin/service/delete/{{ item.id }}" onclick="return confirm('确定删除？')" class="text-red-500 font-bold">删除</a></td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>

        <div class="space-y-6">
            <div class="bg-white p-6 rounded shadow">
                <h2 class="text-xl font-bold mb-4">添加发型师</h2>
                <form action="/admin/stylist/add" method="POST">
                    <div class="mb-3"><label class="block text-sm font-medium">姓名</label><input type="text" name="name" class="w-full border rounded p-2" required></div>
                    <div class="mb-3"><label class="block text-sm font-medium">头衔</label><input type="text" name="title" class="w-full border rounded p-2" required></div>
                    <button class="w-full bg-indigo-600 text-white font-bold py-2 rounded">确认添加</button>
                </form>
            </div>

            <div class="bg-white p-6 rounded shadow">
                <h2 class="text-xl font-bold mb-4">添加服务或套餐</h2>
                <form action="/admin/service/add" method="POST">
                    <div class="mb-3"><label class="block text-sm font-medium">名称</label><input type="text" name="name" class="w-full border rounded p-2" required></div>
                    <div class="mb-3"><label class="block text-sm font-medium">分类</label>
                        <select name="category_type" id="cat_select" onchange="toggleCredit(this)" class="w-full border rounded p-2">
                            <option value="Services">Services (服务)</option>
                            <option value="Packages">Packages (套餐)</option>
                            <option value="Products">Products (产品)</option>
                        </select>
                    </div>
                    <div class="mb-3"><label class="block text-sm font-medium">售价 (RM)</label><input type="number" step="0.01" name="price" class="w-full border rounded p-2" required></div>
                    <div class="mb-3" id="credit_div" style="display:none;"><label class="block text-sm font-medium text-green-600 font-bold">赠送 Credit (RM)</label><input type="number" step="0.01" name="credit_value" class="w-full border rounded p-2"></div>
                    <div class="mb-4"><label class="block text-sm font-medium">耗时 (分钟)</label><input type="number" name="duration" class="w-full border rounded p-2" value="30" required></div>
                    <button class="w-full bg-indigo-600 text-white font-bold py-2 rounded">确认添加</button>
                </form>
            </div>
        </div>
    </div>
    <script>function toggleCredit(sel){document.getElementById('credit_div').style.display = sel.value === 'Packages' ? 'block' : 'none';}</script>
    """

    content_en = """
    <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div class="md:col-span-2 space-y-6">
            <div class="bg-white p-6 rounded shadow">
                <h2 class="text-xl font-bold mb-4">Business Hours & Closures</h2>
                <form action="/admin/settings/update" method="POST" class="grid grid-cols-2 md:grid-cols-4 gap-4 items-end mb-4">
                    <div><label class="block text-sm font-medium">Open Time</label><input type="time" name="open_time" value="{{ open_time }}" class="w-full border rounded p-2" required></div>
                    <div><label class="block text-sm font-medium">Close Time</label><input type="time" name="close_time" value="{{ close_time }}" class="w-full border rounded p-2" required></div>
                    <div><label class="block text-sm font-medium">Fixed Weekly Rest Day</label>
                        <select name="closed_weekdays" class="w-full border rounded p-2">
                            <option value="0" {% if closed_wd == '0' %}selected{% endif %}>Sunday</option>
                            <option value="1" {% if closed_wd == '1' %}selected{% endif %}>Monday</option>
                            <option value="2" {% if closed_wd == '2' %}selected{% endif %}>Tuesday</option>
                            <option value="-1" {% if closed_wd == '-1' %}selected{% endif %}>None</option>
                        </select>
                    </div>
                    <div><button class="w-full bg-indigo-600 text-white font-bold py-2 rounded hover:bg-indigo-700">Save Settings</button></div>
                </form>
                <hr class="my-4">
                <h3 class="font-bold mb-2">Add Specific Temporary Holiday</h3>
                <form action="/admin/holiday/add" method="POST" class="flex gap-2">
                    <input type="date" name="date_str" class="border rounded p-2" required>
                    <input type="text" name="reason" placeholder="Reason (e.g. Holiday)" class="border rounded p-2 flex-grow">
                    <button class="bg-red-500 text-white px-4 py-2 rounded font-bold hover:bg-red-600">Add Holiday</button>
                </form>
                <div class="mt-4 flex flex-wrap gap-2">
                    {% for h in holidays %}
                    <span class="bg-red-50 text-red-700 px-3 py-1 rounded border border-red-200 text-sm flex items-center gap-2">
                        {{ h.date_str }} ({{ h.reason }})
                        <a href="/admin/holiday/delete/{{ h.id }}" class="font-bold hover:text-red-900">×</a>
                    </span>
                    {% endfor %}
                </div>
            </div>

            <div class="bg-white p-6 rounded shadow">
                <h2 class="text-xl font-bold mb-4">Stylist Team Management</h2>
                <table class="w-full text-left">
                    <thead><tr class="border-b"><th class="p-2">Name</th><th class="p-2">Title</th><th class="p-2">Action</th></tr></thead>
                    <tbody>
                        {% for st in stylists %}
                        <tr class="border-b"><td class="p-2 font-bold text-indigo-600">{{ st.name }}</td><td class="p-2">{{ st.title }}</td><td class="p-2"><a href="/admin/stylist/delete/{{ st.id }}" onclick="return confirm('Delete?')" class="text-red-500 font-bold">Delete</a></td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>

            <div class="bg-white p-6 rounded shadow">
                <h2 class="text-xl font-bold mb-4">Services & Packages Management</h2>
                <table class="w-full text-left">
                    <thead><tr class="border-b"><th class="p-2">Category</th><th class="p-2">Name</th><th class="p-2">Price</th><th class="p-2">Credit Value</th><th class="p-2">Action</th></tr></thead>
                    <tbody>
                        {% for item in services %}
                        <tr class="border-b">
                            <td class="p-2 font-bold text-indigo-600">{{ item.category_type }}</td>
                            <td class="p-2">{{ item.name }}</td>
                            <td class="p-2 font-bold text-red-600">RM {{ "%.2f"|format(item.price) }}</td>
                            <td class="p-2 font-bold text-green-600">{% if item.category_type == 'Packages' %}RM {{ "%.2f"|format(item.credit_value) }}{% else %}-{% endif %}</td>
                            <td class="p-2"><a href="/admin/service/delete/{{ item.id }}" onclick="return confirm('Delete?')" class="text-red-500 font-bold">Delete</a></td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>

        <div class="space-y-6">
            <div class="bg-white p-6 rounded shadow">
                <h2 class="text-xl font-bold mb-4">Add Stylist</h2>
                <form action="/admin/stylist/add" method="POST">
                    <div class="mb-3"><label class="block text-sm font-medium">Name</label><input type="text" name="name" class="w-full border rounded p-2" required></div>
                    <div class="mb-3"><label class="block text-sm font-medium">Title</label><input type="text" name="title" class="w-full border rounded p-2" required></div>
                    <button class="w-full bg-indigo-600 text-white font-bold py-2 rounded">Add Stylist</button>
                </form>
            </div>

            <div class="bg-white p-6 rounded shadow">
                <h2 class="text-xl font-bold mb-4">Add Service or Package</h2>
                <form action="/admin/service/add" method="POST">
                    <div class="mb-3"><label class="block text-sm font-medium">Name</label><input type="text" name="name" class="w-full border rounded p-2" required></div>
                    <div class="mb-3"><label class="block text-sm font-medium">Category</label>
                        <select name="category_type" id="cat_select" onchange="toggleCredit(this)" class="w-full border rounded p-2">
                            <option value="Services">Services</option>
                            <option value="Packages">Packages</option>
                            <option value="Products">Products</option>
                        </select>
                    </div>
                    <div class="mb-3"><label class="block text-sm font-medium">Price (RM)</label><input type="number" step="0.01" name="price" class="w-full border rounded p-2" required></div>
                    <div class="mb-3" id="credit_div" style="display:none;"><label class="block text-sm font-medium text-green-600 font-bold">Bonus Credit (RM)</label><input type="number" step="0.01" name="credit_value" class="w-full border rounded p-2"></div>
                    <div class="mb-4"><label class="block text-sm font-medium">Duration (Minutes)</label><input type="number" name="duration" class="w-full border rounded p-2" value="30" required></div>
                    <button class="w-full bg-indigo-600 text-white font-bold py-2 rounded">Add Service</button>
                </form>
            </div>
        </div>
    </div>
    <script>function toggleCredit(sel){document.getElementById('credit_div').style.display = sel.value === 'Packages' ? 'block' : 'none';}</script>
    """

    content = content_en if lang == "en" else content_zh
    full_html = get_layout().replace("{% block content %}{% endblock %}", content)
    return render_template_string(full_html, services=services, stylists=stylists, holidays=holidays, open_time=open_time, close_time=close_time, closed_wd=closed_wd)

@app.route("/admin/stylist/add", methods=["POST"])
@admin_required
def add_stylist():
    name = request.form.get("name")
    title = request.form.get("title")
    with get_db() as conn:
        try:
            conn.execute("INSERT INTO stylists (name, title) VALUES (?, ?)", (name, title))
        except:
            pass
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/stylist/delete/<int:id>")
@admin_required
def delete_stylist(id):
    with get_db() as conn:
        conn.execute("DELETE FROM stylists WHERE id = ?", (id,))
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/settings/update", methods=["POST"])
@admin_required
def update_settings():
    open_time = request.form.get("open_time", "10:00")
    close_time = request.form.get("close_time", "20:00")
    closed_weekdays = request.form.get("closed_weekdays", "1")
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('open_time', ?)", (open_time,))
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('close_time', ?)", (close_time,))
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('closed_weekdays', ?)", (closed_weekdays,))
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/holiday/add", methods=["POST"])
@admin_required
def add_holiday():
    date_str = request.form.get("date_str")
    reason = request.form.get("reason", "Holiday")
    with get_db() as conn:
        try:
            conn.execute("INSERT INTO holidays (date_str, reason) VALUES (?, ?)", (date_str, reason))
        except:
            pass
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/holiday/delete/<int:id>")
@admin_required
def delete_holiday(id):
    with get_db() as conn:
        conn.execute("DELETE FROM holidays WHERE id = ?", (id,))
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/service/add", methods=["POST"])
@admin_required
def add_service():
    name = request.form.get("name")
    cat = request.form.get("category_type")
    price = float(request.form.get("price", 0))
    duration = int(request.form.get("duration", 30))
    credit_value = float(request.form.get("credit_value", 0)) if cat == 'Packages' else 0.0
    with get_db() as conn:
        conn.execute("""
            INSERT INTO services (name, category_type, sub_category, price, duration, credit_value)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (name, cat, cat, price, duration, credit_value))
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/service/delete/<int:id>")
@admin_required
def delete_service(id):
    with get_db() as conn:
        conn.execute("DELETE FROM services WHERE id = ?", (id,))
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/customers")
@admin_required
def admin_customers():
    lang = get_lang()
    search_query = request.args.get("q", "").strip()
    with get_db() as conn:
        if search_query:
            customers = conn.execute("""
                SELECT c.*, 
                       (SELECT COUNT(*) FROM appointments WHERE customer_id = c.id) as app_count,
                       (SELECT COUNT(*) FROM orders WHERE customer_id = c.id AND status = 'NORMAL') as order_count
                FROM customers c 
                WHERE c.name LIKE ? OR c.phone LIKE ?
                ORDER BY c.id DESC
            """, (f"%{search_query}%", f"%{search_query}%")).fetchall()
        else:
            customers = conn.execute("""
                SELECT c.*, 
                       (SELECT COUNT(*) FROM appointments WHERE customer_id = c.id) as app_count,
                       (SELECT COUNT(*) FROM orders WHERE customer_id = c.id AND status = 'NORMAL') as order_count
                FROM customers c 
                ORDER BY c.id DESC
            """).fetchall()

    content_zh = f"""
    <div class="bg-white p-6 rounded shadow">
        <div class="flex justify-between items-center mb-4">
            <h2 class="text-xl font-bold">会员与历史记录管理</h2>
            <form method="GET" class="flex gap-2">
                <input type="text" name="q" value="{search_query}" placeholder="搜姓名或电话" class="border rounded p-2 text-sm">
                <button class="bg-indigo-600 text-white px-4 py-2 rounded text-sm font-bold">搜索</button>
            </form>
        </div>
        <table class="w-full text-left">
            <thead><tr class="border-b"><th class="p-2">姓名</th><th class="p-2">电话</th><th class="p-2">余额 (Credits)</th><th class="p-2">预约数</th><th class="p-2">订单数</th><th class="p-2">操作</th></tr></thead>
            <tbody>
                {{% for c in customers %}}
                <tr class="border-b">
                    <td class="p-2 font-bold text-indigo-600">{{ c.name }}</td>
                    <td class="p-2">{{ c.phone }}</td>
                    <td class="p-2 font-bold text-green-600">RM {{ "%.2f"|format(c.credits) }}</td>
                    <td class="p-2">{{ c.app_count }}</td>
                    <td class="p-2">{{ c.order_count }}</td>
                    <td class="p-2"><a href="/admin/customer/detail/{{ c.id }}" class="text-indigo-600 font-bold hover:underline">查看详情 / 调整余额</a></td>
                </tr>
                {{% endfor %}}
            </tbody>
        </table>
    </div>
    """

    content_en = f"""
    <div class="bg-white p-6 rounded shadow">
        <div class="flex justify-between items-center mb-4">
            <h2 class="text-xl font-bold">Members & History Management</h2>
            <form method="GET" class="flex gap-2">
                <input type="text" name="q" value="{search_query}" placeholder="Search name/phone" class="border rounded p-2 text-sm">
                <button class="bg-indigo-600 text-white px-4 py-2 rounded text-sm font-bold">Search</button>
            </form>
        </div>
        <table class="w-full text-left">
            <thead><tr class="border-b"><th class="p-2">Name</th><th class="p-2">Phone</th><th class="p-2">Credits</th><th class="p-2">Appointments</th><th class="p-2">Orders</th><th class="p-2">Action</th></tr></thead>
            <tbody>
                {{% for c in customers %}}
                <tr class="border-b">
                    <td class="p-2 font-bold text-indigo-600">{{ c.name }}</td>
                    <td class="p-2">{{ c.phone }}</td>
                    <td class="p-2 font-bold text-green-600">RM {{ "%.2f"|format(c.credits) }}</td>
                    <td class="p-2">{{ c.app_count }}</td>
                    <td class="p-2">{{ c.order_count }}</td>
                    <td class="p-2"><a href="/admin/customer/detail/{{ c.id }}" class="text-indigo-600 font-bold hover:underline">View / Adjust Credits</a></td>
                </tr>
                {{% endfor %}}
            </tbody>
        </table>
    </div>
    """

    content = content_en if lang == "en" else content_zh
    full_html = get_layout().replace("{% block content %}{% endblock %}", content)
    return render_template_string(full_html, customers=customers)

@app.route("/admin/customer/detail/<int:customer_id>", methods=["GET", "POST"])
@admin_required
def admin_customer_detail(customer_id):
    lang = get_lang()
    with get_db() as conn:
        if request.method == "POST":
            action = request.form.get("action")
            if action == "adjust":
                delta = float(request.form.get("delta", 0))
                conn.execute("UPDATE customers SET credits = credits + ? WHERE id = ?", (delta, customer_id))
            return redirect(url_for("admin_customer_detail", customer_id=customer_id))

        customer = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        appointments = conn.execute("SELECT a.*, s.name as service_name FROM appointments a JOIN services s ON a.service_id = s.id WHERE a.customer_id = ? ORDER BY a.id DESC", (customer_id,)).fetchall()
        orders = conn.execute("SELECT * FROM orders WHERE customer_id = ? ORDER BY id DESC", (customer_id,)).fetchall()

    content_zh = """
    <div class="space-y-6">
        <div class="bg-white p-6 rounded shadow flex justify-between items-center">
            <div>
                <h2 class="text-xl font-bold mb-1">{{ customer.name }} ({{ customer.phone }})</h2>
                <p class="text-gray-600">账户余额: <span class="font-bold text-green-600">RM {{ "%.2f"|format(customer.credits) }}</span></p>
            </div>
            <form method="POST" class="flex gap-2 items-center">
                <input type="hidden" name="action" value="adjust">
                <input type="number" step="0.01" name="delta" placeholder="增减额 (+/-)" class="border rounded p-2 text-sm" required>
                <button class="bg-indigo-600 text-white px-4 py-2 rounded text-sm font-bold">调整余额</button>
            </form>
        </div>

        <div class="bg-white p-6 rounded shadow">
            <h3 class="text-lg font-bold mb-3">预约记录</h3>
            <table class="w-full text-left">
                <thead><tr class="border-b"><th class="p-2">服务</th><th class="p-2">发型师</th><th class="p-2">时间</th><th class="p-2">状态</th></tr></thead>
                <tbody>
                    {% for a in appointments %}
                    <tr class="border-b"><td class="p-2">{{ a.service_name }}</td><td class="p-2">{{ a.stylist }}</td><td class="p-2">{{ a.start_time }}</td><td class="p-2">{{ a.status }}</td></tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>

        <div class="bg-white p-6 rounded shadow">
            <h3 class="text-lg font-bold mb-3">订单消费记录</h3>
            <table class="w-full text-left">
                <thead><tr class="border-b"><th class="p-2">订单号</th><th class="p-2">金额</th><th class="p-2">支付详情</th><th class="p-2">时间</th></tr></thead>
                <tbody>
                    {% for o in orders %}
                    <tr class="border-b"><td class="p-2">{{ o.order_no }}</td><td class="p-2 font-bold text-red-600">RM {{ "%.2f"|format(o.total_amount) }}</td><td class="p-2">{{ o.payment_details }}</td><td class="p-2">{{ o.created_at }}</td></tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
    """

    content_en = """
    <div class="space-y-6">
        <div class="bg-white p-6 rounded shadow flex justify-between items-center">
            <div>
                <h2 class="text-xl font-bold mb-1">{{ customer.name }} ({{ customer.phone }})</h2>
                <p class="text-gray-600">Credits: <span class="font-bold text-green-600">RM {{ "%.2f"|format(customer.credits) }}</span></p>
            </div>
            <form method="POST" class="flex gap-2 items-center">
                <input type="hidden" name="action" value="adjust">
                <input type="number" step="0.01" name="delta" placeholder="Amount (+/-)" class="border rounded p-2 text-sm" required>
                <button class="bg-indigo-600 text-white px-4 py-2 rounded text-sm font-bold">Adjust Credits</button>
            </form>
        </div>

        <div class="bg-white p-6 rounded shadow">
            <h3 class="text-lg font-bold mb-3">Appointments</h3>
            <table class="w-full text-left">
                <thead><tr class="border-b"><th class="p-2">Service</th><th class="p-2">Stylist</th><th class="p-2">Time</th><th class="p-2">Status</th></tr></thead>
                <tbody>
                    {% for a in appointments %}
                    <tr class="border-b"><td class="p-2">{{ a.service_name }}</td><td class="p-2">{{ a.stylist }}</td><td class="p-2">{{ a.start_time }}</td><td class="p-2">{{ a.status }}</td></tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>

        <div class="bg-white p-6 rounded shadow">
            <h3 class="text-lg font-bold mb-3">Orders</h3>
            <table class="w-full text-left">
                <thead><tr class="border-b"><th class="p-2">Order No</th><th class="p-2">Total</th><th class="p-2">Payment</th><th class="p-2">Time</th></tr></thead>
                <tbody>
                    {% for o in orders %}
                    <tr class="border-b"><td class="p-2">{{ o.order_no }}</td><td class="p-2 font-bold text-red-600">RM {{ "%.2f"|format(o.total_amount) }}</td><td class="p-2">{{ o.payment_details }}</td><td class="p-2">{{ o.created_at }}</td></tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
    """

    content = content_en if lang == "en" else content_zh
    full_html = get_layout().replace("{% block content %}{% endblock %}", content)
    return render_template_string(full_html, customer=customer, appointments=appointments, orders=orders)

@app.route("/admin/appointments")
@admin_required
def admin_appointments():
    lang = get_lang()
    date_str = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))
    with get_db() as conn:
        appointments = conn.execute("""
            SELECT a.*, c.name as customer_name, c.phone as customer_phone, s.name as service_name 
            FROM appointments a 
            JOIN customers c ON a.customer_id = c.id 
            JOIN services s ON a.service_id = s.id 
            WHERE a.start_time LIKE ? 
            ORDER BY a.start_time ASC
        """, (f"{date_str}%",)).fetchall()
        stylists = conn.execute("SELECT * FROM stylists").fetchall()

    content_zh = f"""
    <div class="bg-white p-6 rounded shadow">
        <div class="flex justify-between items-center mb-4">
            <h2 class="text-xl font-bold">预约管理</h2>
            <form method="GET" class="flex gap-2">
                <input type="date" name="date" value="{date_str}" class="border rounded p-2 text-sm">
                <button class="bg-indigo-600 text-white px-4 py-2 rounded text-sm font-bold">查看日期</button>
            </form>
        </div>
        <table class="w-full text-left">
            <thead><tr class="border-b"><th class="p-2">时间</th><th class="p-2">客户姓名</th><th class="p-2">电话</th><th class="p-2">服务</th><th class="p-2">发型师</th><th class="p-2">状态</th><th class="p-2">操作</th></tr></thead>
            <tbody>
                {{% for a in appointments %}}
                <tr class="border-b">
                    <td class="p-2 font-bold">{{ a.start_time }}</td>
                    <td class="p-2 font-bold text-indigo-600">{{ a.customer_name }}</td>
                    <td class="p-2">{{ a.customer_phone }}</td>
                    <td class="p-2">{{ a.service_name }}</td>
                    <td class="p-2">{{ a.stylist }}</td>
                    <td class="p-2">{{ a.status }}</td>
                    <td class="p-2"><a href="/admin/appointment/delete/{{ a.id }}" onclick="return confirm('取消预约？')" class="text-red-500 font-bold">取消</a></td>
                </tr>
                {{% endfor %}}
            </tbody>
        </table>
    </div>
    """

    content_en = f"""
    <div class="bg-white p-6 rounded shadow">
        <div class="flex justify-between items-center mb-4">
            <h2 class="text-xl font-bold">Appointments Management</h2>
            <form method="GET" class="flex gap-2">
                <input type="date" name="date" value="{date_str}" class="border rounded p-2 text-sm">
                <button class="bg-indigo-600 text-white px-4 py-2 rounded text-sm font-bold">View Date</button>
            </form>
        </div>
        <table class="w-full text-left">
            <thead><tr class="border-b"><th class="p-2">Time</th><th class="p-2">Customer</th><th class="p-2">Phone</th><th class="p-2">Service</th><th class="p-2">Stylist</th><th class="p-2">Status</th><th class="p-2">Action</th></tr></thead>
            <tbody>
                {{% for a in appointments %}}
                <tr class="border-b">
                    <td class="p-2 font-bold">{{ a.start_time }}</td>
                    <td class="p-2 font-bold text-indigo-600">{{ a.customer_name }}</td>
                    <td class="p-2">{{ a.customer_phone }}</td>
                    <td class="p-2">{{ a.service_name }}</td>
                    <td class="p-2">{{ a.stylist }}</td>
                    <td class="p-2">{{ a.status }}</td>
                    <td class="p-2"><a href="/admin/appointment/delete/{{ a.id }}" onclick="return confirm('Cancel appointment?')" class="text-red-500 font-bold">Cancel</a></td>
                </tr>
                {{% endfor %}}
            </tbody>
        </table>
    </div>
    """

    content = content_en if lang == "en" else content_zh
    full_html = get_layout().replace("{% block content %}{% endblock %}", content)
    return render_template_string(full_html, appointments=appointments)

@app.route("/admin/appointment/delete/<int:id>")
@admin_required
def delete_appointment(id):
    with get_db() as conn:
        conn.execute("DELETE FROM appointments WHERE id = ?", (id,))
    return redirect(request.referrer or url_for("admin_appointments"))

@app.route("/admin/orders")
@admin_required
def admin_orders():
    lang = get_lang()
    with get_db() as conn:
        orders = conn.execute("""
            SELECT o.*, c.name as customer_name, c.phone as customer_phone 
            FROM orders o 
            JOIN customers c ON o.customer_id = c.id 
            ORDER BY o.id DESC LIMIT 100
        """).fetchall()

    content_zh = """
    <div class="bg-white p-6 rounded shadow">
        <h2 class="text-xl font-bold mb-4">历史订单</h2>
        <table class="w-full text-left">
            <thead><tr class="border-b"><th class="p-2">订单号</th><th class="p-2">客户姓名</th><th class="p-2">电话</th><th class="p-2">总金额</th><th class="p-2">支付详情</th><th class="p-2">时间</th></tr></thead>
            <tbody>
                {% for o in orders %}
                <tr class="border-b">
                    <td class="p-2 font-bold">{{ o.order_no }}</td>
                    <td class="p-2 font-bold text-indigo-600">{{ o.customer_name }}</td>
                    <td class="p-2">{{ o.customer_phone }}</td>
                    <td class="p-2 font-bold text-red-600">RM {{ "%.2f"|format(o.total_amount) }}</td>
                    <td class="p-2">{{ o.payment_details }}</td>
                    <td class="p-2">{{ o.created_at }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    """

    content_en = """
    <div class="bg-white p-6 rounded shadow">
        <h2 class="text-xl font-bold mb-4">Order History</h2>
        <table class="w-full text-left">
            <thead><tr class="border-b"><th class="p-2">Order No</th><th class="p-2">Customer</th><th class="p-2">Phone</th><th class="p-2">Total</th><th class="p-2">Payment Details</th><th class="p-2">Time</th></tr></thead>
            <tbody>
                {% for o in orders %}
                <tr class="border-b">
                    <td class="p-2 font-bold">{{ o.order_no }}</td>
                    <td class="p-2 font-bold text-indigo-600">{{ o.customer_name }}</td>
                    <td class="p-2">{{ o.customer_phone }}</td>
                    <td class="p-2 font-bold text-red-600">RM {{ "%.2f"|format(o.total_amount) }}</td>
                    <td class="p-2">{{ o.payment_details }}</td>
                    <td class="p-2">{{ o.created_at }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    """

    content = content_en if lang == "en" else content_zh
    full_html = get_layout().replace("{% block content %}{% endblock %}", content)
    return render_template_string(full_html, orders=orders)

@app.route("/admin/reports")
@admin_required
def admin_reports():
    lang = get_lang()
    with get_db() as conn:
        stats = conn.execute("""
            SELECT date(created_at) as dt, SUM(total_amount) as total, COUNT(*) as count 
            FROM orders 
            WHERE status = 'NORMAL' 
            GROUP BY date(created_at) 
            ORDER BY dt DESC LIMIT 90
        """).fetchall()

    content_zh = """
    <div class="bg-white p-6 rounded shadow">
        <h2 class="text-xl font-bold mb-4">近90天营业报表</h2>
        <table class="w-full text-left">
            <thead><tr class="border-b"><th class="p-2">日期</th><th class="p-2">订单数</th><th class="p-2">营业额</th></tr></thead>
            <tbody>
                {% for s in stats %}
                <tr class="border-b">
                    <td class="p-2 font-bold">{{ s.dt }}</td>
                    <td class="p-2">{{ s.count }}</td>
                    <td class="p-2 font-bold text-green-600">RM {{ "%.2f"|format(s.total) }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    """

    content_en = """
    <div class="bg-white p-6 rounded shadow">
        <h2 class="text-xl font-bold mb-4">90-Day Revenue Reports</h2>
        <table class="w-full text-left">
            <thead><tr class="border-b"><th class="p-2">Date</th><th class="p-2">Orders Count</th><th class="p-2">Revenue</th></tr></thead>
            <tbody>
                {% for s in stats %}
                <tr class="border-b">
                    <td class="p-2 font-bold">{{ s.dt }}</td>
                    <td class="p-2">{{ s.count }}</td>
                    <td class="p-2 font-bold text-green-600">RM {{ "%.2f"|format(s.total) }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    """

    content = content_en if lang == "en" else content_zh
    full_html = get_layout().replace("{% block content %}{% endblock %}", content)
    return render_template_string(full_html, stats=stats)

@app.route("/admin/pos", methods=["GET", "POST"])
@admin_required
def admin_pos():
    lang = get_lang()
    with get_db() as conn:
        services = conn.execute("SELECT * FROM services").fetchall()
        customers = conn.execute("SELECT * FROM customers ORDER BY id DESC").fetchall()

    if request.method == "POST":
        customer_id = request.form.get("customer_id")
        selected_services = request.form.getlist("service_ids")
        use_credit = request.form.get("use_credit") == "on"
        remark = request.form.get("remark", "")

        if not customer_id or not selected_services:
            return redirect(url_for("admin_pos"))

        with get_db() as conn:
            services_map = {str(s["id"]): s for s in conn.execute("SELECT * FROM services").fetchall()}
            customer = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()

            total_amount = sum(services_map[str(sid)]["price"] for sid in selected_services if str(sid) in services_map)
            
            pay_details_list = []
            final_paid = total_amount

            if use_credit and customer["credits"] > 0:
                deduct = min(customer["credits"], total_amount)
                conn.execute("UPDATE customers SET credits = credits - ? WHERE id = ?", (deduct, customer_id))
                pay_details_list.append(f"Credit Deducted: RM {deduct:.2f}")
                final_paid -= deduct

            if final_paid > 0:
                pay_details_list.append(f"Cash/Other Paid: RM {final_paid:.2f}")
            else:
                pay_details_list.append("Fully Paid by Credit")

            # Check if any service grants credit (Packages)
            total_bonus_credit = sum(services_map[str(sid)]["credit_value"] for sid in selected_services if str(sid) in services_map)
            if total_bonus_credit > 0:
                conn.execute("UPDATE customers SET credits = credits + ? WHERE id = ?", (total_bonus_credit, customer_id))
                pay_details_list.append(f"Bonus Credit Added: RM {total_bonus_credit:.2f}")

            order_no = "ORD" + datetime.now().strftime("%Y%m%d%H%M%S") + secrets.token_hex(2).upper()
            created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            payment_str = " | ".join(pay_details_list)

            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO orders (order_no, customer_id, total_amount, payment_details, remark, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'NORMAL', ?)
            """, (order_no, customer_id, total_amount, payment_str, remark, created_at))
            order_id = cursor.lastrowid

            for sid in selected_services:
                if str(sid) in services_map:
                    item = services_map[str(sid)]
                    cursor.execute("""
                        INSERT INTO order_items (order_id, item_name, price, qty)
                        VALUES (?, ?, ?, 1)
                    """, (order_id, item["name"], item["price"]))

        return redirect(url_for("admin_orders"))

    content_zh = """
    <div class="bg-white p-6 rounded shadow max-w-2xl mx-auto">
        <h2 class="text-xl font-bold mb-4">POS 收银台</h2>
        <form method="POST" class="space-y-4">
            <div>
                <label class="block text-sm font-medium mb-1">选择客户</label>
                <select name="customer_id" class="w-full border rounded p-2" required>
                    <option value="">-- 请选择客户 --</option>
                    {% for c in customers %}
                    <option value="{{ c.id }}">{{ c.name }} ({{ c.phone }}) - 余额: RM {{ "%.2f"|format(c.credits) }}</option>
                    {% endfor %}
                </select>
            </div>
            <div>
                <label class="block text-sm font-medium mb-1">选择服务 / 套餐 / 产品 (可多选)</label>
                <div class="max-h-60 overflow-y-auto border rounded p-2 space-y-2">
                    {% for s in services %}
                    <label class="flex items-center justify-between border-b pb-1">
                        <span class="flex items-center gap-2">
                            <input type="checkbox" name="service_ids" value="{{ s.id }}">
                            <span>{{ s.name }} <span class="text-gray-500 text-xs">({{ s.category_type }})</span></span>
                        </span>
                        <span class="font-bold text-red-600">RM {{ "%.2f"|format(s.price) }}</span>
                    </label>
                    {% endfor %}
                </div>
            </div>
            <div class="flex items-center gap-2">
                <input type="checkbox" name="use_credit" id="use_credit">
                <label for="use_credit" class="text-sm font-medium text-green-600">优先使用会员 Credit 抵扣</label>
            </div>
            <div>
                <label class="block text-sm font-medium mb-1">备注</label>
                <input type="text" name="remark" class="w-full border rounded p-2" placeholder="可选备注">
            </div>
            <button class="w-full bg-indigo-600 text-white font-bold py-2 rounded">结账生成订单</button>
        </form>
    </div>
    """

    content_en = """
    <div class="bg-white p-6 rounded shadow max-w-2xl mx-auto">
        <h2 class="text-xl font-bold mb-4">POS Checkout</h2>
        <form method="POST" class="space-y-4">
            <div>
                <label class="block text-sm font-medium mb-1">Select Customer</label>
                <select name="customer_id" class="w-full border rounded p-2" required>
                    <option value="">-- Select Customer --</option>
                    {% for c in customers %}
                    <option value="{{ c.id }}">{{ c.name }} ({{ c.phone }}) - Credit: RM {{ "%.2f"|format(c.credits) }}</option>
                    {% endfor %}
                </select>
            </div>
            <div>
                <label class="block text-sm font-medium mb-1">Select Services / Packages / Products</label>
                <div class="max-h-60 overflow-y-auto border rounded p-2 space-y-2">
                    {% for s in services %}
                    <label class="flex items-center justify-between border-b pb-1">
                        <span class="flex items-center gap-2">
                            <input type="checkbox" name="service_ids" value="{{ s.id }}">
                            <span>{{ s.name }} <span class="text-gray-500 text-xs">({{ s.category_type }})</span></span>
                        </span>
                        <span class="font-bold text-red-600">RM {{ "%.2f"|format(s.price) }}</span>
                    </label>
                    {% endfor %}
                </div>
            </div>
            <div class="flex items-center gap-2">
                <input type="checkbox" name="use_credit" id="use_credit">
                <label for="use_credit" class="text-sm font-medium text-green-600">Use Customer Credit</label>
            </div>
            <div>
                <label class="block text-sm font-medium mb-1">Remark</label>
                <input type="text" name="remark" class="w-full border rounded p-2" placeholder="Optional remark">
            </div>
            <button class="w-full bg-indigo-600 text-white font-bold py-2 rounded">Complete Checkout</button>
        </form>
    </div>
    """

    content = content_en if lang == "en" else content_zh
    full_html = get_layout().replace("{% block content %}{% endblock %}", content)
    return render_template_string(full_html, services=services, customers=customers)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
