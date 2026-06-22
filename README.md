# WiNS Hub Saúde

Inteligência territorial de saúde no Brasil: **índice de oportunidade de investimento por município**, cruzando carência assistencial × demanda × mercado pagante × infraestrutura. Tudo agregado por município (sem PII).

## 🌐 Site ao vivo
**https://willahc.github.io/WiNS-Hub-Sa-de/**

| Página | Descrição |
|---|---|
| [Dashboard](https://willahc.github.io/WiNS-Hub-Sa-de/) | Mapa de desertos (médico/enfermagem/oportunidade) com clustering, KPIs e gráficos |
| [Índice de Oportunidade](https://willahc.github.io/WiNS-Hub-Sa-de/oportunidade.html) | Tabela interativa (filtros, busca fuzzy, ordenação) + gráficos + export **CSV/Excel/PDF** |
| [Mapa](https://willahc.github.io/WiNS-Hub-Sa-de/mapa.html) | Coroplético: cada município pintado por tier/índice (malha IBGE) |
| [Para quem vender](https://willahc.github.io/WiNS-Hub-Sa-de/vender.html) | Segmentos de comprador B2B |

## 🧱 Como funciona
- **Banco** PostgreSQL local (`wins_hub_saude`) com as camadas agregadas por município (índice de oportunidade, densidades, mercado ANS, perfil IBGE).
- **App Flask** (`wins_hub_app.py`) serve as páginas ao vivo localmente (`http://localhost:5000`).
- **Gerador estático** (`wins_hub_saude_static_site.py`) exporta tudo para `docs/` (snapshot) — é o que o **GitHub Pages** publica de graça, sem servidor.

## 🔄 Atualizar o site
```bash
python wins_hub_saude_dashboard.py     # regenera o dashboard (se mudaram dados)
python wins_hub_saude_static_site.py   # regenera docs/ (páginas, JSON, mapa, SEO)
git add -A && git commit -m "atualiza" && git push
```
O Pages reconstrói sozinho em ~1 min. (Há automação opcional em [AUTOMACAO.md](AUTOMACAO.md).)

## 🛠️ Stack (tudo grátis / CDN)
Tabulator · Chart.js · Leaflet (+ markercluster) · Fuse.js · jsPDF · SheetJS · malha municipal IBGE · GitHub Pages + Actions.

## 📊 Fontes
CNES/DATASUS, ANS, IBGE (PIB/Censo/malha), Receita Federal (QSA/decisor B2B).

## ⚠️ Aviso
Camadas vendáveis = **agregado territorial** + **decisor B2B (PJ)**. Não há cadastro nominal em massa de profissionais (PF) — restrição assumida por finalidade/LGPD.
