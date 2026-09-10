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
                FOREIGN KEY(order_id) REFERENCES orders(id)
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                service_id INTEGER NOT NULL,
                stylist TEXT NOT NULL,
                start_time TEXT NOT NULL,
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
                ("专业修护洗发水", "Products", "洗护系列", 150.0, 0),
                ("强力定型喷雾", "Products", "造型系列", 98.0, 0),
                ("头皮滋养精华液", "Products", "特别护理", 280.0, 0),
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
                <a href="/admin" class="hover:bg-indigo-700 px-2 py-1 rounded">项目分类</a>
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
                <label class="block text-sm font-bold mb-1">选择时间段 (15分钟间隔)</label>
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
    with get_db() as conn:
        services = conn.execute("SELECT * FROM services ORDER BY category_type, sub_category").fetchall()
    return render_template_string(LAYOUT_TEMPLATE.replace("{% block content %}{% endblock %}", """
        <div class="bg-white p-6 rounded shadow">
            <h2 class="text-xl font-bold mb-4">项目与分类管理列表</h2>
            <table class="w-full text-left">
                <thead><tr class="border-b"><th class="p-2">主分类</th><th class="p-2">子分类</th><th class="p-2">名称</th><th class="p-2">价格</th></tr></thead>
                <tbody>
                    {% for item in services %}
                    <tr class="border-b"><td class="p-2 font-bold text-indigo-600">{{ item.category_type }}</td><td class="p-2">{{ item.sub_category }}</td><td class="p-2">{{ item.name }}</td><td class="p-2">￥{{ "%.2f"|format(item.price) }}</td></tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    """), services=services)

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
                        <label class="block text-sm font-medium mb-1">支付方式 (Split Payment)</label>
                        <select name="payment_method" class="w-full border rounded p-2">
                            <option value="Cash">Cash (现金)</option>
                            <option value="Credit Card">Credit Card (刷卡)</option>
                            <option value="QRPay">QRPay (扫码)</option>
                            <option value="Split Payment">Split Payment (组合拆分支付)</option>
                        </select>
                    </div>
                    <button class="w-full bg-green-600 text-white font-bold py-3 rounded hover:bg-green-700">完成收款并生成电子发票</button>
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
    cart_data = json.loads(request.form.get("cart_data", "[]"))
    name = request.form.get("customer_name")
    phone = request.form.get("customer_phone")
    pay_method = request.form.get("payment_method")
    total = sum(item["price"] for item in cart_data)
    order_no = "INV" + datetime.now().strftime("%Y%m%d%H%M%S")
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO customers (name, phone, token) VALUES (?, ?, ?)", (name, phone, secrets.token_hex(8)))
        cursor.execute("SELECT id FROM customers WHERE phone = ?", (phone,))
        cust_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO orders (order_no, customer_id, total_amount, payment_details, created_at) VALUES (?, ?, ?, ?, ?)", (order_no, cust_id, total, pay_method, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        order_id = cursor.lastrowid
        for item in cart_data:
            cursor.execute("INSERT INTO order_items (order_id, item_name, price) VALUES (?, ?, ?)", (order_id, item["name"], item["price"]))
    return f"""
        <div style="max-width:500px;margin:50px auto;padding:20px;border:1px solid #000;font-family:sans-serif;border-radius:8px;">
            <h2>Dew Hair Salon 官方电子发票</h2>
            <p>Reg No: 202503122676</p>
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

@app.route("/book", methods=["GET", "POST"])
def public_booking():
    if request.method == "POST":
        service_id = request.form.get("service_id")
        stylist = request.form.get("stylist")
        b_date = request.form.get("booking_date")
        b_time = request.form.get("booking_time")
        c_name = request.form.get("customer_name")
        c_phone = request.form.get("customer_phone")
        start_time_str = f"{b_date} {b_time}"
        token = secrets.token_hex(8)
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR IGNORE INTO customers (name, phone, token) VALUES (?, ?, ?)", (c_name, c_phone, token))
            cursor.execute("SELECT id, token FROM customers WHERE phone = ?", (c_phone,))
            cust = cursor.fetchone()
            cursor.execute("""
                INSERT INTO appointments (customer_id, service_id, stylist, start_time)
                VALUES (?, ?, ?, ?)
            """, (cust["id"], service_id, stylist, start_time_str))
        return f"""
            <div style="max-width:400px;margin:50px auto;text-align:center;font-family:sans-serif;padding:20px;border:1px solid #ddd;border-radius:8px;">
                <h2 style="color:green;">预约成功！</h2>
                <p>感谢您，{c_name}！您的预约已成功提交。</p>
                <p><strong>预约时间：</strong>{start_time_str}</p>
                <p><strong>发型师：</strong>{stylist}</p>
                <hr style="margin:20px 0;">
                <p>这是您的个人专属 Profile Link：</p>
                <a href="/customer/{cust['token']}" style="display:inline-block;padding:10px 15px;background:#4f46e5;color:white;text-decoration:none;border-radius:5px;font-weight:bold;">查看我的 Profile 专属页</a>
            </div>
        """
    timeslots = []
    start = datetime.strptime("10:00", "%H:%M")
    end = datetime.strptime("20:00", "%H:%M")
    while start <= end:
        timeslots.append(start.strftime("%H:%M"))
        start += timedelta(minutes=15)
    with get_db() as conn:
        services = conn.execute("SELECT * FROM services WHERE category_type='Services'").fetchall()
    return render_template_string(BOOKING_TEMPLATE, services=services, timeslots=timeslots)

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
            <p><strong>储值余额 (Credit Balance)：</strong> <span style="color:green;font-weight:bold;">￥{{ "%.2f"|format(cust.credits) }}</span></p>
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
            <h2 class="text-xl font-bold mb-4">预约记录列表</h2>
            <table class="w-full text-left border-collapse">
                <thead>
                    <tr class="border-b bg-gray-50">
                        <th class="p-2">预约时间</th>
                        <th class="p-2">顾客姓名</th>
                        <th class="p-2">电话</th>
                        <th class="p-2">服务项目</th>
                        <th class="p-2">发型师</th>
                        <th class="p-2">状态</th>
                    </tr>
                </thead>
                <tbody>
                    {% for app in appointments %}
                    <tr class="border-b">
                        <td class="p-2 font-bold">{{ app.start_time }}</td>
                        <td class="p-2">{{ app.customer_name }}</td>
                        <td class="p-2">{{ app.customer_phone }}</td>
                        <td class="p-2">{{ app.service_name }}</td>
                        <td class="p-2">{{ app.stylist }}</td>
                        <td class="p-2 text-green-600 font-bold">{{ app.status }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    """), appointments=appointments)

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
