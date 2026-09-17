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

# Primeiro vendedor de teste.
# Se o nickname nao corresponder, o diagnostico
# vai mostrar sem inventar produto.
NICKNAME = "samsung"


def ml_headers():
    return {
        "Authorization": f"Bearer {ML_ACCESS_TOKEN}"
    }


def formatar_preco(valor):
    if valor is None:
        return "Preco nao informado"

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

        return round(
            ((original - preco) / original) * 100
        )

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
        <a href="/buscar-vendedor">
            2 - Buscar anuncio real 🔥
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
        <a href="/buscar-vendedor">
            Buscar anuncio real 🔥
        </a>
    </p>
    """


def buscar_por_nickname():
    try:
        resposta = requests.get(
            (
                "https://api.mercadolibre.com/"
                "sites/MLB/search"
            ),
            headers=ml_headers(),
            params={
                "nickname": NICKNAME,
                "limit": 10
            },
            timeout=TIMEOUT
        )

    except requests.RequestException:
        return [], "ERRO_CONEXAO"

    status = str(resposta.status_code)

    if resposta.status_code != 200:
        return [], status

    try:
        dados = resposta.json()
    except ValueError:
        return [], "JSON_INVALIDO"

    return dados.get("results") or [], "200"


def escolher_anuncio(resultados):
    candidatos = []

    for item in resultados:
        item_id = item.get("id")
        titulo = item.get("title")
        link = item.get("permalink")
        preco = item.get("price")

        if not all([
            item_id,
            titulo,
            link
        ]):
            continue

        if preco is None:
            continue

        original = item.get("original_price")

        desconto = calcular_desconto(
            preco,
            original
        )

        thumbnail = (
            item.get("thumbnail")
            or ""
        )

        shipping = item.get("shipping") or {}

        candidato = {
            "id": item_id,
            "titulo": titulo,
            "preco": preco,
            "original": original,
            "desconto": desconto,
            "link": link,
            "imagem": thumbnail,
            "frete_gratis": shipping.get(
                "free_shipping",
                False
            )
        }

        candidatos.append(candidato)

    if not candidatos:
        return None

    # Se houver desconto, prioriza o maior.
    candidatos.sort(
        key=lambda x: (
            x["desconto"] is not None,
            x["desconto"] or 0
        ),
        reverse=True
    )

    return candidatos[0]


def enviar_telegram(oferta):
    preco = formatar_preco(
        oferta["preco"]
    )

    original = oferta["original"]
    desconto = oferta["desconto"]

    if original and desconto:
        bloco_preco = (
            f"💸 De: {formatar_preco(original)}\n"
            f"🔥 Por: {preco}\n"
            f"📉 {desconto}% OFF"
        )

    else:
        bloco_preco = (
            f"💰 Preco: {preco}"
        )

    frete = ""

    if oferta["frete_gratis"]:
        frete = "\n🚚 Frete gratis"

    texto = (
        "🔥 OFERTA MERCADO LIVRE!\n\n"
        f"📦 {oferta['titulo']}\n\n"
        f"{bloco_preco}"
        f"{frete}\n\n"
        f"🆔 {oferta['id']}\n\n"
        "🛒 LINK REAL:\n"
        f"{oferta['link']}"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID
    }

    if oferta["imagem"]:
        payload["photo"] = oferta["imagem"]
        payload["caption"] = texto

        return telegram_api(
            "sendPhoto",
            payload
        )

    payload["text"] = texto

    return telegram_api(
        "sendMessage",
        payload
    )


@app.route("/buscar-vendedor")
def buscar_vendedor():
    if not ML_ACCESS_TOKEN:
        return """
        <h3>
            Conecte o Mercado Livre primeiro.
        </h3>

        <p>
            <a href="/login">
                Conectar Mercado Livre
            </a>
        </p>
        """

    resultados, status = buscar_por_nickname()

    if status != "200":
        return f"""
        <h2>Teste do vendedor concluido</h2>

        <p>
            Nickname testado:
            <b>{NICKNAME}</b>
        </p>

        <p>
            Busca HTTP:
            <b>{status}</b>
        </p>

        <p>
            Nenhum produto foi enviado.
        </p>
        """

    if not resultados:
        return f"""
        <h2>API respondeu HTTP 200 ✅</h2>

        <p>
            Mas nenhum anuncio apareceu
            para o nickname:
            <b>{NICKNAME}</b>
        </p>

        <p>
            Vamos trocar pelo seller_id
            correto de uma loja.
        </p>
        """

    oferta = escolher_anuncio(
        resultados
    )

    if not oferta:
        return f"""
        <h2>Anuncios encontrados ✅</h2>

        <p>
            Quantidade:
            {len(resultados)}
        </p>

        <p>
            Mas nenhum tinha todos os
            campos necessarios.
        </p>
        """

    try:
        tg = enviar_telegram(
            oferta
        )

    except requests.RequestException:
        return """
        <h2>ANUNCIO REAL ENCONTRADO! 🔥</h2>

        <p>
            Mas o envio ao Telegram falhou.
        </p>
        """

    if tg.status_code != 200:
        return f"""
        <h2>ANUNCIO REAL ENCONTRADO! 🔥</h2>

        <p>
            Telegram HTTP:
            {tg.status_code}
        </p>
        """

    desconto = oferta["desconto"]

    if desconto:
        resultado_desconto = (
            f"{desconto}% OFF"
        )
    else:
        resultado_desconto = (
            "sem desconto informado"
        )

    return f"""
    <h2>
        ANUNCIO REAL ENCONTRADO! 🔥🔥🔥
    </h2>

    <p>
        ID: {oferta['id']}
    </p>

    <p>
        Desconto:
        {resultado_desconto}
    </p>

    <p>
        Produto enviado para seu Telegram.
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
