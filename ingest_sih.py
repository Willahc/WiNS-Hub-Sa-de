"""
Ingestao do VOLUME DE DEMANDA hospitalar do SIH/SUS por municipio de RESIDENCIA.
- Fonte: arquivos reduzidos de AIH (RD<UF><AAMM>.dbc) do DATASUS via FTP.
- AGREGADO em memoria por MUNIC_RES; grava SOMENTE totais por municipio.
- Nenhum registro individual / campo de paciente e persistido.
Caminho tecnico: ftplib (FTP nativo, porta 21) + datasus_dbc + dbfread,
rodando em Python 3.12 (pysus/datasus-dbc nao compilam em 3.14).
"""
import os, sys, time, re
from ftplib import FTP
from collections import defaultdict
import datasus_dbc
from dbfread import DBF
import psycopg2
from dotenv import dotenv_values

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CFG = dotenv_values(os.path.join(BASE_DIR, ".env.saude"))
SU = CFG["SUPERUSER_URL"]

FTP_HOST = "ftp.datasus.gov.br"
FTP_DIR = "/dissemin/publicos/SIHSUS/200801_/Dados"
TMP = os.path.join(BASE_DIR, "sih_tmp")
os.makedirs(TMP, exist_ok=True)

UFS = ["RO","AC","AM","RR","PA","AP","TO","MA","PI","CE","RN","PB","PE","AL","SE","BA",
       "MG","ES","RJ","SP","PR","SC","RS","MS","MT","GO","DF"]
# Janela completa de 12 meses publicados para as 27 UFs: abr/2025 a mar/2026
MONTHS = ["2504","2505","2506","2507","2508","2509","2510","2511","2512","2601","2602","2603"]
PERIODO = "2025-04 a 2026-03 (12 meses, RD/SIH-SUS)"

# acumuladores nacionais por municipio de residencia
internacoes = defaultdict(int)
valor = defaultdict(float)

def connect_ftp():
    for attempt in range(5):
        try:
            ftp = FTP(FTP_HOST, timeout=180)
            ftp.login()
            ftp.cwd(FTP_DIR)
            return ftp
        except Exception as e:
            print(f"  [ftp connect retry {attempt+1}] {type(e).__name__}: {e}", flush=True)
            time.sleep(5)
    raise RuntimeError("FTP connect failed")

def download(ftp, fn, dest):
    for attempt in range(4):
        try:
            with open(dest, "wb") as f:
                ftp.retrbinary("RETR " + fn, f.write)
            return True
        except Exception as e:
            print(f"  [retr retry {attempt+1} {fn}] {type(e).__name__}: {e}", flush=True)
            try: ftp.voidcmd("NOOP")
            except Exception:
                try: ftp.quit()
                except Exception: pass
                ftp = connect_ftp()
            time.sleep(4)
    return False

def process_file(ftp, fn):
    dbc = os.path.join(TMP, fn)
    dbf = dbc[:-4] + ".dbf"
    if not download(ftp, fn, dbc):
        return ftp, 0, "download_fail"
    try:
        datasus_dbc.decompress(dbc, dbf)
    except Exception as e:
        for p in (dbc, dbf):
            if os.path.exists(p): os.remove(p)
        return ftp, 0, f"decode_fail:{e}"
    n = 0
    try:
        for rec in DBF(dbf, encoding="latin-1", load=False):
            mr = rec.get("MUNIC_RES")
            vt = rec.get("VAL_TOT")
            try:
                cod = int(mr)
            except (TypeError, ValueError):
                continue
            internacoes[cod] += 1
            try:
                valor[cod] += float(vt) if vt is not None else 0.0
            except (TypeError, ValueError):
                pass
            n += 1
    finally:
        for p in (dbc, dbf):
            if os.path.exists(p):
                try: os.remove(p)
                except Exception: pass
    return ftp, n, "ok"

def main():
    ftp = connect_ftp()
    total_rows = 0
    t0 = time.time()
    for ym in MONTHS:
        for uf in UFS:
            fn = f"RD{uf}{ym}.dbc"
            try:
                ftp, n, status = process_file(ftp, fn)
            except (EOFError, OSError) as e:
                print(f"  [reconnect after {type(e).__name__}] {fn}", flush=True)
                try: ftp.quit()
                except Exception: pass
                ftp = connect_ftp()
                ftp, n, status = process_file(ftp, fn)
            total_rows += n
            if status != "ok":
                print(f"{fn}: {status}", flush=True)
        el = time.time() - t0
        print(f"== mes {ym} done. cum AIH={total_rows:,} municipios={len(internacoes)} elapsed={el:.0f}s", flush=True)
    try: ftp.quit()
    except Exception: pass

    print(f"\nTOTAL AIH processadas: {total_rows:,}", flush=True)
    print(f"Municipios distintos com internacao: {len(internacoes)}", flush=True)

    # ---- gravar agregados ----
    conn = psycopg2.connect(SU)
    conn.autocommit = False
    cur = conn.cursor()
    cur.execute("SELECT municipio_cod, municipio_nome, uf, populacao FROM desertos_medicos")
    munis = cur.fetchall()
    print(f"Municipios em desertos_medicos: {len(munis)}", flush=True)

    rows = []
    for cod, nome, uf, pop in munis:
        i = internacoes.get(cod, 0)
        v = round(valor.get(cod, 0.0), 2)
        ipm = round(i / pop * 1000, 2) if pop and pop > 0 else None
        vpc = round(v / pop, 2) if pop and pop > 0 else None
        rows.append((cod, nome, uf, pop, i, v, ipm, vpc, PERIODO))

    cur.executemany("""
        INSERT INTO demanda_sih
          (municipio_cod, municipio_nome, uf, populacao, internacoes, valor_total,
           internacoes_por_mil, valor_per_capita, periodo, captado_em)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW())
        ON CONFLICT (municipio_cod) DO UPDATE SET
          municipio_nome=EXCLUDED.municipio_nome, uf=EXCLUDED.uf, populacao=EXCLUDED.populacao,
          internacoes=EXCLUDED.internacoes, valor_total=EXCLUDED.valor_total,
          internacoes_por_mil=EXCLUDED.internacoes_por_mil,
          valor_per_capita=EXCLUDED.valor_per_capita,
          periodo=EXCLUDED.periodo, captado_em=NOW()
    """, rows)
    conn.commit()
    print(f"Upserted {len(rows)} municipios into demanda_sih.", flush=True)

    # quantos codigos do SIH nao casaram com desertos_medicos
    known = set(c for c,_,_,_ in munis)
    unmatched = [c for c in internacoes if c not in known]
    um_aih = sum(internacoes[c] for c in unmatched)
    print(f"Codigos MUNIC_RES sem match em desertos_medicos: {len(unmatched)} (AIH ignoradas: {um_aih:,})", flush=True)
    cur.close(); conn.close()

if __name__ == "__main__":
    main()
