from flask import Flask, request, redirect
import os
import requests

app = Flask(__name__)

CLIENT_ID = os.environ.get("ML_CLIENT_ID")
CLIENT_SECRET = os.environ.get("ML_CLIENT_SECRET")
REDIRECT_URI = "https://ofertas-mercado-livre-bot.onrender.com/oauth/callback"


@app.route("/")
def home():
    return """
    <h2>Bot Ofertas Mercado Livre BR - Online!</h2>
    <a href="/login">Conectar com Mercado Livre</a>
    """


@app.route("/login")
def login():
    auth_url = (
        "https://auth.mercadolivre.com.br/authorization"
        f"?response_type=code"
        f"&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
    )
    return redirect(auth_url)


@app.route("/oauth/callback")
def oauth_callback():
    code = request.args.get("code")

    if not code:
        return "Nenhum codigo de autorizacao recebido."

    token_url = "https://api.mercadolibre.com/oauth/token"

    data = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }

    response = requests.post(token_url, data=data)

    if response.status_code != 200:
        return f"Erro ao obter autorizacao: {response.text}"

    return "Mercado Livre conectado com sucesso!"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
