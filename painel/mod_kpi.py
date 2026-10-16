# ==============================================================================
# Dengue PE | Modulo: Painel de KPIs
#
# Quatro value_box no topo do painel:
#   1. casos acumulados no ano mais recente com dados (total historico na nota)
#   2. incidencia da ultima semana disponivel
#   3. Rt da ultima semana com estimativa
#   4. abrangencia do recorte
#
# Renderizados no servidor porque a COR carrega informacao: escurece conforme
# a situacao piora, seguindo a rampa da dengue (TONS_DENGUE). Nao consulta o
# banco: consome a serie semanal ja carregada pelo app.
# ==============================================================================

from __future__ import annotations

import pandas as pd
from htmltools import tags
from shiny import module, reactive, render, req, ui

from .config import PALETA_DENGUE, TONS_AZUL, TONS_DENGUE, fmt_int, fmt_num
from .dados import calcular_indicadores


@module.ui
def kpi_ui():
    return ui.output_ui("boxes")


def _tom(bg: str, claro: bool = False):
    # Tons claros pedem texto escuro para continuar legiveis.
    return ui.value_box_theme(bg=bg, fg=PALETA_DENGUE["tinta"] if claro else "#FFFFFF")


def _tema_incidencia(inc):
    if inc is None or pd.isna(inc):
        return _tom(PALETA_DENGUE["linha"], claro=True)
    if inc >= 300:
        return _tom(TONS_DENGUE["800"])
    if inc >= 100:
        return _tom(TONS_DENGUE["600"])
    return _tom(TONS_DENGUE["100"], claro=True)


def _tema_rt(rt):
    if rt is None or pd.isna(rt):
        return _tom(PALETA_DENGUE["linha"], claro=True)
    if rt > 1:
        return _tom(TONS_DENGUE["800"])
    return _tom(TONS_AZUL["100"], claro=True)


@module.server
def kpi_server(input, output, session, geo, serie_semanal):

    @reactive.calc
    def indicadores():
        d = calcular_indicadores(serie_semanal(), geo())
        req(len(d) > 0)
        d = d.sort_values("data_ini_se")
        ultima = d.iloc[-1]
        ano = ultima["data_ini_se"].year
        no_ano = d["data_ini_se"].dt.year == ano
        # O Rt da ultima semana pode faltar: recua ate a mais recente com valor
        # e informa a data, para o numero nao ser lido como se fosse de hoje.
        com_rt = d[d["rt"].notna()]
        return {
            "ano": ano,
            "casos_ano": d.loc[no_ano, "casos"].sum(),
            "casos_total": d["casos"].sum(),
            "inc_ultima": ultima["incidencia"],
            "casos_ultima": ultima["casos"],
            "data_ultima": ultima["data_ini_se"],
            "rt": com_rt["rt"].iloc[-1] if len(com_rt) else None,
            "data_rt": com_rt["data_ini_se"].iloc[-1] if len(com_rt) else None,
            "n_semanas": len(d),
        }

    @render.ui
    def boxes():
        k = indicadores()
        g = geo()
        rt = k["rt"]
        if rt is None:
            nota_rt = "sem estimativa disponível"
        else:
            lado = "acima" if rt > 1 else "abaixo"
            nota_rt = f"{lado} do limiar · {k['data_rt']:%d/%m/%Y}"

        return ui.layout_columns(
            ui.value_box(
                f"Casos acumulados em {k['ano']}",
                fmt_int(k["casos_ano"]),
                tags.span(f"{fmt_int(k['casos_total'])} no histórico completo", class_="small"),
                theme=_tom(PALETA_DENGUE["dengue_forte"]),
            ),
            ui.value_box(
                "Incidência na última semana",
                fmt_num(k["inc_ultima"], 1),
                tags.span(
                    f"por 100 mil · {fmt_int(k['casos_ultima'])} casos · "
                    f"SE de {k['data_ultima']:%d/%m/%Y}",
                    class_="small",
                ),
                theme=_tema_incidencia(k["inc_ultima"]),
            ),
            ui.value_box(
                "Rt atual",
                "—" if rt is None else fmt_num(rt, 2),
                tags.span(nota_rt, class_="small"),
                theme=_tema_rt(rt),
            ),
            ui.value_box(
                "Recorte",
                g.rotulo,
                tags.span(f"{fmt_int(g.n_mun)} município(s) · {fmt_int(k['n_semanas'])} semanas",
                          class_="small"),
                theme=_tom(PALETA_DENGUE["azul_titulo"]),
            ),
            fill=False,
            class_="mb-1",
        )
