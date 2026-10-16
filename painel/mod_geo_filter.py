# ==============================================================================
# Dengue PE | Modulo: filtro geografico hierarquico
# Pernambuco -> Regiao de Saude (GERES) -> Municipio. Unica fonte de verdade do
# recorte espacial. Equivale ao R/mod_geo_filter.R do RIDE, com a Regiao de
# Saude no lugar do Estado (PE tem uma UF so).
#
# ESCOPO DINAMICO: regioes e municipios vem de REGIOES/GEO_DICT, derivados de
# um DISTINCT sobre a base. Nao ha lista escrita neste arquivo.
#
# Contrato de saida: reactive.calc que devolve um dados.Geo.
#
# Entrada `foco_externo`: reactive com None ou dict(nivel, regiao_id,
# geocodigo[, nonce]) - o canal por onde o app propaga o estado global
# `selected_location` (clique no mapa = "municipio"; botao "Visao geral" =
# "base"). O caminho inverso (seletor -> resto do painel) e simetrico:
# escolher um municipio no seletor leva a Abrangencia para "Municipio".
# ==============================================================================

from __future__ import annotations

from htmltools import tags
from shiny import module, reactive, render, req, ui

from . import config as cfg
from .dados import GEO_DICT, REGIAO_DO_MUNICIPIO, REGIAO_PADRAO, REGIOES, Geo


@module.ui
def geo_filter_ui():
    return ui.layout_column_wrap(
        ui.input_radio_buttons(
            "nivel", "Abrangência",
            choices={"base": cfg.ROTULO_BASE, "regiao": cfg.ROTULO_REGIAO, "municipio": "Município"},
            selected="base", inline=True,
        ),
        # Choices vazios: preenchidos no server a partir da base. dropdownParent
        # = "body" tira o menu de dentro do card (overflow: hidden cortava a lista).
        ui.input_selectize("regiao", cfg.ROTULO_REGIAO, choices=[], width="100%",
                           options={"dropdownParent": "body"}),
        ui.input_selectize("municipio", "Município", choices=[], width="100%",
                           options={"placeholder": "Digite para buscar...",
                                    "dropdownParent": "body"}),
        width=1 / 3,
        gap="1rem",
    )


@module.ui
def geo_filter_resumo():
    """Resumo compacto do recorte, para o cabecalho do card de filtros."""
    return ui.output_ui("resumo_selecao", inline=True)


@module.server
def geo_filter_server(input, output, session, foco_externo):

    # Municipio que o proximo repovoamento da lista deve deixar selecionado
    # (trocar a regiao dispara a cascata, que por padrao escolhe o primeiro).
    mun_pendente = reactive.value(None)
    # Valor que o SERVIDOR acabou de empurrar para o seletor de municipio. O
    # navegador devolve esse valor como input.municipio(); sem esta marca nao
    # ha como distinguir o eco de uma escolha do usuario.
    mun_esperado = reactive.value(None)

    selecao_inicial = str(REGIAO_PADRAO)
    ui.update_selectize(
        "regiao",
        choices={str(r.regiao_id): f"{r.regiao_nome} ({r.n_mun})" for r in REGIOES.itertuples()},
        selected=selecao_inicial,
    )

    def repovoar_municipios(regiao_id: int) -> None:
        mun = GEO_DICT[GEO_DICT["regiao_id"] == regiao_id].sort_values("municipio_nome")
        if mun.empty:
            return
        with reactive.isolate():
            alvo = mun_pendente()
        if alvo == "__limpar__":
            sel = ""   # reset "Visao geral": seletor vazio (placeholder)
        elif alvo is not None and alvo in set(mun["municipio_geocodigo"]):
            sel = str(alvo)
        else:
            sel = str(mun["municipio_geocodigo"].iloc[0])
        if alvo is not None:
            mun_pendente.set(None)
        mun_esperado.set(sel)
        ui.update_selectize(
            "municipio",
            choices={str(c): n for c, n in zip(mun["municipio_geocodigo"], mun["municipio_nome"])},
            selected=sel,
            server=True,
        )

    # -- cascata: trocar a regiao repovoa a lista de municipios ----------------
    @reactive.effect
    @reactive.event(input.regiao)
    def _cascata():
        try:
            rid = int(input.regiao())
        except (TypeError, ValueError):
            return
        repovoar_municipios(rid)

    # -- foco externo (clique no mapa, botao "Visao geral") --------------------
    @reactive.effect
    @reactive.event(foco_externo, ignore_init=True)
    def _foco():
        f = foco_externo()
        if not f or not f.get("nivel"):
            return

        if f["nivel"] == "base":
            ui.update_radio_buttons("nivel", selected="base")
            # Cosmetico: devolve os seletores ao estado inicial, para nao
            # continuarem exibindo o ultimo clique.
            mun_pendente.set("__limpar__")
            if input.regiao() != selecao_inicial:
                ui.update_selectize("regiao", selected=selecao_inicial)
            else:
                repovoar_municipios(int(selecao_inicial))
            return

        if f["nivel"] == "municipio" and f.get("geocodigo") is not None:
            with reactive.isolate():
                try:
                    atual = geo()
                except Exception:  # noqa: BLE001 - req() silencioso durante a inicializacao
                    atual = None
            if atual is not None and atual.nivel == "municipio" and atual.geocodigo == f["geocodigo"]:
                return   # ja e o recorte em vigor: nada a reposicionar
            mun_pendente.set(int(f["geocodigo"]))

        rid = f.get("regiao_id")
        if rid is None:
            return
        alvo = str(int(rid))
        if input.regiao() != alvo:
            ui.update_selectize("regiao", selected=alvo)   # a cascata consome mun_pendente
        elif mun_pendente() is not None:
            repovoar_municipios(int(alvo))
        ui.update_radio_buttons("nivel", selected=f["nivel"])

    # -- escolha do usuario no seletor de municipio -----------------------------
    # Mesmo efeito do clique no mapa: leva a Abrangencia para "Municipio". O eco
    # de um valor empurrado pelo servidor (mun_esperado) e ignorado - senao a
    # cascata de "trocar a regiao" forcaria a Abrangencia para Municipio sozinha.
    @reactive.effect
    @reactive.event(input.municipio, ignore_init=True)
    def _escolha_municipio():
        v = input.municipio()
        if not v:
            return
        if v == mun_esperado():
            mun_esperado.set(None)
            return
        mun_esperado.set(None)
        if input.nivel() != "municipio":
            ui.update_radio_buttons("nivel", selected="municipio")

    # -- contrato de saida -------------------------------------------------------
    @reactive.calc
    def geo() -> Geo:
        nivel = input.nivel()
        req(nivel)
        if nivel == "base":
            return Geo("base")

        req(input.regiao())
        rid = int(input.regiao())
        if nivel == "regiao":
            return Geo("regiao", regiao_id=rid)

        req(input.municipio())
        cod = int(input.municipio())
        # Estado transitorio: entre trocar a regiao e o update chegar ao
        # navegador, input.municipio() ainda aponta para a regiao anterior.
        req(REGIAO_DO_MUNICIPIO.get(cod) == rid)
        return Geo("municipio", regiao_id=rid, geocodigo=cod)

    @render.ui
    def resumo_selecao():
        g = geo()
        return tags.span(
            tags.strong(g.rotulo),
            tags.span(f" · {g.n_mun} município(s)", class_="opacity-75"),
            class_="small",
        )

    return geo

