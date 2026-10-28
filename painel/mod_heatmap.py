# ==============================================================================
# Dengue PE | Modulo: Heatmap de sazonalidade
#
# Eixo X = semana epidemiologica (1-53), eixo Y = ano, cor = incidencia. A
# leitura util e vertical: em que semanas a transmissao sobe, e como essa
# janela se desloca entre os anos. Incidencia = sum(casos)/sum(pop)*1e5 na
# area inteira (nunca media de razoes entre municipios).
# ==============================================================================

from __future__ import annotations

import json

from shiny import module, reactive, req, ui

from .config import FONTE_GRAFICO, PECAS_INCIDENCIA, RAMPA_MAPA
from .dados import ANO_MAX, ANO_MIN, calcular_indicadores
from .echarts import JS, mensagem, output_echarts, render_echarts, toolbox_salvar


@module.ui
def heatmap_controles():
    return ui.TagList(
        ui.input_slider("anos", "Anos exibidos", min=ANO_MIN, max=ANO_MAX,
                        value=(max(ANO_MIN, ANO_MAX - 9), ANO_MAX), step=1, sep=""),
        # Padrao continuo: as faixas do MS sao para incidencia ACUMULADA no ano, e
        # a semanal de PE quase nunca passa de 100/100 mil - com faixas, tudo
        # cairia em "< 100" (heatmap inteiro numa cor so).
        ui.input_radio_buttons("escala", "Escala de cor",
                               choices={"continua": "Contínua", "faixas": "Faixas do MS"},
                               selected="continua", inline=True),
        ui.help_text(ui.tags.small(
            "As faixas do MS se referem à incidência acumulada no ano; aqui cada célula é uma "
            "semana, então quase todas ficam abaixo de 100 por 100 mil."
        )),
    )


@module.ui
def heatmap_saida(height: str = "480px"):
    return output_echarts("grafico", height=height)


@module.server
def heatmap_server(input, output, session, geo, serie_semanal):

    @reactive.calc
    def dados():
        anos = input.anos()
        req(anos)
        d = calcular_indicadores(serie_semanal(), geo())
        d["ano_num"] = d["se"] // 100
        d["semana_num"] = d["se"] % 100
        d = d[(d["semana_num"] >= 1) & (d["semana_num"] <= 53)
              & (d["ano_num"] >= anos[0]) & (d["ano_num"] <= anos[1])]
        return d.sort_values(["semana_num", "ano_num"])

    @render_echarts
    def grafico():
        d = dados()
        req(len(d) > 0)
        if not d["incidencia"].notna().any():
            return mensagem("Sem incidência calculável neste recorte (população ausente ou zerada).")

        semanas = [str(s) for s in sorted(d["semana_num"].unique())]
        anos = [str(a) for a in sorted(d["ano_num"].unique())]
        pontos = [[str(s), str(a), None if v != v else round(v, 1)]
                  for s, a, v in zip(d["semana_num"], d["ano_num"], d["incidencia"])]
        casos = {f"{a}-{s}": float(c) for s, a, c in zip(d["semana_num"], d["ano_num"], d["casos"])}

        tooltip = JS(f"""function(p){{
            var casos = {json.dumps(casos)};
            if (!p.value) return '';
            var semana = p.value[0], ano = p.value[1], inc = p.value[2];
            var c = casos[ano + '-' + semana];
            function f(x, d){{ return (x === null || x === undefined) ? '—'
              : x.toLocaleString('pt-BR', {{maximumFractionDigits: d}}); }}
            return '<b>SE ' + semana + ' / ' + ano + '</b><br/>' +
                   'Incidência: <b>' + f(inc, 1) + '</b> /100 mil<br/>Casos: ' + f(c, 0);
        }}""")

        if input.escala() == "faixas":
            visual = {"type": "piecewise", "pieces": PECAS_INCIDENCIA, "orient": "horizontal",
                      "left": "center", "bottom": 0, "itemWidth": 14, "itemHeight": 12, "dimension": 2}
        else:
            vmax = float(d["incidencia"].max())
            visual = {"type": "continuous", "min": 0, "max": vmax if vmax > 0 else 1,
                      "orient": "horizontal", "left": "center", "bottom": 0, "dimension": 2,
                      "calculable": True, "inRange": {"color": RAMPA_MAPA},
                      "formatter": JS("function(v){return v.toLocaleString('pt-BR',{maximumFractionDigits:0});}")}

        return {
            "textStyle": {"fontFamily": FONTE_GRAFICO},
            "xAxis": {"type": "category", "data": semanas, "name": "Semana epidemiológica",
                      "nameLocation": "middle", "nameGap": 28, "axisLabel": {"interval": 3},
                      "splitArea": {"show": True}},
            "yAxis": {"type": "category", "data": anos, "splitArea": {"show": True}},
            "visualMap": visual,
            "grid": {"left": 60, "right": 24, "top": 20, "bottom": 90},
            "tooltip": {"trigger": "item", "formatter": tooltip},
            "toolbox": toolbox_salvar(),
            "series": [{"type": "heatmap", "name": "Incidência /100 mil", "data": pontos,
                        "itemStyle": {"borderColor": "#FFFFFF", "borderWidth": 0.5}}],
        }
