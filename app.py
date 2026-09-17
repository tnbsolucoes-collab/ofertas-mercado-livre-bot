from flask import Flask, request, redirect
import os
import requests

app = Flask(__name__)

CLIENT_ID = os.environ.get("ML_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("ML_CLIENT_SECRET", "").strip()

TELEGRAM_BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN", ""
).strip()

TELEGRAM_CHAT_ID = os.environ.get(
    "TELEGRAM_CHAT_ID", ""
).strip()

BASE_URL = "https://ofertas-mercado-livre-bot.onrender.com"
REDIRECT_URI = f"{BASE_URL}/oauth/callback"

ML_ACCESS_TOKEN = None

TIMEOUT = 5

TERMOS = [
    "iphone",
    "samsung galaxy",
    "motorola",
    "fone bluetooth",
    "air fryer",
    "smart tv",
    "notebook",
    "perfume"
]


def ml_headers():
    return {
        "Authorization": f"Bearer {ML_ACCESS_TOKEN}"
    }


def formatar_preco(valor):
    if valor is None:
        return "-"

    try:
        valor = float(valor)
    except (TypeError, ValueError):
        return str(valor)

    texto = f"R$ {valor:,.2f}"

    return (
        texto
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def calcular_desconto(preco, original):
    try:
        preco = float(preco)
        original = float(original)

        if original <= preco or original <= 0:
            return None

        desconto = (
            (original - preco) / original
        ) * 100

        return round(desconto)

    except (TypeError, ValueError):
        return None


def telegram_api(metodo, payload):
    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/{metodo}"
    )

    return requests.post(
        url,
        json=payload,
        timeout=TIMEOUT
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
        <a href="/buscar-buybox">
            2 - Procurar oferta real 🔥
        </a>
    </p>
    """


@app.route("/login")
def login():
    url = (
        "https://auth.mercadolivre.com.br/authorization"
        "?response_type=code"
        f"&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
    )

    return redirect(url)


@app.route("/oauth/callback")
def oauth_callback():
    global ML_ACCESS_TOKEN

    code = request.args.get("code")

    if not code:
        return "Codigo OAuth nao recebido."

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
            timeout=TIMEOUT
        )

    except requests.RequestException:
        return "Erro ao conectar ao Mercado Livre."

    if resposta.status_code != 200:
        return (
            "Erro OAuth. HTTP "
            f"{resposta.status_code}"
        )

    try:
        dados = resposta.json()
    except ValueError:
        return "Resposta OAuth invalida."

    ML_ACCESS_TOKEN = dados.get("access_token")

    if not ML_ACCESS_TOKEN:
        return "Access token nao recebido."

    return """
    <h2>Mercado Livre conectado! ✅</h2>

    <p>
        <a href="/buscar-buybox">
            Procurar oferta real 🔥
        </a>
    </p>
    """


def buscar_produtos(termo):
    try:
        resposta = requests.get(
            (
                "https://api.mercadolibre.com/"
                "products/search"
            ),
            headers=ml_headers(),
            params={
                "status": "active",
                "site_id": "MLB",
                "q": termo,
                "limit": 10
            },
            timeout=TIMEOUT
        )

    except requests.RequestException:
        return [], "ERRO_CONEXAO"

    if resposta.status_code != 200:
        return [], str(resposta.status_code)

    try:
        dados = resposta.json()
    except ValueError:
        return [], "JSON_INVALIDO"

    return dados.get("results") or [], "200"


def consultar_produto(product_id):
    try:
        resposta = requests.get(
            (
                "https://api.mercadolibre.com/"
                f"products/{product_id}"
            ),
            headers=ml_headers(),
            timeout=TIMEOUT
        )

    except requests.RequestException:
        return None

    if resposta.status_code != 200:
        return None

    try:
        return resposta.json()
    except ValueError:
        return None


def imagem_produto(produto):
    pictures = produto.get("pictures") or []

    if pictures:
        primeira = pictures[0]

        if isinstance(primeira, dict):
            return (
                primeira.get("secure_url")
                or primeira.get("url")
                or ""
            )

    return ""


def extrair_oferta(produto, termo):
    buybox = produto.get("buy_box_winner")

    if not isinstance(buybox, dict):
        return None

    item_id = buybox.get("item_id")
    preco = buybox.get("price")

    if not item_id or preco is None:
        return None

    original = buybox.get("original_price")

    desconto = calcular_desconto(
        preco,
        original
    )

    nome = (
        produto.get("name")
        or produto.get("family_name")
        or "Produto Mercado Livre"
    )

    # Preferimos o permalink oficial
    # da pagina de produto retornado pela API.
    link = produto.get("permalink")

    if not link:
        return None

    return {
        "item_id": item_id,
        "product_id": produto.get("id"),
        "titulo": nome,
        "preco": preco,
        "original": original,
        "desconto": desconto,
        "link": link,
        "imagem": imagem_produto(produto),
        "termo": termo,
        "frete_gratis": (
            buybox.get("shipping", {})
            .get("free_shipping", False)
        )
    }


def encontrar_oferta():
    diagnostico = []

    for termo in TERMOS:

        produtos, status = buscar_produtos(termo)

        diagnostico.append(
            f"{termo}: HTTP {status} / "
            f"{len(produtos)} produtos"
        )

        if status != "200":
            continue

        # Evita dezenas de requisicoes.
        # Primeiro usamos os dados retornados
        # pelo proprio products/search.
        for resumo in produtos:

            buybox = resumo.get(
                "buy_box_winner"
            )

            if isinstance(buybox, dict):

                oferta = extrair_oferta(
                    resumo,
                    termo
                )

                if oferta:
                    return oferta, diagnostico

        # Se a resposta resumida nao trouxe
        # buy box, consultamos poucos produtos.
        for resumo in produtos[:4]:

            product_id = resumo.get("id")

            if not product_id:
                continue

            produto = consultar_produto(
                product_id
            )

            if not produto:
                continue

            oferta = extrair_oferta(
                produto,
                termo
            )

            if oferta:
                return oferta, diagnostico

    return None, diagnostico


def enviar_telegram(oferta):
    preco = formatar_preco(
        oferta["preco"]
    )

    original = oferta["original"]
    desconto = oferta["desconto"]

    linhas_preco = f"💰 Preco: {preco}"

    if original and desconto:
        linhas_preco = (
            f"~~{formatar_preco(original)}~~\n"
            f"🔥 {preco}\n"
            f"📉 {desconto}% OFF"
        )

    frete = ""

    if oferta["frete_gratis"]:
        frete = "\n🚚 Frete gratis"

    texto = (
        "🔥 OFERTA REAL ENCONTRADA!\n\n"
        f"📦 {oferta['titulo']}\n\n"
        f"{linhas_preco}"
        f"{frete}\n\n"
        f"🔎 Item: {oferta['item_id']}\n\n"
        "🛒 VER PRODUTO:\n"
        f"{oferta['link']}"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID
    }

    imagem = oferta["imagem"]

    if imagem:
        payload["photo"] = imagem
        payload["caption"] = texto
        payload["parse_mode"] = "Markdown"

        return telegram_api(
            "sendPhoto",
            payload
        )

    payload["text"] = texto
    payload["parse_mode"] = "Markdown"

    return telegram_api(
        "sendMessage",
        payload
    )


@app.route("/buscar-buybox")
def buscar_buybox():
    if not ML_ACCESS_TOKEN:
        return """
        <h3>Conecte o Mercado Livre primeiro.</h3>

        <p>
            <a href="/login">
                Conectar Mercado Livre
            </a>
        </p>
        """

    oferta, diagnostico = encontrar_oferta()

    if not oferta:
        linhas = ""

        for linha in diagnostico:
            linhas += f"<p>{linha}</p>"

        return f"""
        <h2>Busca concluida ✅</h2>

        <p>
            Nenhum Buy Box utilizavel foi
            encontrado nesta rodada.
        </p>

        <h3>Diagnostico:</h3>

        {linhas}

        <p>
            Nenhum produto errado foi enviado.
        </p>
        """

    try:
        resposta_tg = enviar_telegram(
            oferta
        )

    except requests.RequestException:
        return """
        <h2>Oferta encontrada ✅</h2>

        <p>
            Mas ocorreu erro ao enviar
            para o Telegram.
        </p>
        """

    if resposta_tg.status_code != 200:
        return (
            "<h2>Oferta encontrada ✅</h2>"
            "<p>Telegram respondeu HTTP "
            f"{resposta_tg.status_code}</p>"
        )

    desconto = oferta["desconto"]

    info_desconto = (
        f"{desconto}% OFF"
        if desconto
        else "sem desconto informado"
    )

    return f"""
    <h2>OFERTA REAL ENCONTRADA! 🔥🔥🔥</h2>

    <p>
        Produto enviado para seu Telegram.
    </p>

    <p>
        Item: {oferta['item_id']}
    </p>

    <p>
        Desconto:
        {info_desconto}
    </p>

    <p>
        Agora confira o produto no Telegram.
    </p>
    """


if __name__ == "__main__":
    port = int(
        os.environ.get("PORT", 5000)
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
