# ==============================================================================
# Dengue PE | forecasting/get_forecast.py
#
# Funcao consumida pelo painel: le a serie do recorte via DuckDB, aplica o
# ensemble treinado (forecasting/models/) e devolve observado + previsto,
# respeitando o ano sanitario. Porto do get_forecast.R do RIDE.
#
#   from forecasting.get_forecast import get_dengue_forecast
#   fc = get_dengue_forecast(None)          # Pernambuco
#   fc = get_dengue_forecast(26010)         # I GERES (regiao_id)
#   fc = get_dengue_forecast(2611606)       # Recife
# ==============================================================================

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from . import utils_forecast as U

_MEMO: dict = {}


def _ler_memo(caminho: Path):
    """Le um JSON de models/ uma vez por processo (chave = caminho + tamanho + mtime)."""
    if not caminho.exists():
        return None
    st = caminho.stat()
    chave = (str(caminho), st.st_size, st.st_mtime)
    if chave not in _MEMO:
        _MEMO[chave] = U.ler_json(caminho)
    return _MEMO[chave]


def forecast_disponivel(models_dir: Path | None = None) -> bool:
    return ((models_dir or U.MODELS_DIR) / "ensemble_config.json").exists()


def get_dengue_forecast(location_id=None, data_atual: date | None = None, horizon: int | None = None,
                        semanas_recentes: int | None = None, n_semanas_hist: int | None = 104,
                        models_dir: Path | None = None, con=None, log=print) -> pd.DataFrame:
    """Previsao de casos por recorte (ensemble em dois niveis).

    Colunas: Data_SE, SE, Semana_Epi, Ano_Sanitario, Temporada, Tipo
    ("observado"/"previsto"), Pop, Casos_Observados, Casos_Nowcast,
    Casos_Previstos_Ensemble / Limite_Inferior / Limite_Superior (ensemble
    global, IP 80%), Prev_<modelo>, Prev_ens_<classe>, Lim_inf_<classe>,
    Lim_sup_<classe>. Metadados em `df.attrs`.

    O corte de treino recua `semanas_recentes` (4) semanas a partir da ULTIMA
    linha da base (nao de "hoje"): essas semanas saem com dois registros,
    observado e previsto. O horizonte padrao vai ate a SE 26 do fim da
    temporada da ultima semana disponivel.
    """
    models_dir = models_dir or U.MODELS_DIR
    cfg_json = _ler_memo(models_dir / "ensemble_config.json")
    if cfg_json is None:
        raise RuntimeError("Modelos nao treinados: rode `python forecasting/train_models.py`.")
    pesos = cfg_json["weights"]

    proprio = con is None
    if proprio:
        con = U.fc_connect()
    try:
        geo = U.fc_resolve_geo(location_id)
        d_all = U.fc_prepare(U.fc_query_serie(con, geo))
        fp_ok = U.fc_fingerprint_ok(cfg_json.get("parquet_fingerprint"), U.fc_fingerprint(con))
    finally:
        if proprio:
            con.close()

    data_atual = pd.Timestamp(data_atual or date.today())
    d_obs = d_all[d_all["data_ini_se"] <= data_atual].reset_index(drop=True)
    if len(d_obs) < U.FC_MIN_SEMANAS:
        raise ValueError(f"Serie observada curta demais para previsao confiavel ({len(d_obs)} semanas).")

    n_recentes = int(semanas_recentes if semanas_recentes is not None else U.FC_SEMANAS_RECENTES)
    ultima_disponivel = d_obs["data_ini_se"].iloc[-1]
    d_fit = U.fc_corte_treino(d_obs, n_recentes)
    h = int(horizon or U.fc_horizon_fim_temporada(d_fit["data_ini_se"].iloc[-1], referencia=ultima_disponivel))

    # Especificacoes pre-escolhidas no treino (ordem do ARIMA, lambda): valem se
    # o Parquet nao mudou de conteudo e o corte de treino tem o mesmo tamanho.
    cache = None
    if fp_ok:
        specs = _ler_memo(models_dir / "specs_cache.json") or {}
        entry = specs.get(U.fc_cache_key(geo))
        if entry and entry.get("n_treino") == len(d_fit):
            cache = entry.get("specs")

    res = U.fc_prever_todos(d_fit, pesos, h, cache=cache, log=log)
    gl = res["global"]

    def vec(p, campo):
        return np.full(h, np.nan) if p is None else np.round(np.asarray(p[campo], float), 1)

    fdatas = d_fit["data_ini_se"].iloc[-1] + pd.to_timedelta(7 * np.arange(1, h + 1), unit="D")
    fse = [a * 100 + s for a, s in map(U.epi_ano_semana, fdatas)]

    hist = pd.DataFrame({
        "Data_SE": d_obs["data_ini_se"], "SE": d_obs["se"], "Tipo": "observado", "Pop": d_obs["pop"],
        "Casos_Observados": d_obs["casos"].round(1), "Casos_Nowcast": d_obs["casos_est"].round(1),
        "Casos_Previstos_Ensemble": np.nan, "Limite_Inferior": np.nan, "Limite_Superior": np.nan,
    })
    fut = pd.DataFrame({
        "Data_SE": fdatas, "SE": fse, "Tipo": "previsto", "Pop": float(d_fit["pop"].iloc[-1]),
        "Casos_Observados": np.nan, "Casos_Nowcast": np.nan,
        "Casos_Previstos_Ensemble": vec(gl, "mean"), "Limite_Inferior": vec(gl, "lower"),
        "Limite_Superior": vec(gl, "upper"),
    })
    for k in U.FC_MODELOS:
        hist[f"Prev_{k}"] = np.nan
        fut[f"Prev_{k}"] = vec(res["modelos"].get(k), "mean")
    for cl in U.fc_classes_com_modelo():
        ec = res["classes"].get(cl)
        for pref, campo in (("Prev_ens_", "mean"), ("Lim_inf_", "lower"), ("Lim_sup_", "upper")):
            hist[f"{pref}{cl}"] = np.nan
            fut[f"{pref}{cl}"] = vec(ec, campo)

    if n_semanas_hist:
        hist = hist.tail(int(n_semanas_hist))
    out = pd.concat([hist, fut], ignore_index=True)
    out["Semana_Epi"] = out["SE"] % 100
    out["Ano_Sanitario"] = U.ano_sanitario(out["SE"] // 100, out["Semana_Epi"])
    out["Temporada"] = out["Ano_Sanitario"].map(U.rotulo_ano_sanitario)

    num = ["Casos_Observados", "Casos_Nowcast", "Casos_Previstos_Ensemble", "Limite_Inferior",
           "Limite_Superior"] + [c for c in out.columns if c.startswith(("Prev_", "Lim_inf_", "Lim_sup_"))]
    out = U.fc_dobrar_semana_53(out, num)
    ini = ["Data_SE", "SE", "Semana_Epi", "Ano_Sanitario", "Temporada", "Tipo", "Pop",
           "Casos_Observados", "Casos_Nowcast", "Casos_Previstos_Ensemble", "Limite_Inferior",
           "Limite_Superior"]
    out = out.sort_values(["Data_SE", "Tipo"]).reset_index(drop=True)
    out = out[ini + [c for c in out.columns if c not in ini]]

    out.attrs.update({
        "location": geo["rotulo"], "pesos": pesos,
        "pesos_efetivos": {"global": gl["pesos"], "classes": {k: v["pesos"] for k, v in res["classes"].items()}},
        "data_atual": str(data_atual.date()), "horizon": h, "modelo_cache": bool(cache),
    })
    return out
