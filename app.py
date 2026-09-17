from flask import Flask, request, redirect
import os
import requests

app = Flask(__name__)

CLIENT_ID = os.environ.get("ML_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("ML_CLIENT_SECRET", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

REDIRECT_URI = "https://ofertas-mercado-livre-bot.onrender.com/oauth/callback"


def enviar_telegram(texto):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": texto
    }

    return requests.post(url, json=payload, timeout=15)


@app.route("/")
def home():
    return """
    <h2>Bot Ofertas Mercado Livre BR - Online!</h2>

    <p><a href="/login">Conectar com Mercado Livre</a></p>

    <p><a href="/enviar-teste">
    Testar Telegram
    </a></p>

    <p><a href="/buscar-produto">
    Buscar produto no Mercado Livre
    </a></p>
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

    response = requests.post(
        token_url,
        data=data,
        timeout=15
    )

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


@app.route("/enviar-teste")
def enviar_teste():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return "Telegram nao configurado."

    response = enviar_telegram(
        "🚀 Bot Ofertas Mercado Livre BR\n\n"
        "✅ Telegram funcionando!"
    )

    if response.status_code != 200:
        return "Erro ao enviar mensagem."

    return "Mensagem enviada! Confira seu Telegram."


@app.route("/buscar-produto")
def buscar_produto():
    termo = request.args.get("q", "smartphone")

    url = "https://api.mercadolibre.com/sites/MLB/search"

    try:
        response = requests.get(
            url,
            params={
                "q": termo,
                "limit": 10
            },
            timeout=15
        )
    except requests.RequestException:
        return "Erro de conexao com o Mercado Livre."

    if response.status_code != 200:
        return (
            "Mercado Livre nao permitiu a busca. "
            f"Codigo: {response.status_code}"
        )

    dados = response.json()
    produtos = dados.get("results", [])

    if not produtos:
        return "Nenhum produto encontrado."

    produto = produtos[0]

    titulo = produto.get("title", "Produto")
    preco = produto.get("price")
    link = produto.get("permalink", "")

    if preco is None:
        preco_texto = "Preco nao informado"
    else:
        preco_texto = f"R$ {preco:,.2f}"
        preco_texto = (
            preco_texto
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

    mensagem = (
        "🔥 OFERTA ENCONTRADA\n\n"
        f"📦 {titulo}\n\n"
        f"💰 {preco_texto}\n\n"
        f"🔗 {link}\n\n"
        "🧪 Este e apenas o primeiro teste."
    )

    telegram = enviar_telegram(mensagem)

    if telegram.status_code != 200:
        return "Produto encontrado, mas houve erro ao enviar ao Telegram."

    return (
        "Produto encontrado e enviado! "
        "Confira seu Telegram."
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port
    )
