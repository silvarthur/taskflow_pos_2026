# Observabilidade da TaskFlow

Logs estruturados em JSON, coletados pelo Promtail, armazenados no Loki e consultados no Grafana.

```
web (grava JSON em /app/logs) -> volume taskflow_logs -> promtail (lê :ro) -> loki -> grafana
```

## Como subir

```bash
# 1. Aplicação (cria o volume taskflow_logs)
docker compose up -d --build --wait

# 2. Stack de observabilidade
cp observabilidade/.env.example observabilidade/.env   # defina a senha do Grafana
docker compose -f observabilidade/docker-compose.yml up -d
```

Grafana em http://localhost:3000, menu **Explore**, fonte de dados **Loki** (já provisionada). Ajuste o intervalo de tempo para os últimos 15 minutos.

## O que é logado

Cada linha do log é um objeto JSON. Todo evento de uma requisição carrega o mesmo `request_id`, que também volta ao cliente no header `X-Request-ID`.

| Evento | Quando | Campos principais |
|---|---|---|
| `http_request` | Fim de toda requisição | `method`, `path`, `status_code`, `duration_ms`, `user`, `ip` |
| `login_attempt` | Login com sucesso ou falha | `result` (`success`/`failure`), `username`, `ip` |
| `logout` | Saída do usuário | `username`, `ip` |
| `tasks_list_accessed` | Listagem ou busca de tarefas | `username`, `search_term`, `results_count` |
| `task_created` | Criação de tarefa | `username`, `title`, `description_length` |
| `app_startup` | Container iniciado | |
| `db_connect_retry`, `unhandled_exception` | Falhas internas | `attempt`, `error` / traceback |

**Nunca entram nos logs:** senha, cookie, token, corpo de formulário e o texto da descrição da tarefa. A consulta 5 abaixo comprova isso.

**Labels do Loki:** apenas `job`, `app`, `level` e `event` (poucos valores distintos). Campos como `ip`, `username` e `request_id` têm muitos valores e por isso não são labels: as consultas os extraem com `| json`.

## Consultas LogQL

Todas foram executadas contra o Loki com tráfego real da aplicação (12 logins falhos de `admin`, 3 de outros usuários, 1 login válido, criação e busca de tarefa, 1 rota inexistente).

### 1. Falhas de login por IP

**Pergunta:** de quais IPs vêm as tentativas de login que falham? É a consulta de detecção de força bruta.

```logql
sum by (ip) (count_over_time({job="taskflow", event="login_attempt"} | json | result="failure" [5m]))
```

Resultado do teste: `{ip="172.21.0.1"}` = 15 (12 de `admin` + 3 de outros usuários). Para listar só IPs acima de um limite, acrescente `> 10` ao final.

### 2. Seguir uma requisição pelo `request_id`

**Pergunta:** o que aconteceu em uma requisição específica, do início ao fim? Útil a partir do `X-Request-ID` que o cliente recebeu.

```logql
{job="taskflow"} |= "<request_id>" | json | request_id="<request_id>"
```

O filtro `|= "<request_id>"` acelera a busca por filtrar o texto antes de interpretar o JSON. Resultado do teste: dois eventos, `http_request` (`POST /tasks/new`, status 302) e `task_created`, com o mesmo `request_id`.

### 3. Erros HTTP e latência por rota

**Pergunta:** quais rotas estão devolvendo erro e quais estão lentas?

Erros (status 400 ou mais), ignorando o healthcheck do Compose (`127.0.0.1`):

```logql
sum by (path, status_code) (count_over_time({job="taskflow", event="http_request"} | json | ip != "127.0.0.1" | status_code >= 400 [5m]))
```

Latência p95 por rota, em milissegundos:

```logql
quantile_over_time(0.95, {job="taskflow", event="http_request"} | json | ip != "127.0.0.1" | unwrap duration_ms [5m]) by (path)
```

Resultado do teste: um erro, `/nao-existe` com 404. O `/login` foi a rota mais lenta (p95 de aproximadamente 193 ms), por causa da verificação do hash da senha. Para só erros de servidor, use `status_code >= 500`.

### 4. Usuários mais atacados

**Pergunta:** quais contas estão sendo alvo de tentativas falhas?

```logql
topk(5, sum by (username) (count_over_time({job="taskflow", event="login_attempt"} | json | result="failure" [5m])))
```

Resultado do teste: `admin` = 12, `guest` = 2, `root` = 1.

### 5. Verificar que nada sensível foi logado

**Pergunta:** algum log contém senha, cookie ou token? O resultado esperado é vazio.

```logql
{job="taskflow"} |~ "(?i)password|senha|cookie|token"
```

Resultado do teste: 0 linhas, mesmo com senhas de teste enviadas nos logins falhos.

## Notas

- **IP de origem:** `ip` vem de `request.remote_addr`. Com a aplicação em Docker, o valor visto é o gateway da rede (`172.x.0.1`), não o IP real do cliente. A aplicação não usa o header `X-Forwarded-For`, porque o cliente pode forjá-lo. Atrás de um proxy confiável, o proxy deve fornecer o IP real e a aplicação deve confiar apenas nele.
- **Healthcheck:** o Compose faz `GET /login` a cada 30 segundos a partir de `127.0.0.1`. Esse tráfego aparece como `http_request` e é filtrado nas consultas de erro e latência.
- **Retenção:** os dados do Loki ficam dentro do container; recriá-lo apaga os logs.
