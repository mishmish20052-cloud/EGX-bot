import os
import sys
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from tradingview_ta import TA_Handler, Interval

# ===========================================================
# 1. الإعدادات الأساسية والمنطقة الزمنية
# ===========================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

CAIRO_TZ = ZoneInfo("Africa/Cairo")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8222819132:AAFmMjXCVnUFU8JUEcsujHKVjdmrJ1_zzPg")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "5418506244")

DATA_FILE = "daily_history.json"
PENDING_FILE = "pending_signals.json"
TRADES_FILE = "today_trades.json"

EGX33_SYMBOLS = [
    "ABUK", "MFPC", "SKPC", "AMOC", "MBSC", "SCEM", 
    "TMGH", "OCDI", "MASR", "EMFD", "ORAS", "ORHD", "HELI", 
    "CLHO", "ISPH", "RMDA", "PHAR", "JUFO", "OLFI", "SUGR", 
    "EFID", "EFIH", "FWRY", "ETEL", "ALCN", "CSAG", "ORWE", 
    "ARAB", "CICH", "EALR"
]

_cache = {}

# ===========================================================
# 2. دوال مساعدة لإدارة JSON والتليجرام
# ===========================================================
def load_json(file_path, default=None):
    if default is None:
        default = {}
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.warning(f"تعذر قراءة {file_path}: {e}")
            return default
    return default

def save_json(file_path, data):
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"فشل حفظ {file_path}: {e}")

def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("⚠️ بيانات تليجرام غير مضافة.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code != 200:
            logging.error(f"خطأ تليجرام: {res.text}")
    except Exception as e:
        logging.error(f"خطأ اتصال بتليجرام: {e}")

# ===========================================================
# 3. جلب بيانات الأسهم مع التخزين اللحظي (In-Memory Cache)
# ===========================================================
def fetch_stock_data(symbol: str):
    if symbol in _cache:
        return _cache[symbol]
    
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
        
        data = {
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
        _cache[symbol] = data
        return data
    except Exception as e:
        logging.warning(f"تعذر جلب بيانات السهم {symbol}: {e}")
        return None

# ===========================================================
# 4. محرك التقييم وإدارة المخاطر (Dynamic Risk & TP3 Engine)
# ===========================================================
def evaluate_stock(data: dict, current_time, history: dict):
    symbol = data["symbol"]
    hour_min = current_time.hour + current_time.minute / 60.0
    
    if hour_min < 11.5:
        min_rvol_trend = 1.3
    elif hour_min < 13.5:
        min_rvol_trend = 0.9
    else:
        min_rvol_trend = 1.1

    rsi = data["rsi"]
    rvol = data["rvol"]
    change_pct = data["change_pct"]
    close = data["close"]
    
    # حماية من الأسهم القريبة من الحد الأقصى اليومي
    if change_pct >= 8.5:
        return {"type": "None", "score": 0, "details": "قريب من الحد الأقصى اليومي"}

    # 1. مسار الانفجار الفوري (Super Breakout)
    if rvol >= 2.5 and change_pct >= 1.5 and (50.0 <= rsi <= 72.0):
        return {
            "type": "Super Breakout 🚀",
            "score": 95,
            "instant": True,
            "details": f"سيولة فائقة {round(rvol,2)}x وتغير +{round(change_pct,2)}%"
        }

    # 2. مسار الانفجار المعتدل (Moderate Breakout)
    if rvol >= 1.8 and (0.5 <= change_pct <= 2.0) and close > data["ema25"]:
        return {
            "type": "Moderate Breakout ⚡",
            "score": 85,
            "instant": False,
            "details": f"انفجار معتدل مبكر {round(rvol,2)}x وتغير +{round(change_pct,2)}%"
        }

    # 3. مسار الاتجاه التراكمي المنتظم (Regular Trend)
    score = 0
    if 45.0 <= rsi <= 63.0:
        score += 30
    elif 63.0 < rsi <= 68.0:
        score += 15
        
    if close > data["ema25"]: score += 20
    if close > data["ema50"]: score += 20
    if data["macd"] > data["macd_signal"]: score += 15
    if data["is_green"]: score += 15

    prev_data = history.get(symbol, {})
    if prev_data.get("score", 0) >= 55 and rvol >= 1.0:
        score += 15

    if score >= 65 and rvol >= min_rvol_trend:
        return {
            "type": "Regular Trend 📈",
            "score": score,
            "instant": False,
            "details": f"اتجاه منتظم بـ تقييم ({score}/100) وسيولة {round(rvol,2)}x"
        }

    return {"type": "None", "score": score, "instant": False, "details": "لم يتجاوز الفلتر"}

def calculate_targets(entry_price: float):
    """حساب مستويات الوقف والأهداف الثلاثة"""
    sl = round(entry_price * 0.98, 2)       # وقف خسارة -2%
    tp1 = round(entry_price * 1.03, 2)      # هدف أول +3%
    tp2 = round(entry_price * 1.06, 2)      # هدف ثانٍ +6%
    tp3 = round(entry_price * 1.10, 2)      # هدف ثالث +10%
    return sl, tp1, tp2, tp3

def send_opportunity_alert(symbol, data, eval_res):
    close = data["close"]
    sl, tp1, tp2, tp3 = calculate_targets(close)
    
    # حساب أرقام الأرباح/الخسائر المفترضة على رأسمال 10,000 ج.م
    loss_amount = round(10000 * 0.02)
    tp1_amount = round(10000 * 0.03)
    tp2_amount = round(10000 * 0.06)
    tp3_amount = round(10000 * 0.10)

    msg = (
        f"🎯 **تنبيه فرصة تداول [{eval_res['type']}]**\n\n"
        f"🔹 **السهم:** `{symbol}`\n"
        f"💵 **سعر الدخول:** {close} ج.م\n"
        f"🏆 **التقييم الفني:** {eval_res['score']}/100\n"
        f"📊 **RSI:** {round(data['rsi'], 1)} | **RVOL:** {round(data['rvol'], 2)}x\n\n"
        f"🎯 **الأهداف ووقف الخسارة الديناميكي:**\n"
        f"├ 🛑 **وقف الخسارة (SL):** `{sl}` ج.م (-2.0%)\n"
        f"│  └ 📉 الخسارة المتوقعة: -{loss_amount} ج.م / لكل 10,000 ج.م\n"
        f"├ 🎯 **الهدف الأول (TP1):** `{tp1}` ج.م (+3.0%)\n"
        f"│  └ 📈 الربح: +{tp1_amount} ج.م ➔ *يرفع الستوب تلقائياً إلى {close} ج.م (الدخول)*\n"
        f"├ 🚀 **الهدف الثاني (TP2):** `{tp2}` ج.م (+6.0%)\n"
        f"│  └ 📈 الربح: +{tp2_amount} ج.م ➔ *يرفع الستوب تلقائياً إلى {tp1} ج.م (TP1)*\n"
        f"└ 🚀🚀 **الهدف الثالث (TP3):** `{tp3}` ج.م (+10.0%)\n"
        f"   └ 📈 الربح: +{tp3_amount} ج.م ➔ *يرفع الستوب تلقائياً إلى {tp2} ج.م (TP2)*\n\n"
        f"📝 {eval_res['details']}"
    )
    send_telegram(msg)
    
    trades = load_json(TRADES_FILE, {})
    trades[symbol] = {
        "entry": close,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "time": datetime.now(CAIRO_TZ).strftime('%H:%M'),
        "score": eval_res["score"],
        "max_seen": close
    }
    save_json(TRADES_FILE, trades)

# ===========================================================
# 5. نظام التأكيد المزدوج (Double Confirmation Engine)
# ===========================================================
def process_pending_signals(current_data):
    pending = load_json(PENDING_FILE, {})
    if not pending:
        return

    updated_pending = {}
    for symbol, sig in pending.items():
        if symbol not in current_data:
            continue
        
        curr = current_data[symbol]
        prev_close = sig["entry_price"]
        prev_rvol = sig["rvol"]
        
        price_change = ((curr["close"] - prev_close) / prev_close) * 100
        vol_ratio = curr["rvol"] / prev_rvol if prev_rvol > 0 else 1.0
        
        if price_change >= -0.5 and vol_ratio >= 0.8:
            logging.info(f"✅ تم تأكيد إشارة السهم {symbol}")
            eval_res = {
                "type": f"مؤكد {sig['type']}",
                "score": sig["score"],
                "details": f"تأكيد الثبات السعري ({round(price_change, 2)}%) والسيولة"
            }
            send_opportunity_alert(symbol, curr, eval_res)
        else:
            logging.info(f"❌ تم إلغاء الإشارة المؤقتة للسهم {symbol}")
            
    save_json(PENDING_FILE, updated_pending)

# ===========================================================
# 6. تدقيق النتائج بنهاية اليوم (Post-Market Audit Engine)
# ===========================================================
def run_post_market_audit(all_data, history):
    trades = load_json(TRADES_FILE, {})
    if not trades:
        send_telegram("🏁 **تقرير إغلاق الجلسة:** لم تفعل أي صفقات جديدة اليوم.")
        return

    wins = 0
    losses = 0
    in_progress = 0
    audit_details = ""
    
    for symbol, t in trades.items():
        if symbol in all_data:
            curr_close = all_data[symbol]["close"]
            entry = t["entry"]
            sl = t["sl"]
            tp1 = t["tp1"]
            tp2 = t["tp2"]
            tp3 = t.get("tp3", round(entry * 1.10, 2))
            
            if curr_close >= tp3:
                status = "🎯🎯🎯 حقق الهدف الثالث (+10%)"
                wins += 1
            elif curr_close >= tp2:
                status = "🎯🎯 حقق الهدف الثاني (+6%)"
                wins += 1
            elif curr_close >= tp1:
                status = "🎯 حقق الهدف الأول (+3%)"
                wins += 1
            elif curr_close <= sl:
                status = "🛑 ضرب وقف الخسارة (-2%)"
                losses += 1
            else:
                status = "⏳ صفقة مستمرة/محايدة"
                in_progress += 1
                
            audit_details += f"• `{symbol}`: دخول {entry} | إغلاق {curr_close} ➔ {status}\n"

    total_closed = wins + losses
    win_rate = round((wins / total_closed * 100), 1) if total_closed > 0 else 0.0

    green_count = sum(1 for d in all_data.values() if d["is_green"])
    avg_rvol = sum(d["rvol"] for d in all_data.values()) / len(all_data) if all_data else 1.0

    report = (
        f"🏁 **تقرير تدقيق الصفقات المكتملة ونسبة النجاح (Audit Digest)**\n\n"
        f"📊 **مؤشر البورصة العامة اليوم:**\n"
        f"├ أسهم صاعدة: {green_count}/{len(all_data)}\n"
        f"└ متوسط سيولة السوق: {round(avg_rvol, 2)}x\n\n"
        f"📈 **نتائج التوصيات الصادرة:**\n"
        f"├ 🎯 الصفقات الرابحة: {wins}\n"
        f"├ 🛑 الصفقات الخاسرة: {losses}\n"
        f"├ ⏳ صفقات مستمرة: {in_progress}\n"
        f"└ 🏆 **نسبة النجاح (Win Rate): {win_rate}%**\n\n"
        f"📝 **تفاصيل أداء صفقات اليوم:**\n{audit_details}\n"
        f"🔝 **أفضل اقتراحات التجميع للغد:**\n"
    )

    top_stocks = sorted(
        [item for item in history.items() if item[1].get('score', 0) >= 50],
        key=lambda x: x[1]['score'], reverse=True
    )[:5]
    
    for idx, (sym, metrics) in enumerate(top_stocks, 1):
        report += f"{idx}. `{sym}` - تقييم: {metrics['score']}/100 | RVOL: {round(metrics.get('max_rvol',0), 2)}x\n"

    send_telegram(report)
    logging.info("📜 تم إرسال تقرير التدقيق النهائي بنجاح.")

# ===========================================================
# 7. دالة الفحص والتحكم الزمني الذكي
# ===========================================================
def run_market_scan():
    now_cairo = datetime.now(CAIRO_TZ)
    current_time_str = now_cairo.strftime('%H:%M')
    hour = now_cairo.hour
    minute = now_cairo.minute

    start_market = now_cairo.replace(hour=10, minute=0, second=0, microsecond=0)
    end_market = now_cairo.replace(hour=14, minute=25, second=0, microsecond=0)

    if not (start_market <= now_cairo <= end_market):
        logging.info(f"⏳ [{current_time_str} مصر] خارج ساعات التداول. خروج فوري لتوفير الموارد.")
        sys.exit(0)

    if hour == 10 and minute < 45:
        is_scan_time = True
    elif hour == 14 and minute >= 15:
        is_scan_time = True
    else:
        is_scan_time = (minute % 15 == 0)

    if not is_scan_time:
        logging.info(f"💤 [{current_time_str} مصر] فترة الهدوء.")
        sys.exit(0)

    logging.info(f"🔍 بدء الفحص الذكي للأسهم [{current_time_str} مصر]...")

    all_data = {}
    for symbol in EGX33_SYMBOLS:
        data = fetch_stock_data(symbol)
        if data:
            all_data[symbol] = data

    if not all_data:
        logging.warning("⚠️ تعذر جلب بيانات الأسهم.")
        sys.exit(0)

    process_pending_signals(all_data)

    history = load_json(DATA_FILE, {})
    pending_to_add = load_json(PENDING_FILE, {})
    today_results = history.copy()

    for symbol, data in all_data.items():
        eval_res = evaluate_stock(data, now_cairo, history)

        prev_max_rvol = today_results.get(symbol, {}).get("max_rvol", 0.0)
        today_results[symbol] = {
            "score": eval_res["score"],
            "price": data["close"],
            "rvol": data["rvol"],
            "max_rvol": max(prev_max_rvol, data["rvol"]),
            "rsi": data["rsi"],
            "type": eval_res["type"]
        }

        if eval_res["type"] != "None":
            if eval_res.get("instant", False):
                send_opportunity_alert(symbol, data, eval_res)
            else:
                trades = load_json(TRADES_FILE, {})
                if symbol not in pending_to_add and symbol not in trades:
                    pending_to_add[symbol] = {
                        "entry_price": data["close"],
                        "rvol": data["rvol"],
                        "score": eval_res["score"],
                        "type": eval_res["type"],
                        "time": current_time_str
                    }

    save_json(DATA_FILE, today_results)
    save_json(PENDING_FILE, pending_to_add)

    if hour == 14 and minute >= 15:
        run_post_market_audit(all_data, today_results)

    logging.info("✅ اكتمل الفحص بنجاح. الخروج الفوري.")
    sys.exit(0)

if __name__ == "__main__":
    run_market_scan()
