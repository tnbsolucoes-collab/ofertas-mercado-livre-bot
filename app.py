from flask import Flask, request, redirect
import os
import requests

app = Flask(__name__)

CLIENT_ID = os.environ.get("ML_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("ML_CLIENT_SECRET", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

REDIRECT_URI = "https://ofertas-mercado-livre-bot.onrender.com/oauth/callback"

# Token temporario enquanto o servidor estiver ligado
ML_ACCESS_TOKEN = None


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

    <p><a href="/login">1 - Conectar Mercado Livre</a></p>

    <p><a href="/buscar-produto">
    2 - Buscar produto e enviar ao Telegram
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
    global ML_ACCESS_TOKEN

    code = request.args.get("code")

    if not code:
        return "Nenhum codigo recebido."

    response = requests.post(
        "https://api.mercadolibre.com/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "redirect_uri": REDIRECT_URI
        },
        timeout=15
    )

    if response.status_code != 200:
        return "Erro ao conectar Mercado Livre."

    dados = response.json()

    ML_ACCESS_TOKEN = dados.get("access_token")

    if not ML_ACCESS_TOKEN:
        return "Mercado Livre nao retornou access token."

    return """
    <h2>Mercado Livre conectado! ✅</h2>
    <p>
    Agora volte para a pagina inicial
    e clique em Buscar produto.
    </p>
    """


@app.route("/buscar-produto")
def buscar_produto():
    if not ML_ACCESS_TOKEN:
        return """
        <h3>Primeiro conecte sua conta do Mercado Livre.</h3>
        <a href="/login">Conectar agora</a>
        """

    termo = request.args.get("q", "smartphone")

    headers = {
        "Authorization": f"Bearer {ML_ACCESS_TOKEN}"
    }

    try:
        response = requests.get(
            "https://api.mercadolibre.com/sites/MLB/search",
            headers=headers,
            params={
                "q": termo,
                "limit": 10
            },
            timeout=15
        )

    except requests.RequestException:
        return "Erro de conexao com Mercado Livre."

    if response.status_code != 200:
        return (
            "Erro na busca do Mercado Livre. "
            f"Codigo: {response.status_code}"
        )

    produtos = response.json().get("results", [])

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
        "🧪 Primeiro produto encontrado pelo bot!"
    )

    telegram = enviar_telegram(mensagem)

    if telegram.status_code != 200:
        return "Produto encontrado, mas o Telegram deu erro."

    return """
    <h2>DEU CERTO! 🚀</h2>
    <p>Produto enviado para seu Telegram.</p>
    """


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port
    )
