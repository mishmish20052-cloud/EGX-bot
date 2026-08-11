import os
import sys
import time
import json
import base64
import logging
import math
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from tradingview_ta import TA_Handler, Interval

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
CAIRO = ZoneInfo("Africa/Cairo")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")

DNA_FILE = "stocks_dna_memory.json"
TRADES_FILE = "active_trades.json"
STATS_FILE = "daily_stats.json"

TOTAL_CAPITAL = float(os.environ.get("TOTAL_CAPITAL", "50000"))
BASE_RISK = 0.01
TIME_STOP_DAYS = 5
MIN_VOLUME = 50000

# 🕌 أسهم EGX33 المتوافقة مع الشريعة
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
# 💾 الذاكرة والتليجرام
# ===========================================================
def load_json_local(p, d=None):
    if d is None: d = {}
    if os.path.exists(p):
        try:
            with open(p, 'r', encoding='utf-8') as fh: return json.load(fh)
        except Exception: return d
    return d

def save_json_local(p, data):
    try:
        with open(p, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"حفظ محلي فشل: {e}")

def save_to_github(name, data, msg):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        save_json_local(name, data); return
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{name}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    sha = None
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200: sha = r.json().get("sha")
    except Exception: pass
    payload = {"message": msg,
               "content": base64.b64encode(json.dumps(data, ensure_ascii=False, indent=2).encode()).decode()}
    if sha: payload["sha"] = sha
    try:
        requests.put(url, headers=headers, json=payload, timeout=15)
    except Exception as e:
        logging.error(f"GitHub خطأ: {e}")

def send_tg(msg):
    if not BOT_TOKEN or not CHAT_ID: return
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                      json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        logging.error(f"TG خطأ: {e}")

def get_dna(sym):
    mem = load_json_local(DNA_FILE, {})
    return mem.get(sym, {"min_rvol": 0.85, "min_score": 60, "rsi_min": 38, "rsi_max": 76,
                         "total_trades": 0, "winning_trades": 0, "win_rate": 100.0})

# ===========================================================
# 🌍 حالة السوق (EGX30)
# ===========================================================
def market_regime():
    try:
        h = TA_Handler(symbol="EGX30", screener="egypt", exchange="EGX", interval=Interval.INTERVAL_1_DAY)
        i = h.get_analysis().indicators
        c = i.get("close", 0); e50 = i.get("EMA50", 0); e200 = i.get("EMA200", 0)
        rsi = i.get("RSI", 50); macd = i.get("MACD.macd", 0) or 0; msig = i.get("MACD.signal", 0) or 0
        o = i.get("open", c)
        chg = ((c - o) / o * 100) if o else 0
        if chg <= -3.0 or (e200 and c < e200 and rsi < 30):
            return {"type": "CRASH ⚫", "mult": 0.0, "max_trades": 0, "risk": "EXTREME", "chg": chg}
        if e50 and e200 and c > e50 > e200 and macd > msig and rsi < 75:
            return {"type": "STRONG_BULL 🟢🟢", "mult": 1.4, "max_trades": 5, "risk": "LOW", "chg": chg}
        if e50 and c > e50 and macd > msig:
            return {"type": "BULL 🟢", "mult": 1.1, "max_trades": 4, "risk": "LOW", "chg": chg}
        if e50 and c < e50 and macd < msig:
            return {"type": "BEAR 🔴", "mult": 0.5, "max_trades": 2, "risk": "HIGH", "chg": chg, "defensive": True}
        return {"type": "SIDEWAYS 🟠", "mult": 0.8, "max_trades": 3, "risk": "MEDIUM", "chg": chg}
    except Exception as e:
        logging.error(f"regime: {e}")
        return {"type": "UNKNOWN 🟡", "mult": 0.6, "max_trades": 3, "risk": "MEDIUM", "chg": 0}

# ===========================================================
# 📊 جلب البيانات
# ===========================================================
def fetch(stock):
    try:
        h15 = TA_Handler(symbol=stock, screener="egypt", exchange="EGX", interval=Interval.INTERVAL_15_MINUTES)
        i15 = h15.get_analysis().indicators
        h1 = TA_Handler(symbol=stock, screener="egypt", exchange="EGX", interval=Interval.INTERVAL_1_DAY)
        i1 = h1.get_analysis().indicators
    except Exception:
        return None

    c = i15.get("close", 0) or 0
    o = i15.get("open", 0) or 0
    v = i15.get("volume", 0) or 0
    if c <= 0: return None

    vs = i15.get("volume.SMA20", 0) or 0
    rvol = round(v / vs, 2) if vs else 1.0
    c1 = i1.get("close", c) or c
    e50d = i1.get("EMA50", c1) or c1

    return {
        "sym": stock, "close": c, "open": o, "volume": v, "rvol": rvol,
        "chg": ((c - o) / o * 100) if o else 0,
        "rsi15": i15.get("RSI", 50) or 50,
        "e25_15": i15.get("EMA25", c) or c,
        "e50_15": i15.get("EMA50", c) or c,
        "atr1": i1.get("ATR", c1 * 0.02) or c1 * 0.02,
        "stoch": i1.get("Stoch.K", 50) or 50,
        "green15": c > o,
        "bull1d": c1 >= e50d,
    }

# ===========================================================
# 🎯 التقييم (قواعد ذكية + DNA)
# ===========================================================
def evaluate(d, dna, regime):
    if d["volume"] < MIN_VOLUME: return None
    if d["chg"] >= 8.5: return None
    if not d["bull1d"] and d["chg"] < 2.5: return None
    if d["stoch"] > 80: return None
    if regime["risk"] == "EXTREME": return None

    min_score = dna["min_score"] + (15 if regime["risk"] == "HIGH" else 0)
    if dna["total_trades"] >= 3 and dna["win_rate"] < 50: min_score += 10

    now = datetime.now(CAIRO)
    rvol_need = dna["min_rvol"] * (1.3 if (now.hour == 10 and now.minute <= 30) else 1.0)

    score = 0
    if dna["rsi_min"] <= d["rsi15"] <= dna["rsi_max"]: score += 25
    if d["close"] > d["e25_15"]: score += 20
    if d["close"] > d["e50_15"]: score += 20
    if d["bull1d"]: score += 20
    if d["green15"]: score += 15

    instant = (d["chg"] >= 2.0 and d["rvol"] >= rvol_need and
               dna["rsi_min"] <= d["rsi15"] <= dna["rsi_max"] and d["bull1d"])

    if instant: return {"type": "Super Breakout 🚀", "score": 98}
    if score >= min_score and d["rvol"] >= rvol_need:
        return {"type": "Trend 📈", "score": score}
    return None

# ===========================================================
# 💼 خطة الصفقة (إدارة مخاطر ATR)
# ===========================================================
def make_plan(c, atr1, mult):
    sd = max(1.5 * atr1, c * 0.04)
    sd = min(sd, c * 0.06)
    sl = round(c - sd, 2)
    risk_amt = TOTAL_CAPITAL * BASE_RISK * max(mult, 0.5)
    shares = max(1, math.floor(risk_amt / sd))
    return {
        "sl": sl, "shares": shares,
        "t1": round(c + 1.5 * sd, 2), "t2": round(c + 3 * sd, 2), "t3": round(c + 4.5 * sd, 2),
        "weight": round(shares * c / TOTAL_CAPITAL * 100, 1),
    }

# ===========================================================
# 🔄 متابعة الصفقات + DNA
# ===========================================================
def track(all_data):
    trades = load_json_local(TRADES_FILE, {})
    dna_mem = load_json_local(DNA_FILE, {})
    updated = False
    now = datetime.now(CAIRO)

    for sym, t in list(trades.items()):
        if sym not in all_data: continue
        price = all_data[sym]["close"]
        name = SHARIA_STOCKS[sym][0]
        dna = dna_mem.setdefault(sym, get_dna(sym))

        days = (now.replace(tzinfo=None) - datetime.strptime(t["entry_date"], '%Y-%m-%d')).days
        if days >= TIME_STOP_DAYS and not t.get("t1_hit"):
            send_tg(f"⏳ *إغلاق زمني*\n📌 `{name}` راكد {days} أيام - حرر رأس المال")
            del trades[sym]; updated = True; continue

        if not t.get("t1_hit") and price >= t["t1"]:
            t["t1_hit"] = True; t["current_stop"] = t["entry_price"]; updated = True
            if not t.get("recorded"):
                dna["winning_trades"] += 1; dna["total_trades"] += 1
                dna["win_rate"] = round(dna["winning_trades"] / dna["total_trades"] * 100, 1)
                t["recorded"] = True
                bump_stat("wins")
            send_tg(f"🎯 *T1 تحقق*\n📌 `{name}` | 💵 {price}\n✅ بع 40% وارفع الستوب للدخول `{t['entry_price']}`")

        elif t.get("t1_hit") and not t.get("t2_hit") and price >= t["t2"]:
            t["t2_hit"] = True; t["current_stop"] = t["t1"]; updated = True
            send_tg(f"🚀 *T2 تحقق*\n📌 `{name}` | 💵 {price}\n✅ بع 30% وارفع الستوب لـ `{t['t1']}`")

        elif t.get("t2_hit") and not t.get("t3_hit") and price >= t["t3"]:
            t["t3_hit"] = True; updated = True
            send_tg(f"🔥 *T3 تحقق*\n📌 `{name}` | 💵 {price}\n✅ بع المتبقي بالكامل")

        elif price <= t.get("current_stop", t["sl"]):
            if not t.get("recorded"):
                dna["total_trades"] += 1
                dna["win_rate"] = round(dna["winning_trades"] / dna["total_trades"] * 100, 1)
                dna["min_score"] = min(95, dna.get("min_score", 60) + 3)
                bump_stat("losses")
            send_tg(f"🛑 *ستوب لوس*\n📌 `{name}` | 📉 كسر `{t.get('current_stop', t['sl'])}`")
            del trades[sym]; updated = True

    if updated:
        save_json_local(TRADES_FILE, trades)
        save_to_github(TRADES_FILE, trades, "trades update")
        save_json_local(DNA_FILE, dna_mem)
        save_to_github(DNA_FILE, dna_mem, "DNA update")

def bump_stat(key):
    stats = load_json_local(STATS_FILE, {})
    today = datetime.now(CAIRO).strftime("%Y-%m-%d")
    d = stats.setdefault(today, {"wins": 0, "losses": 0, "signals": 0})
    d[key] = d.get(key, 0) + 1
    save_json_local(STATS_FILE, stats)

# ===========================================================
# 📋 التقارير
# ===========================================================
def morning_report(regime):
    stats = load_json_local(STATS_FILE, {})
    today = datetime.now(CAIRO).strftime("%Y-%m-%d")
    if stats.get("_meta", {}).get("report") == today: return
    send_tg(f"🌍 *تقرير الصباح*\n\n📊 EGX30: `{regime['chg']:+.2f}%`\n🎯 الحالة: `{regime['type']}`\n⚠️ المخاطرة: `{regime['risk']}`\n💼 أقصى صفقات اليوم: `{regime['max_trades']}`")
    stats["_meta"] = {"report": today}
    save_json_local(STATS_FILE, stats)

def eod_report(trades):
    now = datetime.now(CAIRO)
    if not (now.hour == 14 and now.minute >= 15): return
    stats = load_json_local(STATS_FILE, {})
    today = now.strftime("%Y-%m-%d")
    if stats.get("_meta", {}).get("eod") == today: return
    d = stats.get(today, {"wins": 0, "losses": 0, "signals": 0})
    msg = (f"🌙 *تقرير الإغلاق*\n\n📅 {today}\n🎯 إشارات: `{d['signals']}`\n✅ أهداف: `{d['wins']}`\n🛑 ستوبات: `{d['losses']}`\n💼 مفتوحة: `{len(trades)}`")
    if now.weekday() == 3:
        tot_w = sum(v.get("wins", 0) for k, v in stats.items() if k != "_meta")
        tot_l = sum(v.get("losses", 0) for k, v in stats.items() if k != "_meta")
        msg += f"\n\n📊 *ملخص الأسبوع:* ✅ {tot_w} | 🛑 {tot_l}"
    send_tg(msg)
    stats["_meta"] = stats.get("_meta", {}); stats["_meta"]["eod"] = today
    save_json_local(STATS_FILE, stats)

# ===========================================================
# 🚀 المحرك الرئيسي
# ===========================================================
def run():
    regime = market_regime()
    logging.info(f"🌍 السوق: {regime['type']}")

    if regime["mult"] == 0.0:
        trades = load_json_local(TRADES_FILE, {})
        if trades:
            send_tg(f"🚨 *انهيار سوق!*\nEGX30: `{regime['chg']:+.2f}%`\n💰 أغلق كل الصفقات - كاش")
            save_to_github(TRADES_FILE, {}, "emergency close")
        return

    morning_report(regime)

    allowed = STOCKS
    if regime.get("defensive"):
        allowed = [s for s in STOCKS if SHARIA_STOCKS[s][1] in DEFENSIVE]

    trades = load_json_local(TRADES_FILE, {})
    all_data = {}

    for sym in allowed:
        d = fetch(sym)
        if not d: continue
        all_data[sym] = d

        if d["chg"] >= 4 and d["rvol"] >= 1.5 and sym not in trades:
            send_tg(f"🚨 *حركة قوية + سيولة*\n📌 `{SHARIA_STOCKS[sym][0]}` | 📈 {d['chg']:+.1f}% | 🔊 {d['rvol']}x")

        res = evaluate(d, get_dna(sym), regime)
        if res and sym not in trades and len(trades) < regime["max_trades"]:
            plan = make_plan(d["close"], d["atr1"], regime["mult"])
            trades[sym] = {"entry_price": d["close"], "entry_date": datetime.now(CAIRO).strftime('%Y-%m-%d'),
                           "sl": plan["sl"], "current_stop": plan["sl"],
                           "t1": plan["t1"], "t2": plan["t2"], "t3": plan["t3"],
                           "t1_hit": False, "t2_hit": False, "t3_hit": False}
            bump_stat("signals")
            send_tg(
                f"🚀 *{res['type']}*\n\n"
                f"🌍 السوق: `{regime['type']}`\n"
                f"📌 `{SHARIA_STOCKS[sym][0]}` ({SHARIA_STOCKS[sym][1]})\n"
                f"💵 دخول: `{d['close']}` | 📊 RSI: `{d['rsi15']:.0f}` | 🔊 `{d['rvol']}x`\n"
                f"🔢 الكمية: `{plan['shares']}` سهم ({plan['weight']}%)\n\n"
                f"🛑 ستوب: `{plan['sl']}`\n"
                f"🎯 T1 `{plan['t1']}` | T2 `{plan['t2']}` | T3 `{plan['t3']}`")
            save_json_local(TRADES_FILE, trades)
        time.sleep(0.35)

    track(all_data)
    eod_report(trades)

if __name__ == "__main__":
    run()
    sys.exit(0)
