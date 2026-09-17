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
        <a href="/diagnostico-ranking">
            2 - Diagnosticar ranking 🔎
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
        return "Resposta invalida do Mercado Livre."

    ML_ACCESS_TOKEN = dados.get("access_token")

    if not ML_ACCESS_TOKEN:
        return "Access token nao recebido."

    return """
    <h2>Mercado Livre conectado! ✅</h2>

    <p>
        <a href="/diagnostico-ranking">
            Executar diagnostico 🔎
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

    return ranking[:5]


def diagnosticar_product(product_id, headers):
    resultado = {
        "id": str(product_id),
        "http": None,
        "status": "-",
        "tem_buy_box": False,
        "item_id": "-",
        "catalog_product_id": "-",
        "domain_id": "-",
        "children": "-"
    }

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
        resultado["http"] = "ERRO_CONEXAO"
        return resultado

    resultado["http"] = resposta.status_code

    if resposta.status_code != 200:
        return resultado

    try:
        produto = resposta.json()
    except ValueError:
        resultado["status"] = "JSON_INVALIDO"
        return resultado

    resultado["status"] = produto.get(
        "status",
        "-"
    )

    resultado["catalog_product_id"] = produto.get(
        "catalog_product_id",
        "-"
    )

    resultado["domain_id"] = produto.get(
        "domain_id",
        "-"
    )

    buy_box = produto.get("buy_box_winner")

    if isinstance(buy_box, dict) and buy_box:
        resultado["tem_buy_box"] = True

        resultado["item_id"] = buy_box.get(
            "item_id",
            "-"
        )

    children = (
        produto.get("children_ids")
        or produto.get("children")
        or produto.get("variations")
        or []
    )

    if isinstance(children, list):
        resultado["children"] = len(children)
    elif children:
        resultado["children"] = "SIM"
    else:
        resultado["children"] = 0

    return resultado


@app.route("/diagnostico-ranking")
def diagnostico_ranking():
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
        return """
        <h3>
            Nao consegui descobrir a categoria.
        </h3>
        """

    ranking = consultar_ranking(
        category_id,
        headers
    )

    if not ranking:
        return """
        <h3>
            Ranking nao retornou resultados.
        </h3>
        """

    linhas = []

    for resultado_ranking in ranking:
        tipo = str(
            resultado_ranking.get("type", "")
        ).upper()

        resultado_id = resultado_ranking.get("id")
        posicao = resultado_ranking.get(
            "position",
            "?"
        )

        if tipo != "PRODUCT":
            linhas.append(
                {
                    "posicao": posicao,
                    "tipo": tipo,
                    "id": resultado_id,
                    "http": "-",
                    "status": "-",
                    "buy_box": "-",
                    "item_id": "-",
                    "catalog": "-",
                    "children": "-"
                }
            )

            continue

        diagnostico = diagnosticar_product(
            resultado_id,
            headers
        )

        linhas.append(
            {
                "posicao": posicao,
                "tipo": tipo,
                "id": diagnostico["id"],
                "http": diagnostico["http"],
                "status": diagnostico["status"],
                "buy_box": (
                    "SIM"
                    if diagnostico["tem_buy_box"]
                    else "NAO"
                ),
                "item_id": diagnostico["item_id"],
                "catalog": diagnostico[
                    "catalog_product_id"
                ],
                "children": diagnostico["children"]
            }
        )

    html = f"""
    <h2>DIAGNOSTICO DO RANKING 🔎</h2>

    <p>
        Categoria descoberta:
        <b>{category_id}</b>
    </p>

    <p>
        Termo:
        <b>{TERMO_ATUAL}</b>
    </p>

    <hr>
    """

    for linha in linhas:
        html += f"""
        <h3>
            Ranking #{linha["posicao"]}
        </h3>

        <p>
            Tipo:
            <b>{linha["tipo"]}</b>
        </p>

        <p>
            ID:
            <b>{linha["id"]}</b>
        </p>

        <p>
            HTTP /products:
            <b>{linha["http"]}</b>
        </p>

        <p>
            Status:
            <b>{linha["status"]}</b>
        </p>

        <p>
            Tem buy_box_winner:
            <b>{linha["buy_box"]}</b>
        </p>

        <p>
            item_id:
            <b>{linha["item_id"]}</b>
        </p>

        <p>
            catalog_product_id:
            <b>{linha["catalog"]}</b>
        </p>

        <p>
            Filhos/variacoes detectados:
            <b>{linha["children"]}</b>
        </p>

        <hr>
        """

    html += """
    <p>
        Nenhum token ou segredo foi exibido
        neste diagnostico.
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
