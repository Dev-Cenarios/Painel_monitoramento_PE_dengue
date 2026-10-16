# ==============================================================================
# Dengue PE | etl_pe_api.py
# Baixa a serie semanal dos 185 municipios de Pernambuco direto da API publica
# do InfoDengue (https://info.dengue.mat.br/services/api) e grava
# data/dengue_pe.parquet - a unica fonte de dados lida pelo painel.
#
# Mesma fonte e mesmo formato do etl_ride_api.R do painel RIDE-DF: as colunas
# da API sao renomeadas para os nomes da tabela "Info_Dengue".dengue do banco.
#
# Nao precisa de credenciais, so de internet.
#
# Uso (pasta do projeto):
#   python etl_pe_api.py              # 2015 ate o ano corrente
#   python etl_pe_api.py 2020         # a partir de 2020
#   python etl_pe_api.py 2020 2024    # de 2020 a 2024
#
# Variavel de ambiente opcional: DENGUE_PARQUET (caminho de saida; o padrao e
# o mesmo lido pelo app, data/dengue_pe.parquet).
# ==============================================================================

from __future__ import annotations

import io
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests

AQUI = Path(__file__).resolve().parent

# Nomes com acento no console do Windows (cp1252) sem quebrar o log.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ANO_INICIO = int(sys.argv[1]) if len(sys.argv) >= 2 else 2015
ANO_FIM = int(sys.argv[2]) if len(sys.argv) >= 3 else date.today().year
SAIDA = Path(os.environ.get("DENGUE_PARQUET", AQUI / "data" / "dengue_pe.parquet"))

API_URL = "https://info.dengue.mat.br/api/alertcity"

# Codigos IBGE dos 185 municipios de Pernambuco (184 + Fernando de Noronha),
# da API de localidades do IBGE. O nome fica aqui para nao depender da grafia
# devolvida pela API do InfoDengue.
PE_MUNICIPIOS = {
    "2600054": "Abreu e Lima",
    "2600104": "Afogados da Ingazeira",
    "2600203": "Afrânio",
    "2600302": "Agrestina",
    "2600401": "Água Preta",
    "2600500": "Águas Belas",
    "2600609": "Alagoinha",
    "2600708": "Aliança",
    "2600807": "Altinho",
    "2600906": "Amaraji",
    "2601003": "Angelim",
    "2601052": "Araçoiaba",
    "2601102": "Araripina",
    "2601201": "Arcoverde",
    "2601300": "Barra de Guabiraba",
    "2601409": "Barreiros",
    "2601508": "Belém de Maria",
    "2601607": "Belém do São Francisco",
    "2601706": "Belo Jardim",
    "2601805": "Betânia",
    "2601904": "Bezerros",
    "2602001": "Bodocó",
    "2602100": "Bom Conselho",
    "2602209": "Bom Jardim",
    "2602308": "Bonito",
    "2602407": "Brejão",
    "2602506": "Brejinho",
    "2602605": "Brejo da Madre de Deus",
    "2602704": "Buenos Aires",
    "2602803": "Buíque",
    "2602902": "Cabo de Santo Agostinho",
    "2603009": "Cabrobó",
    "2603108": "Cachoeirinha",
    "2603207": "Caetés",
    "2603306": "Calçado",
    "2603405": "Calumbi",
    "2603454": "Camaragibe",
    "2603504": "Camocim de São Félix",
    "2603603": "Camutanga",
    "2603702": "Canhotinho",
    "2603801": "Capoeiras",
    "2603900": "Carnaíba",
    "2603926": "Carnaubeira da Penha",
    "2604007": "Carpina",
    "2604106": "Caruaru",
    "2604155": "Casinhas",
    "2604205": "Catende",
    "2604304": "Cedro",
    "2604403": "Chã de Alegria",
    "2604502": "Chã Grande",
    "2604601": "Condado",
    "2604700": "Correntes",
    "2604809": "Cortês",
    "2604908": "Cumaru",
    "2605004": "Cupira",
    "2605103": "Custódia",
    "2605152": "Dormentes",
    "2605202": "Escada",
    "2605301": "Exu",
    "2605400": "Feira Nova",
    "2605459": "Fernando de Noronha",
    "2605509": "Ferreiros",
    "2605608": "Flores",
    "2605707": "Floresta",
    "2605806": "Frei Miguelinho",
    "2605905": "Gameleira",
    "2606002": "Garanhuns",
    "2606101": "Glória do Goitá",
    "2606200": "Goiana",
    "2606309": "Granito",
    "2606408": "Gravatá",
    "2606507": "Iati",
    "2606606": "Ibimirim",
    "2606705": "Ibirajuba",
    "2606804": "Igarassu",
    "2606903": "Iguaracy",
    "2607000": "Inajá",
    "2607109": "Ingazeira",
    "2607208": "Ipojuca",
    "2607307": "Ipubi",
    "2607406": "Itacuruba",
    "2607505": "Itaíba",
    "2607604": "Ilha de Itamaracá",
    "2607653": "Itambé",
    "2607703": "Itapetim",
    "2607752": "Itapissuma",
    "2607802": "Itaquitinga",
    "2607901": "Jaboatão dos Guararapes",
    "2607950": "Jaqueira",
    "2608008": "Jataúba",
    "2608057": "Jatobá",
    "2608107": "João Alfredo",
    "2608206": "Joaquim Nabuco",
    "2608255": "Jucati",
    "2608305": "Jupi",
    "2608404": "Jurema",
    "2608453": "Lagoa do Carro",
    "2608503": "Lagoa de Itaenga",
    "2608602": "Lagoa do Ouro",
    "2608701": "Lagoa dos Gatos",
    "2608750": "Lagoa Grande",
    "2608800": "Lajedo",
    "2608909": "Limoeiro",
    "2609006": "Macaparana",
    "2609105": "Machados",
    "2609154": "Manari",
    "2609204": "Maraial",
    "2609303": "Mirandiba",
    "2609402": "Moreno",
    "2609501": "Nazaré da Mata",
    "2609600": "Olinda",
    "2609709": "Orobó",
    "2609808": "Orocó",
    "2609907": "Ouricuri",
    "2610004": "Palmares",
    "2610103": "Palmeirina",
    "2610202": "Panelas",
    "2610301": "Paranatama",
    "2610400": "Parnamirim",
    "2610509": "Passira",
    "2610608": "Paudalho",
    "2610707": "Paulista",
    "2610806": "Pedra",
    "2610905": "Pesqueira",
    "2611002": "Petrolândia",
    "2611101": "Petrolina",
    "2611200": "Poção",
    "2611309": "Pombos",
    "2611408": "Primavera",
    "2611507": "Quipapá",
    "2611533": "Quixaba",
    "2611606": "Recife",
    "2611705": "Riacho das Almas",
    "2611804": "Ribeirão",
    "2611903": "Rio Formoso",
    "2612000": "Sairé",
    "2612109": "Salgadinho",
    "2612208": "Salgueiro",
    "2612307": "Saloá",
    "2612406": "Sanharó",
    "2612455": "Santa Cruz",
    "2612471": "Santa Cruz da Baixa Verde",
    "2612505": "Santa Cruz do Capibaribe",
    "2612554": "Santa Filomena",
    "2612604": "Santa Maria da Boa Vista",
    "2612703": "Santa Maria do Cambucá",
    "2612802": "Santa Terezinha",
    "2612901": "São Benedito do Sul",
    "2613008": "São Bento do Una",
    "2613107": "São Caitano",
    "2613206": "São João",
    "2613305": "São Joaquim do Monte",
    "2613404": "São José da Coroa Grande",
    "2613503": "São José do Belmonte",
    "2613602": "São José do Egito",
    "2613701": "São Lourenço da Mata",
    "2613800": "São Vicente Férrer",
    "2613909": "Serra Talhada",
    "2614006": "Serrita",
    "2614105": "Sertânia",
    "2614204": "Sirinhaém",
    "2614303": "Moreilândia",
    "2614402": "Solidão",
    "2614501": "Surubim",
    "2614600": "Tabira",
    "2614709": "Tacaimbó",
    "2614808": "Tacaratu",
    "2614857": "Tamandaré",
    "2615003": "Taquaritinga do Norte",
    "2615102": "Terezinha",
    "2615201": "Terra Nova",
    "2615300": "Timbaúba",
    "2615409": "Toritama",
    "2615508": "Tracunhaém",
    "2615607": "Trindade",
    "2615706": "Triunfo",
    "2615805": "Tupanatinga",
    "2615904": "Tuparetama",
    "2616001": "Venturosa",
    "2616100": "Verdejante",
    "2616183": "Vertente do Lério",
    "2616209": "Vertentes",
    "2616308": "Vicência",
    "2616407": "Vitória de Santo Antão",
    "2616506": "Xexéu",
}

# Nomes da API (CSV) -> nomes da tabela "Info_Dengue".dengue do banco, que e o
# que o app espera. As demais colunas so passam para minusculas.
RENOMEAR = {
    "data_iniSE": "data_ini_se",
    "SE": "se",
    "Rt": "rt",
    "Localidade_id": "localidade_id",
    "id": "id_origem",
    "municipio_nome": "municipio_nome_api",
}

COLS_INT = ["se", "casos", "casos_est_min", "casos_est_max", "casprov",
            "casprov_est_min", "casprov_est_max", "casconf", "notif_accum_year",
            "nivel", "nivel_inc", "receptivo", "transmissao", "localidade_id"]
COLS_NUM = ["casos_est", "casprov_est", "p_rt1", "rt", "p_inc100k", "pop",
            "tempmin", "tempmed", "tempmax", "umidmin", "umidmed", "umidmax"]


def baixar_municipio(geocodigo: str) -> pd.DataFrame:
    """Baixa a serie de um municipio (CSV da API), com ate 3 tentativas."""
    params = {
        "geocode": geocodigo, "disease": "dengue", "format": "csv",
        "ew_start": 1, "ew_end": 53, "ey_start": ANO_INICIO, "ey_end": ANO_FIM,
    }
    ultimo_erro = None
    for tentativa in range(1, 4):
        try:
            r = requests.get(API_URL, params=params, timeout=60)
            r.raise_for_status()
            # id como texto: tem 18 digitos e perderia precisao como float.
            return pd.read_csv(io.StringIO(r.text), dtype={"id": "string"})
        except Exception as e:  # noqa: BLE001 - qualquer falha de rede/parse tenta de novo
            ultimo_erro = e
            print(f"  {geocodigo}: tentativa {tentativa} falhou ({e})", flush=True)
            time.sleep(2 * tentativa)
    raise RuntimeError(
        f"Nao foi possivel baixar o municipio {geocodigo} da API do InfoDengue: {ultimo_erro}"
    )


def preparar(geocodigo: str, d: pd.DataFrame) -> pd.DataFrame | None:
    if d.empty:
        print(f"  aviso: sem dados na API para {geocodigo}", flush=True)
        return None
    d = d.rename(columns=RENOMEAR)
    d.columns = [c.lower() for c in d.columns]
    d["municipio_geocodigo"] = int(geocodigo)
    d["municipio_nome"] = PE_MUNICIPIOS[geocodigo]
    d["uf_ibge"] = int(geocodigo) // 100000
    d["uf"] = str(d["uf_ibge"].iloc[0])  # o app espera o codigo em texto ("26")
    return d


def main() -> None:
    print(f"[etl_pe_api] InfoDengue: {len(PE_MUNICIPIOS)} municipios, "
          f"{ANO_INICIO} a {ANO_FIM}.", flush=True)

    def tarefa(geocodigo: str):
        d = preparar(geocodigo, baixar_municipio(geocodigo))
        print(f"- {geocodigo} {PE_MUNICIPIOS[geocodigo]}"
              f" ({0 if d is None else len(d)} linhas)", flush=True)
        return d

    # Poucas conexoes simultaneas: e uma API publica, nao vale sobrecarrega-la.
    with ThreadPoolExecutor(max_workers=4) as ex:
        partes = [d for d in ex.map(tarefa, PE_MUNICIPIOS) if d is not None]

    if not partes:
        raise SystemExit("A API nao devolveu dados para nenhum municipio.")

    # concat une as partes mesmo que algum municipio venha com colunas a mais.
    dados = pd.concat(partes, ignore_index=True, sort=False)

    dados["data_ini_se"] = pd.to_datetime(dados["data_ini_se"]).dt.date
    for col in COLS_INT:
        if col in dados:
            dados[col] = pd.to_numeric(dados[col], errors="coerce").astype("Int32")
    for col in COLS_NUM:
        if col in dados:
            dados[col] = pd.to_numeric(dados[col], errors="coerce").astype("float64")
    dados["municipio_geocodigo"] = dados["municipio_geocodigo"].astype("int64")
    dados["uf_ibge"] = dados["uf_ibge"].astype("int32")
    if "id_origem" in dados:
        dados["id_origem"] = pd.to_numeric(dados["id_origem"], errors="coerce").astype("Int64")

    dados = dados.sort_values(["municipio_geocodigo", "data_ini_se"]).reset_index(drop=True)

    # Grava num temporario e troca de uma vez: o app nunca le um arquivo pela metade.
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    tmp = SAIDA.with_name(SAIDA.name + ".tmp")
    pq.write_table(pa.Table.from_pandas(dados, preserve_index=False), tmp)
    os.replace(tmp, SAIDA)

    n_mun = dados["municipio_geocodigo"].nunique()
    print(f"[etl_pe_api] {SAIDA} gravado: {len(dados)} linhas, {n_mun} municipio(s), "
          f"SE {dados['se'].min()} a {dados['se'].max()}.", flush=True)
    if n_mun < len(PE_MUNICIPIOS):
        print(f"AVISO: apenas {n_mun} de {len(PE_MUNICIPIOS)} municipios vieram da API.",
              flush=True)


if __name__ == "__main__":
    main()
