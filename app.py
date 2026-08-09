import os
import sys
import time
import json
import random
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
import telebot
from tradingview_ta import TA_Handler, Interval

# ===========================================================
# 1. الإعدادات الأساسية والمنطقة الزمنية
# ===========================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

CAIRO_TZ = ZoneInfo("Africa/Cairo")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8222819132:AAFmMjXCVnUFU8JUEcsujHKVjdmrJ1_zzPg")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "5418506244")

bot = telebot.TeleBot(BOT_TOKEN)

DATA_FILE = "daily_history.json"
PENDING_FILE = "pending_signals.json"
TRADES_FILE = "today_trades.json"
CONFIG_FILE = "strategy_config.json"  # ملف المعايير الذكية المتعلمة

audit_sent_today = False
last_audit_date = None

EGX33_SYMBOLS_MAP = {
    "ABUK": "ABUK.CA (أبو قير للأسمدة)", "MFPC": "MFPC.CA (موبكو)",
    "SKPC": "SKPC.CA (سيدبك)", "AMOC": "AMOC.CA (أموك)",
    "MBSC": "MBSC.CA (مصر بني سويف للأسمنت)", "SCEM": "SCEM.CA (سيناء للأسمنت)",
    "TMGH": "TMGH.CA (طلعت مصطفى)", "OCDI": "OCDI.CA (سوديك)",
    "MASR": "MASR.CA (مدينة مصر)", "EMFD": "EMFD.CA (إعمار مصر)",
    "ORAS": "ORAS.CA (أوراسكوم للإنشاء)", "ORHD": "ORHD.CA (أوراسكوم التنمية)",
    "HELI": "HELI.CA (مصر الجديدة للإسكان)", "CLHO": "CLHO.CA (كليوباترا)",
    "ISPH": "ISPH.CA (ابن سينا فارما)", "RMDA": "RMDA.CA (العاشر من رمضان - رميدا)",
    "PHAR": "PHAR.CA (إيبارشيو - فاركو)", "JUFO": "JUFO.CA (جهينة)",
    "OLFI": "OLFI.CA (عبور لاند)", "SUGR": "SUGR.CA (الدلتا للسكر)",
    "EFID": "EFID.CA (إدفيتا)", "EFIH": "EFIH.CA (إي فاينانس)",
    "FWRY": "FWRY.CA (فوري)", "ETEL": "ETEL.CA (المصرية للاتصالات)",
    "ALCN": "ALCN.CA (القناة للتوكيلات)", "CSAG": "CSAG.CA (القاهرة للزيوت)",
    "ORWE": "ORWE.CA (النساجون الشرقيون)", "ARAB": "ARAB.CA (عربية حجيج)",
    "CICH": "CICH.CA (سي آي كابيتال)", "EALR": "EALR.CA (مصر للألومنيوم)"
}

STOCKS = list(EGX33_SYMBOLS_MAP.keys())

# ===========================================================
# 2. دوال التخزين وإدارة استراتيجية التعلم
# ===========================================================
def load_json(file_path, default=None):
    if default is None: default = {}
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return default
    return default

def save_json(file_path, data):
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"فشل حفظ {file_path}: {e}")

def get_learned_config():
    """تحميل إعدادات الاستراتيجية المكيّفة تلقائياً"""
    default_config = {
        "min_rvol_trend": 1.1,
        "min_score": 65,
        "rsi_min": 45.0,
        "rsi_max": 68.0,
        "learned_days": 0
    }
    return load_json(CONFIG_FILE, default_config)

# ===========================================================
# 3. جلب بيانات الأسهم والتوقيت
# ===========================================================
def fetch_stock_data_safe(symbol, max_retries=3):
    for attempt in range(max_retries):
        try:
            handler = TA_Handler(
                symbol=symbol,
                screener="egypt",
                exchange="EGX",
                interval=Interval.INTERVAL_15_MINUTES
            )
            analysis = handler.get_analysis()
            ind = analysis.indicators

            close = ind.get("close", 0)
            open_p = ind.get("open", 0)
            volume = ind.get("volume", 0)
            rsi = ind.get("RSI", 50)
            ema25 = ind.get("EMA25", 0)
            ema50 = ind.get("EMA50", 0)
            macd = ind.get("MACD.macd", 0)
            macd_signal = ind.get("MACD.signal", 0)

            change_pct = ((close - open_p) / open_p * 100) if open_p > 0 else 0
            volume_sma20 = ind.get("volume.SMA20", volume)
            rvol = (volume / volume_sma20) if (volume_sma20 and volume_sma20 > 0) else 1.0

            return {
                "symbol": symbol,
                "close": close,
                "change_pct": change_pct,
                "rsi": rsi,
                "rvol": rvol,
                "ema25": ema25,
                "ema50": ema50,
                "macd": macd,
                "macd_signal": macd_signal,
                "is_green": close > open_p
            }
        except Exception as e:
            if "429" in str(e) or "Too Many Requests" in str(e):
                wait_time = (attempt + 1) * 2.0 + random.uniform(0.5, 1.5)
                time.sleep(wait_time)
            else:
                logging.error(f"❌ خطأ في جلب {symbol}: {e}")
                break
    return None

def is_market_open():
    """التحقق التام من توقيت البورصة المصرية (10:00 إلى 14:30) من الأحد للخميس"""
    now_cairo = datetime.now(CAIRO_TZ)
    if now_cairo.weekday() in [4, 5]: # الجمعة والسبت عطلة
        return False
    
    start_time = now_cairo.replace(hour=10, minute=0, second=0, microsecond=0)
    end_time = now_cairo.replace(hour=14, minute=30, second=0, microsecond=0)
    return start_time <= now_cairo <= end_time

# ===========================================================
# 4. محرك التقييم الديناميكي ومحرك التعلم اليومي
# ===========================================================
def evaluate_stock(data, current_time, config):
    rsi = data["rsi"]
    rvol = data["rvol"]
    change_pct = data["change_pct"]
    close = data["close"]
    
    if change_pct >= 8.5:
        return {"type": "None", "score": 0}

    # مسار الانفجار الفوري
    if rvol >= 2.5 and change_pct >= 1.5 and (50.0 <= rsi <= 72.0):
        return {"type": "Super Breakout 🚀", "score": 95, "instant": True}

    # التقييم بناءً على المعايير الديناميكية المتعلمة
    score = 0
    if config["rsi_min"] <= rsi <= config["rsi_max"]:
        score += 30
    
    if close > data["ema25"]: score += 20
    if close > data["ema50"]: score += 20
    if data["macd"] > data["macd_signal"]: score += 15
    if data["is_green"]: score += 15

    if score >= config["min_score"] and rvol >= config["min_rvol_trend"]:
        return {"type": "Regular Trend 📈", "score": score, "instant": False}

    return {"type": "None", "score": score, "instant": False}

def update_learning_engine(all_data, history):
    """خوارزمية التعلم الذاتي: تعديل المعايير بناءً على أداء أسهم اليوم"""
    config = get_learned_config()
    
    winning_rvols = []
    winning_rsis = []
    
    for sym, metrics in history.items():
        start_price = metrics.get("first_price", 0)
        close_price = all_data.get(sym, {}).get("close", 0)
        
        if start_price > 0 and close_price > 0:
            change_pct = ((close_price - start_price) / start_price) * 100
            
            # السهم يعتبر ناجحاً إذا حقق أرباحاً أعلى من +1.5%
            if change_pct >= 1.5:
                winning_rvols.append(metrics.get("rvol", 1.0))
                winning_rsis.append(metrics.get("rsi", 50.0))

    changes_log = []
    
    # تحسين السيولة المطلوبة إذا وُجد سلوك واضح
    if winning_rvols:
        avg_winner_rvol = sum(winning_rvols) / len(winning_rvols)
        new_min_rvol = round(max(1.0, min(1.6, (config["min_rvol_trend"] + avg_winner_rvol) / 2)), 2)
        if new_min_rvol != config["min_rvol_trend"]:
            changes_log.append(f"• السيولة الأدنى (RVOL): تعدلت من `{config['min_rvol_trend']}x` إلى `{new_min_rvol}x`")
            config["min_rvol_trend"] = new_min_rvol

    # تحسين نطاق RSI
    if winning_rsis:
        avg_winner_rsi = sum(winning_rsis) / len(winning_rsis)
        if avg_winner_rsi > 58:
            config["rsi_min"] = round(min(52.0, config["rsi_min"] + 1.0), 1)
            changes_log.append(f"• رفع حد RSI الأدنى إلى `{config['rsi_min']}` لتركيز الفرص القوية.")

    config["learned_days"] += 1
    save_json(CONFIG_FILE, config)
    
    return changes_log, config

# ===========================================================
# 5. تدقيق نهاية اليوم والتقرير التعليمي
# ===========================================================
def run_post_market_audit(all_data, history):
    trades = load_json(TRADES_FILE, {})
    
    # تشغيل خوارزمية التعلم الذاتي
    learning_changes, new_config = update_learning_engine(all_data, history)
    
    green_count = sum(1 for d in all_data.values() if d["is_green"])
    avg_rvol = sum(d["rvol"] for d in all_data.values()) / len(all_data) if all_data else 1.0

    report = "🏁 **تقرير تدقيق الجلسة وتحديث الذكاء الاصطناعي (AI Digest)**\n\n"
    report += (
        f"📊 **ملخص البورصة اليوم:**\n"
        f"├ أسهم صاعدة: {green_count}/{len(all_data)}\n"
        f"└ متوسط سيولة السوق: {round(avg_rvol, 2)}x\n\n"
    )

    if trades:
        report += f"📈 **عدد التوصيات المفعلة اليوم:** {len(trades)} صفقة.\n\n"
    else:
        report += "ℹ️ **التوصيات:** لم تُفعل أي صفقات جديدة اليوم لحمايتك من التذبذب.\n\n"

    # عرض نتائج التعلم والتحديث الذاتي
    report += f"🧠 **تحديث المعايير الذكية (أيام التعلم: {new_config['learned_days']}):**\n"
    if learning_changes:
        for change in learning_changes:
            report += f"{change}\n"
    else:
        report += "• تم التأكد من كفاءة المعايير الحالية واستمرار العمل بها للغد.\n"

    try:
        bot.send_message(CHAT_ID, report, parse_mode="Markdown")
        logging.info("📜 تم إرسال تقرير التدقيق والتعلم بنجاح.")
    except Exception as e:
        logging.error(f"خطأ إرسال التقرير: {e}")

# ===========================================================
# 6. المحرك الرئيسي للفحص
# ===========================================================
def run_smart_scan(force=False):
    global audit_sent_today, last_audit_date
    now_cairo = datetime.now(CAIRO_TZ)
    today_str = now_cairo.strftime("%Y-%m-%d")

    if last_audit_date != today_str:
        audit_sent_today = False
        last_audit_date = today_str

    if not force and not is_market_open():
        msg = f"⏳ [{now_cairo.strftime('%H:%M')} مصر] السوق مغلق حالياً. الفحص التلقائي يعمل فقط أثناء وقت التداول لتوفير الموارد."
        logging.info(msg)
        return msg

    config = get_learned_config()
    logging.info(f"🔍 بدء الفحص الذكي للأسهم [{'تشغيل يدوي' if force else now_cairo.strftime('%H:%M مصر')}]...")
    
    all_data = {}
    history = load_json(DATA_FILE, {})

    for stock in STOCKS:
        data = fetch_stock_data_safe(stock)
        if data:
            all_data[stock] = data
            eval_res = evaluate_stock(data, now_cairo, config)
            
            prev_metrics = history.get(stock, {})
            history[stock] = {
                "max_score": max(prev_metrics.get("max_score", 0), eval_res["score"]),
                "first_price": prev_metrics.get("first_price", data["close"]),
                "price": data["close"],
                "rvol": data["rvol"],
                "rsi": data["rsi"]
            }
        time.sleep(0.3)

    save_json(DATA_FILE, history)

    # تشغيل تقرير الإغلاق والتعلم اليومي عند الإغلاق (14:15 - 14:30)
    if not force and now_cairo.hour == 14 and now_cairo.minute >= 15 and not audit_sent_today:
        run_post_market_audit(all_data, history)
        audit_sent_today = True

    summary = f"✅ اكتمل الفحص.\n📊 تم فحص: {len(all_data)}/{len(STOCKS)} سهم."
    logging.info(summary)
    return summary

# ===========================================================
# 7. الأوامر التفاعلية المنظمة
# ===========================================================
@bot.message_handler(commands=['scan'])
def handle_manual_scan(message):
    """أمر الفحص العادي المحمي بوقت التداول فقط"""
    if not is_market_open():
        bot.reply_to(
            message, 
            "⏳ **السوق مغلق حالياً.**\n"
            "أمر `/scan` يعمل فقط أثناء ساعات تداول البورصة المصرية (10:00 ص - 2:30 ظ) لتوفير موارد السيرفر.\n"
            "إذا أردت تجربة الفحص القسري خارج الجلسة، استخدم الأمر: `/force`",
            parse_mode="Markdown"
        )
        return

    bot.reply_to(message, "🚀 جاري بدء الفحص المباشر في سوق التداول...")
    result = run_smart_scan(force=True)
    bot.send_message(message.chat.id, result)

@bot.message_handler(commands=['force'])
def handle_force_scan(message):
    """أمر تجربة الفحص القسري خارج الجلسة"""
    bot.reply_to(message, "⚠️ جاري تشغيل فحص قسري وتجاوز قيود التوقيت...")
    result = run_smart_scan(force=True)
    bot.send_message(message.chat.id, result)

if __name__ == "__main__":
    if os.environ.get("RUN_MODE") == "CRON":
        run_smart_scan(force=False)
    else:
        logging.info("🤖 البوت يعمل باستمرار ويستمع لأمر (/scan)...")
        bot.infinity_polling()
