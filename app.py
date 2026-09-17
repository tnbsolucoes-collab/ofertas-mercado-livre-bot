from flask import Flask, request, redirect
import os
import requests
import html
import json

app = Flask(__name__)

CLIENT_ID = os.environ.get("ML_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("ML_CLIENT_SECRET", "").strip()

BASE_URL = "https://ofertas-mercado-livre-bot.onrender.com"
REDIRECT_URI = f"{BASE_URL}/oauth/callback"

ML_ACCESS_TOKEN = None
TIMEOUT = 5


def ml_headers():
    return {
        "Authorization": f"Bearer {ML_ACCESS_TOKEN}"
    }


def seguro(valor):
    if valor is None:
        return "-"

    if isinstance(valor, (dict, list)):
        valor = json.dumps(
            valor,
            ensure_ascii=False,
            indent=2
        )

    return html.escape(str(valor))


@app.route("/")
def home():
    return """
    <h2>Bot Mercado Livre 🤖</h2>

    <p>
        <a href="/login">
            1 - Conectar Mercado Livre
        </a>
    </p>

    <p>
        <a href="/diagnostico-produto">
            2 - Diagnosticar produto 🔎
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
        return "Erro de conexao."

    if resposta.status_code != 200:
        return (
            "Erro OAuth HTTP "
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
        <a href="/diagnostico-produto">
            Diagnosticar produto 🔎
        </a>
    </p>
    """


def pesquisar():
    try:
        resposta = requests.get(
            "https://api.mercadolibre.com/products/search",
            headers=ml_headers(),
            params={
                "status": "active",
                "site_id": "MLB",
                "q": "iphone",
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


def detalhe(product_id):
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
        return None, "ERRO_CONEXAO"

    if resposta.status_code != 200:
        return None, str(resposta.status_code)

    try:
        return resposta.json(), "200"
    except ValueError:
        return None, "JSON_INVALIDO"


@app.route("/diagnostico-produto")
def diagnostico():
    if not ML_ACCESS_TOKEN:
        return """
        <h3>Conecte o Mercado Livre primeiro.</h3>

        <p>
            <a href="/login">
                Conectar
            </a>
        </p>
        """

    produtos, status = pesquisar()

    if status != "200":
        return (
            "<h3>products/search falhou.</h3>"
            f"<p>HTTP: {seguro(status)}</p>"
        )

    if not produtos:
        return "products/search retornou vazio."

    resumo = produtos[0]

    product_id = resumo.get("id")

    if not product_id:
        return "Primeiro produto veio sem ID."

    produto, status_detalhe = detalhe(product_id)

    if not produto:
        return f"""
        <h2>Produto encontrado</h2>

        <p>ID: {seguro(product_id)}</p>

        <p>
            /products retornou:
            {seguro(status_detalhe)}
        </p>
        """

    children = produto.get("children_ids") or []

    buybox = produto.get("buy_box_winner")

    permalink = produto.get("permalink")

    campos = [
        "id",
        "name",
        "family_name",
        "status",
        "domain_id",
        "catalog_product_id",
        "parent_id",
        "listing_strategy",
        "permalink",
        "children_ids",
        "buy_box_winner"
    ]

    linhas = ""

    for campo in campos:
        linhas += (
            f"<h4>{seguro(campo)}</h4>"
            f"<pre>{seguro(produto.get(campo))}</pre>"
        )

    todas_chaves = ", ".join(
        sorted(produto.keys())
    )

    resultado = f"""
    <h2>DIAGNOSTICO DO PRODUTO 🔎</h2>

    <p>
        <b>Busca:</b> iphone
    </p>

    <p>
        <b>Product ID:</b>
        {seguro(product_id)}
    </p>

    <p>
        <b>HTTP detalhe:</b>
        {seguro(status_detalhe)}
    </p>

    <hr>

    {linhas}

    <hr>

    <h3>Resumo</h3>

    <p>
        Children encontrados:
        <b>{len(children)}</b>
    </p>

    <p>
        Tem permalink:
        <b>{"SIM" if permalink else "NAO"}</b>
    </p>

    <p>
        Tem Buy Box:
        <b>{"SIM" if isinstance(buybox, dict) else "NAO"}</b>
    </p>

    <h3>Todas as chaves recebidas</h3>

    <p>{seguro(todas_chaves)}</p>
    """

    return resultado


if __name__ == "__main__":
    port = int(
        os.environ.get("PORT", 5000)
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
