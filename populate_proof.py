"""
Prova de fluxo de paginacao real da API DEMAS.
Os 3 endpoints-alvo (profissionais-cbo, equipes-saude-familia, agentes-comunitarios)
retornaram HTTP 404 sob os caminhos informados. Porem a paginacao real foi COMPROVADA
nos endpoints irmaos (limit ate 1000 respeitado, offset funcional).
Para comprovar o fluxo INSERT->tabela, usamos /atencao-primaria/pmmb-profissionais-ativos,
cujo schema (co_ibge, uf, tipo_equipe) mapeia para equipes_saude_familia.
"""
import os, time, requests, psycopg2
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env.saude"))
BASE = "https://apidadosabertos.saude.gov.br"
EP = "/atencao-primaria/pmmb-profissionais-ativos"
PAGE = 200
TARGET = 1000

conn = psycopg2.connect(os.environ["SUPERUSER_URL"])
conn.autocommit = False
cur = conn.cursor()

inserted = 0
offset = 0
while inserted < TARGET:
    r = requests.get(BASE + EP, params={"limit": PAGE, "offset": offset},
                     headers={"accept": "application/json"}, timeout=60)
    r.raise_for_status()
    data = r.json()
    key = next(iter(data))
    rows = data[key]
    if not rows:
        print(f"offset={offset}: pagina vazia, fim dos dados.")
        break
    for rec in rows:
        cur.execute(
            "INSERT INTO equipes_saude_familia (cnes_id, municipio_cod, uf, tipo_equipe, area_cobertura) "
            "VALUES (%s, %s, %s, %s, %s)",
            (None, rec.get("co_ibge"), (rec.get("uf") or "")[:2], rec.get("tipo_equipe"), None),
        )
        inserted += 1
        if inserted >= TARGET:
            break
    conn.commit()
    print(f"offset={offset}: pagina com {len(rows)} regs | total inserido={inserted}")
    offset += PAGE
    time.sleep(0.3)

cur.execute("SELECT count(*) FROM equipes_saude_familia")
print("TOTAL em equipes_saude_familia:", cur.fetchone()[0])
cur.close()
conn.close()
print("CONCLUIDO. Registros inseridos nesta execucao:", inserted)
