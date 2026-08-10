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
GITHUB_REPO = os.environ.get("GITHUB_REPO", "") # mishmish20052-cloud/EGX-bot
DNA_FILE = "stocks_dna_memory.json"

# قائمة الأسهم المراقبة شاملة
EGX33_SYMBOLS_MAP = {
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
    "CICH": "CICH.CA (سي آي كابيتال)", 
    "EALR": "EALR.CA (مصر للألومنيوم)"
}

STOCKS = list(EGX33_SYMBOLS_MAP.keys())

# ===========================================================
# 2. إدارة الذاكرة الدائمة عبر GitHub API
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

def save_dna_to_github(data):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        save_json_local(DNA_FILE, data)
        return

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{DNA_FILE}"
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
        logging.error(f"خطأ قراءة sha من GitHub: {e}")

    content_str = json.dumps(data, ensure_ascii=False, indent=2)
    content_encoded = base64.b64encode(content_str.encode('utf-8')).decode('utf-8')

    payload = {
        "message": "🧠 Auto-learned Autonomous Stock DNA Update",
        "content": content_encoded
    }
    if sha:
        payload["sha"] = sha

    try:
        put_res = requests.put(url, headers=headers, json=payload, timeout=15)
        if put_res.status_code in [200, 201]:
            logging.info("✅ تم تحديث الذاكرة الدائمة تلقائياً في GitHub بنجاح!")
        else:
            logging.error(f"فشل الحفظ في GitHub: {put_res.text}")
    except Exception as e:
        logging.error(f"خطأ اتصال أثناء التحديث في GitHub: {e}")

def get_stock_dna(symbol):
    """
    يقوم بجلب الذاكرة الخاصة بالسهم، وإن لم تكن موجودة يُنشئ له DNA افتراضي يتم تعديله آلياً لاحقاً
    """
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
# 3. جلب البيانات وتقييم الأسهم
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

def evaluate_stock_with_dna(data):
    sym = data["symbol"]
    dna = get_stock_dna(sym)
    
    rsi, rvol, change_pct, close = data["rsi"], data["rvol"], data["change_pct"], data["close"]
    
    # تجنب الشراء القريب جداً من القمة المرتفعة للجلسة
    if change_pct >= 8.5:
        return {"type": "None", "score": 0, "instant": False}

    # 1. التقاط ديناميكي تلقائي لأي اختراق صاعد دون شروط يدوية
    if (change_pct >= 2.0 and rvol >= dna["min_rvol"] and (dna["rsi_min"] <= rsi <= dna["rsi_max"])) or \
       (change_pct >= 1.5 and rvol >= (dna["min_rvol"] * 1.25)):
        return {"type": "Super Breakout 🚀", "score": 95, "instant": True}

    # 2. تقييم الاتجاه العام بالنقاط
    score = 0
    if dna["rsi_min"] <= rsi <= dna["rsi_max"]: score += 30
    if close > data["ema25"]: score += 20
    if close > data["ema50"]: score += 20
    if data["macd"] > data["macd_signal"]: score += 15
    if data["is_green"]: score += 15

    if score >= dna["min_score"] and rvol >= dna["min_rvol"]:
        return {"type": "Regular Trend 📈", "score": score, "instant": False}

    return {"type": "None", "score": score, "instant": False}

# ===========================================================
# 4. محرك التعلم الذاتي والتكيّف المستمر
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
        
        # إذا صعد السهم بـ 3% أو أكثر وكان شرط السيولة منع التنبيه، يتم خفض الشرط للسهم تلقائياً
        if change_pct >= 3.0 and rvol < dna["min_rvol"]:
            old_rvol = dna["min_rvol"]
            dna["min_rvol"] = max(0.60, round(dna["min_rvol"] - 0.1, 2))
            dna["missed_trades"] += 1
            reports_log.append(f"• `{mb_name}`: حقق صعود (+{round(change_pct,2)}%) ➔ تم تخفيض شرط السيولة آلياً من `{old_rvol}x` إلى `{dna['min_rvol']}x`.")

        # إذا تجاوز السهم نطاق RSI المعتاد مع استمرار الصعود، يتم توسيع النطاق آلياً
        if change_pct >= 3.5 and rsi > dna["rsi_max"] and dna["rsi_max"] < 82.0:
            dna["rsi_max"] = min(82.0, round(dna["rsi_max"] + 2.0, 1))
            reports_log.append(f"• `{mb_name}`: توسيع نطاق RSI المسموح آلياً إلى `{dna['rsi_max']}`.")

        dna["learned_sessions"] += 1
        dna_memory[sym] = dna

    # حفظ وتزامن التعديلات في ملف الـ DNA وفي GitHub
    save_json_local(DNA_FILE, dna_memory)
    save_dna_to_github(dna_memory)

    if reports_log:
        report = "🧠 **تكيّف تلقائي للذاكرة (Autonomous Stock DNA Update)**\n\n"
        report += "قام البوت برصد حركة الأسهم وتعديل معاييرها آلياً وحفظها في GitHub:\n\n"
        report += "\n".join(reports_log[:8])
        send_telegram_direct(report)

# ===========================================================
# 5. الدورة الرئيسية للفحص
# ===========================================================
def run_pipeline():
    now_cairo = datetime.now(CAIRO_TZ)
    logging.info(f"🔍 تشغيل الفحص الذكي الذاتي [{now_cairo.strftime('%H:%M')} مصر]...")
    
    all_data = {}
    for stock in STOCKS:
        data = fetch_stock_data_safe(stock)
        if data:
            all_data[stock] = data
            eval_res = evaluate_stock_with_dna(data)
            
            if eval_res["instant"]:
                mb_name = EGX33_SYMBOLS_MAP.get(stock, stock)
                msg = (
                    f"🚀 **إشارة اقتناص فوري ({eval_res['type']})**\n\n"
                    f"السهم: `{mb_name}`\n"
                    f"السعر الحالي: {data['close']}\n"
                    f"نسبة التغير: +{round(data['change_pct'], 2)}%\n"
                    f"السيولة النسبية: {round(data['rvol'], 2)}x\n"
                    f"مؤشر RSI: {round(data['rsi'], 1)}"
                )
                send_telegram_direct(msg)
        time.sleep(0.3)

    # تشغيل التعلم والتحليل الذاتي عند الساعة 02:15 ظهراً وحفظ التحديثات تلقائياً
    if now_cairo.hour == 14 and now_cairo.minute >= 15:
        run_deep_learning_analysis(all_data)

if __name__ == "__main__":
    run_pipeline()
    sys.exit(0)
    
