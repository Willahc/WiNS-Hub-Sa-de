"""
WiNS Hub Saude - Fase 2: inferencia de e-mail do decisor por dominio
====================================================================
Para estabelecimentos que ja tem decisor_nome (do QSA) mas nao tem
decisor_email, infere o e-mail do decisor a partir do DOMINIO PROPRIO do
e-mail do estabelecimento, usando o padrao corporativo dominante no Brasil:

    primeiro.ultimo@dominio        (ex: "Maria Silva Santos" @clinica.com.br
                                    -> maria.santos@clinica.com.br)

So funciona para dominio CORPORATIVO. Provedores gratuitos (gmail, hotmail,
yahoo, uol, ...) sao marcados como DOMINIO_GRATUITO e NAO recebem palpite
(uma conta pessoal nao tem padrao de dominio).

IMPORTANTE: o e-mail inferido NAO esta verificado. Recebe
decisor_email_status='INFERIDO_DOMINIO'. A verificacao SMTP/MX e etapa
seguinte; ate la, tratar como palpite de padrao.

Uso:
    python wins_hub_saude_inferir_email.py
"""

import os
import re
import sys
import unicodedata

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import execute_values

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env.saude"))

# Provedores gratuitos / pessoais: dominio nao permite inferir e-mail de pessoa.
FREE = {
    "gmail.com", "gmail.com.br", "googlemail.com",
    "hotmail.com", "hotmail.com.br", "hotmail.es",
    "outlook.com", "outlook.com.br", "live.com", "msn.com",
    "yahoo.com", "yahoo.com.br", "ymail.com", "rocketmail.com",
    "uol.com.br", "bol.com.br", "ig.com.br", "terra.com.br",
    "globo.com", "globomail.com", "r7.com", "oi.com.br",
    "superig.com.br", "zipmail.com.br", "pop.com.br",
    "icloud.com", "me.com", "mac.com",
    "aol.com", "protonmail.com", "proton.me", "yandex.com",
}

DDL = """
ALTER TABLE estabelecimentos
    ADD COLUMN IF NOT EXISTS decisor_email_status VARCHAR(30);
"""


def reconciliar(super_url):
    conn = psycopg2.connect(super_url)
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()
    conn.close()
    print("  coluna decisor_email_status garantida.")


def slug(token: str) -> str:
    t = unicodedata.normalize("NFKD", token or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z]", "", t.lower())


def extrair_dominio(email: str) -> str | None:
    # pega o primeiro e-mail caso haja varios separados por ; / , espaco
    primeiro = re.split(r"[;,/ ]", (email or "").strip())[0]
    if "@" not in primeiro:
        return None
    dom = primeiro.split("@", 1)[1].lower().strip().strip(".")
    return dom or None


# Sufixos (nao sao sobrenome) e particulas (preposicoes do nome)
SUFIXOS = {"junior", "jr", "filho", "neto", "sobrinho", "segundo",
           "sobrinha", "filha", "neta", "terceiro"}
PARTICULAS = {"da", "de", "do", "dos", "das", "e", "del", "di", "du",
              "la", "le", "van", "von", "di"}


def inferir_local(nome: str) -> str | None:
    partes = [p for p in (slug(x) for x in (nome or "").split()) if p]
    if not partes:
        return None
    primeiro = partes[0]
    # sobrenome = ultimo token real (pula sufixos como Junior/Filho e particulas)
    sobrenome = None
    for tok in reversed(partes[1:]):
        if tok in SUFIXOS or tok in PARTICULAS:
            continue
        sobrenome = tok
        break
    return f"{primeiro}.{sobrenome}" if sobrenome else primeiro


def main():
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    super_url = os.environ.get("SUPERUSER_URL")

    print("=" * 60)
    print("WiNS Hub Saude - Inferencia de e-mail do decisor por dominio")
    print("=" * 60)

    print("\n[schema] garantindo coluna decisor_email_status...")
    reconciliar(super_url or os.environ["DATABASE_URL"])

    print("\n[select] alvos: decisor sem e-mail e estabelecimento com e-mail...")
    with conn.cursor() as cur:
        cur.execute("""
            SELECT cnes_id, decisor_nome, email
            FROM estabelecimentos
            WHERE decisor_nome IS NOT NULL
              AND email LIKE '%@%'
              AND (
                    (decisor_email_status IS NULL AND (decisor_email IS NULL OR decisor_email = ''))
                 OR decisor_email_status = 'INFERIDO_DOMINIO'
              )
        """)
        alvos = cur.fetchall()
    print(f"  {len(alvos):,} alvos.")

    inferidos, gratuitos, invalidos = [], 0, 0
    updates = []  # (cnes_id, email_or_None, status)
    for cnes_id, nome, email in alvos:
        dom = extrair_dominio(email)
        if not dom or "." not in dom:
            invalidos += 1
            updates.append((cnes_id, None, "DOMINIO_INVALIDO"))
            continue
        if dom in FREE:
            gratuitos += 1
            updates.append((cnes_id, None, "DOMINIO_GRATUITO"))
            continue
        local = inferir_local(nome)
        if not local:
            invalidos += 1
            updates.append((cnes_id, None, "NOME_INVALIDO"))
            continue
        email_inf = f"{local}@{dom}"
        updates.append((cnes_id, email_inf, "INFERIDO_DOMINIO"))
        inferidos.append(email_inf)

    print("\n[update] gravando...")
    with conn.cursor() as cur:
        execute_values(cur, """
            UPDATE estabelecimentos e
               SET decisor_email = v.email,
                   decisor_email_status = v.status
              FROM (VALUES %s) AS v(cnes_id, email, status)
             WHERE e.cnes_id = v.cnes_id
        """, updates, template="(%s::int, %s::text, %s::text)", page_size=10000)
    conn.commit()

    with conn.cursor() as cur:
        cur.execute("""
            SELECT decisor_email_status, count(*)
            FROM estabelecimentos WHERE decisor_email_status IS NOT NULL
            GROUP BY 1 ORDER BY 2 DESC
        """)
        resumo = cur.fetchall()
    conn.close()

    print("\n" + "=" * 60)
    print("RESUMO")
    print(f"  e-mails inferidos (corporativo) : {len(inferidos):,}")
    print(f"  dominio gratuito (sem palpite)  : {gratuitos:,}")
    print(f"  dominio/nome invalido           : {invalidos:,}")
    print("  --- decisor_email_status no banco ---")
    for s, c in resumo:
        print(f"  {s:<22} {c:,}")
    if inferidos:
        print("  --- amostras ---")
        for e in inferidos[:5]:
            print(f"    {e}")
    print("=" * 60)


if __name__ == "__main__":
    sys.exit(main())
