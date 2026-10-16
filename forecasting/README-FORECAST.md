# Módulo de Forecasting — Dengue PE

Previsão de casos de dengue para Pernambuco, por recorte (estado, Região de
Saúde/GERES ou município). Porto em Python do módulo de previsão do painel
[RIDE-DF](https://github.com/InVictorLopes/Dengue_monitoramento_ride), com a
mesma metodologia (inspirada no [Mosqlimate](https://mosqlimate.org/)): vários
modelos treinados de forma independente e combinados por **ensemble** ponderado
pelo desempenho histórico.

O treino é **isolado do painel**: `train_models.py` roda offline; o app só
chama `get_dengue_forecast()`.

---

## 1. Dados

A série vem do mesmo Parquet do painel (`data/dengue_pe.parquet`), pela mesma
VIEW `dengue` do DuckDB (`painel/fonte.py`, que acrescenta a Região de Saúde).
Por recorte, uma linha por semana:

```sql
SELECT data_ini_se, max(se) AS se, sum(casos) AS casos, sum(casos_est) AS casos_est,
       sum(pop) AS pop,
       sum(tempmed * pop) / nullif(sum(pop), 0) AS tempmed,   -- média ponderada
       sum(umidmed * pop) / nullif(sum(pop), 0) AS umidmed
FROM dengue WHERE <recorte> GROUP BY data_ini_se ORDER BY data_ini_se
```

Semanas sem registro viram `casos = 0`; população é carregada para a frente e
o clima é interpolado.

## 2. Regras respeitadas

- Os modelos preveem **contagem de casos** (offset `log(pop)` no GAM); a
  incidência é derivada depois (`casos / pop × 100.000`).
- **Ano sanitário** (SE 27 → SE 26) nos rótulos da saída; **SE 53** dobrada
  nas vizinhas (SE 52 / SE 1) da mesma temporada.
- **As 4 semanas mais recentes são sempre reprojetadas** (`FC_SEMANAS_RECENTES`):
  o corte de treino recua 4 semanas a partir da **última linha da base** (não
  de "hoje"). Essas semanas saem com dois registros: `observado` e `previsto`.
- **Horizonte dinâmico**: até a **SE 26** do fim da temporada da última semana
  disponível (`fc_horizon_fim_temporada()`), não um número fixo.
- Pesos e métricas calculados sobre **Pernambuco agregado**; a previsão de
  cada recorte reaproveita os pesos e reajusta os modelos à série do recorte.

## 3. Modelos

| Classe | Chave | Modelo | Implementação |
|---|---|---|---|
| Estatísticos | `arima` | ARIMA + Fourier | `statsmodels` SARIMAX não sazonal + 3 harmônicas de Fourier (período 52) como exógenas. Ordem (p,d,q) por busca *stepwise* (AICc), `d` pelo teste KPSS, Box-Cox só sem zeros. |
| | `sarima` | SARIMA | Diferença sazonal de lag 52 + ARIMA (p,d,q) buscado na série diferenciada, ou seja, (p,d,q)(0,1,0)[52]. IP propagado ao reintegrar a diferença. |
| | `gam` | GAM (Bin. Negativa) | Regressão binomial negativa (`statsmodels`) com splines de regressão (`patsy`): `cc(semana)` cíclica + `cr(t)` + `cr(tempmed_l4)` + `cr(umidmed_l4)` + `cr(casos_l4)` + offset `log(pop)`. IP por Monte Carlo (1.500 simulações). |
| | `stlets` | STL + ETS | STL robusta com sazonalidade fixa (período 52) + ETS na série dessazonalizada (nível, tendência ou tendência amortecida, por AICc). |
| Machine Learning | `rf` | Random Forest | `scikit-learn`, 300 árvores, mesmas variáveis do GAM + `inc_l4`. IP pelos quantis 10/90 das previsões das árvores. Opcional (sem `scikit-learn` a classe fica vazia). |
| Mecanicistas | — | — | Classe registrada, sem modelo ainda. |

**Diferenças em relação ao painel em R** (não há `auto.arima`, `mgcv` nem
`ranger` em Python):

- a busca do ARIMA é uma versão própria do *stepwise* do `auto.arima`;
- o SARIMA não testa termos AR/MA sazonais (P, Q), só a diferença sazonal — com
  período 52 isso seria caro demais no `statsmodels`;
- o GAM usa splines de regressão sem penalização (os mesmos termos do `mgcv`),
  e a tendência `t` é mantida no último valor ao prever (o `mgcv` extrapola);
- o IP do Random Forest vem da dispersão entre árvores (no `ranger`, da
  floresta quantílica).

Para adicionar um modelo: escreva `fit_*` / `predict_*` (contrato: `predict`
devolve `dict(mean, lower, upper)` de comprimento `h`, tudo ≥ 0) e registre em
`FC_MODELOS` (`utils_forecast.py`) com a `classe`. Backtest, ensembles, saída e
checkboxes do painel se ajustam sozinhos.

Para horizontes acima de 4 semanas, os preditores defasados de GAM e RF
**congelam no último valor observado**; ARIMA, SARIMA e STL+ETS não têm essa
limitação. Por isso a faixa do IP 80% no painel é mostrada só nas 4 primeiras
semanas previstas.

## 4. Avaliação — backtest de origem rolante

Para cada uma das últimas 24 origens: treina em `1:o` (janela expansível, ≥ 156
semanas), prevê `o+1 … o+4`. Métricas: RMSE, MAE, MAPE (só semanas com ≥ 5
casos), sMAPE e MASE (escala = ingênuo sazonal, lag 52).

## 5. Ensembles em dois níveis

Média ponderada pelo **inverso do RMSE** do backtest:

1. **ensemble de cada classe** — combina os modelos daquela classe;
2. **ensemble global** — combina os ensembles de classe.

IP de cada nível = média ponderada dos limites 80% dos componentes. Os pesos são
renormalizados sobre os componentes que rodaram no recorte. Os mesmos dados de
backtest definem os pesos e as métricas do ensemble (leve otimismo nas linhas
`ensemble_*`).

## 6. Resultados

<!-- METRICS:START -->

_Backtest de origem rolante: 24 origens, horizonte 4 semanas, janela expansível (>= 156 semanas de treino). Escopo: Pernambuco. Atualizado em 2026-10-16 17:59._

| Classe | Modelo | RMSE | MAE | MAPE (%) | sMAPE (%) | MASE | Peso |
|---|---|---:|---:|---:|---:|---:|---:|
| estatistico | ARIMA + Fourier | 339.13 | 276.39 | 25.19 | 19.63 | 0.27 | 0.297 |
| estatistico | SARIMA | 439.56 | 375.74 | 33.50 | 25.79 | 0.37 | 0.229 |
| estatistico | GAM (Bin. Negativa) | 399.73 | 315.50 | 28.46 | 22.33 | 0.31 | 0.252 |
| estatistico | STL + ETS | 454.74 | 337.01 | 28.18 | 24.25 | 0.33 | 0.222 |
| ml | Random Forest | 245.42 | 198.83 | 23.73 | 15.65 | 0.20 | 1.000 |
| estatistico | *Ensemble Estatísticos* | 352.02 | 271.31 | 25.22 | 19.03 | 0.27 | 0.411 |
| ml | *Ensemble Machine Learning* | 245.42 | 198.83 | 23.73 | 15.65 | 0.20 | 0.589 |
| global | **Ensemble global** | 254.20 | 204.84 | 23.14 | 15.60 | 0.20 | -- |

> Peso de um modelo = seu peso DENTRO da classe. Peso de um ensemble de classe = seu peso no ensemble global. MAPE só em semanas com casos observados >= 5. MASE < 1 = melhor que o ingênuo sazonal (lag 52).

<!-- METRICS:END -->

Detalhe por passo de horizonte em [`model_metrics.csv`](model_metrics.csv).

## 7. Artefatos e cache

```
forecasting/
├── utils_forecast.py        camada comum (dados, modelos, métricas, ensemble)
├── train_models.py          pipeline offline
├── get_forecast.py          get_dengue_forecast() para o painel
├── model_metrics.csv        métricas por classe × modelo × horizonte (gerado)
└── models/
    ├── ensemble_config.json pesos, métricas, parâmetros e impressão digital do Parquet (gerado)
    └── specs_cache.json     especificações pré-escolhidas por recorte (gerado)
```

A parte cara é a **busca** da ordem do ARIMA/SARIMA (~10 s por série). O
treino pré-escolhe essas especificações para Pernambuco, as 12 Regiões de
Saúde e os 185 municípios, no mesmo corte de treino que a previsão ao vivo
usará. Com isso, a previsão de qualquer recorte leva ~1–3 s (os modelos são
reajustados com a ordem conhecida). O cache vale enquanto o Parquet não mudar
(bytes + nº de linhas) — **rode `train_models.py` depois de cada ETL**. Sem o
cache, o painel continua funcionando, só mais lento na primeira consulta de
cada recorte.

O painel também guarda a previsão pronta em disco (`app_cache/previsao/`), com
chave = recorte + dia + versão do Parquet.

## 8. Uso

```bash
python forecasting/train_models.py
python forecasting/train_models.py --origens=30 --min_treino=208 --processos=4
```

```python
from forecasting.get_forecast import get_dengue_forecast
fc = get_dengue_forecast(None)        # Pernambuco
fc = get_dengue_forecast(26010)       # I GERES – Recife (regiao_id do DATASUS)
fc = get_dengue_forecast(2611606)     # Recife
```

| coluna | descrição |
|---|---|
| `Data_SE`, `SE`, `Semana_Epi` | início, código `AAAASS` e número da semana epidemiológica |
| `Ano_Sanitario`, `Temporada` | rótulos do ano sanitário |
| `Tipo` | `observado` ou `previsto` |
| `Pop` | população do recorte |
| `Casos_Observados`, `Casos_Nowcast` | notificados e `casos_est` do InfoDengue |
| `Casos_Previstos_Ensemble`, `Limite_Inferior`, `Limite_Superior` | ponto + IP 80% do ensemble global |
| `Prev_<modelo>` | ponto de cada modelo |
| `Prev_ens_<classe>`, `Lim_inf_<classe>`, `Lim_sup_<classe>` | ensemble de cada classe |
