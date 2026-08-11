import json
import logging
import numpy as np
import pandas as pd
import yfinance as yf
import joblib
from datetime import datetime
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report

logging.basicConfig(level=logging.INFO)

# أسهم EGX33 Sharia (بنفس رموز yfinance)
STOCKS = [
    "ADIB.CA","SAUD.CA","FAIT.CA","EGAL.CA","AMOC.CA","SKPC.CA","ICFC.CA",
    "ARCC.CA","MCQE.CA","LCSW.CA","ATQA.CA","ORWE.CA","MTIE.CA","ACGC.CA",
    "ISPH.CA","RMDA.CA","EFID.CA","JUFO.CA","OLFI.CA","MPCO.CA",
    "MASR.CA","ORHD.CA","PHDC.CA","OCDI.CA","TMGH.CA","CIRA.CA",
    "EFIH.CA","ETEL.CA","RACC.CA","EGAS.CA","ETRS.CA","IFAP.CA","ORAS.CA"
]

HORIZON = 5        # أفق الصفقة: 5 جلسات (Swing)
TARGET_PCT = 2.0   # الهدف: ربح 2%+
MAX_DD_PCT = 4.0   # أقصى تراجع مقبول قبل تحقيق الهدف

FEATURES = ["rsi","macd_pct","macd_hist_pct","close_vs_ema25",
            "close_vs_ema50","atr_pct","stoch_k","is_green","dow"]

def compute_features(df):
    """حساب المؤشرات بنفس طريقة البوت تماماً (مهم للتطابق!)"""
    close, high, low, opn = df["Close"], df["High"], df["Low"], df["Open"]
    f = pd.DataFrame(index=df.index)

    # RSI (تنعيم Wilder مثل TradingView)
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    ag = gain.ewm(alpha=1/14, adjust=False).mean()
    al = loss.ewm(alpha=1/14, adjust=False).mean()
    f["rsi"] = 100 - 100 / (1 + ag / al.replace(0, np.nan))

    # MACD كنسبة من السعر (للمقارنة بين الأسهم)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    f["macd_pct"] = macd / close * 100
    f["macd_hist_pct"] = (macd - signal) / close * 100

    # المسافة عن المتوسطات
    f["close_vs_ema25"] = (close - close.ewm(span=25, adjust=False).mean()) / close * 100
    f["close_vs_ema50"] = (close - close.ewm(span=50, adjust=False).mean()) / close * 100

    # التذبذب ATR%
    tr = pd.concat([high-low, (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
    f["atr_pct"] = tr.ewm(alpha=1/14, adjust=False).mean() / close * 100

    # Stochastic %K
    l14, h14 = low.rolling(14).min(), high.rolling(14).max()
    f["stoch_k"] = (close - l14) / (h14 - l14).replace(0, np.nan) * 100

    f["is_green"] = (close > opn).astype(int)
    f["dow"] = df.index.dayofweek
    return f

def make_labels(df):
    """1 = صفقة كانت ستنجح (ربح 2%+ خلال 5 جلسات دون تراجع 4%)"""
    close, low = df["Close"], df["Low"]
    future_close = close.shift(-HORIZON)
    future_min = low.rolling(HORIZON).min().shift(-HORIZON)
    profit = future_close >= close * (1 + TARGET_PCT/100)
    safe = future_min >= close * (1 - MAX_DD_PCT/100)
    return (profit & safe).astype(int)

def main():
    all_x, all_y = [], []
    for sym in STOCKS:
        try:
            df = yf.download(sym, period="3y", interval="1d",
                             auto_adjust=True, progress=False)
            if df is None or len(df) < 250:
                logging.warning(f"⚠️ بيانات غير كافية: {sym}")
                continue
            # تسطيح أعمدة MultiIndex من yfinance
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            f = compute_features(df)
            y = make_labels(df)
            data = f.join(y.rename("label")).dropna()
            all_x.append(data[FEATURES])
            all_y.append(data["label"])
            logging.info(f"✅ {sym}: {len(data)} صف")
        except Exception as e:
            logging.error(f"❌ {sym}: {e}")

    if not all_x:
        raise SystemExit("لا توجد بيانات كافية للتدريب!")

    X = pd.concat(all_x)
    y = pd.concat(all_y)
    logging.info(f"📊 إجمالي البيانات: {len(X)} | نسبة الإيجابيات: {y.mean()*100:.1f}%")

    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, shuffle=False)

    model = RandomForestClassifier(
        n_estimators=150, max_depth=8, min_samples_leaf=10,
        class_weight="balanced", random_state=42, n_jobs=2
    )
    model.fit(X_tr, y_tr)

    pred = model.predict(X_te)
    acc = accuracy_score(y_te, pred)
    logging.info(f"🎯 الدقة على بيانات الاختبار: {acc*100:.1f}%")
    print(classification_report(y_te, pred, digits=2))

    joblib.dump(model, "ml/egx_model.joblib")

    meta = {
        "trained_at": datetime.utcnow().isoformat(),
        "accuracy": round(acc, 3),
        "samples": int(len(X)),
        "positive_rate": round(float(y.mean()), 3),
        "features": FEATURES,
        "importance": {k: round(float(v), 3) for k, v in
                       zip(FEATURES, model.feature_importances_)}
    }
    with open("ml/model_meta.json", "w") as fh:
        json.dump(meta, fh, indent=2)

    logging.info("💾 تم حفظ النموذج بنجاح!")

if __name__ == "__main__":
    main()
