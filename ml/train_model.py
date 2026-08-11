import json
import logging
import numpy as np
import pandas as pd
import yfinance as yf
import joblib
from datetime import datetime
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
from sklearn.utils.class_weight import compute_sample_weight
from sklearn.inspection import permutation_importance

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

STOCKS = [
    "ADIB.CA","SAUD.CA","FAIT.CA","EGAL.CA","AMOC.CA","SKPC.CA","ICFC.CA",
    "ARCC.CA","MCQE.CA","LCSW.CA","ATQA.CA","ORWE.CA","MTIE.CA","ACGC.CA",
    "ISPH.CA","RMDA.CA","EFID.CA","JUFO.CA","OLFI.CA","MPCO.CA",
    "MASR.CA","ORHD.CA","PHDC.CA","OCDI.CA","TMGH.CA","CIRA.CA",
    "EFIH.CA","ETEL.CA","RACC.CA","EGAS.CA","ETRS.CA","IFAP.CA","ORAS.CA"
]

FEATURES = ["rs_5d","rs_20d","rs_60d","momentum_60d","reversal_5d",
            "vol_zscore_20","vol_zscore_5","volatility_20","atr_pct","market_regime"]

HORIZON = 5
TARGET_PCT = 1.5
MAX_DD_PCT = 5.0

def get_market_regime_series(idx):
    """حساب حالة السوق لكل يوم تاريخياً"""
    try:
        egx = yf.download("EGX30.CA", period="5y", interval="1d",
                          auto_adjust=True, progress=False)
        if egx is None or len(egx) < 200:
            egx = yf.download("^EGX30", period="5y", interval="1d",
                              auto_adjust=True, progress=False)
        if isinstance(egx.columns, pd.MultiIndex):
            egx.columns = egx.columns.get_level_values(0)
        c = egx["Close"]
        sma50 = c.rolling(50).mean()
        sma200 = c.rolling(200).mean()
        regime = pd.Series(1, index=egx.index)  # Sideways = 1
        regime[(c > sma50) & (sma50 > sma200)] = 2  # Bull = 2
        regime[(c < sma50) & (sma50 < sma200)] = 0  # Bear = 0
        return regime.reindex(idx, method="ffill").fillna(1)
    except Exception as e:
        logging.error(f"Market regime error: {e}")
        return pd.Series(1, index=idx)

def compute_smart_features(df, regime_series):
    c = df["Close"]; h = df["High"]; l = df["Low"]; v = df["Volume"]
    f = pd.DataFrame(index=df.index)

    # 🏆 القوة النسبية (Relative Strength)
    f["rs_5d"]  = c.pct_change(5)  * 100
    f["rs_20d"] = c.pct_change(20) * 100
    f["rs_60d"] = c.pct_change(60) * 100

    # 🏆 Momentum و Reversal
    f["momentum_60d"] = c.pct_change(60) * 100
    f["reversal_5d"]  = -c.pct_change(5) * 100  # سالب لأن الانعكاس = الشراء بعد الهبوط

    # 🏆 Volume Z-Score (شذوذ أحجام التداول)
    v_mean20 = v.rolling(20).mean(); v_std20 = v.rolling(20).std()
    v_mean5  = v.rolling(5).mean();  v_std5  = v.rolling(5).std()
    f["vol_zscore_20"] = (v - v_mean20) / v_std20.replace(0, np.nan)
    f["vol_zscore_5"]  = (v - v_mean5)  / v_std5.replace(0, np.nan)

    # 🏆 التقلب 20 يوم
    f["volatility_20"] = c.pct_change().rolling(20).std() * 100

    # 🏆 ATR%
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    f["atr_pct"] = tr.ewm(alpha=1/14, adjust=False).mean() / c * 100

    # 🏆 حالة السوق (ميزة خارجية)
    f["market_regime"] = regime_series.reindex(df.index).fillna(1)

    return f

def make_labels(df):
    c = df["Close"]; l = df["Low"]
    future_close = c.shift(-HORIZON)
    future_min = l.rolling(HORIZON).min().shift(-HORIZON)
    profit = future_close >= c * (1 + TARGET_PCT/100)
    safe = future_min >= c * (1 - MAX_DD_PCT/100)
    return (profit & safe).astype(int)

def main():
    logging.info("🚀 بدء V4 - Smart Features...")
    tr_x, tr_y, te_x, te_y = [], [], [], []

    # تحميل EGX30 مرة واحدة (محسّن)
    logging.info("📥 تحميل EGX30 لحساب Market Regime...")
    egx_regime_base = None
    try:
        egx = yf.download("EGX30.CA", period="5y", interval="1d",
                          auto_adjust=True, progress=False)
        if egx is None or len(egx) < 200:
            egx = yf.download("^EGX30", period="5y", interval="1d",
                              auto_adjust=True, progress=False)
        if isinstance(egx.columns, pd.MultiIndex):
            egx.columns = egx.columns.get_level_values(0)
        c = egx["Close"]
        sma50 = c.rolling(50).mean()
        sma200 = c.rolling(200).mean()
        egx_regime_base = pd.Series(1, index=egx.index)
        egx_regime_base[(c > sma50) & (sma50 > sma200)] = 2
        egx_regime_base[(c < sma50) & (sma50 < sma200)] = 0
        logging.info(f"✅ EGX30: {len(egx)} يوم")
    except Exception as e:
        logging.error(f"فشل تحميل EGX30: {e}")

    for sym in STOCKS:
        try:
            df = yf.download(sym, period="5y", interval="1d",
                             auto_adjust=True, progress=False)
            if df is None or len(df) < 300:
                logging.warning(f"⚠️ {sym}: بيانات غير كافية")
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            # محاذاة regime مع تواريخ السهم
            if egx_regime_base is not None:
                regime = egx_regime_base.reindex(df.index, method="ffill").fillna(1)
            else:
                regime = pd.Series(1, index=df.index)

            f = compute_smart_features(df, regime)
            y = make_labels(df)
            data = f.join(y.rename("label")).dropna()
            if len(data) < 100: continue

            cut = int(len(data) * 0.8)
            tr = data.iloc[:cut].iloc[:-HORIZON]
            te = data.iloc[cut:]

            tr_x.append(tr[FEATURES]); tr_y.append(tr["label"])
            te_x.append(te[FEATURES]); te_y.append(te["label"])
            logging.info(f"✅ {sym}: {len(data)} صف | نجاح: {data['label'].mean()*100:.1f}%")
        except Exception as e:
            logging.error(f"❌ {sym}: {e}")

    if not tr_x: raise SystemExit("💥 لا توجد بيانات!")

    X_tr = pd.concat(tr_x); y_tr = pd.concat(tr_y)
    X_te = pd.concat(te_x); y_te = pd.concat(te_y)
    logging.info(f"📊 تدريب: {len(X_tr)} | اختبار: {len(X_te)} | نجاح: {y_tr.mean()*100:.1f}%")

    w = compute_sample_weight("balanced", y_tr)

    model = HistGradientBoostingClassifier(
        max_iter=300, learning_rate=0.05, max_depth=6,
        min_samples_leaf=25, l2_regularization=1.5, random_state=42)
    model.fit(X_tr, y_tr, sample_weight=w)

    proba = model.predict_proba(X_te)[:, 1]

    # 🎯 اختبار جميع العتبات
    logging.info("=" * 60)
    logging.info("📊 نتائج العتبات المختلفة:")
    logging.info("=" * 60)
    thresholds = {}
    best_t, best_score = 0.5, -1
    for t in [0.45, 0.5, 0.55, 0.6, 0.65, 0.7]:
        p_t = (proba >= t).astype(int)
        prec = precision_score(y_te, p_t, zero_division=0)
        rec = recall_score(y_te, p_t, zero_division=0)
        f1 = f1_score(y_te, p_t, zero_division=0)
        thresholds[str(t)] = {"precision": round(prec, 3), "recall": round(rec, 3), "f1": round(f1, 3)}
        logging.info(f"عتبة {t}: Prec {prec*100:5.1f}% | Rec {rec*100:5.1f}% | F1 {f1*100:5.1f}%")
        # أفضل عتبة: F1 عالٍ مع Precision لا تقل عن 40%
        if prec >= 0.40 and f1 > best_score:
            best_score = f1; best_t = t

    final_pred = (proba >= best_t).astype(int)
    f_acc = accuracy_score(y_te, final_pred)
    f_prec = precision_score(y_te, final_pred, zero_division=0)
    f_rec = recall_score(y_te, final_pred, zero_division=0)
    f_f1 = f1_score(y_te, final_pred, zero_division=0)

    logging.info("=" * 60)
    logging.info(f"🏆 العتبة المختارة {best_t}:")
    logging.info(f"   Accuracy : {f_acc*100:.1f}%")
    logging.info(f"   Precision: {f_prec*100:.1f}%  ← الأهم")
    logging.info(f"   Recall   : {f_rec*100:.1f}%")
    logging.info(f"   F1 Score : {f_f1*100:.1f}%")
    logging.info("=" * 60)

    # Feature importance
    try:
        imp = permutation_importance(model, X_te, y_te, n_repeats=5, random_state=42)
        importance = dict(sorted(zip(FEATURES, imp.importances_mean), key=lambda x: x[1], reverse=True))
    except Exception:
        importance = {k: round(v, 3) for k, v in zip(FEATURES, model.feature_importances_)}

    joblib.dump(model, "ml/egx_model.joblib")
    meta = {
        "trained_at": datetime.utcnow().isoformat(),
        "version": "V4 - Smart Features",
        "accuracy": round(f_acc, 3),
        "precision": round(f_prec, 3),
        "recall": round(f_rec, 3),
        "f1_score": round(f_f1, 3),
        "recommended_threshold": best_t,
        "thresholds": thresholds,
        "samples": int(len(X_tr) + len(X_te)),
        "positive_rate": round(float(y_tr.mean()), 3),
        "features": FEATURES,
        "importance": {k: round(float(v), 3) for k, v in importance.items()}
    }
    with open("ml/model_meta.json", "w") as fh:
        json.dump(meta, fh, indent=2)
    logging.info("💾 تم حفظ V4!")

if __name__ == "__main__":
    main()
