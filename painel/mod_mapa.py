# ==============================================================================
# Dengue PE | Modulo: mapa coropletico
#
# GRANULARIDADE: sempre municipal, e SEMPRE a base inteira (185 municipios).
# Clicar num municipio NAO recorta o mapa: so o DESTACA (estado 'select' do
# ECharts, via mensagem 'dengue_map_highlight' em www/dengue.js). Quem filtra
# os demais graficos e o recorte global (geo), nao o mapa.
#
# Metrica: incidencia = sum(casos) / pop * 100.000 no periodo. max(pop) e nao
# sum(pop): somar populacao ao longo das semanas multiplicaria o denominador.
#
# Contrato de saida: reactive.value com None ou dict(nivel, regiao_id,
# geocodigo[, nonce]) - pedido de foco para o filtro global.
#
# BORDAS: municipios em cinza claro e, por cima, o contorno do estado
# (CODIGOS_CONTORNO, em painel/dados.py) - sem isso, municipio na cor mais
# clara da escala some no fundo branco do card.
# ==============================================================================

from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd
from shiny import module, reactive, req, ui

from . import config as cfg
from .config import PALETA_DENGUE, PECAS_INCIDENCIA, RAMPA_MAPA, TONS_AZUL
from .dados import (ANO_MAX, ANO_MIN, CODIGOS_CONTORNO, DENGUE, GEO_DICT, MALHA_BASE_URL,
                    REGIAO_DO_MUNICIPIO)
from .echarts import JS, output_echarts, render_echarts, toolbox_salvar


COR_BORDA_MUN = "#CBD5DB"   # entre municipios
COR_BORDA_UF = "#94A3AC"    # contorno do estado


def itens_contorno() -> list[dict]:
    """Itens de dado do contorno do estado (fim da malha BASE_MUN).

    Sem valor (o visualMap os ignora), sem preenchimento e `silent` (clique,
    hover e tooltip atravessam para o municipio de baixo) - so a borda.
    opacity = 1 no proprio item: o destaque (dengue_map_highlight) baixa a
    opacidade da serie inteira, e o contorno deve continuar visivel. Todo mapa
    que usa BASE_MUN precisa deles: sem os itens, a feicao do contorno seria
    pintada por cima de tudo.
    """
    return [{"name": c,
             "itemStyle": {"areaColor": "none", "opacity": 1,
                           "borderColor": COR_BORDA_UF, "borderWidth": 1.2},
             "emphasis": {"disabled": True}, "select": {"disabled": True},
             "tooltip": {"show": False}, "silent": True}
            for c in CODIGOS_CONTORNO]


@module.ui
def mapa_controles():
    return ui.TagList(
        ui.input_radio_buttons(
            "metrica", "Métrica",
            choices={"incidencia": "Incidência /100 mil", "casos": "Casos notificados"},
            selected="incidencia",
        ),
        ui.input_slider("anos", "Período (ano)", min=ANO_MIN, max=ANO_MAX,
                        value=(max(ANO_MIN, ANO_MAX - 1), ANO_MAX), step=1, sep=""),
        ui.help_text(ui.tags.small("Clique em uma área do mapa para focar o painel nela.")),
    )


@module.ui
def mapa_saida(height: str = "540px"):
    # Botao "Visao geral" sobreposto ao canto do mapa (o toolbox fica no canto
    # direito e o visualMap no inferior esquerdo; este canto esta livre).
    return ui.div(
        ui.input_action_button(
            "limpar", "↺ Visão geral",
            class_="btn btn-sm btn-light shadow-sm",
            style="position:absolute; top:8px; left:8px; z-index:5; padding:.15rem .5rem; font-size:.8rem;",
        ),
        # html-fill-*: o mapa estica ate a altura do card, que acompanha a
        # coluna da direita (Rt + evolucao temporal) em app.py.
        output_echarts("mapa", height=height).add_class("html-fill-item"),
        class_="position-relative html-fill-item html-fill-container",
    )


def _dados_periodo(ano_ini: int, ano_fim: int):
    b = DENGUE[DENGUE["data_ini_se"].between(pd.Timestamp(f"{ano_ini}-01-01"), pd.Timestamp(f"{ano_fim}-12-31"))]
    g = b.groupby("municipio_geocodigo")
    d = pd.DataFrame({"casos": g["casos"].sum(min_count=1), "pop": g["pop"].max()}).reset_index()
    d["municipio_geocodigo"] = d["municipio_geocodigo"].astype(int)
    # Junta com a lista completa: municipio sem notificacao continua desenhado.
    areas = GEO_DICT[["municipio_geocodigo", "municipio_nome"]].merge(
        d, on="municipio_geocodigo", how="left")
    areas["casos"] = areas["casos"].fillna(0)
    return areas


@module.server
def mapa_server(input, output, session, selected_location):

    @reactive.calc
    def dados_mapa():
        anos = input.anos()
        req(anos)
        d = _dados_periodo(int(anos[0]), int(anos[1]))
        if input.metrica() == "incidencia":
            d["valor"] = np.where(d["pop"] > 0, d["casos"] / d["pop"] * 1e5, np.nan)
        else:
            d["valor"] = d["casos"]
        d["valor"] = d["valor"].round(1)
        d["chave"] = d["municipio_geocodigo"].astype(str)
        return d

    @render_echarts
    def mapa():
        d = dados_mapa()
        req(len(d) > 0)
        incidencia = input.metrica() == "incidencia"
        rotulo = "Incidência /100 mil" if incidencia else "Casos"
        nomes = json.dumps(dict(zip(d["chave"], d["municipio_nome"])), ensure_ascii=False)

        tooltip = JS(f"""function(p){{
            var nomes = {nomes};
            var n = nomes[p.name] || p.name;
            if (p.value === undefined || p.value === null || isNaN(p.value))
              return '<b>' + n + '</b><br/>sem dado';
            return '<b>' + n + '</b><br/>{rotulo}: ' +
                   p.value.toLocaleString('pt-BR', {{maximumFractionDigits: 1}});
        }}""")

        if incidencia:
            visual = {"type": "piecewise", "pieces": PECAS_INCIDENCIA, "orient": "vertical",
                      "left": "left", "bottom": "6%", "itemWidth": 14, "itemHeight": 12}
        else:
            visual = {"type": "continuous", "min": 0, "max": float(np.nanmax(d["valor"]) or 1),
                      "orient": "vertical", "left": "left", "bottom": "6%", "calculable": True,
                      "inRange": {"color": RAMPA_MAPA},
                      "formatter": JS("function(v){return v.toLocaleString('pt-BR',{maximumFractionDigits:0});}")}

        return {
            "__mapa__": {"nome": "BASE_MUN", "url": MALHA_BASE_URL},
            "__clique__": session.ns("clique"),
            "textStyle": {"fontFamily": cfg.FONTE_GRAFICO},
            "tooltip": {"trigger": "item", "formatter": tooltip},
            "toolbox": toolbox_salvar(),
            "visualMap": visual,
            "series": [{
                "type": "map",
                "map": "BASE_MUN",
                "name": rotulo,
                "nameProperty": cfg.GEO_NAME_PROPERTY,
                "roam": True,
                "itemStyle": {"borderColor": COR_BORDA_MUN, "borderWidth": 0.6},
                # Sem isto o ECharts desenha o "nome" interno (o codigo IBGE) ao
                # passar o mouse, mesmo com o tooltip ja resolvendo o nome.
                "label": {"show": False},
                "emphasis": {"itemStyle": {"areaColor": TONS_AZUL["300"]}, "label": {"show": False}},
                "selectedMode": "single",
                "select": {"itemStyle": {"areaColor": PALETA_DENGUE["azul_acao"], "opacity": 1},
                           "label": {"show": False}},
                "data": [{"name": c, "value": v} for c, v in zip(d["chave"], d["valor"])]
                        + itens_contorno(),
            }],
        }

    # -- destaque do local selecionado (sem re-renderizar o mapa) --------------
    # Depende tambem de dados_mapa(): quando metrica/periodo re-renderizam o
    # mapa, a mensagem e reemitida.
    @reactive.effect
    async def _destaque():
        sl = selected_location()
        try:
            chaves = set(dados_mapa()["chave"])
        except Exception:  # noqa: BLE001 - req() silencioso
            chaves = set()
        cod = None
        if sl and sl.get("nivel") == "municipio" and sl.get("geocodigo") is not None:
            c0 = str(sl["geocodigo"])
            if c0 in chaves:
                cod = c0
        await session.send_custom_message("dengue_map_highlight",
                                          {"id": session.ns("mapa"), "name": cod})

    # -- pedido de foco para o filtro global -------------------------------------
    pedido_foco = reactive.value(None)

    @reactive.effect
    @reactive.event(input.clique)
    def _clique():
        try:
            cod = int(input.clique())
        except (TypeError, ValueError):
            return
        pedido_foco.set({"nivel": "municipio", "regiao_id": REGIAO_DO_MUNICIPIO.get(cod),
                         "geocodigo": cod, "ts": time.time()})

    @reactive.effect
    @reactive.event(input.limpar)
    def _limpar():
        pedido_foco.set({"nivel": "base", "regiao_id": None, "geocodigo": None, "ts": time.time()})

    return pedido_foco
