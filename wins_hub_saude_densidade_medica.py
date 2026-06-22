"""
WiNS Hub Saude - Densidade medica por municipio (CNES agregado)
===============================================================
Tarefa 1 reformulada. A partir da base CNES aberta (tbCargaHorariaSus +
tbAtividadeProfissional dentro do ZIP), conta MEDICOS por municipio de forma
AGREGADA (sem guardar nomes/CPF) e calcula medicos por mil habitantes,
usando a populacao IBGE ja gravada em desertos_medicos.

Isso transforma desertos_medicos num mapa de deserto medico de verdade
(medicos/mil hab), nao apenas densidade de estabelecimentos.

Mapeamentos validados empiricamente:
  - CO_UNIDADE (carga horaria) -> cnes_id = int(ultimos 7 digitos)  [99.9% valido]
  - medico = CO_CBO cuja descricao em tbAtividadeProfissional comeca com
    'MEDICO' e nao e veterinario (familias CBO 2251/2252/2253/2231).
  - medico atuando no municipio = distinct CO_PROFISSIONAL_SUS por municipio
    (via cnes_id -> estabelecimentos.municipio_cod).

Uso:
    python wins_hub_saude_densidade_medica.py
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

# Limiares de classificacao (medicos por mil hab) - ajustaveis
LIM_DESERTO = 0.5
LIM_BAIXA = 1.0

DDL = """
ALTER TABLE desertos_medicos ADD COLUMN IF NOT EXISTS n_medicos INTEGER DEFAULT 0;
"""


def carregar_cbos_medicos(z):
    cbos = set()
    with z.open("tbAtividadeProfissional202605.csv") as f:
        for row in csv.reader(io.TextIOWrapper(f, encoding="latin-1"), delimiter=";", quotechar='"'):
            if len(row) >= 2:
                ds = row[1].upper()
                if ds.startswith("MEDICO") and "VETERINAR" not in ds:
                    cbos.add(row[0].strip())
    return cbos


def main():
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    super_url = os.environ.get("SUPERUSER_URL", os.environ["DATABASE_URL"])

    print("=" * 60)
    print("WiNS Hub Saude - Densidade medica por municipio (CNES agregado)")
    print("=" * 60)

    # DDL
    sc = psycopg2.connect(super_url)
    with sc.cursor() as c:
        c.execute(DDL)
    sc.commit(); sc.close()

    z = zipfile.ZipFile(ZIP)
    medico_cbos = carregar_cbos_medicos(z)
    print(f"  CBOs de medico: {len(medico_cbos)}")

    # cnes -> municipio_cod
    with conn.cursor() as cur:
        cur.execute("SELECT cnes_id, municipio_cod FROM estabelecimentos WHERE cnes_id IS NOT NULL AND municipio_cod IS NOT NULL")
        c2m = {cid: m for cid, m in cur.fetchall()}
    print(f"  estabelecimentos mapeados: {len(c2m):,}")

    # Stream carga horaria -> distinct (municipio, profissional)
    print("  lendo tbCargaHorariaSus (865 MB)...")
    pares = set()  # (municipio_cod, CO_PROFISSIONAL_SUS)
    lidos = 0
    with z.open("tbCargaHorariaSus202605.csv") as f:
        r = csv.reader(io.TextIOWrapper(f, encoding="latin-1", newline=""), delimiter=";", quotechar='"')
        next(r)
        for row in r:
            lidos += 1
            if lidos % 2000000 == 0:
                print(f"    ... {lidos:,} linhas | {len(pares):,} pares medico-municipio")
            if row[2].strip() not in medico_cbos:
                continue
            u = row[0]
            last7 = u[-7:]
            if not last7.isdigit():
                continue
            muni = c2m.get(int(last7))
            if muni is None:
                continue
            pares.add((muni, row[1]))

    print(f"  {lidos:,} linhas lidas; {len(pares):,} pares medico-municipio distintos.")

    # contar medicos distintos por municipio
    por_muni = {}
    profs_unicos = set()
    for muni, prof in pares:
        por_muni[muni] = por_muni.get(muni, 0) + 1
        profs_unicos.add(prof)
    print(f"  medicos distintos (nacional): {len(profs_unicos):,} | municipios com medico: {len(por_muni):,}")

    # aplicar em desertos_medicos
    with conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE _md (municipio_cod INTEGER PRIMARY KEY, n INTEGER) ON COMMIT DROP")
        execute_values(cur, "INSERT INTO _md VALUES %s", list(por_muni.items()), page_size=10000)
        # zera quem nao tem medico, seta quem tem
        cur.execute("UPDATE desertos_medicos SET n_medicos = 0")
        cur.execute("""
            UPDATE desertos_medicos d SET n_medicos = m.n
            FROM _md m WHERE d.municipio_cod = m.municipio_cod
        """)
        # recalcula densidade real (medicos/mil hab) e reclassifica
        cur.execute(f"""
            UPDATE desertos_medicos SET
                medicos_por_mil_hab = CASE WHEN populacao>0
                    THEN ROUND(n_medicos::numeric / populacao * 1000, 2) ELSE 0 END,
                classificacao = CASE
                    WHEN populacao IS NULL OR populacao=0 THEN classificacao
                    WHEN n_medicos::numeric / populacao * 1000 < {LIM_DESERTO} THEN 'DESERTO'
                    WHEN n_medicos::numeric / populacao * 1000 < {LIM_BAIXA} THEN 'BAIXA_COBERTURA'
                    ELSE 'NORMAL' END
        """)
    conn.commit()

    # relatorio
    with conn.cursor() as cur:
        cur.execute("""
            SELECT classificacao, count(*), to_char(sum(populacao),'FM999G999G999'),
                   round(avg(medicos_por_mil_hab),2)
            FROM desertos_medicos GROUP BY 1 ORDER BY min(medicos_por_mil_hab)
        """)
        dist = cur.fetchall()
        cur.execute("""
            SELECT municipio_nome, uf, populacao, n_medicos, medicos_por_mil_hab
            FROM desertos_medicos
            WHERE classificacao='DESERTO' AND populacao > 20000
            ORDER BY populacao DESC LIMIT 15
        """)
        top = cur.fetchall()
    conn.close()

    print("\n" + "=" * 60)
    print("RESUMO - densidade medica por municipio")
    print(f"{'classe':<18}{'munic':>8}{'populacao':>16}{'med/mil(media)':>16}")
    for c, n, pop, avg in dist:
        print(f"{c:<18}{n:>8,}{pop:>16}{str(avg):>16}")
    print("\nMaiores DESERTOS medicos (pop > 20k):")
    for nome, uf, pop, nm, dens in top:
        print(f"  {nome}-{uf:<3} pop={pop:>9,} medicos={nm:>4} -> {dens}/mil")
    print("=" * 60)


if __name__ == "__main__":
    sys.exit(main())
