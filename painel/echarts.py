# ==============================================================================
# Dengue PE | painel/echarts.py
# Saida ECharts para o Shiny for Python - o papel que o echarts4r cumpre no
# painel em R. O servidor monta o `option` do ECharts como dict Python; o
# navegador (www/dengue.js, binding "dengue.echarts") aplica com setOption.
#
# Convencoes do payload (tratadas no JS):
#   JS("function(v){...}")       funcao JavaScript (formatter, cor, max do eixo...)
#   "__mapa__": {nome, url}      registra a malha GeoJSON (baixada uma vez por pagina)
#   "__clique__": "<inputId>"    clique numa feicao/ponto vira input do Shiny
#   mensagem("texto")            no lugar do grafico, mostra um aviso (validate/need)
# ==============================================================================

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from htmltools import Tag, css
from shiny import ui
from shiny.module import resolve_id
from shiny.render.renderer import Jsonifiable, Renderer


class JS(str):
    """Trecho de codigo JavaScript que deve chegar ao navegador como funcao."""


def mensagem(texto: str) -> dict:
    return {"__mensagem__": texto}


def _limpar(x: Any) -> Any:
    """Converte o option em JSON puro: NaN/None -> null, numpy -> nativo, JS -> marcador."""
    if isinstance(x, JS):
        return {"__js__": str(x)}
    if isinstance(x, dict):
        return {str(k): _limpar(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_limpar(v) for v in x]
    if isinstance(x, (np.ndarray, pd.Series, pd.Index)):
        return [_limpar(v) for v in x.tolist()]
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        v = float(x)
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if x is pd.NaT or x is pd.NA:
        return None
    if isinstance(x, pd.Timestamp):
        return x.strftime("%Y-%m-%d")
    return x


def lista(s) -> list:
    """Coluna do pandas -> lista para o ECharts (NaN vira null no _limpar)."""
    return list(pd.Series(s).tolist())


def output_echarts(id: str, height: str = "400px") -> Tag:
    return ui.div(
        ui.div(class_="ec-grafico", style=css(width="100%", height="100%")),
        ui.div(class_="ec-mensagem"),
        id=resolve_id(id),
        class_="dengue-echarts",
        style=css(width="100%", height=height, position="relative"),
    )


class render_echarts(Renderer[dict]):
    """Decorador de saida: a funcao devolve o `option` (dict) do ECharts."""

    def auto_output_ui(self) -> Tag:
        return output_echarts(self.output_id)

    async def transform(self, value: dict) -> Jsonifiable:
        return _limpar(value)


# ----------------------------------------------------------- helpers comuns --
def toolbox_salvar() -> dict:
    return {"feature": {"saveAsImage": {"title": "Salvar imagem"}}, "right": 8}


def formato_data_js(escala: str = "dia") -> str:
    """Expressao JS (sobre a variavel `v`, texto "AAAA-MM-DD") que formata a data em pt-BR.

    Manipulacao de string, NAO new Date(v): "2024-01-05" seria lido como UTC e
    num navegador em UTC-3 viraria 04/01/2024.
    """
    if escala == "mes":
        return ("(function(s){var m=['jan','fev','mar','abr','mai','jun','jul','ago','set',"
                "'out','nov','dez'];var p=String(s).split('-');return m[parseInt(p[1],10)-1]+'/'+p[0];})(v)")
    return "(function(s){var p=String(s).split('-');return p[2]+'/'+p[1]+'/'+p[0];})(v)"
