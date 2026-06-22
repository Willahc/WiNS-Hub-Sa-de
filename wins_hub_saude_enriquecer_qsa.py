"""
WiNS Hub Saude - Enriquecimento de decisores via QSA (BrasilAPI)
================================================================
Popula decisor_nome / decisor_cargo dos estabelecimentos com CNPJ,
extraindo o socio prioritario do Quadro de Socios e Administradores (QSA)
da Receita Federal, exposto pela BrasilAPI (gratuita, sem auth).

    CNPJ -> https://brasilapi.com.br/api/cnpj/v1/{cnpj} -> qsa[] -> decisor

Caracteristicas:
  - Retomavel: o proprio estado do banco (fonte_enriquecimento) define o que
    falta; alem disso grava checkpoint em wins_hub_saude_qsa_checkpoint.json.
  - Idempotente: so processa linhas com decisor_nome IS NULL e
    fonte_enriquecimento IS NULL (erros transitorios ficam com fonte NULL e
    sao re-tentados; resultados definitivos recebem uma fonte e sao pulados).
  - Prioriza hospitais/clinicas (tipo_unidade_cod 1,2,4,5,7,36).
  - Cache por CNPJ dentro da execucao (evita repetir chamada p/ CNPJ duplicado).
  - Rate limit 0.3s; 429 -> espera 60s e re-tenta; timeout -> backoff 2x.
  - Erros em wins_hub_saude_qsa_erros.log (separado do stdout/tqdm).

Uso:
    python wins_hub_saude_enriquecer_qsa.py --limit 500   # teste
    python wins_hub_saude_enriquecer_qsa.py --all         # base completa
    (sem argumento: assume --limit 500)

Requisitos: requests, tqdm, psycopg2-binary, python-dotenv
"""

import os
import re
import sys
import json
import time
import logging
import argparse
import unicodedata

import requests
from tqdm import tqdm
from dotenv import load_dotenv
import psycopg2

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env.saude"))

CHECKPOINT = os.path.join(BASE_DIR, "wins_hub_saude_qsa_checkpoint.json")
ERR_LOG = os.path.join(BASE_DIR, "wins_hub_saude_qsa_erros.log")

API = "https://brasilapi.com.br/api/cnpj/v1/{cnpj}"
SLEEP_REQ = 0.3
TIPOS_PRIORITARIOS = (1, 2, 4, 5, 7, 36)
COMMIT_A_CADA = 50

# log de erros separado do stdout
logging.basicConfig(
    filename=ERR_LOG, level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("qsa")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "WiNS-Hub-Saude/1.0 (enriquecimento QSA dados abertos)"})


# ─────────────────────────────────────────
# SELECAO DO DECISOR PRIORITARIO
# ─────────────────────────────────────────
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.upper().strip()


def _rank(qualificacao: str) -> int:
    q = _norm(qualificacao)
    if "PRESIDENTE" in q or "DIRETOR" in q:
        return 0
    if "ADMINISTRADOR" in q and "SOCIO" not in q:
        return 1
    if "ADMINISTRADOR" in q and "SOCIO" in q:
        return 2
    if "SOCIO" in q:
        return 3
    return 4


def escolher_decisor(qsa: list) -> dict | None:
    """Retorna o socio de maior prioridade, ou None se QSA vazio."""
    if not qsa:
        return None
    melhor = min(qsa, key=lambda s: _rank(s.get("qualificacao_socio", "")))
    return melhor


def _titulo(nome: str) -> str:
    return (nome or "").strip().title()


# ─────────────────────────────────────────
# CHAMADA BRASILAPI
# ─────────────────────────────────────────
class CnpjInvalido(Exception):
    pass


def consultar_brasilapi(cnpj: str) -> dict:
    """Retorna o JSON da BrasilAPI. Levanta CnpjInvalido em 404.
    Trata 429 (espera 60s) e timeouts (backoff)."""
    url = API.format(cnpj=cnpj)
    tentativas = 0
    while True:
        try:
            r = SESSION.get(url, timeout=30)
        except (requests.Timeout, requests.ConnectionError) as e:
            tentativas += 1
            if tentativas > 2:
                raise
            time.sleep(2 * tentativas + 1)  # 3s, 5s
            continue

        if r.status_code == 200:
            return r.json()
        if r.status_code == 404:
            raise CnpjInvalido(cnpj)
        if r.status_code == 429:
            log.warning("429 rate limit em %s - aguardando 60s", cnpj)
            time.sleep(60)
            continue
        # 5xx ou outros: re-tenta com backoff
        tentativas += 1
        if tentativas > 2:
            r.raise_for_status()
        time.sleep(2 * tentativas + 1)


# ─────────────────────────────────────────
# CHECKPOINT
# ─────────────────────────────────────────
def salvar_checkpoint(stats: dict):
    stats = dict(stats)
    stats["atualizado_em"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(CHECKPOINT, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────
# PIPELINE
# ─────────────────────────────────────────
SQL_CANDIDATOS = """
SELECT cnes_id, cnpj, tipo_unidade_cod
FROM estabelecimentos
WHERE cnpj IS NOT NULL AND cnpj <> ''
  AND decisor_nome IS NULL
  AND fonte_enriquecimento IS NULL
ORDER BY (CASE WHEN tipo_unidade_cod = ANY(%s) THEN 0 ELSE 1 END), cnes_id
{limite}
"""

SQL_UPDATE = """
UPDATE estabelecimentos
SET decisor_nome = %s,
    decisor_cargo = %s,
    fonte_enriquecimento = %s,
    enriquecido_em = NOW()
WHERE cnes_id = %s
"""


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--limit", type=int, help="processar no maximo N estabelecimentos")
    g.add_argument("--all", action="store_true", help="processar a base completa")
    args = ap.parse_args()
    limite = None if args.all else (args.limit or 500)

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()

    lim_sql = "" if limite is None else f"LIMIT {int(limite)}"
    cur.execute(SQL_CANDIDATOS.format(limite=lim_sql), (list(TIPOS_PRIORITARIOS),))
    candidatos = cur.fetchall()

    modo = "BASE COMPLETA" if limite is None else f"TESTE (limit {limite})"
    print("=" * 60)
    print(f"WiNS Hub Saude - Enriquecimento QSA - {modo}")
    print(f"Candidatos a processar: {len(candidatos):,}")
    print(f"Log de erros: {ERR_LOG}")
    print("=" * 60)

    stats = {"processados": 0, "enriquecidos": 0, "qsa_vazio": 0,
             "cnpj_invalido": 0, "erros": 0, "cache_hits": 0, "limite": limite}
    cache: dict[str, tuple] = {}  # cnpj -> (fonte, nome, cargo)
    upd = cur  # mesmo cursor para updates

    try:
        for cnes_id, cnpj_raw, _tipo in tqdm(candidatos, desc="QSA", unit="estab"):
            cnpj = re.sub(r"\D", "", cnpj_raw or "")
            if len(cnpj) != 14:
                upd.execute(SQL_UPDATE, (None, None, "CNPJ_INVALIDO", cnes_id))
                stats["cnpj_invalido"] += 1
                stats["processados"] += 1
                continue

            if cnpj in cache:
                fonte, nome, cargo = cache[cnpj]
                stats["cache_hits"] += 1
            else:
                try:
                    data = consultar_brasilapi(cnpj)
                    decisor = escolher_decisor(data.get("qsa") or [])
                    if decisor:
                        fonte = "QSA_BRASILAPI"
                        nome = _titulo(decisor.get("nome_socio"))
                        cargo = (decisor.get("qualificacao_socio") or "").strip() or None
                    else:
                        fonte, nome, cargo = "QSA_VAZIO", None, None
                except CnpjInvalido:
                    fonte, nome, cargo = "CNPJ_INVALIDO", None, None
                except Exception as e:  # noqa: BLE001
                    log.warning("erro cnes=%s cnpj=%s: %s", cnes_id, cnpj, e)
                    stats["erros"] += 1
                    stats["processados"] += 1
                    time.sleep(SLEEP_REQ)
                    continue
                finally:
                    pass
                cache[cnpj] = (fonte, nome, cargo)
                time.sleep(SLEEP_REQ)  # rate limit so em chamada real

            upd.execute(SQL_UPDATE, (nome, cargo, fonte, cnes_id))
            stats["processados"] += 1
            if fonte == "QSA_BRASILAPI":
                stats["enriquecidos"] += 1
            elif fonte == "QSA_VAZIO":
                stats["qsa_vazio"] += 1
            elif fonte == "CNPJ_INVALIDO":
                stats["cnpj_invalido"] += 1

            if stats["processados"] % COMMIT_A_CADA == 0:
                conn.commit()
                salvar_checkpoint(stats)

    except KeyboardInterrupt:
        print("\nInterrompido pelo usuario - salvando progresso...")
    finally:
        conn.commit()
        salvar_checkpoint(stats)
        cur.close()
        conn.close()

    print("\n" + "=" * 60)
    print("RESUMO")
    for k in ["processados", "enriquecidos", "qsa_vazio", "cnpj_invalido", "erros", "cache_hits"]:
        print(f"  {k:<16} {stats[k]:>8,}")
    print("=" * 60)


if __name__ == "__main__":
    sys.exit(main())
