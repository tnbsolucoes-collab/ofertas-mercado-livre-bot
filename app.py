from flask import Flask, request
import os
import re
import uuid
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN", ""
).strip()

# Seu chat privado onde o bot manda as ofertas
TELEGRAM_ADMIN_CHAT_ID = os.environ.get(
    "TELEGRAM_CHAT_ID", ""
).strip()

# Canal onde a oferta aprovada será publicada.
# Se ainda nao colocou, o bot vai avisar.
TELEGRAM_CHANNEL_ID = os.environ.get(
    "TELEGRAM_CHANNEL_ID", ""
).strip()

BASE_URL = (
    "https://ofertas-mercado-livre-bot.onrender.com"
)

WEBHOOK_URL = (
    f"{BASE_URL}/telegram/webhook"
)

URL_OFERTAS = (
    "https://www.mercadolivre.com.br/ofertas"
)

TIMEOUT = 12

# Guarda temporariamente as ofertas aguardando aprovacao.
# Exemplo:
# pending["abc123"] = oferta
pending = {}

# Guarda quem foi aprovado e esta esperando
# o usuario mandar o link de afiliado.
aguardando_afiliado = {}


def headers_navegador():
    return {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/152.0 Safari/537.36"
        ),
        "Accept-Language": "pt-BR,pt;q=0.9"
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


def configurar_webhook():
    if not TELEGRAM_BOT_TOKEN:
        return False

    try:
        resposta = telegram_api(
            "setWebhook",
            {
                "url": WEBHOOK_URL
            }
        )

        return resposta.status_code == 200

    except requests.RequestException:
        return False


def limpar_texto(texto):
    return re.sub(
        r"\s+",
        " ",
        texto or ""
    ).strip()


def achar_desconto(texto):
    padroes = [
        r"(\d{1,2})%\s*OFF",
        r"(\d{1,2})%\s*off"
    ]

    for padrao in padroes:
        achou = re.search(
            padrao,
            texto
        )

        if achou:
            return int(
                achou.group(1)
            )

    return 0


def extrair_cards(html):
    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    candidatos = []

    for link_tag in soup.find_all(
        "a",
        href=True
    ):
        href = link_tag.get("href", "")

        if not href:
            continue

        eh_produto = (
            "/p/" in href
            or "produto.mercadolivre.com.br" in href
            or "/MLB-" in href
        )

        if not eh_produto:
            continue

        # Primeiro tenta pegar imagem diretamente
        # dentro do proprio link.
        imagem = ""

        img = link_tag.find("img")

        if img:
            imagem = (
                img.get("data-src")
                or img.get("src")
                or img.get("data-lazy")
                or ""
            )

        # Procura um card proximo do link.
        card = link_tag

        melhor_card = None

        for _ in range(5):
            if not card.parent:
                break

            card = card.parent

            texto_card = limpar_texto(
                card.get_text(
                    " ",
                    strip=True
                )
            )

            desconto = achar_desconto(
                texto_card
            )

            if desconto > 0:
                melhor_card = card
                break

        if melhor_card is not None:
            card = melhor_card

        texto = limpar_texto(
            card.get_text(
                " ",
                strip=True
            )
        )

        desconto = achar_desconto(
            texto
        )

        if desconto <= 0:
            continue

        titulo = limpar_texto(
            link_tag.get_text(
                " ",
                strip=True
            )
        )

        if len(titulo) < 10:
            tags = card.find_all(
                ["h2", "h3", "p"]
            )

            for tag in tags:
                candidato = limpar_texto(
                    tag.get_text(
                        " ",
                        strip=True
                    )
                )

                if len(candidato) > len(titulo):
                    titulo = candidato

        if len(titulo) < 10:
            continue

        url = urljoin(
            "https://www.mercadolivre.com.br",
            href
        )

        # Se nao achou imagem no link,
        # tenta somente dentro do card.
        if not imagem:
            img = card.find("img")

            if img:
                imagem = (
                    img.get("data-src")
                    or img.get("src")
                    or ""
                )

        candidatos.append({
            "titulo_card": titulo[:250],
            "desconto": desconto,
            "link": url,
            "imagem_card": imagem
        })

    # Remove links repetidos.
    unicos = {}

    for oferta in candidatos:
        if oferta["link"] not in unicos:
            unicos[oferta["link"]] = oferta

    ofertas = list(
        unicos.values()
    )

    ofertas.sort(
        key=lambda x: x["desconto"],
        reverse=True
    )

    return ofertas


def enriquecer_produto(oferta):
    """
    Abre a pagina individual do produto.
    Isso evita misturar a imagem de um card
    com o titulo de outro card.
    """

    try:
        resposta = requests.get(
            oferta["link"],
            headers=headers_navegador(),
            timeout=TIMEOUT
        )

    except requests.RequestException:
        return oferta

    if resposta.status_code != 200:
        return oferta

    soup = BeautifulSoup(
        resposta.text,
        "html.parser"
    )

    titulo = ""

    og_title = soup.find(
        "meta",
        property="og:title"
    )

    if og_title:
        titulo = (
            og_title.get("content")
            or ""
        )

    if not titulo:
        title_tag = soup.find("title")

        if title_tag:
            titulo = limpar_texto(
                title_tag.get_text()
            )

    imagem = ""

    og_image = soup.find(
        "meta",
        property="og:image"
    )

    if og_image:
        imagem = (
            og_image.get("content")
            or ""
        )

    if titulo:
        oferta["titulo"] = limpar_texto(
            titulo
        )[:300]
    else:
        oferta["titulo"] = (
            oferta["titulo_card"]
        )

    if imagem:
        oferta["imagem"] = imagem
    else:
        # So usa a imagem do card como fallback.
        oferta["imagem"] = (
            oferta["imagem_card"]
        )

    return oferta


def buscar_ofertas():
    try:
        resposta = requests.get(
            URL_OFERTAS,
            headers=headers_navegador(),
            timeout=TIMEOUT
        )

    except requests.RequestException:
        return [], "ERRO_CONEXAO"

    if resposta.status_code != 200:
        return [], str(
            resposta.status_code
        )

    ofertas = extrair_cards(
        resposta.text
    )

    if not ofertas:
        return [], "SEM_OFERTAS"

    # Enriquece somente as melhores.
    # Assim nao fazemos dezenas de requisicoes.
    ofertas_finais = []

    for oferta in ofertas[:10]:
        oferta = enriquecer_produto(
            oferta
        )

        ofertas_finais.append(
            oferta
        )

    return ofertas_finais, "200"


def enviar_oferta_para_aprovacao(oferta):
    token = uuid.uuid4().hex[:12]

    pending[token] = oferta

    desconto = oferta["desconto"]

    texto = (
        "🔥 OFERTA ENCONTRADA!\n\n"
        f"📦 {oferta.get('titulo', oferta['titulo_card'])}\n\n"
        f"📉 {desconto}% OFF\n\n"
        "🔗 Link normal:\n"
        f"{oferta['link']}\n\n"
        "⚠️ Confira o produto antes de publicar."
    )

    teclado = {
        "inline_keyboard": [
            [
                {
                    "text": "✅ APROVAR",
                    "callback_data": (
                        f"aprovar:{token}"
                    )
                },
                {
                    "text": "❌ DESCARTAR",
                    "callback_data": (
                        f"descartar:{token}"
                    )
                }
            ]
        ]
    }

    payload = {
        "chat_id": TELEGRAM_ADMIN_CHAT_ID,
        "reply_markup": teclado
    }

    imagem = oferta.get("imagem")

    if imagem:
        payload["photo"] = imagem
        payload["caption"] = texto

        resposta = telegram_api(
            "sendPhoto",
            payload
        )

    else:
        payload["text"] = texto

        resposta = telegram_api(
            "sendMessage",
            payload
        )

    if resposta.status_code != 200:
        pending.pop(token, None)

    return resposta


def responder_callback(callback):
    callback_id = callback.get("id")
    data = callback.get("data", "")

    # Remove o aviso de carregamento do botao.
    if callback_id:
        try:
            telegram_api(
                "answerCallbackQuery",
                {
                    "callback_query_id": callback_id
                }
            )
        except requests.RequestException:
            pass

    if ":" not in data:
        return

    acao, token = data.split(
        ":",
        1
    )

    oferta = pending.get(token)

    if not oferta:
        return

    mensagem = callback.get(
        "message"
    ) or {}

    chat = mensagem.get(
        "chat"
    ) or {}

    chat_id = str(
        chat.get("id", "")
    )

    if chat_id != str(
        TELEGRAM_ADMIN_CHAT_ID
    ):
        return

    if acao == "descartar":
        pending.pop(
            token,
            None
        )

        try:
            telegram_api(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": (
                        "❌ Oferta descartada.\n\n"
                        "Vou deixar essa de fora."
                    )
                }
            )
        except requests.RequestException:
            pass

        return

    if acao == "aprovar":
        # Move para a lista de ofertas aprovadas.
        pending.pop(
            token,
            None
        )

        aguardando_afiliado[chat_id] = oferta

        try:
            telegram_api(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": (
                        "💰 OFERTA APROVADA!\n\n"
                        "Agora me envie o seu "
                        "LINK DE AFILIADO dessa oferta.\n\n"
                        "⚠️ Nao vou publicar o link "
                        "normal do Mercado Livre."
                    )
                }
            )
        except requests.RequestException:
            pass


def publicar_oferta(oferta, link_afiliado):
    titulo = oferta.get(
        "titulo",
        oferta["titulo_card"]
    )

    desconto = oferta["desconto"]

    texto = (
        "🔥 OFERTA DO DIA!\n\n"
        f"📦 {titulo}\n\n"
        f"📉 {desconto}% OFF\n\n"
        "🛒 COMPRAR AGORA:\n"
        f"{link_afiliado}"
    )

    payload = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "text": texto
    }

    imagem = oferta.get("imagem")

    if imagem:
        payload_photo = {
            "chat_id": TELEGRAM_CHANNEL_ID,
            "photo": imagem,
            "caption": texto
        }

        return telegram_api(
            "sendPhoto",
            payload_photo
        )

    return telegram_api(
        "sendMessage",
        payload
    )


def processar_mensagem(message):
    chat = message.get("chat") or {}

    chat_id = str(
        chat.get("id", "")
    )

    if chat_id != str(
        TELEGRAM_ADMIN_CHAT_ID
    ):
        return

    texto = (
        message.get("text")
        or ""
    ).strip()

    if not texto:
        return

    oferta = aguardando_afiliado.get(
        chat_id
    )

    if not oferta:
        return

    # Aceita somente algo que pareca URL.
    if not re.match(
        r"^https?://",
        texto,
        re.IGNORECASE
    ):
        try:
            telegram_api(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": (
                        "⚠️ Esse nao parece ser "
                        "um link.\n\n"
                        "Envie seu link de afiliado "
                        "com https://"
                    )
                }
            )
        except requests.RequestException:
            pass

        return

    if not TELEGRAM_CHANNEL_ID:
        try:
            telegram_api(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": (
                        "✅ Link de afiliado recebido!\n\n"
                        "Falta configurar "
                        "TELEGRAM_CHANNEL_ID no Render "
                        "para eu publicar no canal.\n\n"
                        "O link foi recebido, mas "
                        "nao publiquei nada ainda."
                    )
                }
            )
        except requests.RequestException:
            pass

        return

    try:
        resposta = publicar_oferta(
            oferta,
            texto
        )

    except requests.RequestException:
        return

    if resposta.status_code == 200:
        aguardando_afiliado.pop(
            chat_id,
            None
        )

        try:
            telegram_api(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": (
                        "🚀 PUBLICADO!\n\n"
                        "Sua oferta foi enviada "
                        "para o canal."
                    )
                }
            )
        except requests.RequestException:
            pass

    else:
        try:
            telegram_api(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": (
                        "❌ Nao consegui publicar "
                        "no canal.\n\n"
                        f"Telegram HTTP: "
                        f"{resposta.status_code}"
                    )
                }
            )
        except requests.RequestException:
            pass


@app.route("/")
def home():
    return """
    <h2>BOT DE OFERTAS 🔥</h2>

    <p>
        <a href="/buscar-ofertas">
            🔎 Buscar nova oferta
        </a>
    </p>

    <p>
        O bot encontra a oferta,
        manda para aprovacao e
        somente publica depois do
        seu link de afiliado.
    </p>
    """


@app.route("/buscar-ofertas")
def rota_buscar_ofertas():
    # Garante que o Telegram sabe para onde
    # mandar os callbacks dos botoes.
    configurar_webhook()

    ofertas, status = buscar_ofertas()

    if status != "200":
        return f"""
        <h2>Busca falhou</h2>

        <p>
            Status: {status}
        </p>
        """

    if not ofertas:
        return """
        <h2>Nenhuma oferta encontrada.</h2>
        """

    enviadas = 0

    # Envia somente as 3 melhores para aprovacao.
    for oferta in ofertas[:3]:
        resposta = enviar_oferta_para_aprovacao(
            oferta
        )

        if resposta.status_code == 200:
            enviadas += 1

    return f"""
    <h2>OFERTAS ENCONTRADAS! 🔥🔥🔥</h2>

    <p>
        Ofertas analisadas:
        <b>{len(ofertas)}</b>
    </p>

    <p>
        Enviadas para aprovacao:
        <b>{enviadas}</b>
    </p>

    <p>
        Agora abra seu Telegram.
    </p>

    <p>
        O bot vai esperar voce
        clicar em APROVAR ou DESCARTAR.
    </p>
    """


@app.route(
    "/telegram/webhook",
    methods=["POST"]
)
def telegram_webhook():
    try:
        update = request.get_json(
            silent=True
        ) or {}

        callback = update.get(
            "callback_query"
        )

        if callback:
            responder_callback(
                callback
            )

        message = update.get(
            "message"
        )

        if message:
            processar_mensagem(
                message
            )

    except Exception:
        # Nunca deixa o Telegram receber
        # erro 500 por uma mensagem.
        pass

    return "OK"


@app.route("/configurar-webhook")
def rota_webhook():
    sucesso = configurar_webhook()

    if sucesso:
        return """
        <h2>Webhook configurado! ✅</h2>

        <p>
            O Telegram ja pode receber
            os botoes de APROVAR e DESCARTAR.
        </p>
        """

    return """
    <h2>Falha ao configurar webhook ❌</h2>

    <p>
        Confira TELEGRAM_BOT_TOKEN.
    </p>
    """


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
