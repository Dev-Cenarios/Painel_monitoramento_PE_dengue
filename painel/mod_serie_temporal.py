# ==============================================================================
# Dengue PE | Modulo: Serie Temporal
#
# Linha do notificado contra a do nowcast, ao longo de data_ini_se. Controles:
# agrupamento (semana epidemiologica ou mes) e metrica (casos ou incidencia).
# A serie inteira vai para o navegador e a navegacao e no dataZoom.
# ==============================================================================

from __future__ import annotations

from shiny import module, reactive, req, ui

from .config import FONTE_GRAFICO, PALETA_DENGUE, SERIES_CENARIOS
from .dados import calcular_indicadores, reagrupar_periodo
from .echarts import JS, formato_data_js, lista, output_echarts, render_echarts, toolbox_salvar


@module.ui
def serie_temporal_controles():
    return ui.TagList(
        ui.input_radio_buttons("agrupamento", "Agrupamento",
                               choices={"semana": "Semana epidemiológica", "mes": "Mensal"},
                               selected="semana", inline=True),
        ui.input_radio_buttons("metrica", "Métrica",
                               choices={"casos": "Casos", "incidencia": "Incidência /100 mil"},
                               selected="casos", inline=True),
        ui.input_checkbox("mostrar_est", "Mostrar nowcast", value=True),
    )


@module.ui
def serie_temporal_saida(height: str = "420px"):
    return output_echarts("grafico", height=height)


def tooltip_eixo(fmt_data: str, casas: int) -> JS:
    return JS(f"""function(params){{
        if (!params || !params.length) return '';
        var v = params[0].axisValue;
        var txt = '<b>' + {fmt_data} + '</b>';
        params.forEach(function(p){{
          var val = (p.value === null || p.value === undefined) ? '—'
            : p.value.toLocaleString('pt-BR', {{maximumFractionDigits: {casas}}});
          txt += '<br/>' + p.marker + p.seriesName + ': <b>' + val + '</b>';
        }});
        return txt;
    }}""")


@module.server
def serie_temporal_server(input, output, session, geo, serie_semanal):

    @reactive.calc
    def dados():
        return calcular_indicadores(reagrupar_periodo(serie_semanal(), input.agrupamento()), geo())

    @render_echarts
    def grafico():
        d = dados()
        req(len(d) > 0)
        por_inc = input.metrica() == "incidencia"
        obs = d["incidencia"] if por_inc else d["casos"]
        est = d["incidencia_est"] if por_inc else d["casos_est"]
        casas = 1 if por_inc else 0
        fmt_data = formato_data_js("mes" if input.agrupamento() == "mes" else "dia")

        series = [{
            "type": "line", "name": "Incidência notificada" if por_inc else "Casos notificados",
            "data": lista(obs.round(casas)), "symbol": "none",
            "lineStyle": {"color": PALETA_DENGUE["dengue"], "width": 2},
            "itemStyle": {"color": PALETA_DENGUE["dengue"]},
        }]
        if input.mostrar_est():
            series.append({
                "type": "line", "name": "Incidência (nowcast)" if por_inc else "Casos estimados (nowcast)",
                "data": lista(est.round(casas)), "symbol": "none",
                "lineStyle": {"color": SERIES_CENARIOS[2], "width": 2, "type": "dashed"},
                "itemStyle": {"color": SERIES_CENARIOS[2]},
            })

        return {
            "textStyle": {"fontFamily": FONTE_GRAFICO},
            # Eixo categorico com texto "AAAA-MM-DD" (ver formato_data_js).
            "xAxis": {"type": "category", "data": lista(d["periodo"].dt.strftime("%Y-%m-%d")),
                      "axisLabel": {"formatter": JS(f"function(v){{ return {fmt_data}; }}")}},
            "yAxis": {"type": "value", "name": "/100 mil" if por_inc else "casos",
                      "nameLocation": "end", "nameGap": 12},
            "dataZoom": [{"type": "slider", "xAxisIndex": 0, "bottom": 4}],
            "legend": {"top": 0, "type": "scroll"},
            "grid": {"left": 70, "right": 24, "top": 40, "bottom": 78},
            "tooltip": {"trigger": "axis", "formatter": tooltip_eixo(fmt_data, casas)},
            "toolbox": toolbox_salvar(),
            "series": series,
        }
