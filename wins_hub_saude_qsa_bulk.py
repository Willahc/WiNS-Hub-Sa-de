"""
WiNS Hub Saude - Enriquecimento QSA em LOTE via Dados Abertos CNPJ (RFB)
=======================================================================
Em vez de 335k chamadas a BrasilAPI (~3 dias), baixa os arquivos de SOCIOS
dos Dados Abertos do CNPJ (mesma fonte da RFB que a BrasilAPI consome) e faz
o JOIN local no Postgres. Sem rate-limit; minutos/horas em vez de dias.

Fonte (Nextcloud publico da Receita):
    https://arquivos.receitafederal.gov.br/public.php/webdav/<AAAA-MM>/Socios{0..9}.zip
    .../Qualificacoes.zip   (lookup codigo->descricao da qualificacao do socio)

Chave de ligacao: CNPJ_BASICO (8 primeiros digitos do CNPJ de 14). O QSA e
definido no nivel da empresa (raiz), entao todos os estabelecimentos de um
mesmo CNPJ_BASICO recebem o mesmo decisor.

Layout SOCIOS (CSV ; latin-1, sem cabecalho):
    0 CNPJ_BASICO | 1 IDENT_SOCIO | 2 NOME_SOCIO | 3 CNPJ_CPF_SOCIO
    4 QUALIFICACAO_SOCIO (codigo) | 5 DATA_ENTRADA | ...

Uso:
    python wins_hub_saude_qsa_bulk.py            # baixa (se preciso) e processa
    python wins_hub_saude_qsa_bulk.py --skip-download
"""

import os
import io
import csv
import sys
import glob
import zipfile
import argparse
import unicodedata

import requests
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import execute_values

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DADOS_DIR = os.path.join(BASE_DIR, "wins_hub_saude_dados")
RFB_DIR = os.path.join(DADOS_DIR, "rfb_socios")
os.makedirs(RFB_DIR, exist_ok=True)
load_dotenv(os.path.join(BASE_DIR, ".env.saude"))

SHARE_TOKEN = os.environ.get("RFB_SHARE_TOKEN", "")  # defina em .env.saude (NAO versionar)
WEBDAV = "https://arquivos.receitafederal.gov.br/public.php/webdav/{ym}/{fname}"
YM = os.environ.get("RFB_YM", "2026-06")
ARQUIVOS = [f"Socios{i}.zip" for i in range(10)] + ["Qualificacoes.zip"]

csv.field_size_limit(10 * 1024 * 1024)


# ─────────────────────────────────────────
# PRIORIDADE DE CARGO (mesma logica da API)
# ─────────────────────────────────────────
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.upper().strip()


def _rank(desc: str) -> int:
    q = _norm(desc)
    if "PRESIDENTE" in q or "DIRETOR" in q:
        return 0
    if "ADMINISTRADOR" in q and "SOCIO" not in q:
        return 1
    if "ADMINISTRADOR" in q and "SOCIO" in q:
        return 2
    if "SOCIO" in q:
        return 3
    return 4


# ─────────────────────────────────────────
# DOWNLOAD
# ─────────────────────────────────────────
def baixar():
    sess = requests.Session()
    sess.auth = (SHARE_TOKEN, "")
    for fname in ARQUIVOS:
        destino = os.path.join(RFB_DIR, fname)
        if os.path.exists(destino):
            try:
                with zipfile.ZipFile(destino) as z:
                    if z.testzip() is None:
                        print(f"  ok (cache): {fname} ({os.path.getsize(destino)/1e6:.0f} MB)")
                        continue
            except zipfile.BadZipFile:
                pass
        url = WEBDAV.format(ym=YM, fname=fname)
        print(f"  baixando {fname} ...")
        with sess.get(url, stream=True, timeout=600) as r:
            r.raise_for_status()
            n = 0
            with open(destino, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk); n += len(chunk)
        print(f"    {n/1e6:.0f} MB")


# ─────────────────────────────────────────
# LOOKUP QUALIFICACOES
# ─────────────────────────────────────────
def carregar_qualificacoes() -> dict:
    path = os.path.join(RFB_DIR, "Qualificacoes.zip")
    mapa = {}
    with zipfile.ZipFile(path) as z:
        nome = z.namelist()[0]
        with z.open(nome) as f:
            for row in csv.reader(io.TextIOWrapper(f, encoding="latin-1"), delimiter=";", quotechar='"'):
                if len(row) >= 2:
                    mapa[row[0].strip()] = row[1].strip()
    print(f"  {len(mapa)} qualificacoes carregadas.")
    return mapa


# ─────────────────────────────────────────
# ALVOS (raizes de CNPJ a enriquecer)
# ─────────────────────────────────────────
def carregar_alvos(conn) -> set:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT substr(cnpj, 1, 8)
            FROM estabelecimentos
            WHERE cnpj IS NOT NULL AND length(cnpj) = 14 AND decisor_nome IS NULL
        """)
        alvos = {r[0] for r in cur.fetchall()}
    print(f"  {len(alvos):,} raizes de CNPJ alvo (estabelecimentos sem decisor).")
    return alvos


# ─────────────────────────────────────────
# PROCESSAR SOCIOS
# ─────────────────────────────────────────
def processar_socios(alvos: set, qualif: dict) -> dict:
    """Retorna raiz -> (nome, cargo_desc) do socio prioritario."""
    melhores: dict[str, tuple] = {}  # raiz -> (rank, nome, cargo)
    arquivos = sorted(glob.glob(os.path.join(RFB_DIR, "Socios*.zip")))
    lidos = 0
    for zpath in arquivos:
        with zipfile.ZipFile(zpath) as z:
            nome_int = z.namelist()[0]
            print(f"  lendo {os.path.basename(zpath)} ...")
            with z.open(nome_int) as f:
                rd = csv.reader(io.TextIOWrapper(f, encoding="latin-1", newline=""),
                                delimiter=";", quotechar='"')
                for row in rd:
                    lidos += 1
                    if len(row) < 5:
                        continue
                    raiz = row[0].strip().zfill(8)
                    if raiz not in alvos:
                        continue
                    nome = row[2].strip()
                    cargo = qualif.get(row[4].strip(), row[4].strip())
                    rk = _rank(cargo)
                    atual = melhores.get(raiz)
                    if atual is None or rk < atual[0]:
                        melhores[raiz] = (rk, nome.title(), cargo)
    print(f"  {lidos:,} linhas de socios lidas; {len(melhores):,} raizes com decisor encontrado.")
    return {raiz: (n, c) for raiz, (_, n, c) in melhores.items()}


# ─────────────────────────────────────────
# APLICAR NO BANCO
# ─────────────────────────────────────────
def aplicar(conn, decisores: dict):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE _dec_rfb (raiz CHAR(8) PRIMARY KEY, nome TEXT, cargo TEXT)
            ON COMMIT DROP
        """)
        execute_values(cur,
                        "INSERT INTO _dec_rfb (raiz, nome, cargo) VALUES %s ON CONFLICT DO NOTHING",
                        [(r, n, c) for r, (n, c) in decisores.items()], page_size=10000)
        cur.execute("""
            UPDATE estabelecimentos e
               SET decisor_nome = d.nome,
                   decisor_cargo = d.cargo,
                   fonte_enriquecimento = 'QSA_RFB_BULK',
                   enriquecido_em = NOW()
              FROM _dec_rfb d
             WHERE substr(e.cnpj, 1, 8) = d.raiz
               AND e.cnpj IS NOT NULL AND length(e.cnpj) = 14
               AND e.decisor_nome IS NULL
        """)
        enriquecidos = cur.rowcount
        # raizes alvo sem socio = QSA vazio (nao re-tentar)
        cur.execute("""
            UPDATE estabelecimentos
               SET fonte_enriquecimento = 'QSA_VAZIO', enriquecido_em = NOW()
             WHERE cnpj IS NOT NULL AND length(cnpj) = 14
               AND decisor_nome IS NULL
               AND fonte_enriquecimento IS NULL
        """)
        vazios = cur.rowcount
    conn.commit()
    return enriquecidos, vazios


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-download", action="store_true")
    args = ap.parse_args()

    print("=" * 60)
    print(f"WiNS Hub Saude - Enriquecimento QSA em LOTE (RFB {YM})")
    print("=" * 60)

    if not args.skip_download:
        print("\n[1/4] Download dos arquivos de Socios + Qualificacoes...")
        baixar()
    else:
        print("\n[1/4] Download pulado (--skip-download).")

    print("\n[2/4] Carregando lookup e alvos...")
    qualif = carregar_qualificacoes()
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    alvos = carregar_alvos(conn)

    print("\n[3/4] Cruzando socios x alvos...")
    decisores = processar_socios(alvos, qualif)

    print("\n[4/4] Aplicando no banco...")
    enriquecidos, vazios = aplicar(conn, decisores)

    with conn.cursor() as cur:
        cur.execute("SELECT * FROM stats_saude;")
        cols = [d[0] for d in cur.description]
        vals = cur.fetchone()
    conn.close()

    print("\n" + "=" * 60)
    print("RESUMO")
    print(f"  enriquecidos neste lote : {enriquecidos:,}")
    print(f"  marcados QSA_VAZIO      : {vazios:,}")
    print("  --- stats_saude ---")
    for c, v in zip(cols, vals):
        print(f"  {c:<26} {v:,}" if isinstance(v, int) else f"  {c:<26} {v}")
    print("=" * 60)


if __name__ == "__main__":
    sys.exit(main())
