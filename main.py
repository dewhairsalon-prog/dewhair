import os
import sqlite3
import secrets
import hmac
import json
import urllib.parse
from datetime import datetime, timedelta
from functools import wraps
from flask import (
    Flask, request, redirect, url_for, session, render_template_string, jsonify
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "123456")

# 彻底修复数据丢失问题：强制使用绝对持久化目录
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
                status TEXT DEFAULT 'NORMAL',
                remark TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(customer_id) REFERENCES customers(id)
            );
        """)
        try:
            conn.execute("ALTER TABLE orders ADD COLUMN remark TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass

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
            <h1 class="text-xl font-bold">Dew Hair Salon 管理后台</h1>
            <div class="flex space-x-2 text-sm font-bold">
                <a href="/admin/pos" class="hover:bg-indigo-700 px-2 py-1 rounded">POS 收银</a>
                <a href="/admin/appointments" class="hover:bg-indigo-700 px-2 py-1 rounded">预约管理</a>
                <a href="/admin/customers" class="hover:bg-indigo-700 px-2 py-1 rounded">会员与历史记录</a>
                <a href="/admin/orders" class="hover:bg-indigo-700 px-2 py-1 rounded">订单历史</a>
                <a href="/admin" class="hover:bg-indigo-700 px-2 py-1 rounded">营业、项目与员工</a>
                <a href="/admin/reports" class="hover:bg-indigo-700 px-2 py-1 rounded">90天报表</a>
                <a href="/admin/logout" class="bg-red-500 px-2 py-1 rounded hover:bg-red-600">退出</a>
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
        <div style="max-width:400px;margin:100px auto;padding:20px;border:1px solid #ccc;text-align:center;font-family:sans-serif;border-radius:8px;background:white;">
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
        
    return render_template_string(LAYOUT_TEMPLATE.replace("{% block content %}{% endblock %}", """
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div class="md:col-span-2 space-y-6">
                <!-- 营业与休息日设置 -->
                <div class="bg-white p-6 rounded shadow">
                    <h2 class="text-xl font-bold mb-4">营业与休息日设置</h2>
                    <form action="/admin/settings/update" method="POST" class="grid grid-cols-2 md:grid-cols-4 gap-4 items-end mb-4">
                        <div>
                            <label class="block text-sm font-medium">开门时间</label>
                            <input type="time" name="open_time" value="{{ open_time }}" class="w-full border rounded p-2" required>
                        </div>
                        <div>
                            <label class="block text-sm font-medium">关门时间</label>
                            <input type="time" name="close_time" value="{{ close_time }}" class="w-full border rounded p-2" required>
                        </div>
                        <div>
                            <label class="block text-sm font-medium">每周固定休息日</label>
                            <select name="closed_weekdays" class="w-full border rounded p-2">
                                <option value="0" {% if closed_wd == '0' %}selected{% endif %}>周日休息</option>
                                <option value="1" {% if closed_wd == '1' %}selected{% endif %}>周一休息</option>
                                <option value="2" {% if closed_wd == '2' %}selected{% endif %}>周二休息</option>
                                <option value="-1" {% if closed_wd == '-1' %}selected{% endif %}>无固定休息日</option>
                            </select>
                        </div>
                        <div>
                            <button class="w-full bg-indigo-600 text-white font-bold py-2 rounded hover:bg-indigo-700">保存设置</button>
                        </div>
                    </form>

                    <hr class="my-4">
                    <h3 class="font-bold mb-2">添加特定临时休息日</h3>
                    <form action="/admin/holiday/add" method="POST" class="flex gap-2">
                        <input type="date" name="date_str" class="border rounded p-2" required>
                        <input type="text" name="reason" placeholder="休息原因 (如: 公共假期/员工培训)" class="border rounded p-2 flex-grow">
                        <button class="bg-red-500 text-white px-4 py-2 rounded font-bold hover:bg-red-600">添加闭店日</button>
                    </form>
                    
                    <div class="mt-4">
                        <h4 class="text-sm font-bold text-gray-600 mb-2">已设定的临时休息日：</h4>
                        <div class="flex flex-wrap gap-2">
                            {% for h in holidays %}
                            <span class="bg-red-50 text-red-700 px-3 py-1 rounded border border-red-200 text-sm flex items-center gap-2">
                                {{ h.date_str }} ({{ h.reason }})
                                <a href="/admin/holiday/delete/{{ h.id }}" class="font-bold hover:text-red-900">×</a>
                            </span>
                            {% else %}
                            <span class="text-gray-400 text-sm">暂无临时闭店日</span>
                            {% endfor %}
                        </div>
                    </div>
                </div>

                <!-- 员工团队管理 -->
                <div class="bg-white p-6 rounded shadow">
                    <h2 class="text-xl font-bold mb-4">发型师 / 员工团队管理</h2>
                    <table class="w-full text-left">
                        <thead><tr class="border-b"><th class="p-2">姓名</th><th class="p-2">职级/头衔</th><th class="p-2">操作</th></tr></thead>
                        <tbody>
                            {% for st in stylists %}
                            <tr class="border-b">
                                <td class="p-2 font-bold text-indigo-600">{{ st.name }}</td>
                                <td class="p-2">{{ st.title }}</td>
                                <td class="p-2">
                                    <a href="/admin/stylist/delete/{{ st.id }}" onclick="return confirm('确定要删除该发型师吗？')" class="text-red-500 text-sm font-bold">删除</a>
                                </td>
                            </tr>
                            {% else %}
                            <tr><td colspan="3" class="p-2 text-gray-400">暂无员工</td></tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>

                <!-- 项目与套餐列表 -->
                <div class="bg-white p-6 rounded shadow">
                    <h2 class="text-xl font-bold mb-4">项目与充值套餐管理</h2>
                    <table class="w-full text-left">
                        <thead><tr class="border-b"><th class="p-2">分类</th><th class="p-2">名称</th><th class="p-2">售价 (RM)</th><th class="p-2">获得 Credit (RM)</th><th class="p-2">操作</th></tr></thead>
                        <tbody>
                            {% for item in services %}
                            <tr class="border-b">
                                <td class="p-2 font-bold text-indigo-600">{{ item.category_type }}</td>
                                <td class="p-2">{{ item.name }}</td>
                                <td class="p-2 font-bold text-red-600">RM {{ "%.2f"|format(item.price) }}</td>
                                <td class="p-2 font-bold text-green-600">{% if item.category_type == 'Packages' %}RM {{ "%.2f"|format(item.credit_value) }}{% else %}-{% endif %}</td>
                                <td class="p-2">
                                    <a href="/admin/service/delete/{{ item.id }}" onclick="return confirm('确定要删除吗？')" class="text-red-500 text-sm font-bold">删除</a>
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- 右侧：添加表单 -->
            <div class="space-y-6">
                <div class="bg-white p-6 rounded shadow">
                    <h2 class="text-xl font-bold mb-4">添加发型师</h2>
                    <form action="/admin/stylist/add" method="POST">
                        <div class="mb-3">
                            <label class="block text-sm font-medium">姓名</label>
                            <input type="text" name="name" class="w-full border rounded p-2" required placeholder="如: Kevin">
                        </div>
                        <div class="mb-3">
                            <label class="block text-sm font-medium">职级 / 简介</label>
                            <input type="text" name="title" class="w-full border rounded p-2" required placeholder="如: 高级造型师">
                        </div>
                        <button class="w-full bg-indigo-600 text-white font-bold py-2 rounded hover:bg-indigo-700">确认添加员工</button>
                    </form>
                </div>

                <div class="bg-white p-6 rounded shadow">
                    <h2 class="text-xl font-bold mb-4">添加服务或套餐</h2>
                    <form action="/admin/service/add" method="POST">
                        <div class="mb-3">
                            <label class="block text-sm font-medium">名称</label>
                            <input type="text" name="name" class="w-full border rounded p-2" required>
                        </div>
                        <div class="mb-3">
                            <label class="block text-sm font-medium">分类类型</label>
                            <select name="category_type" id="cat_type_select" onchange="toggleCreditInput(this)" class="w-full border rounded p-2">
                                <option value="Services">Services (服务项目)</option>
                                <option value="Packages">Packages (储值套餐)</option>
                                <option value="Products">Products (零售产品)</option>
                            </select>
                        </div>
                        <div class="mb-3">
                            <label class="block text-sm font-medium">售价 / 金额 (RM)</label>
                            <input type="number" step="0.01" name="price" class="w-full border rounded p-2" required>
                        </div>
                        <div class="mb-3" id="credit_value_div" style="display:none;">
                            <label class="block text-sm font-medium text-green-600 font-bold">赠送 Credit (RM)</label>
                            <input type="number" step="0.01" name="credit_value" class="w-full border rounded p-2" placeholder="例如: 1200">
                        </div>
                        <div class="mb-4">
                            <label class="block text-sm font-medium">耗时 (分钟)</label>
                            <input type="number" name="duration" class="w-full border rounded p-2" value="30" required>
                        </div>
                        <button class="w-full bg-indigo-600 text-white font-bold py-2 rounded hover:bg-indigo-700">确认添加服务</button>
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
    """), services=services, stylists=stylists, holidays=holidays, open_time=open_time, close_time=close_time, closed_wd=closed_wd)

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
            
    return render_template_string(LAYOUT_TEMPLATE.replace("{% block content %}{% endblock %}", """
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div class="md:col-span-2 bg-white p-6 rounded shadow">
                <div class="flex flex-col md:flex-row justify-between items-start md:items-center mb-4 gap-3">
                    <h2 class="text-xl font-bold">会员列表与消费档案</h2>
                    <form action="/admin/customers" method="GET" class="flex gap-2 w-full md:w-auto">
                        <input type="text" name="q" value="{{ search_query }}" placeholder="搜姓名或手机号..." class="border rounded px-3 py-1 text-sm flex-grow">
                        <button class="bg-indigo-600 text-white px-3 py-1 rounded text-sm font-bold">搜索</button>
                        {% if search_query %}
                        <a href="/admin/customers" class="bg-gray-300 text-gray-700 px-3 py-1 rounded text-sm font-bold flex items-center">重置</a>
                        {% endif %}
                    </form>
                </div>
                <table class="w-full text-left border-collapse">
                    <thead>
                        <tr class="border-b bg-gray-50 text-sm">
                            <th class="p-2">姓名</th>
                            <th class="p-2">电话</th>
                            <th class="p-2">Credit 余额</th>
                            <th class="p-2">专属链接</th>
                            <th class="p-2">操作</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for c in customers %}
                        <tr class="border-b hover:bg-gray-50">
                            <td class="p-2 font-bold">{{ c.name }}</td>
                            <td class="p-2">{{ c.phone }}</td>
                            <td class="p-2 text-green-600 font-bold">RM {{ "%.2f"|format(c.credits) }}</td>
                            <td class="p-2">
                                <a href="/customer/{{ c.token }}" target="_blank" class="text-indigo-600 underline text-sm font-bold">查看页面</a>
                            </td>
                            <td class="p-2">
                                <a href="/admin/customer/detail/{{ c.id }}" class="bg-indigo-600 text-white px-3 py-1 rounded text-xs font-bold hover:bg-indigo-700">查看详情</a>
                            </td>
                        </tr>
                        {% else %}
                        <tr><td colspan="5" class="p-6 text-center text-gray-400">没有找到相关会员</td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            
            <div class="bg-white p-6 rounded shadow h-fit">
                <h2 class="text-xl font-bold mb-4">手动添加会员档案</h2>
                <form action="/admin/customer/add" method="POST">
                    <div class="mb-3">
                        <label class="block text-sm font-medium">会员姓名</label>
                        <input type="text" name="name" class="w-full border rounded p-2" required>
                    </div>
                    <div class="mb-3">
                        <label class="block text-sm font-medium">电话号码 (唯一凭证)</label>
                        <input type="text" name="phone" class="w-full border rounded p-2" required>
                    </div>
                    <div class="mb-4">
                        <label class="block text-sm font-medium">初始赠送 Credit (RM)</label>
                        <input type="number" step="0.01" name="credits" class="w-full border rounded p-2" value="0.00">
                    </div>
                    <button class="w-full bg-indigo-600 text-white font-bold py-2 rounded hover:bg-indigo-700">保存会员</button>
                </form>
            </div>
        </div>
    """), customers=customers, search_query=search_query)

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
    return render_template_string(LAYOUT_TEMPLATE.replace("{% block content %}{% endblock %}", """
        <div class="bg-white p-6 rounded shadow space-y-6">
            <div class="flex justify-between items-center border-b pb-4">
                <div>
                    <h2 class="text-2xl font-bold text-indigo-600">{{ cust.name }} 的会员档案与消费记录</h2>
                    <p class="text-gray-600">电话: {{ cust.phone }} | 专属链接码: {{ cust.token }}</p>
                </div>
                <div class="text-right">
                    <div class="text-sm text-gray-500">账户 Credit 余额</div>
                    <div class="text-2xl font-bold text-green-600">RM {{ "%.2f"|format(cust.credits) }}</div>
                </div>
            </div>

            <div>
                <h3 class="text-lg font-bold mb-2">历史预约记录</h3>
                <table class="w-full text-left border-collapse">
                    <thead><tr class="border-b bg-gray-50"><th class="p-2">时间段</th><th class="p-2">服务项目</th><th class="p-2">发型师</th><th class="p-2">状态</th></tr></thead>
                    <tbody>
                        {% for a in appointments %}
                        <tr class="border-b"><td class="p-2 font-medium">{{ a.start_time }} ~ {{ a.end_time.split()[1] }}</td><td class="p-2">{{ a.service_name }}</td><td class="p-2">{{ a.stylist }}</td><td class="p-2 text-green-600 font-bold">{{ a.status }}</td></tr>
                        {% else %}
                        <tr><td colspan="4" class="p-2 text-gray-400">暂无预约记录</td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>

            <div>
                <h3 class="text-lg font-bold mb-2">历史消费与订单明细</h3>
                <table class="w-full text-left border-collapse">
                    <thead><tr class="border-b bg-gray-50"><th class="p-2">单号</th><th class="p-2">时间</th><th class="p-2">项目</th><th class="p-2">金额</th><th class="p-2">支付方式</th></tr></thead>
                    <tbody>
                        {% for o in orders %}
                        <tr class="border-b {% if o.status == 'VOID' %}bg-red-50 text-gray-400 line-through{% endif %}">
                            <td class="p-2 font-bold">{{ o.order_no }}</td>
                            <td class="p-2">{{ o.created_at }}</td>
                            <td class="p-2">{{ o.item_name }}</td>
                            <td class="p-2 font-bold">RM {{ "%.2f"|format(o.price) }}</td>
                            <td class="p-2 font-bold">{{ '已作废' if o.status == 'VOID' else o.payment_details }}</td>
                        </tr>
                        {% else %}
                        <tr><td colspan="5" class="p-2 text-gray-400">暂无消费订单</td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            <div>
                <a href="/admin/customers" class="bg-gray-500 text-white px-4 py-2 rounded font-bold hover:bg-gray-600">返回会员列表</a>
            </div>
        </div>
    """), cust=cust, orders=orders, appointments=appointments)

ADMIN_APPOINTMENTS_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dew Hair Salon - 预约管理</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 min-h-screen">
    <nav class="bg-indigo-600 text-white p-4 shadow-md">
        <div class="container mx-auto flex justify-between items-center">
            <h1 class="text-xl font-bold">Dew Hair Salon 管理后台</h1>
            <div class="flex space-x-2 text-sm font-bold">
                <a href="/admin/pos" class="hover:bg-indigo-700 px-2 py-1 rounded">POS 收银</a>
                <a href="/admin/appointments" class="hover:bg-indigo-700 px-2 py-1 rounded bg-indigo-800">预约管理</a>
                <a href="/admin/customers" class="hover:bg-indigo-700 px-2 py-1 rounded">会员与历史记录</a>
                <a href="/admin/orders" class="hover:bg-indigo-700 px-2 py-1 rounded">订单历史</a>
                <a href="/admin" class="hover:bg-indigo-700 px-2 py-1 rounded">营业、项目与员工</a>
                <a href="/admin/reports" class="hover:bg-indigo-700 px-2 py-1 rounded">90天报表</a>
                <a href="/admin/logout" class="bg-red-500 px-2 py-1 rounded hover:bg-red-600">退出</a>
            </div>
        </div>
    </nav>
    <main class="container mx-auto p-6">
        <div class="bg-white p-6 rounded-xl shadow-md mb-6">
            <div class="flex flex-col md:flex-row justify-between items-start md:items-center mb-6 gap-4 border-b pb-4">
                <h2 class="text-xl font-extrabold text-indigo-600">预约记录与时间轴管理</h2>
                <div class="flex items-center gap-3">
                    <label class="text-sm font-bold text-gray-700">切换日期：</label>
                    <input type="date" id="admin_date_picker" value="{{ selected_date }}" class="border rounded-lg p-2 font-medium" onchange="changeAdminDate(this.value)">
                    <button onclick="changeAdminDate('{{ today_str }}')" class="bg-gray-200 hover:bg-gray-300 text-gray-800 px-3 py-2 rounded-lg text-sm font-bold">今天</button>
                    <button onclick="openAdminBookModal()" class="bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg text-sm font-bold shadow">+ 手动代客预约</button>
                </div>
            </div>

            <div class="mb-6">
                <div class="flex gap-2 overflow-x-auto pb-2">
                    {% for d in date_strip %}
                    <a href="/admin/appointments?date={{ d.date_str }}" class="flex-shrink-0 w-24 p-3 rounded-xl border text-center transition {% if d.date_str == selected_date %}bg-indigo-600 text-white border-indigo-600 shadow-md font-bold{% else %}bg-white text-gray-700 hover:border-indigo-400{% endif %}">
                        <div class="text-xs opacity-80">{{ d.weekday }}</div>
                        <div class="text-sm font-bold my-1">{{ d.display_date }}</div>
                        <div class="text-[10px] bg-opacity-25 py-0.5 px-1 rounded {% if d.date_str == selected_date %}bg-indigo-800 text-white{% else %}bg-gray-100 text-gray-600{% endif %}">
                            {{ d.count }} 场预约
                        </div>
                    </a>
                    {% endfor %}
                </div>
            </div>

            <div class="overflow-x-auto">
                <table class="w-full text-left border-collapse">
                    <thead>
                        <tr class="border-b bg-gray-50 text-gray-700 text-sm">
                            <th class="p-3">时间段</th>
                            <th class="p-3">顾客姓名</th>
                            <th class="p-3">电话</th>
                            <th class="p-3">服务项目</th>
                            <th class="p-3">发型师</th>
                            <th class="p-3">状态</th>
                            <th class="p-3">操作</th>
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
                            <td class="p-3"><span class="bg-green-100 text-green-800 px-2 py-1 rounded text-xs font-bold">{{ app.status }}</span></td>
                            <td class="p-3">
                                <a href="/admin/appointment/delete/{{ app.id }}" onclick="return confirm('确定取消此预约吗？')" class="text-red-500 hover:text-red-700 text-sm font-bold">删除</a>
                            </td>
                        </tr>
                        {% else %}
                        <tr><td colspan="7" class="p-8 text-center text-gray-400">该日期 ({{ selected_date }}) 暂无预约记录</td></tr>
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
                <h3 class="text-lg font-bold text-indigo-600">后台手动代客预约</h3>
                <button onclick="closeAdminBookModal()" class="text-gray-500 font-bold text-xl">&times;</button>
            </div>
            <form action="/admin/appointment/add" method="POST">
                <div class="mb-3">
                    <label class="block text-sm font-bold mb-1">选择已有会员 (可选)</label>
                    <select name="customer_id" onchange="fillAdminCustomer(this)" class="w-full border rounded p-2 text-sm">
                        <option value="">-- 手动输入新客信息 --</option>
                        {% for c in customers %}
                        <option value="{{ c.id }}" data-name="{{ c.name }}" data-phone="{{ c.phone }}">{{ c.name }} ({{ c.phone }})</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="grid grid-cols-2 gap-3 mb-3">
                    <div>
                        <label class="block text-sm font-bold mb-1">顾客姓名</label>
                        <input type="text" name="customer_name" id="admin_cust_name" class="w-full border rounded p-2 text-sm" required>
                    </div>
                    <div>
                        <label class="block text-sm font-bold mb-1">顾客电话</label>
                        <input type="text" name="customer_phone" id="admin_cust_phone" class="w-full border rounded p-2 text-sm" required>
                    </div>
                </div>
                <div class="mb-3">
                    <label class="block text-sm font-bold mb-1">选择服务项目</label>
                    <select name="service_id" class="w-full border rounded p-2 text-sm" required>
                        {% for s in services %}
                        <option value="{{ s.id }}">{{ s.name }} (RM {{ "%.2f"|format(s.price) }} / {{ s.duration }}分钟)</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="mb-3">
                    <label class="block text-sm font-bold mb-1">选择发型师</label>
                    <select name="stylist" class="w-full border rounded p-2 text-sm" required>
                        {% for st in stylists %}
                        <option value="{{ st.name }} ({{ st.title }})">{{ st.name }} ({{ st.title }})</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="grid grid-cols-2 gap-3 mb-4">
                    <div>
                        <label class="block text-sm font-bold mb-1">预约日期</label>
                        <input type="date" name="booking_date" value="{{ selected_date }}" class="w-full border rounded p-2 text-sm" required>
                    </div>
                    <div>
                        <label class="block text-sm font-bold mb-1">预约时间段</label>
                        <select name="booking_time" class="w-full border rounded p-2 text-sm" required>
                            {% for t in timeslots %}
                            <option value="{{ t }}">{{ t }}</option>
                            {% endfor %}
                        </select>
                    </div>
                </div>
                <div class="flex justify-end gap-2">
                    <button type="button" onclick="closeAdminBookModal()" class="bg-gray-300 px-4 py-2 rounded text-sm font-bold">取消</button>
                    <button type="submit" class="bg-indigo-600 text-white px-4 py-2 rounded text-sm font-bold hover:bg-indigo-700">确认添加预约</button>
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
            wd_map = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            wd_str = wd_map[d.weekday()]
            if d_str == today_str: wd_str = "今天"
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
        
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM customers WHERE phone = ?", (c_phone,))
        cust = cursor.fetchone()
        if not cust:
            token = secrets.token_hex(8)
            cursor.execute("INSERT INTO customers (name, phone, token) VALUES (?, ?, ?)", (c_name, c_phone, token))
            cust_id = cursor.lastrowid
        else:
            cust_id = cust["id"]
            
        cursor.execute("INSERT INTO appointments (customer_id, service_id, stylist, start_time, end_time) VALUES (?, ?, ?, ?, ?)", (cust_id, service_id, stylist, start_str, end_str))
        
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
            SELECT o.*, c.name as customer_name, c.phone as customer_phone, c.token as customer_token 
            FROM orders o 
            JOIN customers c ON o.customer_id = c.id 
            ORDER BY o.created_at DESC
        """).fetchall()
    return render_template_string(LAYOUT_TEMPLATE.replace("{% block content %}{% endblock %}", """
        <div class="bg-white p-6 rounded shadow">
            <h2 class="text-xl font-bold mb-4">历史订单管理与 WhatsApp 发送单据</h2>
            <table class="w-full text-left border-collapse">
                <thead>
                    <tr class="border-b bg-gray-50 text-sm">
                        <th class="p-2">单号</th>
                        <th class="p-2">时间</th>
                        <th class="p-2">顾客姓名与电话</th>
                        <th class="p-2">金额 (RM)</th>
                        <th class="p-2">支付/状态</th>
                        <th class="p-2">备注</th>
                        <th class="p-2">操作</th>
                    </tr>
                </thead>
                <tbody>
                    {% for order in orders %}
                    <tr class="border-b {% if order.status == 'VOID' %}bg-red-50 text-gray-400 line-through{% endif %}">
                        <td class="p-2 font-bold text-indigo-600">
                            <a href="/admin/order/invoice/{{ order.id }}" class="underline hover:text-indigo-800">{{ order.order_no }}</a>
                        </td>
                        <td class="p-2">{{ order.created_at }}</td>
                        <td class="p-2">{{ order.customer_name }} ({{ order.customer_phone }})</td>
                        <td class="p-2 font-bold">RM {{ "%.2f"|format(order.total_amount) }}</td>
                        <td class="p-2 font-bold">{% if order.status == 'VOID' %}<span class="text-red-600">【已作废】</span>{% else %}{{ order.payment_details }}{% endif %}</td>
                        <td class="p-2">
                            <form action="/admin/order/remark/{{ order.id }}" method="POST" class="flex gap-1 items-center">
                                <input type="text" name="remark" value="{{ order.remark or '' }}" placeholder="备注..." class="border rounded px-2 py-1 text-xs w-32">
                                <button class="bg-gray-200 hover:bg-gray-300 text-gray-700 px-2 py-1 rounded text-xs font-bold">保存</button>
                            </form>
                        </td>
                        <td class="p-2 flex gap-2 items-center text-sm">
                            <a href="/admin/order/invoice/{{ order.id }}" class="text-indigo-600 font-bold hover:underline">查看</a>
                            <a href="/admin/order/whatsapp/{{ order.id }}" target="_blank" class="bg-green-600 text-white px-2.5 py-1 rounded font-bold hover:bg-green-700 text-xs flex items-center gap-1">
                                💬 发送 WhatsApp
                            </a>
                            {% if order.status != 'VOID' %}
                            <a href="/admin/order/void/{{ order.id }}" onclick="return confirm('确定作废此订单吗？')" class="text-red-500 font-bold">作废</a>
                            {% endif %}
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    """), orders=orders)

@app.route("/admin/order/remark/<int:id>", methods=["POST"])
@admin_required
def update_order_remark(id):
    remark = request.form.get("remark", "")
    with get_db() as conn:
        conn.execute("UPDATE orders SET remark = ? WHERE id = ?", (remark, id))
    return redirect(url_for("admin_orders"))

@app.route("/admin/order/whatsapp/<int:id>")
@admin_required
def admin_order_whatsapp(id):
    with get_db() as conn:
        order = conn.execute("""
            SELECT o.*, c.name as customer_name, c.phone as customer_phone, c.token as customer_token 
            FROM orders o JOIN customers c ON o.customer_id = c.id WHERE o.id = ?
        """, (id,)).fetchone()
        if not order:
            return "Order not found", 404
        items = conn.execute("SELECT * FROM order_items WHERE order_id = ?", (id,)).fetchall()
        
    items_str = "\n".join([f"- {item['item_name']}: RM {item['price']:.2f}" for item in items])
    portal_link = request.host_url.rstrip('/') + f"/customer/{order['customer_token']}"
    
    msg = (
        f"🌟 *Dew Hair Salon - Official Invoice* 🌟\n\n"
        f"Hello *{order['customer_name']}*,\n"
        f"Thank you for visiting us! Here is your receipt details:\n\n"
        f"🧾 *Order No:* {order['order_no']}\n"
        f"📅 *Date:* {order['created_at']}\n\n"
        f"*Purchased Items:*\n{items_str}\n\n"
        f"💰 *Total Amount:* RM {order['total_amount']:.2f}\n"
        f"💳 *Payment Method:* {order['payment_details']}\n"
    )
    if order['remark']:
        msg += f"📝 *Remark:* {order['remark']}\n"
        
    msg += f"\n🔗 View your member profile & credit balance here:\n{portal_link}\n\nHope to see you again soon!"
    
    # 清理电话号码格式并拼接 WhatsApp 链接
    phone = "".join(filter(str.isdigit, order['customer_phone']))
    if phone.startswith('0'):
        phone = '6' + phone  # 默认适配大马手机号格式 601xxxxxxx
        
    wa_url = f"https://api.whatsapp.com/send?phone={phone}&text={urllib.parse.quote(msg)}"
    return redirect(wa_url)

@app.route("/admin/order/invoice/<int:id>")
@admin_required
def admin_order_invoice(id):
    with get_db() as conn:
        order = conn.execute("""
            SELECT o.*, c.name as customer_name, c.phone as customer_phone, c.token as customer_token 
            FROM orders o JOIN customers c ON o.customer_id = c.id WHERE o.id = ?
        """, (id,)).fetchone()
        if not order: return "Order not found", 404
        items = conn.execute("SELECT * FROM order_items WHERE order_id = ?", (id,)).fetchall()
        
    return render_template_string("""
        <div style="max-width:550px;margin:40px auto;padding:25px;border:1px solid #ccc;font-family:sans-serif;border-radius:8px;background:#fff;box-shadow:0 2px 8px rgba(0,0,0,0.1);">
            <div style="display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid #4f46e5;padding-bottom:10px;margin-bottom:15px;">
                <h2 style="color:#4f46e5;margin:0;">Dew Hair Salon - Invoice</h2>
                <span style="font-size:14px;font-weight:bold;color:{% if order.status == 'VOID' %}red{% else %}green{% endif %};">{{ order.status }}</span>
            </div>
            <p><strong>Order No:</strong> {{ order.order_no }}</p>
            <p><strong>Date/Time:</strong> {{ order.created_at }}</p>
            <p><strong>Customer:</strong> {{ order.customer_name }} ({{ order.customer_phone }})</p>
            <p><strong>Payment Method:</strong> {{ order.payment_details }}</p>
            {% if order.remark %}<p><strong>Remark:</strong> <span style="color:#d97706;">{{ order.remark }}</span></p>{% endif %}
            <hr style="border:0;border-top:1px solid #eee;margin:15px 0;">
            <h3 style="font-size:16px;margin-bottom:8px;">Items Purchased</h3>
            <table style="width:100%;border-collapse:collapse;font-size:14px;margin-bottom:15px;">
                <thead><tr style="border-bottom:1px solid #ddd;background:#f9fafb;"><th style="text-align:left;padding:6px;">Item Name</th><th style="text-align:right;padding:6px;">Price (RM)</th></tr></thead>
                <tbody>
                    {% for item in items %}
                    <tr style="border-bottom:1px solid #eee;"><td style="padding:6px;">{{ item.item_name }}</td><td style="text-align:right;padding:6px;">RM {{ "%.2f"|format(item.price) }}</td></tr>
                    {% endfor %}
                </tbody>
            </table>
            <div style="text-align:right;font-size:18px;font-weight:bold;margin-bottom:20px;">
                Total Amount: <span style="color:#dc2626;">RM {{ "%.2f"|format(order.total_amount) }}</span>
            </div>
            <div style="display:flex;gap:10px;">
                <a href="/admin/order/whatsapp/{{ order.id }}" target="_blank" style="flex:1;text-align:center;padding:12px;background:#10b981;color:white;text-decoration:none;border-radius:6px;font-weight:bold;">💬 Send via WhatsApp</a>
                <a href="/admin/orders" style="flex:1;text-align:center;padding:12px;background:#4f46e5;color:white;text-decoration:none;border-radius:6px;font-weight:bold;">Back to Orders</a>
            </div>
        </div>
    """, order=order, items=items)

@app.route("/admin/order/void/<int:id>")
@admin_required
def void_order(id):
    with get_db() as conn:
        cursor = conn.cursor()
        order = cursor.execute("SELECT * FROM orders WHERE id = ? AND status = 'NORMAL'", (id,)).fetchone()
        if not order: return redirect(url_for("admin_orders"))
        
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
    return render_template_string(LAYOUT_TEMPLATE.replace("{% block content %}{% endblock %}", """
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div class="md:col-span-2 bg-white p-6 rounded-lg shadow">
                <h2 class="text-xl font-bold mb-4">点选服务 / 套餐 / 产品 (POS)</h2>
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
                <h2 class="text-xl font-bold mb-4">当前订单结账</h2>
                <div id="order-items" class="min-h-[150px] border-b mb-4"><p class="text-gray-400">点击左侧项目加入订单</p></div>
                <div class="text-xl font-bold mb-4">总金额: <span id="total-amount" class="text-red-600">RM 0.00</span></div>
                <form action="/admin/checkout" method="POST">
                    <input type="hidden" name="cart_data" id="cart_data_input">
                    <div class="mb-3">
                        <label class="block text-sm font-medium">选择已有会员</label>
                        <select name="customer_phone" id="cust_select" onchange="fillCustomer(this)" class="w-full border rounded p-2">
                            <option value="">-- 新客或手动输入 --</option>
                            {% for c in customers %}
                            <option value="{{ c.phone }}" data-name="{{ c.name }}">{{ c.name }} ({{ c.phone }}) - 余额: RM {{ c.credits }}</option>
                            {% endfor %}
                        </select>
                    </div>
                    <div class="mb-3">
                        <label class="block text-sm font-medium">顾客姓名</label>
                        <input type="text" name="customer_name" id="cust_name" class="w-full border rounded p-2" required>
                    </div>
                    <div class="mb-3">
                        <label class="block text-sm font-medium">顾客电话</label>
                        <input type="text" name="customer_phone_input" id="cust_phone" class="w-full border rounded p-2" required>
                    </div>
                    <div class="mb-4">
                        <label class="block text-sm font-medium mb-1">支付方式</label>
                        <select name="payment_method" class="w-full border rounded p-2">
                            <option value="Cash">Cash (现金)</option>
                            <option value="Credit Card">Credit Card (刷卡)</option>
                            <option value="TNG / QRPay">TNG / QRPay (电子钱包)</option>
                            <option value="Credit Balance Deduct">Credit Balance Deduct (储值余额扣款)</option>
                        </select>
                    </div>
                    <button class="w-full bg-green-600 text-white font-bold py-3 rounded hover:bg-green-700">完成收款与记账</button>
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
    """), services=services, customers=customers)

@app.route("/admin/checkout", methods=["POST"])
@admin_required
def checkout():
    try:
        cart_data = json.loads(request.form.get("cart_data", "[]"))
        name = request.form.get("customer_name")
        phone = request.form.get("customer_phone_input") or request.form.get("customer_phone")
        pay_method = request.form.get("payment_method")
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
                if current_credits < total: return "结算失败：该顾客 Credit 余额不足！", 400
                current_credits -= total
                cursor.execute("UPDATE customers SET credits = ? WHERE id = ?", (current_credits, cust_id))
                
            cursor.execute("INSERT INTO orders (order_no, customer_id, total_amount, payment_details, status, created_at) VALUES (?, ?, ?, ?, 'NORMAL', ?)", (order_no, cust_id, total, pay_method, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            order_id = cursor.lastrowid
            
            for item in cart_data:
                cursor.execute("INSERT INTO order_items (order_id, item_name, price) VALUES (?, ?, ?)", (order_id, item["name"], item["price"]))
                
        return f"""
            <div style="max-width:500px;margin:50px auto;padding:20px;border:1px solid #ccc;font-family:sans-serif;border-radius:8px;background:#fff;">
                <h2 style="color:green;">收银成功凭证</h2>
                <hr>
                <p><strong>单号：</strong> {order_no}</p>
                <p><strong>顾客：</strong> {name} ({phone})</p>
                <p><strong>金额：</strong> RM {total:.2f} ({pay_method})</p>
                <div style="margin-top:20px;display:flex;gap:10px;">
                    <a href="/admin/order/whatsapp/{order_id}" target="_blank" style="padding:10px 15px;background:#10b981;color:white;text-decoration:none;border-radius:6px;font-weight:bold;">💬 立即通过 WhatsApp 发送单据</a>
                    <a href="/admin/pos" style="padding:10px 15px;background:#4f46e5;color:white;text-decoration:none;border-radius:6px;font-weight:bold;">返回 POS 收银台</a>
                </div>
            </div>
        """
    except Exception as e:
        return f"结账发生错误: {str(e)}", 500

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
        <h2 class="text-3xl font-extrabold text-center text-indigo-600 mb-2">Dew Hair Salon 在线预约</h2>
        <p class="text-center text-sm text-gray-500 mb-6">营业时间: {{ open_time }} - {{ close_time }}</p>
        
        {% if error %}
        <div class="mb-4 p-3 bg-red-100 text-red-700 rounded text-sm font-bold">{{ error }}</div>
        {% endif %}
        
        <form action="/book" method="POST" id="bookingForm">
            <div class="mb-5">
                <label class="block text-sm font-bold mb-2">1. 选择美发服务项目</label>
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    {% for item in services %}
                    <label class="border rounded-lg p-3 cursor-pointer hover:border-indigo-600 flex items-center justify-between">
                        <div>
                            <div class="font-bold">{{ item.name }}</div>
                            <div class="text-xs text-gray-500">耗时: {{ item.duration }}分钟</div>
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
                <label class="block text-sm font-bold mb-2">2. 选择专属发型师</label>
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
                    <label class="block text-sm font-bold">3. 选择预约日期</label>
                    <input type="date" name="booking_date" id="booking_date" value="{{ selected_date }}" min="{{ today_str }}" class="border rounded px-2 py-1 text-sm text-indigo-600 font-bold" onchange="selectDateCard(this.value)">
                </div>
                <div class="flex gap-2 overflow-x-auto pb-2">
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
                <label class="block text-sm font-bold mb-2">4. 选择时间段</label>
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
                    <label class="block text-sm font-bold mb-1">您的姓名</label>
                    <input type="text" name="customer_name" class="w-full border rounded p-3" required>
                </div>
                <div>
                    <label class="block text-sm font-bold mb-1">您的电话号码</label>
                    <input type="text" name="customer_phone" class="w-full border rounded p-3" required>
                </div>
            </div>

            <button class="w-full bg-indigo-600 text-white font-bold py-3.5 rounded-lg text-lg hover:bg-indigo-700 shadow-md">确认提交预约</button>
        </form>
    </div>
    <script>
        document.querySelectorAll('input[name="booking_time"]').forEach(input => {
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
        
        with get_db() as conn:
            srv = conn.execute("SELECT duration FROM services WHERE id = ?", (service_id,)).fetchone()
            duration = srv["duration"] if srv else 30
            start_dt = datetime.strptime(f"{b_date} {b_time}", "%Y-%m-%d %H:%M")
            end_dt = start_dt + timedelta(minutes=duration)
            start_str = start_dt.strftime("%Y-%m-%d %H:%M")
            end_str = end_dt.strftime("%Y-%m-%d %H:%M")
            
            cursor = conn.cursor()
            cursor.execute("SELECT id, token FROM customers WHERE phone = ?", (c_phone,))
            cust = cursor.fetchone()
            if not cust:
                token = secrets.token_hex(8)
                cursor.execute("INSERT INTO customers (name, phone, token) VALUES (?, ?, ?)", (c_name, c_phone, token))
                cust_token = token
            else:
                cust_token = cust["token"]
                
            cursor.execute("INSERT INTO appointments (customer_id, service_id, stylist, start_time, end_time) VALUES (?, ?, ?, ?, ?)", (cust.id if cust else cursor.lastrowid, service_id, stylist, start_str, end_str))
            
        return f"""
            <div style="max-width:400px;margin:50px auto;text-align:center;font-family:sans-serif;padding:30px;border:1px solid #ddd;border-radius:8px;background:#fff;">
                <h2 style="color:green;margin-top:0;">预约成功！</h2>
                <p>感谢您，<strong>{c_name}</strong>！您的预约已成功记录。</p>
                <p><strong>时间：</strong>{start_str} ~ {end_str.split()[1]}</p>
                <p><strong>发型师：</strong>{stylist}</p>
                <hr style="margin:20px 0;">
                <a href="/customer/{cust_token}" style="display:inline-block;padding:12px 20px;background:#4f46e5;color:white;text-decoration:none;border-radius:6px;font-weight:bold;">点此进入我的会员专属页</a>
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
    wd_map = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    for i in range(14):
        d = base_dt + timedelta(days=i)
        d_str = d.strftime("%Y-%m-%d")
        wd_str = "今天" if i == 0 else ("明天" if i == 1 else wd_map[d.weekday()])
        date_strip.append({"date_str": d_str, "display_date": d.strftime("%m月%d日"), "weekday": wd_str, "year": d.strftime("%Y")})
        
    return render_template_string(BOOKING_CALENDAR_TEMPLATE, services=services, stylists=stylists, timeslots=timeslots, open_time=open_time_str, close_time=close_time_str, today_str=today_str, selected_date=selected_date, date_strip=date_strip, error=error)

@app.route("/customer/<token>")
def customer_profile(token):
    with get_db() as conn:
        cust = conn.execute("SELECT * FROM customers WHERE token = ?", (token,)).fetchone()
        if not cust: return "Invalid customer link.", 404
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
        <div style="max-width:550px;margin:30px auto;padding:25px;border:1px solid #ccc;font-family:sans-serif;border-radius:8px;background:#fff;">
            <h2 style="color:#4f46e5;margin-top:0;">Dew Hair Salon - My Member Portal</h2>
            <p><strong>Name:</strong> {{ cust.name }}</p>
            <p><strong>Phone:</strong> {{ cust.phone }}</p>
            <div style="background:#f0fdf4;border:1px solid #bbf7d0;padding:12px;border-radius:6px;margin:15px 0;">
                <span style="font-size:14px;color:#166534;">Current Credit Balance:</span>
                <div style="font-size:24px;font-weight:bold;color:#15803d;">RM {{ "%.2f"|format(cust.credits) }}</div>
            </div>
            <hr style="border:0;border-top:1px solid #eee;margin:15px 0;">
            <h3 style="font-size:16px;">My Appointments</h3>
            {% if appointments %}
            <ul style="padding-left:20px;font-size:14px;">
                {% for a in appointments %}
                <li style="margin-bottom:8px;">{{ a.start_time }} - <strong>{{ a.service_name }}</strong> (Stylist: {{ a.stylist }}) - <span style="color:green;font-weight:bold;">{{ a.status }}</span></li>
                {% endfor %}
            </ul>
            {% else %}
            <p style="color:gray;font-size:14px;">No appointments.</p>
            {% endif %}
            <hr style="border:0;border-top:1px solid #eee;margin:15px 0;">
            <h3 style="font-size:16px;">Order History</h3>
            {% if orders %}
            <ul style="padding-left:20px;font-size:14px;">
                {% for o in orders %}
                <li style="margin-bottom:10px; {% if o.status == 'VOID' %}color:#9ca3af;text-decoration:line-through;{% endif %}">
                    <strong>{{ o.order_no }}</strong> ({{ o.created_at }})<br>
                    Item: {{ o.item_name }} - <strong>RM {{ "%.2f"|format(o.price) }}</strong>
                    {% if o.status == 'VOID' %}<span style="color:red;font-weight:bold;">[VOIDED]</span>{% else %}<span style="color:#4f46e5;">[{{ o.payment_details }}]</span>{% endif %}
                    {% if o.remark %}<br><span style="color:#d97706;font-size:13px;">Remark: {{ o.remark }}</span>{% endif %}
                </li>
                {% endfor %}
            </ul>
            {% else %}
            <p style="color:gray;font-size:14px;">No order history.</p>
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
    return render_template_string(LAYOUT_TEMPLATE.replace("{% block content %}{% endblock %}", """
        <div class="bg-white p-6 rounded shadow">
            <h2 class="text-xl font-bold mb-6 border-b pb-2">90天历史数据看板</h2>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-center">
                <div class="bg-indigo-50 p-4 rounded shadow">
                    <div class="text-gray-500 text-sm">有效订单数</div>
                    <div class="text-3xl font-bold text-indigo-600">{{ order_count }}</div>
                </div>
                <div class="bg-green-50 p-4 rounded shadow">
                    <div class="text-gray-500 text-sm">总营业额 (RM)</div>
                    <div class="text-3xl font-bold text-green-600">RM {{ "%.2f"|format(total_revenue) }}</div>
                </div>
                <div class="bg-yellow-50 p-4 rounded shadow">
                    <div class="text-gray-500 text-sm">项目销售量</div>
                    <div class="text-3xl font-bold text-yellow-600">{{ item_count }}</div>
                </div>
                <div class="prop bg-purple-50 p-4 rounded shadow">
                    <div class="text-gray-500 text-sm">会员总数</div>
                    <div class="text-3xl font-bold text-purple-600">{{ customer_count }}</div>
                </div>
            </div>
        </div>
    """))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=False)
