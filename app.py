from flask import Flask, request, redirect
import os
import requests

app = Flask(__name__)

CLIENT_ID = os.environ.get("ML_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("ML_CLIENT_SECRET", "").strip()

print("ML_CLIENT_ID carregado:", bool(CLIENT_ID))
print("ML_CLIENT_SECRET carregado:", bool(CLIENT_SECRET))
print("Tamanho do CLIENT_ID:", len(CLIENT_ID))
print("Tamanho do CLIENT_SECRET:", len(CLIENT_SECRET))

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
        "?response_type=code"
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

    token_data = response.json()

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    user_id = token_data.get("user_id")

    print("Access token recebido:", bool(access_token))
    print("Refresh token recebido:", bool(refresh_token))
    print("User ID recebido:", user_id)

    return "Mercado Livre conectado com sucesso!"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
