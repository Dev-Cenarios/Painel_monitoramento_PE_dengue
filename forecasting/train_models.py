# ==============================================================================
# Dengue PE | forecasting/train_models.py
#
# Pipeline OFFLINE de treino e avaliacao da previsao (padrao Mosqlimate). Nao
# e chamado pelo painel - roda a mao, depois de cada atualizacao do Parquet:
#
#     python forecasting/train_models.py
#     python forecasting/train_models.py --origens=24 --min_treino=156
#
# Etapas:
#   1. Serie agregada de Pernambuco (= serie_semanal do painel).
#   2. Backtest de origem rolante de todos os modelos de FC_MODELOS.
#   3. Metricas por classe/modelo/horizonte -> forecasting/model_metrics.csv.
#   4. Pesos: inverso do RMSE dentro da classe (modelo) e entre classes (global).
#   5. Pre-escolhe as especificacoes dos modelos `cacheavel` (ordem do ARIMA,
#      lambda do Box-Cox) para PE, cada Regiao de Saude e cada municipio, no
#      mesmo corte de treino que a previsao ao vivo usara -> specs_cache.json.
#   6. Grava ensemble_config.json e atualiza a tabela do README-FORECAST.md.
#
# Os pesos vem SO de Pernambuco agregado (serie mais longa e estavel); a
# previsao ao vivo reaproveita esses pesos e reajusta os modelos ao recorte.
# ==============================================================================

from __future__ import annotations

import argparse
import sys
import time
import warnings
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
warnings.filterwarnings("ignore")

from forecasting import utils_forecast as U  # noqa: E402

AQUI = Path(__file__).resolve().parent


def backtest(d: pd.DataFrame, h: int, n_origens: int, min_treino: int) -> pd.DataFrame:
    """Para cada origem o: treina em 1..o e preve o+1..o+h, para todos os modelos."""
    n = len(d)
    primeira = max(min_treino, n - h - n_origens + 1)
    origens = list(range(primeira, n - h + 1))
    if len(origens) < 3:
        raise SystemExit("Serie curta demais para o backtest.")
    linhas = []
    for i, o in enumerate(origens, start=1):
        tr = d.iloc[:o].reset_index(drop=True)
        te = d.iloc[o:o + h]
        ok = []
        for k, m in U.FC_MODELOS.items():
            try:
                p = m["predict"](m["fit"](tr), tr, h)
            except Exception:  # noqa: BLE001 - modelo que falha nesta origem fica de fora
                continue
            ok.append(k)
            linhas.append(pd.DataFrame({
                "origem": o, "h": np.arange(1, h + 1), "modelo": k, "classe": m["classe"],
                "obs": te["casos"].to_numpy(), "pred": p["mean"], "lower": p["lower"], "upper": p["upper"],
            }))
        print(f"[backtest] origem {i}/{len(origens)} (n_treino = {o})  ok: {', '.join(ok)}", flush=True)
    return pd.concat(linhas, ignore_index=True)


def _bloco(df: pd.DataFrame, modelo: str, classe: str, escala: float) -> list[dict]:
    out = [{"modelo": modelo, "classe": classe, "horizonte": "todos",
            **U.fc_metricas(df["obs"], df["pred"], escala)}]
    for hh in sorted(df["h"].unique()):
        x = df[df["h"] == hh]
        out.append({"modelo": modelo, "classe": classe, "horizonte": f"h{hh}",
                    **U.fc_metricas(x["obs"], x["pred"], escala)})
    return out


def _rmse(df: pd.DataFrame) -> float:
    return float(np.sqrt(np.mean((df["obs"] - df["pred"]) ** 2)))


def consolidar(bt: pd.DataFrame, d: pd.DataFrame) -> dict:
    """Metricas e pesos em tres niveis: modelo, ensemble de classe, ensemble global."""
    escala = U.fc_escala_sazonal(d["casos"])
    modelos = [k for k in U.FC_MODELOS if k in set(bt["modelo"])]
    linhas = []
    rmse_mod = {}
    for m in modelos:
        b = bt[bt["modelo"] == m]
        linhas += _bloco(b, m, U.FC_MODELOS[m]["classe"], escala)
        rmse_mod[m] = _rmse(b)

    pesos_mod = {}
    for cl in U.FC_CLASSES:
        mk = [m for m in modelos if U.FC_MODELOS[m]["classe"] == cl]
        if mk:
            pesos_mod.update(U.fc_pesos_inv_rmse({m: rmse_mod[m] for m in mk}))

    def combinar(df, chave_peso, grupos):
        df = df.assign(w=df[chave_peso])
        g = df.groupby(grupos)
        return pd.DataFrame({
            c: g.apply(lambda x, c=c: np.sum(x["w"] * x[c]) / np.sum(x["w"]), include_groups=False)
            for c in ("pred", "lower", "upper")
        }).reset_index()

    bt = bt.assign(wm=bt["modelo"].map(pesos_mod))
    ens_cl = combinar(bt, "wm", ["origem", "h", "classe", "obs"])
    classes = [cl for cl in U.FC_CLASSES if cl in set(ens_cl["classe"])]
    rmse_cl = {}
    for cl in classes:
        x = ens_cl[ens_cl["classe"] == cl]
        linhas += _bloco(x, f"ensemble_{cl}", cl, escala)
        rmse_cl[cl] = _rmse(x)
    pesos_cl = U.fc_pesos_inv_rmse(rmse_cl)

    ens_cl = ens_cl.assign(wc=ens_cl["classe"].map(pesos_cl))
    ens_gl = combinar(ens_cl, "wc", ["origem", "h", "obs"])
    linhas += _bloco(ens_gl, "ensemble_global", "global", escala)

    met = pd.DataFrame(linhas)
    met["peso"] = np.nan
    todos = met["horizonte"] == "todos"
    met.loc[todos & met["modelo"].isin(pesos_mod), "peso"] = met["modelo"].map(pesos_mod)
    cl_mask = todos & met["modelo"].str.startswith("ensemble_") & (met["modelo"] != "ensemble_global")
    met.loc[cl_mask, "peso"] = met.loc[cl_mask, "modelo"].str.replace("ensemble_", "").map(pesos_cl)
    met["peso"] = met["peso"].round(4)
    return {"metrics": met, "pesos": {"modelo": pesos_mod, "classe": pesos_cl},
            "rmse": {"modelo": rmse_mod, "classe": rmse_cl}, "escala_sazonal": escala}


@lru_cache(maxsize=1)
def _base_processo() -> pd.DataFrame:
    """Base lida uma vez por processo filho (cada um atende varios recortes)."""
    return U.fc_carregar()


def specs_do_recorte(loc) -> tuple[str, dict | None, str]:
    """Escolhe as specs dos modelos `cacheavel` no corte de treino do recorte (roda num processo filho)."""
    warnings.filterwarnings("ignore")
    geo = U.fc_resolve_geo(loc)
    chave = U.fc_cache_key(geo)
    d = U.fc_prepare(U.fc_query_serie(_base_processo(), geo))
    if len(d) < U.FC_MIN_SEMANAS:
        return chave, None, f"serie curta ({len(d)} semanas) - reajuste ao vivo"
    d_fit = U.fc_corte_treino(d)
    specs = {}
    for k, m in U.FC_MODELOS.items():
        if not m["cacheavel"]:
            continue
        try:
            specs[k] = m["fit"](d_fit)["spec"]
        except Exception as e:  # noqa: BLE001
            print(f"   [{chave}] {k}: {e}", flush=True)
    return chave, {"n_treino": len(d_fit), "specs": specs}, ", ".join(f"{k}{tuple(v['order'])}" for k, v in specs.items())


def atualizar_readme(met: pd.DataFrame, n_origens: int, min_treino: int) -> None:
    readme = AQUI / "README-FORECAST.md"
    if not readme.exists():
        return
    tbl = met[met["horizonte"] == "todos"].copy()
    ordem = list(U.FC_MODELOS) + [f"ensemble_{c}" for c in U.FC_CLASSES] + ["ensemble_global"]
    tbl["_o"] = tbl["modelo"].map({k: i for i, k in enumerate(ordem)})
    tbl = tbl.sort_values("_o")

    def rot(k):
        if k == "ensemble_global":
            return "**Ensemble global**"
        if k.startswith("ensemble_"):
            return f"*Ensemble {U.fc_rotulo_classe(k.removeprefix('ensemble_'))}*"
        return U.fc_rotulo_modelo(k)

    def f(x, casas=2):
        return "--" if pd.isna(x) else f"{x:.{casas}f}"

    linhas = [
        f"_Backtest de origem rolante: {n_origens} origens, horizonte {U.FC_HORIZON_DEFAULT} semanas, "
        f"janela expansível (>= {min_treino} semanas de treino). Escopo: Pernambuco. "
        f"Atualizado em {datetime.now():%Y-%m-%d %H:%M}._",
        "",
        "| Classe | Modelo | RMSE | MAE | MAPE (%) | sMAPE (%) | MASE | Peso |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ] + [
        f"| {r.classe} | {rot(r.modelo)} | {f(r.RMSE)} | {f(r.MAE)} | {f(r.MAPE)} | {f(r.sMAPE)} | "
        f"{f(r.MASE)} | {f(r.peso, 3)} |"
        for r in tbl.itertuples()
    ] + [
        "",
        "> Peso de um modelo = seu peso DENTRO da classe. Peso de um ensemble de classe = seu peso "
        "no ensemble global. MAPE só em semanas com casos observados >= 5. MASE < 1 = melhor que o "
        "ingênuo sazonal (lag 52).",
    ]
    txt = readme.read_text(encoding="utf-8")
    ini, fim = "<!-- METRICS:START -->", "<!-- METRICS:END -->"
    if ini in txt and fim in txt:
        a, b = txt.index(ini) + len(ini), txt.index(fim)
        readme.write_text(txt[:a] + "\n\n" + "\n".join(linhas) + "\n\n" + txt[b:], encoding="utf-8")
        print("   Tabela de metricas atualizada no README.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--origens", type=int, default=24)
    ap.add_argument("--min_treino", type=int, default=156)
    ap.add_argument("--processos", type=int, default=4, help="processos paralelos no pre-aquecimento")
    args = ap.parse_args()
    t0 = time.time()

    print("\n== 1. Base historica (Pernambuco agregado) ==")
    base = U.fc_carregar()
    fp = U.fc_fingerprint(base)
    geo = U.fc_resolve_geo(None)
    d = U.fc_prepare(U.fc_query_serie(base, geo))
    recortes = (base.loc[base["municipio_geocodigo"].notna(), ["regiao_id", "municipio_geocodigo"]]
                .drop_duplicates())
    print(f"   {len(d)} semanas | {d['data_ini_se'].min():%Y-%m-%d} a {d['data_ini_se'].max():%Y-%m-%d} "
          f"| {int(d['casos'].sum())} casos")

    print(f"\n== 2. Backtest de origem rolante ({args.origens} origens, h = {U.FC_HORIZON_DEFAULT}) ==")
    bt = backtest(d, U.FC_HORIZON_DEFAULT, args.origens, args.min_treino)

    print("\n== 3. Metricas e pesos do ensemble ==")
    cons = consolidar(bt, d)
    met = cons["metrics"]
    met.insert(0, "escopo", geo["rotulo"])
    for c in ("RMSE", "MAE", "MAPE", "sMAPE", "MASE"):
        met[c] = met[c].round(3)
    met.to_csv(AQUI / "model_metrics.csv", index=False, encoding="utf-8")
    print(met[met["horizonte"] == "todos"][["classe", "modelo", "RMSE", "MAE", "MAPE", "MASE", "peso"]]
          .to_string(index=False))

    print("\n== 4. Pre-escolha das especificacoes por recorte (PE, regioes, municipios) ==")
    locs = ([None] + sorted(int(r) for r in recortes["regiao_id"].dropna().unique())
            + sorted(int(m) for m in recortes["municipio_geocodigo"].unique()))
    specs = {}
    t1 = time.time()
    with ProcessPoolExecutor(max_workers=max(1, args.processos)) as ex:
        for i, (chave, entry, resumo) in enumerate(ex.map(specs_do_recorte, locs), start=1):
            if entry:
                specs[chave] = entry
            print(f"   [{i}/{len(locs)}] {chave}: {resumo}", flush=True)
    print(f"   {len(specs)} recorte(s) em {(time.time() - t1) / 60:.1f} min.")
    U.gravar_json(specs, U.MODELS_DIR / "specs_cache.json")

    cfg_json = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "parquet_fingerprint": fp,
        "training_scope": geo["rotulo"],
        "series_range": [f"{d['data_ini_se'].min():%Y-%m-%d}", f"{d['data_ini_se'].max():%Y-%m-%d}"],
        "horizon": U.FC_HORIZON_DEFAULT, "fourier_K": U.FC_FOURIER_K, "lag_steps": U.FC_LAG,
        "pi_level": U.FC_PI_LEVEL,
        "classes": U.FC_CLASSES,
        "modelos": {k: {"rotulo": m["rotulo"], "classe": m["classe"]} for k, m in U.FC_MODELOS.items()},
        "weights": cons["pesos"], "rmse": cons["rmse"],
        "seasonal_scale_mae": cons["escala_sazonal"],
        "backtest": {"n_origens": args.origens, "min_treino": args.min_treino},
    }
    U.gravar_json(cfg_json, U.MODELS_DIR / "ensemble_config.json")
    print(f"   -> {U.MODELS_DIR / 'ensemble_config.json'}")

    print("\n== 5. Atualizacao do README-FORECAST.md ==")
    atualizar_readme(met, args.origens, args.min_treino)
    print(f"\n== Concluido em {(time.time() - t0) / 60:.1f} min ==")


if __name__ == "__main__":
    main()
