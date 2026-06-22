# ============================================================
# WiNS Hub Saude - Setup PostgreSQL local (Windows PowerShell)
# ============================================================
# Pre-requisito: PostgreSQL instalado
# Download: https://www.enterprisedb.com/downloads/postgres-postgresql-installers
# Versao recomendada: PostgreSQL 16
#
# Como rodar:
#   1. Abra o PowerShell como Administrador
#   2. cd ate a pasta onde salvou este arquivo
#   3. Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   4. .\wins_hub_saude_setup.ps1
# ============================================================

$ErrorActionPreference = "Stop"

# ─────────────────────────────────────────
# CONFIG — ajuste se necessario
# ─────────────────────────────────────────
$PG_BIN     = "C:\Program Files\PostgreSQL\18\bin"   # pasta bin do PostgreSQL
$PG_HOST    = "localhost"
$PG_PORT    = "5432"
$PG_SUPER   = "postgres"                              # superusuario padrao
$DB_NAME    = "wins_hub_saude"
$DB_USER    = "wins_saude"
$DB_PASS    = if ($env:WINS_DB_PASS) { $env:WINS_DB_PASS } else { Read-Host "Defina a senha do usuario de aplicacao ($DB_USER)" }

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  WiNS Hub Saude - Setup do banco local" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# ─────────────────────────────────────────
# VERIFICAR SE POSTGRES ESTA INSTALADO
# ─────────────────────────────────────────
$psql = Join-Path $PG_BIN "psql.exe"
if (-not (Test-Path $psql)) {
    Write-Host "ERRO: psql.exe nao encontrado em $PG_BIN" -ForegroundColor Red
    Write-Host "Ajuste a variavel PG_BIN no topo do script." -ForegroundColor Yellow
    Write-Host "Download: https://www.enterprisedb.com/downloads/postgres-postgresql-installers" -ForegroundColor Yellow
    exit 1
}

$psqlVersion = & $psql --version 2>&1
Write-Host "PostgreSQL encontrado: $psqlVersion" -ForegroundColor Green

# ─────────────────────────────────────────
# SENHA DO SUPERUSUARIO
# ─────────────────────────────────────────
Write-Host ""
$pgPass = Read-Host "Digite a senha do usuario postgres (superusuario)" -AsSecureString
$pgPassPlain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($pgPass)
)
$env:PGPASSWORD = $pgPassPlain

# ─────────────────────────────────────────
# CRIAR USUARIO E BANCO
# ─────────────────────────────────────────
Write-Host ""
Write-Host "[1/3] Criando usuario '$DB_USER' e banco '$DB_NAME'..." -ForegroundColor Yellow

$sqlSetup = @"
-- Criar usuario se nao existir
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$DB_USER') THEN
    CREATE ROLE $DB_USER LOGIN PASSWORD '$DB_PASS';
    RAISE NOTICE 'Usuario $DB_USER criado.';
  ELSE
    ALTER ROLE $DB_USER WITH PASSWORD '$DB_PASS';
    RAISE NOTICE 'Usuario $DB_USER ja existia -- senha atualizada.';
  END IF;
END
\$\$;

-- Criar banco se nao existir
SELECT 'CREATE DATABASE $DB_NAME OWNER $DB_USER ENCODING ''UTF8'' LC_COLLATE ''pt_BR.UTF-8'' LC_CTYPE ''pt_BR.UTF-8'' TEMPLATE template0'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$DB_NAME')\gexec
"@

$sqlSetup | & $psql -h $PG_HOST -p $PG_PORT -U $PG_SUPER -c $sqlSetup 2>&1

# Criar banco separadamente (mais confiavel)
$checkDb = & $psql -h $PG_HOST -p $PG_PORT -U $PG_SUPER -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" 2>&1
if ($checkDb -ne "1") {
    & $psql -h $PG_HOST -p $PG_PORT -U $PG_SUPER -c "CREATE DATABASE $DB_NAME OWNER $DB_USER ENCODING 'UTF8';" 2>&1
    Write-Host "  Banco '$DB_NAME' criado." -ForegroundColor Green
} else {
    Write-Host "  Banco '$DB_NAME' ja existe." -ForegroundColor Green
}

# Garantir permissoes
& $psql -h $PG_HOST -p $PG_PORT -U $PG_SUPER -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;" 2>&1

# ─────────────────────────────────────────
# CRIAR SCHEMA E TABELAS
# ─────────────────────────────────────────
Write-Host ""
Write-Host "[2/3] Criando estrutura de tabelas..." -ForegroundColor Yellow

$env:PGPASSWORD = $pgPassPlain

$sqlSchema = @"
-- ============================================================
-- WINS HUB SAUDE - Schema inicial
-- ============================================================

-- Extensoes
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ─────────────────────────────────────────
-- TABELA: estabelecimentos
-- Fonte: CNES / API DEMAS Ministerio da Saude
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS estabelecimentos (
    id                  SERIAL PRIMARY KEY,

    -- Identificadores
    cnes_id             INTEGER UNIQUE,
    cnpj                VARCHAR(18),
    cnpj_entidade       VARCHAR(18),

    -- Dados cadastrais
    razao_social        TEXT,
    nome_fantasia       TEXT,
    tipo_unidade_cod    INTEGER,
    esfera              VARCHAR(20),        -- FEDERAL / ESTADUAL / MUNICIPAL / PRIVADA

    -- Endereco
    logradouro          TEXT,
    numero              VARCHAR(20),
    bairro              TEXT,
    cep                 VARCHAR(10),
    municipio_cod       INTEGER,
    uf_cod              INTEGER,
    uf                  CHAR(2),
    municipio_nome      TEXT,

    -- Contato
    telefone            VARCHAR(30),
    email               TEXT,

    -- Geo
    latitude            NUMERIC(12,8),
    longitude           NUMERIC(12,8),

    -- Capacidade
    tem_internacao      SMALLINT DEFAULT 0,
    tem_cirurgia        SMALLINT DEFAULT 0,
    tem_centro_obstetrico SMALLINT DEFAULT 0,
    atende_sus          VARCHAR(3),
    turno               TEXT,

    -- Flags de qualidade
    tem_telefone        SMALLINT GENERATED ALWAYS AS (CASE WHEN telefone IS NOT NULL AND telefone <> '' THEN 1 ELSE 0 END) STORED,
    tem_email           SMALLINT GENERATED ALWAYS AS (CASE WHEN email IS NOT NULL AND email <> '' THEN 1 ELSE 0 END) STORED,
    tem_cnpj            SMALLINT GENERATED ALWAYS AS (CASE WHEN cnpj IS NOT NULL AND cnpj <> '' THEN 1 ELSE 0 END) STORED,

    -- Enriquecimento (preenchido depois)
    decisor_nome        TEXT,
    decisor_cargo       TEXT,
    decisor_email       TEXT,
    decisor_linkedin    TEXT,
    decisor_telefone    TEXT,
    decisor_crm         VARCHAR(20),
    decisor_especialidade TEXT,
    fonte_enriquecimento TEXT,
    enriquecido_em      TIMESTAMP,

    -- Controle
    data_atualizacao_cnes DATE,
    captado_em          TIMESTAMP DEFAULT NOW(),
    atualizado_em       TIMESTAMP DEFAULT NOW()
);

-- ─────────────────────────────────────────
-- TABELA: operadoras_ans
-- Fonte: ANS - dados abertos operadoras ativas
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS operadoras_ans (
    id                  SERIAL PRIMARY KEY,
    registro_ans        VARCHAR(20) UNIQUE,
    cnpj                VARCHAR(18),
    razao_social        TEXT,
    nome_fantasia       TEXT,
    modalidade          VARCHAR(80),

    -- Endereco
    logradouro          TEXT,
    numero              VARCHAR(20),
    complemento         TEXT,
    bairro              TEXT,
    municipio           TEXT,
    uf                  CHAR(2),
    cep                 VARCHAR(10),

    -- Contato direto
    ddd                 VARCHAR(5),
    telefone            VARCHAR(20),
    email               TEXT,

    -- Decisor identificado
    representante       TEXT,
    cargo_representante TEXT,
    regiao_comercializacao TEXT,

    -- Flags
    tem_email           SMALLINT GENERATED ALWAYS AS (CASE WHEN email IS NOT NULL AND email <> '' THEN 1 ELSE 0 END) STORED,
    tem_representante   SMALLINT GENERATED ALWAYS AS (CASE WHEN representante IS NOT NULL AND representante <> '' THEN 1 ELSE 0 END) STORED,

    data_registro_ans   DATE,
    captado_em          TIMESTAMP DEFAULT NOW()
);

-- ─────────────────────────────────────────
-- TABELA: medicos
-- Fonte: CFM webservice (R$772/ano) ou scraping portal
-- Preenchida em fase 2
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS medicos (
    id                  SERIAL PRIMARY KEY,
    crm                 VARCHAR(20),
    uf_crm              CHAR(2),
    nome                TEXT,
    situacao            VARCHAR(30),        -- ATIVO / CANCELADO / SUSPENSO
    especialidades      TEXT[],             -- array de especialidades
    municipio_atuacao   TEXT,
    uf_atuacao          CHAR(2),

    -- Vinculo com estabelecimento (preenchido no cruzamento)
    cnes_id             INTEGER REFERENCES estabelecimentos(cnes_id),

    -- Contato (raramente disponivel via CFM publico)
    email               TEXT,
    telefone            TEXT,

    captado_em          TIMESTAMP DEFAULT NOW(),
    atualizado_em       TIMESTAMP DEFAULT NOW(),

    UNIQUE(crm, uf_crm)
);

-- ─────────────────────────────────────────
-- TABELA: desertos_medicos
-- Municipios com grande populacao e baixa cobertura
-- Analogo ao deserto veterinario do Agro
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS desertos_medicos (
    id                  SERIAL PRIMARY KEY,
    municipio_cod       INTEGER UNIQUE,
    municipio_nome      TEXT,
    uf                  CHAR(2),
    populacao           INTEGER,
    medicos_por_mil_hab NUMERIC(6,2),
    estabelecimentos_sus INTEGER DEFAULT 0,
    classificacao       VARCHAR(20),        -- DESERTO / BAIXA_COBERTURA / NORMAL
    captado_em          TIMESTAMP DEFAULT NOW()
);

-- ─────────────────────────────────────────
-- TABELA: hospitais_leitos
-- Fonte: DEMAS /assistencia-a-saude/hospitais-e-leitos
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS hospitais_leitos (
    id                  SERIAL PRIMARY KEY,
    cnes_id             INTEGER REFERENCES estabelecimentos(cnes_id),
    raw_json            JSONB,              -- armazena o JSON completo da API
    captado_em          TIMESTAMP DEFAULT NOW()
);

-- ─────────────────────────────────────────
-- INDICES
-- ─────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_estab_cnpj      ON estabelecimentos(cnpj);
CREATE INDEX IF NOT EXISTS idx_estab_uf        ON estabelecimentos(uf);
CREATE INDEX IF NOT EXISTS idx_estab_municipio ON estabelecimentos(municipio_cod);
CREATE INDEX IF NOT EXISTS idx_estab_email     ON estabelecimentos(email) WHERE email IS NOT NULL AND email <> '';
CREATE INDEX IF NOT EXISTS idx_estab_tipo      ON estabelecimentos(tipo_unidade_cod);
CREATE INDEX IF NOT EXISTS idx_estab_internacao ON estabelecimentos(tem_internacao);
CREATE INDEX IF NOT EXISTS idx_estab_razao_trgm ON estabelecimentos USING gin(razao_social gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_ans_cnpj        ON operadoras_ans(cnpj);
CREATE INDEX IF NOT EXISTS idx_ans_uf          ON operadoras_ans(uf);
CREATE INDEX IF NOT EXISTS idx_ans_modalidade  ON operadoras_ans(modalidade);

CREATE INDEX IF NOT EXISTS idx_medicos_uf      ON medicos(uf_atuacao);
CREATE INDEX IF NOT EXISTS idx_medicos_cnes    ON medicos(cnes_id);

-- ─────────────────────────────────────────
-- VIEWS UTEIS
-- ─────────────────────────────────────────

-- Visao de cobertura por UF
CREATE OR REPLACE VIEW cobertura_uf AS
SELECT
    uf,
    COUNT(*)                                    AS total_estabelecimentos,
    SUM(tem_email)                              AS com_email,
    SUM(tem_telefone)                           AS com_telefone,
    SUM(tem_cnpj)                               AS com_cnpj,
    SUM(tem_internacao)                         AS hospitais,
    ROUND(SUM(tem_email)::numeric / COUNT(*) * 100, 1) AS pct_email
FROM estabelecimentos
GROUP BY uf
ORDER BY total_estabelecimentos DESC;

-- Decisores prontos para prospeccao
CREATE OR REPLACE VIEW decisores_prontos AS
SELECT
    e.cnes_id,
    e.razao_social,
    e.nome_fantasia,
    e.uf,
    e.municipio_nome,
    e.tipo_unidade_cod,
    e.telefone,
    e.email                AS email_estabelecimento,
    e.decisor_nome,
    e.decisor_cargo,
    e.decisor_email,
    e.decisor_especialidade,
    e.tem_internacao,
    e.atende_sus
FROM estabelecimentos e
WHERE e.decisor_nome IS NOT NULL
   OR (e.email IS NOT NULL AND e.email <> '')
ORDER BY e.tem_internacao DESC, e.uf;

-- Stats gerais (equivalente ao stats-public do Comercial)
CREATE OR REPLACE VIEW stats_saude AS
SELECT
    COUNT(*)                                        AS total_estabelecimentos,
    SUM(tem_email)                                  AS com_email,
    SUM(tem_telefone)                               AS com_telefone,
    SUM(tem_cnpj)                                   AS com_cnpj,
    SUM(tem_internacao)                             AS hospitais,
    COUNT(DISTINCT uf)                              AS ufs_cobertas,
    COUNT(DISTINCT municipio_cod)                   AS municipios_cobertos,
    SUM(CASE WHEN decisor_nome IS NOT NULL THEN 1 ELSE 0 END) AS com_decisor_enriquecido
FROM estabelecimentos;

-- ─────────────────────────────────────────
-- PERMISSOES
-- ─────────────────────────────────────────
GRANT ALL ON ALL TABLES IN SCHEMA public TO $DB_USER;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO $DB_USER;
GRANT ALL ON ALL FUNCTIONS IN SCHEMA public TO $DB_USER;

SELECT 'Schema WiNS Hub Saude criado com sucesso!' AS status;
"@

# Salvar SQL em arquivo temporario e executar
$sqlFile = "$env:TEMP\wins_hub_saude_schema.sql"
$sqlSchema | Out-File -FilePath $sqlFile -Encoding UTF8

$env:PGPASSWORD = $pgPassPlain
& $psql -h $PG_HOST -p $PG_PORT -U $PG_SUPER -d $DB_NAME -f $sqlFile

Write-Host "  Tabelas e indices criados." -ForegroundColor Green

# ─────────────────────────────────────────
# GERAR .ENV PARA O CODE
# ─────────────────────────────────────────
Write-Host ""
Write-Host "[3/3] Gerando arquivo .env para o Code..." -ForegroundColor Yellow

$envContent = @"
# WiNS Hub Saude - Variaveis de ambiente
# Gerado automaticamente pelo setup.ps1

DB_HOST=$PG_HOST
DB_PORT=$PG_PORT
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASS=$DB_PASS
DATABASE_URL=postgresql://${DB_USER}:${DB_PASS}@${PG_HOST}:${PG_PORT}/${DB_NAME}
"@

$envContent | Out-File -FilePath ".\.env.saude" -Encoding UTF8

# ─────────────────────────────────────────
# RESUMO FINAL
# ─────────────────────────────────────────
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  Setup concluido!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Banco:    $DB_NAME" -ForegroundColor White
Write-Host "  Usuario:  $DB_USER" -ForegroundColor White
Write-Host "  Senha:    $DB_PASS" -ForegroundColor White
Write-Host "  Host:     ${PG_HOST}:${PG_PORT}" -ForegroundColor White
Write-Host ""
Write-Host "  Tabelas criadas:" -ForegroundColor White
Write-Host "    - estabelecimentos     (CNES - 300k+ registros esperados)" -ForegroundColor Gray
Write-Host "    - operadoras_ans       (ANS - 1.1k registros)" -ForegroundColor Gray
Write-Host "    - medicos              (CFM - fase 2)" -ForegroundColor Gray
Write-Host "    - desertos_medicos     (analise geografica)" -ForegroundColor Gray
Write-Host "    - hospitais_leitos     (DEMAS)" -ForegroundColor Gray
Write-Host ""
Write-Host "  Views criadas:" -ForegroundColor White
Write-Host "    - cobertura_uf         (cobertura por estado)" -ForegroundColor Gray
Write-Host "    - decisores_prontos    (leads prospectaveis)" -ForegroundColor Gray
Write-Host "    - stats_saude          (numeros gerais)" -ForegroundColor Gray
Write-Host ""
Write-Host "  Proximo passo:" -ForegroundColor Yellow
Write-Host "    1. Rode o script de coleta:" -ForegroundColor White
Write-Host "       python wins_hub_saude_coleta.py" -ForegroundColor Cyan
Write-Host "    2. Importe os CSVs:" -ForegroundColor White
Write-Host "       python wins_hub_saude_importar.py" -ForegroundColor Cyan
Write-Host ""
Write-Host "  String de conexao salva em: .env.saude" -ForegroundColor White
Write-Host "============================================================" -ForegroundColor Green

# Limpar senha da memoria
$env:PGPASSWORD = ""
Remove-Item $sqlFile -ErrorAction SilentlyContinue


