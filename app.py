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
        timeout=15
    )


def formatar_preco(preco):
    if preco is None:
        return "Consulte o preco no Mercado Livre"

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

        dados = response.json()

    except Exception:
        return "Erro ao configurar webhook."

    if not dados.get("ok"):
        return "Telegram nao aceitou o webhook."

    return """
    <h2>WEBHOOK CONFIGURADO! ✅</h2>
    <p>O bot esta pronto para receber botoes e mensagens.</p>
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


def encontrar_anuncio_real(produtos, headers):
    """
    Percorre produtos de catalogo ate encontrar
    um buy_box_winner com item_id.

    Depois consulta /items/{item_id} para obter
    o permalink real do anuncio.
    """

    for produto in produtos:
        product_id = produto.get("id")

        if not product_id:
            continue

        try:
            resposta_produto = requests.get(
                f"https://api.mercadolibre.com/products/{product_id}",
                headers=headers,
                timeout=15
            )
        except requests.RequestException:
            continue

        if resposta_produto.status_code != 200:
            continue

        dados_produto = resposta_produto.json()

        vencedor = dados_produto.get("buy_box_winner") or {}

        item_id = vencedor.get("item_id")

        if not item_id:
            continue

        try:
            resposta_item = requests.get(
                f"https://api.mercadolibre.com/items/{item_id}",
                headers=headers,
                timeout=15
            )
        except requests.RequestException:
            continue

        if resposta_item.status_code != 200:
            continue

        item = resposta_item.json()

        permalink = item.get("permalink")

        if not permalink:
            continue

        nome = (
            item.get("title")
            or produto.get("name")
            or produto.get("title")
            or "Produto"
        )

        preco = (
            vencedor.get("price")
            or item.get("price")
        )

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
            "product_id": str(product_id),
            "item_id": str(item_id),
            "nome": nome,
            "preco": formatar_preco(preco),
            "imagem": imagem,
            "link_normal": permalink
        }

    return None


@app.route("/buscar-produto")
def buscar_produto():
    if not ML_ACCESS_TOKEN:
        return """
        <h3>Conecte o Mercado Livre primeiro.</h3>
        <a href="/login">Conectar Mercado Livre</a>
        """

    termo = request.args.get("q", "smartphone").strip()

    if not termo:
        termo = "smartphone"

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
                "limit": 20
            },
            timeout=15
        )

    except requests.RequestException:
        return "Erro ao buscar produtos."

    if busca.status_code != 200:
        return (
            "Erro na busca de produtos. "
            f"Codigo: {busca.status_code}"
        )

    produtos = busca.json().get("results", [])

    if not produtos:
        return "Nenhum produto encontrado."

    oferta = encontrar_anuncio_real(
        produtos,
        headers
    )

    if not oferta:
        return """
        <h3>Nenhum anuncio compravel encontrado nessa busca.</h3>
        <p>Tente novamente ou pesquise outro produto.</p>
        """

    product_id = oferta["product_id"]

    OFERTAS[product_id] = oferta

    nome = oferta["nome"]
    preco = oferta["preco"]
    imagem = oferta["imagem"]
    link_normal = oferta["link_normal"]

    legenda = (
        "🔥 OFERTA ENCONTRADA!\n\n"
        f"📦 {nome}\n\n"
        f"💰 {preco}\n\n"
        "🔗 LINK DO PRODUTO:\n"
        f"{link_normal}\n\n"
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

    try:
        if imagem:
            payload["photo"] = imagem
            payload["caption"] = legenda

            resposta_telegram = telegram_api(
                "sendPhoto",
                payload
            )

        else:
            payload["text"] = legenda

            resposta_telegram = telegram_api(
                "sendMessage",
                payload
            )

    except requests.RequestException:
        return "Erro ao conectar com Telegram."

    if resposta_telegram.status_code != 200:
        return (
            "Erro ao enviar oferta para Telegram. "
            f"Codigo: {resposta_telegram.status_code}"
        )

    return """
    <h2>OFERTA ENVIADA! 🚀</h2>
    <p>Confira seu Telegram.</p>
    """


@app.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    update = request.get_json(silent=True) or {}

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

            if AGUARDANDO_LINK.get(str(chat_id)) == product_id:
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
                        "Nao vou publicar esse produto."
                    )
                }
            )

            return "OK", 200

        if callback_data.startswith("publicar:"):
            product_id = callback_data.split(":", 1)[1]

            oferta = OFERTAS.get(product_id)

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
                            "na memoria do bot.\n\n"
                            "Busque uma nova oferta."
                        )
                    }
                )

                return "OK", 200

            link_normal = oferta.get("link_normal")

            if not link_normal:
                telegram_api(
                    "answerCallbackQuery",
                    {
                        "callback_query_id": callback_id,
                        "text": "Link nao encontrado."
                    }
                )

                return "OK", 200

            AGUARDANDO_LINK[str(chat_id)] = product_id

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
                        "🔗 COPIE O LINK DO PRODUTO:\n\n"
                        f"{link_normal}\n\n"
                        "Agora coloque esse link no "
                        "Gerador de Links do Mercado Livre.\n\n"
                        "Depois copie o seu link de afiliado "
                        "gerado e mande aqui para o bot. 👇"
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

        texto = mensagem.get("text", "").strip()

        if str(chat_id) != str(TELEGRAM_CHAT_ID):
            return "OK", 200

        product_id = AGUARDANDO_LINK.get(
            str(chat_id)
        )

        if not product_id:
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
                        "⚠️ Mande o link completo "
                        "gerado pelo Mercado Livre."
                    )
                }
            )

            return "OK", 200

        oferta = OFERTAS.get(product_id)

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

        # Usa exatamente o link enviado pelo usuario.
        # Nao inventa parametros de afiliado.
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
                    "🔗 Link associado a oferta.\n\n"
                    "🔒 Ainda nao publiquei no canal.\n\n"
                    "Agora falta apenas criar o envio "
                    "final para o seu canal."
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
