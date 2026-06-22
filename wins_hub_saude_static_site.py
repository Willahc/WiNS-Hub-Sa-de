"""
WiNS Hub Saude - Gerador do site ESTATICO (GitHub Pages)
========================================================
Converte as 3 paginas do app Flask (wins_hub_app.py) em arquivos estaticos
em docs/, para publicar no GitHub Pages SEM servidor/banco:

  docs/index.html        -> Dashboard (publico, agregado) + navegacao
  docs/oportunidade.html -> Indice de Oportunidade: Tabulator + graficos Chart.js
                            (tier, top UFs, dispersao), busca fuzzy (Fuse.js),
                            export CSV/Excel/PDF (jsPDF). Tudo no NAVEGADOR.
  docs/oportunidade.json -> dados da tabela oportunidade_investimento (snapshot)
  docs/vender.html       -> "Para quem vender" (estatico)
  docs/wins-logo.png     -> logo (favicon / Open Graph)

Sem PII (so agregado por municipio). Idempotente: regrava docs/ a cada run.

Uso:
    python wins_hub_saude_static_site.py
Depois: git add -A && git commit -m "atualiza site" && git push
"""
import os
import json
import shutil
import urllib.request
from datetime import date
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
LOGO_SRC = r"C:\Users\kbadmin\Documents\Projetos\WiNS Hub\anexos\LOGO_WINS HUB.png"
SITE_URL = "https://willahc.github.io/WiNS-Hub-Sa-de/"
os.makedirs(DOCS, exist_ok=True)

# Minificacao de HTML (opcional). minify_js=False por seguranca (nao toca na
# logica dos scripts inline); colapsa whitespace/comentarios e CSS inline.
try:
    import minify_html as _mh

    def minify(html):
        try:
            return _mh.minify(html, minify_css=True, minify_js=False,
                              do_not_minify_doctype=True, keep_closing_tags=True)
        except Exception:
            return html
except ImportError:
    def minify(html):
        return html

# Navegacao com links RELATIVOS (no Pages o site fica sob /WiNS-Hub-Sa-de/)
NAV = """
<nav style="position:sticky;top:0;z-index:9999;background:#0a0f1a;border-bottom:1px solid #22304d;
            padding:11px 22px;display:flex;gap:6px;align-items:center;font:600 14px sans-serif">
  <span style="color:#37d7a6;margin-right:18px">WiNS Hub Saude</span>
  <a href="index.html" style="color:#cfe;text-decoration:none;padding:6px 12px;border-radius:8px">Dashboard</a>
  <a href="oportunidade.html" style="color:#cfe;text-decoration:none;padding:6px 12px;border-radius:8px">Indice de Oportunidade</a>
  <a href="mapa.html" style="color:#cfe;text-decoration:none;padding:6px 12px;border-radius:8px">Mapa</a>
  <a href="vender.html" style="color:#cfe;text-decoration:none;padding:6px 12px;border-radius:8px">Para quem vender</a>
</nav>
"""

# municipio_cod (IBGE) entra para busca fuzzy e p/ o mapa coropletico (futuro)
COLS = ["municipio_cod", "municipio_nome", "uf", "populacao", "medicos_por_mil",
        "enfermeiros_por_mil", "tem_tomografo", "cobertura_privada_pct",
        "pib_per_capita", "indice_oportunidade", "tier", "sweet_spot"]

# Reaproveita o bloco <style> do template do app (fonte unica de estilo)
STYLE = PAGE.split("<style>", 1)[1].split("</style>", 1)[0]

# Analytics GoatCounter (gratis) - ativo.
ANALYTICS = """<script data-goatcounter="https://william.goatcounter.com/count" async src="//gc.zgo.at/count.js"></script>"""

OG_DESC = ("Inteligencia territorial de saude no Brasil: indice de oportunidade por "
           "municipio, carencia assistencial, mercado pagante e infraestrutura.")


def meta(title, page):
    """Tags de favicon + Open Graph (compartilhamento) por pagina."""
    url = SITE_URL + page
    return (
        f'<link rel="icon" href="wins-logo.png">\n'
        f'<meta name="description" content="{OG_DESC}">\n'
        f'<meta property="og:type" content="website">\n'
        f'<meta property="og:title" content="{title} - WiNS Hub Saude">\n'
        f'<meta property="og:description" content="{OG_DESC}">\n'
        f'<meta property="og:image" content="{SITE_URL}wins-logo.png">\n'
        f'<meta property="og:url" content="{url}">\n'
        f'<meta name="twitter:card" content="summary_large_image">\n'
        f'{ANALYTICS}\n'
    )


def inject_head(html, title, page):
    """Insere as meta tags antes de </head> (uniforme p/ todas as paginas)."""
    return html.replace("</head>", meta(title, page) + "</head>", 1)


def _jsonable(v):
    if isinstance(v, Decimal):
        return round(float(v), 2)  # arredonda p/ encolher o JSON sem perder utilidade
    return v


def gerar_assets():
    if os.path.exists(LOGO_SRC):
        shutil.copyfile(LOGO_SRC, os.path.join(DOCS, "wins-logo.png"))
        print("  wins-logo.png copiado")
    else:
        print("  (logo nao encontrado; favicon/OG sem imagem)")


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
    html = inject_head(html, "Dashboard", "index.html")
    with open(os.path.join(DOCS, "index.html"), "w", encoding="utf-8") as f:
        f.write(minify(html))
    print(f"  index.html: {len(html)/1024:.0f} KB (dashboard + nav)")


# Pagina de Oportunidade: Tabulator + Chart.js (tier/UF/dispersao) + Fuse.js +
# export CSV/Excel/PDF. Tudo no navegador, sobre o JSON.
OPORT_PAGE = """<!doctype html><html lang=pt-BR><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Indice de Oportunidade - WiNS Hub Saude</title>
<link rel="stylesheet" href="https://unpkg.com/tabulator-tables@6.3.1/dist/css/tabulator_midnight.min.css">
<script src="https://unpkg.com/tabulator-tables@6.3.1/dist/js/tabulator.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<script src="https://cdn.sheetjs.com/xlsx-0.20.3/package/dist/xlsx.full.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/fuse.js@7.0.0/dist/fuse.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/jspdf@2.5.1/dist/jspdf.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/jspdf-autotable@3.8.2/dist/jspdf.plugin.autotable.min.js"></script>
<style>
{{style}}
.charts{display:grid;grid-template-columns:1fr 1fr 1.2fr;gap:14px;margin-bottom:14px}
@media(max-width:980px){.charts{grid-template-columns:1fr}}
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
  <div class=chartbox><h3>Carencia medica x mercado pagante</h3><canvas id=chScatter></canvas></div>
</div>
<div class=toolbar>
  <input id=search placeholder="Buscar municipio (tolerante a erro)..." style="min-width:260px">
  <button class=alt onclick="table.download('csv','oportunidade_wins_hub_saude.csv',{bom:true})">CSV</button>
  <button class=alt onclick="table.download('xlsx','oportunidade_wins_hub_saude.xlsx',{sheetName:'Oportunidade'})">Excel</button>
  <button onclick="exportPDF()">Relatorio PDF</button>
  <span class=pill id=count></span>
</div>
<div id=tbl></div>
</div>
<script>
const ptInt=c=>{const v=c.getValue();return v==null?'-':Number(v).toLocaleString('pt-BR',{maximumFractionDigits:0});};
const ptDec=d=>c=>{const v=c.getValue();return v==null?'-':Number(v).toLocaleString('pt-BR',{minimumFractionDigits:d,maximumFractionDigits:d});};
function tierFmt(c){const v=c.getValue();const col=v==='ALTA'?'#37d7a6':v==='MEDIA'?'#f6c453':'#8aa0c0';const el=c.getElement();el.style.color=col;el.style.fontWeight=600;return v;}
let table, DATA=[], fuse=null, chTier=null, chUf=null, chScatter=null;
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
  chTier=new Chart(document.getElementById('chTier'),{type:'doughnut',
    data:{labels:tiers,datasets:[{data:tiers.map(t=>d.filter(r=>r.tier===t).length),backgroundColor:['#37d7a6','#f6c453','#4a5a78'],borderColor:'#131c30',borderWidth:2}]},
    options:{responsive:true,animation:false,plugins:{legend:{position:'right'}}}});
  const byUf={};d.forEach(r=>{if(r.sweet_spot){byUf[r.uf]=(byUf[r.uf]||0)+1}});
  const top=Object.entries(byUf).sort((a,b)=>b[1]-a[1]).slice(0,12);
  chUf=new Chart(document.getElementById('chUf'),{type:'bar',
    data:{labels:top.map(x=>x[0]),datasets:[{data:top.map(x=>x[1]),backgroundColor:'#4f9cf9'}]},
    options:{responsive:true,animation:false,plugins:{legend:{display:false}},scales:{x:{grid:{display:false}},y:{grid:{color:'#22304d'},beginAtZero:true}}}});
  const colT={ALTA:'#37d7a6',MEDIA:'#f6c453',BAIXA:'#8aa0c0'};
  const ds=tiers.map(t=>({label:t,backgroundColor:colT[t],pointRadius:2.5,
    data:d.filter(r=>r.tier===t&&r.medicos_por_mil!=null&&r.cobertura_privada_pct!=null).map(r=>({x:r.medicos_por_mil,y:r.cobertura_privada_pct}))}));
  chScatter=new Chart(document.getElementById('chScatter'),{type:'scatter',data:{datasets:ds},
    options:{responsive:true,animation:false,plugins:{legend:{position:'top'}},
      scales:{x:{title:{display:true,text:'Medicos / mil hab'},grid:{color:'#22304d'}},
              y:{title:{display:true,text:'Cobertura privada %'},grid:{color:'#22304d'}}}}});
}
function exportPDF(){
  const {jsPDF}=window.jspdf;
  const doc=new jsPDF({orientation:'landscape',unit:'pt',format:'a4'});
  const active=table.getData('active');
  doc.setFontSize(16);doc.setTextColor(20);doc.text('WiNS Hub Saude - Indice de Oportunidade',40,40);
  doc.setFontSize(10);doc.setTextColor(110);
  doc.text(`${active.length} municipios filtrados  |  gerado em ${new Date().toLocaleDateString('pt-BR')}`,40,58);
  let y=72;
  try{doc.addImage(chTier.toBase64Image(),'PNG',40,y,150,120);}catch(e){}
  try{doc.addImage(chUf.toBase64Image(),'PNG',210,y,300,120);}catch(e){}
  try{doc.addImage(chScatter.toBase64Image(),'PNG',525,y,290,120);}catch(e){}
  doc.autoTable({startY:y+135,
    head:[['Municipio','UF','Pop','Indice','Tier','Med/mil','Enf/mil','Cob%','PIB pc']],
    body:active.map(r=>[r.municipio_nome,r.uf,r.populacao,r.indice_oportunidade,r.tier,r.medicos_por_mil,r.enfermeiros_por_mil,r.cobertura_privada_pct,r.pib_per_capita==null?'':Math.round(r.pib_per_capita)]),
    styles:{fontSize:7,cellPadding:2},headStyles:{fillColor:[31,28,48]},alternateRowStyles:{fillColor:[244,246,250]}});
  doc.save('relatorio_oportunidade_wins_hub_saude.pdf');
}
fetch('oportunidade.json').then(r=>r.json()).then(d=>{
  DATA=d; miniKpis(d); charts(d);
  fuse=new Fuse(d,{keys:['municipio_nome'],threshold:0.34,ignoreLocation:true});
  table=new Tabulator('#tbl',{
    data:d, layout:'fitColumns', responsiveLayout:'collapse', height:'620px',
    pagination:true, paginationSize:50, paginationSizeSelector:[25,50,100,250],
    movableColumns:true, columnDefaults:{headerTooltip:true},
    initialSort:[{column:'indice_oportunidade',dir:'desc'}], columns:cols,
  });
  const upd=()=>{document.getElementById('count').textContent=table.getDataCount('active')+' municipios';};
  table.on('tableBuilt',upd); table.on('dataFiltered',upd);
  document.getElementById('search').addEventListener('input',e=>{
    const q=e.target.value.trim();
    if(!q){table.clearFilter(true);return;}
    const hits=new Set(fuse.search(q).map(h=>h.item.municipio_cod));
    table.setFilter(row=>hits.has(row.municipio_cod));
  });
}).catch(e=>{document.getElementById('tbl').textContent='Falha ao carregar oportunidade.json: '+e;});
</script>
</body></html>"""


def gerar_oportunidade():
    html = OPORT_PAGE.replace("{{nav}}", NAV).replace("{{style}}", STYLE)
    html = inject_head(html, "Indice de Oportunidade", "oportunidade.html")
    with open(os.path.join(DOCS, "oportunidade.html"), "w", encoding="utf-8") as f:
        f.write(minify(html))
    print(f"  oportunidade.html: {len(html)/1024:.0f} KB (Tabulator+Chart.js+Fuse+jsPDF)")


def gerar_vender():
    html = render("Para quem vender", vender_body())
    html = inject_head(html, "Para quem vender", "vender.html")
    with open(os.path.join(DOCS, "vender.html"), "w", encoding="utf-8") as f:
        f.write(minify(html))
    print(f"  vender.html: {len(html)/1024:.0f} KB")


MALHA_URL = ("https://servicodados.ibge.gov.br/api/v4/malhas/paises/BR"
             "?intrarregiao=municipio&qualidade=minima&formato=application/vnd.geo+json")
MALHA_FILE = os.path.join(DOCS, "municipios_br.geojson")


def _round_coords(obj, nd=3):
    if isinstance(obj, list):
        if obj and isinstance(obj[0], (int, float)):
            return [round(float(obj[0]), nd), round(float(obj[1]), nd)]
        return [_round_coords(x, nd) for x in obj]
    return obj


def gerar_malha(force=False):
    """Baixa a malha municipal do IBGE (qualidade minima) e simplifica as
    coordenadas (3 casas ~ 100m) p/ aliviar o payload. Cacheia por existencia."""
    if os.path.exists(MALHA_FILE) and not force:
        print(f"  municipios_br.geojson: ja existe ({os.path.getsize(MALHA_FILE)/1024/1024:.1f} MB)")
        return
    req = urllib.request.Request(MALHA_URL, headers={"User-Agent": "wins-hub"})
    data = urllib.request.urlopen(req, timeout=180).read()
    if data[:2] == b"\x1f\x8b":  # resposta gzipada
        import gzip
        data = gzip.decompress(data)
    g = json.loads(data)
    for f in g["features"]:
        f["properties"] = {"cod": str(f["properties"].get("codarea", ""))[:6]}  # 6 digitos p/ casar c/ os dados
        f["geometry"]["coordinates"] = _round_coords(f["geometry"]["coordinates"])
    with open(MALHA_FILE, "w", encoding="utf-8") as fh:
        json.dump(g, fh, separators=(",", ":"))
    print(f"  municipios_br.geojson: {len(g['features'])} munic ({os.path.getsize(MALHA_FILE)/1024/1024:.1f} MB)")


# Pagina do MAPA COROPLETICO: Leaflet pinta cada municipio por tier/indice,
# casando a malha do IBGE (por codigo) com oportunidade.json. Renderer canvas.
MAPA_PAGE = """<!doctype html><html lang=pt-BR><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Mapa de Oportunidade - WiNS Hub Saude</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
{{style}}
#map{height:72vh;border-radius:10px}
.toolbar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
.legend{display:flex;gap:14px;flex-wrap:wrap;margin-top:10px;color:var(--mut);font-size:13px}
.legend i{display:inline-block;width:14px;height:14px;border-radius:3px;margin-right:5px;vertical-align:-2px}
.leaflet-popup-content{font:13px sans-serif}
button.on{outline:2px solid var(--acc)}
</style></head><body>{{nav}}
<div class=wrap>
<h1>Mapa de Oportunidade por Municipio</h1>
<p class=sub>Cada municipio pintado pelo indice de oportunidade. Malha IBGE x snapshot agregado (sem PII).</p>
<div class=toolbar>
  <button id=bTier class=on onclick="setMode('tier')">Colorir por tier</button>
  <button id=bIdx class=alt onclick="setMode('index')">Colorir por indice</button>
  <span class=pill id=info>Carregando mapa...</span>
</div>
<div class=card><div id=map></div>
  <div class=legend id=legend></div>
</div>
</div>
<script>
const TIERCOL={ALTA:'#37d7a6',MEDIA:'#f6c453',BAIXA:'#8aa0c0'};
const SEMDADO='#243049';
const fmt=n=>n==null?'-':Number(n).toLocaleString('pt-BR');
function idxColor(v){ // gradiente cinza->verde 0..100
  if(v==null)return SEMDADO;
  const t=Math.max(0,Math.min(100,v))/100;
  const r=Math.round(74+(55-74)*t), g=Math.round(90+(215-90)*t), b=Math.round(120+(166-120)*t);
  return `rgb(${r},${g},${b})`;
}
let DMAP=new Map(), layer=null, mode='tier', map;
function styleFor(props){
  const rec=DMAP.get(props.cod);
  const fill = !rec ? SEMDADO : (mode==='tier'? (TIERCOL[rec.tier]||SEMDADO) : idxColor(rec.indice_oportunidade));
  return {fillColor:fill,weight:.3,color:'#0b1220',fillOpacity:rec?0.78:0.25};
}
function popupFor(props){
  const r=DMAP.get(props.cod);
  if(!r)return `<b>Municipio ${props.cod}</b><br>sem dado`;
  return `<b>${r.municipio_nome}-${r.uf}</b><br>Indice: <b>${r.indice_oportunidade}</b> (${r.tier})${r.sweet_spot?' &#9733;':''}`+
         `<br>Pop: ${fmt(r.populacao)}<br>Medicos: ${r.medicos_por_mil}/mil<br>Cobertura privada: ${r.cobertura_privada_pct}%`;
}
function setMode(m){
  mode=m;
  document.getElementById('bTier').className=m==='tier'?'on':'alt';
  document.getElementById('bIdx').className=m==='index'?'on':'alt';
  if(layer)layer.setStyle(f=>styleFor(f.properties));
  legend();
}
function legend(){
  const el=document.getElementById('legend');
  if(mode==='tier'){
    el.innerHTML=Object.entries(TIERCOL).map(([k,c])=>`<span><i style="background:${c}"></i>${k}</span>`).join('')+`<span><i style="background:${SEMDADO}"></i>sem dado</span>`;
  } else {
    el.innerHTML=[0,25,50,75,100].map(v=>`<span><i style="background:${idxColor(v)}"></i>${v}</span>`).join('')+'  (indice 0-100)';
  }
}
map=L.map('map',{preferCanvas:true,scrollWheelZoom:false}).setView([-15,-53],4);
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',{attribution:'&copy; OSM &copy; CARTO',maxZoom:12}).addTo(map);
Promise.all([
  fetch('oportunidade.json').then(r=>r.json()),
  fetch('municipios_br.geojson').then(r=>r.json())
]).then(([dados,geo])=>{
  dados.forEach(r=>DMAP.set(String(r.municipio_cod),r));
  layer=L.geoJSON(geo,{style:f=>styleFor(f.properties),
    onEachFeature:(f,l)=>l.bindPopup(()=>popupFor(f.properties))}).addTo(map);
  document.getElementById('info').textContent=DMAP.size+' municipios com dado';
  legend();
}).catch(e=>{document.getElementById('info').textContent='Falha ao carregar mapa: '+e;});
</script>
</body></html>"""


def gerar_mapa():
    html = MAPA_PAGE.replace("{{nav}}", NAV).replace("{{style}}", STYLE)
    html = inject_head(html, "Mapa de Oportunidade", "mapa.html")
    with open(os.path.join(DOCS, "mapa.html"), "w", encoding="utf-8") as f:
        f.write(minify(html))
    print(f"  mapa.html: {len(html)/1024:.0f} KB (Leaflet coropletico)")


PAGINAS = ["index.html", "oportunidade.html", "mapa.html", "vender.html"]


def gerar_seo():
    hoje = date.today().isoformat()
    # robots.txt
    with open(os.path.join(DOCS, "robots.txt"), "w", encoding="utf-8") as f:
        f.write(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}sitemap.xml\n")
    # sitemap.xml
    urls = "".join(
        f"  <url><loc>{SITE_URL}{p}</loc><lastmod>{hoje}</lastmod></url>\n"
        for p in PAGINAS)
    with open(os.path.join(DOCS, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                f"{urls}</urlset>\n")
    # .nojekyll (evita processamento Jekyll no Pages)
    open(os.path.join(DOCS, ".nojekyll"), "w").close()
    # 404 custom
    p404 = ("""<!doctype html><html lang=pt-BR><head><meta charset=utf-8>
<title>404 - WiNS Hub Saude</title><link rel="icon" href="wins-logo.png">
<style>{{style}}</style></head><body>{{nav}}
<div class=wrap style="text-align:center;padding-top:60px">
<h1>404</h1><p class=sub>Pagina nao encontrada.</p>
<p><a href="index.html">Voltar ao Dashboard</a></p></div></body></html>""")
    p404 = p404.replace("{{nav}}", NAV).replace("{{style}}", STYLE)
    with open(os.path.join(DOCS, "404.html"), "w", encoding="utf-8") as f:
        f.write(minify(p404))
    print("  seo: robots.txt, sitemap.xml, 404.html, .nojekyll")


if __name__ == "__main__":
    print("Gerando site estatico em docs/ ...")
    gerar_assets()
    gerar_malha()
    gerar_dados()
    gerar_index()
    gerar_oportunidade()
    gerar_mapa()
    gerar_vender()
    gerar_seo()
    print("OK. Publique com: git add -A && git commit -m 'site' && git push")
