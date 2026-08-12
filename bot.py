import os
import sys
import time
import json
import base64
import logging
import math
from datetime import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor
import requests
from tradingview_ta import TA_Handler, Interval

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
CAIRO = ZoneInfo("Africa/Cairo")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")
FORCE_RUN = os.environ.get("FORCE_RUN", "0") == "1"

# 📝 وضع القياس الورقي (False عند التداول الحقيقي)
MEASUREMENT_MODE = True

# 💓 النبض الداخلي: 4 دورات بفارق 60 ثانية داخل كل تشغيل
PULSE_CYCLES = 4
PULSE_SLEEP = 60

DNA_FILE = "stocks_dna_memory.json"
TRADES_FILE = "active_trades.json"
STATS_FILE = "daily_stats.json"

TOTAL_CAPITAL = float(os.environ.get("TOTAL_CAPITAL", "50000"))
STOP_MIN_PCT = 0.02
STOP_MAX_PCT = 0.06
MAX_TOTAL_EXPOSURE = 90.0
TIME_STOP_DAYS = 5
MIN_VOLUME = 50000

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
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                      json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        if r.status_code != 200:
            logging.error(f"TG فشل: {r.status_code}")
    except Exception as e:
        logging.error(f"TG خطأ: {e}")

def get_dna(sym):
    mem = load_json_local(DNA_FILE, {})
    return mem.get(sym, {"min_rvol": 0.85, "min_score": 60, "rsi_min": 38, "rsi_max": 76,
                         "total_trades": 0, "winning_trades": 0, "win_rate": 100.0})

def mark_alerted(sym):
    """منع تكرار تنبيه الحركة القوية لنفس السهم في اليوم"""
    stats = load_json_local(STATS_FILE, {})
    meta = stats.setdefault("_meta", {})
    alerted = meta.setdefault("alerted", {})
    today = datetime.now(CAIRO).strftime("%Y-%m-%d")
    if alerted.get(sym) == today: return False
    alerted[sym] = today
    save_json_local(STATS_FILE, stats)
    return True

# ===========================================================
# 🌍 حالة السوق
# ===========================================================
def market_regime():
    for sym in ["EGX30", "EGX30.CA", "^EGX30", "TMGH"]:
        try:
            h = TA_Handler(symbol=sym, screener="egypt", exchange="EGX", interval=Interval.INTERVAL_1_DAY)
            i = h.get_analysis().indicators
            c = i.get("close", 0)
            if c and c > 0:
                e50 = i.get("EMA50", c); e200 = i.get("EMA200", c)
                rsi = i.get("RSI", 50); macd = i.get("MACD.macd", 0) or 0
                msig = i.get("MACD.signal", 0) or 0
                o = i.get("open", c)
                chg = ((c - o) / o * 100) if o else 0
                if chg <= -3.0 or (e200 and c < e200 and rsi < 30):
                    return {"type": "CRASH ⚫", "mult": 0.0, "max_trades": 0, "risk": "EXTREME", "chg": chg}
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
# 📊 جلب البيانات (مع إعادة محاولة)
# ===========================================================
def fetch(stock):
    for attempt in range(2):
        try:
            h15 = TA_Handler(symbol=stock, screener="egypt", exchange="EGX", interval=Interval.INTERVAL_15_MINUTES)
            i15 = h15.get_analysis().indicators
            h1 = TA_Handler(symbol=stock, screener="egypt", exchange="EGX", interval=Interval.INTERVAL_1_DAY)
            i1 = h1.get_analysis().indicators

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
        except Exception:
            if attempt == 0: time.sleep(1.0)
    return None

# ===========================================================
# 🎯 التقييم
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
# 💼 خطة الصفقة (وزن نسبي + دخول كامل + مبالغ بالجنيه)
# ===========================================================
def quality_weight(score):
    if score >= 85: return 0.25
    if score >= 70: return 0.20
    return 0.12

def make_plan(c, atr, score, deployed):
    sd = min(max(1.5 * atr, c * STOP_MIN_PCT), c * STOP_MAX_PCT)
    sl = round(c - sd, 2)
    weight = quality_weight(score)
    shares = math.floor((TOTAL_CAPITAL * weight) / c)
    if not MEASUREMENT_MODE:
        remaining = (TOTAL_CAPITAL * MAX_TOTAL_EXPOSURE / 100) - deployed
        shares = min(shares, max(0, math.floor(remaining / c)))
    if shares < 1: return None
    t1 = round(c + 1.2 * atr, 2)
    t2 = round(c + 2.5 * atr, 2)
    t3 = round(c + 4.0 * atr, 2)
    return {
        "sl": sl, "shares": shares, "weight": weight * 100,
        "t1": t1, "t2": t2, "t3": t3,
        "loss_egp": round(shares * (c - sl)),
        "p1": round(shares * (t1 - c)), "p2": round(shares * (t2 - c)), "p3": round(shares * (t3 - c)),
    }

# ===========================================================
# 🔄 متابعة الصفقات (بيع متدرج واضح)
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
            send_tg(f"⏳ *إغلاق زمني*\n📌 `{sym} - {name}` راكد {days} أيام\n🛒 *بع الكل:* `{t.get('remaining', t['shares'])}` سهم")
            del trades[sym]; updated = True; continue

        if not t.get("t1_hit") and price >= t["t1"]:
            t["t1_hit"] = True
            sold = max(1, math.floor(t["shares"] * 0.40))
            t["remaining"] = t.get("remaining", t["shares"]) - sold
            t["current_stop"] = t["entry_price"]
            updated = True
            if not t.get("recorded"):
                dna["winning_trades"] += 1; dna["total_trades"] += 1
                dna["win_rate"] = round(dna["winning_trades"] / dna["total_trades"] * 100, 1)
                t["recorded"] = True; bump_stat("wins")
            send_tg(f"🎯 *T1 تحقق*\n📌 `{sym} - {name}` | 💵 {price}\n\n🛒 *بع الآن:* `{sold}` سهم (40%)\n📦 المتبقي: `{t['remaining']}` سهم\n🛑 *الستوب الجديد:* `{t['entry_price']}` (نقطة التعادل)")

        elif t.get("t1_hit") and not t.get("t2_hit") and price >= t["t2"]:
            t["t2_hit"] = True
            sold = max(1, math.floor(t["shares"] * 0.30))
            t["remaining"] = t.get("remaining", t["shares"]) - sold
            t["current_stop"] = t["t1"]
            updated = True
            send_tg(f"🚀 *T2 تحقق*\n📌 `{sym} - {name}` | 💵 {price}\n\n🛒 *بع الآن:* `{sold}` سهم (30%)\n📦 المتبقي: `{t['remaining']}` سهم\n🛑 *الستوب الجديد:* `{t['t1']}` (قفل ربح)")

        elif t.get("t2_hit") and not t.get("t3_hit") and price >= t["t3"]:
            t["t3_hit"] = True
            updated = True
            send_tg(f"🔥 *T3 تحقق - اكتمال الربح*\n📌 `{sym} - {name}` | 💵 {price}\n\n🛒 *بع المتبقي:* `{t.get('remaining', t['shares'])}` سهم\n✅ صفقة مكتملة بنجاح")
            del trades[sym]; continue

        elif price <= t.get("current_stop", t["sl"]):
            if not t.get("recorded"):
                dna["total_trades"] += 1
                dna["win_rate"] = round(dna["winning_trades"] / dna["total_trades"] * 100, 1)
                dna["min_score"] = min(95, dna.get("min_score", 60) + 3)
                bump_stat("losses")
            send_tg(f"🛑 *ستوب لوس*\n📌 `{sym} - {name}` | 📉 كسر `{t.get('current_stop', t['sl'])}`\n🛒 *بع كل المتبقي:* `{t.get('remaining', t['shares'])}` سهم")
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
    mode_note = "\n📝 *وضع القياس مفعل: رصد بلا حدود*" if MEASUREMENT_MODE else ""
    send_tg(f"🌍 *تقرير الصباح*\n\n📊 EGX30: `{regime['chg']:+.2f}%`\n🎯 الحالة: `{regime['type']}`\n⚠️ المخاطرة: `{regime['risk']}`\n💼 أقصى صفقات اليوم: `{regime['max_trades']}`{mode_note}")
    stats["_meta"] = {"report": today}
    save_json_local(STATS_FILE, stats)

def eod_adaptation(all_data):
    dna_mem = load_json_local(DNA_FILE, {})
    reports = []
    for sym, d in all_data.items():
        dna = dna_mem.setdefault(sym, get_dna(sym))
        if d["chg"] >= 3.0 and d["rvol"] < dna["min_rvol"]:
            dna["min_rvol"] = max(0.60, round(dna["min_rvol"] - 0.08, 2))
            reports.append(f"• `{sym} - {SHARIA_STOCKS[sym][0]}`: خفض شرط السيولة إلى `{dna['min_rvol']}x`")
        dna["learned_sessions"] = dna.get("learned_sessions", 0) + 1
    save_json_local(DNA_FILE, dna_mem)
    save_to_github(DNA_FILE, dna_mem, "DNA EOD adaptation")
    return reports

def eod_report(trades, all_data):
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

    adapt = eod_adaptation(all_data)
    if adapt:
        send_tg("🧬 *تكيف DNA اليومي*\n\n" + "\n".join(adapt[:5]))

    stats["_meta"] = stats.get("_meta", {}); stats["_meta"]["eod"] = today
    save_json_local(STATS_FILE, stats)

# ===========================================================
# 🚀 المحرك الرئيسي (بالنبض الداخلي)
# ===========================================================
def run():
    logging.info(f"🚀 بدء التشغيل - FORCE={FORCE_RUN} MEASURE={MEASUREMENT_MODE}")
    regime = market_regime()
    logging.info(f"🌍 السوق: {regime['type']}")

    if FORCE_RUN:
        send_tg(f"🧪 *تشغيل يدوي*\n\n🌍 السوق: `{regime['type']}`\n📊 التغير: `{regime['chg']:+.2f}%`\n💰 رأس المال: `{TOTAL_CAPITAL:,.0f}` ج.م\n💓 نبض: {PULSE_CYCLES} دورات × {PULSE_SLEEP} ثانية")

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
    total_signals = 0

    for cycle in range(PULSE_CYCLES):
        logging.info(f"💓 دورة النبض {cycle + 1}/{PULSE_CYCLES}")
        allowed = STOCKS if not regime.get("defensive") else [s for s in STOCKS if SHARIA_STOCKS[s][1] in DEFENSIVE]

        # فحص متوازي سريع لكل الأسهم
        with ThreadPoolExecutor(max_workers=8) as ex:
            fetched = list(ex.map(fetch, allowed))

        for sym, d in zip(allowed, fetched):
            if not d: continue
            all_data[sym] = d

            if d["chg"] >= 4 and sym not in trades and mark_alerted(sym):
                send_tg(f"🚨 *حركة قوية*\n📌 `{sym} - {SHARIA_STOCKS[sym][0]}` | 📈 {d['chg']:+.1f}%")

            res = evaluate(d, get_dna(sym), regime)
            if res and sym not in trades and len(trades) < max_trades:
                plan = make_plan(d["close"], d["atr1"], res["score"], deployed)
                if not plan: continue
                trades[sym] = {"entry_price": d["close"], "entry_date": datetime.now(CAIRO).strftime('%Y-%m-%d'),
                               "shares": plan["shares"], "remaining": plan["shares"],
                               "sl": plan["sl"], "current_stop": plan["sl"],
                               "t1": plan["t1"], "t2": plan["t2"], "t3": plan["t3"],
                               "t1_hit": False, "t2_hit": False, "t3_hit": False}
                deployed += plan["shares"] * d["close"]
                bump_stat("signals")
                total_signals += 1
                paper_note = "\n📝 *صفقة ورقية - وضع القياس*" if MEASUREMENT_MODE else ""
                send_tg(
                    f"🚀 *{res['type']}*\n"
                    f"🎖️ الجودة: `{res['score']}/100` → الوزن: `{plan['weight']:.0f}%`\n\n"
                    f"🌍 السوق: `{regime['type']}`\n"
                    f"📌 `{sym} - {SHARIA_STOCKS[sym][0]}` ({SHARIA_STOCKS[sym][1]})\n"
                    f"💵 دخول: `{d['close']}` | 📊 RSI: `{d['rsi15']:.0f}`\n"
                    f"📦 الكمية: `{plan['shares']}` سهم (دخول كامل)\n\n"
                    f"💸 الخسارة عند الستوب `{plan['sl']}`: ≈ `{plan['loss_egp']:,.0f}` ج.م\n"
                    f"💰 الأرباح المحتملة:\n"
                    f"   🎯 T1 `{plan['t1']}`: +`{plan['p1']:,.0f}` ج.م\n"
                    f"   🚀 T2 `{plan['t2']}`: +`{plan['p2']:,.0f}` ج.م\n"
                    f"   🔥 T3 `{plan['t3']}`: +`{plan['p3']:,.0f}` ج.م{paper_note}")
                save_json_local(TRADES_FILE, trades)
                save_to_github(TRADES_FILE, trades, f"new trade {sym}")

        track(all_data)
        if cycle < PULSE_CYCLES - 1:
            time.sleep(PULSE_SLEEP)

    eod_report(trades, all_data)
    save_to_github(TRADES_FILE, load_json_local(TRADES_FILE, {}), "trades sync")
    save_to_github(STATS_FILE, load_json_local(STATS_FILE, {}), "stats sync")

    if FORCE_RUN:
        send_tg(f"✅ *اكتمل الفحص*\n\n💓 دورات النبض: {PULSE_CYCLES}\n📊 فحصت {len(all_data)} سهم\n🎯 إشارات جديدة: {total_signals}\n💼 مفتوحة: {len(trades)}\n💵 مستثمر (ورقي): {deployed:,.0f} ج.م ({deployed/TOTAL_CAPITAL*100:.0f}%)")

if __name__ == "__main__":
    run()
    sys.exit(0)
