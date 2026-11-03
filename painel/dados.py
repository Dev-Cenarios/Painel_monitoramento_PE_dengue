# ==============================================================================
# Dengue PE | painel/dados.py
# Fonte de dados (Parquet em memoria via pandas), dicionario geografico, malhas
# e helpers de consulta. Equivale a parte de dados do global.R do RIDE;
# executado UMA vez, na importacao.
#
# A base inteira (~114 mil linhas) fica num DataFrame por processo; as
# agregacoes por recorte sao groupby sobre ele, cacheadas por recorte.
#
# Recorte em tres niveis: Pernambuco (base inteira) -> Regiao de Saude (GERES)
# -> Municipio. A regiao vem de data/regioes_saude_pe.csv (DATASUS, ver
# prep/00_regioes_saude.py) e entra na base por juncao, de modo que filtrar por
# regiao e um filtro de coluna como outro qualquer.
# ==============================================================================

from __future__ import annotations

import hashlib
import json
import logging
import tempfile
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from . import config as cfg
from .fonte import carregar, filtrar

log = logging.getLogger("dengue_pe")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="[Dengue PE] %(message)s")

# ------------------------------------------------------------ fonte de dados --
if not cfg.PARQUET_DENGUE.exists():
    raise FileNotFoundError(
        f"Arquivo Parquet nao encontrado em '{cfg.PARQUET_DENGUE}'. "
        "Rode `python etl_pe_api.py` ou defina DENGUE_PARQUET."
    )
if not cfg.REGIOES_CSV.exists():
    raise FileNotFoundError(
        f"Tabela de regioes de saude nao encontrada em '{cfg.REGIOES_CSV}'. "
        "Rode `python prep/00_regioes_saude.py`."
    )


# Uma copia da base por processo, somente leitura: pode ser lida de qualquer
# thread (ex.: previsao calculada fora do laco de eventos) sem lock.
DENGUE = carregar()


log.info("Fonte: %s (%.1f MB).", cfg.PARQUET_DENGUE, cfg.PARQUET_DENGUE.stat().st_size / 1024**2)

# Versao dos dados (tamanho + data do Parquet): entra na chave do cache em
# disco da previsao - uma atualizacao do ETL nunca serve previsao velha.
_st = cfg.PARQUET_DENGUE.stat()
VERSAO_DADOS = f"{_st.st_size}-{int(_st.st_mtime)}"

# ------------------------------------------------- dicionario geografico -----
# Um DISTINCT sobre a base: e AQUI que o escopo do painel se define. Nao ha
# lista de municipios no app - trocar o Parquet troca os seletores.
GEO_DICT = (DENGUE.loc[DENGUE["municipio_geocodigo"].notna(),
                       ["municipio_geocodigo", "municipio_nome", "uf_ibge",
                        "regiao_id", "regiao_nome", "regiao_ordem"]]
            .drop_duplicates())
GEO_DICT["municipio_geocodigo"] = GEO_DICT["municipio_geocodigo"].astype(int)
GEO_DICT = GEO_DICT.sort_values(["regiao_ordem", "municipio_nome"]).reset_index(drop=True)

sem_regiao = GEO_DICT[GEO_DICT["regiao_id"].isna()]
if len(sem_regiao):
    log.warning("%d municipio(s) sem regiao de saude: %s", len(sem_regiao),
                ", ".join(sem_regiao["municipio_nome"]))

REGIOES = (GEO_DICT.dropna(subset=["regiao_id"])
           .groupby(["regiao_id", "regiao_nome", "regiao_ordem"], as_index=False)
           .size().rename(columns={"size": "n_mun"})
           .sort_values("regiao_ordem").reset_index(drop=True))
REGIOES["regiao_id"] = REGIOES["regiao_id"].astype(int)

# Regiao padrao dos seletores: a I GERES (Recife), a primeira da ordem.
REGIAO_PADRAO = int(REGIOES["regiao_id"].iloc[0])

NOME_MUNICIPIO = {int(c): n for c, n in zip(GEO_DICT["municipio_geocodigo"], GEO_DICT["municipio_nome"])}
REGIAO_DO_MUNICIPIO = {int(c): int(r) for c, r in zip(GEO_DICT["municipio_geocodigo"], GEO_DICT["regiao_id"])
                       if pd.notna(r)}
NOME_REGIAO = {int(r): n for r, n in zip(REGIOES["regiao_id"], REGIOES["regiao_nome"])}

log.info("Dicionario pronto: %d municipios / %d regioes de saude.", len(GEO_DICT), len(REGIOES))

# ------------------------------------------------- cobertura temporal --------
ANO_MIN = DENGUE["data_ini_se"].min().year
ANO_MAX = DENGUE["data_ini_se"].max().year
log.info("Cobertura temporal: %d a %d.", ANO_MIN, ANO_MAX)


# --------------------------------------------------------------- recorte -----
@dataclass(frozen=True)
class Geo:
    """Recorte geografico. nivel: "base" | "regiao" | "municipio"."""
    nivel: str = "base"
    regiao_id: int | None = None
    geocodigo: int | None = None

    @property
    def rotulo(self) -> str:
        if self.nivel == "regiao":
            return NOME_REGIAO.get(self.regiao_id, str(self.regiao_id))
        if self.nivel == "municipio":
            return f"{NOME_MUNICIPIO.get(self.geocodigo, self.geocodigo)} / PE"
        return cfg.ROTULO_BASE

    @property
    def n_mun(self) -> int:
        if self.nivel == "regiao":
            return int((GEO_DICT["regiao_id"] == self.regiao_id).sum())
        if self.nivel == "municipio":
            return 1
        return len(GEO_DICT)

    def filtro(self) -> tuple[str | None, int | None]:
        """(coluna, valor) do recorte - o equivalente de aplicar_filtro_geo()."""
        if self.nivel == "regiao":
            return "regiao_id", int(self.regiao_id)
        if self.nivel == "municipio":
            return "municipio_geocodigo", int(self.geocodigo)
        return None, None


GEO_BASE = Geo()


# ------------------------------------------ serie semanal por municipio -----
# Uma linha por municipio/semana, nada somado entre municipios. Usada pelo
# mapa de tendencia (quadrantes) e pela aba Analises. ~114 mil linhas: trivial
# manter em memoria, calculada uma vez no start.
_g = DENGUE.groupby(["municipio_geocodigo", "data_ini_se"])
SERIE_MUNICIPIOS = pd.DataFrame({
    "se": _g["se"].max(), "casos": _g["casos"].sum(min_count=1),
    "pop": _g["pop"].max(), "rt": _g["rt"].max(),
}).reset_index()
SERIE_MUNICIPIOS["municipio_geocodigo"] = SERIE_MUNICIPIOS["municipio_geocodigo"].astype(int)
SERIE_MUNICIPIOS["data_ini_se"] = pd.to_datetime(SERIE_MUNICIPIOS["data_ini_se"])
SERIE_MUNICIPIOS["se"] = SERIE_MUNICIPIOS["se"].astype(int)
SERIE_MUNICIPIOS["ano"] = SERIE_MUNICIPIOS["se"] // 100
SERIE_MUNICIPIOS["semana"] = SERIE_MUNICIPIOS["se"] % 100
for _c in ("casos", "pop", "rt"):
    SERIE_MUNICIPIOS[_c] = SERIE_MUNICIPIOS[_c].astype(float)
SERIE_MUNICIPIOS["municipio_nome"] = SERIE_MUNICIPIOS["municipio_geocodigo"].map(NOME_MUNICIPIO)
log.info("Serie por municipio pronta: %d linhas.", len(SERIE_MUNICIPIOS))


# ----------------------------------------------------------------- helpers ---
@lru_cache(maxsize=256)
def consultar_serie_semanal(geo: Geo) -> pd.DataFrame:
    """Serie semanal agregada para um recorte (uma linha por semana).

    Colunas: data_ini_se, se, casos, casos_est, pop, rt_direto, rt_num,
    rt_peso (media de Rt ponderada por casos), n_mun, n_mun_rt1.
    Cacheada por recorte (o Geo e hashable) e compartilhada entre sessoes.
    """
    b = filtrar(DENGUE, *geo.filtro())
    g = b.groupby("data_ini_se")
    d = pd.DataFrame({
        "se":        g["se"].max(),
        "casos":     g["casos"].sum(min_count=1),
        "casos_est": g["casos_est"].sum(min_count=1),
        "pop":       g["pop"].sum(min_count=1),
        "rt_direto": g["rt"].max(),
        "rt_num":    (b["rt"] * b["casos"]).groupby(b["data_ini_se"]).sum(min_count=1),
        "rt_peso":   b["casos"].where(b["rt"].notna(), 0).groupby(b["data_ini_se"]).sum(),
        "n_mun":     g["municipio_geocodigo"].nunique(),
        "n_mun_rt1": (b["rt"] > 1).groupby(b["data_ini_se"]).sum(),
    }).reset_index()
    d["se"] = d["se"].astype(int)
    for c in ("casos", "casos_est", "pop", "rt_direto", "rt_num", "rt_peso"):
        d[c] = d[c].astype(float)
    return d


def reagrupar_periodo(d: pd.DataFrame, agrupamento: str = "semana") -> pd.DataFrame:
    """Reagrupa a serie semanal por mes (semana inteira no mes em que COMECA)."""
    d = d.copy()
    if agrupamento == "semana":
        d["periodo"] = d["data_ini_se"]
        return d
    d["periodo"] = d["data_ini_se"].dt.to_period("M").dt.to_timestamp()
    out = d.groupby("periodo", as_index=False).agg(
        casos=("casos", "sum"),
        casos_est=("casos_est", "sum"),
        # Populacao nao se soma ao longo do tempo: mesmo conjunto de pessoas.
        pop=("pop", "max"),
        rt_direto=("rt_direto", "mean"),
        rt_num=("rt_num", "sum"),
        rt_peso=("rt_peso", "sum"),
        n_mun=("n_mun", "max"),
        n_mun_rt1=("n_mun_rt1", "mean"),
    )
    out["n_mun_rt1"] = out["n_mun_rt1"].round()
    return out


def calcular_indicadores(d: pd.DataFrame, geo: Geo) -> pd.DataFrame:
    """Incidencia (casos/pop*1e5, nunca media de razoes) e Rt.

    Rt: no municipio e o valor da coluna; acima disso, media ponderada pelos
    casos - aproximacao (Rt nao e aditivo entre areas).
    """
    d = d.copy()
    pop_ok = d["pop"] > 0
    d["incidencia"] = np.where(pop_ok, d["casos"] / d["pop"] * 1e5, np.nan)
    d["incidencia_est"] = np.where(pop_ok, d["casos_est"] / d["pop"] * 1e5, np.nan)
    if geo.nivel == "municipio":
        d["rt"] = d["rt_direto"]
    else:
        d["rt"] = d["rt_num"] / d["rt_peso"].where(d["rt_peso"] > 0)
    return d


def dobrar_semana_53(d: pd.DataFrame, colunas: list[str]) -> pd.DataFrame:
    """Dobra a SE 53 nas vizinhas da MESMA temporada: metade na 52, metade na 1.

    Assim toda temporada ocupa exatamente 52 posicoes no eixo.
    """
    com_53 = d[d["semana"] == 53]
    d = d[d["semana"] != 53].copy()
    for _, linha in com_53.iterrows():
        m52 = (d["ano_san"] == linha["ano_san"]) & (d["semana"] == 52)
        m1 = (d["ano_san"] == linha["ano_san"]) & (d["semana"] == 1)
        for col in colunas:
            metade = linha[col] / 2
            if pd.isna(metade):
                continue
            for m in (m52, m1):
                if m.any():
                    d.loc[m, col] = d.loc[m, col].fillna(0) + metade
    return d


# --------------------------------------------------------- malhas (GeoJSON) --
# Recorta a malha municipal do IBGE aos municipios da base e grava um GeoJSON
# unico (BASE_MUN), servido como arquivo estatico em /geo/. O recorte segue
# GEO_DICT (acompanha o Parquet) e e refeito so quando a impressao digital
# (municipios + tamanho dos GeoJSON fonte) muda.
def _dir_cache_geo() -> Path:
    for d in (cfg.APP_CACHE / "geo", Path(tempfile.gettempdir()) / "dengue_pe_geo"):
        try:
            d.mkdir(parents=True, exist_ok=True)
            teste = d / ".escrita"
            teste.write_text("ok")
            teste.unlink()
            return d
        except OSError:
            continue
    raise RuntimeError("Nenhuma pasta gravavel para o cache de malhas.")


GEO_CACHE_DIR = _dir_cache_geo()


# Fernando de Noronha fica ~350 km mar adentro: no enquadramento real ele
# achataria o continente. Vira um encarte ampliado (9x) na costa, a leste do
# Recife - convencao comum nos mapas de PE. So muda o desenho, nao o dado.
NORONHA = "2605459"
ENCARTE_NORONHA = {"centro": (-34.3, -7.75), "escala": 9.0}


def _mover_noronha(feicao: dict) -> dict:
    geom = feicao["geometry"]

    def pontos(c):
        return [c] if isinstance(c[0], (int, float)) else [p for x in c for p in pontos(x)]

    todos = pontos(geom["coordinates"])
    cx = sum(p[0] for p in todos) / len(todos)
    cy = sum(p[1] for p in todos) / len(todos)
    (ax, ay), k = ENCARTE_NORONHA["centro"], ENCARTE_NORONHA["escala"]

    def mover(c):
        if isinstance(c[0], (int, float)):
            return [ax + (c[0] - cx) * k, ay + (c[1] - cy) * k]
        return [mover(x) for x in c]

    return {**feicao, "geometry": {**geom, "coordinates": mover(geom["coordinates"])}}


# Contorno do estado (gerado por prep/01_contorno_pe.R), juntado ao FIM da
# malha: a feicao extra e desenhada por ultimo, por cima dos municipios, e por
# ser parte do mesmo mapa acompanha zoom e arraste. Os mapas a desenham so
# como borda (mod_mapa.itens_contorno). Opcional: sem o arquivo, o mapa sai
# so com as bordas dos municipios.
CONTORNO_ARQUIVO = cfg.GEO_DIR / "contorno_pe.geojson"


def _feicoes_contorno() -> list[dict]:
    if not CONTORNO_ARQUIVO.exists():
        return []
    js = json.loads(CONTORNO_ARQUIVO.read_text(encoding="utf-8"))
    return [f for f in js["features"] if isinstance(f["properties"].get(cfg.GEO_NAME_PROPERTY), str)]


def preparar_malhas_base() -> str:
    """Grava BASE_MUN.json e devolve o nome do arquivo com a versao (cache-busting)."""
    ufs = sorted(GEO_DICT["uf_ibge"].dropna().astype(int).unique())
    fontes = [cfg.GEO_DIR / f"MUN_{uf}.geojson" for uf in ufs]
    impressao = json.dumps({
        "geocodigos": sorted(GEO_DICT["municipio_geocodigo"].tolist()),
        "bytes": [f.stat().st_size if f.exists() else None for f in fontes + [CONTORNO_ARQUIVO]],
        "encarte": ENCARTE_NORONHA,
    })
    versao = hashlib.md5(impressao.encode()).hexdigest()[:10]
    nome = f"BASE_MUN_{versao}.json"
    destino = GEO_CACHE_DIR / nome
    if destino.exists():
        return nome

    codigos = {str(c) for c in GEO_DICT["municipio_geocodigo"]}
    feicoes = []
    for f in fontes:
        if not f.exists():
            log.warning("Malha ausente: %s", f.name)
            continue
        js = json.loads(f.read_text(encoding="utf-8"))
        feicoes += [_mover_noronha(ft) if str(ft["properties"].get(cfg.GEO_NAME_PROPERTY)) == NORONHA else ft
                    for ft in js["features"]
                    if str(ft["properties"].get(cfg.GEO_NAME_PROPERTY)) in codigos]
    if len(feicoes) < len(GEO_DICT):
        log.warning("%d de %d municipios sem poligono. O mapa ficara incompleto.",
                    len(GEO_DICT) - len(feicoes), len(GEO_DICT))
    tmp = destino.with_suffix(".tmp")
    feicoes += _feicoes_contorno()
    tmp.write_text(json.dumps({"type": "FeatureCollection", "features": feicoes}), encoding="utf-8")
    tmp.replace(destino)
    log.info("Malhas recortadas: %d municipios -> %s", len(feicoes), destino)
    return nome


MALHA_BASE_ARQUIVO = preparar_malhas_base()
CODIGOS_CONTORNO = [f["properties"][cfg.GEO_NAME_PROPERTY] for f in _feicoes_contorno()]
MALHA_BASE_URL = f"geo/{MALHA_BASE_ARQUIVO}"


def hoje() -> date:
    return date.today()
