"""
WiNS Hub Saúde — Coleta e tratamento local de dados
=====================================================
Roda no PC antes de criar VPS/banco.
Gera CSVs limpos prontos para análise ou importação futura.

Requisitos:
    pip install requests pandas openpyxl tqdm

Uso:
    python wins_hub_saude_coleta.py

Saída (pasta ./wins_hub_saude_dados/):
    estabelecimentos_cnes.csv   — ~300k estabelecimentos com telefone/email/geo
    operadoras_ans.csv          — ~1.1k operadoras com representante/e-mail/telefone
    hospitais_leitos.csv        — hospitais com capacidade instalada
    ubs.csv                     — unidades básicas de saúde
    medicos_mais_medicos.csv    — médicos do Programa Mais Médicos por município
    resumo.txt                  — contagens e qualidade dos dados
"""

import os
import time
import requests
import pandas as pd
from tqdm import tqdm

# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────
OUTPUT_DIR = "./wins_hub_saude_dados"
os.makedirs(OUTPUT_DIR, exist_ok=True)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "WiNS-Hub-Saude/1.0 (coleta academica dados abertos)"})

BASE_DEMAS = "https://apidadosabertos.saude.gov.br"
PAGE_SIZE  = 200   # registros por página da API DEMAS
SLEEP_REQ  = 0.3   # segundos entre requests (respeitar rate limit)


# ─────────────────────────────────────────
# UTILS
# ─────────────────────────────────────────
def salvar(df: pd.DataFrame, nome: str, info: str = ""):
    path = os.path.join(OUTPUT_DIR, nome)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"  ✅ {nome} — {len(df):,} registros{' — ' + info if info else ''}")
    return df


def get_paginado(endpoint: str, campo_lista: str, params_extra: dict = None) -> list:
    """
    Faz paginação automática na API DEMAS.
    Retorna lista completa de registros.
    """
    registros = []
    offset = 0
    params = {"limit": PAGE_SIZE, **(params_extra or {})}

    with tqdm(desc=f"  → {endpoint}", unit=" regs", leave=False) as pbar:
        while True:
            params["offset"] = offset
            try:
                r = SESSION.get(f"{BASE_DEMAS}{endpoint}", params=params, timeout=30)
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                print(f"\n  ⚠️  Erro em {endpoint} offset={offset}: {e}")
                break

            lote = data.get(campo_lista, [])
            if not lote:
                break

            registros.extend(lote)
            pbar.update(len(lote))
            offset += PAGE_SIZE
            time.sleep(SLEEP_REQ)

            if len(lote) < PAGE_SIZE:
                break  # última página

    return registros


# ─────────────────────────────────────────
# 1. CNES — ESTABELECIMENTOS DE SAÚDE
# ─────────────────────────────────────────
def coletar_estabelecimentos():
    print("\n[1/5] CNES — Estabelecimentos de saúde (API DEMAS)...")

    registros = get_paginado(
        endpoint="/cnes/estabelecimentos",
        campo_lista="estabelecimentos"
    )

    if not registros:
        print("  ❌ Nenhum registro retornado.")
        return

    df = pd.DataFrame(registros)

    # Renomear para nomes legíveis
    rename = {
        "codigo_cnes":                          "cnes_id",
        "numero_cnpj_entidade":                 "cnpj_entidade",
        "numero_cnpj":                          "cnpj",
        "nome_razao_social":                    "razao_social",
        "nome_fantasia":                        "nome_fantasia",
        "codigo_tipo_unidade":                  "tipo_unidade_cod",
        "codigo_uf":                            "uf_cod",
        "codigo_municipio":                     "municipio_cod",
        "endereco_estabelecimento":             "logradouro",
        "numero_estabelecimento":               "numero",
        "bairro_estabelecimento":               "bairro",
        "codigo_cep_estabelecimento":           "cep",
        "numero_telefone_estabelecimento":      "telefone",
        "endereco_email_estabelecimento":       "email",
        "latitude_estabelecimento_decimo_grau": "latitude",
        "longitude_estabelecimento_decimo_grau":"longitude",
        "descricao_esfera_administrativa":      "esfera",
        "descricao_turno_atendimento":          "turno",
        "estabelecimento_faz_atendimento_ambulatorial_sus": "atende_sus",
        "estabelecimento_possui_atendimento_hospitalar":    "tem_internacao",
        "estabelecimento_possui_centro_cirurgico":          "tem_cirurgia",
        "data_atualizacao":                     "data_atualizacao",
    }
    df.rename(columns={k: v for k, v in rename.items() if k in df.columns}, inplace=True)

    # Limpeza básica
    df["telefone"]  = df["telefone"].fillna("").str.strip()
    df["email"]     = df["email"].fillna("").str.strip().str.lower()
    df["razao_social"] = df["razao_social"].fillna("").str.strip().str.title()

    # Flag de contato disponível
    df["tem_telefone"] = df["telefone"].ne("").astype(int)
    df["tem_email"]    = df["email"].ne("").astype(int)
    df["tem_cnpj"]     = df["cnpj"].fillna("").ne("").astype(int)

    com_email    = df["tem_email"].sum()
    com_telefone = df["tem_telefone"].sum()
    com_cnpj     = df["tem_cnpj"].sum()

    salvar(df, "estabelecimentos_cnes.csv",
           f"e-mail: {com_email:,} | telefone: {com_telefone:,} | CNPJ: {com_cnpj:,}")

    return df


# ─────────────────────────────────────────
# 2. ANS — OPERADORAS DE PLANOS DE SAÚDE
# ─────────────────────────────────────────
def coletar_operadoras_ans():
    print("\n[2/5] ANS — Operadoras de planos de saúde ativas...")

    url = "https://dadosabertos.ans.gov.br/FTP/PDA/operadoras_de_plano_de_saude_ativas/Relatorio_cadop.csv"
    try:
        r = SESSION.get(url, timeout=60)
        r.raise_for_status()
        from io import StringIO
        df = pd.read_csv(StringIO(r.content.decode("latin-1")), sep=";", on_bad_lines="skip")
    except Exception as e:
        print(f"  ❌ Erro: {e}")
        return

    # Limpeza
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    df["endereco_eletronico"] = df.get("endereco_eletronico", pd.Series()).fillna("").str.strip().str.lower()
    df["representante"]       = df.get("representante", pd.Series()).fillna("").str.strip().str.title()
    df["cargo_representante"] = df.get("cargo_representante", pd.Series()).fillna("").str.strip()
    df["telefone"]            = df.get("telefone", pd.Series()).fillna("").str.strip()

    df["tem_email"]        = df["endereco_eletronico"].ne("").astype(int)
    df["tem_representante"]= df["representante"].ne("").astype(int)

    com_email = df["tem_email"].sum()
    com_rep   = df["tem_representante"].sum()

    salvar(df, "operadoras_ans.csv",
           f"com e-mail: {com_email:,} | com representante: {com_rep:,}")
    return df


# ─────────────────────────────────────────
# 3. DEMAS — HOSPITAIS E LEITOS
# ─────────────────────────────────────────
def coletar_hospitais():
    print("\n[3/5] DEMAS — Hospitais e leitos...")

    registros = get_paginado(
        endpoint="/assistencia-a-saude/hospitais-e-leitos",
        campo_lista="hospitais_e_leitos"
    )

    if not registros:
        print("  ⚠️  Nenhum dado retornado.")
        return

    df = pd.DataFrame(registros)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    salvar(df, "hospitais_leitos.csv")
    return df


# ─────────────────────────────────────────
# 4. DEMAS — UBS
# ─────────────────────────────────────────
def coletar_ubs():
    print("\n[4/5] DEMAS — Unidades Básicas de Saúde...")

    registros = get_paginado(
        endpoint="/assistencia-a-saude/unidade-basicas-de-saude",
        campo_lista="unidades_basicas_de_saude"
    )

    if not registros:
        # Tentar campo diferente
        registros = get_paginado(
            endpoint="/assistencia-a-saude/unidade-basicas-de-saude",
            campo_lista="results"
        )

    if not registros:
        print("  ⚠️  Nenhum dado retornado — verificar campo da lista na resposta.")
        # Fazer uma chamada manual para inspecionar
        try:
            r = SESSION.get(f"{BASE_DEMAS}/assistencia-a-saude/unidade-basicas-de-saude",
                            params={"limit": 1}, timeout=15)
            print(f"  → Status: {r.status_code} | Chaves: {list(r.json().keys())}")
        except Exception as e:
            print(f"  → Erro inspeção: {e}")
        return

    df = pd.DataFrame(registros)
    salvar(df, "ubs.csv")
    return df


# ─────────────────────────────────────────
# 5. DEMAS — MÉDICOS MAIS MÉDICOS (proxy geográfico)
# ─────────────────────────────────────────
def coletar_mais_medicos():
    print("\n[5/5] DEMAS — Profissionais Mais Médicos ativos por município...")

    registros = get_paginado(
        endpoint="/atencao-primaria/pmmb-profissionais-ativos",
        campo_lista="profissionais"
    )

    if not registros:
        # Inspecionar campo real
        try:
            r = SESSION.get(f"{BASE_DEMAS}/atencao-primaria/pmmb-profissionais-ativos",
                            params={"limit": 1}, timeout=15)
            keys = list(r.json().keys()) if r.ok else []
            print(f"  → Status: {r.status_code} | Chaves: {keys}")
            if keys:
                registros = get_paginado(
                    endpoint="/atencao-primaria/pmmb-profissionais-ativos",
                    campo_lista=keys[0]
                )
        except Exception as e:
            print(f"  → Erro inspeção: {e}")

    if not registros:
        print("  ⚠️  Nenhum dado retornado.")
        return

    df = pd.DataFrame(registros)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    salvar(df, "medicos_mais_medicos.csv")
    return df


# ─────────────────────────────────────────
# 6. RESUMO FINAL
# ─────────────────────────────────────────
def gerar_resumo(resultados: dict):
    print("\n" + "="*60)
    print("RESUMO WINS HUB SAÚDE — COLETA LOCAL")
    print("="*60)

    linhas = []
    total_registros = 0
    total_com_contato = 0

    for nome, df in resultados.items():
        if df is None or not isinstance(df, pd.DataFrame):
            continue
        n = len(df)
        total_registros += n

        col_email = next((c for c in ["email", "endereco_eletronico", "email_estabelecimento"]
                          if c in df.columns), None)
        col_tel   = next((c for c in ["telefone", "numero_telefone_estabelecimento"]
                          if c in df.columns), None)

        com_email = df[col_email].fillna("").ne("").sum() if col_email else 0
        com_tel   = df[col_tel].fillna("").ne("").sum() if col_tel else 0
        com_contato = max(com_email, com_tel)
        total_com_contato += com_contato

        linha = f"  {nome:<30} {n:>8,} registros"
        if com_email: linha += f" | e-mail: {com_email:,}"
        if com_tel:   linha += f" | tel: {com_tel:,}"
        linhas.append(linha)
        print(linha)

    print(f"\n  {'TOTAL':<30} {total_registros:>8,} registros")
    print(f"  {'Com algum contato':<30} {total_com_contato:>8,}")
    print(f"\n  Arquivos salvos em: {os.path.abspath(OUTPUT_DIR)}/")
    print("="*60)

    # Salvar resumo em txt
    resumo_path = os.path.join(OUTPUT_DIR, "resumo.txt")
    with open(resumo_path, "w", encoding="utf-8") as f:
        f.write("WiNS Hub Saúde — Resumo de coleta\n")
        f.write("="*60 + "\n")
        for l in linhas:
            f.write(l + "\n")
        f.write(f"\nTotal: {total_registros:,} registros\n")
        f.write(f"Com contato: {total_com_contato:,}\n")


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
if __name__ == "__main__":
    print("="*60)
    print("WiNS Hub Saúde — Coleta de dados regulatórios abertos")
    print("Fontes: CNES/DEMAS (MS) · ANS · Mais Médicos")
    print("="*60)

    resultados = {
        "estabelecimentos_cnes":  coletar_estabelecimentos(),
        "operadoras_ans":         coletar_operadoras_ans(),
        "hospitais_leitos":       coletar_hospitais(),
        "ubs":                    coletar_ubs(),
        "mais_medicos":           coletar_mais_medicos(),
    }

    gerar_resumo(resultados)

    print("\nPróximos passos:")
    print("  1. Abra os CSVs no Excel ou pandas para inspecionar qualidade")
    print("  2. Cruzar estabelecimentos_cnes.csv com operadoras_ans.csv pelo CNPJ")
    print("  3. Quando validado, construir a VPS e pipeline de enriquecimento")
    print("  4. Adicionar CFM (R$772/ano) para vincular médicos aos estabelecimentos")
