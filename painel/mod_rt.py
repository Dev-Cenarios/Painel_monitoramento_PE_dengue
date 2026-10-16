# ==============================================================================
# Dengue PE | Modulo: Numero de Reproducao Efetivo (Rt)
#
# Linha de Rt com limiar em 1,0: acima de 1 a transmissao esta em expansao.
#
# AGREGACAO: `rt` e estimado POR MUNICIPIO pelo InfoDengue. Nao ha operacao
# aritmetica que combine Rt de municipios num Rt regional (seria preciso
# reestimar com a serie agregada). Entao:
#   - municipio        -> valor da coluna `rt`, sem transformacao;
#   - regiao / estado  -> media ponderada pelos casos (aproximacao, rotulada),
#     e o tooltip mostra quantos municipios estao com Rt > 1 (contraprova).
# Sempre semanal: Rt mensal misturaria estimativas de fases distintas do surto.
# ==============================================================================

from __future__ import annotations

import json

import pandas as pd
from htmltools import tags
from shiny import module, reactive, render, req, ui

from .config import FONTE_GRAFICO, PALETA_DENGUE
from .dados import calcular_indicadores
from .echarts import JS, formato_data_js, lista, mensagem, output_echarts, render_echarts, toolbox_salvar

# Rt tem cauda longa em municipios pequenos; o teto evita que uma semana com
# Rt = 40 achate visualmente o resto.
RT_TETO_VISUAL = 5


@module.ui
def rt_controles():
    return ui.TagList(
        ui.input_checkbox("limitar_escala", f"Limitar eixo a Rt = {RT_TETO_VISUAL}", value=True),
        ui.tags.hr(),
        ui.output_ui("nota_metodo"),
    )


@module.ui
def rt_saida(height: str = "420px"):
    return output_echarts("grafico", height=height)


@module.server
def rt_server(input, output, session, geo, serie_semanal):

    @reactive.calc
    def dados():
        return calcular_indicadores(serie_semanal(), geo())

    @reactive.calc
    def agregado():
        return geo().nivel != "municipio"

    @render.ui
    def nota_metodo():
        if agregado():
            corpo = [tags.strong("Rt agregado."), tags.br(),
                     "Média ponderada pelos casos de cada município. Aproximação: "
                     "Rt não é aditivo entre áreas."]
        else:
            corpo = [tags.strong("Rt municipal."), tags.br(),
                     "Valor direto da coluna ", tags.code("rt"), " do InfoDengue."]
        return tags.div(*corpo, class_="small text-muted")

    @render_echarts
    def grafico():
        d = dados()
        req(len(d) > 0)
        if not d["rt"].notna().any():
            return mensagem("Sem estimativas de Rt disponíveis para este recorte.")

        fmt_data = formato_data_js("dia")
        tabela = [
            {"rt": None if pd.isna(r) else round(r, 2), "n_mun": int(n), "n_mun_rt1": int(n1),
             "casos": float(c)}
            for r, n, n1, c in zip(d["rt"], d["n_mun"], d["n_mun_rt1"], d["casos"])
        ]
        tooltip = JS(f"""function(params){{
            if (!params || !params.length) return '';
            var tab = {json.dumps(tabela)};
            var r = tab[params[0].dataIndex];
            var v = params[0].axisValue;
            if (!r) return '';
            function f(x, d){{ return (x === null || x === undefined) ? '—'
              : x.toLocaleString('pt-BR', {{maximumFractionDigits: d}}); }}
            var cor = (r.rt !== null && r.rt > 1) ? '{PALETA_DENGUE["dengue_forte"]}' : '{PALETA_DENGUE["azul_titulo"]}';
            var txt = '<b>' + {fmt_data} + '</b><br/>' +
                      'Rt: <b style="color:' + cor + '">' + f(r.rt, 2) + '</b><br/>' +
                      'Casos na semana: ' + f(r.casos, 0);
            if ({'true' if agregado() else 'false'}) {{
              txt += '<br/><span style="opacity:.7">' + f(r.n_mun_rt1, 0) +
                     ' de ' + f(r.n_mun, 0) + ' municípios com Rt > 1</span>';
            }}
            return txt;
        }}""")

        teto = RT_TETO_VISUAL if input.limitar_escala() else max(float(d["rt"].max()), 1.5)
        return {
            "textStyle": {"fontFamily": FONTE_GRAFICO},
            "xAxis": {"type": "category", "data": lista(d["data_ini_se"].dt.strftime("%Y-%m-%d")),
                      "axisLabel": {"formatter": JS(f"function(v){{ return {fmt_data}; }}")}},
            "yAxis": {"type": "value", "name": "Rt", "nameLocation": "end", "nameGap": 12,
                      "min": 0, "max": round(teto, 2)},
            "dataZoom": [{"type": "slider", "xAxisIndex": 0, "bottom": 4}],
            "legend": {"top": 0, "type": "scroll"},
            "grid": {"left": 60, "right": 24, "top": 40, "bottom": 78},
            "tooltip": {"trigger": "axis", "formatter": tooltip},
            "toolbox": toolbox_salvar(),
            "series": [
                {"type": "line", "name": "Rt", "data": lista(d["rt"].round(3)), "symbol": "none",
                 "lineStyle": {"color": PALETA_DENGUE["dengue"], "width": 2},
                 "itemStyle": {"color": PALETA_DENGUE["dengue"]}},
                # Limiar como serie constante: aparece na legenda.
                {"type": "line", "name": "Limiar (Rt = 1)", "data": [1] * len(d), "symbol": "none",
                 "silent": True, "tooltip": {"show": False},
                 "lineStyle": {"color": PALETA_DENGUE["tinta"], "width": 1.5, "type": "dashed"},
                 "itemStyle": {"color": PALETA_DENGUE["tinta"]}},
            ],
        }
