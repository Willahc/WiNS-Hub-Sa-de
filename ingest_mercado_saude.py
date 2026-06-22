#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Ingestao ANS - beneficiarios de planos de saude por municipio.
Fonte: https://dadosabertos.ans.gov.br/FTP/PDA/informacoes_consolidadas_de_beneficiarios-024/
Agrega QT_BENEFICIARIO_ATIVO por CD_MUNICIPIO (6 digitos IBGE), somando todas
as operadoras/modalidades/planos. Apenas agregado por municipio - sem PII.
Idempotente: ON CONFLICT (municipio_cod) DO UPDATE.
"""
import os, sys, io, csv, zipfile, tempfile, time
import requests
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env.saude"))
SUPERUSER_URL = os.environ["SUPERUSER_URL"]

COMPETENCIA_DIR = "202604"          # pasta FTP
COMPETENCIA = "2026-04"             # valor gravado (ID_CMPT_MOVEL)
BASE = ("https://dadosabertos.ans.gov.br/FTP/PDA/"
        "informacoes_consolidadas_de_beneficiarios-024/")
UFS = ["AC","AL","AM","AP","BA","CE","DF","ES","GO","MA","MG","MS","MT","PA",
       "PB","PE","PI","PR","RJ","RN","RO","RR","RS","SC","SE","SP","TO"]
# XX excluido: nao corresponde a municipio (desconhecido/exterior)

def stream_download(url, dest):
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 20):
                fh.write(chunk)

def main():
    benef_por_mun = {}   # cd_municipio(int) -> soma QT_BENEFICIARIO_ATIVO
    total_linhas = 0
    t0 = time.time()

    for uf in UFS:
        fname = f"pda-024-icb-{uf}-{COMPETENCIA_DIR[:4]}_{COMPETENCIA_DIR[4:]}.zip"
        url = f"{BASE}{COMPETENCIA_DIR}/{fname}"
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tf:
            tmp = tf.name
        try:
            stream_download(url, tmp)
            with zipfile.ZipFile(tmp) as z:
                member = z.namelist()[0]
                with z.open(member) as raw:
                    text = io.TextIOWrapper(raw, encoding="latin-1", newline="")
                    reader = csv.reader(text, delimiter=";")
                    header = next(reader)
                    idx_mun = header.index("CD_MUNICIPIO")
                    idx_qt = header.index("QT_BENEFICIARIO_ATIVO")
                    n = 0
                    for row in reader:
                        if len(row) <= idx_qt:
                            continue
                        cd = row[idx_mun].strip().strip('"')
                        qt = row[idx_qt].strip().strip('"')
                        if not cd or not cd.isdigit():
                            continue
                        try:
                            q = int(qt) if qt else 0
                        except ValueError:
                            q = 0
                        benef_por_mun[int(cd)] = benef_por_mun.get(int(cd), 0) + q
                        n += 1
                    total_linhas += n
            print(f"  {uf}: {n} linhas  (acum municipios={len(benef_por_mun)})",
                  flush=True)
        finally:
            try: os.remove(tmp)
            except OSError: pass

    print(f"Linhas processadas: {total_linhas}  | municipios ANS: {len(benef_por_mun)}"
          f"  | tempo: {time.time()-t0:.0f}s", flush=True)
    total_nacional = sum(benef_por_mun.values())
    print(f"Total nacional beneficiarios ativos: {total_nacional}", flush=True)

    # --- gravar no banco ---
    conn = psycopg2.connect(SUPERUSER_URL)
    conn.autocommit = False
    cur = conn.cursor()

    # base: todos os 5570 municipios de desertos_medicos
    cur.execute("SELECT municipio_cod, municipio_nome, uf, populacao FROM desertos_medicos")
    base = cur.fetchall()

    rows = []
    for cod, nome, uf, pop in base:
        b = benef_por_mun.get(cod, 0)
        pct = round(b / pop * 100, 2) if pop and pop > 0 else None
        rows.append((cod, nome, uf, pop, b, pct, COMPETENCIA))

    execute_values(cur, """
        INSERT INTO mercado_saude
          (municipio_cod, municipio_nome, uf, populacao, beneficiarios,
           cobertura_privada_pct, competencia)
        VALUES %s
        ON CONFLICT (municipio_cod) DO UPDATE SET
          municipio_nome = EXCLUDED.municipio_nome,
          uf             = EXCLUDED.uf,
          populacao      = EXCLUDED.populacao,
          beneficiarios  = EXCLUDED.beneficiarios,
          cobertura_privada_pct = EXCLUDED.cobertura_privada_pct,
          competencia    = EXCLUDED.competencia,
          captado_em     = NOW()
    """, rows, page_size=1000)
    conn.commit()

    # municipios ANS sem correspondencia em desertos_medicos (diagnostico)
    base_cods = {c for c, *_ in base}
    orfaos = [(c, v) for c, v in benef_por_mun.items() if c not in base_cods]
    orfao_benef = sum(v for _, v in orfaos)
    print(f"Municipios ANS sem match em desertos_medicos: {len(orfaos)} "
          f"(beneficiarios nao mapeados: {orfao_benef})", flush=True)

    cur.close()
    conn.close()
    print("OK - mercado_saude populada.", flush=True)

if __name__ == "__main__":
    main()
