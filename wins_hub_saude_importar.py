"""
WiNS Hub Saude - Importacao dos CSVs para o PostgreSQL local
============================================================
Le as credenciais de .env.saude e importa os CSVs da pasta
wins_hub_saude_dados/ para as tabelas do banco wins_hub_saude.

Faz tambem (idempotente):
  - reconciliacao do schema: adiciona as colunas geradas
    tem_email / tem_telefone / tem_cnpj em `estabelecimentos`;
  - (re)cria a view stats_saude.

CSVs importados:
  estabelecimentos_cnes.csv   -> estabelecimentos   (base completa CNES/DATASUS)
  operadoras_ans.csv          -> operadoras_ans      (ANS)
  medicos_mais_medicos.csv    -> medicos             (Programa Mais Medicos)

Requisitos:
    pip install psycopg2-binary python-dotenv

Uso:
    python wins_hub_saude_importar.py
"""

import os
import csv
import sys

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import execute_values

csv.field_size_limit(10 * 1024 * 1024)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DADOS_DIR = os.path.join(BASE_DIR, "wins_hub_saude_dados")
BATCH = 5000

load_dotenv(os.path.join(BASE_DIR, ".env.saude"))


# ─────────────────────────────────────────
# LIMPEZA DE VALORES
# ─────────────────────────────────────────
def fix_mojibake(s: str) -> str:
    """Repara texto UTF-8 que foi gravado com dupla codificacao (ex: 'BENEFÃCIOS')."""
    if not s or s.isascii():
        return s
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def clean(v):
    """String vazia -> None."""
    if v is None:
        return None
    v = v.strip()
    return v if v != "" else None


def clean_txt(v):
    v = clean(v)
    return fix_mojibake(v) if v else v


def strip_float0(v):
    """'32.0' -> '32' (campos que vieram como float no CSV de origem)."""
    v = clean(v)
    if v and v.endswith(".0") and v[:-2].isdigit():
        return v[:-2]
    return v


# ─────────────────────────────────────────
# CONEXAO
# ─────────────────────────────────────────
def conectar():
    dsn = os.environ.get("DATABASE_URL")
    if dsn:
        return psycopg2.connect(dsn)
    return psycopg2.connect(
        host=os.environ["DB_HOST"], port=os.environ["DB_PORT"],
        dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"],
        password=os.environ["DB_PASS"],
    )


def conectar_super():
    """Conexao de superusuario para DDL. Cai para a conexao app se nao houver."""
    dsn = os.environ.get("SUPERUSER_URL")
    return psycopg2.connect(dsn) if dsn else None


# ─────────────────────────────────────────
# RECONCILIACAO DE SCHEMA + VIEW
# ─────────────────────────────────────────
DDL = """
-- A view depende das colunas geradas; removemos antes de mexer no schema e recriamos no fim.
DROP VIEW IF EXISTS stats_saude;

-- CNES as vezes traz varios telefones num campo so (ex: '(34)33254300 / 33121079').
-- Removemos a coluna gerada dependente para poder alargar 'telefone', depois recriamos.
ALTER TABLE estabelecimentos DROP COLUMN IF EXISTS tem_telefone;
ALTER TABLE estabelecimentos ALTER COLUMN telefone TYPE VARCHAR(60);

ALTER TABLE estabelecimentos
    ADD COLUMN IF NOT EXISTS tem_email SMALLINT
    GENERATED ALWAYS AS (CASE WHEN email IS NOT NULL AND email <> '' THEN 1 ELSE 0 END) STORED;
ALTER TABLE estabelecimentos
    ADD COLUMN IF NOT EXISTS tem_telefone SMALLINT
    GENERATED ALWAYS AS (CASE WHEN telefone IS NOT NULL AND telefone <> '' THEN 1 ELSE 0 END) STORED;
ALTER TABLE estabelecimentos
    ADD COLUMN IF NOT EXISTS tem_cnpj SMALLINT
    GENERATED ALWAYS AS (CASE WHEN cnpj IS NOT NULL AND cnpj <> '' THEN 1 ELSE 0 END) STORED;

CREATE OR REPLACE VIEW stats_saude AS
SELECT
    COUNT(*)                                                  AS total_estabelecimentos,
    SUM(tem_email)                                            AS com_email,
    SUM(tem_telefone)                                         AS com_telefone,
    SUM(tem_cnpj)                                             AS com_cnpj,
    SUM(tem_internacao)                                       AS hospitais,
    COUNT(DISTINCT uf)                                        AS ufs_cobertas,
    COUNT(DISTINCT municipio_cod)                             AS municipios_cobertos,
    SUM(CASE WHEN decisor_nome IS NOT NULL THEN 1 ELSE 0 END) AS com_decisor_enriquecido
FROM estabelecimentos;

-- Garante que o usuario da aplicacao consiga inserir/ler.
GRANT ALL ON ALL TABLES IN SCHEMA public TO wins_saude;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO wins_saude;
GRANT SELECT ON stats_saude TO wins_saude;
"""


def reconciliar(super_conn):
    print("\n[schema] Reconciliando colunas geradas e view stats_saude (como postgres)...")
    with super_conn.cursor() as cur:
        cur.execute(DDL)
    super_conn.commit()
    print("  OK - tem_email/tem_telefone/tem_cnpj garantidas; view stats_saude criada; grants aplicados.")


# ─────────────────────────────────────────
# IMPORTACAO GENERICA
# ─────────────────────────────────────────
def importar(conn, *, csv_nome, tabela, conflito, mapa):
    """
    mapa: lista de (coluna_destino, coluna_csv, funcao_limpeza)
    conflito: coluna(s) do ON CONFLICT ... DO NOTHING
    """
    caminho = os.path.join(DADOS_DIR, csv_nome)
    if not os.path.exists(caminho):
        print(f"  AVISO: {csv_nome} nao encontrado - pulando {tabela}.")
        return 0

    destino_cols = [m[0] for m in mapa]
    sql = (f"INSERT INTO {tabela} ({', '.join(destino_cols)}) VALUES %s "
           f"ON CONFLICT ({conflito}) DO NOTHING")

    total_lidos = 0
    inseridos = 0
    lote = []
    with conn.cursor() as cur, open(caminho, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total_lidos += 1
            lote.append(tuple(func(row.get(csv_col)) for _, csv_col, func in mapa))
            if len(lote) >= BATCH:
                execute_values(cur, sql, lote, page_size=BATCH)
                inseridos += cur.rowcount
                lote.clear()
        if lote:
            execute_values(cur, sql, lote, page_size=BATCH)
            inseridos += cur.rowcount
    conn.commit()
    pulados = total_lidos - inseridos
    extra = f" ({pulados:,} ja existentes/duplicados ignorados)" if pulados else ""
    print(f"  {tabela:<16} {inseridos:>8,} inseridos de {total_lidos:,} lidos{extra}")
    return inseridos


# ─────────────────────────────────────────
# MAPEAMENTOS
# ─────────────────────────────────────────
MAPA_ESTAB = [
    ("cnes_id", "cnes_id", clean), ("cnpj", "cnpj", clean),
    ("cnpj_entidade", "cnpj_entidade", clean), ("razao_social", "razao_social", clean_txt),
    ("nome_fantasia", "nome_fantasia", clean_txt), ("tipo_unidade_cod", "tipo_unidade_cod", clean),
    ("logradouro", "logradouro", clean_txt), ("numero", "numero", clean),
    ("bairro", "bairro", clean_txt), ("cep", "cep", clean),
    ("municipio_cod", "municipio_cod", clean), ("uf_cod", "uf_cod", clean),
    ("uf", "uf", clean), ("telefone", "telefone", clean),
    ("email", "email", clean), ("latitude", "latitude", clean),
    ("longitude", "longitude", clean), ("turno", "turno", clean),
    ("data_atualizacao_cnes", "data_atualizacao_cnes", clean),
]

MAPA_OPERADORAS = [
    ("registro_ans", "registro_operadora", clean), ("cnpj", "cnpj", clean),
    ("razao_social", "razao_social", clean_txt), ("nome_fantasia", "nome_fantasia", clean_txt),
    ("modalidade", "modalidade", clean_txt), ("logradouro", "logradouro", clean_txt),
    ("numero", "numero", clean), ("complemento", "complemento", clean_txt),
    ("bairro", "bairro", clean_txt), ("municipio", "cidade", clean_txt),
    ("uf", "uf", clean), ("cep", "cep", clean),
    ("ddd", "ddd", strip_float0), ("telefone", "telefone", strip_float0),
    ("email", "endereco_eletronico", clean), ("representante", "representante", clean_txt),
    ("cargo_representante", "cargo_representante", clean_txt),
    ("regiao_comercializacao", "regiao_de_comercializacao", strip_float0),
    ("data_registro_ans", "data_registro_ans", clean),
]

MAPA_MEDICOS = [
    ("crm", "crm", clean), ("uf_crm", "uf", clean),
    ("nome", "no_profissional", clean_txt), ("situacao", "perfil", clean),
    ("municipio_atuacao", "municipio_dsei", clean_txt), ("uf_atuacao", "uf", clean),
]


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
def main():
    print("=" * 60)
    print("WiNS Hub Saude - Importacao para PostgreSQL")
    print("=" * 60)

    conn = conectar()
    print(f"  Conectado a {os.environ.get('DB_NAME')} @ {os.environ.get('DB_HOST')}")

    super_conn = conectar_super()
    if super_conn is None:
        print("  AVISO: SUPERUSER_URL ausente - tentando DDL com o usuario da aplicacao.")
        super_conn = conn
    try:
        reconciliar(super_conn)
    finally:
        if super_conn is not conn:
            super_conn.close()

    print("\n[import] Carregando CSVs...")
    importar(conn, csv_nome="estabelecimentos_cnes.csv", tabela="estabelecimentos",
             conflito="cnes_id", mapa=MAPA_ESTAB)
    importar(conn, csv_nome="operadoras_ans.csv", tabela="operadoras_ans",
             conflito="registro_ans", mapa=MAPA_OPERADORAS)
    importar(conn, csv_nome="medicos_mais_medicos.csv", tabela="medicos",
             conflito="crm, uf_crm", mapa=MAPA_MEDICOS)

    print("\n[stats] SELECT * FROM stats_saude;")
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM stats_saude;")
        cols = [d[0] for d in cur.description]
        vals = cur.fetchone()
        for c, v in zip(cols, vals):
            print(f"  {c:<26} {v:,}" if isinstance(v, int) else f"  {c:<26} {v}")

    conn.close()
    print("=" * 60)
    print("Importacao concluida.")
    print("=" * 60)


if __name__ == "__main__":
    sys.exit(main())
