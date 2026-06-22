# -*- coding: utf-8 -*-
"""
WiNS Hub Saude - Enriquecimento do decisor (QSA / Receita Federal) em operadoras_ans.

Usa os arquivos JA BAIXADOS dos Dados Abertos CNPJ (Socios0..9.zip + Qualificacoes.zip)
em C:\\Users\\kbadmin\\Documents\\wins_hub_saude_dados\\rfb_socios\\.

NAO baixa nada novo. NAO usa API por CNPJ. Idempotente.
"""
import os
import io
import csv
import sys
import zipfile
import unicodedata

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RFB_DIR = os.path.join(BASE_DIR, "wins_hub_saude_dados", "rfb_socios")
ENV_PATH = os.path.join(BASE_DIR, ".env.saude")

load_dotenv(ENV_PATH)
DATABASE_URL = os.environ["DATABASE_URL"]
SUPERUSER_URL = os.environ["SUPERUSER_URL"]


def strip_accents(s):
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).upper()


def rank_for(desc_norm):
    """Prioridade sobre a DESCRICAO (ja normalizada/uppercase/sem acento) da qualificacao."""
    has_pres_dir = ("PRESIDENTE" in desc_norm) or ("DIRETOR" in desc_norm)
    has_admin = "ADMINISTRADOR" in desc_norm
    has_socio = "SOCIO" in desc_norm
    if has_pres_dir:
        return 0
    if has_admin and not has_socio:
        return 1
    if has_admin and has_socio:
        return 2
    if has_socio:
        return 3
    return 4


def main():
    # ------------------------------------------------------------------ 1. DDL
    print("[1] DDL (idempotente) via SUPERUSER_URL...")
    ddl = psycopg2.connect(SUPERUSER_URL)
    ddl.autocommit = True
    with ddl.cursor() as cur:
        cur.execute("ALTER TABLE operadoras_ans ADD COLUMN IF NOT EXISTS decisor_qsa_nome TEXT;")
        cur.execute("ALTER TABLE operadoras_ans ADD COLUMN IF NOT EXISTS decisor_qsa_cargo TEXT;")
        cur.execute("ALTER TABLE operadoras_ans ADD COLUMN IF NOT EXISTS fonte_decisor VARCHAR(30);")
    ddl.close()
    print("    colunas garantidas: decisor_qsa_nome, decisor_qsa_cargo, fonte_decisor")

    # ------------------------------------------------- 2. raizes alvo (8 digitos)
    conn = psycopg2.connect(DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT substr(cnpj,1,8) FROM operadoras_ans "
            "WHERE cnpj ~ '^[0-9]{14}$';"
        )
        target_roots = {r[0] for r in cur.fetchall()}
    print(f"[2] Raizes de CNPJ alvo (operadoras com CNPJ de 14 digitos): {len(target_roots)}")

    # ----------------------------------------------------- 3. lookup Qualificacoes
    qual = {}
    qpath = os.path.join(RFB_DIR, "Qualificacoes.zip")
    with zipfile.ZipFile(qpath) as zf:
        name = zf.namelist()[0]
        with zf.open(name) as raw:
            text = io.TextIOWrapper(raw, encoding="latin-1", newline="")
            reader = csv.reader(text, delimiter=";", quotechar='"')
            for row in reader:
                if len(row) >= 2:
                    qual[row[0].strip()] = row[1].strip()
    print(f"[3] Qualificacoes carregadas: {len(qual)}")

    # ------------------------------------------------- 4. streaming Socios*.zip
    # best[raiz] = (rank, nome_title, descricao_cargo)
    best = {}
    total_socio_lines = 0
    matched_lines = 0
    for i in range(10):
        zpath = os.path.join(RFB_DIR, f"Socios{i}.zip")
        with zipfile.ZipFile(zpath) as zf:
            name = zf.namelist()[0]
            with zf.open(name) as raw:
                text = io.TextIOWrapper(raw, encoding="latin-1", newline="")
                reader = csv.reader(text, delimiter=";", quotechar='"')
                for row in reader:
                    total_socio_lines += 1
                    if len(row) < 5:
                        continue
                    raiz = row[0].strip()
                    if raiz not in target_roots:
                        continue
                    matched_lines += 1
                    nome = row[2].strip()
                    qcod = row[4].strip()
                    desc = qual.get(qcod, "")
                    desc_norm = strip_accents(desc)
                    rk = rank_for(desc_norm)
                    cur_best = best.get(raiz)
                    if cur_best is None or rk < cur_best[0]:
                        best[raiz] = (rk, nome.title(), desc)
        print(f"    Socios{i}.zip processado | linhas acumuladas={total_socio_lines:,} | "
              f"socios alvo acumulados={matched_lines:,} | raizes com decisor={len(best):,}")

    print(f"[4] Streaming concluido. Linhas totais lidas={total_socio_lines:,} | "
          f"linhas de socios em raizes alvo={matched_lines:,} | "
          f"raizes alvo com pelo menos 1 socio={len(best):,}")

    # ------------------------------------------------- 5. UPDATE via temp + JOIN
    print("[5] Aplicando UPDATE via temp table + JOIN...")
    rows = [(raiz, val[1], val[2]) for raiz, val in best.items()]
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TEMP TABLE _qsa_decisor ("
            "raiz char(8) PRIMARY KEY, nome text, cargo text) ON COMMIT DROP;"
        )
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO _qsa_decisor (raiz, nome, cargo) VALUES %s",
            rows,
            page_size=1000,
        )
        cur.execute(
            "UPDATE operadoras_ans o "
            "SET decisor_qsa_nome = d.nome, "
            "    decisor_qsa_cargo = d.cargo, "
            "    fonte_decisor = 'QSA_RFB_BULK' "
            "FROM _qsa_decisor d "
            "WHERE substr(o.cnpj,1,8) = d.raiz;"
        )
        updated = cur.rowcount
    conn.commit()
    print(f"    operadoras atualizadas (rowcount): {updated}")

    # ------------------------------------------------------------ 6. Relatorios
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM operadoras_ans WHERE fonte_decisor = 'QSA_RFB_BULK';"
        )
        enriquecidas = cur.fetchone()[0]

        cur.execute("SELECT count(*) FROM operadoras_ans;")
        total_op = cur.fetchone()[0]

        # operadoras com CNPJ de 14 digitos sem socio no QSA
        cur.execute(
            "SELECT count(*) FROM operadoras_ans "
            "WHERE cnpj ~ '^[0-9]{14}$' "
            "  AND substr(cnpj,1,8) NOT IN (SELECT substr(cnpj,1,8) FROM operadoras_ans o2 "
            "                               WHERE o2.fonte_decisor='QSA_RFB_BULK');"
        )
        sem_socio_qsa = cur.fetchone()[0]

        # tem representante ANS mas (agora) sem QSA
        cur.execute(
            "SELECT count(*) FROM operadoras_ans "
            "WHERE representante IS NOT NULL AND representante <> '' "
            "  AND fonte_decisor IS DISTINCT FROM 'QSA_RFB_BULK';"
        )
        rep_sem_qsa = cur.fetchone()[0]

        cur.execute(
            "SELECT razao_social, decisor_qsa_nome, decisor_qsa_cargo "
            "FROM operadoras_ans WHERE fonte_decisor = 'QSA_RFB_BULK' "
            "ORDER BY razao_social LIMIT 8;"
        )
        amostra = cur.fetchall()

    conn.close()

    print()
    print("================= RELATORIO FINAL =================")
    print(f"Total de operadoras na tabela.................: {total_op}")
    print(f"Operadoras ENRIQUECIDAS com decisor QSA.......: {enriquecidas}")
    print(f"Operadoras (CNPJ 14 dig) SEM socio no QSA.....: {sem_socio_qsa}")
    print(f"Tem representante ANS mas SEM QSA agora.......: {rep_sem_qsa}")
    print()
    print("Amostra (razao_social | decisor_qsa_nome | decisor_qsa_cargo):")
    for rs, nm, cg in amostra:
        print(f"  - {rs[:45]:45} | {str(nm)[:30]:30} | {cg}")
    print("===================================================")


if __name__ == "__main__":
    main()
