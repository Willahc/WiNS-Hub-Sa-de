"""
WiNS Hub Saude - Ingestao SIA/APAC PARALELA (alta complexidade ambulatorial)
============================================================================
Baseado em wins_hub_saude_sih_parallel.py. Baixa via FTP (porta 21,
ftp.datasus.gov.br) os arquivos APAC mensais do SIASUS, decodifica em paralelo
(ProcessPoolExecutor + wins_hub_saude_dbc + dbfread) e agrega por municipio de
RESIDENCIA do paciente. So agregado, sem PII.

Tipos APAC agregados:
  - ONCOLOGIA: AQ (quimioterapia) + AR (radioterapia)
  - DIALISE  : AD (APAC de dialise / terapia renal substitutiva)
    OBS: o brief pedia "AN" para dialise, mas o prefixo AN (nefrologia) foi
    descontinuado em 2014 (ultimo ~ANxx1409). A dialise hoje sai no arquivo AD,
    que tem cobertura 27/27 UF nos meses recentes. Por isso usamos AD.

Layout APAC: arquivo <PREFIXO><UF><AAMM>.dbc (ano de 2 digitos).
  ex: AQSP2604.dbc, ARSP2604.dbc, ADSP2604.dbc
Campo de municipio do paciente: AP_MUNPCN (6 digitos IBGE).
Campo de valor: AP_VL_AP. Cada registro = 1 APAC.

Tabela destino (NOVA): demanda_apac (PK municipio_cod).

Uso: python wins_hub_saude_apac.py
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
TMP = os.path.join(BASE_DIR, "apac_tmp")
os.makedirs(TMP, exist_ok=True)
CKPT = os.path.join(BASE_DIR, "wins_hub_saude_apac_checkpoint.json")

FTP_HOST = "ftp.datasus.gov.br"
FTP_DIR = "/dissemin/publicos/SIASUS/200801_/Dados"
UFS = ["AC","AL","AP","AM","BA","CE","DF","ES","GO","MA","MT","MS","MG","PA","PB","PR",
       "PE","PI","RJ","RN","RS","RO","RR","SC","SP","SE","TO"]

# Prefixos por tipo de demanda
ONCO = ("AQ", "AR")   # quimioterapia + radioterapia
DIAL = ("AD",)        # APAC de dialise (substitui o antigo AN)
PREFIXOS = ONCO + DIAL

N_MESES = 3
WORKERS = 4
MUN_FIELD = "AP_MUNPCN"   # municipio do paciente (residencia), 6 digitos
VAL_FIELD = "AP_VL_AP"    # valor da APAC


def _ftp_conn():
    f = FTP(FTP_HOST, timeout=180)
    f.login()
    f.cwd(FTP_DIR)
    f.voidcmd("TYPE I")
    return f


def baixar_decode(tarefa):
    """Worker: baixa+decodifica <pref><uf><ym>.dbc -> agrega por AP_MUNPCN.
    Retorna (pref, uf, ym, n_registros, {cod:[n,valor]}, status)."""
    pref, uf, ym = tarefa
    name = f"{pref}{uf}{ym}.dbc"
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
            return (pref, uf, ym, 0, {}, "inexistente")
        except Exception as e:
            if tent == 2:
                return (pref, uf, ym, 0, {}, f"erro download: {e}")
            time.sleep(3)
    dbf = dest[:-4] + ".dbf"
    agg = {}
    try:
        dbc.dbc_to_dbf(dest, dbf)
        for rec in dbfread.DBF(dbf, encoding="latin-1", load=False):
            mr = (rec.get(MUN_FIELD) or "").strip()
            if not mr.isdigit() or len(mr) < 6:
                continue
            cod = int(mr)
            v = rec.get(VAL_FIELD) or 0
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
        return (pref, uf, ym, n, agg, "ok")
    except Exception as e:
        return (pref, uf, ym, 0, {}, f"erro decode: {e}")
    finally:
        for p in (dest, dbf):
            try:
                os.remove(p)
            except OSError:
                pass


def meses_completos(prefixos, n):
    """Os n meses mais recentes em que TODOS os prefixos tem cobertura ampla.
    Para AR aceita-se 1 UF faltante (RR nao tem radioterapia)."""
    f = _ftp_conn()
    nomes = set(x.upper() for x in f.nlst())
    try:
        f.quit()
    except Exception:
        pass
    achados = []
    ano, mes = 2026, 6
    for _ in range(24):
        ym = f"{ano % 100:02d}{mes:02d}"
        ok = True
        for pref in prefixos:
            presentes = sum(1 for uf in UFS if f"{pref}{uf}{ym}.DBC" in nomes)
            # AR tolera 1 faltante (RR sem radioterapia); demais exigem 27/27
            minimo = 26 if pref == "AR" else 27
            if presentes < minimo:
                ok = False
                break
        if ok:
            achados.append(ym)
            if len(achados) >= n:
                break
        mes -= 1
        if mes == 0:
            mes = 12; ano -= 1
    return achados


def main():
    t0 = time.time()
    meses = meses_completos(PREFIXOS, N_MESES)
    if not meses:
        sys.exit("Nenhum mes completo do SIA/APAC encontrado.")
    fator = 12.0 / len(meses)
    print(f"Prefixos onco={ONCO} dialise={DIAL}", flush=True)
    print(f"Meses completos: {meses} | fator anualizacao x{fator:.2f} | workers={WORKERS}", flush=True)

    tarefas = [(pref, uf, ym) for ym in meses for pref in PREFIXOS for uf in UFS]
    # agg_onco / agg_dial: cod -> [n_apac, valor]
    agg_onco = {}
    agg_dial = {}
    tot_onco = tot_dial = 0
    done = 0
    with ProcessPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(baixar_decode, t): t for t in tarefas}
        for fut in as_completed(futs):
            pref, uf, ym, n, a, st = fut.result()
            done += 1
            dest = agg_onco if pref in ONCO else agg_dial
            for cod, val in a.items():
                x = dest.get(cod)
                if x is None:
                    dest[cod] = [val[0], val[1]]
                else:
                    x[0] += val[0]; x[1] += val[1]
            if pref in ONCO:
                tot_onco += n
            else:
                tot_dial += n
            print(f"  [{done}/{len(tarefas)}] {pref}{uf}{ym}: {n:,} APAC [{st}] "
                  f"(onco {tot_onco:,} | dial {tot_dial:,}) {time.time()-t0:.0f}s", flush=True)
            json.dump({"feitos": done, "de": len(tarefas), "onco": tot_onco,
                       "dial": tot_dial, "mun_onco": len(agg_onco),
                       "mun_dial": len(agg_dial)}, open(CKPT, "w"))

    print(f"\nDecode concluido: onco {tot_onco:,} APAC / dialise {tot_dial:,} APAC em "
          f"{time.time()-t0:.0f}s. Anualizando x{fator:.2f} e gravando...", flush=True)

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    super_url = os.environ.get("SUPERUSER_URL", os.environ["DATABASE_URL"])
    sc = psycopg2.connect(super_url)
    with sc.cursor() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS demanda_apac (
                municipio_cod INTEGER PRIMARY KEY,
                municipio_nome TEXT,
                uf CHAR(2),
                populacao INTEGER,
                apac_onco INTEGER DEFAULT 0,
                apac_dialise INTEGER DEFAULT 0,
                onco_por_mil NUMERIC(10,3),
                dialise_por_mil NUMERIC(10,3),
                periodo VARCHAR(60),
                captado_em TIMESTAMP DEFAULT NOW());
            GRANT ALL ON demanda_apac TO wins_saude;
        """)
    sc.commit(); sc.close()

    with conn.cursor() as cur:
        cur.execute("SELECT municipio_cod, municipio_nome, uf, populacao FROM desertos_medicos")
        info = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}

    periodo = f"{meses[-1]}..{meses[0]} (x{fator:.1f} anualizado)"
    linhas = []
    for cod, (nome, uf, pop) in info.items():
        o = agg_onco.get(cod, (0, 0.0))
        d = agg_dial.get(cod, (0, 0.0))
        onco = round(o[0] * fator)
        dial = round(d[0] * fator)
        opm = round(onco / pop * 1000, 3) if pop else 0
        dpm = round(dial / pop * 1000, 3) if pop else 0
        linhas.append((cod, nome, uf, pop, onco, dial, opm, dpm, periodo))

    with conn.cursor() as cur:
        cur.execute("TRUNCATE demanda_apac")
        execute_values(cur, """
            INSERT INTO demanda_apac (municipio_cod,municipio_nome,uf,populacao,
              apac_onco,apac_dialise,onco_por_mil,dialise_por_mil,periodo) VALUES %s
        """, linhas, page_size=10000)
    conn.commit()

    with conn.cursor() as cur:
        cur.execute("SELECT sum(apac_onco), sum(apac_dialise), "
                    "round(avg(onco_por_mil),2), round(avg(dialise_por_mil),2) "
                    "FROM demanda_apac")
        tot = cur.fetchone()
        cur.execute("""SELECT municipio_nome, uf, apac_onco, onco_por_mil, populacao
                       FROM demanda_apac WHERE populacao >= 5000
                       ORDER BY onco_por_mil DESC LIMIT 10""")
        top_onco = cur.fetchall()
        cur.execute("""SELECT municipio_nome, uf, apac_dialise, dialise_por_mil, populacao
                       FROM demanda_apac WHERE populacao >= 5000
                       ORDER BY dialise_por_mil DESC LIMIT 10""")
        top_dial = cur.fetchall()
    conn.close()

    print("\n" + "=" * 64)
    print(f"demanda_apac gravado. Onco/ano (estim): {tot[0]:,} APAC | "
          f"Dialise/ano: {tot[1]:,} APAC")
    print(f"Media onco {tot[2]}/mil | media dialise {tot[3]}/mil")
    print("\nTop 10 municipios por onco_por_mil (pop>=5k):")
    for nome, uf, n, pm, pop in top_onco:
        print(f"  {nome[:24]:<24}-{uf} {n:>7,} APAC  {pm:>8}/mil (pop {pop:,})")
    print("\nTop 10 municipios por dialise_por_mil (pop>=5k):")
    for nome, uf, n, pm, pop in top_dial:
        print(f"  {nome[:24]:<24}-{uf} {n:>7,} APAC  {pm:>8}/mil (pop {pop:,})")
    print("=" * 64)


if __name__ == "__main__":
    sys.exit(main())
