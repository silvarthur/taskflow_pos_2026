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

import os
import time

import psycopg2
import psycopg2.extras

from flask import Flask, g, redirect, request, session, url_for, render_template
from dotenv import load_dotenv
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

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
            print(
                f"[db] tentativa {attempt}/{DB_CONNECT_RETRIES} falhou: {exc}",
                flush=True,
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

        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            return redirect(url_for("tasks"))
        error = "Usuario ou senha invalidos."

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
