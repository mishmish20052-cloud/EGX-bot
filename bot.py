"""
نظام التداول الآلي المتكامل - البورصة المصرية (EGX33)
الإصدار 6.3 - مع الإغلاق الزمني التكيفي وإصلاح شامل للتكرار

الميزات الجديدة:
- إغلاق زمني متكيف مع حالة السوق (10/7/5/3/1 يوم حسب الحالة)
- منع تكرار تقرير الصباح (حفظ على GitHub + متغير مؤقت)
- منع تكرار الإغلاق الزمني لنفس السهم في الدورة
- إصلاح تقرير الإغلاق (EOD) بإرساله في آخر دورة نبض
- رفع جميع التغييرات إلى GitHub فوراً

الميزات الأساسية:
- فلتر حجم التداول الديناميكي (10% من متوسط الحجم)
- أهداف ربح متكيفة مع التقلب (ATR)
- فلتر ADX لقوة الاتجاه (ADX > 25)
- بونص إضافي للصفقات ذات ADX قوي (>40)
- تحديد رأس المال من متغير البيئة
- خصم العمولات والانزلاق السعري والضرائب (0.375%)
- وزن نسبي حسب الجودة (1-4% من رأس المال)
- مخاطرة ثابتة لا تتجاوز 1.5%
- نظام تعلم ذاتي (DNA) لكل سهم
- توافق مع قائمة الأسهم الشرعية الرسمية
- وضع القياس (التداول الورقي)
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
MEASUREMENT_MODE = True
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

# ===========================================================
# ⏱️ إعدادات الإغلاق الزمني التكيفي (جديد)
# ===========================================================
def get_time_stop_days(regime):
    """
    تحديد مدة الإغلاق الزمني بناءً على حالة السوق.
    - STRONG_BULL: 10 أيام (نعطي فرصة أكبر في الصعود القوي)
    - BULL: 7 أيام
    - SIDEWAYS: 5 أيام (كما كان سابقاً)
    - BEAR: 3 أيام (نخرج بسرعة في الهبوط)
    - CRASH: يوم واحد (حماية قصوى)
    """
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
        return 5  # القيمة الافتراضية الآمنة

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

# ===========================================================
# 🧠 ملفات الذاكرة والمتغيرات العامة لمنع التكرار
# ===========================================================
DNA_FILE = "stocks_dna_memory.json"
TRADES_FILE = "active_trades.json"
STATS_FILE = "daily_stats.json"
ADX_CACHE = {}
VOLUME_CACHE = {}

# متغيرات عامة لمنع تكرار الرسائل في نفس الدورة
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

def save_to_github(name, data, msg):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        save_json_local(name, data)
        return
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{name}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    sha = None
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            sha = r.json().get("sha")
    except Exception:
        pass
    content = json.dumps(data, ensure_ascii=False, indent=2)
    payload = {"message": msg, "content": base64.b64encode(content.encode()).decode()}
    if sha:
        payload["sha"] = sha
    try:
        requests.put(url, headers=headers, json=payload, timeout=15)
        logging.info(f"✅ تم رفع {name} إلى GitHub: {msg}")
    except Exception as e:
        logging.error(f"GitHub خطأ: {e}")

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

# ===========================================================
# 🧬 نظام الذاكرة والتعلم (DNA)
# ===========================================================
def get_dna(sym):
    mem = load_json_local(DNA_FILE, {})
    default = {
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
    return mem.get(sym, default.copy())

def update_dna(sym, result, price_change, net_pnl=0):
    dna = get_dna(sym)
    dna["total_trades"] += 1
    if result == "win":
        dna["winning_trades"] += 1
        dna["consecutive_losses"] = 0
        dna["min_rvol"] = max(0.60, dna["min_rvol"] - 0.05)
        dna["min_score"] = max(50, dna["min_score"] - 2)
        dna["risk_multiplier"] = min(1.5, dna["risk_multiplier"] + 0.05)
    else:
        dna["consecutive_losses"] += 1
        dna["min_rvol"] = min(1.50, dna["min_rvol"] + 0.08)
        dna["min_score"] = min(85, dna["min_score"] + 5)
        if dna["consecutive_losses"] >= 2:
            dna["risk_multiplier"] = max(0.40, dna["risk_multiplier"] - 0.15)
    dna["win_rate"] = round(dna["winning_trades"] / dna["total_trades"] * 100, 1)
    dna["learned_sessions"] += 1
    if price_change > 0 and result == "win":
        dna["rsi_min"] = max(30, dna["rsi_min"] - 1)
        dna["rsi_max"] = min(80, dna["rsi_max"] + 1)
    elif result == "loss":
        dna["rsi_min"] = min(50, dna["rsi_min"] + 2)
        dna["rsi_max"] = max(65, dna["rsi_max"] - 2)
    mem = load_json_local(DNA_FILE, {})
    mem[sym] = dna
    save_json_local(DNA_FILE, mem)
    save_to_github(DNA_FILE, mem, f"DNA update {sym}")

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
# ===========================================================
def market_regime():
    for sym in ["EGX30", "EGX30.CA", "^EGX30", "TMGH"]:
        try:
            time.sleep(REQUEST_DELAY * 0.5)
            h = TA_Handler(symbol=sym, screener="egypt", exchange="EGX", interval=Interval.INTERVAL_1_DAY)
            i = h.get_analysis().indicators
            c = i.get("close", 0)
            if not c or c <= 0:
                continue
            e50 = i.get("EMA50", c)
            e200 = i.get("EMA200", c)
            rsi = i.get("RSI", 50)
            macd = i.get("MACD.macd", 0) or 0
            msig = i.get("MACD.signal", 0) or 0
            o = i.get("open", c)
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
        except Exception:
            continue
    return {"type": "UNKNOWN 🟡", "mult": 1.0, "max_trades": 3, "risk": "MEDIUM", "chg": 0}

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
                "source": "tradingview"
            }
        except Exception as e:
            logging.warning(f"TradingView محاولة {attempt+1}/{MAX_RETRIES} فشلت لـ {symbol}: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
    return None

def fetch_from_yfinance(symbol):
    if not YFINANCE_AVAILABLE:
        return None
    try:
        yf_symbol = symbol + ".CA"
        df = yf.download(yf_symbol, period="5d", interval="15m", progress=False)
        if df.empty:
            return None
        last = df.iloc[-1]
        vol_sma = df["Volume"].rolling(20).mean().iloc[-1] if len(df) >= 20 else last["Volume"]
        rvol = (last["Volume"] / vol_sma) if vol_sma > 0 else 1.0
        df["RSI"] = 50
        df["EMA25"] = df["Close"].ewm(span=25).mean()
        df["EMA50"] = df["Close"].ewm(span=50).mean()
        df["ATR"] = (df["High"] - df["Low"]).rolling(14).mean()
        atr_val = float(df["ATR"].iloc[-1]) if not pd.isna(df["ATR"].iloc[-1]) else float(last["Close"] * 0.02)
        return {
            "sym": symbol,
            "close": float(last["Close"]),
            "open": float(last["Open"]),
            "volume": float(last["Volume"]),
            "rvol": round(rvol, 2),
            "chg": ((last["Close"] - last["Open"]) / last["Open"] * 100) if last["Open"] > 0 else 0,
            "rsi15": float(df["RSI"].iloc[-1]),
            "e25_15": float(df["EMA25"].iloc[-1]),
            "e50_15": float(df["EMA50"].iloc[-1]),
            "atr1": atr_val,
            "stoch": 50,
            "green15": last["Close"] > last["Open"],
            "bull1d": last["Close"] > df["EMA50"].iloc[-1],
            "source": "yfinance"
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
        logging.info(f"📊 {d['sym']}: حجم منخفض ({d['volume']:,} < {dynamic_min_volume:,}) - تخطي")
        return None
    adx = get_adx(d["sym"])
    if adx < ADX_THRESHOLD:
        logging.info(f"📊 {d['sym']}: ADX ضعيف ({adx:.1f} < {ADX_THRESHOLD}) - تخطي")
        return None
    if d["chg"] >= 8.5:
        return None
    if not d["bull1d"] and d["chg"] < 2.5:
        return None
    if d["stoch"] > 80:
        return None
    if regime["risk"] == "EXTREME":
        return None
    min_score = dna.get("min_score", 60)
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
        logging.info(f"📈 {d['sym']}: ADX قوي ({adx:.1f} > {ADX_BONUS_THRESHOLD}) +10 نقاط")
    instant = (
        d["chg"] >= 2.0 and
        d["rvol"] >= rvol_need and
        dna.get("rsi_min", 38) <= d["rsi15"] <= dna.get("rsi_max", 76) and
        d["bull1d"]
    )
    if instant:
        return {"type": "Super Breakout 🚀", "score": 98, "adx": adx}
    if score >= min_score and d["rvol"] >= rvol_need:
        return {"type": "Trend 📈", "score": min(score, 100), "adx": adx}
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
# 🔄 متابعة الصفقات المفتوحة (مع الإغلاق الزمني التكيفي)
# ===========================================================
def track(all_data, regime):
    """متابعة الصفقات المفتوحة مع إغلاق زمني متكيف حسب حالة السوق"""
    global _closed_this_run
    trades = load_json_local(TRADES_FILE, {})
    dna_mem = load_json_local(DNA_FILE, {})
    updated = False
    now = datetime.now(CAIRO)
    
    # حساب مدة الإغلاق الزمني بناءً على حالة السوق الحالية
    time_stop_days = get_time_stop_days(regime)
    
    for sym, t in list(trades.items()):
        if sym not in all_data:
            continue
        price = all_data[sym]["close"]
        name = SHARIA_STOCKS[sym][0]
        dna = dna_mem.setdefault(sym, get_dna(sym))
        entry_date_str = t.get("entry_date", datetime.now(CAIRO).strftime("%Y-%m-%d"))
        days = (now.replace(tzinfo=None) - datetime.strptime(entry_date_str, "%Y-%m-%d")).days
        
        # --- الإغلاق الزمني المتكيف مع منع التكرار في نفس الدورة ---
        if days >= time_stop_days and not t.get("t1_hit", False):
            if sym in _closed_this_run:
                continue
            _closed_this_run.add(sym)
            remaining = t.get("remaining", t.get("shares", 0))
            net_pnl = calculate_net_pnl(t.get("entry_price", 0), price, remaining)
            send_tg(
                f"⏳ *إغلاق زمني متكيف*\n"
                f"📌 `{sym} - {name}` راكد {days} أيام (الحد: {time_stop_days})\n"
                f"🛒 *بع الكل:* `{remaining}` سهم\n"
                f"💸 صافي الخسارة: `{net_pnl:,.0f}` ج.م"
            )
            price_change = (price - t.get("entry_price", price)) / t.get("entry_price", 1) * 100
            update_dna(sym, "loss", price_change, net_pnl)
            bump_stat("losses")
            del trades[sym]
            updated = True
            continue
        
        # --- باقي منطق التتبع (T1, T2, T3, Stop) ---
        remaining = t.get("remaining", t.get("shares", 0))
        if not t.get("t3_hit", False) and price >= t.get("t3", 999999):
            t["t3_hit"] = True
            sold = remaining
            t["remaining"] = 0
            net_pnl = calculate_net_pnl(t.get("entry_price", 0), price, sold)
            send_tg(
                f"🔥 *T3 تحقق - اكتمال الربح*\n"
                f"📌 `{sym} - {name}` | 💵 {price}\n"
                f"🛒 *بع المتبقي:* `{sold}` سهم\n"
                f"💰 صافي الربح: `{net_pnl:,.0f}` ج.م\n"
                f"✅ صفقة مكتملة بنجاح"
            )
            price_change = (price - t.get("entry_price", price)) / t.get("entry_price", 1) * 100
            update_dna(sym, "win", price_change, net_pnl)
            bump_stat("wins")
            del trades[sym]
            updated = True
            continue
        
        if t.get("t1_hit", False) and not t.get("t2_hit", False) and price >= t.get("t2", 999999):
            t["t2_hit"] = True
            sold = max(1, math.floor(t.get("shares", 0) * 0.30))
            t["remaining"] = remaining - sold
            t["current_stop"] = t.get("t1", 0)
            net_pnl = calculate_net_pnl(t.get("entry_price", 0), price, sold)
            send_tg(
                f"🚀 *T2 تحقق*\n"
                f"📌 `{sym} - {name}` | 💵 {price}\n"
                f"🛒 *بع الآن:* `{sold}` سهم (30%)\n"
                f"📦 المتبقي: `{t['remaining']}` سهم\n"
                f"💰 صافي الربح المحقق: `{net_pnl:,.0f}` ج.م\n"
                f"🛑 *الستوب الجديد:* `{t.get('t1', 0)}` (قفل ربح)"
            )
            updated = True
        
        if not t.get("t1_hit", False) and price >= t.get("t1", 999999):
            t["t1_hit"] = True
            sold = max(1, math.floor(t.get("shares", 0) * 0.40))
            t["remaining"] = remaining - sold
            t["current_stop"] = t.get("entry_price", 0)
            net_pnl = calculate_net_pnl(t.get("entry_price", 0), price, sold)
            send_tg(
                f"🎯 *T1 تحقق*\n"
                f"📌 `{sym} - {name}` | 💵 {price}\n"
                f"🛒 *بع الآن:* `{sold}` سهم (40%)\n"
                f"📦 المتبقي: `{t['remaining']}` سهم\n"
                f"💰 صافي الربح المحقق: `{net_pnl:,.0f}` ج.م\n"
                f"🛑 *الستوب الجديد:* `{t.get('entry_price', 0)}` (نقطة التعادل)"
            )
            updated = True
        
        current_stop = t.get("current_stop", t.get("sl", 0))
        if price <= current_stop:
            sold = t.get("remaining", t.get("shares", 0))
            net_pnl = calculate_net_pnl(t.get("entry_price", 0), price, sold)
            send_tg(
                f"🛑 *ستوب لوس*\n"
                f"📌 `{sym} - {name}` | 📉 كسر `{current_stop}`\n"
                f"🛒 *بع كل المتبقي:* `{sold}` سهم\n"
                f"💸 صافي الخسارة: `{net_pnl:,.0f}` ج.م"
            )
            price_change = (price - t.get("entry_price", price)) / t.get("entry_price", 1) * 100
            update_dna(sym, "loss", price_change, net_pnl)
            bump_stat("losses")
            del trades[sym]
            updated = True
    
    if updated:
        save_json_local(TRADES_FILE, trades)
        save_to_github(TRADES_FILE, trades, "trades update")
        save_json_local(DNA_FILE, dna_mem)
        save_to_github(DNA_FILE, dna_mem, "DNA update")

# ===========================================================
# 📋 التقارير (مع منع التكرار)
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
    
    mode_note = "\n📝 *وضع القياس مفعل: رصد بلا حدود*" if MEASUREMENT_MODE else ""
    send_tg(
        f"🌍 *تقرير الصباح*\n\n"
        f"📊 EGX30: `{regime['chg']:+.2f}%`\n"
        f"🎯 الحالة: `{regime['type']}`\n"
        f"⚠️ المخاطرة: `{regime['risk']}`\n"
        f"💼 أقصى صفقات اليوم: `{regime['max_trades']}`{mode_note}"
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
    """تقرير نهاية اليوم مع منع التكرار وإرساله في آخر دورة"""
    global _last_eod_day
    now = datetime.now(CAIRO)
    today = now.strftime("%Y-%m-%d")
    
    if _last_eod_day == today:
        return
    
    stats = load_json_local(STATS_FILE, {})
    if stats.get("_meta", {}).get("eod") == today:
        return
    
    # الشرط: إما بعد الساعة 14:15، أو في آخر دورة نبض (قرب نهاية الجلسة)
    is_eod_time = (now.hour == 14 and now.minute >= 15)
    is_last_pulse = (cycle == PULSE_CYCLES - 1 and now.hour >= 13)
    
    if not (is_eod_time or is_last_pulse):
        return
    
    d = stats.get(today, {"wins": 0, "losses": 0, "signals": 0})
    msg = (
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
    
    logging.info(f"🚀 بدء التشغيل - رأس المال: {TOTAL_CAPITAL:,.0f} ج.م")
    logging.info(f"📝 وضع القياس: {'مفعل' if MEASUREMENT_MODE else 'غير مفعل'}")
    logging.info(f"💰 إجمالي العمولات: {TOTAL_FEE_RATE*100:.2f}%")
    
    regime = market_regime()
    logging.info(f"🌍 السوق: {regime['type']}")
    
    # إعادة تعيين مجموعة الإغلاق في بداية كل تشغيل
    _closed_this_run = set()
    
    if FORCE_RUN:
        send_tg(
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
        
        for sym, d in all_data.items():
            # تنبيه الحركة القوية
            if d["chg"] >= 2.5 and d["rvol"] >= 2.0 and d["rsi15"] <= 70:
                if mark_alerted(sym):
                    send_tg(
                        f"🚨 *حركة قوية مدعومة*\n"
                        f"📌 `{sym} - {SHARIA_STOCKS[sym][0]}`\n"
                        f"📈 التغير: `{d['chg']:+.1f}%`\n"
                        f"📊 RVOL: `{d['rvol']}x` | RSI: `{d['rsi15']:.0f}`"
                    )
            
            dna = get_dna(sym)
            res = evaluate(d, dna, regime)
            if res and sym not in trades and len(trades) < max_trades:
                plan = make_plan(
                    c=d["close"],
                    atr=d["atr1"],
                    score=res["score"],
                    deployed=deployed,
                    risk_multiplier=dna.get("risk_multiplier", 1.0),
                    rsi=d["rsi15"],
                    symbol=sym
                )
                if not plan: continue
                if plan["rr_ratio"] < 1.5: continue
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
                    "t3_hit": False
                }
                deployed += plan["shares"] * d["close"]
                bump_stat("signals")
                
                adx_info = f"\n📊 ADX: `{res.get('adx', 0):.1f}`"
                target_info = f"\n📐 مضاعف الأهداف: `{plan.get('target_multiplier', 1.0)}x`"
                paper_note = "\n📝 *صفقة ورقية - وضع القياس*" if MEASUREMENT_MODE else ""
                risk_note = f"\n🛡️ المخاطرة: `{plan['risk_pct']}%` (حد أقصى 1.5%)"
                rr_note = f"\n✅ نسبة المخاطرة/المكافأة: 1:{plan['rr_ratio']}"
                fees_note = (
                    f"\n💰 صافي الأرباح بعد العمولات ({TOTAL_FEE_RATE*100:.2f}%):\n"
                    f"   🎯 T1: +`{plan['net_p1']:,.0f}` ج.م\n"
                    f"   🚀 T2: +`{plan['net_p2']:,.0f}` ج.م\n"
                    f"   🔥 T3: +`{plan['net_p3']:,.0f}` ج.م"
                )
                
                send_tg(
                    f"🚀 *{res['type']}*\n"
                    f"🎖️ الجودة: `{res['score']}/100` → الوزن: `{plan['weight']:.1f}%`{adx_info}{target_info}\n\n"
                    f"🌍 السوق: `{regime['type']}`\n"
                    f"📌 `{sym} - {SHARIA_STOCKS[sym][0]}` ({SHARIA_STOCKS[sym][1]})\n"
                    f"💵 دخول: `{d['close']}` | 📊 RSI: `{d['rsi15']:.0f}`\n"
                    f"📦 الكمية: `{plan['shares']}` سهم (بقيمة `{plan['shares'] * d['close']:,.0f}` ج.م)\n"
                    f"💰 نسبة من رأس المال: `{plan['weight']:.1f}%`\n\n"
                    f"💸 الخسارة عند الستوب `{plan['sl']}`: ≈ `{plan['loss_egp']:,.0f}` ج.م\n"
                    f"💰 الأرباح المحتملة (الإجمالي):\n"
                    f"   🎯 T1 `{plan['t1']}`: +`{plan['p1']:,.0f}` ج.م\n"
                    f"   🚀 T2 `{plan['t2']}`: +`{plan['p2']:,.0f}` ج.م\n"
                    f"   🔥 T3 `{plan['t3']}`: +`{plan['p3']:,.0f}` ج.م"
                    f"{fees_note}{risk_note}{rr_note}{paper_note}"
                )
                
                save_json_local(TRADES_FILE, trades)
                save_to_github(TRADES_FILE, trades, f"new trade {sym}")
        
        # متابعة الصفقات مع تمرير حالة السوق للإغلاق الزمني التكيفي
        track(all_data, regime)
        
        # محاولة إرسال تقرير الإغلاق بعد كل دورة (سيتم التحقق داخلياً)
        eod_report(trades, all_data, cycle)
        
        if cycle < PULSE_CYCLES - 1:
            time.sleep(PULSE_SLEEP)
    
    # محاولة أخيرة لتقرير الإغلاق في نهاية جميع الدورات
    eod_report(trades, all_data, PULSE_CYCLES - 1)
    save_to_github(TRADES_FILE, load_json_local(TRADES_FILE, {}), "trades sync")
    save_to_github(STATS_FILE, load_json_local(STATS_FILE, {}), "stats sync")
    logging.info("✅ اكتمل التشغيل بنجاح")

# ===========================================================
# 🏁 نقطة الدخول
# ===========================================================
if __name__ == "__main__":
    run()
