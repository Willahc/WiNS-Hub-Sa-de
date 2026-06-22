# -*- coding: utf-8 -*-
"""
WiNS Hub Saude - Ingester de CAPACIDADE INSTALADA do CNES (leitos + equipamentos)
==================================================================================
Le, de um ZIP LOCAL da base CNES (202605), a capacidade instalada por estabelecimento
e agrega por municipio (codigo IBGE 6 digitos = primeiros 6 de CO_UNIDADE), gravando
numa tabela NOVA `cnes_capacidade`.

Fonte LOCAL (nada e baixado):
  wins_hub_saude_dados/BASE_DE_DADOS_CNES_202605.ZIP

Arquivos do ZIP usados:
  - rlEstabComplementar202605.csv : leitos por estabelecimento
        CO_UNIDADE; CO_LEITO; CO_TIPO_LEITO; ...; QT_EXIST; QT_CONTR; QT_SUS; ...
  - tbLeito202605.csv             : dicionario CO_LEITO -> DS_LEITO (p/ identificar UTI)
  - rlEstabEquipamento202605.csv  : equipamentos por estabelecimento
        CO_UNIDADE; CO_EQUIPAMENTO; CO_TIPO_EQUIPAMENTO; QT_EXISTENTE; ...
  - tbEquipamento202605.csv       : dicionario equipamento -> DS_EQUIPAMENTO

CSV DATASUS: encoding latin-1, separador ';'.

Municipio: CO_UNIDADE = CO_MUNICIPIO_IBGE7 (com digito verificador) + CO_CNES.
Os 6 primeiros digitos de CO_UNIDADE = codigo IBGE de 6 digitos (sem verificador),
que casa diretamente com desertos_medicos.municipio_cod (verificado: 3777/3778 hit).

Tabela cnes_capacidade (PK municipio_cod):
  municipio_nome, uf, populacao,
  leitos_total INTEGER, leitos_sus INTEGER, leitos_uti INTEGER,
  leitos_sus_por_mil NUMERIC,
  equip_tomografo INTEGER, equip_ressonancia INTEGER, equip_mamografo INTEGER

municipio_nome/uf/populacao vem de desertos_medicos.
DDL via SUPERUSER_URL + GRANT ALL TO wins_saude; INSERT via DATABASE_URL.

Uso:
    python wins_hub_saude_cnes_capacidade.py
"""

import os
import io
import csv
import sys
import unicodedata
import zipfile
from collections import defaultdict

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ZIP_PATH = os.path.join(BASE_DIR, "wins_hub_saude_dados", "BASE_DE_DADOS_CNES_202605.ZIP")
load_dotenv(os.path.join(BASE_DIR, ".env.saude"))

LEITOS_CSV = "rlEstabComplementar202605.csv"
TBLEITO_CSV = "tbLeito202605.csv"
EQUIP_CSV = "rlEstabEquipamento202605.csv"
TBEQUIP_CSV = "tbEquipamento202605.csv"


def noacc(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c)).upper().strip()


def to_int(s) -> int:
    try:
        return int((s or "0").strip() or 0)
    except (ValueError, TypeError):
        return 0


def muni6(co_unidade: str):
    """Primeiros 6 digitos de CO_UNIDADE = codigo IBGE 6 digitos."""
    cu = (co_unidade or "").strip().strip('"')
    if len(cu) >= 6 and cu[:6].isdigit():
        return int(cu[:6])
    return None


def classify_equip(desc: str):
    """Grupos de equipamento de imagem relevantes."""
    d = noacc(desc)
    grupos = []
    if "TOMOGRAFO" in d and "SIMULADOR" not in d:  # exclui simulador de radioterapia
        grupos.append("tomografo")
    if "RESSONANCIA MAGNETICA" in d:
        grupos.append("ressonancia")
    if "MAMOGRAFO" in d or "MAMOGRAFIA" in d:
        grupos.append("mamografo")
    return grupos


def open_csv(z, name):
    f = z.open(name)
    return f, csv.reader(io.TextIOWrapper(f, encoding="latin-1", newline=""), delimiter=";")


def carregar_uti_codes(z) -> set:
    """CO_LEITO cujos DS_LEITO indicam UTI (terapia intensiva)."""
    f, r = open_csv(z, TBLEITO_CSV)
    next(r)
    uti = set()
    for row in r:
        co = row[0].strip()
        ds = noacc(row[1])
        if ds.startswith("UTI"):  # UTI-A, UTI PEDIATRICA, UTI NEONATAL, UTI-Q, UTI II...
            uti.add(co)
    f.close()
    print(f"  tbLeito: {len(uti)} codigos de leito classificados como UTI -> {sorted(uti)}")
    return uti


def coletar_leitos(z, uti_codes):
    """muni6 -> {total, sus, uti} somando QT_EXIST / QT_SUS por estabelecimento."""
    f, r = open_csv(z, LEITOS_CSV)
    hdr = [c.strip().strip('"') for c in next(r)]
    iCO = hdr.index("CO_UNIDADE")
    iLE = hdr.index("CO_LEITO")
    iEX = hdr.index("QT_EXIST")
    iSUS = hdr.index("QT_SUS")

    agg = defaultdict(lambda: {"total": 0, "sus": 0, "uti": 0})
    linhas = 0
    nat_total = nat_sus = nat_uti = 0
    for row in r:
        linhas += 1
        m = muni6(row[iCO])
        if m is None:
            continue
        qex = to_int(row[iEX])
        qsus = to_int(row[iSUS])
        if qex <= 0 and qsus <= 0:
            continue
        a = agg[m]
        a["total"] += qex
        a["sus"] += qsus
        nat_total += qex
        nat_sus += qsus
        if row[iLE].strip() in uti_codes:
            a["uti"] += qex
            nat_uti += qex
    f.close()
    print(f"  rlEstabComplementar: {linhas:,} linhas | nacional leitos_total={nat_total:,} "
          f"leitos_sus={nat_sus:,} leitos_uti={nat_uti:,}")
    return agg, nat_total, nat_sus, nat_uti


def carregar_lookup_equip(z):
    """(CO_EQUIPAMENTO, CO_TIPO_EQUIPAMENTO) -> set(grupos)."""
    f, r = open_csv(z, TBEQUIP_CSV)
    next(r)
    lookup = {}
    descr = defaultdict(set)
    for row in r:
        co_eq = row[0].strip()
        co_tipo = row[1].strip()
        grupos = classify_equip(row[2])
        if grupos:
            lookup[(co_eq, co_tipo)] = grupos
            for g in grupos:
                descr[g].add(row[2].strip())
    f.close()
    print("  tbEquipamento: descricoes mapeadas por grupo:")
    for g in ("tomografo", "ressonancia", "mamografo"):
        print(f"    [{g}] {len(descr[g])} descricoes: {sorted(descr[g])}")
    return lookup


def coletar_equipamentos(z, lookup):
    """muni6 -> {grupo: qt} somando QT_EXISTENTE."""
    f, r = open_csv(z, EQUIP_CSV)
    hdr = [c.strip().strip('"') for c in next(r)]
    iCO = hdr.index("CO_UNIDADE")
    iEQ = hdr.index("CO_EQUIPAMENTO")
    iTP = hdr.index("CO_TIPO_EQUIPAMENTO")
    iEX = hdr.index("QT_EXISTENTE")

    agg = defaultdict(lambda: defaultdict(int))
    linhas = 0
    nat = defaultdict(int)
    for row in r:
        linhas += 1
        grupos = lookup.get((row[iEQ].strip(), row[iTP].strip()))
        if not grupos:
            continue
        qex = to_int(row[iEX])
        if qex <= 0:
            continue
        m = muni6(row[iCO])
        if m is None:
            continue
        for g in grupos:
            agg[m][g] += qex
            nat[g] += qex
    f.close()
    print(f"  rlEstabEquipamento: {linhas:,} linhas | nacional "
          f"tomografo={nat['tomografo']:,} ressonancia={nat['ressonancia']:,} "
          f"mamografo={nat['mamografo']:,}")
    return agg


def main():
    if not os.path.exists(ZIP_PATH):
        sys.exit(f"ZIP nao encontrado: {ZIP_PATH}")

    print("=" * 70)
    print(f"WiNS Hub Saude - Capacidade instalada CNES ({os.path.basename(ZIP_PATH)})")
    print("=" * 70)

    z = zipfile.ZipFile(ZIP_PATH)
    uti_codes = carregar_uti_codes(z)
    leitos, nat_total, nat_sus, nat_uti = coletar_leitos(z, uti_codes)
    lookup = carregar_lookup_equip(z)
    equip = coletar_equipamentos(z, lookup)
    z.close()

    # ---- DDL via SUPERUSER ----
    sup = psycopg2.connect(os.environ["SUPERUSER_URL"])
    with sup.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cnes_capacidade (
                municipio_cod      INTEGER PRIMARY KEY,
                municipio_nome     TEXT,
                uf                 CHAR(2),
                populacao          INTEGER,
                leitos_total       INTEGER DEFAULT 0,
                leitos_sus         INTEGER DEFAULT 0,
                leitos_uti         INTEGER DEFAULT 0,
                leitos_sus_por_mil NUMERIC,
                equip_tomografo    INTEGER DEFAULT 0,
                equip_ressonancia  INTEGER DEFAULT 0,
                equip_mamografo    INTEGER DEFAULT 0,
                captado_em         TIMESTAMP DEFAULT NOW()
            );
        """)
        cur.execute("GRANT ALL ON cnes_capacidade TO wins_saude;")
    sup.commit()
    sup.close()
    print("\n  Tabela cnes_capacidade criada/garantida; GRANT ALL -> wins_saude OK.")

    # ---- montar linhas a partir de desertos_medicos (universo de municipios) ----
    app = psycopg2.connect(os.environ["DATABASE_URL"])
    with app.cursor() as cur:
        cur.execute("SELECT municipio_cod, municipio_nome, uf, populacao FROM desertos_medicos")
        base = cur.fetchall()

        rows = []
        for muni, nome, uf, pop in base:
            l = leitos.get(muni, {"total": 0, "sus": 0, "uti": 0})
            e = equip.get(muni, {})
            sus = l["sus"]
            pop_i = pop or 0
            sus_por_mil = round(sus / pop_i * 1000, 4) if pop_i > 0 else None
            rows.append((
                muni, nome, uf, pop,
                l["total"], sus, l["uti"], sus_por_mil,
                e.get("tomografo", 0), e.get("ressonancia", 0), e.get("mamografo", 0),
            ))

        execute_values(cur, """
            INSERT INTO cnes_capacidade
              (municipio_cod, municipio_nome, uf, populacao,
               leitos_total, leitos_sus, leitos_uti, leitos_sus_por_mil,
               equip_tomografo, equip_ressonancia, equip_mamografo)
            VALUES %s
            ON CONFLICT (municipio_cod) DO UPDATE SET
              municipio_nome=EXCLUDED.municipio_nome, uf=EXCLUDED.uf, populacao=EXCLUDED.populacao,
              leitos_total=EXCLUDED.leitos_total, leitos_sus=EXCLUDED.leitos_sus,
              leitos_uti=EXCLUDED.leitos_uti, leitos_sus_por_mil=EXCLUDED.leitos_sus_por_mil,
              equip_tomografo=EXCLUDED.equip_tomografo, equip_ressonancia=EXCLUDED.equip_ressonancia,
              equip_mamografo=EXCLUDED.equip_mamografo, captado_em=NOW();
        """, rows, page_size=10000)
        app.commit()

        # ---- validacao / relatorio ----
        cur.execute("SELECT COALESCE(SUM(leitos_total),0), COALESCE(SUM(leitos_sus),0), "
                    "COALESCE(SUM(leitos_uti),0) FROM cnes_capacidade")
        s_total, s_sus, s_uti = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM cnes_capacidade")
        n_muni = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM cnes_capacidade WHERE leitos_total = 0")
        sem_leito = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM cnes_capacidade WHERE leitos_sus = 0")
        sem_sus = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM(equip_tomografo),0), COALESCE(SUM(equip_ressonancia),0), "
                    "COALESCE(SUM(equip_mamografo),0) FROM cnes_capacidade")
        e_tom, e_res, e_mam = cur.fetchone()

        cur.execute("""SELECT municipio_nome, uf, populacao, leitos_sus, leitos_sus_por_mil
                       FROM cnes_capacidade WHERE populacao >= 50000 AND leitos_sus_por_mil IS NOT NULL
                       ORDER BY leitos_sus_por_mil DESC LIMIT 8""")
        top = cur.fetchall()
        cur.execute("""SELECT municipio_nome, uf, populacao, leitos_sus, leitos_sus_por_mil
                       FROM cnes_capacidade WHERE populacao >= 50000 AND leitos_sus_por_mil IS NOT NULL
                       ORDER BY leitos_sus_por_mil ASC LIMIT 8""")
        bottom = cur.fetchall()
    app.close()

    print("\n" + "=" * 70)
    print("RELATORIO - cnes_capacidade")
    print("=" * 70)
    print(f"Municipios gravados            : {n_muni:,}")
    print(f"Leitos TOTAL (nacional, tabela): {s_total:,}")
    print(f"Leitos SUS   (nacional, tabela): {s_sus:,}   (ref. Brasil ~440 mil)")
    print(f"Leitos UTI   (nacional, tabela): {s_uti:,}")
    print(f"Equipamentos: tomografo={e_tom:,}  ressonancia={e_res:,}  mamografo={e_mam:,}")
    print(f"Municipios com 0 leitos (total): {sem_leito:,}")
    print(f"Municipios com 0 leitos SUS    : {sem_sus:,}")

    print("\nTOP 8 leitos_sus_por_mil (pop>=50k):")
    for nome, uf, pop, sus, pm in top:
        print(f"   {nome[:28]:<28} {uf}  pop={pop:>9,}  sus={sus:>5}  /mil={pm}")
    print("\nBOTTOM 8 leitos_sus_por_mil (pop>=50k):")
    for nome, uf, pop, sus, pm in bottom:
        print(f"   {nome[:28]:<28} {uf}  pop={pop:>9,}  sus={sus:>5}  /mil={pm}")
    print("=" * 70)
    print("OK.")


if __name__ == "__main__":
    sys.exit(main())
