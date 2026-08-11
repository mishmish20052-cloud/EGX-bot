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

# 🆕 12 ميزة (9 قديمة + 3 جديدة متوافقة مع البوت)
FEATURES = ["rsi","macd_pct","macd_hist_pct","close_vs_ema25",
            "close_vs_ema50","atr_pct","stoch_k","is_green","dow",
            "close_vs_ema200","bb_pos","adx"]

HORIZON = 5
TARGET_PCT = 1.5
MAX_DD_PCT = 5.0

def compute_features(df):
    close = df["Close"]; high = df["High"]; low = df["Low"]; opn = df["Open"]
    f = pd.DataFrame(index=df.index)

    # RSI
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    ag = gain.ewm(alpha=1/14, adjust=False).mean()
    al = loss.ewm(alpha=1/14, adjust=False).mean()
    f["rsi"] = 100 - 100 / (1 + ag / al.replace(0, np.nan))

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    f["macd_pct"] = macd / close * 100
    f["macd_hist_pct"] = (macd - signal) / close * 100

    # EMAs
    f["close_vs_ema25"] = (close - close.ewm(span=25, adjust=False).mean()) / close * 100
    f["close_vs_ema50"] = (close - close.ewm(span=50, adjust=False).mean()) / close * 100
    f["close_vs_ema200"] = (close - close.ewm(span=200, adjust=False).mean()) / close * 100

    # ATR
    tr = pd.concat([high-low, (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
    f["atr_pct"] = tr.ewm(alpha=1/14, adjust=False).mean() / close * 100

    # Stochastic
    l14 = low.rolling(14).min(); h14 = high.rolling(14).max()
    f["stoch_k"] = (close - l14) / (h14 - l14).replace(0, np.nan) * 100

    # 🆕 Bollinger Position (0-1)
    mid = close.rolling(20).mean()
    sd = close.rolling(20).std()
    up = mid + 2*sd; lo = mid - 2*sd
    f["bb_pos"] = (close - lo) / (up - lo).replace(0, np.nan)

    # 🆕 ADX (قوة الاتجاه)
    up_move = high.diff(); down_move = -low.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    atr14 = tr.ewm(alpha=1/14, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1/14, adjust=False).mean() / atr14.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1/14, adjust=False).mean() / atr14.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    f["adx"] = dx.ewm(alpha=1/14, adjust=False).mean()

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
    logging.info("🚀 بدء التدريب الاحترافي V3...")
    tr_x, tr_y, te_x, te_y = [], [], [], []

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

            # 🛡️ تقسيم زمني مع Purge لمنع تسريب البيانات
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
        max_iter=250, learning_rate=0.05, max_depth=5,
        min_samples_leaf=20, l2_regularization=1.0, random_state=42)
    model.fit(X_tr, y_tr, sample_weight=w)

    pred = model.predict(X_te)
    proba = model.predict_proba(X_te)[:, 1]

    acc = accuracy_score(y_te, pred)
    logging.info(f"🎯 عند عتبة 0.5 => Acc {acc*100:.1f}% | Prec {precision_score(y_te, pred, zero_division=0)*100:.1f}% | Rec {recall_score(y_te, pred, zero_division=0)*100:.1f}%")

    # 🎯 ضبط العتبة الذكي: أعلى Precision مع Recall >= 15%
    best_t, best_p = 0.5, 0.0
    thresholds = {}
    for t in [0.5, 0.55, 0.6, 0.65, 0.7, 0.75]:
        p_t = (proba >= t).astype(int)
        prec_t = precision_score(y_te, p_t, zero_division=0)
        rec_t = recall_score(y_te, p_t, zero_division=0)
        thresholds[str(t)] = {"precision": round(prec_t, 3), "recall": round(rec_t, 3)}
        logging.info(f"عتبة {t}: Precision {prec_t*100:.1f}% | Recall {rec_t*100:.1f}%")
        if rec_t >= 0.15 and prec_t > best_p:
            best_p = prec_t; best_t = t

    final_pred = (proba >= best_t).astype(int)
    f_acc = accuracy_score(y_te, final_pred)
    f_prec = precision_score(y_te, final_pred, zero_division=0)
    f_rec = recall_score(y_te, final_pred, zero_division=0)
    f_f1 = f1_score(y_te, final_pred, zero_division=0)
    logging.info(f"🏆 العتبة المثلى {best_t}: Prec {f_prec*100:.1f}% | Rec {f_rec*100:.1f}% | F1 {f_f1*100:.1f}%")

    # أهمية الميزات
    imp = permutation_importance(model, X_te, y_te, n_repeats=5, random_state=42)
    importance = dict(sorted(zip(FEATURES, imp.importances_mean), key=lambda x: x[1], reverse=True))

    joblib.dump(model, "ml/egx_model.joblib")
    meta = {
        "trained_at": datetime.utcnow().isoformat(),
        "version": "V3",
        "accuracy": round(f_acc, 3),
        "precision": round(f_prec, 3),
        "recall": round(f_rec, 3),
        "f1_score": round(f_f1, 3),
        "recommended_threshold": best_t,
        "thresholds": thresholds,
        "samples": int(len(X_tr) + len(X_te)),
        "positive_rate": round(float(y_tr.mean()), 3),
        "features": FEATURES,
        "importance": {k: round(v, 3) for k, v in importance.items()}
    }
    with open("ml/model_meta.json", "w") as fh:
        json.dump(meta, fh, indent=2)
    logging.info("💾 تم حفظ النموذج V3!")

if __name__ == "__main__":
    main()
