import json
import logging
import numpy as np
import pandas as pd
import yfinance as yf
import joblib
from datetime import datetime
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
from sklearn.utils.class_weight import compute_sample_weight

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

STOCKS = [
    "ADIB.CA","SAUD.CA","FAIT.CA","EGAL.CA","AMOC.CA","SKPC.CA","ICFC.CA",
    "ARCC.CA","MCQE.CA","LCSW.CA","ATQA.CA","ORWE.CA","MTIE.CA","ACGC.CA",
    "ISPH.CA","RMDA.CA","EFID.CA","JUFO.CA","OLFI.CA","MPCO.CA",
    "MASR.CA","ORHD.CA","PHDC.CA","OCDI.CA","TMGH.CA","CIRA.CA",
    "EFIH.CA","ETEL.CA","RACC.CA","EGAS.CA","ETRS.CA","IFAP.CA","ORAS.CA"
]

# نفس ميزات V1 لضمان توافق البوت الحالي
FEATURES = ["rsi","macd_pct","macd_hist_pct","close_vs_ema25",
            "close_vs_ema50","atr_pct","stoch_k","is_green","dow"]

# 🎯 معايير أكثر واقعية للسوق المصري
HORIZON = 5
TARGET_PCT = 1.5
MAX_DD_PCT = 5.0

def compute_features(df):
    close = df["Close"]; high = df["High"]; low = df["Low"]; opn = df["Open"]
    f = pd.DataFrame(index=df.index)
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    ag = gain.ewm(alpha=1/14, adjust=False).mean()
    al = loss.ewm(alpha=1/14, adjust=False).mean()
    f["rsi"] = 100 - 100 / (1 + ag / al.replace(0, np.nan))
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    f["macd_pct"] = macd / close * 100
    f["macd_hist_pct"] = (macd - signal) / close * 100
    f["close_vs_ema25"] = (close - close.ewm(span=25, adjust=False).mean()) / close * 100
    f["close_vs_ema50"] = (close - close.ewm(span=50, adjust=False).mean()) / close * 100
    tr = pd.concat([high-low, (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
    f["atr_pct"] = tr.ewm(alpha=1/14, adjust=False).mean() / close * 100
    l14 = low.rolling(14).min(); h14 = high.rolling(14).max()
    f["stoch_k"] = (close - l14) / (h14 - l14).replace(0, np.nan) * 100
    f["is_green"] = (close > opn).astype(int)
    f["dow"] = df.index.dayofweek
    return f

def make_labels(df):
    close = df["Close"]; low = df["Low"]
    future_close = close.shift(-HORIZON)
    future_min = low.rolling(HORIZON).min().shift(-HORIZON)
    profit = future_close >= close * (1 + TARGET_PCT/100)
    safe = future_min >= close * (1 - MAX_DD_PCT/100)
    return (profit & safe).astype(int)

def main():
    logging.info("🚀 بدء التدريب المحسّن V2...")
    all_x, all_y = [], []
    for sym in STOCKS:
        try:
            df = yf.download(sym, period="5y", interval="1d", auto_adjust=True, progress=False)
            if df is None or len(df) < 300:
                logging.warning(f"⚠️ {sym}: بيانات غير كافية")
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            f = compute_features(df)
            y = make_labels(df)
            data = f.join(y.rename("label")).dropna()
            if len(data) < 100: continue
            all_x.append(data[FEATURES]); all_y.append(data["label"])
            logging.info(f"✅ {sym}: {len(data)} صف | نجاح: {data['label'].mean()*100:.1f}%")
        except Exception as e:
            logging.error(f"❌ {sym}: {e}")

    if not all_x: raise SystemExit("💥 لا توجد بيانات!")

    X = pd.concat(all_x); y = pd.concat(all_y)
    logging.info(f"📊 إجمالي: {len(X)} | نسبة النجاح: {y.mean()*100:.1f}%")

    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, shuffle=False)
    w = compute_sample_weight("balanced", y_tr)

    model = GradientBoostingClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.05,
        min_samples_leaf=20, subsample=0.8, random_state=42)
    model.fit(X_tr, y_tr, sample_weight=w)

    pred = model.predict(X_te)
    acc = accuracy_score(y_te, pred)
    prec = precision_score(y_te, pred, zero_division=0)
    rec = recall_score(y_te, pred, zero_division=0)
    f1 = f1_score(y_te, pred, zero_division=0)
    logging.info(f"🎯 Acc {acc*100:.1f}% | Prec {prec*100:.1f}% | Rec {rec*100:.1f}% | F1 {f1*100:.1f}%")

    joblib.dump(model, "ml/egx_model.joblib")
    meta = {
        "trained_at": datetime.utcnow().isoformat(),
        "version": "V2",
        "accuracy": round(acc, 3), "precision": round(prec, 3),
        "recall": round(rec, 3), "f1_score": round(f1, 3),
        "samples": int(len(X)), "positive_rate": round(float(y.mean()), 3),
        "features": FEATURES
    }
    with open("ml/model_meta.json", "w") as fh:
        json.dump(meta, fh, indent=2)
    logging.info("💾 تم حفظ النموذج المحسّن!")

if __name__ == "__main__":
    main()
