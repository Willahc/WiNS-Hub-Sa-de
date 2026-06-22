# WiNS Hub Saúde — Status (16/06/2026)

> **Local do projeto (reorganizado 22/06/2026):** `C:\Users\kbadmin\Documents\Projetos\WiNS Hub Saúde\`
> Scripts + `.env.saude` + dashboards + logs na raiz; dados (CSV/ZIP/rfb_socios) em `wins_hub_saude_dados\`;
> `sih_tmp\` e site público em `site\`. **Todos os caminhos nos scripts agora são relativos a `BASE_DIR`
> (a raiz do projeto)** — rode os comandos abaixo a partir desta pasta.

Ponto de retomada. Tudo local: PostgreSQL 18 `wins_hub_saude`, credenciais em `.env.saude`.
psql: `C:\Program Files\PostgreSQL\18\bin\psql.exe`. App user `wins_saude`; DDL exige `postgres` (SUPERUSER_URL no .env).

## Como ligar amanhã
```
python wins_hub_app.py        # site dinâmico -> http://localhost:5000
                              #   /  Dashboard | /oportunidade | /vender
```
Regenerar o dashboard estático (se mudar dados): `python wins_hub_saude_dashboard.py`
→ gera `wins_hub_saude_dashboard.html` (interno, com PII) e `_publico.html` (sem PII, p/ deploy).

## Camadas no banco (prontas)
| Tabela | Conteúdo / números |
|---|---|
| estabelecimentos | 623.208 (CNES) · 301.310 decisores (QSA) · 72.134 e-mail decisor inferido |
| desertos_medicos | densidade médica: 576.725 médicos · 45 desertos · 496 baixa cobertura |
| densidade_enfermagem | 435k enfermeiros · 954k técnicos · 148k auxiliares · 525 desertos |
| densidade_equipamento | deserto diagnóstico: 4.268 sem tomógrafo · 605 desertos (pop>20k) |
| mercado_saude | beneficiários ANS 2026-04 (88,9M) · 2.108 municípios <5% cobertura |
| municipios_perfil | PIB 2023 + per capita + % idosos (IBGE) |
| oportunidade_investimento | **índice 0-100: 548 ALTA · 1.257 sweet spots** |
| operadoras_ans | 1.112 · 799 com decisor QSA |
| **demanda_sih** | **PENDENTE — ver abaixo** |

## PENDENTE: SIH/SUS (volume de demanda) — bloqueado por outage do DATASUS
- **A parte difícil está resolvida:** `wins_hub_saude_dbc.py` = decoder `.dbc` em Python puro (pysus/datasus-dbc não compilam em py3.14). **Validado:** RS 2026-02 = 68.151 internações, R$ 142,4M (Porto Alegre no topo).
- **Bloqueio:** `ftp.datasus.gov.br` estava fora do ar (HEAD/GET timeout HTTP 000) na noite de 16/06. Externo, não é o código.
- **Para finalizar quando o DATASUS voltar:**
  ```
  python wins_hub_saude_sih.py     # baixa 3 meses x 27 UF, decodifica, anualiza -> demanda_sih
                                   # idempotente: pula arquivos já baixados em sih_tmp/
  ```
  Teste rápido se o DATASUS voltou:
  ```
  curl -sI --max-time 15 "https://ftp.datasus.gov.br/dissemin/publicos/SIHSUS/200801_/Dados/RDAC2602.dbc"
  ```
- **Depois do SIH**, re-rodar para incorporar demanda real ao índice:
  ```
  python wins_hub_saude_oportunidade.py   # (opcional: adicionar demanda_sih como eixo de demanda)
  python wins_hub_saude_dashboard.py      # regenerar dashboards
  ```
  > Nota: o índice atual usa demanda = população + % idosos. Com o SIH, dá pra trocar/somar
  > internações/mil como demanda *real*. Ajuste no CTE de `wins_hub_saude_oportunidade.py`.

## Mapa de arquivos (raiz = C:\Users\kbadmin\Documents\Projetos\WiNS Hub Saúde\)
- `wins_hub_app.py` — app Flask (3 páginas + API ao vivo)
- `wins_hub_saude_dashboard.py` — gera os 2 dashboards HTML (mapas Leaflet, toggle médico/enfermagem/oportunidade)
- `wins_hub_saude_oportunidade.py` — índice de oportunidade
- `wins_hub_saude_densidade_medica.py` / `_enfermagem.py` — densidades (CNES)
- `wins_hub_saude_dbc.py` — decoder .dbc puro-python (SIH)
- `wins_hub_saude_sih.py` — ingestão SIH (pendente DATASUS)
- demais: `_cnes_download.py`, `_importar.py`, `_views.sql`, `_enriquecer_qsa.py`, `_qsa_bulk.py`, `_leitos.py`, `_inferir_email.py`, `_ans_qsa.py`, `_desertos.py`
- ZIP CNES: `wins_hub_saude_dados\BASE_DE_DADOS_CNES_202605.ZIP` (725MB, não apagar)

## Decisões/limites assumidos (importante)
- **Não** construir lista nominal em massa de profissionais (PF) para revenda (LGPD/finalidade). Camadas vendáveis = **agregado territorial** + **decisor B2B**. Scraping de conselhos (CFF/CFO/COFFITO/CFN/COFEN) descartado — caminho legítimo seria convênio oficial.
- Dashboard público = só agregado (sem PII). Deploy dinâmico precisa de PaaS (não GitHub Pages).
