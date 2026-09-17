from flask import Flask, request, redirect
import os
import requests

app = Flask(__name__)

CLIENT_ID = os.environ.get("ML_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("ML_CLIENT_SECRET", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

REDIRECT_URI = "https://ofertas-mercado-livre-bot.onrender.com/oauth/callback"

ML_ACCESS_TOKEN = None


def enviar_telegram(texto):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    return requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": texto
        },
        timeout=15
    )


@app.route("/")
def home():
    return """
    <h2>Bot Ofertas Mercado Livre BR 🤖</h2>

    <p>
        <a href="/login">
            1 - Conectar Mercado Livre
        </a>
    </p>

    <p>
        <a href="/buscar-produto?q=smartphone">
            2 - Buscar smartphone
        </a>
    </p>
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

    try:
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

    except requests.RequestException:
        return "Erro de conexao durante a autorizacao."

    if response.status_code != 200:
        return (
            "Erro ao conectar Mercado Livre. "
            f"Codigo: {response.status_code}"
        )

    dados = response.json()
    ML_ACCESS_TOKEN = dados.get("access_token")

    if not ML_ACCESS_TOKEN:
        return "Access token nao recebido."

    return """
    <h2>Mercado Livre conectado! ✅</h2>
    <p>Agora clique em Buscar produto.</p>
    <p>
        <a href="/buscar-produto?q=smartphone">
            Buscar produto
        </a>
    </p>
    """


@app.route("/buscar-produto")
def buscar_produto():
    if not ML_ACCESS_TOKEN:
        return """
        <h3>Conecte o Mercado Livre primeiro.</h3>
        <a href="/login">Conectar Mercado Livre</a>
        """

    termo = request.args.get("q", "smartphone").strip()

    headers = {
        "Authorization": f"Bearer {ML_ACCESS_TOKEN}"
    }

    try:
        response = requests.get(
            "https://api.mercadolibre.com/products/search",
            headers=headers,
            params={
                "status": "active",
                "site_id": "MLB",
                "q": termo,
                "limit": 10
            },
            timeout=15
        )

    except requests.RequestException:
        return "Erro de conexao com Mercado Livre."

    if response.status_code != 200:
        return (
            "Erro na busca de produtos. "
            f"Codigo: {response.status_code}"
        )

    dados = response.json()
    produtos = dados.get("results", [])

    if not produtos:
        return "A busca funcionou, mas nenhum produto foi encontrado."

    produto = produtos[0]

    product_id = produto.get("id", "Sem ID")
    nome = produto.get("name", "Produto sem nome")

    mensagem = (
        "🔥 PRODUTO ENCONTRADO!\n\n"
        f"📦 {nome}\n\n"
        f"🆔 {product_id}\n\n"
        f"🔎 Pesquisa: {termo}\n\n"
        "✅ Busca no Mercado Livre funcionando!"
    )

    telegram = enviar_telegram(mensagem)

    if telegram.status_code != 200:
        return "Produto encontrado, mas houve erro no Telegram."

    return """
    <h2>DEU CERTO! 🚀</h2>
    <p>Produto encontrado no Mercado Livre.</p>
    <p>Confira seu Telegram.</p>
    """


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port
    )
