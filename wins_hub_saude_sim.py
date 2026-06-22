"""
WiNS Hub Saude - Ingestao SIM/SUS (mortalidade por municipio de residencia)
===========================================================================
Baixa via FTP (porta 21) os arquivos anuais DO<UF><AAAA>.dbc do SIM/CID10/DORES,
decodifica com o decoder puro (wins_hub_saude_dbc.py) em PARALELO, agrega por
CODMUNRES: obitos totais e obitos infantis (idade < 1 ano). So agregado, sem PII.
Grava em mortalidade_sim. Usa o ano mais recente disponivel (auto-deteccao).

Uso: python wins_hub_saude_sim.py
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

FTP_HOST = "ftp.datasus.gov.br"
FTP_DIR = "/dissemin/publicos/SIM/CID10/DORES"
UFS = ["AC","AL","AP","AM","BA","CE","DF","ES","GO","MA","MT","MS","MG","PA","PB","PR",
       "PE","PI","RJ","RN","RS","RO","RR","SC","SP","SE","TO"]
WORKERS = 6


def _ftp_conn():
    f = FTP(FTP_HOST, timeout=120)
    f.login()
    f.cwd(FTP_DIR)
    f.voidcmd("TYPE I")
    return f


def _is_infantil(idade):
    """IDADE do SIM (3 chars): 1o digito = unidade (1=min,2=horas,3=dias/meses,
    4=anos,5=100+anos). Idade < 1 ano = unidade < 4, ou '400' (0 anos)."""
    if not idade or len(idade) < 3:
        return False
    return idade[0] in "0123" or idade == "400"


def baixar_decode(tarefa):
    """Worker: baixa e decodifica UM DO<uf><ano>.dbc -> (total, infantil) por municipio."""
    uf, ano = tarefa
    name = f"DO{uf}{ano}.dbc"
    dest = os.path.join(TMP, name)
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
            return (uf, ano, 0, {}, "inexistente")
        except Exception as e:
            if tent == 1:
                return (uf, ano, 0, {}, f"erro download: {e}")
            time.sleep(2)
    dbf = dest[:-4] + ".dbf"
    agg = {}
    try:
        dbc.dbc_to_dbf(dest, dbf)
        for rec in dbfread.DBF(dbf, encoding="latin-1", load=False):
            mr = (rec.get("CODMUNRES") or "").strip()
            if not mr.isdigit():
                continue
            cod = int(mr)
            inf = 1 if _is_infantil((rec.get("IDADE") or "").strip()) else 0
            a = agg.get(cod)
            if a is None:
                agg[cod] = [1, inf]
            else:
                a[0] += 1; a[1] += inf
        n = sum(a[0] for a in agg.values())
        return (uf, ano, n, agg, "ok")
    except Exception as e:
        return (uf, ano, 0, {}, f"erro decode: {e}")
    finally:
        for p in (dest, dbf):
            try:
                os.remove(p)
            except OSError:
                pass


def ano_recente():
    """Ano mais recente com cobertura total (27/27 UF)."""
    f = _ftp_conn()
    for ano in range(2024, 2014, -1):
        ok = True
        for uf in UFS:
            try:
                f.size(f"DO{uf}{ano}.dbc")
            except error_perm:
                ok = False; break
            except Exception:
                f = _ftp_conn()
                try:
                    f.size(f"DO{uf}{ano}.dbc")
                except Exception:
                    ok = False; break
        if ok:
            try:
                f.quit()
            except Exception:
                pass
            return ano
    try:
        f.quit()
    except Exception:
        pass
    return None


def main():
    t0 = time.time()
    ano = ano_recente()
    if not ano:
        sys.exit("Nenhum ano completo do SIM encontrado.")
    print(f"Ano: {ano} | workers={WORKERS}", flush=True)

    tarefas = [(uf, ano) for uf in UFS]
    agg = {}
    total = 0
    done = 0
    with ProcessPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(baixar_decode, t): t for t in tarefas}
        for fut in as_completed(futs):
            uf, an, n, a, st = fut.result()
            done += 1
            for cod, val in a.items():
                x = agg.get(cod)
                if x is None:
                    agg[cod] = [val[0], val[1]]
                else:
                    x[0] += val[0]; x[1] += val[1]
            total += n
            print(f"  [{done}/{len(tarefas)}] {uf}{an}: {n:,} obitos [{st}] "
                  f"(acum {total:,}) {time.time()-t0:.0f}s", flush=True)

    print(f"\nDecode concluido: {total:,} obitos, {len(agg):,} municipios em {time.time()-t0:.0f}s. Gravando...", flush=True)

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    super_url = os.environ.get("SUPERUSER_URL", os.environ["DATABASE_URL"])
    sc = psycopg2.connect(super_url)
    with sc.cursor() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS mortalidade_sim (
                municipio_cod INTEGER PRIMARY KEY, municipio_nome TEXT, uf CHAR(2), populacao INTEGER,
                obitos_total INTEGER DEFAULT 0, obitos_infantis INTEGER DEFAULT 0,
                taxa_mortalidade_mil NUMERIC(8,2), ano INTEGER, captado_em TIMESTAMP DEFAULT NOW());
            GRANT ALL ON mortalidade_sim TO wins_saude;
        """)
    sc.commit(); sc.close()

    with conn.cursor() as cur:
        cur.execute("SELECT municipio_cod, municipio_nome, uf, populacao FROM desertos_medicos")
        info = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}

    linhas = []
    for cod, (nome, uf, pop) in info.items():
        tot, inf = agg.get(cod, (0, 0))
        tmil = round(tot / pop * 1000, 2) if pop else 0
        linhas.append((cod, nome, uf, pop, tot, inf, tmil, ano))

    with conn.cursor() as cur:
        cur.execute("TRUNCATE mortalidade_sim")
        execute_values(cur, """
            INSERT INTO mortalidade_sim (municipio_cod,municipio_nome,uf,populacao,
              obitos_total,obitos_infantis,taxa_mortalidade_mil,ano) VALUES %s
        """, linhas, page_size=10000)
    conn.commit()

    with conn.cursor() as cur:
        cur.execute("SELECT sum(obitos_total), sum(obitos_infantis), round(avg(taxa_mortalidade_mil),2) FROM mortalidade_sim")
        tot = cur.fetchone()
        cur.execute("""SELECT municipio_nome, uf, obitos_total, taxa_mortalidade_mil
                       FROM mortalidade_sim WHERE populacao > 20000
                       ORDER BY taxa_mortalidade_mil DESC LIMIT 10""")
        top = cur.fetchall()
    conn.close()

    print("\n" + "=" * 60)
    print(f"SIM gravado ({ano}). Obitos: {tot[0]:,} | infantis: {tot[1]:,} | taxa media {tot[2]}/mil")
    print("Top 10 maior mortalidade/mil (pop>20k):")
    for nome, uf, ob, tx in top:
        print(f"  {nome}-{uf:<3} {ob:>7,} obitos  {tx:>6}/mil")
    print("=" * 60)


if __name__ == "__main__":
    sys.exit(main())
