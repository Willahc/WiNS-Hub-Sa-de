"""
WiNS Hub Saude - Ingestao SIH/SUS PARALELA (acelerada)
======================================================
Igual ao wins_hub_saude_sih.py, mas baixa+decodifica varios arquivos em
paralelo (ProcessPoolExecutor), cada worker com sua propria conexao FTP.
Via FTP (porta 21) porque o HTTP/HTTPS do DATASUS cai. So agregado, sem PII.

Uso: python wins_hub_saude_sih_parallel.py
"""
import os, sys, json, time
from ftplib import FTP, error_perm
from concurrent.futures import ProcessPoolExecutor, as_completed

import dbfread
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import execute_values
import wins_hub_saude_dbc as dbc

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env.saude"))
TMP = os.path.join(BASE_DIR, "sih_tmp")
os.makedirs(TMP, exist_ok=True)
CKPT = os.path.join(BASE_DIR, "wins_hub_saude_sih_checkpoint.json")

FTP_HOST = "ftp.datasus.gov.br"
FTP_DIR = "/dissemin/publicos/SIHSUS/200801_/Dados"
UFS = ["AC","AL","AP","AM","BA","CE","DF","ES","GO","MA","MT","MS","MG","PA","PB","PR",
       "PE","PI","RJ","RN","RS","RO","RR","SC","SP","SE","TO"]
N_MESES = 3
WORKERS = 6  # conexoes FTP simultaneas


def _ftp_conn():
    f = FTP(FTP_HOST, timeout=120)
    f.login()
    f.cwd(FTP_DIR)
    f.voidcmd("TYPE I")
    return f


def baixar_decode(tarefa):
    """Worker: baixa e decodifica UM arquivo RD<uf><ym>.dbc -> agrega por MUNIC_RES."""
    uf, ym = tarefa
    name = f"RD{uf}{ym}.dbc"
    dest = os.path.join(TMP, name)
    # download (com 1 retry)
    for tent in range(2):
        try:
            f = _ftp_conn()
            with open(dest, "wb") as fh:
                f.retrbinary("RETR " + name, fh.write)
            try:
                f.quit()
            except Exception:
                pass
            break
        except error_perm:
            return (uf, ym, 0, {}, "inexistente")
        except Exception as e:
            if tent == 1:
                return (uf, ym, 0, {}, f"erro download: {e}")
            time.sleep(2)
    # decode
    dbf = dest[:-4] + ".dbf"
    agg = {}
    try:
        dbc.dbc_to_dbf(dest, dbf)
        for rec in dbfread.DBF(dbf, encoding="latin-1", load=False):
            mr = (rec.get("MUNIC_RES") or "").strip()
            if not mr.isdigit():
                continue
            cod = int(mr)
            v = rec.get("VAL_TOT") or 0
            try:
                v = float(v)
            except (TypeError, ValueError):
                v = 0.0
            a = agg.get(cod)
            if a is None:
                agg[cod] = [1, v]
            else:
                a[0] += 1; a[1] += v
        n = sum(a[0] for a in agg.values())
        return (uf, ym, n, agg, "ok")
    except Exception as e:
        return (uf, ym, 0, {}, f"erro decode: {e}")
    finally:
        for p in (dest, dbf):
            try:
                os.remove(p)
            except OSError:
                pass


def meses_completos(n):
    """Os n meses mais recentes com cobertura total (27/27 UF)."""
    f = _ftp_conn()
    achados = []
    ano, mes = 2026, 6
    for _ in range(18):
        ym = f"{ano % 100:02d}{mes:02d}"
        ok = True
        for uf in UFS:
            try:
                f.size(f"RD{uf}{ym}.dbc")
            except error_perm:
                ok = False; break
            except Exception:
                f = _ftp_conn()
                try:
                    f.size(f"RD{uf}{ym}.dbc")
                except Exception:
                    ok = False; break
        if ok:
            achados.append(ym)
            if len(achados) >= n:
                break
        mes -= 1
        if mes == 0:
            mes = 12; ano -= 1
    try:
        f.quit()
    except Exception:
        pass
    return achados


def main():
    t0 = time.time()
    meses = meses_completos(N_MESES)
    if not meses:
        sys.exit("Nenhum mes completo do SIH encontrado.")
    fator = 12.0 / len(meses)
    print(f"Meses completos: {meses} | fator anualizacao x{fator:.2f} | workers={WORKERS}", flush=True)

    tarefas = [(uf, ym) for ym in meses for uf in UFS]
    agg = {}
    total_aih = 0
    done = 0
    with ProcessPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(baixar_decode, t): t for t in tarefas}
        for fut in as_completed(futs):
            uf, ym, n, a, st = fut.result()
            done += 1
            for cod, val in a.items():
                x = agg.get(cod)
                if x is None:
                    agg[cod] = [val[0], val[1]]
                else:
                    x[0] += val[0]; x[1] += val[1]
            total_aih += n
            print(f"  [{done}/{len(tarefas)}] {uf}{ym}: {n:,} AIH [{st}] "
                  f"(acum {total_aih:,}) {time.time()-t0:.0f}s", flush=True)
            json.dump({"feitos": done, "de": len(tarefas), "aih": total_aih,
                       "municipios": len(agg)}, open(CKPT, "w"))

    print(f"\nDecode concluido: {total_aih:,} AIH, {len(agg):,} municipios em {time.time()-t0:.0f}s. "
          f"Anualizando x{fator:.2f} e gravando...", flush=True)

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    super_url = os.environ.get("SUPERUSER_URL", os.environ["DATABASE_URL"])
    sc = psycopg2.connect(super_url)
    with sc.cursor() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS demanda_sih (
                municipio_cod INTEGER PRIMARY KEY, municipio_nome TEXT, uf CHAR(2), populacao INTEGER,
                internacoes BIGINT DEFAULT 0, valor_total NUMERIC DEFAULT 0,
                internacoes_por_mil NUMERIC(8,2), valor_per_capita NUMERIC(10,2),
                periodo VARCHAR(40), captado_em TIMESTAMP DEFAULT NOW());
            GRANT ALL ON demanda_sih TO wins_saude;
        """)
    sc.commit(); sc.close()

    with conn.cursor() as cur:
        cur.execute("SELECT municipio_cod, municipio_nome, uf, populacao FROM desertos_medicos")
        info = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}

    periodo = f"{meses[-1]}..{meses[0]} (x{fator:.1f} anualizado)"
    linhas = []
    for cod, (nome, uf, pop) in info.items():
        c_int, c_val = agg.get(cod, (0, 0.0))
        intern = round(c_int * fator)
        valor = round(c_val * fator, 2)
        ipm = round(intern / pop * 1000, 2) if pop else 0
        vpc = round(valor / pop, 2) if pop else 0
        linhas.append((cod, nome, uf, pop, intern, valor, ipm, vpc, periodo))

    with conn.cursor() as cur:
        cur.execute("TRUNCATE demanda_sih")
        execute_values(cur, """
            INSERT INTO demanda_sih (municipio_cod,municipio_nome,uf,populacao,internacoes,
              valor_total,internacoes_por_mil,valor_per_capita,periodo) VALUES %s
        """, linhas, page_size=10000)
    conn.commit()

    with conn.cursor() as cur:
        cur.execute("SELECT sum(internacoes), round(sum(valor_total)), round(avg(internacoes_por_mil),1) FROM demanda_sih")
        tot = cur.fetchone()
        cur.execute("""SELECT municipio_nome, uf, internacoes, round(valor_total)
                       FROM demanda_sih ORDER BY valor_total DESC LIMIT 10""")
        top = cur.fetchall()
    conn.close()

    print("\n" + "=" * 60)
    print(f"SIH gravado. Internacoes/ano (estim): {tot[0]:,} | R$ {tot[1]:,} | media {tot[2]}/mil")
    print("Top 10 municipios por valor:")
    for nome, uf, it, vl in top:
        print(f"  {nome}-{uf:<3} {it:>8,} internacoes  R$ {vl:>14,.0f}")
    print("=" * 60)


if __name__ == "__main__":
    sys.exit(main())
