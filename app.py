import os
import logging
from datetime import datetime
import pytz
import requests
from tradingview_ta import TA_Handler, Interval, Exchange

# ---------------------------------------------------------
# 1. إعدادات التسجيل والمنطقة الزمنية
# ---------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

CAIRO_TZ = pytz.timezone('Africa/Cairo')

# بيانات التليجرام من متغيّرات البيئة
TELEGRAM_BOT_TOKEN = os.getenv("8222819132:AAFmMjXCVnUFU8JUEcsujHKVjdmrJ1_zzPg")
TELEGRAM_CHAT_ID = os.getenv("5418506244")

# ---------------------------------------------------------
# 2. قائمة أسهم EGX33 المعتمدة والمنظفة
# ---------------------------------------------------------
EGX33_SYMBOLS = [
    "ABUK", "MFPC", "SKPC", "AMOC", "MBSC", "SCEM", 
    "TMGH", "OCDI", "MASR", "EMFD", "ORAS", "ORHD", "HELI", 
    "CLHO", "ISPH", "RMDA", "PHAR", "JUFO", "OLFI", "SUGR", 
    "EFID", "EFIH", "FWRY", "ETEL", "ALCN", "CSAG", "ORWE", 
    "ARAB", "CICH", "EALR"
]

# ---------------------------------------------------------
# 3. إرسال تنبيهات تليجرام
# ---------------------------------------------------------
def send_telegram_message(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("⚠️ لم يتم ضبط متغيّرات بيئة تليجرام (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).")
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
            logging.error(f"فشل إرسال رسالة تليجرام: {res.text}")
    except Exception as e:
        logging.error(f"خطأ أثناء الاتصال بتليجرام: {e}")

# ---------------------------------------------------------
# 4. جلب البيانات الفنية للسهم
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
        
        # التغير السعري اللحظي
        change_pct = ((close - open_p) / open_p * 100) if open_p > 0 else 0
        
        # حساب السيولة النسبية التقريبية (RVOL)
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
# 5. محرك التقييم الديناميكي (Dynamic RVOL + Sensitivity Tuning)
# ---------------------------------------------------------
def evaluate_stock_opportunity(data: dict, current_time):
    # أ) تحديد شرط RVOL الديناميكي بناءً على وقت الجلسة
    hour_min = current_time.hour + current_time.minute / 60.0
    
    if hour_min < 11.5:        # من 10:00 حتى 11:30 (افتتاح نَشِط)
        min_rvol_trend = 1.3
    elif hour_min < 13.5:      # من 11:30 حتى 13:30 (منتصف الجلسة الهادئ)
        min_rvol_trend = 0.9
    else:                      # من 13:30 حتى 14:15 (نهاية الجلسة)
        min_rvol_trend = 1.1

    rsi = data["rsi"]
    rvol = data["rvol"]
    change_pct = data["change_pct"]
    close = data["close"]
    
    # 1. مسار صائد القفزات (Breakout Hunter)
    if rvol >= 2.0 and change_pct >= 2.0 and (50.0 <= rsi <= 72.0):
        return {
            "type": "Breakout Hunter 🚀",
            "score": 90,
            "details": f"سيولة انفجارية {round(rvol,2)}x وتغير +{round(change_pct,2)}%"
        }

    # 2. مسار الاتجاه المنتظم (Regular Trend) - تخفيف الحساسية
    score = 0
    
    # شرط الزخم المعدل (من 45 إلى 63)
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

    # قبول الفرصة عند تقييم >= 65 وشعاع سيولة مناسب للوقت الحالي
    if score >= 65 and rvol >= min_rvol_trend:
        return {
            "type": "Regular Trend 📈",
            "score": score,
            "details": f"تقييم ممتاز ({score}/100) وسيولة {round(rvol,2)}x (الحد المطلوب: {min_rvol_trend}x)"
        }

    return {"type": "None", "score": score, "details": "لم يتجاوز الفلتر"}

# ---------------------------------------------------------
# 6. دالة الفحص الرئيسية (Single-Execution for Cron Job)
# ---------------------------------------------------------
def run_market_scan():
    now_cairo = datetime.now(CAIRO_TZ)
    current_time_str = now_cairo.strftime('%H:%M')
    
    # التحقق من ساعات عمل السوق الرسمي (10:00 - 14:15)
    start_market = now_cairo.replace(hour=10, minute=0, second=0, microsecond=0)
    end_market = now_cairo.replace(hour=14, minute=15, second=0, microsecond=0)

    if not (start_market <= now_cairo <= end_market):
        logging.info(f"⏳ [{current_time_str} مصر] خارج ساعات تداول البورصة المصرية. تم إنهاء المهام.")
        return

    logging.info(f"🔍 بدء فحص أسهم EGX33 التفاعلي [{current_time_str} مصر]...")
    
    detected_opportunities = []

    for symbol in EGX33_SYMBOLS:
        data = fetch_stock_data(symbol)
        if not data:
            continue
        
        eval_res = evaluate_stock_opportunity(data, now_cairo)
        
        # --- وضع التتقرير الشامل (Audit Logging) ---
        logging.info(
            f"📊 [{symbol}] السعر: {data['close']} | "
            f"RVOL: {round(data['rvol'], 2)}x | "
            f"RSI: {round(data['rsi'], 1)} | "
            f"التغير: {round(data['change_pct'], 2)}% | "
            f"النتيجة: {eval_res['score']}/100 ({eval_res['type']})"
        )

        if eval_res["type"] != "None":
            msg = (
                f"🎯 **تنبيه فرصة تداول جديدة [{eval_res['type']}]**\n\n"
                f"🔹 **السهم:** `{symbol}`\n"
                f"💵 **سعر الإغلاق:** {data['close']} ج.م\n"
                f"📊 **مؤشر الزخم RSI:** {round(data['rsi'], 1)}\n"
                f"💥 **حجم السيولة RVOL:** {round(data['rvol'], 2)}x\n"
                f"📈 **التغير اللحظي:** {round(data['change_pct'], 2)}%\n"
                f"🏆 **درجة التقييم:** {eval_res['score']}/100\n\n"
                f"📝 **ملاحظة Engine:** {eval_res['details']}"
            )
            send_telegram_message(msg)
            detected_opportunities.append(symbol)

    logging.info(f"✅ اكتمل الفحص. عدد التنبيهات المرسلة: {len(detected_opportunities)}")

if __name__ == "__main__":
    run_market_scan()
    
