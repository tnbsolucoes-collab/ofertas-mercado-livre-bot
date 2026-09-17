from flask import Flask, request, redirect
import os
import requests

app = Flask(__name__)

CLIENT_ID = os.environ.get("ML_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("ML_CLIENT_SECRET", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

REDIRECT_URI = "https://ofertas-mercado-livre-bot.onrender.com/oauth/callback"


@app.route("/")
def home():
    return """
    <h2>Bot Ofertas Mercado Livre BR - Online!</h2>
    <p><a href="/login">Conectar com Mercado Livre</a></p>
    <p><a href="/telegram-test">Verificar mensagens recebidas</a></p>
    <p><a href="/enviar-teste">Enviar mensagem de teste</a></p>
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

    response = requests.post(token_url, data=data, timeout=10)

    if response.status_code != 200:
        return "Erro ao obter autorizacao do Mercado Livre."

    token_data = response.json()

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    user_id = token_data.get("user_id")

    print("Access token recebido:", bool(access_token))
    print("Refresh token recebido:", bool(refresh_token))
    print("User ID recebido:", user_id)

    return "Mercado Livre conectado com sucesso!"


@app.route("/telegram-test")
def telegram_test():
    if not TELEGRAM_BOT_TOKEN:
        return "TELEGRAM_BOT_TOKEN nao configurado."

    response = requests.get(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
        timeout=10
    )

    if response.status_code != 200:
        return "Erro ao consultar o Telegram."

    data = response.json()

    for update in reversed(data.get("result", [])):
        message = update.get("message")

        if message and message.get("chat", {}).get("type") == "private":
            return f"Telegram conectado. Chat ID: {message['chat']['id']}"

    return "Telegram conectado, mas nenhuma mensagem privada foi encontrada."


@app.route("/enviar-teste")
def enviar_teste():
    if not TELEGRAM_BOT_TOKEN:
        return "TELEGRAM_BOT_TOKEN nao configurado."

    if not TELEGRAM_CHAT_ID:
        return "TELEGRAM_CHAT_ID nao configurado."

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": (
            "🚀 Bot Ofertas Mercado Livre BR\n\n"
            "✅ Telegram conectado com sucesso!\n\n"
            "Agora o servidor ja consegue enviar mensagens para voce."
        )
    }

    response = requests.post(url, json=payload, timeout=10)

    if response.status_code != 200:
        return "Erro ao enviar mensagem para o Telegram."

    return "Mensagem enviada! Confira seu Telegram."


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
