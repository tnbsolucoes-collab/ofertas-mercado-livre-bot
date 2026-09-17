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

# Termos usados para encontrar categorias diferentes.
# Depois vamos ampliar e adicionar filtros de promocao.
TERMOS_CATEGORIAS = [
    "smartphone",
    "fone bluetooth",
    "smart tv",
    "notebook",
    "tenis",
    "perfume",
    "air fryer"
]


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
            2 - Buscar mais vendidos 🔥
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


def descobrir_categoria(termo, headers):
    try:
        resposta = requests.get(
            "https://api.mercadolibre.com/sites/MLB/domain_discovery/search",
            headers=headers,
            params={
                "q": termo,
                "limit": 1
            },
            timeout=20
        )

    except requests.RequestException:
        return None

    if resposta.status_code != 200:
        return None

    resultados = resposta.json()

    if not resultados:
        return None

    category_id = resultados[0].get("category_id")

    if not category_id:
        return None

    return category_id


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

    if item.get("status") != "active":
        return None

    permalink = item.get("permalink")

    if not permalink:
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


def consultar_ranking_categoria(category_id, headers):
    try:
        resposta = requests.get(
            (
                "https://api.mercadolibre.com/"
                f"highlights/MLB/category/{category_id}"
            ),
            headers=headers,
            timeout=20
        )

    except requests.RequestException:
        return None

    if resposta.status_code != 200:
        return None

    dados = resposta.json()

    return dados.get("content") or []


def encontrar_item_mais_vendido(headers):
    for termo in TERMOS_CATEGORIAS:

        category_id = descobrir_categoria(
            termo,
            headers
        )

        if not category_id:
            continue

        ranking = consultar_ranking_categoria(
            category_id,
            headers
        )

        if not ranking:
            continue

        # Ranking ja vem com a posicao.
        ranking = sorted(
            ranking,
            key=lambda x: x.get("position", 999)
        )

        for resultado in ranking:

            tipo = str(
                resultado.get("type", "")
            ).upper()

            item_id = resultado.get("id")
            posicao = resultado.get("position")

            # Primeiro teste:
            # usamos apenas ITEM porque ele representa
            # uma publicacao individual.
            if tipo != "ITEM":
                continue

            if not item_id:
                continue

            oferta = pegar_item(
                item_id,
                headers
            )

            if not oferta:
                continue

            oferta["posicao"] = posicao
            oferta["categoria_id"] = category_id
            oferta["termo_categoria"] = termo

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

    oferta = encontrar_item_mais_vendido(
        headers
    )

    if not oferta:
        return """
        <h3>Nao encontrei um ITEM direto nos rankings testados.</h3>

        <p>
            O ranking respondeu, mas os resultados podem ser
            PRODUCT ou USER_PRODUCT.
        </p>

        <p>
            Nesse caso, a proxima etapa sera converter esses
            tipos para uma oferta compravel.
        </p>
        """

    item_id = oferta["item_id"]

    OFERTAS[item_id] = oferta

    nome = oferta["nome"]
    preco = oferta["preco"]
    imagem = oferta["imagem"]
    link_normal = oferta["link_normal"]
    posicao = oferta.get("posicao", "?")

    legenda = (
        "🔥 MAIS VENDIDO ENCONTRADO!\n\n"
        f"🏆 Posicao no ranking: #{posicao}\n\n"
        f"📦 {nome}\n\n"
        f"💰 {preco}\n\n"
        "🔗 LINK DO PRODUTO:\n"
        f"{link_normal}\n\n"
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
                    "🔒 Ainda NAO publiquei no canal."
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
