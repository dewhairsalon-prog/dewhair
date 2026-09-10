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

# 修复数据丢失：如果是在 Render 等云端，优先使用持久化目录 /opt/render/project/src 或当前目录
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
        
        # 兼容旧数据库：为 orders 表检查并补全 remark 字段
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(orders);")
        columns = [col["name"] for col in cursor.fetchall()]
        if "remark" not in columns:
            conn.execute("ALTER TABLE orders ADD COLUMN remark TEXT DEFAULT ''")

        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('open_time', '10:00')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('close_time', '20:00')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('closed_weekdays', '1')")
        
        cursor.execute("SELECT COUNT(*) FROM services")
        if cursor.fetchone()[0] == 0:
            sample_services = [
                ("高级总监剪发", "Services", "剪发", 120.0, 45, 0.0),
                ("植物精油染发", "Services", "染发", 380.0, 90, 0.0),
                ("充值 1000 送 200", "Packages", "储值套餐", 1000.0, 0, 1200.0),
            ]
            cursor.executemany("""
                INSERT INTO services (name, category_type, sub_category, price, duration, credit_value)
                VALUES (?, ?, ?, ?, ?, ?)
            """, sample_services)

        cursor.execute("SELECT COUNT(*) FROM stylists")
        if cursor.fetchone()[0] == 0:
            sample_stylists = [
                ("Alex", "总监"),
                ("David", "资深设计师"),
                ("Emma", "高级造型师")
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

# 多语言文字字典
LANG = {
    "zh": {
        "title": "Dew Hair Salon 管理后台",
        "pos": "POS 收银",
        "appointments": "预约管理",
        "customers": "会员与历史记录",
        "orders": "订单历史",
        "settings": "营业、项目与员工",
        "reports": "90天报表",
        "logout": "退出",
        "lang_switch": "English",
        "save": "保存设置",
        "add_holiday": "添加闭店日",
        "add_stylist": "确认添加员工",
        "add_service": "确认添加服务",
        "search": "搜索",
        "reset": "重置",
        "back": "返回会员列表"
    },
    "en": {
        "title": "Dew Hair Salon Admin Dashboard",
        "pos": "POS Checkout",
        "appointments": "Appointments",
        "customers": "Members & History",
        "orders": "Order History",
        "settings": "Settings, Services & Staff",
        "reports": "90-Day Reports",
        "logout": "Logout",
        "lang_switch": "中文",
        "save": "Save Settings",
        "add_holiday": "Add Closed Date",
        "add_stylist": "Add Staff",
        "add_service": "Add Service/Package",
        "search": "Search",
        "reset": "Reset",
        "back": "Back to Members"
    }
}

def t(key):
    lang = session.get("lang", "zh")
    return LANG.get(lang, LANG["zh"]).get(key, key)

@app.route("/admin/lang/<lang_code>")
def set_language(lang_code):
    if lang_code in ["zh", "en"]:
        session["lang"] = lang_code
    return redirect(request.referrer or url_for("admin_dashboard"))

LAYOUT_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dew Hair Salon 管理系统</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 min-h-screen">
    <nav class="bg-indigo-600 text-white p-4 shadow-md">
        <div class="container mx-auto flex justify-between items-center">
            <h1 class="text-xl font-bold">{{ t('title') }}</h1>
            <div class="flex space-x-2 text-sm font-bold items-center">
                <a href="/admin/pos" class="hover:bg-indigo-700 px-2 py-1 rounded">{{ t('pos') }}</a>
                <a href="/admin/appointments" class="hover:bg-indigo-700 px-2 py-1 rounded">{{ t('appointments') }}</a>
                <a href="/admin/customers" class="hover:bg-indigo-700 px-2 py-1 rounded">{{ t('customers') }}</a>
                <a href="/admin/orders" class="hover:bg-indigo-700 px-2 py-1 rounded">{{ t('orders') }}</a>
                <a href="/admin" class="hover:bg-indigo-700 px-2 py-1 rounded">{{ t('settings') }}</a>
                <a href="/admin/reports" class="hover:bg-indigo-700 px-2 py-1 rounded">{{ t('reports') }}</a>
                <a href="/admin/lang/{{ 'en' if session.get('lang', 'zh') == 'zh' else 'zh' }}" class="bg-indigo-800 hover:bg-indigo-900 px-2 py-1 rounded text-yellow-300 border border-yellow-300">{{ t('lang_switch') }}</a>
                <a href="/admin/logout" class="bg-red-500 px-2 py-1 rounded hover:bg-red-600">{{ t('logout') }}</a>
            </div>
        </div>
    </nav>
    <main class="container mx-auto p-6">
        {% block content %}{% endblock %}
    </main>
</body>
</html>
"""

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        if hmac.compare_digest(password, ADMIN_PASSWORD):
            session["is_admin"] = True
            return redirect(url_for("admin_dashboard"))
        error = "密码错误，默认密码为：123456"
    return render_template_string("""
        <div style="max-width:400px;margin:100px auto;padding:20px;border:1px solid #ccc;text-align:center;font-family:sans-serif;border-radius:8px;">
            <h2>Dew Hair Salon 管理后台</h2>
            {% if error %}<p style="color:red;">{{ error }}</p>{% endif %}
            <form method="POST">
                <input type="password" name="password" placeholder="密码 (默认: 123456)" style="width:100%;padding:10px;margin:10px 0;box-sizing:border-box;" required>
                <button style="width:100%;padding:10px;background:#4f46e5;color:white;border:none;border-radius:4px;font-weight:bold;cursor:pointer;">登录系统</button>
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
    open_time = get_setting("open_time", "10:00")
    close_time = get_setting("close_time", "20:00")
    closed_wd = get_setting("closed_weekdays", "1")
    
    with get_db() as conn:
        services = conn.execute("SELECT * FROM services ORDER BY category_type, sub_category").fetchall()
        stylists = conn.execute("SELECT * FROM stylists ORDER BY id DESC").fetchall()
        holidays = conn.execute("SELECT * FROM holidays ORDER BY date_str DESC").fetchall()
        
    lang = session.get("lang", "zh")
    content_html = """
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div class="md:col-span-2 space-y-6">
                <!-- 营业与休息日设置 -->
                <div class="bg-white p-6 rounded shadow">
                    <h2 class="text-xl font-bold mb-4">{}</h2>
                    <form action="/admin/settings/update" method="POST" class="grid grid-cols-2 md:grid-cols-4 gap-4 items-end mb-4">
                        <div>
                            <label class="block text-sm font-medium">{}</label>
                            <input type="time" name="open_time" value="{{ open_time }}" class="w-full border rounded p-2" required>
                        </div>
                        <div>
                            <label class="block text-sm font-medium">{}</label>
                            <input type="time" name="close_time" value="{{ close_time }}" class="w-full border rounded p-2" required>
                        </div>
                        <div>
                            <label class="block text-sm font-medium">{}</label>
                            <select name="closed_weekdays" class="w-full border rounded p-2">
                                <option value="0" {% if closed_wd == '0' %}selected{% endif %}>{}</option>
                                <option value="1" {% if closed_wd == '1' %}selected{% endif %}>{}</option>
                                <option value="2" {% if closed_wd == '2' %}selected{% endif %}>{}</option>
                                <option value="-1" {% if closed_wd == '-1' %}selected{% endif %}>{}</option>
                            </select>
                        </div>
                        <div>
                            <button class="w-full bg-indigo-600 text-white font-bold py-2 rounded hover:bg-indigo-700">{}</button>
                        </div>
                    </form>

                    <hr class="my-4">
                    <h3 class="font-bold mb-2">{}</h3>
                    <form action="/admin/holiday/add" method="POST" class="flex gap-2">
                        <input type="date" name="date_str" class="border rounded p-2" required>
                        <input type="text" name="reason" placeholder="{}" class="border rounded p-2 flex-grow">
                        <button class="bg-red-500 text-white px-4 py-2 rounded font-bold hover:bg-red-600">{}</button>
                    </form>
                    
                    <div class="mt-4">
                        <h4 class="text-sm font-bold text-gray-600 mb-2">{}</h4>
                        <div class="flex flex-wrap gap-2">
                            {% for h in holidays %}
                            <span class="bg-red-50 text-red-700 px-3 py-1 rounded border border-red-200 text-sm flex items-center gap-2">
                                {{ h.date_str }} ({{ h.reason }})
                                <a href="/admin/holiday/delete/{{ h.id }}" class="font-bold hover:text-red-900">×</a>
                            </span>
                            {% else %}
                            <span class="text-gray-400 text-sm">{}</span>
                            {% endfor %}
                        </div>
                    </div>
                </div>

                <!-- 员工团队管理 -->
                <div class="bg-white p-6 rounded shadow">
                    <h2 class="text-xl font-bold mb-4">{}</h2>
                    <table class="w-full text-left">
                        <thead><tr class="border-b"><th class="p-2">{}</th><th class="p-2">{}</th><th class="p-2">{}</th></tr></thead>
                        <tbody>
                            {% for st in stylists %}
                            <tr class="border-b">
                                <td class="p-2 font-bold text-indigo-600">{{ st.name }}</td>
                                <td class="p-2">{{ st.title }}</td>
                                <td class="p-2">
                                    <a href="/admin/stylist/delete/{{ st.id }}" onclick="return confirm('{}')" class="text-red-500 text-sm font-bold">{}</a>
                                </td>
                            </tr>
                            {% else %}
                            <tr><td colspan="3" class="p-2 text-gray-400">{}</td></tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>

                <!-- 项目与充值套餐列表 -->
                <div class="bg-white p-6 rounded shadow">
                    <h2 class="text-xl font-bold mb-4">{}</h2>
                    <table class="w-full text-left">
                        <thead><tr class="border-b"><th class="p-2">{}</th><th class="p-2">{}</th><th class="p-2">{}</th><th class="p-2">{}</th><th class="p-2">{}</th></tr></thead>
                        <tbody>
                            {% for item in services %}
                            <tr class="border-b">
                                <td class="p-2 font-bold text-indigo-600">{{ item.category_type }}</td>
                                <td class="p-2">{{ item.name }}</td>
                                <td class="p-2 font-bold text-red-600">RM {{ "%.2f"|format(item.price) }}</td>
                                <td class="p-2 font-bold text-green-600">{% if item.category_type == 'Packages' %}RM {{ "%.2f"|format(item.credit_value) }}{% else %}-{% endif %}</td>
                                <td class="p-2">
                                    <a href="/admin/service/delete/{{ item.id }}" onclick="return confirm('{}')" class="text-red-500 text-sm font-bold">{}</a>
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- 右侧：添加服务与添加员工表单 -->
            <div class="space-y-6">
                <div class="bg-white p-6 rounded shadow">
                    <h2 class="text-xl font-bold mb-4">{}</h2>
                    <form action="/admin/stylist/add" method="POST">
                        <div class="mb-3">
                            <label class="block text-sm font-medium">{}</label>
                            <input type="text" name="name" class="w-full border rounded p-2" required placeholder="如: Kevin">
                        </div>
                        <div class="mb-3">
                            <label class="block text-sm font-medium">{}</label>
                            <input type="text" name="title" class="w-full border rounded p-2" required placeholder="如: 高级造型师">
                        </div>
                        <button class="w-full bg-indigo-600 text-white font-bold py-2 rounded hover:bg-indigo-700">{}</button>
                    </form>
                </div>

                <div class="bg-white p-6 rounded shadow">
                    <h2 class="text-xl font-bold mb-4">{}</h2>
                    <form action="/admin/service/add" method="POST">
                        <div class="mb-3">
                            <label class="block text-sm font-medium">{}</label>
                            <input type="text" name="name" class="w-full border rounded p-2" required>
                        </div>
                        <div class="mb-3">
                            <label class="block text-sm font-medium">{}</label>
                            <select name="category_type" id="cat_type_select" onchange="toggleCreditInput(this)" class="w-full border rounded p-2">
                                <option value="Services">Services (服务项目)</option>
                                <option value="Packages">Packages (储值套餐)</option>
                                <option value="Products">Products (零售产品)</option>
                            </select>
                        </div>
                        <div class="mb-3">
                            <label class="block text-sm font-medium">{}</label>
                            <input type="number" step="0.01" name="price" class="w-full border rounded p-2" required>
                        </div>
                        <div class="mb-3" id="credit_value_div" style="display:none;">
                            <label class="block text-sm font-medium text-green-600 font-bold">{}</label>
                            <input type="number" step="0.01" name="credit_value" class="w-full border rounded p-2" placeholder="例如: 1200">
                        </div>
                        <div class="mb-4">
                            <label class="block text-sm font-medium">{}</label>
                            <input type="number" name="duration" class="w-full border rounded p-2" value="30" required>
                        </div>
                        <button class="w-full bg-indigo-600 text-white font-bold py-2 rounded hover:bg-indigo-700">{}</button>
                    </form>
                </div>
            </div>
        </div>
        <script>
            function toggleCreditInput(sel) {
                const div = document.getElementById('credit_value_div');
                div.style.display = (sel.value === 'Packages') ? 'block' : 'none';
            }
        </script>
    """
    
    if lang == "en":
        filled_content = content_html.format(
            "Business Hours & Closing Days", "Opening Time", "Closing Time", "Weekly Day Off",
            "Sunday Off", "Monday Off", "Tuesday Off", "No Fixed Day Off", "Save Settings",
            "Add Temporary Closed Date", "Reason (e.g. Public Holiday / Staff Training)", "Add Closed Date",
            "Configured Temporary Closed Days:", "No temporary closed days",
            "Stylist / Staff Team Management", "Name", "Title / Role", "Action",
            "Are you sure to delete this stylist?", "Delete", "No staff yet, please add on the right",
            "Services & Packages Management", "Category", "Name", "Price (RM)", "Earn Credit (RM)", "Action",
            "Are you sure to delete?", "Delete",
            "Add Stylist", "Name", "Title / Bio", "Add Staff",
            "Add Service or Package", "Name", "Category Type", "Price / Amount (RM)", "Bonus Credit (RM)", "Duration (mins, 0 for packages)", "Add Service/Package"
        )
    else:
        filled_content = content_html.format(
            "营业与休息日设置", "开门时间", "关门时间", "每周固定休息日",
            "周日休息", "周一休息", "周二休息", "无固定休息日", "保存设置",
            "添加特定临时休息日", "休息原因 (如: 公共假期/员工培训)", "添加闭店日",
            "已设定的临时休息日：", "暂无临时闭店日",
            "发型师 / 员工团队管理", "姓名", "职级/头衔", "操作",
            "确定要删除该发型师吗？", "删除", "暂无员工，请在右侧添加",
            "项目与充值套餐管理", "分类", "名称", "售价 (RM)", "获得 Credit (RM)", "操作",
            "确定要删除吗？", "删除",
            "添加发型师", "姓名", "职级 / 简介", "确认添加员工",
            "添加服务或套餐", "名称", "分类类型", "售价 / 金额 (RM)", "赠送 Credit (RM)", "耗时 (分钟，套餐填0)", "确认添加服务"
        )

    return render_template_string(LAYOUT_TEMPLATE.replace("{% block content %}{% endblock %}", filled_content), services=services, stylists=stylists, holidays=holidays, open_time=open_time, close_time=close_time, closed_wd=closed_wd)

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
    reason = request.form.get("reason", "闭店休息")
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
            
    lang = session.get("lang", "zh")
    is_en = (lang == "en")
    
    html = """
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div class="md:col-span-2 bg-white p-6 rounded shadow">
                <div class="flex flex-col md:flex-row justify-between items-start md:items-center mb-4 gap-3">
                    <h2 class="text-xl font-bold">{}</h2>
                    <form action="/admin/customers" method="GET" class="flex gap-2 w-full md:w-auto">
                        <input type="text" name="q" value="{{ search_query }}" placeholder="{}" class="border rounded px-3 py-1 text-sm flex-grow">
                        <button class="bg-indigo-600 text-white px-3 py-1 rounded text-sm font-bold">{}</button>
                        {% if search_query %}
                        <a href="/admin/customers" class="bg-gray-300 text-gray-700 px-3 py-1 rounded text-sm font-bold flex items-center">{}</a>
                        {% endif %}
                    </form>
                </div>
                <table class="w-full text-left border-collapse">
                    <thead>
                        <tr class="border-b bg-gray-50 text-sm">
                            <th class="p-2">{}</th>
                            <th class="p-2">{}</th>
                            <th class="p-2">{}</th>
                            <th class="p-2">{}</th>
                            <th class="p-2">{}</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for c in customers %}
                        <tr class="border-b hover:bg-gray-50">
                            <td class="p-2 font-bold">{{ c.name }}</td>
                            <td class="p-2">{{ c.phone }}</td>
                            <td class="p-2 text-green-600 font-bold">RM {{ "%.2f"|format(c.credits) }}</td>
                            <td class="p-2">
                                <a href="/customer/{{ c.token }}" target="_blank" class="text-indigo-600 underline text-sm font-bold">{}</a>
                            </td>
                            <td class="p-2">
                                <a href="/admin/customer/detail/{{ c.id }}" class="bg-indigo-600 text-white px-3 py-1 rounded text-xs font-bold hover:bg-indigo-700">{}</a>
                            </td>
                        </tr>
                        {% else %}
                        <tr>
                            <td colspan="5" class="p-6 text-center text-gray-400">{}</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            
            <div class="bg-white p-6 rounded shadow h-fit">
                <h2 class="text-xl font-bold mb-4">{}</h2>
                <form action="/admin/customer/add" method="POST">
                    <div class="mb-3">
                        <label class="block text-sm font-medium">{}</label>
                        <input type="text" name="name" class="w-full border rounded p-2" required>
                    </div>
                    <div class="mb-3">
                        <label class="block text-sm font-medium">{}</label>
                        <input type="text" name="phone" class="w-full border rounded p-2" required>
                    </div>
                    <div class="mb-4">
                        <label class="block text-sm font-medium">{}</label>
                        <input type="number" step="0.01" name="credits" class="w-full border rounded p-2" value="0.00">
                    </div>
                    <button class="w-full bg-indigo-600 text-white font-bold py-2 rounded hover:bg-indigo-700">{}</button>
                </form>
            </div>
        </div>
    """
    
    if is_en:
        filled = html.format(
            "Member List & Consumption Profile", "Search name or phone...", "Search", "Reset",
            "Name", "Phone", "Credit Balance", "Profile Link", "Action", "View Page", "View History",
            "No members found, add on the right or check query",
            "Manually Add Member", "Member Name", "Phone (Unique Credential)", "Initial Bonus Credit (RM)", "Save Member"
        )
    else:
        filled = html.format(
            "会员列表与消费档案", "搜姓名或手机号...", "搜索", "重置",
            "姓名", "电话", "Credit 余额", "专属链接", "操作", "查看页面", "查看消费与预约记录",
            "没有找到相关会员，请在右侧添加或检查搜索词",
            "手动添加会员档案", "会员姓名", "电话号码 (唯一凭证)", "初始赠送 Credit (RM)", "保存会员"
        )

    return render_template_string(LAYOUT_TEMPLATE.replace("{% block content %}{% endblock %}", filled), customers=customers, search_query=search_query)

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
    with get_db() as conn:
        cust = conn.execute("SELECT * FROM customers WHERE id = ?", (id,)).fetchone()
        if not cust:
            return "找不到该会员", 404
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
        
    lang = session.get("lang", "zh")
    is_en = (lang == "en")
    
    html = """
        <div class="bg-white p-6 rounded shadow space-y-6">
            <div class="flex justify-between items-center border-b pb-4">
                <div>
                    <h2 class="text-2xl font-bold text-indigo-600">{{ cust.name }} {}</h2>
                    <p class="text-gray-600">{} {{ cust.phone }} | {} {{ cust.token }}</p>
                </div>
                <div class="text-right">
                    <div class="text-sm text-gray-500">{}</div>
                    <div class="text-2xl font-bold text-green-600">RM {{ "%.2f"|format(cust.credits) }}</div>
                </div>
            </div>

            <div>
                <h3 class="text-lg font-bold mb-2">{}</h3>
                <table class="w-full text-left border-collapse">
                    <thead><tr class="border-b bg-gray-50"><th class="p-2">{}</th><th class="p-2">{}</th><th class="p-2">{}</th><th class="p-2">{}</th></tr></thead>
                    <tbody>
                        {% for a in appointments %}
                        <tr class="border-b"><td class="p-2 font-medium">{{ a.start_time }} ~ {{ a.end_time.split()[1] }}</td><td class="p-2">{{ a.service_name }}</td><td class="p-2">{{ a.stylist }}</td><td class="p-2 text-green-600 font-bold">{{ a.status }}</td></tr>
                        {% else %}
                        <tr><td colspan="4" class="p-2 text-gray-400">{}</td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>

            <div>
                <h3 class="text-lg font-bold mb-2">{}</h3>
                <table class="w-full text-left border-collapse">
                    <thead><tr class="border-b bg-gray-50"><th class="p-2">{}</th><th class="p-2">{}</th><th class="p-2">{}</th><th class="p-2">{}</th><th class="p-2">{}</th></tr></thead>
                    <tbody>
                        {% for o in orders %}
                        <tr class="border-b {% if o.status == 'VOID' %}bg-red-50 text-gray-400 line-through{% endif %}">
                            <td class="p-2 font-bold">{{ o.order_no }}</td>
                            <td class="p-2">{{ o.created_at }}</td>
                            <td class="p-2">{{ o.item_name }}{% if o.remark %} <span class="text-xs text-gray-500">(Remark: {{ o.remark }})</span>{% endif %}</td>
                            <td class="p-2 font-bold">RM {{ "%.2f"|format(o.price) }}</td>
                            <td class="p-2 font-bold">{{ 'Voided' if o.status == 'VOID' and is_en else ('已作废' if o.status == 'VOID' else o.payment_details) }}</td>
                        </tr>
                        {% else %}
                        <tr><td colspan="5" class="p-2 text-gray-400">{}</td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            <div>
                <a href="/admin/customers" class="bg-gray-500 text-white px-4 py-2 rounded font-bold hover:bg-gray-600">{}</a>
            </div>
        </div>
    """
    
    if is_en:
        filled = html.format(
            "Member Profile & Consumption Records", "Phone:", "Token Code:", "Account Credit Balance",
            "Appointment History", "Time Slot", "Service", "Stylist", "Status", "No appointment records",
            "Consumption History & Order Details", "Order No", "Time", "Item/Package", "Amount", "Status/Payment", "No consumption orders",
            "Back to Members"
        )
    else:
        filled = html.format(
            "的会员档案与消费记录", "电话:", "专属链接码:", "账户 Credit 余额",
            "历史预约记录", "时间段", "服务项目", "发型师", "状态", "暂无预约记录",
            "历史消费与订单明细", "单号", "时间", "项目/套餐", "金额", "状态/支付方式", "暂无消费订单",
            "返回会员列表"
        )

    return render_template_string(LAYOUT_TEMPLATE.replace("{% block content %}{% endblock %}", filled), cust=cust, orders=orders, appointments=appointments, is_en=is_en)

ADMIN_APPOINTMENTS_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dew Hair Salon 管理系统 - 预约管理</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 min-h-screen">
    <nav class="bg-indigo-600 text-white p-4 shadow-md">
        <div class="container mx-auto flex justify-between items-center">
            <h1 class="text-xl font-bold">{{ t('title') }}</h1>
            <div class="flex space-x-2 text-sm font-bold items-center">
                <a href="/admin/pos" class="hover:bg-indigo-700 px-2 py-1 rounded">{{ t('pos') }}</a>
                <a href="/admin/appointments" class="hover:bg-indigo-700 px-2 py-1 rounded bg-indigo-800">{{ t('appointments') }}</a>
                <a href="/admin/customers" class="hover:bg-indigo-700 px-2 py-1 rounded">{{ t('customers') }}</a>
                <a href="/admin/orders" class="hover:bg-indigo-700 px-2 py-1 rounded">{{ t('orders') }}</a>
                <a href="/admin" class="hover:bg-indigo-700 px-2 py-1 rounded">{{ t('settings') }}</a>
                <a href="/admin/reports" class="hover:bg-indigo-700 px-2 py-1 rounded">{{ t('reports') }}</a>
                <a href="/admin/lang/{{ 'en' if session.get('lang', 'zh') == 'zh' else 'zh' }}" class="bg-indigo-800 hover:bg-indigo-900 px-2 py-1 rounded text-yellow-300 border border-yellow-300">{{ t('lang_switch') }}</a>
                <a href="/admin/logout" class="bg-red-500 px-2 py-1 rounded hover:bg-red-600">{{ t('logout') }}</a>
            </div>
        </div>
    </nav>
    <main class="container mx-auto p-6">
        <div class="bg-white p-6 rounded-xl shadow-md mb-6">
            <div class="flex flex-col md:flex-row justify-between items-start md:items-center mb-6 gap-4 border-b pb-4">
                <h2 class="text-xl font-extrabold text-indigo-600">{{ 'Appointment Records & Timeline' if is_en else '预约记录与时间轴管理' }}</h2>
                <div class="flex items-center gap-3">
                    <label class="text-sm font-bold text-gray-700">{{ 'Filter Date:' if is_en else '切换/筛选日期：' }}</label>
                    <input type="date" id="admin_date_picker" value="{{ selected_date }}" class="border rounded-lg p-2 font-medium" onchange="changeAdminDate(this.value)">
                    <button onclick="changeAdminDate('{{ today_str }}')" class="bg-gray-200 hover:bg-gray-300 text-gray-800 px-3 py-2 rounded-lg text-sm font-bold">{{ 'Today' if is_en else '今天' }}</button>
                    <button onclick="openAdminBookModal()" class="bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg text-sm font-bold shadow">{{ '+ Manual Booking' if is_en else '+ 手动代客预约' }}</button>
                </div>
            </div>

            <!-- 横向滑动按天查看条 -->
            <div class="mb-6">
                <div class="flex items-center justify-between mb-2">
                    <span class="text-sm font-bold text-gray-600">{{ 'Quick Date Scroll' if is_en else '快速按天滑动查看' }}</span>
                    <span class="text-xs text-gray-400">{{ 'Support horizontal scroll' if is_en else '支持左右滚动' }}</span>
                </div>
                <div class="flex gap-2 overflow-x-auto pb-2 scrollbar-thin">
                    {% for d in date_strip %}
                    <a href="/admin/appointments?date={{ d.date_str }}" class="flex-shrink-0 w-24 p-3 rounded-xl border text-center transition {% if d.date_str == selected_date %}bg-indigo-600 text-white border-indigo-600 shadow-md font-bold{% else %}bg-white text-gray-700 hover:border-indigo-400{% endif %}">
                        <div class="text-xs opacity-80">{{ d.weekday }}</div>
                        <div class="text-sm font-bold my-1">{{ d.display_date }}</div>
                        <div class="text-[10px] bg-opacity-25 py-0.5 px-1 rounded {% if d.date_str == selected_date %}bg-indigo-800 text-white{% else %}bg-gray-100 text-gray-600{% endif %}">
                            {{ d.count }} {{ 'Appts' if is_en else '场预约' }}
                        </div>
                    </a>
                    {% endfor %}
                </div>
            </div>

            <!-- 预约列表 -->
            <div class="overflow-x-auto">
                <table class="w-full text-left border-collapse">
                    <thead>
                        <tr class="border-b bg-gray-50 text-gray-700 text-sm">
                            <th class="p-3">{{ 'Time Slot' if is_en else '时间段' }}</th>
                            <th class="p-3">{{ 'Customer Name' if is_en else '顾客姓名' }}</th>
                            <th class="p-3">{{ 'Phone' if is_en else '电话' }}</th>
                            <th class="p-3">{{ 'Service' if is_en else '服务项目' }}</th>
                            <th class="p-3">{{ 'Stylist' if is_en else '发型师' }}</th>
                            <th class="p-3">{{ 'Status' if is_en else '状态' }}</th>
                            <th class="p-3">{{ 'Action' if is_en else '操作' }}</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for app in appointments %}
                        <tr class="border-b hover:bg-gray-50">
                            <td class="p-3 font-bold text-indigo-600">{{ app.start_time.split()[1] }} ~ {{ app.end_time.split()[1] }}</td>
                            <td class="p-3 font-bold">{{ app.customer_name }}</td>
                            <td class="p-3 text-gray-600">{{ app.customer_phone }}</td>
                            <td class="p-3">{{ app.service_name }}</td>
                            <td class="p-3 font-medium text-gray-800">{{ app.stylist }}</td>
                            <td class="p-3">
                                <span class="bg-green-100 text-green-800 px-2 py-1 rounded text-xs font-bold">{{ app.status }}</span>
                            </td>
                            <td class="p-3">
                                <a href="/admin/appointment/delete/{{ app.id }}" onclick="return confirm('{{ 'Are you sure to cancel this appointment?' if is_en else '确定取消此预约吗？' }}')" class="text-red-500 hover:text-red-700 text-sm font-bold">{{ 'Delete' if is_en else '删除' }}</a>
                            </td>
                        </tr>
                        {% else %}
                        <tr>
                            <td colspan="7" class="p-8 text-center text-gray-400">{{ 'No appointments on this date (' + selected_date + ')' if is_en else '该日期 (' + selected_date + ') 暂无预约记录' }}</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </main>

    <!-- 后台代客预约弹窗 -->
    <div id="adminBookModal" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 hidden">
        <div class="bg-white p-6 rounded-xl shadow-xl max-w-lg w-full max-h-[90vh] overflow-y-auto">
            <div class="flex justify-between items-center mb-4 border-b pb-2">
                <h3 class="text-lg font-bold text-indigo-600">{{ 'Manual Booking by Admin' if is_en else '后台手动代客预约' }}</h3>
                <button onclick="closeAdminBookModal()" class="text-gray-500 font-bold text-xl">&times;</button>
            </div>
            {% if error %}
            <div class="mb-4 p-3 bg-red-100 text-red-700 rounded text-sm font-bold">{{ error }}</div>
            {% endif %}
            <form action="/admin/appointment/add" method="POST">
                <div class="mb-3">
                    <label class="block text-sm font-bold mb-1">{{ 'Select Existing Member (Optional)' if is_en else '选择已有会员 (可选)' }}</label>
                    <select name="customer_id" onchange="fillAdminCustomer(this)" class="w-full border rounded p-2 text-sm">
                        <option value="">{{ '-- New customer or manual input --' if is_en else '-- 手动输入新客信息 --' }}</option>
                        {% for c in customers %}
                        <option value="{{ c.id }}" data-name="{{ c.name }}" data-phone="{{ c.phone }}">{{ c.name }} ({{ c.phone }})</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="grid grid-cols-2 gap-3 mb-3">
                    <div>
                        <label class="block text-sm font-bold mb-1">{{ 'Customer Name' if is_en else '顾客姓名' }}</label>
                        <input type="text" name="customer_name" id="admin_cust_name" class="w-full border rounded p-2 text-sm" required>
                    </div>
                    <div>
                        <label class="block text-sm font-bold mb-1">{{ 'Customer Phone' if is_en else '顾客电话' }}</label>
                        <input type="text" name="customer_phone" id="admin_cust_phone" class="w-full border rounded p-2 text-sm" required>
                    </div>
                </div>
                <div class="mb-3">
                    <label class="block text-sm font-bold mb-1">{{ 'Select Service' if is_en else '选择服务项目' }}</label>
                    <select name="service_id" class="w-full border rounded p-2 text-sm" required>
                        {% for s in services %}
                        <option value="{{ s.id }}">{{ s.name }} (RM {{ "%.2f"|format(s.price) }} / {{ s.duration }} mins)</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="mb-3">
                    <label class="block text-sm font-bold mb-1">{{ 'Select Stylist' if is_en else '选择发型师' }}</label>
                    <select name="stylist" class="w-full border rounded p-2 text-sm" required>
                        {% for st in stylists %}
                        <option value="{{ st.name }} ({{ st.title }})">{{ st.name }} ({{ st.title }})</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="grid grid-cols-2 gap-3 mb-4">
                    <div>
                        <label class="block text-sm font-bold mb-1">{{ 'Booking Date' if is_en else '预约日期' }}</label>
                        <input type="date" name="booking_date" value="{{ selected_date }}" class="w-full border rounded p-2 text-sm" required>
                    </div>
                    <div>
                        <label class="block text-sm font-bold mb-1">{{ 'Time Slot' if is_en else '预约时间段' }}</label>
                        <select name="booking_time" class="w-full border rounded p-2 text-sm" required>
                            {% for t in timeslots %}
                            <option value="{{ t }}">{{ t }}</option>
                            {% endfor %}
                        </select>
                    </div>
                </div>
                <div class="flex justify-end gap-2">
                    <button type="button" onclick="closeAdminBookModal()" class="bg-gray-300 px-4 py-2 rounded text-sm font-bold">{{ 'Cancel' if is_en else '取消' }}</button>
                    <button type="submit" class="bg-indigo-600 text-white px-4 py-2 rounded text-sm font-bold hover:bg-indigo-700">{{ 'Confirm Add Appointment' if is_en else '确认添加预约' }}</button>
                </div>
            </form>
        </div>
    </div>

    <script>
        function changeAdminDate(dateStr) {
            window.location.href = "/admin/appointments?date=" + dateStr;
        }
        function openAdminBookModal() {
            document.getElementById('adminBookModal').classList.remove('hidden');
        }
        function closeAdminBookModal() {
            document.getElementById('adminBookModal').classList.add('hidden');
        }
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
            
            wd_map = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            wd_str = wd_map[d.weekday()]
            if d_str == today_str:
                wd_str = "今天"
                
            cnt = conn.execute("SELECT COUNT(*) FROM appointments WHERE start_time LIKE ? AND status = 'CONFIRMED'", (f"{d_str}%",)).fetchone()[0]
            
            date_strip.append({
                "date_str": d_str,
                "display_date": d.strftime("%m-%d"),
                "weekday": wd_str,
                "count": cnt
            })
            
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
            
    lang = session.get("lang", "zh")
    is_en = (lang == "en")
        
    return render_template_string(
        ADMIN_APPOINTMENTS_TEMPLATE, 
        appointments=appointments, 
        date_strip=date_strip, 
        selected_date=selected_date, 
        today_str=today_str,
        services=services,
        stylists=stylists,
        customers=customers,
        timeslots=timeslots,
        error=None,
        is_en=is_en
    )

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
            return f"<script>alert('预约失败：发型师在该时间段已有冲突！'); window.history.back();</script>"

        cursor = conn.cursor()
        cursor.execute("SELECT id FROM customers WHERE phone = ?", (c_phone,))
        cust = cursor.fetchone()
        if not cust:
            token = secrets.token_hex(8)
            cursor.execute("INSERT INTO customers (name, phone, token) VALUES (?, ?, ?)", (c_name, c_phone, token))
            cust_id = cursor.lastrowid
        else:
            cust_id = cust["id"]
            # 如果已有会员名字更新了也可以选择不覆盖，或者保留原有名字
            
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
        
    lang = session.get("lang", "zh")
    is_en = (lang == "en")
    
    html = """
        <div class="bg-white p-6 rounded shadow">
            <h2 class="text-xl font-bold mb-4">{}</h2>
            <table class="w-full text-left border-collapse">
                <thead>
                    <tr class="border-b bg-gray-50">
                        <th class="p-2">{}</th>
                        <th class="p-2">{}</th>
                        <th class="p-2">{}</th>
                        <th class="p-2">{}</th>
                        <th class="p-2">{}</th>
                        <th class="p-2">{}</th>
                        <th class="p-2">{}</th>
                    </tr>
                </thead>
                <tbody>
                    {% for order in orders %}
                    <tr class="border-b {% if order.status == 'VOID' %}bg-red-50 text-gray-400 line-through{% endif %}">
                        <td class="p-2 font-bold text-indigo-600">{{ order.order_no }}</td>
                        <td class="p-2">{{ order.created_at }}</td>
                        <td class="p-2">{{ order.customer_name }} ({{ order.customer_phone }})</td>
                        <td class="p-2 font-bold">RM {{ "%.2f"|format(order.total_amount) }}</td>
                        <td class="p-2 text-sm text-gray-600">{{ order.remark if order.remark else '-' }}</td>
                        <td class="p-2 font-bold">{% if order.status == 'VOID' %}<span class="text-red-650">{{ '【Voided】' if is_en else '【已作废】' }}</span>{% else %}{{ order.payment_details }}{% endif %}</td>
                        <td class="p-2">
                            {% if order.status != 'VOID' %}
                            <a href="/admin/order/void/{{ order.id }}" onclick="return confirm('{{ 'Are you sure to void this order? Credits or balances will be rolled back.' if is_en else '确定要作废此订单吗？若涉及充值或余额扣款将自动回滚。' }}')" class="text-red-500 font-bold text-sm">{{ 'Void Order' if is_en else '作废订单' }}</a>
                            {% else %}
                            <span class="text-gray-400 text-sm">{{ 'Archived' if is_en else '已归档' }}</span>
                            {% endif %}
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    """
    
    if is_en:
        filled = html.format(
            "Order History Management (Voided orders will be automatically archived)",
            "Order No", "Time", "Customer", "Amount (RM)", "Remark", "Payment/Status", "Action"
        )
    else:
        filled = html.format(
            "历史订单管理 (作废订单将自动归档保留)",
            "单号", "时间", "顾客姓名", "金额 (RM)", "备注", "支付/状态", "操作"
        )

    return render_template_string(LAYOUT_TEMPLATE.replace("{% block content %}{% endblock %}", filled), orders=orders, is_en=is_en)

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
        
    lang = session.get("lang", "zh")
    is_en = (lang == "en")
    
    html = """
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div class="md:col-span-2 bg-white p-6 rounded-lg shadow">
                <h2 class="text-xl font-bold mb-4">{}</h2>
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
                <h2 class="text-xl font-bold mb-4">{}</h2>
                <div id="order-items" class="min-h-[120px] border-b mb-4 pb-2">
                    <p class="text-gray-400">{}</p>
                </div>
                <div class="text-xl font-bold mb-4">
                    {}: <span id="total-amount" class="text-red-600">RM 0.00</span>
                </div>
                <form action="/admin/checkout" method="POST">
                    <input type="hidden" name="cart_data" id="cart_data_input">
                    <div class="mb-3">
                        <label class="block text-sm font-medium">{}</label>
                        <select name="customer_phone" id="cust_select" onchange="fillCustomer(this)" class="w-full border rounded p-2">
                            <option value="">{}</option>
                            {% for c in customers %}
                            <option value="{{ c.phone }}" data-name="{{ c.name }}">{{ c.name }} ({{ c.phone }}) - Balance: RM {{ c.credits }}</option>
                            {% endfor %}
                        </select>
                    </div>
                    <div class="mb-3">
                        <label class="block text-sm font-medium">{}</label>
                        <input type="text" name="customer_name" id="cust_name" class="w-full border rounded p-2" required>
                    </div>
                    <div class="mb-3">
                        <label class="block text-sm font-medium">{}</label>
                        <input type="text" name="customer_phone_input" id="cust_phone" class="w-full border rounded p-2" required>
                    </div>
                    <div class="mb-3">
                        <label class="block text-sm font-medium">{}</label>
                        <input type="text" name="remark" class="w-full border rounded p-2" placeholder="{}">
                    </div>
                    <div class="mb-4">
                        <label class="block text-sm font-medium mb-1">{}</label>
                        <select name="payment_method" class="w-full border rounded p-2">
                            <option value="Cash">Cash</option>
                            <option value="Credit Card">Credit Card</option>
                            <option value="TNG / QRPay">TNG / QRPay</option>
                            <option value="Credit Balance Deduct">Credit Balance Deduct</option>
                        </select>
                    </div>
                    <button class="w-full bg-green-600 text-white font-bold py-3 rounded hover:bg-green-700">{}</button>
                </form>
            </div>
        </div>
        <script>
            let cart = [];
            function addToOrder(name, price) {
                cart.push({name, price});
                renderCart();
            }
            function renderCart() {
                const container = document.getElementById('order-items');
                let total = 0;
                container.innerHTML = '';
                cart.forEach((item) => {
                    total += item.price;
                    container.innerHTML += `<div class="flex justify-between py-1"><span>${item.name}</span><span>RM ${item.price.toFixed(2)}</span></div>`;
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
    
    if is_en:
        filled = html.format(
            "Select Services / Packages / Products (POS)", "Current Order Checkout", "Click items on the left to add",
            "Total Amount", "Select Existing Member", "-- New customer or manual input --",
            "Customer Name", "Customer Phone", "Remark / Note", "Optional note (e.g. promo used)",
            "Payment Method", "Complete Checkout & Record"
        )
    else:
        filled = html.format(
            "点选服务 / 套餐 / 产品 (POS)", "当前订单结账", "点击左侧项目加入订单",
            "总金额", "选择已有会员", "-- 新客或手动输入 --",
            "顾客姓名", "顾客电话", "备注 (Remark)", "可选填备注信息",
            "支付方式", "完成收款与记账"
        )

    return render_template_string(LAYOUT_TEMPLATE.replace("{% block content %}{% endblock %}", filled), services=services, customers=customers)

@app.route("/admin/checkout", methods=["POST"])
@admin_required
def checkout():
    try:
        cart_data = json.loads(request.form.get("cart_data", "[]"))
        name = request.form.get("customer_name")
        phone = request.form.get("customer_phone_input") or request.form.get("customer_phone")
        pay_method = request.form.get("payment_method")
        remark = request.form.get("remark", "").strip()
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
                    return "结算失败：该顾客 Credit 余额不足！ / Checkout failed: Insufficient credit balance!", 400
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
                
        # 纯英文 Invoice 凭证
        return f"""
            <div style="max-width:500px;margin:50px auto;padding:25px;border:1px solid #ddd;font-family:sans-serif;border-radius:8px;background:#fff;box-shadow:0 4px 6px rgba(0,0,0,0.1);">
                <h2 style="color:#4f46e5;margin-top:0;text-align:center;">Dew Hair Salon Invoice</h2>
                <hr style="border:0;border-top:1px solid #eee;margin:15px 0;">
                <p><strong>Order No:</strong> {order_no}</p>
                <p><strong>Customer:</strong> {name} ({phone})</p>
                <p><strong>Total Amount:</strong> RM {total:.2f}</p>
                <p><strong>Payment Method:</strong> {pay_method}</p>
                {"<p><strong>Remark:</strong> " + remark + "</p>" if remark else ""}
                <p><strong>Customer Portal Link:</strong> <br><a href="/customer/{cust_token}" target="_blank" style="color:#4f46e5;word-break:break-all;">Click to view member profile</a></p>
                <hr style="border:0;border-top:1px solid #eee;margin:15px 0;">
                <div style="text-align:center;">
                    <a href="/admin/pos" style="display:inline-block;padding:10px 20px;background:#4f46e5;color:white;font-weight:bold;text-decoration:none;border-radius:6px;">Back to POS Checkout</a>
                </div>
            </div>
        """
    except Exception as e:
        return f"Checkout Error: {str(e)}", 500

BOOKING_CALENDAR_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dew Hair Salon - 在线预约日历</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 min-h-screen p-4 md:p-8">
    <div class="max-w-3xl mx-auto bg-white p-6 md:p-8 rounded-xl shadow-md">
        <h2 class="text-3xl font-extrabold text-center text-indigo-600 mb-2">Dew Hair Salon Online Booking</h2>
        <p class="text-center text-sm text-gray-500 mb-6">Business Hours: {{ open_time }} - {{ close_time }} (Swipe or click date cards to select)</p>
        
        {% if error %}
        <div class="mb-4 p-3 bg-red-100 text-red-700 rounded text-sm font-bold">{{ error }}</div>
        {% endif %}
        
        <form action="/book" method="POST" id="bookingForm">
            <!-- 选择服务项目 -->
            <div class="mb-5">
                <label class="block text-sm font-bold mb-2">1. Select Hair Service</label>
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

            <!-- 选择发型师 -->
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

            <!-- 优化：横向滑动按天查看日期栏 + 隐藏/联动日期输入 -->
            <div class="mb-5">
                <div class="flex justify-between items-center mb-2">
                    <label class="block text-sm font-bold">3. Select Booking Date (Swipeable)</label>
                    <input type="date" name="booking_date" id="booking_date" value="{{ selected_date }}" min="{{ today_str }}" class="border rounded px-2 py-1 text-sm text-indigo-600 font-bold" onchange="syncDateInput(this.value)">
                </div>
                
                <!-- 左右滑动卡片条 -->
                <div class="flex gap-2 overflow-x-auto pb-2 scrollbar-thin" id="dateStripContainer">
                    {% for d in date_strip %}
                    <div onclick="selectDateCard('{{ d.date_str }}')" class="date-card flex-shrink-0 w-24 p-3 rounded-xl border text-center cursor-pointer transition {% if d.date_str == selected_date %}bg-indigo-600 text-white border-indigo-600 shadow-md font-bold{% else %}bg-white text-gray-700 hover:border-indigo-400{% endif %}" data-date="{{ d.date_str }}">
                        <div class="text-xs opacity-80">{{ d.weekday }}</div>
                        <div class="text-sm font-bold my-1">{{ d.display_date }}</div>
                        <div class="text-[10px] opacity-70">{{ d.year }}</div>
                    </div>
                    {% endfor %}
                </div>
            </div>

            <!-- 选择时间段 -->
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

            <!-- 顾客填写信息 -->
            <div class="border-t pt-4 grid grid-cols-1 sm:grid-cols-2 gap-4 mb-6">
                <div>
                    <label class="block text-sm font-bold mb-1">Your Full Name</label>
                    <input type="text" name="customer_name" class="w-full border rounded p-3" placeholder="Enter your name" required>
                </div>
                <div>
                    <label class="block text-sm font-bold mb-1">Your Phone Number (Credential)</label>
                    <input type="text" name="customer_phone" class="w-full border rounded p-3" placeholder="Enter mobile number" required>
                </div>
            </div>

            <button class="w-full bg-indigo-600 text-white font-bold py-3.5 rounded-lg text-lg hover:bg-indigo-700 shadow-md">Confirm & Submit Appointment</button>
        </form>
    </div>
    <script>
        const timeLabels = document.querySelectorAll('input[name="booking_time"]');
        timeLabels.forEach(input => {
            input.addEventListener('change', function() {
                document.querySelectorAll('input[name="booking_time"]').forEach(i => {
                    i.parentElement.classList.remove('bg-indigo-600', 'text-white', 'border-indigo-600');
                    i.parentElement.classList.add('bg-white', 'text-black');
                });
                if(this.checked) {
                    this.parentElement.classList.remove('bg-white', 'text-black');
                    this.parentElement.classList.add('bg-indigo-600', 'text-white', 'border-indigo-600');
                }
            });
        });

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

        function syncDateInput(dateStr) {
            selectDateCard(dateStr);
        }
    </script>
</body>
</html>
"""

@app.route("/book", methods=["GET", "POST"])
def public_booking():
    open_time_str = get_setting("open_time", "10:00")
    close_time_str = get_setting("close_time", "20:00")
    closed_wd = int(get_setting("closed_weekdays", "1"))
    
    if request.method == "POST":
        service_id = request.form.get("service_id")
        stylist = request.form.get("stylist")
        b_date = request.form.get("booking_date")
        b_time = request.form.get("booking_time")
        c_name = request.form.get("customer_name")
        c_phone = request.form.get("customer_phone")
        
        try:
            d_obj = datetime.strptime(b_date, "%Y-%m-%d")
            if d_obj.weekday() == closed_wd:
                return public_booking_render(error="预约失败：该日期为沙龙固定休息日，请选择其他日期！")
        except:
            return public_booking_render(error="日期格式错误！")
            
        with get_db() as conn:
            holiday = conn.execute("SELECT * FROM holidays WHERE date_str = ?", (b_date,)).fetchone()
            if holiday:
                return public_booking_render(error=f"预约失败：该天为临时闭店日 ({holiday['reason']})，无法预约！")
                
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
                return public_booking_render(error=f"预约失败：发型师 {stylist} 在此时间段已有预约冲突，请选择其他时间！")

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
                <h2 style="color:green;margin-top:0;">Booking Success!</h2>
                <p>Thank you, <strong>{c_name}</strong>! Your appointment has been recorded.</p>
                <p><strong>Time:</strong> {start_str} ~ {end_str.split()[1]}</p>
                <p><strong>Stylist:</strong> {stylist}</p>
                <hr style="margin:20px 0;">
                <p>This is your <strong>Member Profile & History Link</strong>:</p>
                <a href="/customer/{cust_token}" style="display:inline-block;padding:12px 20px;background:#4f46e5;color:white;text-decoration:none;border-radius:6px;font-weight:bold;">Enter My Member Portal</a>
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
            return "Invalid member token", 404
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
            <h2 style="color:#4f46e5;margin-top:0;">Dew Hair Salon - Member Center</h2>
            <p><strong>Name:</strong> {{ cust.name }}</p>
            <p><strong>Phone:</strong> {{ cust.phone }}</p>
            <div style="background:#f0fdf4;border:1px solid #bbf7d0;padding:12px;border-radius:6px;margin:15px 0;">
                <span style="font-size:14px;color:#166534;">Current Credit Balance:</span>
                <div style="font-size:24px;font-weight:bold;color:#15803d;">RM {{ "%.2f"|format(cust.credits) }}</div>
            </div>
            <hr>
            <h3>My Appointments</h3>
            {% if appointments %}
            <ul style="padding-left:20px;font-size:14px;">
                {% for a in appointments %}
                <li style="margin-bottom:6px;">{{ a.start_time }} - <strong>{{ a.service_name }}</strong> (Stylist: {{ a.stylist }}) - <span style="color:green;">{{ a.status }}</span></li>
                {% endfor %}
            </ul>
            {% else %}
            <p style="color:gray;font-size:14px;">No appointments yet.</p>
            {% endif %}
            <hr>
            <h3>Consumption History</h3>
            {% if orders %}
            <ul style="padding-left:20px;font-size:14px;">
                {% for o in orders %}
                <li style="margin-bottom:6px; {% if o.status == 'VOID' %}color:#9ca3af;text-decoration:line-through;{% endif %}">
                    {{ o.created_at }} - <strong>{{ o.item_name }}</strong> {% if o.remark %}(Remark: {{ o.remark }}){% endif %} (RM {{ "%.2f"|format(o.price) }}) 
                    {% if o.status == 'VOID' %}<span style="color:red;font-weight:bold;">[Voided]</span>{% else %}[Pay: {{ o.payment_details }}]{% endif %}
                </li>
                {% endfor %}
            </ul>
            {% else %}
            <p style="color:gray;font-size:14px;">No consumption history yet.</p>
            {% endif %}
        </div>
    """, cust=cust, orders=orders, appointments=appointments)

@app.route("/admin/reports")
@admin_required
def admin_reports():
    with get_db() as conn:
        order_count = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'NORMAL'").fetchone()[0]
        total_revenue = conn.execute("SELECT SUM(total_amount) FROM orders WHERE status = 'NORMAL'").fetchone()[0] or 0.0
        customer_count = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        item_count = conn.execute("SELECT COUNT(*) FROM order_items i JOIN orders o ON i.order_id = o.id WHERE o.status = 'NORMAL'").fetchone()[0]
        
    lang = session.get("lang", "zh")
    is_en = (lang == "en")
    
    html = """
        <div class="bg-white p-6 rounded shadow">
            <h2 class="text-xl font-bold mb-6 border-b pb-2">{}</h2>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-center">
                <div class="bg-indigo-50 p-4 rounded shadow">
                    <div class="text-gray-500 text-sm">{}</div>
                    <div class="text-3xl font-bold text-indigo-600">{{ order_count }}</div>
                </div>
                <div class="bg-green-50 p-4 rounded shadow">
                    <div class="text-gray-500 text-sm">{}</div>
                    <div class="text-3xl font-bold text-green-600">RM {{ "%.2f"|format(total_revenue) }}</div>
                </div>
                <div class="bg-yellow-50 p-4 rounded shadow">
                    <div class="text-gray-500 text-sm">{}</div>
                    <div class="text-3xl font-bold text-yellow-600">{{ item_count }}</div>
                </div>
                <div class="bg-purple-50 p-4 rounded shadow">
                    <div class="text-gray-500 text-sm">{}</div>
                    <div class="text-3xl font-bold text-purple-600">{{ customer_count }}</div>
                </div>
            </div>
        </div>
    """
    
    if is_en:
        filled = html.format(
            "90-Day Historical Data Dashboard",
            "Valid Orders", "Total Revenue (RM)", "Item Sales Qty", "Total Members"
        )
    else:
        filled = html.format(
            "90天历史数据看板",
            "有效订单数", "总营业额 (RM)", "项目销售量", "会员总数"
        )

    return render_template_string(LAYOUT_TEMPLATE.replace("{% block content %}{% endblock %}", filled), order_count=order_count, total_revenue=total_revenue, customer_count=customer_count, item_count=item_count)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=False)
