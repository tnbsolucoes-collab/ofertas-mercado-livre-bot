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

OFERTAS = {}
AGUARDANDO_LINK = {}


def telegram_api(metodo, payload):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{metodo}"

    return requests.post(
        url,
        json=payload,
        timeout=20
    )


def formatar_preco(preco):
    if preco is None:
        return "Consulte o preco"

    try:
        preco = float(preco)
    except (TypeError, ValueError):
        return str(preco)

    texto = f"R$ {preco:,.2f}"

    return (
        texto
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
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
        <a href="/buscar-mais-vendidos">
            2 - Buscar mais vendidos
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
        resposta = telegram_api(
            "setWebhook",
            {
                "url": WEBHOOK_URL,
                "allowed_updates": [
                    "callback_query",
                    "message"
                ]
            }
        )

        dados = resposta.json()

    except Exception:
        return "Erro ao configurar webhook."

    if not dados.get("ok"):
        return "Telegram nao aceitou o webhook."

    return """
    <h2>WEBHOOK CONFIGURADO! ✅</h2>
    <p>Telegram conectado ao bot.</p>
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
        resposta = requests.post(
            "https://api.mercadolibre.com/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "code": code,
                "redirect_uri": REDIRECT_URI
            },
            timeout=20
        )

    except requests.RequestException:
        return "Erro de conexao com Mercado Livre."

    if resposta.status_code != 200:
        return (
            "Erro ao conectar Mercado Livre. "
            f"Codigo: {resposta.status_code}"
        )

    dados = resposta.json()

    ML_ACCESS_TOKEN = dados.get("access_token")

    if not ML_ACCESS_TOKEN:
        return "Access token nao recebido."

    return """
    <h2>Mercado Livre conectado! ✅</h2>

    <p>
        <a href="/buscar-mais-vendidos">
            Buscar mais vendidos 🔥
        </a>
    </p>
    """


def pegar_item(item_id, headers):
    try:
        resposta = requests.get(
            f"https://api.mercadolibre.com/items/{item_id}",
            headers=headers,
            timeout=20
        )
    except requests.RequestException:
        return None

    if resposta.status_code != 200:
        return None

    item = resposta.json()

    permalink = item.get("permalink")

    if not permalink:
        return None

    if item.get("status") != "active":
        return None

    titulo = item.get("title") or "Produto"

    preco = item.get("price")

    imagem = (
        item.get("secure_thumbnail")
        or item.get("thumbnail")
    )

    pictures = item.get("pictures") or []

    if pictures:
        imagem = (
            pictures[0].get("secure_url")
            or pictures[0].get("url")
            or imagem
        )

    return {
        "item_id": str(item_id),
        "nome": titulo,
        "preco": formatar_preco(preco),
        "preco_numero": preco,
        "imagem": imagem,
        "link_normal": permalink
    }


def procurar_item_nos_highlights(resultados, headers):
    for resultado in resultados:
        tipo = str(
            resultado.get("type", "")
        ).upper()

        item_id = resultado.get("id")

        if tipo != "ITEM":
            continue

        if not item_id:
            continue

        oferta = pegar_item(
            item_id,
            headers
        )

        if oferta:
            return oferta

    return None


@app.route("/buscar-mais-vendidos")
def buscar_mais_vendidos():
    if not ML_ACCESS_TOKEN:
        return """
        <h3>Conecte o Mercado Livre primeiro.</h3>
        <p><a href="/login">Conectar Mercado Livre</a></p>
        """

    headers = {
        "Authorization": f"Bearer {ML_ACCESS_TOKEN}"
    }

    try:
        resposta = requests.get(
            "https://api.mercadolibre.com/highlights/MLB",
            headers=headers,
            timeout=20
        )

    except requests.RequestException:
        return "Erro ao buscar os mais vendidos."

    if resposta.status_code != 200:
        return (
            "Erro ao consultar mais vendidos. "
            f"Codigo: {resposta.status_code}"
        )

    dados = resposta.json()

    resultados = dados.get("content") or []

    if not resultados:
        return """
        <h3>Nenhum resultado recebido.</h3>
        <p>Tente novamente.</p>
        """

    oferta = procurar_item_nos_highlights(
        resultados,
        headers
    )

    if not oferta:
        return """
        <h3>
            Os mais vendidos retornados nao possuem
            um ITEM direto disponivel.
        </h3>

        <p>
            Precisamos tratar os resultados de catalogo
            na proxima etapa.
        </p>
        """

    item_id = oferta["item_id"]

    OFERTAS[item_id] = oferta

    nome = oferta["nome"]
    preco = oferta["preco"]
    imagem = oferta["imagem"]
    link_normal = oferta["link_normal"]

    legenda = (
        "🔥 MAIS VENDIDO ENCONTRADO!\n\n"
        f"📦 {nome}\n\n"
        f"💰 {preco}\n\n"
        "🔗 LINK DO PRODUTO:\n"
        f"{link_normal}\n\n"
        "⭐ Produto encontrado entre os "
        "mais vendidos do Mercado Livre.\n\n"
        "👇 Deseja preparar essa oferta?"
    )

    botoes = {
        "inline_keyboard": [
            [
                {
                    "text": "✅ PUBLICAR",
                    "callback_data": f"publicar:{item_id}"
                },
                {
                    "text": "❌ IGNORAR",
                    "callback_data": f"ignorar:{item_id}"
                }
            ]
        ]
    }

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "reply_markup": botoes
    }

    try:
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

    except requests.RequestException:
        return "Erro ao conectar com Telegram."

    if telegram.status_code != 200:
        return (
            "Erro ao enviar para Telegram. "
            f"Codigo: {telegram.status_code}"
        )

    return """
    <h2>MAIS VENDIDO ENVIADO! 🔥</h2>
    <p>Confira seu Telegram.</p>
    """


@app.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    update = request.get_json(silent=True) or {}

    callback = update.get("callback_query")

    if callback:
        callback_id = callback.get("id")
        callback_data = callback.get("data", "")

        mensagem_callback = callback.get(
            "message",
            {}
        )

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
            item_id = callback_data.split(
                ":",
                1
            )[1]

            OFERTAS.pop(item_id, None)

            if AGUARDANDO_LINK.get(
                str(chat_id)
            ) == item_id:

                AGUARDANDO_LINK.pop(
                    str(chat_id),
                    None
                )

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
                        "Vou deixar essa de fora."
                    )
                }
            )

            return "OK", 200

        if callback_data.startswith("publicar:"):
            item_id = callback_data.split(
                ":",
                1
            )[1]

            oferta = OFERTAS.get(item_id)

            if not oferta:
                telegram_api(
                    "answerCallbackQuery",
                    {
                        "callback_query_id": callback_id,
                        "text": "Oferta expirou."
                    }
                )

                telegram_api(
                    "sendMessage",
                    {
                        "chat_id": TELEGRAM_CHAT_ID,
                        "text": (
                            "⚠️ Essa oferta nao esta mais "
                            "na memoria.\n\n"
                            "Busque uma nova."
                        )
                    }
                )

                return "OK", 200

            link_normal = oferta.get(
                "link_normal",
                ""
            )

            if not link_normal:
                telegram_api(
                    "answerCallbackQuery",
                    {
                        "callback_query_id": callback_id,
                        "text": "Link nao encontrado."
                    }
                )

                return "OK", 200

            AGUARDANDO_LINK[
                str(chat_id)
            ] = item_id

            telegram_api(
                "answerCallbackQuery",
                {
                    "callback_query_id": callback_id,
                    "text": "Oferta aprovada! ✅"
                }
            )

            telegram_api(
                "sendMessage",
                {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": (
                        "💰 OFERTA APROVADA!\n\n"
                        "🔗 COPIE ESTE LINK:\n\n"
                        f"{link_normal}\n\n"
                        "Coloque esse link no Gerador "
                        "de Links do Mercado Livre.\n\n"
                        "Depois envie aqui para o bot "
                        "o link de afiliado gerado. 👇"
                    )
                }
            )

            return "OK", 200

    mensagem = update.get("message")

    if mensagem:
        chat_id = (
            mensagem
            .get("chat", {})
            .get("id")
        )

        texto = mensagem.get(
            "text",
            ""
        ).strip()

        if str(chat_id) != str(TELEGRAM_CHAT_ID):
            return "OK", 200

        item_id = AGUARDANDO_LINK.get(
            str(chat_id)
        )

        if not item_id:
            return "OK", 200

        if not (
            texto.startswith("https://")
            or texto.startswith("http://")
        ):
            telegram_api(
                "sendMessage",
                {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": (
                        "⚠️ Cole o link completo "
                        "gerado pelo Mercado Livre."
                    )
                }
            )

            return "OK", 200

        oferta = OFERTAS.get(item_id)

        if not oferta:
            AGUARDANDO_LINK.pop(
                str(chat_id),
                None
            )

            telegram_api(
                "sendMessage",
                {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": (
                        "⚠️ Essa oferta expirou.\n\n"
                        "Busque uma nova oferta."
                    )
                }
            )

            return "OK", 200

        # Guarda exatamente o link enviado.
        # Nao altera nem inventa parametros.
        oferta["link_afiliado"] = texto

        AGUARDANDO_LINK.pop(
            str(chat_id),
            None
        )

        telegram_api(
            "sendMessage",
            {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": (
                    "✅ LINK RECEBIDO!\n\n"
                    f"📦 {oferta['nome']}\n\n"
                    f"💰 {oferta['preco']}\n\n"
                    "🔗 Link associado a essa oferta.\n\n"
                    "🔒 Ainda NAO publiquei no canal.\n\n"
                    "Depois vamos adicionar o botao "
                    "final para publicar no canal."
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
