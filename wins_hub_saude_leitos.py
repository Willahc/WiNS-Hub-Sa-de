"""
WiNS Hub Saude - Tarefa 2: popular tem_internacao via leitos CNES
=================================================================
Le rlEstabComplementar<AAAAMM>.csv (leitos por estabelecimento) de dentro do
ZIP da base CNES e marca tem_internacao=1 nos estabelecimentos que possuem
ao menos um leito existente (QT_EXIST > 0).

CO_UNIDADE = CO_MUNICIPIO(6) + CO_CNES(7); logo cnes_id = int(CO_UNIDADE[-7:]).

Uso:
    python wins_hub_saude_leitos.py
"""

import os
import io
import csv
import sys
import glob
import zipfile

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import execute_values

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DADOS_DIR = os.path.join(BASE_DIR, "wins_hub_saude_dados")
load_dotenv(os.path.join(BASE_DIR, ".env.saude"))


def achar_zip() -> str:
    cands = sorted(glob.glob(os.path.join(DADOS_DIR, "BASE_DE_DADOS_CNES_*.ZIP")))
    if not cands:
        sys.exit("ZIP CNES nao encontrado em wins_hub_saude_dados/. "
                 "Rode wins_hub_saude_cnes_download.py primeiro.")
    return cands[-1]


def coletar_cnes_com_leito(zip_path: str) -> set[int]:
    nome = next(n for n in zipfile.ZipFile(zip_path).namelist()
                if n.lower().startswith("rlestabcomplementar"))
    print(f"  Lendo {nome} ...")
    com_leito: set[int] = set()
    linhas = 0
    with zipfile.ZipFile(zip_path) as z, z.open(nome) as raw:
        txt = io.TextIOWrapper(raw, encoding="latin-1", newline="")
        r = csv.reader(txt, delimiter=";", quotechar='"')
        header = [c.strip().strip('"') for c in next(r)]
        i_uni = header.index("CO_UNIDADE")
        i_qt = header.index("QT_EXIST")
        for row in r:
            linhas += 1
            try:
                qt = int((row[i_qt] or "0").strip() or 0)
            except ValueError:
                qt = 0
            if qt > 0:
                co_unidade = (row[i_uni] or "").strip().strip('"')
                if len(co_unidade) >= 7 and co_unidade[-7:].isdigit():
                    com_leito.add(int(co_unidade[-7:]))
    print(f"  {linhas:,} linhas de leitos; {len(com_leito):,} estabelecimentos com leito (QT_EXIST>0).")
    return com_leito


def main():
    zip_path = achar_zip()
    print("=" * 60)
    print(f"WiNS Hub Saude - tem_internacao via leitos ({os.path.basename(zip_path)})")
    print("=" * 60)

    cnes = coletar_cnes_com_leito(zip_path)

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    with conn.cursor() as cur:
        # zera e re-marca (idempotente)
        cur.execute("UPDATE estabelecimentos SET tem_internacao = 0 WHERE tem_internacao <> 0;")
        cur.execute("CREATE TEMP TABLE _cnes_leito (cnes_id INTEGER PRIMARY KEY) ON COMMIT DROP;")
        execute_values(cur, "INSERT INTO _cnes_leito (cnes_id) VALUES %s ON CONFLICT DO NOTHING",
                       [(c,) for c in cnes], page_size=10000)
        cur.execute("""
            UPDATE estabelecimentos e SET tem_internacao = 1
            FROM _cnes_leito l WHERE e.cnes_id = l.cnes_id;
        """)
        marcados = cur.rowcount
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM estabelecimentos WHERE tem_internacao = 1;")
        total = cur.fetchone()[0]
    conn.close()

    print(f"  UPDATE: {marcados:,} estabelecimentos marcados com internacao.")
    print(f"  Total com tem_internacao=1 no banco: {total:,}")
    print("=" * 60)


if __name__ == "__main__":
    sys.exit(main())
