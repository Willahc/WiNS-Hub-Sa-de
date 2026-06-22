"""
WiNS Hub Saude - Ingestao SINASC/SUS (nascidos vivos por municipio de residencia)
=================================================================================
Baixa via FTP (porta 21) os arquivos anuais DN<UF><AAAA>.dbc do SINASC/NOV/DNRES,
decodifica com o decoder puro (wins_hub_saude_dbc.py) em PARALELO, agrega por
CODMUNRES (municipio de RESIDENCia da mae): nascidos vivos (1 por registro),
% de partos cesareos e media de consultas pre-natal. So agregado, sem PII.
Grava em nascimentos_sinasc. Usa o ano mais recente com cobertura 27/27 UF.

Este denominador (nascidos_vivos) sera cruzado depois com mortalidade_sim
(coluna obitos_infantis) para calcular a mortalidade infantil por mil.
O municipio_cod tem 6 digitos, igual ao de mortalidade_sim/desertos_medicos.

Uso: python wins_hub_saude_sinasc.py
"""
import os, sys, time
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
FTP_DIR = "/dissemin/publicos/SINASC/NOV/DNRES"
UFS = ["AC","AL","AP","AM","BA","CE","DF","ES","GO","MA","MT","MS","MG","PA","PB","PR",
       "PE","PI","RJ","RN","RS","RO","RR","SC","SP","SE","TO"]
WORKERS = 4


def _ftp_conn():
    f = FTP(FTP_HOST, timeout=180)
    f.login()
    f.cwd(FTP_DIR)
    f.voidcmd("TYPE I")
    return f


def baixar_decode(tarefa):
    """Worker: baixa e decodifica UM DN<uf><ano>.dbc.
    Retorna agg[cod] = [nascidos, cesareas, parto_validos, soma_cons, cons_validos]."""
    uf, ano = tarefa
    name = f"DN{uf}{ano}.dbc"
    dest = os.path.join(TMP, name)
    for tent in range(3):
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
            if tent == 2:
                return (uf, ano, 0, {}, f"erro download: {e}")
            time.sleep(3)
    dbf = dest[:-4] + ".dbf"
    agg = {}
    try:
        dbc.dbc_to_dbf(dest, dbf)
        for rec in dbfread.DBF(dbf, encoding="latin-1", load=False):
            mr = (rec.get("CODMUNRES") or "").strip()
            if not mr.isdigit():
                continue
            cod = int(mr)
            # PARTO: 1=vaginal, 2=cesareo, 9/vazio=ignorado
            parto = (rec.get("PARTO") or "").strip()
            is_ces = 1 if parto == "2" else 0
            parto_ok = 1 if parto in ("1", "2") else 0
            # CONSPRENAT: numero de consultas pre-natal (numerico); 99/vazio=ignorado
            cp = (rec.get("CONSPRENAT") or "").strip()
            cons_val = 0
            cons_ok = 0
            if cp.isdigit():
                v = int(cp)
                if v != 99:
                    cons_val = v
                    cons_ok = 1
            a = agg.get(cod)
            if a is None:
                agg[cod] = [1, is_ces, parto_ok, cons_val, cons_ok]
            else:
                a[0] += 1; a[1] += is_ces; a[2] += parto_ok
                a[3] += cons_val; a[4] += cons_ok
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
    """Ano mais recente com cobertura total (27/27 UF). Sonda para tras."""
    f = _ftp_conn()
    try:
        nomes = set(n.upper() for n in f.nlst())
    finally:
        try:
            f.quit()
        except Exception:
            pass
    for ano in range(2025, 2009, -1):
        if all(f"DN{uf}{ano}.DBC" in nomes for uf in UFS):
            return ano
    return None


def main():
    t0 = time.time()
    ano = ano_recente()
    if not ano:
        sys.exit("Nenhum ano completo do SINASC encontrado.")
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
                    agg[cod] = list(val)
                else:
                    for i in range(5):
                        x[i] += val[i]
            total += n
            print(f"  [{done}/{len(tarefas)}] {uf}{an}: {n:,} nascidos [{st}] "
                  f"(acum {total:,}) {time.time()-t0:.0f}s", flush=True)

    print(f"\nDecode concluido: {total:,} nascidos vivos, {len(agg):,} municipios "
          f"em {time.time()-t0:.0f}s. Gravando...", flush=True)

    # DDL via superusuario
    super_url = os.environ.get("SUPERUSER_URL", os.environ["DATABASE_URL"])
    sc = psycopg2.connect(super_url)
    with sc.cursor() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS nascimentos_sinasc (
                municipio_cod INTEGER PRIMARY KEY, municipio_nome TEXT, uf CHAR(2),
                populacao INTEGER, nascidos_vivos INTEGER DEFAULT 0,
                pct_cesarea NUMERIC(5,2), media_consultas_prenatal NUMERIC(5,2),
                ano INTEGER, captado_em TIMESTAMP DEFAULT NOW());
            GRANT ALL ON nascimentos_sinasc TO wins_saude;
        """)
    sc.commit(); sc.close()

    # leitura e insercao via usuario da app
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    with conn.cursor() as cur:
        cur.execute("SELECT municipio_cod, municipio_nome, uf, populacao FROM desertos_medicos")
        info = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}

    linhas = []
    for cod, (nome, uf, pop) in info.items():
        v = agg.get(cod)
        if v is None:
            nasc = ces = parto_ok = soma_c = cons_ok = 0
        else:
            nasc, ces, parto_ok, soma_c, cons_ok = v
        pct_ces = round(ces / parto_ok * 100, 2) if parto_ok else None
        media_c = round(soma_c / cons_ok, 2) if cons_ok else None
        linhas.append((cod, nome, uf, pop, nasc, pct_ces, media_c, ano))

    with conn.cursor() as cur:
        cur.execute("TRUNCATE nascimentos_sinasc")
        execute_values(cur, """
            INSERT INTO nascimentos_sinasc (municipio_cod,municipio_nome,uf,populacao,
              nascidos_vivos,pct_cesarea,media_consultas_prenatal,ano) VALUES %s
        """, linhas, page_size=10000)
    conn.commit()

    # validacao
    with conn.cursor() as cur:
        cur.execute("""SELECT count(*), sum(nascidos_vivos),
                              count(*) FILTER (WHERE nascidos_vivos>0),
                              round(avg(pct_cesarea),2), round(avg(media_consultas_prenatal),2)
                       FROM nascimentos_sinasc""")
        nmun, tnasc, nmun_pos, avg_ces, avg_cons = cur.fetchone()

        # sanity check do join com mortalidade_sim: mortalidade infantil /mil
        cur.execute("""
            SELECT n.municipio_nome, n.uf, n.nascidos_vivos, m.obitos_infantis,
                   round(m.obitos_infantis::numeric / NULLIF(n.nascidos_vivos,0) * 1000, 2) AS mi_mil
            FROM nascimentos_sinasc n
            JOIN mortalidade_sim m USING (municipio_cod)
            WHERE n.nascidos_vivos > 10000
            ORDER BY n.nascidos_vivos DESC
            LIMIT 5
        """)
        join_rows = cur.fetchall()

        # nacional do join
        cur.execute("""
            SELECT sum(m.obitos_infantis), sum(n.nascidos_vivos),
                   round(sum(m.obitos_infantis)::numeric / NULLIF(sum(n.nascidos_vivos),0) * 1000, 2)
            FROM nascimentos_sinasc n JOIN mortalidade_sim m USING (municipio_cod)
        """)
        nac_obi, nac_nasc, nac_mi = cur.fetchone()
    conn.close()

    print("\n" + "=" * 64)
    print(f"SINASC gravado (ano {ano}). Nascidos vivos: {tnasc:,}")
    print(f"Municipios: {nmun:,} (com nascidos>0: {nmun_pos:,}) | "
          f"% cesarea media {avg_ces} | consultas pre-natal media {avg_cons}")
    print("-" * 64)
    print("Sanity check JOIN mortalidade_sim (5 municipios grandes):")
    print(f"  {'municipio':<22}{'nasc':>10}{'ob.inf':>8}{'MI/mil':>9}")
    for nome, uf, nasc, obi, mi in join_rows:
        print(f"  {(nome+'-'+uf):<22}{nasc:>10,}{obi:>8,}{str(mi):>9}")
    print("-" * 64)
    print(f"Nacional (join): obitos_infantis={nac_obi:,} / nascidos={nac_nasc:,} "
          f"=> MI={nac_mi}/mil")
    print("=" * 64)


if __name__ == "__main__":
    sys.exit(main())
