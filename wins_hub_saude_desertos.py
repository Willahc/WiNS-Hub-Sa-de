#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WiNS Hub Saude - Analise de desertos medicos por municipio.

Popula a tabela `desertos_medicos` cruzando dados ja existentes no banco
(estabelecimentos, medicos do Programa Mais Medicos) com a populacao do
Censo 2022 do IBGE (API aberta gratuita).

Fontes (apenas oficiais/abertas):
  - IBGE / SIDRA agregado 1378, variavel 93 (Populacao residente, Censo 2022)
  - Banco local wins_hub_saude (tabelas estabelecimentos e medicos)

NOTA SOBRE A METRICA PRINCIPAL
------------------------------
A metrica de classificacao e ESTABELECIMENTOS por mil habitantes
(estab / populacao * 1000). A coluna `medicos_por_mil_hab` da tabela e
reaproveitada para gravar essa densidade de ESTABELECIMENTOS, porque a
tabela `medicos` (Programa Mais Medicos) guarda o municipio apenas por
NOME (sem codigo IBGE), o que torna uma densidade medica por codigo
pouco confiavel. A contagem de medicos do Mais Medicos por municipio e
feita como complemento best-effort (casamento por nome normalizado + UF)
e e apenas aproximada e parcial (cobre so o Mais Medicos).

Idempotente: usa ON CONFLICT (municipio_cod) DO UPDATE.
"""

import os
import sys
import unicodedata
from collections import defaultdict

import requests
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

# ----------------------------------------------------------------------------
# Heuristica de classificacao (limiares AJUSTAVEIS).
# Baseada em ESTABELECIMENTOS de saude por mil habitantes.
# Sao limiares de triagem para priorizacao comercial/operacional, nao
# parametros regulatorios oficiais.
# ----------------------------------------------------------------------------
LIMIAR_DESERTO = 0.30          # < 0.30 estab/mil hab  => DESERTO
LIMIAR_BAIXA_COBERTURA = 0.70  # < 0.70 estab/mil hab  => BAIXA_COBERTURA
#                                >= 0.70                => NORMAL

# Agregado 4709 = "Populacao residente" do Censo 2022 (variavel 93).
# (O agregado 1378 corresponde ao Censo 2010; 4709 e o equivalente de 2022.)
IBGE_URL = (
    "https://servicodados.ibge.gov.br/api/v3/agregados/4709/"
    "periodos/2022/variaveis/93?localidades=N6[all]"
)

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.saude")


def strip_accents(txt):
    if txt is None:
        return ""
    nfkd = unicodedata.normalize("NFKD", txt)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def norm_nome(txt):
    """Normaliza nome de municipio: upper, sem acento, espacos colapsados."""
    return " ".join(strip_accents(txt).upper().split())


def split_nome_uf(nome_ibge):
    """A API traz localidade.nome como 'Municipio - UF'. Separa em (nome, uf)."""
    if nome_ibge and " - " in nome_ibge:
        nome, uf = nome_ibge.rsplit(" - ", 1)
        uf = uf.strip()
        if len(uf) == 2 and uf.isalpha():
            return nome.strip(), uf.upper()
    return (nome_ibge or "").strip(), None


def fetch_populacao_ibge():
    """Retorna dict {codigo7(str) -> (populacao(int|None), nome(str), uf(str|None))}."""
    print(f"[IBGE] GET {IBGE_URL}")
    resp = requests.get(IBGE_URL, timeout=180)
    resp.raise_for_status()
    data = resp.json()

    pop = {}
    # Estrutura: data[].resultados[].series[]
    for variavel in data:
        for resultado in variavel.get("resultados", []):
            for serie in resultado.get("series", []):
                loc = serie.get("localidade", {})
                cod7 = loc.get("id")
                nome, uf_ibge = split_nome_uf(loc.get("nome", ""))
                valores = serie.get("serie", {})
                raw = valores.get("2022")
                if raw in (None, "-", "...", "..", "X"):
                    populacao = None
                else:
                    try:
                        populacao = int(float(str(raw).replace(",", ".")))
                    except (TypeError, ValueError):
                        populacao = None
                if cod7:
                    pop[str(cod7)] = (populacao, nome, uf_ibge)
    validas = sum(1 for p, _, _ in pop.values() if p is not None)
    print(f"[IBGE] {len(pop)} municipios recebidos "
          f"({validas} com populacao valida)")
    return pop


def main():
    load_dotenv(ENV_PATH)
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERRO: DATABASE_URL nao encontrada no .env.saude", file=sys.stderr)
        sys.exit(1)

    # 1) Populacao IBGE: codigo7 -> (pop, nome)
    pop_por_cod7 = fetch_populacao_ibge()

    # codigo6 -> (pop, nome, uf). Casamento por truncamento do codigo IBGE 7->6.
    pop_por_cod6 = {}
    for cod7, (populacao, nome, uf_ibge) in pop_por_cod7.items():
        if len(cod7) == 7:
            cod6 = cod7[:6]
            pop_por_cod6[cod6] = (populacao, nome, uf_ibge)

    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    cur = conn.cursor()

    # 3) Agregacao de estabelecimentos por municipio_cod (codigo de 6 digitos)
    cur.execute("""
        SELECT municipio_cod,
               COUNT(*)                         AS total_estab,
               COALESCE(SUM(tem_internacao), 0) AS hospitais
          FROM estabelecimentos
         WHERE municipio_cod IS NOT NULL
         GROUP BY municipio_cod
    """)
    estab_por_cod6 = {}
    for municipio_cod, total_estab, hospitais in cur.fetchall():
        estab_por_cod6[str(municipio_cod)] = (int(total_estab), int(hospitais))
    print(f"[DB] {len(estab_por_cod6)} municipios com estabelecimentos")

    # UF mais comum por municipio_cod (derivada dos estabelecimentos)
    cur.execute("""
        SELECT municipio_cod, uf, COUNT(*) AS n
          FROM estabelecimentos
         WHERE municipio_cod IS NOT NULL AND uf IS NOT NULL
         GROUP BY municipio_cod, uf
    """)
    uf_rank = defaultdict(list)
    for municipio_cod, uf, n in cur.fetchall():
        uf_rank[str(municipio_cod)].append((int(n), uf))
    uf_por_cod6 = {}
    for cod6, lst in uf_rank.items():
        lst.sort(reverse=True)  # maior contagem primeiro
        uf_por_cod6[cod6] = (lst[0][1] or "").strip()

    # 4) Complemento best-effort: medicos do Mais Medicos por (nome_norm, uf)
    #    APROXIMADO e PARCIAL (so Mais Medicos). Nao usado na classificacao.
    cur.execute("""
        SELECT municipio_atuacao, uf_atuacao, COUNT(*) AS n
          FROM medicos
         WHERE municipio_atuacao IS NOT NULL
         GROUP BY municipio_atuacao, uf_atuacao
    """)
    medicos_por_nome_uf = {}
    for municipio_atuacao, uf_atuacao, n in cur.fetchall():
        chave = (norm_nome(municipio_atuacao), (uf_atuacao or "").strip())
        medicos_por_nome_uf[chave] = medicos_por_nome_uf.get(chave, 0) + int(n)
    print(f"[DB] {len(medicos_por_nome_uf)} pares (municipio,uf) com medicos Mais Medicos "
          f"(complemento aproximado)")

    # ------------------------------------------------------------------
    # Monta as linhas. Universo = municipios do IBGE (com codigo de 6 dig).
    # ------------------------------------------------------------------
    linhas = []
    medicos_casados = 0
    medicos_total_casado = 0
    for cod6, (populacao, nome_ibge, uf_ibge) in pop_por_cod6.items():
        total_estab, hospitais = estab_por_cod6.get(cod6, (0, 0))
        # UF: preferencia a UF mais comum nos estabelecimentos; se o municipio
        # nao tiver estabelecimentos, usa a UF do proprio IBGE.
        uf = uf_por_cod6.get(cod6) or (uf_ibge or "")

        if populacao and populacao > 0:
            estab_por_mil = total_estab / populacao * 1000.0
        else:
            estab_por_mil = None

        # Complemento medicos Mais Medicos (aproximado)
        n_medicos = medicos_por_nome_uf.get((norm_nome(nome_ibge), uf))
        if n_medicos is not None:
            medicos_casados += 1
            medicos_total_casado += n_medicos

        # 5) Classificacao por densidade de estabelecimentos
        if estab_por_mil is None:
            classificacao = "NORMAL"  # sem populacao valida: nao classifica como deserto
        elif estab_por_mil < LIMIAR_DESERTO:
            classificacao = "DESERTO"
        elif estab_por_mil < LIMIAR_BAIXA_COBERTURA:
            classificacao = "BAIXA_COBERTURA"
        else:
            classificacao = "NORMAL"

        densidade_grava = round(estab_por_mil, 2) if estab_por_mil is not None else None

        linhas.append((
            int(cod6),                 # municipio_cod (6 digitos)
            nome_ibge,                 # municipio_nome (nome do IBGE)
            uf[:2] if uf else None,    # uf
            populacao,                 # populacao
            densidade_grava,           # medicos_por_mil_hab = densidade de ESTABELECIMENTOS
            total_estab,               # estabelecimentos_sus (proxy: total de estab)
            classificacao,
        ))

    print(f"[CALC] {len(linhas)} municipios a inserir/atualizar")
    print(f"[CALC] complemento medicos Mais Medicos: {medicos_casados} municipios casados "
          f"por nome+UF, {medicos_total_casado} medicos atribuidos (APROXIMADO/PARCIAL)")

    # 7) INSERT ... ON CONFLICT DO UPDATE (idempotente)
    execute_values(cur, """
        INSERT INTO desertos_medicos
            (municipio_cod, municipio_nome, uf, populacao,
             medicos_por_mil_hab, estabelecimentos_sus, classificacao)
        VALUES %s
        ON CONFLICT (municipio_cod) DO UPDATE SET
            municipio_nome       = EXCLUDED.municipio_nome,
            uf                   = EXCLUDED.uf,
            populacao            = EXCLUDED.populacao,
            medicos_por_mil_hab  = EXCLUDED.medicos_por_mil_hab,
            estabelecimentos_sus = EXCLUDED.estabelecimentos_sus,
            classificacao        = EXCLUDED.classificacao,
            captado_em           = now()
    """, linhas, page_size=1000)
    conn.commit()
    print(f"[DB] {len(linhas)} linhas gravadas (commit ok)")

    # ------------------------------------------------------------------
    # 8) Relatorio final
    # ------------------------------------------------------------------
    print("\n" + "=" * 78)
    print("NOTA: 'medicos_por_mil_hab' armazena a DENSIDADE DE ESTABELECIMENTOS")
    print("      por mil habitantes (estab/pop*1000). A densidade de medicos nao")
    print("      e confiavel pois a tabela medicos (Mais Medicos) so tem municipio")
    print("      por nome, sem codigo IBGE, e cobre apenas o Programa Mais Medicos.")
    print(f"Limiares (ajustaveis): DESERTO < {LIMIAR_DESERTO} | "
          f"BAIXA_COBERTURA < {LIMIAR_BAIXA_COBERTURA} estab/mil hab")
    print("=" * 78)

    cur.execute("""
        SELECT classificacao,
               COUNT(*)                   AS municipios,
               COALESCE(SUM(populacao),0) AS populacao
          FROM desertos_medicos
         GROUP BY classificacao
         ORDER BY CASE classificacao
                    WHEN 'DESERTO' THEN 1
                    WHEN 'BAIXA_COBERTURA' THEN 2
                    WHEN 'NORMAL' THEN 3 ELSE 4 END
    """)
    print("\nDISTRIBUICAO POR CLASSIFICACAO")
    print(f"{'Classificacao':<18}{'Municipios':>12}{'Populacao':>16}")
    print("-" * 46)
    total_m = total_p = 0
    for classificacao, municipios, populacao in cur.fetchall():
        total_m += municipios
        total_p += populacao
        print(f"{classificacao:<18}{municipios:>12,}{populacao:>16,}")
    print("-" * 46)
    print(f"{'TOTAL':<18}{total_m:>12,}{total_p:>16,}")

    cur.execute("""
        SELECT municipio_nome, uf, populacao, estabelecimentos_sus,
               medicos_por_mil_hab, classificacao
          FROM desertos_medicos
         WHERE classificacao IN ('DESERTO', 'BAIXA_COBERTURA')
         ORDER BY populacao DESC NULLS LAST
         LIMIT 15
    """)
    print("\n15 MAIORES MUNICIPIOS DESERTO / BAIXA_COBERTURA (por populacao)")
    print(f"{'Municipio':<28}{'UF':>3}{'Populacao':>12}{'Estab':>7}"
          f"{'Dens.':>8}  Classif.")
    print("-" * 78)
    for nome, uf, populacao, estab, dens, classif in cur.fetchall():
        nome_s = (nome or "")[:27]
        pop_s = f"{populacao:,}" if populacao is not None else "n/d"
        dens_s = f"{dens:.2f}" if dens is not None else "n/d"
        print(f"{nome_s:<28}{(uf or ''):>3}{pop_s:>12}{estab:>7}"
              f"{dens_s:>8}  {classif}")

    cur.close()
    conn.close()
    print("\n[OK] Concluido.")


if __name__ == "__main__":
    main()
