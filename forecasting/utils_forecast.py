# ==============================================================================
# Dengue PE | forecasting/utils_forecast.py
#
# Camada compartilhada do pipeline de previsao (padrao Mosqlimate). Usada por
# train_models.py (treino/backtest offline) e por get_forecast.py (funcao
# chamada pelo painel). Porto em Python do forecasting/utils_forecast.R do
# painel RIDE - mesmas regras, mesmos nomes de colunas na saida.
#
# Pacotes: statsmodels (ARIMA, SARIMA, GAM Bin. Negativa, STL + ETS), scipy
# (Box-Cox), patsy (splines) e, opcional, scikit-learn (Random Forest).
#
# Diferencas em relacao ao R (nao ha auto.arima/mgcv/ranger em Python):
#   - ARIMA/SARIMA: busca stepwise propria por AICc (estilo auto.arima), com d
#     escolhido pelo teste KPSS. No SARIMA a parte sazonal e (0,1,0)[52]: a
#     diferenca sazonal e aplicada e a parte nao sazonal e buscada - no R ela
#     ainda testa P,Q <= 1, caro demais com periodo 52 no statsmodels.
#   - GAM: regressao Binomial Negativa com splines de regressao (patsy cc/cr,
#     sem penalizacao) - os mesmos termos do mgcv. A tendencia `t` fica
#     congelada no ultimo valor ao prever (o mgcv extrapola linearmente).
#   - Random Forest: scikit-learn; o IP vem dos quantis das previsoes das
#     arvores (no ranger, da floresta quantilica de Meinshausen).
# ==============================================================================

from __future__ import annotations

import json
import math
import os
import sys
import warnings
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from scipy.special import inv_boxcox

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from painel import config as cfg  # noqa: E402
from painel.fonte import conectar  # noqa: E402

try:  # opcional: liga a classe "machine learning"
    from sklearn.ensemble import RandomForestRegressor
    FC_TEM_SKLEARN = not os.environ.get("DENGUE_FC_NO_SKLEARN")
except ImportError:  # pragma: no cover
    FC_TEM_SKLEARN = False

# ------------------------------------------------------------- parametros -----
FC_HORIZON_DEFAULT = 4     # horizonte do BACKTEST (semanas), nao da previsao ao vivo
FC_FOURIER_K = 3           # harmonicas de Fourier para a sazonalidade anual
FC_LAG = 4                 # defasagem dos preditores exogenos (GAM/RF)
FC_PI_LEVEL = 80           # nivel do intervalo de predicao (%)
FC_MC_SIM = 1500           # simulacoes Monte Carlo para o IP do GAM
FC_PERIODO = 52
# Semanas mais recentes SEMPRE reprojetadas (notificacao ainda incompleta):
# o corte de treino recua isso a partir da ULTIMA linha da base, nunca de "hoje".
FC_SEMANAS_RECENTES = 4
FC_MIN_SEMANAS = 160

MODELS_DIR = Path(__file__).resolve().parent / "models"
SEMANA_CORTE_SANITARIA = cfg.SEMANA_CORTE_SANITARIA
ano_sanitario = cfg.ano_sanitario
rotulo_ano_sanitario = cfg.rotulo_ano_sanitario

_Z = stats.norm.ppf(1 - (1 - FC_PI_LEVEL / 100) / 2)


# ------------------------------------------------- calendario epidemiologico --
def _inicio_ano_epi(ano: int) -> date:
    """Domingo que abre a SE 1 (calendario MMWR, o do InfoDengue/SINAN)."""
    jan1 = date(ano, 1, 1)
    dow = (jan1.weekday() + 1) % 7          # domingo = 0
    return jan1 - timedelta(days=dow) if dow <= 3 else jan1 + timedelta(days=7 - dow)


def epi_ano_semana(d) -> tuple[int, int]:
    d = pd.Timestamp(d).date()
    ano = d.year
    if d >= _inicio_ano_epi(ano + 1):
        ano += 1
    elif d < _inicio_ano_epi(ano):
        ano -= 1
    return ano, (d - _inicio_ano_epi(ano)).days // 7 + 1


def fc_horizon_fim_temporada(ultima_data, referencia=None, semana_alvo: int = 26, max_h: int = 110) -> int:
    """Semanas de `ultima_data` ate a SE 26 do fim da temporada de `referencia`.

    Horizonte operacional padrao: "ate a SE 26 do fim da temporada" - muda a
    cada chamada, nao e um numero fixo.
    """
    referencia = referencia if referencia is not None else ultima_data
    ano_ref, sem_ref = epi_ano_semana(referencia)
    ano_alvo = ano_sanitario(ano_ref, sem_ref) + 1
    if ano_ref == ano_alvo and sem_ref == semana_alvo:
        ano_alvo += 1
    d = pd.Timestamp(ultima_data)
    for h in range(1, max_h + 1):
        d = d + pd.Timedelta(weeks=1)
        if epi_ano_semana(d) == (ano_alvo, semana_alvo):
            return h
    raise ValueError(f"Nao encontrei SE {semana_alvo}/{ano_alvo} em {max_h} semanas a partir de {ultima_data}.")


def fc_corte_treino(d_obs: pd.DataFrame, n_recentes: int = FC_SEMANAS_RECENTES) -> pd.DataFrame:
    """Recua `n_recentes` semanas a partir da ULTIMA linha da base (regra unica do treino e do ao vivo)."""
    corte = d_obs["data_ini_se"].iloc[-1] - pd.Timedelta(weeks=int(n_recentes))
    d_fit = d_obs[d_obs["data_ini_se"] <= corte]
    return d_obs if len(d_fit) < FC_MIN_SEMANAS else d_fit.reset_index(drop=True)


# ============================================================================
# 1. FONTE DE DADOS
# ============================================================================
def fc_connect(parquet: Path | None = None):
    return conectar(parquet)


def fc_fingerprint(con, parquet: Path | None = None) -> dict:
    """Impressao digital do Parquet. So bytes + linhas decidem a validade (mtime nao)."""
    p = Path(parquet or cfg.PARQUET_DENGUE)
    return {"arquivo": p.name, "bytes": p.stat().st_size,
            "nrow": int(con.execute("SELECT count(*) FROM dengue").fetchone()[0])}


def fc_fingerprint_ok(a: dict | None, b: dict | None) -> bool:
    return bool(a and b and a.get("bytes") == b.get("bytes") and a.get("nrow") == b.get("nrow"))


def fc_resolve_geo(location_id) -> dict:
    """location_id -> filtro SQL. None/"base" = Pernambuco; 5 digitos = Regiao de
    Saude (regiao_id, ex. 26010); 7 digitos = municipio. Aceita o dados.Geo do app."""
    base = {"nivel": "base", "where": "TRUE", "params": [], "rotulo": cfg.ROTULO_BASE, "id": None}
    if location_id is None:
        return base
    if hasattr(location_id, "nivel"):          # painel.dados.Geo
        g = location_id
        location_id = {"municipio": g.geocodigo, "regiao": g.regiao_id}.get(g.nivel)
        if location_id is None:
            return base
    if isinstance(location_id, str) and location_id.strip().lower() in ("", "0", "base", "pe", "pernambuco"):
        return base
    x = int(location_id)
    if x >= 1_000_000:
        return {"nivel": "municipio", "where": "municipio_geocodigo = ?", "params": [x],
                "rotulo": f"Municipio {x}", "id": x}
    if 10_000 <= x < 100_000:
        return {"nivel": "regiao", "where": "regiao_id = ?", "params": [x],
                "rotulo": f"Regiao de Saude {x}", "id": x}
    raise ValueError(f"location_id fora do padrao (5 ou 7 digitos): {x}")


def fc_cache_key(geo: dict) -> str:
    return {"base": "base", "regiao": f"reg_{geo['id']}", "municipio": f"mun_{geo['id']}"}[geo["nivel"]]


def fc_query_serie(con, geo: dict) -> pd.DataFrame:
    """Serie semanal do recorte: casos/pop somam; clima e media ponderada pela populacao."""
    d = con.execute(f"""
        SELECT data_ini_se,
               max(se)                                  AS se,
               sum(casos)                               AS casos,
               sum(casos_est)                           AS casos_est,
               sum(pop)                                 AS pop,
               sum(tempmed * pop) / nullif(sum(pop), 0) AS tempmed,
               sum(umidmed * pop) / nullif(sum(pop), 0) AS umidmed
        FROM dengue
        WHERE {geo['where']}
        GROUP BY data_ini_se
        ORDER BY data_ini_se
    """, geo["params"]).df()
    d["data_ini_se"] = pd.to_datetime(d["data_ini_se"])
    for c in ("casos", "casos_est", "pop", "tempmed", "umidmed"):
        d[c] = d[c].astype(float)
    return d


# ============================================================================
# 2. PREPARO DA SERIE
# ============================================================================
def fc_prepare(d: pd.DataFrame) -> pd.DataFrame:
    """Grade semanal continua (semana sem registro = 0 caso) + rotulos temporais."""
    d = d.dropna(subset=["data_ini_se"]).sort_values("data_ini_se")
    if d.empty:
        raise ValueError("Serie vazia apos filtro geografico.")
    grade = pd.DataFrame({"data_ini_se": pd.date_range(d["data_ini_se"].min(), d["data_ini_se"].max(), freq="7D")})
    d = grade.merge(d, on="data_ini_se", how="left")
    d["casos"] = d["casos"].fillna(0.0)
    d["casos_est"] = d["casos_est"].fillna(d["casos"])
    d["pop"] = d["pop"].ffill().bfill()
    for c in ("tempmed", "umidmed"):
        d[c] = d[c].interpolate(limit_direction="both")
    faltando = d["se"].isna()
    if faltando.any():
        d.loc[faltando, "se"] = [a * 100 + s for a, s in map(epi_ano_semana, d.loc[faltando, "data_ini_se"])]
    d["se"] = d["se"].astype(int)
    d["ano"] = d["se"] // 100
    d["semana"] = d["se"] % 100
    d["ano_san"] = ano_sanitario(d["ano"], d["semana"])
    d["t"] = np.arange(1, len(d) + 1)
    return d.reset_index(drop=True)


def fc_features(d: pd.DataFrame, lag: int = FC_LAG) -> pd.DataFrame:
    d = d.copy()
    d["casos_l"] = d["casos"].shift(lag)
    d["tempmed_l"] = d["tempmed"].shift(lag)
    d["umidmed_l"] = d["umidmed"].shift(lag)
    return d


# ============================================================================
# 3. MODELOS BASE  (contrato: predict -> dict(mean, lower, upper), len h, >= 0)
# ============================================================================
def _clamp(x) -> np.ndarray:
    return np.maximum(np.nan_to_num(np.asarray(x, dtype=float), nan=0.0), 0.0)


def _lambda_boxcox(y: np.ndarray):
    """Box-Cox so sem zeros (como no forecast do R); None = sem transformacao."""
    if np.min(y) <= 0:
        return None
    lam = float(stats.boxcox_normmax(y, method="mle"))
    return float(np.clip(lam, -1, 2))


def _bc(y, lam):
    return y if lam is None else stats.boxcox(y, lam)


def _ibc(y, lam):
    return np.asarray(y) if lam is None else inv_boxcox(np.asarray(y), lam)


def _escolher_d(y: np.ndarray, max_d: int = 2) -> int:
    from statsmodels.tsa.stattools import kpss
    d, z = 0, np.asarray(y, dtype=float)
    while d < max_d:
        if np.std(z) == 0:
            break
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            p = kpss(z, regression="c", nlags="auto")[1]
        if p >= 0.05:
            break
        z = np.diff(z)
        d += 1
    return d


def _fourier(t: np.ndarray, K: int = FC_FOURIER_K, periodo: int = FC_PERIODO) -> np.ndarray:
    cols = []
    for k in range(1, K + 1):
        cols += [np.sin(2 * np.pi * k * t / periodo), np.cos(2 * np.pi * k * t / periodo)]
    return np.column_stack(cols)


def _ajustar_arima(y, order, exog=None):
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    trend = "c" if order[1] == 0 else "n"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mod = SARIMAX(y, exog=exog, order=order, trend=trend,
                      enforce_stationarity=False, enforce_invertibility=False)
        return mod.fit(disp=False, maxiter=60)


def _aicc(res) -> float:
    k = len(res.params)
    n = res.nobs
    return res.aic + (2 * k * (k + 1)) / max(n - k - 1, 1)


def _auto_arima(y, d: int, exog=None, max_p: int = 3, max_q: int = 3) -> tuple:
    """Busca stepwise (Hyndman-Khandakar simplificada) de (p, q) por AICc."""
    vistos: dict = {}

    def avaliar(p, q):
        if (p, q) not in vistos:
            try:
                vistos[(p, q)] = _aicc(_ajustar_arima(y, (p, d, q), exog))
            except Exception:  # noqa: BLE001 - ordem que nao converge sai da busca
                vistos[(p, q)] = math.inf
        return vistos[(p, q)]

    for pq in [(2, 2), (0, 0), (1, 0), (0, 1)]:
        avaliar(*pq)
    melhor = min(vistos, key=vistos.get)
    while True:
        p, q = melhor
        viz = [(p + a, q + b) for a in (-1, 0, 1) for b in (-1, 0, 1) if (a, b) != (0, 0)]
        viz = [(a, b) for a, b in viz if 0 <= a <= max_p and 0 <= b <= max_q]
        for pq in viz:
            avaliar(*pq)
        novo = min(vistos, key=vistos.get)
        if novo == melhor:
            break
        melhor = novo
    return (melhor[0], d, melhor[1])


# ---- M1: ARIMA nao sazonal + termos de Fourier ------------------------------
def fit_fc_arima(d: pd.DataFrame, spec: dict | None = None) -> dict:
    y = d["casos"].to_numpy(float)
    t = d["t"].to_numpy(float)
    if spec is None:
        lam = _lambda_boxcox(y)
        w = _bc(y, lam)
        order = _auto_arima(w, _escolher_d(w), exog=_fourier(t))
        spec = {"order": list(order), "lam": lam}
    w = _bc(y, spec["lam"])
    res = _ajustar_arima(w, tuple(spec["order"]), exog=_fourier(t))
    return {"spec": spec, "res": res}


def predict_fc_arima(obj: dict, d: pd.DataFrame, h: int) -> dict:
    tf = d["t"].iloc[-1] + np.arange(1, h + 1)
    fr = obj["res"].get_forecast(steps=h, exog=_fourier(tf.astype(float)))
    m = np.asarray(fr.predicted_mean)
    ci = np.asarray(fr.conf_int(alpha=1 - FC_PI_LEVEL / 100))
    lam = obj["spec"]["lam"]
    return {"mean": _clamp(_ibc(m, lam)), "lower": _clamp(_ibc(ci[:, 0], lam)),
            "upper": _clamp(_ibc(ci[:, 1], lam))}


# ---- M1b: SARIMA (p,d,q)(0,1,0)[52] -----------------------------------------
# A diferenca sazonal (lag 52) entra de fato - e o que separa este modelo do
# ARIMA + Fourier. A parte nao sazonal e buscada na serie diferenciada.
def fit_fc_sarima(d: pd.DataFrame, spec: dict | None = None) -> dict:
    y = d["casos"].to_numpy(float)
    if len(y) < 2 * FC_PERIODO + 20:
        raise ValueError("Serie curta demais para o SARIMA.")
    if spec is None:
        lam = _lambda_boxcox(y)
        w = _bc(y, lam)
        z = w[FC_PERIODO:] - w[:-FC_PERIODO]
        order = _auto_arima(z, _escolher_d(z, max_d=1))
        spec = {"order": list(order), "lam": lam}
    w = _bc(y, spec["lam"])
    z = w[FC_PERIODO:] - w[:-FC_PERIODO]
    return {"spec": spec, "res": _ajustar_arima(z, tuple(spec["order"])), "w": w}


def predict_fc_sarima(obj: dict, d: pd.DataFrame, h: int) -> dict:
    fr = obj["res"].get_forecast(steps=h)
    zm = np.asarray(fr.predicted_mean)
    zv = np.asarray(fr.var_pred_mean)
    w = list(obj["w"])
    n = len(w)
    var = [0.0] * n
    for i in range(h):
        j = n + i - FC_PERIODO               # mesma semana, um ano antes
        w.append(zm[i] + w[j])
        var.append(zv[i] + var[j])            # var[j] = 0 quando j ainda e observado
    m = np.array(w[n:])
    sd = np.sqrt(np.array(var[n:]))
    lam = obj["spec"]["lam"]
    return {"mean": _clamp(_ibc(m, lam)), "lower": _clamp(_ibc(m - _Z * sd, lam)),
            "upper": _clamp(_ibc(m + _Z * sd, lam))}


# ---- M2: GAM Binomial Negativa, offset populacional --------------------------
# casos ~ ciclica(SE) + tendencia(t) + clima e casos defasados (4 sem) + offset(log pop)
def _limites_gam(d: pd.DataFrame) -> dict:
    lim = {}
    for c in ("tempmed", "umidmed"):
        v = d[c].dropna()
        lim[c] = (float(v.min()) - 1.0, float(v.max()) + 1.0)
    lim["casos"] = (-1.0, float(d["casos"].max()) * 1.5 + 1.0)
    lim["t"] = (0.0, float(d["t"].max()) + 1.0)
    return lim


def fit_fc_gam(d: pd.DataFrame, spec: dict | None = None, lag: int = FC_LAG) -> dict:
    import patsy
    from statsmodels.discrete.discrete_model import NegativeBinomial
    df = fc_features(d, lag)
    df = df.dropna(subset=["casos", "pop", "semana", "t", "tempmed_l", "umidmed_l", "casos_l"])
    df = df[df["pop"] > 0]
    if len(df) < 104:
        raise ValueError(f"Serie curta demais para o GAM: {len(df)} semanas.")
    lim = _limites_gam(d)
    n_unicos = df["casos_l"].nunique()
    k_c = int(min(5, max(3, n_unicos - 1)))
    formula = (
        "casos ~ cc(semana, df=8, lower_bound=0.5, upper_bound=53.5, constraints='center')"
        f" + cr(t, df=5, lower_bound={lim['t'][0]}, upper_bound={lim['t'][1]}, constraints='center')"
        f" + cr(tempmed_l, df=3, lower_bound={lim['tempmed'][0]}, upper_bound={lim['tempmed'][1]}, constraints='center')"
        f" + cr(umidmed_l, df=3, lower_bound={lim['umidmed'][0]}, upper_bound={lim['umidmed'][1]}, constraints='center')"
        f" + cr(casos_l, df={k_c}, lower_bound={lim['casos'][0]}, upper_bound={lim['casos'][1]}, constraints='center')"
    )
    y, X = patsy.dmatrices(formula, df, return_type="dataframe")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mod = NegativeBinomial(y, X, offset=np.log(df["pop"].to_numpy()))
        try:
            res = mod.fit(disp=0, maxiter=200)
        except Exception:  # noqa: BLE001
            res = mod.fit(disp=0, maxiter=500, method="bfgs")
    return {"res": res, "design_info": X.design_info, "lag": lag, "lim": lim, "spec": None}


def predict_fc_gam(obj: dict, d: pd.DataFrame, h: int, nsim: int = FC_MC_SIM) -> dict:
    import patsy
    res, lag, lim = obj["res"], obj["lag"], obj["lim"]
    n = len(d)
    li = np.clip(n + np.arange(1, h + 1) - lag, 1, n) - 1         # indice do valor defasado
    fdatas = d["data_ini_se"].iloc[-1] + pd.to_timedelta(7 * np.arange(1, h + 1), unit="D")

    def dentro(v, k):
        return np.clip(np.asarray(v, float), lim[k][0] + 1e-6, lim[k][1] - 1e-6)

    nd = pd.DataFrame({
        "semana": [epi_ano_semana(x)[1] for x in fdatas],
        "t": float(d["t"].iloc[-1]),                                # tendencia congelada
        "tempmed_l": dentro(d["tempmed"].to_numpy()[li], "tempmed"),
        "umidmed_l": dentro(d["umidmed"].to_numpy()[li], "umidmed"),
        "casos_l": dentro(d["casos"].to_numpy()[li], "casos"),
    })
    X = np.asarray(patsy.build_design_matrices([obj["design_info"]], nd)[0])
    beta = np.asarray(res.params)[:-1]
    cov = np.asarray(res.cov_params())[:-1, :-1]
    alpha = float(np.asarray(res.params)[-1])
    eta = X @ beta + np.log(float(d["pop"].iloc[-1]))
    se = np.sqrt(np.maximum(np.einsum("ij,jk,ik->i", X, cov, X), 0))
    rng = np.random.default_rng(20260903)
    eta_sim = rng.normal(eta, se, size=(nsim, h))
    mu = np.exp(np.clip(eta_sim, -30, 30))
    size = 1.0 / max(alpha, 1e-8)
    sim = rng.negative_binomial(size, size / (size + mu))
    a = (1 - FC_PI_LEVEL / 100) / 2
    return {"mean": _clamp(sim.mean(axis=0)), "lower": _clamp(np.quantile(sim, a, axis=0)),
            "upper": _clamp(np.quantile(sim, 1 - a, axis=0))}


# ---- M3: STL + ETS ------------------------------------------------------------
# STL "periodica" (sazonalidade fixa, robusta) + ETS na serie dessazonalizada,
# escolhido por AICc entre nivel, tendencia e tendencia amortecida.
def fit_fc_stlets(d: pd.DataFrame, spec: dict | None = None) -> dict:
    return {"spec": None}


def predict_fc_stlets(obj: dict, d: pd.DataFrame, h: int) -> dict:
    from statsmodels.tsa.exponential_smoothing.ets import ETSModel
    from statsmodels.tsa.seasonal import STL
    y = d["casos"].to_numpy(float)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        stl = STL(y, period=FC_PERIODO, seasonal=2 * (len(y) // 2) + 1, robust=True).fit()
        ajust = pd.Series(y - stl.seasonal)
        melhor = None
        for trend, damped in ((None, False), ("add", False), ("add", True)):
            try:
                r = ETSModel(ajust, error="add", trend=trend, damped_trend=damped).fit(disp=False)
            except Exception:  # noqa: BLE001
                continue
            if melhor is None or r.aicc < melhor.aicc:
                melhor = r
        if melhor is None:
            raise RuntimeError("ETS nao convergiu.")
        pr = melhor.get_prediction(start=len(y), end=len(y) + h - 1).summary_frame(alpha=1 - FC_PI_LEVEL / 100)
    saz = np.array([stl.seasonal[len(y) - FC_PERIODO + (i % FC_PERIODO)] for i in range(h)])
    return {"mean": _clamp(pr["mean"].to_numpy() + saz), "lower": _clamp(pr["pi_lower"].to_numpy() + saz),
            "upper": _clamp(pr["pi_upper"].to_numpy() + saz)}


# ---- M4: Random Forest (scikit-learn) ----------------------------------------
def _features_rf(df: pd.DataFrame) -> np.ndarray:
    return df[["semana", "t", "pop", "tempmed_l", "umidmed_l", "casos_l", "inc_l"]].to_numpy(float)


def fit_fc_rf(d: pd.DataFrame, spec: dict | None = None, lag: int = FC_LAG) -> dict:
    df = fc_features(d, lag).dropna(subset=["casos", "pop", "semana", "t", "tempmed_l", "umidmed_l", "casos_l"])
    df = df[df["pop"] > 0].copy()
    if len(df) < 104:
        raise ValueError(f"Serie curta demais para o RF: {len(df)} semanas.")
    df["inc_l"] = df["casos_l"] / df["pop"] * 1e5
    rf = RandomForestRegressor(n_estimators=300, min_samples_leaf=5, random_state=20260903, n_jobs=-1)
    rf.fit(_features_rf(df), df["casos"].to_numpy(float))
    return {"res": rf, "lag": lag, "spec": None}


def predict_fc_rf(obj: dict, d: pd.DataFrame, h: int) -> dict:
    rf, lag, n = obj["res"], obj["lag"], len(d)
    li = np.clip(n + np.arange(1, h + 1) - lag, 1, n) - 1
    fdatas = d["data_ini_se"].iloc[-1] + pd.to_timedelta(7 * np.arange(1, h + 1), unit="D")
    nd = pd.DataFrame({
        "semana": [epi_ano_semana(x)[1] for x in fdatas],
        "t": d["t"].iloc[-1] + np.arange(1, h + 1),
        "pop": float(d["pop"].iloc[-1]),
        "tempmed_l": d["tempmed"].to_numpy()[li],
        "umidmed_l": d["umidmed"].to_numpy()[li],
        "casos_l": d["casos"].to_numpy()[li],
    })
    nd["inc_l"] = nd["casos_l"] / nd["pop"] * 1e5
    X = _features_rf(nd)
    arvores = np.stack([e.predict(X) for e in rf.estimators_])
    a = (1 - FC_PI_LEVEL / 100) / 2
    return {"mean": _clamp(rf.predict(X)), "lower": _clamp(np.quantile(arvores, a, axis=0)),
            "upper": _clamp(np.quantile(arvores, 1 - a, axis=0))}


# ============================================================================
# 3b. REGISTRO DE MODELOS E CLASSES
# ============================================================================
# Para adicionar um modelo: escreva fit_*/predict_* e registre aqui com a
# `classe`. Backtest, ensembles, saida e checkboxes do painel se ajustam sozinhos.
# `cacheavel`: o treino offline guarda a ESPECIFICACAO (ordem do ARIMA, lambda)
# por recorte - a busca e a parte cara; reajustar uma ordem conhecida e rapido.
FC_CLASSES = {
    "estatistico": "Estatísticos",
    "ml": "Machine Learning",
    "mecanicista": "Mecanicistas",      # sem modelo ainda - estrutura pronta
}

FC_MODELOS = {
    "arima": {"rotulo": "ARIMA + Fourier", "classe": "estatistico",
              "fit": fit_fc_arima, "predict": predict_fc_arima, "cacheavel": True},
    "sarima": {"rotulo": "SARIMA", "classe": "estatistico",
               "fit": fit_fc_sarima, "predict": predict_fc_sarima, "cacheavel": True},
    "gam": {"rotulo": "GAM (Bin. Negativa)", "classe": "estatistico",
            "fit": fit_fc_gam, "predict": predict_fc_gam, "cacheavel": False},
    "stlets": {"rotulo": "STL + ETS", "classe": "estatistico",
               "fit": fit_fc_stlets, "predict": predict_fc_stlets, "cacheavel": False},
}
if FC_TEM_SKLEARN:
    FC_MODELOS["rf"] = {"rotulo": "Random Forest", "classe": "ml",
                        "fit": fit_fc_rf, "predict": predict_fc_rf, "cacheavel": False}


def fc_modelos_da_classe(cl: str) -> list[str]:
    return [k for k, m in FC_MODELOS.items() if m["classe"] == cl]


def fc_classes_com_modelo() -> list[str]:
    return [cl for cl in FC_CLASSES if fc_modelos_da_classe(cl)]


def fc_rotulo_modelo(k: str) -> str:
    return FC_MODELOS.get(k, {}).get("rotulo", k)


def fc_rotulo_classe(cl: str) -> str:
    return FC_CLASSES.get(cl, cl)


# ============================================================================
# 4. ENSEMBLE (media ponderada pelo inverso do RMSE, em dois niveis)
# ============================================================================
def fc_pesos_inv_rmse(rmse: dict) -> dict:
    inv = {k: 1 / v for k, v in rmse.items() if v and np.isfinite(v)}
    tot = sum(inv.values())
    return {k: v / tot for k, v in inv.items()}


def fc_ensemble(preds: dict, pesos: dict | None) -> dict:
    """Media ponderada de mean/lower/upper, renormalizando sobre os componentes disponiveis."""
    w = {k: (pesos or {}).get(k, 0.0) for k in preds}
    if sum(w.values()) <= 0:
        w = {k: 1.0 for k in preds}
    tot = sum(w.values())
    w = {k: v / tot for k, v in w.items()}
    comb = {campo: sum(w[k] * np.asarray(p[campo]) for k, p in preds.items())
            for campo in ("mean", "lower", "upper")}
    comb["pesos"] = w
    return comb


def fc_prever_todos(d_obs: pd.DataFrame, pesos: dict, h: int, cache: dict | None = None,
                    log=print) -> dict:
    """Ajusta todos os modelos (usando as specs em cache quando houver) e monta os ensembles."""
    P = {}
    for k, spec in FC_MODELOS.items():
        try:
            fit = spec["fit"](d_obs, (cache or {}).get(k))
        except Exception as e:  # noqa: BLE001
            log(f"[forecast] {k} (ajuste) falhou: {e}")
            continue
        try:
            P[k] = spec["predict"](fit, d_obs, h)
        except Exception as e:  # noqa: BLE001
            log(f"[forecast] {k} (previsao) falhou: {e}")
    if not P:
        raise RuntimeError("Todos os modelos falharam para este recorte.")
    ensc = {}
    for cl in fc_classes_com_modelo():
        mk = {k: P[k] for k in fc_modelos_da_classe(cl) if k in P}
        if mk:
            ensc[cl] = fc_ensemble(mk, pesos.get("modelo"))
    gl = fc_ensemble(ensc, pesos.get("classe"))
    return {"modelos": P, "classes": ensc, "global": gl}


# ============================================================================
# 5. METRICAS
# ============================================================================
def fc_metricas(obs, pred, escala: float = np.nan, piso_mape: float = 5) -> dict:
    obs, pred = np.asarray(obs, float), np.asarray(pred, float)
    ok = np.isfinite(obs) & np.isfinite(pred)
    obs, pred = obs[ok], pred[ok]
    e = obs - pred
    usa = obs >= piso_mape
    return {
        "RMSE": float(np.sqrt(np.mean(e ** 2))),
        "MAE": float(np.mean(np.abs(e))),
        "MAPE": float(np.mean(np.abs(e[usa] / obs[usa])) * 100) if usa.any() else np.nan,
        "sMAPE": float(np.mean(2 * np.abs(e) / np.maximum(np.abs(obs) + np.abs(pred), 1e-12)) * 100),
        "MASE": float(np.mean(np.abs(e)) / escala) if np.isfinite(escala) and escala > 0 else np.nan,
        "n": int(len(obs)),
        "n_mape": int(usa.sum()),
    }


def fc_escala_sazonal(y, periodo: int = FC_PERIODO) -> float:
    y = np.asarray(y, float)
    return float(np.nanmean(np.abs(y[periodo:] - y[:-periodo])))


# ============================================================================
# 6. SAIDA (ano sanitario + dobra da SE 53)
# ============================================================================
def fc_dobrar_semana_53(d: pd.DataFrame, colunas: list[str]) -> pd.DataFrame:
    """Metade da SE 53 vai para a SE 52, metade para a SE 1 (mesma temporada e mesmo Tipo)."""
    com53 = d[d["Semana_Epi"] == 53]
    if com53.empty:
        return d
    d = d[d["Semana_Epi"] != 53].copy()
    manter = []
    for _, l in com53.iterrows():
        mesmo = (d["Ano_Sanitario"] == l["Ano_Sanitario"]) & (d["Tipo"] == l["Tipo"])
        i52 = mesmo & (d["Semana_Epi"] == 52)
        i1 = mesmo & (d["Semana_Epi"] == 1)
        if not i52.any() and not i1.any():
            manter.append(l)
            continue
        for col in colunas:
            m = l[col] / 2
            if pd.isna(m):
                continue
            for idx in (i52, i1):
                if idx.any():
                    d.loc[idx, col] = d.loc[idx, col].fillna(0) + m
    if manter:
        d = pd.concat([d, pd.DataFrame(manter)], ignore_index=True)
    return d


# ------------------------------------------------------------ artefatos -----
def ler_json(caminho: Path):
    if not caminho.exists():
        return None
    return json.loads(caminho.read_text(encoding="utf-8"))


def gravar_json(obj, caminho: Path) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    tmp.replace(caminho)
