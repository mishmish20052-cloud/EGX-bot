import os, sys, time, json, base64, logging, math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests
from tradingview_ta import TA_Handler, Interval

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
CAIRO_TZ = ZoneInfo("Africa/Cairo")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")

DNA_FILE = "stocks_dna_memory.json"
ACTIVE_TRADES_FILE = "active_trades.json"
DAILY_STATS_FILE = "daily_stats.json"
MODEL_PATH = "ml/egx_model.joblib"

TOTAL_CAPITAL = float(os.environ.get("TOTAL_CAPITAL") or 50000)
RISK_PER_TRADE_PCT = 0.01
MIN_VOLUME_THRESHOLD = 30000
TIME_STOP_DAYS = 5

FEATURES = ["rsi","macd_pct","macd_hist_pct","close_vs_ema25",
            "close_vs_ema50","atr_pct","stoch_k","is_green","dow"]

# ---------- تحميل نموذج ML (إن وجد) ----------
ml_model = None
try:
    import joblib, pandas as pd
    if os.path.exists(MODEL_PATH):
        ml_model = joblib.load(MODEL_PATH)
        logging.info("🧠 تم تحميل نموذج ML بنجاح")
except Exception as e:
    logging.warning(f"⚠️ نموذج ML غير متوفر: {e}")

EGX33_SHARIA_MAP = {
    "ADIB": {"name": "أبوظبي الإسلامي", "sector": "BANKING"},
    "SAUD": {"name": "بنك البركة", "sector": "BANKING"},
    "FAIT": {"name": "بنك فيصل الإسلامي", "sector": "BANKING"},
    "EGAL": {"name": "مصر للألومنيوم", "sector": "INDUSTRY"},
    "AMOC": {"name": "أموك", "sector": "ENERGY"},
    "SKPC": {"name": "سيدبك", "sector": "CHEMICALS"},
    "ICFC": {"name": "الدولية للأسمدة", "sector": "CHEMICALS"},
    "ARCC": {"name": "العربية للأسمنت", "sector": "CONSTRUCTION"},
    "MCQE": {"name": "أسمنت قنا", "sector": "CONSTRUCTION"},
    "LCSW": {"name": "ليسيكو مصر", "sector": "INDUSTRY"},
    "ATQA": {"name": "الحديد والصلب - عتاقة", "sector": "INDUSTRY"},
    "ORWE": {"name": "النساجون الشرقيون", "sector": "INDUSTRY"},
    "MTIE": {"name": "MM Group", "sector": "INDUSTRY"},
    "ACGC": {"name": "القطن العربي", "sector": "INDUSTRY"},
    "ISPH": {"name": "ابن سينا فارما", "sector": "HEALTHCARE"},
    "RMDA": {"name": "رميدا", "sector": "HEALTHCARE"},
    "EFID": {"name": "إدفيتا", "sector": "FOOD"},
    "JUFO": {"name": "جهينة", "sector": "FOOD"},
    "OLFI": {"name": "عبور لاند", "sector": "FOOD"},
    "MPCO": {"name": "المنصورة للدواجن", "sector": "FOOD"},
    "MASR": {"name": "مدينة مصر", "sector": "REALESTATE"},
    "ORHD": {"name": "أوراسكوم للتنمية", "sector": "REALESTATE"},
    "PHDC": {"name": "بالم هيلز", "sector": "REALESTATE"},
    "OCDI": {"name": "سوديك", "sector": "REALESTATE"},
    "TMGH": {"name": "طلعت مصطفى", "sector": "REALESTATE"},
    "CIRA": {"name": "CIRA التعليم", "sector": "REALESTATE"},
    "EFIH": {"name": "إي فاينانس", "sector": "TECH"},
    "ETEL": {"name": "المصرية للاتصالات", "sector": "TELECOM"},
    "RACC": {"name": "رايا", "sector": "TECH"},
    "EGAS": {"name": "مصر للغاز", "sector": "ENERGY"},
    "ETRS": {"name": "النقل التجاري", "sector": "LOGISTICS"},
    "IFAP": {"name": "الدولية للمحاصيل", "sector": "AGRICULTURE"},
    "ORAS": {"name": "أوراسكوم للإنشاء", "sector": "CONSTRUCTION"}
}
STOCKS = list(EGX33_SHARIA_MAP.keys())

# ---------- الذاكرة ----------
def load_json_local(fp, default=None):
    if default is None: default = {}
    if os.path.exists(fp):
        try:
            with open(fp, 'r', encoding='utf-8') as f: return json.load(f)
        except Exception: return default
    return default

def save_json_local(fp, data):
    try:
        with open(fp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e: logging.error(f"فشل حفظ {fp}: {e}")

def save_file_to_github(fn, data, msg):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        save_json_local(fn, data); return
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{fn}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
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
    except Exception as e: logging.error(f"GitHub error: {e}")

def get_stock_dna(sym):
    return load_json_local(DNA_FILE, {}).get(sym, {
        "min_rvol": 0.85, "min_score": 60, "rsi_min": 38.0, "rsi_max": 76.0,
        "total_trades": 0, "winning_trades": 0, "win_rate": 100.0})

# ---------- تليجرام ----------
def send_telegram(message, buttons=None):
    if not BOT_TOKEN or not CHAT_ID: return
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    if buttons: payload["reply_markup"] = {"inline_keyboard": buttons}
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload, timeout=10)
    except Exception as e: logging.error(f"Telegram error: {e}")

# ---------- أوقات السوق ----------
def is_egx_market_hours():
    now = datetime.now(CAIRO_TZ)
    if now.weekday() in [4, 5]: return False
    return (10 <= now.hour < 14) or (now.hour == 14 and now.minute <= 30)

# ---------- السوق العام ----------
def analyze_market_regime():
    try:
        ind = TA_Handler(symbol="EGX30", screener="egypt", exchange="EGX",
                         interval=Interval.INTERVAL_1_DAY).get_analysis().indicators
        c, e50, e200 = ind.get("close",0), ind.get("EMA50",0), ind.get("EMA200",0)
        rsi, macd, sig = ind.get("RSI",50), ind.get("MACD.macd",0), ind.get("MACD.signal",0)
        chg = ((c - ind.get("open", c)) / ind.get("open", c) * 100) if ind.get("open") else 0

        if c < e200 and (chg < -3 or rsi < 30):
            return {"regime":"CRASH ⚫⚫","risk":"EXTREME","mult":0.0,"max_trades":0,"strategy":"CASH IS KING","chg":chg,"price":c}
        if c > e50 > e200 and macd > sig and rsi < 75:
            return {"regime":"STRONG_BULL 🟢🟢","risk":"LOW","mult":1.3,"max_trades":5,"strategy":"شراء الاختراقات","chg":chg,"price":c}
        if c > e50 and macd > sig:
            return {"regime":"MODERATE_BULL 🟢","risk":"LOW","mult":1.1,"max_trades":4,"strategy":"شراء الاتجاه","chg":chg,"price":c}
        if abs(c-e50)/e50 < 0.02:
            return {"regime":"SIDEWAYS 🟠","risk":"MEDIUM","mult":0.8,"max_trades":3,"strategy":"انتقائي جداً","chg":chg,"price":c}
        if c < e50 and macd < sig:
            return {"regime":"BEAR 🔴","risk":"HIGH","mult":0.5,"max_trades":2,"strategy":"دفاعي فقط","chg":chg,"price":c}
        return {"regime":"CAUTIOUS 🟡","risk":"MEDIUM","mult":0.7,"max_trades":3,"strategy":"حذر","chg":chg,"price":c}
    except Exception as e:
        logging.error(f"Market analysis error: {e}")
        return {"regime":"UNKNOWN","risk":"HIGH","mult":0.5,"max_trades":3,"strategy":"حذر","chg":0,"price":0}

# ---------- جلب البيانات ----------
def fetch_stock_data_safe(symbol, max_retries=2):
    for attempt in range(max_retries):
        try:
            i15 = TA_Handler(symbol=symbol, screener="egypt", exchange="EGX",
                             interval=Interval.INTERVAL_15_MINUTES).get_analysis().indicators
            i1d = TA_Handler(symbol=symbol, screener="egypt", exchange="EGX",
                             interval=Interval.INTERVAL_1_DAY).get_analysis().indicators

            close, opn = i15.get("close",0), i15.get("open",0)
            volume = i15.get("volume",0)
            atr = i15.get("ATR", close*0.02) or close*0.02
            c1d = i1d.get("close", close)
            o1d = i1d.get("open", c1d)
            rsi1d = i1d.get("RSI", 50)
            m1d = i1d.get("MACD.macd", 0) or 0
            ms1d = i1d.get("MACD.signal", 0) or 0
            e25 = i1d.get("EMA25", c1d)
            e50 = i1d.get("EMA50", c1d)
            atr1d = i1d.get("ATR", c1d*0.02) or c1d*0.02
            stk = i1d.get("Stoch.K", 50)

            vsma = i15.get("volume.SMA20", volume) or volume
            return {
                "symbol": symbol, "close": close, "open": opn,
                "change_pct": ((close-opn)/opn*100) if opn else 0,
                "rsi": i15.get("RSI",50), "volume": volume,
                "rvol": (volume/vsma) if vsma else 1.0,
                "ema25": i15.get("EMA25",0), "ema50": i15.get("EMA50",0),
                "atr": atr, "is_daily_bullish": c1d >= e50, "is_green": close > opn,
                "macd": m1d, "macd_signal": ms1d, "stoch_k": i15.get("Stoch.K",50),
                # مميزات ML (من الإطار اليومي - مطابقة للتدريب)
                "ml_rsi": rsi1d,
                "ml_macd_pct": (m1d/c1d*100) if c1d else 0,
                "ml_hist_pct": ((m1d-ms1d)/c1d*100) if c1d else 0,
                "ml_vs_e25": ((c1d-e25)/e25*100) if e25 else 0,
                "ml_vs_e50": ((c1d-e50)/e50*100) if e50 else 0,
                "ml_atr_pct": (atr1d/c1d*100) if c1d else 0,
                "ml_stoch": stk, "ml_green": 1 if c1d > o1d else 0
            }
        except Exception as e:
            if "429" in str(e): time.sleep((attempt+1)*2)
            else: break
    return None

# ---------- توقع ML ----------
def ml_probability(data):
    if ml_model is None: return None
    try:
        row = pd.DataFrame([{
            "rsi": data["ml_rsi"], "macd_pct": data["ml_macd_pct"],
            "macd_hist_pct": data["ml_hist_pct"], "close_vs_ema25": data["ml_vs_e25"],
            "close_vs_ema50": data["ml_vs_e50"], "atr_pct": data["ml_atr_pct"],
            "stoch_k": data["ml_stoch"], "is_green": data["ml_green"],
            "dow": datetime.now(CAIRO_TZ).weekday()
        }])[FEATURES]
        return round(float(ml_model.predict_proba(row)[0][1]) * 100, 1)
    except Exception as e:
        logging.warning(f"ML error: {e}")
        return None

# ---------- التقييم ----------
def evaluate_stock(data, dna, regime):
    if not data["is_daily_bullish"] and data["change_pct"] < 2.5:
        return {"type":"None","score":0,"instant":False,"ml":None}
    if data["volume"] < MIN_VOLUME_THRESHOLD:
        return {"type":"None","score":0,"instant":False,"ml":None}
    if not (data["macd"] > data["macd_signal"]) or data["stoch_k"] >= 80:
        return {"type":"None","score":0,"instant":False,"ml":None}
    if data["change_pct"] >= 8.5:
        return {"type":"None","score":0,"instant":False,"ml":None}

    ml_prob = ml_probability(data)
    if ml_prob is not None and ml_prob < 40:   # 🧠 فيتو الذكاء الاصطناعي
        return {"type":"None","score":0,"instant":False,"ml":ml_prob}

    min_score = dna["min_score"]
    if regime["risk"] == "HIGH": min_score += 15
    if regime["risk"] == "EXTREME": return {"type":"None","score":0,"instant":False,"ml":ml_prob}
    if dna["total_trades"] >= 3 and dna["win_rate"] < 50: min_score += 10

    now = datetime.now(CAIRO_TZ)
    min_rvol = dna["min_rvol"] * 1.3 if (now.hour == 10 and now.minute <= 30) else dna["min_rvol"]

    if (data["change_pct"] >= 2.0 and data["rvol"] >= min_rvol and
        dna["rsi_min"] <= data["rsi"] <= dna["rsi_max"] and data["is_daily_bullish"] and
        (ml_prob is None or ml_prob >= 50)):
        return {"type":"Super MTF Breakout 🚀","score":98,"instant":True,"ml":ml_prob}

    score = 0
    if dna["rsi_min"] <= data["rsi"] <= dna["rsi_max"]: score += 25
    if data["close"] > data["ema25"]: score += 20
    if data["close"] > data["ema50"]: score += 20
    if data["is_daily_bullish"]: score += 20
    if data["is_green"]: score += 15
    if data["macd"] > data["macd_signal"]: score += 10
    if ml_prob is not None and ml_prob >= 60: score += 10   # 🧠 مكافأة ML

    if score >= min_score and data["rvol"] >= min_rvol:
        return {"type":"Regular Trend 📈","score":score,"instant":False,"ml":ml_prob}
    return {"type":"None","score":score,"instant":False,"ml":ml_prob}

# ---------- خطة الصفقة ----------
def calculate_plan(close, atr, mult=1.0):
    sl_dist = max(1.5*atr, close*0.05)
    stop = round(close - sl_dist, 2)
    risk_amt = TOTAL_CAPITAL * RISK_PER_TRADE_PCT * mult
    shares = max(1, math.floor(risk_amt / sl_dist))
    if shares * close > TOTAL_CAPITAL * 0.4:
        shares = math.floor(TOTAL_CAPITAL * 0.4 / close)
    return {
        "stop_loss": stop,
        "t1": round(close + 1.5*sl_dist, 2),
        "t2": round(close + 3.0*sl_dist, 2),
        "t3": round(close + 4.5*sl_dist, 2),
        "shares": shares,
        "weight_pct": round(shares*close/TOTAL_CAPITAL*100, 1),
        "risk_amount": round(risk_amt, 2)
    }

# ---------- متابعة الصفقات ----------
def track_active_trades(all_data):
    trades = load_json_local(ACTIVE_TRADES_FILE, {})
    dna_mem = load_json_local(DNA_FILE, {})
    stats = load_json_local(DAILY_STATS_FILE, {})
    now = datetime.now(CAIRO_TZ)
    today = now.strftime('%Y-%m-%d')
    stats.setdefault(today, {"wins":0,"losses":0,"pnl":0})
    updated = False

    for stock, tr in list(trades.items()):
        if stock not in all_data: continue
        price = all_data[stock]["close"]
        name = EGX33_SHARIA_MAP.get(stock, {}).get("name", stock)
        dna = dna_mem.get(stock, get_stock_dna(stock))

        days = (now.replace(tzinfo=None) - datetime.strptime(tr.get("entry_date", today), '%Y-%m-%d')).days
        if days >= TIME_STOP_DAYS and not tr.get("t1_hit"):
            send_telegram(f"⏳ **إغلاق زمني**\n📌 `{name}` راكد {days} أيام\n💡 حرر رأس المال لفرص أفضل")
            del trades[stock]; updated = True; continue

        if not tr.get("t1_hit") and price >= tr["t1"]:
            tr.update(t1_hit=True, current_stop=tr["entry_price"]); updated = True
            if not tr.get("recorded_win"):
                dna["winning_trades"] += 1; dna["total_trades"] += 1
                dna["win_rate"] = round(dna["winning_trades"]/dna["total_trades"]*100, 1)
                dna_mem[stock] = dna; tr["recorded_win"] = True
                stats[today]["wins"] += 1
            send_telegram(f"🎯 **T1 تحقق!**\n📌 `{name}` 💵 {price}\n✅ بِع 40% وارفع الستوب لسعر الدخول",
                          [[{"text":"✅ تم التنفيذ","callback_data":f"done_{stock}"}]])

        elif tr.get("t1_hit") and not tr.get("t2_hit") and price >= tr["t2"]:
            tr.update(t2_hit=True, current_stop=tr["t1"]); updated = True
            send_telegram(f"🚀 **T2 تحقق!**\n📌 `{name}` 💵 {price}\n✅ بِع 30% وارفع الستوب لـ T1")

        elif tr.get("t2_hit") and not tr.get("t3_hit") and price >= tr["t3"]:
            tr["t3_hit"] = True; updated = True
            send_telegram(f"🔥 **T3 تحقق!**\n📌 `{name}` 💵 {price}\n✅ بِع المتبقي بالكامل")

        elif price <= tr.get("current_stop", tr["stop_loss"]):
            if not tr.get("recorded_win"):
                dna["total_trades"] += 1
                dna["win_rate"] = round(dna["winning_trades"]/dna["total_trades"]*100, 1)
                dna["min_score"] = min(95, dna.get("min_score",60) + 3)
                dna_mem[stock] = dna
                stats[today]["losses"] += 1
            pnl = (price - tr["entry_price"]) * tr.get("shares", 1)
            stats[today]["pnl"] += pnl
            send_telegram(f"🛑 **Stop Loss صارم**\n📌 `{name}`\n📉 الخسارة: {pnl:+.0f} ج.م\n⚠️ أغلق الصفقة ولا تتراجع!")
            del trades[stock]; updated = True

    if updated:
        save_json_local(ACTIVE_TRADES_FILE, trades)
        save_file_to_github(ACTIVE_TRADES_FILE, trades, "🔄 trades update")
        save_json_local(DNA_FILE, dna_mem)
        save_file_to_github(DNA_FILE, dna_mem, "🧬 DNA update")
        save_json_local(DAILY_STATS_FILE, stats)

# ---------- التقارير ----------
def send_morning_report(regime):
    send_telegram(
        f"☀️ **التقرير الصباحي**\n\n"
        f"📊 EGX30: `{regime['price']:.0f}` ({regime['chg']:+.2f}%)\n"
        f"🎯 النظام: `{regime['regime']}`\n"
        f"💡 الاستراتيجية: {regime['strategy']}\n"
        f"🎯 أقصى صفقات: {regime['max_trades']}\n"
        f"🧠 نموذج ML: {'مفعّل ✅' if ml_model else 'غير متوفر ⚠️'}")

def send_closing_report(trades):
    stats = load_json_local(DAILY_STATS_FILE, {})
    s = stats.get(datetime.now(CAIRO_TZ).strftime('%Y-%m-%d'), {"wins":0,"losses":0,"pnl":0})
    tot = s["wins"] + s["losses"]
    wr = (s["wins"]/tot*100) if tot else 0
    send_telegram(
        f"🌙 **تقرير الإغلاق**\n\n"
        f"✅ رابحة: {s['wins']} | ❌ خاسرة: {s['losses']}\n"
        f"📈 نسبة النجاح: {wr:.0f}%\n"
        f"💰 صافي اليوم: {s['pnl']:+.0f} ج.م\n"
        f"💼 صفقات مفتوحة: {len(trades)}")

# ---------- الدورة الرئيسية ----------
def run_pipeline():
    if not is_egx_market_hours():
        logging.info("⏸️ السوق مغلق")
        return

    regime = analyze_market_regime()
    logging.info(f"🌍 السوق: {regime['regime']}")

    if regime["regime"] == "CRASH ⚫⚫":
        trades = load_json_local(ACTIVE_TRADES_FILE, {})
        if trades:
            send_telegram(f"🚨 **انهيار سوق!** إغلاق {len(trades)} صفقة فوراً. 💰 الكاش ملك")
            save_json_local(ACTIVE_TRADES_FILE, {})
            save_file_to_github(ACTIVE_TRADES_FILE, {}, "🚨 emergency close")
        return

    now = datetime.now(CAIRO_TZ)
    if now.hour == 10 and now.minute <= 5: send_morning_report(regime)
    if now.hour == 14 and 25 <= now.minute <= 30:
        send_closing_report(load_json_local(ACTIVE_TRADES_FILE, {}))

    all_data, trades = {}, load_json_local(ACTIVE_TRADES_FILE, {})

    for stock in STOCKS:
        data = fetch_stock_data_safe(stock)
        if not data: continue
        all_data[stock] = data

        res = evaluate_stock(data, get_stock_dna(stock), regime)
        if res["instant"] and stock not in trades and len(trades) < regime["max_trades"]:
            plan = calculate_plan(data["close"], data["atr"], regime["mult"])
            trades[stock] = {
                "entry_price": data["close"], "entry_date": now.strftime('%Y-%m-%d'),
                "stop_loss": plan["stop_loss"], "current_stop": plan["stop_loss"],
                "t1": plan["t1"], "t2": plan["t2"], "t3": plan["t3"],
                "shares": plan["shares"], "t1_hit": False, "t2_hit": False, "t3_hit": False
            }
            info = EGX33_SHARIA_MAP[stock]
            ml_line = f"🧠 **ثقة ML:** {res['ml']}%\n" if res["ml"] is not None else ""
            send_telegram(
                f"🚀 **إشارة شراء ({res['type']})**\n\n"
                f"🕌 `{info['name']}` ({info['sector']})\n"
                f"💵 السعر: {data['close']} ج.م\n"
                f"{ml_line}"
                f"📊 الكمية: {plan['shares']} سهم ({plan['weight_pct']}%)\n"
                f"💰 المخاطرة: {plan['risk_amount']} ج.م\n\n"
                f"🛡️ ستوب: `{plan['stop_loss']}`\n"
                f"🎯 T1: `{plan['t1']}` | T2: `{plan['t2']}` | T3: `{plan['t3']}`",
                [[{"text":"✅ تأكيد","callback_data":f"ok_{stock}"},
                  {"text":"❌ تجاهل","callback_data":f"no_{stock}"}]])
            save_json_local(ACTIVE_TRADES_FILE, trades)
            save_file_to_github(ACTIVE_TRADES_FILE, trades, f"➕ {stock}")
        time.sleep(0.5)

    track_active_trades(all_data)
    logging.info("✅ اكتمل التشغيل")

if __name__ == "__main__":
    run_pipeline()
    sys.exit(0)
