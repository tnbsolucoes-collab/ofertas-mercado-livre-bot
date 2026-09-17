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

# Comecamos com uma categoria por busca para evitar
# o WORKER TIMEOUT do Render.
TERMO_ATUAL = "smartphone"

REQUEST_TIMEOUT = 4


def telegram_api(metodo, payload):
    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/{metodo}"
    )

    return requests.post(
        url,
        json=payload,
        timeout=REQUEST_TIMEOUT
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


def calcular_desconto(preco, preco_original):
    try:
        preco = float(preco)
        preco_original = float(preco_original)

        if preco_original <= preco:
            return None

        desconto = (
            (preco_original - preco)
            / preco_original
        ) * 100

        return round(desconto)

    except (TypeError, ValueError, ZeroDivisionError):
        return None


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
            timeout=REQUEST_TIMEOUT
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


def descobrir_categoria(headers):
    try:
        resposta = requests.get(
            (
                "https://api.mercadolibre.com/"
                "sites/MLB/domain_discovery/search"
            ),
            headers=headers,
            params={
                "q": TERMO_ATUAL,
                "limit": 1
            },
            timeout=REQUEST_TIMEOUT
        )

    except requests.RequestException:
        return None

    if resposta.status_code != 200:
        return None

    try:
        resultados = resposta.json()
    except ValueError:
        return None

    if not resultados:
        return None

    return resultados[0].get("category_id")


def consultar_ranking(category_id, headers):
    try:
        resposta = requests.get(
            (
                "https://api.mercadolibre.com/"
                f"highlights/MLB/category/{category_id}"
            ),
            headers=headers,
            timeout=REQUEST_TIMEOUT
        )

    except requests.RequestException:
        return []

    if resposta.status_code != 200:
        return []

    try:
        dados = resposta.json()
    except ValueError:
        return []

    ranking = dados.get("content") or []

    ranking = sorted(
        ranking,
        key=lambda x: x.get("position", 999)
    )

    # So testamos os 5 primeiros.
    return ranking[:5]


def consultar_item(item_id, headers):
    try:
        resposta = requests.get(
            f"https://api.mercadolibre.com/items/{item_id}",
            headers=headers,
            timeout=REQUEST_TIMEOUT
        )

    except requests.RequestException:
        return None

    if resposta.status_code != 200:
        return None

    try:
        item = resposta.json()
    except ValueError:
        return None

    if item.get("status") != "active":
        return None

    permalink = item.get("permalink")

    if not permalink:
        return None

    preco = item.get("price")
    preco_original = item.get("original_price")

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
        "nome": item.get("title") or "Produto",
        "preco_numero": preco,
        "preco": formatar_preco(preco),
        "preco_original": preco_original,
        "imagem": imagem,
        "link_normal": permalink
    }


def converter_product(product_id, headers):
    try:
        resposta = requests.get(
            (
                "https://api.mercadolibre.com/"
                f"products/{product_id}"
            ),
            headers=headers,
            timeout=REQUEST_TIMEOUT
        )

    except requests.RequestException:
        return None

    if resposta.status_code != 200:
        return None

    try:
        produto = resposta.json()
    except ValueError:
        return None

    vencedor = produto.get("buy_box_winner") or {}

    item_id = vencedor.get("item_id")

    if not item_id:
        return None

    oferta = consultar_item(
        item_id,
        headers
    )

    if not oferta:
        return None

    preco = vencedor.get("price")

    if preco is not None:
        oferta["preco_numero"] = preco
        oferta["preco"] = formatar_preco(preco)

    preco_original = vencedor.get("original_price")

    if preco_original is not None:
        oferta["preco_original"] = preco_original

    oferta["product_id"] = str(product_id)

    return oferta


def encontrar_oferta(headers):
    category_id = descobrir_categoria(headers)

    if not category_id:
        return None, "categoria"

    ranking = consultar_ranking(
        category_id,
        headers
    )

    if not ranking:
        return None, "ranking"

    tipos_encontrados = []

    for resultado in ranking:
        tipo = str(
            resultado.get("type", "")
        ).upper()

        resultado_id = resultado.get("id")
        posicao = resultado.get("position")

        tipos_encontrados.append(tipo)

        if not resultado_id:
            continue

        oferta = None

        if tipo == "ITEM":
            oferta = consultar_item(
                resultado_id,
                headers
            )

        elif tipo == "PRODUCT":
            oferta = converter_product(
                resultado_id,
                headers
            )

        # USER_PRODUCT fica para a proxima etapa.
        elif tipo == "USER_PRODUCT":
            continue

        if not oferta:
            continue

        oferta["ranking_tipo"] = tipo
        oferta["ranking_id"] = str(resultado_id)
        oferta["posicao"] = posicao
        oferta["categoria_id"] = category_id

        oferta["desconto"] = calcular_desconto(
            oferta.get("preco_numero"),
            oferta.get("preco_original")
        )

        return oferta, "ok"

    tipos = ", ".join(
        sorted(set(tipos_encontrados))
    )

    if not tipos:
        tipos = "desconhecido"

    return None, f"tipos:{tipos}"


@app.route("/buscar-mais-vendidos")
def buscar_mais_vendidos():
    if not ML_ACCESS_TOKEN:
        return """
        <h3>Conecte o Mercado Livre primeiro.</h3>
        <p>
            <a href="/login">
                Conectar Mercado Livre
            </a>
        </p>
        """

    headers = {
        "Authorization": f"Bearer {ML_ACCESS_TOKEN}"
    }

    oferta, motivo = encontrar_oferta(headers)

    if not oferta:
        if motivo == "categoria":
            return """
            <h3>Nao consegui descobrir a categoria.</h3>
            """

        if motivo == "ranking":
            return """
            <h3>Nao consegui carregar o ranking.</h3>
            """

        return f"""
        <h3>
            Ranking carregou, mas ainda nao achei
            anuncio compravel.
        </h3>

        <p>
            Tipos encontrados nos primeiros resultados:
            {motivo.replace("tipos:", "")}
        </p>

        <p>
            Importante: o bot respondeu sem travar.
        </p>
        """

    item_id = oferta["item_id"]

    OFERTAS[item_id] = oferta

    nome = oferta["nome"]
    preco = oferta["preco"]
    imagem = oferta["imagem"]
    link_normal = oferta["link_normal"]
    posicao = oferta.get("posicao", "?")
    desconto = oferta.get("desconto")

    legenda = (
        "🔥 MAIS VENDIDO ENCONTRADO!\n\n"
        f"🏆 Ranking: #{posicao}\n\n"
        f"📦 {nome}\n\n"
    )

    if desconto:
        preco_original = formatar_preco(
            oferta.get("preco_original")
        )

        legenda += (
            f"🏷️ {desconto}% OFF\n"
            f"❌ De: {preco_original}\n"
            f"✅ Por: {preco}\n\n"
        )

    else:
        legenda += (
            f"💰 Preco: {preco}\n\n"
        )

    legenda += (
        "🔗 LINK DO PRODUTO:\n"
        f"{link_normal}\n\n"
        "👇 Deseja preparar essa oferta?"
    )

    botoes = {
        "inline_keyboard": [
            [
                {
                    "text": "✅ PUBLICAR",
                    "callback_data":
                        f"publicar:{item_id}"
                },
                {
                    "text": "❌ IGNORAR",
                    "callback_data":
                        f"ignorar:{item_id}"
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
    <h2>OFERTA ENVIADA! 🔥</h2>
    <p>Confira seu Telegram.</p>
    """


@app.route(
    "/telegram/webhook",
    methods=["POST"]
)
def telegram_webhook():
    update = request.get_json(
        silent=True
    ) or {}

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

        if str(chat_id) != str(
            TELEGRAM_CHAT_ID
        ):
            if callback_id:
                telegram_api(
                    "answerCallbackQuery",
                    {
                        "callback_query_id":
                            callback_id,
                        "text":
                            "Acesso nao autorizado."
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
                    "callback_query_id":
                        callback_id,
                    "text":
                        "Oferta ignorada ❌"
                }
            )

            telegram_api(
                "sendMessage",
                {
                    "chat_id":
                        TELEGRAM_CHAT_ID,
                    "text":
                        "❌ Oferta descartada."
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
                        "callback_query_id":
                            callback_id,
                        "text":
                            "Oferta expirou."
                    }
                )

                telegram_api(
                    "sendMessage",
                    {
                        "chat_id":
                            TELEGRAM_CHAT_ID,
                        "text": (
                            "⚠️ Essa oferta expirou.\n\n"
                            "Busque uma nova."
                        )
                    }
                )

                return "OK", 200

            link_normal = oferta.get(
                "link_normal",
                ""
            )

            AGUARDANDO_LINK[
                str(chat_id)
            ] = item_id

            telegram_api(
                "answerCallbackQuery",
                {
                    "callback_query_id":
                        callback_id,
                    "text":
                        "Oferta aprovada! ✅"
                }
            )

            telegram_api(
                "sendMessage",
                {
                    "chat_id":
                        TELEGRAM_CHAT_ID,
                    "text": (
                        "💰 OFERTA APROVADA!\n\n"
                        "🔗 COPIE ESTE LINK:\n\n"
                        f"{link_normal}\n\n"
                        "Gere seu link de afiliado "
                        "no Mercado Livre e mande "
                        "o link gerado aqui. 👇"
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

        if str(chat_id) != str(
            TELEGRAM_CHAT_ID
        ):
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
                    "chat_id":
                        TELEGRAM_CHAT_ID,
                    "text": (
                        "⚠️ Mande o link completo "
                        "de afiliado."
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
                    "chat_id":
                        TELEGRAM_CHAT_ID,
                    "text": (
                        "⚠️ Oferta expirou. "
                        "Busque uma nova."
                    )
                }
            )

            return "OK", 200

        oferta["link_afiliado"] = texto

        AGUARDANDO_LINK.pop(
            str(chat_id),
            None
        )

        telegram_api(
            "sendMessage",
            {
                "chat_id":
                    TELEGRAM_CHAT_ID,
                "text": (
                    "✅ LINK RECEBIDO!\n\n"
                    f"📦 {oferta['nome']}\n\n"
                    f"💰 {oferta['preco']}\n\n"
                    "🔒 Ainda NAO publiquei "
                    "no canal."
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
