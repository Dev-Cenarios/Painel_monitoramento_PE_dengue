# ==============================================================================
# Dengue PE | Modulo: Curva Epidemica
#
# Casos notificados de cada semana da temporada em analise contra o volume
# historico das temporadas de referencia. Mesma ordenacao de semana (ano
# sanitario, SE 27 -> 26) do diagrama de controle.
#
# DIFERENCA para o diagrama de controle: casos ABSOLUTOS, e o fundo historico
# e a SOMA dos casos de cada semana nas temporadas de referencia (barras) +
# MEDIANA historica (linha) - "volume notificado naquela semana ao longo dos
# anos", nao "faixa esperada" (isso e o diagrama de controle).
# ==============================================================================

from __future__ import annotations

import pandas as pd
from shiny import module, reactive, req, ui

from .config import (FONTE_GRAFICO, NIVEIS_SEMANA_SANITARIA, PALETA_DENGUE, SEMANA_CORTE_SANITARIA,
                     SERIES_CENARIOS, TONS_DENGUE, ano_sanitario, rotulo_ano_sanitario)
from .dados import ANO_MAX, ANO_MIN, dobrar_semana_53
from .echarts import JS, lista, output_echarts, render_echarts, toolbox_salvar

COR_CURVA_HIST_MEDIANA = PALETA_DENGUE["azul_acao"]
COR_CURVA_ATUAL = PALETA_DENGUE["tinta"]
COR_CURVA_NOWCAST = SERIES_CENARIOS[2]


def escolhas_temporada() -> dict:
    return {str(a): rotulo_ano_sanitario(a) for a in range(ANO_MAX, ANO_MIN - 1, -1)}


def temporada_padrao() -> str:
    """Temporada da semana mais recente da base (ex.: SE 40/2026 -> 2026/2027).

    No RIDE o padrao e ANO_MAX, que so coincide com a temporada corrente a
    partir da SE 27; antes disso apontaria para uma temporada ainda vazia.
    """
    from .dados import SERIE_MUNICIPIOS
    ult = SERIE_MUNICIPIOS["se"].max()
    return str(ano_sanitario(ult // 100, ult % 100))


def serie_por_temporada(serie_semanal: pd.DataFrame, temporadas: list[int], colunas: list[str]) -> pd.DataFrame:
    d = serie_semanal[["se"] + colunas].copy()
    d["ano"] = d["se"] // 100
    d["semana"] = d["se"] % 100
    d = d[(d["semana"] >= 1) & (d["semana"] <= 53)]
    d["ano_san"] = ano_sanitario(d["ano"], d["semana"])
    d = d[d["ano_san"].isin(temporadas)]
    return dobrar_semana_53(d, colunas)


@module.ui
def curva_epidemica_controles():
    return ui.TagList(
        ui.input_select("ano", "Temporada em análise", choices=escolhas_temporada(),
                        selected=temporada_padrao()),
        ui.input_slider("n_ref", "Temporadas de referência", min=3, max=10, value=5, step=1),
        ui.input_checkbox("mostrar_nowcast", "Mostrar nowcast", value=True),
        ui.help_text(ui.tags.small(
            "Barras: soma dos casos notificados daquela semana em todas as temporadas de "
            "referência (mais escuras perto do máximo histórico). Linha azul: mediana "
            "histórica. Semana no eixo segue o ano sanitário (27 a 26), igual ao diagrama "
            "de controle."
        )),
    )


@module.ui
def curva_epidemica_saida(height: str = "420px"):
    return output_echarts("grafico", height=height)


@module.server
def curva_epidemica_server(input, output, session, serie_semanal):

    @reactive.calc
    def ano_analise() -> int:
        return int(input.ano())

    @reactive.calc
    def anos_referencia() -> list[int]:
        alvo = ano_analise()
        return [a for a in range(alvo - int(input.n_ref()), alvo) if ANO_MIN <= a <= ANO_MAX]

    @reactive.calc
    def dados_grafico() -> pd.DataFrame:
        temporadas = anos_referencia() + [ano_analise()]
        d = serie_por_temporada(serie_semanal(), temporadas, ["casos", "casos_est"])
        hist = (d[d["ano_san"].isin(anos_referencia())].groupby("semana")["casos"]
                .agg(casos_hist_total="sum", mediana_hist="median").reset_index())
        atual = d[d["ano_san"] == ano_analise()][["semana", "casos", "casos_est"]].rename(
            columns={"casos": "casos_atual", "casos_est": "casos_nowcast"})
        out = (pd.DataFrame({"semana": NIVEIS_SEMANA_SANITARIA})
               .merge(hist, on="semana", how="left").merge(atual, on="semana", how="left"))
        return out.round(1)

    @render_echarts
    def grafico():
        d = dados_grafico()
        req(len(d) > 0)
        rot = rotulo_ano_sanitario(ano_analise())
        nome_atual, nome_nowcast = f"Casos {rot}", f"Nowcast {rot}"

        max_hist = d["casos_hist_total"].max()
        if pd.isna(max_hist) or max_hist <= 0:
            max_hist = 1
        # Barra mais escura quanto mais perto do maximo historico.
        cor_barra = JS(f"""function(p){{
            var v = p.value, m = {float(max_hist)};
            if (v === null || v === undefined) return '{TONS_DENGUE["100"]}';
            if (v > m * 0.8) return '{TONS_DENGUE["600"]}';
            if (v > m * 0.5) return '{TONS_DENGUE["400"]}';
            return '{TONS_DENGUE["200"]}';
        }}""")
        # Linha vertical na virada da temporada (semana 52 -> semana 1).
        idx_corte = len(range(SEMANA_CORTE_SANITARIA, 53)) - 1

        series = [
            {"type": "bar", "name": "Histórico (soma)", "data": lista(d["casos_hist_total"]),
             "itemStyle": {"color": cor_barra, "borderRadius": [2, 2, 0, 0]}},
            {"type": "line", "name": "Mediana histórica", "data": lista(d["mediana_hist"]),
             "symbol": "none", "lineStyle": {"color": COR_CURVA_HIST_MEDIANA, "width": 2},
             "itemStyle": {"color": COR_CURVA_HIST_MEDIANA}},
            {"type": "line", "name": nome_atual, "data": lista(d["casos_atual"]),
             "symbol": "circle", "symbolSize": 4,
             "lineStyle": {"color": COR_CURVA_ATUAL, "width": 2.5, "type": "dashed"},
             "itemStyle": {"color": COR_CURVA_ATUAL},
             "markLine": {"silent": True, "symbol": "none", "label": {"show": False},
                          "lineStyle": {"color": PALETA_DENGUE["sub"], "type": "solid", "width": 1},
                          "data": [{"xAxis": idx_corte}]}},
        ]
        legenda = ["Histórico (soma)", "Mediana histórica", nome_atual]
        if input.mostrar_nowcast():
            series.append({"type": "line", "name": nome_nowcast, "data": lista(d["casos_nowcast"]),
                           "symbol": "none",
                           "lineStyle": {"color": COR_CURVA_NOWCAST, "width": 2, "type": "dotted"},
                           "itemStyle": {"color": COR_CURVA_NOWCAST}})
            legenda.append(nome_nowcast)

        return {
            "textStyle": {"fontFamily": FONTE_GRAFICO},
            "xAxis": {"type": "category", "data": [str(s) for s in d["semana"]],
                      "name": "Semana epidemiológica (temporada jul–jun)",
                      "nameLocation": "middle", "nameGap": 28, "axisLabel": {"interval": 3}},
            "yAxis": {"type": "value", "name": "casos", "nameLocation": "end", "nameGap": 12},
            "legend": {"data": legenda, "top": 0, "type": "scroll"},
            "grid": {"left": 70, "right": 24, "top": 40, "bottom": 60},
            "tooltip": {"trigger": "axis"},
            "toolbox": toolbox_salvar(),
            "series": series,
        }
