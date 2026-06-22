"""
WiNS Hub Saude - Download e conversao da base completa CNES (DATASUS)
=====================================================================
Resolve a limitacao de 20 registros da API apidadosabertos.saude.gov.br
baixando a base de dados completa do CNES diretamente do DATASUS.

A pagina https://cnes.datasus.gov.br/pages/downloads/arquivosBaseDados.jsp
monta os links via JavaScript (AngularJS). O download real e servido por:
    https://cnes.datasus.gov.br/EstatisticasServlet?path=BASE_DE_DADOS_CNES_<AAAAMM>.ZIP

Este script:
  1. Descobre o arquivo BASE_DE_DADOS_CNES mais recente (sonda meses para tras).
  2. Baixa o ZIP (pula se ja existir e estiver integro).
  3. Extrai tbEstabelecimento<AAAAMM>.csv (layout DATASUS, latin-1, separador ';').
  4. Mapeia as colunas para o schema da tabela `estabelecimentos` e grava
     estabelecimentos_cnes.csv pronto para o wins_hub_saude_importar.py.

Uso:
    python wins_hub_saude_cnes_download.py

Requisitos: requests (so para o download; conversao usa stdlib).
"""

import os
import csv
import sys
import zipfile
import datetime as dt

import requests

# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wins_hub_saude_dados")
os.makedirs(OUTPUT_DIR, exist_ok=True)

SERVLET = "https://cnes.datasus.gov.br/EstatisticasServlet?path={fname}"
ZIP_TPL = "BASE_DE_DADOS_CNES_{ym}.ZIP"
CSV_OUT = os.path.join(OUTPUT_DIR, "estabelecimentos_cnes.csv")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "WiNS-Hub-Saude/1.0 (coleta dados abertos CNES)"})

# Codigo IBGE da UF (2 digitos) -> sigla
UF_COD = {
    11: "RO", 12: "AC", 13: "AM", 14: "RR", 15: "PA", 16: "AP", 17: "TO",
    21: "MA", 22: "PI", 23: "CE", 24: "RN", 25: "PB", 26: "PE", 27: "AL",
    28: "SE", 29: "BA", 31: "MG", 32: "ES", 33: "RJ", 35: "SP", 41: "PR",
    42: "SC", 43: "RS", 50: "MS", 51: "MT", 52: "GO", 53: "DF",
}

# DATASUS tbEstabelecimento -> coluna destino na tabela estabelecimentos
SAIDA_COLS = [
    "cnes_id", "cnpj", "cnpj_entidade", "razao_social", "nome_fantasia",
    "tipo_unidade_cod", "logradouro", "numero", "bairro", "cep",
    "municipio_cod", "uf_cod", "uf", "telefone", "email",
    "latitude", "longitude", "turno", "data_atualizacao_cnes",
]


# ─────────────────────────────────────────
# 1. DESCOBRIR ARQUIVO MAIS RECENTE
# ─────────────────────────────────────────
def descobrir_mais_recente(meses_para_tras: int = 8) -> str:
    """Sonda os ultimos meses e retorna o AAAAMM disponivel mais recente."""
    hoje = dt.date.today()
    for i in range(meses_para_tras):
        ano = hoje.year
        mes = hoje.month - i
        while mes <= 0:
            mes += 12
            ano -= 1
        ym = f"{ano}{mes:02d}"
        url = SERVLET.format(fname=ZIP_TPL.format(ym=ym))
        try:
            r = SESSION.head(url, timeout=30, allow_redirects=True)
            if r.status_code == 200 and "zip" in r.headers.get("Content-Type", "").lower():
                print(f"  Base mais recente encontrada: {ZIP_TPL.format(ym=ym)}")
                return ym
        except requests.RequestException:
            pass
    raise RuntimeError("Nenhuma base BASE_DE_DADOS_CNES encontrada nos ultimos meses.")


# ─────────────────────────────────────────
# 2. DOWNLOAD
# ─────────────────────────────────────────
def baixar(ym: str) -> str:
    fname = ZIP_TPL.format(ym=ym)
    destino = os.path.join(OUTPUT_DIR, fname)
    url = SERVLET.format(fname=fname)

    # Se ja existe e abre como zip valido, reaproveita.
    if os.path.exists(destino):
        try:
            with zipfile.ZipFile(destino) as z:
                if z.testzip() is None:
                    print(f"  ZIP ja presente e integro: {fname} "
                          f"({os.path.getsize(destino)/1e6:.0f} MB) - pulando download.")
                    return destino
        except zipfile.BadZipFile:
            print("  ZIP existente corrompido/incompleto - rebaixando.")

    print(f"  Baixando {fname} ... (servidor usa chunked, sem Content-Length)")
    with SESSION.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        baixado = 0
        with open(destino, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):  # 1 MB
                if not chunk:
                    continue
                f.write(chunk)
                baixado += len(chunk)
                if baixado % (50 << 20) < (1 << 20):  # a cada ~50 MB
                    print(f"    ... {baixado/1e6:.0f} MB")
    print(f"  Concluido: {baixado/1e6:.0f} MB")

    # valida
    with zipfile.ZipFile(destino) as z:
        if z.testzip() is not None:
            raise RuntimeError("ZIP baixado esta corrompido.")
    return destino


# ─────────────────────────────────────────
# 3. CONVERSAO tbEstabelecimento -> CSV mapeado
# ─────────────────────────────────────────
def _to_int(v):
    v = (v or "").strip()
    return int(v) if v.isdigit() else ""


def _to_float(v):
    v = (v or "").strip().replace(",", ".")
    if not v:
        return ""
    try:
        return repr(float(v))
    except ValueError:
        return ""


def _to_date(v):
    v = (v or "").strip()
    if not v:
        return ""
    try:
        return dt.datetime.strptime(v, "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def converter(zip_path: str, ym: str) -> int:
    nome_interno = f"tbEstabelecimento{ym}.csv"
    print(f"  Convertendo {nome_interno} -> {os.path.basename(CSV_OUT)}")

    n = 0
    with zipfile.ZipFile(zip_path) as z, z.open(nome_interno) as raw:
        import io
        txt = io.TextIOWrapper(raw, encoding="latin-1", newline="")
        reader = csv.reader(txt, delimiter=";", quotechar='"')
        header = next(reader)
        idx = {col.strip().strip('"'): i for i, col in enumerate(header)}

        def g(row, col):
            i = idx.get(col)
            return row[i].strip() if i is not None and i < len(row) else ""

        with open(CSV_OUT, "w", newline="", encoding="utf-8") as out:
            w = csv.writer(out)
            w.writerow(SAIDA_COLS)
            for row in reader:
                if not row:
                    continue
                uf_cod = _to_int(g(row, "CO_ESTADO_GESTOR"))
                uf = UF_COD.get(uf_cod, "") if uf_cod != "" else ""
                w.writerow([
                    _to_int(g(row, "CO_CNES")),
                    g(row, "NU_CNPJ"),
                    g(row, "NU_CNPJ_MANTENEDORA"),
                    g(row, "NO_RAZAO_SOCIAL").title(),
                    g(row, "NO_FANTASIA"),
                    _to_int(g(row, "TP_UNIDADE")),
                    g(row, "NO_LOGRADOURO"),
                    g(row, "NU_ENDERECO"),
                    g(row, "NO_BAIRRO"),
                    g(row, "CO_CEP"),
                    _to_int(g(row, "CO_MUNICIPIO_GESTOR")),
                    uf_cod,
                    uf,
                    g(row, "NU_TELEFONE"),
                    g(row, "NO_EMAIL").lower(),
                    _to_float(g(row, "NU_LATITUDE")),
                    _to_float(g(row, "NU_LONGITUDE")),
                    g(row, "CO_TURNO_ATENDIMENTO"),
                    _to_date(g(row, "TO_CHAR(DT_ATUALIZACAO,'DD/MM/YYYY')")),
                ])
                n += 1
                if n % 100000 == 0:
                    print(f"    ... {n:,} linhas")
    print(f"  {n:,} estabelecimentos gravados em {CSV_OUT}")
    return n


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
def main():
    print("=" * 60)
    print("WiNS Hub Saude - Base completa CNES (DATASUS)")
    print("=" * 60)

    ym = os.environ.get("CNES_YM") or descobrir_mais_recente()
    zip_path = baixar(ym)
    n = converter(zip_path, ym)

    print("=" * 60)
    print(f"OK - {n:,} registros prontos. Proximo: python wins_hub_saude_importar.py")
    print("=" * 60)


if __name__ == "__main__":
    sys.exit(main())
