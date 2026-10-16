# ==============================================================================
# Dengue PE | painel/fonte.py
# A VIEW `dengue` (Parquet + Regiao de Saude), compartilhada pelo painel
# (painel/dados.py) e pelo forecasting (forecasting/utils_forecast.py) - os
# dois lados filtram o recorte com o mesmo SQL. Modulo leve de proposito: o
# treino offline importa so isto, sem subir o resto do painel.
# ==============================================================================

from __future__ import annotations

from pathlib import Path

import duckdb

from . import config as cfg


def _sql_str(p: Path) -> str:
    return str(Path(p).resolve()).replace("\\", "/").replace("'", "''")


def conectar(parquet: Path | None = None) -> duckdb.DuckDBPyConnection:
    """Conexao DuckDB em memoria com a VIEW `dengue`. Nada e carregado na RAM aqui."""
    con = duckdb.connect(":memory:")
    try:
        con.execute("SET threads TO 4")
    except duckdb.Error:
        pass
    con.execute(f"""
        CREATE OR REPLACE VIEW dengue AS
        SELECT d.*, r.regiao_id, r.regiao_nome, r.regiao_ordem
        FROM read_parquet('{_sql_str(parquet or cfg.PARQUET_DENGUE)}') d
        LEFT JOIN read_csv('{_sql_str(cfg.REGIOES_CSV)}', header = true,
                           columns = {{'municipio_cod6': 'BIGINT', 'regiao_id': 'INTEGER',
                                       'regiao_ordem': 'INTEGER', 'regiao_nome': 'VARCHAR'}}) r
          ON d.municipio_geocodigo // 10 = r.municipio_cod6
    """)
    return con
