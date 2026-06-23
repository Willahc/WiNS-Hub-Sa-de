"""
WiNS Hub Saude - Acesso espacial a leitos SUS (2SFCA, distancia em linha reta)
==============================================================================
Metodologia E2SFCA/2SFCA (Two-Step Floating Catchment Area) com decaimento
gaussiano. Captura que pacientes cruzam fronteiras municipais e que oferta
distante "pesa menos" -- muito mais defensavel que leitos/mil dentro da fronteira.

  oferta  S_j = leitos_sus do municipio j (cnes_capacidade)
  demanda P_i = populacao do municipio i (desertos_medicos)
  centroide   = media lat/long dos estabelecimentos do municipio (CNES)
  peso        W(d) = exp(-d^2 / (2 sigma^2)), d <= cutoff (km), senao 0
  R_j = S_j / sum_i P_i W_ij          (passo 1: razao oferta/demanda)
  A_i = sum_j R_j W_ij                (passo 2: acesso acumulado)

A_i alto = bom acesso; baixo = carencia de acesso. So agregado, sem PII.
Sem Docker/OSRM (linha reta). Grava em acesso_espacial.

Uso: python wins_hub_saude_acesso.py
"""
import os
import numpy as np
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import execute_values

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env.saude"))

SIGMA_KM = 30.0      # decaimento: ~peso 0.6 a 30km, 0.13 a 60km
CUTOFF_KM = 90.0     # raio de captacao (3 sigma)


def main():
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    with conn.cursor() as cur:
        # centroide = media das coords dos estabelecimentos (dentro do Brasil)
        cur.execute("""
            SELECT municipio_cod, AVG(latitude)::float, AVG(longitude)::float
            FROM estabelecimentos
            WHERE latitude IS NOT NULL AND longitude IS NOT NULL
              AND latitude BETWEEN -34 AND 6 AND longitude BETWEEN -74 AND -34
            GROUP BY municipio_cod
        """)
        cent = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        cur.execute("SELECT municipio_cod, municipio_nome, uf, populacao FROM desertos_medicos")
        info = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}
        cur.execute("SELECT municipio_cod, leitos_sus FROM cnes_capacidade")
        leitos = {r[0]: (r[1] or 0) for r in cur.fetchall()}

    # universo = municipios com centroide E populacao
    cods = [c for c in info if c in cent and info[c][2]]
    n = len(cods)
    lat = np.array([cent[c][0] for c in cods])
    lon = np.array([cent[c][1] for c in cods])
    P = np.array([info[c][2] for c in cods], dtype=float)          # demanda
    S = np.array([leitos.get(c, 0) for c in cods], dtype=float)    # oferta

    # projecao equiretangular -> km
    lat0 = np.radians(lat.mean())
    x = np.radians(lon) * np.cos(lat0) * 6371.0
    y = np.radians(lat) * 6371.0

    inv2s2 = 1.0 / (2.0 * SIGMA_KM ** 2)
    cut2 = CUTOFF_KM ** 2
    CH = 700  # tamanho do chunk de linhas

    def matvec(vec):
        out = np.zeros(n)
        for i in range(0, n, CH):
            j = slice(i, min(i + CH, n))
            d2 = (x[j, None] - x[None, :]) ** 2 + (y[j, None] - y[None, :]) ** 2
            w = np.exp(-d2 * inv2s2)
            w[d2 > cut2] = 0.0
            out[i:j.stop] = w @ vec
        return out

    print(f"2SFCA: {n} municipios | sigma={SIGMA_KM}km cutoff={CUTOFF_KM}km")
    WP = matvec(P)                       # passo 1 denominador
    R = np.where(WP > 0, S / np.where(WP > 0, WP, 1), 0.0)
    A = matvec(R)                        # passo 2 acesso
    # escala 0-100 (percentil-friendly): normaliza pelo max
    A100 = np.round(A / A.max() * 100, 2) if A.max() > 0 else A

    super_url = os.environ.get("SUPERUSER_URL", os.environ["DATABASE_URL"])
    sc = psycopg2.connect(super_url)
    with sc.cursor() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS acesso_espacial (
                municipio_cod INTEGER PRIMARY KEY, municipio_nome TEXT, uf CHAR(2),
                populacao INTEGER, leitos_sus INTEGER,
                acesso_2sfca NUMERIC(10,4), acesso_idx NUMERIC(6,2),
                captado_em TIMESTAMP DEFAULT NOW());
            GRANT ALL ON acesso_espacial TO wins_saude;
        """)
    sc.commit(); sc.close()

    linhas = []
    for k, cod in enumerate(cods):
        nome, uf, pop = info[cod]
        linhas.append((cod, nome, uf, pop, int(S[k]), float(round(A[k], 4)), float(A100[k])))

    with conn.cursor() as cur:
        cur.execute("TRUNCATE acesso_espacial")
        execute_values(cur, """
            INSERT INTO acesso_espacial (municipio_cod,municipio_nome,uf,populacao,
              leitos_sus,acesso_2sfca,acesso_idx) VALUES %s
        """, linhas, page_size=10000)
    conn.commit()

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM acesso_espacial WHERE acesso_idx < 5")
        baixo = cur.fetchone()[0]
        cur.execute("""SELECT municipio_nome, uf, populacao, acesso_idx
                       FROM acesso_espacial WHERE populacao > 50000
                       ORDER BY acesso_idx ASC LIMIT 10""")
        pior = cur.fetchall()
    conn.close()

    print("=" * 60)
    print(f"acesso_espacial gravado: {n} municipios | {baixo} com acesso_idx<5 (desertos de acesso)")
    print("Pior acesso a leitos SUS (pop>50k):")
    for nome, uf, pop, idx in pior:
        print(f"  {nome}-{uf:<3} pop {pop:>8,}  acesso_idx {idx}")
    print("=" * 60)


if __name__ == "__main__":
    main()
