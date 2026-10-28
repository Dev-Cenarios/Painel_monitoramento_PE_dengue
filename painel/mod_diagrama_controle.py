# ==============================================================================
# Dengue PE | Modulo: Diagrama de Controle (+ Previsao por classe de modelo)
#
# Incidencia semanal da temporada em analise sobre o canal endemico das 5
# temporadas anteriores. Eixo SE 27 -> SE 26 (ano sanitario), com uma linha
# vertical na virada; o rotulo traz a SE em cima e o ano embaixo.
#
# REGRA do canal (estrita): EXCLUSIVAMENTE quartis - faixa inferior Q1..Q2,
# faixa superior Q2..Q3, linha central Q2. Cada semana precisa de >= 3
# temporadas de referencia.
#
# CAMADA DE PREVISAO (forecasting/): um ensemble por classe de modelo e um
# ensemble global. Checkboxes para cada ensemble e modelo, com RMSE/MAPE do
# backtest; "Ensemble global" marcado por padrao. Cada linha e continuacao
# tracejada da curva notificada, ancorada no ultimo ponto notificado. Faixa =
# IP 80% do ensemble global, so nas 4 primeiras semanas previstas (alem disso
# o IP nao e calibrado e dominaria o grafico).
#
# Incidencia = sum(casos)/sum(pop)*1e5 na semana. NUNCA media de razoes.
# ==============================================================================

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import pickle
import time
from datetime import date

import numpy as np
import pandas as pd
from htmltools import tags
from shiny import module, reactive, render, req, ui

from . import config as cfg
from .config import (FONTE_GRAFICO, NIVEIS_SEMANA_SANITARIA, PALETA_DENGUE, SEMANA_CORTE_SANITARIA,
                     SERIES_CENARIOS, TONS_AZUL, TONS_DENGUE, fmt_num, rotulo_ano_sanitario)
from .dados import ANO_MAX, ANO_MIN, DENGUE, VERSAO_DADOS, Geo
from .echarts import JS, lista, mensagem, output_echarts, render_echarts, toolbox_salvar
from .mod_curva_epidemica import escolhas_temporada, serie_por_temporada, temporada_padrao

log = logging.getLogger("dengue_pe")

MIN_ANOS_REFERENCIA = 3
N_TEMPORADAS_REFERENCIA = 5

COR_MEDIANA = PALETA_DENGUE["azul_titulo"]
COR_ATUAL = PALETA_DENGUE["dengue_forte"]
COR_NOWCAST = TONS_DENGUE["400"]
COR_LIMITE_SUPERIOR = TONS_AZUL["100"]
COR_LIMITE_INFERIOR = TONS_AZUL["50"]
COR_PREV_GLOBAL = PALETA_DENGUE["tinta"]
CORES_CLASSE = SERIES_CENARIOS[2:5]
CORES_MODELO = SERIES_CENARIOS[2:8]

# ------------------------------------------------------------- forecasting ---
# Se o pacote de previsao ou os artefatos do treino faltarem, o painel segue
# funcionando sem a camada de previsao.
FORECAST_DISPONIVEL = False
FORECAST_METRICS = None
FC = None
try:
    from forecasting import get_forecast as _gf
    from forecasting import utils_forecast as FC
    FORECAST_DISPONIVEL = _gf.forecast_disponivel()
    _met = FC.MODELS_DIR.parent / "model_metrics.csv"
    if _met.exists():
        _m = pd.read_csv(_met)
        FORECAST_METRICS = _m[_m["horizonte"] == "todos"]
    log.info("Forecasting %s.", "ativo" if FORECAST_DISPONIVEL else
             "sem artefatos - rode `python forecasting/train_models.py`")
except Exception as e:  # noqa: BLE001
    log.warning("Forecasting indisponivel: %s", e)


def catalogo_previsao() -> dict:
    """Linhas de previsao disponiveis: global | ens_<classe> | <modelo>."""
    if not FORECAST_DISPONIVEL:
        return {}
    cat = {"global": {"nome": "Previsão — Ensemble global", "cor": COR_PREV_GLOBAL, "w": 2.6,
                      "metrica": "ensemble_global", "coluna": "Casos_Previstos_Ensemble"}}
    for i, cl in enumerate(FC.fc_classes_com_modelo()):
        cat[f"ens_{cl}"] = {"nome": f"Previsão — Ensemble {FC.fc_rotulo_classe(cl)}",
                            "cor": CORES_CLASSE[i % len(CORES_CLASSE)], "w": 1.9,
                            "metrica": f"ensemble_{cl}", "coluna": f"Prev_ens_{cl}"}
    for i, k in enumerate(FC.FC_MODELOS):
        cat[k] = {"nome": f"Previsão — {FC.fc_rotulo_modelo(k)}",
                  "cor": CORES_MODELO[i % len(CORES_MODELO)], "w": 1.2,
                  "metrica": k, "coluna": f"Prev_{k}"}
    return cat


def _rotulo_item(ch: str, cat: dict):
    rot = cat[ch]["nome"].removeprefix("Previsão — ")
    extra = None
    if FORECAST_METRICS is not None:
        r = FORECAST_METRICS[FORECAST_METRICS["modelo"] == cat[ch]["metrica"]]
        if len(r):
            extra = tags.small(f"RMSE {fmt_num(r['RMSE'].iloc[0], 1)} · MAPE {fmt_num(r['MAPE'].iloc[0], 1)}%",
                               class_="text-muted")
    return tags.span(tags.strong(rot), tags.br(), extra)


# ----------------------------------------------------- cache da previsao -----
# Em disco (app_cache/previsao), compartilhado entre processos e reinicios. A
# chave inclui o dia e a versao do Parquet: atualizar o ETL nunca serve
# previsao velha.
def _dir_cache_previsao():
    d = cfg.APP_CACHE / "previsao"
    try:
        d.mkdir(parents=True, exist_ok=True)
        return d
    except OSError:
        return None


_CACHE_DIR = _dir_cache_previsao()
_CACHE_MEM: dict = {}


def previsao_cacheada(geo: Geo) -> pd.DataFrame | None:
    chave = hashlib.md5(json.dumps([geo.nivel, geo.regiao_id, geo.geocodigo, str(date.today()),
                                    VERSAO_DADOS]).encode()).hexdigest()
    if chave in _CACHE_MEM:
        return _CACHE_MEM[chave]
    arq = _CACHE_DIR / f"{chave}.pkl" if _CACHE_DIR else None
    if arq and arq.exists():
        try:
            fc = pickle.loads(arq.read_bytes())
            _CACHE_MEM[chave] = fc
            return fc
        except Exception:  # noqa: BLE001 - cache corrompido: recalcula
            pass
    t0 = time.time()
    try:
        fc = _gf.get_dengue_forecast(geo, n_semanas_hist=0, base=DENGUE, log=log.info)
    except Exception as e:  # noqa: BLE001
        log.warning("[diagrama] previsao indisponivel (%s): %s", geo.rotulo, e)
        return None
    log.info("[diagrama] previsao %s em %.1fs (specs em cache: %s).", geo.rotulo, time.time() - t0,
             fc.attrs.get("modelo_cache"))
    _CACHE_MEM[chave] = fc
    if arq:
        try:
            arq.write_bytes(pickle.dumps(fc))
        except OSError:
            pass
    return fc


# ------------------------------------------------------------------ painel ----
@module.ui
def diagrama_controle_painel():
    """Painel lateral: temporada + nowcast + camada de previsao."""
    cat = catalogo_previsao()
    if cat:
        ens = ["global"] + [f"ens_{cl}" for cl in FC.fc_classes_com_modelo()]
        blocos = [ui.input_checkbox_group("prev_ens", "Ensembles",
                                          choices={k: _rotulo_item(k, cat) for k in ens},
                                          selected=["global"])]
        for cl in FC.fc_classes_com_modelo():
            mods = FC.fc_modelos_da_classe(cl)
            blocos.append(ui.input_checkbox_group(
                f"prev_mod_{cl}", f"Modelos — {FC.fc_rotulo_classe(cl)}",
                choices={k: _rotulo_item(k, cat) for k in mods}, selected=[]))
        bloco_prev = ui.TagList(
            ui.tags.hr(class_="my-2"),
            ui.tags.h6("Previsão — até o fim da temporada", class_="mt-0 mb-1 fw-semibold"),
            *blocos,
            ui.output_ui("nota_previsao"),
        )
    else:
        bloco_prev = ui.TagList(
            ui.tags.hr(class_="my-2"),
            ui.tags.small("Previsão indisponível. Rode ", ui.tags.code("python forecasting/train_models.py"),
                          " e reinicie o app.", class_="text-muted"),
        )
    return ui.TagList(
        ui.input_select("ano", "Temporada em análise", choices=escolhas_temporada(),
                        selected=temporada_padrao()),
        ui.input_checkbox("nowcast", ui.tags.span("Mostrar linha de nowcast ",
                                                  ui.tags.small("(casos_est)", class_="text-muted")),
                          value=True),
        ui.output_ui("nota_referencia"),
        bloco_prev,
    )


@module.ui
def diagrama_controle_saida(height: str = "440px"):
    return output_echarts("grafico", height=height)


# ------------------------------------------------------------------ server ----
@module.server
def diagrama_controle_server(input, output, session, serie_semanal, geo):

    @reactive.calc
    def ano_analise() -> int:
        return int(input.ano())

    @reactive.calc
    def anos_referencia() -> list[int]:
        alvo = ano_analise()
        return [a for a in range(alvo - N_TEMPORADAS_REFERENCIA, alvo) if ANO_MIN <= a <= ANO_MAX]

    @reactive.calc
    def serie() -> pd.DataFrame:
        d = serie_semanal()[["se", "casos", "casos_est", "pop"]].copy()
        ok = d["pop"] > 0
        d["inc"] = np.where(ok, d["casos"] / d["pop"].where(ok) * 1e5, np.nan)
        d["inc_nowcast"] = np.where(ok, d["casos_est"] / d["pop"].where(ok) * 1e5, np.nan)
        return serie_por_temporada(d.drop(columns=["casos", "casos_est", "pop"]),
                                   anos_referencia() + [ano_analise()], ["inc", "inc_nowcast"])

    @reactive.calc
    def canal() -> pd.DataFrame:
        d = serie()
        ref = d[d["ano_san"].isin(anos_referencia()) & d["inc"].notna()]
        c = ref.groupby("semana")["inc"].agg(
            n_anos="size",
            q1=lambda x: x.quantile(0.25), q2=lambda x: x.quantile(0.50), q3=lambda x: x.quantile(0.75),
        ).reset_index()
        for q in ("q1", "q2", "q3"):
            c.loc[c["n_anos"] < MIN_ANOS_REFERENCIA, q] = np.nan
        return c

    # Previsao: uma por recorte e dia. Calculada numa thread (to_thread) para
    # nao travar as outras sessoes do processo enquanto os modelos ajustam.
    @reactive.calc
    async def previsao():
        if not FORECAST_DISPONIVEL:
            return None
        g = geo()
        with ui.Progress() as p:
            p.set(0.6, message="Calculando previsão…")
            return await asyncio.to_thread(previsao_cacheada, g)

    @reactive.calc
    async def temporadas_com_previsao() -> list[int]:
        fc = await previsao()
        if fc is None or fc.empty:
            return []
        return sorted(fc.loc[fc["Tipo"] == "previsto", "Ano_Sanitario"].unique().tolist())

    def selecionadas() -> list[str]:
        cat = catalogo_previsao()
        if not cat:
            return []
        sel = list(input.prev_ens() or [])
        for cl in FC.fc_classes_com_modelo():
            sel += list(input[f"prev_mod_{cl}"]() or [])
        return [k for k in cat if k in sel]

    @reactive.calc
    async def dados_grafico() -> pd.DataFrame:
        d, cnl = serie(), canal()
        atual = d[d["ano_san"] == ano_analise()][["semana", "inc", "inc_nowcast"]].rename(
            columns={"inc": "inc_notif", "inc_nowcast": "inc_now"})
        df = (pd.DataFrame({"semana": NIVEIS_SEMANA_SANITARIA})
              .merge(cnl, on="semana", how="left").merge(atual, on="semana", how="left"))
        df["faixa_base"] = df["q1"]
        df["amplitude_inferior"] = (df["q2"] - df["q1"]).clip(lower=0)
        df["amplitude_superior"] = (df["q3"] - df["q2"]).clip(lower=0)

        cat = catalogo_previsao()
        for ch in cat:
            df[f"inc_{ch}"] = np.nan
        df["inc_lo"] = np.nan
        df["inc_hi"] = np.nan

        fc = await previsao()
        if fc is not None and len(fc) and cat:
            fcw = fc[(fc["Tipo"] == "previsto") & (fc["Ano_Sanitario"] == ano_analise()) & (fc["Pop"] > 0)]
            fcw = fcw.sort_values("Data_SE")
            if len(fcw):
                pos = {s: i for i, s in enumerate(df["semana"])}
                idx = [pos.get(s) for s in fcw["Semana_Epi"]]
                k = 1e5 / fcw["Pop"].to_numpy()
                for ch, spec in cat.items():
                    if spec["coluna"] in fcw:
                        vals = fcw[spec["coluna"]].to_numpy() * k
                        for i, v in zip(idx, vals):
                            if i is not None:
                                df.loc[i, f"inc_{ch}"] = round(v, 2)
                banda = fcw.head(FC.FC_HORIZON_DEFAULT)
                kb = 1e5 / banda["Pop"].to_numpy()
                for s, lo, hi in zip(banda["Semana_Epi"], banda["Limite_Inferior"] * kb,
                                     banda["Limite_Superior"] * kb):
                    if s in pos:
                        df.loc[pos[s], ["inc_lo", "inc_hi"]] = [round(lo, 2), round(hi, 2)]
                # Ancora: ultima semana observada antes da 1a prevista (sem buraco).
                p0 = NIVEIS_SEMANA_SANITARIA.index(int(fcw["Semana_Epi"].iloc[0])) \
                    if int(fcw["Semana_Epi"].iloc[0]) in NIVEIS_SEMANA_SANITARIA else None
                if p0 and p0 > 0:
                    anc = p0 - 1
                    val = df.loc[anc, "inc_notif"]
                    if pd.notna(val):
                        for ch in cat:
                            if df[f"inc_{ch}"].notna().any():
                                df.loc[anc, f"inc_{ch}"] = round(val, 2)
                        df.loc[anc, ["inc_lo", "inc_hi"]] = round(val, 2)

        df["faixa_ic_base"] = df["inc_lo"]
        df["faixa_ic_amp"] = (df["inc_hi"] - df["inc_lo"]).clip(lower=0)
        return df.round(2)

    # -- notas ----------------------------------------------------------------
    @render.ui
    def nota_referencia():
        anos = anos_referencia()
        curtas = int((canal()["n_anos"] < MIN_ANOS_REFERENCIA).sum())
        faixa = f"{rotulo_ano_sanitario(min(anos))} – {rotulo_ano_sanitario(max(anos))}" if anos else "—"
        partes = [tags.strong("Referência: "), faixa, tags.br(), f"{len(anos)} temporada(s) no canal"]
        if curtas:
            partes += [tags.br(), tags.span(f"{curtas} semana(s) sem histórico suficiente", class_="text-warning")]
        return tags.div(*partes, class_="small text-muted")

    @render.ui
    async def nota_previsao():
        if not FORECAST_DISPONIVEL:
            return None
        fc = await previsao()
        temps = await temporadas_com_previsao()
        if not temps:
            return tags.div("Previsão indisponível para este recorte.", class_="small text-muted")
        if ano_analise() not in temps:
            plural = len(temps) > 1
            return tags.div(
                f"Previsão exibida só n{'as' if plural else 'a'} temporada{'s' if plural else ''} "
                f"{', '.join(rotulo_ano_sanitario(t) for t in temps)}.", class_="small text-warning")
        f = fc[fc["Tipo"] == "previsto"]
        n_ta = int((f["Ano_Sanitario"] == ano_analise()).sum())
        ult = f.iloc[-1]
        return tags.div(f"Previsão até SE {int(ult['Semana_Epi']):02d}/{int(ult['SE']) // 100} — "
                        f"{n_ta} semana{'' if n_ta == 1 else 's'} nesta temporada.", class_="small text-muted")

    # -- grafico -----------------------------------------------------------------
    @render_echarts
    async def grafico():
        d = await dados_grafico()
        req(len(d) > 0)
        if not anos_referencia():
            return mensagem(f"Sem temporadas de referência disponíveis antes de "
                            f"{rotulo_ano_sanitario(ano_analise())}.")

        rot = rotulo_ano_sanitario(ano_analise())
        nome_obs, nome_now = f"Coef. incidência {rot}", f"Nowcast {rot}"
        com_nowcast = bool(input.nowcast()) and d["inc_now"].notna().any()
        cat = catalogo_previsao()
        sel = selecionadas()
        tem_banda = "global" in sel and d["faixa_ic_amp"].notna().any()
        idx_corte = len(range(SEMANA_CORTE_SANITARIA, 53)) - 1
        ano_base = ano_analise()

        eixo_x = JS(f"function(v){{var se=parseInt(v,10);var a=(se>={SEMANA_CORTE_SANITARIA})?{ano_base}:"
                    f"{ano_base + 1};return '{{se|'+v+'}}\\n{{yr|'+a+'}}';}}")

        cols = [c for c in ("semana", "q1", "q2", "q3", "inc_notif", "inc_now", "inc_global",
                            "inc_lo", "inc_hi", "n_anos") if c in d]
        tabela = json.loads(d[cols].to_json(orient="records"))
        tooltip = JS(f"""function(params){{
            var tab = {json.dumps(tabela)};
            if (!params || !params.length) return '';
            var r = tab[params[0].dataIndex];
            if (!r) return '';
            function f(v){{return (v===null||v===undefined)?'—':v.toLocaleString('pt-BR',{{minimumFractionDigits:1,maximumFractionDigits:1}});}}
            var out = '<b>Semana epidemiológica ' + r.semana + '</b>';
            if (r.inc_notif !== null && r.inc_notif !== undefined) {{
              var sit = '';
              if (r.q3 !== null && r.q1 !== null && r.q3 !== undefined) {{
                if (r.inc_notif > r.q3)      sit = '<span style="color:{COR_ATUAL}">acima do esperado</span>';
                else if (r.inc_notif < r.q1) sit = '<span style="opacity:.7">abaixo do esperado</span>';
                else                          sit = '<span style="opacity:.7">dentro do esperado</span>';
              }}
              out += '<br/>{nome_obs}: <b>' + f(r.inc_notif) + '</b> ' + sit;
            }}
            if (r.inc_now !== null && r.inc_now !== undefined)
              out += '<br/><span style="color:{COR_NOWCAST}">{nome_now}: <b>' + f(r.inc_now) + '</b></span>';
            if (r.inc_global !== null && r.inc_global !== undefined) {{
              out += '<br/><span style="color:{COR_PREV_GLOBAL}">Previsão (Ensemble global): <b>' + f(r.inc_global) + '</b></span>';
              if (r.inc_lo !== null && r.inc_hi !== null && r.inc_lo !== undefined)
                out += '<br/><span style="opacity:.7">IP 80%: ' + f(r.inc_lo) + ' – ' + f(r.inc_hi) + '</span>';
            }}
            if (r.q2 !== null && r.q2 !== undefined) {{
              out += '<hr style="margin:4px 0;opacity:.3"/>' +
                     '<span style="opacity:.75">Limite superior (Q3): ' + f(r.q3) + '<br/>' +
                     'Mediana (Q2): ' + f(r.q2) + '<br/>Limite inferior (Q1): ' + f(r.q1) + '</span>' +
                     '<br/><span style="opacity:.55">' + (r.n_anos || 0) + ' temporada(s) de referência</span>';
            }}
            return out;
        }}""")

        invisivel = {"lineStyle": {"opacity": 0, "width": 0}, "areaStyle": {"color": "transparent", "opacity": 0},
                     "itemStyle": {"color": "transparent", "opacity": 0}, "emphasis": {"disabled": True},
                     "tooltip": {"show": False}, "silent": True, "symbol": "none"}
        series = [
            {"type": "line", "name": "faixa_base", "stack": "canal", "data": lista(d["faixa_base"]), **invisivel},
            {"type": "line", "name": "Limite inferior", "stack": "canal", "data": lista(d["amplitude_inferior"]),
             "symbol": "none", "silent": True, "lineStyle": {"opacity": 0},
             "areaStyle": {"color": COR_LIMITE_INFERIOR, "opacity": 1}, "itemStyle": {"color": COR_LIMITE_INFERIOR}},
            {"type": "line", "name": "Limite superior", "stack": "canal", "data": lista(d["amplitude_superior"]),
             "symbol": "none", "silent": True, "lineStyle": {"opacity": 0},
             "areaStyle": {"color": COR_LIMITE_SUPERIOR, "opacity": 1}, "itemStyle": {"color": COR_LIMITE_SUPERIOR}},
            {"type": "line", "name": "Mediana (Q2)", "data": lista(d["q2"]), "symbol": "none",
             "lineStyle": {"color": COR_MEDIANA, "width": 1.2}, "itemStyle": {"color": COR_MEDIANA}},
            {"type": "line", "name": nome_obs, "data": lista(d["inc_notif"]), "symbol": "none",
             "connectNulls": False, "lineStyle": {"color": COR_ATUAL, "width": 2.2},
             "itemStyle": {"color": COR_ATUAL},
             "markLine": {"silent": True, "symbol": "none", "label": {"show": False},
                          "lineStyle": {"color": PALETA_DENGUE["sub"], "type": "solid", "width": 1},
                          "data": [{"xAxis": idx_corte}]}},
        ]
        legenda = ["Limite inferior", "Limite superior", "Mediana (Q2)", nome_obs]

        if com_nowcast:
            series.append({"type": "line", "name": nome_now, "data": lista(d["inc_now"]), "symbol": "none",
                           "connectNulls": False, "lineStyle": {"color": COR_NOWCAST, "width": 2, "type": "dotted"},
                           "itemStyle": {"color": COR_NOWCAST}})
            legenda.append(nome_now)

        nome_ip = f"IP 80% (global, {FC.FC_HORIZON_DEFAULT if FC else 4} sem.)"
        if tem_banda:
            series += [
                {"type": "line", "name": "faixa_ic_base", "stack": "ic_prev", "data": lista(d["faixa_ic_base"]),
                 **invisivel},
                {"type": "line", "name": nome_ip, "stack": "ic_prev", "data": lista(d["faixa_ic_amp"]),
                 "symbol": "none", "silent": True, "lineStyle": {"opacity": 0},
                 "areaStyle": {"color": COR_PREV_GLOBAL, "opacity": 0.10}, "itemStyle": {"color": COR_PREV_GLOBAL}},
            ]
            legenda.append(nome_ip)

        for ch, spec in cat.items():
            col = f"inc_{ch}"
            if ch not in sel or col not in d or not d[col].notna().any():
                continue
            series.append({"type": "line", "name": spec["nome"], "data": lista(d[col]), "symbol": "circle",
                           "symbolSize": 6 if ch == "global" else 4, "connectNulls": True,
                           "lineStyle": {"color": spec["cor"], "width": spec["w"], "type": "dashed"},
                           "itemStyle": {"color": spec["cor"]}})
            legenda.append(spec["nome"])

        return {
            "textStyle": {"fontFamily": FONTE_GRAFICO},
            "xAxis": {"type": "category", "data": [str(s) for s in d["semana"]], "boundaryGap": False,
                      "name": "Semana epidemiológica (temporada jul–jun)", "nameLocation": "middle", "nameGap": 42,
                      "axisLabel": {"interval": 3, "formatter": eixo_x, "lineHeight": 15,
                                    "rich": {"se": {"fontSize": 11},
                                             "yr": {"fontSize": 9, "color": PALETA_DENGUE["sub"],
                                                    "padding": [3, 0, 0, 0]}}}},
            "yAxis": {"type": "value", "name": "Coeficiente de Incidência /100 mil",
                      "nameLocation": "middle", "nameGap": 48},
            "legend": {"data": legenda, "bottom": 0, "type": "scroll"},
            "grid": {"left": 70, "right": 24, "top": 24, "bottom": 88},
            "tooltip": {"trigger": "axis", "formatter": tooltip},
            "toolbox": toolbox_salvar(),
            "series": series,
        }
