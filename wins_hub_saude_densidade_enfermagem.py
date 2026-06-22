"""
WiNS Hub Saude - Densidade de enfermagem por municipio (CNES agregado)
======================================================================
Mesmo metodo da densidade medica. Conta, de forma AGREGADA (sem nomes/CPF),
profissionais de enfermagem por municipio a partir de tbCargaHorariaSus do ZIP
CNES, em 3 categorias:
  - Enfermeiro          (CBO familia 2235 / descricao 'ENFERMEIRO...')
  - Tecnico enfermagem  (descricao 'TECNICO DE/EM ENFERMAGEM')
  - Auxiliar enfermagem (descricao 'AUXILIAR DE/EM ENFERMAGEM')

NOTA: no CBO oficial, tecnico e auxiliar de enfermagem ficam ambos na familia
3222 (a distincao e no codigo de 6 digitos); por isso categorizamos pela
DESCRICAO do CBO (tbAtividadeProfissional), nao pelo prefixo 3221/3222.

Tabela densidade_enfermagem:
  municipio_cod, municipio_nome, uf, populacao,
  n_enfermeiros, n_tecnicos, n_auxiliares,
  enfermeiros_por_mil, tecnicos_por_mil, total_por_mil, classificacao
Classificacao por ENFERMEIROS/mil hab: <1 DESERTO; <2 BAIXA_COBERTURA; >=2 NORMAL.

Uso: python wins_hub_saude_densidade_enfermagem.py
"""

import os
import io
import csv
import sys
import zipfile

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import execute_values

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DADOS = os.path.join(BASE_DIR, "wins_hub_saude_dados")
load_dotenv(os.path.join(BASE_DIR, ".env.saude"))
ZIP = os.path.join(DADOS, "BASE_DE_DADOS_CNES_202605.ZIP")

LIM_DESERTO = 1.0
LIM_BAIXA = 2.0

DDL = """
CREATE TABLE IF NOT EXISTS densidade_enfermagem (
    municipio_cod        INTEGER PRIMARY KEY,
    municipio_nome       TEXT,
    uf                   CHAR(2),
    populacao            INTEGER,
    n_enfermeiros        INTEGER DEFAULT 0,
    n_tecnicos           INTEGER DEFAULT 0,
    n_auxiliares         INTEGER DEFAULT 0,
    enfermeiros_por_mil  NUMERIC(6,2),
    tecnicos_por_mil     NUMERIC(6,2),
    total_por_mil        NUMERIC(6,2),
    classificacao        VARCHAR(20),
    captado_em           TIMESTAMP DEFAULT NOW()
);
GRANT ALL ON densidade_enfermagem TO wins_saude;
"""


def categorizar_cbos(z):
    """Retorna dict CO_CBO -> 'ENF'|'TEC'|'AUX' a partir das descricoes."""
    cat = {}
    amostras = {"ENF": [], "TEC": [], "AUX": []}
    with z.open("tbAtividadeProfissional202605.csv") as f:
        for row in csv.reader(io.TextIOWrapper(f, encoding="latin-1"), delimiter=";", quotechar='"'):
            if len(row) < 2:
                continue
            cbo, ds = row[0].strip(), row[1].upper()
            c = None
            if ds.startswith("ENFERMEIRO"):
                c = "ENF"
            elif "AUXILIAR" in ds and "ENFERMAGEM" in ds:
                c = "AUX"
            elif ("TECNICO DE ENFERMAGEM" in ds or "TECNICO EM ENFERMAGEM" in ds
                  or ("TECNICO" in ds and "ENFERMAGEM" in ds)):
                c = "TEC"
            if c:
                cat[cbo] = c
                if len(amostras[c]) < 6:
                    amostras[c].append(f"{cbo} {row[1].strip()}")
    print("  CBOs por categoria:")
    for k, v in amostras.items():
        print(f"    {k}: {sum(1 for x in cat.values() if x==k)} codigos -> " + " | ".join(v))
    return cat


def main():
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    super_url = os.environ.get("SUPERUSER_URL", os.environ["DATABASE_URL"])

    print("=" * 60)
    print("WiNS Hub Saude - Densidade de ENFERMAGEM por municipio")
    print("=" * 60)

    sc = psycopg2.connect(super_url)
    with sc.cursor() as c:
        c.execute(DDL)
    sc.commit(); sc.close()

    z = zipfile.ZipFile(ZIP)
    cbo_cat = categorizar_cbos(z)

    with conn.cursor() as cur:
        cur.execute("SELECT cnes_id, municipio_cod FROM estabelecimentos WHERE cnes_id IS NOT NULL AND municipio_cod IS NOT NULL")
        c2m = {cid: m for cid, m in cur.fetchall()}
        # populacao/nome/uf reaproveitados de desertos_medicos (IBGE Censo 2022)
        cur.execute("SELECT municipio_cod, municipio_nome, uf, populacao FROM desertos_medicos")
        muni_info = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}
    print(f"  estabelecimentos mapeados: {len(c2m):,} | municipios IBGE: {len(muni_info):,}")

    # sets de (municipio, prof_int) por categoria
    pares = {"ENF": set(), "TEC": set(), "AUX": set()}
    print("  lendo tbCargaHorariaSus (865 MB)...")
    lidos = 0
    with z.open("tbCargaHorariaSus202605.csv") as f:
        r = csv.reader(io.TextIOWrapper(f, encoding="latin-1", newline=""), delimiter=";", quotechar='"')
        next(r)
        for row in r:
            lidos += 1
            if lidos % 2000000 == 0:
                print(f"    ... {lidos:,} linhas")
            cat = cbo_cat.get(row[2].strip())
            if cat is None:
                continue
            last7 = row[0][-7:]
            if not last7.isdigit():
                continue
            muni = c2m.get(int(last7))
            if muni is None:
                continue
            try:
                prof = int(row[1], 16)
            except ValueError:
                prof = hash(row[1])
            pares[cat].add((muni, prof))
    print(f"  {lidos:,} linhas lidas.")

    # conta por municipio
    def por_muni(cat):
        d = {}
        for muni, _ in pares[cat]:
            d[muni] = d.get(muni, 0) + 1
        return d
    enf, tec, aux = por_muni("ENF"), por_muni("TEC"), por_muni("AUX")
    print(f"  distintos -> enfermeiros:{len(set(p for p in pares['ENF'])):,} "
          f"tecnicos:{len(pares['TEC']):,} auxiliares:{len(pares['AUX']):,} (pares muni-prof)")

    # monta linhas para todos os municipios com populacao
    linhas = []
    for muni, (nome, uf, pop) in muni_info.items():
        ne, nt, na = enf.get(muni, 0), tec.get(muni, 0), aux.get(muni, 0)
        if pop and pop > 0:
            epm = round(ne / pop * 1000, 2)
            tpm = round(nt / pop * 1000, 2)
            tot = round((ne + nt + na) / pop * 1000, 2)
        else:
            epm = tpm = tot = 0
        classe = ("DESERTO" if epm < LIM_DESERTO else
                  "BAIXA_COBERTURA" if epm < LIM_BAIXA else "NORMAL")
        linhas.append((muni, nome, uf, pop, ne, nt, na, epm, tpm, tot, classe))

    with conn.cursor() as cur:
        cur.execute("TRUNCATE densidade_enfermagem")
        execute_values(cur, """
            INSERT INTO densidade_enfermagem
              (municipio_cod,municipio_nome,uf,populacao,n_enfermeiros,n_tecnicos,
               n_auxiliares,enfermeiros_por_mil,tecnicos_por_mil,total_por_mil,classificacao)
            VALUES %s
        """, linhas, page_size=10000)
    conn.commit()

    with conn.cursor() as cur:
        cur.execute("""
            SELECT classificacao, count(*), to_char(sum(populacao),'FM999G999G999'),
                   round(avg(enfermeiros_por_mil),2)
            FROM densidade_enfermagem GROUP BY 1 ORDER BY min(enfermeiros_por_mil)
        """)
        dist = cur.fetchall()
        cur.execute("""
            SELECT municipio_nome, uf, populacao, n_enfermeiros, enfermeiros_por_mil
            FROM densidade_enfermagem WHERE classificacao='DESERTO' AND populacao>20000
            ORDER BY populacao DESC LIMIT 12
        """)
        top = cur.fetchall()
    conn.close()

    print("\n" + "=" * 60)
    print("RESUMO - densidade de enfermagem (classificada por enfermeiros/mil hab)")
    print(f"{'classe':<18}{'munic':>8}{'populacao':>16}{'enf/mil(media)':>16}")
    for c, n, pop, avg in dist:
        print(f"{c:<18}{n:>8,}{pop:>16}{str(avg):>16}")
    print("\nMaiores DESERTOS de enfermagem (pop > 20k):")
    for nome, uf, pop, ne, dens in top:
        print(f"  {nome}-{uf:<3} pop={pop:>9,} enfermeiros={ne:>4} -> {dens}/mil")
    print("=" * 60)


if __name__ == "__main__":
    sys.exit(main())
