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
import subprocess
import urllib.request
from datetime import date
from decimal import Decimal

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor

# Reaproveita o template e o conteudo de "Para quem vender" do app (fonte unica)
from wins_hub_app import PAGE, oportunidades_body

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
  <a href="oportunidades.html" style="color:#cfe;text-decoration:none;padding:6px 12px;border-radius:8px">Oportunidades</a>
</nav>
"""

# municipio_cod (IBGE) entra para busca fuzzy e p/ o mapa coropletico (futuro)
COLS = ["municipio_cod", "municipio_nome", "uf", "populacao", "medicos_por_mil",
        "enfermeiros_por_mil", "tem_tomografo", "cobertura_privada_pct",
        "internacoes_por_mil", "leitos_sus_por_mil", "evitaveis_por_mil", "mortalidade_infantil",
        "apac_onco_por_mil", "apac_dialise_por_mil", "acesso_idx", "pib_per_capita",
        "indice_oportunidade", "tier", "sweet_spot"]

# Reaproveita o bloco <style> do template do app (fonte unica de estilo)
STYLE = PAGE.split("<style>", 1)[1].split("</style>", 1)[0]

OPORTUNIDADES_METADATA = [
    {
        "slug": "oportunidade-equipamentos",
        "title": "Equipamentos Diagnósticos (MedTech)",
        "emoji": "🏥",
        "badge_text": "Oportunidade Geral",
        "badge_class": "badge-general",
        "why": "Existe um mercado altamente relevante de hospitais e clínicas privadas em cidades de médio e grande porte com forte penetração de planos de saúde, mas que hoje não possuem equipamentos próprios de tomografia cadastrados no CNES. A venda ou locação estruturada de equipamentos de imagem (MedTech) nessas praças atende a uma demanda local reprimida e evita o deslocamento de pacientes premium.",
        "where": "Cidades com população superior a 25.000 habitantes, penetração de cobertura de saúde privada superior a 15% e que atualmente não possuem nenhum tomógrafo cadastrado no sistema público ou privado do CNES.",
        "js_filter": "row.tem_tomografo === false && row.populacao > 25000 && row.cobertura_privada_pct > 15"
    },
    {
        "slug": "oportunidade-telemedicina",
        "title": "Telemedicina Corporativa",
        "emoji": "📞",
        "badge_text": "Oportunidade Geral",
        "badge_class": "badge-general",
        "why": "Cidades classificadas como desertos médicos (menos de 0.5 médicos por mil habitantes) criam um sério gargalo operacional e financeiro para cooperativas agroindustriais, indústrias locais e prefeituras. O atendimento via telemedicina e a estruturação de clínicas primárias híbridas reduzem o absenteísmo, otimizam a gestão de sinistros de planos corporativos e entregam acesso básico e especializado de forma imediata.",
        "where": "Municípios com população superior a 15.000 habitantes e com densidade médica crítica inferior a 0.5 médicos por mil habitantes.",
        "js_filter": "row.medicos_por_mil < 0.5 && row.populacao > 15000"
    },
    {
        "slug": "oportunidade-oncologia",
        "title": "Expansão Oncologia e Diálise",
        "emoji": "🧬",
        "badge_text": "Oportunidade Geral",
        "badge_class": "badge-general",
        "why": "Procedimentos ambulatoriais de alta complexidade em oncologia e diálise (faturados via APAC) apresentam forte recorrência e margens consistentes tanto no SUS quanto na saúde privada. A identificação de cidades polo com altos índices de procedimentos per capita, mas que não possuem leitos de alta complexidade ou hospitais especializados locais, sinaliza pontos ideais para abertura ou expansão de novos centros integrados.",
        "where": "Cidades com faturamento de procedimentos de diálise superior a 1.5 por mil habitantes ou faturamento oncológico superior a 1.0 por mil habitantes e que não possuem hospitais de especialidade locais.",
        "js_filter": "row.apac_dialise_por_mil > 1.5 || row.apac_onco_por_mil > 1.0"
    },
    {
        "slug": "oportunidade-enfermagem",
        "title": "Outsourcing de Enfermagem",
        "emoji": "🧑‍⚕️",
        "badge_text": "Oportunidade Geral",
        "badge_class": "badge-general",
        "why": "Hospitais gerais activos, com alto volume de internações anuais, que se localizam em regiões com escassez severa de profissionais de enfermagem (< 1.0/mil hab) enfrentam altos custos com horas extras, fadiga de pessoal e turnover elevado. Empresas de recrutamento especializado, capacitação e outsourcing encontram nestes estabelecimentos parceiros corporativos com alta urgência de contratação.",
        "where": "Municípios com atividade de internação significativa (>50 internações por mil habitantes/ano) e densidade de enfermeiros inferior a 1.0 por mil habitantes.",
        "js_filter": "row.enfermeiros_por_mil < 1.0 && row.internacoes_por_mil > 50"
    },
    {
        "slug": "oportunidade-planos",
        "title": "Venda de Planos de Saúde B2B",
        "emoji": "💎",
        "badge_text": "Oportunidade Geral",
        "badge_class": "badge-general",
        "why": "Municípios ricos com forte atividade agrícola, industrial ou comercial (PIB per capita elevado) que apresentam baixa penetração de planos de saúde privados (<10%) são oceanos azuis comerciais. Corretoras de seguros e operadoras de saúde que realizarem prospecção ativa de planos corporativos coletivos e PME (Pequenas e Médias Empresas) encontrarão forte capacidade financeira local associada à carência de cobertura privada.",
        "where": "Municípios com PIB per capita superior a R$ 35.000 e índice de cobertura de saúde privada de operadoras inferior a 10.0%.",
        "js_filter": "row.pib_per_capita > 35000 && row.cobertura_privada_pct < 10"
    },
    {
        "slug": "oportunidade-sirio-oncologia",
        "title": "Sírio - Expansão Oncologia",
        "emoji": "🎗️",
        "badge_text": "Sírio-Libanês",
        "badge_class": "badge-sirio",
        "why": "A marca premium do Centro de Oncologia Sírio-Libanês busca municípios de alta renda com forte presença de beneficiários de planos de saúde de alto padrão (convênios premium), onde haja demanda local de oncologia faturada pelo SUS, indicando que os pacientes da região precisam se deslocar para capitais para obter o tratamento adequado. Um satélite Sírio nestes locais captura o mercado premium local.",
        "where": "Cidades polo de alta renda com PIB per capita superior a R$ 40.000, taxa geral de planos de saúde superior a 25% e incidência de procedimentos de quimioterapia/radioterapia superior a 0.8 por mil habitantes.",
        "js_filter": "row.apac_onco_por_mil > 0.8 && row.cobertura_privada_pct > 25 && row.pib_per_capita > 40000"
    },
    {
        "slug": "oportunidade-sirio-corporativa",
        "title": "Sírio - Saúde Corporativa B2B",
        "emoji": "💼",
        "badge_text": "Sírio-Libanês",
        "badge_class": "badge-sirio",
        "why": "Grandes empregadores industriais e cooperativas localizados em desertos médicos são leads excelentes para o portfólio de Atenção Primária Corporativa e Gestão de Saúde Integrada (Telemedicina/Ambulatório In-Company) do Sírio-Libanês. O Sírio assume a saúde básica dessas corporações e reduz a sinistralidade do plano premium deles.",
        "where": "Municípios ricos (PIBpc > R$ 45.000) classificados como desertos médicos ou com carência severa de médicos generalistas/especialistas (< 1.0 médicos por mil habitantes).",
        "js_filter": "row.medicos_por_mil < 1.0 && row.pib_per_capita > 45000"
    },
    {
        "slug": "oportunidade-sirio-diagnosticos",
        "title": "Sírio - Expansão de Diagnósticos",
        "emoji": "🔬",
        "badge_text": "Sírio-Libanês",
        "badge_class": "badge-sirio",
        "why": "Identificação de praças com população de médio e grande porte, com alta cobertura de convênios médicos premium, mas que possuem déficit de equipamentos de imagem de última geração. Permite à bandeira Sírio Diagnósticos abrir laboratórios próprios, postos de coleta ou firmar parcerias preferenciais locais de alto ticket.",
        "where": "Cidades populosas com população superior a 40.000 habitantes, mercado premium forte (>30% de planos de saúde) e ausência de tomógrafos de alta capacidade cadastrados no CNES.",
        "js_filter": "row.populacao > 40000 && row.cobertura_privada_pct > 30 && row.tem_tomografo === false"
    },
    {
        "slug": "oportunidade-operadoras",
        "title": "Operadoras e Seguradoras B2B",
        "emoji": "🏢",
        "badge_text": "Comprador B2B",
        "badge_class": "badge-general",
        "why": "Operadoras de saúde e seguradoras (como Hapvida, Bradesco, Porto) buscam regiões com forte PIB per capita mas com baixa cobertura de planos de saúde para expandir suas redes credenciadas e vender planos coletivos. Identificar essas praças evita o investimento em regiões saturadas e acelera a captação de clientes corporativos locais.",
        "where": "Municípios com PIB per capita superior a R$ 30.000 e índice de cobertura privada de saúde inferior a 15.0%.",
        "js_filter": "row.cobertura_privada_pct < 15 && row.pib_per_capita > 30000"
    },
    {
        "slug": "oportunidade-redes",
        "title": "Redes de Clínicas e Hospitais",
        "emoji": "🏥",
        "badge_text": "Comprador B2B",
        "badge_class": "badge-general",
        "why": "Grandes redes de saúde (como Dr. Consulta, Fleury, Dasa, Rede D'Or) utilizam estudos de Site Selection baseados em demanda assistencial reprimida. Municípios populosos com escassez de leitos gerais indicam praças ideias para novas unidades de atendimento ambulatorial ou hospitalar.",
        "where": "Cidades populosas com mais de 50.000 habitantes e taxa de leitos hospitalares SUS inferior a 1.5 por mil habitantes.",
        "js_filter": "row.populacao > 50000 && row.leitos_sus_por_mil < 1.5"
    },
    {
        "slug": "oportunidade-farmacias",
        "title": "Expansão de Redes de Farmácias",
        "emoji": "💊",
        "badge_text": "Comprador B2B",
        "badge_class": "badge-general",
        "why": "Grandes redes varejistas de medicamentos (RaiaDrogasil, Pague Menos) estão convertendo suas lojas em hubs de saúde básica (consultórios farmacêuticos). Mapear municípios de porte médio com escassez geral de médicos e alto PIB ajuda a direcionar a abertura de lojas com forte apelo de atendimento primário local.",
        "where": "Municípios com população superior a 30.000 habitantes e densidade geral de médicos inferior a 0.8 por mil habitantes.",
        "js_filter": "row.populacao > 30000 && row.medicos_por_mil < 0.8"
    },
    {
        "slug": "oportunidade-fundos",
        "title": "Fundos VC/PE e Investidores",
        "emoji": "📈",
        "badge_text": "Comprador B2B",
        "badge_class": "badge-general",
        "why": "Fundos de Venture Capital e Private Equity focados em saúde utilizam dados de atratividade territorial para validar teses de investimento de suas investidas (healthtechs e redes de clínicas). Cidades classificadas como Sweet Spots possuem o balanço ideal de demanda reprimida, PIB forte e crescimento de beneficiários.",
        "where": "Municípios classificados formalmente como Sweet Spot de investimento na base de dados.",
        "js_filter": "row.sweet_spot === true"
    },
    {
        "slug": "oportunidade-industria",
        "title": "Indústria e Distribuidoras Farma",
        "emoji": "🏭",
        "badge_text": "Comprador B2B",
        "badge_class": "badge-general",
        "why": "A indústria farmacêutica (EMS, Hypera) e distribuidoras planejam seus territórios comerciais de representantes médicos com base na atividade de internação e consumo local de medicamentos. Municípios com altos índices de internações hospitalares demandam maior atenção comercial.",
        "where": "Municípios com índice de internações hospitalares anualizado superior a 60 por mil habitantes.",
        "js_filter": "row.internacoes_por_mil > 60"
    },
    {
        "slug": "oportunidade-consultorias",
        "title": "Consultorias e FIIs Imobiliários",
        "emoji": "💼",
        "badge_text": "Comprador B2B",
        "badge_class": "badge-general",
        "why": "Fundos Imobiliários (FIIs) de hospitais e consultorias estratégicas (McKinsey, BCG) analisam o potencial de sustentabilidade de longo prazo de ativos físicos de saúde. Cidades populosas de alta renda fornecem o fluxo de caixa estável necessário para sustentar operações de Real Estate em saúde.",
        "where": "Municípios polo com população superior a 80.000 habitantes e PIB per capita superior a R$ 45.000.",
        "js_filter": "row.populacao > 80000 && row.pib_per_capita > 45000"
    },
    {
        "slug": "oportunidade-publico",
        "title": "Políticas Públicas e ONGs",
        "emoji": "🏛️",
        "badge_text": "Comprador B2B",
        "badge_class": "badge-general",
        "why": "Secretarias estaduais de saúde e organizações multilaterais buscam alocar recursos e políticas públicas de forma otimizada. Identificar municípios com taxas alarmantes de mortalidade evitável ou mortalidade infantil aponta onde as ações de atenção básica são mais urgentes.",
        "where": "Cidades com óbitos por causas evitáveis superiores a 10 por mil habitantes ou mortalidade infantil superior a 15 por mil nascimentos.",
        "js_filter": "row.evitaveis_por_mil > 10 || row.mortalidade_infantil > 15"
    },
    {
        "slug": "oportunidade-sales-intel",
        "title": "Sales Intelligence B2B",
        "emoji": "💻",
        "badge_text": "Enriquecimento",
        "badge_class": "badge-general",
        "why": "Plataformas de inteligência de vendas (como Lusha, Apollo) enriquecem suas bases corporativas com dados de contato direto de proprietários e sócios (QSA) obtidos de forma estruturada. Clínicas e hospitais privados em praças de alto interesse são leads quentes.",
        "where": "Estabelecimentos de saúde privados em municípios Sweet Spot com decisor cadastrado.",
        "js_filter": "row.sweet_spot === true"
    },
    {
        "slug": "oportunidade-fornecedores",
        "title": "Fornecedores e Material Médico",
        "emoji": "🛒",
        "badge_text": "Enriquecimento",
        "badge_class": "badge-general",
        "why": "Fabricantes de equipamentos cirúrgicos e insumos médicos descartáveis prosperam ao vender para clínicas privadas ativas em mercados de alto PIB e alta cobertura de convênios. O acesso ao decisor nestas praças otimiza o pipeline comercial outbound.",
        "where": "Municípios com PIB per capita superior a R$ 35.000 e cobertura de saúde privada de operadoras superior a 20.0%.",
        "js_filter": "row.pib_per_capita > 35000 && row.cobertura_privada_pct > 20"
    },
    {
        "slug": "oportunidade-software-gestao",
        "title": "Software de Gestão e Prontuários",
        "emoji": "🖥️",
        "badge_text": "Enriquecimento",
        "badge_class": "badge-general",
        "why": "SaaS de prontuário eletrônico e ERP (iClinic, Pixeon) vendem para clínicas médicas privadas de médio porte. Praças populosas e com planos de saúde consolidados concentram a maior fatia de clínicas aptas a pagar por digitalização de prontuários.",
        "where": "Municípios com mais de 20.000 habitantes e cobertura de saúde privada superior a 15.0%.",
        "js_filter": "row.populacao > 20000 && row.cobertura_privada_pct > 15"
    },
    {
        "slug": "oportunidade-fintechs",
        "title": "Fintechs e Crédito da Saúde",
        "emoji": "💳",
        "badge_text": "Enriquecimento",
        "badge_class": "badge-general",
        "why": "Fintechs de antecipação de recebíveis médicos (de planos de saúde/SUS) e crédito para investimentos em capex focam em clínicas privadas estabelecidas em regiões prósperas. A saúde financeira destas clínicas garante baixos índices de inadimplência.",
        "where": "Estabelecimentos em municípios ricos com PIB per capita superior a R$ 40.000.",
        "js_filter": "row.pib_per_capita > 40000"
    }
]

MODAL_HTML = """
<!-- Modal de Detalhes do Municipio -->
<div id="muni-modal" style="display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(11,18,32,0.85); z-index:10000; justify-content:center; align-items:center; backdrop-filter:blur(5px)">
  <div class="card" style="width:90%; max-width:650px; background:var(--card); border:2px solid var(--bd); border-radius:16px; padding:24px; position:relative; box-shadow:0 10px 30px rgba(0,0,0,0.5); max-height:90vh; overflow-y:auto">
    <button onclick="closeModal()" style="position:absolute; top:16px; right:16px; background:none; border:0; color:var(--mut); font-size:24px; cursor:pointer; line-height:1">&times;</button>
    <h2 id="modal-title" style="margin-top:0; border-left:4px solid var(--acc2); padding-left:10px; font-size:20px">Nome do Municipio</h2>
    <p class="sub" id="modal-subtitle">UF | Populacao: 100.000</p>
    
    <div style="display:grid; grid-template-columns:1fr 1fr; gap:20px; margin-top:20px;">
      <div>
        <h3 style="color:var(--acc2); margin:0 0 8px; font-size:14px; border-bottom:1px solid var(--bd); padding-bottom:4px">Mercado e Indices</h3>
        <table class="modal-grid-tbl">
          <tr><td style="color:var(--mut)">Indice Oportunidade:</td><td id="m-idx" style="font-weight:bold; text-align:right">85.0</td></tr>
          <tr><td style="color:var(--mut)">Tier:</td><td id="m-tier" style="font-weight:bold; text-align:right">ALTA</td></tr>
          <tr><td style="color:var(--mut)">Sweet Spot:</td><td id="m-sweet" style="text-align:right">Sim</td></tr>
          <tr><td style="color:var(--mut)">PIB per capita:</td><td id="m-pib" style="text-align:right">R$ 45.000</td></tr>
          <tr><td style="color:var(--mut)">Cobertura Privada:</td><td id="m-cob" style="text-align:right">32%</td></tr>
        </table>
      </div>
      
      <div>
        <h3 style="color:var(--acc); margin:0 0 8px; font-size:14px; border-bottom:1px solid var(--bd); padding-bottom:4px">Infraestrutura e Oferta</h3>
        <table class="modal-grid-tbl">
          <tr><td style="color:var(--mut)">Medicos / mil hab:</td><td id="m-med" style="text-align:right">1.25</td></tr>
          <tr><td style="color:var(--mut)">Enfermeiros / mil:</td><td id="m-enf" style="text-align:right">1.80</td></tr>
          <tr><td style="color:var(--mut)">Tomografo:</td><td id="m-tomo" style="text-align:right">Sim</td></tr>
          <tr><td style="color:var(--mut)">Leitos SUS / mil:</td><td id="m-leitos" style="text-align:right">2.1</td></tr>
          <tr><td style="color:var(--mut)">Internacoes / mil:</td><td id="m-intern" style="text-align:right">75.3</td></tr>
        </table>
      </div>
    </div>

    <div style="margin-top:20px; background:var(--card2); padding:14px; border-radius:10px; border:1px solid var(--bd)">
      <h3 style="margin:0 0 8px; font-size:13px; color:var(--txt)">Procedimentos de Alta Complexidade (APAC / mil hab)</h3>
      <div style="display:flex; justify-content:space-between; font-size:13px">
        <span>Oncologia: <b id="m-onco">0.5</b></span>
        <span>Dialise: <b id="m-dialise">1.2</b></span>
        <span>Acesso Leitos: <b id="m-acesso">2.4</b></span>
      </div>
    </div>
    
    <div style="margin-top:16px; background:rgba(239,95,95,0.06); padding:14px; border-radius:10px; border:1px solid rgba(239,95,95,0.2)">
      <h3 style="margin:0 0 8px; font-size:13px; color:var(--red)">Indicadores de Mortalidade e Saude</h3>
      <div style="display:flex; justify-content:space-between; font-size:13px">
        <span>Obitos Evitaveis: <b id="m-evit" style="color:var(--red)">12.5</b> /mil hab</span>
        <span>Mortalidade Infantil: <b id="m-inf" style="color:var(--red)">14.2</b> /mil nasc.</span>
      </div>
    </div>
    
    <!-- Secao de ROI do Sirio (Exibida apenas nas subpaginas do Sirio) -->
    <div id="modal-roi-section" style="display:none; margin-top:16px; background:linear-gradient(180deg,#1b253c,#131c30); padding:16px; border-radius:10px; border:1px solid #ffa657">
      <h3 style="margin:0 0 8px; font-size:13px; color:#ffa657">🧮 Simulação de Viabilidade Econômica (ROI)</h3>
      <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:12px; font-size:12.5px" id="modal-roi-results">
      </div>
    </div>
  </div>
</div>
"""

OPORT_DETAIL_PAGE = """<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} - WiNS Hub Saude</title>
  <link rel="stylesheet" href="https://unpkg.com/tabulator-tables@6.3.1/dist/css/tabulator_midnight.min.css">
  <script src="https://unpkg.com/tabulator-tables@6.3.1/dist/js/tabulator.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/fuse.js@6.6.2"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js"></script>
  <script src="https://cdn.sheetjs.com/xlsx-0.20.3/package/dist/xlsx.full.min.js"></script>
  <style>
    {style}
    .grid-desc {{ display: grid; grid-template-columns: 3fr 2fr; gap: 20px; margin-bottom: 24px; }}
    @media (max-width: 768px) {{ .grid-desc {{ grid-template-columns: 1fr; }} }}
    .oport-badge {{ display: inline-block; padding: 4px 10px; border-radius: 6px; font-size: 12px; font-weight: bold; margin-bottom: 12px; }}
    .badge-sirio {{ background: rgba(255,166,87,0.15); color: #ffa657; border: 1px solid #ffa657; }}
    .badge-general {{ background: rgba(55,215,166,0.15); color: #37d7a6; border: 1px solid #37d7a6; }}
    .desc-card {{ background: var(--card); border: 1px solid var(--bd); border-radius: 12px; padding: 20px; }}
    .desc-card h3 {{ margin-top: 0; color: var(--acc2); border-bottom: 1px solid var(--bd); padding-bottom: 8px; font-size: 15px; }}
    .desc-card p {{ font-size: 13.5px; color: var(--mut); margin-bottom: 0; line-height: 1.6; }}
    .modal-grid-tbl {{ width: 100%; border-collapse: collapse; }}
    .modal-grid-tbl td {{ border: 0 !important; padding: 6px 0 !important; font-size: 13px !important; text-align: left !important; }}
    input[type=range] {{ -webkit-appearance: none; width: 100%; background: var(--card2); border: 1px solid var(--bd); height: 6px; border-radius: 3px; outline: none; margin: 8px 0; }}
    input[type=range]::-webkit-slider-thumb {{ -webkit-appearance: none; appearance: none; width: 16px; height: 16px; border-radius: 50%; background: #ffa657; cursor: pointer; transition: background .15s; }}
    input[type=range]::-webkit-slider-thumb:hover {{ background: #ffbe82; }}
  </style>
</head>
<body>
  {nav}
  <div class="wrap">
    <a href="oportunidades.html" style="text-decoration:none; color:var(--acc2); font-size:13px; font-weight:600; display:inline-block; margin-bottom:12px">&larr; Voltar para Oportunidades</a>
    
    <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:16px; flex-wrap:wrap; gap:12px">
      <div>
        <span class="oport-badge {badge_class}">{badge_text}</span>
        <h1 style="margin:0; font-size:26px">{emoji} {title}</h1>
        <p class="sub" style="margin:4px 0 0 0">Tese de negocio, analise territorial e leads estruturados para prospecao B2B.</p>
      </div>
      <div class="kpi-mini"><div class="v" id="count-muni">-</div><div class="l">Municipios Mapeados</div></div>
    </div>
    
    <div class="grid-desc">
      <div class="desc-card">
        <h3>💡 Por que e uma Oportunidade comercial?</h3>
        <p>{why}</p>
        <h3 style="margin-top:20px; color:var(--acc)">📍 Onde se aplica (Criterios de Filtro)</h3>
        <p>{where}</p>
      </div>
      <div class="desc-card" style="display:flex; flex-direction:column; align-items:stretch">
        <h3>📊 Top 7 Estados com maior volume de leads</h3>
        <div style="flex-grow:1; min-height:180px; position:relative">
          <canvas id="state-chart"></canvas>
        </div>
      </div>
    </div>
    
    <div id="roi-card" class="desc-card" style="display:none; margin-top:20px; border-color:#ffa657; background:linear-gradient(180deg,#1b253c,#131c30)">
      <h3 id="roi-title" style="color:#ffa657; border-bottom:1px solid rgba(255,166,87,0.2); margin-bottom:12px">🧮 Simulação de ROI</h3>
      <p style="font-size:12.5px; color:var(--mut); margin-bottom:16px">Selecione um município na tabela abaixo para simular com dados reais da praça. Ajuste as premissas:</p>
      
      <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:24px" id="roi-inputs">
      </div>
      
      <div style="margin-top:20px; padding:16px; background:rgba(255,166,87,0.05); border-radius:10px; border:1px solid rgba(255,166,87,0.15)">
        <h4 style="margin:0 0 10px; font-size:13px; color:#ffa657">Resultados da Simulação (<span id="roi-selected-city">Nenhum município selecionado</span>)</h4>
        <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:12px; font-size:13px" id="roi-results">
        </div>
      </div>
    </div>
    
    <h2>🔍 Lista de Oportunidades e Leads Corporativos Mapeados</h2>
    <div class="toolbar">
      <input id="search" placeholder="Buscar municipio..." style="min-width:260px">
      <button class="alt" onclick="table.download('csv','leads_{slug}.csv',{{bom:true}})">Exportar CSV</button>
      <button class="alt" onclick="table.download('xlsx','leads_{slug}.xlsx',{{sheetName:'Leads'}})">Exportar Excel</button>
      <span class="pill" id="table-count">Carregando...</span>
    </div>
    <div id="tbl"></div>
  </div>
  
  {modal}
  
  <script>
    const ptInt=c=>{{const v=c.getValue();return v==null?'-':Number(v).toLocaleString('pt-BR',{{maximumFractionDigits:0}});}};
    const ptDec=d=>c=>{{const v=c.getValue();return v==null?'-':Number(v).toLocaleString('pt-BR',{{minimumFractionDigits:d,maximumFractionDigits:d}});}};
    function tierFmt(c){{const v=c.getValue();const col=v==='ALTA'?'#37d7a6':v==='MEDIA'?'#f6c453':'#8aa0c0';const el=c.getElement();el.style.color=col;el.style.fontWeight=600;return v;}}
    function fmt(n){{return n==null?'-':Number(n).toLocaleString('pt-BR');}}
    
    let table, DATA=[], fuse=null, selectedMuniData=null;
    const cols=[
      {{title:"Municipio",field:"municipio_nome",headerFilter:"input",minWidth:160,widthGrow:3}},
      {{title:"UF",field:"uf",headerFilter:"list",headerFilterParams:{{valuesLookup:true,clearable:true}},hozAlign:"center",width:70}},
      {{title:"Populacao",field:"populacao",sorter:"number",hozAlign:"right",formatter:ptInt,width:100}},
      {{title:"Indice",field:"indice_oportunidade",sorter:"number",formatter:"progress",formatterParams:{{color:["#ef5f5f", "#f6a443", "#37d7a6"], min:0, max:100, legend:true}},width:115}},
      {{title:"Tier",field:"tier",headerFilter:"list",headerFilterParams:{{values:["","ALTA","MEDIA","BAIXA"]}},formatter:tierFmt,hozAlign:"center",width:90}},
      {{title:"Med/mil",field:"medicos_por_mil",sorter:"number",hozAlign:"right",formatter:ptDec(2),width:90}},
      {{title:"Enf/mil",field:"enfermeiros_por_mil",sorter:"number",hozAlign:"right",formatter:ptDec(2),width:90}},
      {{title:"Cob.%",field:"cobertura_privada_pct",sorter:"number",hozAlign:"right",formatter:ptDec(1),width:90}},
      {{title:"PIB pc",field:"pib_per_capita",sorter:"number",hozAlign:"right",formatter:ptInt,width:95}},
      {{title:"Tomografo",field:"tem_tomografo",formatter:"tickCross",hozAlign:"center",width:95,headerFilter:"tickCross",headerFilterParams:{{tristate:true}}}},
      {{title:"Sweet",field:"sweet_spot",formatter:"tickCross",hozAlign:"center",width:80,headerFilter:"tickCross",headerFilterParams:{{tristate:true}}}},
    ];
    
    fetch('oportunidade.json').then(r=>r.json()).then(res=>{{
      const allData=res.data.map(row=>{{
        let obj={{}};
        res.columns.forEach((col,idx)=>{{obj[col]=row[idx];}});
        return obj;
      }});
      
      const filtered = allData.filter(row => {{
        return {js_filter};
      }});
      
      DATA = filtered;
      document.getElementById('count-muni').textContent = fmt(filtered.length);
      document.getElementById('table-count').textContent = fmt(filtered.length) + ' municipios';
      
      renderChart(filtered);
      
      fuse=new Fuse(filtered,{{keys:['municipio_nome'],threshold:0.34,ignoreLocation:true}});
      
      table=new Tabulator('#tbl',{{
        data:filtered, layout:'fitColumns', responsiveLayout:'collapse', height:'550px',
        pagination:true, paginationSize:50, paginationSizeSelector:[25,50,100,250],
        movableColumns:true, columnDefaults:{{headerTooltip:true}},
        initialSort:[{{column:'indice_oportunidade',dir:'desc'}}], columns:cols,
        rowClick:function(e, row){{
          const rdata = row.getData();
          selectedMuniData = rdata;
          try {{
            openModal(rdata);
          }} catch(err) {{
            console.error("openModal error:", err);
          }}
          try {{
            if (typeof calcROI === "function") calcROI();
          }} catch(err) {{
            console.error("calcROI error:", err);
          }}
        }}
      }});
      
      const upd=()=>{{document.getElementById('table-count').textContent=table.getDataCount('active')+' municipios';}};
      table.on('tableBuilt',upd); table.on('dataFiltered',upd);
      
      document.getElementById('search').addEventListener('input',e=>{{
        const q=e.target.value.trim();
        if(!q){{table.clearFilter(true);return;}}
        const hits=new Set(fuse.search(q).map(h=>h.item.municipio_cod));
        table.setFilter(row=>hits.has(row.municipio_cod));
      }});
    }}).catch(e=>{{
      document.getElementById('tbl').textContent='Falha ao carregar dados: '+e;
    }});
    
    function openModal(data) {{
      document.getElementById('modal-title').textContent = data.municipio_nome;
      document.getElementById('modal-subtitle').textContent = data.uf + ' | População: ' + fmt(data.populacao);
      document.getElementById('m-idx').textContent = fmt(data.indice_oportunidade);
      document.getElementById('m-tier').textContent = data.tier;
      document.getElementById('m-tier').className = 'tier-' + data.tier;
      document.getElementById('m-sweet').textContent = data.sweet_spot ? 'Sim 🌟' : 'Não';
      document.getElementById('m-pib').textContent = data.pib_per_capita != null ? 'R$ ' + fmt(data.pib_per_capita) : '-';
      document.getElementById('m-cob').textContent = data.cobertura_privada_pct != null ? fmt(data.cobertura_privada_pct) + '%' : '-';
      document.getElementById('m-med').textContent = data.medicos_por_mil != null ? fmt(data.medicos_por_mil) : '-';
      document.getElementById('m-enf').textContent = data.enfermeiros_por_mil != null ? fmt(data.enfermeiros_por_mil) : '-';
      document.getElementById('m-tomo').textContent = data.tem_tomografo ? 'Sim ✅' : 'Não ❌';
      document.getElementById('m-leitos').textContent = data.leitos_sus_por_mil != null ? fmt(data.leitos_sus_por_mil) : '-';
      document.getElementById('m-intern').textContent = data.internacoes_por_mil != null ? fmt(data.internacoes_por_mil) : '-';
      document.getElementById('m-onco').textContent = data.apac_onco_por_mil != null ? fmt(data.apac_onco_por_mil) : '-';
      document.getElementById('m-dialise').textContent = data.apac_dialise_por_mil != null ? fmt(data.apac_dialise_por_mil) : '-';
      document.getElementById('m-acesso').textContent = data.acesso_idx != null ? fmt(data.acesso_idx) : '-';
      document.getElementById('m-evit').textContent = data.evitaveis_por_mil != null ? fmt(data.evitaveis_por_mil) : '-';
      document.getElementById('m-inf').textContent = data.mortalidade_infantil != null ? fmt(data.mortalidade_infantil) : '-';
      
      const modalRoiSec = document.getElementById('modal-roi-section');
      if (modalRoiSec) {{
        modalRoiSec.style.display = SLUG.includes('sirio') ? 'block' : 'none';
      }}
      
      document.getElementById('muni-modal').style.display = 'flex';
    }}
    
    function closeModal() {{
      document.getElementById('muni-modal').style.display = 'none';
    }}
    
    window.onclick = function(event) {{
      const modal = document.getElementById('muni-modal');
      if (event.target == modal) {{
        modal.style.display = 'none';
      }}
    }}
    
    function renderChart(data) {{
      const byUf = {{}};
      data.forEach(r => {{ byUf[r.uf] = (byUf[r.uf] || 0) + 1; }});
      const sorted = Object.entries(byUf).sort((a,b)=>b[1]-a[1]).slice(0, 7);
      
      const labels = sorted.map(x => x[0]);
      const values = sorted.map(x => x[1]);
      
      Chart.defaults.color='#8aa0c0';
      new Chart(document.getElementById('state-chart'), {{
        type: 'bar',
        data: {{
          labels: labels,
          datasets: [{{
            data: values,
            backgroundColor: '#37d7a6',
            borderColor: '#37d7a6',
            borderRadius: 4,
            borderWidth: 0
          }}]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          indexAxis: 'y',
          plugins: {{
            legend: {{ display: false }},
            tooltip: {{ enabled: true }}
          }},
          scales: {{
            x: {{ grid: {{ color: '#22304d' }} }},
            y: {{ grid: {{ display: false }} }}
          }}
        }}
      }});
    }}
    
    const SLUG = "{slug}";
    
    if (SLUG.includes("sirio")) {{
      document.getElementById("roi-card").style.display = "block";
      initROICalculator();
    }}
    
    function initROICalculator() {{
      const inputs = document.getElementById("roi-inputs");
      if (SLUG === "oportunidade-sirio-oncologia") {{
        document.getElementById("roi-title").textContent = "🧮 Simulação de ROI — Expansão Oncológica Sírio-Libanês";
        inputs.innerHTML = `
          <div>
            <label style="font-size:12px; color:var(--mut)">CAPEX Inicial: <b id="lbl-capex" style="color:#ffa657">R$ 8,0 M</b></label>
            <input type="range" id="val-capex" min="3000000" max="20000000" step="500000" value="8000000" oninput="calcROI()">
          </div>
          <div>
            <label style="font-size:12px; color:var(--mut)">Ticket Médio Anual/Paciente: <b id="lbl-ticket" style="color:#ffa657">R$ 50.000</b></label>
            <input type="range" id="val-ticket" min="20000" max="100000" step="2000" value="50000" oninput="calcROI()">
          </div>
          <div>
            <label style="font-size:12px; color:var(--mut)">Market Share Sírio: <b id="lbl-share" style="color:#ffa657">15%</b></label>
            <input type="range" id="val-share" min="5" max="50" step="1" value="15" oninput="calcROI()">
          </div>
        `;
      }} else if (SLUG === "oportunidade-sirio-corporativa") {{
        document.getElementById("roi-title").textContent = "🧮 Simulação de ROI — Saúde Corporativa Sírio-Libanês";
        inputs.innerHTML = `
          <div>
            <label style="font-size:12px; color:var(--mut)">Mensalidade/Funcionário: <b id="lbl-mensal" style="color:#ffa657">R$ 45</b></label>
            <input type="range" id="val-mensal" min="20" max="150" step="5" value="45" oninput="calcROI()">
          </div>
          <div>
            <label style="font-size:12px; color:var(--mut)">Custo Plano/Funcionário/ano: <b id="lbl-plano" style="color:#ffa657">R$ 4.800</b></label>
            <input type="range" id="val-plano" min="2000" max="12000" step="200" value="4800" oninput="calcROI()">
          </div>
          <div>
            <label style="font-size:12px; color:var(--mut)">Redução Sinistralidade: <b id="lbl-sinist" style="color:#ffa657">18%</b></label>
            <input type="range" id="val-sinist" min="5" max="30" step="1" value="18" oninput="calcROI()">
          </div>
        `;
      }} else if (SLUG === "oportunidade-sirio-diagnosticos") {{
        document.getElementById("roi-title").textContent = "🧮 Simulação de ROI — Sírio Diagnósticos (Imagem)";
        inputs.innerHTML = `
          <div>
            <label style="font-size:12px; color:var(--mut)">CAPEX Inicial: <b id="lbl-capex" style="color:#ffa657">R$ 5,0 M</b></label>
            <input type="range" id="val-capex" min="1500000" max="10000000" step="250000" value="5000000" oninput="calcROI()">
          </div>
          <div>
            <label style="font-size:12px; color:var(--mut)">Ticket Médio Exame: <b id="lbl-ticket" style="color:#ffa657">R$ 900</b></label>
            <input type="range" id="val-ticket" min="400" max="2500" step="50" value="900" oninput="calcROI()">
          </div>
          <div>
            <label style="font-size:12px; color:var(--mut)">Taxa Exame per capita/ano: <b id="lbl-taxa" style="color:#ffa657">0.15</b></label>
            <input type="range" id="val-taxa" min="0.05" max="0.40" step="0.01" value="0.15" oninput="calcROI()">
          </div>
          <div>
            <label style="font-size:12px; color:var(--mut)">Market Share Sírio: <b id="lbl-share" style="color:#ffa657">20%</b></label>
            <input type="range" id="val-share" min="5" max="50" step="1" value="20" oninput="calcROI()">
          </div>
        `;
      }}
      calcROI();
    }}
    
    function calcROI() {{
      if (!SLUG.includes("sirio")) return;
      
      const cityLabel = document.getElementById("roi-selected-city");
      const results = document.getElementById("roi-results");
      const modalResults = document.getElementById("modal-roi-results");
      
      let pop = 100000;
      let cob = 30;
      let onco = 1.2;
      
      if (selectedMuniData) {{
        cityLabel.textContent = selectedMuniData.municipio_nome + " - " + selectedMuniData.uf;
        pop = selectedMuniData.populacao;
        cob = selectedMuniData.cobertura_privada_pct || 15;
        onco = selectedMuniData.apac_onco_por_mil || 0.8;
      }} else {{
        cityLabel.textContent = "Simulação Padrão (Selecione um município abaixo)";
      }}
      
      const popPrivada = pop * (cob / 100);
      
      if (SLUG === "oportunidade-sirio-oncologia") {{
        const capex = Number(document.getElementById("val-capex").value);
        const ticket = Number(document.getElementById("val-ticket").value);
        const share = Number(document.getElementById("val-share").value);
        
        document.getElementById("lbl-capex").textContent = "R$ " + (capex / 1000000).toFixed(1) + " M";
        document.getElementById("lbl-ticket").textContent = "R$ " + fmt(ticket);
        document.getElementById("lbl-share").textContent = share + "%";
        
        const casos = popPrivada * (onco / 1000) * 1.2;
        const pacientes = casos * (share / 100);
        const receita = pacientes * ticket;
        const ebitda = receita * 0.25; 
        const roi = capex > 0 ? (ebitda / capex) * 100 : 0;
        const payback = ebitda > 0 ? capex / ebitda : 0;
        
        const html = `
          <div><div style="color:var(--mut)">Pacientes Anuais:</div><b style="font-size:16px; color:#ffa657">${{Math.round(pacientes)}}</b></div>
          <div><div style="color:var(--mut)">Faturamento/ano:</div><b style="font-size:16px; color:var(--acc)">R$ ${{((receita/1000000)).toFixed(2)}}M</b></div>
          <div><div style="color:var(--mut)">EBITDA/ano (25%):</div><b style="font-size:16px; color:var(--acc)">R$ ${{((ebitda/1000000)).toFixed(2)}}M</b></div>
          <div><div style="color:var(--mut)">ROI Anual:</div><b style="font-size:16px; color:#ffa657">${{roi.toFixed(1)}}%</b></div>
          <div><div style="color:var(--mut)">Tempo Retorno:</div><b style="font-size:16px; color:#ffa657">${{payback > 0 ? payback.toFixed(1) + ' anos' : '-'}}</b></div>
        `;
        results.innerHTML = html;
        if (modalResults) modalResults.innerHTML = html;
      }} else if (SLUG === "oportunidade-sirio-corporativa") {{
        const mensal = Number(document.getElementById("val-mensal").value);
        const plano = Number(document.getElementById("val-plano").value);
        const sinist = Number(document.getElementById("val-sinist").value);
        
        document.getElementById("lbl-mensal").textContent = "R$ " + mensal;
        document.getElementById("lbl-plano").textContent = "R$ " + fmt(plano);
        document.getElementById("lbl-sinist").textContent = sinist + "%";
        
        const custoAnualSirio = mensal * 12;
        const economiaPlano = plano * (sinist / 100);
        const economiaAbsenteismo = 250; 
        const economiaTotal = economiaPlano + economiaAbsenteismo;
        const retornoLiquido = economiaTotal - custoAnualSirio;
        const roi = custoAnualSirio > 0 ? (retornoLiquido / custoAnualSirio) * 100 : 0;
        
        const html = `
          <div><div style="color:var(--mut)">Economia Anual/Vida:</div><b style="font-size:16px; color:var(--acc)">R$ ${{Math.round(economiaTotal)}}</b></div>
          <div><div style="color:var(--mut)">Custo Sírio/Vida:</div><b style="font-size:16px; color:var(--mut)">R$ ${{Math.round(custoAnualSirio)}}</b></div>
          <div><div style="color:var(--mut)">Retorno Líquido/Vida:</div><b style="font-size:16px; color:var(--acc)">R$ ${{Math.round(retornoLiquido)}}</b></div>
          <div><div style="color:var(--mut)">ROI Contratante:</div><b style="font-size:16px; color:#ffa657">${{roi.toFixed(1)}}%</b></div>
          <div><div style="color:var(--mut)">Público Alvo (Cidade):</div><b style="font-size:15px; color:var(--txt)">${{fmt(pop)}} hab</b></div>
        `;
        results.innerHTML = html;
        if (modalResults) modalResults.innerHTML = html;
      }} else if (SLUG === "oportunidade-sirio-diagnosticos") {{
        const capex = Number(document.getElementById("val-capex").value);
        const ticket = Number(document.getElementById("val-ticket").value);
        const taxa = Number(document.getElementById("val-taxa").value);
        const share = Number(document.getElementById("val-share").value);
        
        document.getElementById("lbl-capex").textContent = "R$ " + (capex / 1000000).toFixed(2) + " M";
        document.getElementById("lbl-ticket").textContent = "R$ " + ticket;
        document.getElementById("lbl-taxa").textContent = taxa.toFixed(2);
        document.getElementById("lbl-share").textContent = share + "%";
        
        const examesCidade = popPrivada * taxa;
        const examesSirio = examesCidade * (share / 100);
        const receita = examesSirio * ticket;
        const ebitda = receita * 0.30; 
        const roi = capex > 0 ? (ebitda / capex) * 100 : 0;
        const payback = ebitda > 0 ? capex / ebitda : 0;
        
        const html = `
          <div><div style="color:var(--mut)">Exames Sírio/ano:</div><b style="font-size:16px; color:#ffa657">${{Math.round(examesSirio)}}</b></div>
          <div><div style="color:var(--mut)">Receita Anual:</div><b style="font-size:16px; color:var(--acc)">R$ ${{((receita/1000000)).toFixed(2)}}M</b></div>
          <div><div style="color:var(--mut)">EBITDA/ano (30%):</div><b style="font-size:16px; color:var(--acc)">R$ ${{((ebitda/1000000)).toFixed(2)}}M</b></div>
          <div><div style="color:var(--mut)">ROI Anual:</div><b style="font-size:16px; color:#ffa657">${{roi.toFixed(1)}}%</b></div>
          <div><div style="color:var(--mut)">Tempo Retorno:</div><b style="font-size:16px; color:#ffa657">${{payback > 0 ? payback.toFixed(1) + ' anos' : '-'}}</b></div>
        `;
        results.innerHTML = html;
        if (modalResults) modalResults.innerHTML = html;
      }}
    }}
  </script>
</body>
</html>
"""

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
        raw_rows = cur.fetchall()
        rows = []
        for r in raw_rows:
            rows.append([_jsonable(r[col]) for col in COLS])
    path = os.path.join(DOCS, "oportunidade.json")
    compact_data = {
        "columns": COLS,
        "data": rows
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(compact_data, f, ensure_ascii=False, separators=(",", ":"))
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
.tabulator-row{cursor:pointer}
.tabulator-row:hover{background-color:var(--card2) !important}
.modal-grid-tbl{width:100%;border-collapse:collapse}
.modal-grid-tbl td{border:0;padding:6px 0;font-size:13px}
.opportunity-tabs{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap;border-bottom:1px solid var(--bd);padding-bottom:16px}
.tab-btn{background:var(--card2);border:1px solid var(--bd);color:var(--mut);padding:8px 16px;border-radius:8px;cursor:pointer;font-weight:600;font-size:13px;transition:0.2s}
.tab-btn:hover{background:var(--card);color:var(--txt)}
.tab-btn.active{background:var(--acc2);color:#06101f;border-color:var(--acc2)}
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
<div class=card style="border-color:var(--acc);background:linear-gradient(180deg,#10231c,#131c30)">
  <b>Quer a base completa, um recorte por estado/segmento ou consultoria?</b>
  <!-- Para ATIVAR: crie conta gratis em https://web3forms.com e troque SUA-CHAVE-WEB3FORMS pela sua access key. -->
  <form action="https://api.web3forms.com/submit" method="POST" style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">
    <input type="hidden" name="access_key" value="SUA-CHAVE-WEB3FORMS">
    <input type="hidden" name="subject" value="Lead - WiNS Hub Saude">
    <input type="text" name="nome" placeholder="Nome" required>
    <input type="email" name="email" placeholder="E-mail" required>
    <input type="text" name="empresa" placeholder="Empresa">
    <button type="submit">Quero acesso</button>
  </form>
</div>
<h2 style="margin-top:24px;color:var(--acc)">Filtrar Oportunidades de Negócios B2B</h2>
<div class="opportunity-tabs">
  <button id="tab-all" class="tab-btn active" onclick="filterTab('all')">Todos os Municípios</button>
  <button id="tab-medtech" class="tab-btn" onclick="filterTab('medtech')">🏥 1. Equipamentos Diagnósticos</button>
  <button id="tab-telemed" class="tab-btn" onclick="filterTab('telemed')">📞 2. Telemedicina Corporativa</button>
  <button id="tab-oncology" class="tab-btn" onclick="filterTab('oncology')">🧬 3. Expansão Oncologia/Diálise</button>
  <button id="tab-staffing" class="tab-btn" onclick="filterTab('staffing')">🧑‍⚕️ 4. Outsourcing Enfermagem</button>
  <button id="tab-insurance" class="tab-btn" onclick="filterTab('insurance')">💎 5. Planos de Saúde B2B</button>
  <button id="tab-sirio-onco" class="tab-btn" style="border-color:#ffa657" onclick="filterTab('sirio-onco')">🎗️ Sírio - Oncologia Expansão</button>
  <button id="tab-sirio-corp" class="tab-btn" style="border-color:#ffa657" onclick="filterTab('sirio-corp')">💼 Sírio - Saúde Corporativa</button>
  <button id="tab-sirio-diag" class="tab-btn" style="border-color:#ffa657" onclick="filterTab('sirio-diag')">🔬 Sírio - Diagnósticos</button>
</div>
<div class=toolbar>
  <input id=search placeholder="Buscar municipio (tolerante a erro)..." style="min-width:260px">
  <button class=alt onclick="table.download('csv','oportunidade_wins_hub_saude.csv',{bom:true})">CSV</button>
  <button class=alt onclick="table.download('xlsx','oportunidade_wins_hub_saude.xlsx',{sheetName:'Oportunidade'})">Excel</button>
  <button onclick="exportPDF()">Relatorio PDF</button>
  <span class=pill id=count></span>
</div>
<div id=tbl></div>

<!-- Modal de Detalhes do Município -->
<div id="muni-modal" style="display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(11,18,32,0.85); z-index:10000; justify-content:center; align-items:center; backdrop-filter:blur(5px)">
  <div class="card" style="width:90%; max-width:650px; background:var(--card); border:2px solid var(--bd); border-radius:16px; padding:24px; position:relative; box-shadow:0 10px 30px rgba(0,0,0,0.5); max-height:90vh; overflow-y:auto">
    <button onclick="closeModal()" style="position:absolute; top:16px; right:16px; background:none; border:0; color:var(--mut); font-size:24px; cursor:pointer; line-height:1">&times;</button>
    <h2 id="modal-title" style="margin-top:0; border-left:4px solid var(--acc2); padding-left:10px; font-size:20px">Nome do Município</h2>
    <p class="sub" id="modal-subtitle">UF | População: 100.000</p>
    
    <div style="display:grid; grid-template-columns:1fr 1fr; gap:20px; margin-top:20px;">
      <div>
        <h3 style="color:var(--acc2); margin:0 0 8px; font-size:14px; border-bottom:1px solid var(--bd); padding-bottom:4px">Mercado e Índices</h3>
        <table class="modal-grid-tbl">
          <tr><td style="color:var(--mut)">Índice Oportunidade:</td><td id="m-idx" style="font-weight:bold; text-align:right">85.0</td></tr>
          <tr><td style="color:var(--mut)">Tier:</td><td id="m-tier" style="font-weight:bold; text-align:right">ALTA</td></tr>
          <tr><td style="color:var(--mut)">Sweet Spot:</td><td id="m-sweet" style="text-align:right">Sim</td></tr>
          <tr><td style="color:var(--mut)">PIB per capita:</td><td id="m-pib" style="text-align:right">R$ 45.000</td></tr>
          <tr><td style="color:var(--mut)">Cobertura Privada:</td><td id="m-cob" style="text-align:right">32%</td></tr>
        </table>
      </div>
      
      <div>
        <h3 style="color:var(--acc); margin:0 0 8px; font-size:14px; border-bottom:1px solid var(--bd); padding-bottom:4px">Infraestrutura e Oferta</h3>
        <table class="modal-grid-tbl">
          <tr><td style="color:var(--mut)">Médicos / mil hab:</td><td id="m-med" style="text-align:right">1.25</td></tr>
          <tr><td style="color:var(--mut)">Enfermeiros / mil:</td><td id="m-enf" style="text-align:right">1.80</td></tr>
          <tr><td style="color:var(--mut)">Tomógrafo:</td><td id="m-tomo" style="text-align:right">Sim</td></tr>
          <tr><td style="color:var(--mut)">Leitos SUS / mil:</td><td id="m-leitos" style="text-align:right">2.1</td></tr>
          <tr><td style="color:var(--mut)">Internações / mil:</td><td id="m-intern" style="text-align:right">75.3</td></tr>
        </table>
      </div>
    </div>

    <div style="margin-top:20px; background:var(--card2); padding:14px; border-radius:10px; border:1px solid var(--bd)">
      <h3 style="margin:0 0 8px; font-size:13px; color:var(--txt)">Procedimentos de Alta Complexidade (APAC / mil hab)</h3>
      <div style="display:flex; justify-content:space-between; font-size:13px">
        <span>Oncologia: <b id="m-onco">0.5</b></span>
        <span>Diálise: <b id="m-dialise">1.2</b></span>
        <span>Acesso Leitos: <b id="m-acesso">2.4</b></span>
      </div>
    </div>
    
    <div style="margin-top:16px; background:rgba(239,95,95,0.06); padding:14px; border-radius:10px; border:1px solid rgba(239,95,95,0.2)">
      <h3 style="margin:0 0 8px; font-size:13px; color:var(--red)">Indicadores de Mortalidade e Saúde</h3>
      <div style="display:flex; justify-content:space-between; font-size:13px">
        <span>Óbitos Evitáveis: <b id="m-evit" style="color:var(--red)">12.5</b> /mil hab</span>
        <span>Mortalidade Infantil: <b id="m-inf" style="color:var(--red)">14.2</b> /mil nasc.</span>
      </div>
    </div>
  </div>
</div>

</div>
<script>
const ptInt=c=>{const v=c.getValue();return v==null?'-':Number(v).toLocaleString('pt-BR',{maximumFractionDigits:0});};
const ptDec=d=>c=>{const v=c.getValue();return v==null?'-':Number(v).toLocaleString('pt-BR',{minimumFractionDigits:d,maximumFractionDigits:d});};
function tierFmt(c){const v=c.getValue();const col=v==='ALTA'?'#37d7a6':v==='MEDIA'?'#f6c453':'#8aa0c0';const el=c.getElement();el.style.color=col;el.style.fontWeight=600;return v;}
let table, DATA=[], fuse=null, chTier=null, chUf=null, chScatter=null;
const cols=[
 {title:"Municipio",field:"municipio_nome",headerFilter:"input",minWidth:160,widthGrow:3},
 {title:"UF",field:"uf",headerFilter:"list",headerFilterParams:{valuesLookup:true,clearable:true},hozAlign:"center",width:70},
 {title:"População",field:"populacao",sorter:"number",hozAlign:"right",formatter:ptInt,width:100},
 {title:"Índice",field:"indice_oportunidade",sorter:"number",formatter:"progress",formatterParams:{color:["#ef5f5f", "#f6a443", "#37d7a6"], min:0, max:100, legend:true},width:115},
 {title:"Tier",field:"tier",headerFilter:"list",headerFilterParams:{values:["","ALTA","MEDIA","BAIXA"]},formatter:tierFmt,hozAlign:"center",width:90},
 {title:"Med/mil",field:"medicos_por_mil",sorter:"number",hozAlign:"right",formatter:ptDec(2),width:90},
 {title:"Enf/mil",field:"enfermeiros_por_mil",sorter:"number",hozAlign:"right",formatter:ptDec(2),width:90},
 {title:"Cob.%",field:"cobertura_privada_pct",sorter:"number",hozAlign:"right",formatter:ptDec(1),width:90},
 {title:"PIB pc",field:"pib_per_capita",sorter:"number",hozAlign:"right",formatter:ptInt,width:95},
 {title:"Tomógrafo",field:"tem_tomografo",formatter:"tickCross",hozAlign:"center",width:95,headerFilter:"tickCross",headerFilterParams:{tristate:true}},
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
fetch('oportunidade.json').then(r=>r.json()).then(res=>{
  const d=res.data.map(row=>{
    let obj={};
    res.columns.forEach((col,idx)=>{obj[col]=row[idx];});
    return obj;
  });
  DATA=d; miniKpis(d); charts(d);
  fuse=new Fuse(d,{keys:['municipio_nome'],threshold:0.34,ignoreLocation:true});
  table=new Tabulator('#tbl',{
    data:d, layout:'fitColumns', responsiveLayout:'collapse', height:'620px',
    pagination:true, paginationSize:50, paginationSizeSelector:[25,50,100,250],
    movableColumns:true, columnDefaults:{headerTooltip:true},
    initialSort:[{column:'indice_oportunidade',dir:'desc'}], columns:cols,
    rowClick:function(e, row){
      openModal(row.getData());
    }
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

function filterTab(mode) {
  document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
  document.getElementById('tab-' + mode).classList.add('active');
  
  if (!table) return;
  table.clearFilter();
  
  if (mode === 'all') {
    // Sem filtro
  } else if (mode === 'medtech') {
    // Equipamentos Diagnósticos: tem_tomografo === false && populacao > 25000 && cobertura_privada_pct > 15
    table.setFilter([
      {field: "tem_tomografo", type: "==", value: false},
      {field: "populacao", type: ">", value: 25000},
      {field: "cobertura_privada_pct", type: ">", value: 15}
    ]);
  } else if (mode === 'telemed') {
    // Telemedicina Corporativa: medicos_por_mil < 0.5 && populacao > 15000
    table.setFilter([
      {field: "medicos_por_mil", type: "<", value: 0.5},
      {field: "populacao", type: ">", value: 15000}
    ]);
  } else if (mode === 'oncology') {
    // Expansão Oncologia/Diálise: apac_dialise_por_mil > 1.5 ou apac_onco_por_mil > 1.0
    table.setFilter(row => {
      return (row.apac_dialise_por_mil > 1.5) || (row.apac_onco_por_mil > 1.0);
    });
  } else if (mode === 'staffing') {
    // Outsourcing Enfermagem: enfermeiros_por_mil < 1.0 && internacoes_por_mil > 50
    table.setFilter([
      {field: "enfermeiros_por_mil", type: "<", value: 1.0},
      {field: "internacoes_por_mil", type: ">", value: 50}
    ]);
  } else if (mode === 'insurance') {
    // Planos de Saúde B2B: pib_per_capita > 35000 && cobertura_privada_pct < 10
    table.setFilter([
      {field: "pib_per_capita", type: ">", value: 35000},
      {field: "cobertura_privada_pct", type: "<", value: 10}
    ]);
  } else if (mode === 'sirio-onco') {
    // Sírio Oncologia: apac_onco_por_mil > 0.8 && cobertura_privada_pct > 25 && pib_per_capita > 40000
    table.setFilter([
      {field: "apac_onco_por_mil", type: ">", value: 0.8},
      {field: "cobertura_privada_pct", type: ">", value: 25.0},
      {field: "pib_per_capita", type: ">", value: 40000}
    ]);
  } else if (mode === 'sirio-corp') {
    // Sírio Saúde Corporativa: medicos_por_mil < 1.0 (deserto/baixa) && pib_per_capita > 45000
    table.setFilter([
      {field: "medicos_por_mil", type: "<", value: 1.0},
      {field: "pib_per_capita", type: ">", value: 45000}
    ]);
  } else if (mode === 'sirio-diag') {
    // Sírio Diagnósticos: populacao > 40000 && cobertura_privada_pct > 30.0 && tem_tomografo === false
    table.setFilter([
      {field: "populacao", type: ">", value: 40000},
      {field: "cobertura_privada_pct", type: ">", value: 30.0},
      {field: "tem_tomografo", type: "==", value: false}
    ]);
  }
}

function openModal(data) {
  document.getElementById('modal-title').textContent = data.municipio_nome;
  document.getElementById('modal-subtitle').textContent = data.uf + ' | População: ' + fmt(data.populacao);
  document.getElementById('m-idx').textContent = fmt(data.indice_oportunidade);
  document.getElementById('m-tier').textContent = data.tier;
  document.getElementById('m-tier').className = 'tier-' + data.tier;
  document.getElementById('m-sweet').textContent = data.sweet_spot ? 'Sim 🌟' : 'Não';
  document.getElementById('m-pib').textContent = data.pib_per_capita != null ? 'R$ ' + fmt(data.pib_per_capita) : '-';
  document.getElementById('m-cob').textContent = data.cobertura_privada_pct != null ? fmt(data.cobertura_privada_pct) + '%' : '-';
  document.getElementById('m-med').textContent = data.medicos_por_mil != null ? fmt(data.medicos_por_mil) : '-';
  document.getElementById('m-enf').textContent = data.enfermeiros_por_mil != null ? fmt(data.enfermeiros_por_mil) : '-';
  document.getElementById('m-tomo').textContent = data.tem_tomografo ? 'Sim ✅' : 'Não ❌';
  document.getElementById('m-leitos').textContent = data.leitos_sus_por_mil != null ? fmt(data.leitos_sus_por_mil) : '-';
  document.getElementById('m-intern').textContent = data.internacoes_por_mil != null ? fmt(data.internacoes_por_mil) : '-';
  document.getElementById('m-onco').textContent = data.apac_onco_por_mil != null ? fmt(data.apac_onco_por_mil) : '-';
  document.getElementById('m-dialise').textContent = data.apac_dialise_por_mil != null ? fmt(data.apac_dialise_por_mil) : '-';
  document.getElementById('m-acesso').textContent = data.acesso_idx != null ? fmt(data.acesso_idx) : '-';
  document.getElementById('m-evit').textContent = data.evitaveis_por_mil != null ? fmt(data.evitaveis_por_mil) : '-';
  document.getElementById('m-inf').textContent = data.mortalidade_infantil != null ? fmt(data.mortalidade_infantil) : '-';
  
  document.getElementById('muni-modal').style.display = 'flex';
}

function closeModal() {
  document.getElementById('muni-modal').style.display = 'none';
}

// Fechar modal ao clicar fora
window.onclick = function(event) {
  const modal = document.getElementById('muni-modal');
  if (event.target == modal) {
    modal.style.display = 'none';
  }
}
</script>
</body></html>"""


def gerar_oportunidade():
    html = OPORT_PAGE.replace("{{nav}}", NAV).replace("{{style}}", STYLE)
    html = inject_head(html, "Indice de Oportunidade", "oportunidade.html")
    with open(os.path.join(DOCS, "oportunidade.html"), "w", encoding="utf-8") as f:
        f.write(minify(html))
    print(f"  oportunidade.html: {len(html)/1024:.0f} KB (Tabulator+Chart.js+Fuse+jsPDF)")


def gerar_oportunidades_main():
    html = render("Oportunidades", oportunidades_body())
    html = inject_head(html, "Oportunidades de Negócios B2B", "oportunidades.html")
    with open(os.path.join(DOCS, "oportunidades.html"), "w", encoding="utf-8") as f:
        f.write(minify(html))
    print(f"  oportunidades.html: {len(html)/1024:.0f} KB")


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


def gerar_topojson():
    """Gera o TopoJSON (mapshaper/Node) ~67% menor que o GeoJSON p/ o mapa.
    Best-effort: so roda se o .topojson nao existir e houver Node/npx."""
    topo = os.path.join(DOCS, "municipios_br.topojson")
    if os.path.exists(topo):
        print(f"  municipios_br.topojson: ja existe ({os.path.getsize(topo)/1024/1024:.1f} MB)")
        return
    try:
        subprocess.run(["npx", "-y", "mapshaper", "municipios_br.geojson",
                        "-simplify", "18%", "keep-shapes",
                        "-o", "format=topojson", "quantization=1e5", "municipios_br.topojson"],
                       cwd=DOCS, check=True, capture_output=True, timeout=300,
                       shell=(os.name == "nt"))
        print(f"  municipios_br.topojson gerado ({os.path.getsize(topo)/1024/1024:.1f} MB)")
    except Exception as e:
        print(f"  (topojson nao regenerado: {e}; mapa usara o existente/geojson)")


# Pagina do MAPA COROPLETICO: Leaflet pinta cada municipio por tier/indice,
# casando a malha do IBGE (por codigo) com oportunidade.json. Renderer canvas.
MAPA_PAGE = """<!doctype html><html lang=pt-BR><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Mapa de Oportunidade - WiNS Hub Saude</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/topojson-client@3"></script>
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
  fetch('municipios_br.topojson').then(r=>r.json())
]).then(([res,topo])=>{
  res.data.forEach(row=>{
    let r={};
    res.columns.forEach((col,idx)=>{r[col]=row[idx];});
    DMAP.set(String(r.municipio_cod),r);
  });
  const obj=topo.objects[Object.keys(topo.objects)[0]];
  const geo=topojson.feature(topo,obj);
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


PAGINAS = ["index.html", "oportunidade.html", "mapa.html", "oportunidades.html"] + [f"{o['slug']}.html" for o in OPORTUNIDADES_METADATA]


def gerar_oportunidades_detalhadas():
    print("  Gerando paginas de oportunidades detalhadas ...")
    for o in OPORTUNIDADES_METADATA:
        # substitui as variáveis no template
        html = OPORT_DETAIL_PAGE.format(
            title=o["title"],
            emoji=o["emoji"],
            badge_text=o["badge_text"],
            badge_class=o["badge_class"],
            why=o["why"],
            where=o["where"],
            js_filter=o["js_filter"],
            slug=o["slug"],
            style=STYLE,
            nav=NAV,
            modal=MODAL_HTML
        )
        html = inject_head(html, o["title"], f"{o['slug']}.html")
        filename = os.path.join(DOCS, f"{o['slug']}.html")
        with open(filename, "w", encoding="utf-8") as f:
            f.write(minify(html))
        print(f"    -> {o['slug']}.html gerado.")


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
    p404 = p404.replace("{{nav}}", NAV).replace("{{style}}", STYLE).replace("</head>", ANALYTICS + "</head>")
    with open(os.path.join(DOCS, "404.html"), "w", encoding="utf-8") as f:
        f.write(minify(p404))
    print("  seo: robots.txt, sitemap.xml, 404.html, .nojekyll")


if __name__ == "__main__":
    print("Gerando site estatico em docs/ ...")
    gerar_assets()
    gerar_malha()
    gerar_topojson()
    gerar_dados()
    gerar_index()
    gerar_oportunidade()
    gerar_mapa()
    gerar_oportunidades_main()
    gerar_oportunidades_detalhadas()
    gerar_seo()
    print("OK. Publique com: git add -A && git commit -m 'site' && git push")
