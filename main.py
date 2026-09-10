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
                            {{ h.date_str }} ({{ h.reason }}) <a href="/admin/holiday/delete/{{ h.id }}" class="font-bold hover:text-red-900">×</a>
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
                            <tr class="border-b"><td class="p-2 font-bold text-indigo-600">{{ item.category_type }}</td><td class="p-2">{{ item.name }}</td><td class="p-2 font-bold text-red-600">RM {{ "%.2f"|format(item.price) }}</td><td class="p-2 font-bold text-green-600">{% if item.category_type == 'Packages' %}RM {{ "%.2f"|format(item.credit_value) }}{% else %}-{% endif %}</td><td class="p-2"><a href="/admin/service/delete/{{ item.id }}" onclick="return confirm('确定删除？')" class="text-red-500 font-bold">删除</a></td></tr>
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
                            {{ h.date_str }} ({{ h.reason }}) <a href="/admin/holiday/delete/{{ h.id }}" class="font-bold hover:text-red-900">×</a>
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
                            <tr class="border-b"><td class="p-2 font-bold text-indigo-600">{{ item.category_type }}</td><td class="p-2">{{ item.name }}</td><td class="p-2 font-bold text-red-600">RM {{ "%.2f"|format(item.price) }}</td><td class="p-2 font-bold text-green-600">{% if item.category_type == 'Packages' %}RM {{ "%.2f"|format(item.credit_value) }}{% else %}-{% endif %}</td><td class="p-2"><a href="/admin/service/delete/{{ item.id }}" onclick="return confirm('Delete?')" class="text-red-500 font-bold">Delete</a></td></tr>
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
    return render_template_string(get_layout().replace("{% block content %}{% endblock %}", content), services=services, stylists=stylists, holidays=holidays, open_time=open_time, close_time=close_time, closed_wd=closed_wd)

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
            
    content_zh = """
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div class="md:col-span-2 bg-white p-6 rounded shadow">
                <div class="flex flex-col md:flex-row justify-between items-start md:items-center mb-4 gap-3">
                    <h2 class="text-xl font-bold">会员列表与消费档案</h2>
                    <form action="/admin/customers" method="GET" class="flex gap-2 w-full md:w-auto">
                        <input type="text" name="q" value="{{ search_query }}" placeholder="搜姓名或手机号..." class="border rounded px-3 py-1 text-sm flex-grow">
                        <button class="bg-indigo-600 text-white px-3 py-1 rounded text-sm font-bold">搜索</button>
                        {% if search_query %}<a href="/admin/customers" class="bg-gray-300 text-gray-700 px-3 py-1 rounded text-sm font-bold flex items-center">重置</a>{% endif %}
                    </form>
                </div>
                <table class="w-full text-left border-collapse">
                    <thead><tr class="border-b bg-gray-50 text-sm"><th class="p-2">姓名</th><th class="p-2">电话</th><th class="p-2">Credit 余额</th><th class="p-2">专属链接</th><th class="p-2">操作</th></tr></thead>
                    <tbody>
                        {% for c in customers %}
                        <tr class="border-b hover:bg-gray-50">
                            <td class="p-2 font-bold">{{ c.name }}</td><td class="p-2">{{ c.phone }}</td><td class="p-2 text-green-600 font-bold">RM {{ "%.2f"|format(c.credits) }}</td>
                            <td class="p-2"><a href="/customer/{{ c.token }}" target="_blank" class="text-indigo-600 underline text-sm font-bold">查看页面</a></td>
                            <td class="p-2"><a href="/admin/customer/detail/{{ c.id }}" class="bg-indigo-600 text-white px-3 py-1 rounded text-xs font-bold hover:bg-indigo-700">查看消费与预约记录</a></td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            <div class="bg-white p-6 rounded shadow h-fit">
                <h2 class="text-xl font-bold mb-4">手动添加会员档案</h2>
                <form action="/admin/customer/add" method="POST">
                    <div class="mb-3"><label class="block text-sm font-medium">会员姓名</label><input type="text" name="name" class="w-full border rounded p-2" required></div>
                    <div class="mb-3"><label class="block text-sm font-medium">电话号码</label><input type="text" name="phone" class="w-full border rounded p-2" required></div>
                    <div class="mb-4"><label class="block text-sm font-medium">初始 Credit</label><input type="number" step="0.01" name="credits" class="w-full border rounded p-2" value="0.00"></div>
                    <button class="w-full bg-indigo-600 text-white font-bold py-2 rounded">保存会员</button>
                </form>
            </div>
        </div>
    """

    content_en = """
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div class="md:col-span-2 bg-white p-6 rounded shadow">
                <div class="flex flex-col md:flex-row justify-between items-start md:items-center mb-4 gap-3">
                    <h2 class="text-xl font-bold">Customer Directory & History</h2>
                    <form action="/admin/customers" method="GET" class="flex gap-2 w-full md:w-auto">
                        <input type="text" name="q" value="{{ search_query }}" placeholder="Search name or phone..." class="border rounded px-3 py-1 text-sm flex-grow">
                        <button class="bg-indigo-600 text-white px-3 py-1 rounded text-sm font-bold">Search</button>
                        {% if search_query %}<a href="/admin/customers" class="bg-gray-300 text-gray-700 px-3 py-1 rounded text-sm font-bold flex items-center">Reset</a>{% endif %}
                    </form>
                </div>
                <table class="w-full text-left border-collapse">
                    <thead><tr class="border-b bg-gray-50 text-sm"><th class="p-2">Name</th><th class="p-2">Phone</th><th class="p-2">Credit Balance</th><th class="p-2">Portal Link</th><th class="p-2">Action</th></tr></thead>
                    <tbody>
                        {% for c in customers %}
                        <tr class="border-b hover:bg-gray-50">
                            <td class="p-2 font-bold">{{ c.name }}</td><td class="p-2">{{ c.phone }}</td><td class="p-2 text-green-600 font-bold">RM {{ "%.2f"|format(c.credits) }}</td>
                            <td class="p-2"><a href="/customer/{{ c.token }}" target="_blank" class="text-indigo-600 underline text-sm font-bold">View Portal</a></td>
                            <td class="p-2"><a href="/admin/customer/detail/{{ c.id }}" class="bg-indigo-600 text-white px-3 py-1 rounded text-xs font-bold hover:bg-indigo-700">View History</a></td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            <div class="bg-white p-6 rounded shadow h-fit">
                <h2 class="text-xl font-bold mb-4">Add Customer Profile</h2>
                <form action="/admin/customer/add" method="POST">
                    <div class="mb-3"><label class="block text-sm font-medium">Name</label><input type="text" name="name" class="w-full border rounded p-2" required></div>
                    <div class="mb-3"><label class="block text-sm font-medium">Phone</label><input type="text" name="phone" class="w-full border rounded p-2" required></div>
                    <div class="mb-4"><label class="block text-sm font-medium">Initial Credit</label><input type="number" step="0.01" name="credits" class="w-full border rounded p-2" value="0.00"></div>
                    <button class="w-full bg-indigo-600 text-white font-bold py-2 rounded">Save Customer</button>
                </form>
            </div>
        </div>
    """
    content = content_en if lang == "en" else content_zh
    return render_template_string(get_layout().replace("{% block content %}{% endblock %}", content), customers=customers, search_query=search_query)

@app.route("/admin/customer/add", methods=["POST"])
@admin_required
def admin_add_customer():
    name = request.form.get("name")
    phone = request.form.get("phone")
    credits = float(request.form.get("credits", 0))
    token = secrets.token_hex(8)
    with get_db() as conn:
        try:
            conn.execute("INSERT INTO customers (name, phone, token, credits) VALUES (?, ?, ?, ?)", (name, phone, token, credits))
        except:
            pass
    return redirect(url_for("admin_customers"))

@app.route("/admin/customer/detail/<int:id>")
@admin_required
def admin_customer_detail(id):
    lang = get_lang()
    with get_db() as conn:
        cust = conn.execute("SELECT * FROM customers WHERE id = ?", (id,)).fetchone()
        if not cust:
            return "Customer not found", 404
        orders = conn.execute("""
            SELECT o.*, i.item_name, i.price FROM orders o 
            LEFT JOIN order_items i ON o.id = i.order_id 
            WHERE o.customer_id = ? 
            ORDER BY o.created_at DESC
        """, (id,)).fetchall()
        appointments = conn.execute("""
            SELECT a.*, s.name as service_name FROM appointments a
            JOIN services s ON a.service_id = s.id
            WHERE a.customer_id = ?
            ORDER BY a.start_time DESC
        """, (id,)).fetchall()
        
    title_text = "Member Profile & Records" if lang == "en" else "会员档案与消费记录"
    bal_text = "Credit Balance" if lang == "en" else "账户 Credit 余额"
    app_text = "Appointment History" if lang == "en" else "历史预约记录"
    ord_text = "Order & Spending History" if lang == "en" else "历史消费与订单明细"
    back_text = "Back to Members" if lang == "en" else "返回会员列表"
    
    content = f"""
        <div class="bg-white p-6 rounded shadow space-y-6">
            <div class="flex justify-between items-center border-b pb-4">
                <div>
                    <h2 class="text-2xl font-bold text-indigo-600">{{ cust.name }}'s {title_text}</h2>
                    <p class="text-gray-600">Phone: {{ cust.phone }} | Token: {{ cust.token }}</p>
                </div>
                <div class="text-right">
                    <div class="text-sm text-gray-500">{bal_text}</div>
                    <div class="text-2xl font-bold text-green-600">RM {{{{ "%.2f"|format(cust.credits) }}}}</div>
                </div>
            </div>
            <div>
                <h3 class="text-lg font-bold mb-2">{app_text}</h3>
                <table class="w-full text-left border-collapse">
                    <thead><tr class="border-b bg-gray-50"><th class="p-2">Time</th><th class="p-2">Service</th><th class="p-2">Stylist</th><th class="p-2">Status</th></tr></thead>
                    <tbody>
                        {% for a in appointments %}
                        <tr class="border-b"><td class="p-2">{{ a.start_time }} ~ {{ a.end_time.split()[1] }}</td><td class="p-2">{{ a.service_name }}</td><td class="p-2">{{ a.stylist }}</td><td class="p-2 text-green-600 font-bold">{{ a.status }}</td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            <div>
                <h3 class="text-lg font-bold mb-2">{ord_text}</h3>
                <table class="w-full text-left border-collapse">
                    <thead><tr class="border-b bg-gray-50"><th class="p-2">Order No</th><th class="p-2">Time</th><th class="p-2">Item</th><th class="p-2">Price</th><th class="p-2">Remark / Status</th></tr></thead>
                    <tbody>
                        {% for o in orders %}
                        <tr class="border-b {% if o.status == 'VOID' %}bg-red-50 text-gray-400 line-through{% endif %}">
                            <td class="p-2 font-bold">{{ o.order_no }}</td><td class="p-2">{{ o.created_at }}</td><td class="p-2">{{ o.item_name }}</td>
                            <td class="p-2 font-bold">RM {{{{ "%.2f"|format(o.price) }}}}</td>
                            <td class="p-2">{{ 'VOIDED' if o.status == 'VOID' else (o.remark or o.payment_details) }}</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            <div><a href="/admin/customers" class="bg-gray-500 text-white px-4 py-2 rounded font-bold">{back_text}</a></div>
        </div>
    """
    return render_template_string(get_layout().replace("{% block content %}{% endblock %}", content), cust=cust, orders=orders, appointments=appointments)

ADMIN_APPOINTMENTS_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dew Hair Salon - Appointments</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 min-h-screen">
    <nav class="bg-indigo-600 text-white p-4 shadow-md">
        <div class="container mx-auto flex justify-between items-center">
            <h1 class="text-xl font-bold">Dew Hair Salon Admin</h1>
            <div class="flex items-center space-x-2 text-sm font-bold">
                <a href="/admin/pos" class="hover:bg-indigo-700 px-2 py-1 rounded">POS</a>
                <a href="/admin/appointments" class="hover:bg-indigo-700 px-2 py-1 rounded bg-indigo-800">Appointments</a>
                <a href="/admin/customers" class="hover:bg-indigo-700 px-2 py-1 rounded">Members</a>
                <a href="/admin/orders" class="hover:bg-indigo-700 px-2 py-1 rounded">Orders</a>
                <a href="/admin" class="hover:bg-indigo-700 px-2 py-1 rounded">Settings</a>
                <a href="/admin/reports" class="hover:bg-indigo-700 px-2 py-1 rounded">Reports</a>
                <a href="/admin/lang/toggle" class="bg-indigo-800 px-2.5 py-1 rounded border border-indigo-400">Lang</a>
                <a href="/admin/logout" class="bg-red-500 px-2 py-1 rounded">Logout</a>
            </div>
        </div>
    </nav>
    <main class="container mx-auto p-6">
        <div class="bg-white p-6 rounded-xl shadow-md mb-6">
            <div class="flex flex-col md:flex-row justify-between items-start md:items-center mb-6 gap-4 border-b pb-4">
                <h2 class="text-xl font-extrabold text-indigo-600">Appointments Timeline</h2>
                <div class="flex items-center gap-3">
                    <label class="text-sm font-bold text-gray-700">Filter Date:</label>
                    <input type="date" id="admin_date_picker" value="{{ selected_date }}" class="border rounded-lg p-2 font-medium" onchange="changeAdminDate(this.value)">
                    <button onclick="changeAdminDate('{{ today_str }}')" class="bg-gray-200 hover:bg-gray-300 text-gray-800 px-3 py-2 rounded-lg text-sm font-bold">Today</button>
                    <button onclick="openAdminBookModal()" class="bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg text-sm font-bold shadow">+ Manual Booking</button>
                </div>
            </div>
            <div class="mb-6">
                <div class="flex gap-2 overflow-x-auto pb-2">
                    {% for d in date_strip %}
                    <a href="/admin/appointments?date={{ d.date_str }}" class="flex-shrink-0 w-24 p-3 rounded-xl border text-center transition {% if d.date_str == selected_date %}bg-indigo-600 text-white border-indigo-600 shadow-md font-bold{% else %}bg-white text-gray-700 hover:border-indigo-400{% endif %}">
                        <div class="text-xs opacity-80">{{ d.weekday }}</div>
                        <div class="text-sm font-bold my-1">{{ d.display_date }}</div>
                        <div class="text-[10px] bg-opacity-25 py-0.5 px-1 rounded {% if d.date_str == selected_date %}bg-indigo-800 text-white{% else %}bg-gray-100 text-gray-600{% endif %}">{{ d.count }} apps</div>
                    </a>
                    {% endfor %}
                </div>
            </div>
            <div class="overflow-x-auto">
                <table class="w-full text-left border-collapse">
                    <thead><tr class="border-b bg-gray-50 text-gray-700 text-sm"><th class="p-3">Time</th><th class="p-3">Customer</th><th class="p-3">Phone</th><th class="p-3">Service</th><th class="p-3">Stylist</th><th class="p-3">Status</th><th class="p-3">Action</th></tr></thead>
                    <tbody>
                        {% for app in appointments %}
                        <tr class="border-b hover:bg-gray-50">
                            <td class="p-3 font-bold text-indigo-600">{{ app.start_time.split()[1] }} ~ {{ app.end_time.split()[1] }}</td>
                            <td class="p-3 font-bold">{{ app.customer_name }}</td><td class="p-3 text-gray-600">{{ app.customer_phone }}</td>
                            <td class="p-3">{{ app.service_name }}</td><td class="p-3 font-medium">{{ app.stylist }}</td>
                            <td class="p-3"><span class="bg-green-100 text-green-800 px-2 py-1 rounded text-xs font-bold">{{ app.status }}</span></td>
                            <td class="p-3"><a href="/admin/appointment/delete/{{ app.id }}" onclick="return confirm('Cancel appointment?')" class="text-red-500 font-bold text-sm">Cancel</a></td>
                        </tr>
                        {% else %}
                        <tr><td colspan="7" class="p-8 text-center text-gray-400">No appointments on {{ selected_date }}</td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </main>
    <div id="adminBookModal" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 hidden">
        <div class="bg-white p-6 rounded-xl shadow-xl max-w-lg w-full max-h-[90vh] overflow-y-auto">
            <div class="flex justify-between items-center mb-4 border-b pb-2">
                <h3 class="text-lg font-bold text-indigo-600">Manual Booking (Auto creates member if new)</h3>
                <button onclick="closeAdminBookModal()" class="text-gray-500 font-bold text-xl">&times;</button>
            </div>
            <form action="/admin/appointment/add" method="POST">
                <div class="mb-3">
                    <label class="block text-sm font-bold mb-1">Select Existing Customer (Optional)</label>
                    <select name="customer_id" onchange="fillAdminCustomer(this)" class="w-full border rounded p-2 text-sm">
                        <option value="">-- New Customer / Type Manually --</option>
                        {% for c in customers %}
                        <option value="{{ c.id }}" data-name="{{ c.name }}" data-phone="{{ c.phone }}">{{ c.name }} ({{ c.phone }})</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="grid grid-cols-2 gap-3 mb-3">
                    <div><label class="block text-sm font-bold mb-1">Customer Name</label><input type="text" name="customer_name" id="admin_cust_name" class="w-full border rounded p-2 text-sm" required></div>
                    <div><label class="block text-sm font-bold mb-1">Customer Phone</label><input type="text" name="customer_phone" id="admin_cust_phone" class="w-full border rounded p-2 text-sm" required></div>
                </div>
                <div class="mb-3">
                    <label class="block text-sm font-bold mb-1">Service</label>
                    <select name="service_id" class="w-full border rounded p-2 text-sm" required>
                        {% for s in services %}
                        <option value="{{ s.id }}">{{ s.name }} (RM {{ "%.2f"|format(s.price) }} / {{ s.duration }}m)</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="mb-3">
                    <label class="block text-sm font-bold mb-1">Stylist</label>
                    <select name="stylist" class="w-full border rounded p-2 text-sm" required>
                        {% for st in stylists %}
                        <option value="{{ st.name }} ({{ st.title }})">{{ st.name }} ({{ st.title }})</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="grid grid-cols-2 gap-3 mb-4">
                    <div><label class="block text-sm font-bold mb-1">Date</label><input type="date" name="booking_date" value="{{ selected_date }}" class="w-full border rounded p-2 text-sm" required></div>
                    <div><label class="block text-sm font-bold mb-1">Time Slot</label>
                        <select name="booking_time" class="w-full border rounded p-2 text-sm" required>
                            {% for t in timeslots %}<option value="{{ t }}">{{ t }}</option>{% endfor %}
                        </select>
                    </div>
                </div>
                <div class="flex justify-end gap-2">
                    <button type="button" onclick="closeAdminBookModal()" class="bg-gray-300 px-4 py-2 rounded text-sm font-bold">Cancel</button>
                    <button type="submit" class="bg-indigo-600 text-white px-4 py-2 rounded text-sm font-bold">Confirm Booking</button>
                </div>
            </form>
        </div>
    </div>
    <script>
        function changeAdminDate(dateStr) { window.location.href = "/admin/appointments?date=" + dateStr; }
        function openAdminBookModal() { document.getElementById('adminBookModal').classList.remove('hidden'); }
        function closeAdminBookModal() { document.getElementById('adminBookModal').classList.add('hidden'); }
        function fillAdminCustomer(sel) {
            const opt = sel.options[sel.selectedIndex];
            if(opt.value) {
                document.getElementById('admin_cust_name').value = opt.getAttribute('data-name');
                document.getElementById('admin_cust_phone').value = opt.getAttribute('data-phone');
            } else {
                document.getElementById('admin_cust_name').value = '';
                document.getElementById('admin_cust_phone').value = '';
            }
        }
    </script>
</body>
</html>
"""

@app.route("/admin/appointments")
@admin_required
def admin_appointments():
    selected_date = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    date_strip = []
    base_dt = datetime.strptime(selected_date, "%Y-%m-%d")
    start_loop = base_dt - timedelta(days=5)
    
    with get_db() as conn:
        for i in range(15):
            d = start_loop + timedelta(days=i)
            d_str = d.strftime("%Y-%m-%d")
            wd_map = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            wd_str = wd_map[d.weekday()]
            if d_str == today_str:
                wd_str = "Today"
            cnt = conn.execute("SELECT COUNT(*) FROM appointments WHERE start_time LIKE ? AND status = 'CONFIRMED'", (f"{d_str}%",)).fetchone()[0]
            date_strip.append({"date_str": d_str, "display_date": d.strftime("%m-%d"), "weekday": wd_str, "count": cnt})
            
        appointments = conn.execute("""
            SELECT a.*, c.name as customer_name, c.phone as customer_phone, s.name as service_name 
            FROM appointments a 
            JOIN customers c ON a.customer_id = c.id 
            JOIN services s ON a.service_id = s.id 
            WHERE a.start_time LIKE ?
            ORDER BY a.start_time ASC
        """, (f"{selected_date}%",)).fetchall()
        
        services = conn.execute("SELECT * FROM services WHERE category_type != 'Packages'").fetchall()
        stylists = conn.execute("SELECT * FROM stylists").fetchall()
        customers = conn.execute("SELECT * FROM customers").fetchall()
        
        open_time_str = get_setting("open_time", "10:00")
        close_time_str = get_setting("close_time", "20:00")
        timeslots = []
        st = datetime.strptime(open_time_str, "%H:%M")
        et = datetime.strptime(close_time_str, "%H:%M")
        while st <= et:
            timeslots.append(st.strftime("%H:%M"))
            st += timedelta(minutes=30)
        
    return render_template_string(ADMIN_APPOINTMENTS_TEMPLATE, appointments=appointments, date_strip=date_strip, selected_date=selected_date, today_str=today_str, services=services, stylists=stylists, customers=customers, timeslots=timeslots)

@app.route("/admin/appointment/add", methods=["POST"])
@admin_required
def admin_add_appointment():
    service_id = request.form.get("service_id")
    stylist = request.form.get("stylist")
    b_date = request.form.get("booking_date")
    b_time = request.form.get("booking_time")
    c_name = request.form.get("customer_name")
    c_phone = request.form.get("customer_phone")
    
    with get_db() as conn:
        srv = conn.execute("SELECT duration FROM services WHERE id = ?", (service_id,)).fetchone()
        duration = srv["duration"] if srv else 30
        
        start_dt = datetime.strptime(f"{b_date} {b_time}", "%Y-%m-%d %H:%M")
        end_dt = start_dt + timedelta(minutes=duration)
        start_str = start_dt.strftime("%Y-%m-%d %H:%M")
        end_str = end_dt.strftime("%Y-%m-%d %H:%M")
        
        conflict = conn.execute("""
            SELECT id FROM appointments 
            WHERE stylist = ? AND status = 'CONFIRMED' 
            AND start_time < ? AND end_time > ?
        """, (stylist, end_str, start_str)).fetchone()
        
        if conflict:
            return f"<script>alert('Booking failed: Stylist busy in this time slot!'); window.history.back();</script>"

        cursor = conn.cursor()
        cursor.execute("SELECT id FROM customers WHERE phone = ?", (c_phone,))
        cust = cursor.fetchone()
        if not cust:
            token = secrets.token_hex(8)
            cursor.execute("INSERT INTO customers (name, phone, token) VALUES (?, ?, ?)", (c_name, c_phone, token))
            cust_id = cursor.lastrowid
        else:
            cust_id = cust["id"]
            
        cursor.execute("""
            INSERT INTO appointments (customer_id, service_id, stylist, start_time, end_time)
            VALUES (?, ?, ?, ?, ?)
        """, (cust_id, service_id, stylist, start_str, end_str))
        
    return redirect(url_for("admin_appointments", date=b_date))

@app.route("/admin/appointment/delete/<int:id>")
@admin_required
def delete_appointment(id):
    with get_db() as conn:
        conn.execute("DELETE FROM appointments WHERE id = ?", (id,))
    return redirect(url_for("admin_appointments"))

@app.route("/admin/orders")
@admin_required
def admin_orders():
    with get_db() as conn:
        orders = conn.execute("""
            SELECT o.*, c.name as customer_name, c.phone as customer_phone 
            FROM orders o 
            JOIN customers c ON o.customer_id = c.id 
            ORDER BY o.created_at DESC
        """).fetchall()
    
    content = """
        <div class="bg-white p-6 rounded shadow">
            <h2 class="text-xl font-bold mb-4">Order History (Voided orders will reverse credits)</h2>
            <table class="w-full text-left border-collapse">
                <thead><tr class="border-b bg-gray-50"><th class="p-2">Order No</th><th class="p-2">Time</th><th class="p-2">Customer</th><th class="p-2">Amount</th><th class="p-2">Payment / Remark</th><th class="p-2">Action</th></tr></thead>
                <tbody>
                    {% for order in orders %}
                    <tr class="border-b {% if order.status == 'VOID' %}bg-red-50 text-gray-400 line-through{% endif %}">
                        <td class="p-2 font-bold text-indigo-600">{{ order.order_no }}</td>
                        <td class="p-2">{{ order.created_at }}</td>
                        <td class="p-2">{{ order.customer_name }} ({{ order.customer_phone }})</td>
                        <td class="p-2 font-bold">RM {{ "%.2f"|format(order.total_amount) }}</td>
                        <td class="p-2">{% if order.status == 'VOID' %}<span class="text-red-600">[VOIDED]</span>{% else %}<strong>{{ order.payment_details }}</strong>{% if order.remark %}<br><span class="text-xs text-gray-500">Remark: {{ order.remark }}</span>{% endif %}{% endif %}</td>
                        <td class="p-2">
                            {% if order.status != 'VOID' %}
                            <a href="/admin/order/void/{{ order.id }}" onclick="return confirm('Void this order? Credits and payment will be reversed.')" class="text-red-500 font-bold text-sm">Void</a>
                            {% else %}
                            <span class="text-gray-400 text-sm">Archived</span>
                            {% endif %}
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    """
    return render_template_string(get_layout().replace("{% block content %}{% endblock %}", content), orders=orders)

@app.route("/admin/order/void/<int:id>")
@admin_required
def void_order(id):
    with get_db() as conn:
        cursor = conn.cursor()
        order = cursor.execute("SELECT * FROM orders WHERE id = ? AND status = 'NORMAL'", (id,)).fetchone()
        if not order:
            return redirect(url_for("admin_orders"))
        
        cust_id = order["customer_id"]
        total = order["total_amount"]
        payment = order["payment_details"]
        
        items = cursor.execute("SELECT * FROM order_items WHERE order_id = ?", (id,)).fetchall()
        cust = cursor.execute("SELECT credits FROM customers WHERE id = ?", (cust_id,)).fetchone()
        current_credits = cust["credits"] if cust else 0.0
        
        for item in items:
            srv = cursor.execute("SELECT credit_value FROM services WHERE name = ?", (item["item_name"],)).fetchone()
            if srv and srv["credit_value"] > 0:
                current_credits -= srv["credit_value"]
        
        if payment == "Credit Balance Deduct":
            current_credits += total
            
        cursor.execute("UPDATE customers SET credits = ? WHERE id = ?", (max(0.0, current_credits), cust_id))
        cursor.execute("UPDATE orders SET status = 'VOID' WHERE id = ?", (id,))
        
    return redirect(url_for("admin_orders"))

@app.route("/admin/pos")
@admin_required
def admin_pos():
    with get_db() as conn:
        services = conn.execute("SELECT * FROM services").fetchall()
        customers = conn.execute("SELECT * FROM customers").fetchall()
    content = """
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div class="md:col-span-2 bg-white p-6 rounded-lg shadow">
                <h2 class="text-xl font-bold mb-4">Select Services / Packages / Products (POS)</h2>
                <div class="grid grid-cols-3 gap-4">
                    {% for item in services %}
                    <button onclick="addToOrder('{{ item.name }}', {{ item.price }})" class="p-4 border rounded hover:bg-indigo-50 text-left">
                        <div class="text-xs text-indigo-600 font-bold">{{ item.category_type }}</div>
                        <div class="font-bold text-lg">{{ item.name }}</div>
                        <div class="text-gray-600">RM {{ "%.2f"|format(item.price) }}</div>
                    </button>
                    {% endfor %}
                </div>
            </div>
            <div class="bg-white p-6 rounded-lg shadow">
                <h2 class="text-xl font-bold mb-4">Checkout Order</h2>
                <div id="order-items" class="min-h-[120px] border-b mb-4 pb-2">
                    <p class="text-gray-400">Click items on left to add</p>
                </div>
                <div class="text-xl font-bold mb-4">
                    Total: <span id="total-amount" class="text-red-600">RM 0.00</span>
                </div>
                <form action="/admin/checkout" method="POST">
                    <input type="hidden" name="cart_data" id="cart_data_input">
                    <div class="mb-3">
                        <label class="block text-sm font-medium">Select Existing Member</label>
                        <select name="customer_phone" id="cust_select" onchange="fillCustomer(this)" class="w-full border rounded p-2 text-sm">
                            <option value="">-- New Customer / Type Manually --</option>
                            {% for c in customers %}
                            <option value="{{ c.phone }}" data-name="{{ c.name }}">{{ c.name }} ({{ c.phone }}) - Balance: RM {{ c.credits }}</option>
                            {% endfor %}
                        </select>
                    </div>
                    <div class="mb-3">
                        <label class="block text-sm font-medium">Customer Name</label>
                        <input type="text" name="customer_name" id="cust_name" class="w-full border rounded p-2 text-sm" required>
                    </div>
                    <div class="mb-3">
                        <label class="block text-sm font-medium">Customer Phone</label>
                        <input type="text" name="customer_phone_input" id="cust_phone" class="w-full border rounded p-2 text-sm" required>
                    </div>
                    <div class="mb-3">
                        <label class="block text-sm font-medium">Payment Method</label>
                        <select name="payment_method" class="w-full border rounded p-2 text-sm">
                            <option value="Cash">Cash</option>
                            <option value="Credit Card">Credit Card</option>
                            <option value="TNG / QRPay">TNG / QRPay</option>
                            <option value="Credit Balance Deduct">Credit Balance Deduct</option>
                        </select>
                    </div>
                    <div class="mb-4">
                        <label class="block text-sm font-medium">Remark / Notes</label>
                        <input type="text" name="remark" class="w-full border rounded p-2 text-sm" placeholder="e.g. Discount given, styling preference...">
                    </div>
                    <button class="w-full bg-green-600 text-white font-bold py-3 rounded hover:bg-green-700">Complete Payment & Invoice</button>
                </form>
            </div>
        </div>
        <script>
            let cart = [];
            function addToOrder(name, price) { cart.push({name, price}); renderCart(); }
            function renderCart() {
                const container = document.getElementById('order-items');
                let total = 0; container.innerHTML = '';
                cart.forEach((item) => {
                    total += item.price;
                    container.innerHTML += `<div class="flex justify-between py-1 text-sm"><span>${item.name}</span><span>RM ${item.price.toFixed(2)}</span></div>`;
                });
                document.getElementById('total-amount').innerText = 'RM ' + total.toFixed(2);
                document.getElementById('cart_data_input').value = JSON.stringify(cart);
            }
            function fillCustomer(select) {
                const opt = select.options[select.selectedIndex];
                if (opt.value) {
                    document.getElementById('cust_phone').value = opt.value;
                    document.getElementById('cust_name').value = opt.getAttribute('data-name');
                }
            }
        </script>
    """
    return render_template_string(get_layout().replace("{% block content %}{% endblock %}", content), services=services, customers=customers)

@app.route("/admin/checkout", methods=["POST"])
@admin_required
def checkout():
    try:
        cart_data = json.loads(request.form.get("cart_data", "[]"))
        name = request.form.get("customer_name")
        phone = request.form.get("customer_phone_input") or request.form.get("customer_phone")
        pay_method = request.form.get("payment_method")
        remark = request.form.get("remark", "")
        total = sum(item["price"] for item in cart_data)
        order_no = "INV" + datetime.now().strftime("%Y%m%d%H%M%S")
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, token, credits FROM customers WHERE phone = ?", (phone,))
            cust = cursor.fetchone()
            if cust:
                cust_id = cust["id"]
                cust_token = cust["token"]
                current_credits = cust["credits"]
            else:
                cust_token = secrets.token_hex(8)
                cursor.execute("INSERT INTO customers (name, phone, token, credits) VALUES (?, ?, ?, 0.0)", (name, phone, cust_token))
                cust_id = cursor.lastrowid
                current_credits = 0.0
            
            for item in cart_data:
                srv = cursor.execute("SELECT credit_value FROM services WHERE name = ?", (item["name"],)).fetchone()
                if srv and srv["credit_value"] > 0:
                    current_credits += srv["credit_value"]
                    cursor.execute("UPDATE customers SET credits = ? WHERE id = ?", (current_credits, cust_id))

            if pay_method == "Credit Balance Deduct":
                if current_credits < total:
                    return "Checkout failed: Insufficient Credit Balance!", 400
                current_credits -= total
                cursor.execute("UPDATE customers SET credits = ? WHERE id = ?", (current_credits, cust_id))
                
            cursor.execute("""
                INSERT INTO orders (order_no, customer_id, total_amount, payment_details, remark, status, created_at) 
                VALUES (?, ?, ?, ?, ?, 'NORMAL', ?)
            """, (order_no, cust_id, total, pay_method, remark, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            order_id = cursor.lastrowid
            
            for item in cart_data:
                cursor.execute("""
                    INSERT INTO order_items (order_id, item_name, price) 
                    VALUES (?, ?, ?)
                """, (order_id, item["name"], item["price"]))
                
        items_html = "".join([f"<li>{i['name']} - RM {i['price']:.2f}</li>" for i in cart_data])
        remark_html = f"<p><strong>Remark:</strong> {remark}</p>" if remark else ""
        
        return f"""
            <div style="max-width:500px;margin:50px auto;padding:25px;border:1px solid #ccc;font-family:sans-serif;border-radius:8px;background:#fff;">
                <h2 style="color:#4f46e5;margin-top:0;">Dew Hair Salon - Invoice</h2>
                <hr style="border:0;border-top:1px solid #eee;margin:15px 0;">
                <p><strong>Order No:</strong> {order_no}</p>
                <p><strong>Customer:</strong> {name} ({phone})</p>
                <p><strong>Items:</strong></p>
                <ul style="margin:5px 0 15px 20px;">{items_html}</ul>
                <p><strong>Total Amount:</strong> RM {total:.2f}</p>
                <p><strong>Payment Method:</strong> {pay_method}</p>
                {remark_html}
                <p><strong>Customer Portal Link:</strong> <a href="/customer/{cust_token}" target="_blank">View Member Portal</a></p>
                <hr style="border:0;border-top:1px solid #eee;margin:15px 0;">
                <a href="/admin/pos" style="color:#4f46e5;font-weight:bold;text-decoration:none;">&larr; Back to POS</a>
            </div>
        """
    except Exception as e:
        return f"Checkout error: {str(e)}", 500

BOOKING_CALENDAR_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dew Hair Salon - Online Booking</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 min-h-screen p-4 md:p-8">
    <div class="max-w-3xl mx-auto bg-white p-6 md:p-8 rounded-xl shadow-md">
        <h2 class="text-3xl font-extrabold text-center text-indigo-600 mb-2">Dew Hair Salon Online Booking</h2>
        <p class="text-center text-sm text-gray-500 mb-6">Hours: {{ open_time }} - {{ close_time }} (Select date card or use date picker)</p>
        
        {% if error %}
        <div class="mb-4 p-3 bg-red-100 text-red-700 rounded text-sm font-bold">{{ error }}</div>
        {% endif %}
        
        <form action="/book" method="POST" id="bookingForm">
            <div class="mb-5">
                <label class="block text-sm font-bold mb-2">1. Select Service</label>
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    {% for item in services %}
                    <label class="border rounded-lg p-3 cursor-pointer hover:border-indigo-600 flex items-center justify-between">
                        <div>
                            <div class="font-bold">{{ item.name }}</div>
                            <div class="text-xs text-gray-500">Duration: {{ item.duration }} mins</div>
                        </div>
                        <div class="text-right">
                            <span class="text-indigo-600 font-bold">RM {{ "%.2f"|format(item.price) }}</span>
                            <input type="radio" name="service_id" value="{{ item.id }}" class="ml-2" required {% if loop.first %}checked{% endif %}>
                        </div>
                    </label>
                    {% endfor %}
                </div>
            </div>

            <div class="mb-5">
                <label class="block text-sm font-bold mb-2">2. Select Stylist</label>
                <div class="grid grid-cols-3 gap-3">
                    {% for st in stylists %}
                    <label class="border rounded-lg p-3 text-center cursor-pointer hover:border-indigo-600">
                        <div class="font-bold text-gray-800">{{ st.name }}</div>
                        <div class="text-xs text-gray-500 mb-2">{{ st.title }}</div>
                        <input type="radio" name="stylist" value="{{ st.name }} ({{ st.title }})" required {% if loop.first %}checked{% endif %}>
                    </label>
                    {% endfor %}
                </div>
            </div>

            <div class="mb-5">
                <div class="flex justify-between items-center mb-2">
                    <label class="block text-sm font-bold">3. Select Date</label>
                    <input type="date" name="booking_date" id="booking_date" value="{{ selected_date }}" min="{{ today_str }}" class="border rounded px-2 py-1 text-sm text-indigo-600 font-bold" onchange="syncDateInput(this.value)">
                </div>
                <div class="flex gap-2 overflow-x-auto pb-2" id="dateStripContainer">
                    {% for d in date_strip %}
                    <div onclick="selectDateCard('{{ d.date_str }}')" class="date-card flex-shrink-0 w-24 p-3 rounded-xl border text-center cursor-pointer transition {% if d.date_str == selected_date %}bg-indigo-600 text-white border-indigo-600 shadow-md font-bold{% else %}bg-white text-gray-700 hover:border-indigo-400{% endif %}" data-date="{{ d.date_str }}">
                        <div class="text-xs opacity-80">{{ d.weekday }}</div>
                        <div class="text-sm font-bold my-1">{{ d.display_date }}</div>
                        <div class="text-[10px] opacity-70">{{ d.year }}</div>
                    </div>
                    {% endfor %}
                </div>
            </div>

            <div class="mb-6">
                <label class="block text-sm font-bold mb-2">4. Select Time Slot</label>
                <div class="grid grid-cols-4 sm:grid-cols-6 gap-2 max-h-48 overflow-y-auto p-2 border rounded bg-gray-50">
                    {% for t in timeslots %}
                    <label class="border bg-white text-center py-2 rounded cursor-pointer hover:bg-indigo-600 hover:text-white transition text-sm font-medium">
                        <input type="radio" name="booking_time" value="{{ t }}" class="sr-only peer" required>
                        <span class="peer-checked:font-bold">{{ t }}</span>
                    </label>
                    {% endfor %}
                </div>
            </div>

            <div class="border-t pt-4 grid grid-cols-1 sm:grid-cols-2 gap-4 mb-6">
                <div>
                    <label class="block text-sm font-bold mb-1">Your Name</label>
                    <input type="text" name="customer_name" class="w-full border rounded p-3" placeholder="Full name" required>
                </div>
                <div>
                    <label class="block text-sm font-bold mb-1">Your Phone (Member Credential)</label>
                    <input type="text" name="customer_phone" class="w-full border rounded p-3" placeholder="Phone number" required>
                </div>
            </div>

            <button class="w-full bg-indigo-600 text-white font-bold py-3.5 rounded-lg text-lg hover:bg-indigo-700 shadow-md">Confirm Booking & Auto Register Member</button>
        </form>
    </div>
    <script>
        function selectDateCard(dateStr) {
            document.getElementById('booking_date').value = dateStr;
            document.querySelectorAll('.date-card').forEach(card => {
                if(card.getAttribute('data-date') === dateStr) {
                    card.classList.add('bg-indigo-600', 'text-white', 'border-indigo-600', 'shadow-md', 'font-bold');
                    card.classList.remove('bg-white', 'text-gray-700');
                } else {
                    card.classList.remove('bg-indigo-600', 'text-white', 'border-indigo-600', 'shadow-md', 'font-bold');
                    card.classList.add('bg-white', 'text-gray-700');
                }
            });
        }
        function syncDateInput(dateStr) { selectDateCard(dateStr); }
    </script>
</body>
</html>
"""

@app.route("/book", methods=["GET", "POST"])
def public_booking():
    if request.method == "POST":
        service_id = request.form.get("service_id")
        stylist = request.form.get("stylist")
        b_date = request.form.get("booking_date")
        b_time = request.form.get("booking_time")
        c_name = request.form.get("customer_name")
        c_phone = request.form.get("customer_phone")
        
        try:
            d_obj = datetime.strptime(b_date, "%Y-%m-%d")
            closed_wd = int(get_setting("closed_weekdays", "1"))
            if closed_wd != -1 and d_obj.weekday() == closed_wd:
                return public_booking_render(error="Booking failed: Salon is closed on this day!")
        except:
            return public_booking_render(error="Invalid date format!")
            
        with get_db() as conn:
            holiday = conn.execute("SELECT * FROM holidays WHERE date_str = ?", (b_date,)).fetchone()
            if holiday:
                return public_booking_render(error=f"Booking failed: Temporary holiday ({holiday['reason']})!")
                
            srv = conn.execute("SELECT duration FROM services WHERE id = ?", (service_id,)).fetchone()
            duration = srv["duration"] if srv else 30
            
            start_dt = datetime.strptime(f"{b_date} {b_time}", "%Y-%m-%d %H:%M")
            end_dt = start_dt + timedelta(minutes=duration)
            start_str = start_dt.strftime("%Y-%m-%d %H:%M")
            end_str = end_dt.strftime("%Y-%m-%d %H:%M")
            
            conflict = conn.execute("""
                SELECT id FROM appointments 
                WHERE stylist = ? AND status = 'CONFIRMED' 
                AND start_time < ? AND end_time > ?
            """, (stylist, end_str, start_str)).fetchone()
            
            if conflict:
                return public_booking_render(error=f"Booking failed: Stylist {stylist} is busy at this time!")

            cursor = conn.cursor()
            cursor.execute("SELECT id, token FROM customers WHERE phone = ?", (c_phone,))
            cust = cursor.fetchone()
            if not cust:
                token = secrets.token_hex(8)
                cursor.execute("INSERT INTO customers (name, phone, token) VALUES (?, ?, ?)", (c_name, c_phone, token))
                cust_id = cursor.lastrowid
                cust_token = token
            else:
                cust_id = cust["id"]
                cust_token = cust["token"]
                
            cursor.execute("""
                INSERT INTO appointments (customer_id, service_id, stylist, start_time, end_time)
                VALUES (?, ?, ?, ?, ?)
            """, (cust_id, service_id, stylist, start_str, end_str))
            
        return f"""
            <div style="max-width:400px;margin:50px auto;text-align:center;font-family:sans-serif;padding:30px;border:1px solid #ddd;border-radius:8px;background:#fff;box-shadow:0 2px 5px rgba(0,0,0,0.1);">
                <h2 style="color:green;margin-top:0;">Booking Confirmed!</h2>
                <p>Thank you, <strong>{c_name}</strong>! Your appointment and member account have been registered.</p>
                <p><strong>Time:</strong> {start_str} ~ {end_str.split()[1]}</p>
                <p><strong>Stylist:</strong> {stylist}</p>
                <hr style="margin:20px 0;">
                <p>Your <strong>Member Portal Link</strong>:</p>
                <a href="/customer/{cust_token}" style="display:inline-block;padding:12px 20px;background:#4f46e5;color:white;text-decoration:none;border-radius:6px;font-weight:bold;">Access My Member Portal</a>
            </div>
        """
    return public_booking_render(error=None)

def public_booking_render(error=None):
    open_time_str = get_setting("open_time", "10:00")
    close_time_str = get_setting("close_time", "20:00")
    timeslots = []
    st = datetime.strptime(open_time_str, "%H:%M")
    et = datetime.strptime(close_time_str, "%H:%M")
    while st <= et:
        timeslots.append(st.strftime("%H:%M"))
        st += timedelta(minutes=30)
        
    with get_db() as conn:
        services = conn.execute("SELECT * FROM services WHERE category_type != 'Packages'").fetchall()
        stylists = conn.execute("SELECT * FROM stylists").fetchall()
        
    today_str = datetime.now().strftime("%Y-%m-%d")
    selected_date = request.args.get("date", today_str)
    
    date_strip = []
    base_dt = datetime.now()
    wd_map = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for i in range(14):
        d = base_dt + timedelta(days=i)
        d_str = d.strftime("%Y-%m-%d")
        wd_str = wd_map[d.weekday()]
        if i == 0:
            wd_str = "Today"
        elif i == 1:
            wd_str = "Tomorrow"
            
        date_strip.append({
            "date_str": d_str,
            "display_date": d.strftime("%b %d"),
            "weekday": wd_str,
            "year": d.strftime("%Y")
        })
        
    return render_template_string(
        BOOKING_CALENDAR_TEMPLATE, 
        services=services, 
        stylists=stylists, 
        timeslots=timeslots, 
        open_time=open_time_str, 
        close_time=close_time_str, 
        today_str=today_str,
        selected_date=selected_date,
        date_strip=date_strip,
        error=error
    )

@app.route("/customer/<token>")
def customer_profile(token):
    with get_db() as conn:
        cust = conn.execute("SELECT * FROM customers WHERE token = ?", (token,)).fetchone()
        if not cust:
            return "Invalid member link", 404
        orders = conn.execute("""
            SELECT o.*, i.item_name, i.price FROM orders o 
            LEFT JOIN order_items i ON o.id = i.order_id 
            WHERE o.customer_id = ? 
            ORDER BY o.created_at DESC
        """, (cust["id"],)).fetchall()
        appointments = conn.execute("""
            SELECT a.*, s.name as service_name FROM appointments a
            JOIN services s ON a.service_id = s.id
            WHERE a.customer_id = ?
            ORDER BY a.start_time DESC
        """, (cust["id"],)).fetchall()
    return render_template_string("""
        <div style="max-width:500px;margin:30px auto;padding:20px;border:1px solid #ccc;font-family:sans-serif;border-radius:8px;background:#fff;">
            <h2 style="color:#4f46e5;margin-top:0;">Dew Hair Salon - Member Portal</h2>
            <p><strong>Name:</strong> {{ cust.name }}</p>
            <p><strong>Phone:</strong> {{ cust.phone }}</p>
            <div style="background:#f0fdf4;border:1px solid #bbf7d0;padding:12px;border-radius:6px;margin:15px 0;">
                <span style="font-size:14px;color:#166534;">Credit Balance:</span>
                <div style="font-size:24px;font-weight:bold;color:#15803d;">RM {{ "%.2f"|format(cust.credits) }}</div>
            </div>
            <hr>
            <h3>Appointments</h3>
            {% if appointments %}
            <ul style="padding-left:20px;font-size:14px;">
                {% for a in appointments %}
                <li style="margin-bottom:6px;">{{ a.start_time }} - <strong>{{ a.service_name }}</strong> (Stylist: {{ a.stylist }}) - <span style="color:green;">{{ a.status }}</span></li>
                {% endfor %}
            </ul>
            {% else %}
            <p style="color:gray;font-size:14px;">No appointments.</p>
            {% endif %}
            <hr>
            <h3>Spending History</h3>
            {% if orders %}
            <ul style="padding-left:20px;font-size:14px;">
                {% for o in orders %}
                <li style="margin-bottom:6px; {% if o.status == 'VOID' %}color:#9ca3af;text-decoration:line-through;{% endif %}">
                    {{ o.created_at }} - <strong>{{ o.item_name }}</strong> (RM {{ "%.2f"|format(o.price) }}) 
                    {% if o.status == 'VOID' %}<span style="color:red;font-weight:bold;">[VOIDED]</span>{% else %}[Payment: {{ o.payment_details }} {% if o.remark %}| Remark: {{ o.remark }}{% endif %}]{% endif %}
                </li>
                {% endfor %}
            </ul>
            {% else %}
            <p style="color:gray;font-size:14px;">No spending history.</p>
            {% endif %}
        </div>
    """, cust=cust, orders=orders, appointments=appointments)

@app.route("/admin/reports")
@admin_required
def admin_reports():
    lang = get_lang()
    with get_db() as conn:
        order_count = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'NORMAL'").fetchone()[0]
        total_revenue = conn.execute("SELECT SUM(total_amount) FROM orders WHERE status = 'NORMAL'").fetchone()[0] or 0.0
        customer_count = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        item_count = conn.execute("SELECT COUNT(*) FROM order_items i JOIN orders o ON i.order_id = o.id WHERE o.status = 'NORMAL'").fetchone()[0]
        
    title = "90-Day Performance Dashboard" if lang == "en" else "90天历史数据看板"
    lbl1 = "Valid Orders" if lang == "en" else "有效订单数"
    lbl2 = "Total Revenue (RM)" if lang == "en" else "总营业额 (RM)"
    lbl3 = "Items Sold" if lang == "en" else "项目销售量"
    lbl4 = "Total Members" if lang == "en" else "会员总数"
    
    content = f"""
        <div class="bg-white p-6 rounded shadow">
            <h2 class="text-xl font-bold mb-6 border-b pb-2">{title}</h2>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-center">
                <div class="bg-indigo-50 p-4 rounded shadow"><div class="text-gray-500 text-sm">{lbl1}</div><div class="text-3xl font-bold text-indigo-600">{{ order_count }}</div></div>
                <div class="bg-green-50 p-4 rounded shadow"><div class="text-gray-500 text-sm">{lbl2}</div><div class="text-3xl font-bold text-green-600">RM {{{{ "%.2f"|format(total_revenue) }}}}</div></div>
                <div class="bg-yellow-50 p-4 rounded shadow"><div class="text-gray-500 text-sm">{lbl3}</div><div class="text-3xl font-bold text-yellow-600">{{ item_count }}</div></div>
                <div class="bg-purple-50 p-4 rounded shadow"><div class="text-gray-500 text-sm">{lbl4}</div><div class="text-3xl font-bold text-purple-600">{{ customer_count }}</div></div>
            </div>
        </div>
    """
    return render_template_string(get_layout().replace("{% block content %}{% endblock %}", content), order_count=order_count, total_revenue=total_revenue, customer_count=customer_count, item_count=item_count)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=False)
