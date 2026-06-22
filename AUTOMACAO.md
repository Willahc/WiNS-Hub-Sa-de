# Automação de publicação (opcional)

O workflow [`.github/workflows/publish.yml`](.github/workflows/publish.yml) regenera o site a partir do **PostgreSQL local** e publica sozinho — manualmente (1 clique) ou agendado (segunda 09:00 UTC).

Como a nuvem do GitHub **não enxerga seu banco local**, ele roda num **runner self-hosted** (um agente na sua máquina). Setup único (~5 min):

## 1) Instalar o runner self-hosted (grátis)
No GitHub: **Settings → Actions → Runners → New self-hosted runner → Windows**, e siga os comandos mostrados (algo como):

```powershell
mkdir actions-runner; cd actions-runner
# baixe o pacote indicado na página, depois:
./config.cmd --url https://github.com/Willahc/WiNS-Hub-Sa-de --token <TOKEN_DA_PAGINA>
./run.cmd          # ou instale como serviço: ./svc.sh install
```

> Deixe o runner rodando (ou instale como serviço do Windows). Ele precisa de `python` no PATH com acesso ao Postgres local.

## 2) Cadastrar os secrets (credenciais do banco)
O workflow recria o `.env.saude` a partir de secrets criptografados do repositório. Defina (Settings → Secrets and variables → Actions → New repository secret), **ou** pelo terminal:

```powershell
gh secret set DATABASE_URL
gh secret set SUPERUSER_URL
gh secret set RFB_SHARE_TOKEN
```
(o `gh` pede o valor sem exibir na tela). Use os mesmos valores do seu `.env.saude` local.

## 3) Rodar
**Actions → Publicar site (self-hosted) → Run workflow.** Ele regenera dashboard + site e dá push; o Pages publica em ~1 min. O `.env.saude` recriado **não** é versionado (está no `.gitignore`).

---
### Workflow de qualidade (já ativo, roda na nuvem grátis)
[`.github/workflows/quality.yml`](.github/workflows/quality.yml) roda a cada push: **verificador de links** (lychee) e **Lighthouse** (performance/SEO/acessibilidade), com relatório no log da Action. Não precisa de runner nem secrets.
