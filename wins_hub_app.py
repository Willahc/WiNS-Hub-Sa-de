"""
WiNS Hub Saude - App web (Flask)
================================
Site dinamico local servindo o PostgreSQL ao vivo. Tres paginas:
  /              -> Dashboard (mapas/KPIs/indice) -- reaproveita o HTML gerado
  /oportunidade  -> Indice de Oportunidade interativo (filtros + export CSV, via API)
  /vender        -> "Para quem vender" (segmentos de comprador, com validacao)

Uso:
    python wins_hub_app.py        # http://localhost:5000

Requisitos: flask, psycopg2-binary, python-dotenv
"""

import os
import io
import csv

from flask import Flask, jsonify, request, Response, render_template_string
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env.saude"))
DSN = os.environ["DATABASE_URL"]
DASH_FILE = os.path.join(BASE_DIR, "wins_hub_saude_dashboard_publico.html")

app = Flask(__name__)


def db():
    return psycopg2.connect(DSN, cursor_factory=RealDictCursor)


NAV = """
<nav style="position:sticky;top:0;z-index:9999;background:#0a0f1a;border-bottom:1px solid #22304d;
            padding:11px 22px;display:flex;gap:6px;align-items:center;font:600 14px sans-serif">
  <span style="color:#37d7a6;margin-right:18px">WiNS Hub Saude</span>
  <a href="/" style="color:#cfe;text-decoration:none;padding:6px 12px;border-radius:8px">Dashboard</a>
  <a href="/oportunidade" style="color:#cfe;text-decoration:none;padding:6px 12px;border-radius:8px">Indice de Oportunidade</a>
  <a href="/vender" style="color:#cfe;text-decoration:none;padding:6px 12px;border-radius:8px">Para quem vender</a>
</nav>
"""

PAGE = """<!doctype html><html lang=pt-BR><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1"><title>{{title}} - WiNS Hub Saude</title>
<style>
:root{--bg:#0b1220;--card:#131c30;--card2:#1a2740;--txt:#e6edf7;--mut:#8aa0c0;--acc:#37d7a6;--acc2:#4f9cf9;--bd:#22304d}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1200px;margin:0 auto;padding:24px 20px 60px}
h1{font-size:24px;margin:6px 0 4px}h2{color:var(--acc);font-size:16px;border-left:3px solid var(--acc);padding-left:10px;margin:28px 0 12px}
.sub{color:var(--mut);margin:0 0 16px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:16px;margin-bottom:14px}
select,input{background:var(--card2);border:1px solid var(--bd);color:var(--txt);border-radius:8px;padding:8px 10px;font-size:14px}
button{background:var(--acc2);border:0;color:#06101f;border-radius:8px;padding:9px 16px;font:600 14px sans-serif;cursor:pointer}
button.alt{background:var(--card2);color:var(--txt);border:1px solid var(--bd)}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:8px;border-bottom:1px solid var(--bd)}
th{color:var(--mut);font-size:12px;text-transform:uppercase;cursor:pointer;user-select:none}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.filters{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:14px}
.tier-ALTA{color:#37d7a6;font-weight:700}.tier-MEDIA{color:#f6c453}.tier-BAIXA{color:#8aa0c0}
.seg{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
.alvo{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:14px}
.alvo.ok{border-color:var(--acc);background:linear-gradient(180deg,#10231c,#131c30)}
.alvo h4{margin:0 0 4px;font-size:15px}.alvo .ex{color:var(--acc2);font-size:13px}.alvo .uso{color:var(--mut);font-size:13px;margin-top:4px}
.chk{display:flex;align-items:center;gap:7px;margin-top:10px;font-size:13px;color:var(--mut);cursor:pointer}
.pill{display:inline-block;background:var(--card2);border:1px solid var(--bd);border-radius:20px;padding:3px 12px;font-size:12px;color:var(--mut);margin-right:6px}
a{color:var(--acc2)}
</style></head><body>{{nav|safe}}<div class=wrap>{{body|safe}}</div></body></html>"""


# ---------------------------------------------------------------- Dashboard
@app.route("/")
def dashboard():
    try:
        with open(DASH_FILE, encoding="utf-8") as f:
            html = f.read()
        # injeta a nav logo apos <body>
        html = html.replace("<body>", "<body>" + NAV, 1)
        return Response(html, mimetype="text/html")
    except FileNotFoundError:
        return Response("Gere o dashboard antes: python wins_hub_saude_dashboard.py", mimetype="text/plain")


# ---------------------------------------------------------------- API
def query_oportunidade(uf, tier, minpop, q, limit):
    sql = """
        SELECT municipio_nome, uf, populacao, medicos_por_mil, enfermeiros_por_mil,
               tem_tomografo, cobertura_privada_pct, pib_per_capita,
               indice_oportunidade, tier, sweet_spot
        FROM oportunidade_investimento
        WHERE populacao >= %(minpop)s
          AND (%(uf)s = '' OR uf = %(uf)s)
          AND (%(tier)s = '' OR tier = %(tier)s)
          AND (%(q)s = '' OR municipio_nome ILIKE %(like)s)
        ORDER BY indice_oportunidade DESC, populacao DESC
        LIMIT %(limit)s
    """
    params = {"uf": uf, "tier": tier, "minpop": minpop, "q": q,
              "like": f"%{q}%", "limit": limit}
    with db() as c, c.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


@app.route("/api/oportunidade")
def api_oport():
    rows = query_oportunidade(
        request.args.get("uf", ""), request.args.get("tier", ""),
        int(request.args.get("minpop", 0) or 0), request.args.get("q", ""),
        int(request.args.get("limit", 200) or 200))
    return jsonify(rows)


@app.route("/api/oportunidade.csv")
def api_oport_csv():
    rows = query_oportunidade(
        request.args.get("uf", ""), request.args.get("tier", ""),
        int(request.args.get("minpop", 0) or 0), request.args.get("q", ""), 6000)
    buf = io.StringIO()
    w = csv.writer(buf)
    cols = ["municipio_nome", "uf", "populacao", "medicos_por_mil", "enfermeiros_por_mil",
            "tem_tomografo", "cobertura_privada_pct", "pib_per_capita",
            "indice_oportunidade", "tier", "sweet_spot"]
    w.writerow(cols)
    for r in rows:
        w.writerow([r[c] for c in cols])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=oportunidade_wins_hub_saude.csv"})


@app.route("/api/ufs")
def api_ufs():
    with db() as c, c.cursor() as cur:
        cur.execute("SELECT DISTINCT uf FROM oportunidade_investimento WHERE uf IS NOT NULL ORDER BY 1")
        return jsonify([r["uf"] for r in cur.fetchall()])


# ---------------------------------------------------------------- Oportunidade page
OPORT_BODY = """
<h1>Indice de Oportunidade de Investimento em Saude</h1>
<p class=sub>Carencia assistencial x demanda x mercado pagante x infraestrutura. Score 0-100 por municipio, ao vivo do banco.</p>
<div class=card>
  <div class=filters>
    <label>UF <select id=uf><option value="">Todas</option></select></label>
    <label>Tier <select id=tier><option value="">Todos</option><option>ALTA</option><option>MEDIA</option><option>BAIXA</option></select></label>
    <label>Pop. minima <input id=minpop type=number value=0 style="width:110px"></label>
    <label>Buscar <input id=q placeholder="municipio..."></label>
    <button onclick=load()>Filtrar</button>
    <button class=alt onclick=exportCsv()>Exportar CSV</button>
    <span class=pill id=count></span>
  </div>
  <div id=tbl></div>
</div>
<script>
const fmt=n=>n==null?'-':Number(n).toLocaleString('pt-BR');
let cur=[],sortKey='indice_oportunidade',sortDir=-1;
async function ufs(){const u=await (await fetch('/api/ufs')).json();document.getElementById('uf').innerHTML='<option value="">Todas</option>'+u.map(x=>`<option>${x}</option>`).join('')}
function qs(){return `uf=${document.getElementById('uf').value}&tier=${document.getElementById('tier').value}&minpop=${document.getElementById('minpop').value||0}&q=${encodeURIComponent(document.getElementById('q').value)}`}
async function load(){cur=await (await fetch('/api/oportunidade?limit=500&'+qs())).json();render()}
function exportCsv(){window.location='/api/oportunidade.csv?'+qs()}
function sortBy(k){if(sortKey===k)sortDir=-sortDir;else{sortKey=k;sortDir=-1}render()}
function render(){
  document.getElementById('count').textContent=cur.length+' municipios';
  const c=[...cur].sort((a,b)=>{let x=a[sortKey],y=b[sortKey];if(typeof x==='string')return sortDir*x.localeCompare(y);return sortDir*((x||0)-(y||0))});
  const H=[['municipio_nome','Municipio'],['uf','UF'],['populacao','Pop'],['indice_oportunidade','Indice'],['tier','Tier'],['medicos_por_mil','Med/mil'],['enfermeiros_por_mil','Enf/mil'],['cobertura_privada_pct','Cob.%'],['pib_per_capita','PIB pc']];
  let h='<table><tr>'+H.map(x=>`<th class="${['populacao','indice_oportunidade','medicos_por_mil','enfermeiros_por_mil','cobertura_privada_pct','pib_per_capita'].includes(x[0])?'n':''}" onclick="sortBy('${x[0]}')">${x[1]}</th>`).join('')+'</tr>';
  h+=c.map(r=>`<tr><td>${r.municipio_nome}</td><td>${r.uf}</td><td class=n>${fmt(r.populacao)}</td><td class=n><b>${r.indice_oportunidade}</b></td><td class="tier-${r.tier}">${r.tier}${r.sweet_spot?' &#9733;':''}</td><td class=n>${r.medicos_por_mil}</td><td class=n>${r.enfermeiros_por_mil}</td><td class=n>${r.cobertura_privada_pct}</td><td class=n>${fmt(Math.round(r.pib_per_capita))}</td></tr>`).join('');
  document.getElementById('tbl').innerHTML=h+'</table>';
}
ufs();load();
</script>
"""


@app.route("/oportunidade")
def oportunidade():
    return render_template_string(PAGE, title="Indice de Oportunidade", nav=NAV, body=OPORT_BODY)


# ---------------------------------------------------------------- Para quem vender
VENDER = [
    {"grupo": "A &mdash; Inteligencia territorial / Indice de Oportunidade",
     "desc": "Agregado, sem risco LGPD. Vende como relatorio, dashboard ou assinatura de API. Carro-chefe.",
     "alvos": [
         {"nome": "Operadoras e seguradoras", "ex": "Hapvida, SulAmerica, Bradesco/Porto Saude", "uso": "planejamento de rede; onde ha mercado descoberto"},
         {"nome": "Redes de clinica/hospital/diagnostico", "ex": "Dr. Consulta, Dasa, Fleury, Rede D'Or, Oncoclinicas, Kora", "uso": "site selection: onde abrir a proxima unidade"},
         {"nome": "Redes de farmacia", "ex": "RaiaDrogasil, Pague Menos, DPSP", "uso": "expansao e clinica em farmacia"},
         {"nome": "VC/PE de saude e healthtechs", "ex": "DNA Capital, Crescera, Kaszek, Monashees", "uso": "tese de investimento; onde a demanda justifica"},
         {"nome": "Industria farma e distribuidoras", "ex": "EMS, Hypera, Profarma", "uso": "planejamento de territorio de vendas"},
         {"nome": "Consultorias e fundos imobiliarios de saude", "ex": "BCG, McKinsey, EY; FIIs de hospitais", "uso": "estudos de mercado; ativos de saude"},
         {"nome": "Setor publico / multilaterais", "ex": "Secretarias, BNDES, OPAS, ONGs", "uso": "politica publica (paga menos, valida muito)"},
     ]},
    {"grupo": "B &mdash; Contatos de decisor (B2B)",
     "desc": "Camada contatavel no nivel de estabelecimento/operadora (decisor PJ). Vende como enriquecimento / lista B2B.",
     "alvos": [
         {"nome": "Plataformas de sales intelligence", "ex": "Apollo, ZoomInfo, Coresignal, PDL, Lusha", "uso": "contato de decisor PJ (dentro do enquadramento LGPD)"},
         {"nome": "Quem vende PARA clinicas", "ex": "distribuidoras de material medico, fabricantes de equipamento", "uso": "prospecao do dono/decisor da clinica"},
         {"nome": "Software de gestao/prontuario", "ex": "iClinic, Feegow, Tasy, Pixeon", "uso": "lead do gestor da clinica/hospital"},
         {"nome": "Fintechs de credito para saude", "ex": "credito para clinicas, maquininha", "uso": "contato do decisor financeiro"},
     ]},
]


def vender_body():
    h = ['<h1>Para quem vender</h1><p class=sub>Dois produtos, compradores diferentes. Marque o que validar &mdash; fica salvo neste navegador.</p>']
    for i, g in enumerate(VENDER):
        h.append(f'<h2>{g["grupo"]}</h2><p class=sub>{g["desc"]}</p><div class=seg>')
        for j, a in enumerate(g["alvos"]):
            cid = f"v{i}_{j}"
            h.append(f'''<div class=alvo id=card_{cid}>
              <h4>{a["nome"]}</h4><div class=ex>{a["ex"]}</div><div class=uso>{a["uso"]}</div>
              <label class=chk><input type=checkbox data-k="{cid}" onchange="mark(this)"> validado</label>
            </div>''')
        h.append('</div>')
    h.append("""<script>
    function mark(el){const k=el.dataset.k;localStorage.setItem('val_'+k,el.checked?'1':'0');document.getElementById('card_'+k).classList.toggle('ok',el.checked)}
    document.querySelectorAll('input[type=checkbox]').forEach(el=>{const k=el.dataset.k;if(localStorage.getItem('val_'+k)==='1'){el.checked=true;document.getElementById('card_'+k).classList.add('ok')}});
    </script>""")
    return "".join(h)


@app.route("/vender")
def vender():
    return render_template_string(PAGE, title="Para quem vender", nav=NAV, body=vender_body())


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
