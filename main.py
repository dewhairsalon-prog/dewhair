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
        # 默认营业时间设定
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('open_time', '10:00')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('close_time', '20:00')")
        
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM services")
        if cursor.fetchone()[0] == 0:
            sample_services = [
                ("高级总监剪发", "Services", "剪发", 120.0, 45),
                ("植物精油染发", "Services", "染发", 380.0, 90),
                ("蛋白修护烫发", "Services", "烫发", 450.0, 120),
                ("深度发膜护理", "Services", "护理", 260.0, 60),
                ("防脱头皮理疗", "Services", "头皮理疗", 320.0, 60),
                ("日常洗吹造型", "Services", "造型/其他", 60.0, 30),
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
            <div class="flex space-x-3 text-sm font-bold">
                <a href="/admin/pos" class="hover:bg-indigo-700 px-2 py-1 rounded">POS 收银台</a>
                <a href="/admin/appointments" class="hover:bg-indigo-700 px-2 py-1 rounded">预约管理</a>
                <a href="/admin/orders" class="hover:bg-indigo-700 px-2 py-1 rounded">历史订单</a>
                <a href="/admin" class="hover:bg-indigo-700 px-2 py-1 rounded">项目与营业设置</a>
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
        <h2 class="text-2xl font-bold text-center text-indigo-600 mb-6">Dew Hair Salon 在线预约</h2>
        {% if error %}
        <div class="mb-4 p-3 bg-red-100 text-red-700 rounded text-sm font-bold">{{ error }}</div>
        {% endif %}
        <form action="/book" method="POST">
            <div class="mb-4">
                <label class="block text-sm font-bold mb-1">选择服务项目 (自动计算耗时)</label>
                <select name="service_id" class="w-full border rounded p-2" required>
                    {% for item in services %}
                    <option value="{{ item.id }}">{{ item.name }} - ￥{{ "%.2f"|format(item.price) }} (耗时: {{ item.duration }}分钟)</option>
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
                <label class="block text-sm font-bold mb-1">选择开始时间段 (营业时间: {{ open_time }} - {{ close_time }})</label>
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
                <label class="block text-sm font-bold mb-1">您的电话号码</label>
                <input type="text" name="customer_phone" class="w-full border rounded p-2" required>
            </div>
            <button class="w-full bg-indigo-600 text-white font-bold py-3 rounded hover:bg-indigo-700">提交预约 (自动排期防冲突)</button>
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
    with get_db() as conn:
        services = conn.execute("SELECT * FROM services ORDER BY category_type, sub_category").fetchall()
    return render_template_string(LAYOUT_TEMPLATE.replace("{% block content %}{% endblock %}", """
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div class="md:col-span-2 space-y-6">
                <div class="bg-white p-6 rounded shadow">
                    <h2 class="text-xl font-bold mb-4">营业时间设置 (Settings)</h2>
                    <form action="/admin/settings/update" method="POST" class="grid grid-cols-3 gap-4 items-end">
                        <div>
                            <label class="block text-sm font-medium">开门时间</label>
                            <input type="time" name="open_time" value="{{ open_time }}" class="w-full border rounded p-2" required>
                        </div>
                        <div>
                            <label class="block text-sm font-medium">关门时间</label>
                            <input type="time" name="close_time" value="{{ close_time }}" class="w-full border rounded p-2" required>
                        </div>
                        <div>
                            <button class="w-full bg-indigo-600 text-white font-bold py-2 rounded hover:bg-indigo-700">保存设置</button>
                        </div>
                    </form>
                </div>

                <div class="bg-white p-6 rounded shadow">
                    <h2 class="text-xl font-bold mb-4">项目与耗时管理列表</h2>
                    <table class="w-full text-left">
                        <thead><tr class="border-b"><th class="p-2">分类</th><th class="p-2">名称</th><th class="p-2">价格</th><th class="p-2">耗时(分钟)</th><th class="p-2">操作</th></tr></thead>
                        <tbody>
                            {% for item in services %}
                            <tr class="border-b">
                                <td class="p-2 font-bold text-indigo-600">{{ item.category_type }}</td>
                                <td class="p-2">{{ item.name }}</td>
                                <td class="p-2">￥{{ "%.2f"|format(item.price) }}</td>
                                <td class="p-2 font-bold text-green-600">{{ item.duration }}分钟</td>
                                <td class="p-2">
                                    <a href="/admin/service/delete/{{ item.id }}" onclick="return confirm('确定要删除吗？')" class="text-red-500 text-sm font-bold">删除</a>
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>

            <div class="bg-white p-6 rounded shadow h-fit">
                <h2 class="text-xl font-bold mb-4">添加服务/产品与耗时</h2>
                <form action="/admin/service/add" method="POST">
                    <div class="mb-3">
                        <label class="block text-sm font-medium">项目名称</label>
                        <input type="text" name="name" class="w-full border rounded p-2" required>
                    </div>
                    <div class="mb-3">
                        <label class="block text-sm font-medium">主分类</label>
                        <select name="category_type" class="w-full border rounded p-2">
                            <option value="Services">Services (服务项目)</option>
                            <option value="Products">Products (零售产品)</option>
                        </select>
                    </div>
                    <div class="mb-3">
                        <label class="block text-sm font-medium">子分类</label>
                        <input type="text" name="sub_category" class="w-full border rounded p-2" value="常规项目" required>
                    </div>
                    <div class="mb-3">
                        <label class="block text-sm font-medium">价格 (￥)</label>
                        <input type="number" step="0.01" name="price" class="w-full border rounded p-2" required>
                    </div>
                    <div class="mb-4">
                        <label class="block text-sm font-medium">耗时 (分钟)</label>
                        <input type="number" name="duration" class="w-full border rounded p-2" value="30" required>
                    </div>
                    <button class="w-full bg-indigo-600 text-white font-bold py-2 rounded hover:bg-indigo-700">确认添加</button>
                </form>
            </div>
        </div>
    """), services=services, open_time=open_time, close_time=close_time)

@app.route("/admin/settings/update", methods=["POST"])
@admin_required
def update_settings():
    open_time = request.form.get("open_time", "10:00")
    close_time = request.form.get("close_time", "20:00")
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('open_time', ?)", (open_time,))
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('close_time', ?)", (close_time,))
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
                        <th class="p-2">开始时间</th>
                        <th class="p-2">结束时间 (自动计算)</th>
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
                        <td class="p-2 font-bold">{{ app.start_time }}</td>
                        <td class="p-2 text-indigo-600">{{ app.end_time }}</td>
                        <td class="p-2">{{ app.customer_name }}</td>
                        <td class="p-2">{{ app.customer_phone }}</td>
                        <td class="p-2">{{ app.service_name }}</td>
                        <td class="p-2">{{ app.stylist }}</td>
                        <td class="p-2">
                            <span class="text-green-600 font-bold mr-2">{{ app.status }}</span>
                            <a href="/admin/appointment/delete/{{ app.id }}" onclick="return confirm('确定取消/删除此预约吗？')" class="text-red-500 text-sm font-bold">删除</a>
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
                            <a href="/admin/order/delete/{{ order.id }}" onclick="return confirm('确定要删除/作废此订单吗？')" class="text-red-500 font-bold text-sm">删除订单</a>
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
    return render_template_string(LAYOUT_TEMPLATE.replace("{% block content %}{% endblock %}", """
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div class="md:col-span-2 bg-white p-6 rounded-lg shadow">
                <h2 class="text-xl font-bold mb-4">点选服务 / 产品 (POS)</h2>
                <div class="grid grid-cols-3 gap-4">
                    {% for item in services %}
                    <button onclick="addToOrder('{{ item.name }}', {{ item.price }})" class="p-4 border rounded hover:bg-indigo-50 text-left">
                        <div class="text-xs text-indigo-600 font-bold">{{ item.category_type }} - {{ item.sub_category }}</div>
                        <div class="font-bold text-lg">{{ item.name }}</div>
                        <div class="text-gray-600">￥{{ "%.2f"|format(item.price) }}</div>
                    </button>
                    {% endfor %}
                </div>
            </div>
            <div class="bg-white p-6 rounded-lg shadow">
                <h2 class="text-xl font-bold mb-4">当前订单结账</h2>
                <div id="order-items" class="min-h-[180px] border-b mb-4">
                    <p class="text-gray-400">点击左侧项目加入订单</p>
                </div>
                <div class="text-xl font-bold mb-4">
                    总金额: <span id="total-amount" class="text-red-600">￥0.00</span>
                </div>
                <form action="/admin/checkout" method="POST">
                    <input type="hidden" name="cart_data" id="cart_data_input">
                    <div class="mb-3">
                        <label class="block text-sm font-medium">顾客姓名</label>
                        <input type="text" name="customer_name" class="w-full border rounded p-2" required>
                    </div>
                    <div class="mb-3">
                        <label class="block text-sm font-medium">顾客电话</label>
                        <input type="text" name="customer_phone" class="w-full border rounded p-2" required>
                    </div>
                    <div class="mb-4">
                        <label class="block text-sm font-medium mb-1">支付方式</label>
                        <select name="payment_method" class="w-full border rounded p-2">
                            <option value="Cash">Cash (现金)</option>
                            <option value="Credit Card">Credit Card (刷卡)</option>
                            <option value="QRPay">QRPay (扫码)</option>
                            <option value="Split Payment">Split Payment (组合支付)</option>
                        </select>
                    </div>
                    <button class="w-full bg-green-600 text-white font-bold py-3 rounded hover:bg-green-700">完成收款并生成发票</button>
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
        </script>
    """), services=services)

@app.route("/admin/checkout", methods=["POST"])
@admin_required
def checkout():
    try:
        cart_data = json.loads(request.form.get("cart_data", "[]"))
        name = request.form.get("customer_name")
        phone = request.form.get("customer_phone")
        pay_method = request.form.get("payment_method")
        total = sum(item["price"] for item in cart_data)
        order_no = "INV" + datetime.now().strftime("%Y%m%d%H%M%S")
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, token FROM customers WHERE phone = ?", (phone,))
            cust = cursor.fetchone()
            if cust:
                cust_id = cust["id"]
                cust_token = cust["token"]
            else:
                cust_token = secrets.token_hex(8)
                cursor.execute("INSERT INTO customers (name, phone, token) VALUES (?, ?, ?)", (name, phone, cust_token))
                cust_id = cursor.lastrowid
                
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
                <h2>Dew Hair Salon 官方电子发票</h2>
                <hr>
                <p><strong>发票单号：</strong> {order_no}</p>
                <p><strong>顾客姓名：</strong> {name} ({phone})</p>
                <p><strong>支付金额：</strong> ￥{total:.2f}</p>
                <p><strong>支付方式：</strong> {pay_method}</p>
                <hr>
                <a href="https://wa.me/{phone}?text=感谢光临 Dew Hair Salon！您的电子发票单号：{order_no}，总金额：￥{total:.2f}" target="_blank" style="display:inline-block;padding:10px 15px;background:#25D366;color:white;text-decoration:none;border-radius:5px;font-weight:bold;">一键发送 WhatsApp 发票</a>
                <br><br>
                <a href="/admin/pos" style="color:#4f46e5;font-weight:bold;text-decoration:none;">返回 POS 收银台</a>
            </div>
        """
    except Exception as e:
        return f"结账发生错误: {str(e)}", 500

@app.route("/book", methods=["GET", "POST"])
def public_booking():
    open_time_str = get_setting("open_time", "10:00")
    close_time_str = get_setting("close_time", "20:00")
    
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
            
            # 防冲突检查：检查该发型师在同一时间段内是否已有预约交叉
            conflict = conn.execute("""
                SELECT id FROM appointments 
                WHERE stylist = ? AND status = 'CONFIRMED' 
                AND start_time < ? AND end_time > ?
            """, (stylist, end_str, start_str)).fetchone()
            
            if conflict:
                # 重新加载预约页面并提示错误
                services = conn.execute("SELECT * FROM services WHERE category_type='Services'").fetchall()
                timeslots = []
                st = datetime.strptime(open_time_str, "%H:%M")
                et = datetime.strptime(close_time_str, "%H:%M")
                while st <= et:
                    timeslots.append(st.strftime("%H:%M"))
                    st += timedelta(minutes=15)
                return render_template_string(BOOKING_TEMPLATE, services=services, timeslots=timeslots, open_time=open_time_str, close_time=close_time_str, error=f"预约失败：发型师 {stylist} 在此时间段已有其他预约，时间冲突，请选择其他时间！")

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
                <p>感谢您，{c_name}！您的预约已成功提交并自动排期。</p>
                <p><strong>项目排期：</strong>{start_str} ~ {end_str.split()[1]}</p>
                <p><strong>发型师：</strong>{stylist}</p>
                <hr style="margin:20px 0;">
                <a href="/customer/{cust_token}" style="display:inline-block;padding:10px 15px;background:#4f46e5;color:white;text-decoration:none;border-radius:5px;font-weight:bold;">查看我的 Profile 专属页</a>
            </div>
        """
        
    timeslots = []
    st = datetime.strptime(open_time_str, "%H:%M")
    et = datetime.strptime(close_time_str, "%H:%M")
    while st <= et:
        timeslots.append(st.strftime("%H:%M"))
        st += timedelta(minutes=15)
        
    with get_db() as conn:
        services = conn.execute("SELECT * FROM services WHERE category_type='Services'").fetchall()
    return render_template_string(BOOKING_TEMPLATE, services=services, timeslots=timeslots, open_time=open_time_str, close_time=close_time_str, error=None)

@app.route("/customer/<token>")
def customer_profile(token):
    with get_db() as conn:
        cust = conn.execute("SELECT * FROM customers WHERE token = ?", (token,)).fetchone()
        if not cust:
            return "无效的顾客专属 Link", 404
        orders = conn.execute("""
            SELECT o.*, i.item_name, i.price FROM orders o 
            LEFT JOIN order_items i ON o.id = i.order_id 
            WHERE o.customer_id = ? 
            ORDER BY o.created_at DESC
        """, (cust["id"],)).fetchall()
    return render_template_string("""
        <div style="max-width:500px;margin:30px auto;padding:20px;border:1px solid #ccc;font-family:sans-serif;border-radius:8px;">
            <h2 style="color:#4f46e5;">Dew Hair Salon - 顾客专属个人中心</h2>
            <p><strong>姓名：</strong> {{ cust.name }}</p>
            <p><strong>电话：</strong> {{ cust.phone }}</p>
            <p><strong>储值余额：</strong> <span style="color:green;font-weight:bold;">￥{{ "%.2f"|format(cust.credits) }}</span></p>
            <hr>
            <h3>历史消费与服务记录</h3>
            {% if orders %}
            <ul style="padding-left:20px;">
                {% for o in orders %}
                <li style="margin-bottom: 6px;">{{ o.created_at }} - <strong>{{ o.item_name }}</strong> (￥{{ "%.2f"|format(o.price) }})</li>
                {% endfor %}
            </ul>
            {% else %}
            <p style="color:gray;">暂无历史消费记录。</p>
            {% endif %}
        </div>
    """, cust=cust, orders=orders)

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
                    <div class="text-gray-500 text-sm">总营业时间总额</div>
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
