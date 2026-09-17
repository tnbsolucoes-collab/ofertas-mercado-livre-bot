from flask import Flask, request
import os
import re
import io
import json
import uuid
import requests
from bs4 import BeautifulSoup
from PIL import Image

app = Flask(__name__)

# =========================================================
# CONFIGURAÇÕES
# =========================================================

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()

BASE_URL = "https://ofertas-mercado-livre-bot.onrender.com"

WEBHOOK_URL = f"{BASE_URL}/telegram/webhook"

OFERTAS_URL = "https://www.mercadolivre.com.br/ofertas"

TIMEOUT = 25

# =========================================================
# MEMÓRIA
# =========================================================

ofertas_pendentes = {}
aguardando_link = {}


# =========================================================
# HEADERS
# =========================================================

def headers():

    return {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/140.0 Safari/537.36"
        ),
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,image/webp,*/*;q=0.8"
        )
    }


# =========================================================
# TELEGRAM
# =========================================================

def telegram(metodo, dados):

    if not BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN não configurado."
        )

    url = (
        "https://api.telegram.org/"
        f"bot{BOT_TOKEN}/{metodo}"
    )

    return requests.post(
        url,
        json=dados,
        timeout=TIMEOUT
    )


def telegram_foto(
    chat_id,
    foto,
    legenda,
    teclado=None
):

    if not BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN não configurado."
        )

    url = (
        "https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendPhoto"
    )

    files = {
        "photo": (
            "produto.jpg",
            foto,
            "image/jpeg"
        )
    }

    data = {
        "chat_id": chat_id,
        "caption": legenda
    }

    if teclado:

        data["reply_markup"] = json.dumps(
            teclado,
            ensure_ascii=False
        )

    return requests.post(
        url,
        data=data,
        files=files,
        timeout=TIMEOUT
    )


# =========================================================
# WEBHOOK
# =========================================================

def configurar_webhook():

    if not BOT_TOKEN:
        print("BOT_TOKEN não configurado.")
        return False

    try:

        resposta = telegram(
            "setWebhook",
            {
                "url": WEBHOOK_URL
            }
        )

        print(
            "WEBHOOK:",
            resposta.text
        )

        return resposta.status_code == 200

    except Exception as erro:

        print(
            "ERRO WEBHOOK:",
            erro
        )

        return False


# =========================================================
# UTILIDADES
# =========================================================

def dinheiro(numero):

    if numero is None:
        return ""

    try:

        numero = float(numero)

        if numero <= 0:
            return ""

        return (
            f"R$ {numero:,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

    except Exception:

        return ""


def numero_monetario(valor):

    if valor is None:
        return 0

    try:

        valor = str(valor)

        valor = (
            valor
            .replace("R$", "")
            .replace(" ", "")
            .replace(".", "")
            .replace(",", ".")
        )

        return float(valor)

    except Exception:

        return 0


def calcular_desconto(
    atual,
    original
):

    if atual <= 0:
        return 0

    if original <= atual:
        return 0

    return round(
        (
            (original - atual)
            / original
        ) * 100
    )


# =========================================================
# ID DO PRODUTO
# =========================================================

def encontrar_id_produto(url):

    if not url:
        return ""

    resultado = re.search(
        r"(MLB\d+)",
        url.upper()
    )

    if resultado:
        return resultado.group(1)

    return ""


# =========================================================
# DADOS DO PRODUTO
# =========================================================

def dados_item_mercado_livre(item_id):

    if not item_id:
        return None

    url = (
        "https://api.mercadolibre.com/items/"
        f"{item_id}"
    )

    try:

        resposta = requests.get(
            url,
            headers=headers(),
            timeout=TIMEOUT
        )

        print(
            "API ITEM:",
            item_id,
            resposta.status_code
        )

        if resposta.status_code != 200:

            print(
                "API ERRO:",
                resposta.text[:500]
            )

            return None

        return resposta.json()

    except Exception as erro:

        print(
            "ERRO API ITEM:",
            erro
        )

        return None


# =========================================================
# IMAGEM
# =========================================================

def baixar_imagem(url):

    if not url:
        return None

    try:

        resposta = requests.get(
            url,
            headers=headers(),
            timeout=TIMEOUT
        )

        print(
            "IMAGEM HTTP:",
            resposta.status_code
        )

        if resposta.status_code != 200:
            return None

        imagem = Image.open(
            io.BytesIO(
                resposta.content
            )
        )

        if imagem.mode != "RGB":

            imagem = imagem.convert("RGB")

        largura, altura = imagem.size

        limite = 1600

        if (
            largura > limite
            or altura > limite
        ):

            imagem.thumbnail(
                (limite, limite)
            )

        memoria = io.BytesIO()

        imagem.save(
            memoria,
            format="JPEG",
            quality=90,
            optimize=True
        )

        memoria.seek(0)

        return memoria.getvalue()

    except Exception as erro:

        print(
            "ERRO BAIXANDO IMAGEM:",
            erro
        )

        return None


# =========================================================
# BUSCAR LINKS DE OFERTAS
# =========================================================

def buscar_links_ofertas():

    try:

        resposta = requests.get(
            OFERTAS_URL,
            headers=headers(),
            timeout=TIMEOUT
        )

    except Exception as erro:

        print(
            "ERRO OFERTAS:",
            erro
        )

        return []

    print(
        "OFERTAS HTTP:",
        resposta.status_code
    )

    if resposta.status_code != 200:
        return []

    soup = BeautifulSoup(
        resposta.text,
        "html.parser"
    )

    encontrados = {}

    for link in soup.find_all(
        "a",
        href=True
    ):

        href = (
            link.get("href")
            or ""
        ).strip()

        if not href:
            continue

        item_id = encontrar_id_produto(
            href
        )

        if not item_id:
            continue

        if href.startswith("/"):

            href = (
                "https://www.mercadolivre.com.br"
                + href
            )

        encontrados[item_id] = href

    print(
        "PRODUTOS ENCONTRADOS:",
        len(encontrados)
    )

    return list(
        encontrados.items()
    )


# =========================================================
# MONTAR PRODUTO
# =========================================================

def montar_produto(
    item_id,
    url_original
):

    dados = dados_item_mercado_livre(
        item_id
    )

    if not dados:
        return None

    # -----------------------------------------------------
    # NOME
    # -----------------------------------------------------

    titulo = (
        dados.get("title")
        or
        dados.get("family_name")
        or
        "Produto Mercado Livre"
    )

    # -----------------------------------------------------
    # PREÇO ATUAL
    # -----------------------------------------------------

    preco = numero_monetario(
        dados.get("price")
    )

    # -----------------------------------------------------
    # PREÇO ORIGINAL
    # -----------------------------------------------------

    original = numero_monetario(
        dados.get("original_price")
    )

    # -----------------------------------------------------
    # IMAGEM
    # -----------------------------------------------------

    imagem = (
        dados.get("thumbnail")
        or ""
    )

    if not imagem:

        pictures = (
            dados.get("pictures")
            or []
        )

        if pictures:

            primeira = pictures[0]

            if isinstance(
                primeira,
                dict
            ):

                imagem = (
                    primeira.get("secure_url")
                    or
                    primeira.get("url")
                    or
                    ""
                )

    # -----------------------------------------------------
    # LINK
    # -----------------------------------------------------

    permalink = (
        dados.get("permalink")
        or url_original
    )

    # -----------------------------------------------------
    # DESCONTO
    # -----------------------------------------------------

    desconto = calcular_desconto(
        preco,
        original
    )

    # Não mostrar R$ 0,00
    if original <= preco:
        original = 0
        desconto = 0

    produto = {

        "id": item_id,

        "titulo": str(
            titulo
        ).strip(),

        "preco": preco,

        "original": original,

        "desconto": desconto,

        "imagem": imagem,

        "link": permalink
    }

    print("")
    print("======================================")
    print("PRODUTO:", produto["titulo"])
    print("ID:", produto["id"])
    print("PREÇO:", produto["preco"])
    print("ORIGINAL:", produto["original"])
    print("DESCONTO:", produto["desconto"])
    print("IMAGEM:", produto["imagem"])
    print("LINK:", produto["link"])
    print("======================================")
    print("")

    return produto


# =========================================================
# TEXTO DE APROVAÇÃO
# =========================================================

def texto_aprovacao(produto):

    texto = (
        "🔥 OFERTA ENCONTRADA!\n\n"
        f"📦 {produto['titulo']}\n\n"
    )

    if produto["preco"] > 0:

        texto += (
            f"🔥 Por: "
            f"{dinheiro(produto['preco'])}\n"
        )

    if produto["original"] > 0:

        texto += (
            f"💵 De: "
            f"{dinheiro(produto['original'])}\n"
        )

    if produto["desconto"] > 0:

        texto += (
            f"📉 "
            f"{produto['desconto']}% OFF\n"
        )

    texto += (
        "\n🔗 Link normal:\n"
        f"{produto['link']}\n\n"
        "⚠️ Confira a oferta antes de publicar."
    )

    return texto


# =========================================================
# ENVIAR APROVAÇÃO
# =========================================================

def enviar_aprovacao(produto):

    token = uuid.uuid4().hex[:12]

    ofertas_pendentes[token] = produto

    teclado = {
        "inline_keyboard": [
            [
                {
                    "text": "✅ APROVAR",
                    "callback_data":
                        f"aprovar:{token}"
                },
                {
                    "text": "❌ DESCARTAR",
                    "callback_data":
                        f"descartar:{token}"
                }
            ]
        ]
    }

    texto = texto_aprovacao(
        produto
    )

    imagem = baixar_imagem(
        produto["imagem"]
    )

    if imagem:

        try:

            resposta = telegram_foto(
                ADMIN_CHAT_ID,
                imagem,
                texto,
                teclado
            )

            if resposta.status_code == 200:

                print(
                    "✅ ENVIADA COM FOTO"
                )

                return resposta

            print(
                "ERRO TELEGRAM FOTO:",
                resposta.text
            )

        except Exception as erro:

            print(
                "ERRO FOTO:",
                erro
            )

    print(
        "⚠️ Enviando somente texto."
    )

    return telegram(
        "sendMessage",
        {
            "chat_id": ADMIN_CHAT_ID,
            "text": texto,
            "reply_markup": teclado
        }
    )


# =========================================================
# EXECUTAR BUSCA
# =========================================================

def executar_busca():

    links = buscar_links_ofertas()

    if not links:
        return 0

    enviadas = 0

    for item_id, url in links:

        produto = montar_produto(
            item_id,
            url
        )

        if not produto:
            continue

        # Sem preço, ignora
        if produto["preco"] <= 0:
            continue

        # Sem preço original, ignora
        if produto["original"] <= produto["preco"]:
            continue

        # Sem desconto, ignora
        if produto["desconto"] <= 0:
            continue

        try:

            resposta = enviar_aprovacao(
                produto
            )

            if resposta.status_code == 200:
                enviadas += 1

        except Exception as erro:

            print(
                "ERRO ENVIANDO OFERTA:",
                erro
            )

        # Máximo 3 por busca
        if enviadas >= 3:
            break

    return enviadas


# =========================================================
# PUBLICAR NO CANAL
# =========================================================

def publicar_canal(
    produto,
    link_afiliado
):

    texto = (
        "🔥 OFERTA DO DIA! 🔥\n\n"
        f"📦 {produto['titulo']}\n\n"
        f"🔥 Por: "
        f"{dinheiro(produto['preco'])}\n"
        f"💵 De: "
        f"{dinheiro(produto['original'])}\n"
        f"📉 "
        f"{produto['desconto']}% OFF\n\n"
        "🛒 COMPRAR AGORA:\n"
        f"{link_afiliado}"
    )

    imagem = baixar_imagem(
        produto["imagem"]
    )

    if imagem:

        try:

            resposta = telegram_foto(
                CHANNEL_ID,
                imagem,
                texto
            )

            if resposta.status_code == 200:

                return resposta

            print(
                "ERRO FOTO CANAL:",
                resposta.text
            )

        except Exception as erro:

            print(
                "ERRO CANAL FOTO:",
                erro
            )

    return telegram(
        "sendMessage",
        {
            "chat_id": CHANNEL_ID,
            "text": texto
        }
    )


# =========================================================
# CALLBACK
# =========================================================

def callback_telegram(callback):

    callback_id = callback.get(
        "id"
    )

    data = callback.get(
        "data",
        ""
    )

    if callback_id:

        try:

            telegram(
                "answerCallbackQuery",
                {
                    "callback_query_id":
                        callback_id
                }
            )

        except Exception:
            pass

    if ":" not in data:
        return

    acao, token = data.split(
        ":",
        1
    )

    produto = ofertas_pendentes.get(
        token
    )

    if not produto:
        return

    mensagem = callback.get(
        "message"
    ) or {}

    chat = mensagem.get(
        "chat"
    ) or {}

    chat_id = str(
        chat.get(
            "id",
            ""
        )
    )

    if chat_id != str(
        ADMIN_CHAT_ID
    ):
        return

    # -----------------------------------------------------
    # DESCARTAR
    # -----------------------------------------------------

    if acao == "descartar":

        ofertas_pendentes.pop(
            token,
            None
        )

        telegram(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text":
                    "❌ Oferta descartada."
            }
        )

        return

    # -----------------------------------------------------
    # APROVAR
    # -----------------------------------------------------

    if acao == "aprovar":

        ofertas_pendentes.pop(
            token,
            None
        )

        aguardando_link[
            chat_id
        ] = produto

        telegram(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": (
                    "✅ OFERTA APROVADA!\n\n"
                    "Agora me mande seu "
                    "LINK DE AFILIADO."
                )
            }
        )


# =========================================================
# RECEBER LINK DE AFILIADO
# =========================================================

def mensagem_telegram(message):

    chat = message.get(
        "chat"
    ) or {}

    chat_id = str(
        chat.get(
            "id",
            ""
        )
    )

    if chat_id != str(
        ADMIN_CHAT_ID
    ):
        return

    texto = (
        message.get("text")
        or ""
    ).strip()

    if not texto:
        return

    produto = aguardando_link.get(
        chat_id
    )

    if not produto:
        return

    if not re.match(
        r"^https?://",
        texto,
        re.IGNORECASE
    ):

        telegram(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text":
                    (
                        "⚠️ Envie um link válido "
                        "começando com https://"
                    )
            }
        )

        return

    if not CHANNEL_ID:

        telegram(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text":
                    (
                        "❌ TELEGRAM_CHANNEL_ID "
                        "não está configurado."
                    )
            }
        )

        return

    try:

        resposta = publicar_canal(
            produto,
            texto
        )

    except Exception as erro:

        print(
            "ERRO PUBLICAÇÃO:",
            erro
        )

        telegram(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text":
                    (
                        "❌ Erro ao publicar. "
                        "Veja os Logs."
                    )
            }
        )

        return

    if resposta.status_code == 200:

        aguardando_link.pop(
            chat_id,
            None
        )

        telegram(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text":
                    "🚀 Publicado no canal!"
            }
        )

    else:

        print(
            "ERRO TELEGRAM:",
            resposta.text
        )

        telegram(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text":
                    (
                        "❌ Telegram recusou "
                        "a publicação.\n\n"
                        f"HTTP: "
                        f"{resposta.status_code}"
                    )
            }
        )


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():

    return """
    <h1>🔥 Bot Mercado Livre</h1>

    <p>
        <a href="/buscar-ofertas">
            🔎 BUSCAR OFERTAS
        </a>
    </p>

    <p>
        <a href="/configurar-webhook">
            ⚙️ CONFIGURAR WEBHOOK
        </a>
    </p>
    """


# =========================================================
# BUSCAR OFERTAS
# =========================================================

@app.route(
    "/buscar-ofertas"
)
def buscar():

    # NÃO chama configurar_webhook aqui.
    # O webhook é configurado separadamente.

    quantidade = executar_busca()

    return f"""
    <h1>🔥 Busca concluída!</h1>

    <p>
        Ofertas enviadas para aprovação:
        <b>{quantidade}</b>
    </p>

    <p>
        Confira seu Telegram.
    </p>
    """


# =========================================================
# WEBHOOK
# =========================================================

@app.route(
    "/telegram/webhook",
    methods=["POST"]
)
def webhook():

    try:

        update = request.get_json(
            silent=True
        ) or {}

        callback = update.get(
            "callback_query"
        )

        if callback:

            callback_telegram(
                callback
            )

        message = update.get(
            "message"
        )

        if message:

            mensagem_telegram(
                message
            )

    except Exception as erro:

        print(
            "ERRO WEBHOOK:",
            erro
        )

    return "OK"


# =========================================================
# CONFIGURAR WEBHOOK
# =========================================================

@app.route(
    "/configurar-webhook"
)
def rota_configurar_webhook():

    sucesso = configurar_webhook()

    if sucesso:

        return """
        <h1>✅ Webhook configurado!</h1>

        <p>
            O bot está pronto para receber
            os botões de aprovação.
        </p>
        """

    return """
    <h1>❌ Erro no webhook</h1>

    <p>
        Confira o TELEGRAM_BOT_TOKEN
        nas variáveis do Render.
    </p>
    """


# =========================================================
# INICIAR
# =========================================================

if __name__ == "__main__":

    porta = int(
        os.environ.get(
            "PORT",
            5000
        )
    )

    app.run(
        host="0.0.0.0",
        port=porta
    )
