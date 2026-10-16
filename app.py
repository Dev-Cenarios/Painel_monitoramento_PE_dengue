# ==============================================================================
# Dengue PE | app.py
# Painel de monitoramento epidemiologico da dengue em Pernambuco - Shiny for
# Python. Replica o painel dengue_ride (R), com Pernambuco -> Regiao de Saude
# (GERES) -> Municipio no lugar de RIDE-DF -> Estado -> Municipio.
#
# Duas abas:
#   "Painel"   - recorte geografico, KPIs, mapa | evolucao temporal, tendencia
#                por municipio, diagrama de controle + previsao, sazonalidade,
#                ultimas semanas / SQL.
#   "Análises" - Epi Scanner (mapa + Top 10 por municipio, sempre PE inteiro).
#
# Rodar:  shiny run app.py   (ou  python -m shiny run app.py --port 8000)
# ==============================================================================

from __future__ import annotations

from pathlib import Path

import numpy as np
from shiny import App, reactive, render, ui

from painel import dados
from painel.config import fmt_int, fmt_num
from painel.dados import ANO_MAX, ANO_MIN, GEO_CACHE_DIR, GEO_DICT, consultar_serie_semanal
from painel.mod_curva_epidemica import (curva_epidemica_controles, curva_epidemica_saida,
                                        curva_epidemica_server)
from painel.mod_diagrama_controle import (diagrama_controle_painel, diagrama_controle_saida,
                                          diagrama_controle_server)
from painel.mod_epi_scanner import epi_scanner_server, epi_scanner_ui
from painel.mod_geo_filter import geo_filter_resumo, geo_filter_server, geo_filter_ui
from painel.mod_heatmap import heatmap_controles, heatmap_saida, heatmap_server
from painel.mod_kpi import kpi_server, kpi_ui
from painel.mod_mapa import mapa_controles, mapa_saida, mapa_server
from painel.mod_mapa_rt import mapa_rt_controles, mapa_rt_saida, mapa_rt_server
from painel.mod_rt import rt_controles, rt_saida, rt_server
from painel.mod_serie_temporal import serie_temporal_controles, serie_temporal_saida, serie_temporal_server
from painel.ui_utils import TEMA, assets, cartao_grafico, gatilho_opcoes

AQUI = Path(__file__).resolve().parent

# ------------------------------------------------------------------------ UI --
aba_painel = ui.nav_panel(
    "Painel",
    # cabecalho
    ui.div(
        ui.div(
            ui.h4("Dengue PE", class_="mb-0 fw-semibold"),
            ui.span("Painel de monitoramento epidemiológico · fonte InfoDengue", class_="text-muted small"),
        ),
        ui.span(f"{fmt_int(len(GEO_DICT))} municípios · {ANO_MIN}–{ANO_MAX}", class_="text-muted small"),
        class_="d-flex justify-content-between align-items-end mb-3",
    ),
    # 1. recorte geografico
    ui.card(
        ui.card_header(
            ui.span("Recorte geográfico", class_="fw-semibold"),
            ui.div(
                geo_filter_resumo("geo"),
                ui.input_action_button("limpar_selecao", "Visão geral", class_="btn btn-sm btn-light"),
                class_="d-flex align-items-center gap-3",
            ),
            class_="bg-primary text-white d-flex justify-content-between align-items-center",
        ),
        ui.card_body(geo_filter_ui("geo")),
        class_="mb-3",
    ),
    # 2. KPIs
    kpi_ui("kpi"),
    # 3. mapa (7) | evolucao temporal (5)
    ui.layout_columns(
        cartao_grafico("Distribuição espacial", mapa_saida("mapa", height="540px"), mapa_controles("mapa")),
        ui.navset_card_tab(
            ui.nav_panel("Série", ui.card_body(serie_temporal_saida("serie", height="460px"), class_="p-0")),
            ui.nav_panel("Curva epidêmica",
                         ui.card_body(curva_epidemica_saida("curva", height="460px"), class_="p-0")),
            # "(InfoDengue)": este e o Rt real; o card de tendencia abaixo e outro indicador.
            ui.nav_panel("Rt (InfoDengue)", ui.card_body(rt_saida("rt", height="460px"), class_="p-0")),
            ui.nav_spacer(),
            ui.nav_control(ui.popover(
                gatilho_opcoes(),
                ui.h6("Série temporal", class_="mt-0"), serie_temporal_controles("serie"), ui.hr(),
                ui.h6("Curva epidêmica"), curva_epidemica_controles("curva"), ui.hr(),
                ui.h6("Rt (InfoDengue)"), rt_controles("rt"),
                title="Opções", placement="left",
            )),
            title=ui.span("Evolução temporal", class_="fw-semibold"),
            full_screen=True,
        ),
        col_widths=(7, 5),
    ),
    # 4. tendencia por municipio (nao e o Rt real - ver painel/mod_mapa_rt.py)
    cartao_grafico("Rt — Tendência por município (quadrantes)", mapa_rt_saida("tendencia", height="560px"),
                   mapa_rt_controles("tendencia")),
    # 5a. diagrama de controle + previsao
    ui.card(
        ui.card_header(ui.span("Diagrama de controle (canal endêmico) & previsão de casos", class_="fw-semibold"),
                       class_="bg-primary text-white"),
        ui.card_body(
            ui.layout_columns(
                diagrama_controle_saida("diagrama", height="520px"),
                ui.div(diagrama_controle_painel("diagrama"), class_="overflow-auto ps-2 border-start",
                       style="max-height: 520px;"),
                col_widths=(9, 3),
                class_="g-3",
            ),
        ),
        full_screen=True,
        class_="mb-3",
    ),
    # 5b. sazonalidade
    cartao_grafico("Sazonalidade — incidência por ano e semana", heatmap_saida("heatmap", height="400px"),
                   heatmap_controles("heatmap")),
    # 6. ultimas semanas / SQL
    ui.navset_card_tab(
        ui.nav_panel(
            "Últimas semanas",
            ui.card_body(
                ui.output_table("tab_serie"),
                ui.p("Agregação feita no DuckDB sobre o Parquet; apenas as linhas exibidas chegam ao Python.",
                     class_="small text-muted mb-0"),
            ),
        ),
        ui.nav_panel(
            "SQL gerado",
            ui.card_body(
                ui.p("Consulta enviada ao DuckDB para o recorte atual.", class_="small text-muted"),
                ui.output_code("sql_gerado"),
            ),
        ),
        title=ui.span(ui.output_text("titulo_recorte", inline=True), class_="fw-semibold"),
    ),
)

app_ui = ui.page_navbar(
    aba_painel,
    ui.nav_panel("Análises", epi_scanner_ui("epi_scanner")),
    ui.nav_spacer(),
    title="Dengue PE",
    theme=TEMA,
    window_title="Dengue PE — Painel de Monitoramento Epidemiológico",
    padding="1rem",
    fillable=False,
    header=assets(),
)


# -------------------------------------------------------------------- server --
def server(input, output, session):

    # Estado global: local selecionado (None = visao geral). Desacopla quem
    # PEDE o foco (mapa, seletores) de quem o APLICA (modulo geografico).
    selected_location = reactive.value(None)
    nonce = {"n": 0}

    def limpar_selecao():
        nonce["n"] += 1   # forca a notificacao mesmo se o estado ja era "base"
        selected_location.set({"nivel": "base", "regiao_id": None, "geocodigo": None, "nonce": nonce["n"]})

    geo = geo_filter_server("geo", foco_externo=selected_location)

    # Serie semanal compartilhada: UMA consulta por recorte (cache por processo).
    @reactive.calc
    def serie_semanal():
        return consultar_serie_semanal(geo())

    kpi_server("kpi", geo=geo, serie_semanal=serie_semanal)

    # Mapa: desenha sempre PE inteiro e so DESTACA o municipio selecionado.
    clique_mapa = mapa_server("mapa", selected_location=selected_location)

    @reactive.effect
    @reactive.event(clique_mapa, ignore_init=True)
    def _clique_mapa():
        pedido = clique_mapa()
        if not pedido:
            return
        if pedido["nivel"] == "base":
            limpar_selecao()
        else:
            selected_location.set(pedido)

    @reactive.effect
    @reactive.event(input.limpar_selecao, ignore_init=True)
    def _limpar():
        limpar_selecao()

    # Caminho inverso do clique: municipio escolhido nos seletores tambem e
    # destacado no mapa e na tendencia. Regiao/estado limpam o destaque.
    @reactive.effect
    @reactive.event(geo, ignore_init=True)
    def _seletor_para_estado():
        g = geo()
        with reactive.isolate():
            atual = selected_location()
        cod_atual = atual["geocodigo"] if atual and atual.get("nivel") == "municipio" else None
        if g.nivel == "municipio":
            if cod_atual != g.geocodigo:
                selected_location.set({"nivel": "municipio", "regiao_id": g.regiao_id, "geocodigo": g.geocodigo})
        elif cod_atual is not None:
            selected_location.set(None)

    diagrama_controle_server("diagrama", serie_semanal=serie_semanal, geo=geo)
    serie_temporal_server("serie", geo=geo, serie_semanal=serie_semanal)
    rt_server("rt", geo=geo, serie_semanal=serie_semanal)
    curva_epidemica_server("curva", serie_semanal=serie_semanal)
    heatmap_server("heatmap", geo=geo, serie_semanal=serie_semanal)
    # Tendencia e Analises comparam os municipios entre si: nao recebem geo().
    # Um clique num ponto da tendencia recorta o painel, como o clique no mapa.
    clique_tendencia = mapa_rt_server("tendencia", selected_location=selected_location)

    @reactive.effect
    @reactive.event(clique_tendencia, ignore_init=True)
    def _clique_tendencia():
        if clique_tendencia():
            selected_location.set(clique_tendencia())
    epi_scanner_server("epi_scanner")

    # -- ultimas semanas / SQL ----------------------------------------------------
    @reactive.calc
    def sql_ultimas():
        where, params = geo().where()
        sql = ("SELECT data_ini_se, se,\n       sum(casos) AS casos, sum(casos_est) AS casos_est,"
               " sum(pop) AS pop\nFROM dengue\n"
               f"WHERE {where}\nGROUP BY data_ini_se, se\nORDER BY data_ini_se DESC\nLIMIT 12")
        return sql, params

    @render.text
    def titulo_recorte():
        return geo().rotulo

    @render.table(classes="table table-striped table-hover table-sm")
    def tab_serie():
        sql, params = sql_ultimas()
        d = dados.consultar(sql, params)
        inc = np.where(d["pop"] > 0, d["casos"] / d["pop"] * 1e5, np.nan)
        return d.assign(
            **{"Semana": [x.strftime("%d/%m/%Y") for x in d["data_ini_se"]],
               "SE": d["se"].astype(str),
               "Casos": [fmt_int(x) for x in d["casos"]],
               "Casos est.": [fmt_num(x, 0) for x in d["casos_est"]],
               "Inc. /100k": [fmt_num(x, 1) for x in inc]}
        )[["Semana", "SE", "Casos", "Casos est.", "Inc. /100k"]]

    @render.code
    def sql_gerado():
        sql, params = sql_ultimas()
        for p in params:
            sql = sql.replace("?", str(p), 1)
        return sql


# Arquivos estaticos: www/ (ECharts, JS do painel) e a malha recortada.
app = App(app_ui, server, static_assets={"/static": AQUI / "www", "/geo": GEO_CACHE_DIR})
