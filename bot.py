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
