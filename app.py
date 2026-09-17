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
TERMO = "smartphone"


def headers_ml():
    return {
        "Authorization": f"Bearer {ML_ACCESS_TOKEN}"
    }


def telegram_api(metodo, payload):
    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/{metodo}"
    )

    return requests.post(
        url,
        json=payload,
        timeout=TIMEOUT
    )


def formatar_preco(valor):
    try:
        valor = float(valor)
    except (TypeError, ValueError):
        return "Consulte o preco"

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
        <a href="/buscar-oferta">
            2 - Buscar oferta compravel 🔥
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
            "Erro OAuth. Codigo HTTP: "
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
        <a href="/buscar-oferta">
            Buscar oferta compravel 🔥
        </a>
    </p>
    """


def descobrir_categoria():
    try:
        resposta = requests.get(
            (
                "https://api.mercadolibre.com/"
                "sites/MLB/domain_discovery/search"
            ),
            headers=headers_ml(),
            params={
                "q": TERMO,
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


def ranking_categoria(category_id):
    try:
        resposta = requests.get(
            (
                "https://api.mercadolibre.com/"
                f"highlights/MLB/category/{category_id}"
            ),
            headers=headers_ml(),
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

    ranking = dados.get("content") or []

    return sorted(
        ranking,
        key=lambda x: x.get("position", 999)
    )[:3]


def consultar_product(product_id):
    try:
        resposta = requests.get(
            (
                "https://api.mercadolibre.com/"
                f"products/{product_id}"
            ),
            headers=headers_ml(),
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


def procurar_anuncios(nome):
    """
    Tenta a busca publica do site para obter
    anuncios compraveis relacionados ao nome.

    Se o Mercado Livre bloquear essa busca,
    devolvemos o codigo HTTP para diagnostico,
    em vez de ficar fazendo varias chamadas.
    """

    try:
        resposta = requests.get(
            "https://api.mercadolibre.com/sites/MLB/search",
            params={
                "q": nome,
                "limit": 5
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


def escolher_anuncio(anuncios, product_id):
    """
    Prioriza anuncio cujo catalog_product_id
    seja exatamente o PRODUCT do ranking.
    """

    for anuncio in anuncios:
        if (
            str(anuncio.get("catalog_product_id"))
            == str(product_id)
        ):
            if (
                anuncio.get("permalink")
                and anuncio.get("price") is not None
            ):
                return anuncio

    # Se nenhum bateu exatamente, NAO usamos
    # produto parecido. Isso evita mandar oferta errada.
    return None


@app.route("/buscar-oferta")
def buscar_oferta():
    if not ML_ACCESS_TOKEN:
        return """
        <h3>Conecte o Mercado Livre primeiro.</h3>

        <p>
            <a href="/login">
                Conectar Mercado Livre
            </a>
        </p>
        """

    categoria = descobrir_categoria()

    if not categoria:
        return "Nao consegui descobrir a categoria."

    ranking = ranking_categoria(categoria)

    if not ranking:
        return "Nao consegui carregar o ranking."

    relatorio = []

    for posicao in ranking:
        tipo = str(
            posicao.get("type", "")
        ).upper()

        product_id = posicao.get("id")
        ranking_posicao = posicao.get(
            "position",
            "?"
        )

        if tipo != "PRODUCT":
            continue

        produto = consultar_product(product_id)

        if not produto:
            continue

        nome = (
            produto.get("name")
            or produto.get("family_name")
        )

        if not nome:
            continue

        anuncios, http_busca = procurar_anuncios(nome)

        relatorio.append(
            (
                ranking_posicao,
                product_id,
                http_busca
            )
        )

        if http_busca != "200":
            # Nao insistimos para evitar timeout.
            break

        anuncio = escolher_anuncio(
            anuncios,
            product_id
        )

        if not anuncio:
            continue

        titulo = anuncio.get("title") or nome
        preco = anuncio.get("price")
        link = anuncio.get("permalink")

        imagem = anuncio.get("thumbnail") or ""

        texto = (
            "🔥 OFERTA COMPRAVEL ENCONTRADA!\n\n"
            f"🏆 Ranking: #{ranking_posicao}\n\n"
            f"📦 {titulo}\n\n"
            f"💰 {formatar_preco(preco)}\n\n"
            "🔗 LINK REAL:\n"
            f"{link}"
        )

        payload = {
            "chat_id": TELEGRAM_CHAT_ID
        }

        try:
            if imagem:
                payload["photo"] = imagem
                payload["caption"] = texto

                resposta_tg = telegram_api(
                    "sendPhoto",
                    payload
                )

            else:
                payload["text"] = texto

                resposta_tg = telegram_api(
                    "sendMessage",
                    payload
                )

        except requests.RequestException:
            return "Oferta encontrada, mas Telegram falhou."

        if resposta_tg.status_code != 200:
            return (
                "Oferta encontrada, mas Telegram "
                f"retornou {resposta_tg.status_code}."
            )

        return """
        <h2>OFERTA COMPRAVEL ENCONTRADA! 🔥</h2>
        <p>Confira seu Telegram.</p>
        """

    if relatorio:
        linhas = ""

        for pos, pid, status in relatorio:
            linhas += (
                f"<p>Ranking #{pos} - "
                f"{pid} - "
                f"Busca HTTP: <b>{status}</b></p>"
            )

        return f"""
        <h2>Ranking funcionou ✅</h2>

        <p>
            Mas ainda nao conseguimos obter
            um anuncio compravel correspondente.
        </p>

        {linhas}

        <p>
            Nenhum produto parecido foi enviado.
        </p>
        """

    return """
    <h3>
        Nenhum PRODUCT valido encontrado.
    </h3>
    """


if __name__ == "__main__":
    port = int(
        os.environ.get("PORT", 5000)
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
