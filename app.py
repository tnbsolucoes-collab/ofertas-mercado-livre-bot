from flask import Flask, request, redirect
import os
import requests

app = Flask(__name__)

CLIENT_ID = os.environ.get("ML_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("ML_CLIENT_SECRET", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

BASE_URL = "https://ofertas-mercado-livre-bot.onrender.com"
REDIRECT_URI = f"{BASE_URL}/oauth/callback"
WEBHOOK_URL = f"{BASE_URL}/telegram/webhook"

ML_ACCESS_TOKEN = None

# Guarda temporariamente as ofertas encontradas
OFERTAS = {}

# Guarda qual oferta esta esperando link de afiliado
AGUARDANDO_LINK = {}


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

    <p>
        <a href="/configurar-webhook">
            3 - Configurar Webhook
        </a>
    </p>
    """


@app.route("/configurar-webhook")
def configurar_webhook():
    if not TELEGRAM_BOT_TOKEN:
        return "Token do Telegram nao configurado."

    try:
        response = telegram_api(
            "setWebhook",
            {
                "url": WEBHOOK_URL,
                "allowed_updates": [
                    "callback_query",
                    "message"
                ]
            }
        )

    except requests.RequestException:
        return "Erro ao configurar webhook."

    if response.status_code != 200:
        return "Erro ao configurar webhook."

    dados = response.json()

    if not dados.get("ok"):
        return "Telegram nao aceitou o webhook."

    return """
    <h2>WEBHOOK CONFIGURADO! ✅</h2>
    <p>O bot agora recebe botoes e mensagens.</p>
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

    if not product_id:
        return "Produto sem ID."

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
            "Erro ao buscar detalhes. "
            f"Codigo: {detalhes.status_code}"
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

    link_normal = ""

    if item_id:
        numero_item = str(item_id).replace("MLB", "")

        link_normal = (
            "https://produto.mercadolivre.com.br/"
            f"MLB-{numero_item}"
        )

    # Guarda os dados para usar quando clicar PUBLICAR
    OFERTAS[str(product_id)] = {
        "nome": nome,
        "preco": preco_texto,
        "imagem": imagem,
        "link_normal": link_normal
    }

    legenda = (
        "🔥 OFERTA ENCONTRADA!\n\n"
        f"📦 {nome}\n\n"
        f"💰 {preco_texto}\n\n"
    )

    if link_normal:
        legenda += (
            "🔗 LINK DO PRODUTO:\n"
            f"{link_normal}\n\n"
        )

    legenda += (
        "👇 Escolha o que fazer com essa oferta."
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
        return "Erro ao enviar produto para Telegram."

    return """
    <h2>OFERTA ENVIADA! 🚀</h2>
    <p>Confira seu Telegram.</p>
    """


@app.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    update = request.get_json(silent=True) or {}

    # ==============================
    # CLIQUES NOS BOTOES
    # ==============================

    callback = update.get("callback_query")

    if callback:
        callback_id = callback.get("id")
        callback_data = callback.get("data", "")

        mensagem_callback = callback.get("message", {})

        chat_id = (
            mensagem_callback
            .get("chat", {})
            .get("id")
        )

        if str(chat_id) != str(TELEGRAM_CHAT_ID):

            if callback_id:
                telegram_api(
                    "answerCallbackQuery",
                    {
                        "callback_query_id": callback_id,
                        "text": "Acesso nao autorizado."
                    }
                )

            return "OK", 200

        if callback_data.startswith("ignorar:"):
            product_id = callback_data.split(":", 1)[1]

            OFERTAS.pop(product_id, None)

            telegram_api(
                "answerCallbackQuery",
                {
                    "callback_query_id": callback_id,
                    "text": "Oferta ignorada ❌"
                }
            )

            telegram_api(
                "sendMessage",
                {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": (
                        "❌ Oferta descartada.\n\n"
                        "Nao vou publicar esse produto."
                    )
                }
            )

            return "OK", 200

        if callback_data.startswith("publicar:"):
            product_id = callback_data.split(":", 1)[1]

            oferta_salva = OFERTAS.get(product_id)

            if not oferta_salva:
                telegram_api(
                    "answerCallbackQuery",
                    {
                        "callback_query_id": callback_id,
                        "text": "Oferta nao encontrada."
                    }
                )

                return "OK", 200

            AGUARDANDO_LINK[str(chat_id)] = product_id

            link_normal = oferta_salva.get(
                "link_normal",
                ""
            )

            telegram_api(
                "answerCallbackQuery",
                {
                    "callback_query_id": callback_id,
                    "text": "Oferta aprovada! ✅"
                }
            )

            texto = (
                "💰 OFERTA APROVADA!\n\n"
                "Agora precisamos transformar o link "
                "normal em link de afiliado.\n\n"
            )

            if link_normal:
                texto += (
                    "🔗 COPIE ESTE LINK:\n\n"
                    f"{link_normal}\n\n"
                    "1️⃣ Cole no Gerador de Links "
                    "do Mercado Livre.\n\n"
                    "2️⃣ Gere seu link de afiliado.\n\n"
                    "3️⃣ Volte aqui e COLE o link "
                    "de afiliado na conversa comigo. 👇"
                )

            else:
                texto += (
                    "Nao encontrei o link normal dessa oferta."
                )

            telegram_api(
                "sendMessage",
                {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": texto
                }
            )

            return "OK", 200

    # ==============================
    # MENSAGENS ENVIADAS AO BOT
    # ==============================

    mensagem = update.get("message")

    if mensagem:
        chat_id = mensagem.get(
            "chat",
            {}
        ).get("id")

        texto = mensagem.get(
            "text",
            ""
        ).strip()

        if str(chat_id) != str(TELEGRAM_CHAT_ID):
            return "OK", 200

        product_id = AGUARDANDO_LINK.get(
            str(chat_id)
        )

        if not product_id:
            return "OK", 200

        if (
            texto.startswith("https://")
            or texto.startswith("http://")
        ):
            oferta_salva = OFERTAS.get(product_id)

            if not oferta_salva:
                AGUARDANDO_LINK.pop(
                    str(chat_id),
                    None
                )

                return "OK", 200

            oferta_salva["link_afiliado"] = texto

            AGUARDANDO_LINK.pop(
                str(chat_id),
                None
            )

            nome = oferta_salva.get(
                "nome",
                "Produto"
            )

            preco = oferta_salva.get(
                "preco",
                ""
            )

            telegram_api(
                "sendMessage",
                {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": (
                        "✅ LINK DE AFILIADO RECEBIDO!\n\n"
                        f"📦 {nome}\n\n"
                        f"💰 {preco}\n\n"
                        "🔗 Seu link foi associado "
                        "a essa oferta.\n\n"
                        "🔒 Ainda NAO publiquei no canal.\n\n"
                        "Agora podemos montar a postagem "
                        "final e enviar para o canal."
                    )
                }
            )

        else:
            telegram_api(
                "sendMessage",
                {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": (
                        "⚠️ Estou esperando o seu "
                        "link de afiliado.\n\n"
                        "Cole o link completo aqui."
                    )
                }
            )

        return "OK", 200

    return "OK", 200


if __name__ == "__main__":
    port = int(
        os.environ.get(
            "PORT",
            5000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
