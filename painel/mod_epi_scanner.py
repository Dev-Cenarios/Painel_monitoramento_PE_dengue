# ==============================================================================
# Dengue PE | Modulo: Analises (Epi Scanner)
#
# Aba de analise de longo prazo, inspirada no Epi Scanner do InfoDengue: mapa
# + tabela comparando os municipios de PE entre si. Por design NAO participa
# do recorte global - o valor esta em comparar os municipios entre si.
#
# Metricas (desde o inicio da serie, 2015): semanas com Rt > 1, Rt maximo do
# ano mais recente e casos acumulados. Fonte: SERIE_MUNICIPIOS (em memoria),
# calculado uma vez por processo.
# ==============================================================================

from __future__ import annotations

import json
from functools import lru_cache

import pandas as pd
from htmltools import HTML
from shiny import module, render, req, ui

from .config import FONTE_GRAFICO, RAMPA_MAPA, TONS_AZUL, fmt_int, fmt_num
from . import config as cfg
from .dados import ANO_MAX, GEO_DICT, MALHA_BASE_URL, SERIE_MUNICIPIOS
from .echarts import JS, output_echarts, render_echarts, toolbox_salvar
from .mod_mapa import COR_BORDA_MUN, itens_contorno
from .ui_utils import cartao_grafico

ROTULOS = {
    "semanas_rt_alto": "Semanas com Rt > 1",
    "rt_max_atual": f"Rt máximo ({ANO_MAX})",
    "casos_totais": "Casos acumulados",
}
TITULOS = {
    "semanas_rt_alto": "Top 10 — Semanas com Rt > 1 (desde 2015)",
    "rt_max_atual": f"Top 10 — Rt máximo ({ANO_MAX})",
    "casos_totais": "Top 10 — Casos acumulados (desde 2015)",
}


@lru_cache(maxsize=1)
def dados_municipio() -> pd.DataFrame:
    s = SERIE_MUNICIPIOS
    desde = s[s["ano"] >= 2015]
    d = pd.DataFrame({
        "semanas_rt_alto": desde.assign(alto=desde["rt"] > 1).groupby("municipio_geocodigo")["alto"].sum(),
        "rt_max_atual": s[s["ano"] == ANO_MAX].groupby("municipio_geocodigo")["rt"].max(),
        "casos_totais": desde.groupby("municipio_geocodigo")["casos"].sum(),
    }).reset_index()
    return (d.merge(GEO_DICT[["municipio_geocodigo", "municipio_nome", "regiao_nome"]],
                    on="municipio_geocodigo", how="left")
            .sort_values("municipio_nome").reset_index(drop=True))


@module.ui
def epi_scanner_ui():
    return ui.TagList(
        ui.card(
            ui.card_body(
                ui.input_radio_buttons(
                    "metrica", "Métrica do mapa",
                    choices={"semanas_rt_alto": "Semanas com Rt > 1 (desde 2015)",
                             "rt_max_atual": "Rt máximo (temporada atual)",
                             "casos_totais": "Total acumulado de casos (desde 2015)"},
                    selected="casos_totais", inline=True,
                ),
            ),
            class_="mb-3",
        ),
        ui.layout_columns(
            cartao_grafico("Mapa por município", output_echarts("mapa", height="520px")),
            ui.card(
                ui.card_header(ui.tags.span("Tabela por município", class_="fw-semibold"),
                               class_="bg-primary text-white"),
                ui.card_body(ui.output_ui("tabela")),
                full_screen=True,
            ),
            col_widths=(6, 6),
        ),
    )


@module.server
def epi_scanner_server(input, output, session):

    @render_echarts
    def mapa():
        req(input.metrica())
        d = dados_municipio().copy()
        met = input.metrica()
        d["chave"] = d["municipio_geocodigo"].astype(str)
        d["valor"] = d[met].astype(float).round(2)
        rotulo = ROTULOS[met]
        nomes = json.dumps(dict(zip(d["chave"], d["municipio_nome"])), ensure_ascii=False)
        tooltip = JS(f"""function(p){{
            var nomes = {nomes};
            var n = nomes[p.name] || p.name;
            if (p.value === undefined || p.value === null || isNaN(p.value))
              return '<b>' + n + '</b><br/>sem dado';
            return '<b>' + n + '</b><br/>{rotulo}: ' +
                   p.value.toLocaleString('pt-BR', {{maximumFractionDigits: 2}});
        }}""")
        vmax = float(d["valor"].max()) if d["valor"].notna().any() else 1.0
        return {
            "__mapa__": {"nome": "BASE_MUN", "url": MALHA_BASE_URL},
            "textStyle": {"fontFamily": FONTE_GRAFICO},
            "tooltip": {"trigger": "item", "formatter": tooltip},
            "toolbox": toolbox_salvar(),
            "visualMap": {"type": "continuous", "min": 0, "max": vmax or 1, "orient": "vertical",
                          "left": "left", "bottom": "6%", "calculable": True,
                          "inRange": {"color": RAMPA_MAPA},
                          "formatter": JS("function(v){return v.toLocaleString('pt-BR',{maximumFractionDigits:1});}")},
            "series": [{
                "type": "map", "map": "BASE_MUN", "name": rotulo,
                "nameProperty": cfg.GEO_NAME_PROPERTY, "roam": True,
                "itemStyle": {"borderColor": COR_BORDA_MUN, "borderWidth": 0.6},
                "label": {"show": False},
                "emphasis": {"itemStyle": {"areaColor": TONS_AZUL["300"]}, "label": {"show": False}},
                "data": [{"name": c, "value": v} for c, v in zip(d["chave"], d["valor"])]
                        + itens_contorno(),
            }],
        }

    # Top 10 pela metrica do mapa; municipios sem valor ficam de fora do ranking.
    @render.ui
    def tabela():
        req(input.metrica())
        met = input.metrica()
        d = dados_municipio()
        d = d[d[met].notna()].sort_values(met, ascending=False).head(10)
        if d.empty:
            return ui.p("Nenhum município com dado suficiente", class_="text-muted")
        colunas = [("semanas_rt_alto", "Semanas Rt > 1", lambda v: fmt_int(v)),
                   ("rt_max_atual", "Rt máximo", lambda v: fmt_num(v, 2)),
                   ("casos_totais", "Casos acumulados", lambda v: fmt_int(v))]
        cab = "".join(f"<th class='text-end'>{t}</th>" for _, t, _ in colunas)
        linhas = []
        for i, r in enumerate(d.itertuples(), start=1):
            cels = "".join(
                f"<td class='text-end{' fw-bold' if k == met else ''}'>{f(getattr(r, k))}</td>"
                for k, _, f in colunas)
            linhas.append(f"<tr><td>{i}</td><td>{r.municipio_nome}</td>"
                          f"<td class='small text-muted'>{r.regiao_nome}</td>{cels}</tr>")
        return HTML(
            f"<table class='table table-sm table-striped table-hover tabela-top10'>"
            f"<caption>{TITULOS[met]}</caption>"
            f"<thead><tr><th>#</th><th>Município</th><th>Região de Saúde</th>{cab}</tr></thead>"
            f"<tbody>{''.join(linhas)}</tbody></table>"
        )
