import os
import secrets
import psycopg2
import psycopg2.extras
import hmac
import json
import urllib.parse
from datetime import datetime, timedelta
from pytz import timezone
from functools import wraps
from flask import (
    Flask, request, redirect, url_for, session, render_template_string, jsonify
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "123456")

# 设置马来西亚实时时区
MY_TZ = timezone('Asia/Kuala_Lumpur')

def get_current_time():
    return datetime.now(MY_TZ).strftime("%Y-%m-%d %H:%M:%S")

def get_current_date():
    return datetime.now(MY_TZ).strftime("%Y-%m-%d")

# ==========================================
# PostgreSQL 数据库连接配置 (完美适配 Render + PgBouncer 事务池)
# ==========================================
DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db():
    if DATABASE_URL:
        # 兼容 PgBouncer 事务模式及参数解析
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        conn = psycopg2.connect("dbname=salon user=postgres password=postgres", cursor_factory=psycopg2.extras.RealDictCursor)
    return conn

def init_db():
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS services (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    category_type TEXT NOT NULL,
                    sub_category TEXT NOT NULL,
                    price DOUBLE PRECISION NOT NULL,
                    duration INTEGER DEFAULT 30,
                    credit_value DOUBLE PRECISION DEFAULT 0.0
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stylists (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    title TEXT NOT NULL
                );
            """)
            # 兼容旧库：补上佣金方式与佣金数值字段
            cursor.execute("ALTER TABLE stylists ADD COLUMN IF NOT EXISTS commission_type TEXT DEFAULT 'percent'")
            cursor.execute("ALTER TABLE stylists ADD COLUMN IF NOT EXISTS commission_value DOUBLE PRECISION DEFAULT 0.0")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS customers (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    phone TEXT UNIQUE NOT NULL,
                    token TEXT UNIQUE NOT NULL,
                    credits DOUBLE PRECISION DEFAULT 0.0
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id SERIAL PRIMARY KEY,
                    order_no TEXT UNIQUE NOT NULL,
                    customer_id INTEGER NOT NULL,
                    total_amount DOUBLE PRECISION NOT NULL,
                    payment_details TEXT NOT NULL,
                    status TEXT DEFAULT 'NORMAL',
                    remark TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(customer_id) REFERENCES customers(id)
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS order_items (
                    id SERIAL PRIMARY KEY,
                    order_id INTEGER NOT NULL,
                    item_name TEXT NOT NULL,
                    price DOUBLE PRECISION NOT NULL,
                    qty INTEGER DEFAULT 1,
                    staff_name TEXT DEFAULT '',
                    commission DOUBLE PRECISION DEFAULT 0.0,
                    FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS appointments (
                    id SERIAL PRIMARY KEY,
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
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS holidays (
                    id SERIAL PRIMARY KEY,
                    date_str TEXT UNIQUE NOT NULL,
                    reason TEXT
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS order_item_staff (
                    id SERIAL PRIMARY KEY,
                    order_item_id INTEGER NOT NULL,
                    staff_name TEXT NOT NULL,
                    commission_amount DOUBLE PRECISION DEFAULT 0.0,
                    FOREIGN KEY(order_item_id) REFERENCES order_items(id) ON DELETE CASCADE
                );
            """)
            
            cursor.execute("INSERT INTO settings (key, value) VALUES ('open_time', '10:00') ON CONFLICT (key) DO NOTHING")
            cursor.execute("INSERT INTO settings (key, value) VALUES ('close_time', '20:00') ON CONFLICT (key) DO NOTHING")
            cursor.execute("INSERT INTO settings (key, value) VALUES ('closed_weekdays', '1') ON CONFLICT (key) DO NOTHING")
            
            cursor.execute("SELECT COUNT(*) FROM services")
            if cursor.fetchone()["count"] == 0:
                sample_services = [
                    ("高级总监剪发", "Services", "剪发", 120.0, 45, 0.0),
                    ("植物精油染发", "Services", "染发", 380.0, 90, 0.0),
                    ("充值 1000 送 200", "Packages", "储值套餐", 1000.0, 0, 1200.0),
                ]
                for s in sample_services:
                    cursor.execute("""
                        INSERT INTO services (name, category_type, sub_category, price, duration, credit_value)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, s)

            cursor.execute("SELECT COUNT(*) FROM stylists")
            if cursor.fetchone()["count"] == 0:
                sample_stylists = [
                    ("Alex", "总监"),
                    ("David", "资深设计师"),
                    ("Emma", "高级造型师")
                ]
                for st in sample_stylists:
                    cursor.execute("INSERT INTO stylists (name, title) VALUES (%s, %s)", st)
        conn.commit()

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
        with conn.cursor() as cursor:
            cursor.execute("SELECT value FROM settings WHERE key = %s", (key,))
            row = cursor.fetchone()
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
                <a href="/admin/staff" class="hover:bg-indigo-700 px-2 py-1 rounded">员工与佣金管理</a>
                <a href="/admin/reports" class="hover:bg-indigo-700 px-2 py-1 rounded">90天报表</a>
                <a href="/" target="_blank" class="bg-green-600 px-2 py-1 rounded hover:bg-green-700">🔗 顾客预约页面</a>
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
    
    current_month_prefix = datetime.now(MY_TZ).strftime("%Y-%m")
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM services ORDER BY category_type, sub_category")
            services = cursor.fetchall()
            cursor.execute("SELECT * FROM stylists ORDER BY id DESC")
            stylists = cursor.fetchall()
            cursor.execute("SELECT * FROM holidays ORDER BY date_str DESC")
            holidays = cursor.fetchall()
            
            cursor.execute("""
                SELECT staff_name, SUM(item_count) as item_count, SUM(total_sales) as total_sales, SUM(total_commission) as total_commission
                FROM (
                    -- 新版协作结账记录（更新后产生的订单）
                    SELECT os.staff_name as staff_name,
                           COUNT(os.id) as item_count,
                           SUM(i.price) as total_sales,
                           SUM(os.commission_amount) as total_commission
                    FROM order_item_staff os
                    JOIN order_items i ON os.order_item_id = i.id
                    JOIN orders o ON i.order_id = o.id
                    WHERE o.status = 'NORMAL' AND o.created_at LIKE %s
                    GROUP BY os.staff_name

                    UNION ALL

                    -- 旧版单员工记录（更新前产生的订单，没有协作明细表数据）
                    SELECT i.staff_name as staff_name,
                           COUNT(i.id) as item_count,
                           SUM(i.price) as total_sales,
                           SUM(i.commission) as total_commission
                    FROM order_items i
                    JOIN orders o ON i.order_id = o.id
                    WHERE o.status = 'NORMAL' AND o.created_at LIKE %s
                      AND i.staff_name IS NOT NULL AND i.staff_name != ''
                      AND NOT EXISTS (SELECT 1 FROM order_item_staff os2 WHERE os2.order_item_id = i.id)
                ) combined
                GROUP BY staff_name
                ORDER BY total_commission DESC
            """, (f"{current_month_prefix}%", f"{current_month_prefix}%"))
            staff_performance = cursor.fetchall()
        
    return render_template_string(LAYOUT_TEMPLATE.replace("{% block content %}{% endblock %}", """
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div class="md:col-span-2 space-y-6">
                <!-- 当月员工佣金与业绩统计 -->
                <div class="bg-white p-6 rounded shadow">
                    <h2 class="text-xl font-bold mb-4 text-indigo-600">🏆 本月 ({{ current_month }}) 员工销售业绩与佣金看板</h2>
                    <table class="w-full text-left">
                        <thead>
                            <tr class="border-b bg-gray-50 text-sm">
                                <th class="p-2">员工姓名</th>
                                <th class="p-2">服务项目数</th>
                                <th class="p-2">总销售额 (RM)</th>
                                <th class="p-2 text-green-700">应发佣金 (RM)</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for sp in staff_performance %}
                            <tr class="border-b">
                                <td class="p-2 font-bold">{{ sp.staff_name }}</td>
                                <td class="p-2">{{ sp.item_count }}</td>
                                <td class="p-2 font-bold">RM {{ "%.2f"|format(sp.total_sales) }}</td>
                                <td class="p-2 font-bold text-green-600">RM {{ "%.2f"|format(sp.total_commission) }}</td>
                            </tr>
                            {% else %}
                            <tr><td colspan="4" class="p-3 text-gray-400">本月暂无员工佣金结算记录</td></tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>

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
                    <div class="flex justify-between items-center mb-4">
                        <h2 class="text-xl font-bold">发型师 / 员工团队管理</h2>
                        <a href="/admin/staff" class="bg-indigo-600 text-white px-3 py-1.5 rounded text-sm font-bold hover:bg-indigo-700">👥 前往员工与佣金管理 →</a>
                    </div>
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
    """), services=services, stylists=stylists, holidays=holidays, open_time=open_time, close_time=close_time, closed_wd=closed_wd, current_month=current_month_prefix, staff_performance=staff_performance)

@app.route("/admin/stylist/add", methods=["POST"])
@admin_required
def add_stylist():
    name = request.form.get("name")
    title = request.form.get("title")
    commission_type = request.form.get("commission_type", "percent")
    commission_value = float(request.form.get("commission_value", 0) or 0)
    with get_db() as conn:
        with conn.cursor() as cursor:
            try:
                cursor.execute("""
                    INSERT INTO stylists (name, title, commission_type, commission_value)
                    VALUES (%s, %s, %s, %s)
                """, (name, title, commission_type, commission_value))
                conn.commit()
            except:
                conn.rollback()
    # 从员工管理页添加的，添加完返回员工管理页；否则返回原来的总览页
    if request.referrer and "/admin/staff" in request.referrer:
        return redirect(url_for("admin_staff"))
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/stylist/delete/<int:id>")
@admin_required
def delete_stylist(id):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM stylists WHERE id = %s", (id,))
            conn.commit()
    return redirect(url_for("admin_dashboard"))

STAFF_MANAGEMENT_TEMPLATE = """
    <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div class="md:col-span-2 bg-white p-6 rounded shadow">
            <h2 class="text-xl font-bold mb-4 text-indigo-600">👥 员工与佣金管理</h2>
            <table class="w-full text-left border-collapse">
                <thead>
                    <tr class="border-b bg-gray-50 text-sm">
                        <th class="p-2">姓名</th>
                        <th class="p-2">职级/头衔</th>
                        <th class="p-2">提成方式</th>
                        <th class="p-2">提成数值</th>
                        <th class="p-2">操作</th>
                    </tr>
                </thead>
                <tbody>
                    {% for st in stylists %}
                    <tr class="border-b" id="row-{{ st.id }}">
                        <form action="/admin/staff/update/{{ st.id }}" method="POST" class="contents">
                        <td class="p-2">
                            <input type="text" name="name" value="{{ st.name }}" class="border rounded p-1.5 w-full font-bold text-indigo-600" required>
                        </td>
                        <td class="p-2">
                            <input type="text" name="title" value="{{ st.title }}" class="border rounded p-1.5 w-full" required>
                        </td>
                        <td class="p-2">
                            <select name="commission_type" class="border rounded p-1.5">
                                <option value="percent" {% if st.commission_type == 'percent' %}selected{% endif %}>按百分比 (%)</option>
                                <option value="fixed" {% if st.commission_type == 'fixed' %}selected{% endif %}>固定金额 (RM)</option>
                            </select>
                        </td>
                        <td class="p-2">
                            <input type="number" step="0.01" name="commission_value" value="{{ st.commission_value }}" class="border rounded p-1.5 w-24">
                        </td>
                        <td class="p-2 flex gap-2 items-center">
                            <button class="bg-indigo-600 text-white px-3 py-1 rounded text-xs font-bold hover:bg-indigo-700">保存</button>
                        </form>
                            <a href="/admin/stylist/delete/{{ st.id }}" onclick="return confirm('确定要删除该员工吗？')" class="text-red-500 text-xs font-bold">删除</a>
                        </td>
                    </tr>
                    {% else %}
                    <tr><td colspan="5" class="p-3 text-gray-400">暂无员工，请在右侧添加</td></tr>
                    {% endfor %}
                </tbody>
            </table>
            <p class="text-xs text-gray-500 mt-3">💡 提成方式说明：选择「按百分比」时，员工做该项目会按售价的百分比自动计算建议佣金；选择「固定金额」则每次固定建议这个金额，两种方式都可以在 POS 收银时临时手动调整具体金额。</p>
        </div>

        <div class="bg-white p-6 rounded shadow h-fit">
            <h2 class="text-xl font-bold mb-4">添加新员工</h2>
            <form action="/admin/stylist/add" method="POST">
                <div class="mb-3">
                    <label class="block text-sm font-medium">姓名</label>
                    <input type="text" name="name" class="w-full border rounded p-2" required placeholder="如: Kevin">
                </div>
                <div class="mb-3">
                    <label class="block text-sm font-medium">职级 / 简介</label>
                    <input type="text" name="title" class="w-full border rounded p-2" required placeholder="如: 高级造型师">
                </div>
                <div class="mb-3">
                    <label class="block text-sm font-medium">提成方式</label>
                    <select name="commission_type" class="w-full border rounded p-2">
                        <option value="percent">按百分比 (%)</option>
                        <option value="fixed">固定金额 (RM)</option>
                    </select>
                </div>
                <div class="mb-4">
                    <label class="block text-sm font-medium">提成数值</label>
                    <input type="number" step="0.01" name="commission_value" class="w-full border rounded p-2" value="0" placeholder="例如: 20 (代表20%) 或 15 (代表RM15)">
                </div>
                <button class="w-full bg-indigo-600 text-white font-bold py-2 rounded hover:bg-indigo-700">确认添加员工</button>
            </form>
        </div>
    </div>
"""

@app.route("/admin/staff")
@admin_required
def admin_staff():
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM stylists ORDER BY id DESC")
            stylists = cursor.fetchall()
    return render_template_string(LAYOUT_TEMPLATE.replace("{% block content %}{% endblock %}", STAFF_MANAGEMENT_TEMPLATE), stylists=stylists)

@app.route("/admin/staff/update/<int:id>", methods=["POST"])
@admin_required
def admin_staff_update(id):
    name = request.form.get("name")
    title = request.form.get("title")
    commission_type = request.form.get("commission_type", "percent")
    commission_value = float(request.form.get("commission_value", 0) or 0)
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE stylists SET name = %s, title = %s, commission_type = %s, commission_value = %s
                WHERE id = %s
            """, (name, title, commission_type, commission_value, id))
            conn.commit()
    return redirect(url_for("admin_staff"))

@app.route("/admin/settings/update", methods=["POST"])
@admin_required
def update_settings():
    open_time = request.form.get("open_time", "10:00")
    close_time = request.form.get("close_time", "20:00")
    closed_weekdays = request.form.get("closed_weekdays", "1")
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO settings (key, value) VALUES ('open_time', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (open_time,))
            cursor.execute("INSERT INTO settings (key, value) VALUES ('close_time', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (close_time,))
            cursor.execute("INSERT INTO settings (key, value) VALUES ('closed_weekdays', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (closed_weekdays,))
            conn.commit()
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/holiday/add", methods=["POST"])
@admin_required
def add_holiday():
    date_str = request.form.get("date_str")
    reason = request.form.get("reason", "闭店休息")
    with get_db() as conn:
        with conn.cursor() as cursor:
            try:
                cursor.execute("INSERT INTO holidays (date_str, reason) VALUES (%s, %s)", (date_str, reason))
                conn.commit()
            except:
                conn.rollback()
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/holiday/delete/<int:id>")
@admin_required
def delete_holiday(id):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM holidays WHERE id = %s", (id,))
            conn.commit()
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
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO services (name, category_type, sub_category, price, duration, credit_value) 
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (name, cat, cat, price, duration, credit_value))
            conn.commit()
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/service/delete/<int:id>")
@admin_required
def delete_service(id):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM services WHERE id = %s", (id,))
            conn.commit()
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/customers")
@admin_required
def admin_customers():
    search_query = request.args.get("q", "").strip()
    with get_db() as conn:
        with conn.cursor() as cursor:
            if search_query:
                cursor.execute("""
                    SELECT c.*, 
                           (SELECT COUNT(*) FROM appointments WHERE customer_id = c.id) as app_count,
                           (SELECT COUNT(*) FROM orders WHERE customer_id = c.id AND status = 'NORMAL') as order_count
                    FROM customers c 
                    WHERE c.name ILIKE %s OR c.phone ILIKE %s
                    ORDER BY c.id DESC
                """, (f"%{search_query}%", f"%{search_query}%"))
            else:
                cursor.execute("""
                    SELECT c.*, 
                           (SELECT COUNT(*) FROM appointments WHERE customer_id = c.id) as app_count,
                           (SELECT COUNT(*) FROM orders WHERE customer_id = c.id AND status = 'NORMAL') as order_count
                    FROM customers c 
                    ORDER BY c.id DESC
                """)
            customers = cursor.fetchall()
            
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
        with conn.cursor() as cursor:
            try:
                cursor.execute("INSERT INTO customers (name, phone, token, credits) VALUES (%s, %s, %s, %s)", (name, phone, token, credits))
                conn.commit()
            except:
                conn.rollback()
    return redirect(url_for("admin_customers"))

@app.route("/admin/customer/detail/<int:id>")
@admin_required
def admin_customer_detail(id):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM customers WHERE id = %s", (id,))
            cust = cursor.fetchone()
            if not cust:
                return "找不到该会员", 404
            cursor.execute("""
                SELECT o.*, i.item_name, i.price FROM orders o 
                LEFT JOIN order_items i ON o.id = i.order_id 
                WHERE o.customer_id = %s 
                ORDER BY o.created_at DESC
            """, (id,))
            orders = cursor.fetchall()
            cursor.execute("""
                SELECT a.*, s.name as service_name FROM appointments a
                JOIN services s ON a.service_id = s.id
                WHERE a.customer_id = %s
                ORDER BY a.start_time DESC
            """, (id,))
            appointments = cursor.fetchall()
            
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
                <a href="/admin/staff" class="hover:bg-indigo-700 px-2 py-1 rounded">员工与佣金管理</a>
                <a href="/admin/reports" class="hover:bg-indigo-700 px-2 py-1 rounded">90天报表</a>
                <a href="/" target="_blank" class="bg-green-600 px-2 py-1 rounded hover:bg-green-700">🔗 顾客预约页面</a>
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
    selected_date = request.args.get("date", get_current_date())
    today_str = get_current_date()
    date_strip = []
    base_dt = datetime.strptime(selected_date, "%Y-%m-%d")
    start_loop = base_dt - timedelta(days=5)
    
    end_loop = start_loop + timedelta(days=14)
    with get_db() as conn:
        with conn.cursor() as cursor:
            # 一次性查出这 15 天内每天的预约数量，避免逐天单独查询造成的多次网络往返
            cursor.execute("""
                SELECT LEFT(start_time, 10) as day_str, COUNT(*) as cnt
                FROM appointments
                WHERE status = 'CONFIRMED' AND start_time >= %s AND start_time < %s
                GROUP BY LEFT(start_time, 10)
            """, (start_loop.strftime("%Y-%m-%d"), (end_loop + timedelta(days=1)).strftime("%Y-%m-%d")))
            counts_map = {row["day_str"]: row["cnt"] for row in cursor.fetchall()}

            for i in range(15):
                d = start_loop + timedelta(days=i)
                d_str = d.strftime("%Y-%m-%d")
                wd_map = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
                wd_str = wd_map[d.weekday()]
                if d_str == today_str: wd_str = "今天"
                cnt = counts_map.get(d_str, 0)
                date_strip.append({"date_str": d_str, "display_date": d.strftime("%m-%d"), "weekday": wd_str, "count": cnt})
                
            cursor.execute("""
                SELECT a.*, c.name as customer_name, c.phone as customer_phone, s.name as service_name 
                FROM appointments a 
                JOIN customers c ON a.customer_id = c.id 
                JOIN services s ON a.service_id = s.id 
                WHERE a.start_time LIKE %s
                ORDER BY a.start_time ASC
            """, (f"{selected_date}%",))
            appointments = cursor.fetchall()
            
            cursor.execute("SELECT * FROM services WHERE category_type != 'Packages'")
            services = cursor.fetchall()
            cursor.execute("SELECT * FROM stylists")
            stylists = cursor.fetchall()
            cursor.execute("SELECT * FROM customers")
            customers = cursor.fetchall()
        
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
        with conn.cursor() as cursor:
            cursor.execute("SELECT duration FROM services WHERE id = %s", (service_id,))
            srv = cursor.fetchone()
            duration = srv["duration"] if srv else 30
            start_dt = datetime.strptime(f"{b_date} {b_time}", "%Y-%m-%d %H:%M")
            end_dt = start_dt + timedelta(minutes=duration)
            start_str = start_dt.strftime("%Y-%m-%d %H:%M")
            end_str = end_dt.strftime("%Y-%m-%d %H:%M")
            
            cursor.execute("SELECT id FROM customers WHERE phone = %s", (c_phone,))
            cust = cursor.fetchone()
            if not cust:
                token = secrets.token_hex(8)
                cursor.execute("INSERT INTO customers (name, phone, token) VALUES (%s, %s, %s) RETURNING id", (c_name, c_phone, token))
                cust_id = cursor.fetchone()["id"]
            else:
                cust_id = cust["id"]
                
            cursor.execute("INSERT INTO appointments (customer_id, service_id, stylist, start_time, end_time) VALUES (%s, %s, %s, %s, %s)", (cust_id, service_id, stylist, start_str, end_str))
            conn.commit()
        
    return redirect(url_for("admin_appointments", date=b_date))

@app.route("/admin/appointment/delete/<int:id>")
@admin_required
def delete_appointment(id):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM appointments WHERE id = %s", (id,))
            conn.commit()
    return redirect(url_for("admin_appointments"))

@app.route("/admin/orders")
@admin_required
def admin_orders():
    search_q = request.args.get("q", "").strip()
    date_q = request.args.get("date", "").strip()
    
    with get_db() as conn:
        with conn.cursor() as cursor:
            query = """
                SELECT o.*, c.name as customer_name, c.phone as customer_phone, c.token as customer_token 
                FROM orders o 
                JOIN customers c ON o.customer_id = c.id 
                WHERE 1=1
            """
            params = []
            if search_q:
                query += " AND (o.order_no ILIKE %s OR c.name ILIKE %s OR c.phone ILIKE %s)"
                params.extend([f"%{search_q}%", f"%{search_q}%", f"%{search_q}%"])
            if date_q:
                query += " AND o.created_at LIKE %s"
                params.append(f"{date_q}%")
                
            query += " ORDER BY o.created_at DESC"
            cursor.execute(query, params)
            orders = cursor.fetchall()
        
    return render_template_string(LAYOUT_TEMPLATE.replace("{% block content %}{% endblock %}", """
        <div class="bg-white p-6 rounded shadow space-y-4">
            <div class="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
                <h2 class="text-xl font-bold">历史订单管理与单据查询</h2>
                <form action="/admin/orders" method="GET" class="flex flex-wrap gap-2 items-center w-full md:w-auto">
                    <input type="date" name="date" value="{{ date_q }}" class="border rounded px-3 py-1 text-sm">
                    <input type="text" name="q" value="{{ search_q }}" placeholder="搜单号、姓名、电话..." class="border rounded px-3 py-1 text-sm flex-grow">
                    <button class="bg-indigo-600 text-white px-3 py-1 rounded text-sm font-bold">筛选/查询</button>
                    {% if search_q or date_q %}
                    <a href="/admin/orders" class="bg-gray-300 text-gray-700 px-3 py-1 rounded text-sm font-bold">重置</a>
                    {% endif %}
                </form>
            </div>
            
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
                            <a href="/admin/order/invoice/{{ order.id }}" class="text-indigo-600 font-bold hover:underline">查看/下载</a>
                            <a href="/admin/order/whatsapp/{{ order.id }}" target="_blank" class="bg-green-600 text-white px-2.5 py-1 rounded font-bold hover:bg-green-700 text-xs flex items-center gap-1">
                                💬 WhatsApp
                            </a>
                            {% if order.status != 'VOID' %}
                            <a href="/admin/order/void/{{ order.id }}" onclick="return confirm('确定作废此订单吗？')" class="text-red-500 font-bold">作废</a>
                            {% endif %}
                        </td>
                    </tr>
                    {% else %}
                    <tr><td colspan="7" class="p-6 text-center text-gray-400">没有找到相关历史订单</td></tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    """), orders=orders, search_q=search_q, date_q=date_q)

@app.route("/admin/order/remark/<int:id>", methods=["POST"])
@admin_required
def update_order_remark(id):
    remark = request.form.get("remark", "")
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE orders SET remark = %s WHERE id = %s", (remark, id))
            conn.commit()
    return redirect(url_for("admin_orders"))

@app.route("/admin/order/whatsapp/<int:id>")
@admin_required
def admin_order_whatsapp(id):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT o.*, c.name as customer_name, c.phone as customer_phone, c.token as customer_token 
                FROM orders o JOIN customers c ON o.customer_id = c.id WHERE o.id = %s
            """, (id,))
            order = cursor.fetchone()
            if not order:
                return "Order not found", 404
            cursor.execute("SELECT * FROM order_items WHERE order_id = %s", (id,))
            items = cursor.fetchall()
        
    items_str = "\n".join([f"- {item['item_name']}: RM {item['price']:.2f}" for item in items])
    portal_link = request.host_url.rstrip('/') + f"/customer/{order['customer_token']}"
    
    msg = (
        f"🌟 *Dew Hair Salon - Official Invoice* 🌟\n\n"
        f"Hello *{order['customer_name']}*,\n"
        f"Thank you for visiting us! You can view and download your receipt & profile here:\n\n"
        f"🧾 *Order No:* {order['order_no']}\n"
        f"📅 *Date:* {order['created_at']}\n\n"
        f"*Purchased Items:*\n{items_str}\n\n"
        f"💰 *Total Amount:* RM {order['total_amount']:.2f}\n"
        f"💳 *Payment Method:* {order['payment_details']}\n"
    )
    if order['remark']:
        msg += f"📝 *Remark:* {order['remark']}\n"
        
    msg += f"\n🔗 *My Member Portal & Download Receipt:*\n{portal_link}\n\nHope to see you again soon!"
    
    phone = "".join(filter(str.isdigit, order['customer_phone']))
    if phone.startswith('0'):
        phone = '6' + phone  
        
    wa_url = f"https://api.whatsapp.com/send?phone={phone}&text={urllib.parse.quote(msg)}"
    return redirect(wa_url)

@app.route("/admin/order/invoice/<int:id>")
@admin_required
def admin_order_invoice(id):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT o.*, c.name as customer_name, c.phone as customer_phone, c.token as customer_token 
                FROM orders o JOIN customers c ON o.customer_id = c.id WHERE o.id = %s
            """, (id,))
            order = cursor.fetchone()
            if not order: return "Order not found", 404
            cursor.execute("SELECT * FROM order_items WHERE order_id = %s", (id,))
            items = cursor.fetchall()
            for item in items:
                cursor.execute("SELECT staff_name, commission_amount FROM order_item_staff WHERE order_item_id = %s", (item["id"],))
                item["collaborators"] = cursor.fetchall()
        
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
            <h3 style="font-size:16px;margin-bottom:8px;">Items Purchased & Staff Commission</h3>
            <table style="width:100%;border-collapse:collapse;font-size:14px;margin-bottom:15px;">
                <thead>
                    <tr style="border-bottom:1px solid #ddd;background:#f9fafb;">
                        <th style="text-align:left;padding:6px;">Item Name</th>
                        <th style="text-align:left;padding:6px;">Staff</th>
                        <th style="text-align:right;padding:6px;">Price & Commission</th>
                    </tr>
                </thead>
                <tbody>
                    {% for item in items %}
                    <tr style="border-bottom:1px solid #eee;">
                        <td style="padding:6px;">{{ item.item_name }}</td>
                        <td style="padding:6px;color:#4f46e5;">
                            {% if item.collaborators %}
                                {% for c in item.collaborators %}
                                    <div>{{ c.staff_name }} <span style="color:green;font-size:11px;">(RM {{ "%.2f"|format(c.commission_amount) }})</span></div>
                                {% endfor %}
                            {% elif item.staff_name %}
                                <div>{{ item.staff_name }} {% if item.commission > 0 %}<span style="color:green;font-size:11px;">(RM {{ "%.2f"|format(item.commission) }})</span>{% endif %}</div>
                            {% else %}-{% endif %}
                        </td>
                        <td style="text-align:right;padding:6px;">
                            RM {{ "%.2f"|format(item.price) }}
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            <div style="text-align:right;font-size:18px;font-weight:bold;margin-bottom:20px;">
                Total Amount: <span style="color:#dc2626;">RM {{ "%.2f"|format(order.total_amount) }}</span>
            </div>
            <div style="display:flex;gap:10px;">
                <button onclick="window.print()" style="flex:1;padding:12px;background:#10b981;color:white;border:none;border-radius:6px;font-weight:bold;cursor:pointer;">📥 下载/打印收据 (PDF)</button>
                <a href="/admin/order/whatsapp/{{ order.id }}" target="_blank" style="flex:1;text-align:center;padding:12px;background:#25d366;color:white;text-decoration:none;border-radius:6px;font-weight:bold;">💬 发送 WhatsApp</a>
                <a href="/admin/orders" style="padding:12px 15px;background:#4f46e5;color:white;text-decoration:none;border-radius:6px;font-weight:bold;">返回</a>
            </div>
        </div>
    """, order=order, items=items)

@app.route("/admin/order/void/<int:id>")
@admin_required
def void_order(id):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM orders WHERE id = %s AND status = 'NORMAL'", (id,))
            order = cursor.fetchone()
            if not order: return redirect(url_for("admin_orders"))
            
            cust_id = order["customer_id"]
            total = order["total_amount"]
            payment = order["payment_details"]
            
            cursor.execute("SELECT * FROM order_items WHERE order_id = %s", (id,))
            items = cursor.fetchall()
            cursor.execute("SELECT credits FROM customers WHERE id = %s", (cust_id,))
            cust = cursor.fetchone()
            current_credits = cust["credits"] if cust else 0.0
            
            for item in items:
                cursor.execute("SELECT credit_value FROM services WHERE name = %s", (item["item_name"],))
                srv = cursor.fetchone()
                if srv and srv["credit_value"] > 0:
                    current_credits -= srv["credit_value"]
            
            if payment == "Credit Balance Deduct":
                current_credits += total
                
            cursor.execute("UPDATE customers SET credits = %s WHERE id = %s", (max(0.0, current_credits), cust_id))
            cursor.execute("UPDATE orders SET status = 'VOID' WHERE id = %s", (id,))
            conn.commit()
        
    return redirect(url_for("admin_orders"))

@app.route("/admin/reports")
@admin_required
def admin_reports():
    with get_db() as conn:
        with conn.cursor() as cursor:
            days_data = []
            now_dt = datetime.now(MY_TZ)
            range_start = (now_dt - timedelta(days=89)).strftime("%Y-%m-%d")
            # 一次性按天汇总最近 90 天的订单数据，避免 90 次单独查询
            cursor.execute("""
                SELECT LEFT(created_at, 10) as day_str, COUNT(id) as cnt, SUM(total_amount) as total
                FROM orders
                WHERE status = 'NORMAL' AND created_at >= %s
                GROUP BY LEFT(created_at, 10)
            """, (range_start,))
            day_map = {row["day_str"]: row for row in cursor.fetchall()}
            for i in range(90):
                d = now_dt - timedelta(days=i)
                d_str = d.strftime("%Y-%m-%d")
                row = day_map.get(d_str)
                cnt = row["cnt"] if row and row["cnt"] else 0
                total = row["total"] if row and row["total"] else 0.0
                days_data.append({"date_str": d_str, "count": cnt, "total": total})
            
    return render_template_string(LAYOUT_TEMPLATE.replace("{% block content %}{% endblock %}", """
        <div class="bg-white p-6 rounded shadow space-y-4">
            <h2 class="text-xl font-bold text-indigo-600">📊 最近 90 天营业报表</h2>
            <table class="w-full text-left border-collapse">
                <thead>
                    <tr class="border-b bg-gray-50 text-sm">
                        <th class="p-2">日期</th>
                        <th class="p-2">完成订单数</th>
                        <th class="p-2">营业额 (RM)</th>
                    </tr>
                </thead>
                <tbody>
                    {% for d in days_data %}
                    <tr class="border-b hover:bg-gray-50">
                        <td class="p-2 font-bold">{{ d.date_str }}</td>
                        <td class="p-2">{{ d.count }} 单</td>
                        <td class="p-2 font-bold text-green-600">RM {{ "%.2f"|format(d.total) }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    """), days_data=days_data)

@app.route("/admin/pos")
@admin_required
def admin_pos():
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM services")
            services = cursor.fetchall()
            cursor.execute("SELECT * FROM stylists")
            stylists = cursor.fetchall()
            cursor.execute("SELECT * FROM customers")
            customers = cursor.fetchall()
    stylists_json = json.dumps([
        {"name": st["name"], "title": st["title"], "commission_type": st["commission_type"], "commission_value": st["commission_value"]}
        for st in stylists
    ])
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
                <div id="order-items" class="min-h-[150px] border-b mb-4 pb-2"><p class="text-gray-400">点击左侧项目加入订单</p></div>
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
        
        <!-- 协作员工与佣金配置弹窗 -->
        <div id="staffModal" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 hidden">
            <div class="bg-white p-6 rounded-xl shadow-xl max-w-md w-full max-h-[85vh] overflow-y-auto">
                <h3 class="text-lg font-bold mb-1 text-indigo-600">设置协作员工与佣金</h3>
                <p id="modal_item_name" class="text-sm font-medium text-gray-700 mb-3"></p>

                <div id="collaborator_list" class="space-y-2 mb-3"></div>

                <button type="button" onclick="addCollaboratorRow()" class="w-full border-2 border-dashed border-indigo-300 text-indigo-600 font-bold py-2 rounded text-sm hover:bg-indigo-50 mb-4">
                    + 添加协作员工
                </button>

                <div class="text-right text-sm font-bold text-gray-700 mb-4">
                    佣金总额: RM <span id="modal_total_commission">0.00</span>
                </div>

                <div class="flex justify-end gap-2">
                    <button type="button" onclick="closeStaffModal()" class="bg-gray-300 px-3 py-1.5 rounded text-sm font-bold">取消</button>
                    <button type="button" onclick="saveStaffModal()" class="bg-indigo-600 text-white px-4 py-1.5 rounded text-sm font-bold">确定保存</button>
                </div>
            </div>
        </div>

        <script>
            let cart = [];
            let editingIndex = null;
            const STYLISTS = {{ stylists_json|safe }};

            function addToOrder(name, price) {
                cart.push({name, price, collaborators: []});
                renderCart();
            }

            // 根据员工自己设置的提成方式，自动算出这一笔项目该给他的建议佣金
            function calcSuggestedCommission(staffName, itemPrice) {
                const st = STYLISTS.find(s => s.name === staffName);
                if (!st) return 0;
                if (st.commission_type === 'percent') {
                    return Math.round(itemPrice * (st.commission_value / 100) * 100) / 100;
                }
                return st.commission_value;
            }

            function openStaffModal(index) {
                editingIndex = index;
                const item = cart[index];
                document.getElementById('modal_item_name').innerText = "项目: " + item.name + " (售价: RM " + item.price.toFixed(2) + ")";
                if (!item.collaborators || item.collaborators.length === 0) {
                    item.collaborators = [{staff: '', commission: 0}];
                }
                renderCollaboratorRows(item.collaborators);
                document.getElementById('staffModal').classList.remove('hidden');
            }

            function renderCollaboratorRows(collaborators) {
                const list = document.getElementById('collaborator_list');
                list.innerHTML = '';
                collaborators.forEach((c, i) => {
                    const options = STYLISTS.map(st =>
                        `<option value="${st.name}" ${c.staff === st.name ? 'selected' : ''}>${st.name} (${st.title})</option>`
                    ).join('');
                    const row = document.createElement('div');
                    row.className = 'flex gap-2 items-center border rounded p-2 bg-gray-50';
                    row.innerHTML = `
                        <select class="collab-staff border rounded p-1.5 text-sm flex-grow" onchange="onCollaboratorStaffChange(${i}, this)">
                            <option value="">-- 选择员工 --</option>
                            ${options}
                        </select>
                        <input type="number" step="0.01" class="collab-commission border rounded p-1.5 text-sm w-24" value="${c.commission}" onchange="onCollaboratorCommissionChange(${i}, this)">
                        <button type="button" onclick="removeCollaboratorRow(${i})" class="text-red-500 font-bold px-1">×</button>
                    `;
                    list.appendChild(row);
                });
                updateModalTotal();
            }

            function currentItem() { return cart[editingIndex]; }

            function addCollaboratorRow() {
                currentItem().collaborators.push({staff: '', commission: 0});
                renderCollaboratorRows(currentItem().collaborators);
            }

            function removeCollaboratorRow(i) {
                currentItem().collaborators.splice(i, 1);
                renderCollaboratorRows(currentItem().collaborators);
            }

            function onCollaboratorStaffChange(i, sel) {
                const item = currentItem();
                item.collaborators[i].staff = sel.value;
                // 自动带入这位员工按自己提成设置算出的建议佣金，方便直接用或手动改
                item.collaborators[i].commission = calcSuggestedCommission(sel.value, item.price);
                renderCollaboratorRows(item.collaborators);
            }

            function onCollaboratorCommissionChange(i, input) {
                currentItem().collaborators[i].commission = parseFloat(input.value) || 0;
                updateModalTotal();
            }

            function updateModalTotal() {
                const item = currentItem();
                const total = (item.collaborators || []).reduce((sum, c) => sum + (parseFloat(c.commission) || 0), 0);
                document.getElementById('modal_total_commission').innerText = total.toFixed(2);
            }

            function closeStaffModal() {
                document.getElementById('staffModal').classList.add('hidden');
                editingIndex = null;
            }

            function saveStaffModal() {
                if (editingIndex !== null) {
                    // 去掉没有选员工的空行
                    cart[editingIndex].collaborators = (cart[editingIndex].collaborators || []).filter(c => c.staff);
                    renderCart();
                }
                closeStaffModal();
            }

            function removeFromCart(index) {
                cart.splice(index, 1);
                renderCart();
            }

            function renderCart() {
                const container = document.getElementById('order-items');
                let total = 0; container.innerHTML = '';
                cart.forEach((item, index) => {
                    total += item.price;
                    const collabs = item.collaborators || [];
                    const staffSummary = collabs.length > 0
                        ? collabs.map(c => `${c.staff}(RM${c.commission.toFixed(2)})`).join(' + ')
                        : '无';
                    container.innerHTML += `
                        <div class="flex justify-between items-center py-2 border-b text-sm">
                            <div>
                                <div class="font-bold">${item.name}</div>
                                <div class="text-xs text-gray-500">协作员工: ${staffSummary}</div>
                            </div>
                            <div class="flex items-center gap-2">
                                <span class="font-bold">RM ${item.price.toFixed(2)}</span>
                                <button type="button" onclick="openStaffModal(${index})" class="text-indigo-600 text-xs bg-indigo-50 px-2 py-1 rounded font-bold">+ 添加协作员工</button>
                                <button type="button" onclick="removeFromCart(${index})" class="text-red-500 font-bold">×</button>
                            </div>
                        </div>`;
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
    """), services=services, stylists=stylists, customers=customers, stylists_json=stylists_json)

@app.route("/admin/checkout", methods=["POST"])
@admin_required
def checkout():
    try:
        cart_data = json.loads(request.form.get("cart_data", "[]"))
        if not cart_data:
            return "订单不能为空", 400
        name = request.form.get("customer_name")
        phone = request.form.get("customer_phone_input") or request.form.get("customer_phone")
        pay_method = request.form.get("payment_method")
        total = sum(item["price"] for item in cart_data)
        
        current_time_str = get_current_time()
        order_no = "INV" + datetime.now(MY_TZ).strftime("%Y%m%d%H%M%S")
        
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT id, token, credits FROM customers WHERE phone = %s", (phone,))
                cust = cursor.fetchone()
                if cust:
                    cust_id = cust["id"]
                    cust_token = cust["token"]
                    current_credits = cust["credits"]
                else:
                    cust_token = secrets.token_hex(8)
                    cursor.execute("INSERT INTO customers (name, phone, token, credits) VALUES (%s, %s, %s, 0.0) RETURNING id", (name, phone, cust_token))
                    cust_id = cursor.fetchone()["id"]
                    current_credits = 0.0
                
                for item in cart_data:
                    cursor.execute("SELECT credit_value FROM services WHERE name = %s", (item["name"],))
                    srv = cursor.fetchone()
                    if srv and srv["credit_value"] > 0:
                        current_credits += srv["credit_value"]
                        cursor.execute("UPDATE customers SET credits = %s WHERE id = %s", (current_credits, cust_id))

                if pay_method == "Credit Balance Deduct":
                    if current_credits < total: return "结算失败：该顾客 Credit 余额不足！", 400
                    current_credits -= total
                    cursor.execute("UPDATE customers SET credits = %s WHERE id = %s", (current_credits, cust_id))
                    
                cursor.execute("INSERT INTO orders (order_no, customer_id, total_amount, payment_details, status, created_at) VALUES (%s, %s, %s, %s, 'NORMAL', %s) RETURNING id", (order_no, cust_id, total, pay_method, current_time_str))
                order_id = cursor.fetchone()["id"]
                
                for item in cart_data:
                    collaborators = item.get("collaborators", [])
                    # 兼容旧版单员工数据结构（如果前端还是传 staff/commission 单字段）
                    if not collaborators and item.get("staff"):
                        collaborators = [{"staff": item.get("staff"), "commission": item.get("commission", 0.0)}]

                    total_item_commission = sum(float(c.get("commission", 0) or 0) for c in collaborators)
                    primary_staff = ", ".join(c["staff"] for c in collaborators if c.get("staff")) if collaborators else ""

                    cursor.execute("""
                        INSERT INTO order_items (order_id, item_name, price, staff_name, commission) 
                        VALUES (%s, %s, %s, %s, %s) RETURNING id
                    """, (order_id, item["name"], item["price"], primary_staff, total_item_commission))
                    order_item_id = cursor.fetchone()["id"]

                    for c in collaborators:
                        if not c.get("staff"):
                            continue
                        cursor.execute("""
                            INSERT INTO order_item_staff (order_item_id, staff_name, commission_amount)
                            VALUES (%s, %s, %s)
                        """, (order_item_id, c["staff"], float(c.get("commission", 0) or 0)))
                conn.commit()
                
        return f"""
            <div style="max-width:500px;margin:50px auto;padding:20px;border:1px solid #ccc;font-family:sans-serif;border-radius:8px;background:#fff;">
                <h2 style="color:green;">收银成功凭证</h2>
                <hr>
                <p><strong>单号：</strong> {order_no}</p>
                <p><strong>时间：</strong> {current_time_str}</p>
                <p><strong>顾客：</strong> {name} ({phone})</p>
                <p><strong>金额：</strong> RM {total:.2f} ({pay_method})</p>
                <div style="margin-top:20px;display:flex;gap:10px;">
                    <a href="/admin/order/invoice/{order_id}" style="padding:10px 15px;background:#4f46e5;color:white;text-decoration:none;border-radius:6px;font-weight:bold;">📥 查看并下载收据</a>
                    <a href="/admin/order/whatsapp/{order_id}" target="_blank" style="padding:10px 15px;background:#25d366;color:white;text-decoration:none;border-radius:6px;font-weight:bold;">💬 发送 WhatsApp 单据</a>
                    <a href="/admin/pos" style="padding:10px 15px;background:#6b7280;color:white;text-decoration:none;border-radius:6px;font-weight:bold;">返回 POS</a>
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
        <p class="text-center text-sm text-gray-500 mb-6">营业时间: {{ open_time }} - {{ close_time }} (马来西亚时间)</p>
        
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
                    i.parentElement.classList.add('bg-white', 'text-gray-800');
                });
                if(this.checked) {
                    this.parentElement.classList.remove('bg-white', 'text-gray-800');
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

@app.route("/", methods=["GET"])
def index():
    selected_date = request.args.get("date", get_current_date())
    today_str = get_current_date()
    date_strip = []
    base_dt = datetime.strptime(selected_date, "%Y-%m-%d")
    start_loop = base_dt - timedelta(days=2)
    
    with get_db() as conn:
        with conn.cursor() as cursor:
            for i in range(10):
                d = start_loop + timedelta(days=i)
                d_str = d.strftime("%Y-%m-%d")
                wd_map = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
                wd_str = wd_map[d.weekday()]
                if d_str == today_str: wd_str = "今天"
                date_strip.append({"date_str": d_str, "display_date": d.strftime("%m-%d"), "year": d.strftime("%Y"), "weekday": wd_str})
                
            cursor.execute("SELECT * FROM services WHERE category_type != 'Packages'")
            services = cursor.fetchall()
            cursor.execute("SELECT * FROM stylists")
            stylists = cursor.fetchall()
            
    open_time_str = get_setting("open_time", "10:00")
    close_time_str = get_setting("close_time", "20:00")
    timeslots = []
    st = datetime.strptime(open_time_str, "%H:%M")
    et = datetime.strptime(close_time_str, "%H:%M")
    while st <= et:
        timeslots.append(st.strftime("%H:%M"))
        st += timedelta(minutes=30)
        
    error = request.args.get("error")
    return render_template_string(BOOKING_CALENDAR_TEMPLATE, services=services, stylists=stylists, date_strip=date_strip, selected_date=selected_date, today_str=today_str, open_time=open_time_str, close_time=close_time_str, timeslots=timeslots, error=error)

@app.route("/book", methods=["POST"])
def book_appointment():
    service_id = request.form.get("service_id")
    stylist = request.form.get("stylist")
    b_date = request.form.get("booking_date")
    b_time = request.form.get("booking_time")
    c_name = request.form.get("customer_name")
    c_phone = request.form.get("customer_phone")
    
    with get_db() as conn:
        with conn.cursor() as cursor:
            # 校验休息日
            cursor.execute("SELECT * FROM holidays WHERE date_str = %s", (b_date,))
            if cursor.fetchone():
                return redirect(url_for("index", date=b_date, error="该日期为临时闭店日，无法预约！"))
            
            dt_obj = datetime.strptime(b_date, "%Y-%m-%d")
            closed_wd = get_setting("closed_weekdays", "1")
            if closed_wd != '-1' and dt_obj.weekday() == int(closed_wd):
                return redirect(url_for("index", date=b_date, error="该日期为门店固定休息日，无法预约！"))
                
            cursor.execute("SELECT duration FROM services WHERE id = %s", (service_id,))
            srv = cursor.fetchone()
            duration = srv["duration"] if srv else 30
            
            start_dt = datetime.strptime(f"{b_date} {b_time}", "%Y-%m-%d %H:%M")
            end_dt = start_dt + timedelta(minutes=duration)
            start_str = start_dt.strftime("%Y-%m-%d %H:%M")
            end_str = end_dt.strftime("%Y-%m-%d %H:%M")
            
            cursor.execute("SELECT id, token FROM customers WHERE phone = %s", (c_phone,))
            cust = cursor.fetchone()
            if not cust:
                token = secrets.token_hex(8)
                cursor.execute("INSERT INTO customers (name, phone, token) VALUES (%s, %s, %s) RETURNING id, token", (c_name, c_phone, token))
                res = cursor.fetchone()
                cust_id = res["id"]
                cust_token = res["token"]
            else:
                cust_id = cust["id"]
                cust_token = cust["token"]
                
            cursor.execute("INSERT INTO appointments (customer_id, service_id, stylist, start_time, end_time) VALUES (%s, %s, %s, %s, %s) RETURNING id", (cust_id, service_id, stylist, start_str, end_str))
            app_id = cursor.fetchone()["id"]
            conn.commit()
            
    return redirect(url_for("customer_portal", token=cust_token))

@app.route("/customer/<token>")
def customer_portal(token):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM customers WHERE token = %s", (token,))
            cust = cursor.fetchone()
            if not cust: return "会员页面不存在或链接错误", 404
            
            cursor.execute("""
                SELECT a.*, s.name as service_name, s.price FROM appointments a 
                JOIN services s ON a.service_id = s.id 
                WHERE a.customer_id = %s ORDER BY a.start_time DESC
            """, (cust["id"],))
            appointments = cursor.fetchall()
            
            cursor.execute("""
                SELECT o.*, i.item_name, i.price FROM orders o 
                LEFT JOIN order_items i ON o.id = i.order_id 
                WHERE o.customer_id = %s ORDER BY o.created_at DESC
            """, (cust["id"],))
            orders = cursor.fetchall()
            
    return render_template_string("""
        <!DOCTYPE html>
        <html lang="zh-CN">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{{ cust.name }} 的会员中心 - Dew Hair Salon</title>
            <script src="https://cdn.tailwindcss.com"></script>
        </head>
        <body class="bg-gray-50 min-h-screen p-4 md:p-8">
            <div class="max-w-3xl mx-auto space-y-6">
                <div class="bg-white p-6 rounded-xl shadow flex justify-between items-center">
                    <div>
                        <h2 class="text-2xl font-bold text-indigo-600">✨ {{ cust.name }} 的专属会员中心</h2>
                        <p class="text-sm text-gray-500">电话: {{ cust.phone }}</p>
                    </div>
                    <div class="text-right">
                        <div class="text-xs text-gray-400">账户 Credit 余额</div>
                        <div class="text-2xl font-bold text-green-600">RM {{ "%.2f"|format(cust.credits) }}</div>
                    </div>
                </div>

                <div class="bg-white p-6 rounded-xl shadow">
                    <h3 class="text-lg font-bold mb-3 text-gray-800">📅 我的预约记录</h3>
                    <div class="space-y-3">
                        {% for a in appointments %}
                        <div class="border p-4 rounded-lg flex justify-between items-center bg-gray-50">
                            <div>
                                <div class="font-bold text-indigo-600">{{ a.service_name }}</div>
                                <div class="text-sm text-gray-600">时间: {{ a.start_time }} ~ {{ a.end_time.split()[1] }}</div>
                                <div class="text-xs text-gray-500">发型师: {{ a.stylist }}</div>
                            </div>
                            <span class="bg-green-100 text-green-800 px-3 py-1 rounded-full text-xs font-bold">{{ a.status }}</span>
                        </div>
                        {% else %}
                        <p class="text-gray-400 text-sm">暂无预约记录</p>
                        {% endfor %}
                    </div>
                </div>

                <div class="bg-white p-6 rounded-xl shadow">
                    <h3 class="text-lg font-bold mb-3 text-gray-800">🧾 我的消费与充值历史</h3>
                    <table class="w-full text-left text-sm">
                        <thead><tr class="border-b bg-gray-50"><th class="p-2">单号</th><th class="p-2">时间</th><th class="p-2">项目</th><th class="p-2">金额</th><th class="p-2">支付</th></tr></thead>
                        <tbody>
                            {% for o in orders %}
                            <tr class="border-b {% if o.status == 'VOID' %}line-through text-gray-400 bg-red-50{% endif %}">
                                <td class="p-2 font-bold">{{ o.order_no }}</td>
                                <td class="p-2">{{ o.created_at }}</td>
                                <td class="p-2">{{ o.item_name }}</td>
                                <td class="p-2 font-bold">RM {{ "%.2f"|format(o.price) }}</td>
                                <td class="p-2">{{ '已作废' if o.status == 'VOID' else o.payment_details }}</td>
                            </tr>
                            {% else %}
                            <tr><td colspan="5" class="p-3 text-gray-400">暂无消费订单</td></tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </body>
        </html>
    """, cust=cust, appointments=appointments, orders=orders)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
