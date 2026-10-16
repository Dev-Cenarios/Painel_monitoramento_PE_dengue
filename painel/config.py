# ==============================================================================
# Dengue PE | painel/config.py
# Constantes do painel: caminhos, identidade visual (paleta Cenarios, igual a
# dos paineis dengue_df / dengue_ride / PVdengue_df), escala de incidencia e
# regras do ano sanitario. Equivale a parte de constantes do global.R do RIDE.
# ==============================================================================

from __future__ import annotations

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

# ------------------------------------------------------------ fonte de dados --
PARQUET_DENGUE = Path(os.environ.get("DENGUE_PARQUET", RAIZ / "data" / "dengue_pe.parquet"))
REGIOES_CSV = Path(os.environ.get("DENGUE_REGIOES_CSV", RAIZ / "data" / "regioes_saude_pe.csv"))
GEO_DIR = Path(os.environ.get("DENGUE_GEO_DIR", RAIZ / "data" / "geo"))

# Propriedade do GeoJSON usada como chave de juncao com os dados ("codarea" e
# o geocodigo IBGE, padrao da API de malhas do IBGE).
GEO_NAME_PROPERTY = os.environ.get("DENGUE_GEO_NAME_PROP", "codarea")

# Pasta de caches em disco (malha recortada, previsao). No servidor e criada
# com dono shiny, fora do git - mesma convencao dos paineis em R.
APP_CACHE = Path(os.environ.get("DENGUE_APP_CACHE", RAIZ / "app_cache"))

# Rotulo do nivel agregado (a base inteira) e do nivel intermediario.
ROTULO_BASE = os.environ.get("DENGUE_ROTULO_BASE", "Pernambuco")
ROTULO_REGIAO = "Região de Saúde"

# ------------------------------------------------------------- paleta dengue --
# Identidade visual comum aos paineis, baseada no guia Cenarios+: azul
# Cenarios na interface e rampa terracota para a dengue. Mesmos valores do
# global.R do dengue_ride.
#
# Regras de uso:
#   - magnitude (incidencia, casos)   -> TONS_DENGUE, claro = pouco, escuro = muito
#   - polaridade (Rt, razao historica) -> azul abaixo do limiar, terracota acima
#   - estado (alerta, obito)           -> ok/atencao/grave/critico, sempre com rotulo
#   - categorias (modelos, classes)    -> SERIES_CENARIOS, na ordem, sem reciclar
TONS_DENGUE = {
    "50": "#FFF2EB", "100": "#FDDDCF", "200": "#F7C0A8", "300": "#ED9E7C",
    "400": "#D97F55", "500": "#C0663C", "600": "#A34F25", "700": "#853B15",
    "800": "#682B0A", "900": "#4E1D03",
}

TONS_AZUL = {
    "50": "#ECF7FE", "100": "#CFE9F9", "200": "#A8D5F1", "300": "#7ABEE6",
    "400": "#4FA4D3", "500": "#3891C0", "600": "#0E7BA8", "700": "#00638C",
    "800": "#004565", "900": "#00324C",
}

PALETA_DENGUE = {
    "fundo": "#F2F6F9",
    "superficie": "#FFFFFF",
    "linha": "#D8E3EA",
    "tinta": "#12222B",   # titulos de grafico, numeros, serie em foco
    "texto": "#4A4A4A",   # corpo
    "sub": "#7A8B95",     # eixos, legendas, linhas de referencia
    "neutro": "#B8C4CC",  # "sem dado", "ignorado"
    "azul": TONS_AZUL["500"],
    "azul_acao": TONS_AZUL["600"],
    "azul_titulo": TONS_AZUL["700"],
    "dengue": TONS_DENGUE["500"],
    "dengue_forte": TONS_DENGUE["700"],
    "ok": "#17895B",
    "atencao": "#DCA744",
    "grave": "#C65D26",
    "critico": "#8F2639",
}

SERIES_CENARIOS = ["#3891C0", "#C0663C", "#6C5CA8", "#5A9E2E",
                   "#8E3B46", "#B08A1E", "#3F4E9E", "#1B9AAA"]

# Rampa continua da dengue (visualMap continuo).
RAMPA_DENGUE = [TONS_DENGUE[k] for k in ("50", "200", "300", "500", "700", "900")]

# Fonte dos graficos (o ECharts nao herda a fonte do CSS da pagina).
FONTE_GRAFICO = "'DM Sans', system-ui, sans-serif"


def cor_alpha(hexcor: str, alpha: float) -> str:
    """"#RRGGBB" -> "rgba(r,g,b,a)"."""
    h = hexcor.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


# ------------------------------------------------- escala de incidencia -----
# Cortes usados pelo Ministerio da Saude (casos/100 mil hab.): < 100 baixa,
# 100-299 media, >= 300 alta; os dois ultimos niveis separam situacoes
# epidemicas que a faixa ">= 300" sozinha achataria.
#
# Cores dos MAPAS: escala amarelo -> marrom (ColorBrewer YlOrBr, 5 classes),
# escolhida pelo usuario para o painel PE. Vale para o mapa de incidencia,
# o mapa da aba Analises e o heatmap de sazonalidade (mesma escala, para as
# leituras continuarem comparaveis).
ESCALA_MAPA = ["#ffffd4", "#fed98e", "#fe9929", "#d95f0e", "#993404"]
RAMPA_MAPA = ESCALA_MAPA

PECAS_INCIDENCIA = [
    {"min": 1000, "label": "≥ 1.000", "color": ESCALA_MAPA[4]},
    {"min": 500, "max": 1000, "label": "500 – 999", "color": ESCALA_MAPA[3]},
    {"min": 300, "max": 500, "label": "300 – 499", "color": ESCALA_MAPA[2]},
    {"min": 100, "max": 300, "label": "100 – 299", "color": ESCALA_MAPA[1]},
    {"min": 0.01, "max": 100, "label": "< 100", "color": ESCALA_MAPA[0]},
    {"value": 0, "label": "Sem casos", "color": PALETA_DENGUE["fundo"]},
]

# ------------------------------------------------- ano sanitario (jul-jun) ---
# A temporada comeca na SE 27 (inicio da baixa transmissao) e termina na SE 26
# do ano seguinte: o pico do verao nunca e partido ao meio pela virada do ano.
SEMANA_CORTE_SANITARIA = 27

# Ordem das semanas no eixo dos graficos por temporada: 27..52, 1..26.
NIVEIS_SEMANA_SANITARIA = list(range(SEMANA_CORTE_SANITARIA, 53)) + list(
    range(1, SEMANA_CORTE_SANITARIA)
)


def ano_sanitario(ano, semana):
    """Temporada de uma SE: semanas < 27 pertencem a temporada do ano anterior.

    Aceita escalares ou Series do pandas.
    """
    return ano - (semana < SEMANA_CORTE_SANITARIA).astype(int) if hasattr(semana, "astype") \
        else ano - int(semana < SEMANA_CORTE_SANITARIA)


def rotulo_ano_sanitario(ano_san: int) -> str:
    return f"{int(ano_san)}/{int(ano_san) + 1}"


# ------------------------------------------------------------ formatacao ----
def fmt_int(x) -> str:
    """12345 -> "12.345"."""
    try:
        if x is None or x != x:
            return "-"
        return f"{round(float(x)):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return "-"


def fmt_num(x, digits: int = 1) -> str:
    """1234.56 -> "1.234,6"."""
    try:
        if x is None or x != x:
            return "-"
        s = f"{float(x):,.{digits}f}"
        return s.replace(",", "§").replace(".", ",").replace("§", ".")
    except (TypeError, ValueError):
        return "-"
