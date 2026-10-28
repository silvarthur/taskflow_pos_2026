"""
TaskFlow - Aplicacao de exemplo da disciplina DevSecOps.

ATENCAO: Esta aplicacao contem vulnerabilidades INTRODUZIDAS DE PROPOSITO
para fins didaticos. NUNCA use este codigo como referencia de boas praticas
e NUNCA implante em ambiente de producao ou exposto a internet.

Vulnerabilidades presentes nesta versao (linha de base do curso):
  1. SQL Injection no login e na busca de tarefas (Modulo 3 - SAST)
  2. Cross-Site Scripting (XSS) armazenado na descricao da tarefa (Modulo 3/4)
  3. Segredo de sessao (SECRET_KEY) hardcoded no codigo (Modulo 1/3)
  4. Senhas armazenadas em texto puro no banco (Modulo 2/3)
  5. Endpoint de debug exposto publicamente (Modulo 2/4)
  6. Dependencias com CVEs conhecidas em requirements.txt (Modulo 3 - SCA)

Ao longo dos encontros, cada uma dessas falhas sera identificada por uma
ferramenta especifica da esteira e corrigida em uma versao "fixed" do codigo.
"""

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from logging.handlers import WatchedFileHandler

import psycopg2
import psycopg2.extras

from flask import (
    Flask,
    g,
    has_request_context,
    redirect,
    request,
    session,
    url_for,
    render_template,
)
from dotenv import load_dotenv
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()


# ---------------------------------------------------------------------------
# Logging estruturado (Modulo 6)
#
# Cada log e um objeto JSON em uma linha (JSON Lines), emitido em stdout e em
# um arquivo (TASKFLOW_LOG_DIR/taskflow.log) que o Promtail le e envia ao Loki.
#
# REGRA: nunca logar request.form inteiro, senha, token ou cookie. Os eventos
# abaixo recebem apenas campos escolhidos um a um (username, ip, result...).
# ---------------------------------------------------------------------------
LOG_DIR = os.environ.get("TASKFLOW_LOG_DIR", "logs")
LOG_FILE = "taskflow.log"

# Atributos que todo LogRecord ja tem; o que sobrar vem de `extra={...}`.
_RESERVED_RECORD_KEYS = set(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Formata cada LogRecord como uma linha JSON."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_KEYS and key not in entry:
                entry[key] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def configurar_logging() -> logging.Logger:
    logger = logging.getLogger("taskflow")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False
    formatter = JsonFormatter()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # WatchedFileHandler (e nao RotatingFileHandler): o gunicorn roda varios
    # workers gravando no mesmo arquivo, e a rotacao feita por cada um deles
    # corromperia o log. A rotacao fica por conta de agente externo.
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        file_handler = WatchedFileHandler(
            os.path.join(LOG_DIR, LOG_FILE), encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError as exc:
        logger.warning(
            "arquivo de log indisponivel, usando apenas stdout",
            extra={"event": "log_file_unavailable", "log_dir": LOG_DIR, "error": str(exc)},
        )
    return logger


logger = configurar_logging()

app = Flask(__name__)
_secret_key = os.environ.get("TASKFLOW_SECRET_KEY")
if not _secret_key:
    raise RuntimeError(
        "Variavel de ambiente TASKFLOW_SECRET_KEY nao foi definida. "
        "Configure-a antes de iniciar a aplicacao "
        "(ex: export TASKFLOW_SECRET_KEY='valor-aleatorio-e-longo')."
    )
app.config["SECRET_KEY"] = _secret_key


# PostgresSQL Connecttion String
# postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB}
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "Variavel de ambiente DATABASE_URL nao foi definida. "
        "Ex: postgresql://usuario:senha@db:5432/taskflow"
    )

DB_CONNECT_RETRIES = 10
DB_CONNECT_DELAY_SECONDS = 2


def _connect_with_retry():
    """Abre a conexao com o Postgres, tentando de novo se o banco ainda
    estiver iniciando (o healthcheck do compose ja ajuda, mas nao custa ser
    resiliente fora do Compose)."""
    last_error = None
    for attempt in range(1, DB_CONNECT_RETRIES + 1):
        try:
            return psycopg2.connect(
                DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor
            )
        except psycopg2.OperationalError as exc:
            last_error = exc
            logger.warning(
                "falha ao conectar no banco",
                extra={
                    "event": "db_connect_retry",
                    "attempt": attempt,
                    "max_attempts": DB_CONNECT_RETRIES,
                    "error": str(exc).strip(),
                    "request_id": g.get("request_id") if has_request_context() else None,
                },
            )
            time.sleep(DB_CONNECT_DELAY_SECONDS)
    raise last_error


def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = _connect_with_retry()
    return db


@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                done INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users (id)
            );
            """
        )
    db.commit()

    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS total FROM users")
        total = cur.fetchone()["total"]

        if total == 0:
            cur.execute(
                "INSERT INTO users (username, password) VALUES (%s, %s) "
                "ON CONFLICT (username) DO NOTHING",
                ("admin", generate_password_hash("admin123")),
            )
            cur.execute(
                "INSERT INTO users (username, password) VALUES (%s, %s)"
                "ON CONFLICT (username) DO NOTHING",
                ("aluno", generate_password_hash("senha123")),
            )
    db.commit()
    logger.info(
        "banco inicializado, aplicacao pronta",
        extra={"event": "app_startup"},
    )


@app.before_request
def gerar_request_id():
    # Sempre gerado no servidor: aceitar X-Request-ID do cliente permitiria
    # forjar identificadores e poluir a correlacao dos logs.
    g.request_id = str(uuid.uuid4())
    g.start_time = time.time()


@app.after_request
def logar_fim_da_requisicao(response):
    request_id = g.get("request_id")
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "requisicao finalizada",
        extra={
            "event": "http_request",
            "request_id": request_id,
            "method": request.method,
            "path": request.path,
            "status_code": response.status_code,
            "duration_ms": round((time.time() - g.get("start_time", time.time())) * 1000, 2),
            "user": session.get("username"),
            "ip": request.remote_addr,
        },
    )
    return response


@app.errorhandler(Exception)
def tratar_erro_inesperado(exc):
    # Erros HTTP normais (404, 400...) seguem o fluxo padrao do Flask.
    if isinstance(exc, HTTPException):
        return exc
    logger.error(
        "erro nao tratado",
        exc_info=exc,
        extra={
            "event": "unhandled_exception",
            "request_id": g.get("request_id"),
            "path": request.path,
        },
    )
    return "Erro interno do servidor.", 500


@app.route("/")
def index():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return redirect(url_for("tasks"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                "SELECT * FROM users WHERE username = %s", (username,)
            )
            user = cur.fetchone()

        # Apenas username e IP vao para o log. A senha nao tem motivo de
        # negocio para existir em log, nem mascarada.
        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            logger.info(
                "login bem-sucedido",
                extra={
                    "event": "login_attempt",
                    "result": "success",
                    "username": username,
                    "ip": request.remote_addr,
                    "request_id": g.get("request_id"),
                },
            )
            return redirect(url_for("tasks"))
        error = "Usuario ou senha invalidos."
        logger.warning(
            "login falhou",
            extra={
                "event": "login_attempt",
                "result": "failure",
                "username": username,
                "ip": request.remote_addr,
                "request_id": g.get("request_id"),
            },
        )

    return f"""
    <h1>TaskFlow - Login</h1>
    <form method="post">
        Usuario: <input type="text" name="username"><br>
        Senha: <input type="password" name="password"><br>
        <input type="submit" value="Entrar">
    </form>
    <p style="color:red">{error or ""}</p>
    """


@app.route("/logout")
def logout():
    logger.info(
        "logout",
        extra={
            "event": "logout",
            "username": session.get("username"),
            "ip": request.remote_addr,
            "request_id": g.get("request_id"),
        },
    )
    session.clear()
    return redirect(url_for("login"))


@app.route("/tasks", methods=["GET"])
def tasks():
    if "user_id" not in session:
        return redirect(url_for("login"))

    search = request.args.get("q", "")
    db = get_db()

    with db.cursor() as cur:
        if search:
            like_pattern = f"%{search}%"
            cur.execute(
                "SELECT * FROM tasks WHERE user_id = %s AND title LIKE %s",
                (session["user_id"], like_pattern),
            )
        else:
            cur.execute(
                "SELECT * FROM tasks WHERE user_id = %s", (session["user_id"],)
            )
        rows = cur.fetchall()

    logger.info(
        "lista de tarefas acessada",
        extra={
            "event": "tasks_list_accessed",
            "username": session["username"],
            "search_term": search or None,
            "results_count": len(rows),
            "request_id": g.get("request_id"),
        },
    )

    items = ""
    for row in rows:
        items += f"""
        <li>
            <b>{row['title']}</b> - {row['description']}
            {'(feita)' if row['done'] else ''}
        </li>
        """
    return render_template(
        "tasks.html",
        rows=rows,
        search=search,
        username=session["username"],
    )


@app.route("/tasks/new", methods=["GET", "POST"])
def new_task():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        title = request.form["title"]
        description = request.form["description"]
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO tasks (user_id, title, description) VALUES (%s, %s, %s)",
                (session["user_id"], title, description),
            )
        db.commit()
        # So o titulo e o tamanho da descricao: o texto pode conter dado
        # sensivel digitado pelo usuario.
        logger.info(
            "tarefa criada",
            extra={
                "event": "task_created",
                "username": session["username"],
                "title": title,
                "description_length": len(description or ""),
                "request_id": g.get("request_id"),
            },
        )
        return redirect(url_for("tasks"))

    return render_template("new_task.html")


if __name__ == "__main__":
    with app.app_context():
        init_db()
    # Mudei o host de 0.0.0.0 para 127.0.0.1 e a porte de
    # 5000 para 5001 para evitar conflitos com outras aplicacoes locais (Airtop no MacOS).
    # Talvez seja preciso revisa esse ponto no futuro se precisarmos de um host que expõe
    # o app a toda a rede.
    app.run(host="127.0.0.1", port=5001)
