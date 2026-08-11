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
# 2. إدارة الذاكرة والتحديث في GitHub
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
        "total_trades": 0,
        "winning_trades": 0,
        "win_rate": 100.0,
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
# 3. دالة فحص الجاهزية عند الإقلاع (تُرسل مرة واحدة يومياً)
# ===========================================================
def run_startup_verification():
    now_cairo_dt = datetime.now(CAIRO_TZ)
    today_str = now_cairo_dt.strftime('%Y-%m-%d')
    now_cairo_formatted = now_cairo_dt.strftime('%Y-%m-%d %H:%M:%S')

    dna_memory = load_json_local(DNA_FILE, {})
    last_startup_date = dna_memory.get("_sys_meta", {}).get("last_startup_date", "")

    # إذا تم إرسال التقرير اليوم بالفعل، نكتفي بالـ Logging دون إرسال تليجرام
    if last_startup_date == today_str:
        logging.info("ℹ️ تم إرسال تقرير جاهزية النظام لهذا اليوم مسبقاً.")
        return True

    logging.info("⚙️ بدء فحص جاهزية النظام والربط لليوم الجديد...")

    if not GITHUB_TOKEN or not GITHUB_REPO:
        msg = "⚠️ *تنبيه بدء التشغيل:*\nمتغيرات GitHub غير مكتملة."
        logging.warning(msg)
        send_telegram_direct(msg)
        return False

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{DNA_FILE}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code != 200:
            msg = f"❌ *تنبيه خطأ الربط:*\nفشل الوصول إلى `{DNA_FILE}` على GitHub.\nرمز الحالة: `{res.status_code}`"
            logging.error(msg)
            send_telegram_direct(msg)
            return False

        file_info = res.json()
        sha = file_info.get("sha")

        # تحديث تاريخ آخر إرسال لتقرير الإقلاع
        if "_sys_meta" not in dna_memory: dna_memory["_sys_meta"] = {}
        dna_memory["_sys_meta"]["last_startup_date"] = today_str
        
        content_str = json.dumps(dna_memory, ensure_ascii=False, indent=2)
        encoded_content = base64.b64encode(content_str.encode("utf-8")).decode("utf-8")

        test_payload = {
            "message": f"system: v2 daily startup check [{today_str}]",
            "content": encoded_content,
            "sha": sha
        }

        put_res = requests.put(url, headers=headers, json=test_payload, timeout=15)

        if put_res.status_code in [200, 201]:
            msg = (
                "✅ *تقرير جاهزية النظام والمحرك المطور (V2):*\n\n"
                f"🕒 **توقيت الفحص:** `{now_cairo_formatted}`\n"
                f"📦 **المستودع:** `{GITHUB_REPO}`\n"
                "• **الاتصال بـ GitHub:** *ناجح (200 OK)*\n"
                "• **تحليل الأطر التراكمية (MTF):** *مفعل (1D + 15M)*\n"
                "• **إدارة المخاطر الحركية (ATR):** *مفعلة*\n"
                "• **إرسال التليجرام:** *متصل ومعتمد*\n\n"
                "🚀 *البوت الذكي جاهز لبدء الجلسة باقتناصات متعددة الأطر!*"
            )
            logging.info("✅ تم إرسال تقرير جاهزية اليوم بنجاح!")
            send_telegram_direct(msg)
            save_json_local(DNA_FILE, dna_memory)
            return True
        else:
            msg = f"❌ *خطأ في صلاحيات الكتابة:* `{put_res.status_code}`"
            logging.error(msg)
            send_telegram_direct(msg)
            return False

    except Exception as e:
        msg = f"❌ *حدث خطأ أثناء فحص جاهزية النظام:* `{str(e)}`"
        logging.error(msg)
        send_telegram_direct(msg)
        return False

# ===========================================================
# 4. جلب البيانات المتقدمة والتحليل متعدد الأطر (MTF Analysis)
# ===========================================================
def fetch_stock_data_safe(symbol, max_retries=3):
    for attempt in range(max_retries):
        try:
            handler_15m = TA_Handler(
                symbol=symbol,
                screener="egypt",
                exchange="EGX",
                interval=Interval.INTERVAL_15_MINUTES
            )
            analysis_15m = handler_15m.get_analysis()
            ind_15m = analysis_15m.indicators

            handler_1d = TA_Handler(
                symbol=symbol,
                screener="egypt",
                exchange="EGX",
                interval=Interval.INTERVAL_1_DAY
            )
            analysis_1d = handler_1d.get_analysis()
            ind_1d = analysis_1d.indicators

            close = ind_15m.get("close", 0)
            open_p = ind_15m.get("open", 0)
            volume = ind_15m.get("volume", 0)
            rsi = ind_15m.get("RSI", 50)
            ema25 = ind_15m.get("EMA25", 0)
            ema50 = ind_15m.get("EMA50", 0)

            atr = ind_15m.get("ATR", close * 0.02)
            if not atr or atr <= 0: atr = close * 0.02

            close_1d = ind_1d.get("close", close)
            ema50_1d = ind_1d.get("EMA50", close_1d)
            is_daily_bullish = close_1d >= ema50_1d

            change_pct = ((close - open_p) / open_p * 100) if open_p > 0 else 0
            volume_sma20 = ind_15m.get("volume.SMA20", volume)
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
                "atr": atr,
                "is_daily_bullish": is_daily_bullish,
                "is_green": close > open_p
            }
        except Exception as e:
            if "429" in str(e) or "Too Many Requests" in str(e):
                time.sleep((attempt + 1) * 2.0)
            else:
                break
    return None

def calculate_atr_portfolio_plan(close_price, atr, rsi):
    stop_loss = round(max(close_price * 0.93, close_price - (1.5 * atr)), 2)
    t1 = round(close_price + (1.2 * atr), 2)
    t2 = round(close_price + (2.5 * atr), 2)
    t3 = round(close_price + (4.0 * atr), 2)

    volatility_ratio = (atr / close_price) * 100
    if volatility_ratio > 3.0 or rsi > 70:
        position_size = "7% - 9% (تذبذب عالٍ - إدارة مخاطر مشددة)"
    elif volatility_ratio < 1.5:
        position_size = "12% - 15% (سهم مستقر - حجم قياسي)"
    else:
        position_size = "10% - 12% (تذبذب متوازن)"

    return {
        "stop_loss": stop_loss,
        "t1": t1,
        "t2": t2,
        "t3": t3,
        "position_size": position_size,
        "atr": round(atr, 2)
    }

def evaluate_stock_with_dna(data):
    sym = data["symbol"]
    dna = get_stock_dna(sym)
    now_cairo = datetime.now(CAIRO_TZ)

    if not data["is_daily_bullish"] and data["change_pct"] < 2.5:
        return {"type": "None", "score": 0, "instant": False}

    min_score_required = dna["min_score"]
    if dna["total_trades"] >= 3 and dna["win_rate"] < 50.0:
        min_score_required += 10

    is_opening_session = (now_cairo.hour == 10 and now_cairo.minute <= 30)
    min_rvol_threshold = dna["min_rvol"] * 1.3 if is_opening_session else dna["min_rvol"]

    rsi, rvol, change_pct, close = data["rsi"], data["rvol"], data["change_pct"], data["close"]

    if change_pct >= 8.5:
        return {"type": "None", "score": 0, "instant": False}

    if (change_pct >= 2.0 and rvol >= min_rvol_threshold and (dna["rsi_min"] <= rsi <= dna["rsi_max"]) and data["is_daily_bullish"]):
        return {"type": "Super MTF Breakout 🚀", "score": 98, "instant": True}

    score = 0
    if dna["rsi_min"] <= rsi <= dna["rsi_max"]: score += 25
    if close > data["ema25"]: score += 20
    if close > data["ema50"]: score += 20
    if data["is_daily_bullish"]: score += 20
    if data["is_green"]: score += 15

    if score >= min_score_required and rvol >= min_rvol_threshold:
        return {"type": "Regular Trend 📈", "score": score, "instant": False}

    return {"type": "None", "score": score, "instant": False}

# ===========================================================
# 5. محرك متابعة وتحديث الصفقات المفتوحة
# ===========================================================
def track_active_trades(all_data):
    active_trades = load_json_local(ACTIVE_TRADES_FILE, {})
    dna_memory = load_json_local(DNA_FILE, {})
    updated = False

    for stock, trade in list(active_trades.items()):
        if stock not in all_data: continue
        current_price = all_data[stock]["close"]
        mb_name = EGX33_SYMBOLS_MAP.get(stock, stock)
        dna = dna_memory.get(stock, get_stock_dna(stock))

        if not trade.get("t1_hit") and current_price >= trade["t1"]:
            trade["t1_hit"] = True
            trade["current_stop"] = trade["entry_price"]
            updated = True

            if not trade.get("recorded_win"):
                dna["winning_trades"] += 1
                dna["total_trades"] += 1
                dna["win_rate"] = round((dna["winning_trades"] / dna["total_trades"]) * 100, 1)
                dna_memory[stock] = dna
                trade["recorded_win"] = True

            msg = (
                f"🎯 **تحديث هدف [الهدف الأول تحقق - ATR Plan]**\n\n"
                f"📌 **السهم:** `{mb_name}`\n"
                f"💵 **السعر الحالي:** {current_price} ج.م\n"
                f"📊 **نسبة نجاح السهم التاريخية:** `{dna['win_rate']}%`\n\n"
                f"✅ **الإجراءات المطلوبة:**\n"
                f"1️⃣ بيع **40%** لتأمين الأرباح.\n"
                f"2️⃣ رفع **وقف الخسارة** للمتبقي إلى سعر الدخول: `{trade['entry_price']} ج.م`."
            )
            send_telegram_direct(msg)

        elif trade.get("t1_hit") and not trade.get("t2_hit") and current_price >= trade["t2"]:
            trade["t2_hit"] = True
            trade["current_stop"] = trade["t1"]
            updated = True
            msg = (
                f"🚀 **تحديث هدف [الهدف الثاني تحقق]**\n\n"
                f"📌 **السهم:** `{mb_name}`\n"
                f"💵 **السعر الحالي:** {current_price} ج.م\n\n"
                f"✅ **الإجراءات المطلوبة:** بيع **30%** إضافية ورفع الستوب إلى `{trade['t1']} ج.م`."
            )
            send_telegram_direct(msg)

        elif trade.get("t2_hit") and not trade.get("t3_hit") and current_price >= trade["t3"]:
            trade["t3_hit"] = True
            updated = True
            msg = (
                f"🔥 **تحقيق أقصى هدف متوقع [الهدف الثالث]**\n\n"
                f"📌 **السهم:** `{mb_name}`\n"
                f"💵 **السعر الحالي:** {current_price} ج.م\n\n"
                f"✅ **الإجراءات:** بيع الكمية المتبقية بالكامل."
            )
            send_telegram_direct(msg)

        elif current_price <= trade.get("current_stop", trade["stop_loss"]):
            if not trade.get("recorded_win"):
                dna["total_trades"] += 1
                dna["win_rate"] = round((dna["winning_trades"] / dna["total_trades"]) * 100, 1)
                dna_memory[stock] = dna

            msg = (
                f"🛑 **تنبيه وقف الخسارة التأميني (ATR Stop)**\n\n"
                f"📌 **السهم:** `{mb_name}`\n"
                f"📉 **سعر الكسر:** {current_price} ج.م\n"
                f"🛡️ **مستوى الستوب المفعل:** {trade.get('current_stop', trade['stop_loss'])} ج.م\n\n"
                f"⚠️ **الإجراءات:** إغلاق المراكز المتبقية وتأمين المحفظة."
            )
            send_telegram_direct(msg)
            del active_trades[stock]
            updated = True

    if updated:
        save_json_local(ACTIVE_TRADES_FILE, active_trades)
        save_file_to_github(ACTIVE_TRADES_FILE, active_trades, "🔄 Auto-update active trades tracker")
        save_json_local(DNA_FILE, dna_memory)
        save_file_to_github(DNA_FILE, dna_memory, "🧠 Auto-update Win Rates in Stock DNA")

# ===========================================================
# 6. تقرير نهاية اليوم والتكيف الذاتي (EOD Report)
# ===========================================================
def run_end_of_day_summary(all_data):
    dna_memory = load_json_local(DNA_FILE, {})
    active_trades = load_json_local(ACTIVE_TRADES_FILE, {})
    now_cairo_str = datetime.now(CAIRO_TZ).strftime('%Y-%m-%d')

    reports_log = []
    for sym, data in all_data.items():
        mb_name = EGX33_SYMBOLS_MAP.get(sym, sym)
        dna = dna_memory.get(sym, get_stock_dna(sym))

        change_pct = data["change_pct"]
        rvol = data["rvol"]

        if change_pct >= 3.0 and rvol < dna["min_rvol"]:
            dna["min_rvol"] = max(0.60, round(dna["min_rvol"] - 0.08, 2))
            reports_log.append(f"• `{mb_name}`: تعديل شرط السيولة آلياً إلى `{dna['min_rvol']}x`.")

        dna["learned_sessions"] += 1
        dna_memory[sym] = dna

    save_json_local(DNA_FILE, dna_memory)
    save_file_to_github(DNA_FILE, dna_memory, "🧠 Auto-update Stock DNA")

    # صياغة تقرير ختام الجلسة
    report = f"📋 **تقرير نهاية الجلسة اليومي ({now_cairo_str})**\n\n"
    report += f"📊 **عدد الأسهم المفحوصة:** `{len(all_data)}` سهم\n"
    report += f"💼 **الصفقات النشطة المستمرة للغد:** `{len(active_trades)}` صفقة\n\n"

    if active_trades:
        report += "📌 **تفاصيل الصفقات المفتوحة:**\n"
        for st, tr in active_trades.items():
            st_name = EGX33_SYMBOLS_MAP.get(st, st)
            report += f"• `{st_name}` | الدخول: `{tr['entry_price']}` | الستوب الحالي: `{tr.get('current_stop', tr['stop_loss'])}`\n"
        report += "\n"

    if reports_log:
        report += "🧠 **تعديلات الذاكرة والتكيف اليومي:**\n"
        report += "\n".join(reports_log[:6])
    else:
        report += "🧠 **حالة الذاكرة:** مستقرة تماماً ولا تتطلب تعديلات معايير."

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

            # اقتناص فرصة جديدة
            if eval_res["instant"] and stock not in active_trades:
                mb_name = EGX33_SYMBOLS_MAP.get(stock, stock)
                plan = calculate_atr_portfolio_plan(data["close"], data["atr"], data["rsi"])

                active_trades[stock] = {
                    "entry_price": data["close"],
                    "stop_loss": plan["stop_loss"],
                    "current_stop": plan["stop_loss"],
                    "t1": plan["t1"],
                    "t2": plan["t2"],
                    "t3": plan["t3"],
                    "t1_hit": False,
                    "t2_hit": False,
                    "t3_hit": False,
                    "recorded_win": False
                }
                save_json_local(ACTIVE_TRADES_FILE, active_trades)
                save_file_to_github(ACTIVE_TRADES_FILE, active_trades, f"➕ Add active trade: {stock}")

                daily_status = "مؤكد إيجابي 🟢" if data['is_daily_bullish'] else "تذبذب/محايد 🟡"

                msg = (
                    f"🚀 **إشارة اقتناص فوري مطورة ({eval_res['type']})**\n\n"
                    f"📌 **السهم:** `{mb_name}`\n"
                    f"💵 **سعر الدخول:** {data['close']} ج
