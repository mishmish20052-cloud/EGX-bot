"""
نظام التداول الآلي المتكامل - البورصة المصرية (EGX33)
الإصدار النهائي 5.0 - مع جميع التعديلات المطلوبة

الميزات:
- تحديد رأس المال مباشرة في الكود
- خصم العمولات والانزلاق السعري والضرائب (0.375% إجمالي)
- تأخير بين طلبات API لتجنب حظر IP
- مصدر بيانات احتياطي (yfinance)
- وزن نسبي حسب الجودة (1-4% من رأس المال)
- مخاطرة ثابتة لا تتجاوز 1.5%
- نظام تعلم ذاتي (DNA) لكل سهم
- تكيف مع 6 حالات للسوق
- تقارير يومية وأسبوعية
- توافق مع قائمة الأسهم الشرعية الرسمية
- وضع القياس (تداول ورقي)
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
import yfinance as yf
import pandas as pd
import numpy as np

# ===========================================================
# 🔧 الإعدادات الأساسية (عدّل هنا مباشرة)
# ===========================================================

# 💰 رأس المال - عدل هذا الرقم حسب محفظتك
TOTAL_CAPITAL = 10000  # مثلاً: 100,000 جنيه مصري

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
    # الاسم في النظام: (الاسم الكامل, القطاع)
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

# كاش البيانات
_data_cache = {}

# ===========================================================
# 📁 دوال إدارة الملفات
# ===========================================================
def load_json_local(p, d=None):
    if d is None: d = {}
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
    """
    تحديث ذاكرة السهم بعد كل صفقة.
    result: 'win' أو 'loss'
    price_change: نسبة تغير السعر
    net_pnl: صافي الربح/الخسارة بالجنيه بعد العمولات
    """
    dna = get_dna(sym)
    dna["total_trades"] += 1
    
    if result == "win":
        dna["winning_trades"] += 1
        dna["consecutive_losses"] = 0
        # زيادة الثقة في السهم: خفض شروط الدخول قليلاً
        dna["min_rvol"] = max(0.60, dna["min_rvol"] - 0.05)
        dna["min_score"] = max(50, dna["min_score"] - 2)
        dna["risk_multiplier"] = min(1.5, dna["risk_multiplier"] + 0.05)
    else:
        dna["consecutive_losses"] += 1
        # تشديد الشروط بعد الخسارة
        dna["min_rvol"] = min(1.50, dna["min_rvol"] + 0.08)
        dna["min_score"] = min(85, dna["min_score"] + 5)
        # تخفيض المخاطرة بعد خسارتين متتاليتين
        if dna["consecutive_losses"] >= 2:
            dna["risk_multiplier"] = max(0.40, dna["risk_multiplier"] - 0.15)
    
    # تحديث نسبة النجاح
    dna["win_rate"] = round(
        dna["winning_trades"] / dna["total_trades"] * 100, 1
    )
    dna["learned_sessions"] += 1
    
    # تعديل نطاق RSI بناءً على النتائج
    if price_change > 0 and result == "win":
        dna["rsi_min"] = max(30, dna["rsi_min"] - 1)
        dna["rsi_max"] = min(80, dna["rsi_max"] + 1)
    elif result == "loss":
        dna["rsi_min"] = min(50, dna["rsi_min"] + 2)
        dna["rsi_max"] = max(65, dna["rsi_max"] - 2)
    
    # حفظ
    mem = load_json_local(DNA_FILE, {})
    mem[sym] = dna
    save_json_local(DNA_FILE, mem)
    save_to_github(DNA_FILE, mem, f"DNA update {sym}")

def bump_stat(key, value=1):
    """تسجيل إحصائية يومية."""
    stats = load_json_local(STATS_FILE, {})
    today = datetime.now(CAIRO).strftime("%Y-%m-%d")
    d = stats.setdefault(today, {"wins": 0, "losses": 0, "signals": 0})
    d[key] = d.get(key, 0) + value
    save_json_local(STATS_FILE, stats)

def mark_alerted(sym):
    """منع تكرار تنبيه الحركة القوية لنفس السهم في اليوم."""
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
    """
    تحليل حالة السوق باستخدام مؤشرات EGX30.
    يعيد: (النوع, المضاعف, الحد الأقصى للصفقات, المخاطرة)
    """
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
                return {
                    "type": "CRASH ⚫",
                    "mult": 0.0,
                    "max_trades": 0,
                    "risk": "EXTREME",
                    "chg": chg,
                    "defensive": True
                }
            if e50 and e200 and c > e50 > e200 and macd > msig and rsi < 75:
                return {
                    "type": "STRONG_BULL 🟢",
                    "mult": 1.4,
                    "max_trades": 5,
                    "risk": "LOW",
                    "chg": chg
                }
            if e50 and c > e50 and macd > msig:
                return {
                    "type": "BULL 🟢",
                    "mult": 1.1,
                    "max_trades": 4,
                    "risk": "LOW",
                    "chg": chg
                }
            if e50 and c < e50 and macd < msig:
                return {
                    "type": "BEAR 🔴",
                    "mult": 0.5,
                    "max_trades": 2,
                    "risk": "HIGH",
                    "chg": chg,
                    "defensive": True
                }
            return {
                "type": "SIDEWAYS 🟠",
                "mult": 0.8,
                "max_trades": 3,
                "risk": "MEDIUM",
                "chg": chg
            }
        except Exception:
            continue
    
    return {
        "type": "UNKNOWN 🟡",
        "mult": 1.0,
        "max_trades": 3,
        "risk": "MEDIUM",
        "chg": 0
    }

# ===========================================================
# 📊 جلب البيانات (مع مصدر احتياطي وتأخير بين الطلبات)
# ===========================================================
def fetch_from_tradingview(symbol):
    """جلب البيانات من TradingView مع تأخير لتجنب الحظر."""
    time.sleep(REQUEST_DELAY)  # تأخير بين كل طلب
    
    for attempt in range(MAX_RETRIES):
        try:
            h15 = TA_Handler(
                symbol=symbol,
                screener="egypt",
                exchange="EGX",
                interval=Interval.INTERVAL_15_MINUTES
            )
            i15 = h15.get_analysis().indicators
            
            h1 = TA_Handler(
                symbol=symbol,
                screener="egypt",
                exchange="EGX",
                interval=Interval.INTERVAL_1_DAY
            )
            i1 = h1.get_analysis().indicators

            c = i15.get("close", 0) or 0
            o = i15.get("open", 0) or 0
            v = i15.get("volume", 0) or 0
            
            if c <= 0:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)  # تأخير متزايد: 1, 2, 4, 8, 16
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
    """جلب البيانات من yfinance كمصدر احتياطي."""
    try:
        yf_symbol = symbol + ".CA"
        df = yf.download(yf_symbol, period="5d", interval="15m", progress=False)
        if df.empty:
            return None
        
        last = df.iloc[-1]
        if len(df) >= 20:
            vol_sma = df['Volume'].rolling(20).mean().iloc[-1]
        else:
            vol_sma = last['Volume']
        rvol = (last['Volume'] / vol_sma) if vol_sma > 0 else 1.0
        
        # حساب المؤشرات
        df['RSI'] = 50  # مبسط
        df['EMA25'] = df['Close'].ewm(span=25).mean()
        df['EMA50'] = df['Close'].ewm(span=50).mean()
        df['ATR'] = (df['High'] - df['Low']).rolling(14).mean()
        
        return {
            "sym": symbol,
            "close": float(last['Close']),
            "open": float(last['Open']),
            "volume": float(last['Volume']),
            "rvol": round(rvol, 2),
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
    """جلب البيانات من المصدر الرئيسي مع التبديل إلى الاحتياطي عند الفشل."""
    if symbol in _data_cache:
        return _data_cache[symbol]
    
    # المحاولة من TradingView أولاً
    data = fetch_from_tradingview(symbol)
    
    # إذا فشل، استخدم yfinance
    if data is None:
        logging.info(f"↻ استخدام yfinance كبديل لـ {symbol}")
        data = fetch_from_yfinance(symbol)
    
    if data:
        # إضافة القطاع
        data["sector"] = SHARIA_STOCKS.get(symbol, ("غير معروف", "OTHER"))[1]
        # تخزين في الكاش
        _data_cache[symbol] = data
        return data
    
    return None

def fetch_all_stocks(selected_stocks=None):
    """جلب جميع الأسهم بالتوازي مع تأخير بين الطلبات."""
    if selected_stocks is None:
        selected_stocks = STOCKS
    
    all_data = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(fetch_stock, sym): sym for sym in selected_stocks}
        for future in as_completed(futures):
            sym = futures[future]
            try:
                data = future.result(timeout=15)
                if data:
                    all_data[sym] = data
            except Exception as e:
                logging.warning(f"خطأ في جلب {sym}: {e}")
    return all_data

# ===========================================================
# 🎯 نظام التقييم
# ===========================================================
def evaluate(d, dna, regime):
    """
    تقييم السهم بناءً على المؤشرات والذاكرة وحالة السوق.
    تعيد (النوع, النقاط) أو None إذا غير مؤهل.
    """
    # الفلاتر الأساسية
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

    # تعديل الحد الأدنى حسب الظروف
    min_score = dna["min_score"]
    if regime["risk"] == "HIGH":
        min_score += 15
    if dna["total_trades"] >= 3 and dna["win_rate"] < 50:
        min_score += 10
    if dna["consecutive_losses"] >= 2:
        min_score += 15

    # احتياج RVOL حسب الوقت
    now = datetime.now(CAIRO)
    rvol_need = dna["min_rvol"]
    if now.hour == 10 and now.minute <= 30:
        rvol_need *= 1.3

    # حساب النقاط
    score = 0
    if dna["rsi_min"] <= d["rsi15"] <= dna["rsi_max"]:
        score += 25
    if d["close"] > d["e25_15"]:
        score += 20
    if d["close"] > d["e50_15"]:
        score += 20
    if d["bull1d"]:
        score += 20
    if d["green15"]:
        score += 15

    # انفجار فوري
    instant = (
        d["chg"] >= 2.0 and
        d["rvol"] >= rvol_need and
        dna["rsi_min"] <= d["rsi15"] <= dna["rsi_max"] and
        d["bull1d"]
    )
    if instant:
        return {"type": "Super Breakout 🚀", "score": 98}

    # اتجاه منتظم
    if score >= min_score and d["rvol"] >= rvol_need:
        return {"type": "Trend 📈", "score": min(score, 100)}

    return None

# ===========================================================
# 💼 حساب خطة الصفقة (مع خصم العمولات)
# ===========================================================
def quality_weight(score, risk_multiplier=1.0):
    """حساب الوزن النسبي للصفقة بناءً على الجودة (1-4% من رأس المال)."""
    if score >= 90:
        base = 0.040
    elif score >= 80:
        base = 0.032
    elif score >= 70:
        base = 0.025
    elif score >= 60:
        base = 0.018
    else:
        base = 0.010
    adjusted = base * risk_multiplier
    return min(adjusted, 0.050)

def apply_fees(price, shares, is_buy=True):
    """
    تطبيق العمولات والضرائب على السعر أو قيمة الصفقة.
    """
    rate = TOTAL_FEE_RATE if is_buy else TOTAL_FEE_RATE
    return price * (1 + rate) if is_buy else price * (1 - rate)

def calculate_net_pnl(entry, exit, shares):
    """
    حساب صافي الربح/الخسارة بعد خصم جميع العمولات والضرائب.
    """
    # سعر الدخول مع العمولات
    entry_with_fees = entry * (1 + TOTAL_FEE_RATE)
    # سعر الخروج مع العمولات
    exit_with_fees
