from flask import Flask, request, redirect
import os
import requests
import html

app = Flask(__name__)

CLIENT_ID = os.environ.get("ML_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("ML_CLIENT_SECRET", "").strip()

BASE_URL = "https://ofertas-mercado-livre-bot.onrender.com"
REDIRECT_URI = f"{BASE_URL}/oauth/callback"

ML_ACCESS_TOKEN = None

TIMEOUT = 5
TERMO = "perfume"


def ml_headers():
    return {
        "Authorization": f"Bearer {ML_ACCESS_TOKEN}"
    }


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
        <a href="/diagnostico-user-product">
            2 - Testar USER_PRODUCT 🔎
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
        <a href="/diagnostico-user-product">
            Testar USER_PRODUCT 🔎
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
            headers=ml_headers(),
            params={
                "q": TERMO,
                "limit": 1
            },
            timeout=TIMEOUT
        )

    except requests.RequestException:
        return None, "ERRO_CONEXAO"

    if resposta.status_code != 200:
        return None, str(resposta.status_code)

    try:
        dados = resposta.json()
    except ValueError:
        return None, "JSON_INVALIDO"

    if not dados:
        return None, "VAZIO"

    return dados[0].get("category_id"), "200"


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
        return [], "ERRO_CONEXAO"

    if resposta.status_code != 200:
        return [], str(resposta.status_code)

    try:
        dados = resposta.json()
    except ValueError:
        return [], "JSON_INVALIDO"

    return dados.get("content") or [], "200"


def consultar_user_product(user_product_id):
    try:
        resposta = requests.get(
            (
                "https://api.mercadolibre.com/"
                f"user-products/{user_product_id}"
            ),
            headers=ml_headers(),
            timeout=TIMEOUT
        )

    except requests.RequestException:
        return None, "ERRO_CONEXAO"

    status = str(resposta.status_code)

    if resposta.status_code != 200:
        try:
            erro = resposta.json()
        except ValueError:
            erro = resposta.text[:500]

        return {
            "erro": erro
        }, status

    try:
        return resposta.json(), status
    except ValueError:
        return None, "JSON_INVALIDO"


def valor_seguro(valor):
    if valor is None:
        return "-"

    return html.escape(str(valor))


@app.route("/diagnostico-user-product")
def diagnostico_user_product():
    if not ML_ACCESS_TOKEN:
        return """
        <h3>Conecte o Mercado Livre primeiro.</h3>

        <p>
            <a href="/login">
                Conectar Mercado Livre
            </a>
        </p>
        """

    categoria, status_categoria = descobrir_categoria()

    if not categoria:
        return (
            "<h3>Falha ao descobrir categoria.</h3>"
            f"<p>HTTP: {status_categoria}</p>"
        )

    ranking, status_ranking = consultar_ranking(
        categoria
    )

    if not ranking:
        return (
            "<h3>Ranking vazio ou indisponivel.</h3>"
            f"<p>HTTP: {status_ranking}</p>"
        )

    user_product = None

    for resultado in ranking:
        tipo = str(
            resultado.get("type", "")
        ).upper()

        if tipo == "USER_PRODUCT":
            user_product = resultado
            break

    if not user_product:
        tipos = []

        for resultado in ranking:
            tipos.append(
                str(
                    resultado.get("type", "")
                ).upper()
            )

        tipos = sorted(set(tipos))

        return f"""
        <h2>Nenhum USER_PRODUCT encontrado.</h2>

        <p>
            Categoria: {valor_seguro(categoria)}
        </p>

        <p>
            Tipos encontrados:
            {valor_seguro(", ".join(tipos))}
        </p>
        """

    user_product_id = user_product.get("id")
    posicao = user_product.get("position", "?")

    dados, status = consultar_user_product(
        user_product_id
    )

    if not dados:
        return f"""
        <h2>USER_PRODUCT encontrado ✅</h2>

        <p>
            Ranking: #{valor_seguro(posicao)}
        </p>

        <p>
            ID: {valor_seguro(user_product_id)}
        </p>

        <p>
            Mas a consulta falhou.
            HTTP: {valor_seguro(status)}
        </p>
        """

    if status != "200":
        return f"""
        <h2>USER_PRODUCT encontrado ✅</h2>

        <p>
            Ranking: #{valor_seguro(posicao)}
        </p>

        <p>
            ID: {valor_seguro(user_product_id)}
        </p>

        <p>
            Consulta /user-products:
            HTTP {valor_seguro(status)}
        </p>

        <pre>
{valor_seguro(dados)}
        </pre>
        """

    # Mostramos somente campos uteis.
    # Nao exibimos token nem credenciais.
    campos = [
        "id",
        "name",
        "status",
        "site_id",
        "domain_id",
        "catalog_product_id",
        "family_name",
        "user_id",
        "seller_id",
        "item_id",
        "item_ids",
        "permalink",
        "price"
    ]

    linhas = ""

    for campo in campos:
        if campo in dados:
            linhas += (
                "<p><b>"
                f"{valor_seguro(campo)}"
                ":</b> "
                f"{valor_seguro(dados.get(campo))}"
                "</p>"
            )

    # Alguns dados podem estar dentro
    # de estruturas internas.
    chaves = ", ".join(
        sorted(dados.keys())
    )

    return f"""
    <h2>USER_PRODUCT CONSULTADO! 🔥</h2>

    <p>
        <b>Termo:</b> perfume
    </p>

    <p>
        <b>Categoria:</b>
        {valor_seguro(categoria)}
    </p>

    <p>
        <b>Ranking:</b>
        #{valor_seguro(posicao)}
    </p>

    <p>
        <b>USER_PRODUCT ID:</b>
        {valor_seguro(user_product_id)}
    </p>

    <p>
        <b>HTTP /user-products:</b>
        {valor_seguro(status)}
    </p>

    <hr>

    <h3>Campos encontrados:</h3>

    {linhas}

    <hr>

    <p>
        <b>Todas as chaves recebidas:</b>
        {valor_seguro(chaves)}
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
