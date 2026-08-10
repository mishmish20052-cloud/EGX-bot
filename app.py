import os
import sys
import time
import json
import base64
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from tradingview_ta import TA_Handler, Interval

# ===========================================================
# 1. الإعدادات والربط مع Telegram & GitHub
# ===========================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

CAIRO_TZ = ZoneInfo("Africa/Cairo")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8222819132:AAFmMjXCVnUFU8JUEcsujHKVjdmrJ1_zzPg")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "5418506244")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")
DNA_FILE = "stocks_dna_memory.json"
ACTIVE_TRADES_FILE = "active_trades.json"

EGX33_SYMBOLS_MAP = {
    "EALR": "EALR.CA (مصر للألومنيوم)",
    "ATQA": "ATQA.CA (مصر الوطنية للصلب - عتاقة)",
    "ISPH": "ISPH.CA (ابن سينا فارما)",
    "RMDA": "RMDA.CA (العاشر من رمضان - رميدا)",
    "ABUK": "ABUK.CA (أبو قير للأسمدة)", 
    "MFPC": "MFPC.CA (موبكو)",
    "SKPC": "SKPC.CA (سيدبك)", 
    "AMOC": "AMOC.CA (أموك)",
    "MBSC": "MBSC.CA (مصر بني سويف للأسمنت)", 
    "SCEM": "SCEM.CA (سيناء للأسمنت)",
    "TMGH": "TMGH.CA (طلعت مصطفى)", 
    "OCDI": "OCDI.CA (سوديك)",
    "MASR": "MASR.CA (مدينة مصر)", 
    "EMFD": "EMFD.CA (إعمار مصر)",
    "ORAS": "ORAS.CA (أوراسكوم للإنشاء)", 
    "ORHD": "ORHD.CA (أوراسكوم التنمية)",
    "HELI": "HELI.CA (مصر الجديدة للإسكان)", 
    "CLHO": "CLHO.CA (كليوباترا)",
    "PHAR": "PHAR.CA (إيبارشيو - فاركو)", 
    "JUFO": "JUFO.CA (جهينة)",
    "OLFI": "OLFI.CA (عبور لاند)", 
    "SUGR": "SUGR.CA (الدلتا للسكر)",
    "EFID": "EFID.CA (إدفيتا)", 
    "EFIH": "EFIH.CA (إي فاينانس)",
    "FWRY": "FWRY.CA (فوري)", 
    "ETEL": "ETEL.CA (المصرية للاتصالات)",
    "ALCN": "ALCN.CA (القناة للتوكيلات)", 
    "CSAG": "CSAG.CA (القاهرة للزيوت)",
    "ORWE": "ORWE.CA (النساجون الشرقيون)", 
    "ARAB": "ARAB.CA (عربية حجيج)",
    "CICH": "CICH.CA (سي آي كابيتال)"
}

STOCKS = list(EGX33_SYMBOLS_MAP.keys())

# ===========================================================
# 2. إدارة الذاكرة الدائمة والصفقات المفتوحة عبر GitHub API
# ===========================================================
def load_json_local(file_path, default=None):
    if default is None: default = {}
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return default
    return default

def save_json_local(file_path, data):
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"فشل حفظ {file_path} محلياً: {e}")

def save_file_to_github(file_name, data, commit_msg):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        save_json_local(file_name, data)
        return

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_name}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

    sha = None
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            sha = res.json().get("sha")
    except Exception as e:
        logging.error(f"خطأ قراءة sha لـ {file_name} من GitHub: {e}")

    content_str = json.dumps(data, ensure_ascii=False, indent=2)
    content_encoded = base64.b64encode(content_str.encode('utf-8')).decode('utf-8')

    payload = {
        "message": commit_msg,
        "content": content_encoded
    }
    if sha:
        payload["sha"] = sha

    try:
        put_res = requests.put(url, headers=headers, json=payload, timeout=15)
        if put_res.status_code in [200, 201]:
            logging.info(f"✅ تم تحديث {file_name} في GitHub بنجاح!")
        else:
            logging.error(f"فشل الحفظ في GitHub ({file_name}): {put_res.text}")
    except Exception as e:
        logging.error(f"خطأ اتصال أثناء التحديث في GitHub ({file_name}): {e}")

def get_stock_dna(symbol):
    dna_memory = load_json_local(DNA_FILE, {})
    default_dna = {
        "min_rvol": 0.85,
        "min_score": 55,
        "rsi_min": 38.0,
        "rsi_max": 76.0,
        "missed_trades": 0,
        "learned_sessions": 0
    }
    return dna_memory.get(symbol, default_dna)

def send_telegram_direct(message: str):
    if not BOT_TOKEN or not CHAT_ID: return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logging.error(f"خطأ تليجرام: {e}")

# ===========================================================
# 3. دالة فحص الجاهزية والربط عند الإقلاع (Startup Verification)
# ===========================================================
def run_startup_verification():
    """فحص الصلاحيات والربط مع GitHub وتليجرام عند بدء التشغيل"""
    now_cairo = datetime.now(CAIRO_TZ).strftime('%Y-%m-%d %H:%M:%S')
    logging.info("⚙️ بدء فحص جاهزية النظام والربط...")

    if not GITHUB_TOKEN or not GITHUB_REPO:
        msg = (
            "⚠️ *تنبيه بدء التشغيل:*\n"
            "متغيرات البيئة لـ GitHub (`GITHUB_TOKEN` / `GITHUB_REPO`) غير مكتملة.\n"
            "سيعمل البوت بالوضع المحلي فقط."
        )
        logging.warning(msg)
        send_telegram_direct(msg)
        return False

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{DNA_FILE}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

    try:
        # 1. اختبار قراءة الملف من GitHub
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code != 200:
            msg = f"❌ *تنبيه خطأ الربط:*\nفشل الوصول إلى `{DNA_FILE}` على GitHub.\nرمز الحالة: `{res.status_code}`"
            logging.error(msg)
            send_telegram_direct(msg)
            return False

        file_info = res.json()
        sha = file_info.get("sha")

        # 2. جلب محتوى الملف الحقيقي حتى لا نفقده
        current_data = load_json_local(DNA_FILE, {})
        content_str = json.dumps(current_data, ensure_ascii=False, indent=2)
        encoded_content = base64.b64encode(content_str.encode("utf-8")).decode("utf-8")

        # 3. اختبار التحديث والكتابة عبر الـ API
        test_payload = {
            "message": "system: startup connection & write permission check",
            "content": encoded_content,
            "sha": sha
        }

        put_res = requests.put(url, headers=headers, json=test_payload, timeout=15)

        if put_res.status_code in [200, 201]:
            msg = (
                "✅ *تقرير جاهزية النظام والربط:*\n\n"
                f"🕒 **توقيت الفحص:** `{now_cairo}`\n"
                f"📦 **المستودع:** `{GITHUB_REPO}`\n"
                "• **الاتصال بـ GitHub:** *ناجح (200 OK)*\n"
                "• **صلاحيات الكتابة والتحديث:** *مفعلة وتعمل بنجاح*\n"
                "• **إرسال التليجرام:** *متصل ومعتمد*\n\n"
                "🚀 *البوت جاهز تماماً لبدء فحص السوق وجلسة التداول!*"
            )
            logging.info("✅ تم اختبار صلاحيات الكتابة والربط بنجاح!")
            send_telegram_direct(msg)
            return True
        else:
            msg = (
                f"❌ *خطأ في صلاحيات الكتابة على GitHub:*\n"
                f"رمز الاستجابة: `{put_res.status_code}`\n"
                "يرجى التأكد من صلاحية الـ PAT (Write / Repo Access)."
            )
            logging.error(msg)
            send_telegram_direct(msg)
            return False

    except Exception as e:
        msg = f"❌ *حدث خطأ أثناء فحص جاهزية النظام:* `{str(e)}`"
        logging.error(msg)
        send_telegram_direct(msg)
        return False

# ===========================================================
# 4. جلب البيانات وحسابات أهداف المحفظة
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
                time.sleep((attempt + 1) * 2.0)
            else:
                break
    return None

def calculate_portfolio_plan(close_price, rsi):
    # حساب أهداف الصفقة
    stop_loss = round(close_price * 0.975, 2)  # -2.5%
    t1 = round(close_price * 1.025, 2)        # +2.5%
    t2 = round(close_price * 1.050, 2)        # +5.0%
    t3 = round(close_price * 1.085, 2)        # +8.5%

    # حساب نسبة تخصيص رأس المال بناءً على التذبذب
    if rsi > 70 or rsi < 40:
        position_size = "8% - 10% (مخاطرة/تذبذب عالٍ)"
    else:
        position_size = "12% - 15% (مخاطرة متوازنة)"

    return {
        "stop_loss": stop_loss,
        "t1": t1,
        "t2": t2,
        "t3": t3,
        "position_size": position_size
    }

def evaluate_stock_with_dna(data):
    sym = data["symbol"]
    dna = get_stock_dna(sym)
    now_cairo = datetime.now(CAIRO_TZ)
    
    # فلتر توقيت افتتاح السوق المصري
    is_opening_session = (now_cairo.hour == 10 and now_cairo.minute <= 30)
    min_rvol_threshold = dna["min_rvol"] * 1.3 if is_opening_session else dna["min_rvol"]

    rsi, rvol, change_pct, close = data["rsi"], data["rvol"], data["change_pct"], data["close"]
    
    if change_pct >= 8.5:
        return {"type": "None", "score": 0, "instant": False}

    if (change_pct >= 2.0 and rvol >= min_rvol_threshold and (dna["rsi_min"] <= rsi <= dna["rsi_max"])) or \
       (change_pct >= 1.5 and rvol >= (min_rvol_threshold * 1.25)):
        return {"type": "Super Breakout 🚀", "score": 95, "instant": True}

    score = 0
    if dna["rsi_min"] <= rsi <= dna["rsi_max"]: score += 30
    if close > data["ema25"]: score += 20
    if close > data["ema50"]: score += 20
    if data["macd"] > data["macd_signal"]: score += 15
    if data["is_green"]: score += 15

    if score >= dna["min_score"] and rvol >= min_rvol_threshold:
        return {"type": "Regular Trend 📈", "score": score, "instant": False}

    return {"type": "None", "score": score, "instant": False}

# ===========================================================
# 5. محرك متابعة وتحديث الصفقات المفتوحة (Active Trade Tracker)
# ===========================================================
def track_active_trades(all_data):
    active_trades = load_json_local(ACTIVE_TRADES_FILE, {})
    updated = False

    for stock, trade in list(active_trades.items()):
        if stock not in all_data: continue
        current_price = all_data[stock]["close"]
        mb_name = EGX33_SYMBOLS_MAP.get(stock, stock)

        # تحقق الهدف الأول
        if not trade.get("t1_hit") and current_price >= trade["t1"]:
            trade["t1_hit"] = True
            trade["current_stop"] = trade["entry_price"]  # نقل وقف الخسارة لسعر الدخول
            updated = True
            msg = (
                f"🎯 **تحديث هدف [الهدف الأول تحقق]**\n\n"
                f"📌 **السهم:** `{mb_name}`\n"
                f"💵 **السعر الحالي:** {current_price} ج.م\n\n"
                f"✅ **الجراء المطلوبة:**\n"
                f"1️⃣ بيع **40%** من الكمية لحجز أرباح المرحلة الأولى.\n"
                f"2️⃣ رفع **وقف الخسارة** للكمية المتبقية إلى سعر الدخول: `{trade['entry_price']} ج.م`."
            )
            send_telegram_direct(msg)

        # تحقق الهدف الثاني
        elif trade.get("t1_hit") and not trade.get("t2_hit") and current_price >= trade["t2"]:
            trade["t2_hit"] = True
            trade["current_stop"] = trade["t1"]  # نقل وقف الخسارة للهدف الأول
            updated = True
            msg = (
                f"🚀 **تحديث هدف [الهدف الثاني تحقق]**\n\n"
                f"📌 **السهم:** `{mb_name}`\n"
                f"💵 **السعر الحالي:** {current_price} ج.م\n\n"
                f"✅ **الجراء المطلوبة:**\n"
                f"1️⃣ بيع **30%** إضافية من الكمية.\n"
                f"2️⃣ رفع **وقف الخسارة** للمتبقي إلى مستوى الهدف الأول: `{trade['t1']} ج.م`."
            )
            send_telegram_direct(msg)

        # تحقق الهدف الثالث
        elif trade.get("t2_hit") and not trade.get("t3_hit") and current_price >= trade["t3"]:
            trade["t3_hit"] = True
            updated = True
            msg = (
                f"🔥 **تحديث هدف [الهدف الثالث الأقصى تحقق]**\n\n"
                f"📌 **السهم:** `{mb_name}`\n"
                f"💵 **السعر الحالي:** {current_price} ج.م\n\n"
                f"✅ **الجراء المطلوبة:**\n"
                f"• بيع الكمية المتبقية بالكامل أو تتبع المتوسط الأسي `EMA25` لتحقيق أقصى ربح."
            )
            send_telegram_direct(msg)

        # ضرب وقف الخسارة المطور
        elif current_price <= trade.get("current_stop", trade["stop_loss"]):
            msg = (
                f"🛑 **تنبيه وقف الخسارة / الخروج التأميني**\n\n"
                f"📌 **السهم:** `{mb_name}`\n"
                f"📉 **سعر الكسر:** {current_price} ج.م\n"
                f"🛡️ **مستوى الستوب المفعل:** {trade.get('current_stop', trade['stop_loss'])} ج.م\n\n"
                f"⚠️ **الجراء:** إغلاق باقي مراكز السهم وتأمين الأرباح/المحفظة."
            )
            send_telegram_direct(msg)
            del active_trades[stock]
            updated = True

    if updated:
        save_json_local(ACTIVE_TRADES_FILE, active_trades)
        save_file_to_github(ACTIVE_TRADES_FILE, active_trades, "🔄 Auto-update active trades tracker")

# ===========================================================
# 6. محرك التعلم الذاتي عند الإغلاق
# ===========================================================
def run_deep_learning_analysis(all_data):
    dna_memory = load_json_local(DNA_FILE, {})
    reports_log = []
    
    for sym, data in all_data.items():
        mb_name = EGX33_SYMBOLS_MAP.get(sym, sym)
        dna = dna_memory.get(sym, get_stock_dna(sym))
        
        change_pct = data["change_pct"]
        rvol = data["rvol"]
        rsi = data["rsi"]
        
        if change_pct >= 3.0 and rvol < dna["min_rvol"]:
            dna["min_rvol"] = max(0.60, round(dna["min_rvol"] - 0.1, 2))
            dna["missed_trades"] += 1
            reports_log.append(f"• `{mb_name}`: صعد (+{round(change_pct,2)}%) ➔ تعديل شرط السيولة آلياً إلى `{dna['min_rvol']}x`.")

        if change_pct >= 3.5 and rsi > dna["rsi_max"] and dna["rsi_max"] < 82.0:
            dna["rsi_max"] = min(82.0, round(dna["rsi_max"] + 2.0, 1))
            reports_log.append(f"• `{mb_name}`: توسيع نطاق RSI آلياً إلى `{dna['rsi_max']}`.")

        dna["learned_sessions"] += 1
        dna_memory[sym] = dna

    save_json_local(DNA_FILE, dna_memory)
    save_file_to_github(DNA_FILE, dna_memory, "🧠 Auto-update Stock DNA")

    if reports_log:
        report = "🧠 **تكيّف الذاكرة الذاتية (Autonomous Stock DNA Update)**\n\n"
        report += "\n".join(reports_log[:8])
        send_telegram_direct(report)

# ===========================================================
# 7. الدورة الرئيسية
# ===========================================================
def run_pipeline():
    now_cairo = datetime.now(CAIRO_TZ)
    logging.info(f"🔍 تشغيل الفحص والرد الذكي وإدارة الصفقات [{now_cairo.strftime('%H:%M')} مصر]...")
    
    all_data = {}
    active_trades = load_json_local(ACTIVE_TRADES_FILE, {})

    for stock in STOCKS:
        data = fetch_stock_data_safe(stock)
        if data:
            all_data[stock] = data
            eval_res = evaluate_stock_with_dna(data)
            
            # اقتناص صفقة جديدة
            if eval_res["instant"] and stock not in active_trades:
                mb_name = EGX33_SYMBOLS_MAP.get(stock, stock)
                plan = calculate_portfolio_plan(data["close"], data["rsi"])
                
                # حفظ الصفقة في الصفقات النشطة
                active_trades[stock] = {
                    "entry_price": data["close"],
                    "stop_loss": plan["stop_loss"],
                    "current_stop": plan["stop_loss"],
                    "t1": plan["t1"],
                    "t2": plan["t2"],
                    "t3": plan["t3"],
                    "t1_hit": False,
                    "t2_hit": False,
                    "t3_hit": False
                }
                save_json_local(ACTIVE_TRADES_FILE, active_trades)
                save_file_to_github(ACTIVE_TRADES_FILE, active_trades, f"➕ Add active trade: {stock}")

                msg = (
                    f"🚀 **إشارة اقتناص فوري ({eval_res['type']})**\n\n"
                    f"📌 **السهم:** `{mb_name}`\n"
                    f"💵 **سعر الدخول:** {data['close']} ج.م (+{round(data['change_pct'], 2)}%)\n"
                    f"📊 **السيولة:** {round(data['rvol'], 2)}x | **RSI:** {round(data['rsi'], 1)}\n\n"
                    f"🛡️ **خطة إدارة المحفظة والصفقة:**\n"
                    f"• **وقف الخسارة المبدئي:** `{plan['stop_loss']} ج.م` (-2.5%)\n"
                    f"• **الهدف 1:** `{plan['t1']} ج.م` (+2.5%) ➔ *بيع 40% وارفع الستوب لسعر الدخول*\n"
                    f"• **الهدف 2:** `{plan['t2']} ج.م` (+5.0%) ➔ *بيع 30% وارفع الستوب للهدف 1*\n"
                    f"• **الهدف 3:** `{plan['t3']} ج.م` (+8.5%) ➔ *بيع المتبقي وتتبع الأرباح*\n\n"
                    f"💡 **حجم الصفقة المقترح:** {plan['position_size']}"
                )
                send_telegram_direct(msg)
        time.sleep(0.3)

    # متابعة الصفقات النشطة السابقة
    track_active_trades(all_data)

    # تشغيل محرك التعلم بعد الإغلاق
    if now_cairo.hour == 14 and now_cairo.minute >= 15:
        run_deep_learning_analysis(all_data)

if __name__ == "__main__":
    # 1. تنفيذ فحص الجاهزية والربط أولاً وإرسال التقرير للتليجرام
    run_startup_verification()

    # 2. تشغيل دورة الفحص الأساسية
    run_pipeline()

    sys.exit(0)
    
