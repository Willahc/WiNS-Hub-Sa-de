"""
WiNS Hub Saude - Ingestao SIM: MORTALIDADE EVITAVEL (5 a 74 anos)
=================================================================
Baixa via FTP (porta 21) os arquivos anuais DO<UF><AAAA>.dbc do SIM/CID10/DORES,
decodifica com o decoder puro (wins_hub_saude_dbc.py) em PARALELO, e classifica
cada obito como EVITAVEL ou nao segundo a CAUSABAS (CID-10), restrito a faixa
etaria 5-74 anos. Agrega por CODMUNRES (6 digitos = municipio_cod). So agregado.
Grava na tabela NOVA mortalidade_evitavel. Usa ano mais recente (auto-deteccao).

CLASSIFICACAO DE EVITABILIDADE
------------------------------
Baseada na "Lista Brasileira de Causas Evitaveis de 5 a 74 anos" (Malta DC et al.,
Epidemiol Serv Saude 2011; atualizacao da SVS/MS), transcrita da Nota Tecnica
oficial do DATASUS (Obitos por causas evitaveis 5 a 74 anos - Lista de Tabulacao).

Um obito de 5-74 anos e' considerado EVITAVEL se a CAUSABAS (CID-10) cai em
qualquer dos agrupamentos 1.1 a 1.5 abaixo. Causas mal definidas (R00-R94, R96-R99)
e "demais causas nao claramente evitaveis" NAO contam como evitaveis (grupos 2 e 3
da lista oficial). Faixa etaria: 5-74 anos completos; <5 e >=75 ficam fora do
numerador de evitaveis (sao contados a parte, mas nao entram em obitos_5a74 nem
em obitos_evitaveis).

Os ranges abaixo replicam a lista oficial. A comparacao e' feita por categoria
de 3 caracteres (letra+2 digitos), com excecoes de subcategoria explicitadas
(N73.6 excluida; J02.8/J02.9/J03.8/J03.9/J02.0/J03.0; B57.0-B57.2; etc.).

Uso: python wins_hub_saude_sim_evitavel.py
"""
import os, sys, time
from ftplib import FTP, error_perm
from concurrent.futures import ProcessPoolExecutor, as_completed

import dbfread
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import execute_values
import wins_hub_saude_dbc as dbc

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env.saude"))
TMP = os.path.join(BASE_DIR, "sih_tmp")
os.makedirs(TMP, exist_ok=True)

FTP_HOST = "ftp.datasus.gov.br"
FTP_DIR = "/dissemin/publicos/SIM/CID10/DORES"
UFS = ["AC","AL","AP","AM","BA","CE","DF","ES","GO","MA","MT","MS","MG","PA","PB","PR",
       "PE","PI","RJ","RN","RS","RO","RR","SC","SP","SE","TO"]
WORKERS = 4  # baixo para nao saturar o FTP (ha outros downloads em paralelo)


# ---------------------------------------------------------------------------
# Classificacao de evitabilidade (CID-10)
# ---------------------------------------------------------------------------
# Ranges de CATEGORIA de 3 caracteres (inclusive) considerados EVITAVEIS.
# Cada par (inicio, fim) cobre categorias contiguas, ex ("A00","A09").
_EVIT_RANGES = [
    # 1.1 Reduzivel por imunoprevencao
    ("A17", "A17"), ("A19", "A19"), ("A34", "A37"), ("A80", "A80"),
    ("B05", "B06"), ("B16", "B16"),
    # 1.2 Doencas infecciosas (promocao/prevencao/controle)
    ("A15", "A16"), ("A18", "A18"), ("B90", "B90"),
    ("A00", "A09"), ("B20", "B24"), ("B15", "B15"), ("B17", "B19"),
    ("A50", "A59"), ("A63", "A64"),
    ("N70", "N76"),  # exceto N73.6 (tratado abaixo)
    ("I00", "I09"),
    ("J00", "J01"), ("J04", "J22"),  # infeccoes respiratorias (J02/J03 parciais abaixo)
    ("L02", "L08"),
    # Outras de notificacao compulsoria / outras infeccoes
    ("A20", "A22"), ("A27", "A27"), ("A30", "A30"), ("A77", "A77"),
    ("A82", "A82"), ("A90", "A91"), ("A95", "A95"), ("B03", "B03"),
    ("A23", "A26"), ("A28", "A28"), ("A31", "A32"), ("A38", "A41"),
    ("A46", "A46"), ("B50", "B55"), ("G00", "G01"),
    # 1.3 Doencas nao transmissiveis (DCNT)
    ("C00", "C00"), ("C43", "C44"), ("C22", "C22"), ("C16", "C16"),
    ("C18", "C21"), ("C01", "C06"), ("C09", "C10"), ("C12", "C15"),
    ("C32", "C34"), ("C50", "C50"), ("C53", "C53"), ("C62", "C62"),
    ("C73", "C73"), ("C81", "C81"), ("C91", "C92"),
    ("E01", "E05"), ("E10", "E14"), ("E66", "E66"),
    ("F10", "F10"), ("K70", "K70"),
    ("G40", "G41"),
    ("I10", "I13"), ("I20", "I25"), ("I50", "I50"), ("I60", "I70"),
    ("J40", "J47"), ("J81", "J81"), ("J60", "J70"),
    ("K25", "K28"), ("K35", "K35"), ("K40", "K46"), ("K56", "K56"),
    ("K80", "K83"),
    ("N18", "N18"),
    # 1.4 Mortes maternas
    ("O00", "O26"), ("O29", "O99"),
    # 1.5 Causas externas (acoes intersetoriais)
    ("V01", "V99"), ("W00", "W99"),
    ("X00", "X09"), ("X10", "X39"), ("X40", "X49"), ("X58", "X59"),
    ("X60", "X84"),       # lesoes autoprovocadas (suicidios)
    ("X85", "Y09"),       # agressoes (homicidios)
    ("Y10", "Y36"), ("Y40", "Y84"),
]

# Subcategorias (4 chars) extras incluidas (alem das categorias de 3 chars acima).
_EVIT_SUBCODES = {
    "G000",            # Meningite por Haemophilus (1.1) - ja coberto por G00 acima
    "B570", "B571", "B572", "B65",  # outras doencas notificacao (B57.0-B57.2, B65)
    "A693", "A692",    # cobertura defensiva p/ A69.2 (outras infeccoes)
    "I426",            # psicose alcoolica - cardiomiopatia alcoolica
    "K292", "K860",    # gastrite alcoolica / pancreatite alcoolica
    "N390",            # infeccao trato urinario nao especificada
    "A923",            # febre do Nilo (notificacao compulsoria)
    "A985",            # febre hemorragica (notificacao compulsoria)
    "J028", "J029", "J038", "J039", "J020", "J030",  # faringite/amigdalite agudas
}

# Subcategorias EXCLUIDAS mesmo a categoria de 3 chars estando incluida.
_EVIT_EXCLUDE_SUB = {
    "N736",            # 1.2 exclui N73.6 (peritonite pelvica feminina - aderencias)
}


def _is_evitavel(causabas):
    """True se a CAUSABAS (CID-10, ex 'I219','C169','X910') e' causa EVITAVEL
    pela Lista Brasileira 5-74 anos. Compara categoria de 3 chars + excecoes."""
    if not causabas:
        return False
    c = causabas.strip().upper()
    if len(c) < 3:
        return False
    cat = c[:3]              # letra + 2 digitos
    sub4 = c[:4]            # letra + 3 digitos (subcategoria)
    if sub4 in _EVIT_EXCLUDE_SUB:
        return False
    for lo, hi in _EVIT_RANGES:
        if lo <= cat <= hi:
            return True
    if sub4 in _EVIT_SUBCODES or cat in _EVIT_SUBCODES:
        return True
    return False


def _idade_anos(idade):
    """Converte o campo IDADE do SIM (3 chars) em anos inteiros, ou None.
    1o digito = unidade: 4=anos, 5=100+anos; <4 = menos de 1 ano (min/horas/dias/meses).
    Ex '434' = 34 anos; '501' = 101 anos; '023' = 23 minutos (=> 0 anos)."""
    if not idade or len(idade) < 3 or not idade.isdigit():
        return None
    unidade = idade[0]
    valor = int(idade[1:])
    if unidade == "5":      # 5XX = 100 + XX anos
        return 100 + valor
    if unidade == "4":      # 4XX = XX anos
        return valor
    return 0                 # unidades 1/2/3 (min/horas/dias-meses) => < 1 ano


def _ftp_conn():
    f = FTP(FTP_HOST, timeout=120)
    f.login()
    f.cwd(FTP_DIR)
    f.voidcmd("TYPE I")
    return f


def baixar_decode(tarefa):
    """Worker: baixa e decodifica UM DO<uf><ano>.dbc.
    Retorna (uf, ano, n_5a74, agg, status) onde agg[cod] = [obitos_5a74, evitaveis]."""
    uf, ano = tarefa
    name = f"DO{uf}{ano}.dbc"
    dest = os.path.join(TMP, name)
    for tent in range(3):
        try:
            f = _ftp_conn()
            with open(dest, "wb") as fh:
                f.retrbinary("RETR " + name, fh.write)
            try:
                f.quit()
            except Exception:
                pass
            break
        except error_perm:
            return (uf, ano, 0, {}, "inexistente")
        except Exception as e:
            if tent == 2:
                return (uf, ano, 0, {}, f"erro download: {e}")
            time.sleep(3)
    dbf = dest[:-4] + ".dbf"
    agg = {}
    try:
        dbc.dbc_to_dbf(dest, dbf)
        for rec in dbfread.DBF(dbf, encoding="latin-1", load=False):
            mr = (rec.get("CODMUNRES") or "").strip()
            if not mr.isdigit():
                continue
            anos = _idade_anos((rec.get("IDADE") or "").strip())
            if anos is None or anos < 5 or anos > 74:
                continue  # fora da faixa 5-74
            cod = int(mr)
            ev = 1 if _is_evitavel(rec.get("CAUSABAS")) else 0
            a = agg.get(cod)
            if a is None:
                agg[cod] = [1, ev]
            else:
                a[0] += 1; a[1] += ev
        n = sum(a[0] for a in agg.values())
        return (uf, ano, n, agg, "ok")
    except Exception as e:
        return (uf, ano, 0, {}, f"erro decode: {e}")
    finally:
        for p in (dest, dbf):
            try:
                os.remove(p)
            except OSError:
                pass


def ano_recente():
    """Ano mais recente com cobertura total (27/27 UF)."""
    f = _ftp_conn()
    for ano in range(2024, 2014, -1):
        ok = True
        for uf in UFS:
            try:
                f.size(f"DO{uf}{ano}.dbc")
            except error_perm:
                ok = False; break
            except Exception:
                f = _ftp_conn()
                try:
                    f.size(f"DO{uf}{ano}.dbc")
                except Exception:
                    ok = False; break
        if ok:
            try:
                f.quit()
            except Exception:
                pass
            return ano
    try:
        f.quit()
    except Exception:
        pass
    return None


def main():
    t0 = time.time()
    ano = ano_recente()
    if not ano:
        sys.exit("Nenhum ano completo do SIM encontrado.")
    print(f"Ano: {ano} | workers={WORKERS}", flush=True)

    tarefas = [(uf, ano) for uf in UFS]
    agg = {}
    total_5a74 = 0
    total_evit = 0
    done = 0
    with ProcessPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(baixar_decode, t): t for t in tarefas}
        for fut in as_completed(futs):
            uf, an, n, a, st = fut.result()
            done += 1
            ev_uf = 0
            for cod, val in a.items():
                x = agg.get(cod)
                if x is None:
                    agg[cod] = [val[0], val[1]]
                else:
                    x[0] += val[0]; x[1] += val[1]
                ev_uf += val[1]
            total_5a74 += n
            total_evit += ev_uf
            print(f"  [{done}/{len(tarefas)}] {uf}{an}: {n:,} obitos 5-74 "
                  f"({ev_uf:,} evit) [{st}] (acum {total_evit:,}/{total_5a74:,}) "
                  f"{time.time()-t0:.0f}s", flush=True)

    print(f"\nDecode concluido: {total_evit:,} evitaveis de {total_5a74:,} obitos 5-74 anos, "
          f"{len(agg):,} municipios em {time.time()-t0:.0f}s. Gravando...", flush=True)

    # cria tabela via SUPERUSER e da GRANT
    super_url = os.environ.get("SUPERUSER_URL", os.environ["DATABASE_URL"])
    sc = psycopg2.connect(super_url)
    with sc.cursor() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS mortalidade_evitavel (
                municipio_cod   INTEGER PRIMARY KEY,
                municipio_nome  TEXT,
                uf              CHAR(2),
                populacao       INTEGER,
                obitos_5a74     INTEGER DEFAULT 0,
                obitos_evitaveis INTEGER DEFAULT 0,
                pct_evitaveis   NUMERIC(6,2),
                evitaveis_por_mil NUMERIC(8,3),
                ano             INTEGER,
                captado_em      TIMESTAMP DEFAULT NOW());
            GRANT ALL ON mortalidade_evitavel TO wins_saude;
        """)
    sc.commit(); sc.close()

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    with conn.cursor() as cur:
        cur.execute("SELECT municipio_cod, municipio_nome, uf, populacao FROM desertos_medicos")
        info = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}

    linhas = []
    for cod, (nome, uf, pop) in info.items():
        tot, ev = agg.get(cod, (0, 0))
        pct = round(ev / tot * 100, 2) if tot else 0
        epm = round(ev / pop * 1000, 3) if pop else 0
        linhas.append((cod, nome, uf, pop, tot, ev, pct, epm, ano))

    with conn.cursor() as cur:
        cur.execute("TRUNCATE mortalidade_evitavel")
        execute_values(cur, """
            INSERT INTO mortalidade_evitavel (municipio_cod,municipio_nome,uf,populacao,
              obitos_5a74,obitos_evitaveis,pct_evitaveis,evitaveis_por_mil,ano) VALUES %s
        """, linhas, page_size=10000)
    conn.commit()

    with conn.cursor() as cur:
        cur.execute("SELECT sum(obitos_5a74), sum(obitos_evitaveis) FROM mortalidade_evitavel")
        s5, sev = cur.fetchone()
        cur.execute("""SELECT municipio_nome, uf, populacao, obitos_evitaveis, evitaveis_por_mil
                       FROM mortalidade_evitavel WHERE populacao > 20000
                       ORDER BY evitaveis_por_mil DESC LIMIT 10""")
        top = cur.fetchall()
    conn.close()

    pct_nac = round(sev / s5 * 100, 2) if s5 else 0
    print("\n" + "=" * 70)
    print(f"mortalidade_evitavel gravada (ano {ano}).")
    print(f"Obitos 5-74 anos: {s5:,} | EVITAVEIS: {sev:,} | % nacional: {pct_nac}%")
    print("Top 10 evitaveis_por_mil (pop>20k):")
    for nome, uf, pop, ev, epm in top:
        print(f"  {nome}-{uf:<3} pop {pop:>8,}  {ev:>5,} evit  {epm:>6}/mil")
    print("=" * 70)


if __name__ == "__main__":
    sys.exit(main())
