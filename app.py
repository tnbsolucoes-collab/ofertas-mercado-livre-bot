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


def telegram_api(metodo, payload):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{metodo}"

    return requests.post(
        url,
        json=payload,
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
            2 - Buscar produto
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
        busca = requests.get(
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
        return "Erro ao buscar produtos."

    if busca.status_code != 200:
        return f"Erro na busca. Codigo: {busca.status_code}"

    produtos = busca.json().get("results", [])

    if not produtos:
        return "Nenhum produto encontrado."

    produto = produtos[0]

    product_id = produto.get("id")
    nome = produto.get("name", "Produto")

    try:
        detalhes = requests.get(
            f"https://api.mercadolibre.com/products/{product_id}",
            headers=headers,
            timeout=15
        )

    except requests.RequestException:
        return "Erro ao buscar detalhes do produto."

    if detalhes.status_code != 200:
        return (
            "Produto encontrado, mas nao consegui "
            f"buscar os detalhes. Codigo: {detalhes.status_code}"
        )

    dados = detalhes.json()

    imagem = None
    pictures = dados.get("pictures", [])

    if pictures:
        imagem = (
            pictures[0].get("secure_url")
            or pictures[0].get("url")
        )

    oferta = dados.get("buy_box_winner") or {}

    preco = oferta.get("price")
    item_id = oferta.get("item_id")

    if preco is None:
        preco_texto = "Consulte o preco no Mercado Livre"
    else:
        preco_texto = f"R$ {preco:,.2f}"
        preco_texto = (
            preco_texto
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

    link = ""

    if item_id:
        numero_item = str(item_id).replace("MLB", "")

        link = (
            "https://produto.mercadolivre.com.br/"
            f"MLB-{numero_item}"
        )

    legenda = (
        "🔥 OFERTA ENCONTRADA!\n\n"
        f"📦 {nome}\n\n"
        f"💰 {preco_texto}\n\n"
    )

    if link:
        legenda += (
            f"🔗 Link normal:\n{link}\n\n"
            "💰 Gere o link de afiliado antes de publicar."
        )

    botoes = {
        "inline_keyboard": [
            [
                {
                    "text": "✅ PUBLICAR",
                    "callback_data": f"publicar:{product_id}"
                },
                {
                    "text": "❌ IGNORAR",
                    "callback_data": f"ignorar:{product_id}"
                }
            ]
        ]
    }

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "reply_markup": botoes
    }

    if imagem:
        payload["photo"] = imagem
        payload["caption"] = legenda

        telegram = telegram_api(
            "sendPhoto",
            payload
        )

    else:
        payload["text"] = legenda

        telegram = telegram_api(
            "sendMessage",
            payload
        )

    if telegram.status_code != 200:
        return "Produto encontrado, mas houve erro no Telegram."

    return """
    <h2>OFERTA ENVIADA! 🚀</h2>
    <p>Confira seu Telegram.</p>
    """


@app.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    update = request.get_json(silent=True) or {}

    callback = update.get("callback_query")

    if not callback:
        return "OK", 200

    callback_id = callback.get("id")
    dados = callback.get("data", "")

    usuario_chat_id = (
        callback
        .get("message", {})
        .get("chat", {})
        .get("id")
    )

    # Somente voce pode usar os botoes de aprovacao
    if str(usuario_chat_id) != str(TELEGRAM_CHAT_ID):

        if callback_id:
            telegram_api(
                "answerCallbackQuery",
                {
                    "callback_query_id": callback_id,
                    "text": "Acesso nao autorizado."
                }
            )

        return "OK", 200

    if dados.startswith("ignorar:"):

        telegram_api(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_id,
                "text": "Oferta ignorada ❌"
            }
        )

        mensagem = callback.get("message", {})
        message_id = mensagem.get("message_id")

        if message_id:
            telegram_api(
                "editMessageReplyMarkup",
                {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "message_id": message_id,
                    "reply_markup": {
                        "inline_keyboard": []
                    }
                }
            )

        telegram_api(
            "sendMessage",
            {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": "❌ Oferta descartada. Vou deixar essa de fora."
            }
        )

        return "OK", 200

    if dados.startswith("publicar:"):

        telegram_api(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_id,
                "text": "Falta o link de afiliado 💰"
            }
        )

        telegram_api(
            "sendMessage",
            {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": (
                    "💰 Antes de publicar, precisamos usar "
                    "seu link de afiliado.\n\n"
                    "Por enquanto NAO vou publicar o link normal."
                )
            }
        )

        return "OK", 200

    return "OK", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port
    )
