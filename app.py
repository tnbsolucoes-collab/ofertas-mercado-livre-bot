from flask import Flask, request, redirect
import os
import requests

app = Flask(__name__)

CLIENT_ID = os.environ.get("ML_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("ML_CLIENT_SECRET", "").strip()

BASE_URL = "https://ofertas-mercado-livre-bot.onrender.com"
REDIRECT_URI = f"{BASE_URL}/oauth/callback"

ML_ACCESS_TOKEN = None
REQUEST_TIMEOUT = 4

TERMO_ATUAL = "smartphone"


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
        <a href="/teste-produtos">
            2 - Testar links dos produtos 🔎
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

    try:
        dados = resposta.json()
    except ValueError:
        return "Resposta invalida."

    ML_ACCESS_TOKEN = dados.get("access_token")

    if not ML_ACCESS_TOKEN:
        return "Access token nao recebido."

    return """
    <h2>Mercado Livre conectado! ✅</h2>

    <p>
        <a href="/teste-produtos">
            Testar produtos 🔎
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
        dados = resposta.json()
    except ValueError:
        return None

    if not dados:
        return None

    return dados[0].get("category_id")


def pegar_ranking(category_id, headers):
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

    return ranking[:5]


def pegar_produto(product_id, headers):
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

    pictures = produto.get("pictures") or []

    imagem = ""

    if pictures:
        primeira = pictures[0]

        if isinstance(primeira, dict):
            imagem = (
                primeira.get("url")
                or primeira.get("secure_url")
                or ""
            )

    settings = produto.get("settings") or {}

    return {
        "id": produto.get("id", product_id),
        "status": produto.get("status", "-"),
        "nome": (
            produto.get("name")
            or produto.get("family_name")
            or "-"
        ),
        "permalink": produto.get("permalink") or "",
        "domain_id": produto.get("domain_id") or "-",
        "parent_id": produto.get("parent_id") or "-",
        "listing_strategy": (
            settings.get("listing_strategy")
            or "-"
        ),
        "imagem": imagem,
        "tem_buy_box": bool(
            produto.get("buy_box_winner")
        )
    }


@app.route("/teste-produtos")
def teste_produtos():
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

    category_id = descobrir_categoria(headers)

    if not category_id:
        return "Nao consegui descobrir a categoria."

    ranking = pegar_ranking(
        category_id,
        headers
    )

    if not ranking:
        return "Ranking vazio."

    html = f"""
    <h2>TESTE DOS PRODUTOS 🔎</h2>

    <p>
        Categoria:
        <b>{category_id}</b>
    </p>

    <p>
        Termo:
        <b>{TERMO_ATUAL}</b>
    </p>

    <hr>
    """

    encontrados = 0

    for resultado in ranking:
        tipo = str(
            resultado.get("type", "")
        ).upper()

        product_id = resultado.get("id")
        posicao = resultado.get(
            "position",
            "?"
        )

        if tipo != "PRODUCT":
            continue

        produto = pegar_produto(
            product_id,
            headers
        )

        if not produto:
            continue

        encontrados += 1

        link = produto["permalink"]

        if link:
            link_html = (
                f'<a href="{link}" '
                f'target="_blank">'
                f'ABRIR PRODUTO 🔗'
                f'</a>'
            )

            resultado_link = "TEM LINK ✅"

        else:
            link_html = "SEM LINK"
            resultado_link = "SEM LINK ❌"

        html += f"""
        <h3>
            Ranking #{posicao}
        </h3>

        <p>
            <b>{produto["nome"]}</b>
        </p>

        <p>
            Product ID:
            <b>{produto["id"]}</b>
        </p>

        <p>
            Status:
            <b>{produto["status"]}</b>
        </p>

        <p>
            Link:
            <b>{resultado_link}</b>
        </p>

        <p>
            Listing strategy:
            <b>{produto["listing_strategy"]}</b>
        </p>

        <p>
            Parent:
            <b>{produto["parent_id"]}</b>
        </p>

        <p>
            Buy box:
            <b>
                {"SIM" if produto["tem_buy_box"] else "NAO"}
            </b>
        </p>

        <p>
            {link_html}
        </p>

        <hr>
        """

    if encontrados == 0:
        html += """
        <h3>
            Nenhum PRODUCT conseguiu ser consultado.
        </h3>
        """

    html += """
    <p>
        🔒 Nenhum token ou segredo foi exibido.
    </p>
    """

    return html


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
