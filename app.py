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

TIMEOUT = 4

# Poucas categorias por execucao para evitar
# WORKER TIMEOUT no Render.
TERMOS = [
    "smartphone",
    "fone bluetooth",
    "air fryer",
    "perfume"
]


def ml_headers():
    return {
        "Authorization": f"Bearer {ML_ACCESS_TOKEN}"
    }


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


def formatar_preco(valor):
    if valor is None:
        return "Consulte o preco"

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
        <a href="/buscar-item-ranking">
            2 - Cacar anuncio nos mais vendidos 🔥
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
        return "Erro de conexao com Mercado Livre."

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
        <a href="/buscar-item-ranking">
            Cacar anuncio 🔥
        </a>
    </p>
    """


def descobrir_categoria(termo):
    try:
        resposta = requests.get(
            (
                "https://api.mercadolibre.com/"
                "sites/MLB/domain_discovery/search"
            ),
            headers=ml_headers(),
            params={
                "q": termo,
                "limit": 1
            },
            timeout=TIMEOUT
        )

    except requests.RequestException:
        return None

    if resposta.status_code != 200:
        return None

    try:
        dados = resposta.json()
    except ValueError:
        return None

    if not dados:
        return None

    return dados[0].get("category_id")


def consultar_ranking(category_id):
    try:
        resposta = requests.get(
            (
                "https://api.mercadolibre.com/"
                f"highlights/MLB/category/{category_id}"
            ),
            headers=ml_headers(),
            timeout=TIMEOUT
        )

    except requests.RequestException:
        return []

    if resposta.status_code != 200:
        return []

    try:
        dados = resposta.json()
    except ValueError:
        return []

    resultados = dados.get("content") or []

    return sorted(
        resultados,
        key=lambda x: x.get("position", 999)
    )[:10]


def consultar_item(item_id):
    try:
        resposta = requests.get(
            (
                "https://api.mercadolibre.com/"
                f"items/{item_id}"
            ),
            headers=ml_headers(),
            timeout=TIMEOUT
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

    pictures = item.get("pictures") or []

    imagem = (
        item.get("secure_thumbnail")
        or item.get("thumbnail")
        or ""
    )

    if pictures:
        primeira = pictures[0]

        if isinstance(primeira, dict):
            imagem = (
                primeira.get("secure_url")
                or primeira.get("url")
                or imagem
            )

    return {
        "id": str(item_id),
        "titulo": item.get("title") or "Produto",
        "preco": item.get("price"),
        "preco_original": item.get("original_price"),
        "link": permalink,
        "imagem": imagem
    }


def encontrar_item_ranking():
    relatorio = []

    for termo in TERMOS:
        categoria = descobrir_categoria(termo)

        if not categoria:
            relatorio.append(
                f"{termo}: categoria nao encontrada"
            )
            continue

        ranking = consultar_ranking(categoria)

        if not ranking:
            relatorio.append(
                f"{termo}: ranking vazio"
            )
            continue

        tipos = []

        for resultado in ranking:
            tipo = str(
                resultado.get("type", "")
            ).upper()

            tipos.append(tipo)

            if tipo != "ITEM":
                continue

            item_id = resultado.get("id")

            if not item_id:
                continue

            item = consultar_item(item_id)

            if not item:
                continue

            item["posicao"] = resultado.get(
                "position",
                "?"
            )

            item["termo"] = termo
            item["categoria"] = categoria

            return item, relatorio

        resumo = ", ".join(
            sorted(set(tipos))
        )

        relatorio.append(
            f"{termo}: {resumo}"
        )

    return None, relatorio


@app.route("/buscar-item-ranking")
def buscar_item_ranking():
    if not ML_ACCESS_TOKEN:
        return """
        <h3>Conecte o Mercado Livre primeiro.</h3>

        <p>
            <a href="/login">
                Conectar Mercado Livre
            </a>
        </p>
        """

    item, relatorio = encontrar_item_ranking()

    if not item:
        linhas = ""

        for linha in relatorio:
            linhas += f"<p>{linha}</p>"

        return f"""
        <h2>
            Busca terminou sem travar ✅
        </h2>

        <p>
            Ainda nao apareceu ITEM compravel
            nos rankings testados.
        </p>

        <h3>Resultado:</h3>

        {linhas}

        <p>
            Nenhum produto errado foi enviado.
        </p>
        """

    titulo = item["titulo"]
    preco = formatar_preco(item["preco"])
    link = item["link"]
    imagem = item["imagem"]
    posicao = item["posicao"]
    termo = item["termo"]

    texto = (
        "🔥 MAIS VENDIDO COM ANUNCIO REAL!\n\n"
        f"🏆 Ranking: #{posicao}\n"
        f"📂 Busca: {termo}\n\n"
        f"📦 {titulo}\n\n"
        f"💰 {preco}\n\n"
        "🔗 LINK REAL DO ANUNCIO:\n"
        f"{link}"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID
    }

    try:
        if imagem:
            payload["photo"] = imagem
            payload["caption"] = texto

            telegram = telegram_api(
                "sendPhoto",
                payload
            )

        else:
            payload["text"] = texto

            telegram = telegram_api(
                "sendMessage",
                payload
            )

    except requests.RequestException:
        return """
        <h3>
            Achei o ITEM, mas o Telegram
            nao respondeu.
        </h3>
        """

    if telegram.status_code != 200:
        return (
            "<h3>Achei o ITEM, mas Telegram HTTP "
            f"{telegram.status_code}</h3>"
        )

    return """
    <h2>ACHAMOS UM ITEM REAL! 🔥🔥🔥</h2>

    <p>
        Produto enviado para o Telegram.
    </p>

    <p>
        O link veio diretamente do anuncio.
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
