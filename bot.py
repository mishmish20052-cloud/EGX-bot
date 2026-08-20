"""
نظام التداول الآلي المتكامل - البورصة المصرية (EGX33)
الإصدار النهائي 5.2 - إصلاح شامل لـ KeyError وتكرار التقارير

الميزات الأساسية:
- تحديد رأس المال من متغير البيئة أو القيمة الافتراضية
- خصم العمولات والانزلاق السعري والضرائب (0.375% إجمالي)
- تأخير بين طلبات API لتجنب حظر IP (0.5 ثانية)
- مصدر بيانات احتياطي (yfinance) مع معالجة مرنة
- وزن نسبي حسب الجودة (1-4% من رأس المال)
- مخاطرة ثابتة لا تتجاوز 1.5% من رأس المال
- نظام تعلم ذاتي (DNA) لكل سهم مع استخدام .get() لتجنب الأخطاء
- تكيف مع 6 حالات للسوق (STRONG_BULL, BULL, SIDEWAYS, BEAR, CRASH, UNKNOWN)
- تقارير يومية وأسبوعية مع منع التكرار
- توافق مع قائمة الأسهم الشرعية الرسمية
- وضع القياس (التداول الورقي) للتجربة الآمنة
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
# 🔧 الإعدادات الأساسية (عدّل هنا مباشرة أو عبر البيئة)
# ===========================================================

# 💰 رأس المال - من متغير البيئة أو القيمة الافتراضية
TOTAL_CAPITAL = float(os.environ.get("TOTAL_CAPITAL", "100000"))

# 📝 وضع القياس: True = تداول ورقي (بدون مخاطرة حقيقية)
#                    False = تداول حقيقي (مع تطبيق حدود المخاطرة)
MEASUREMENT_MODE = True

# ⚙️ إعدادات التشغيل
PULSE_CYCLES = 4          # عدد دورات الفحص في كل تشغيل
PULSE_SLEEP = 60          # ثواني بين كل دورة
MAX_DAILY_TRADES = 5      # الحد الأقصى للصفقات في اليوم
MAX_SECTOR_POSITIONS = 2  # الحد الأقصى لصفقات القطاع الواحد
RISK_PER_TRADE = 0.015    # 1.5% كحد أقصى للمخاطرة لكل صفقة
TIME_STOP_DAYS = 5        # إغلاق الصفقة تلقائياً بعد 5 أيام ركود
MIN_VOLUME = 50000        # الحد الأدنى لحجم التداول

# 💰 إعدادات العمولات والضرائب (حسب البورصة المصرية)
COMMISSION_RATE = 0.0015   # 0.15% عمولة تداول
SLIPPAGE_RATE = 0.001      # 0.1% انزلاق سعري (متوسط)
TAX_RATE = 0.00125         # 0.125% ضريبة (في مصر)
TOTAL_FEE_RATE = COMMISSION_RATE + SLIPPAGE_RATE + TAX_RATE  # 0.375%

# ⏱️ إعدادات الـ Rate Limiting (لمنع حظر IP)
REQUEST_DELAY = 0.5        # تأخير بين كل طلب (ثواني)
MAX_RETRIES = 5            # عدد محاولات الجلب

# ===========================================================
# 📡 إعدادات تليجرام (من متغيرات البيئة)
# ===========================================================
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")
FORCE_RUN = os.environ.get("FORCE_RUN", "0") == "1"

# ===========================================================
# 📋 قائمة الأسهم المتوافقة مع الشريعة (الرسمية)
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
# 🧠 الإعدادات والذاكرة
# ===========================================================
DNA_FILE = "stocks_dna_memory.json"
TRADES_FILE = "active_trades.json"
STATS_FILE = "daily_stats.json"

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
            with open(p, 'r', encoding='utf-8') as fh:
                return json.load(fh)
        except Exception:
            return d
    return d

def save_json_local(p, data):
    try:
        with open(p, 'w', encoding='utf-8') as fh:
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
    payload = {
        "message": msg,
        "content": base64.b64encode(
            json.dumps(data, ensure_ascii=False, indent=2).encode()
        ).decode()
    }
    if sha:
        payload["sha"] = sha
    try:
        requests.put(url, headers=headers, json=payload, timeout=15)
    except Exception as e:
        logging.error(f"GitHub خطأ: {e}")

# ===========================================================
# 📨 إرسال رسائل تليجرام
# ===========================================================
def send_tg(msg):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        )
        if r.status_code != 200:
            logging.error(f"TG فشل: {r.status_code}")
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
    
    dna["win_rate"] = round(
        dna["winning_trades"] / dna["total_trades"] * 100, 1
    )
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

def mark_alerted(sym):
    stats = load_json_local(STATS_FILE, {})
    meta = stats.setdefault("_meta", {})
    alerted = meta.setdefault("alerted", {})
    today = datetime.now(CAIRO).strftime("%Y-%m-%d")
    if alerted.get(sym) == today:
        return False
    alerted[sym] = today
    save_json_local(STATS_FILE, stats)
    return True

# ===========================================================
# 🌍 تحليل حالة السوق
# ===========================================================
def market_regime():
    for sym in ["EGX30", "EGX30.CA", "^EGX30", "TMGH"]:
        try:
            time.sleep(REQUEST_DELAY * 0.5)
            h = TA_Handler(
                symbol=sym,
                screener="egypt",
                exchange="EGX",
                interval=Interval.INTERVAL_1_DAY
            )
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
# 📊 جلب البيانات (مع مصدر احتياطي وتأخير)
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
                "sym": symbol, "close": c, "open": o, "volume": v, "rvol": rvol,
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
        vol_sma = df['Volume'].rolling(20).mean().iloc[-1] if len(df) >= 20 else last['Volume']
        rvol = (last['Volume'] / vol_sma) if vol_sma > 0 else 1.0
        
        df['RSI'] = 50
        df['EMA25'] = df['Close'].ewm(span=25).mean()
        df['EMA50'] = df['Close'].ewm(span=50).mean()
        df['ATR'] = (df['High'] - df['Low']).rolling(14).mean()
        
        return {
            "sym": symbol, "close": float(last['Close']), "open": float(last['Open']),
            "volume": float(last['Volume']), "rvol": round(rvol, 2),
            "chg": ((last['Close'] - last['Open']) / last['Open'] * 100) if last['Open'] > 0 else 0,
            "rsi15": float(df['RSI'].iloc[-1]),
            "e25_15": float(df['EMA25'].iloc[-1]),
            "e50_15": float(df['EMA50'].iloc[-1]),
            "atr1": float(df['ATR'].iloc[-1] if not pd.isna(df['ATR'].iloc[-1]) else last['Close'] * 0.02),
            "stoch": 50,
            "green15": last['Close'] > last['Open'],
            "bull1d": last['Close'] > df['EMA50'].iloc[-1],
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
# 🎯 نظام التقييم (مع استخدام .get() لتجنب KeyError)
# ===========================================================
def evaluate(d, dna, regime):
    """تقييم السهم مع استخدام .get() لتجنب KeyError"""
    if d["volume"] < MIN_VOLUME:
        return None
    if d["chg"] >= 8.5:
        return None
    if not d["bull1d"] and d["chg"] < 2.5:
        return None
    if d["stoch"] > 80:
        return None
    if regime["risk"] == "EXTREME":
        return None

    # استخدام .get() مع قيم افتراضية لتجنب KeyError
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

    instant = (d["chg"] >= 2.0 and d["rvol"] >= rvol_need and
               dna.get("rsi_min", 38) <= d["rsi15"] <= dna.get("rsi_max", 76) and d["bull1d"])
    if instant:
        return {"type": "Super Breakout 🚀", "score": 98}
    if score >= min_score and d["rvol"] >= rvol_need:
        return {"type": "Trend 📈", "score": min(score, 100)}
    return None

# ===========================================================
# 💼 حساب خطة الصفقة (مع خصم العمولات)
# ===========================================================
def quality_weight(score, risk_multiplier=1.0):
    if score >= 90: base = 0.040
    elif score >= 80: base = 0.032
    elif score >= 70: base = 0.025
    elif score >= 60: base = 0.018
    else: base = 0.010
    adjusted = base * risk_multiplier
    return min(adjusted, 0.050)

def calculate_net_pnl(entry, exit, shares):
    entry_with_fees = entry * (1 + TOTAL_FEE_RATE)
    exit_with_fees = exit * (1 - TOTAL_FEE_RATE)
    return (exit_with_fees - entry_with_fees) * shares

def make_plan(c, atr, score, deployed, risk_multiplier=1.0):
    weight = quality_weight(score, risk_multiplier)
    risk_amount = TOTAL_CAPITAL * RISK_PER_TRADE
    
    if score >= 80: stop_distance = max(atr * 1.5, c * 0.015)
    elif score >= 70: stop_distance = max(atr * 1.2, c * 0.012)
    else: stop_distance = max(atr * 1.0, c * 0.010)
    
    sl = round(c - stop_distance, 2)
    risk_per_share = c - sl
    if risk_per_share <= 0: return None
    
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
    t1 = round(c + (stop_distance * 1.2 * fee_adj), 2)
    t2 = round(c + (stop_distance * 2.0 * fee_adj), 2)
    t3 = round(c + (stop_distance * 3.0 * fee_adj), 2)
    
    actual_risk = shares * (c - sl) * (1 + TOTAL_FEE_RATE)
    risk_pct = actual_risk / TOTAL_CAPITAL * 100
    
    return {
        "sl": sl, "shares": shares, "weight": weight * 100,
        "risk_pct": round(risk_pct, 2),
        "t1": t1, "t2": t2, "t3": t3,
        "loss_egp": round(shares * (c - sl)),
        "p1": round(shares * (t1 - c)), "p2": round(shares * (t2 - c)), "p3": round(shares * (t3 - c)),
        "net_p1": round(shares * (t1 - c) * (1 - TOTAL_FEE_RATE)),
        "net_p2": round(shares * (t2 - c) * (1 - TOTAL_FEE_RATE)),
        "net_p3": round(shares * (t3 - c) * (1 - TOTAL_FEE_RATE)),
        "rr_ratio": round((t1 - c) / (c - sl), 2) if (c - sl)
