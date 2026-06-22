"""
WiNS Hub Saude - Gerador do site ESTATICO (GitHub Pages)
========================================================
Converte as 3 paginas do app Flask (wins_hub_app.py) em arquivos estaticos
em docs/, para publicar no GitHub Pages SEM servidor/banco:

  docs/index.html        -> Dashboard (publico, agregado) + navegacao
  docs/oportunidade.html -> Indice de Oportunidade (Tabulator + graficos Chart.js,
                            filtros/ordenacao/CSV/Excel no NAVEGADOR sobre o JSON)
  docs/oportunidade.json -> dados da tabela oportunidade_investimento (snapshot)
  docs/vender.html       -> "Para quem vender" (estatico)

Sem PII (so agregado por municipio). Idempotente: regrava docs/ a cada run.

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

# Reaproveita o bloco <style> do template do app (fonte unica de estilo)
STYLE = PAGE.split("<style>", 1)[1].split("</style>", 1)[0]


def _jsonable(v):
    if isinstance(v, Decimal):
        return round(float(v), 2)  # arredonda p/ encolher o JSON sem perder utilidade
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
    print(f"  oportunidade.json: {len(rows)} municipios ({os.path.getsize(path)/1024:.0f} KB)")
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


# Pagina de Oportunidade: Tabulator (paginacao/virtual DOM, filtro por coluna,
# ordenacao, export CSV/Excel) + graficos Chart.js. Tudo no navegador, sobre o JSON.
OPORT_PAGE = """<!doctype html><html lang=pt-BR><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Indice de Oportunidade - WiNS Hub Saude</title>
<link rel="stylesheet" href="https://unpkg.com/tabulator-tables@6.3.1/dist/css/tabulator_midnight.min.css">
<script src="https://unpkg.com/tabulator-tables@6.3.1/dist/js/tabulator.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<script src="https://cdn.sheetjs.com/xlsx-0.20.3/package/dist/xlsx.full.min.js"></script>
<style>
{{style}}
.charts{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}
@media(max-width:880px){.charts{grid-template-columns:1fr}}
.chartbox{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:14px}
.chartbox h3{margin:0 0 10px;font-size:14px;color:var(--mut);font-weight:600}
.chartbox canvas{max-height:240px}
.toolbar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
.kpi-row{display:flex;gap:12px;flex-wrap:wrap;margin:4px 0 16px}
.kpi-mini{background:var(--card2);border:1px solid var(--bd);border-radius:10px;padding:10px 14px}
.kpi-mini .v{font-size:20px;font-weight:700}.kpi-mini .l{color:var(--mut);font-size:12px}
.tabulator{background:var(--card);border:1px solid var(--bd);border-radius:12px;font-size:13px}
</style></head><body>{{nav}}
<div class=wrap>
<h1>Indice de Oportunidade de Investimento em Saude</h1>
<p class=sub>Carencia assistencial x demanda x mercado pagante x infraestrutura. Score 0-100 por municipio (snapshot agregado, sem PII).</p>
<div class="kpi-row" id=kpis></div>
<div class=charts>
  <div class=chartbox><h3>Municipios por tier</h3><canvas id=chTier></canvas></div>
  <div class=chartbox><h3>Top 12 UFs por sweet spots</h3><canvas id=chUf></canvas></div>
</div>
<div class=toolbar>
  <input id=search placeholder="Buscar municipio..." style="min-width:220px">
  <button class=alt onclick="table.download('csv','oportunidade_wins_hub_saude.csv',{bom:true})">Exportar CSV</button>
  <button class=alt onclick="table.download('xlsx','oportunidade_wins_hub_saude.xlsx',{sheetName:'Oportunidade'})">Exportar Excel</button>
  <span class=pill id=count></span>
</div>
<div id=tbl></div>
</div>
<script>
const ptInt=c=>{const v=c.getValue();return v==null?'-':Number(v).toLocaleString('pt-BR',{maximumFractionDigits:0});};
const ptDec=d=>c=>{const v=c.getValue();return v==null?'-':Number(v).toLocaleString('pt-BR',{minimumFractionDigits:d,maximumFractionDigits:d});};
function tierFmt(c){const v=c.getValue();const col=v==='ALTA'?'#37d7a6':v==='MEDIA'?'#f6c453':'#8aa0c0';const el=c.getElement();el.style.color=col;el.style.fontWeight=600;return v;}
let table, DATA=[];
const cols=[
 {title:"Municipio",field:"municipio_nome",headerFilter:"input",minWidth:150,widthGrow:3},
 {title:"UF",field:"uf",headerFilter:"list",headerFilterParams:{valuesLookup:true,clearable:true},hozAlign:"center",width:80},
 {title:"Pop.",field:"populacao",sorter:"number",hozAlign:"right",formatter:ptInt,width:100},
 {title:"Indice",field:"indice_oportunidade",sorter:"number",hozAlign:"right",formatter:ptDec(1),width:90},
 {title:"Tier",field:"tier",headerFilter:"list",headerFilterParams:{values:["","ALTA","MEDIA","BAIXA"]},formatter:tierFmt,hozAlign:"center",width:90},
 {title:"Med/mil",field:"medicos_por_mil",sorter:"number",hozAlign:"right",formatter:ptDec(2),width:95},
 {title:"Enf/mil",field:"enfermeiros_por_mil",sorter:"number",hozAlign:"right",formatter:ptDec(2),width:95},
 {title:"Cob.%",field:"cobertura_privada_pct",sorter:"number",hozAlign:"right",formatter:ptDec(1),width:90},
 {title:"PIB pc",field:"pib_per_capita",sorter:"number",hozAlign:"right",formatter:ptInt,width:100},
 {title:"Tomografo",field:"tem_tomografo",formatter:"tickCross",hozAlign:"center",width:100,headerFilter:"tickCross",headerFilterParams:{tristate:true}},
 {title:"Sweet",field:"sweet_spot",formatter:"tickCross",hozAlign:"center",width:80,headerFilter:"tickCross",headerFilterParams:{tristate:true}},
];
function miniKpis(d){
  const alta=d.filter(r=>r.tier==='ALTA').length, sweet=d.filter(r=>r.sweet_spot).length;
  const semtomo=d.filter(r=>r.tem_tomografo===false).length;
  document.getElementById('kpis').innerHTML=[
    ['Municipios',d.length],['Tier ALTA',alta],['Sweet spots',sweet],['Sem tomografo',semtomo]
  ].map(x=>`<div class=kpi-mini><div class=v>${Number(x[1]).toLocaleString('pt-BR')}</div><div class=l>${x[0]}</div></div>`).join('');
}
function charts(d){
  Chart.defaults.color='#8aa0c0';
  const tiers=['ALTA','MEDIA','BAIXA'];
  const tierCounts=tiers.map(t=>d.filter(r=>r.tier===t).length);
  new Chart(document.getElementById('chTier'),{type:'doughnut',
    data:{labels:tiers,datasets:[{data:tierCounts,backgroundColor:['#37d7a6','#f6c453','#4a5a78'],borderColor:'#131c30',borderWidth:2}]},
    options:{responsive:true,plugins:{legend:{position:'right'}}}});
  const byUf={};d.forEach(r=>{if(r.sweet_spot){byUf[r.uf]=(byUf[r.uf]||0)+1}});
  const top=Object.entries(byUf).sort((a,b)=>b[1]-a[1]).slice(0,12);
  new Chart(document.getElementById('chUf'),{type:'bar',
    data:{labels:top.map(x=>x[0]),datasets:[{data:top.map(x=>x[1]),backgroundColor:'#4f9cf9'}]},
    options:{responsive:true,plugins:{legend:{display:false}},scales:{x:{grid:{display:false}},y:{grid:{color:'#22304d'},beginAtZero:true}}}});
}
fetch('oportunidade.json').then(r=>r.json()).then(d=>{
  DATA=d; miniKpis(d); charts(d);
  table=new Tabulator('#tbl',{
    data:d, layout:'fitColumns', responsiveLayout:'collapse', height:'620px',
    pagination:true, paginationSize:50, paginationSizeSelector:[25,50,100,250],
    movableColumns:true, columnDefaults:{headerTooltip:true},
    initialSort:[{column:'indice_oportunidade',dir:'desc'}], columns:cols,
  });
  const upd=()=>{document.getElementById('count').textContent=table.getDataCount('active')+' municipios';};
  table.on('tableBuilt',upd); table.on('dataFiltered',upd);
  document.getElementById('search').addEventListener('input',e=>{table.setFilter('municipio_nome','like',e.target.value);});
}).catch(e=>{document.getElementById('tbl').textContent='Falha ao carregar oportunidade.json: '+e;});
</script>
</body></html>"""


def gerar_oportunidade():
    html = OPORT_PAGE.replace("{{nav}}", NAV).replace("{{style}}", STYLE)
    with open(os.path.join(DOCS, "oportunidade.html"), "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  oportunidade.html: {len(html)/1024:.0f} KB (Tabulator + Chart.js)")


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
