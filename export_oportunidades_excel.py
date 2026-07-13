import os
import sys
import psycopg2
import pandas as pd
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env.saude"))
DSN = os.environ.get("DATABASE_URL")
OUT_FILE = os.path.join(BASE_DIR, "oportunidades_leads_b2b.xlsx")

QUERIES = {
    "1. Equipamentos Imagem": """
        SELECT 
            e.razao_social, 
            e.nome_fantasia, 
            e.uf, 
            e.municipio_nome, 
            round(mp.pib_per_capita, 0) AS pib_per_capita,
            round(ms.cobertura_privada_pct, 1) AS cobertura_privada_pct,
            e.decisor_nome, 
            e.decisor_cargo, 
            e.decisor_email, 
            e.telefone
        FROM estabelecimentos e
        JOIN densidade_equipamento de ON de.municipio_cod = e.municipio_cod
        JOIN municipios_perfil mp ON mp.municipio_cod = e.municipio_cod
        JOIN mercado_saude ms ON ms.municipio_cod = e.municipio_cod
        WHERE de.tem_tomografo = false              
          AND de.populacao > 25000                   
          AND ms.cobertura_privada_pct > 15.0        
          AND e.tipo_unidade_cod IN (5, 7, 4)        
          AND e.decisor_nome IS NOT NULL             
        ORDER BY mp.pib_per_capita DESC;
    """,
    
    "2. Telemedicina Corporativa": """
        SELECT 
            e.razao_social, 
            e.cnpj,
            e.uf, 
            e.municipio_nome, 
            dm.populacao,
            round(dm.medicos_por_mil_hab, 2) AS medicos_por_mil,
            e.decisor_nome, 
            e.decisor_cargo, 
            e.decisor_email,
            e.telefone
        FROM estabelecimentos e
        JOIN desertos_medicos dm ON dm.municipio_cod = e.municipio_cod
        WHERE dm.classificacao IN ('DESERTO', 'BAIXA_COBERTURA')
          AND e.decisor_nome IS NOT NULL
        ORDER BY dm.populacao DESC;
    """,
    
    "3. Oncologia e Diálise": """
        SELECT 
            da.municipio_nome, 
            da.uf, 
            da.populacao,
            round(da.dialise_por_mil, 2) AS dialise_por_mil,
            round(da.onco_por_mil, 2) AS onco_por_mil
        FROM demanda_apac da
        WHERE (da.dialise_por_mil > 1.5 OR da.onco_por_mil > 1.0)
          AND da.municipio_cod NOT IN (
              SELECT DISTINCT municipio_cod 
              FROM estabelecimentos 
              WHERE tipo_unidade_cod = 36            
                 OR tem_internacao = 1
          )
        ORDER BY da.dialise_por_mil DESC;
    """,
    
    "4. Outsourcing Enfermagem": """
        SELECT 
            e.razao_social, 
            e.uf, 
            e.municipio_nome, 
            round(de.enfermeiros_por_mil, 2) AS enfermeiros_por_mil,
            ds.internacoes AS internacoes_ano,
            e.decisor_nome, 
            e.decisor_cargo, 
            e.decisor_email,
            e.telefone
        FROM estabelecimentos e
        JOIN densidade_enfermagem de ON de.municipio_cod = e.municipio_cod
        JOIN demanda_sih ds ON ds.municipio_cod = e.municipio_cod
        WHERE de.classificacao = 'DESERTO'
          AND e.tem_internacao = 1                   
          AND ds.internacoes > 500                   
          AND e.decisor_nome IS NOT NULL
        ORDER BY ds.internacoes DESC;
    """,
    
    "5. Planos de Saúde B2B": """
        SELECT 
            e.razao_social, 
            e.uf, 
            e.municipio_nome, 
            round(mp.pib_per_capita, 0) AS pib_per_capita,
            round(ms.cobertura_privada_pct, 1) AS cobertura_privada_pct,
            e.decisor_nome, 
            e.decisor_cargo, 
            e.decisor_email,
            e.telefone
        FROM estabelecimentos e
        JOIN municipios_perfil mp ON mp.municipio_cod = e.municipio_cod
        JOIN mercado_saude ms ON ms.municipio_cod = e.municipio_cod
        WHERE mp.pib_per_capita > 35000              
          AND ms.cobertura_privada_pct < 10.0        
          AND e.decisor_nome IS NOT NULL             
        ORDER BY mp.pib_per_capita DESC;
    """
}

def main():
    print("=" * 60)
    print("Exportando Oportunidades e Leads para Excel com Múltiplas Abas")
    print("=" * 60)
    
    try:
        conn = psycopg2.connect(DSN)
    except Exception as e:
        print(f"Erro ao conectar ao PostgreSQL: {e}")
        sys.exit(1)
        
    writer = pd.ExcelWriter(OUT_FILE, engine="openpyxl")
    
    for sheet_name, sql in QUERIES.items():
        print(f"  Processando aba: {sheet_name} ...")
        try:
            df = pd.read_sql(sql, conn)
            df.to_excel(writer, sheet_name=sheet_name, index=False)
            print(f"    -> {len(df)} registros exportados.")
        except Exception as e:
            print(f"    -> Erro ao processar query: {e}")
            
    writer.close()
    conn.close()
    
    print("\n" + "=" * 60)
    print(f"Arquivo gerado com sucesso: {os.path.basename(OUT_FILE)}")
    print(f"Localização: {OUT_FILE}")
    print("=" * 60)

if __name__ == "__main__":
    main()
