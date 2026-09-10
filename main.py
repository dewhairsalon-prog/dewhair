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
DB_NAME = "salon.db"

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
                duration INTEGER DEFAULT 30
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
        
        # 默认系统设置
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('open_time', '10:00')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('close_time', '20:00')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('closed_weekdays', '1')") # 默认周一休息 (0=周日, 1=周一...)
        
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM services")
        if cursor.fetchone()[0] == 0:
            sample_services = [
                ("高级总监剪发", "Services", "剪发", 120.0, 45),
                ("植物精油染发", "Services", "染发", 380.0, 90),
                ("蛋白修护烫发", "Services", "烫发", 450.0, 120),
                ("深度发膜护理", "Services", "护理", 260.0, 60),
                ("防脱头皮理疗", "Services", "头皮理疗", 320.0, 60),
                ("充值 1000 元送 200", "Packages", "储值套餐", 1000.0, 0),
                ("充值 500 元送 80", "Packages", "储值套餐", 500.0, 0),
            ]
            cursor.executemany("""
                INSERT INTO services (name, category_type, sub_category, price, duration)
                VALUES (?, ?, ?, ?, ?)
            """, sample_services)

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
                <a href="/admin" class="hover:bg-indigo-700 px-2 py-1 rounded">营业与项目设置</a>
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

BOOKING_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dew Hair Salon - 在线预约</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 min-h-screen p-4">
    <div class="max-w-md mx-auto bg-white p-6 rounded-lg shadow-md">
        <h2 class="text-2xl font-bold text-center text-indigo-600 mb-2">Dew Hair Salon 在线预约</h2>
        <p class="text-center text-sm text-gray-500 mb-6">营业时间: {{ open_time }} - {{ close_time }}</p>
        
        {% if error %}
        <div class="mb-4 p-3 bg-red-100 text-red-700 rounded text-sm font-bold">{{ error }}</div>
        {% endif %}
        
        <form action="/book" method="POST">
            <div class="mb-4">
                <label class="block text-sm font-bold mb-1">选择服务项目</label>
                <select name="service_id" class="w-full border rounded p-2" required>
                    {% for item in services %}
                    <option value="{{ item.id }}">{{ item.name }} - ￥{{ "%.2f"|format(item.price) }} ({{ item.duration }}分钟)</option>
                    {% endfor %}
                </select>
            </div>
            <div class="mb-4">
                <label class="block text-sm font-bold mb-1">选择发型师</label>
                <select name="stylist" class="w-full border rounded p-2" required>
                    <option value="Alex (总监)">Alex (总监)</option>
                    <option value="David (资深设计师)">David (资深设计师)</option>
                    <option value="Emma (高级造型师)">Emma (高级造型师)</option>
                </select>
            </div>
            <div class="mb-4">
                <label class="block text-sm font-bold mb-1">选择日期</label>
                <input type="date" name="booking_date" class="w-full border rounded p-2" required>
            </div>
            <div class="mb-4">
                <label class="block text-sm font-bold mb-1">选择开始时间段</label>
                <select name="booking_time" class="w-full border rounded p-2" required>
                    {% for t in timeslots %}
                    <option value="{{ t }}">{{ t }}</option>
                    {% endfor %}
                </select>
            </div>
            <div class="mb-4 border-t pt-4">
                <label class="block text-sm font-bold mb-1">您的姓名</label>
                <input type="text" name="customer_name" class="w-full border rounded p-2" required>
            </div>
            <div class="mb-6">
                <label class="block text-sm font-bold mb-1">您的电话号码 (用于查询个人中心)</label>
                <input type="text" name="customer_phone" class="w-full border rounded p-2" required>
            </div>
            <button class="w-full bg-indigo-600 text-white font-bold py-3 rounded hover:bg-indigo-700">提交预约</button>
        </form>
    </div>
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
                    <h3 class="font-bold mb-2">添加特定临时休息日 (特殊假期/闭店)</h3>
                    <form action="/admin/holiday/add" method="POST" class="flex gap-2">
                        <input type="date" name="date_str" class="border rounded p-2" required>
                        <input type="text" name="reason" placeholder="休息原因 (如: 员工培训/春节假期)" class="border rounded p-2 flex-grow">
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

                <!-- 项目与充值套餐列表 -->
                <div class="bg-white p-6 rounded shadow">
                    <h2 class="text-xl font-bold mb-4">项目与充值套餐管理</h2>
                    <table class="w-full text-left">
                        <thead><tr class="border-b"><th class="p-2">大分类</th><th class="p-2">名称</th><th class="p-2">价格 / 面额</th><th class="p-2">耗时</th><th class="p-2">操作</th></tr></thead>
                        <tbody>
                            {% for item in services %}
                            <tr class="border-b">
                                <td class="p-2 font-bold text-indigo-600">{{ item.category_type }}</td>
                                <td class="p-2">{{ item.name }}</td>
                                <td class="p-2 font-bold text-red-600">￥{{ "%.2f"|format(item.price) }}</td>
                                <td class="p-2">{{ item.duration }}分钟</td>
                                <td class="p-2">
                                    <a href="/admin/service/delete/{{ item.id }}" onclick="return confirm('确定要删除吗？')" class="text-red-500 text-sm font-bold">删除</a>
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- 添加新服务或充值套餐 -->
            <div class="bg-white p-6 rounded shadow h-fit">
                <h2 class="text-xl font-bold mb-4">添加服务或储值套餐</h2>
                <form action="/admin/service/add" method="POST">
                    <div class="mb-3">
                        <label class="block text-sm font-medium">名称 (如: 剪发 / 充值1000送200)</label>
                        <input type="text" name="name" class="w-full border rounded p-2" required>
                    </div>
                    <div class="mb-3">
                        <label class="block text-sm font-medium">分类类型</label>
                        <select name="category_type" class="w-full border rounded p-2">
                            <option value="Services">Services (服务项目)</option>
                            <option value="Packages">Packages (储值套餐)</option>
                            <option value="Products">Products (零售产品)</option>
                        </select>
                    </div>
                    <div class="mb-3">
                        <label class="block text-sm font-medium">子分类描述</label>
                        <input type="text" name="sub_category" class="w-full border rounded p-2" value="标准分类" required>
                    </div>
                    <div class="mb-3">
                        <label class="block text-sm font-medium">价格 / 充值金额 (￥)</label>
                        <input type="number" step="0.01" name="price" class="w-full border rounded p-2" required>
                    </div>
                    <div class="mb-4">
                        <label class="block text-sm font-medium">耗时 (分钟，套餐填0)</label>
                        <input type="number" name="duration" class="w-full border rounded p-2" value="30" required>
                    </div>
                    <button class="w-full bg-indigo-600 text-white font-bold py-2 rounded hover:bg-indigo-700">确认添加</button>
                </form>
            </div>
        </div>
    """), services=services, holidays=holidays, open_time=open_time, close_time=close_time, closed_wd=closed_wd)

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
    sub_cat = request.form.get("sub_category")
    price = float(request.form.get("price", 0))
    duration = int(request.form.get("duration", 30))
    with get_db() as conn:
        conn.execute("INSERT INTO services (name, category_type, sub_category, price, duration) VALUES (?, ?, ?, ?, ?)", (name, cat, sub_cat, price, duration))
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
    with get_db() as conn:
        customers = conn.execute("""
            SELECT c.*, 
                   (SELECT COUNT(*) FROM appointments WHERE customer_id = c.id) as app_count,
                   (SELECT COUNT(*) FROM orders WHERE customer_id = c.id) as order_count
            FROM customers c 
            ORDER BY c.id DESC
        """).fetchall()
    return render_template_string(LAYOUT_TEMPLATE.replace("{% block content %}{% endblock %}", """
        <div class="bg-white p-6 rounded shadow">
            <h2 class="text-xl font-bold mb-4">会员与历史消费档案管理</h2>
            <table class="w-full text-left border-collapse">
                <thead>
                    <tr class="border-b bg-gray-50">
                        <th class="p-2">会员姓名</th>
                        <th class="p-2">电话号码</th>
                        <th class="p-2">储值余额 (Credit)</th>
                        <th class="p-2">预约次数</th>
                        <th class="p-2">消费订单数</th>
                        <th class="p-2">顾客专属链接 (Client Link)</th>
                        <th class="p-2">操作</th>
                    </tr>
                </thead>
                <tbody>
                    {% for c in customers %}
                    <tr class="border-b">
                        <td class="p-2 font-bold">{{ c.name }}</td>
                        <td class="p-2">{{ c.phone }}</td>
                        <td class="p-2 text-green-600 font-bold">￥{{ "%.2f"|format(c.credits) }}</td>
                        <td class="p-2">{{ c.app_count }} 次</td>
                        <td class="p-2">{{ c.order_count }} 单</td>
                        <td class="p-2">
                            <a href="/customer/{{ c.token }}" target="_blank" class="text-indigo-600 underline text-sm font-bold">打开专属页面</a>
                        </td>
                        <td class="p-2">
                            <a href="/admin/customer/detail/{{ c.id }}" class="bg-indigo-50 text-indigo-700 px-3 py-1 rounded text-sm font-bold hover:bg-indigo-100">查看消费历史</a>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    """), customers=customers)

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
                    <h2 class="text-2xl font-bold text-indigo-600">{{ cust.name }} 的会员档案</h2>
                    <p class="text-gray-600">电话: {{ cust.phone }} | 专属链接 Token: {{ cust.token }}</p>
                </div>
                <div class="text-right">
                    <div class="text-sm text-gray-500">账户储值余额</div>
                    <div class="text-2xl font-bold text-green-600">￥{{ "%.2f"|format(cust.credits) }}</div>
                </div>
            </div>

            <div>
                <h3 class="text-lg font-bold mb-2">历史预约记录</h3>
                <table class="w-full text-left border-collapse">
                    <thead><tr class="border-b bg-gray-50"><th class="p-2">预约时间段</th><th class="p-2">服务项目</th><th class="p-2">发型师</th><th class="p-2">状态</th></tr></thead>
                    <tbody>
                        {% for a in appointments %}
                        <tr class="border-b"><td class="p-2">{{ a.start_time }} ~ {{ a.end_time.split()[1] }}</td><td class="p-2">{{ a.service_name }}</td><td class="p-2">{{ a.stylist }}</td><td class="p-2 text-green-600 font-bold">{{ a.status }}</td></tr>
                        {% else %}
                        <tr><td colspan="4" class="p-2 text-gray-400">暂无预约记录</td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>

            <div>
                <h3 class="text-lg font-bold mb-2">历史消费与订单明细</h3>
                <table class="w-full text-left border-collapse">
                    <thead><tr class="border-b bg-gray-50"><th class="p-2">订单号</th><th class="p-2">消费时间</th><th class="p-2">项目/商品</th><th class="p-2">金额</th><th class="p-2">支付方式</th></tr></thead>
                    <tbody>
                        {% for o in orders %}
                        <tr class="border-b"><td class="p-2 font-bold">{{ o.order_no }}</td><td class="p-2">{{ o.created_at }}</td><td class="p-2">{{ o.item_name }}</td><td class="p-2 text-red-600 font-bold">￥{{ "%.2f"|format(o.price) }}</td><td class="p-2">{{ o.payment_details }}</td></tr>
                        {% else %}
                        <tr><td colspan="5" class="p-2 text-gray-400">暂无消费订单</td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            <div>
                <a href="/admin/customers" class="bg-gray-500 text-white px-4 py-2 rounded font-bold">返回会员列表</a>
            </div>
        </div>
    """), cust=cust, orders=orders, appointments=appointments)

@app.route("/admin/appointments")
@admin_required
def admin_appointments():
    with get_db() as conn:
        appointments = conn.execute("""
            SELECT a.*, c.name as customer_name, c.phone as customer_phone, s.name as service_name 
            FROM appointments a 
            JOIN customers c ON a.customer_id = c.id 
            JOIN services s ON a.service_id = s.id 
            ORDER BY a.start_time DESC
        """).fetchall()
    return render_template_string(LAYOUT_TEMPLATE.replace("{% block content %}{% endblock %}", """
        <div class="bg-white p-6 rounded shadow">
            <h2 class="text-xl font-bold mb-4">预约记录与时间轴管理</h2>
            <table class="w-full text-left border-collapse">
                <thead>
                    <tr class="border-b bg-gray-50">
                        <th class="p-2">时间段</th>
                        <th class="p-2">顾客姓名</th>
                        <th class="p-2">电话</th>
                        <th class="p-2">服务项目</th>
                        <th class="p-2">发型师</th>
                        <th class="p-2">状态 / 操作</th>
                    </tr>
                </thead>
                <tbody>
                    {% for app in appointments %}
                    <tr class="border-b">
                        <td class="p-2 font-bold">{{ app.start_time }} ~ {{ app.end_time.split()[1] }}</td>
                        <td class="p-2">{{ app.customer_name }}</td>
                        <td class="p-2">{{ app.customer_phone }}</td>
                        <td class="p-2">{{ app.service_name }}</td>
                        <td class="p-2">{{ app.stylist }}</td>
                        <td class="p-2">
                            <span class="text-green-600 font-bold mr-2">{{ app.status }}</span>
                            <a href="/admin/appointment/delete/{{ app.id }}" onclick="return confirm('确定取消此预约吗？')" class="text-red-500 text-sm font-bold">删除</a>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    """), appointments=appointments)

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
    return render_template_string(LAYOUT_TEMPLATE.replace("{% block content %}{% endblock %}", """
        <div class="bg-white p-6 rounded shadow">
            <h2 class="text-xl font-bold mb-4">历史订单管理与删除</h2>
            <table class="w-full text-left border-collapse">
                <thead>
                    <tr class="border-b bg-gray-50">
                        <th class="p-2">单号</th>
                        <th class="p-2">时间</th>
                        <th class="p-2">顾客姓名</th>
                        <th class="p-2">电话</th>
                        <th class="p-2">金额</th>
                        <th class="p-2">支付方式</th>
                        <th class="p-2">操作</th>
                    </tr>
                </thead>
                <tbody>
                    {% for order in orders %}
                    <tr class="border-b">
                        <td class="p-2 font-bold text-indigo-600">{{ order.order_no }}</td>
                        <td class="p-2">{{ order.created_at }}</td>
                        <td class="p-2">{{ order.customer_name }}</td>
                        <td class="p-2">{{ order.customer_phone }}</td>
                        <td class="p-2 text-red-600 font-bold">￥{{ "%.2f"|format(order.total_amount) }}</td>
                        <td class="p-2">{{ order.payment_details }}</td>
                        <td class="p-2">
                            <a href="/admin/order/delete/{{ order.id }}" onclick="return confirm('确定要删除此订单吗？')" class="text-red-500 font-bold text-sm">删除订单</a>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    """), orders=orders)

@app.route("/admin/order/delete/<int:id>")
@admin_required
def delete_order(id):
    with get_db() as conn:
        conn.execute("DELETE FROM orders WHERE id = ?", (id,))
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
                        <div class="text-gray-600">￥{{ "%.2f"|format(item.price) }}</div>
                    </button>
                    {% endfor %}
                </div>
            </div>
            <div class="bg-white p-6 rounded-lg shadow">
                <h2 class="text-xl font-bold mb-4">当前订单结账</h2>
                <div id="order-items" class="min-h-[150px] border-b mb-4">
                    <p class="text-gray-400">点击左侧项目加入订单</p>
                </div>
                <div class="text-xl font-bold mb-4">
                    总金额: <span id="total-amount" class="text-red-600">￥0.00</span>
                </div>
                <form action="/admin/checkout" method="POST">
                    <input type="hidden" name="cart_data" id="cart_data_input">
                    <div class="mb-3">
                        <label class="block text-sm font-medium">选择已有会员 (或下方直接输入新客)</label>
                        <select name="customer_phone" id="cust_select" onchange="fillCustomer(this)" class="w-full border rounded p-2">
                            <option value="">-- 新客或手动输入 --</option>
                            {% for c in customers %}
                            <option value="{{ c.phone }}" data-name="{{ c.name }}">{{ c.name }} ({{ c.phone }}) - 余额: ￥{{ c.credits }}</option>
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
                            <option value="QRPay">QRPay (扫码)</option>
                            <option value="Credit Balance Deduct">Credit Balance Deduct (储值余额扣款)</option>
                        </select>
                    </div>
                    <button class="w-full bg-green-600 text-white font-bold py-3 rounded hover:bg-green-700">完成收款与记账</button>
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
                    container.innerHTML += `<div class="flex justify-between py-1"><span>${item.name}</span><span>￥${item.price.toFixed(2)}</span></div>`;
                });
                document.getElementById('total-amount').innerText = '￥' + total.toFixed(2);
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
            
            # 如果购买了充值套餐，给会员自动加余额
            for item in cart_data:
                if "充值" in item["name"]:
                    # 解析套餐赠送金额，例如 "充值 1000 元送 200" 自动加 1200
                    add_val = item["price"]
                    if "1000" in item["name"]: add_val = 1200.0
                    elif "500" in item["name"]: add_val = 580.0
                    current_credits += add_val
                    cursor.execute("UPDATE customers SET credits = ? WHERE id = ?", (current_credits, cust_id))

            # 如果使用储值余额扣款
            if pay_method == "Credit Balance Deduct":
                if current_credits < total:
                    return "结算失败：该顾客储值余额不足！", 400
                current_credits -= total
                cursor.execute("UPDATE customers SET credits = ? WHERE id = ?", (current_credits, cust_id))
                
            cursor.execute("""
                INSERT INTO orders (order_no, customer_id, total_amount, payment_details, created_at) 
                VALUES (?, ?, ?, ?, ?)
            """, (order_no, cust_id, total, pay_method, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            order_id = cursor.lastrowid
            
            for item in cart_data:
                cursor.execute("""
                    INSERT INTO order_items (order_id, item_name, price) 
                    VALUES (?, ?, ?)
                """, (order_id, item["name"], item["price"]))
                
        return f"""
            <div style="max-width:500px;margin:50px auto;padding:20px;border:1px solid #000;font-family:sans-serif;border-radius:8px;">
                <h2>Dew Hair Salon 收银成功凭证</h2>
                <hr>
                <p><strong>单号：</strong> {order_no}</p>
                <p><strong>顾客：</strong> {name} ({phone})</p>
                <p><strong>金额：</strong> ￥{total:.2f} ({pay_method})</p>
                <p><strong>顾客个人专属链接：</strong> <a href="/customer/{cust_token}" target="_blank">点击查看 / 发送给顾客</a></p>
                <hr>
                <a href="/admin/pos" style="color:#4f46e5;font-weight:bold;text-decoration:none;">返回 POS 收银台</a>
            </div>
        """
    except Exception as e:
        return f"结账发生错误: {str(e)}", 500

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
        
        # 校验是否为休息日
        d_obj = datetime.strptime(b_date, "%Y-%m-%d")
        if d_obj.weekday() == closed_wd:
            return public_booking_render(error="预约失败：该日期为沙龙固定休息日，请选择其他日期！")
            
        with get_db() as conn:
            # 校验临时假期
            holiday = conn.execute("SELECT * FROM holidays WHERE date_str = ?", (b_date,)).fetchone()
            if holiday:
                return public_booking_render(error=f"预约失败：该天为临时闭店日 ({holiday.reason})，无法预约！")
                
            srv = conn.execute("SELECT duration FROM services WHERE id = ?", (service_id,)).fetchone()
            duration = srv["duration"] if srv else 30
            
            start_dt = datetime.strptime(f"{b_date} {b_time}", "%Y-%m-%d %H:%M")
            end_dt = start_dt + timedelta(minutes=duration)
            start_str = start_dt.strftime("%Y-%m-%d %H:%M")
            end_str = end_dt.strftime("%Y-%m-%d %H:%M")
            
            # 防冲突检查
            conflict = conn.execute("""
                SELECT id FROM appointments 
                WHERE stylist = ? AND status = 'CONFIRMED' 
                AND start_time < ? AND end_time > ?
            """, (stylist, end_str, start_str)).fetchone()
            
            if conflict:
                return public_booking_render(error=f"预约失败：发型师 {stylist} 在此时间段已有预约冲突，请更换时间！")

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
            <div style="max-width:400px;margin:50px auto;text-align:center;font-family:sans-serif;padding:20px;border:1px solid #ddd;border-radius:8px;">
                <h2 style="color:green;">预约成功！</h2>
                <p>感谢您，{c_name}！您的预约已成功记录。</p>
                <p><strong>时间：</strong>{start_str} ~ {end_str.split()[1]}</p>
                <p><strong>发型师：</strong>{stylist}</p>
                <hr style="margin:20px 0;">
                <p>这是您的<strong>专属会员与历史记录链接</strong>（建议长按复制保存）：</p>
                <a href="/customer/{cust_token}" style="display:inline-block;padding:10px 15px;background:#4f46e5;color:white;text-decoration:none;border-radius:5px;font-weight:bold;">点此进入我的会员专属页</a>
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
        st += timedelta(minutes=15)
        
    with get_db() as conn:
        services = conn.execute("SELECT * FROM services WHERE category_type != 'Packages'").fetchall()
    return render_template_string(BOOKING_TEMPLATE, services=services, timeslots=timeslots, open_time=open_time_str, close_time=close_time_str, error=error)

@app.route("/customer/<token>")
def customer_profile(token):
    with get_db() as conn:
        cust = conn.execute("SELECT * FROM customers WHERE token = ?", (token,)).fetchone()
        if not cust:
            return "无效的会员专属链接", 404
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
            <h2 style="color:#4f46e5;margin-top:0;">Dew Hair Salon - 我的会员中心</h2>
            <p><strong>姓名：</strong> {{ cust.name }}</p>
            <p><strong>电话：</strong> {{ cust.phone }}</p>
            <div style="background:#f0fdf4;border:1px solid #bbf7d0;padding:12px;border-radius:6px;margin:15px 0;">
                <span style="font-size:14px;color:#166534;">当前储值余额 (Credit Balance)：</span>
                <div style="font-size:24px;font-weight:bold;color:#15803d;">￥{{ "%.2f"|format(cust.credits) }}</div>
            </div>
            <hr>
            <h3>我的预约记录</h3>
            {% if appointments %}
            <ul style="padding-left:20px;font-size:14px;">
                {% for a in appointments %}
                <li style="margin-bottom:6px;">{{ a.start_time }} - <strong>{{ a.service_name }}</strong> (发型师: {{ a.stylist }}) - <span style="color:green;">{{ a.status }}</span></li>
                {% endfor %}
            </ul>
            {% else %}
            <p style="color:gray;font-size:14px;">暂无预约记录。</p>
            {% endif %}
            <hr>
            <h3>历史消费记录</h3>
            {% if orders %}
            <ul style="padding-left:20px;font-size:14px;">
                {% for o in orders %}
                <li style="margin-bottom:6px;">{{ o.created_at }} - <strong>{{ o.item_name }}</strong> (￥{{ "%.2f"|format(o.price) }}) [支付: {{ o.payment_details }}]</li>
                {% endfor %}
            </ul>
            {% else %}
            <p style="color:gray;font-size:14px;">暂无历史消费记录。</p>
            {% endif %}
        </div>
    """, cust=cust, orders=orders, appointments=appointments)

@app.route("/admin/reports")
@admin_required
def admin_reports():
    with get_db() as conn:
        order_count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        total_revenue = conn.execute("SELECT SUM(total_amount) FROM orders").fetchone()[0] or 0.0
        customer_count = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        item_count = conn.execute("SELECT COUNT(*) FROM order_items").fetchone()[0]
    return render_template_string(LAYOUT_TEMPLATE.replace("{% block content %}{% endblock %}", """
        <div class="bg-white p-6 rounded shadow">
            <h2 class="text-xl font-bold mb-6 border-b pb-2">90天历史数据看板</h2>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-center">
                <div class="bg-indigo-50 p-4 rounded shadow">
                    <div class="text-gray-500 text-sm">订单总量</div>
                    <div class="text-3xl font-bold text-indigo-600">{{ order_count }}</div>
                </div>
                <div class="bg-green-50 p-4 rounded shadow">
                    <div class="text-gray-500 text-sm">总营业额</div>
                    <div class="text-3xl font-bold text-green-600">￥{{ "%.2f"|format(total_revenue) }}</div>
                </div>
                <div class="bg-yellow-50 p-4 rounded shadow">
                    <div class="text-gray-500 text-sm">项目销售量</div>
                    <div class="text-3xl font-bold text-yellow-600">{{ item_count }}</div>
                </div>
                <div class="bg-purple-50 p-4 rounded shadow">
                    <div class="text-gray-500 text-sm">顾客总数</div>
                    <div class="text-3xl font-bold text-purple-600">{{ customer_count }}</div>
                </div>
            </div>
        </div>
    """))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=False)
