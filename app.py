import requests
import json
import os
from datetime import datetime

# بيانات بوت تليجرام
TELEGRAM_TOKEN = os.environ.get("8222819132:AAFmMjXCVnUFU8JUEcsujHKVjdmrJ1_zzPg", "8222819132:AAFmMjXCVnUFU8JUEcsujHKVjdmrJ1_zzPg")
TELEGRAM_CHAT_ID = os.environ.get("5418506244", "5418506244")

EGX33_STOCKS = [
    "ABUK", "MFPC", "SKPC", "AMOC", "KPRE", "MBSC", "SCEM",
    "TMGH", "OCDI", "MASR", "EMFD", "ORAS", "ORHD", "HELI",
    "CLHO", "ISPH", "RMDA", "PHAR", "JUFO", "OLFI", "SUGR",
    "EFID", "EFIH", "FWRY", "ETEL", "ALCN", "CSAG", "ORWE",
    "ARAB", "CICH", "AUTO", "EALR", "ESRS"
]

def send_telegram_message(message):
    if not TELEGRAM_TOKEN or TELEGRAM_TOKEN.startswith("ضع_"):
        print("⚠️ لم يتم تعيين TELEGRAM_TOKEN")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"❌ خطأ تليجرام: {e}")

def fetch_tv_data(symbol):
    url = "https://scanner.tradingview.com/egypt/scan"
    payload = {
        "symbols": {"tickers": [f"EGX:{symbol}"]},
        "columns": [
            "close", "open", "high", "low", "volume",
            "EMA50", "EMA25", "RSI", "MACD.macd", "MACD.signal", "volume|20"
        ]
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if data and "data" in data and len(data["data"]) > 0:
                v = data["data"][0]["d"]
                return {
                    "close": v[0], "open": v[1], "high": v[2], "low": v[3], "volume": v[4],
                    "EMA50": v[5], "EMA25": v[6], "RSI": v[7], "MACD": v[8],
                    "MACD_Signal": v[9], "Vol_SMA": v[10]
                }
    except Exception:
        pass
    return None

def analyze_stock(symbol):
    data = fetch_tv_data(symbol)
    if not data or not data['close'] or not data['EMA50']:
        return None
    
    # 1. فلتر الاتجاه الصاعد اليومي
    if data['close'] <= data['EMA50']:
        return None

    score = 0
    details = []

    # 2. تقييم EMA25 (20 نقطة)
    if data['EMA25'] and data['close'] > data['EMA25']:
        score += 20
        details.append("أعلى من EMA25")

    # 3. تقييم RSI والديناميكية الفتية (25 نقطة)
    rsi_val = data['RSI']
    if rsi_val:
        if 50 <= rsi_val <= 62: # المنطقة الذهبية للزخم الخالي من الإشباع
            score += 25
            details.append(f"RSI مثالي [{rsi_val:.1f}]")
        elif 48 <= rsi_val <= 68:
            score += 15
            details.append(f"RSI مقبول [{rsi_val:.1f}]")

    # 4. تقييم MACD (20 نقطة)
    if data['MACD'] and data['MACD_Signal'] and data['MACD'] > data['MACD_Signal']:
        score += 20
        details.append("MACD إيجابي")

    # 5. حساب الـ RVOL وتقييم السيولة (حتى 25 نقطة)
    rvol = 0.0
    if data['volume'] and data['Vol_SMA'] and data['Vol_SMA'] > 0:
        rvol = round(data['volume'] / data['Vol_SMA'], 2)
        
        if rvol >= 2.0:
            score += 25
            details.append(f"🔥 سيولة انفجارية (RVOL: {rvol}x)")
        elif rvol >= 1.3:
            score += 15
            details.append(f"⚡ سيولة مرتفعة (RVOL: {rvol}x)")
        elif rvol >= 1.0:
            score += 10
            details.append(f"سيولة أعلى من المتوسط (RVOL: {rvol}x)")

    # 6. إغلاق ساعة صاعد (10 نقاط)
    if data['close'] > data['open']:
        score += 10
        details.append("إغلاق صاعد")

    return {
        "ticker": symbol,
        "score": score,
        "rvol": rvol,
        "price": round(float(data['close']), 2),
        "details": details
    }

def main():
    print(f"🔍 بدء تشغيل الفحص المباشر المطور: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    signals = []
    for stock in EGX33_STOCKS:
        res = analyze_stock(stock)
        # تشديد شرط الفحص إلى 80+ نقطة بعد إضافة وزن الـ RVOL
        if res and res['score'] >= 80:
            signals.append(res)

    if signals:
        # 🎯 الترتيب التلقائي: من الأعلى تقييماً ثم الأكبر في الـ RVOL
        signals.sort(key=lambda x: (x['score'], x['rvol']), reverse=True)

        alert_msg = f"🚀 *تنبيه الفرص المرتبة - البورصة المصرية (EGX33)*\n🗓 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        
        for rank, s in enumerate(signals, 1):
            medal = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else "📌"
            alert_msg += (
                f"{medal} *الترتيب #{rank}:* `{s['ticker']}`\n"
                f"📊 *التقييم الإجمالي:* {s['score']}/100 | *RVOL:* {s['rvol']}x\n"
                f"💵 *السعر:* {s['price']} ج.م\n"
                f"📈 *المؤشرات:* {', '.join(s['details'])}\n"
                f"----------------------------------------\n"
            )
        
        send_telegram_message(alert_msg)
        print(f"✅ تم إرسال {len(signals)} فرص مرتبة بنجاح للتليجرام.")
    else:
        print("⚡ لا توجد فرص تنطبق عليها الشروط المتقدمة حالياً.")

if __name__ == "__main__":

    main()
    
