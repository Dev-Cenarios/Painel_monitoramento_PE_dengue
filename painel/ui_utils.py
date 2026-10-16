# ==============================================================================
# Dengue PE | painel/ui_utils.py
# Tema (Bootstrap 5 / Zephyr com a paleta Cenarios) e helpers de composicao de
# cards. Toda decisao de apresentacao mora aqui e no app.py; os modulos so
# expoem controles e saidas. Equivale ao R/utils_ui.R do RIDE.
# ==============================================================================

from __future__ import annotations

from htmltools import Tag, TagList, head_content, tags
from shiny import ui

from .config import PALETA_DENGUE as P

# Paleta: interface em azul Cenarios (moldura, cabecalhos, links), fundo
# cinza-azulado, cards brancos. A terracota fica para os dados de dengue e o
# vermelho `danger` para alertas - se a moldura tivesse a cor do dado, o alerta
# deixaria de se destacar. Tipografia: Poppins nos titulos, DM Sans no corpo.
TEMA = (
    ui.Theme("zephyr")
    .add_defaults(
        body_bg=P["fundo"],
        body_color=P["texto"],
        primary=P["azul_acao"],
        secondary=P["sub"],
        success=P["ok"],
        warning=P["atencao"],
        danger=P["critico"],
        info=P["azul"],
        font_family_sans_serif="'DM Sans', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
        headings_font_family="Poppins, system-ui, sans-serif",
        headings_color=P["azul_titulo"],
        link_color=P["azul_acao"],
        border_radius="0.65rem",
        border_radius_sm="0.45rem",
        card_border_radius="0.65rem",
        card_cap_padding_y="0.6rem",
        card_border_color=P["linha"],
        card_bg=P["superficie"],
        enable_shadows="true",
    )
    .add_rules(f"""
        .card-header.bg-primary {{ background-color: {P['azul_titulo']} !important; }}
        .card-header, .card-header .fw-semibold, .navbar-brand {{
          font-family: Poppins, system-ui, sans-serif;
        }}
        .value-box-value, .bslib-value-box .value-box-value, table td {{
          font-variant-numeric: tabular-nums;
        }}
        .value-box-value {{ font-weight: 700; }}
        .selectize-dropdown {{ z-index: 1080 !important; }}
        .dengue-echarts .ec-mensagem {{
          display: none; position: absolute; inset: 0; align-items: center;
          justify-content: center; text-align: center; padding: 1rem;
          color: {P['sub']}; font-size: .9rem;
        }}
        .tabela-top10 caption {{ caption-side: top; font-weight: 600; color: {P['azul_titulo']}; }}
    """)
)


def assets() -> TagList:
    """Fontes, ECharts (local, www/) e o JS do painel - injetados uma vez no topo."""
    return head_content(
        tags.link(rel="preconnect", href="https://fonts.googleapis.com"),
        tags.link(
            rel="stylesheet",
            href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700"
                 "&family=Poppins:wght@600;700&display=swap",
        ),
        tags.script(src="static/echarts.min.js"),
        tags.script(src="static/dengue.js"),
    )


def gatilho_opcoes(rotulo: str = "Opções") -> Tag:
    """Gatilho de popover: os controles saem do corpo do card para o cabecalho."""
    return tags.span(rotulo, class_="badge rounded-pill bg-light text-primary fw-normal",
                     style="cursor: pointer;")


def cartao_grafico(titulo: str, saida, controles=None, tom: str = "bg-primary") -> Tag:
    """Card padrao de grafico: cabecalho com titulo + popover de opcoes, corpo sem respiro."""
    cabecalho = [tags.span(titulo, class_="fw-semibold")]
    if controles is not None:
        cabecalho.append(ui.popover(gatilho_opcoes(), controles, title=titulo, placement="left"))
    return ui.card(
        ui.card_header(
            *cabecalho,
            class_=f"{tom} text-white d-flex justify-content-between align-items-center",
        ),
        ui.card_body(saida, class_="p-0"),
        full_screen=True,
    )
