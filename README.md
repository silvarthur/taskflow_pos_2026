# TaskFlow (App de Exemplo) — Disciplina DevSecOps

Esta é a aplicação de laboratório usada em **todos os encontros práticos** da
disciplina. É um gerenciador de tarefas (to-do list) simples em Python/Flask,
propositalmente vulnerável, que evolui ao longo do curso

## ⚠️ Aviso importante

Este código contém vulnerabilidades **intencionais** para fins didáticos
(ver comentários `# FALHA` e docstring no topo de `app.py`). Nunca:

- Use este código como referência de boas práticas.
- Implante esta aplicação em ambiente de produção ou exposto à internet.
- Reutilize o padrão de código (concatenação de SQL, senhas em texto puro,
  segredo hardcoded) em projetos reais.

## Como executar localmente o Docker Compose

Execute os comandos na raiz do repositório:

```bash
cp .env.example .env
```

Edite `.env` e substitua os valores de exemplo, especialmente
`POSTGRES_PASSWORD` e `SECRET_KEY`. O arquivo `.env` é ignorado pelo Git e não
deve ser commitado.

```bash
docker compose up -d --build --wait
```

A aplicação estará disponível em <http://localhost:5000>. O banco é
inicializado com os usuários de demonstração:

| Usuário | Senha |
|---------|-------|
| admin   | admin123 |
| aluno   | senha123 |

Para acompanhar os serviços e seus logs:

```bash
docker compose ps
docker compose logs -f
```

Para parar os serviços, mantendo os dados persistidos:

```bash
docker compose down
```

O Compose também define healthchecks para o banco e para a aplicação; o
serviço web aguarda o banco ficar saudável antes de iniciar. O volume de dados
é removido somente se `docker compose down -v` for executado.

## Workflows do GitHub Actions

Os workflows ficam em `.github/workflows/`. Os disparadores abaixo refletem a
configuração atual:

| Workflow | O que executa | Como é disparado |
|----------|---------------|------------------|
| `ci.yml` — CI Básico - TaskFlow | Instala dependências e verifica o código com Flake8. | `push` e `pull_request` direcionados à branch `main`; também pode ser iniciado manualmente (`workflow_dispatch`). |
| `container.yml` — Container - Build e Trivy | Constrói a imagem Docker e verifica vulnerabilidades HIGH e CRITICAL com Trivy. | `push` e `pull_request` direcionados à branch `main`; também pode ser iniciado manualmente (`workflow_dispatch`). |
| `sast.yml` — SAST e Secret Scanning | Executa Semgrep e Gitleaks. | `push` e `pull_request` em qualquer branch; não possui disparo manual configurado. |
| `sca.yml` — SCA - Auditoria de Dependências | Executa `pip-audit` sobre `requirements.txt`. | `push` e `pull_request` em qualquer branch; não possui disparo manual configurado. |
| `dast.yml` — DAST - OWASP ZAP Baseline Scan | Sobe a aplicação com Docker Compose, aguarda a resposta HTTP e executa o ZAP Baseline Scan. | `push` e `pull_request` direcionados à branch `main`; também pode ser iniciado manualmente (`workflow_dispatch`). |

Os workflows com disparo manual podem ser iniciados em **GitHub → Actions**:
selecione o workflow e clique em **Run workflow**. Também é possível usar a
CLI do GitHub, por exemplo:

```bash
gh workflow run ci.yml --ref main
gh workflow run container.yml --ref main
gh workflow run dast.yml --ref main
```

Para executar `sast.yml` ou `sca.yml`, faça push de commits ou abra/atualize um
pull request em qualquer branch; esses arquivos não declaram
`workflow_dispatch`.

## Estrutura do repositório

```text
.
├── app.py                       # Aplicação Flask
├── templates/                   # Templates HTML do app
├── requirements.txt             # Dependências da aplicação
├── Dockerfile                   # Imagem da aplicação
├── docker-compose.yml           # Aplicação, PostgreSQL, redes e volume
├── .env.example                 # Modelo das variáveis de ambiente
├── .github/workflows/           # CI, container, SAST, SCA e DAST
├── observabilidade/             # Stack Loki + Promtail + Grafana (Módulo 6)
├── scripts/                     # bruteforce.py — simulação de ataque
├── codigo/                      # Exemplos e materiais dos encontros
└── docs/                        # Hardening, observabilidade e runbook
```

## Arquitetura da aplicação

```text
                         Host
              http://localhost:5000
                          |
                          v
               +---------------------+
               | web                 |
               | Flask + Gunicorn    |
               | porta 5000          |
               +---------------------+
                  |              |
       frontend   |              | backend
       (bridge)   |              | (bridge)
                  |              v
                  |      +---------------------+
                  |      | db                  |
                  |      | PostgreSQL 16       |
                  |      | porta 5432          |
                  |      +---------------------+
                  |              |
                  |              v
                  |      volume taskflow_db_data
                  |
                  v
          volume taskflow_logs  (lido pela stack de observabilidade,
                                  ver seção seguinte)
```

O serviço `web` publica a porta 5000 para acesso pelo navegador e participa
das redes `frontend` e `backend`. O serviço `db` não publica portas no host:
ele só é acessado pelo `web`, usando o nome `db` e a porta 5432 pela rede
`backend`. Os dados do PostgreSQL persistem no volume nomeado
`taskflow_db_data`.

## Observabilidade (Módulo 6)

Stack separada (`observabilidade/docker-compose.yml`), ligada à aplicação
apenas pelo volume nomeado `taskflow_logs`, que o serviço `web` grava e o
Promtail lê como somente leitura. Não há rede compartilhada entre os dois
Composes.

```text
 docker-compose.yml (raiz)          observabilidade/docker-compose.yml
┌─────────────────────┐            ┌──────────────────────────────────┐
│ web                 │            │  rede: observabilidade (bridge)   │
│ grava JSON em       │            │                                    │
│ /app/logs           │            │  promtail ──> loki ──> grafana    │
└──────────┬──────────┘            │  (lê :ro)   (armazena)  (consulta)│
           │                       └──────────────────────┬───────────┘
           v                                               │
   volume taskflow_logs ─────────────────── montado :ro ───┘
                                        Grafana: http://localhost:3000
                                        Loki:    http://127.0.0.1:3100
```

Como subir, depois da aplicação já estar no ar:

```bash
cp observabilidade/.env.example observabilidade/.env   # defina a senha do Grafana
docker compose -f observabilidade/docker-compose.yml up -d --wait
```

Acesse o Grafana em <http://localhost:3000> (usuário e senha definidos em
`observabilidade/.env`) e use **Explore** com a fonte de dados **Loki**, já
provisionada automaticamente.

Para parar a stack:

```bash
docker compose -f observabilidade/docker-compose.yml down
```

Documentação relacionada:

- [docs/observabilidade.md](docs/observabilidade.md) — eventos logados e consultas LogQL
- [docs/runbook-login-bruteforce.md](docs/runbook-login-bruteforce.md) — resposta a incidente de força bruta no login
- [scripts/bruteforce.py](scripts/bruteforce.py) — simula o ataque usado para testar a detecção
