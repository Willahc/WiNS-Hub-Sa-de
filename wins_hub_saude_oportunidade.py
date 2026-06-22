"""
WiNS Hub Saude - Indice de Oportunidade de Investimento em Saude
================================================================
Sintetiza um score por municipio cruzando as camadas ja no banco (todas
agregadas, dado aberto, sem PII):

  CARENCIA  = deficit medico + deficit enfermagem + deserto diagnostico
  DEMANDA   = internacoes/mil (SIH/SUS, demanda REAL) + populacao + % idosos
  MERCADO   = PIB per capita + cobertura privada (capacidade de pagar)

Cada componente e normalizado por percentil (percent_rank) sobre os 5.570
municipios. indice = 0.35*carencia + 0.35*demanda + 0.30*mercado.

Tiers: ALTA (top 10%), MEDIA (60-90%), BAIXA (<60%).
Tambem marca o "sweet spot" = alta carencia E mercado viavel (deficit>=60 e mercado>=50).

Uso: python wins_hub_saude_oportunidade.py
"""

import os
from dotenv import load_dotenv
import psycopg2

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env.saude"))

DDL = """
CREATE TABLE IF NOT EXISTS oportunidade_investimento (
    municipio_cod         INTEGER PRIMARY KEY,
    municipio_nome        TEXT, uf CHAR(2), populacao INTEGER,
    medicos_por_mil       NUMERIC, enfermeiros_por_mil NUMERIC,
    tem_tomografo         BOOLEAN, cobertura_privada_pct NUMERIC, beneficiarios INTEGER,
    pib_per_capita        NUMERIC, pct_idosos NUMERIC, internacoes_por_mil NUMERIC,
    score_carencia        NUMERIC(5,1), score_demanda NUMERIC(5,1), score_mercado NUMERIC(5,1),
    indice_oportunidade   NUMERIC(5,1), tier VARCHAR(10), sweet_spot BOOLEAN,
    captado_em            TIMESTAMP DEFAULT NOW()
);
ALTER TABLE oportunidade_investimento ADD COLUMN IF NOT EXISTS internacoes_por_mil NUMERIC;
GRANT ALL ON oportunidade_investimento TO wins_saude;
"""

INSERT = """
TRUNCATE oportunidade_investimento;
INSERT INTO oportunidade_investimento
 (municipio_cod,municipio_nome,uf,populacao,medicos_por_mil,enfermeiros_por_mil,
  tem_tomografo,cobertura_privada_pct,beneficiarios,pib_per_capita,pct_idosos,internacoes_por_mil,
  score_carencia,score_demanda,score_mercado,indice_oportunidade,tier,sweet_spot)
WITH base AS (
  SELECT dm.municipio_cod, dm.municipio_nome, dm.uf, dm.populacao,
         dm.medicos_por_mil_hab AS medicos_por_mil,
         de.enfermeiros_por_mil,
         COALESCE(eq.tem_tomografo,false) tem_tomografo,
         COALESCE(ms.cobertura_privada_pct,0) cobertura_privada_pct,
         COALESCE(ms.beneficiarios,0) beneficiarios,
         COALESCE(mp.pib_per_capita,0) pib_per_capita,
         COALESCE(mp.pct_idosos,0) pct_idosos,
         COALESCE(ds.internacoes_por_mil,0) internacoes_por_mil
  FROM desertos_medicos dm
  LEFT JOIN densidade_enfermagem de  USING (municipio_cod)
  LEFT JOIN densidade_equipamento eq USING (municipio_cod)
  LEFT JOIN mercado_saude ms         USING (municipio_cod)
  LEFT JOIN municipios_perfil mp     USING (municipio_cod)
  LEFT JOIN demanda_sih ds           USING (municipio_cod)
  WHERE dm.populacao > 0
),
pr AS (
  SELECT *,
    percent_rank() OVER (ORDER BY medicos_por_mil DESC)      AS d_med,
    percent_rank() OVER (ORDER BY enfermeiros_por_mil DESC)  AS d_enf,
    CASE WHEN tem_tomografo THEN 0 ELSE 1 END                AS d_diag,
    percent_rank() OVER (ORDER BY internacoes_por_mil ASC)   AS r_sih,
    percent_rank() OVER (ORDER BY populacao ASC)             AS r_pop,
    percent_rank() OVER (ORDER BY pct_idosos ASC)            AS r_idoso,
    percent_rank() OVER (ORDER BY pib_per_capita ASC)        AS r_pib,
    percent_rank() OVER (ORDER BY cobertura_privada_pct ASC) AS r_cob
  FROM base
),
sc AS (
  SELECT *,
    round(((d_med + d_enf + d_diag)/3.0*100)::numeric,1)              AS score_carencia,
    round(((0.7*r_sih + 0.2*r_pop + 0.1*r_idoso)*100)::numeric,1)     AS score_demanda,
    round(((0.6*r_pib + 0.4*r_cob)*100)::numeric,1)                   AS score_mercado
  FROM pr
),
idx AS (
  SELECT *,
    round((0.35*score_carencia + 0.35*score_demanda + 0.30*score_mercado)::numeric,1) AS indice_oportunidade
  FROM sc
),
fin AS (
  SELECT *, percent_rank() OVER (ORDER BY indice_oportunidade) AS pr_idx
  FROM idx
)
SELECT municipio_cod,municipio_nome,uf,populacao,medicos_por_mil,enfermeiros_por_mil,
       tem_tomografo,cobertura_privada_pct,beneficiarios,pib_per_capita,pct_idosos,internacoes_por_mil,
       score_carencia,score_demanda,score_mercado,indice_oportunidade,
       CASE WHEN pr_idx>=0.90 THEN 'ALTA' WHEN pr_idx>=0.60 THEN 'MEDIA' ELSE 'BAIXA' END,
       (score_carencia>=60 AND score_mercado>=50)
FROM fin;
"""


def main():
    super_url = os.environ.get("SUPERUSER_URL", os.environ["DATABASE_URL"])
    conn = psycopg2.connect(super_url)
    print("=" * 60)
    print("WiNS Hub Saude - Indice de Oportunidade de Investimento")
    print("=" * 60)
    with conn.cursor() as cur:
        cur.execute(DDL)
        cur.execute(INSERT)
    conn.commit()

    with conn.cursor() as cur:
        cur.execute("""SELECT tier, count(*), to_char(sum(populacao),'FM999G999G999'),
                       round(avg(indice_oportunidade),1)
                       FROM oportunidade_investimento GROUP BY 1
                       ORDER BY min(indice_oportunidade) DESC""")
        tiers = cur.fetchall()
        cur.execute("SELECT count(*) FROM oportunidade_investimento WHERE sweet_spot")
        sweet = cur.fetchone()[0]
        cur.execute("""SELECT municipio_nome,uf,populacao,medicos_por_mil,
                       cobertura_privada_pct,pib_per_capita,indice_oportunidade
                       FROM oportunidade_investimento WHERE sweet_spot
                       ORDER BY indice_oportunidade DESC, populacao DESC LIMIT 15""")
        top = cur.fetchall()
    conn.close()

    print("\nDistribuicao por tier:")
    print(f"{'tier':<8}{'munic':>8}{'populacao':>16}{'indice(media)':>16}")
    for t, n, pop, avg in tiers:
        print(f"{t:<8}{n:>8,}{pop:>16}{str(avg):>16}")
    print(f"\nSweet spot (carencia alta + mercado viavel): {sweet:,} municipios")
    print("\nTop 15 oportunidades 'sweet spot' (alta carencia + mercado pagante):")
    print(f"{'municipio':<26}{'UF':>3}{'pop':>9}{'med/mil':>8}{'cob%':>7}{'PIBpc':>9}{'indice':>8}")
    for nome, uf, pop, med, cob, pib, idx in top:
        print(f"{nome[:25]:<26}{uf:>3}{pop:>9,}{str(med):>8}{str(cob):>7}{int(pib):>9,}{str(idx):>8}")
    print("=" * 60)


if __name__ == "__main__":
    main()
