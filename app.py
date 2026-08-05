import os
import sys
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from tradingview_ta import TA_Handler, Interval

# ---------------------------------------------------------
# 1. الإعدادات والمنطقة الزمنية (باستخدام zoneinfo المدمجة)
# ---------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# استخدام المكتبة القياسية للغة بايثون بدون الحاجة لتثبيت pytz
CAIRO_TZ = ZoneInfo("Africa/Cairo")

TELEGRAM_BOT_TOKEN = os.getenv("8222819132:AAFmMjXCVnUFU8JUEcsujHKVjdmrJ1_zzPg")
TELEGRAM_CHAT_ID = os.getenv("5418506244")
DATA_FILE = "daily_history.json"

# قائمة أسهم EGX33 المعتمدة والمصححة
EGX33_SYMBOLS = [
    "ABUK", "MFPC", "SKPC", "AMOC", "MBSC", "SCEM", 
    "TMGH", "OCDI", "MASR", "EMFD", "ORAS", "ORHD", "HELI", 
    "CLHO", "ISPH", "RMDA", "PHAR", "JUFO", "OLFI", "SUGR", 
    "EFID", "EFIH", "FWRY", "ETEL", "ALCN", "CSAG", "ORWE", 
    "ARAB", "CICH", "EALR"
]

# ---------------------------------------------------------
# 2. إدارة البيانات التاريخية المجانية (JSON File Storage)
# ---------------------------------------------------------
def load_history():
    """تحميل سجلات الأيام السابقة"""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.warning(f"تعذر قراءة ملف التاريخ: {e}")
    return {}

def save_history(history_data):
    """حفظ سجلات التقييم يومياً"""
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(history_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"فشل حفظ ملف التاريخ: {e}")

# ---------------------------------------------------------
# 3. إرسال تنبيهات تليجرام
# ---------------------------------------------------------
def send_telegram_message(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("⚠️ لم يتم ضبط متغيّرات بيئة تليجرام.")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logging.error(f"خطأ أثناء الاتصال بتليجرام: {e}")

# ---------------------------------------------------------
# 4. جلب البيانات الفنية
# ---------------------------------------------------------
def fetch_stock_data(symbol: str):
    try:
        handler = TA_Handler(
            symbol=symbol,
            screener="egypt",
            exchange="EGX",
            interval=Interval.INTERVAL_15_MINUTES
        )
        analysis = handler.get_analysis()
        indicators = analysis.indicators
        
        close = indicators.get("close", 0)
        open_p = indicators.get("open", 0)
        volume = indicators.get("volume", 0)
        rsi = indicators.get("RSI", 50)
        ema25 = indicators.get("EMA25", 0)
        ema50 = indicators.get("EMA50", 0)
        macd = indicators.get("MACD.macd", 0)
        macd_signal = indicators.get("MACD.signal", 0)
        
        change_pct = ((close - open_p) / open_p * 100) if open_p > 0 else 0
        volume_sma20 = indicators.get("volume.SMA20", volume)
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
        logging.warning(f"تعذر جلب بيانات السهم {symbol}: {e}")
        return None

# ---------------------------------------------------------
# 5. محرك التقييم الذكي التراكمي (Quant Accumulation Engine)
# ---------------------------------------------------------
def evaluate_stock_opportunity(data: dict, current_time, history: dict):
    symbol = data["symbol"]
    hour_min = current_time.hour + current_time.minute / 60.0
    
    # RVOL ديناميكي حسب وقت الجلسة
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
    
    # 1. مسار صائد القفزات الانفجارية (Breakout Hunter)
    if rvol >= 2.0 and change_pct >= 2.0 and (50.0 <= rsi <= 72.0):
        return {
            "type": "Breakout Hunter 🚀",
            "score": 90,
            "details": f"سيولة انفجارية {round(rvol,2)}x وتغير +{round(change_pct,2)}%"
        }

    # 2. مسار الاتجاه المنتظم التراكمي (Regular Trend)
    score = 0
    if 45.0 <= rsi <= 63.0:
        score += 30
    elif 63.0 < rsi <= 68.0:
        score += 15
        
    if close > data["ema25"]:
        score += 20
    if close > data["ema50"]:
        score += 20
    if data["macd"] > data["macd_signal"]:
        score += 15
    if data["is_green"]:
        score += 15

    # 🌟 بونص التجميع التراكمي (مقارنة بأداء الأيام السابقة)
    prev_symbol_data = history.get(symbol, {})
    if prev_symbol_data.get("score", 0) >= 55 and rvol >= 1.0:
        score += 15
        logging.info(f"🔥 [{symbol}] حصل على بونص تجميع تراكمي +15 نقطة!")

    if score >= 65 and rvol >= min_rvol_trend:
        return {
            "type": "Regular Trend 📈",
            "score": score,
            "details": f"تقييم ممتاز ({score}/100) وسيولة {round(rvol,2)}x"
        }

    return {"type": "None", "score": score, "details": "لم يتجاوز الفلتر"}

# ---------------------------------------------------------
# 6. دالة الفحص الرئيسية والتحكم الزمني الذكي (Smart Throttling)
# ---------------------------------------------------------
def run_market_scan():
    now_cairo = datetime.now(CAIRO_TZ)
    current_time_str = now_cairo.strftime('%H:%M')
    
    start_market = now_cairo.replace(hour=10, minute=0, second=0, microsecond=0)
    end_market = now_cairo.replace(hour=14, minute=15, second=0, microsecond=0)

    # 1. الخروج الفوري خارج ساعات التداول أو في العطلات الرسمية
    if not (start_market <= now_cairo <= end_market):
        logging.info(f"⏳ [{current_time_str} مصر] خارج ساعات تداول البورصة. خروج فوري لتوفير الموارد.")
        sys.exit(0)

    # 2. التحكم الزمني الذكي (Smart Throttling Engine)
    hour = now_cairo.hour
    minute = now_cairo.minute
    
    # في أول 45 دقيقة (10:00 إلى 10:45): فحص كل 5 دقائق
    if hour == 10 and minute < 45:
        is_scan_time = True
    else:
        # باقي الجلسة (11:00 - 14:15): فحص كل 15 دقيقة فقط (عند الدقائق 00, 15, 30, 45)
        is_scan_time = (minute % 15 == 0)

    if not is_scan_time:
        logging.info(f"💤 [{current_time_str} مصر] فترة الهدوء (تخطي الفحص لتوفير الموارد).")
        sys.exit(0)

    logging.info(f"🔍 بدء فحص أسهم EGX33 التفاعلي [{current_time_str} مصر]...")
    
    history = load_history()
    today_results = {}
    detected_opportunities = []

    for symbol in EGX33_SYMBOLS:
        data = fetch_stock_data(symbol)
        if not data:
            continue
        
        eval_res = evaluate_stock_opportunity(data, now_cairo, history)
        today_results[symbol] = {
            "score": eval_res["score"],
            "price": data["close"],
            "rvol": data["rvol"],
            "rsi": data["rsi"]
        }

        # تسجيل البيانات في السجلات
        logging.info(
            f"📊 [{symbol}] السعر: {data['close']} | "
            f"RVOL: {round(data['rvol'], 2)}x | "
            f"RSI: {round(data['rsi'], 1)} | "
            f"النتيجة: {eval_res['score']}/100 ({eval_res['type']})"
        )

        if eval_res["type"] != "None":
            msg = (
                f"🎯 **تنبيه فرصة تداول [{eval_res['type']}]**\n\n"
                f"🔹 **السهم:** `{symbol}`\n"
                f"💵 **السعر:** {data['close']} ج.م\n"
                f"📊 **RSI:** {round(data['rsi'], 1)} | **RVOL:** {round(data['rvol'], 2)}x\n"
                f"🏆 **التقييم:** {eval_res['score']}/100\n"
                f"📝 {eval_res['details']}"
            )
            send_telegram_message(msg)
            detected_opportunities.append(symbol)

    # حفظ بيانات اليوم للاستفادة منها في الجلسة القادمة
    save_history(today_results)

    # 📊 إرسال تقرير الإغلاق الختامي عند الفحص الأخير (14:15)
    if hour == 14 and minute >= 10:
        top_3 = sorted(today_results.items(), key=lambda x: x[1]['score'], reverse=True)[:3]
        digest_msg = "🏁 **تقرير إغلاق الجلسة واقتراحات الغد (Post-Market Digest)**\n\n"
        digest_msg += "🔝 **أفضل 3 أسهم تجميعية للجلسة القادمة:**\n"
        for idx, (sym, metrics) in enumerate(top_3, 1):
            digest_msg += f"{idx}. `{sym}` - تقييم: {metrics['score']}/100 | RVOL: {round(metrics['rvol'],2)}x\n"
        
        digest_msg += f"\n✅ إجمالي التنبيهات اليوم: {len(detected_opportunities)}"
        send_telegram_message(digest_msg)
        logging.info("📜 تم إرسال تقرير الإغلاق الختامي بنجاح.")

    logging.info(f"✅ اكتمل الفحص. الخروج الفوري لتوفير الموارد.")
    sys.exit(0)

if __name__ == "__main__":
    run_market_scan()
    
