import os
import sys
import time
import json
import random
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

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8222819132:AAFmMjXCVnUFU8JUEcsujHKVjdmrJ1_zzPg")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "5418506244")

DATA_FILE = "daily_history.json"
PENDING_FILE = "pending_signals.json"
TRADES_FILE = "today_trades.json"
CONFIG_FILE = "strategy_config.json"

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
# 2. إدارة التخزين واستراتيجية التعلم
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

def send_telegram_direct(message: str):
    """إرسال مباشر عبر API التليجرام وبدون حاجة للتنصت البطيء"""
    if not BOT_TOKEN or not CHAT_ID: return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code != 200:
            logging.error(f"خطأ تليجرام: {res.text}")
    except Exception as e:
        logging.error(f"خطأ اتصال بتليجرام: {e}")

def get_learned_config():
    default_config = {
        "min_rvol_trend": 1.1,
        "min_score": 65,
        "rsi_min": 45.0,
        "rsi_max": 68.0,
        "use_macd_filter": True,
        "learned_days": 0
    }
    return load_json(CONFIG_FILE, default_config)

# ===========================================================
# 3. جلب البيانات والتوقيت
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
                "open": open_p,
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
    now_cairo = datetime.now(CAIRO_TZ)
    if now_cairo.weekday() in [4, 5]: # عطلة الجمعة والسبت
        return False
    start_time = now_cairo.replace(hour=10, minute=0, second=0, microsecond=0)
    end_time = now_cairo.replace(hour=14, minute=30, second=0, microsecond=0)
    return start_time <= now_cairo <= end_time

def evaluate_stock(data, config):
    rsi, rvol, change_pct, close = data["rsi"], data["rvol"], data["change_pct"], data["close"]
    
    if change_pct >= 8.5:
        return {"type": "None", "score": 0}

    if rvol >= 2.5 and change_pct >= 1.5 and (50.0 <= rsi <= 72.0):
        return {"type": "Super Breakout 🚀", "score": 95, "instant": True}

    score = 0
    if config["rsi_min"] <= rsi <= config["rsi_max"]: score += 30
    if close > data["ema25"]: score += 20
    if close > data["ema50"]: score += 20
    if config.get("use_macd_filter", True) and data["macd"] > data["macd_signal"]: score += 15
    if data["is_green"]: score += 15

    if score >= config["min_score"] and rvol >= config["min_rvol_trend"]:
        return {"type": "Regular Trend 📈", "score": score, "instant": False}

    return {"type": "None", "score": score, "instant": False}

# ===========================================================
# 4. محرك التحليل والتشخيص والتكيف التلقائي (Post-Market AI Engine)
# ===========================================================
def run_post_market_analysis(all_data, history):
    config = get_learned_config()
    trades = load_json(TRADES_FILE, {})
    
    correct_signals = []
    false_signals = []
    missed_opportunities = []

    for sym, data in all_data.items():
        mb_name = EGX33_SYMBOLS_MAP.get(sym, sym)
        hist_info = history.get(sym, {})
        start_p = hist_info.get("first_price", data["open"])
        curr_p = data["close"]
        
        actual_change = round(((curr_p - start_p) / start_p) * 100, 2) if start_p > 0 else 0.0
        max_score = hist_info.get("max_score", 0)

        if sym in trades:
            entry_p = trades[sym]["entry"]
            perf = round(((curr_p - entry_p) / entry_p) * 100, 2)
            if perf >= 1.5:
                correct_signals.append(f"• `{mb_name}`: دخول {entry_p} ➔ إغلاق {curr_p} (+{perf}%)")
            else:
                false_signals.append(
                    f"• `{mb_name}`: دخول {entry_p} ➔ إغلاق {curr_p} ({perf}%)\n"
                    f"  🔍 السبب الفني: سيولة كاذبة (RVOL: {round(data['rvol'],2)}x) وبدء جني أرباح."
                )

        elif actual_change >= 2.0 and max_score < config["min_score"]:
            reasons = []
            if data["rvol"] < config["min_rvol_trend"]:
                reasons.append(f"سيولة أقل من المطلوب ({round(data['rvol'],2)}x < {config['min_rvol_trend']}x)")
            if not (config["rsi_min"] <= data["rsi"] <= config["rsi_max"]):
                reasons.append(f"RSI خارج النطاق ({round(data['rsi'],1)})")
            if data["close"] <= data["ema25"]:
                reasons.append("السعر أسفل EMA25")

            reason_str = " و ".join(reasons) if reasons else "تقييم عام أقل من الحد المطلوب"
            missed_opportunities.append(
                f"• `{mb_name}`: ارتفع (+{actual_change}%)\n"
                f"  🔍 سبب التفويت: {reason_str}"
            )

    adjustments_made = []
    if len(missed_opportunities) >= 2 and config["min_rvol_trend"] > 0.9:
        old_val = config["min_rvol_trend"]
        config["min_rvol_trend"] = round(config["min_rvol_trend"] - 0.1, 2)
        adjustments_made.append(f"• خفض شرط السيولة الأدنى (RVOL): من `{old_val}x` إلى `{config['min_rvol_trend']}x` لعدم تفويت الأسهم الصاعدة.")

    if len(false_signals) >= 1 and config["min_score"] < 75:
        old_score = config["min_score"]
        config["min_score"] += 5
        adjustments_made.append(f"• رفع حد التقييم الأدنى (Score): من `{old_score}` إلى `{config['min_score']}` لفلترة الإشارات الكاذبة.")

    config["learned_days"] += 1
    save_json(CONFIG_FILE, config)

    green_count = sum(1 for d in all_data.values() if d["is_green"])
    avg_rvol = sum(d["rvol"] for d in all_data.values()) / len(all_data) if all_data else 1.0

    report = "🏁 **تقرير تحليل الجلسة والتعلم الآلي (Post-Market AI Report)**\n\n"
    report += f"📊 **ملخص البورصة اليوم:** أسهم صاعدة {green_count}/{len(all_data)} | متوسط السيولة: {round(avg_rvol, 2)}x\n\n"
    
    report += "✅ **1. الإشارات الصحيحة (الرابحة):**\n"
    report += "\n".join(correct_signals) if correct_signals else "لا توجد إشارات ناجحة اليوم.\n"
    report += "\n\n"

    report += "❌ **2. الإشارات الخاطئة وتجميع الأسباب:**\n"
    report += "\n".join(false_signals) if false_signals else "لا توجد إشارات كاذبة اليوم.\n"
    report += "\n\n"

    report += "🚀 **3. الفرص الضائعة وتشخيص أسباب عدم التقاطها:**\n"
    report += "\n".join(missed_opportunities[:4]) if missed_opportunities else "لم تُفقد أي فرص صاعدة كبيرة اليوم.\n"
    report += "\n\n"

    report += f"🧠 **4. التعديلات التلقائية على المقاييس (أيام التعلم: {config['learned_days']}):**\n"
    report += "\n".join(adjustments_made) if adjustments_made else "• المقاييس الحالية أثبتت كفاءتها ولم تتطلب أي تعديل للجلسة القادمة."

    send_telegram_direct(report)
    logging.info("📜 تم إرسال تقرير التحليل والتعلم بنجاح.")

# ===========================================================
# 5. المحرك الرئيسي للفحص
# ===========================================================
def run_smart_scan(force=False):
    global audit_sent_today, last_audit_date
    now_cairo = datetime.now(CAIRO_TZ)
    today_str = now_cairo.strftime("%Y-%m-%d")

    if last_audit_date != today_str:
        audit_sent_today = False
        last_audit_date = today_str

    if not force and not is_market_open():
        msg = f"⏳ [{now_cairo.strftime('%H:%M')} مصر] السوق مغلق حالياً."
        logging.info(msg)
        return msg

    config = get_learned_config()
    logging.info(f"🔍 بدء الفحص الذكي للأسهم...")
    
    all_data = {}
    history = load_json(DATA_FILE, {})

    for stock in STOCKS:
        data = fetch_stock_data_safe(stock)
        if data:
            all_data[stock] = data
            eval_res = evaluate_stock(data, config)
            
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

    # تشغيل تقرير التحليل الشامل والتعلم عند نهاية الجلسة (14:15 - 14:30)
    if not force and now_cairo.hour == 14 and now_cairo.minute >= 15 and not audit_sent_today:
        run_post_market_analysis(all_data, history)
        audit_sent_today = True

    if force:
        run_post_market_analysis(all_data, history)
        return "✅ تم تنفيذ التحليل الشامل وتحديث المقاييس بنجاح."

    summary = f"✅ اكتمل الفحص. تم فحص {len(all_data)} سهم."
    logging.info(summary)
    return summary

# ===========================================================
# 6. نقطة الانطلاق النظيفة (مخصصة للـ Cron Job)
# ===========================================================
if __name__ == "__main__":
    # عند استدعاء السكربت بواسطة Cron Job
    # يتم إجراء الفحص الخفيف وإرسال التقارير ثم الخروج المباشر بسلام
    run_smart_scan(force=False)
    logging.info("🏁 انتهاء المهمة وخروج فوري لعدم حدوث تضارب.")
    sys.exit(0)
    
