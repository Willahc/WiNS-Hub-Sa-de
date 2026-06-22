"""
WiNS Hub Saude - Gerador do site ESTATICO (GitHub Pages)
========================================================
Converte as 3 paginas do app Flask (wins_hub_app.py) em arquivos estaticos
em docs/, para publicar no GitHub Pages SEM servidor/banco:

  docs/index.html        -> Dashboard (publico, agregado) + navegacao
  docs/oportunidade.html -> Indice de Oportunidade (filtros/ordenacao/CSV no NAVEGADOR)
  docs/oportunidade.json -> dados da tabela oportunidade_investimento (snapshot)
  docs/vender.html       -> "Para quem vender" (estatico)

A filtragem que antes era SQL (/api/oportunidade) passa a ser feita em JS sobre o
JSON. Sem PII (so agregado por municipio). Idempotente: regrava docs/ a cada run.

Uso:
    python wins_hub_saude_static_site.py
Depois: git add -A && git commit -m "atualiza site" && git push
"""
import os
import json
from decimal import Decimal

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor

# Reaproveita o template e o conteudo de "Para quem vender" do app (fonte unica)
from wins_hub_app import PAGE, vender_body

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env.saude"))
DSN = os.environ["DATABASE_URL"]
DOCS = os.path.join(BASE_DIR, "docs")
PUBLICO = os.path.join(BASE_DIR, "wins_hub_saude_dashboard_publico.html")
os.makedirs(DOCS, exist_ok=True)

# Navegacao com links RELATIVOS (no Pages o site fica sob /WiNS-Hub-Sa-de/)
NAV = """
<nav style="position:sticky;top:0;z-index:9999;background:#0a0f1a;border-bottom:1px solid #22304d;
            padding:11px 22px;display:flex;gap:6px;align-items:center;font:600 14px sans-serif">
  <span style="color:#37d7a6;margin-right:18px">WiNS Hub Saude</span>
  <a href="index.html" style="color:#cfe;text-decoration:none;padding:6px 12px;border-radius:8px">Dashboard</a>
  <a href="oportunidade.html" style="color:#cfe;text-decoration:none;padding:6px 12px;border-radius:8px">Indice de Oportunidade</a>
  <a href="vender.html" style="color:#cfe;text-decoration:none;padding:6px 12px;border-radius:8px">Para quem vender</a>
</nav>
"""

COLS = ["municipio_nome", "uf", "populacao", "medicos_por_mil", "enfermeiros_por_mil",
        "tem_tomografo", "cobertura_privada_pct", "pib_per_capita",
        "indice_oportunidade", "tier", "sweet_spot"]


def _jsonable(v):
    if isinstance(v, Decimal):
        return float(v)
    return v


def gerar_dados():
    sql = f"""
        SELECT {', '.join(COLS)}
        FROM oportunidade_investimento
        ORDER BY indice_oportunidade DESC, populacao DESC
    """
    with psycopg2.connect(DSN, cursor_factory=RealDictCursor) as c, c.cursor() as cur:
        cur.execute(sql)
        rows = [{k: _jsonable(v) for k, v in r.items()} for r in cur.fetchall()]
    path = os.path.join(DOCS, "oportunidade.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
    print(f"  oportunidade.json: {len(rows)} municipios")
    return len(rows)


def render(title, body):
    return (PAGE.replace("{{title}}", title)
                .replace("{{nav|safe}}", NAV)
                .replace("{{body|safe}}", body))


def gerar_index():
    with open(PUBLICO, encoding="utf-8") as f:
        html = f.read()
    html = html.replace("<body>", "<body>" + NAV, 1)
    with open(os.path.join(DOCS, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  index.html: {len(html)/1024:.0f} KB (dashboard + nav)")


OPORT_BODY = """
<h1>Indice de Oportunidade de Investimento em Saude</h1>
<p class=sub>Carencia assistencial x demanda x mercado pagante x infraestrutura. Score 0-100 por municipio (snapshot agregado, sem PII).</p>
<div class=card>
  <div class=filters>
    <label>UF <select id=uf><option value="">Todas</option></select></label>
    <label>Tier <select id=tier><option value="">Todos</option><option>ALTA</option><option>MEDIA</option><option>BAIXA</option></select></label>
    <label>Pop. minima <input id=minpop type=number value=0 style="width:110px"></label>
    <label>Buscar <input id=q placeholder="municipio..."></label>
    <button onclick=applyFilters()>Filtrar</button>
    <button class=alt onclick=exportCsv()>Exportar CSV</button>
    <span class=pill id=count></span>
  </div>
  <div id=tbl>Carregando dados...</div>
</div>
<script>
const COLS=["municipio_nome","uf","populacao","medicos_por_mil","enfermeiros_por_mil","tem_tomografo","cobertura_privada_pct","pib_per_capita","indice_oportunidade","tier","sweet_spot"];
const fmt=n=>n==null?'-':Number(n).toLocaleString('pt-BR');
const v=id=>document.getElementById(id).value;
let DATA=[],cur=[],sortKey='indice_oportunidade',sortDir=-1;
function applyFilters(){
  const uf=v('uf'),tier=v('tier'),minpop=+(v('minpop')||0),q=v('q').toLowerCase();
  cur=DATA.filter(r=>(r.populacao||0)>=minpop && (!uf||r.uf===uf) && (!tier||r.tier===tier) && (!q||(r.municipio_nome||'').toLowerCase().includes(q)));
  render();
}
function csvcell(x){if(x==null)return '';x=String(x);return /[",\\n]/.test(x)?'"'+x.replace(/"/g,'""')+'"':x;}
function exportCsv(){
  const lines=[COLS.join(',')].concat(cur.map(r=>COLS.map(c=>csvcell(r[c])).join(',')));
  const blob=new Blob(["\\ufeff"+lines.join('\\n')],{type:'text/csv;charset=utf-8'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='oportunidade_wins_hub_saude.csv';a.click();URL.revokeObjectURL(a.href);
}
function sortBy(k){if(sortKey===k)sortDir=-sortDir;else{sortKey=k;sortDir=-1}render()}
function render(){
  document.getElementById('count').textContent=cur.length+' municipios';
  const c=[...cur].sort((a,b)=>{let x=a[sortKey],y=b[sortKey];if(typeof x==='string')return sortDir*x.localeCompare(y);return sortDir*((x||0)-(y||0))});
  const H=[['municipio_nome','Municipio'],['uf','UF'],['populacao','Pop'],['indice_oportunidade','Indice'],['tier','Tier'],['medicos_por_mil','Med/mil'],['enfermeiros_por_mil','Enf/mil'],['cobertura_privada_pct','Cob.%'],['pib_per_capita','PIB pc']];
  const nums=['populacao','indice_oportunidade','medicos_por_mil','enfermeiros_por_mil','cobertura_privada_pct','pib_per_capita'];
  let h='<table><tr>'+H.map(x=>`<th class="${nums.includes(x[0])?'n':''}" onclick="sortBy('${x[0]}')">${x[1]}</th>`).join('')+'</tr>';
  h+=c.map(r=>`<tr><td>${r.municipio_nome}</td><td>${r.uf}</td><td class=n>${fmt(r.populacao)}</td><td class=n><b>${r.indice_oportunidade}</b></td><td class="tier-${r.tier}">${r.tier}${r.sweet_spot?' &#9733;':''}</td><td class=n>${r.medicos_por_mil}</td><td class=n>${r.enfermeiros_por_mil}</td><td class=n>${r.cobertura_privada_pct}</td><td class=n>${fmt(Math.round(r.pib_per_capita))}</td></tr>`).join('');
  document.getElementById('tbl').innerHTML=h+'</table>';
}
fetch('oportunidade.json').then(r=>r.json()).then(d=>{
  DATA=d;
  const ufs=[...new Set(d.map(r=>r.uf).filter(Boolean))].sort();
  document.getElementById('uf').innerHTML='<option value="">Todas</option>'+ufs.map(x=>`<option>${x}</option>`).join('');
  applyFilters();
}).catch(e=>{document.getElementById('tbl').textContent='Falha ao carregar oportunidade.json: '+e;});
</script>
"""


def gerar_oportunidade():
    html = render("Indice de Oportunidade", OPORT_BODY)
    with open(os.path.join(DOCS, "oportunidade.html"), "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  oportunidade.html: {len(html)/1024:.0f} KB")


def gerar_vender():
    html = render("Para quem vender", vender_body())
    with open(os.path.join(DOCS, "vender.html"), "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  vender.html: {len(html)/1024:.0f} KB")


if __name__ == "__main__":
    print("Gerando site estatico em docs/ ...")
    gerar_dados()
    gerar_index()
    gerar_oportunidade()
    gerar_vender()
    print("OK. Publique com: git add -A && git commit -m 'site' && git push")
