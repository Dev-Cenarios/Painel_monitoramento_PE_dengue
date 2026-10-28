# ==============================================================================
# Dengue PE | painel/fonte.py
# A base `dengue` (Parquet + Regiao de Saude) como DataFrame, compartilhada pelo
# painel (painel/dados.py) e pelo forecasting (forecasting/utils_forecast.py) -
# os dois lados filtram o recorte com o mesmo filtrar(). Modulo leve de
# proposito: o treino offline importa so isto, sem subir o resto do painel.
#
# Por que pandas e nao DuckDB: as wheels recentes do DuckDB exigem AVX2 e
# derrubam o processo ("Illegal instruction") em CPUs sem essa instrucao, como
# o Xeon E5 v2 do servidor. A base tem ~114 mil linhas: cabe folgada na RAM.
# ==============================================================================

from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import config as cfg


def carregar(parquet: Path | None = None) -> pd.DataFrame:
    """Le o Parquet e junta a Regiao de Saude (equivale a antiga VIEW `dengue`)."""
    d = pd.read_parquet(parquet or cfg.PARQUET_DENGUE)
    d["data_ini_se"] = pd.to_datetime(d["data_ini_se"])
    r = pd.read_csv(cfg.REGIOES_CSV, dtype={"municipio_cod6": "Int64", "regiao_id": "Int64",
                                            "regiao_ordem": "Int64", "regiao_nome": "string"})
    # O CSV do DATASUS usa o codigo IBGE de 6 digitos (sem o verificador).
    d["municipio_cod6"] = d["municipio_geocodigo"] // 10
    d = d.merge(r, on="municipio_cod6", how="left").drop(columns="municipio_cod6")
    # Inteiros com lacuna viram float (NaN), como o DuckDB devolvia.
    for c in ("regiao_id", "regiao_ordem"):
        d[c] = d[c].astype(float)
    return d


def filtrar(d: pd.DataFrame, coluna: str | None, valor) -> pd.DataFrame:
    """Recorte geografico: coluna None = base inteira."""
    return d if coluna is None else d[d[coluna] == valor]
