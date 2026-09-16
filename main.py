import os
import sqlite3
import base64
from functools import wraps
from flask import Flask, request, jsonify, render_template, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "rubinov_ai_super_secret_key_change_me_in_production"

# ==========================================
#  ИНИЦИАЛИЗАЦИЯ И НАСТРОЙКА БАЗЫ ДАННЫХ
# ==========================================
DATABASE = 'rubinov_ai.db'

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        cursor = conn.cursor()
        # Таблица пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Таблица истории сообщений
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                image_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        conn.commit()

# Инициализируем БД при запуске
init_db()

# ==========================================
#  НАСТРОЙКА АВТОРИЗАЦИИ (Flask-Login)
# ==========================================
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    with get_db() as conn:
        user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
        if user:
            return User(id=user['id'], username=user['username'])
    return None

# ==========================================
#  МАРШРУТЫ АВТОРИЗАЦИИ И СТРАНИЦ
# ==========================================

@app.route('/')
@login_required
def index():
    # Отдаёт главный интерфейс только авторизованным пользователям
    return render_template('index.html', username=current_user.username)

@app.route('/register', methods=['POST'])
def register():
    data = request.get_json() or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    if not username or not password:
        return jsonify({'error': 'Заполните логин и пароль'}), 400

    hashed_password = generate_password_hash(password)

    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT INTO users (username, password_hash) VALUES (?, ?)', 
                           (username, hashed_password))
            conn.commit()
            user_id = cursor.lastrowid
        
        # Автоматический вход после регистрации
        user = User(id=user_id, username=username)
        login_user(user)
        return jsonify({'success': True, 'message': 'Успешная регистрация'})
    except sqlite3.IntegrityError:
        return jsonify({'error': 'Пользователь с таким именем уже существует'}), 400

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    with get_db() as conn:
        user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        if user and check_password_hash(user['password_hash'], password):
            user_obj = User(id=user['id'], username=user['username'])
            login_user(user_obj)
            return jsonify({'success': True, 'message': 'Успешный вход'})

    return jsonify({'error': 'Неверный логин или пароль'}), 401

@app.route('/logout', methods=['POST', 'GET'])
@login_required
def logout():
    logout_user()
    return jsonify({'success': True, 'message': 'Вы вышли из системы'})

@app.route('/api/user_status')
def user_status():
    if current_user.is_authenticated:
        return jsonify({'authenticated': True, 'username': current_user.username})
    return jsonify({'authenticated': False})

# ==========================================
#  ОСНОВНОЙ API-ЭНДПОИНТ ЧАТА И ИИ
# ==========================================

@app.route('/api/chat', methods=['POST'])
@login_required
def chat_handler():
    data = request.get_json() or {}
    prompt = data.get('prompt', '').strip()
    image_base64 = data.get('image', None)

    if not prompt and not image_base64:
        return jsonify({'error': 'Пустой запрос'}), 400

    try:
        # Сохраняем сообщение пользователя в БД
        with get_db() as conn:
            conn.execute('INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)',
                         (current_user.id, 'user', prompt))
            conn.commit()

        # 1. Сценарий: Генерация изображения
        if prompt.lower().startswith('нарисуй') or prompt.lower().startswith('draw'):
            clean_prompt = prompt.split(':', 1)[-1].strip() if ':' in prompt else prompt
            
            bot_response = f"Изображение по запросу «{clean_prompt}» сформировано."
            image_url = "https://picsum.photos/800/600"  # Заглушка (подключите здесь Imagen / DALL-E)

            # Сохраняем ответ бота в БД
            with get_db() as conn:
                conn.execute('INSERT INTO messages (user_id, role, content, image_url) VALUES (?, ?, ?, ?)',
                             (current_user.id, 'bot', bot_response, image_url))
                conn.commit()

            return jsonify({
                'response': bot_response,
                'image_url': image_url
            })

        # 2. Сценарий: Текстовый диалог (и анализ прикрепленного фото)
        else:
            bot_response = f"Ответ Rubinov AI для {current_user.username}: {prompt}"
            if image_base64:
                bot_response += " (Изображение успешно проанализировано)"

            # Сохраняем ответ бота в БД
            with get_db() as conn:
                conn.execute('INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)',
                             (current_user.id, 'bot', bot_response))
                conn.commit()

            return jsonify({
                'response': bot_response
            })

    except Exception as e:
        return jsonify({'error': f'Ошибка обработки на сервере: {str(e)}'}), 500

# Получение истории сообщений текущего пользователя
@app.route('/api/history', methods=['GET'])
@login_required
def get_history():
    with get_db() as conn:
        messages = conn.execute(
            'SELECT role, content, image_url, created_at FROM messages WHERE user_id = ? ORDER BY id ASC',
            (current_user.id,)
        ).fetchall()
        
        history = [dict(msg) for msg in messages]
        return jsonify({'history': history})

if __name__ == '__main__':
    # Зависимости перед запуском: pip install flask flask-login werkzeug
    app.run(host='0.0.0.0', port=5000, debug=True)
