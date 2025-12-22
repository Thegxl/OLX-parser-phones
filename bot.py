import time
import sqlite3
import cloudscraper
from bs4 import BeautifulSoup
import telebot
from telebot import types
import threading
from datetime import datetime, date

# ================= НАСТРОЙКИ =================
BOT_TOKEN = "8422144177:AAGbL1GHjC222JlvuJNVR2LzBD99hPCRUek"
CHAT_ID = 317720309
OLX_URL = "https://www.olx.ua/uk/elektronika/telefony-i-aksesuary/mobilnye-telefony-smartfony/?min_id=910054918&reason=observed_search&search%5Border%5D=created_at%3Adesc&search%5Bprivate_business%5D=private"
CHECK_INTERVAL = 65  
DB_NAME = "ads.db"
FIRST_RUN = True 
ADMIN_ID = 317720309
MIN_PRICE = 500    
MAX_PRICE = 15000    
EXCLUDE_WORDS = ["обмен", "запчасти", "копия", "r-sim", "блокировка", "id", "рассрочка", "магазин"]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
}
# =============================================

bot = telebot.TeleBot(BOT_TOKEN)
scraper = cloudscraper.create_scraper()

# ---------- БАЗА ДАННЫХ ----------
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS ads (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT UNIQUE, title TEXT, price TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                ads_today INTEGER DEFAULT 0,
                last_reset DATE DEFAULT CURRENT_DATE,
                premium_until DATE DEFAULT NULL
            )
        """)
        conn.commit()

def get_or_create_user(user_id):
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT ads_today, last_reset, premium_until FROM users WHERE user_id = ?", (user_id,))
            user = cursor.fetchone()
            today_str = date.today().isoformat()
            
            if not user:
                cursor.execute("INSERT INTO users (user_id, last_reset) VALUES (?, ?)", (user_id, today_str))
                conn.commit()
                return {"ads_today": 0, "is_premium": False}
            
            ads_today, last_reset, premium_until = user
            if last_reset != today_str:
                cursor.execute("UPDATE users SET ads_today = 0, last_reset = ? WHERE user_id = ?", (today_str, user_id))
                conn.commit()
                ads_today = 0
                
            is_premium = False
            if premium_until:
                try:
                    if datetime.strptime(premium_until, '%Y-%m-%d').date() >= date.today():
                        is_premium = True
                except: pass
            return {"ads_today": ads_today, "is_premium": is_premium}
    except: return {"ads_today": 0, "is_premium": False}

def increment_user_ads(user_id):
    try:
        with sqlite3.connect(DB_NAME) as conn:
            conn.execute("UPDATE users SET ads_today = ads_today + 1 WHERE user_id = ?", (user_id,))
            conn.commit()
    except: pass

def ad_exists(url):
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM ads WHERE url = ?", (url,))
            return cursor.fetchone() is not None
    except: return False

def save_ad(url, title, price):
    try:
        with sqlite3.connect(DB_NAME) as conn:
            conn.execute("INSERT OR IGNORE INTO ads (url, title, price) VALUES (?, ?, ?)", (url, title, price))
            conn.commit()
    except: pass

# ---------- ПАРСЕР ----------
def get_full_description(url):
    try:
        time.sleep(1) 
        res = scraper.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")
        tag = soup.find('div', {'data-testid': 'ad_description'})
        return tag.get_text(separator="\n", strip=True)[:400] + "..." if tag else "Описание не найдено"
    except: return "Ошибка загрузки описания"

def check_olx():
    global FIRST_RUN
    print(f"[{time.strftime('%H:%M:%S')}] Поиск...")
    try:
        resp = scraper.get(OLX_URL, headers=HEADERS, timeout=15)
        if resp.status_code != 200: return
        soup = BeautifulSoup(resp.text, "html.parser")
        ads = soup.find_all('div', {'data-cy': 'l-card'}) or soup.find_all('div', {'data-testid': 'ad-card'})
        
        for ad in ads:
            try:
                a_tag = ad.find('a', href=True)
                if not a_tag: continue
                url = a_tag['href'] if a_tag['href'].startswith('http') else 'https://www.olx.ua' + a_tag['href']
                clean_url = url.split('#')[0].split('?')[0]
                if ad_exists(clean_url): continue

                t_tag = ad.find('h6') or ad.find('h4')
                title = t_tag.get_text(strip=True) if t_tag else "Без названия"
                p_tag = ad.find('p', {'data-testid': 'ad-price'}) or ad.find('p', {'data-cy': 'ad-price'})
                price = p_tag.get_text(strip=True) if p_tag else "0"
                img = ad.find('img').get('src') if ad.find('img') else None

                if any(w in title.lower() for w in EXCLUDE_WORDS):
                    save_ad(clean_url, title, price); continue
                
                digits = ''.join(filter(str.isdigit, price))
                num_p = int(digits) if digits else 0
                if (MIN_PRICE > 0 and num_p < MIN_PRICE) or (MAX_PRICE > 0 and num_p > MAX_PRICE):
                    save_ad(clean_url, title, price); continue

                save_ad(clean_url, title, price)

                if not FIRST_RUN:
                    desc = get_full_description(clean_url)
                    markup = types.InlineKeyboardMarkup()
                    markup.add(types.InlineKeyboardButton(text="📱 Открыть на OLX", url=clean_url))
                    photo = f'<a href="{img}">\u200b</a>' if img else ""
                    msg = f"{photo}🚀<b>{title}</b>\n💰 Цена: <b>{price}</b>\n━━━━━━━━━━━━━━\n📝 <b>Описание:</b>\n<i>{desc}</i>\n\n🔗 <a href='{clean_url}'>Ссылка на сайт</a>"

                    with sqlite3.connect(DB_NAME) as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT user_id FROM users")
                        all_users = cursor.fetchall()

                    for (u_id,) in all_users:
                        u_data = get_or_create_user(u_id)
                        if u_data["is_premium"] or u_data["ads_today"] < 30:
                            try:
                                bot.send_message(u_id, msg, parse_mode="HTML", reply_markup=markup)
                                increment_user_ads(u_id)
                                time.sleep(0.1)
                            except: pass
            except: continue
        FIRST_RUN = False
    except: pass

# ---------- КОМАНДЫ ----------
@bot.message_handler(commands=['start', 'me', 'profile'])
def profile_handler(message):
    u_id = message.chat.id
    u_data = get_or_create_user(u_id)
    status = "👑 <b>Премиум</b>" if u_data["is_premium"] else "🎁 <b>Бесплатный</b>"
    limit = "Безлимит" if u_data["is_premium"] else f"{30 - u_data['ads_today']} из 30"
    
    text = (f"👤 <b>Ваш профиль</b>\n━━━━━━━━━━━━━━\n"
            f"🆔 ID: <code>{u_id}</code>\n"
            f"📊 Статус: {status}\n"
            f"📉 Лимит на сегодня: {limit}\n━━━━━━━━━━━━━━\n"
            f"💳 Для покупки подписки нажмите /buy \n"
            f" Объявления приходят автоматически, не нужно нажимать на кнопку старт."
)
    bot.send_message(u_id, text, parse_mode="HTML")

@bot.message_handler(commands=['buy'])
def buy_cmd(message):
    try:
        u_id = message.chat.id
        text = (
            "💎 <b>ТАРИФЫ ПОДПИСКИ</b>\n"
            "━━━━━━━━━━━━━━\n"
            "1️⃣ <b>Премиум на месяц</b>\n"
            "💰 Цена: <b>5$</b>\n"
            "⏳ Срок: 30 дней\n\n"
            "2️⃣ <b>Безлимит навсегда</b>\n"
            "💰 Цена: <b>50$</b>\n"
            "⏳ Срок: Пожизненно\n"
            "━━━━━━━━━━━━━━\n"
            f"💳 <b>Для оплаты:</b>\n"
            f"Пришлите скриншот оплаты и ваш ID: <code>{u_id}</code>\n\n"
            "🔗 Админ: @idrop_kr"
        )
        markup = types.InlineKeyboardMarkup()
        # Прямая ссылка на твой аккаунт
        markup.add(types.InlineKeyboardButton(text="📨 Написать админу", url="https://t.me/idrop_kr"))
        
        bot.send_message(u_id, text, parse_mode="HTML", reply_markup=markup)
    except Exception as e:
        print(f"Ошибка в команде buy: {e}")
# ---------- АДМИН КОМАНДЫ ----------
@bot.message_handler(commands=['give_prem'])
def give_prem_30(message):
    if message.chat.id == ADMIN_ID:
        try:
            target_id = int(message.text.split()[1])
            with sqlite3.connect(DB_NAME) as conn:
                conn.execute("UPDATE users SET premium_until = date('now', '+30 days') WHERE user_id = ?", (target_id,))
                conn.commit()
            bot.send_message(ADMIN_ID, f"✅ Премиум (30 дней) выдан для {target_id}")
            try: bot.send_message(target_id, "👑 Вам активирован Премиум на 30 дней!")
            except: pass
        except: bot.reply_to(message, "Пиши: /give_prem ID")

@bot.message_handler(commands=['give_forever'])
def give_prem_inf(message):
    if message.chat.id == ADMIN_ID:
        try:
            target_id = int(message.text.split()[1])
            with sqlite3.connect(DB_NAME) as conn:
                conn.execute("UPDATE users SET premium_until = date('now', '+99 years') WHERE user_id = ?", (target_id,))
                conn.commit()
            bot.send_message(ADMIN_ID, f"♾️ БЕЗЛИМИТ выдан для {target_id}")
            try: bot.send_message(target_id, "👑🔥 Вам активирован ПОЖИЗНЕННЫЙ БЕЗЛИМИТ!")
            except: pass
        except: bot.reply_to(message, "Пиши: /give_forever ID")

# ---------- ЗАПУСК ----------
def run_parser():
    while True:
        check_olx()
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    init_db()
    bot.set_my_commands([
        telebot.types.BotCommand("start", "Мой профиль"),
        telebot.types.BotCommand("buy", "Купить подписку"),
        telebot.types.BotCommand("me", "Проверить лимиты")
    ])
    print("Бот запущен...")
    threading.Thread(target=run_parser, daemon=True).start()
    bot.polling(none_stop=True)