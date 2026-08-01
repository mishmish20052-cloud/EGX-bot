import os
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

# قراءة بيانات التليجرام المضافة في متغيرات البيئة
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram(message):
    """دالة إرسال الإشعار لتطبيـق التليجرام"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID, 
        "text": message, 
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error sending message: {e}")

@app.route('/webhook', methods=['POST'])
def webhook():
    """مستقبل الإشارات الآلية من تريدينج فيو"""
    try:
        data = request.get_json(force=True)
        ticker = data.get('ticker', 'UNKNOWN')
        price = float(data.get('price', 0.0))
        stage = int(data.get('stage', 1))
        total_capital = float(data.get('capital', 100000))

        if price <= 0:
            return jsonify({"status": "invalid price"}), 400

        # حساب وقف الخسارة المشدد (3%)
        stop_loss = round(price * 0.97, 2)
        
        # حساب توزيع الهرم التجميعي (40% / 35% / 25%)
        ratios = {1: 0.40, 2: 0.35, 3: 0.25}
        current_ratio = ratios.get(stage, 0.40)
        entry_amount = total_capital * current_ratio
        max_loss = entry_amount * 0.03  # المخاطرة الكلية للمحفظة 1.2%

        # صياغة إشعار التنبيه المرتب
        msg = (
            f"🚨 **إشارة دخول جديدة (البورصة المصرية)**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 **السهم:** `{ticker}`\n"
            f"📊 **المرحلة:** المرحلة {stage} ({int(current_ratio * 100)}%)\n"
            f"💵 **سعر الشراء:** `{price:.2f} ج.م`\n"
            f"🛑 **وقف الخسارة (3%):** `{stop_loss:.2f} ج.م`\n"
            f"💰 **المبلغ المطلوب:** `{entry_amount:,.0f} ج.م`\n"
            f"📉 **مخاطرة المحفظة:** `{max_loss:,.0f} ج.م` (1.2%)\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ *إشارة آلية من TradingView*"
        )
        
        send_telegram(msg)
        return jsonify({"status": "success"}), 200

    except Exception as e:
        print(f"Webhook error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/', methods=['GET'])
def home():
    """نقطة الفحص للتأكد من تشغيل السيرفر"""
    return "EGX Trading Bot is Running!", 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
  
