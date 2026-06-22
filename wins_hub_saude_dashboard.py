"""
WiNS Hub Saude - Gerador de dashboard analitico (HTML self-contained)
=====================================================================
Gera DOIS arquivos:
  wins_hub_saude_dashboard.html          -> COMPLETO interno (com amostra/PII, noindex)
  wins_hub_saude_dashboard_publico.html  -> AGREGADO seguro (sem PII) p/ GitHub Pages

Inclui o mapa de desertos medicos (Leaflet) e os numeros da sprint
(densidade medica, operadoras QSA, etc.).

Uso:
    python wins_hub_saude_dashboard.py
"""

import os
import json
import time

from dotenv import load_dotenv
import psycopg2

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env.saude"))
OUT_FULL = os.path.join(BASE_DIR, "wins_hub_saude_dashboard.html")
OUT_PUB = os.path.join(BASE_DIR, "wins_hub_saude_dashboard_publico.html")

# distintos nacionais (recomputaveis pelos scripts de densidade)
MEDICOS_CNES = 576725
ENFERMEIROS_CNES = 473244

TIPOS = {
    1: "Posto de Saude", 2: "Centro de Saude / UBS", 4: "Policlinica",
    5: "Hospital Geral", 7: "Hospital Especializado", 15: "Unidade Mista",
    20: "Pronto Socorro Geral", 21: "Pronto Socorro Especializado",
    22: "Consultorio Isolado", 36: "Clinica / Centro de Especialidade",
    39: "Unidade SADT (Apoio Diagnose/Terapia)", 40: "Unidade Movel Terrestre",
    42: "Unidade Movel Pre-Hospitalar (Urgencia)", 43: "Farmacia",
    45: "Unidade de Saude da Familia", 50: "Unidade SADT", 60: "Cooperativa",
    61: "Centro de Parto Normal", 62: "Hospital/Dia", 67: "LACEN",
    68: "Central de Gestao em Saude", 69: "Hemoterapia/Hematologia",
    70: "CAPS (Atencao Psicossocial)", 71: "Centro Apoio Saude da Familia (NASF)",
    72: "Saude Indigena", 73: "Pronto Atendimento (UPA)", 74: "Polo Academia da Saude",
    75: "Telessaude", 76: "Central Regulacao Urgencias", 77: "Atencao Domiciliar (Home Care)",
}


def q(cur, sql):
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def gerar_dados(cur):
    D = {}
    D["kpis"] = q(cur, "SELECT * FROM stats_saude")[0]
    D["kpis"]["com_email_decisor"] = q(cur, "SELECT count(*) n FROM estabelecimentos WHERE decisor_email IS NOT NULL AND decisor_email<>''")[0]["n"]
    D["kpis"]["operadoras"] = q(cur, "SELECT count(*) n FROM operadoras_ans")[0]["n"]
    D["kpis"]["operadoras_qsa"] = q(cur, "SELECT count(*) n FROM operadoras_ans WHERE decisor_qsa_nome IS NOT NULL")[0]["n"]
    D["kpis"]["medicos_mm"] = q(cur, "SELECT count(*) n FROM medicos")[0]["n"]
    D["kpis"]["medicos_cnes"] = MEDICOS_CNES
    D["kpis"]["com_geo"] = q(cur, "SELECT count(*) n FROM estabelecimentos WHERE latitude IS NOT NULL")[0]["n"]
    dz = q(cur, "SELECT count(*) FILTER (WHERE classificacao='DESERTO') des, count(*) FILTER (WHERE classificacao='BAIXA_COBERTURA') baixa FROM desertos_medicos")[0]
    D["kpis"]["desertos"] = dz["des"]
    D["kpis"]["baixa_cob"] = dz["baixa"]

    k = D["kpis"]
    D["funil"] = [
        {"label": "Estabelecimentos (CNES)", "value": k["total_estabelecimentos"]},
        {"label": "Com CNPJ", "value": k["com_cnpj"]},
        {"label": "Com decisor (QSA)", "value": k["com_decisor_enriquecido"]},
        {"label": "Com e-mail de decisor", "value": k["com_email_decisor"]},
    ]
    D["uf"] = q(cur, """
        SELECT uf, COUNT(*) total, SUM(tem_email) com_email, SUM(tem_internacao) hospitais,
               SUM(CASE WHEN decisor_nome IS NOT NULL THEN 1 ELSE 0 END) com_decisor,
               ROUND(SUM(CASE WHEN decisor_nome IS NOT NULL THEN 1 ELSE 0 END)::numeric/COUNT(*)*100,1) pct_decisor
        FROM estabelecimentos WHERE uf IS NOT NULL AND uf<>'' GROUP BY uf ORDER BY total DESC
    """)
    D["tipos"] = q(cur, "SELECT tipo_unidade_cod cod, COUNT(*) n FROM estabelecimentos WHERE tipo_unidade_cod IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 16")
    for t in D["tipos"]:
        t["nome"] = TIPOS.get(t["cod"], f"Tipo {t['cod']}")
    D["fontes"] = q(cur, "SELECT COALESCE(fonte_enriquecimento,'(nao processado)') fonte, COUNT(*) n FROM estabelecimentos GROUP BY 1 ORDER BY 2 DESC")
    D["cargos"] = q(cur, "SELECT decisor_cargo cargo, COUNT(*) n FROM estabelecimentos WHERE decisor_cargo IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 10")
    D["email_status"] = q(cur, "SELECT decisor_email_status status, COUNT(*) n FROM estabelecimentos WHERE decisor_email_status IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")
    D["completude"] = []
    tot = k["total_estabelecimentos"]
    comp = q(cur, """SELECT SUM(tem_cnpj) cnpj, SUM(tem_email) email, SUM(tem_telefone) telefone,
                     COUNT(latitude) geo, SUM(CASE WHEN decisor_nome IS NOT NULL THEN 1 ELSE 0 END) decisor,
                     SUM(CASE WHEN decisor_email IS NOT NULL AND decisor_email<>'' THEN 1 ELSE 0 END) decisor_email,
                     SUM(tem_internacao) internacao FROM estabelecimentos""")[0]
    rot = {"cnpj": "CNPJ", "email": "E-mail estab.", "telefone": "Telefone", "geo": "Geo (lat/long)",
           "decisor": "Decisor (nome)", "decisor_email": "E-mail do decisor", "internacao": "Internacao"}
    D["completude"] = [{"campo": rot[c], "n": int(v or 0), "pct": round((v or 0)/tot*100, 1)} for c, v in comp.items()]
    D["medicos_uf"] = q(cur, "SELECT uf_atuacao uf, COUNT(*) n FROM medicos WHERE uf_atuacao IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 15")
    D["operadoras_mod"] = q(cur, "SELECT modalidade, COUNT(*) n FROM operadoras_ans WHERE modalidade IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")

    # ---- DESERTOS MEDICOS ----
    D["desertos_dist"] = q(cur, """
        SELECT classificacao, count(*) n, sum(populacao) pop, round(avg(medicos_por_mil_hab),2) dens
        FROM desertos_medicos GROUP BY 1 ORDER BY min(medicos_por_mil_hab)
    """)
    # mapa: deserto + baixa cobertura, com centroide (media das coords dos estabelecimentos)
    mapa = q(cur, """
        SELECT d.municipio_nome nome, d.uf, d.populacao pop, d.n_medicos nmed,
               d.medicos_por_mil_hab dens, d.classificacao classe,
               round(avg(e.latitude)::numeric,4) lat, round(avg(e.longitude)::numeric,4) lng
        FROM desertos_medicos d
        JOIN estabelecimentos e ON e.municipio_cod=d.municipio_cod
        WHERE d.classificacao IN ('DESERTO','BAIXA_COBERTURA')
          AND e.latitude IS NOT NULL AND e.longitude IS NOT NULL
          AND e.latitude BETWEEN -34 AND 6 AND e.longitude BETWEEN -74 AND -34
        GROUP BY 1,2,3,4,5,6
    """)
    for m in mapa:
        m["lat"] = float(m["lat"]) if m["lat"] is not None else None
        m["lng"] = float(m["lng"]) if m["lng"] is not None else None
        m["dens"] = float(m["dens"]) if m["dens"] is not None else 0
    D["desertos_mapa"] = [m for m in mapa if m["lat"] and m["lng"]]
    D["desertos_top"] = q(cur, """
        SELECT municipio_nome nome, uf, populacao pop, n_medicos nmed, medicos_por_mil_hab dens
        FROM desertos_medicos WHERE classificacao='DESERTO' AND populacao>15000
        ORDER BY populacao DESC LIMIT 12
    """)

    # ---- DENSIDADE DE ENFERMAGEM ----
    D["kpis"]["enfermeiros_cnes"] = ENFERMEIROS_CNES
    ez = q(cur, "SELECT count(*) FILTER (WHERE classificacao='DESERTO') des, count(*) FILTER (WHERE classificacao='BAIXA_COBERTURA') baixa FROM densidade_enfermagem")[0]
    D["kpis"]["enf_desertos"] = ez["des"]
    D["kpis"]["enf_baixa"] = ez["baixa"]
    nt = q(cur, "SELECT sum(n_tecnicos) tec, sum(n_auxiliares) aux FROM densidade_enfermagem")[0]
    D["kpis"]["tecnicos_cnes"] = nt["tec"]
    D["kpis"]["auxiliares_cnes"] = nt["aux"]
    D["enf_dist"] = q(cur, """
        SELECT classificacao, count(*) n, sum(populacao) pop, round(avg(enfermeiros_por_mil),2) dens
        FROM densidade_enfermagem GROUP BY 1 ORDER BY min(enfermeiros_por_mil)
    """)
    mape = q(cur, """
        SELECT d.municipio_nome nome, d.uf, d.populacao pop, d.n_enfermeiros nmed,
               d.enfermeiros_por_mil dens, d.classificacao classe,
               round(avg(e.latitude)::numeric,4) lat, round(avg(e.longitude)::numeric,4) lng
        FROM densidade_enfermagem d
        JOIN estabelecimentos e ON e.municipio_cod=d.municipio_cod
        WHERE d.classificacao IN ('DESERTO','BAIXA_COBERTURA')
          AND e.latitude IS NOT NULL AND e.longitude IS NOT NULL
          AND e.latitude BETWEEN -34 AND 6 AND e.longitude BETWEEN -74 AND -34
        GROUP BY 1,2,3,4,5,6
    """)
    for m in mape:
        m["lat"] = float(m["lat"]) if m["lat"] is not None else None
        m["lng"] = float(m["lng"]) if m["lng"] is not None else None
        m["dens"] = float(m["dens"]) if m["dens"] is not None else 0
    D["enf_mapa"] = [m for m in mape if m["lat"] and m["lng"]]
    D["enf_top"] = q(cur, """
        SELECT municipio_nome nome, uf, populacao pop, n_enfermeiros nmed, enfermeiros_por_mil dens
        FROM densidade_enfermagem WHERE classificacao='DESERTO' AND populacao>20000
        ORDER BY populacao DESC LIMIT 12
    """)

    # ---- INDICE DE OPORTUNIDADE ----
    D["kpis"]["oport_alta"] = q(cur, "SELECT count(*) n FROM oportunidade_investimento WHERE tier='ALTA'")[0]["n"]
    D["kpis"]["sweet_spot"] = q(cur, "SELECT count(*) n FROM oportunidade_investimento WHERE sweet_spot")[0]["n"]
    D["oport_dist"] = q(cur, """
        SELECT tier, count(*) n, sum(populacao) pop, round(avg(indice_oportunidade),1) idx
        FROM oportunidade_investimento GROUP BY 1 ORDER BY min(indice_oportunidade) DESC
    """)
    mapo = q(cur, """
        SELECT o.municipio_nome nome, o.uf, o.populacao pop, o.indice_oportunidade idx,
               o.medicos_por_mil med, o.cobertura_privada_pct cob,
               round(avg(e.latitude)::numeric,4) lat, round(avg(e.longitude)::numeric,4) lng
        FROM oportunidade_investimento o
        JOIN estabelecimentos e ON e.municipio_cod=o.municipio_cod
        WHERE o.sweet_spot AND e.latitude IS NOT NULL AND e.longitude IS NOT NULL
          AND e.latitude BETWEEN -34 AND 6 AND e.longitude BETWEEN -74 AND -34
        GROUP BY 1,2,3,4,5,6
    """)
    for m in mapo:
        m["lat"] = float(m["lat"]) if m["lat"] is not None else None
        m["lng"] = float(m["lng"]) if m["lng"] is not None else None
        m["idx"] = float(m["idx"]) if m["idx"] is not None else 0
    D["oport_mapa"] = [m for m in mapo if m["lat"] and m["lng"]]
    D["oport_top"] = q(cur, """
        SELECT municipio_nome nome, uf, populacao pop, medicos_por_mil med,
               cobertura_privada_pct cob, pib_per_capita pib, indice_oportunidade idx
        FROM oportunidade_investimento WHERE sweet_spot
        ORDER BY indice_oportunidade DESC, populacao DESC LIMIT 15
    """)

    # ---- AMOSTRA (PII - so versao interna) ----
    D["amostra"] = q(cur, """
        SELECT razao_social, uf, decisor_nome, decisor_cargo, decisor_email
        FROM estabelecimentos WHERE decisor_nome IS NOT NULL
          AND decisor_email_status='INFERIDO_DOMINIO' AND tem_internacao=1
        ORDER BY cnes_id LIMIT 15
    """)
    D["gerado_em"] = time.strftime("%Y-%m-%d %H:%M")
    return D


HTML = """<!DOCTYPE html>
<html lang="pt-BR"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="__ROBOTS__">
<title>WiNS Hub Saude - Dashboard</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
:root{--bg:#0b1220;--card:#131c30;--card2:#1a2740;--txt:#e6edf7;--mut:#8aa0c0;--acc:#37d7a6;--acc2:#4f9cf9;--red:#ef5f5f;--orange:#f6a443;--bd:#22304d}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1280px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:26px;margin:0 0 2px}h2{font-size:16px;margin:34px 0 14px;color:var(--acc);border-left:3px solid var(--acc);padding-left:10px}
.sub{color:var(--mut);margin:0 0 8px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-top:18px}
.kpi{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:16px}
.kpi.hot{border-color:var(--red);background:linear-gradient(180deg,#1f1420,#131c30)}
.kpi.good{border-color:var(--acc);background:linear-gradient(180deg,#10231c,#131c30)}
.kpi .v{font-size:24px;font-weight:700}.kpi .l{color:var(--mut);font-size:12px;margin-top:4px}.kpi .s{font-size:12px;color:var(--acc);margin-top:6px}
.grid{display:grid;gap:16px}.g2{grid-template-columns:1fr 1fr}.g3{grid-template-columns:2fr 1fr}
@media(max-width:880px){.g2,.g3{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:16px}
.card h3{margin:0 0 12px;font-size:14px}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--bd)}
th{color:var(--mut);font-weight:600;font-size:12px;text-transform:uppercase}td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.bar{height:7px;background:var(--card2);border-radius:4px;overflow:hidden}.bar>i{display:block;height:100%;background:linear-gradient(90deg,var(--acc2),var(--acc))}
.foot{margin-top:40px;color:var(--mut);font-size:12px;border-top:1px solid var(--bd);padding-top:16px}
.warn{background:#2a1d12;border:1px solid #5a3a1a;color:#f6c453;border-radius:10px;padding:12px 14px;margin-top:14px;font-size:13px}
#map{height:460px;border-radius:10px}.leaflet-popup-content{font:13px sans-serif}
canvas{max-height:320px}
.legend{display:flex;gap:16px;margin-top:10px;font-size:12px;color:var(--mut)}.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;vertical-align:middle}
.toggle{display:inline-flex;background:var(--card2);border:1px solid var(--bd);border-radius:10px;padding:3px;margin:0 0 12px}
.tg{background:none;border:0;color:var(--mut);padding:7px 18px;border-radius:8px;cursor:pointer;font:600 13px sans-serif}
.tg.on{background:var(--acc2);color:#06101f}
</style></head><body><div class="wrap">
<h1>WiNS Hub Saude &mdash; Dashboard</h1>
<p class="sub">CNES 202605 (DATASUS) &middot; QSA Receita Federal &middot; ANS &middot; Mais Medicos &middot; IBGE Censo 2022 &middot; gerado em <span id="ts"></span></p>
<div class="kpis" id="kpis"></div>
<div id="piiwarn"></div>

<h2>&#9888; Mapa de carencia &amp; oportunidade &mdash; inteligencia territorial</h2>
<div class="toggle"><button id="btMed" class="tg on">Carencia medica</button><button id="btEnf" class="tg">Carencia enfermagem</button><button id="btOp" class="tg">Oportunidade de investimento</button></div>
<p class="sub" id="desnarr"></p>
<div class="grid g3">
  <div class="card"><div id="map"></div>
    <div class="legend"><span><span class="dot" style="background:#ef5f5f"></span><span id="legDes">Deserto</span></span><span><span class="dot" style="background:#f6a443"></span><span id="legBx">Baixa cobertura</span></span></div></div>
  <div class="card"><h3 id="desTblTit">Cobertura</h3><div id="desTbl"></div>
    <h3 style="margin-top:16px">Maiores desertos (pop &gt; 20k)</h3><div id="desTop"></div></div>
</div>

<h2>Funil de enriquecimento</h2><div class="card"><canvas id="funil"></canvas></div>

<h2>Cobertura geografica</h2>
<div class="grid g3">
  <div class="card"><h3>Estabelecimentos por UF</h3><canvas id="ufBar"></canvas></div>
  <div class="card"><h3>% com decisor por UF</h3><div id="ufTbl"></div></div>
</div>

<h2>Perfil dos estabelecimentos</h2>
<div class="grid g2">
  <div class="card"><h3>Tipos de estabelecimento (top 16)</h3><canvas id="tipos"></canvas></div>
  <div class="card"><h3>Completude dos campos</h3><div id="comp"></div></div>
</div>

<h2>Decisores (QSA)</h2>
<div class="grid g2">
  <div class="card"><h3>Fonte do enriquecimento</h3><canvas id="fontes"></canvas></div>
  <div class="card"><h3>Cargo do decisor</h3><canvas id="cargos"></canvas></div>
</div>

<h2>E-mails &amp; outras bases</h2>
<div class="grid g2">
  <div class="card"><h3>Status do e-mail do decisor</h3><canvas id="emailSt"></canvas></div>
  <div class="card"><h3>Operadoras ANS por modalidade</h3><canvas id="oper"></canvas></div>
</div>
<div class="grid g2">
  <div class="card"><h3>Mais Medicos por UF (top 15)</h3><canvas id="medUf"></canvas></div>
  <div class="card"><h3>Estabelecimentos com e-mail por UF</h3><canvas id="ufEmail"></canvas></div>
</div>

<div id="amostraSec"></div>
<div class="foot" id="foot"></div>
</div>
<script>const DATA=__DATA__;const PUBLICO=__PUBLICO__;</script>
<script>
const fmt=n=>n==null?'-':Number(n).toLocaleString('pt-BR');
const C=['#37d7a6','#4f9cf9','#f6c453','#ef7d7d','#9b8cff','#5fd0e0','#f49ac2','#8aa0c0','#7ed957','#ffa657'];
document.getElementById('ts').textContent=DATA.gerado_em;
const k=DATA.kpis;
const kp=[
 ['Estabelecimentos',k.total_estabelecimentos,'CNES 202605',0],
 ['Medicos mapeados',k.medicos_cnes,'por municipio',0],
 ['Enfermeiros mapeados',k.enfermeiros_cnes,'por municipio',0],
 ['Tecnicos de enfermagem',k.tecnicos_cnes,'por municipio',0],
 ['Auxiliares de enfermagem',k.auxiliares_cnes,'por municipio',0],
 ['Desertos medicos',k.desertos,'<0,5 med/mil hab',1],
 ['Desertos enfermagem',k.enf_desertos,'<1 enf/mil hab',1],
 ['Sweet spots (investir)',k.sweet_spot,'carencia + mercado',2],
 ['Oportunidade ALTA',k.oport_alta,'top 10% municipios',2],
 ['Hospitais',k.hospitais,'',0],
 ['Decisores (QSA)',k.com_decisor_enriquecido,((k.com_decisor_enriquecido/k.com_cnpj*100)|0)+'% c/ CNPJ',0],
 ['E-mail decisor',k.com_email_decisor,'inferido',0],
 ['Operadoras ANS',k.operadoras,k.operadoras_qsa+' c/ QSA',0],
 ['Municipios',k.municipios_cobertos,'',0],['UFs',k.ufs_cobertas,'',0],
 ['Mais Medicos',k.medicos_mm,'',0]
];
const KCLS={1:' hot',2:' good'};
document.getElementById('kpis').innerHTML=kp.map(x=>`<div class="kpi${KCLS[x[3]]||''}"><div class="v">${fmt(x[1])}</div><div class="l">${x[0]}</div>${x[2]?`<div class="s">${x[2]}</div>`:''}</div>`).join('');
if(!PUBLICO){document.getElementById('piiwarn').innerHTML='<div class="warn">Versao INTERNA: contem dados pessoais (amostra de decisores; e-mails inferidos nao verificados). noindex ativo. Nao publicar.</div>';}
// ---- Mapa com toggle: carencia medica / enfermagem / oportunidade ----
const DESCFG={
  medico:{mode:'carencia',mapa:DATA.desertos_mapa,dist:DATA.desertos_dist,top:DATA.desertos_top,prof:'medico',unit:'med/mil',limd:'0,5',limb:'0,5-1,0',col:'Medicos'},
  enfermagem:{mode:'carencia',mapa:DATA.enf_mapa,dist:DATA.enf_dist,top:DATA.enf_top,prof:'enfermeiro',unit:'enf/mil',limd:'1,0',limb:'1,0-2,0',col:'Enfermeiros'},
  oportunidade:{mode:'oport',mapa:DATA.oport_mapa,dist:DATA.oport_dist,top:DATA.oport_top}
};
const map=L.map('map',{scrollWheelZoom:false,preferCanvas:true}).setView([-9,-53],4);
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',{attribution:'&copy; OSM &copy; CARTO',maxZoom:12}).addTo(map);
const desLayer=L.layerGroup().addTo(map);
function renderDesertos(tipo){
  const cfg=DESCFG[tipo];
  desLayer.clearLayers();
  if(cfg.mode==='carencia'){
    const des=cfg.dist.find(d=>d.classificacao==='DESERTO')||{n:0,pop:0};
    const bx=cfg.dist.find(d=>d.classificacao==='BAIXA_COBERTURA')||{n:0,pop:0};
    document.getElementById('desnarr').innerHTML=`<b>${fmt(des.n)}</b> municipios em deserto de ${cfg.prof} (menos de ${cfg.limd} por mil hab, <b>${fmt(des.pop)}</b> pessoas) e <b>${fmt(bx.n)}</b> em baixa cobertura (<b>${fmt(bx.pop)}</b>) &mdash; a nivel de municipio, com nome e coordenadas.`;
    document.getElementById('legDes').textContent=`Deserto (<${cfg.limd} ${cfg.unit})`;
    document.getElementById('legBx').textContent=`Baixa cobertura (${cfg.limb})`;
    document.getElementById('desTblTit').textContent=`Cobertura de ${cfg.prof} (${cfg.unit})`;
    document.getElementById('desTbl').innerHTML='<table><tr><th>Classe</th><th class=n>Munic</th><th class=n>Populacao</th><th class=n>'+cfg.unit+'</th></tr>'+cfg.dist.map(d=>`<tr><td>${d.classificacao}</td><td class=n>${fmt(d.n)}</td><td class=n>${fmt(d.pop)}</td><td class=n>${d.dens}</td></tr>`).join('')+'</table>';
    document.getElementById('desTop').innerHTML='<table><tr><th>Municipio</th><th>UF</th><th class=n>Pop</th><th class=n>'+cfg.col.slice(0,3)+'</th><th class=n>/mil</th></tr>'+cfg.top.map(d=>`<tr><td>${d.nome}</td><td>${d.uf}</td><td class=n>${fmt(d.pop)}</td><td class=n>${fmt(d.nmed)}</td><td class=n>${d.dens}</td></tr>`).join('')+'</table>';
    cfg.mapa.forEach(m=>{
      L.circleMarker([m.lat,m.lng],{radius:m.classe==='DESERTO'?6:4,color:m.classe==='DESERTO'?'#ef5f5f':'#f6a443',fillOpacity:.65,weight:1})
       .addTo(desLayer).bindPopup(`<b>${m.nome}-${m.uf}</b><br>Pop: ${fmt(m.pop)}<br>${cfg.col}: ${fmt(m.nmed)}<br>Densidade: <b>${m.dens}</b>/mil hab`);
    });
  } else {
    const alta=cfg.dist.find(d=>d.tier==='ALTA')||{n:0,pop:0};
    document.getElementById('desnarr').innerHTML=`<b>${fmt(DATA.kpis.sweet_spot)}</b> municipios "sweet spot" &mdash; carencia assistencial alta E mercado pagante viavel (deficit + PIB + cobertura privada). <b>${fmt(alta.n)}</b> sao de oportunidade ALTA (top 10%). Onde investir em saude tem maior impacto e viabilidade.`;
    document.getElementById('legDes').textContent='Sweet spot (investir)';
    document.getElementById('legBx').textContent='';
    document.getElementById('desTblTit').textContent='Distribuicao por tier de oportunidade';
    document.getElementById('desTbl').innerHTML='<table><tr><th>Tier</th><th class=n>Munic</th><th class=n>Populacao</th><th class=n>indice</th></tr>'+cfg.dist.map(d=>`<tr><td>${d.tier}</td><td class=n>${fmt(d.n)}</td><td class=n>${fmt(d.pop)}</td><td class=n>${d.idx}</td></tr>`).join('')+'</table>';
    document.getElementById('desTop').innerHTML='<table><tr><th>Municipio</th><th>UF</th><th class=n>Pop</th><th class=n>med/mil</th><th class=n>cob%</th><th class=n>indice</th></tr>'+cfg.top.map(d=>`<tr><td>${d.nome}</td><td>${d.uf}</td><td class=n>${fmt(d.pop)}</td><td class=n>${d.med}</td><td class=n>${d.cob}</td><td class=n>${d.idx}</td></tr>`).join('')+'</table>';
    cfg.mapa.forEach(m=>{
      L.circleMarker([m.lat,m.lng],{radius:3+m.idx/18,color:'#37d7a6',fillOpacity:.6,weight:1})
       .addTo(desLayer).bindPopup(`<b>${m.nome}-${m.uf}</b><br>Pop: ${fmt(m.pop)}<br>Medicos: ${m.med}/mil<br>Cobertura privada: ${m.cob}%<br>Indice: <b>${m.idx}</b>`);
    });
  }
}
function setTipo(t){
  document.getElementById('btMed').classList.toggle('on',t==='medico');
  document.getElementById('btEnf').classList.toggle('on',t==='enfermagem');
  document.getElementById('btOp').classList.toggle('on',t==='oportunidade');
  renderDesertos(t);setTimeout(()=>map.invalidateSize(),100);
}
document.getElementById('btMed').onclick=()=>setTipo('medico');
document.getElementById('btEnf').onclick=()=>setTipo('enfermagem');
document.getElementById('btOp').onclick=()=>setTipo('oportunidade');
renderDesertos('medico');
const baseOpt=()=>({responsive:true,plugins:{legend:{labels:{color:'#8aa0c0'}}},scales:{x:{ticks:{color:'#8aa0c0'},grid:{color:'#22304d'}},y:{ticks:{color:'#8aa0c0'},grid:{color:'#22304d'}}}});
const bar=(id,labels,data,horiz,col)=>new Chart(document.getElementById(id),{type:'bar',data:{labels,datasets:[{data,backgroundColor:col||C[1]}]},options:Object.assign(baseOpt(),{indexAxis:horiz?'y':'x',plugins:{legend:{display:false}}})});
const dough=(id,labels,data)=>new Chart(document.getElementById(id),{type:'doughnut',data:{labels,datasets:[{data,backgroundColor:C}]},options:{responsive:true,plugins:{legend:{position:'right',labels:{color:'#8aa0c0',font:{size:11}}}}}});
// funil
new Chart(document.getElementById('funil'),{type:'bar',data:{labels:DATA.funil.map(f=>f.label),datasets:[{data:DATA.funil.map(f=>f.value),backgroundColor:[C[1],C[0],C[2],C[3]]}]},options:Object.assign(baseOpt(),{indexAxis:'y',plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>fmt(c.raw)}}}})});
bar('ufBar',DATA.uf.map(u=>u.uf),DATA.uf.map(u=>u.total));
document.getElementById('ufTbl').innerHTML='<table><tr><th>UF</th><th class=n>Total</th><th class=n>Decisor</th><th class=n>%</th></tr>'+DATA.uf.slice(0,14).map(u=>`<tr><td>${u.uf}</td><td class=n>${fmt(u.total)}</td><td class=n>${fmt(u.com_decisor)}</td><td class=n>${u.pct_decisor}%</td></tr>`).join('')+'</table>';
bar('tipos',DATA.tipos.map(t=>t.nome),DATA.tipos.map(t=>t.n),true);
document.getElementById('comp').innerHTML='<table>'+DATA.completude.map(c=>`<tr><td>${c.campo}</td><td style="width:42%"><div class=bar><i style="width:${c.pct}%"></i></div></td><td class=n>${c.pct}%</td></tr>`).join('')+'</table>';
dough('fontes',DATA.fontes.map(f=>f.fonte),DATA.fontes.map(f=>f.n));
bar('cargos',DATA.cargos.map(c=>c.cargo),DATA.cargos.map(c=>c.n),true);
dough('emailSt',DATA.email_status.map(e=>e.status),DATA.email_status.map(e=>e.n));
bar('oper',DATA.operadoras_mod.map(o=>o.modalidade),DATA.operadoras_mod.map(o=>o.n),true);
bar('medUf',DATA.medicos_uf.map(m=>m.uf),DATA.medicos_uf.map(m=>m.n));
bar('ufEmail',DATA.uf.map(u=>u.uf),DATA.uf.map(u=>u.com_email),false,C[0]);
// amostra (so interna)
if(!PUBLICO){document.getElementById('amostraSec').innerHTML='<h2>Amostra &mdash; decisores de hospitais (e-mail inferido)</h2><div class="card"><table><tr><th>Estabelecimento</th><th>UF</th><th>Decisor</th><th>Cargo</th><th>E-mail (inferido)</th></tr>'+DATA.amostra.map(a=>`<tr><td>${a.razao_social||''}</td><td>${a.uf||''}</td><td>${a.decisor_nome||''}</td><td>${a.decisor_cargo||''}</td><td>${a.decisor_email||''}</td></tr>`).join('')+'</table></div>';}
document.getElementById('foot').innerHTML='Fontes: CNES/DATASUS &middot; Receita Federal Dados Abertos CNPJ (QSA) &middot; ANS &middot; Mais Medicos &middot; IBGE Censo 2022. Densidade medica = profissionais distintos com CBO medico por municipio / populacao. '+(PUBLICO?'Versao publica: apenas dados agregados, sem informacao pessoal.':'Versao interna.')+' Gerado em '+DATA.gerado_em+'.';
function fixMap(){try{map.invalidateSize(true)}catch(e){}}
map.whenReady(()=>setTimeout(fixMap,300));
window.addEventListener('load',()=>setTimeout(fixMap,250));
window.addEventListener('resize',fixMap);
setTimeout(fixMap,900);
</script></body></html>"""


def escrever(D, path, publico):
    d = dict(D)
    if publico:
        d.pop("amostra", None)
    html = (HTML.replace("__DATA__", json.dumps(d, ensure_ascii=False, default=str))
                .replace("__PUBLICO__", "true" if publico else "false")
                .replace("__ROBOTS__", "index, follow" if publico else "noindex, nofollow"))
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  {os.path.basename(path)}: {len(html)/1024:.0f} KB ({'PUBLICO' if publico else 'INTERNO'})")


def main():
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    with conn.cursor() as cur:
        D = gerar_dados(cur)
    conn.close()
    print("Gerando dashboards...")
    escrever(D, OUT_FULL, publico=False)
    escrever(D, OUT_PUB, publico=True)
    print(f"  desertos no mapa: {len(D['desertos_mapa'])} | desertos: {D['kpis']['desertos']} | baixa: {D['kpis']['baixa_cob']}")


if __name__ == "__main__":
    main()
