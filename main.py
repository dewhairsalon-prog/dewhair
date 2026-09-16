import os
import psycopg2
import psycopg2.extras
import hmac
import secrets
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

# 严谨适配 Render 的 PostgreSQL 数据库连接
DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db():
    if not DATABASE_URL:
        raise RuntimeError("未检测到 DATABASE_URL 环境变量，请确保已在 Render 中正确绑定 PostgreSQL 数据库！")
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # 创建管理员表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admin (
            id SERIAL PRIMARY KEY,
            password TEXT NOT NULL
        )
    """)
    cursor.execute("SELECT COUNT(*) FROM admin")
    if cursor.fetchone()['count'] == 0:
        cursor.execute("INSERT INTO admin (password) VALUES (%s)", (ADMIN_PASSWORD,))
    
    # 创建服务项目表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS services (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            duration INTEGER NOT NULL
        )
    """)
    
    # 创建预约表 (Appointments)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS appointments (
            id SERIAL PRIMARY KEY,
            customer_name TEXT NOT NULL,
            customer_phone TEXT NOT NULL,
            service_id INTEGER REFERENCES services(id),
            appointment_date TEXT NOT NULL,
            appointment_time TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TEXT NOT NULL
        )
    """)
    
    conn.commit()
    cursor.close()
    conn.close()

# 登录验证装饰器
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

# 管理员登录路由
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    error = None
    if request.method == 'POST':
        pwd = request.form.get('password', '')
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT password FROM admin LIMIT 1")
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        
        saved_pwd = row['password'] if row else ADMIN_PASSWORD
        if hmac.compare_digest(pwd, saved_pwd):
            session['logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        else:
            error = "密码错误，请重试"
            
    return render_template_string("""
<!doctype html>
<html lang="zh">
<head>
    <meta charset="utf-8">
    <title>管理员登录</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: Arial, sans-serif; background: #f4f7f6; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .login-card { background: white; padding: 30px; border-radius: 8px; box-shadow: 0 4px 10px rgba(0,0,0,0.1); width: 100%; max-width: 350px; }
        h2 { text-align: center; color: #333; margin-bottom: 20px; }
        input[type="password"] { width: 100%; padding: 10px; margin-bottom: 15px; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; }
        button { width: 100%; padding: 10px; background: #007bff; border: none; color: white; font-size: 16px; border-radius: 4px; cursor: pointer; }
        button:hover { background: #0056b3; }
        .error { color: red; font-size: 14px; text-align: center; margin-bottom: 10px; }
    </style>
</head>
<body>
    <div class="login-card">
        <h2>后台登录</h2>
        {% if error %}
        <div class="error">{{ error }}</div>
        {% endif %}
        <form method="POST">
            <input type="password" name="password" placeholder="请输入管理员密码" required autofocus>
            <button type="submit">登录</button>
        </form>
    </div>
</body>
</html>
""", error=error)

@app.route('/admin/logout')
def admin_logout():
    session.pop('logged_in', None)
    return redirect(url_for('admin_login'))

@app.route('/admin')
@login_required
def admin_dashboard():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM services ORDER BY id DESC")
    services = cursor.fetchall()
    
    cursor.execute("""
        SELECT a.*, s.name as service_name 
        FROM appointments a 
        LEFT JOIN services s ON a.service_id = s.id 
        ORDER BY a.id DESC
    """)
    appointments = cursor.fetchall()
    cursor.close()
    conn.close()
    
    return render_template_string("""
<!doctype html>
<html lang="zh">
<head>
    <meta charset="utf-8">
    <title>管理员后台</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: Arial, sans-serif; background: #f8f9fa; margin: 0; padding: 20px; }
        .container { max-width: 950px; margin: auto; background: white; padding: 25px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
        h2, h3 { color: #333; }
        h2 { border-bottom: 2px solid #007bff; padding-bottom: 10px; }
        a { color: #007bff; text-decoration: none; }
        a:hover { text-decoration: underline; }
        .btn { display: inline-block; padding: 8px 15px; background: #007bff; color: white; border-radius: 4px; text-decoration: none; font-size: 14px; }
        .btn:hover { background: #0056b3; }
        table { width: 100%; border-collapse: collapse; margin-top: 15px; margin-bottom: 30px; }
        th, td { padding: 10px; border: 1px solid #ddd; text-align: left; font-size: 14px; }
        th { background: #f1f3f5; }
    </style>
</head>
<body>
    <div class="container">
        <h2>管理员仪表盘</h2>
        <p>欢迎回来！ | <a href="{{ url_for('admin_logout') }}">退出登录</a> | <a href="{{ url_for('index') }}" target="_blank">查看前台首页</a> | <a href="{{ url_for('admin_pos') }}">POS收银</a></p>
        <hr style="border: 0; border-top: 1px solid #eee; margin: 20px 0;">
        
        <h3>服务项目管理</h3>
        <a href="{{ url_for('admin_add_service') }}" class="btn">+ 添加新服务</a>
        <table>
            <tr>
                <th>ID</th>
                <th>服务名称</th>
                <th>价格 (RM)</th>
                <th>时长 (分钟)</th>
                <th>操作</th>
            </tr>
            {% for s in services %}
            <tr>
                <td>{{ s.id }}</td>
                <td>{{ s.name }}</td>
                <td>{{ s.price }}</td>
                <td>{{ s.duration }}</td>
                <td>
                    <a href="{{ url_for('admin_edit_service', service_id=s.id) }}">编辑</a> | 
                    <a href="{{ url_for('admin_delete_service', service_id=s.id) }}" onclick="return confirm('确定删除吗？')">删除</a>
                </td>
            </tr>
            {% endfor %}
        </table>

        <h3>客户预约管理</h3>
        <table>
            <tr>
                <th>ID</th>
                <th>姓名</th>
                <th>电话</th>
                <th>预约服务</th>
                <th>日期</th>
                <th>时间</th>
                <th>状态</th>
                <th>操作</th>
            </tr>
            {% for a in appointments %}
            <tr>
                <td>{{ a.id }}</td>
                <td>{{ a.customer_name }}</td>
                <td>{{ a.customer_phone }}</td>
                <td>{{ a.service_name }}</td>
                <td>{{ a.appointment_date }}</td>
                <td>{{ a.appointment_time }}</td>
                <td>{{ a.status }}</td>
                <td>
                    <a href="{{ url_for('admin_delete_appointment', appt_id=a.id) }}" onclick="return confirm('确定删除该预约吗？')">删除</a>
                </td>
            </tr>
            {% endfor %}
        </table>
    </div>
</body>
</html>
""", services=services, appointments=appointments)

# POS 页面（已完美恢复，不再 404）
@app.route('/admin/pos', methods=['GET', 'POST'])
@login_required
def admin_pos():
    conn = get_db()
    cursor = conn.cursor()
    success_msg = None
    
    if request.method == 'POST':
        customer_name = request.form.get('customer_name', '散客')
        customer_phone = request.form.get('customer_phone', '-')
        service_id = request.form.get('service_id')
        appointment_date = get_current_date()
        appointment_time = datetime.now(MY_TZ).strftime("%H:%M")
        created_at = get_current_time()
        
        cursor.execute("""
            INSERT INTO appointments (customer_name, customer_phone, service_id, appointment_date, appointment_time, status, created_at)
            VALUES (%s, %s, %s, %s, %s, '已完成(POS)', %s)
        """, (customer_name, customer_phone, service_id, appointment_date, appointment_time, created_at))
        conn.commit()
        success_msg = "POS 收银记账成功！"

    cursor.execute("SELECT * FROM services ORDER BY id DESC")
    services = cursor.fetchall()
    cursor.close()
    conn.close()
    
    return render_template_string("""
<!doctype html>
<html lang="zh">
<head>
    <meta charset="utf-8">
    <title>POS 收银台</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: Arial, sans-serif; background: #f8f9fa; padding: 20px; }
        .form-card { max-width: 450px; margin: auto; background: white; padding: 25px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
        input, select { width: 100%; padding: 10px; margin: 10px 0 20px 0; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; }
        button { background: #28a745; color: white; border: none; padding: 12px; width: 100%; border-radius: 4px; cursor: pointer; font-size: 16px; font-weight: bold; }
        button:hover { background: #218838; }
        .success { background: #d4edda; color: #155724; padding: 10px; border-radius: 4px; text-align: center; margin-bottom: 15px; }
        a { color: #007bff; text-decoration: none; }
    </style>
</head>
<body>
    <div class="form-card">
        <h2>⚡ 现场 POS 收银</h2>
        {% if success_msg %}
        <div class="success">{{ success_msg }}</div>
        {% endif %}
        <form method="POST">
            <label>顾客姓名：</label>
            <input type="text" name="customer_name" value="散客" required>
            <label>联系电话：</label>
            <input type="text" name="customer_phone" value="-">
            <label>选择服务项目：</label>
            <select name="service_id" required>
                <option value="">-- 请选择服务 --</option>
                {% for s in services %}
                <option value="{{ s.id }}">{{ s.name }} (RM {{ s.price }} / {{ s.duration }}分钟)</option>
                {% endfor %}
            </select>
            <button type="submit">完成结账并记录</button>
        </form>
        <p style="text-align: center; margin-top: 15px;"><a href="{{ url_for('admin_dashboard') }}">返回仪表盘</a></p>
    </div>
</body>
</html>
""", services=services, success_msg=success_msg)

@app.route('/admin/services/add', methods=['GET', 'POST'])
@login_required
def admin_add_service():
    if request.method == 'POST':
        name = request.form.get('name')
        price = request.form.get('price')
        duration = request.form.get('duration')
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO services (name, price, duration) VALUES (%s, %s, %s)", (name, price, duration))
        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for('admin_dashboard'))
        
    return render_template_string("""
<!doctype html>
<html lang="zh">
<head>
    <meta charset="utf-8">
    <title>添加服务</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: Arial, sans-serif; background: #f8f9fa; padding: 20px; }
        .form-card { max-width: 400px; margin: auto; background: white; padding: 25px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
        input { width: 100%; padding: 10px; margin: 10px 0 20px 0; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; }
        button { background: #28a745; color: white; border: none; padding: 10px 15px; width: 100%; border-radius: 4px; cursor: pointer; font-size: 16px; }
        button:hover { background: #218838; }
        a { color: #6c757d; text-decoration: none; }
    </style>
</head>
<body>
    <div class="form-card">
        <h2>添加新服务</h2>
        <form method="POST">
            <label>服务名称：</label>
            <input type="text" name="name" required>
            <label>价格 (RM)：</label>
            <input type="number" step="0.01" name="price" required>
            <label>时长 (分钟)：</label>
            <input type="number" name="duration" required>
            <button type="submit">保存</button>
        </form>
        <p style="text-align: center; margin-top: 15px;"><a href="{{ url_for('admin_dashboard') }}">返回仪表盘</a></p>
    </div>
</body>
</html>
""")

@app.route('/admin/services/edit/<int:service_id>', methods=['GET', 'POST'])
@login_required
def admin_edit_service(service_id):
    conn = get_db()
    cursor = conn.cursor()
    
    if request.method == 'POST':
        name = request.form.get('name')
        price = request.form.get('price')
        duration = request.form.get('duration')
        
        cursor.execute("UPDATE services SET name = %s, price = %s, duration = %s WHERE id = %s", (name, price, duration, service_id))
        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for('admin_dashboard'))
        
    cursor.execute("SELECT * FROM services WHERE id = %s", (service_id,))
    service = cursor.fetchone()
    cursor.close()
    conn.close()
    
    if not service:
        return "服务不存在", 404
        
    return render_template_string("""
<!doctype html>
<html lang="zh">
<head>
    <meta charset="utf-8">
    <title>编辑服务</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: Arial, sans-serif; background: #f8f9fa; padding: 20px; }
        .form-card { max-width: 400px; margin: auto; background: white; padding: 25px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
        input { width: 100%; padding: 10px; margin: 10px 0 20px 0; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; }
        button { background: #ffc107; color: #212529; border: none; padding: 10px 15px; width: 100%; border-radius: 4px; cursor: pointer; font-size: 16px; font-weight: bold; }
        button:hover { background: #e0a800; }
        a { color: #6c757d; text-decoration: none; }
    </style>
</head>
<body>
    <div class="form-card">
        <h2>编辑服务</h2>
        <form method="POST">
            <label>服务名称：</label>
            <input type="text" name="name" value="{{ service.name }}" required>
            <label>价格 (RM)：</label>
            <input type="number" step="0.01" name="price" value="{{ service.price }}" required>
            <label>时长 (分钟)：</label>
            <input type="number" name="duration" value="{{ service.duration }}" required>
            <button type="submit">更新修改</button>
        </form>
        <p style="text-align: center; margin-top: 15px;"><a href="{{ url_for('admin_dashboard') }}">返回仪表盘</a></p>
    </div>
</body>
</html>
""", service=service)

@app.route('/admin/services/delete/<int:service_id>')
@login_required
def admin_delete_service(service_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM services WHERE id = %s", (service_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/appointments/delete/<int:appt_id>')
@login_required
def admin_delete_appointment(appt_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM appointments WHERE id = %s", (appt_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect(url_for('admin_dashboard'))

# 顾客前台首页预约
@app.route('/', methods=['GET', 'POST'])
def index():
    conn = get_db()
    cursor = conn.cursor()
    success_msg = None
    
    if request.method == 'POST':
        customer_name = request.form.get('customer_name')
        customer_phone = request.form.get('customer_phone')
        service_id = request.form.get('service_id')
        appointment_date = request.form.get('appointment_date')
        appointment_time = request.form.get('appointment_time')
        created_at = get_current_time()
        
        cursor.execute("""
            INSERT INTO appointments (customer_name, customer_phone, service_id, appointment_date, appointment_time, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (customer_name, customer_phone, service_id, appointment_date, appointment_time, created_at))
        conn.commit()
        success_msg = "预约提交成功！我们会尽快与您联系。"

    cursor.execute("SELECT * FROM services ORDER BY id DESC")
    services = cursor.fetchall()
    cursor.close()
    conn.close()
    
    return render_template_string("""
<!doctype html>
<html lang="zh">
<head>
    <meta charset="utf-8">
    <title>Dew Hair Salon - 在线预约</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: Arial, sans-serif; background: #fdfcfb; margin: 0; padding: 20px; color: #333; }
        .main-card { max-width: 500px; margin: 30px auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 4px 15px rgba(0,0,0,0.08); }
        h2 { text-align: center; color: #2c3e50; margin-bottom: 25px; }
        label { display: block; margin-top: 15px; font-weight: bold; font-size: 14px; }
        input, select { width: 100%; padding: 10px; margin-top: 5px; border: 1px solid #ddd; border-radius: 5px; box-sizing: border-box; }
        button { background: #e67e22; color: white; border: none; padding: 12px; width: 100%; border-radius: 5px; cursor: pointer; font-size: 16px; margin-top: 25px; font-weight: bold; }
        button:hover { background: #d35400; }
        .success { background: #d4edda; color: #155724; padding: 12px; border-radius: 5px; text-align: center; margin-bottom: 20px; border: 1px solid #c3e6cb; }
        .admin-link { text-align: center; margin-top: 20px; font-size: 13px; }
        .admin-link a { color: #7f8c8d; text-decoration: none; }
    </style>
</head>
<body>
    <div class="main-card">
        <h2>✂️ Dew Hair Salon 线上预约</h2>
        {% if success_msg %}
        <div class="success">{{ success_msg }}</div>
        {% endif %}
        <form method="POST">
            <label>您的姓名：</label>
            <input type="text" name="customer_name" required placeholder="请输入您的称呼">
            
            <label>联系电话：</label>
            <input type="text" name="customer_phone" required placeholder="请输入电话号码">
            
            <label>选择美发服务：</label>
            <select name="service_id" required>
                <option value="">-- 请选择服务项目 --</option>
                {% for s in services %}
                <option value="{{ s.id }}">{{ s.name }} (RM {{ s.price }} / {{ s.duration }}分钟)</option>
                {% endfor %}
            </select>
            
            <label>预约日期：</label>
            <input type="date" name="appointment_date" required>
            
            <label>预约时间：</label>
            <input type="time" name="appointment_time" required>
            
            <button type="submit">提交预约</button>
        </form>
        <div class="admin-link">
            <a href="{{ url_for('admin_login') }}">管理员登录后台</a>
        </div>
    </div>
</body>
</html>
""", services=services, success_msg=success_msg)

@app.before_request
def before_first_request():
    if not getattr(app, '_got_first_request', False):
        try:
            init_db()
        except Exception as e:
            print(f"数据库初始化提示: {e}")
        app._got_first_request = True

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
