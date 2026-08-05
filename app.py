import os
import math
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from tradingview_ta import TA_Handler, Interval, Exchange

# ==========================================
# 1. إعدادات التسجيل والبيئة (Setup & Logging)
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)

TELEGRAM_BOT_TOKEN = os.getenv("8222819132:AAFmMjXCVnUFU8JUEcsujHKVjdmrJ1_zzPg", "8222819132:AAFmMjXCVnUFU8JUEcsujHKVjdmrJ1_zzPg")
TELEGRAM_CHAT_ID = os.getenv("5418506244", "5418506244")

# المنطقة الزمنية لمصر (تضمن محاذاة الوقت على سيرفرات Render)
EGYPT_TZ = ZoneInfo("Africa/Cairo")

# قائمة أسهم مؤشر EGX33 المعدلة والمطابقة لرموز TradingView
EGX33_SYMBOLS = [
    "ABUK", "MFPC", "SKPC", "AMOC", "KZPC", "MBSC", "SCEM", 
    "TMGH", "OCDI", "MASR", "EMFD", "ORAS", "ORHD", "HELI", 
    "CLHO", "ISPH", "RMDA", "PHAR", "JUFO", "OLFI", "SUGR", 
    "EFID", "EFIH", "FWRY", "ETEL", "ALCN", "CSAG", "ORWE", 
    "ARAB", "CICH", "GBCO", "EALR", "IRAX"
]

# ذاكرة تتبع الصفقات الحية (Live Position Tracker)
active_positions = {}

# ==========================================
# 2. وظائف التنبيه والتكامل (Telegram Alert)
# ==========================================
def send_telegram_alert(message: str):
    """إرسال تنبيه مباشر إلى تلجرام"""
    if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logging.info(f"[TELEGRAM SIMULATION]:\n{message}")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            logging.error(f"فشل إرسال التنبيه عبر تلجرام: {response.text}")
    except Exception as e:
        logging.error(f"خطأ في الاتصال بتلجرام: {e}")

# ==========================================
# 3. محرك تحليل البيانات الفنية (TA Engine)
# ==========================================
def fetch_stock_data(symbol: str):
    """سحب البيانات الفنية اللحظية للسهم عبر TradingView"""
    try:
        handler = TA_Handler(
            symbol=symbol,
            screener="egypt",
            exchange="EGX",
            interval=Interval.INTERVAL_15_MINUTES
        )
        analysis = handler.get_analysis()
        indicators = analysis.indicators

        close = indicators.get("close")
        open_p = indicators.get("open")
        high = indicators.get("high")
        low = indicators.get("low")
        volume = indicators.get("volume")
        vol_sma = indicators.get("volume.SMA20") or volume
        rsi = indicators.get("RSI")
        ema25 = indicators.get("EMA25")
        ema50 = indicators.get("EMA50")
        macd = indicators.get("MACD.macd")
        macd_signal = indicators.get("MACD.signal")

        if not close or not open_p or not rsi:
            return None

        rvol = round(volume / vol_sma, 2) if vol_sma and vol_sma > 0 else 1.0
        price_change_pct = round(((close - open_p) / open_p) * 100, 2)

        return {
            "symbol": symbol,
            "close": close,
            "open": open_p,
            "high": high,
            "low": low,
            "volume": volume,
            "rvol": rvol,
            "rsi": rsi,
            "ema25": ema25,
            "ema50": ema50,
            "macd": macd,
            "macd_signal": macd_signal,
            "change_pct": price_change_pct
        }
    except Exception as e:
        logging.warning(f"تعذر جلب بيانات السهم {symbol}: {e}")
        return None

# ==========================================
# 4. محرك المسار المزدوج والتقييم (Quant Rules)
# ==========================================
def evaluate_stock_opportunity(data: dict):
    """تطبيق منطق المسار المزدوج (Regular vs Breakout)"""
    symbol = data["symbol"]
    close = data["close"]
    open_p = data["open"]
    rsi = data["rsi"]
    rvol = data["rvol"]
    change_pct = data["change_pct"]
    low_price = data["low"]

    score = 0

    # 1. سقف RSI ديناميكي مكيّف بحسب قوة السيولة
    max_rsi_allowed = 72.0 if rvol >= 1.8 else 63.0
    
    if 50.0 <= rsi <= max_rsi_allowed:
        score += 30
    elif 45.0 <= rsi < 50.0:
        score += 15

    # 2. مؤشرات المتوسطات والزخم
    if data["ema25"] and close > data["ema25"]: score += 20
    if data["ema50"] and close > data["ema50"]: score += 20
    if data["macd"] and data["macd_signal"] and data["macd"] > data["macd_signal"]: score += 15
    if close > open_p: score += 15

    # ----------------------------------------------------
    # تحديد شروط المسار المزدوج للدخول (Dual-Track Logic)
    # ----------------------------------------------------
    is_regular_setup = (score >= 70 and rvol >= 1.2 and rsi <= 63.0)
    is_breakout_setup = (rvol >= 2.0 and change_pct >= 2.0 and rsi >= 52.0 and rsi <= 72.0)

    if is_regular_setup or is_breakout_setup:
        if is_breakout_setup:
            strategy_name = "صائد قفزات انفجاري 🚀"
            allocation_pct = "30% (دفعة استكشافية اختبارية)"
            is_pilot = True
            stop_loss = round(low_price * 0.995, 2)
        else:
            strategy_name = "اتجاه آمن منتظم 📈"
            allocation_pct = "40% (دخول هرمي أساسي)"
            is_pilot = False
            stop_loss = round(close * 0.975, 2)

        target_1 = round(close * 1.04, 2)

        return {
            "symbol": symbol,
            "score": score,
            "rsi": round(rsi, 1),
            "rvol": rvol,
            "change_pct": change_pct,
            "price": close,
            "strategy": strategy_name,
            "allocation": allocation_pct,
            "is_pilot": is_pilot,
            "stop_loss": stop_loss,
            "target_1": target_1
        }

    return None

# ==========================================
# 5. إدارة الصفقات الحية (Live Position Monitor)
# ==========================================
def monitor_active_positions():
    """متابعة الصفقات المفتوحة وتفعيل Zero-Risk والتعزيز التلقائي"""
    global active_positions
    
    for symbol, pos in list(active_positions.items()):
        data = fetch_stock_data(symbol)
        if not data:
            continue

        current_price = data["close"]
        entry_price = pos["entry_price"]
        gain_pct = ((current_price - entry_price) / entry_price) * 100

        # تفعيل حماية أمان Zero-Risk عند تحقيق +2% صعود
        if gain_pct >= 2.0 and not pos.get("zero_risk_active", False):
            pos["stop_loss"] = entry_price
            pos["zero_risk_active"] = True
            
            if pos.get("pilot_only", False):
                pos["pilot_only"] = False
                msg = (
                    f"⚡ **تأكيد تعزيز صفقة (Reinforcement Signal)**\n"
                    f"السهم: `{symbol}`\n"
                    f"السعر الحالي: `{current_price}` (+{gain_pct:.1f}%)\n"
                    f"🛡️ **الإجراء:** تم نقل وقف الخسارة إلى Zero-Risk (`{entry_price}`).\n"
                    f"💰 **توجيه التداول:** ضخ الـ 70% المتبقية من سيولة الصفقة بثقة!"
                )
                send_telegram_alert(msg)
            else:
                msg = (
                    f"🛡️ **تفعيل حماية الأمان (Zero-Risk Activated)**\n"
                    f"السهم: `{symbol}`\n"
                    f"السعر الحالي: `{current_price}` (+{gain_pct:.1f}%)\n"
                    f"تم رفع وقف الخسارة رسمياً إلى سعر الدخول: `{entry_price}`."
                )
                send_telegram_alert(msg)

        # خروج حتمي عند كسر وقف الخسارة
        elif current_price <= pos["stop_loss"]:
            msg = (
                f"🚨 **تنبيه خروج من الصفقة (Stop-Loss Triggered)**\n"
                f"السهم: `{symbol}`\n"
                f"سعر الخروج: `{current_price}`\n"
                f"مستوى الستوب المفعل: `{pos['stop_loss']}`\n"
                f"النتيجة: {'خروج بدون خسارة (0%)' if pos.get('zero_risk_active') else 'تفعيل وقف الخسارة'}"
            )
            send_telegram_alert(msg)
            del active_positions[symbol]

# ==========================================
# 6. دورة الفحص الرئيسية (Main Market Scanner)
# ==========================================
def run_market_scan():
    """تشغيل دورة الفحص الكاملة لأسهم EGX33"""
    logging.info("🔍 بدء فحص أسهم EGX33 عبر المحرك المطور...")
    
    if active_positions:
        monitor_active_positions()

    for symbol in EGX33_SYMBOLS:
        if symbol in active_positions:
            continue

        data = fetch_stock_data(symbol)
        if not data:
            continue

        opportunity = evaluate_stock_opportunity(data)
        if opportunity:
            active_positions[symbol] = {
                "entry_price": opportunity["price"],
                "pilot_only": opportunity["is_pilot"],
                "stop_loss": opportunity["stop_loss"],
                "target_1": opportunity["target_1"],
                "zero_risk_active": False
            }

            alert_msg = (
                f"🎯 **تنبيه فرصة دخول جديدة ({opportunity['strategy']})**\n\n"
                f"📌 **السهم:** `{opportunity['symbol']}`\n"
                f"💵 **سعر الدخول اللحظي:** `{opportunity['price']} ج.م`\n"
                f"📊 **حجم السيولة (RVOL):** `{opportunity['rvol']}x` | **مؤشر الزخم (RSI):** `{opportunity['rsi']}`\n"
                f"📈 **نسبة التغير الحالية:** `+{opportunity['change_pct']}%` | **التقييم:** `{opportunity['score']}/100`\n\n"
                f"🧱 **حجم الدفعة الموصى به:** `{opportunity['allocation']}`\n"
                f"🛡️ **وقف الخسارة المبدئي:** `{opportunity['stop_loss']} ج.م`\n"
                f"🎯 **الهدف الأول:** `{opportunity['target_1']} ج.م` (+4%)\n\n"
                f"💡 *ملاحظة: سيتم رفع وقف الخسارة تلقائياً إلى Zero-Risk وتأكيد التعزيز عند +2% صعود.*"
            )
            send_telegram_alert(alert_msg)

# ==========================================
# 7. نقطة الإدخال المخصصة لـ Cron Job
# ==========================================
def main():
    """
    نقطة التشغيل المخصصة لجدولة Cron Job:
    تقوم بقراءة وقت القاهرة، التنفيذ مرة واحدة، ثم الإغلاق الفوري للمحافظة على الموارد.
    """
    now_cairo = datetime.now(EGYPT_TZ)
    current_time_str = now_cairo.strftime("%H:%M")
    weekday = now_cairo.weekday()

    # أيام التداول بالبورصة المصرية: الأحد (6)، الإثنين (0)، الثلاثاء (1)، الأربعاء (2)، الخميس (3)
    is_trading_day = (weekday in [6, 0, 1, 2, 3])

    if is_trading_day and "10:00" <= current_time_str <= "14:15":
        run_market_scan()
        logging.info(f"✅ [{current_time_str} مصر] اكتمل فحص الجلسة بنجاح. إنهاء المهمة حتى الفحص التالي.")
    else:
        logging.info(f"⏸️ [{current_time_str} مصر] خارج ساعات تداول البورصة المصرية. إنهاء المهمة.")

if __name__ == "__main__":
    main()
        
