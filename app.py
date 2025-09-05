from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import sqlite3
import os
import json
from datetime import datetime
import logging

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key')

# Конфигурация из переменных окружения Vercel
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_ADMIN_CHAT_ID = os.environ.get('TELEGRAM_ADMIN_CHAT_ID')

# Тестовые товары
products = [
    {'id': 1, 'name': 'Тестовый товар 1', 'price': 1000, 'description': 'Описание товара 1'},
    {'id': 2, 'name': 'Тестовый товар 2', 'price': 2000, 'description': 'Описание товара 2'},
    {'id': 3, 'name': 'Тестовый товар 3', 'price': 3000, 'description': 'Описание товара 3'}
]

# Инициализация базы данных (для Vercel используем временную БД)
def get_db_connection():
    if os.environ.get('VERCEL'):
        # На Vercel используем временную БД в памяти
        return sqlite3.connect(':memory:', check_same_thread=False)
    else:
        return sqlite3.connect('orders.db', check_same_thread=False)

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS orders
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  product_id INTEGER,
                  product_name TEXT,
                  price INTEGER,
                  customer_name TEXT,
                  customer_email TEXT,
                  card_number TEXT,
                  status TEXT DEFAULT 'pending',
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  telegram_message_id INTEGER)''')
    conn.commit()
    conn.close()

init_db()

# Телеграм бот (упрощенная версия для Vercel)
def send_telegram_notification(order_id, product_name, price, customer_name):
    """Отправка уведомления в Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ADMIN_CHAT_ID:
        print(f"Telegram notification would be sent: Order #{order_id} - {product_name} - {price} руб. - {customer_name}")
        return None
    
    try:
        import requests
        message = f"🛒 Новый заказ!\n\nID: #{order_id}\nТовар: {product_name}\nЦена: {price} руб.\nКлиент: {customer_name}\n\nПодтвердить: /approve_{order_id}\nОтклонить: /reject_{order_id}"
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': TELEGRAM_ADMIN_CHAT_ID,
            'text': message,
            'parse_mode': 'HTML'
        }
        
        response = requests.post(url, json=payload)
        response_data = response.json()
        
        if response_data.get('ok'):
            return response_data['result']['message_id']
        return None
        
    except Exception as e:
        print(f"Error sending Telegram message: {e}")
        return None

# Маршруты
@app.route('/')
def index():
    return render_template('index.html', products=products)

@app.route('/product/<int:product_id>')
def product_detail(product_id):
    product = next((p for p in products if p['id'] == product_id), None)
    if not product:
        return "Товар не найден", 404
    return render_template('product.html', product=product)

@app.route('/checkout/<int:product_id>', methods=['GET', 'POST'])
def checkout(product_id):
    product = next((p for p in products if p['id'] == product_id), None)
    if not product:
        return "Товар не найден", 404
    
    if request.method == 'POST':
        # Обработка формы оплаты
        name = request.form.get('name')
        email = request.form.get('email')
        card_number = request.form.get('card_number')
        expiry_date = request.form.get('expiry_date')
        cvv = request.form.get('cvv')
        
        # Простая валидация карты (тестовая)
        if not all([name, email, card_number, expiry_date, cvv]):
            return render_template('checkout.html', product=product, error='Заполните все поля')
        
        # Сохраняем заказ в БД
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('''INSERT INTO orders (product_id, product_name, price, customer_name, customer_email, card_number, status)
                     VALUES (?, ?, ?, ?, ?, ?, ?)''',
                 (product['id'], product['name'], product['price'], name, email, card_number[-4:], 'pending'))
        order_id = c.lastrowid
        conn.commit()
        
        # Отправляем уведомление в Telegram
        message_id = send_telegram_notification(order_id, product['name'], product['price'], name)
        
        if message_id:
            c.execute('UPDATE orders SET telegram_message_id = ? WHERE id = ?', (message_id, order_id))
            conn.commit()
        
        conn.close()
        
        session['order_id'] = order_id
        return redirect(url_for('payment_pending'))
    
    return render_template('checkout.html', product=product)

@app.route('/payment/pending')
def payment_pending():
    order_id = session.get('order_id')
    if not order_id:
        return redirect(url_for('index'))
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT status FROM orders WHERE id = ?', (order_id,))
    order = c.fetchone()
    conn.close()
    
    if order and order[0] != 'pending':
        return redirect(url_for('payment_result', status=order[0]))
    
    return render_template('payment_pending.html')

@app.route('/payment/result/<status>')
def payment_result(status):
    order_id = session.get('order_id')
    message = "Оплата успешно завершена!" if status == 'approved' else "Оплата отклонена."
    return render_template('payment_result.html', status=status, message=message, order_id=order_id)

# API для обработки действий из Telegram
@app.route('/api/order/<int:order_id>/<action>')
def handle_order_action(order_id, action):
    if action not in ['approve', 'reject']:
        return jsonify({'error': 'Invalid action'}), 400
    
    conn = get_db_connection()
    c = conn.cursor()
    
    if action == 'approve':
        c.execute('UPDATE orders SET status = ? WHERE id = ?', ('approved', order_id))
        status = 'approved'
    else:
        c.execute('UPDATE orders SET status = ? WHERE id = ?', ('rejected', order_id))
        status = 'rejected'
    
    conn.commit()
    conn.close()
    
    return jsonify({'status': 'success', 'order_id': order_id, 'action': action})

# Health check для Vercel
@app.route('/api/health')
def health_check():
    return jsonify({'status': 'ok', 'timestamp': datetime.now().isoformat()})

if __name__ == '__main__':
    app.run(debug=True)