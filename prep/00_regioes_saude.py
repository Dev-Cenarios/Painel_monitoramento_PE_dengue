# ==============================================================================
# Dengue PE | prep/00_regioes_saude.py
# Gera data/regioes_saude_pe.csv: municipio (IBGE 7 digitos) -> Regiao de Saude
# (= GERES, Gerencia Regional de Saude da SES-PE), nivel intermediario do
# recorte geografico do painel.
#
# Fonte: base territorial do DATASUS (Ministerio da Saude), tabelas
# rl_municip_regsaud (municipio -> regiao) e tb_regsaud (nomes), em
# ftp://ftp.datasus.gov.br/territorio/tabelas/<ano>/. O CSV gerado e
# versionado: so e preciso rodar de novo se a regionalizacao mudar.
#
# Uso (pasta do projeto):
#   python prep/00_regioes_saude.py                       # versao padrao abaixo
#   python prep/00_regioes_saude.py 2026/06-base_territorial_jun26.zip
# ==============================================================================

import csv
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
ARQUIVO = sys.argv[1] if len(sys.argv) > 1 else "2026/06-base_territorial_jun26.zip"
URL = f"ftp://ftp.datasus.gov.br/territorio/tabelas/{ARQUIVO}"
SAIDA = RAIZ / "data" / "regioes_saude_pe.csv"

# Municipio-sede de cada GERES (SES-PE). A base do DATASUS so traz o numeral
# ("I Regiao de Saude - PE"); a sede e o nome pelo qual a regiao e conhecida.
SEDES = {
    "I": "Recife", "II": "Limoeiro", "III": "Palmares", "IV": "Caruaru",
    "V": "Garanhuns", "VI": "Arcoverde", "VII": "Salgueiro", "VIII": "Petrolina",
    "IX": "Ouricuri", "X": "Afogados da Ingazeira", "XI": "Serra Talhada",
    "XII": "Goiana",
}
ROMANOS = list(SEDES)  # ja na ordem I..XII


def ler_csv(z: zipfile.ZipFile, nome: str, sep: str) -> list[dict]:
    bruto = z.read(nome)
    try:
        texto = bruto.decode("utf-8")
    except UnicodeDecodeError:
        texto = bruto.decode("latin-1")
    return list(csv.DictReader(io.StringIO(texto), delimiter=sep))


def main() -> None:
    print(f"[regioes_saude] baixando {URL}", flush=True)
    with urllib.request.urlopen(URL, timeout=180) as r:
        z = zipfile.ZipFile(io.BytesIO(r.read()))

    regioes = {
        l["CO_REGSAUD"]: l["DS_NOME"]
        for l in ler_csv(z, "tb_regsaud.csv", ",")
        if l["CO_REGSAUD"].startswith("26")
    }
    rel = [l for l in ler_csv(z, "rl_municip_regsaud.csv", ";")
           if l["CO_MUNICIP"].startswith("26")]

    linhas = []
    for l in rel:
        cod_reg = l["CO_REGSAUD"]
        romano = regioes[cod_reg].split(" ")[0]          # "IV Região de Saúde - PE" -> "IV"
        linhas.append({
            "municipio_cod6": l["CO_MUNICIP"],
            "regiao_id": int(cod_reg),
            "regiao_ordem": ROMANOS.index(romano) + 1,
            "regiao_nome": f"{romano} GERES – {SEDES[romano]}",
        })
    linhas.sort(key=lambda x: (x["regiao_ordem"], x["municipio_cod6"]))

    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    with open(SAIDA, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(linhas[0]))
        w.writeheader()
        w.writerows(linhas)
    print(f"[regioes_saude] {SAIDA}: {len(linhas)} municipios em "
          f"{len({l['regiao_id'] for l in linhas})} regioes.", flush=True)


if __name__ == "__main__":
    main()
