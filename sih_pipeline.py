"""
SIH demand pipeline for WiNS Hub Saude.
Downloads 3 most recent complete months of SIH/SUS RD files for all 27 UFs
via FTP, decodes .dbc -> .dbf in pure Python, aggregates internacoes + VAL_TOT
by MUNIC_RES (residence municipality), annualizes (x4), and populates
demanda_sih. Keeps ONLY per-municipality counters in memory (aggregate only).
"""
import os, io, ftplib, time, tempfile, collections
from dotenv import load_dotenv
from dbfread import DBF
import psycopg2
import wins_hub_saude_dbc as dbc

UFS = ['AC','AL','AP','AM','BA','CE','DF','ES','GO','MA','MT','MS','MG','PA',
       'PB','PR','PE','PI','RJ','RN','RS','RO','RR','SC','SP','SE','TO']
MONTHS = ['2601', '2602', '2603']           # Jan/Feb/Mar 2026 (most recent complete)
FACTOR = 4                                   # 3 months -> annual estimate
PERIODO = '2026-01 a 2026-03 (3 meses x4)'
FTP_DIR = '/dissemin/publicos/SIHSUS/200801_/Dados'

WORK = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sih_tmp')
os.makedirs(WORK, exist_ok=True)

internacoes = collections.defaultdict(int)     # munic_cod(int) -> count
valor = collections.defaultdict(float)         # munic_cod(int) -> sum VAL_TOT


def ftp_connect():
    f = ftplib.FTP('ftp.datasus.gov.br', timeout=60)
    f.login()
    f.set_pasv(True)
    f.cwd(FTP_DIR)
    return f


def download(ftp, fname, dest):
    with open(dest, 'wb') as fh:
        ftp.retrbinary('RETR ' + fname, fh.write, blocksize=1 << 16)
    return dest


def process_dbf(dbf_path):
    n = 0
    tbl = DBF(dbf_path, encoding='latin-1', load=False)
    for rec in tbl:
        mr = rec.get('MUNIC_RES')
        vt = rec.get('VAL_TOT')
        if mr is None:
            continue
        try:
            cod = int(str(mr).strip())
        except (ValueError, TypeError):
            continue
        internacoes[cod] += 1
        try:
            valor[cod] += float(vt)
        except (ValueError, TypeError):
            pass
        n += 1
    return n


def main():
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env.saude'))
    t0 = time.time()
    total_recs = 0
    done = 0
    ftp = ftp_connect()
    files = [(uf, mm) for mm in MONTHS for uf in UFS]
    for uf, mm in files:
        fname = 'RD%s%s.dbc' % (uf, mm)
        dbc_path = os.path.join(WORK, fname)
        dbf_path = dbc_path[:-4] + '.dbf'
        for attempt in range(3):
            try:
                download(ftp, fname, dbc_path)
                break
            except (ftplib.error_temp, EOFError, OSError) as e:
                print('  retry %s (%s)' % (fname, type(e).__name__))
                try:
                    ftp.quit()
                except Exception:
                    pass
                time.sleep(2)
                ftp = ftp_connect()
        else:
            raise RuntimeError('failed to download ' + fname)

        dbc.dbc_to_dbf(dbc_path, dbf_path)
        n = process_dbf(dbf_path)
        total_recs += n
        done += 1
        # delete temp files immediately to keep disk small
        for p in (dbc_path, dbf_path):
            try:
                os.remove(p)
            except OSError:
                pass
        print('[%2d/%2d] %s recs=%6d  cum=%8d  munis=%d  %.0fs'
              % (done, len(files), fname, n, total_recs,
                 len(internacoes), time.time() - t0))
    ftp.quit()

    print('\nAggregation done: %d AIH records, %d municipalities, %.0fs'
          % (total_recs, len(internacoes), time.time() - t0))
    print('National 3-month internacoes=%d  VAL_TOT=R$%.2f'
          % (sum(internacoes.values()), sum(valor.values())))

    populate()


def populate():
    su = os.environ['SUPERUSER_URL']
    cn = psycopg2.connect(su)
    cn.autocommit = False
    cur = cn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS demanda_sih (
          municipio_cod INTEGER PRIMARY KEY, municipio_nome TEXT, uf CHAR(2),
          populacao INTEGER,
          internacoes BIGINT DEFAULT 0, valor_total NUMERIC DEFAULT 0,
          internacoes_por_mil NUMERIC(8,2), valor_per_capita NUMERIC(10,2),
          periodo VARCHAR(40), captado_em TIMESTAMP DEFAULT NOW());
    """)
    cur.execute("GRANT ALL ON demanda_sih TO wins_saude;")

    cur.execute("SELECT municipio_cod, municipio_nome, uf, populacao FROM desertos_medicos")
    rows = cur.fetchall()

    matched = 0
    upserts = []
    for cod, nome, uf, pop in rows:
        intern_a = internacoes.get(cod, 0) * FACTOR     # annualized
        val_a = valor.get(cod, 0.0) * FACTOR
        if internacoes.get(cod, 0) > 0:
            matched += 1
        ipm = (intern_a / pop * 1000) if pop and pop > 0 else None
        vpc = (val_a / pop) if pop and pop > 0 else None
        upserts.append((cod, nome, uf, pop, intern_a, round(val_a, 2),
                        round(ipm, 2) if ipm is not None else None,
                        round(vpc, 2) if vpc is not None else None, PERIODO))

    cur.executemany("""
        INSERT INTO demanda_sih
          (municipio_cod, municipio_nome, uf, populacao, internacoes,
           valor_total, internacoes_por_mil, valor_per_capita, periodo, captado_em)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW())
        ON CONFLICT (municipio_cod) DO UPDATE SET
          municipio_nome=EXCLUDED.municipio_nome, uf=EXCLUDED.uf,
          populacao=EXCLUDED.populacao, internacoes=EXCLUDED.internacoes,
          valor_total=EXCLUDED.valor_total,
          internacoes_por_mil=EXCLUDED.internacoes_por_mil,
          valor_per_capita=EXCLUDED.valor_per_capita,
          periodo=EXCLUDED.periodo, captado_em=NOW();
    """, upserts)
    cn.commit()
    print('Upserted %d rows; %d municipalities matched SIH demand.'
          % (len(upserts), matched))

    # report stats
    cur.execute("SELECT count(*), sum(internacoes), sum(valor_total) FROM demanda_sih")
    print('TABLE totals (annualized):', cur.fetchone())
    cur.execute("""SELECT round(avg(internacoes_por_mil),2)
                   FROM demanda_sih WHERE internacoes>0""")
    print('avg internacoes_por_mil (munis w/ demand):', cur.fetchone()[0])
    cur.execute("""SELECT municipio_cod, municipio_nome, uf, internacoes, valor_total
                   FROM demanda_sih ORDER BY valor_total DESC LIMIT 10""")
    print('TOP 10 by valor_total (annualized):')
    for r in cur.fetchall():
        print('  ', r)
    cn.close()


if __name__ == '__main__':
    main()
