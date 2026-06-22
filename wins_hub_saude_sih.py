"""
WiNS Hub Saude - Ingestao SIH/SUS (volume de demanda hospitalar por municipio)
==============================================================================
Usa o decoder puro-python wins_hub_saude_dbc.py para ler os arquivos RD do SIH
(internacoes/AIH), agrega por municipio de RESIDENCIA (MUNIC_RES) -> demanda,
soma valor (VAL_TOT), anualiza e grava em demanda_sih. So agregado, sem PII.

Uso: python wins_hub_saude_sih.py
"""
import os, sys, json, time
import requests
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

URL = "https://ftp.datasus.gov.br/dissemin/publicos/SIHSUS/200801_/Dados/RD{uf}{ym}.dbc"
UFS = ["AC","AL","AP","AM","BA","CE","DF","ES","GO","MA","MT","MS","MG","PA","PB","PR",
       "PE","PI","RJ","RN","RS","RO","RR","SC","SP","SE","TO"]
N_MESES = 3

SESS = requests.Session()
SESS.headers.update({"User-Agent": "WiNS-Hub-Saude/1.0 (dados abertos SIH)"})


def existe(uf, ym):
    try:
        r = SESS.head(URL.format(uf=uf, ym=ym), timeout=30, allow_redirects=True)
        return r.status_code == 200
    except requests.RequestException:
        return False


def meses_recentes(n):
    """Sonda para tras (usando SP como referencia) os n meses mais recentes disponiveis."""
    achados = []
    ano, mes = 2026, 5
    for _ in range(18):
        ym = f"{ano}{mes:02d}"
        if existe("SP", ym):
            achados.append(ym)
            if len(achados) >= n:
                break
        mes -= 1
        if mes == 0:
            mes = 12; ano -= 1
    return achados


def baixar(uf, ym):
    dest = os.path.join(TMP, f"RD{uf}{ym}.dbc")
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest
    try:
        with SESS.get(URL.format(uf=uf, ym=ym), stream=True, timeout=120) as r:
            if r.status_code != 200:
                return None
            with open(dest, "wb") as f:
                for ch in r.iter_content(1 << 18):
                    f.write(ch)
        return dest
    except requests.RequestException:
        return None


def processar(dbc_path, agg):
    dbf_path = dbc_path[:-4] + ".dbf"
    dbc.dbc_to_dbf(dbc_path, dbf_path)
    t = dbfread.DBF(dbf_path, encoding="latin-1", load=False)
    n = 0
    for rec in t:
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
        n += 1
    try:
        os.remove(dbf_path)
    except OSError:
        pass
    return n


def main():
    meses = meses_recentes(N_MESES)
    if not meses:
        sys.exit("Nenhum mes do SIH encontrado.")
    fator = 12.0 / len(meses)
    print(f"Meses usados: {meses} | fator de anualizacao x{fator:.2f}")

    agg = {}
    total_aih = 0
    t0 = time.time()
    tarefas = [(uf, ym) for ym in meses for uf in UFS]
    for i, (uf, ym) in enumerate(tarefas, 1):
        p = baixar(uf, ym)
        if not p:
            print(f"  [{i}/{len(tarefas)}] {uf}{ym} indisponivel - pulando")
            continue
        try:
            n = processar(p, agg)
            total_aih += n
        except Exception as e:  # noqa: BLE001
            print(f"  [{i}/{len(tarefas)}] {uf}{ym} ERRO decode: {e}")
            n = 0
        try:
            os.remove(p)
        except OSError:
            pass
        print(f"  [{i}/{len(tarefas)}] {uf}{ym}: {n:,} AIH (acum {total_aih:,}) {time.time()-t0:.0f}s")
        json.dump({"feitos": i, "de": len(tarefas), "aih": total_aih,
                   "municipios": len(agg)}, open(CKPT, "w"))

    print(f"\nDecode concluido: {total_aih:,} AIH, {len(agg):,} municipios. Anualizando x{fator:.2f} e gravando...")

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
