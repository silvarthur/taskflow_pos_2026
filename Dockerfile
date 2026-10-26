# Dockerfile HARDENED - corrige as seis falhas do Dockerfile original.

# CORRIGIDO (FALHA 1): tag fixada e imagem slim (menos pacotes de SO,
# menos CVEs), em vez de "latest". Build multi-stage: o estagio final
# nao carrega cache nem ferramentas de instalacao.
FROM python:3.12-slim AS build

WORKDIR /app

COPY requirements.txt .

# CORRIGIDO (FALHA 3): --no-cache-dir evita deixar cache de download na camada.
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=build /usr/local/bin /usr/local/bin

WORKDIR /app

# CORRIGIDO (FALHA 2): usuario e grupo dedicados, sem privilegios de root.
RUN groupadd --system taskflow && \
    useradd --system --gid taskflow --home-dir /app --shell /usr/sbin/nologin taskflow && \
    chown taskflow:taskflow /app

COPY --chown=taskflow:taskflow . .

# CORRIGIDO (FALHA 4): nenhum segredo em ENV/ARG. TASKFLOW_SECRET_KEY deve
# ser injetada em tempo de execucao: docker run -e TASKFLOW_SECRET_KEY=...

# CORRIGIDO (FALHA 5): EXPOSE apenas documenta a porta; o servidor de
# desenvolvimento do Flask nao e mais usado.
EXPOSE 5000

USER taskflow

# CORRIGIDO (FALHA 6): gunicorn (WSGI de producao) no lugar de "python app.py".
# Sem debug: o bloco __main__ do app.py nao e executado.
CMD ["sh", "-c", "python -c 'from app import app, init_db; app.app_context().push(); init_db()' && exec gunicorn --bind 0.0.0.0:5000 --workers 2 app:app"]
