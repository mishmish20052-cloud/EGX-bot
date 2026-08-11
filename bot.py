import os
import sys
import logging
import requests

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.environ.get("8222819132:AAFmMjXCVnUFU8JUEcsujHKVjdmrJ1_zzPg", "")
CHAT_ID = os.environ.get("5418506244", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "unknown")
TOTAL_CAPITAL = os.environ.get("TOTAL_CAPITAL", "MISSING")

print("=" * 50)
print("🔍 تشخيص البوت")
print("=" * 50)
print(f"BOT_TOKEN موجود: {'✅' if BOT_TOKEN else '❌'}")
print(f"CHAT_ID: {CHAT_ID[:6]}..." if CHAT_ID else "CHAT_ID: ❌ غير موجود")
print(f"GITHUB_REPO: {GITHUB_REPO}")
print(f"TOTAL_CAPITAL: {TOTAL_CAPITAL}")
print("=" * 50)

def send_tg(msg):
    if not BOT_TOKEN:
        print("❌ لا يوجد BOT_TOKEN")
        return
    if not CHAT_ID:
        print("❌ لا يوجد CHAT_ID")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        )
        print(f"📱 حالة إرسال تليجرام: {r.status_code}")
        if r.status_code != 200:
            print(f"❌ رد تليجرام: {r.text}")
        else:
            print("✅ تم الإرسال بنجاح!")
    except Exception as e:
        print(f"❌ خطأ: {e}")

# اختبار مباشر
send_tg(f"🧪 *اختبار تشخيصي*\n\n✅ البوت يعمل بنجاح!\n📦 Repo: `{GITHUB_REPO}`\n💰 Capital: `{TOTAL_CAPITAL}`")

# اختبار market_regime
print("\n🌍 اختبار market_regime:")
try:
    from tradingview_ta import TA_Handler, Interval
    h = TA_Handler(symbol="EGX30", screener="egypt", exchange="EGX", interval=Interval.INTERVAL_1_DAY)
    i = h.get_analysis().indicators
    c = i.get("close", 0)
    print(f"✅ EGX30: السعر = {c}")
    if c == 0:
        send_tg("⚠️ *تحذير:* EGX30 يرجع سعر 0 - سنستخدم TMGH كبديل")
except Exception as e:
    print(f"❌ فشل EGX30: {e}")
    send_tg(f"⚠️ *EGX30 فشل*\nالخطأ: `{str(e)[:200]}`\nسأستخدم TMGH كبديل")

print("\n✅ انتهى التشخيص")
