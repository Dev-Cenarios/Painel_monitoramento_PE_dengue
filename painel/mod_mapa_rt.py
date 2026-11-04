# ==============================================================================
# Dengue PE | Modulo: Tendencia (quadrantes) por municipio
#
# Eixo X = casos do municipio na janela atual / mediana historica do municipio
#          NESSA MESMA janela de semanas, em anos anteriores.
# Eixo Y = casos da janela atual / casos da janela imediatamente anterior.
# Referencia 1 em ambos: "normal para a epoca" (X) e "estavel" (Y). Forma =
# incidencia acumulada no ano corrente ate a semana de referencia.
#
# NOMENCLATURA: chamado de "Rt" no titulo (como no RIDE), mas NAO e o numero
# de reproducao efetivo - e um indicador de ANOMALIA SAZONAL + TENDENCIA para
# comparar os municipios entre si. O Rt verdadeiro esta em mod_rt e nos KPIs.
#
# Por design IGNORA o recorte global: o valor e comparar municipios entre si.
# O municipio em foco (selected_location) e destacado; os demais ficam
# esmaecidos. Municipios "sem historico suficiente" ficam ocultos por padrao.
# Com 185 municipios, rotulos que se sobrepoem sao escondidos (hideOverlap).
#
# BORDAS: todo ponto tem contorno cinza claro (mesmo tom das bordas do mapa),
# para separar pontos sobrepostos e os de cor clara do fundo.
#
# TRILHA: o municipio em foco mostra onde estava nas semanas anteriores
# (N_SEMANAS_TRILHA posicoes, contando a atual): o quadro e recalculado com a
# base cortada uma, duas e tres semanas antes, e os pontos sao ligados por
# setas ate a posicao atual. Semana sem posicao valida (sem historico) fica
# fora da trilha.
# ==============================================================================

from __future__ import annotations

import json
import time
from functools import lru_cache

import numpy as np
import pandas as pd
from shiny import module, reactive, req, ui

from .config import FONTE_GRAFICO, PALETA_DENGUE, TONS_AZUL, TONS_DENGUE, cor_alpha
from .dados import REGIAO_DO_MUNICIPIO, SERIE_MUNICIPIOS
from .echarts import JS, mensagem, output_echarts, render_echarts

# Limiares de incidencia ACUMULADA no ano (definem forma, nao cor: a cor fica
# para o quadrante, que e a leitura principal).
LIMIARES_FORMA = [
    {"id": "triangle", "teto": 10, "simbolo": "triangle", "rotulo": "▲ < 10 /100k"},
    {"id": "diamond", "teto": 50, "simbolo": "diamond", "rotulo": "◆ 10–50 /100k"},
    {"id": "circle", "teto": 300, "simbolo": "circle", "rotulo": "● 50–300 /100k"},
    {"id": "rect", "teto": float("inf"), "simbolo": "rect", "rotulo": "■ > 300 /100k"},
]

# Minimo de anos historicos na janela para a mediana entrar no eixo X.
MIN_ANOS_HISTORICO_RT = 2

SIMBOLO_FORMA = {f["id"]: f["simbolo"] for f in LIMIARES_FORMA}

# Contorno dos pontos: o mesmo cinza do contorno do estado no mapa.
COR_BORDA_PONTO = "#94A3AC"
LARGURA_BORDA_PONTO = 0.8

# Posicoes na trilha do municipio em foco, contando a semana atual.
N_SEMANAS_TRILHA = 4
TAMANHO_MAX_FOCO = 26

# Escala divergente: abaixo do normal em azul, acima em terracota; dentro de
# cada lado, o tom mais forte e o pior cenario.
CORES_QUADRANTE = {
    "abaixo_estavel": TONS_AZUL["700"],
    "abaixo_crescendo": TONS_AZUL["400"],
    "acima_desacelerando": TONS_DENGUE["400"],
    "acima_crescendo": TONS_DENGUE["700"],
    "sem_base": PALETA_DENGUE["sub"],
}
ROTULOS_QUADRANTE = {
    "abaixo_estavel": "Abaixo do normal · caindo",
    "abaixo_crescendo": "Abaixo do normal · subindo",
    "acima_desacelerando": "Acima do normal · caindo",
    "acima_crescendo": "Acima do normal · subindo",
    "sem_base": "Sem histórico suficiente",
}


@lru_cache(maxsize=64)
def calcular_quadro_tendencia(janela: int = 4, ate: pd.Timestamp | None = None) -> dict:
    """Uma linha por municipio, pronta para o grafico.

    `ate`: corta a base nessa semana (inclusive) - e assim que a trilha
    calcula a posicao das semanas anteriores. None = base inteira.

    LIMITACAO CONHECIDA (igual ao RIDE): o casamento com o historico usa o
    numero da semana; se a janela cruzar a virada do ano, os anos historicos
    ficam levemente deslocados perto da fronteira.
    """
    s = SERIE_MUNICIPIOS
    if ate is not None:
        s = s[s["data_ini_se"] <= ate]
    data_max = s["data_ini_se"].max()
    ano_recente = data_max.year
    se_ref_semana = int(s.loc[s["data_ini_se"] == data_max, "semana"].iloc[0])

    atual_fim = data_max
    atual_ini = atual_fim - pd.Timedelta(weeks=janela - 1)
    ant_fim = atual_ini - pd.Timedelta(weeks=1)
    ant_ini = ant_fim - pd.Timedelta(weeks=janela - 1)

    na_atual = (s["data_ini_se"] >= atual_ini) & (s["data_ini_se"] <= atual_fim)
    janela_atual = s[na_atual].groupby("municipio_geocodigo").agg(
        casos_atual=("casos", "sum"), pop=("pop", "max"))
    janela_anterior = (s[(s["data_ini_se"] >= ant_ini) & (s["data_ini_se"] <= ant_fim)]
                       .groupby("municipio_geocodigo").agg(casos_anterior=("casos", "sum")))

    semanas_da_janela = set(s.loc[na_atual, "semana"])
    historico = (s[s["semana"].isin(semanas_da_janela) & (s["ano"] != ano_recente)]
                 .groupby(["municipio_geocodigo", "ano"])["casos"].sum()
                 .groupby("municipio_geocodigo")
                 .agg(mediana_historica="median", n_anos_historico="size"))

    acumulado = (s[(s["ano"] == ano_recente) & (s["semana"] <= se_ref_semana)]
                 .groupby("municipio_geocodigo").agg(casos_acumulados=("casos", "sum"),
                                                     pop_ref=("pop", "max")))
    acumulado["incidencia_acumulada"] = np.where(
        acumulado["pop_ref"] > 0, acumulado["casos_acumulados"] / acumulado["pop_ref"] * 1e5, np.nan)

    d = (s[["municipio_geocodigo", "municipio_nome"]].drop_duplicates().set_index("municipio_geocodigo")
         .join(janela_atual).join(janela_anterior).join(historico).join(acumulado).reset_index())
    d["casos_atual"] = d["casos_atual"].fillna(0)
    d["casos_anterior"] = d["casos_anterior"].fillna(0)
    d["sem_base"] = ((d["casos_anterior"] == 0) | d["mediana_historica"].isna()
                     | (d["mediana_historica"] == 0)
                     | (d["n_anos_historico"].fillna(0) < MIN_ANOS_HISTORICO_RT))
    d["taxa_cresc"] = np.where(d["casos_anterior"] > 0,
                               d["casos_atual"] / d["casos_anterior"].where(d["casos_anterior"] > 0), np.nan)
    d["x_val"] = np.where(d["mediana_historica"] > 0,
                          d["casos_atual"] / d["mediana_historica"].where(d["mediana_historica"] > 0), np.nan)

    def forma(inc):
        if pd.isna(inc):
            return LIMIARES_FORMA[0]["id"]
        for f in LIMIARES_FORMA:
            if inc < f["teto"]:
                return f["id"]
        return LIMIARES_FORMA[-1]["id"]

    def quadrante(r):
        if r.sem_base:
            return "sem_base"
        if r.x_val < 1:
            return "abaixo_estavel" if r.taxa_cresc < 1 else "abaixo_crescendo"
        return "acima_desacelerando" if r.taxa_cresc < 1 else "acima_crescendo"

    d["forma"] = d["incidencia_acumulada"].map(forma)
    d["quadrante"] = [quadrante(r) for r in d.itertuples()]

    return {
        "dados": d, "ano_recente": ano_recente, "se_ref_semana": se_ref_semana,
        "atual_ini": f"{atual_ini:%d/%m}", "atual_fim": f"{atual_fim:%d/%m}",
        "ant_ini": f"{ant_ini:%d/%m}", "ant_fim": f"{ant_fim:%d/%m}",
    }


@lru_cache(maxsize=16)
def calcular_trilha(janela: int = 4) -> list[dict]:
    """Posicao de cada municipio nas semanas anteriores, da mais antiga a mais recente.

    Cada item: {"semana": "SE 37", "pos": {geocodigo: (x, y)}} - so municipios
    com posicao valida (fora da faixa "sem historico") naquela semana.
    """
    data_max = SERIE_MUNICIPIOS["data_ini_se"].max()
    passos = []
    for k in range(N_SEMANAS_TRILHA - 1, 0, -1):
        ate = data_max - pd.Timedelta(weeks=k)
        d = calcular_quadro_tendencia(janela, ate)["dados"]
        ok = d[(d["quadrante"] != "sem_base") & d["x_val"].notna() & d["taxa_cresc"].notna()]
        sem = int(SERIE_MUNICIPIOS.loc[SERIE_MUNICIPIOS["data_ini_se"] == ate, "semana"].iloc[0])
        passos.append({"semana": f"SE {sem:02d}",
                       "pos": {int(r.municipio_geocodigo): (float(r.x_val), float(r.taxa_cresc))
                               for r in ok.itertuples()}})
    return passos


def _encurtar(a, b, f):
    """Ponto a f do caminho de a ate b: a seta para antes do alvo e nao some sob ele."""
    return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f]


def series_trilha(cod: int, atual: tuple[float, float] | None, se_atual: str, nome: str,
                  janela: int) -> list[dict]:
    """Setas + pontos esmaecidos das semanas anteriores do municipio em foco."""
    pontos = [(p["semana"], p["pos"][cod]) for p in calcular_trilha(janela) if cod in p["pos"]]
    if atual is not None:
        pontos.append((se_atual, atual))
    if len(pontos) < 2:
        return []
    tinta = PALETA_DENGUE["tinta"]

    def tt(rotulo, xy):
        return (f"<b>{nome}</b> · {rotulo}<br/>Vs. mediana histórica: <b>{xy[0]:.2f}x</b>"
                f"<br/>Vs. janela anterior: <b>{xy[1]:.2f}x</b>").replace(".", ",")

    return [
        {"name": "__trilha__", "type": "lines", "coordinateSystem": "cartesian2d", "silent": True,
         "z": 3, "symbol": ["none", "arrow"], "symbolSize": 9,
         "lineStyle": {"color": tinta, "width": 1.6, "type": "solid", "opacity": 0.75},
         "data": [{"coords": [list(a[1]), _encurtar(a[1], b[1], 0.82 if i == len(pontos) - 2 else 0.9)]}
                  for i, (a, b) in enumerate(zip(pontos, pontos[1:]))]},
        {"name": "__trilha__", "type": "scatter", "z": 3, "symbol": "circle", "symbolSize": 9,
         "itemStyle": {"color": "#FFFFFF", "borderColor": tinta, "borderWidth": 1.5, "opacity": 0.95},
         "label": {"show": True, "position": "bottom", "fontSize": 9, "color": PALETA_DENGUE["sub"],
                   "formatter": JS("function(p){ return p.data.se; }")},
         "data": [{"value": [round(xy[0], 3), round(xy[1], 3)], "se": se, "tt": tt(se, xy),
                   "name": nome, "cod": str(cod)} for se, xy in pontos[:-1]]},
    ]


@module.ui
def mapa_rt_controles():
    return ui.TagList(
        ui.input_slider("janela", "Janela de comparação (semanas)", min=1, max=8, value=4, step=1),
        # Desligado por padrao: sem historico, os eixos nao tem denominador e
        # esses municipios ficariam numa faixa artificial no topo do grafico.
        ui.input_checkbox("mostrar_sem_base", "Mostrar municípios sem histórico suficiente", value=False),
        ui.help_text(ui.tags.small(
            "Eixo X: casos da janela atual vs. mediana histórica da mesma janela (outros anos). "
            "Eixo Y: janela atual vs. janela anterior (mesmo ano). Cor = quadrante; forma = "
            "incidência acumulada no ano até a semana mais recente."
        )),
    )


@module.ui
def mapa_rt_saida(height: str = "520px"):
    return output_echarts("grafico", height=height)


def _num(v, casas):
    return None if v is None or pd.isna(v) else round(float(v), casas)


@module.server
def mapa_rt_server(input, output, session, selected_location):

    # Clique num ponto: mesmo pedido de foco do clique no mapa (o app aplica o
    # recorte ao municipio e o destaca aqui e no mapa).
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

    @reactive.calc
    def quadro():
        req(input.janela())
        return calcular_quadro_tendencia(int(input.janela()))

    @render_echarts
    def grafico():
        q = quadro()
        d = q["dados"].copy()
        if d.empty:
            return mensagem("Sem dados suficientes para montar o painel.")

        d["rotulo_curto"] = d["municipio_nome"].str.split(" ").str[0]
        sl = selected_location()
        sel_cod = sl["geocodigo"] if sl and sl.get("nivel") == "municipio" else None
        has_sel = sel_cod is not None
        cor_destaque = PALETA_DENGUE["tinta"]

        # Tamanho da bolha: raiz dos casos, reescalada para 14-46 px.
        raiz = np.sqrt(d["casos_atual"].clip(lower=0))
        amp = raiz.max() - raiz.min()
        d["bolha"] = 24.0 if not amp or pd.isna(amp) else 14 + (raiz - raiz.min()) / amp * 32

        def ponto(r, x, y, cor, sem_base=False):
            sel = has_sel and r.municipio_geocodigo == sel_cod
            if sem_base:
                opac = 1 if sel else (0.25 if has_sel else 0.5)
                borda, larg = (PALETA_DENGUE["superficie"], 2) if sel else (CORES_QUADRANTE["sem_base"], 1.5)
                cor_rot = PALETA_DENGUE["tinta"] if sel else PALETA_DENGUE["sub"]
            else:
                opac = 0.3 if has_sel and not sel else 1
                borda, larg = ((PALETA_DENGUE["superficie"], 2) if sel
                               else (COR_BORDA_PONTO, LARGURA_BORDA_PONTO))
                cor_rot = PALETA_DENGUE["tinta"]
            item = {
                "value": [x, y],
                # Em foco: maior que o normal, mas com teto - senao os municipios
                # grandes (Recife) cobrem a propria trilha.
                "symbolSize": min(r.bolha * 1.7, TAMANHO_MAX_FOCO) if sel else r.bolha,
                "name": r.municipio_nome,
                "cod": str(r.municipio_geocodigo),   # clique -> recorte do painel
                "itemStyle": {"color": cor_destaque if sel else cor, "opacity": opac,
                              "borderColor": borda, "borderWidth": larg},
                "label": {
                    "show": True, "position": "top",
                    "formatter": r.municipio_nome if sel else r.rotulo_curto,
                    "fontSize": 13 if sel else 9, "fontWeight": "bold" if sel else "normal",
                    "color": cor_rot, "opacity": 0.35 if has_sel and not sel else 1,
                    "backgroundColor": "rgba(255,255,255,0.92)" if sel else "transparent",
                    "padding": [2, 5, 2, 5] if sel else 0, "borderRadius": 3 if sel else 0,
                },
            }
            if sem_base:   # mantem a forma da incidencia acumulada tambem nesta faixa
                item["symbol"] = SIMBOLO_FORMA[r.forma]
            return item

        # Uma serie por FORMA, sempre as quatro - mesmo vazia, a forma fica na
        # legenda (que tambem filtra). Cor da serie em cinza: o icone da legenda
        # fala so da forma; a cor de cada ponto (quadrante) vem do proprio ponto.
        series = []
        for f in LIMIARES_FORMA:
            sub = d[(d["forma"] == f["id"]) & d["x_val"].notna() & d["taxa_cresc"].notna()
                    & (d["quadrante"] != "sem_base")]
            series.append({
                "name": f["rotulo"], "type": "scatter", "symbol": f["simbolo"],
                "itemStyle": {"color": PALETA_DENGUE["sub"]},
                "data": [ponto(r, round(r.x_val, 3), round(r.taxa_cresc, 3), CORES_QUADRANTE[r.quadrante])
                         for r in sub.itertuples()],
                "labelLayout": {"hideOverlap": True},
                "emphasis": {"itemStyle": {"shadowBlur": 10,
                                           "shadowColor": cor_alpha(PALETA_DENGUE["tinta"], 0.35)}},
            })

        # "Sem base": ocultos por padrao; o municipio em foco aparece mesmo assim.
        e_sem_base = d["quadrante"] == "sem_base"
        sel_sem_base = (d["municipio_geocodigo"] == sel_cod) if has_sel else False
        sem_base = d[e_sem_base & (input.mostrar_sem_base() | sel_sem_base)]
        n_ocultos = int(e_sem_base.sum()) - len(sem_base)
        ymax = d["taxa_cresc"].max()
        y_faixa = round((ymax if pd.notna(ymax) else 1) * 1.15 + 0.2, 2)
        if not sem_base.empty:
            series.append({
                "name": "Sem histórico suficiente", "type": "scatter", "symbol": "circle",
                "data": [ponto(r, round(r.x_val, 3) if pd.notna(r.x_val) else 0, y_faixa,
                               CORES_QUADRANTE["sem_base"], sem_base=True) for r in sem_base.itertuples()],
                "labelLayout": {"hideOverlap": True},
            })

        # Linhas de referencia (x = 1, y = 1) por serie invisivel + markLine.
        series.append({
            "name": "__ref__", "type": "scatter", "data": [], "silent": True,
            "tooltip": {"show": False},
            "markLine": {
                "silent": True, "symbol": ["none", "none"], "animation": False,
                "data": [
                    {"xAxis": 1, "lineStyle": {"color": PALETA_DENGUE["sub"], "type": "dashed", "width": 1.2},
                     "label": {"formatter": "Mediana histórica", "position": "insideEndTop",
                               "color": PALETA_DENGUE["sub"], "fontSize": 10}},
                    {"yAxis": 1, "lineStyle": {"color": PALETA_DENGUE["tinta"], "type": "dashed", "width": 1.5},
                     "label": {"formatter": "Estável (Y=1)", "position": "end",
                               "color": PALETA_DENGUE["tinta"], "fontSize": 10}},
                ],
            },
        })

        # Trilha das semanas anteriores do municipio em foco.
        if has_sel:
            r_sel = d[d["municipio_geocodigo"] == sel_cod]
            atual = None
            if len(r_sel) and r_sel["quadrante"].iloc[0] != "sem_base":
                atual = (float(r_sel["x_val"].iloc[0]), float(r_sel["taxa_cresc"].iloc[0]))
            nome_sel = r_sel["municipio_nome"].iloc[0] if len(r_sel) else ""
            series += series_trilha(int(sel_cod), atual, f"SE {q['se_ref_semana']:02d}", nome_sel,
                                    int(input.janela()))

        # Tooltip le de uma tabela por nome: a serie "sem base" usa y artificial.
        tabela = {
            r.municipio_nome: {"x": _num(r.x_val, 2), "y": _num(r.taxa_cresc, 2),
                               "quad": ROTULOS_QUADRANTE[r.quadrante], "casos": float(r.casos_atual),
                               "inc": _num(r.incidencia_acumulada, 1)}
            for r in d.itertuples()
        }
        tooltip = JS(f"""function(p){{
            if (p.seriesName === '__ref__') return '';
            if (p.data && p.data.tt) return p.data.tt;
            var tab = {json.dumps(tabela, ensure_ascii=False)};
            var r = tab[p.name];
            if (!r) return '';
            function f(v, d){{ return (v===null||v===undefined) ? '—' :
              v.toLocaleString('pt-BR', {{maximumFractionDigits: d}}); }}
            return '<b>' + p.name + '</b><br/>' + r.quad + '<br/>' +
                   'Vs. mediana histórica: <b>' + f(r.x, 2) + 'x</b><br/>' +
                   'Vs. janela anterior: <b>' + f(r.y, 2) + 'x</b><br/>' +
                   'Casos na janela: ' + f(r.casos, 0) + '<br/>' +
                   'Incidência acumulada: ' + f(r.inc, 1) + ' /100k';
        }}""")

        subtitulo = (f"Janela atual: {q['atual_ini']}–{q['atual_fim']} | Janela anterior: "
                     f"{q['ant_ini']}–{q['ant_fim']} | Incidência acumulada em {q['ano_recente']} "
                     f"até SE {q['se_ref_semana']:02d}")
        if n_ocultos > 0:
            subtitulo += f" | {n_ocultos} sem histórico suficiente (ocultos)"

        quadrantes = [k for k in CORES_QUADRANTE if k != "sem_base" or not sem_base.empty]
        y_max_minimo = round(y_faixa + 0.15, 2) if not sem_base.empty else 0

        return {
            "__clique__": session.ns("clique"),
            "textStyle": {"fontFamily": FONTE_GRAFICO},
            "title": {"text": f"Tendência por município — {int(input.janela())} semana(s)",
                      "subtext": subtitulo,
                      "textStyle": {"color": PALETA_DENGUE["tinta"], "fontSize": 14, "fontWeight": "bold"},
                      "subtextStyle": {"color": PALETA_DENGUE["sub"], "fontSize": 10}},
            "legend": [
                {"data": [f["rotulo"] for f in LIMIARES_FORMA], "top": 46, "left": "center",
                 "textStyle": {"color": PALETA_DENGUE["sub"], "fontSize": 10},
                 "itemWidth": 14, "itemHeight": 12, "itemGap": 14},
                {"data": [{"name": ROTULOS_QUADRANTE[k], "itemStyle": {"color": CORES_QUADRANTE[k]}}
                          for k in quadrantes],
                 "bottom": 6, "left": "center",
                 "textStyle": {"color": PALETA_DENGUE["tinta"], "fontSize": 10},
                 "itemWidth": 14, "itemHeight": 12, "itemGap": 12},
            ],
            "tooltip": {"trigger": "item", "formatter": tooltip, "backgroundColor": "#FFFFFF",
                        "borderColor": PALETA_DENGUE["linha"], "borderWidth": 1,
                        "textStyle": {"color": PALETA_DENGUE["tinta"]}},
            "xAxis": {
                "type": "value", "name": "Vs. mediana histórica (mediana = 1x)",
                "nameLocation": "middle", "nameGap": 32, "min": 0,
                "max": JS("function(v){ return Math.max(v.max*1.15, 1.3).toFixed(1); }"),
                "nameTextStyle": {"color": PALETA_DENGUE["sub"], "fontSize": 11},
                "axisLabel": {"color": PALETA_DENGUE["sub"], "fontSize": 10,
                              "formatter": JS("function(v){ return v.toFixed(1)+'x'; }")},
                "splitLine": {"lineStyle": {"color": PALETA_DENGUE["linha"], "type": "dashed"}},
            },
            "yAxis": {
                "type": "value", "name": "Vs. janela anterior (estável = 1x)",
                "nameLocation": "middle", "nameGap": 46, "min": 0,
                "max": JS(f"function(v){{ return Math.max(v.max*1.1, {y_max_minimo}, 1.3).toFixed(1); }}"),
                "nameTextStyle": {"color": PALETA_DENGUE["sub"], "fontSize": 11},
                "axisLabel": {"color": PALETA_DENGUE["sub"], "fontSize": 10,
                              "formatter": JS("function(v){ return v.toLocaleString('pt-BR',{minimumFractionDigits:1}); }")},
                "splitLine": {"lineStyle": {"color": PALETA_DENGUE["linha"], "type": "dashed"}},
            },
            "series": series,
            "dataZoom": [
                {"type": "inside", "xAxisIndex": 0, "filterMode": "none", "zoomOnMouseWheel": True},
                {"type": "inside", "yAxisIndex": 0, "filterMode": "none", "zoomOnMouseWheel": True},
            ],
            "grid": {"left": "9%", "right": "4%", "top": "26%", "bottom": "18%"},
        }

    return pedido_foco
