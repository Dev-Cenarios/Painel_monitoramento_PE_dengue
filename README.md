# Painel de Monitoramento de Dengue — Pernambuco

Painel interativo para acompanhar a dengue nos **185 municípios de
Pernambuco**, por semana epidemiológica. Mostra onde há mais casos, se a
transmissão está crescendo, se a temporada atual está acima do esperado e traz
uma **previsão de casos** até o fim da temporada.

Feito em **Python + Shiny for Python**, com gráficos em **ECharts** e dados
servidos via **DuckDB** sobre um único arquivo **Parquet**. É a versão para PE
do painel [Monitoramento RIDE-DF](https://github.com/InVictorLopes/Dengue_monitoramento_ride)
(R + Shiny): mesmas visualizações, mesma metodologia, mesma identidade visual.

> ℹ️ Os dados são **agregados por município e semana**, vindos da base pública
> do **InfoDengue** — não há dado individual de paciente.

---

## Sumário

1. [O que o painel mostra](#1-o-que-o-painel-mostra)
2. [Início rápido](#2-início-rápido)
3. [De onde vêm os dados](#3-de-onde-vêm-os-dados)
4. [Ordem de execução dos scripts](#4-ordem-de-execução-dos-scripts)
5. [Metodologia](#5-metodologia)
6. [Estrutura do projeto](#6-estrutura-do-projeto)
7. [Publicação no Shiny Server](#7-publicação-no-shiny-server)
8. [Atualização automática (cron)](#8-atualização-automática-cron)
9. [Solução de problemas](#9-solução-de-problemas)
10. [Diferenças em relação ao painel RIDE-DF](#10-diferenças-em-relação-ao-painel-ride-df)

---

## 1. O que o painel mostra

**Aba "Painel"** — responde ao **recorte geográfico** escolhido no topo:
**Pernambuco → Região de Saúde (GERES) → Município**. Clicar num município no
mapa também aplica o recorte.

| Visualização | O que responde |
|---|---|
| **Indicadores (KPIs)** | Casos no ano mais recente, incidência da última semana, Rt da última semana com estimativa e abrangência do recorte. A cor escurece conforme a situação piora. |
| **Distribuição espacial** | Mapa de incidência (ou casos) por município no período escolhido. Fernando de Noronha aparece como encarte ampliado na costa. |
| **Série** | Casos notificados × estimados (nowcast), por semana ou mês, em casos ou incidência. |
| **Curva epidêmica** | Casos da temporada em análise contra o volume histórico de cada semana. |
| **Rt (InfoDengue)** | Número de reprodução efetivo estimado pelo InfoDengue; acima de 1 = expansão. |
| **Rt — Tendência por município (quadrantes)** | Compara **todos** os municípios: quais estão acima do normal para a época **e** crescendo. Ignora o recorte de propósito. |
| **Diagrama de controle & previsão** | Incidência da temporada contra o canal endêmico (quartis), com a previsão de casos até o fim da temporada. |
| **Sazonalidade** | Mapa de calor ano × semana. |
| **Últimas semanas / SQL gerado** | Tabela das últimas semanas e a consulta usada. |

**Aba "Análises" (Epi Scanner)** — compara os municípios de PE entre si:
semanas com Rt > 1, Rt máximo do ano mais recente e casos acumulados desde 2015.
Mapa + Top 10, com a Região de Saúde de cada município.

---

## 2. Início rápido

### 2.1 Pré-requisitos

- **Python 3.11 ou superior** (testado com 3.14).
- Para **abrir** o painel não é preciso internet: o Parquet já vem no
  repositório. Para **atualizar** os dados, só internet (API pública do
  InfoDengue, sem credenciais).

### 2.2 Ambiente e pacotes (uma vez só)

Na pasta do projeto:

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\python -m pip install -r requirements.txt
```

Linux:

```bash
.venv/bin/python -m pip install -r requirements.txt
```

### 2.3 Abrir o painel

```bash
.venv\Scripts\python -m shiny run app.py --port 8000
```

e abra http://localhost:8000. A camada de **previsão** fica desativada até você
treinar os modelos (2.5).

### 2.4 Atualizar os dados

```bash
.venv\Scripts\python etl_pe_api.py
```

Baixa os 185 municípios da API do InfoDengue (~1 minuto) e grava
`data/dengue_pe.parquet`. Outro período: `etl_pe_api.py 2020` (a partir de 2020)
ou `etl_pe_api.py 2020 2024`. O padrão é de 2015 até o ano corrente.

### 2.5 Treinar a previsão

```bash
.venv\Scripts\python forecasting/train_models.py
```

Leva cerca de 20 minutos (backtest + escolha dos modelos dos 198 recortes, em 4
processos). Gera `forecasting/models/*.json` e `forecasting/model_metrics.csv`.
Reinicie o painel e a previsão aparece no Diagrama de controle. Rode de novo
**depois de cada atualização dos dados**.

---

## 3. De onde vêm os dados

| Dado | Origem | Onde fica | Versionado? |
|---|---|---|---|
| **Casos e estimativas semanais por município** | **InfoDengue** (Fiocruz/FGV), API pública `https://info.dengue.mat.br/api/alertcity` — a mesma fonte do painel RIDE-DF. | `data/dengue_pe.parquet` (gerado por `etl_pe_api.py`) | ✅ (dado público agregado) |
| **Regiões de Saúde (GERES)** | Base territorial do DATASUS (`rl_municip_regsaud`, `tb_regsaud`), FTP do Ministério da Saúde. Gerada por `prep/00_regioes_saude.py`. | `data/regioes_saude_pe.csv` | ✅ |
| **Malha municipal** | API de malhas do IBGE (mesmo arquivo do painel RIDE-DF). | `data/geo/MUN_26.geojson` | ✅ |

O Parquet tem as mesmas colunas da tabela `"Info_Dengue".dengue` usada pelo
painel RIDE-DF (`data_ini_se`, `se`, `casos`, `casos_est`, `p_inc100k`, `rt`,
`pop`, `tempmed`, `umidmed`, …), mais `municipio_geocodigo`, `municipio_nome`,
`uf_ibge` e `uf`.

As 12 GERES: I Recife (20 municípios, inclui Fernando de Noronha), II Limoeiro
(20), III Palmares (22), IV Caruaru (32), V Garanhuns (21), VI Arcoverde (13),
VII Salgueiro (7), VIII Petrolina (7), IX Ouricuri (11), X Afogados da
Ingazeira (12), XI Serra Talhada (10), XII Goiana (10).

---

## 4. Ordem de execução dos scripts

| # | Script | Quando rodar | Gera |
|---|---|---|---|
| 0 | `pip install -r requirements.txt` | uma vez por máquina | ambiente |
| 1 | `etl_pe_api.py` | a cada atualização de dados | `data/dengue_pe.parquet` |
| 2 | `prep/00_regioes_saude.py` | **só** se a regionalização mudar (o CSV já vem no repositório) | `data/regioes_saude_pe.csv` |
| 3 | `forecasting/train_models.py` | depois do passo 1 (recomendado) | `forecasting/models/*.json`, `forecasting/model_metrics.csv` |
| 4 | `shiny run app.py` | — | o painel |

Em produção, depois dos passos 1 e 3 é preciso **reiniciar o app**
(`touch restart.txt`).

---

## 5. Metodologia

Idêntica à do painel RIDE-DF; resumo:

- **Ano sanitário (temporada)**: da **SE 27** à SE 26 do ano seguinte. A
  **SE 53** é dividida meio a meio entre a SE 52 e a SE 1 da mesma temporada.
- **Incidência** = soma dos casos / população × 100.000, sempre recalculada
  para a área (nunca média de incidências). Faixas do Ministério da Saúde:
  < 100 · 100–299 · 300–499 · 500–999 · ≥ 1.000 por 100 mil.
- **Rt**: vem pronto do InfoDengue, por município. Para Região de Saúde ou
  Pernambuco, média dos municípios **ponderada pelos casos** (aproximação; o
  tooltip mostra quantos municípios estão com Rt > 1). Eixo limitado a 5.
- **Diagrama de controle**: canal endêmico das 5 temporadas anteriores, só
  quartis (Q1–Q2, Q2–Q3, mediana); cada semana precisa de ≥ 3 temporadas.
- **Curva epidêmica**: casos absolutos da temporada × soma e mediana
  históricas por semana.
- **Tendência por município (quadrantes)**: X = casos da janela atual ÷
  mediana histórica da mesma janela; Y = janela atual ÷ janela anterior. Forma
  = incidência acumulada no ano. "Sem histórico suficiente" (sem casos na janela
  anterior, mediana zero ou < 2 anos de histórico) fica oculto por padrão — em
  PE, fora da temporada de pico, isso inclui muitos municípios pequenos.
- **Previsão**: 5 modelos (ARIMA + Fourier, SARIMA, GAM binomial negativo,
  STL + ETS, Random Forest), backtest de origem rolante e ensemble em dois
  níveis ponderado pelo inverso do RMSE. As 4 semanas mais recentes são sempre
  reprojetadas e o horizonte vai até a SE 26. Detalhes e diferenças em relação
  ao R em [`forecasting/README-FORECAST.md`](forecasting/README-FORECAST.md).

---

## 6. Estrutura do projeto

```
dengue_pe/
├── app.py                     # Interface + orquestração dos módulos (= ui.R + server.R)
├── etl_pe_api.py              # API do InfoDengue → data/dengue_pe.parquet
├── requirements.txt
├── painel/
│   ├── config.py              # Caminhos, paleta Cenários, escala de incidência, ano sanitário
│   ├── fonte.py               # VIEW `dengue` do DuckDB (Parquet + Região de Saúde)
│   ├── dados.py               # Dicionário geográfico, série por município, malhas, consultas
│   ├── echarts.py             # Saída ECharts para o Shiny (o papel do echarts4r)
│   ├── ui_utils.py            # Tema e cards
│   ├── mod_geo_filter.py      # Recorte: Pernambuco → Região de Saúde → município
│   ├── mod_kpi.py             # Indicadores
│   ├── mod_mapa.py            # Mapa de incidência
│   ├── mod_serie_temporal.py  # Notificado × nowcast
│   ├── mod_curva_epidemica.py # Curva epidêmica por temporada
│   ├── mod_rt.py              # Rt do InfoDengue
│   ├── mod_mapa_rt.py         # Tendência por município (quadrantes)
│   ├── mod_diagrama_controle.py # Canal endêmico + previsão
│   ├── mod_heatmap.py         # Sazonalidade
│   └── mod_epi_scanner.py     # Aba "Análises"
├── www/                       # ECharts 5.5.1 (local) e dengue.js (binding, destaque, popover)
├── forecasting/               # Treino e inferência da previsão
├── prep/00_regioes_saude.py   # DATASUS → data/regioes_saude_pe.csv
├── data/
│   ├── dengue_pe.parquet
│   ├── regioes_saude_pe.csv
│   └── geo/MUN_26.geojson
└── deploy/shiny-server.conf   # Bloco de exemplo para o Shiny Server
```

Não há lista de municípios no código do app: seletores e mapas saem do que
existe no Parquet. Para outro estado, troque a lista em `etl_pe_api.py`, a
tabela de regiões (`prep/00_regioes_saude.py`) e a malha em `data/geo/`.

**Variáveis de ambiente opcionais:** `DENGUE_PARQUET`, `DENGUE_REGIOES_CSV`,
`DENGUE_GEO_DIR`, `DENGUE_APP_CACHE` (pasta dos caches, padrão `app_cache/`),
`DENGUE_ROTULO_BASE` (padrão "Pernambuco").

---

## 7. Publicação no Shiny Server

O Shiny Server (open source) roda apps Python nas versões recentes, desde que
o bloco `location` diga qual Python usar.

1. Clone o projeto em `/srv/shiny-server/dengue_pe`.
2. Crie o ambiente e instale os pacotes **no servidor**:

   ```bash
   cd /srv/shiny-server/dengue_pe && python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
   ```

   O ambiente ocupa ~600 MB (statsmodels, scipy, scikit-learn) — confira o
   espaço em disco antes.
3. Crie a pasta de cache gravável pelo usuário do Shiny:

   ```bash
   sudo install -d -o shiny -g shiny /srv/shiny-server/dengue_pe/app_cache
   ```

4. Treine a previsão: `.venv/bin/python forecasting/train_models.py`.
5. Copie o bloco de [`deploy/shiny-server.conf`](deploy/shiny-server.conf)
   para `/etc/shiny-server/shiny-server.conf` e reinicie o serviço.
6. Depois de atualizar dados ou código: `touch restart.txt`.

---

## 8. Atualização automática (cron)

Exemplo semanal (segunda-feira, 3h), com `crontab -e` no servidor:

```cron
0 3 * * 1 cd /srv/shiny-server/dengue_pe && (.venv/bin/python etl_pe_api.py && .venv/bin/python forecasting/train_models.py && touch restart.txt) >> /tmp/dengue_pe_etl.log 2>&1
```

---

## 9. Solução de problemas

| Sintoma | Causa provável | Solução |
|---|---|---|
| `Arquivo Parquet nao encontrado` | Pasta errada ou ETL não rodado. | Rode a partir da pasta do projeto; `python etl_pe_api.py`. |
| "Previsão indisponível. Rode … train_models.py" | Modelos não treinados. | `python forecasting/train_models.py` e reinicie o app. |
| Previsão demora alguns segundos | Cache de especificações desatualizado (o Parquet mudou depois do treino). | Rode `train_models.py` de novo. |
| Mapa vazio | Malha ausente em `data/geo/` ou pasta de cache sem permissão de escrita. | Confira `MUN_26.geojson` e a mensagem "Malhas recortadas" no log. |
| Painel não mostra dados novos | O app carrega parte dos dados só na inicialização. | `touch restart.txt`. |

---

## 10. Diferenças em relação ao painel RIDE-DF

- **Recorte**: Pernambuco → **Região de Saúde (GERES)** → Município, no lugar de
  RIDE-DF → Estado → Município (PE tem uma UF só).
- **Linguagem**: Python + Shiny for Python + ECharts (no R: echarts4r).
- **Previsão**: mesmos modelos e regras, reimplementados com statsmodels e
  scikit-learn — ver a seção 3 do
  [`README-FORECAST.md`](forecasting/README-FORECAST.md).
- **Mapa**: Fernando de Noronha como encarte ampliado (no enquadramento real
  ele achataria o continente).
- **Tendência por município**: rótulos que se sobrepõem são escondidos (são 185
  pontos, contra 34 no RIDE-DF).
- **Temporada padrão** (diagrama e curva): a da última semana com dados.

---
Desenvolvido com Python + Shiny for Python + ECharts + DuckDB
