# -*- coding: utf-8 -*-
"""Deserto diagnostico: conta equipamentos de imagem por municipio (CNES 202605).
Somente dados agregados por municipio. Idempotente."""
import os, csv, io, sys, unicodedata, zipfile
from collections import defaultdict
import psycopg2
from psycopg2.extras import execute_values
from dotenv import dotenv_values

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.join(BASE_DIR, '.env.saude')
ZIP = os.path.join(BASE_DIR, 'wins_hub_saude_dados', 'BASE_DE_DADOS_CNES_202605.ZIP')
EQUIP_CSV = 'rlEstabEquipamento202605.csv'
LOOKUP_CSV = 'tbEquipamento202605.csv'

def noacc(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c)).upper().strip()

# grupos por palavra-chave na descricao (sem acento, upper)
def classify(desc):
    d = noacc(desc)
    grupos = []
    if 'TOMOGRAFO' in d and 'SIMULADOR' not in d:  # exclui simulador de radioterapia
        grupos.append('tomografo')
    if 'RESSONANCIA MAGNETICA' in d:
        grupos.append('ressonancia')
    if 'MAMOGRAFO' in d or 'MAMOGRAFIA' in d:
        grupos.append('mamografo')
    if 'RAIO X' in d:
        grupos.append('raiox')
    if 'ULTRASSOM' in d:
        grupos.append('ultrassom')
    return grupos

v = dotenv_values(ENV)
conn = psycopg2.connect(v['SUPERUSER_URL'])
cur = conn.cursor()

# ---- 1. lookup (CO_EQUIPAMENTO, CO_TIPO_EQUIPAMENTO) -> descricao + grupos ----
z = zipfile.ZipFile(ZIP)
lookup = {}            # (co_equip, co_tipo) -> set(grupos)
descricoes_por_grupo = defaultdict(set)
with z.open(LOOKUP_CSV) as f:
    t = io.TextIOWrapper(f, encoding='latin-1')
    r = csv.reader(t, delimiter=';')
    next(r)
    for row in r:
        co_eq = row[0].strip(); co_tipo = row[1].strip(); ds = row[2]
        grupos = classify(ds)
        if grupos:
            lookup[(co_eq, co_tipo)] = grupos
            for g in grupos:
                descricoes_por_grupo[g].add(ds)

print('=== Descricoes por grupo (transparencia) ===')
for g in ['tomografo','ressonancia','mamografo','raiox','ultrassom']:
    print('\n[%s] (%d descricoes):' % (g, len(descricoes_por_grupo[g])))
    for d in sorted(descricoes_por_grupo[g]):
        print('   -', d)

# ---- 2. cnes_id -> municipio_cod ----
cur.execute('select cnes_id, municipio_cod from estabelecimentos where cnes_id is not null and municipio_cod is not null')
cnes2muni = dict(cur.fetchall())
print('\ncnes->muni dict: %d entradas' % len(cnes2muni))

# ---- 3+4. percorrer arquivo de equipamentos ----
# contamos numero de EQUIPAMENTOS existentes (soma de QT_EXISTENTE) por municipio/grupo
counts = defaultdict(lambda: defaultdict(int))   # muni -> grupo -> qt
total_rows = matched = unmapped_cnes = 0
with z.open(EQUIP_CSV) as f:
    t = io.TextIOWrapper(f, encoding='latin-1')
    r = csv.reader(t, delimiter=';')
    hdr = next(r)
    iCO=0; iEQ=1; iTIPO=2; iQEX=3
    for row in r:
        total_rows += 1
        key = (row[iEQ].strip(), row[iTIPO].strip())
        grupos = lookup.get(key)
        if not grupos:
            continue
        try:
            qex = int(row[iQEX])
        except (ValueError, IndexError):
            qex = 0
        if qex <= 0:
            continue
        try:
            cid = int(row[iCO][-7:])
        except ValueError:
            continue
        muni = cnes2muni.get(cid)
        if muni is None:
            unmapped_cnes += 1
            continue
        matched += 1
        for g in grupos:
            counts[muni][g] += qex

print('\nLinhas totais: %d | linhas-equip-imagem existentes casadas: %d | cnes sem muni: %d' % (total_rows, matched, unmapped_cnes))

# ---- 5. criar tabela ----
cur.execute('''
CREATE TABLE IF NOT EXISTS densidade_equipamento (
  municipio_cod INTEGER PRIMARY KEY, municipio_nome TEXT, uf CHAR(2), populacao INTEGER,
  n_tomografo INTEGER DEFAULT 0, n_ressonancia INTEGER DEFAULT 0, n_mamografo INTEGER DEFAULT 0,
  n_raiox INTEGER DEFAULT 0, n_ultrassom INTEGER DEFAULT 0,
  tem_tomografo BOOLEAN, tem_ressonancia BOOLEAN, deserto_diagnostico BOOLEAN,
  captado_em TIMESTAMP DEFAULT NOW());
''')
cur.execute('GRANT ALL ON densidade_equipamento TO wins_saude;')
conn.commit()

# ---- iterar sobre TODOS municipios de desertos_medicos ----
cur.execute('select municipio_cod, municipio_nome, uf, populacao from desertos_medicos')
rows = []
for muni, nome, uf, pop in cur.fetchall():
    c = counts.get(muni, {})
    nt = c.get('tomografo',0); nr = c.get('ressonancia',0); nm = c.get('mamografo',0)
    nx = c.get('raiox',0); nu = c.get('ultrassom',0)
    pop_i = pop or 0
    deserto = (pop_i > 20000 and nt == 0)
    rows.append((muni, nome, uf, pop, nt, nr, nm, nx, nu, nt>0, nr>0, deserto))

# ---- 7. upsert ----
execute_values(cur, '''
INSERT INTO densidade_equipamento
 (municipio_cod, municipio_nome, uf, populacao, n_tomografo, n_ressonancia, n_mamografo,
  n_raiox, n_ultrassom, tem_tomografo, tem_ressonancia, deserto_diagnostico)
VALUES %s
ON CONFLICT (municipio_cod) DO UPDATE SET
  municipio_nome=EXCLUDED.municipio_nome, uf=EXCLUDED.uf, populacao=EXCLUDED.populacao,
  n_tomografo=EXCLUDED.n_tomografo, n_ressonancia=EXCLUDED.n_ressonancia, n_mamografo=EXCLUDED.n_mamografo,
  n_raiox=EXCLUDED.n_raiox, n_ultrassom=EXCLUDED.n_ultrassom,
  tem_tomografo=EXCLUDED.tem_tomografo, tem_ressonancia=EXCLUDED.tem_ressonancia,
  deserto_diagnostico=EXCLUDED.deserto_diagnostico, captado_em=NOW();
''', rows)
conn.commit()
print('\nUpsert: %d municipios' % len(rows))

# ---- 8. relatorio ----
cur.execute('select count(*) from densidade_equipamento where n_tomografo=0')
sem_tomo = cur.fetchone()[0]
cur.execute('select count(*) from densidade_equipamento where n_ressonancia=0')
sem_resso = cur.fetchone()[0]
cur.execute('select count(*), coalesce(sum(populacao),0) from densidade_equipamento where deserto_diagnostico')
n_des, pop_des = cur.fetchone()

print('\n================ RELATORIO ================')
print('Municipios SEM tomografo : %d' % sem_tomo)
print('Municipios SEM ressonancia: %d' % sem_resso)
print('Deserto diagnostico (pop>20k e SEM tomografo): %d municipios' % n_des)
print('Populacao somada nesses municipios: %s' % f'{pop_des:,}')

cur.execute('''select municipio_nome, uf, populacao from densidade_equipamento
              where deserto_diagnostico order by populacao desc limit 12''')
print('\n12 maiores municipios deserto_diagnostico (por populacao):')
for nome, uf, pop in cur.fetchall():
    print('   %-30s %s  %s' % (nome, uf, f'{pop:,}'))

cur.close(); conn.close()
print('\nOK.')
