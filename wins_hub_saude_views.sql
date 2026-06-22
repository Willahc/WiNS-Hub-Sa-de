-- ============================================================
-- WiNS Hub Saude - Views de prospeccao (Tarefa 3)
-- Executar como superusuario postgres (tabelas/views pertencem a postgres).
-- ============================================================

-- Decisores prontos (com algum contato identificado)
CREATE OR REPLACE VIEW decisores_prontos AS
SELECT
    e.cnes_id,
    e.razao_social,
    e.nome_fantasia,
    e.uf,
    e.municipio_nome,
    e.tipo_unidade_cod,
    e.telefone         AS tel_estabelecimento,
    e.email            AS email_estabelecimento,
    e.decisor_nome,
    e.decisor_cargo,
    e.decisor_email,
    e.decisor_especialidade,
    e.fonte_enriquecimento,
    e.tem_internacao,
    e.atende_sus
FROM estabelecimentos e
WHERE e.decisor_nome IS NOT NULL
   OR (e.email IS NOT NULL AND e.email <> '')
ORDER BY e.tem_internacao DESC, e.uf;

-- Cobertura por UF
CREATE OR REPLACE VIEW cobertura_uf AS
SELECT
    uf,
    COUNT(*)                                                 AS total,
    SUM(tem_email)                                           AS com_email,
    SUM(tem_telefone)                                        AS com_telefone,
    SUM(CASE WHEN decisor_nome IS NOT NULL THEN 1 ELSE 0 END) AS com_decisor,
    ROUND(SUM(tem_email)::numeric / COUNT(*) * 100, 1)       AS pct_email
FROM estabelecimentos
GROUP BY uf
ORDER BY total DESC;

-- Stats gerais
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

GRANT SELECT ON decisores_prontos, cobertura_uf, stats_saude TO wins_saude;
