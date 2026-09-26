import os
import secrets
import psycopg2
import psycopg2.extras
import hmac
import json
import io
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pytz import timezone
from functools import wraps
from flask import (
    Flask, request, redirect, url_for, session, render_template_string, jsonify, send_file, g
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
    # 同一次请求内复用同一个数据库连接，避免每次调用都新开一个连接却从不关闭（连接泄漏）
    if "db_conn" not in g:
        if DATABASE_URL:
            # 兼容 PgBouncer 事务模式及参数解析
            g.db_conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        else:
            g.db_conn = psycopg2.connect("dbname=salon user=postgres password=postgres", cursor_factory=psycopg2.extras.RealDictCursor)
    return g.db_conn

@app.teardown_appcontext
def close_db_connection(exception=None):
    conn = g.pop("db_conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass

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
            cursor.execute("ALTER TABLE stylists ADD COLUMN IF NOT EXISTS rank_name TEXT DEFAULT ''")
            cursor.execute("ALTER TABLE services ADD COLUMN IF NOT EXISTS stock_qty INTEGER")
            cursor.execute("ALTER TABLE services ADD COLUMN IF NOT EXISTS bookable_online BOOLEAN DEFAULT TRUE")
            cursor.execute("ALTER TABLE services ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS service_categories (
                    id SERIAL PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS staff_ranks (
                    id SERIAL PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    commission_type TEXT DEFAULT 'percent',
                    commission_value DOUBLE PRECISION DEFAULT 0.0
                );
            """)
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
            cursor.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS discount_percent DOUBLE PRECISION DEFAULT 0.0")
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

            cursor.execute("SELECT COUNT(*) FROM staff_ranks")
            if cursor.fetchone()["count"] == 0:
                default_ranks = [
                    ("总监", "percent", 30.0),
                    ("资深设计师", "percent", 20.0),
                    ("初级/助理", "percent", 10.0),
                ]
                for r in default_ranks:
                    cursor.execute("INSERT INTO staff_ranks (name, commission_type, commission_value) VALUES (%s, %s, %s)", r)

            # 首次升级时，把现有服务里已经用过的分类标签迁移进正式的分类表
            cursor.execute("SELECT COUNT(*) FROM service_categories")
            if cursor.fetchone()["count"] == 0:
                cursor.execute("""
                    SELECT DISTINCT sub_category FROM services
                    WHERE sub_category IS NOT NULL AND sub_category NOT IN ('Services', 'Packages', 'Products')
                """)
                for row in cursor.fetchall():
                    cursor.execute("INSERT INTO service_categories (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (row["sub_category"],))
        conn.commit()

with app.app_context():
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

def get_int_setting(key, default, minimum=None):
    """安全读取数字类设置：值损坏/非数字时回退默认值，minimum 可防止 0 或负数造成死循环等问题"""
    raw = get_setting(key, str(default))
    try:
        value = int(raw)
    except (ValueError, TypeError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value

def sync_service_categories(cursor):
    """把散落在各个服务 sub_category 字段里、但还没登记进分类管理表的分类名，自动补登记进去。
    保证「分类管理」里看到的清单，永远等于系统里实际在用的所有分类，不会有遗漏或不一致。"""
    cursor.execute("""
        INSERT INTO service_categories (name)
        SELECT DISTINCT sub_category FROM services
        WHERE sub_category IS NOT NULL AND TRIM(sub_category) != ''
          AND sub_category NOT IN ('Services', 'Packages', 'Products')
        ON CONFLICT (name) DO NOTHING
    """)

def find_conflicting_appointment(cursor, stylist, start_str, end_str, exclude_id=None, buffer_minutes=0):
    """检查某发型师在这个时间段是否已经有其他预约（含缓冲时间，缓冲时间内也视为冲突）"""
    if buffer_minutes:
        start_dt = datetime.strptime(start_str, "%Y-%m-%d %H:%M") - timedelta(minutes=buffer_minutes)
        end_dt = datetime.strptime(end_str, "%Y-%m-%d %H:%M") + timedelta(minutes=buffer_minutes)
        start_str = start_dt.strftime("%Y-%m-%d %H:%M")
        end_str = end_dt.strftime("%Y-%m-%d %H:%M")
    query = """
        SELECT id FROM appointments
        WHERE stylist = %s AND status = 'CONFIRMED'
          AND start_time < %s AND end_time > %s
    """
    params = [stylist, end_str, start_str]
    if exclude_id:
        query += " AND id != %s"
        params.append(exclude_id)
    cursor.execute(query, params)
    return cursor.fetchone()

LAYOUT_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dew Hair Salon 管理系统</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <script src="https://cdn.tailwindcss.com"></script>
    <style>body { font-family: 'Inter', sans-serif; }</style>
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
                <a href="/admin/payroll" class="hover:bg-indigo-700 px-2 py-1 rounded">薪资报表</a>
                <a href="/admin/settings" class="hover:bg-indigo-700 px-2 py-1 rounded">⚙️ 设置</a>
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
    slot_interval = get_setting("slot_interval_minutes", "30")
    max_advance_days = get_setting("max_advance_days", "30")
    buffer_minutes = get_setting("buffer_minutes", "0")
    
    current_month_prefix = datetime.now(MY_TZ).strftime("%Y-%m")
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM services ORDER BY category_type, sub_category")
            services = cursor.fetchall()
            sync_service_categories(cursor)
            conn.commit()
            cursor.execute("SELECT name FROM service_categories ORDER BY name")
            existing_categories = [r["name"] for r in cursor.fetchall()]
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
                    GROUP BY i.staff_name
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

                <!-- 营业与休息日设置：已移到统一设置页 -->
                <div class="bg-white p-6 rounded shadow flex items-center justify-between">
                    <div>
                        <h2 class="text-xl font-bold">营业时间、预约规则、分类与通知</h2>
                        <p class="text-sm text-gray-500 mt-1">开门时间、休息日、预约间隔、服务分类、新预约通知等都整合到了统一的设置页面</p>
                    </div>
                    <a href="/admin/settings" class="bg-indigo-600 text-white px-4 py-2.5 rounded-lg text-sm font-bold hover:bg-indigo-700 flex-shrink-0">⚙️ 前往设置</a>
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
                    <h2 class="text-xl font-bold mb-1">项目与充值套餐管理</h2>
                    <p class="text-xs text-gray-500 mb-4">直接在下面表格里改名字、价格、分类、时长，改完点"保存"就行，不需要删除重加。</p>
                    <table class="w-full text-left">
                        <thead><tr class="border-b text-xs text-gray-500"><th class="p-2">类型</th><th class="p-2">分类标签</th><th class="p-2">名称</th><th class="p-2">售价 (RM)</th><th class="p-2">时长(分)</th><th class="p-2">Credit</th><th class="p-2">库存</th><th class="p-2">线上预约</th><th class="p-2">操作</th></tr></thead>
                        <tbody>
                            {% for item in services %}
                            <tr class="border-b {% if item.is_active == false %}bg-gray-100 opacity-60{% elif item.category_type == 'Products' and item.stock_qty is not none and item.stock_qty <= 5 %}bg-orange-50{% endif %}">
                                <form action="/admin/service/update/{{ item.id }}" method="POST" class="contents">
                                <td class="p-2">
                                    <select name="category_type" class="border rounded p-1 text-xs">
                                        <option value="Services" {% if item.category_type == 'Services' %}selected{% endif %}>Services</option>
                                        <option value="Packages" {% if item.category_type == 'Packages' %}selected{% endif %}>Packages</option>
                                        <option value="Products" {% if item.category_type == 'Products' %}selected{% endif %}>Products</option>
                                    </select>
                                </td>
                                <td class="p-2">
                                    <select name="sub_category" class="border rounded p-1 text-xs w-24">
                                        {% for c in existing_categories %}
                                        <option value="{{ c }}" {% if item.sub_category == c %}selected{% endif %}>{{ c }}</option>
                                        {% endfor %}
                                        {% if item.sub_category and item.sub_category not in existing_categories %}
                                        <option value="{{ item.sub_category }}" selected>{{ item.sub_category }} (未登记)</option>
                                        {% endif %}
                                    </select>
                                </td>
                                <td class="p-2"><input type="text" name="name" value="{{ item.name }}" class="border rounded p-1 text-sm w-28" required></td>
                                <td class="p-2"><input type="number" step="0.01" name="price" value="{{ item.price }}" class="border rounded p-1 text-sm w-20 font-bold text-red-600" required></td>
                                <td class="p-2"><input type="number" name="duration" value="{{ item.duration }}" class="border rounded p-1 text-sm w-16"></td>
                                <td class="p-2"><input type="number" step="0.01" name="credit_value" value="{{ item.credit_value }}" class="border rounded p-1 text-sm w-20" placeholder="仅Packages"></td>
                                <td class="p-2">
                                    {% if item.category_type == 'Products' %}
                                        <span class="font-bold {% if item.stock_qty is none or item.stock_qty <= 5 %}text-orange-600{% else %}text-gray-700{% endif %}">{{ item.stock_qty if item.stock_qty is not none else 0 }}</span>
                                    {% else %}-{% endif %}
                                </td>
                                <td class="p-2 text-center">
                                    <input type="checkbox" name="bookable_online" {% if item.bookable_online %}checked{% endif %} class="w-4 h-4">
                                </td>
                                <td class="p-2 flex gap-2 items-center flex-wrap">
                                    <button class="bg-indigo-600 text-white px-2.5 py-1 rounded text-xs font-bold hover:bg-indigo-700">保存</button>
                                </form>
                                    {% if item.category_type == 'Products' %}
                                    <form action="/admin/service/restock/{{ item.id }}" method="POST" class="inline-flex gap-1">
                                        <input type="number" name="delta" placeholder="+数量" class="border rounded p-1 w-14 text-xs" required>
                                        <button class="bg-green-600 text-white px-2 py-1 rounded text-[10px] font-bold hover:bg-green-700">补货</button>
                                    </form>
                                    {% endif %}
                                    {% if item.is_active == false %}
                                        <a href="/admin/service/reactivate/{{ item.id }}" class="text-green-600 text-xs font-bold">重新上架</a>
                                    {% else %}
                                        <a href="/admin/service/delete/{{ item.id }}" onclick="return confirm('确定要删除吗？如果已有历史订单/预约用过这个项目，会自动改成下架而不是真删。')" class="text-red-500 text-xs font-bold">删除/下架</a>
                                    {% endif %}
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
                            <label class="block text-sm font-medium">服务分类标签 (用于 POS 分类)</label>
                            <select name="sub_category" id="sub_category_select" onchange="toggleNewCategoryInput(this)" class="w-full border rounded p-2">
                                {% if not existing_categories %}
                                <option value="">-- 还没有分类，先在下面新增一个 --</option>
                                {% endif %}
                                {% for c in existing_categories %}
                                <option value="{{ c }}">{{ c }}</option>
                                {% endfor %}
                                <option value="__new__">+ 新增分类...</option>
                            </select>
                            <input type="text" name="new_category_name" id="new_category_name" class="w-full border rounded p-2 mt-2" placeholder="输入新分类名称，如: Ala Carte" style="display:{% if not existing_categories %}block{% else %}none{% endif %};">
                        </div>
                        <div class="mb-3">
                            <label class="block text-sm font-medium">售价 / 金额 (RM)</label>
                            <input type="number" step="0.01" name="price" class="w-full border rounded p-2" required>
                        </div>
                        <div class="mb-3" id="credit_value_div" style="display:none;">
                            <label class="block text-sm font-medium text-green-600 font-bold">赠送 Credit (RM)</label>
                            <input type="number" step="0.01" name="credit_value" class="w-full border rounded p-2" placeholder="例如: 1200">
                        </div>
                        <div class="mb-3" id="stock_qty_div" style="display:none;">
                            <label class="block text-sm font-medium text-orange-600 font-bold">初始库存数量</label>
                            <input type="number" name="stock_qty" class="w-full border rounded p-2" placeholder="例如: 20">
                        </div>
                        <div class="mb-4">
                            <label class="block text-sm font-medium">耗时 (分钟，零售产品可填0)</label>
                            <input type="number" name="duration" class="w-full border rounded p-2" value="30" required>
                        </div>
                        <div class="mb-4 flex items-center gap-2">
                            <input type="checkbox" name="bookable_online" id="bookable_online_chk" checked class="w-4 h-4">
                            <label for="bookable_online_chk" class="text-sm font-medium">开放顾客线上预约（取消勾选则只能后台 POS 销售，不会出现在顾客预约页面）</label>
                        </div>
                        <button class="w-full bg-indigo-600 text-white font-bold py-2 rounded hover:bg-indigo-700">确认添加服务</button>
                    </form>
                </div>
            </div>
        </div>
        <script>
            function toggleCreditInput(sel) {
                document.getElementById('credit_value_div').style.display = (sel.value === 'Packages') ? 'block' : 'none';
                document.getElementById('stock_qty_div').style.display = (sel.value === 'Products') ? 'block' : 'none';
            }
            function toggleNewCategoryInput(sel) {
                document.getElementById('new_category_name').style.display = (sel.value === '__new__' || sel.value === '') ? 'block' : 'none';
            }
        </script>
    """), services=services, stylists=stylists, holidays=holidays, open_time=open_time, close_time=close_time, closed_wd=closed_wd, current_month=current_month_prefix, staff_performance=staff_performance, existing_categories=existing_categories, slot_interval=slot_interval, max_advance_days=max_advance_days, buffer_minutes=buffer_minutes)

@app.route("/admin/stylist/add", methods=["POST"])
@admin_required
def add_stylist():
    name = request.form.get("name")
    title = request.form.get("title")
    rank_name = request.form.get("rank_name", "")
    commission_type = request.form.get("commission_type", "percent")
    commission_value = float(request.form.get("commission_value", 0) or 0)
    with get_db() as conn:
        with conn.cursor() as cursor:
            try:
                cursor.execute("""
                    INSERT INTO stylists (name, title, rank_name, commission_type, commission_value)
                    VALUES (%s, %s, %s, %s, %s)
                """, (name, title, rank_name, commission_type, commission_value))
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
        <div class="md:col-span-2 space-y-6">
            <!-- 职级预设：总监/资深/初级等不同比例 -->
            <div class="bg-white p-6 rounded shadow">
                <h2 class="text-xl font-bold mb-1 text-indigo-600">🎖️ 职级提成预设</h2>
                <p class="text-xs text-gray-500 mb-4">设定好每个职级的默认提成比例，添加/编辑员工时选职级即可一键带入，仍可单独为某个员工微调。</p>
                <table class="w-full text-left border-collapse mb-4">
                    <thead><tr class="border-b bg-gray-50 text-sm"><th class="p-2">职级名称</th><th class="p-2">提成方式</th><th class="p-2">提成数值</th><th class="p-2">操作</th></tr></thead>
                    <tbody>
                        {% for r in ranks %}
                        <tr class="border-b">
                            <form action="/admin/ranks/update/{{ r.id }}" method="POST" class="contents">
                            <td class="p-2"><input type="text" name="name" value="{{ r.name }}" class="border rounded p-1.5 w-full font-bold" required></td>
                            <td class="p-2">
                                <select name="commission_type" class="border rounded p-1.5">
                                    <option value="percent" {% if r.commission_type == 'percent' %}selected{% endif %}>按百分比 (%)</option>
                                    <option value="fixed" {% if r.commission_type == 'fixed' %}selected{% endif %}>固定金额 (RM)</option>
                                </select>
                            </td>
                            <td class="p-2"><input type="number" step="0.01" name="commission_value" value="{{ r.commission_value }}" class="border rounded p-1.5 w-24"></td>
                            <td class="p-2 flex gap-2 items-center">
                                <button class="bg-indigo-600 text-white px-3 py-1 rounded text-xs font-bold hover:bg-indigo-700">保存</button>
                            </form>
                                <a href="/admin/ranks/delete/{{ r.id }}" onclick="return confirm('确定删除该职级吗？已使用该职级的员工不会被删除，只是失去预设标签。')" class="text-red-500 text-xs font-bold">删除</a>
                            </td>
                        </tr>
                        {% else %}
                        <tr><td colspan="4" class="p-3 text-gray-400">暂无职级预设</td></tr>
                        {% endfor %}
                    </tbody>
                </table>
                <form action="/admin/ranks/add" method="POST" class="flex gap-2 items-end">
                    <div class="flex-grow">
                        <label class="block text-xs font-medium text-gray-600">新职级名称</label>
                        <input type="text" name="name" class="border rounded p-2 w-full text-sm" required placeholder="如: 总监">
                    </div>
                    <select name="commission_type" class="border rounded p-2 text-sm">
                        <option value="percent">按百分比 (%)</option>
                        <option value="fixed">固定金额 (RM)</option>
                    </select>
                    <input type="number" step="0.01" name="commission_value" class="border rounded p-2 text-sm w-24" value="0" placeholder="数值">
                    <button class="bg-indigo-600 text-white px-4 py-2 rounded text-sm font-bold hover:bg-indigo-700">添加职级</button>
                </form>
            </div>

            <div class="bg-white p-6 rounded shadow">
                <h2 class="text-xl font-bold mb-4 text-indigo-600">👥 员工列表</h2>
                <table class="w-full text-left border-collapse">
                    <thead>
                        <tr class="border-b bg-gray-50 text-sm">
                            <th class="p-2">姓名</th>
                            <th class="p-2">职级/头衔</th>
                            <th class="p-2">所属职级预设</th>
                            <th class="p-2">提成方式</th>
                            <th class="p-2">提成数值</th>
                            <th class="p-2">操作</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for st in stylists %}
                        <tr class="border-b" id="row-{{ st.id }}">
                            <form action="/admin/staff/update/{{ st.id }}" method="POST" class="contents" onsubmit="return true;">
                            <td class="p-2">
                                <input type="text" name="name" value="{{ st.name }}" class="border rounded p-1.5 w-full font-bold text-indigo-600" required>
                            </td>
                            <td class="p-2">
                                <input type="text" name="title" value="{{ st.title }}" class="border rounded p-1.5 w-full" required>
                            </td>
                            <td class="p-2">
                                <select name="rank_name" class="border rounded p-1.5" onchange="applyRankPreset(this)">
                                    <option value="">-- 不挂职级 --</option>
                                    {% for r in ranks %}
                                    <option value="{{ r.name }}" data-type="{{ r.commission_type }}" data-value="{{ r.commission_value }}" {% if st.rank_name == r.name %}selected{% endif %}>{{ r.name }}</option>
                                    {% endfor %}
                                </select>
                            </td>
                            <td class="p-2">
                                <select name="commission_type" class="border rounded p-1.5 commission-type-select">
                                    <option value="percent" {% if st.commission_type == 'percent' %}selected{% endif %}>按百分比 (%)</option>
                                    <option value="fixed" {% if st.commission_type == 'fixed' %}selected{% endif %}>固定金额 (RM)</option>
                                </select>
                            </td>
                            <td class="p-2">
                                <input type="number" step="0.01" name="commission_value" value="{{ st.commission_value }}" class="border rounded p-1.5 w-24 commission-value-input">
                            </td>
                            <td class="p-2 flex gap-2 items-center">
                                <button class="bg-indigo-600 text-white px-3 py-1 rounded text-xs font-bold hover:bg-indigo-700">保存</button>
                            </form>
                                <a href="/admin/stylist/delete/{{ st.id }}" onclick="return confirm('确定要删除该员工吗？')" class="text-red-500 text-xs font-bold">删除</a>
                            </td>
                        </tr>
                        {% else %}
                        <tr><td colspan="6" class="p-3 text-gray-400">暂无员工，请在右侧添加</td></tr>
                        {% endfor %}
                    </tbody>
                </table>
                <p class="text-xs text-gray-500 mt-3">💡 选好「所属职级预设」会自动带入该职级的提成设置，你也可以在后面两栏手动改成这个员工专属的数值。POS 收银时会按这里最终保存的数值计算建议佣金。</p>
            </div>
        </div>

        <div class="bg-white p-6 rounded shadow h-fit">
            <h2 class="text-xl font-bold mb-4">添加新员工</h2>
            <form action="/admin/stylist/add" method="POST">
                <div class="mb-3">
                    <label class="block text-sm font-medium">姓名</label>
                    <input type="text" name="name" class="w-full border rounded p-2" required placeholder="如: Kevin">
                </div>
                <div class="mb-3">
                    <label class="block text-sm font-medium">职级 / 简介（自由文字）</label>
                    <input type="text" name="title" class="w-full border rounded p-2" required placeholder="如: 高级造型师">
                </div>
                <div class="mb-3">
                    <label class="block text-sm font-medium">所属职级预设（可选，自动带入提成）</label>
                    <select name="rank_name" class="w-full border rounded p-2" onchange="applyRankPresetAdd(this)">
                        <option value="">-- 不挂职级，手动设置 --</option>
                        {% for r in ranks %}
                        <option value="{{ r.name }}" data-type="{{ r.commission_type }}" data-value="{{ r.commission_value }}">{{ r.name }}</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="mb-3">
                    <label class="block text-sm font-medium">提成方式</label>
                    <select name="commission_type" id="add_commission_type" class="w-full border rounded p-2">
                        <option value="percent">按百分比 (%)</option>
                        <option value="fixed">固定金额 (RM)</option>
                    </select>
                </div>
                <div class="mb-4">
                    <label class="block text-sm font-medium">提成数值</label>
                    <input type="number" step="0.01" name="commission_value" id="add_commission_value" class="w-full border rounded p-2" value="0" placeholder="例如: 20 (代表20%) 或 15 (代表RM15)">
                </div>
                <button class="w-full bg-indigo-600 text-white font-bold py-2 rounded hover:bg-indigo-700">确认添加员工</button>
            </form>
        </div>
    </div>
    <script>
        function applyRankPreset(sel) {
            const opt = sel.options[sel.selectedIndex];
            if (!opt.value) return;
            const row = sel.closest('tr');
            row.querySelector('.commission-type-select').value = opt.getAttribute('data-type');
            row.querySelector('.commission-value-input').value = opt.getAttribute('data-value');
        }
        function applyRankPresetAdd(sel) {
            const opt = sel.options[sel.selectedIndex];
            if (!opt.value) return;
            document.getElementById('add_commission_type').value = opt.getAttribute('data-type');
            document.getElementById('add_commission_value').value = opt.getAttribute('data-value');
        }
    </script>
"""

@app.route("/admin/staff")
@admin_required
def admin_staff():
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM stylists ORDER BY id DESC")
            stylists = cursor.fetchall()
            cursor.execute("SELECT * FROM staff_ranks ORDER BY commission_value DESC")
            ranks = cursor.fetchall()
    return render_template_string(LAYOUT_TEMPLATE.replace("{% block content %}{% endblock %}", STAFF_MANAGEMENT_TEMPLATE), stylists=stylists, ranks=ranks)

@app.route("/admin/staff/update/<int:id>", methods=["POST"])
@admin_required
def admin_staff_update(id):
    name = request.form.get("name")
    title = request.form.get("title")
    rank_name = request.form.get("rank_name", "")
    commission_type = request.form.get("commission_type", "percent")
    commission_value = float(request.form.get("commission_value", 0) or 0)
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE stylists SET name = %s, title = %s, rank_name = %s, commission_type = %s, commission_value = %s
                WHERE id = %s
            """, (name, title, rank_name, commission_type, commission_value, id))
            conn.commit()
    return redirect(url_for("admin_staff"))

@app.route("/admin/ranks/add", methods=["POST"])
@admin_required
def admin_ranks_add():
    name = request.form.get("name")
    commission_type = request.form.get("commission_type", "percent")
    commission_value = float(request.form.get("commission_value", 0) or 0)
    with get_db() as conn:
        with conn.cursor() as cursor:
            try:
                cursor.execute("""
                    INSERT INTO staff_ranks (name, commission_type, commission_value) VALUES (%s, %s, %s)
                """, (name, commission_type, commission_value))
                conn.commit()
            except:
                conn.rollback()
    return redirect(url_for("admin_staff"))

@app.route("/admin/ranks/update/<int:id>", methods=["POST"])
@admin_required
def admin_ranks_update(id):
    name = request.form.get("name")
    commission_type = request.form.get("commission_type", "percent")
    commission_value = float(request.form.get("commission_value", 0) or 0)
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT name FROM staff_ranks WHERE id = %s", (id,))
            old = cursor.fetchone()
            cursor.execute("""
                UPDATE staff_ranks SET name = %s, commission_type = %s, commission_value = %s WHERE id = %s
            """, (name, commission_type, commission_value, id))
            # 同步更新已经挂了这个职级名字的员工的显示名（提成数值本身不会被联动覆盖，员工需要重新保存才会应用新数值）
            if old and old["name"] != name:
                cursor.execute("UPDATE stylists SET rank_name = %s WHERE rank_name = %s", (name, old["name"]))
            conn.commit()
    return redirect(url_for("admin_staff"))

@app.route("/admin/ranks/delete/<int:id>")
@admin_required
def admin_ranks_delete(id):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM staff_ranks WHERE id = %s", (id,))
            conn.commit()
    return redirect(url_for("admin_staff"))

@app.route("/admin/settings/update", methods=["POST"])
@admin_required
def update_settings():
    open_time = request.form.get("open_time", "10:00")
    close_time = request.form.get("close_time", "20:00")
    closed_weekdays = request.form.get("closed_weekdays", "1")
    try:
        slot_interval_minutes = str(max(5, int(request.form.get("slot_interval_minutes", "30") or 30)))
    except (ValueError, TypeError):
        slot_interval_minutes = "30"
    max_advance_days = request.form.get("max_advance_days", "30") or "30"
    buffer_minutes = request.form.get("buffer_minutes", "0") or "0"
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO settings (key, value) VALUES ('open_time', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (open_time,))
            cursor.execute("INSERT INTO settings (key, value) VALUES ('close_time', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (close_time,))
            cursor.execute("INSERT INTO settings (key, value) VALUES ('closed_weekdays', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (closed_weekdays,))
            cursor.execute("INSERT INTO settings (key, value) VALUES ('slot_interval_minutes', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (slot_interval_minutes,))
            cursor.execute("INSERT INTO settings (key, value) VALUES ('max_advance_days', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (max_advance_days,))
            cursor.execute("INSERT INTO settings (key, value) VALUES ('buffer_minutes', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (buffer_minutes,))
            conn.commit()
    return redirect(url_for("admin_settings"))

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
    return redirect(url_for("admin_settings"))

@app.route("/admin/holiday/delete/<int:id>")
@admin_required
def delete_holiday(id):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM holidays WHERE id = %s", (id,))
            conn.commit()
    return redirect(url_for("admin_settings"))

@app.route("/admin/categories/add", methods=["POST"])
@admin_required
def admin_categories_add():
    name = (request.form.get("name") or "").strip()
    with get_db() as conn:
        with conn.cursor() as cursor:
            if name:
                try:
                    cursor.execute("INSERT INTO service_categories (name) VALUES (%s)", (name,))
                    conn.commit()
                except:
                    conn.rollback()
    return redirect(url_for("admin_settings"))

@app.route("/admin/categories/update/<int:id>", methods=["POST"])
@admin_required
def admin_categories_update(id):
    new_name = (request.form.get("name") or "").strip()
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT name FROM service_categories WHERE id = %s", (id,))
            old = cursor.fetchone()
            if old and new_name:
                cursor.execute("UPDATE service_categories SET name = %s WHERE id = %s", (new_name, id))
                # 同步更新所有用到这个旧分类名的服务
                cursor.execute("UPDATE services SET sub_category = %s WHERE sub_category = %s", (new_name, old["name"]))
                conn.commit()
    return redirect(url_for("admin_settings"))

@app.route("/admin/categories/delete/<int:id>")
@admin_required
def admin_categories_delete(id):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM service_categories WHERE id = %s", (id,))
            conn.commit()
    return redirect(url_for("admin_settings"))

def send_telegram_notification(message):
    """通过 Telegram Bot 发送新预约通知，没配置 token/chat_id 时静默跳过"""
    bot_token = get_setting("telegram_bot_token", "")
    chat_id = get_setting("telegram_chat_id", "")
    if not bot_token or not chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode()
        req = urllib.request.Request(url, data=data)
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception:
        return False

def send_email_notification(subject, message):
    """通过 Gmail SMTP 发送新预约通知邮件，没配置邮箱信息时静默跳过"""
    gmail_address = get_setting("notify_gmail_address", "")
    gmail_app_password = get_setting("notify_gmail_app_password", "")
    to_email = get_setting("notify_to_email", "") or gmail_address
    if not gmail_address or not gmail_app_password or not to_email:
        return False
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(message, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = gmail_address
        msg["To"] = to_email
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=8) as server:
            server.login(gmail_address, gmail_app_password)
            server.sendmail(gmail_address, [to_email], msg.as_string())
        return True
    except Exception:
        return False

def notify_new_booking(message):
    """新预约通知：Telegram 和邮件哪个配置了就发哪个，两个都配置了就都发"""
    sent_telegram = send_telegram_notification(message)
    sent_email = send_email_notification("🔔 Dew Hair Salon - New Booking", message)
    return sent_telegram or sent_email

@app.route("/admin/notifications/update", methods=["POST"])
@admin_required
def admin_notifications_update():
    bot_token = request.form.get("telegram_bot_token", "").strip()
    chat_id = request.form.get("telegram_chat_id", "").strip()
    gmail_address = request.form.get("notify_gmail_address", "").strip()
    gmail_app_password = request.form.get("notify_gmail_app_password", "").strip()
    to_email = request.form.get("notify_to_email", "").strip()
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO settings (key, value) VALUES ('telegram_bot_token', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (bot_token,))
            cursor.execute("INSERT INTO settings (key, value) VALUES ('telegram_chat_id', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (chat_id,))
            cursor.execute("INSERT INTO settings (key, value) VALUES ('notify_gmail_address', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (gmail_address,))
            cursor.execute("INSERT INTO settings (key, value) VALUES ('notify_gmail_app_password', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (gmail_app_password,))
            cursor.execute("INSERT INTO settings (key, value) VALUES ('notify_to_email', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (to_email,))
            conn.commit()
    return redirect(url_for("admin_settings"))

@app.route("/admin/notifications/test")
@admin_required
def admin_notifications_test():
    ok = notify_new_booking("🔔 Dew Hair Salon: 这是一条测试通知，如果你收到这条消息，说明通知设置成功了！")
    return redirect(url_for("admin_settings", test_result="ok" if ok else "fail"))

@app.route("/admin/set-lang/<lang>")
@admin_required
def admin_set_lang(lang):
    next_url = request.args.get("next") or url_for("admin_dashboard")
    resp = redirect(next_url)
    resp.set_cookie("admin_lang", "en" if lang == "en" else "zh", max_age=60*60*24*365)
    return resp

SETTINGS_TEMPLATE = """
    <div class="space-y-6">
        {% if test_result == 'ok' %}
        <div class="p-3 bg-green-50 border border-green-200 text-green-700 rounded-lg text-sm font-bold">✅ 测试通知已发送，请检查你的 Telegram</div>
        {% elif test_result == 'fail' %}
        <div class="p-3 bg-red-50 border border-red-200 text-red-700 rounded-lg text-sm font-bold">⚠️ 发送失败，请检查 Bot Token 和 Chat ID 是否正确</div>
        {% endif %}

        <!-- 营业时间与预约规则 -->
        <div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-100">
            <h2 class="text-lg font-bold text-gray-900 mb-4">🕐 营业时间与预约规则</h2>
            <form action="/admin/settings/update" method="POST" class="grid grid-cols-2 md:grid-cols-4 gap-4 items-end">
                <div>
                    <label class="block text-xs font-semibold text-gray-500 mb-1">开门时间</label>
                    <input type="time" name="open_time" value="{{ open_time }}" class="w-full border border-gray-200 rounded-lg p-2 text-sm" required>
                </div>
                <div>
                    <label class="block text-xs font-semibold text-gray-500 mb-1">关门时间</label>
                    <input type="time" name="close_time" value="{{ close_time }}" class="w-full border border-gray-200 rounded-lg p-2 text-sm" required>
                </div>
                <div>
                    <label class="block text-xs font-semibold text-gray-500 mb-1">每周固定休息日</label>
                    <select name="closed_weekdays" class="w-full border border-gray-200 rounded-lg p-2 text-sm">
                        <option value="0" {% if closed_wd == '0' %}selected{% endif %}>周日休息</option>
                        <option value="1" {% if closed_wd == '1' %}selected{% endif %}>周一休息</option>
                        <option value="2" {% if closed_wd == '2' %}selected{% endif %}>周二休息</option>
                        <option value="-1" {% if closed_wd == '-1' %}selected{% endif %}>无固定休息日</option>
                    </select>
                </div>
                <div>
                    <label class="block text-xs font-semibold text-gray-500 mb-1">预约时段间隔</label>
                    <select name="slot_interval_minutes" class="w-full border border-gray-200 rounded-lg p-2 text-sm">
                        <option value="15" {% if slot_interval == '15' %}selected{% endif %}>每 15 分钟</option>
                        <option value="30" {% if slot_interval == '30' %}selected{% endif %}>每 30 分钟</option>
                        <option value="45" {% if slot_interval == '45' %}selected{% endif %}>每 45 分钟</option>
                        <option value="60" {% if slot_interval == '60' %}selected{% endif %}>每 60 分钟</option>
                    </select>
                </div>
                <div>
                    <label class="block text-xs font-semibold text-gray-500 mb-1">顾客最多可提前预约 (天)</label>
                    <input type="number" name="max_advance_days" value="{{ max_advance_days }}" min="1" class="w-full border border-gray-200 rounded-lg p-2 text-sm">
                </div>
                <div>
                    <label class="block text-xs font-semibold text-gray-500 mb-1">同发型师预约缓冲 (分钟)</label>
                    <input type="number" name="buffer_minutes" value="{{ buffer_minutes }}" min="0" class="w-full border border-gray-200 rounded-lg p-2 text-sm">
                </div>
                <div>
                    <button class="w-full bg-indigo-600 text-white font-bold py-2 rounded-lg hover:bg-indigo-700 text-sm">保存</button>
                </div>
            </form>

            <hr class="my-5 border-gray-100">
            <h3 class="font-bold text-sm text-gray-800 mb-2">临时休息日</h3>
            <form action="/admin/holiday/add" method="POST" class="flex gap-2 mb-3">
                <input type="date" name="date_str" class="border border-gray-200 rounded-lg p-2 text-sm" required>
                <input type="text" name="reason" placeholder="休息原因 (如: 公共假期)" class="border border-gray-200 rounded-lg p-2 text-sm flex-grow">
                <button class="bg-red-500 text-white px-4 py-2 rounded-lg font-bold text-sm hover:bg-red-600">添加闭店日</button>
            </form>
            <div class="flex flex-wrap gap-2">
                {% for h in holidays %}
                <span class="bg-red-50 text-red-700 px-3 py-1 rounded-full border border-red-200 text-xs flex items-center gap-2">
                    {{ h.date_str }} ({{ h.reason }})
                    <a href="/admin/holiday/delete/{{ h.id }}" class="font-bold hover:text-red-900">×</a>
                </span>
                {% else %}
                <span class="text-gray-400 text-xs">暂无临时闭店日</span>
                {% endfor %}
            </div>
        </div>

        <!-- 服务分类管理 -->
        <div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-100">
            <h2 class="text-lg font-bold text-gray-900 mb-1">🏷️ 服务分类管理</h2>
            <p class="text-xs text-gray-500 mb-4">这里的分类会出现在 POS 收银台的分类标签页，也是添加服务时可以选的分类标签</p>
            <div class="flex flex-wrap gap-2 mb-4">
                {% for c in categories %}
                <div class="flex items-center gap-1 bg-gray-50 border border-gray-100 rounded-full pl-3 pr-1 py-1">
                    <form action="/admin/categories/update/{{ c.id }}" method="POST" class="flex items-center">
                        <input type="text" name="name" value="{{ c.name }}" class="bg-transparent text-sm font-semibold w-20 focus:outline-none" onchange="this.form.requestSubmit()">
                    </form>
                    <a href="/admin/categories/delete/{{ c.id }}" onclick="return confirm('删除分类 {{ c.name }}？已使用该分类的服务不会被删除，只是分类标签会变回类型默认值。')" class="text-red-400 hover:text-red-600 font-bold text-xs px-1">×</a>
                </div>
                {% else %}
                <span class="text-gray-400 text-sm">暂无分类，先在下面添加一个</span>
                {% endfor %}
            </div>
            <form action="/admin/categories/add" method="POST" class="flex gap-2">
                <input type="text" name="name" placeholder="新分类名称，如: 美甲" class="border border-gray-200 rounded-lg p-2 text-sm flex-grow" required>
                <button class="bg-indigo-600 text-white px-4 py-2 rounded-lg font-bold text-sm hover:bg-indigo-700">添加分类</button>
            </form>
        </div>

        <!-- 新预约通知 -->
        <div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-100">
            <h2 class="text-lg font-bold text-gray-900 mb-1">🔔 新预约通知</h2>
            <p class="text-xs text-gray-500 mb-4">配置好 Telegram 或邮箱（可以只用一种，也可以两个都开），每次有顾客在线上预约成功，你会立刻收到通知。都是免费的，不需要额外付费服务。</p>
            <form action="/admin/notifications/update" method="POST" class="space-y-4 mb-3">
                <div>
                    <h3 class="text-sm font-bold text-gray-700 mb-2">方式一：Telegram（推荐，最快）</h3>
                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <div>
                            <label class="block text-xs font-semibold text-gray-500 mb-1">Telegram Bot Token</label>
                            <input type="text" name="telegram_bot_token" value="{{ telegram_bot_token }}" placeholder="123456:ABC-DEF..." class="w-full border border-gray-200 rounded-lg p-2 text-sm">
                        </div>
                        <div>
                            <label class="block text-xs font-semibold text-gray-500 mb-1">Telegram Chat ID</label>
                            <input type="text" name="telegram_chat_id" value="{{ telegram_chat_id }}" placeholder="例如: 123456789" class="w-full border border-gray-200 rounded-lg p-2 text-sm">
                        </div>
                    </div>
                </div>
                <div>
                    <h3 class="text-sm font-bold text-gray-700 mb-2">方式二：邮箱通知（用你自己的 Gmail 发送）</h3>
                    <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
                        <div>
                            <label class="block text-xs font-semibold text-gray-500 mb-1">发送用 Gmail 邮箱</label>
                            <input type="email" name="notify_gmail_address" value="{{ notify_gmail_address }}" placeholder="yourshop@gmail.com" class="w-full border border-gray-200 rounded-lg p-2 text-sm">
                        </div>
                        <div>
                            <label class="block text-xs font-semibold text-gray-500 mb-1">Gmail 应用专用密码</label>
                            <input type="text" name="notify_gmail_app_password" value="{{ notify_gmail_app_password }}" placeholder="16 位应用密码" class="w-full border border-gray-200 rounded-lg p-2 text-sm">
                        </div>
                        <div>
                            <label class="block text-xs font-semibold text-gray-500 mb-1">接收通知的邮箱 (留空则发到上面那个)</label>
                            <input type="email" name="notify_to_email" value="{{ notify_to_email }}" placeholder="me@example.com" class="w-full border border-gray-200 rounded-lg p-2 text-sm">
                        </div>
                    </div>
                </div>
                <div class="flex gap-2">
                    <button class="bg-indigo-600 text-white px-4 py-2 rounded-lg font-bold text-sm hover:bg-indigo-700">保存</button>
                    <a href="/admin/notifications/test" class="bg-gray-100 text-gray-700 px-4 py-2 rounded-lg font-bold text-sm hover:bg-gray-200">发送测试通知</a>
                </div>
            </form>
            <details class="text-xs text-gray-500 mb-2">
                <summary class="cursor-pointer font-semibold text-indigo-600">还没有 Telegram Bot Token？点这里看怎么获取（3 分钟搞定）</summary>
                <ol class="list-decimal list-inside mt-2 space-y-1">
                    <li>在 Telegram 搜索 <b>@BotFather</b>，点 Start，发送 <code>/newbot</code>，按提示取个名字，完成后会给你一个 Token（一长串数字和字母），复制填到上面</li>
                    <li>再搜索 <b>@userinfobot</b>，点 Start，它会回复你的 Chat ID（一串数字），复制填到上面</li>
                    <li>保存后点"发送测试通知"，Telegram 上找到你刚创建的机器人，应该会收到一条测试消息</li>
                </ol>
            </details>
            <details class="text-xs text-gray-500">
                <summary class="cursor-pointer font-semibold text-indigo-600">还没有 Gmail 应用专用密码？点这里看怎么获取</summary>
                <ol class="list-decimal list-inside mt-2 space-y-1">
                    <li>用你的 Gmail 账号登录 <b>myaccount.google.com/security</b>，先确认已经开启"两步验证"（没开的话先开，不然拿不到应用密码）</li>
                    <li>搜索 "应用专用密码" (App Passwords) 这个设置项，点进去创建一个新的，名字随便填（比如 "Salon"）</li>
                    <li>会生成一个 16 位的密码（没有空格那种形式），复制填到上面的"Gmail 应用专用密码"栏——注意不是你平时登录 Gmail 的密码</li>
                </ol>
            </details>
        </div>

        <!-- 语言 -->
        <div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-100">
            <h2 class="text-lg font-bold text-gray-900 mb-1">🌐 语言</h2>
            <p class="text-xs text-gray-500 mb-3">顾客预约页面固定为英文；后台导航栏和这个设置页面支持中英文切换，其余内部页面暂时仍是中文。</p>
            <div class="flex gap-2">
                <a href="/admin/set-lang/zh?next={{ request.path }}" class="px-4 py-2 rounded-lg text-sm font-bold {% if admin_lang == 'zh' %}bg-indigo-600 text-white{% else %}bg-gray-100 text-gray-600{% endif %}">中文</a>
                <a href="/admin/set-lang/en?next={{ request.path }}" class="px-4 py-2 rounded-lg text-sm font-bold {% if admin_lang == 'en' %}bg-indigo-600 text-white{% else %}bg-gray-100 text-gray-600{% endif %}">English</a>
            </div>
        </div>
    </div>
"""

@app.route("/admin/settings")
@admin_required
def admin_settings():
    open_time = get_setting("open_time", "10:00")
    close_time = get_setting("close_time", "20:00")
    closed_wd = get_setting("closed_weekdays", "1")
    slot_interval = get_setting("slot_interval_minutes", "30")
    max_advance_days = get_setting("max_advance_days", "30")
    buffer_minutes = get_setting("buffer_minutes", "0")
    telegram_bot_token = get_setting("telegram_bot_token", "")
    telegram_chat_id = get_setting("telegram_chat_id", "")
    notify_gmail_address = get_setting("notify_gmail_address", "")
    notify_gmail_app_password = get_setting("notify_gmail_app_password", "")
    notify_to_email = get_setting("notify_to_email", "")
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM holidays ORDER BY date_str DESC")
            holidays = cursor.fetchall()
            sync_service_categories(cursor)
            cursor.execute("SELECT * FROM service_categories ORDER BY name")
            categories = cursor.fetchall()
            conn.commit()
    return render_template_string(
        LAYOUT_TEMPLATE.replace("{% block content %}{% endblock %}", SETTINGS_TEMPLATE),
        open_time=open_time, close_time=close_time, closed_wd=closed_wd,
        slot_interval=slot_interval, max_advance_days=max_advance_days, buffer_minutes=buffer_minutes,
        holidays=holidays, categories=categories,
        telegram_bot_token=telegram_bot_token, telegram_chat_id=telegram_chat_id,
        notify_gmail_address=notify_gmail_address, notify_gmail_app_password=notify_gmail_app_password, notify_to_email=notify_to_email,
        test_result=request.args.get("test_result"),
        admin_lang=request.cookies.get("admin_lang", "zh"),
    )

@app.route("/admin/service/add", methods=["POST"])
@admin_required
def add_service():
    name = request.form.get("name")
    cat = request.form.get("category_type")
    sub_category = (request.form.get("sub_category") or "").strip() or cat
    if sub_category == "__new__" or not sub_category:
        sub_category = (request.form.get("new_category_name") or "").strip() or cat
    price = float(request.form.get("price", 0))
    duration = int(request.form.get("duration", 30))
    credit_value = float(request.form.get("credit_value", 0)) if cat == 'Packages' else 0.0
    stock_qty = int(request.form.get("stock_qty") or 0) if cat == 'Products' else None
    bookable_online = request.form.get("bookable_online") == "on"
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO services (name, category_type, sub_category, price, duration, credit_value, stock_qty, bookable_online) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (name, cat, sub_category, price, duration, credit_value, stock_qty, bookable_online))
            if sub_category and sub_category not in ("Services", "Packages", "Products"):
                cursor.execute("INSERT INTO service_categories (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (sub_category,))
            conn.commit()
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/service/toggle_online/<int:id>")
@admin_required
def admin_service_toggle_online(id):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE services SET bookable_online = NOT COALESCE(bookable_online, TRUE) WHERE id = %s", (id,))
            conn.commit()
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/service/restock/<int:id>", methods=["POST"])
@admin_required
def admin_service_restock(id):
    delta = int(request.form.get("delta", 0) or 0)
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT stock_qty FROM services WHERE id = %s", (id,))
            row = cursor.fetchone()
            current = row["stock_qty"] if row and row["stock_qty"] is not None else 0
            new_qty = max(0, current + delta)
            cursor.execute("UPDATE services SET stock_qty = %s WHERE id = %s", (new_qty, id))
            conn.commit()
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/service/delete/<int:id>")
@admin_required
def delete_service(id):
    with get_db() as conn:
        with conn.cursor() as cursor:
            try:
                cursor.execute("DELETE FROM services WHERE id = %s", (id,))
                conn.commit()
            except Exception:
                # 这个服务已经有历史订单或预约引用了，真删会破坏历史记录，改成下架（不再显示/不能再选）而不是硬删
                conn.rollback()
                cursor.execute("UPDATE services SET is_active = FALSE, bookable_online = FALSE WHERE id = %s", (id,))
                conn.commit()
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/service/update/<int:id>", methods=["POST"])
@admin_required
def admin_service_update(id):
    name = request.form.get("name")
    cat = request.form.get("category_type")
    sub_category = (request.form.get("sub_category") or "").strip() or cat
    price = float(request.form.get("price", 0) or 0)
    duration = int(request.form.get("duration", 30) or 30)
    credit_value = float(request.form.get("credit_value", 0) or 0) if cat == 'Packages' else 0.0
    bookable_online = request.form.get("bookable_online") == "on"
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE services SET name = %s, category_type = %s, sub_category = %s, price = %s,
                       duration = %s, credit_value = %s, bookable_online = %s
                WHERE id = %s
            """, (name, cat, sub_category, price, duration, credit_value, bookable_online, id))
            if sub_category and sub_category not in ("Services", "Packages", "Products"):
                cursor.execute("INSERT INTO service_categories (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (sub_category,))
            conn.commit()
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/service/reactivate/<int:id>")
@admin_required
def admin_service_reactivate(id):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE services SET is_active = TRUE WHERE id = %s", (id,))
            conn.commit()
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/customers")
@admin_required
def admin_customers():
    search_query = request.args.get("q", "").strip()
    selected_id = request.args.get("id", type=int)
    with get_db() as conn:
        with conn.cursor() as cursor:
            if search_query:
                cursor.execute("""
                    SELECT c.*, 
                           (SELECT COUNT(*) FROM appointments WHERE customer_id = c.id) as app_count,
                           (SELECT COUNT(*) FROM orders WHERE customer_id = c.id AND status = 'NORMAL') as order_count,
                           (SELECT MAX(created_at) FROM orders WHERE customer_id = c.id AND status = 'NORMAL') as last_order_at
                    FROM customers c 
                    WHERE c.name ILIKE %s OR c.phone ILIKE %s
                    ORDER BY c.id DESC
                """, (f"%{search_query}%", f"%{search_query}%"))
            else:
                cursor.execute("""
                    SELECT c.*, 
                           (SELECT COUNT(*) FROM appointments WHERE customer_id = c.id) as app_count,
                           (SELECT COUNT(*) FROM orders WHERE customer_id = c.id AND status = 'NORMAL') as order_count,
                           (SELECT MAX(created_at) FROM orders WHERE customer_id = c.id AND status = 'NORMAL') as last_order_at
                    FROM customers c 
                    ORDER BY c.id DESC
                """)
            customers = cursor.fetchall()

            if not selected_id and customers:
                selected_id = customers[0]["id"]

            selected_customer = None
            appointments = []
            orders = []
            if selected_id:
                cursor.execute("SELECT * FROM customers WHERE id = %s", (selected_id,))
                selected_customer = cursor.fetchone()
                if selected_customer:
                    cursor.execute("""
                        SELECT a.*, s.name as service_name FROM appointments a
                        JOIN services s ON a.service_id = s.id
                        WHERE a.customer_id = %s
                        ORDER BY a.start_time DESC LIMIT 10
                    """, (selected_id,))
                    appointments = cursor.fetchall()
                    cursor.execute("""
                        SELECT o.*, i.item_name, i.price FROM orders o 
                        LEFT JOIN order_items i ON o.id = i.order_id 
                        WHERE o.customer_id = %s 
                        ORDER BY o.created_at DESC LIMIT 15
                    """, (selected_id,))
                    orders = cursor.fetchall()

    return render_template_string(LAYOUT_TEMPLATE.replace("{% block content %}{% endblock %}", """
        <div class="flex flex-col md:flex-row gap-4" style="height: calc(100vh - 140px); min-height: 500px;">
            <!-- 左侧：会员列表 -->
            <div class="w-full md:w-80 flex-shrink-0 bg-white rounded-2xl shadow-sm border border-gray-100 flex flex-col overflow-hidden">
                <div class="p-4 border-b border-gray-100 space-y-2">
                    <div class="flex items-center justify-between">
                        <h2 class="font-bold text-gray-900">会员列表</h2>
                        <button onclick="document.getElementById('addMemberModal').classList.remove('hidden')" class="bg-indigo-600 text-white text-xs font-bold px-3 py-1.5 rounded-lg hover:bg-indigo-700">+ 添加</button>
                    </div>
                    <form action="/admin/customers" method="GET" class="flex gap-1">
                        <input type="text" name="q" value="{{ search_query }}" placeholder="搜姓名或手机号..." class="border border-gray-200 rounded-lg px-2.5 py-1.5 text-sm flex-grow">
                        {% if search_query %}<a href="/admin/customers" class="text-xs text-gray-400 font-bold px-2 flex items-center">×</a>{% endif %}
                    </form>
                </div>
                <div class="overflow-y-auto flex-grow">
                    {% for c in customers %}
                    <a href="/admin/customers?id={{ c.id }}{% if search_query %}&q={{ search_query }}{% endif %}" class="block px-4 py-3 border-b border-gray-50 hover:bg-gray-50 {% if selected_customer and c.id == selected_customer.id %}bg-indigo-50 border-l-4 border-l-indigo-600{% endif %}">
                        <div class="flex justify-between items-start">
                            <div class="min-w-0">
                                <div class="font-semibold text-gray-900 text-sm truncate">{{ c.name }}</div>
                                <div class="text-xs text-gray-400">{{ c.phone }}</div>
                            </div>
                            <div class="text-right flex-shrink-0 ml-2">
                                <div class="text-xs font-bold text-green-600">RM {{ "%.0f"|format(c.credits) }}</div>
                                <div class="text-[10px] text-gray-400">{{ c.last_order_at.split(' ')[0] if c.last_order_at else '-' }}</div>
                            </div>
                        </div>
                    </a>
                    {% else %}
                    <div class="p-6 text-center text-gray-400 text-sm">没有找到相关会员</div>
                    {% endfor %}
                </div>
            </div>

            <!-- 右侧：会员详情 -->
            <div class="flex-grow bg-white rounded-2xl shadow-sm border border-gray-100 overflow-y-auto">
                {% if selected_customer %}
                <div class="p-6 space-y-6">
                    <div class="flex flex-col sm:flex-row justify-between sm:items-center gap-3 border-b border-gray-100 pb-4">
                        <div>
                            <h2 class="text-xl font-extrabold text-gray-900">{{ selected_customer.name }}</h2>
                            <p class="text-sm text-gray-400">{{ selected_customer.phone }} · 专属链接码: {{ selected_customer.token }}</p>
                            <a href="/customer/{{ selected_customer.token }}" target="_blank" class="text-indigo-600 text-xs font-bold hover:underline">查看顾客专属页面 →</a>
                        </div>
                        <div class="text-right">
                            <div class="text-xs text-gray-400">Credit 余额</div>
                            <div class="text-2xl font-extrabold text-green-600">RM {{ "%.2f"|format(selected_customer.credits) }}</div>
                        </div>
                    </div>

                    <div class="grid grid-cols-2 gap-3">
                        <div class="bg-gray-50 rounded-xl p-3 text-center">
                            <div class="text-lg font-extrabold text-gray-900">{{ appointments|length }}</div>
                            <div class="text-xs text-gray-400">近期预约</div>
                        </div>
                        <div class="bg-gray-50 rounded-xl p-3 text-center">
                            <div class="text-lg font-extrabold text-gray-900">{{ orders|selectattr('status', 'equalto', 'NORMAL')|list|length }}</div>
                            <div class="text-xs text-gray-400">近期销售笔数</div>
                        </div>
                    </div>

                    <div>
                        <h3 class="font-bold text-gray-800 text-sm mb-2">📅 近期预约</h3>
                        <div class="space-y-2">
                            {% for a in appointments %}
                            <div class="flex justify-between items-center bg-gray-50 rounded-lg p-3 text-sm">
                                <div>
                                    <div class="font-semibold text-indigo-600">{{ a.service_name }}</div>
                                    <div class="text-xs text-gray-500">{{ a.start_time }} ~ {{ a.end_time.split()[1] }} · {{ a.stylist }}</div>
                                </div>
                                <span class="text-[11px] font-bold px-2 py-1 rounded-full {% if a.status == 'CONFIRMED' %}bg-green-100 text-green-700{% elif a.status == 'CANCELLED' %}bg-red-100 text-red-600{% else %}bg-gray-100 text-gray-500{% endif %}">{{ a.status }}</span>
                            </div>
                            {% else %}
                            <p class="text-gray-400 text-sm">暂无预约记录</p>
                            {% endfor %}
                        </div>
                    </div>

                    <div>
                        <h3 class="font-bold text-gray-800 text-sm mb-2">🧾 近期销售 / 消费记录</h3>
                        <table class="w-full text-left border-collapse text-sm">
                            <thead><tr class="border-b border-gray-100 text-gray-400 text-xs"><th class="p-2">单号</th><th class="p-2">时间</th><th class="p-2">项目</th><th class="p-2">金额</th><th class="p-2">支付方式</th></tr></thead>
                            <tbody>
                                {% for o in orders %}
                                <tr class="border-b border-gray-50 {% if o.status == 'VOID' %}bg-red-50 text-gray-400 line-through{% endif %}">
                                    <td class="p-2 font-semibold">{{ o.order_no }}</td>
                                    <td class="p-2">{{ o.created_at }}</td>
                                    <td class="p-2">{{ o.item_name }}</td>
                                    <td class="p-2 font-semibold">RM {{ "%.2f"|format(o.price) }}</td>
                                    <td class="p-2">{{ '已作废' if o.status == 'VOID' else o.payment_details }}</td>
                                </tr>
                                {% else %}
                                <tr><td colspan="5" class="p-3 text-gray-400">暂无消费订单</td></tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>
                {% else %}
                <div class="h-full flex items-center justify-center text-gray-400 text-sm">从左侧选择一位会员查看详情，或先添加一位新会员</div>
                {% endif %}
            </div>
        </div>

        <!-- 添加会员弹窗 -->
        <div id="addMemberModal" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 hidden">
            <div class="bg-white p-6 rounded-2xl shadow-xl max-w-sm w-full">
                <div class="flex justify-between items-center mb-4">
                    <h3 class="text-lg font-bold text-gray-900">手动添加会员档案</h3>
                    <button onclick="document.getElementById('addMemberModal').classList.add('hidden')" class="text-gray-400 font-bold text-xl">&times;</button>
                </div>
                <form action="/admin/customer/add" method="POST">
                    <div class="mb-3">
                        <label class="block text-xs font-semibold text-gray-500 mb-1">会员姓名</label>
                        <input type="text" name="name" class="w-full border border-gray-200 rounded-lg p-2 text-sm" required>
                    </div>
                    <div class="mb-3">
                        <label class="block text-xs font-semibold text-gray-500 mb-1">电话号码 (唯一凭证)</label>
                        <input type="text" name="phone" class="w-full border border-gray-200 rounded-lg p-2 text-sm" required>
                    </div>
                    <div class="mb-4">
                        <label class="block text-xs font-semibold text-gray-500 mb-1">初始赠送 Credit (RM)</label>
                        <input type="number" step="0.01" name="credits" class="w-full border border-gray-200 rounded-lg p-2 text-sm" value="0.00">
                    </div>
                    <button class="w-full bg-indigo-600 text-white font-bold py-2.5 rounded-lg hover:bg-indigo-700 text-sm">保存会员</button>
                </form>
            </div>
        </div>
    """), customers=customers, search_query=search_query, selected_customer=selected_customer, appointments=appointments, orders=orders)

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
    # 旧的会员详情单独页面已经合并进 /admin/customers 的左右分栏视图，这里做个跳转保持旧链接可用
    return redirect(url_for("admin_customers", id=id))

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
                <a href="/admin/payroll" class="hover:bg-indigo-700 px-2 py-1 rounded">薪资报表</a>
                <a href="/admin/settings" class="hover:bg-indigo-700 px-2 py-1 rounded">⚙️ 设置</a>
                <a href="/" target="_blank" class="bg-green-600 px-2 py-1 rounded hover:bg-green-700">🔗 顾客预约页面</a>
                <a href="/admin/logout" class="bg-red-500 px-2 py-1 rounded hover:bg-red-600">退出</a>
            </div>
        </div>
    </nav>
    <main class="container mx-auto p-6">
        {% if error %}
        <div class="mb-4 p-3 bg-red-100 text-red-700 rounded-lg text-sm font-bold border border-red-300">⚠️ {{ error }}</div>
        {% endif %}
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

            <div class="flex items-center justify-end gap-2 mb-3">
                <button onclick="showView('timeline')" id="btn-view-timeline" class="text-xs font-bold px-3 py-1.5 rounded-lg bg-indigo-600 text-white">📅 时间轴视图</button>
                <button onclick="showView('list')" id="btn-view-list" class="text-xs font-bold px-3 py-1.5 rounded-lg bg-gray-100 text-gray-600">📋 列表视图</button>
            </div>

            <!-- 时间轴视图（默认） -->
            <div id="view-timeline">
                {% if timeline_columns %}
                <div class="overflow-x-auto border border-gray-100 rounded-xl">
                    <div class="flex" style="min-width: {{ 64 + timeline_columns|length * 200 }}px;">
                        <!-- 左侧时间刻度列 -->
                        <div class="flex-shrink-0 w-16 relative border-r border-gray-100 bg-gray-50" style="height: {{ timeline_height + 40 }}px;">
                            <div class="h-10 border-b border-gray-100"></div>
                            <div class="relative" style="height: {{ timeline_height }}px;">
                                {% for hm in hour_marks %}
                                <div class="absolute left-0 right-0 text-[10px] text-gray-400 font-semibold px-1 -translate-y-1/2" style="top: {{ "%.0f"|format(hm.top) }}px;">{{ hm.label }}</div>
                                {% endfor %}
                            </div>
                        </div>
                        <!-- 每位发型师一列 -->
                        {% for col in timeline_columns %}
                        <div class="flex-shrink-0 border-r border-gray-100 last:border-r-0" style="width: 200px;">
                            <div class="h-10 flex items-center gap-2 px-3 border-b border-gray-100 bg-gray-50 sticky top-0">
                                <div class="w-6 h-6 rounded-full flex items-center justify-center text-[11px] font-bold" style="background:{{ col.color.bg }}; color:{{ col.color.text }};">{{ col.name[0] }}</div>
                                <div class="min-w-0">
                                    <div class="text-xs font-bold text-gray-800 truncate">{{ col.name }}</div>
                                    <div class="text-[10px] text-gray-400 truncate">{{ col.title }}</div>
                                </div>
                            </div>
                            <div class="relative" style="height: {{ timeline_height }}px; background-image: repeating-linear-gradient(to bottom, #f3f4f6 0, #f3f4f6 1px, transparent 1px, transparent {{ "%.0f"|format(60 * 1.6) }}px);">
                                {% for hm in hour_marks %}
                                <div class="absolute left-0 right-0 border-t border-gray-100" style="top: {{ "%.0f"|format(hm.top) }}px;"></div>
                                {% endfor %}
                                {% for block in timeline_blocks.get(col.key, []) %}
                                <div class="absolute left-1 right-1 rounded-lg border-l-4 px-2 py-1 overflow-hidden shadow-sm hover:shadow-md transition cursor-default" style="top: {{ "%.0f"|format(block.top) }}px; height: {{ "%.0f"|format(block.height) }}px; background:{{ block.color.bg }}; border-color:{{ block.color.border }};">
                                    <div class="flex justify-between items-start gap-1">
                                        <div class="min-w-0">
                                            <div class="text-[11px] font-bold truncate" style="color:{{ block.color.text }};">{{ block.customer_name }}</div>
                                            <div class="text-[10px] text-gray-600 truncate">{{ block.service_name }}</div>
                                            <div class="text-[9px] text-gray-400">{{ block.time_range }}</div>
                                        </div>
                                        <div class="flex flex-col items-end gap-0.5 flex-shrink-0">
                                            <a href="/admin/appointment/whatsapp/{{ block.id }}" onclick="event.stopPropagation();" target="_blank" class="text-[11px] font-bold text-green-500 hover:text-green-700">💬</a>
                                            <a href="/admin/appointment/delete/{{ block.id }}" onclick="event.stopPropagation(); return confirm('确定取消此预约吗？')" class="text-[11px] font-bold text-red-400 hover:text-red-600">×</a>
                                        </div>
                                    </div>
                                </div>
                                {% endfor %}
                            </div>
                        </div>
                        {% endfor %}
                    </div>
                </div>
                {% else %}
                <div class="p-8 text-center text-gray-400 border border-gray-100 rounded-xl">还没有添加发型师，先去「员工与佣金管理」添加员工，时间轴才会有列可以显示。</div>
                {% endif %}
            </div>

            <!-- 列表视图（备用，方便一次看细节/电话） -->
            <div id="view-list" class="hidden overflow-x-auto">
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
                            <td class="p-3 flex gap-2 items-center">
                                <a href="/admin/appointment/whatsapp/{{ app.id }}" target="_blank" class="bg-green-600 text-white px-2.5 py-1 rounded text-xs font-bold hover:bg-green-700">💬 WhatsApp</a>
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
        function showView(view) {
            document.getElementById('view-timeline').classList.toggle('hidden', view !== 'timeline');
            document.getElementById('view-list').classList.toggle('hidden', view !== 'list');
            document.getElementById('btn-view-timeline').className = 'text-xs font-bold px-3 py-1.5 rounded-lg ' + (view === 'timeline' ? 'bg-indigo-600 text-white' : 'bg-gray-100 text-gray-600');
            document.getElementById('btn-view-list').className = 'text-xs font-bold px-3 py-1.5 rounded-lg ' + (view === 'list' ? 'bg-indigo-600 text-white' : 'bg-gray-100 text-gray-600');
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
            
            cursor.execute("SELECT * FROM services WHERE category_type != 'Packages' AND COALESCE(is_active, TRUE) = TRUE")
            services = cursor.fetchall()
            cursor.execute("SELECT * FROM stylists")
            stylists = cursor.fetchall()
            cursor.execute("SELECT * FROM customers")
            customers = cursor.fetchall()
        
    open_time_str = get_setting("open_time", "10:00")
    close_time_str = get_setting("close_time", "20:00")
    slot_interval_min = get_int_setting("slot_interval_minutes", 30, minimum=5)
    timeslots = []
    st = datetime.strptime(open_time_str, "%H:%M")
    et = datetime.strptime(close_time_str, "%H:%M")
    while st <= et:
        timeslots.append(st.strftime("%H:%M"))
        st += timedelta(minutes=slot_interval_min)
    COLOR_PALETTE = [
        {"bg": "#eef2ff", "border": "#6366f1", "text": "#4338ca"},  # indigo
        {"bg": "#ecfeff", "border": "#06b6d4", "text": "#0e7490"},  # cyan
        {"bg": "#fef3c7", "border": "#f59e0b", "text": "#b45309"},  # amber
        {"bg": "#fce7f3", "border": "#ec4899", "text": "#be185d"},  # pink
        {"bg": "#dcfce7", "border": "#22c55e", "text": "#15803d"},  # green
        {"bg": "#ede9fe", "border": "#8b5cf6", "text": "#6d28d9"},  # violet
    ]
    PX_PER_MIN = 1.6

    def hm_to_min(hm_str):
        h, m = hm_str.split(":")
        return int(h) * 60 + int(m)

    day_start_min = hm_to_min(open_time_str)
    day_end_min = hm_to_min(close_time_str)
    timeline_height = max(1, (day_end_min - day_start_min)) * PX_PER_MIN

    # 每位发型师一列，顺序固定；用「姓名 (职级)」当作匹配预约记录的 key
    timeline_columns = []
    col_key_lookup = {}
    for i, s in enumerate(stylists):
        key = f"{s['name']} ({s['title']})"
        col = {"key": key, "name": s["name"], "title": s["title"], "color": COLOR_PALETTE[i % len(COLOR_PALETTE)]}
        timeline_columns.append(col)
        col_key_lookup[key] = col

    # 小时刻度线（每小时一条，顶部对齐营业时间）
    hour_marks = []
    h_min = (day_start_min // 60) * 60
    if h_min < day_start_min:
        h_min += 60
    while h_min <= day_end_min:
        hour_marks.append({"label": f"{h_min // 60:02d}:00", "top": (h_min - day_start_min) * PX_PER_MIN})
        h_min += 60

    other_col_needed = False
    timeline_blocks = {col["key"]: [] for col in timeline_columns}
    for a in appointments:
        start_hm = a["start_time"].split(" ")[1]
        end_hm = a["end_time"].split(" ")[1]
        start_min = hm_to_min(start_hm)
        end_min = hm_to_min(end_hm)
        top_px = max(0, (start_min - day_start_min) * PX_PER_MIN)
        height_px = max(22, (end_min - start_min) * PX_PER_MIN)
        block = {
            "id": a["id"], "customer_name": a["customer_name"], "customer_phone": a["customer_phone"],
            "service_name": a["service_name"], "status": a["status"], "stylist": a["stylist"],
            "time_range": f"{start_hm} - {end_hm}", "top": top_px, "height": height_px,
        }
        col = col_key_lookup.get(a["stylist"])
        if col:
            block["color"] = col["color"]
            timeline_blocks[a["stylist"]].append(block)
        else:
            other_col_needed = True
            block["color"] = {"bg": "#f3f4f6", "border": "#9ca3af", "text": "#4b5563"}
            timeline_blocks.setdefault("__other__", []).append(block)
    if other_col_needed:
        timeline_columns.append({"key": "__other__", "name": "Other", "title": "已删除/未匹配员工", "color": {"bg": "#f3f4f6", "border": "#9ca3af", "text": "#4b5563"}})

    return render_template_string(ADMIN_APPOINTMENTS_TEMPLATE, appointments=appointments, date_strip=date_strip, selected_date=selected_date, today_str=today_str, services=services, stylists=stylists, customers=customers, timeslots=timeslots, error=request.args.get("error"), timeline_columns=timeline_columns, timeline_blocks=timeline_blocks, timeline_height=timeline_height, hour_marks=hour_marks)

@app.route("/admin/appointment/add", methods=["POST"])
@admin_required
def admin_add_appointment():
    service_id = request.form.get("service_id")
    stylist = request.form.get("stylist")
    b_date = request.form.get("booking_date")
    b_time = request.form.get("booking_time")
    c_name = request.form.get("customer_name")
    c_phone = request.form.get("customer_phone")
    buffer_minutes = get_int_setting("buffer_minutes", 0, minimum=0)
    
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT duration FROM services WHERE id = %s", (service_id,))
            srv = cursor.fetchone()
            duration = srv["duration"] if srv else 30
            start_dt = datetime.strptime(f"{b_date} {b_time}", "%Y-%m-%d %H:%M")
            end_dt = start_dt + timedelta(minutes=duration)
            start_str = start_dt.strftime("%Y-%m-%d %H:%M")
            end_str = end_dt.strftime("%Y-%m-%d %H:%M")

            if find_conflicting_appointment(cursor, stylist, start_str, end_str, buffer_minutes=buffer_minutes):
                return redirect(url_for("admin_appointments", date=b_date, error="该发型师这个时间段已经有预约了，请换个时间或发型师！"))
            
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

@app.route("/admin/appointment/whatsapp/<int:id>")
@admin_required
def admin_appointment_whatsapp(id):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT a.*, c.name as customer_name, c.phone as customer_phone, c.token as customer_token, s.name as service_name
                FROM appointments a
                JOIN customers c ON a.customer_id = c.id
                JOIN services s ON a.service_id = s.id
                WHERE a.id = %s
            """, (id,))
            appt = cursor.fetchone()
            if not appt:
                return "Appointment not found", 404

    portal_link = request.host_url.rstrip('/') + f"/customer/{appt['customer_token']}"
    msg = (
        f"🌟 *Dew Hair Salon - Booking Confirmation* 🌟\n\n"
        f"Hello *{appt['customer_name']}*,\n"
        f"Your appointment has been confirmed:\n\n"
        f"💇 *Service:* {appt['service_name']}\n"
        f"💈 *Stylist:* {appt['stylist']}\n"
        f"🕐 *Time:* {appt['start_time']} - {appt['end_time'].split(' ')[1]}\n\n"
        f"🔗 *Manage your booking here:*\n{portal_link}\n\n"
        f"See you soon!"
    )
    phone = "".join(filter(str.isdigit, appt['customer_phone']))
    if phone.startswith('0'):
        phone = '6' + phone
    wa_url = f"https://api.whatsapp.com/send?phone={phone}&text={urllib.parse.quote(msg)}"
    return redirect(wa_url)

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
def build_receipt_pdf(order, items):
    """用 reportlab 生成一份真正的 PDF 收据（不含员工佣金，只显示员工名字）"""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=20*mm, bottomMargin=20*mm, leftMargin=20*mm, rightMargin=20*mm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('title', parent=styles['Heading1'], textColor=colors.HexColor('#4f46e5'))
    normal = styles['Normal']

    elements = [
        Paragraph("Dew Hair Salon", title_style),
        Paragraph("Official Receipt", normal),
        Spacer(1, 10),
        Paragraph(f"<b>Order No:</b> {order['order_no']}", normal),
        Paragraph(f"<b>Date/Time:</b> {order['created_at']}", normal),
        Paragraph(f"<b>Customer:</b> {order['customer_name']} ({order['customer_phone']})", normal),
        Paragraph(f"<b>Payment Method:</b> {order['payment_details']}", normal),
        Paragraph(f"<b>Credit Balance:</b> RM {order.get('customer_credits', 0.0):.2f}", normal),
    ]
    if order.get('remark'):
        elements.append(Paragraph(f"<b>Remark:</b> {order['remark']}", normal))
    elements.append(Spacer(1, 14))

    table_data = [["Item", "Staff", "Price (RM)"]]
    subtotal = 0.0
    for item in items:
        staff_names = ", ".join(c["staff_name"] for c in item.get("collaborators", [])) or (item.get("staff_name") or "-")
        table_data.append([item["item_name"], staff_names, f"{item['price']:.2f}"])
        subtotal += item["price"]
    discount_pct = order.get("discount_percent") or 0
    if discount_pct > 0:
        table_data.append(["", "Subtotal", f"RM {subtotal:.2f}"])
        table_data.append(["", f"Discount ({discount_pct:.1f}%)", f"- RM {(subtotal - order['total_amount']):.2f}"])
    table_data.append(["", "Total", f"RM {order['total_amount']:.2f}"])

    tbl = Table(table_data, colWidths=[70*mm, 60*mm, 30*mm])
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f3f4f6')),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('LINEBELOW', (0, 0), (-1, 0), 0.5, colors.grey),
        ('LINEBELOW', (0, -2), (-1, -2), 0.5, colors.grey),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(tbl)
    elements.append(Spacer(1, 20))
    elements.append(Paragraph("Thank you for visiting us! Hope to see you again soon.", normal))

    doc.build(elements)
    buf.seek(0)
    return buf

@app.route("/receipt/<token>/<int:order_id>")
def customer_receipt_pdf(token, order_id):
    """顾客专属的 PDF 收据下载链接，不需要登录后台，但要 token 与订单顾客匹配才给看"""
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT o.*, c.name as customer_name, c.phone as customer_phone, c.token as customer_token, c.credits as customer_credits
                FROM orders o JOIN customers c ON o.customer_id = c.id
                WHERE o.id = %s AND c.token = %s
            """, (order_id, token))
            order = cursor.fetchone()
            if not order:
                return "Receipt not found", 404
            cursor.execute("SELECT * FROM order_items WHERE order_id = %s", (order_id,))
            items = cursor.fetchall()
            for item in items:
                cursor.execute("SELECT staff_name FROM order_item_staff WHERE order_item_id = %s", (item["id"],))
                item["collaborators"] = cursor.fetchall()

    try:
        pdf_buf = build_receipt_pdf(order, items)
    except ImportError:
        return "生成 PDF 需要先在 requirements.txt 里加上 reportlab 这个库，重新部署后再试一次。", 500

    return send_file(pdf_buf, mimetype="application/pdf", as_attachment=False, download_name=f"receipt_{order['order_no']}.pdf")

@app.route("/admin/order/whatsapp/<int:id>")
@admin_required
def admin_order_whatsapp(id):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT o.*, c.name as customer_name, c.phone as customer_phone, c.token as customer_token, c.credits as customer_credits
                FROM orders o JOIN customers c ON o.customer_id = c.id WHERE o.id = %s
            """, (id,))
            order = cursor.fetchone()
            if not order:
                return "Order not found", 404
            cursor.execute("SELECT * FROM order_items WHERE order_id = %s", (id,))
            items = cursor.fetchall()
        
    items_str = "\n".join([f"- {item['item_name']}: RM {item['price']:.2f}" for item in items])
    receipt_link = request.host_url.rstrip('/') + f"/receipt/{order['customer_token']}/{order['id']}"
    
    msg = (
        f"🌟 *Dew Hair Salon - Official Invoice* 🌟\n\n"
        f"Hello *{order['customer_name']}*,\n"
        f"Thank you for visiting us! Here's your receipt:\n\n"
        f"🧾 *Order No:* {order['order_no']}\n"
        f"📅 *Date:* {order['created_at']}\n\n"
        f"*Purchased Items:*\n{items_str}\n\n"
        f"💰 *Total Amount:* RM {order['total_amount']:.2f}\n"
        f"💳 *Payment Method:* {order['payment_details']}\n"
        f"💎 *Credit Balance:* RM {order['customer_credits']:.2f}\n"
    )
    if order['remark']:
        msg += f"📝 *Remark:* {order['remark']}\n"
        
    msg += f"\n📄 *Download your PDF Receipt here:*\n{receipt_link}\n\nHope to see you again soon!"
    
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
                SELECT o.*, c.name as customer_name, c.phone as customer_phone, c.token as customer_token, c.credits as customer_credits
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
            <p><strong>Credit Balance:</strong> <span style="color:#16a34a;font-weight:bold;">RM {{ "%.2f"|format(order.customer_credits) }}</span></p>
            {% if order.remark %}<p><strong>Remark:</strong> <span style="color:#d97706;">{{ order.remark }}</span></p>{% endif %}
            <hr style="border:0;border-top:1px solid #eee;margin:15px 0;">
            <h3 style="font-size:16px;margin-bottom:8px;">Items Purchased</h3>
            <table style="width:100%;border-collapse:collapse;font-size:14px;margin-bottom:15px;">
                <thead>
                    <tr style="border-bottom:1px solid #ddd;background:#f9fafb;">
                        <th style="text-align:left;padding:6px;">Item Name</th>
                        <th style="text-align:left;padding:6px;">Staff</th>
                        <th style="text-align:right;padding:6px;">Price</th>
                    </tr>
                </thead>
                <tbody>
                    {% for item in items %}
                    <tr style="border-bottom:1px solid #eee;">
                        <td style="padding:6px;">{{ item.item_name }}</td>
                        <td style="padding:6px;color:#4f46e5;">
                            {% if item.collaborators %}
                                {% for c in item.collaborators %}
                                    <div>{{ c.staff_name }}</div>
                                {% endfor %}
                            {% elif item.staff_name %}
                                <div>{{ item.staff_name }}</div>
                            {% else %}-{% endif %}
                        </td>
                        <td style="text-align:right;padding:6px;">
                            RM {{ "%.2f"|format(item.price) }}
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            <div style="text-align:right;margin-bottom:20px;">
                {% if order.discount_percent and order.discount_percent > 0 %}
                <div style="font-size:13px;color:#dc2626;margin-bottom:4px;">Discount applied: {{ "%.1f"|format(order.discount_percent) }}%</div>
                {% endif %}
                <div style="font-size:18px;font-weight:bold;">Total Amount: <span style="color:#dc2626;">RM {{ "%.2f"|format(order.total_amount) }}</span></div>
            </div>
            <div style="display:flex;gap:10px;flex-wrap:wrap;">
                <a href="/receipt/{{ order.customer_token }}/{{ order.id }}" target="_blank" style="flex:1;text-align:center;padding:12px;background:#dc2626;color:white;text-decoration:none;border-radius:6px;font-weight:bold;">📄 查看真正的 PDF 收据</a>
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
                cursor.execute("SELECT credit_value, category_type, stock_qty FROM services WHERE name = %s", (item["item_name"],))
                srv = cursor.fetchone()
                if srv and srv["credit_value"] > 0:
                    current_credits -= srv["credit_value"]
                # 作废订单时把之前扣掉的零售产品库存补回去
                if srv and srv["category_type"] == "Products" and srv["stock_qty"] is not None:
                    cursor.execute("UPDATE services SET stock_qty = stock_qty + 1 WHERE name = %s", (item["item_name"],))
            
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

def get_staff_payroll(month_prefix):
    """按月汇总每位员工的销售额与佣金（新版协作记录 + 兼容更新前的旧版单员工记录）"""
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT staff_name, SUM(item_count) as item_count, SUM(total_sales) as total_sales, SUM(total_commission) as total_commission
                FROM (
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

                    SELECT i.staff_name as staff_name,
                           COUNT(i.id) as item_count,
                           SUM(i.price) as total_sales,
                           SUM(i.commission) as total_commission
                    FROM order_items i
                    JOIN orders o ON i.order_id = o.id
                    WHERE o.status = 'NORMAL' AND o.created_at LIKE %s
                      AND i.staff_name IS NOT NULL AND i.staff_name != ''
                      AND NOT EXISTS (SELECT 1 FROM order_item_staff os2 WHERE os2.order_item_id = i.id)
                    GROUP BY i.staff_name
                ) combined
                GROUP BY staff_name
                ORDER BY total_commission DESC
            """, (f"{month_prefix}%", f"{month_prefix}%"))
            return cursor.fetchall()

PAYROLL_TEMPLATE = """
    <div class="bg-white p-6 rounded shadow space-y-4">
        <div class="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
            <h2 class="text-xl font-bold text-indigo-600">💰 月结员工薪资 / 佣金报表</h2>
            <form action="/admin/payroll" method="GET" class="flex gap-2 items-center">
                <input type="month" name="month" value="{{ month }}" class="border rounded px-3 py-1.5 text-sm">
                <button class="bg-indigo-600 text-white px-3 py-1.5 rounded text-sm font-bold">查看</button>
            </form>
        </div>

        <table class="w-full text-left border-collapse">
            <thead>
                <tr class="border-b bg-gray-50 text-sm">
                    <th class="p-2">员工姓名</th>
                    <th class="p-2">服务项目数</th>
                    <th class="p-2">总销售额 (RM)</th>
                    <th class="p-2 text-green-700">应发佣金 (RM)</th>
                </tr>
            </thead>
            <tbody>
                {% for sp in payroll %}
                <tr class="border-b">
                    <td class="p-2 font-bold">{{ sp.staff_name }}</td>
                    <td class="p-2">{{ sp.item_count }}</td>
                    <td class="p-2 font-bold">RM {{ "%.2f"|format(sp.total_sales) }}</td>
                    <td class="p-2 font-bold text-green-600">RM {{ "%.2f"|format(sp.total_commission) }}</td>
                </tr>
                {% else %}
                <tr><td colspan="4" class="p-3 text-gray-400">该月暂无佣金记录</td></tr>
                {% endfor %}
            </tbody>
            {% if payroll %}
            <tfoot>
                <tr class="border-t-2 font-bold bg-gray-50">
                    <td class="p-2">合计</td>
                    <td class="p-2">{{ payroll|sum(attribute='item_count') }}</td>
                    <td class="p-2">RM {{ "%.2f"|format(payroll|sum(attribute='total_sales')) }}</td>
                    <td class="p-2 text-green-700">RM {{ "%.2f"|format(payroll|sum(attribute='total_commission')) }}</td>
                </tr>
            </tfoot>
            {% endif %}
        </table>

        <div class="flex gap-2">
            <a href="/admin/payroll/export?month={{ month }}" class="bg-green-600 text-white px-4 py-2 rounded text-sm font-bold hover:bg-green-700">📥 导出 Excel</a>
            <a href="/admin/payroll/print?month={{ month }}" target="_blank" class="bg-indigo-600 text-white px-4 py-2 rounded text-sm font-bold hover:bg-indigo-700">🖨️ 打印 / 另存为 PDF</a>
        </div>
    </div>
"""

@app.route("/admin/payroll")
@admin_required
def admin_payroll():
    month = request.args.get("month") or datetime.now(MY_TZ).strftime("%Y-%m")
    payroll = get_staff_payroll(month)
    return render_template_string(LAYOUT_TEMPLATE.replace("{% block content %}{% endblock %}", PAYROLL_TEMPLATE), payroll=payroll, month=month)

@app.route("/admin/payroll/export")
@admin_required
def admin_payroll_export():
    month = request.args.get("month") or datetime.now(MY_TZ).strftime("%Y-%m")
    payroll = get_staff_payroll(month)
    try:
        from openpyxl import Workbook
    except ImportError:
        return "导出 Excel 需要先在 requirements.txt 里加上 openpyxl 这个库，重新部署后再试一次。", 500

    wb = Workbook()
    ws = wb.active
    ws.title = f"{month} 薪资报表"
    ws.append(["员工姓名", "服务项目数", "总销售额 (RM)", "应发佣金 (RM)"])
    total_items, total_sales, total_commission = 0, 0.0, 0.0
    for sp in payroll:
        ws.append([sp["staff_name"], sp["item_count"], round(sp["total_sales"], 2), round(sp["total_commission"], 2)])
        total_items += sp["item_count"]
        total_sales += sp["total_sales"]
        total_commission += sp["total_commission"]
    ws.append(["合计", total_items, round(total_sales, 2), round(total_commission, 2)])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name=f"payroll_{month}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

@app.route("/admin/payroll/print")
@admin_required
def admin_payroll_print():
    month = request.args.get("month") or datetime.now(MY_TZ).strftime("%Y-%m")
    payroll = get_staff_payroll(month)
    return render_template_string("""
        <div style="max-width:650px;margin:40px auto;padding:25px;border:1px solid #ccc;font-family:sans-serif;border-radius:8px;background:#fff;">
            <h2 style="color:#4f46e5;">Dew Hair Salon - {{ month }} 员工薪资 / 佣金报表</h2>
            <hr style="border:0;border-top:1px solid #eee;margin:15px 0;">
            <table style="width:100%;border-collapse:collapse;font-size:14px;">
                <thead>
                    <tr style="border-bottom:1px solid #ddd;background:#f9fafb;">
                        <th style="text-align:left;padding:8px;">员工姓名</th>
                        <th style="text-align:right;padding:8px;">服务项目数</th>
                        <th style="text-align:right;padding:8px;">总销售额 (RM)</th>
                        <th style="text-align:right;padding:8px;">应发佣金 (RM)</th>
                    </tr>
                </thead>
                <tbody>
                    {% for sp in payroll %}
                    <tr style="border-bottom:1px solid #eee;">
                        <td style="padding:8px;">{{ sp.staff_name }}</td>
                        <td style="text-align:right;padding:8px;">{{ sp.item_count }}</td>
                        <td style="text-align:right;padding:8px;">RM {{ "%.2f"|format(sp.total_sales) }}</td>
                        <td style="text-align:right;padding:8px;color:green;font-weight:bold;">RM {{ "%.2f"|format(sp.total_commission) }}</td>
                    </tr>
                    {% else %}
                    <tr><td colspan="4" style="padding:8px;color:#999;">该月暂无佣金记录</td></tr>
                    {% endfor %}
                </tbody>
            </table>
            <div style="margin-top:20px;">
                <button onclick="window.print()" style="padding:12px 20px;background:#10b981;color:white;border:none;border-radius:6px;font-weight:bold;cursor:pointer;">📥 打印 / 另存为 PDF</button>
            </div>
        </div>
    """, payroll=payroll, month=month)

@app.route("/admin/pos")
@admin_required
def admin_pos():
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM services WHERE COALESCE(is_active, TRUE) = TRUE ORDER BY sub_category, name")
            services = cursor.fetchall()
            cursor.execute("SELECT * FROM stylists")
            stylists = cursor.fetchall()
            cursor.execute("SELECT * FROM customers")
            customers = cursor.fetchall()
            sync_service_categories(cursor)
            conn.commit()
            cursor.execute("SELECT name FROM service_categories ORDER BY name")
            pos_categories = [r["name"] for r in cursor.fetchall()]
    stylists_json = json.dumps([
        {"name": st["name"], "title": st["title"], "commission_type": st["commission_type"], "commission_value": st["commission_value"]}
        for st in stylists
    ])
    return render_template_string(LAYOUT_TEMPLATE.replace("{% block content %}{% endblock %}", """
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div class="md:col-span-2 bg-white p-5 rounded-2xl shadow-sm border border-gray-100">
                <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-4">
                    <h2 class="text-lg font-bold text-gray-900">Point of Sale</h2>
                    <input type="text" id="pos_search" oninput="filterPOS()" placeholder="🔍 搜索项目名称..." class="border border-gray-200 rounded-lg px-3 py-2 text-sm w-full sm:w-56 focus:border-indigo-500 focus:outline-none">
                </div>

                <!-- 分类标签页 -->
                <div class="flex gap-2 overflow-x-auto pb-3 mb-3 border-b border-gray-100">
                    <button onclick="filterPOS('__all__')" data-cat-btn="__all__" class="pos-tab flex-shrink-0 px-3.5 py-1.5 rounded-full text-xs font-bold bg-indigo-600 text-white">全部</button>
                    {% for c in pos_categories %}
                    <button onclick="filterPOS('{{ c }}')" data-cat-btn="{{ c }}" class="pos-tab flex-shrink-0 px-3.5 py-1.5 rounded-full text-xs font-bold bg-gray-100 text-gray-600 hover:bg-gray-200">{{ c }}</button>
                    {% endfor %}
                </div>

                <div class="grid grid-cols-2 sm:grid-cols-3 gap-3" id="pos_grid">
                    {% for item in services %}
                    <button onclick="addToOrder('{{ item.name }}', {{ item.price }})" data-cat="{{ item.sub_category or item.category_type }}" data-name="{{ item.name|lower }}" class="pos-item p-3.5 border border-gray-100 rounded-xl hover:border-indigo-400 hover:shadow-md text-left transition bg-white">
                        <div class="flex items-center justify-between mb-1">
                            <span class="text-[10px] font-bold px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">{{ item.category_type }}</span>
                        </div>
                        <div class="font-semibold text-gray-900 text-sm leading-snug">{{ item.name }}</div>
                        <div class="text-indigo-600 font-bold text-sm mt-1">RM {{ "%.2f"|format(item.price) }}</div>
                        {% if item.category_type == 'Products' %}
                            <div class="text-[10px] font-bold mt-1 {% if item.stock_qty is none or item.stock_qty <= 0 %}text-red-600{% elif item.stock_qty <= 5 %}text-orange-600{% else %}text-gray-400{% endif %}">
                                库存: {{ item.stock_qty if item.stock_qty is not none else 0 }}{% if item.stock_qty is not none and item.stock_qty <= 0 %} (缺货){% endif %}
                            </div>
                        {% endif %}
                    </button>
                    {% endfor %}
                </div>
                <p id="pos_empty_hint" class="hidden text-center text-gray-400 text-sm py-8">没有符合条件的项目</p>
            </div>
            <div class="bg-white p-5 rounded-2xl shadow-sm border border-gray-100 h-fit">
                <h2 class="text-lg font-bold text-gray-900 mb-4">当前订单结账</h2>
                <div id="order-items" class="min-h-[150px] border-b border-gray-100 mb-4 pb-2"><p class="text-gray-400 text-sm">点击左侧项目加入订单</p></div>

                <div class="mb-3 flex items-center justify-between gap-2">
                    <label class="text-xs font-semibold text-gray-500">整单折扣 %</label>
                    <input type="number" id="discount_percent" min="0" max="100" step="0.1" value="0" oninput="renderCart()" class="w-20 border border-gray-200 rounded-lg p-1.5 text-sm text-right">
                </div>
                <div class="text-sm text-gray-500 flex justify-between mb-1"><span>小计</span><span>RM <span id="subtotal-amount">0.00</span></span></div>
                <div class="text-sm text-red-500 flex justify-between mb-1" id="discount-row" style="display:none;"><span>折扣</span><span>- RM <span id="discount-amount">0.00</span></span></div>
                <div class="text-lg font-bold mb-4 flex justify-between border-t border-gray-100 pt-2"><span class="text-gray-700 text-sm font-medium">应付总额</span><span class="text-red-600">RM <span id="total-amount">0.00</span></span></div>
                
                <form action="/admin/checkout" method="POST">
                    <input type="hidden" name="cart_data" id="cart_data_input">
                    <input type="hidden" name="discount_percent" id="discount_percent_input" value="0">
                    <div class="mb-3">
                        <label class="block text-xs font-semibold text-gray-500 mb-1">选择已有会员</label>
                        <select name="customer_phone" id="cust_select" onchange="fillCustomer(this)" class="w-full border border-gray-200 rounded-lg p-2 text-sm">
                            <option value="">-- 新客或手动输入 --</option>
                            {% for c in customers %}
                            <option value="{{ c.phone }}" data-name="{{ c.name }}">{{ c.name }} ({{ c.phone }}) - 余额: RM {{ c.credits }}</option>
                            {% endfor %}
                        </select>
                    </div>
                    <div class="mb-3">
                        <label class="block text-xs font-semibold text-gray-500 mb-1">顾客姓名</label>
                        <input type="text" name="customer_name" id="cust_name" class="w-full border border-gray-200 rounded-lg p-2 text-sm" required>
                    </div>
                    <div class="mb-3">
                        <label class="block text-xs font-semibold text-gray-500 mb-1">顾客电话</label>
                        <input type="text" name="customer_phone_input" id="cust_phone" class="w-full border border-gray-200 rounded-lg p-2 text-sm" required>
                    </div>
                    <div class="mb-4">
                        <label class="block text-xs font-semibold text-gray-500 mb-1">支付方式</label>
                        <select name="payment_method" class="w-full border border-gray-200 rounded-lg p-2 text-sm">
                            <option value="Cash">Cash (现金)</option>
                            <option value="Credit Card">Credit Card (刷卡)</option>
                            <option value="TNG / QRPay">TNG / QRPay (电子钱包)</option>
                            <option value="Credit Balance Deduct">Credit Balance Deduct (储值余额扣款)</option>
                        </select>
                    </div>
                    <div class="mb-4">
                        <label class="block text-xs font-semibold text-gray-500 mb-1">备注 (会显示在单据上，选填)</label>
                        <input type="text" name="remark" placeholder="例如: 顾客要求下次换用敏感肌染发剂" class="w-full border border-gray-200 rounded-lg p-2 text-sm">
                    </div>
                    <button class="w-full bg-green-600 text-white font-bold py-3 rounded-xl hover:bg-green-700 shadow-sm">完成收款与记账</button>
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
            let currentCat = '__all__';
            const STYLISTS = {{ stylists_json|safe }};

            function filterPOS(cat) {
                if (cat !== undefined) currentCat = cat;
                document.querySelectorAll('.pos-tab').forEach(btn => {
                    const active = btn.getAttribute('data-cat-btn') === currentCat;
                    btn.className = 'pos-tab flex-shrink-0 px-3.5 py-1.5 rounded-full text-xs font-bold ' + (active ? 'bg-indigo-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200');
                });
                const query = document.getElementById('pos_search').value.trim().toLowerCase();
                let visibleCount = 0;
                document.querySelectorAll('.pos-item').forEach(el => {
                    const matchesCat = currentCat === '__all__' || el.getAttribute('data-cat') === currentCat;
                    const matchesSearch = !query || el.getAttribute('data-name').includes(query);
                    const show = matchesCat && matchesSearch;
                    el.style.display = show ? '' : 'none';
                    if (show) visibleCount++;
                });
                document.getElementById('pos_empty_hint').classList.toggle('hidden', visibleCount > 0);
            }

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
                let subtotal = 0; container.innerHTML = '';
                cart.forEach((item, index) => {
                    subtotal += item.price;
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
                let discountPct = parseFloat(document.getElementById('discount_percent').value) || 0;
                if (discountPct < 0) discountPct = 0;
                if (discountPct > 100) discountPct = 100;
                const discountAmt = subtotal * (discountPct / 100);
                const finalTotal = subtotal - discountAmt;

                document.getElementById('subtotal-amount').innerText = subtotal.toFixed(2);
                document.getElementById('discount-row').style.display = discountPct > 0 ? 'flex' : 'none';
                document.getElementById('discount-amount').innerText = discountAmt.toFixed(2);
                document.getElementById('total-amount').innerText = finalTotal.toFixed(2);
                document.getElementById('discount_percent_input').value = discountPct;
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
    """), services=services, stylists=stylists, customers=customers, stylists_json=stylists_json, pos_categories=pos_categories)

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
        remark = (request.form.get("remark") or "").strip()
        subtotal = sum(item["price"] for item in cart_data)
        discount_percent = float(request.form.get("discount_percent", 0) or 0)
        discount_percent = max(0.0, min(100.0, discount_percent))
        total = round(subtotal * (1 - discount_percent / 100), 2)
        
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
                    cursor.execute("SELECT credit_value, category_type, stock_qty FROM services WHERE name = %s", (item["name"],))
                    srv = cursor.fetchone()
                    if srv and srv["credit_value"] > 0:
                        current_credits += srv["credit_value"]
                        cursor.execute("UPDATE customers SET credits = %s WHERE id = %s", (current_credits, cust_id))
                    # 零售产品每卖出一件自动扣一次库存（库存不会扣成负数）
                    if srv and srv["category_type"] == "Products" and srv["stock_qty"] is not None:
                        new_stock = max(0, srv["stock_qty"] - 1)
                        cursor.execute("UPDATE services SET stock_qty = %s WHERE name = %s", (new_stock, item["name"]))

                if pay_method == "Credit Balance Deduct":
                    if current_credits < total: return "结算失败：该顾客 Credit 余额不足！", 400
                    current_credits -= total
                    cursor.execute("UPDATE customers SET credits = %s WHERE id = %s", (current_credits, cust_id))
                    
                cursor.execute("INSERT INTO orders (order_no, customer_id, total_amount, payment_details, status, created_at, discount_percent, remark) VALUES (%s, %s, %s, %s, 'NORMAL', %s, %s, %s) RETURNING id", (order_no, cust_id, total, pay_method, current_time_str, discount_percent, remark))
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
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dew Hair Salon - Book an Appointment</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { font-family: 'Inter', sans-serif; }
        .step-badge { display:inline-flex; align-items:center; justify-content:center; width:26px; height:26px; border-radius:9999px; background:#4f46e5; color:white; font-weight:700; font-size:13px; }
    </style>
</head>
<body class="bg-gray-50 min-h-screen">
    <div class="max-w-2xl mx-auto p-4 md:p-8">
        <!-- Header -->
        <div class="text-center mb-6">
            <h1 class="text-2xl md:text-3xl font-extrabold text-gray-900">Dew Hair Salon</h1>
            <p class="text-sm text-gray-500 mt-1">Book your appointment online in under a minute</p>
            <p class="text-xs text-gray-400 mt-1">Open {{ open_time }} - {{ close_time }} (Malaysia time)</p>
        </div>

        {% if error %}
        <div class="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded-lg text-sm font-semibold">⚠️ {{ error }}</div>
        {% endif %}

        <form action="/book" method="POST" id="bookingForm" class="space-y-5">

            <!-- Step 1: Service -->
            <div class="bg-white rounded-2xl shadow-sm border border-gray-100 p-5">
                <div class="flex items-center gap-2 mb-4">
                    <span class="step-badge">1</span>
                    <h2 class="font-bold text-gray-900">Choose a service</h2>
                </div>
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    {% for item in services %}
                    <label class="group border-2 border-gray-100 rounded-xl p-3 cursor-pointer hover:border-indigo-400 has-[:checked]:border-indigo-600 has-[:checked]:bg-indigo-50 flex items-center justify-between transition">
                        <div>
                            <div class="font-semibold text-gray-900">{{ item.name }}</div>
                            <div class="text-xs text-gray-400">{{ item.duration }} min</div>
                        </div>
                        <div class="text-right flex items-center gap-2">
                            <span class="text-indigo-600 font-bold text-sm">RM {{ "%.2f"|format(item.price) }}</span>
                            <input type="radio" name="service_id" value="{{ item.id }}" class="accent-indigo-600 w-4 h-4" required {% if loop.first %}checked{% endif %}>
                        </div>
                    </label>
                    {% endfor %}
                </div>
            </div>

            <!-- Step 2: Stylist -->
            <div class="bg-white rounded-2xl shadow-sm border border-gray-100 p-5">
                <div class="flex items-center gap-2 mb-4">
                    <span class="step-badge">2</span>
                    <h2 class="font-bold text-gray-900">Pick your stylist</h2>
                </div>
                <div class="grid grid-cols-3 gap-3">
                    {% for st in stylists %}
                    <label class="border-2 border-gray-100 rounded-xl p-3 text-center cursor-pointer hover:border-indigo-400 has-[:checked]:border-indigo-600 has-[:checked]:bg-indigo-50 transition">
                        <div class="w-9 h-9 mx-auto mb-1 rounded-full bg-indigo-100 text-indigo-600 font-bold flex items-center justify-center text-sm">{{ st.name[0] }}</div>
                        <div class="font-semibold text-gray-900 text-sm">{{ st.name }}</div>
                        <div class="text-[11px] text-gray-400 mb-1">{{ st.title }}</div>
                        <input type="radio" name="stylist" value="{{ st.name }} ({{ st.title }})" class="accent-indigo-600 w-4 h-4" required {% if loop.first %}checked{% endif %}>
                    </label>
                    {% endfor %}
                </div>
            </div>

            <!-- Step 3: Date -->
            <div class="bg-white rounded-2xl shadow-sm border border-gray-100 p-5">
                <div class="flex items-center justify-between mb-4">
                    <div class="flex items-center gap-2">
                        <span class="step-badge">3</span>
                        <h2 class="font-bold text-gray-900">Select a date</h2>
                    </div>
                    <input type="date" name="booking_date" id="booking_date" value="{{ selected_date }}" min="{{ today_str }}" max="{{ max_date }}" class="border border-gray-200 rounded-lg px-2 py-1.5 text-sm text-indigo-600 font-semibold" onchange="selectDateCard(this.value)">
                </div>
                <div class="flex gap-2 overflow-x-auto pb-1">
                    {% for d in date_strip %}
                    <div onclick="selectDateCard('{{ d.date_str }}')" class="date-card flex-shrink-0 w-20 p-2.5 rounded-xl border-2 text-center cursor-pointer transition {% if d.date_str == selected_date %}bg-indigo-600 text-white border-indigo-600 shadow-md font-bold{% else %}bg-white text-gray-700 border-gray-100 hover:border-indigo-300{% endif %}" data-date="{{ d.date_str }}">
                        <div class="text-[10px] opacity-80 uppercase tracking-wide">{{ d.weekday }}</div>
                        <div class="text-sm font-bold my-0.5">{{ d.display_date }}</div>
                        <div class="text-[9px] opacity-60">{{ d.year }}</div>
                    </div>
                    {% endfor %}
                </div>
            </div>

            <!-- Step 4: Time -->
            <div class="bg-white rounded-2xl shadow-sm border border-gray-100 p-5">
                <div class="flex items-center gap-2 mb-4">
                    <span class="step-badge">4</span>
                    <h2 class="font-bold text-gray-900">Pick a time slot</h2>
                </div>
                <div class="grid grid-cols-4 sm:grid-cols-6 gap-2 max-h-48 overflow-y-auto p-1">
                    {% for t in timeslots %}
                    <label class="border-2 border-gray-100 bg-white text-center py-2 rounded-lg cursor-pointer hover:border-indigo-400 has-[:checked]:bg-indigo-600 has-[:checked]:border-indigo-600 has-[:checked]:text-white transition text-sm font-medium">
                        <input type="radio" name="booking_time" value="{{ t }}" class="sr-only peer" required>
                        <span>{{ t }}</span>
                    </label>
                    {% endfor %}
                </div>
            </div>

            <!-- Step 5: Your details -->
            <div class="bg-white rounded-2xl shadow-sm border border-gray-100 p-5">
                <div class="flex items-center gap-2 mb-4">
                    <span class="step-badge">5</span>
                    <h2 class="font-bold text-gray-900">Your details</h2>
                </div>
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    <div>
                        <label class="block text-xs font-semibold text-gray-500 mb-1">Full name</label>
                        <input type="text" name="customer_name" placeholder="e.g. Sarah Tan" class="w-full border border-gray-200 rounded-lg p-3 text-sm focus:border-indigo-500 focus:outline-none" required>
                    </div>
                    <div>
                        <label class="block text-xs font-semibold text-gray-500 mb-1">Phone number</label>
                        <input type="text" name="customer_phone" placeholder="e.g. 012-3456789" class="w-full border border-gray-200 rounded-lg p-3 text-sm focus:border-indigo-500 focus:outline-none" required>
                    </div>
                </div>
            </div>

            <button class="w-full bg-indigo-600 text-white font-bold py-4 rounded-xl text-base hover:bg-indigo-700 shadow-md shadow-indigo-200 transition">Confirm Booking</button>
        </form>
    </div>
    <script>
        function selectDateCard(dateStr) {
            document.getElementById('booking_date').value = dateStr;
            document.querySelectorAll('.date-card').forEach(card => {
                if(card.getAttribute('data-date') === dateStr) {
                    card.classList.add('bg-indigo-600', 'text-white', 'border-indigo-600', 'shadow-md', 'font-bold');
                    card.classList.remove('bg-white', 'text-gray-700', 'border-gray-100');
                } else {
                    card.classList.remove('bg-indigo-600', 'text-white', 'border-indigo-600', 'shadow-md', 'font-bold');
                    card.classList.add('bg-white', 'text-gray-700', 'border-gray-100');
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
                wd_map = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                wd_str = wd_map[d.weekday()]
                if d_str == today_str: wd_str = "Today"
                date_strip.append({"date_str": d_str, "display_date": d.strftime("%m-%d"), "year": d.strftime("%Y"), "weekday": wd_str})
                
            cursor.execute("SELECT * FROM services WHERE category_type != 'Packages' AND COALESCE(bookable_online, TRUE) = TRUE AND COALESCE(is_active, TRUE) = TRUE")
            services = cursor.fetchall()
            cursor.execute("SELECT * FROM stylists")
            stylists = cursor.fetchall()
            
    open_time_str = get_setting("open_time", "10:00")
    close_time_str = get_setting("close_time", "20:00")
    slot_interval_min = get_int_setting("slot_interval_minutes", 30, minimum=5)
    max_advance_days = get_int_setting("max_advance_days", 30, minimum=1)
    max_date_str = (datetime.now(MY_TZ) + timedelta(days=max_advance_days)).strftime("%Y-%m-%d")
    timeslots = []
    st = datetime.strptime(open_time_str, "%H:%M")
    et = datetime.strptime(close_time_str, "%H:%M")
    while st <= et:
        timeslots.append(st.strftime("%H:%M"))
        st += timedelta(minutes=slot_interval_min)
        
    error = request.args.get("error")
    return render_template_string(BOOKING_CALENDAR_TEMPLATE, services=services, stylists=stylists, date_strip=date_strip, selected_date=selected_date, today_str=today_str, open_time=open_time_str, close_time=close_time_str, timeslots=timeslots, error=error, max_date=max_date_str)

@app.route("/book", methods=["POST"])
def book_appointment():
    service_id = request.form.get("service_id")
    stylist = request.form.get("stylist")
    b_date = request.form.get("booking_date")
    b_time = request.form.get("booking_time")
    c_name = request.form.get("customer_name")
    c_phone = request.form.get("customer_phone")

    # 先把要用到的设置都读出来，避免在下面的数据库事务进行到一半时再嵌套开连接
    closed_wd = get_setting("closed_weekdays", "1")
    max_advance_days = get_int_setting("max_advance_days", 30, minimum=1)
    buffer_minutes = get_int_setting("buffer_minutes", 0, minimum=0)
    
    with get_db() as conn:
        with conn.cursor() as cursor:
            # 校验休息日
            cursor.execute("SELECT * FROM holidays WHERE date_str = %s", (b_date,))
            if cursor.fetchone():
                return redirect(url_for("index", date=b_date, error="We're closed on this date. Please pick another day."))
            
            dt_obj = datetime.strptime(b_date, "%Y-%m-%d")
            if closed_wd != '-1' and dt_obj.weekday() == int(closed_wd):
                return redirect(url_for("index", date=b_date, error="We're closed on this day of the week. Please pick another day."))

            if dt_obj.date() > (datetime.now(MY_TZ).date() + timedelta(days=max_advance_days)):
                return redirect(url_for("index", date=b_date, error=f"Bookings can only be made up to {max_advance_days} days in advance."))
                
            cursor.execute("SELECT duration, name FROM services WHERE id = %s", (service_id,))
            srv = cursor.fetchone()
            duration = srv["duration"] if srv else 30
            service_name = srv["name"] if srv else "Unknown service"
            
            start_dt = datetime.strptime(f"{b_date} {b_time}", "%Y-%m-%d %H:%M")
            end_dt = start_dt + timedelta(minutes=duration)
            start_str = start_dt.strftime("%Y-%m-%d %H:%M")
            end_str = end_dt.strftime("%Y-%m-%d %H:%M")

            # 检查该发型师这个时间段是否已被预约（含缓冲时间）
            if find_conflicting_appointment(cursor, stylist, start_str, end_str, buffer_minutes=buffer_minutes):
                return redirect(url_for("index", date=b_date, error="This stylist already has a booking at that time. Please choose another time or stylist."))
            
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

    notify_new_booking(
        f"🔔 New Booking!\n👤 {c_name} ({c_phone})\n💇 {service_name}\n💈 {stylist}\n🕐 {start_str} - {end_str.split(' ')[1]}"
    )
    return redirect(url_for("customer_portal", token=cust_token))

@app.route("/customer/<token>")
def customer_portal(token):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM customers WHERE token = %s", (token,))
            cust = cursor.fetchone()
            if not cust: return "Member page not found or invalid link", 404
            
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
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{{ cust.name }}'s Member Center - Dew Hair Salon</title>
            <link rel="preconnect" href="https://fonts.googleapis.com">
            <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
            <script src="https://cdn.tailwindcss.com"></script>
            <style>body { font-family: 'Inter', sans-serif; }</style>
        </head>
        <body class="bg-gray-50 min-h-screen p-4 md:p-8">
            <div class="max-w-2xl mx-auto space-y-5">
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-100 flex justify-between items-center">
                    <div>
                        <h1 class="text-xl font-extrabold text-gray-900">✨ Welcome, {{ cust.name }}</h1>
                        <p class="text-sm text-gray-400">{{ cust.phone }}</p>
                    </div>
                    <div class="text-right">
                        <div class="text-xs text-gray-400">Credit Balance</div>
                        <div class="text-xl font-bold text-green-600">RM {{ "%.2f"|format(cust.credits) }}</div>
                    </div>
                </div>

                <div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-100">
                    <h2 class="font-bold text-gray-900 mb-3">📅 My Appointments</h2>
                    <div class="space-y-2">
                        {% for a in appointments %}
                        <div class="border border-gray-100 p-3.5 rounded-xl flex justify-between items-center bg-gray-50">
                            <div>
                                <div class="font-semibold text-indigo-600 text-sm">{{ a.service_name }}</div>
                                <div class="text-xs text-gray-500">{{ a.start_time }} - {{ a.end_time.split()[1] }}</div>
                                <div class="text-xs text-gray-400">Stylist: {{ a.stylist }}</div>
                            </div>
                            <div class="text-right">
                                <span class="bg-green-100 text-green-700 px-2.5 py-1 rounded-full text-[11px] font-bold">{{ a.status }}</span>
                                {% if a.status == 'CONFIRMED' and a.start_time > now_str %}
                                <div class="mt-1.5">
                                    <a href="/appointment/cancel/{{ token }}/{{ a.id }}" onclick="return confirm('Cancel this appointment?')" class="text-red-500 text-[11px] font-bold hover:text-red-700">Cancel</a>
                                </div>
                                {% endif %}
                            </div>
                        </div>
                        {% else %}
                        <p class="text-gray-400 text-sm">No appointments yet</p>
                        {% endfor %}
                    </div>
                </div>
                <div class="text-center">
                    <a href="/" class="text-indigo-600 text-sm font-semibold hover:underline">+ Book a new appointment</a>
                </div>

                <div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-100">
                    <h2 class="font-bold text-gray-900 mb-3">🧾 Purchase History</h2>
                    <table class="w-full text-left text-sm">
                        <thead><tr class="border-b border-gray-100 text-gray-500 text-xs"><th class="p-2">Order No</th><th class="p-2">Date</th><th class="p-2">Item</th><th class="p-2">Amount</th><th class="p-2">Payment</th></tr></thead>
                        <tbody>
                            {% for o in orders %}
                            <tr class="border-b border-gray-50 {% if o.status == 'VOID' %}line-through text-gray-400 bg-red-50{% endif %}">
                                <td class="p-2 font-semibold">{{ o.order_no }}</td>
                                <td class="p-2">{{ o.created_at }}</td>
                                <td class="p-2">{{ o.item_name }}</td>
                                <td class="p-2 font-semibold">RM {{ "%.2f"|format(o.price) }}</td>
                                <td class="p-2">{{ 'Voided' if o.status == 'VOID' else o.payment_details }}</td>
                            </tr>
                            {% else %}
                            <tr><td colspan="5" class="p-3 text-gray-400">No orders yet</td></tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </body>
        </html>
    """, cust=cust, appointments=appointments, orders=orders, now_str=datetime.now(MY_TZ).strftime("%Y-%m-%d %H:%M"), token=token)

@app.route("/appointment/cancel/<token>/<int:appointment_id>")
def customer_cancel_appointment(token, appointment_id):
    """顾客在会员中心自助取消自己的预约（需要 token 与预约的顾客匹配才允许）"""
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT a.id FROM appointments a
                JOIN customers c ON a.customer_id = c.id
                WHERE a.id = %s AND c.token = %s AND a.status = 'CONFIRMED'
            """, (appointment_id, token))
            appt = cursor.fetchone()
            if appt:
                cursor.execute("UPDATE appointments SET status = 'CANCELLED' WHERE id = %s", (appointment_id,))
                conn.commit()
    return redirect(url_for("customer_portal", token=token))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
