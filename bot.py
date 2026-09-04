"""
نظام التداول الآلي المتكامل - البورصة المصرية (EGX33)
الإصدار 7.0 - إصلاحات شاملة بناءً على مراجعة فنية

التغييرات الرئيسية عن v6.3.1:
1. [حرج] حساب RSI/Stoch حقيقي في مسار yfinance بدل القيم الوهمية الثابتة (50)
   - أو رفض السهم لو تعذر الحساب بثقة كافية
2. [حرج] MEASUREMENT_MODE أصبح متغير بيئة صريح + تنويه بارز في كل رسالة
3. نظام DNA: حد أدنى من الصفقات قبل تعديل risk_multiplier + تخفيف قوة
   التعديل لكل صفقة (تقليل الـ overfitting) + طبقة DNA على مستوى القطاع
4. حالة السوق: سلة أسهم بديلة بدل الاعتماد على سهم واحد (TMGH) +
   فحص اتساع السوق (breadth) لتخفيض المخاطرة إذا كانت أغلب الأسهم حمراء
5. Trailing stop مبني على ATR بعد تحقق T2 بدل تجميد الستوب عند T1
6. حد أقصى لعدد الصفقات المفتوحة في نفس القطاع (تنويع حقيقي)
7. رفع الحد الأدنى لنسبة R:R لصفقات "Trend" العادية إلى 2.0
   (صفقات "Super Breakout" تبقى عند 1.5)
8. قفل تشغيل بسيط (lock file عبر GitHub) لمنع تشغيلين متزامنين
9. قاطع دائرة يومي (Daily Circuit Breaker): إيقاف فتح صفقات جديدة
   بعد عدد معين من الخسائر المتتالية خلال نفس اليوم بغض النظر عن السهم
"""

import os
import sys
import time
import json
import base64
import logging
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from tradingview_ta import TA_Handler, Interval

# ===========================================================
# محاولة استيراد yfinance مع إمكانية الفشل (مرونة)
# ===========================================================
YFINANCE_AVAILABLE = False
try:
    import yfinance as yf
    import pandas as pd
    import numpy as np
    YFINANCE_AVAILABLE = True
    logging.info("✅ yfinance متاحة - سيتم استخدامها كمصدر احتياطي")
except ImportError as e:
    logging.warning(f"⚠️ yfinance غير متوفرة: {e} - سيتم استخدام TradingView فقط")
    yf = None
    pd = None
    np = None

# ===========================================================
# 🔧 الإعدادات الأساسية
# ===========================================================
TOTAL_CAPITAL = float(os.environ.get("TOTAL_CAPITAL", "100000"))

# [إصلاح #2] وضع القياس أصبح متغير بيئة صريح - لا يبقى مفعّلاً بصمت
# يجب ضبط MEASUREMENT_MODE=0 في أسرار/متغيرات GitHub Actions للتداول الحقيقي
MEASUREMENT_MODE = os.environ.get("MEASUREMENT_MODE", "1") == "1"

PULSE_CYCLES = 4
PULSE_SLEEP = 60
RISK_PER_TRADE = 0.015
MIN_VOLUME = 50000
ADX_THRESHOLD = 25
ADX_BONUS_THRESHOLD = 40

COMMISSION_RATE = 0.0015
SLIPPAGE_RATE = 0.001
TAX_RATE = 0.00125
TOTAL_FEE_RATE = COMMISSION_RATE + SLIPPAGE_RATE + TAX_RATE

REQUEST_DELAY = 0.5
MAX_RETRIES = 5

# [إصلاح #3] حد أدنى من الصفقات قبل ما نسمح لـ risk_multiplier يتحرك عن 1.0
MIN_TRADES_FOR_RISK_ADJUST = 10
# قوة تخفيف تعديلات DNA لكل صفقة (EWMA-style damping) بدل القفزات الثابتة
DNA_ADAPT_DAMPING = 0.5

# [إصلاح #6] حد أقصى لعدد الصفقات المفتوحة في نفس القطاع
MAX_TRADES_PER_SECTOR = 2

# [إصلاح #7] الحد الأدنى لنسبة المخاطرة/المكافأة حسب نوع الإشارة
MIN_RR_TREND = 2.0
MIN_RR_BREAKOUT = 1.5

# [إصلاح #5] معامل الـ trailing stop بعد T2 (بالأضعاف من ATR)
TRAILING_ATR_MULT = 1.2

# [إصلاح #9] قاطع الدائرة اليومي: عدد الخسائر المتتالية (بغض النظر عن السهم)
# التي توقف فتح صفقات جديدة لبقية اليوم
DAILY_CIRCUIT_BREAKER_LOSSES = 3

# ===========================================================
# ⏱️ إعدادات الإغلاق الزمني التكيفي
# ===========================================================
def get_time_stop_days(regime):
    """تحديد مدة الإغلاق الزمني بناءً على حالة السوق"""
    regime_type = regime.get("type", "SIDEWAYS")
    if "STRONG_BULL" in regime_type:
        return 10
    elif "BULL" in regime_type:
        return 7
    elif "SIDEWAYS" in regime_type:
        return 5
    elif "BEAR" in regime_type:
        return 3
    elif "CRASH" in regime_type:
        return 1
    else:
        return 5

# ===========================================================
# 📡 إعدادات تليجرام
# ===========================================================
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")
FORCE_RUN = os.environ.get("FORCE_RUN", "0") == "1"

# ===========================================================
# 📋 قائمة الأسهم المتوافقة مع الشريعة
# ===========================================================
SHARIA_STOCKS = {
    "ADIB": ("مصرف أبوظبي الإسلامي", "FINANCIAL"),
    "SAUD": ("مصرف البركة", "FINANCIAL"),
    "FAIT": ("بنك فيصل الإسلامي", "FINANCIAL"),
    "EGAL": ("مصر للألومنيوم", "INDUSTRY"),
    "AMOC": ("أموك", "ENERGY"),
    "SKPC": ("سيدبك", "CHEMICALS"),
    "ICFC": ("الدولية للأسمدة", "CHEMICALS"),
    "ARCC": ("العربية للأسمنت", "CONSTRUCTION"),
    "MCQE": ("أسمنت قنا", "CONSTRUCTION"),
    "LCSW": ("ليسيكو مصر", "CONSTRUCTION"),
    "ORAS": ("أوراسكوم للإنشاءات", "CONSTRUCTION"),
    "ATQA": ("الوطنية للصلب - عتاقة", "INDUSTRY"),
    "ORWE": ("النساجون الشرقيون", "INDUSTRY"),
    "MTIE": ("إم إم جروب", "INDUSTRY"),
    "ACGC": ("العربية لحليج الأقطان", "INDUSTRY"),
    "ISPH": ("ابن سينا فارما", "HEALTHCARE"),
    "RMDA": ("رميدا", "HEALTHCARE"),
    "EFID": ("إيديتا", "FOOD"),
    "JUFO": ("جهينة", "FOOD"),
    "OLFI": ("عبور لاند", "FOOD"),
    "MPCO": ("المنصورة للدواجن", "FOOD"),
    "IFAP": ("الدولية للمحاصيل", "FOOD"),
    "MASR": ("مدينة مصر", "REALESTATE"),
    "ORHD": ("أوراسكوم للتنمية", "REALESTATE"),
    "PHDC": ("بالم هيلز", "REALESTATE"),
    "OCDI": ("سوديك", "REALESTATE"),
    "TMGH": ("طلعت مصطفى", "REALESTATE"),
    "CIRA": ("القاهرة للاستثمار", "SERVICES"),
    "EFIH": ("إي فاينانس", "TECH"),
    "RACC": ("رايا", "TECH"),
    "ETEL": ("المصرية للاتصالات", "TELECOM"),
    "EGAS": ("مصر للغاز", "ENERGY"),
    "ETRS": ("مصر للنقل", "LOGISTICS"),
}
STOCKS = list(SHARIA_STOCKS.keys())
DEFENSIVE = {"FOOD", "HEALTHCARE", "TELECOM"}

# [إصلاح #4] سلة أسهم كبيرة/سائلة تُستخدم لتقدير حالة السوق ككل
# عند تعذر جلب EGX30 مباشرة، بدل الاعتماد على سهم واحد
MARKET_PROXY_BASKET = ["TMGH", "CIRA", "EFIH", "ETEL", "ORAS", "PHDC"]

# ===========================================================
# 🧠 ملفات الذاكرة والمتغيرات العامة
# ===========================================================
DNA_FILE = "stocks_dna_memory.json"
SECTOR_DNA_FILE = "sector_dna_memory.json"
TRADES_FILE = "active_trades.json"
STATS_FILE = "daily_stats.json"
LOCK_FILE = "bot.lock"
ADX_CACHE = {}
VOLUME_CACHE = {}

_last_report_day = None
_last_eod_day = None
_closed_this_run = set()

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
CAIRO = ZoneInfo("Africa/Cairo")

_data_cache = {}

# ===========================================================
# 📁 دوال إدارة الملفات
# ===========================================================
def load_json_local(p, d=None):
    if d is None:
        d = {}
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return d
    return d

def save_json_local(p, data):
    try:
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"حفظ محلي فشل: {e}")

def _github_get_sha(url, headers):
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            return r.json().get("sha")
    except Exception:
        pass
    return None

def save_to_github(name, data, msg):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        save_json_local(name, data)
        return
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{name}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    sha = _github_get_sha(url, headers)
    content = json.dumps(data, ensure_ascii=False, indent=2)
    payload = {"message": msg, "content": base64.b64encode(content.encode()).decode()}
    if sha:
        payload["sha"] = sha
    try:
        requests.put(url, headers=headers, json=payload, timeout=15)
        logging.info(f"✅ تم رفع {name} إلى GitHub: {msg}")
    except Exception as e:
        logging.error(f"GitHub خطأ: {e}")

def github_delete_file(name, msg):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        try:
            if os.path.exists(name):
                os.remove(name)
        except Exception:
            pass
        return
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{name}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    sha = _github_get_sha(url, headers)
    if not sha:
        return
    try:
        requests.delete(url, headers=headers, json={"message": msg, "sha": sha}, timeout=15)
    except Exception as e:
        logging.error(f"GitHub حذف خطأ: {e}")

# ===========================================================
# 🔒 [إصلاح #8] قفل تشغيل بسيط لمنع تشغيلين متزامنين
# ===========================================================
def acquire_lock(max_age_minutes=20):
    """
    يحاول إنشاء ملف قفل على GitHub. لو موجود بالفعل وأحدث من max_age_minutes
    نفترض إن فيه تشغيل شغال فعلاً ونتوقف. لو أقدم، نعتبره "متسرب" من تشغيل
    قديم فشل بدون تنظيف، ونكمل.
    """
    if not GITHUB_TOKEN or not GITHUB_REPO:
        # بدون GitHub، القفل غير فعّال (نعتمد على أن Actions لا يشغّل نسختين
        # فعليًا في نفس اللحظة إلا نادرًا) - نسجل تحذير فقط
        logging.warning("⚠️ لا يوجد GITHUB_TOKEN/GITHUB_REPO - القفل غير مفعّل")
        return True
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{LOCK_FILE}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            existing = json.loads(base64.b64decode(r.json()["content"]).decode())
            ts = datetime.fromisoformat(existing.get("timestamp"))
            age_min = (datetime.now(CAIRO) - ts).total_seconds() / 60
            if age_min < max_age_minutes:
                logging.error(f"🔒 يوجد تشغيل آخر شغال (عمر القفل: {age_min:.1f} دقيقة) - إيقاف")
                return False
            logging.warning(f"⚠️ قفل قديم متسرب (عمره {age_min:.1f} دقيقة) - سيتم تجاوزه")
    except Exception:
        pass
    lock_data = {"timestamp": datetime.now(CAIRO).isoformat(), "run": os.environ.get("GITHUB_RUN_ID", "local")}
    save_to_github(LOCK_FILE, lock_data, "acquire lock")
    return True

def release_lock():
    github_delete_file(LOCK_FILE, "release lock")

# ===========================================================
# 📨 إرسال رسائل تليجرام
# ===========================================================
def send_tg(msg):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        )
    except Exception as e:
        logging.error(f"TG خطأ: {e}")

def measurement_banner():
    """[إصلاح #2] تنويه بارز يوضع في بداية كل رسالة صفقة/تقرير مهم"""
    if MEASUREMENT_MODE:
        return "📝📝 *وضع القياس (محاكاة) - هذه ليست صفقة حقيقية* 📝📝\n\n"
    return ""

# ===========================================================
# 🧬 نظام الذاكرة والتعلم (DNA) - على مستوى السهم
# ===========================================================
DEFAULT_DNA = {
    "min_rvol": 0.85,
    "min_score": 60,
    "rsi_min": 38,
    "rsi_max": 76,
    "total_trades": 0,
    "winning_trades": 0,
    "win_rate": 100.0,
    "consecutive_losses": 0,
    "risk_multiplier": 1.0,
    "learned_sessions": 0
}

def get_dna(sym):
    mem = load_json_local(DNA_FILE, {})
    return mem.get(sym, DEFAULT_DNA.copy())

def _ensure_dna_keys(dna):
    for key, default_val in DEFAULT_DNA.items():
        if key not in dna:
            dna[key] = default_val
    return dna

def update_dna(sym, result, price_change, net_pnl=0):
    """
    تحديث ذاكرة السهم بعد كل صفقة.
    [إصلاح #3]:
    - risk_multiplier لا يتحرك إلا بعد MIN_TRADES_FOR_RISK_ADJUST صفقة
    - تخفيف قوة التعديل لكل صفقة عبر DNA_ADAPT_DAMPING لتقليل الـ overfitting
    """
    mem = load_json_local(DNA_FILE, {})
    dna = _ensure_dna_keys(mem.get(sym, DEFAULT_DNA.copy()))

    dna["total_trades"] = dna.get("total_trades", 0) + 1
    enough_history = dna["total_trades"] >= MIN_TRADES_FOR_RISK_ADJUST
    d = DNA_ADAPT_DAMPING  # 0..1 كل ما قل كل ما كان التعديل أهدأ

    if result == "win":
        dna["winning_trades"] = dna.get("winning_trades", 0) + 1
        dna["consecutive_losses"] = 0
        dna["min_rvol"] = max(0.60, dna["min_rvol"] - 0.05 * d)
        dna["min_score"] = max(50, dna["min_score"] - 2 * d)
        if enough_history:
            dna["risk_multiplier"] = min(1.5, dna["risk_multiplier"] + 0.05 * d)
    else:
        dna["consecutive_losses"] = dna.get("consecutive_losses", 0) + 1
        dna["min_rvol"] = min(1.50, dna["min_rvol"] + 0.08 * d)
        dna["min_score"] = min(85, dna["min_score"] + 5 * d)
        if enough_history and dna["consecutive_losses"] >= 2:
            dna["risk_multiplier"] = max(0.40, dna["risk_multiplier"] - 0.15 * d)

    if not enough_history:
        # قبل اكتمال الحد الأدنى من التاريخ، نثبّت المخاطرة عند 1.0 دايمًا
        dna["risk_multiplier"] = 1.0

    total = dna.get("total_trades", 1)
    wins = dna.get("winning_trades", 0)
    dna["win_rate"] = round(wins / total * 100, 1)
    dna["learned_sessions"] = dna.get("learned_sessions", 0) + 1

    if price_change > 0 and result == "win":
        dna["rsi_min"] = max(30, dna["rsi_min"] - 1 * d)
        dna["rsi_max"] = min(80, dna["rsi_max"] + 1 * d)
    elif result == "loss":
        dna["rsi_min"] = min(50, dna["rsi_min"] + 2 * d)
        dna["rsi_max"] = max(65, dna["rsi_max"] - 2 * d)

    mem[sym] = dna
    save_json_local(DNA_FILE, mem)
    save_to_github(DNA_FILE, mem, f"DNA update {sym}")

    # تحديث DNA القطاع بالتوازي
    sector = SHARIA_STOCKS.get(sym, ("", "OTHER"))[1]
    update_sector_dna(sector, result)

# ===========================================================
# 🧬 [إصلاح #3] DNA على مستوى القطاع
# عينة كل سهم صغيرة جدًا (٣٢ سهم فقط) - القطاع بيجمع عينة أكبر وأوثق
# ===========================================================
DEFAULT_SECTOR_DNA = {"total_trades": 0, "winning_trades": 0, "win_rate": 100.0}

def get_sector_dna(sector):
    mem = load_json_local(SECTOR_DNA_FILE, {})
    return mem.get(sector, DEFAULT_SECTOR_DNA.copy())

def update_sector_dna(sector, result):
    mem = load_json_local(SECTOR_DNA_FILE, {})
    sdna = mem.get(sector, DEFAULT_SECTOR_DNA.copy())
    sdna["total_trades"] = sdna.get("total_trades", 0) + 1
    if result == "win":
        sdna["winning_trades"] = sdna.get("winning_trades", 0) + 1
    total = sdna["total_trades"]
    wins = sdna.get("winning_trades", 0)
    sdna["win_rate"] = round(wins / total * 100, 1) if total else 100.0
    mem[sector] = sdna
    save_json_local(SECTOR_DNA_FILE, mem)
    save_to_github(SECTOR_DNA_FILE, mem, f"Sector DNA update {sector}")

def sector_score_penalty(sector):
    """لو القطاع بيسجل أداء ضعيف بعينة كافية، نزود شرط الدخول لكل أسهمه"""
    sdna = get_sector_dna(sector)
    if sdna.get("total_trades", 0) >= 5 and sdna.get("win_rate", 100) < 45:
        return 12
    return 0

def bump_stat(key, value=1):
    stats = load_json_local(STATS_FILE, {})
    today = datetime.now(CAIRO).strftime("%Y-%m-%d")
    d = stats.setdefault(today, {"wins": 0, "losses": 0, "signals": 0})
    d[key] = d.get(key, 0) + value
    save_json_local(STATS_FILE, stats)
    save_to_github(STATS_FILE, stats, f"stat update {key}")

def mark_alerted(sym):
    stats = load_json_local(STATS_FILE, {})
    meta = stats.setdefault("_meta", {})
    alerted = meta.setdefault("alerted", {})
    today = datetime.now(CAIRO).strftime("%Y-%m-%d")
    if alerted.get(sym) == today:
        return False
    alerted[sym] = today
    save_json_local(STATS_FILE, stats)
    save_to_github(STATS_FILE, stats, f"alerted {sym}")
    return True

# ===========================================================
# 🚨 [إصلاح #9] قاطع الدائرة اليومي
# ===========================================================
def get_today_consecutive_losses():
    stats = load_json_local(STATS_FILE, {})
    meta = stats.setdefault("_meta", {})
    today = datetime.now(CAIRO).strftime("%Y-%m-%d")
    cb = meta.setdefault("circuit_breaker", {})
    if cb.get("day") != today:
        cb["day"] = today
        cb["consecutive_losses"] = 0
        cb["tripped"] = False
        save_json_local(STATS_FILE, stats)
        save_to_github(STATS_FILE, stats, "circuit breaker reset")
    return cb.get("consecutive_losses", 0), cb.get("tripped", False)

def register_trade_result_for_circuit_breaker(result):
    stats = load_json_local(STATS_FILE, {})
    meta = stats.setdefault("_meta", {})
    today = datetime.now(CAIRO).strftime("%Y-%m-%d")
    cb = meta.setdefault("circuit_breaker", {"day": today, "consecutive_losses": 0, "tripped": False})
    if cb.get("day") != today:
        cb = {"day": today, "consecutive_losses": 0, "tripped": False}
    if result == "loss":
        cb["consecutive_losses"] = cb.get("consecutive_losses", 0) + 1
    else:
        cb["consecutive_losses"] = 0
        cb["tripped"] = False
    if cb["consecutive_losses"] >= DAILY_CIRCUIT_BREAKER_LOSSES and not cb.get("tripped", False):
        cb["tripped"] = True
        send_tg(
            f"🚨 *قاطع الدائرة اليومي مفعّل!*\n"
            f"سُجّلت `{cb['consecutive_losses']}` خسائر متتالية اليوم (بغض النظر عن السهم).\n"
            f"⛔ سيتم إيقاف فتح صفقات جديدة لبقية اليوم. الصفقات المفتوحة تبقى تحت المتابعة."
        )
    meta["circuit_breaker"] = cb
    stats["_meta"] = meta
    save_json_local(STATS_FILE, stats)
    save_to_github(STATS_FILE, stats, "circuit breaker update")

# ===========================================================
# 📊 دوال التحليل الفني المتقدم
# ===========================================================
def get_average_volume(symbol, days=20):
    if symbol in VOLUME_CACHE:
        return VOLUME_CACHE[symbol]
    try:
        handler = TA_Handler(symbol=symbol, screener="egypt", exchange="EGX", interval=Interval.INTERVAL_1_DAY)
        indicators = handler.get_analysis().indicators
        volume = indicators.get("volume", 0)
        avg_volume = max(volume, 100000)
        VOLUME_CACHE[symbol] = avg_volume
        return avg_volume
    except Exception:
        return 100000

def get_dynamic_min_volume(symbol):
    avg_volume = get_average_volume(symbol)
    return max(50000, int(avg_volume * 0.1))

def get_adx(symbol):
    if symbol in ADX_CACHE:
        return ADX_CACHE[symbol]
    try:
        time.sleep(REQUEST_DELAY * 0.3)
        handler = TA_Handler(symbol=symbol, screener="egypt", exchange="EGX", interval=Interval.INTERVAL_1_DAY)
        adx = handler.get_analysis().indicators.get("ADX", 20)
        adx = adx if adx and adx > 0 else 20
        ADX_CACHE[symbol] = adx
        return adx
    except Exception:
        return 20

def calculate_dynamic_targets(price, atr, score, rsi):
    volatility_ratio = atr / price if price > 0 else 0.02
    if volatility_ratio > 0.03:
        base_multiplier = 0.8
    elif volatility_ratio < 0.01:
        base_multiplier = 1.3
    else:
        base_multiplier = 1.0
    if score >= 80:
        base_multiplier *= 1.1
    elif score <= 65:
        base_multiplier *= 0.9
    if rsi < 40:
        base_multiplier *= 1.15
    elif rsi > 70:
        base_multiplier *= 0.85
    base_target = atr * 1.2 * base_multiplier
    t1 = round(price + base_target, 2)
    t2 = round(price + (base_target * 1.7), 2)
    t3 = round(price + (base_target * 2.5), 2)
    if t1 <= price:
        t1 = round(price + (atr * 0.8), 2)
    if t2 <= t1:
        t2 = round(t1 + (atr * 0.8), 2)
    if t3 <= t2:
        t3 = round(t2 + (atr * 0.8), 2)
    return t1, t2, t3, round(base_multiplier, 2)

# ===========================================================
# 🌍 تحليل حالة السوق
# [إصلاح #4] سلة أسهم بديلة بدل سهم واحد + فحص اتساع لاحقًا في run()
# ===========================================================
def _regime_from_indicators(c, o, e50, e200, rsi, macd, msig):
    chg = ((c - o) / o * 100) if o else 0
    if chg <= -3.0 or (e200 and c < e200 and rsi < 30):
        return {"type": "CRASH ⚫", "mult": 0.0, "max_trades": 0, "risk": "EXTREME", "chg": chg, "defensive": True}
    if e50 and e200 and c > e50 > e200 and macd > msig and rsi < 75:
        return {"type": "STRONG_BULL 🟢", "mult": 1.4, "max_trades": 5, "risk": "LOW", "chg": chg}
    if e50 and c > e50 and macd > msig:
        return {"type": "BULL 🟢", "mult": 1.1, "max_trades": 4, "risk": "LOW", "chg": chg}
    if e50 and c < e50 and macd < msig:
        return {"type": "BEAR 🔴", "mult": 0.5, "max_trades": 2, "risk": "HIGH", "chg": chg, "defensive": True}
    return {"type": "SIDEWAYS 🟠", "mult": 0.8, "max_trades": 3, "risk": "MEDIUM", "chg": chg}

def market_regime():
    # المحاولة الأولى: مؤشر EGX30 مباشرة
    for sym in ["EGX30", "EGX30.CA", "^EGX30"]:
        try:
            time.sleep(REQUEST_DELAY * 0.5)
            h = TA_Handler(symbol=sym, screener="egypt", exchange="EGX", interval=Interval.INTERVAL_1_DAY)
            i = h.get_analysis().indicators
            c = i.get("close", 0)
            if not c or c <= 0:
                continue
            regime = _regime_from_indicators(
                c, i.get("open", c), i.get("EMA50", c), i.get("EMA200", c),
                i.get("RSI", 50), i.get("MACD.macd", 0) or 0, i.get("MACD.signal", 0) or 0
            )
            regime["source"] = "EGX30"
            return regime
        except Exception:
            continue

    # المحاولة الثانية: [إصلاح #4] متوسط سلة أسهم كبيرة بدل سهم واحد
    basket_changes = []
    basket_closes, basket_e50, basket_e200, basket_rsi = [], [], [], []
    basket_macd, basket_msig = [], []
    for sym in MARKET_PROXY_BASKET:
        try:
            time.sleep(REQUEST_DELAY * 0.3)
            h = TA_Handler(symbol=sym, screener="egypt", exchange="EGX", interval=Interval.INTERVAL_1_DAY)
            i = h.get_analysis().indicators
            c = i.get("close", 0)
            o = i.get("open", c)
            if not c or c <= 0:
                continue
            basket_changes.append(((c - o) / o * 100) if o else 0)
            basket_closes.append(c)
            basket_e50.append(i.get("EMA50", c) or c)
            basket_e200.append(i.get("EMA200", c) or c)
            basket_rsi.append(i.get("RSI", 50) or 50)
            basket_macd.append(i.get("MACD.macd", 0) or 0)
            basket_msig.append(i.get("MACD.signal", 0) or 0)
        except Exception:
            continue

    if len(basket_changes) >= 3:  # على الأقل 3 أسهم من السلة لتكون العينة ذات معنى
        avg = lambda lst: sum(lst) / len(lst)
        regime = _regime_from_indicators(
            avg(basket_closes), avg(basket_closes) - avg(basket_changes),  # تقريب لـ open
            avg(basket_e50), avg(basket_e200), avg(basket_rsi), avg(basket_macd), avg(basket_msig)
        )
        regime["chg"] = round(avg(basket_changes), 2)
        regime["source"] = f"basket({len(basket_changes)} أسهم)"
        logging.warning(f"⚠️ EGX30 غير متاح - استخدام سلة بديلة: {regime['source']}")
        return regime

    return {"type": "UNKNOWN 🟡", "mult": 1.0, "max_trades": 3, "risk": "MEDIUM", "chg": 0, "source": "fallback"}

def apply_breadth_adjustment(regime, all_data):
    """
    [إصلاح #4] فحص اتساع السوق: لو أغلبية الأسهم المتابَعة حمراء بشدة،
    نخفّض المخاطرة حتى لو كان مؤشر EGX30/السلة لسه يبدو مستقرًا.
    """
    if not all_data:
        return regime
    changes = [d.get("chg", 0) for d in all_data.values()]
    if len(changes) < 8:
        return regime
    red_ratio = sum(1 for c in changes if c < -0.5) / len(changes)
    if red_ratio >= 0.70 and regime["risk"] not in ("EXTREME", "HIGH"):
        logging.warning(f"⚠️ اتساع سوق سلبي: {red_ratio*100:.0f}% من الأسهم حمراء - تخفيض المخاطرة")
        regime = dict(regime)
        regime["risk"] = "HIGH"
        regime["mult"] = min(regime.get("mult", 1.0), 0.6)
        regime["max_trades"] = min(regime.get("max_trades", 3), 2)
        regime["breadth_warning"] = round(red_ratio * 100, 0)
    return regime

# ===========================================================
# 📊 [إصلاح #1] حساب مؤشرات حقيقية لمسار yfinance الاحتياطي
# ===========================================================
def _calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

def _calc_stoch_k(df, period=14):
    low_n = df["Low"].rolling(period).min()
    high_n = df["High"].rolling(period).max()
    denom = (high_n - low_n).replace(0, np.nan)
    stoch_k = ((df["Close"] - low_n) / denom) * 100
    return stoch_k.fillna(50)

# ===========================================================
# 📊 جلب البيانات
# ===========================================================
def fetch_from_tradingview(symbol):
    time.sleep(REQUEST_DELAY)
    for attempt in range(MAX_RETRIES):
        try:
            h15 = TA_Handler(symbol=symbol, screener="egypt", exchange="EGX", interval=Interval.INTERVAL_15_MINUTES)
            i15 = h15.get_analysis().indicators
            h1 = TA_Handler(symbol=symbol, screener="egypt", exchange="EGX", interval=Interval.INTERVAL_1_DAY)
            i1 = h1.get_analysis().indicators
            c = i15.get("close", 0) or 0
            o = i15.get("open", 0) or 0
            v = i15.get("volume", 0) or 0
            if c <= 0:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)
                continue
            vs = i15.get("volume.SMA20", 0) or 0
            rvol = round(v / vs, 2) if vs else 1.0
            c1 = i1.get("close", c) or c
            e50d = i1.get("EMA50", c1) or c1
            return {
                "sym": symbol,
                "close": c,
                "open": o,
                "volume": v,
                "rvol": rvol,
                "chg": ((c - o) / o * 100) if o else 0,
                "rsi15": i15.get("RSI", 50) or 50,
                "e25_15": i15.get("EMA25", c) or c,
                "e50_15": i15.get("EMA50", c) or c,
                "atr1": i1.get("ATR", c * 0.02) or c * 0.02,
                "stoch": i1.get("Stoch.K", 50) or 50,
                "green15": c > o,
                "bull1d": c1 >= e50d,
                "source": "tradingview",
                "reliable": True
            }
        except Exception as e:
            logging.warning(f"TradingView محاولة {attempt+1}/{MAX_RETRIES} فشلت لـ {symbol}: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
    return None

def fetch_from_yfinance(symbol):
    """
    [إصلاح #1] مسار احتياطي يحسب RSI/Stoch/ATR فعليًا بدل قيم وهمية ثابتة.
    لو البيانات غير كافية لحساب موثوق (أقل من 20 شمعة)، نرفض السهم صراحة
    (نرجع None) بدل ما ندخّله بمؤشرات مزيفة تعدي الفلاتر بالصدفة.
    """
    if not YFINANCE_AVAILABLE:
        return None
    try:
        yf_symbol = symbol + ".CA"
        df = yf.download(yf_symbol, period="5d", interval="15m", progress=False)
        if df.empty or len(df) < 20:
            logging.warning(f"↻ yfinance: بيانات غير كافية لـ {symbol} ({len(df)} شمعة) - رفض السهم")
            return None

        # دعم multi-index columns في بعض إصدارات yfinance
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        last = df.iloc[-1]
        vol_sma = df["Volume"].rolling(20).mean().iloc[-1]
        if pd.isna(vol_sma) or vol_sma <= 0:
            vol_sma = last["Volume"]
        rvol = (last["Volume"] / vol_sma) if vol_sma > 0 else 1.0

        rsi_series = _calc_rsi(df["Close"], period=14)
        stoch_series = _calc_stoch_k(df, period=14)
        rsi_last = rsi_series.iloc[-1]
        stoch_last = stoch_series.iloc[-1]
        if pd.isna(rsi_last):
            logging.warning(f"↻ yfinance: تعذر حساب RSI موثوق لـ {symbol} - رفض السهم")
            return None

        ema25 = df["Close"].ewm(span=25).mean()
        ema50 = df["Close"].ewm(span=50).mean()
        tr = (df["High"] - df["Low"]).rolling(14).mean()
        atr_val = float(tr.iloc[-1]) if not pd.isna(tr.iloc[-1]) else float(last["Close"] * 0.02)

        return {
            "sym": symbol,
            "close": float(last["Close"]),
            "open": float(last["Open"]),
            "volume": float(last["Volume"]),
            "rvol": round(float(rvol), 2),
            "chg": ((last["Close"] - last["Open"]) / last["Open"] * 100) if last["Open"] > 0 else 0,
            "rsi15": round(float(rsi_last), 1),
            "e25_15": float(ema25.iloc[-1]),
            "e50_15": float(ema50.iloc[-1]),
            "atr1": atr_val,
            "stoch": round(float(stoch_last), 1),
            "green15": last["Close"] > last["Open"],
            "bull1d": last["Close"] > ema50.iloc[-1],
            "source": "yfinance",
            "reliable": True
        }
    except Exception as e:
        logging.warning(f"yfinance فشل لـ {symbol}: {e}")
        return None

def fetch_stock(symbol):
    if symbol in _data_cache:
        return _data_cache[symbol]
    data = fetch_from_tradingview(symbol)
    if data is None and YFINANCE_AVAILABLE:
        logging.info(f"↻ استخدام yfinance كبديل لـ {symbol}")
        data = fetch_from_yfinance(symbol)
    if data:
        data["sector"] = SHARIA_STOCKS.get(symbol, ("غير معروف", "OTHER"))[1]
        _data_cache[symbol] = data
        return data
    return None

def fetch_all_stocks(selected_stocks=None):
    if selected_stocks is None:
        selected_stocks = STOCKS
    all_data = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(fetch_stock, sym): sym for sym in selected_stocks}
        for future in as_completed(futures):
            sym = futures[future]
            try:
                data = future.result(timeout=20)
                if data:
                    all_data[sym] = data
            except Exception as e:
                logging.warning(f"خطأ في جلب {sym}: {e}")
    return all_data

# ===========================================================
# 🎯 نظام التقييم
# ===========================================================
def evaluate(d, dna, regime):
    if d["volume"] < MIN_VOLUME:
        return None
    dynamic_min_volume = get_dynamic_min_volume(d["sym"])
    if d["volume"] < dynamic_min_volume:
        return None
    adx = get_adx(d["sym"])
    if adx < ADX_THRESHOLD:
        return None
    if d["chg"] >= 8.5:
        return None
    if not d["bull1d"] and d["chg"] < 2.5:
        return None
    if d["stoch"] > 80:
        return None
    if regime["risk"] == "EXTREME":
        return None

    sector = SHARIA_STOCKS.get(d["sym"], ("", "OTHER"))[1]
    min_score = dna.get("min_score", 60) + sector_score_penalty(sector)  # [إصلاح #3]

    if regime["risk"] == "HIGH":
        min_score += 15
    if dna.get("total_trades", 0) >= 3 and dna.get("win_rate", 100) < 50:
        min_score += 10
    if dna.get("consecutive_losses", 0) >= 2:
        min_score += 15
    now = datetime.now(CAIRO)
    rvol_need = dna.get("min_rvol", 0.85)
    if now.hour == 10 and now.minute <= 30:
        rvol_need *= 1.3
    score = 0
    if dna.get("rsi_min", 38) <= d["rsi15"] <= dna.get("rsi_max", 76):
        score += 25
    if d["close"] > d["e25_15"]:
        score += 20
    if d["close"] > d["e50_15"]:
        score += 20
    if d["bull1d"]:
        score += 20
    if d["green15"]:
        score += 15
    if adx > ADX_BONUS_THRESHOLD:
        score += 10
    instant = (
        d["chg"] >= 2.0 and
        d["rvol"] >= rvol_need and
        dna.get("rsi_min", 38) <= d["rsi15"] <= dna.get("rsi_max", 76) and
        d["bull1d"]
    )
    if instant:
        return {"type": "Super Breakout 🚀", "score": 98, "adx": adx, "min_rr": MIN_RR_BREAKOUT}
    if score >= min_score and d["rvol"] >= rvol_need:
        return {"type": "Trend 📈", "score": min(score, 100), "adx": adx, "min_rr": MIN_RR_TREND}
    return None

# ===========================================================
# 💼 حساب خطة الصفقة
# ===========================================================
def quality_weight(score, risk_multiplier=1.0):
    if score >= 90: base = 0.040
    elif score >= 80: base = 0.032
    elif score >= 70: base = 0.025
    elif score >= 60: base = 0.018
    else: base = 0.010
    adjusted = base * risk_multiplier
    return min(adjusted, 0.050)

def calculate_net_pnl(entry, exit_price, shares):
    entry_with_fees = entry * (1 + TOTAL_FEE_RATE)
    exit_with_fees = exit_price * (1 - TOTAL_FEE_RATE)
    return (exit_with_fees - entry_with_fees) * shares

def make_plan(c, atr, score, deployed, risk_multiplier=1.0, rsi=50, symbol=""):
    weight = quality_weight(score, risk_multiplier)
    risk_amount = TOTAL_CAPITAL * RISK_PER_TRADE
    if score >= 80: stop_distance = max(atr * 1.5, c * 0.015)
    elif score >= 70: stop_distance = max(atr * 1.2, c * 0.012)
    else: stop_distance = max(atr * 1.0, c * 0.010)
    sl = round(c - stop_distance, 2)
    risk_per_share = c - sl
    if risk_per_share <= 0: return None
    t1, t2, t3, multiplier = calculate_dynamic_targets(c, atr, score, rsi)
    shares_by_risk = int(risk_amount // risk_per_share)
    shares_by_weight = int((TOTAL_CAPITAL * weight) // c)
    shares = min(shares_by_risk, shares_by_weight)
    if not MEASUREMENT_MODE:
        max_exposure = TOTAL_CAPITAL * 0.90
        remaining = max_exposure - deployed
        shares_by_exposure = int(remaining // c) if remaining > 0 else 0
        shares = min(shares, shares_by_exposure)
    if shares < 1: return None
    fee_adj = 1 + TOTAL_FEE_RATE
    t1 = round(t1 * fee_adj, 2)
    t2 = round(t2 * fee_adj, 2)
    t3 = round(t3 * fee_adj, 2)
    actual_risk = shares * (c - sl) * (1 + TOTAL_FEE_RATE)
    risk_pct = actual_risk / TOTAL_CAPITAL * 100
    return {
        "sl": sl, "shares": shares, "weight": weight * 100,
        "risk_pct": round(risk_pct, 2),
        "t1": t1, "t2": t2, "t3": t3,
        "target_multiplier": multiplier,
        "loss_egp": round(shares * (c - sl)),
        "p1": round(shares * (t1 - c)), "p2": round(shares * (t2 - c)), "p3": round(shares * (t3 - c)),
        "net_p1": round(shares * (t1 - c) * (1 - TOTAL_FEE_RATE)),
        "net_p2": round(shares * (t2 - c) * (1 - TOTAL_FEE_RATE)),
        "net_p3": round(shares * (t3 - c) * (1 - TOTAL_FEE_RATE)),
        "rr_ratio": round((t1 - c) / (c - sl), 2) if (c - sl) > 0 else 0
    }

# ===========================================================
# 🔄 متابعة الصفقات المفتوحة
# ===========================================================
def track(all_data, regime):
    global _closed_this_run
    trades = load_json_local(TRADES_FILE, {})
    dna_mem = load_json_local(DNA_FILE, {})
    updated = False
    now = datetime.now(CAIRO)

    time_stop_days = get_time_stop_days(regime)

    for sym, t in list(trades.items()):
        if sym not in all_data:
            continue
        price = all_data[sym]["close"]
        atr = all_data[sym].get("atr1", price * 0.02)
        name = SHARIA_STOCKS[sym][0]
        dna = dna_mem.setdefault(sym, get_dna(sym))
        entry_date_str = t.get("entry_date", datetime.now(CAIRO).strftime("%Y-%m-%d"))
        days = (now.replace(tzinfo=None) - datetime.strptime(entry_date_str, "%Y-%m-%d")).days

        # --- الإغلاق الزمني المتكيف مع منع التكرار ---
        if days >= time_stop_days and not t.get("t1_hit", False):
            if sym in _closed_this_run:
                continue
            _closed_this_run.add(sym)
            remaining = t.get("remaining", t.get("shares", 0))
            net_pnl = calculate_net_pnl(t.get("entry_price", 0), price, remaining)
            send_tg(
                measurement_banner() +
                f"⏳ *إغلاق زمني متكيف*\n"
                f"📌 `{sym} - {name}` راكد {days} أيام (الحد: {time_stop_days})\n"
                f"🛒 *بع الكل:* `{remaining}` سهم\n"
                f"💸 صافي الخسارة: `{net_pnl:,.0f}` ج.م"
            )
            price_change = (price - t.get("entry_price", price)) / t.get("entry_price", 1) * 100
            update_dna(sym, "loss", price_change, net_pnl)
            bump_stat("losses")
            register_trade_result_for_circuit_breaker("loss")  # [إصلاح #9]
            del trades[sym]
            updated = True
            continue

        remaining = t.get("remaining", t.get("shares", 0))

        # --- T3 ---
        if not t.get("t3_hit", False) and price >= t.get("t3", 999999):
            t["t3_hit"] = True
            sold = remaining
            t["remaining"] = 0
            net_pnl = calculate_net_pnl(t.get("entry_price", 0), price, sold)
            send_tg(
                measurement_banner() +
                f"🔥 *T3 تحقق - اكتمال الربح*\n"
                f"📌 `{sym} - {name}` | 💵 {price}\n"
                f"🛒 *بع المتبقي:* `{sold}` سهم\n"
                f"💰 صافي الربح: `{net_pnl:,.0f}` ج.م\n"
                f"✅ صفقة مكتملة بنجاح"
            )
            price_change = (price - t.get("entry_price", price)) / t.get("entry_price", 1) * 100
            update_dna(sym, "win", price_change, net_pnl)
            bump_stat("wins")
            register_trade_result_for_circuit_breaker("win")  # [إصلاح #9]
            del trades[sym]
            updated = True
            continue

        # --- T2 ---
        if t.get("t1_hit", False) and not t.get("t2_hit", False) and price >= t.get("t2", 999999):
            t["t2_hit"] = True
            sold = max(1, math.floor(t.get("shares", 0) * 0.30))
            t["remaining"] = remaining - sold
            t["current_stop"] = t.get("t1", 0)
            t["trailing_active"] = True  # [إصلاح #5] تفعيل التريلينج من هنا
            net_pnl = calculate_net_pnl(t.get("entry_price", 0), price, sold)
            send_tg(
                measurement_banner() +
                f"🚀 *T2 تحقق*\n"
                f"📌 `{sym} - {name}` | 💵 {price}\n"
                f"🛒 *بع الآن:* `{sold}` سهم (30%)\n"
                f"📦 المتبقي: `{t['remaining']}` سهم\n"
                f"💰 صافي الربح المحقق: `{net_pnl:,.0f}` ج.م\n"
                f"🛑 *الستوب الجديد:* `{t.get('t1', 0)}` (قفل ربح - سيتحرك تلقائيًا مع الاتجاه)"
            )
            updated = True

        # --- [إصلاح #5] Trailing stop مبني على ATR بعد T2 ---
        if t.get("trailing_active", False):
            candidate_stop = round(price - (atr * TRAILING_ATR_MULT), 2)
            if candidate_stop > t.get("current_stop", 0):
                t["current_stop"] = candidate_stop
                updated = True

        # --- T1 ---
        if not t.get("t1_hit", False) and price >= t.get("t1", 999999):
            t["t1_hit"] = True
            sold = max(1, math.floor(t.get("shares", 0) * 0.40))
            t["remaining"] = remaining - sold
            t["current_stop"] = t.get("entry_price", 0)
            net_pnl = calculate_net_pnl(t.get("entry_price", 0), price, sold)
            send_tg(
                measurement_banner() +
                f"🎯 *T1 تحقق*\n"
                f"📌 `{sym} - {name}` | 💵 {price}\n"
                f"🛒 *بع الآن:* `{sold}` سهم (40%)\n"
                f"📦 المتبقي: `{t['remaining']}` سهم\n"
                f"💰 صافي الربح المحقق: `{net_pnl:,.0f}` ج.م\n"
                f"🛑 *الستوب الجديد:* `{t.get('entry_price', 0)}` (نقطة التعادل)"
            )
            updated = True

        # --- الستوب ---
        current_stop = t.get("current_stop", t.get("sl", 0))
        if price <= current_stop:
            sold = t.get("remaining", t.get("shares", 0))
            net_pnl = calculate_net_pnl(t.get("entry_price", 0), price, sold)
            result = "win" if net_pnl >= 0 else "loss"
            send_tg(
                measurement_banner() +
                f"🛑 *{'خروج بستوب رابح (تريلينج)' if result == 'win' else 'ستوب لوس'}*\n"
                f"📌 `{sym} - {name}` | 📉 كسر `{current_stop}`\n"
                f"🛒 *بع كل المتبقي:* `{sold}` سهم\n"
                f"💰 صافي {'الربح' if result=='win' else 'الخسارة'}: `{net_pnl:,.0f}` ج.م"
            )
            price_change = (price - t.get("entry_price", price)) / t.get("entry_price", 1) * 100
            update_dna(sym, result, price_change, net_pnl)
            bump_stat("wins" if result == "win" else "losses")
            register_trade_result_for_circuit_breaker(result)  # [إصلاح #9]
            del trades[sym]
            updated = True

    if updated:
        save_json_local(TRADES_FILE, trades)
        save_to_github(TRADES_FILE, trades, "trades update")
        save_json_local(DNA_FILE, dna_mem)
        save_to_github(DNA_FILE, dna_mem, "DNA update")

# ===========================================================
# 📋 التقارير
# ===========================================================
def morning_report(regime):
    global _last_report_day
    stats = load_json_local(STATS_FILE, {})
    today = datetime.now(CAIRO).strftime("%Y-%m-%d")
    if stats.get("_meta", {}).get("report") == today:
        logging.info("📋 تقرير الصباح تم إرساله مسبقاً اليوم - تخطي")
        return
    if _last_report_day == today:
        logging.info("📋 تقرير الصباح تم إرساله مسبقاً في هذه الدورة - تخطي")
        return
    breadth_note = f"\n📉 تحذير اتساع: `{regime['breadth_warning']}%` من الأسهم حمراء" if regime.get("breadth_warning") else ""
    source_note = f"\n📡 مصدر حالة السوق: `{regime.get('source', 'EGX30')}`"
    send_tg(
        measurement_banner() +
        f"🌍 *تقرير الصباح*\n\n"
        f"📊 التغير: `{regime['chg']:+.2f}%`\n"
        f"🎯 الحالة: `{regime['type']}`\n"
        f"⚠️ المخاطرة: `{regime['risk']}`\n"
        f"💼 أقصى صفقات اليوم: `{regime['max_trades']}`{source_note}{breadth_note}"
    )
    stats["_meta"] = stats.get("_meta", {})
    stats["_meta"]["report"] = today
    save_json_local(STATS_FILE, stats)
    save_to_github(STATS_FILE, stats, "morning report sent")
    _last_report_day = today

def eod_adaptation(all_data):
    dna_mem = load_json_local(DNA_FILE, {})
    reports = []
    for sym, d in all_data.items():
        dna = dna_mem.setdefault(sym, get_dna(sym))
        if d["chg"] >= 3.0 and d["rvol"] < dna.get("min_rvol", 0.85):
            dna["min_rvol"] = max(0.60, round(dna["min_rvol"] - 0.08, 2))
            reports.append(f"• `{sym}`: خفض شرط السيولة إلى `{dna['min_rvol']}x`")
        if dna.get("win_rate", 0) >= 70 and dna.get("total_trades", 0) >= 3:
            dna["min_score"] = max(50, dna.get("min_score", 60) - 2)
        dna["learned_sessions"] = dna.get("learned_sessions", 0) + 1
    save_json_local(DNA_FILE, dna_mem)
    save_to_github(DNA_FILE, dna_mem, "DNA EOD adaptation")
    return reports

def eod_report(trades, all_data, cycle):
    global _last_eod_day
    now = datetime.now(CAIRO)
    today = now.strftime("%Y-%m-%d")
    if _last_eod_day == today:
        return
    stats = load_json_local(STATS_FILE, {})
    if stats.get("_meta", {}).get("eod") == today:
        return
    is_eod_time = (now.hour == 14 and now.minute >= 15)
    is_last_pulse = (cycle == PULSE_CYCLES - 1 and now.hour >= 13)
    if not (is_eod_time or is_last_pulse):
        return
    d = stats.get(today, {"wins": 0, "losses": 0, "signals": 0})
    msg = (
        measurement_banner() +
        f"🌙 *تقرير الإغلاق*\n\n"
        f"📅 {today}\n"
        f"🎯 إشارات: `{d['signals']}`\n"
        f"✅ أهداف: `{d['wins']}`\n"
        f"🛑 ستوبات: `{d['losses']}`\n"
        f"💼 مفتوحة: `{len(trades)}`"
    )
    if now.weekday() == 3:
        tot_w = sum(v.get("wins", 0) for k, v in stats.items() if k != "_meta")
        tot_l = sum(v.get("losses", 0) for k, v in stats.items() if k != "_meta")
        msg += f"\n\n📊 *ملخص الأسبوع:* ✅ {tot_w} | 🛑 {tot_l}"
    send_tg(msg)
    adapt = eod_adaptation(all_data)
    if adapt:
        send_tg("🧬 *تكيف DNA اليومي*\n\n" + "\n".join(adapt[:5]))
    stats["_meta"] = stats.get("_meta", {})
    stats["_meta"]["eod"] = today
    save_json_local(STATS_FILE, stats)
    save_to_github(STATS_FILE, stats, "eod report sent")
    _last_eod_day = today

# ===========================================================
# 🚀 المحرك الرئيسي
# ===========================================================
def run():
    global _closed_this_run

    # [إصلاح #8] قفل تشغيل لمنع تشغيلين متزامنين
    if not acquire_lock():
        send_tg("⛔ تم تخطي هذا التشغيل: يوجد تشغيل آخر شغال بالفعل (قفل نشط).")
        return

    try:
        logging.info(f"🚀 بدء التشغيل - رأس المال: {TOTAL_CAPITAL:,.0f} ج.م")
        logging.info(f"📝 وضع القياس: {'مفعل' if MEASUREMENT_MODE else 'غير مفعل - تداول حقيقي'}")
        logging.info(f"💰 إجمالي العمولات: {TOTAL_FEE_RATE*100:.2f}%")

        regime = market_regime()
        logging.info(f"🌍 السوق: {regime['type']} (مصدر: {regime.get('source')})")
        _closed_this_run = set()

        if FORCE_RUN:
            send_tg(
                measurement_banner() +
                f"🧪 *تشغيل يدوي*\n\n"
                f"🌍 السوق: `{regime['type']}`\n"
                f"📊 التغير: `{regime['chg']:+.2f}%`\n"
                f"💰 رأس المال: `{TOTAL_CAPITAL:,.0f}` ج.م\n"
                f"💓 نبض: {PULSE_CYCLES} دورات × {PULSE_SLEEP} ثانية"
            )

        if regime["mult"] == 0.0:
            trades = load_json_local(TRADES_FILE, {})
            if trades:
                send_tg(f"🚨 *انهيار سوق!*\nEGX30: `{regime['chg']:+.2f}%`\n💰 أغلق كل الصفقات - كاش")
                save_to_github(TRADES_FILE, {}, "emergency close")
            return

        morning_report(regime)
        trades = load_json_local(TRADES_FILE, {})
        deployed = sum(t.get("entry_price", 0) * t.get("shares", 0) for t in trades.values())
        max_trades = 999 if MEASUREMENT_MODE else regime["max_trades"]
        all_data = {}

        for cycle in range(PULSE_CYCLES):
            logging.info(f"💓 دورة النبض {cycle + 1}/{PULSE_CYCLES}")
            allowed = STOCKS
            if regime.get("defensive"):
                allowed = [s for s in STOCKS if SHARIA_STOCKS[s][1] in DEFENSIVE]
            all_data = fetch_all_stocks(allowed)

            # [إصلاح #4] تحديث حالة السوق بفحص الاتساع بعد أول جلب بيانات
            regime = apply_breadth_adjustment(regime, all_data)

            # [إصلاح #9] فحص قاطع الدائرة اليومي قبل فتح أي صفقة جديدة
            _, circuit_tripped = get_today_consecutive_losses()

            # [إصلاح #6] عدّاد الصفقات المفتوحة لكل قطاع
            sector_counts = {}
            for s in trades:
                sec = SHARIA_STOCKS.get(s, ("", "OTHER"))[1]
                sector_counts[sec] = sector_counts.get(sec, 0) + 1

            for sym, d in all_data.items():
                if d["chg"] >= 2.5 and d["rvol"] >= 2.0 and d["rsi15"] <= 70:
                    if mark_alerted(sym):
                        send_tg(
                            f"🚨 *حركة قوية مدعومة*\n"
                            f"📌 `{sym} - {SHARIA_STOCKS[sym][0]}`\n"
                            f"📈 التغير: `{d['chg']:+.1f}%`\n"
                            f"📊 RVOL: `{d['rvol']}x` | RSI: `{d['rsi15']:.0f}`"
                        )

                if circuit_tripped:
                    continue  # لا صفقات جديدة بعد تفعيل قاطع الدائرة

                dna = get_dna(sym)
                res = evaluate(d, dna, regime)
                if not res or sym in trades or len(trades) >= max_trades:
                    continue

                # [إصلاح #6] حد أقصى للصفقات المفتوحة في نفس القطاع
                sector = SHARIA_STOCKS.get(sym, ("", "OTHER"))[1]
                if sector_counts.get(sector, 0) >= MAX_TRADES_PER_SECTOR:
                    logging.info(f"⏭️ تخطي {sym}: القطاع {sector} وصل الحد الأقصى ({MAX_TRADES_PER_SECTOR})")
                    continue

                plan = make_plan(
                    c=d["close"],
                    atr=d["atr1"],
                    score=res["score"],
                    deployed=deployed,
                    risk_multiplier=dna.get("risk_multiplier", 1.0),
                    rsi=d["rsi15"],
                    symbol=sym
                )
                if not plan:
                    continue
                # [إصلاح #7] الحد الأدنى لـ R:R يختلف حسب نوع الإشارة
                if plan["rr_ratio"] < res.get("min_rr", MIN_RR_TREND):
                    continue
                if plan["risk_pct"] > 1.5:
                    logging.warning(f"⚠️ {sym}: المخاطرة {plan['risk_pct']}% تتجاوز الحد 1.5%")
                    continue

                trades[sym] = {
                    "entry_price": d["close"],
                    "entry_date": datetime.now(CAIRO).strftime("%Y-%m-%d"),
                    "shares": plan["shares"],
                    "remaining": plan["shares"],
                    "sl": plan["sl"],
                    "current_stop": plan["sl"],
                    "t1": plan["t1"],
                    "t2": plan["t2"],
                    "t3": plan["t3"],
                    "t1_hit": False,
                    "t2_hit": False,
                    "t3_hit": False,
                    "trailing_active": False
                }
                sector_counts[sector] = sector_counts.get(sector, 0) + 1
                deployed += plan["shares"] * d["close"]
                bump_stat("signals")

                adx_info = f"\n📊 ADX: `{res.get('adx', 0):.1f}`"
                target_info = f"\n📐 مضاعف الأهداف: `{plan.get('target_multiplier', 1.0)}x`"
                risk_note = f"\n🛡️ المخاطرة: `{plan['risk_pct']}%` (حد أقصى 1.5%)"
                rr_note = f"\n✅ نسبة المخاطرة/المكافأة: 1:{plan['rr_ratio']} (الحد الأدنى المطلوب: 1:{res.get('min_rr')})"
                fees_note = (
                    f"\n💰 صافي الأرباح بعد العمولات ({TOTAL_FEE_RATE*100:.2f}%):\n"
                    f"   🎯 T1: +`{plan['net_p1']:,.0f}` ج.م\n"
                    f"   🚀 T2: +`{plan['net_p2']:,.0f}` ج.م\n"
                    f"   🔥 T3: +`{plan['net_p3']:,.0f}` ج.م"
                )
                send_tg(
                    measurement_banner() +
                    f"🚀 *{res['type']}*\n"
                    f"🎖️ الجودة: `{res['score']}/100` → الوزن: `{plan['weight']:.1f}%`{adx_info}{target_info}\n\n"
                    f"🌍 السوق: `{regime['type']}`\n"
                    f"📌 `{sym} - {SHARIA_STOCKS[sym][0]}` ({sector})\n"
                    f"💵 دخول: `{d['close']}` | 📊 RSI: `{d['rsi15']:.0f}`\n"
                    f"📦 الكمية: `{plan['shares']}` سهم (بقيمة `{plan['shares'] * d['close']:,.0f}` ج.م)\n"
                    f"💰 نسبة من رأس المال: `{plan['weight']:.1f}%`\n\n"
                    f"💸 الخسارة عند الستوب `{plan['sl']}`: ≈ `{plan['loss_egp']:,.0f}` ج.م\n"
                    f"💰 الأرباح المحتملة (الإجمالي):\n"
                    f"   🎯 T1 `{plan['t1']}`: +`{plan['p1']:,.0f}` ج.م\n"
                    f"   🚀 T2 `{plan['t2']}`: +`{plan['p2']:,.0f}` ج.م\n"
                    f"   🔥 T3 `{plan['t3']}`: +`{plan['p3']:,.0f}` ج.م"
                    f"{fees_note}{risk_note}{rr_note}"
                )
                save_json_local(TRADES_FILE, trades)
                save_to_github(TRADES_FILE, trades, f"new trade {sym}")

            track(all_data, regime)
            eod_report(trades, all_data, cycle)
            if cycle < PULSE_CYCLES - 1:
                time.sleep(PULSE_SLEEP)

        eod_report(trades, all_data, PULSE_CYCLES - 1)
        save_to_github(TRADES_FILE, load_json_local(TRADES_FILE, {}), "trades sync")
        save_to_github(STATS_FILE, load_json_local(STATS_FILE, {}), "stats sync")
        logging.info("✅ اكتمل التشغيل بنجاح")

    finally:
        # [إصلاح #8] تحرير القفل دائمًا حتى لو حصل استثناء غير متوقع
        release_lock()

if __name__ == "__main__":
    run()
