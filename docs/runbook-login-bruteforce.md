# Runbook: força bruta no login

Resposta a um volume anormal de falhas de autenticação na TaskFlow, seguindo as quatro fases do NIST SP 800-61 (Computer Security Incident Handling Guide).

## Condição de disparo

**Mais de 10 falhas de login do mesmo IP em uma janela de 5 minutos.**

O limite de 10 foi escolhido por ser superior ao número de erros de digitação esperado de um usuário legítimo (2 a 3 tentativas) e ainda assim baixo o suficiente para pegar um ataque automatizado nos primeiros segundos — confirmado no ensaio da Seção 4: 26 falhas em menos de 10 segundos.

## Query de detecção

A mesma consulta documentada em [docs/observabilidade.md](observabilidade.md#1-falhas-de-login-por-ip), executada no Grafana (Explore → Loki) ou direto na API do Loki:

```logql
sum by (ip) (count_over_time({job="taskflow", event="login_attempt"} | json | result="failure" [5m])) > 10
```

Um alerta do Grafana sobre essa mesma query, avaliado a cada 1 minuto com o limiar `> 10`, dispara este runbook automaticamente (ver seção "Alerta" ao final).

## Papéis e responsabilidades

| Papel | Responsabilidade neste incidente |
|---|---|
| Quem detecta | Monitora o Grafana ou recebe o alerta; abre o incidente |
| Quem responde | Executa a contenção (bloqueio de IP, invalidação de sessão) |
| Dono da aplicação | Decide sobre reset de senha e comunicação aos usuários afetados |
| Registro | Preenche este runbook com os dados reais do incidente |

Em um grupo pequeno, a mesma pessoa pode acumular os quatro papéis; o importante é que cada passo abaixo seja executado por alguém e registrado.

## Checklist de evidências

Antes de encerrar o incidente, confirmar que foram coletados:

- [ ] Print ou export da query de detecção no Grafana, com o IP e a contagem de falhas
- [ ] IP de origem, janela de tempo (início e fim) e usuário(s) alvo
- [ ] Resultado: o ataque teve sucesso (login válido) ou só falhas?
- [ ] Ação de contenção tomada e horário
- [ ] Se houve sucesso: lista de sessões/senhas invalidadas

## As quatro fases (NIST SP 800-61)

### 1. Preparação

O que já está em vigor antes de qualquer ataque acontecer:

- Logging estruturado em JSON com `request_id`, `username`, `ip` e `result` em todo evento de login (`app.py`), sem senha, cookie ou token — ver [docs/observabilidade.md](observabilidade.md)
- Stack Grafana + Loki + Promtail no ar, com a query de detecção acima salva e testada
- Este runbook, revisado pelo grupo antes do incidente real
- Hashing de senha com `werkzeug.security` (nunca texto puro), herdado do Módulo 2/3

### 2. Detecção e Análise

1. Identificar o IP de origem e o(s) usuário(s) alvo com a query de detecção
2. Confirmar se alguma tentativa teve sucesso, com a mesma query trocando `result="failure"` por `result="success"` no mesmo IP e janela
3. Rastrear uma requisição específica pelo `request_id` (consulta 2 de `observabilidade.md`) para ver a sequência completa de eventos
4. Classificar a severidade:
   - Só falhas → tentativa de força bruta sem sucesso
   - Falhas seguidas de sucesso → **credencial comprometida**, severidade alta

### 3. Contenção, Erradicação e Recuperação

Ações em ordem de urgência:

1. **Conter:** bloquear o IP de origem (regra de firewall ou, em produção, no proxy/WAF na frente da aplicação)
2. **Erradicar:** se houve sucesso, encerrar a sessão da conta afetada (`session.clear()` do lado do servidor já invalida sessões ativas ao reiniciar o segredo, ou revogar manualmente) e forçar reset de senha
3. **Recuperar:** confirmar com uma nova consulta que as falhas do IP pararam; restaurar acesso normal à conta afetada após o reset

### 4. Atividades Pós-Incidente

1. Registrar o incidente neste documento (ver registro abaixo)
2. Avaliar se o limiar de disparo (10 falhas / 5 min) continua adequado
3. Discutir melhorias: bloqueio automático após N falhas (rate limiting), MFA, lista de IPs permitidos para contas administrativas
4. Compartilhar o aprendizado com o grupo antes do próximo incidente

## Registro do incidente simulado

Preenchido com o ataque real executado por [scripts/bruteforce.py](../scripts/bruteforce.py) durante a Seção 4, evidências em [docs/evidencias/](evidencias/).

| Campo | Valor |
|---|---|
| Data | 2026-10-28 |
| Início | 19:17:20 UTC |
| Fim | 19:17:29 UTC |
| Duração | ~9 segundos |
| IP de origem | `172.21.0.1` (gateway da rede Docker — ver nota sobre IP real em `observabilidade.md`) |
| Usuário alvo | `admin` |
| Tentativas falhas | 26 |
| Resultado | 1 login com sucesso após as 26 falhas (senha de laboratório `admin123`) |
| Severidade | Alta — credencial comprometida no ensaio |
| Detectado por | Query de detecção, confirmada em `docs/evidencias/falhas-por-ip.json` |
| Contenção aplicada | Nenhuma (ambiente de laboratório); em produção: bloqueio do IP e reset da senha de `admin` |
| Fases executadas | Detecção e Análise concluídas (fases 1 e 4 não exigem ação sobre este incidente específico, além do registro) |

## Alerta (opcional)

Para disparar este runbook automaticamente, provisionar no Grafana uma regra de alerta com:

- Query: a mesma da seção "Query de detecção"
- Condição: `> 10`
- Avaliação: a cada 1 minuto, por 1 minuto consecutivo
- Notificação: canal do grupo (e-mail, Slack ou similar)
