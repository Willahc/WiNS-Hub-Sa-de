#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WiNS Hub Saude - Perfil economico/demografico por municipio (IBGE aberto, so agregado).
A) PIB municipal + PIB per capita (agregado 5938, variavel 37; per capita derivado de populacao)
B) % populacao idosa 60+ (Censo 2022, agregado 9514, variavel 93, classificacao Idade=287)
Idempotente: cria tabela se nao existir e faz UPSERT por municipio_cod (6 digitos).
"""
import os, sys, time, requests, psycopg2
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env.saude"))
SUPERUSER_URL = os.environ["SUPERUSER_URL"]

API = "https://servicodados.ibge.gov.br/api/v3/agregados"
PIB_ANO = "2023"          # ano mais recente confirmado com dados em 5938 var 37
CENSO_ANO = "2022"        # agregado 9514 var 93
# Categorias da classificacao 287 (Idade) que somam 60 anos ou mais:
CATS_60MAIS = {"93095", "93096", "93097", "93098", "49108", "49109", "60040", "60041", "6653"}
CAT_TOTAL = "100362"      # categoria "Total" da classificacao 287
NULOS = {"-", "...", "..", "X", "", None}

S = requests.Session()
S.headers.update({"User-Agent": "WiNS-Hub-Saude/1.0"})


def get_json(url, params, tries=5, pause=3):
    for i in range(tries):
        try:
            r = S.get(url, params=params, timeout=180)
            if r.status_code == 200:
                return r.json()
            print(f"   HTTP {r.status_code} (tentativa {i+1}) {r.text[:80]}")
        except Exception as e:
            print(f"   ERRO {e} (tentativa {i+1})")
        time.sleep(pause * (i + 1))
    raise RuntimeError(f"Falha apos {tries} tentativas: {url} {params}")


def num(v):
    if v in NULOS:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def captar_pib():
    print(f"[A] PIB municipal (agregado 5938 var 37, ano {PIB_ANO}) ...")
    d = get_json(f"{API}/5938/periodos/{PIB_ANO}/variaveis/37", {"localidades": "N6[all]"})
    series = d[0]["resultados"][0]["series"]
    pib = {}
    for x in series:
        cod7 = x["localidade"]["id"]
        val = num(list(x["serie"].values())[0])   # mil R$
        pib[cod7] = val
    print(f"    {len(pib)} municipios; com dado: {sum(1 for v in pib.values() if v is not None)}")
    return pib


def captar_idosos(codigos7):
    print(f"[B] Idosos 60+ (Censo {CENSO_ANO}, agregado 9514 var 93, classif 287) ...")
    url = f"{API}/9514/periodos/{CENSO_ANO}/variaveis/93"
    idosos = {}   # cod7 -> (total, pop60mais)
    lote = 100
    codigos7 = sorted(codigos7)
    for i in range(0, len(codigos7), lote):
        chunk = codigos7[i:i + lote]
        d = get_json(url, {"localidades": "N6[" + ",".join(chunk) + "]",
                           "classificacao": "287[all]"})
        # acumular por municipio
        acc = {c: {"total": None, "p60": 0, "tem60": False} for c in chunk}
        for res in d[0]["resultados"]:
            cat = list(res["classificacoes"][0]["categoria"].keys())[0]
            for s in res["series"]:
                cod = s["localidade"]["id"]
                if cod not in acc:
                    continue
                val = num(list(s["serie"].values())[0])
                if cat == CAT_TOTAL:
                    acc[cod]["total"] = val
                elif cat in CATS_60MAIS:
                    if val is not None:
                        acc[cod]["p60"] += val
                        acc[cod]["tem60"] = True
        for c, a in acc.items():
            tot = a["total"]
            p60 = a["p60"] if a["tem60"] else None
            idosos[c] = (tot, p60)
        print(f"    lote {i//lote+1}: {len(chunk)} munis (acumulado {len(idosos)})")
    com = sum(1 for t, p in idosos.values() if p is not None and t)
    print(f"    municipios com 60+ valido: {com}")
    return idosos


def main():
    conn = psycopg2.connect(SUPERUSER_URL)
    conn.autocommit = False
    cur = conn.cursor()

    # DDL idempotente
    cur.execute("""
        CREATE TABLE IF NOT EXISTS municipios_perfil (
            municipio_cod INTEGER PRIMARY KEY,
            municipio_nome TEXT,
            uf CHAR(2),
            populacao INTEGER,
            pib_total_mil NUMERIC,
            pib_per_capita NUMERIC,
            pct_idosos NUMERIC(5,2),
            captado_em TIMESTAMP DEFAULT NOW()
        );
    """)
    cur.execute("GRANT ALL ON municipios_perfil TO wins_saude;")
    conn.commit()

    # Base: reaproveitar nome/uf/populacao de desertos_medicos (municipio_cod = 6 digitos)
    cur.execute("SELECT municipio_cod, municipio_nome, uf, populacao FROM desertos_medicos;")
    base = {row[0]: {"nome": row[1], "uf": row[2], "pop": row[3]} for row in cur.fetchall()}
    print(f"Base desertos_medicos: {len(base)} municipios")

    # Captar dados IBGE
    pib = captar_pib()                      # cod7 -> pib mil R$
    idosos = captar_idosos(list(pib.keys()))  # cod7 -> (total, p60)

    # Montar linhas casando cod7[:6] -> municipio_cod
    rows = []
    for cod7, pib_mil in pib.items():
        cod6 = int(cod7[:6])
        b = base.get(cod6, {})
        nome = b.get("nome")
        uf = b.get("uf")
        pop = b.get("pop")
        tot_censo, p60 = idosos.get(cod7, (None, None))

        # populacao: preferir desertos_medicos; senao total do Censo 2022
        populacao = pop if pop else (int(tot_censo) if tot_censo else None)

        # PIB per capita = (pib_mil * 1000) / populacao  (derivado; 5938 nao expoe per capita)
        per_capita = None
        if pib_mil is not None and populacao:
            per_capita = round(pib_mil * 1000.0 / populacao, 2)

        # pct_idosos a partir do Censo 2022 (base = total do proprio Censo p/ coerencia)
        pct = None
        if p60 is not None and tot_censo:
            pct = round(p60 / tot_censo * 100.0, 2)

        rows.append((cod6, nome, uf, populacao, pib_mil, per_capita, pct))

    # UPSERT
    upsert = """
        INSERT INTO municipios_perfil
            (municipio_cod, municipio_nome, uf, populacao, pib_total_mil, pib_per_capita, pct_idosos, captado_em)
        VALUES (%s,%s,%s,%s,%s,%s,%s, NOW())
        ON CONFLICT (municipio_cod) DO UPDATE SET
            municipio_nome = COALESCE(EXCLUDED.municipio_nome, municipios_perfil.municipio_nome),
            uf            = COALESCE(EXCLUDED.uf, municipios_perfil.uf),
            populacao     = COALESCE(EXCLUDED.populacao, municipios_perfil.populacao),
            pib_total_mil = EXCLUDED.pib_total_mil,
            pib_per_capita= EXCLUDED.pib_per_capita,
            pct_idosos    = EXCLUDED.pct_idosos,
            captado_em    = NOW();
    """
    cur.executemany(upsert, rows)
    conn.commit()
    print(f"\nUPSERT: {len(rows)} municipios em municipios_perfil")

    # ---- RELATORIO ----
    print("\n===== RELATORIO =====")
    print(f"Ano PIB usado: {PIB_ANO} (agregado 5938, variavel 37)")
    cur.execute("SELECT count(*) FROM municipios_perfil;")
    print("Municipios na tabela:", cur.fetchone()[0])
    cur.execute("SELECT count(*) FROM municipios_perfil WHERE pib_total_mil IS NOT NULL;")
    print("Com PIB:", cur.fetchone()[0])
    cur.execute("SELECT SUM(pib_total_mil) FROM municipios_perfil;")
    pib_nac = cur.fetchone()[0]
    print(f"PIB nacional somado: {pib_nac:,.0f} mil R$  (= R$ {float(pib_nac)/1e6:,.2f} bilhoes)")
    cur.execute("SELECT AVG(pib_per_capita) FROM municipios_perfil WHERE pib_per_capita IS NOT NULL;")
    print(f"Media PIB per capita: R$ {cur.fetchone()[0]:,.2f}")
    cur.execute("SELECT count(*), AVG(pct_idosos) FROM municipios_perfil WHERE pct_idosos IS NOT NULL;")
    c_id, avg_id = cur.fetchone()
    print(f"pct_idosos: {'SIM' if c_id else 'NAO'} -> {c_id} munis, media {avg_id:.2f}%" if c_id else "pct_idosos: NAO obtido")
    print("\nTop 10 municipios por PIB:")
    cur.execute("""SELECT municipio_nome, uf, pib_total_mil, pib_per_capita, pct_idosos
                   FROM municipios_perfil ORDER BY pib_total_mil DESC NULLS LAST LIMIT 10;""")
    for r in cur.fetchall():
        nome, uf, pibm, pc, pct = r
        print(f"  {nome or '?'}/{uf or '?'}: PIB {float(pibm)/1e6:,.2f} bi | per capita R$ {pc:,.0f} | idosos {pct}%")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
