from flask import Flask, request
import os
import re
import uuid
import json
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, unquote

app = Flask(__name__)

# =========================================================
# CONFIGURAÇÃO
# =========================================================

TELEGRAM_BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN", ""
).strip()

TELEGRAM_ADMIN_CHAT_ID = os.environ.get(
    "TELEGRAM_CHAT_ID", ""
).strip()

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

TIMEOUT = 15

# Ofertas aguardando aprovação
pending = {}

# Oferta aprovada aguardando link afiliado
aguardando_afiliado = {}


# =========================================================
# HEADERS
# =========================================================

def headers_navegador():
    return {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/152.0 Safari/537.36"
        ),
        "Accept-Language": (
            "pt-BR,pt;q=0.9,en;q=0.8"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,image/avif,"
            "image/webp,*/*;q=0.8"
        ),
    }


# =========================================================
# TELEGRAM
# =========================================================

def telegram_api(metodo, payload):
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN não configurado."
        )

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

    except Exception:
        return False


# =========================================================
# UTILIDADES
# =========================================================

def limpar_texto(texto):
    return re.sub(
        r"\s+",
        " ",
        texto or ""
    ).strip()


def achar_desconto(texto):
    if not texto:
        return 0

    padroes = [
        r"(\d{1,2})%\s*OFF",
        r"(\d{1,2})%\s*off",
        r"(\d{1,2})\s*%\s*de\s*desconto",
    ]

    for padrao in padroes:
        resultado = re.search(
            padrao,
            texto
        )

        if resultado:
            try:
                valor = int(
                    resultado.group(1)
                )

                if 1 <= valor <= 99:
                    return valor

            except Exception:
                pass

    return 0


def extrair_preco(texto):
    if not texto:
        return ""

    valores = re.findall(
        r"R\$\s*[\d\.]+(?:,\d{2})?",
        texto
    )

    if valores:
        return valores[0].strip()

    return ""


def normalizar_preco(valor):
    if valor is None:
        return ""

    valor = str(valor).strip()

    if not valor:
        return ""

    if "R$" in valor:
        return valor

    try:
        if "." in valor and "," not in valor:
            numero = float(valor)

            return (
                f"R$ {numero:,.2f}"
                .replace(",", "X")
                .replace(".", ",")
                .replace("X", ".")
            )
    except Exception:
        pass

    return f"R$ {valor}"


def calcular_desconto(preco, original):
    try:
        p = (
            str(preco)
            .replace("R$", "")
            .replace(".", "")
            .replace(",", ".")
            .strip()
        )

        o = (
            str(original)
            .replace("R$", "")
            .replace(".", "")
            .replace(",", ".")
            .strip()
        )

        preco_num = float(p)
        original_num = float(o)

        if original_num <= preco_num:
            return 0

        return round(
            (
                (original_num - preco_num)
                / original_num
            ) * 100
        )

    except Exception:
        return 0


# =========================================================
# PEGAR NOME PELO LINK
# =========================================================

def nome_pelo_link(url):
    try:
        texto = unquote(
            url.split("?")[0]
        )

        texto = re.split(
            r"/p/|/MLB",
            texto,
            flags=re.IGNORECASE
        )[0]

        partes = texto.rstrip(
            "/"
        ).split("/")

        if not partes:
            return ""

        slug = partes[-1]

        if not slug:
            return ""

        slug = slug.replace(
            "-",
            " "
        )

        slug = re.sub(
            r"\s+",
            " ",
            slug
        ).strip()

        if len(slug) < 5:
            return ""

        palavras = []

        for palavra in slug.split():
            if palavra.lower() in [
                "de",
                "da",
                "do",
                "das",
                "dos",
                "e",
                "em",
                "com",
                "para",
            ]:
                palavras.append(
                    palavra.lower()
                )
            else:
                palavras.append(
                    palavra.capitalize()
                )

        return " ".join(
            palavras
        )[:300]

    except Exception:
        return ""


# =========================================================
# IMAGEM DO LINK
# =========================================================

def imagem_do_link(link_tag):
    if not link_tag:
        return ""

    img = link_tag.find("img")

    if not img:
        return ""

    atributos = [
        "src",
        "data-src",
        "data-original",
        "data-lazy",
        "data-image",
        "data-srcset",
    ]

    for atributo in atributos:
        valor = img.get(
            atributo
        )

        if not valor:
            continue

        if atributo == "data-srcset":
            valor = valor.split(
                ","
            )[0].strip()

            valor = valor.split(
                " "
            )[0]

        if valor.startswith("//"):
            valor = "https:" + valor

        if valor.startswith("/"):
            valor = urljoin(
                "https://www.mercadolivre.com.br",
                valor
            )

        if valor.startswith("http"):
            return valor

    return ""


# =========================================================
# EXTRAIR CARDS DA PÁGINA DE OFERTAS
# =========================================================

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
        href = (
            link_tag.get("href")
            or ""
        ).strip()

        if not href:
            continue

        eh_produto = (
            "/p/" in href
            or "produto.mercadolivre.com.br"
            in href
            or "/MLB-" in href
        )

        if not eh_produto:
            continue

        imagem = imagem_do_link(
            link_tag
        )

        texto_link = limpar_texto(
            link_tag.get_text(
                " ",
                strip=True
            )
        )

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

            desconto_card = achar_desconto(
                texto_card
            )

            if desconto_card > 0:
                melhor_card = card
                break

        if melhor_card is not None:
            card = melhor_card

        texto_card = limpar_texto(
            card.get_text(
                " ",
                strip=True
            )
        )

        desconto = achar_desconto(
            texto_card
        )

        if desconto <= 0:
            continue

        titulo = ""

        if len(texto_link) >= 10:
            if texto_link.lower() not in [
                "mercado livre",
                "ver oferta",
                "comprar",
                "oferta",
            ]:
                titulo = texto_link

        if not titulo:
            for tag in card.find_all(
                [
                    "h1",
                    "h2",
                    "h3",
                    "h4",
                    "p",
                ]
            ):
                candidato = limpar_texto(
                    tag.get_text(
                        " ",
                        strip=True
                    )
                )

                if (
                    len(candidato) >= 10
                    and candidato.lower()
                    not in [
                        "mercado livre",
                        "ver oferta",
                        "comprar",
                    ]
                ):
                    if len(candidato) > len(
                        titulo
                    ):
                        titulo = candidato

        preco = extrair_preco(
            texto_card
        )

        url = urljoin(
            "https://www.mercadolivre.com.br",
            href
        )

        candidatos.append({
            "titulo_card": titulo,
            "desconto": desconto,
            "link": url,
            "imagem_card": imagem,
            "texto_card": texto_card[:4000],
            "preco_card": preco,
        })

    unicos = {}

    for oferta in candidatos:
        link = oferta["link"]

        if link not in unicos:
            unicos[link] = oferta

    ofertas = list(
        unicos.values()
    )

    ofertas.sort(
        key=lambda x: x.get(
            "desconto",
            0
        ),
        reverse=True
    )

    return ofertas


# =========================================================
# ENRIQUECER PRODUTO
# =========================================================

def enriquecer_produto(oferta):
    link = oferta.get(
        "link",
        ""
    )

    nome_slug = nome_pelo_link(
        link
    )

    titulo = (
        nome_slug
        or oferta.get(
            "titulo_card",
            ""
        )
    )

    imagem = oferta.get(
        "imagem_card",
        ""
    )

    preco = oferta.get(
        "preco_card",
        ""
    )

    preco_original = ""

    try:
        resposta = requests.get(
            link,
            headers=headers_navegador(),
            timeout=TIMEOUT,
            allow_redirects=True
        )

    except requests.RequestException:
        oferta["titulo"] = (
            titulo
            or "Produto Mercado Livre"
        )

        oferta["imagem"] = imagem
        oferta["preco"] = preco

        return oferta

    if resposta.status_code != 200:
        oferta["titulo"] = (
            titulo
            or "Produto Mercado Livre"
        )

        oferta["imagem"] = imagem
        oferta["preco"] = preco

        return oferta

    soup = BeautifulSoup(
        resposta.text,
        "html.parser"
    )

    # -----------------------------------------------------
    # OG TITLE
    # -----------------------------------------------------

    og_title = soup.find(
        "meta",
        property="og:title"
    )

    if og_title:
        valor = (
            og_title.get("content")
            or ""
        ).strip()

        if (
            valor
            and valor.lower()
            not in [
                "mercado livre",
                "mercadolivre",
            ]
        ):
            titulo = valor

    # -----------------------------------------------------
    # H1
    # -----------------------------------------------------

    h1 = soup.find(
        "h1"
    )

    if h1:
        valor = limpar_texto(
            h1.get_text(
                " ",
                strip=True
            )
        )

        if (
            len(valor) >= 10
            and valor.lower()
            not in [
                "mercado livre",
                "mercadolivre",
            ]
        ):
            titulo = valor

    # -----------------------------------------------------
    # OG IMAGE
    # -----------------------------------------------------

    og_image = soup.find(
        "meta",
        property="og:image"
    )

    if og_image:
        valor = (
            og_image.get("content")
            or ""
        ).strip()

        if valor.startswith("http"):
            imagem = valor

    # -----------------------------------------------------
    # JSON-LD
    # -----------------------------------------------------

    scripts = soup.find_all(
        "script",
        type="application/ld+json"
    )

    for script in scripts:
        texto_json = (
            script.string
            or script.get_text()
            or ""
        ).strip()

        if not texto_json:
            continue

        try:
            dados = json.loads(
                texto_json
            )
        except Exception:
            continue

        blocos = []

        if isinstance(
            dados,
            dict
        ):
            blocos.append(
                dados
            )

        elif isinstance(
            dados,
            list
        ):
            blocos.extend(
                dados
            )

        for bloco in blocos:
            if not isinstance(
                bloco,
                dict
            ):
                continue

            nome_json = bloco.get(
                "name"
            )

            if (
                nome_json
                and len(str(nome_json)) >= 10
            ):
                if (
                    str(nome_json).lower()
                    not in [
                        "mercado livre",
                        "mercadolivre",
                    ]
                ):
                    titulo = str(
                        nome_json
                    )

            imagem_json = bloco.get(
                "image"
            )

            if isinstance(
                imagem_json,
                list
            ):
                if imagem_json:
                    candidato = str(
                        imagem_json[0]
                    )

                    if candidato.startswith(
                        "http"
                    ):
                        imagem = candidato

            elif isinstance(
                imagem_json,
                dict
            ):
                candidato = (
                    imagem_json.get(
                        "url"
                    )
                    or ""
                )

                if candidato.startswith(
                    "http"
                ):
                    imagem = candidato

            elif isinstance(
                imagem_json,
                str
            ):
                if imagem_json.startswith(
                    "http"
                ):
                    imagem = imagem_json

            offers = bloco.get(
                "offers"
            )

            if isinstance(
                offers,
                list
            ):
                offers = (
                    offers[0]
                    if offers
                    else {}
                )

            if isinstance(
                offers,
                dict
            ):
                preco_json = offers.get(
                    "price"
                )

                if preco_json is not None:
                    preco = str(
                        preco_json
                    )

                high_price = offers.get(
                    "highPrice"
                )

                if high_price:
                    preco_original = str(
                        high_price
                    )

    # -----------------------------------------------------
    # PREÇO NO HTML
    # -----------------------------------------------------

    if not preco:
        preco = extrair_preco(
            resposta.text
        )

    # -----------------------------------------------------
    # FALLBACKS
    # -----------------------------------------------------

    if not titulo:
        titulo = (
            nome_slug
            or oferta.get(
                "titulo_card",
                ""
            )
            or "Produto Mercado Livre"
        )

    if not imagem:
        imagem = oferta.get(
            "imagem_card",
            ""
        )

    if not preco:
        preco = oferta.get(
            "preco_card",
            ""
        )

    oferta["titulo"] = limpar_texto(
        titulo
    )[:300]

    oferta["imagem"] = imagem

    oferta["preco"] = (
        normalizar_preco(
            preco
        )
        if preco
        else ""
    )

    oferta["preco_original"] = (
        normalizar_preco(
            preco_original
        )
        if preco_original
        else ""
    )

    desconto = oferta.get(
        "desconto",
        0
    )

    if (
        oferta["preco"]
        and oferta["preco_original"]
    ):
        calculado = calcular_desconto(
            oferta["preco"],
            oferta["preco_original"]
        )

        if calculado > 0:
            desconto = calculado

    oferta["desconto"] = desconto

    return oferta


# =========================================================
# BUSCAR OFERTAS
# =========================================================

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

    finais = []

    for oferta in ofertas[:10]:
        oferta = enriquecer_produto(
            oferta
        )

        nome = oferta.get(
            "titulo",
            ""
        ).strip()

        if (
            not nome
            or nome.lower()
            in [
                "mercado livre",
                "mercadolivre",
                "produto mercado livre",
            ]
        ):
            nome = nome_pelo_link(
                oferta.get(
                    "link",
                    ""
                )
            )

            if nome:
                oferta["titulo"] = nome

        if not oferta.get(
            "titulo"
        ):
            continue

        finais.append(
            oferta
        )

    return finais, "200"


# =========================================================
# ENVIAR PARA APROVAÇÃO
# =========================================================

def enviar_oferta_para_aprovacao(
    oferta
):
    token = uuid.uuid4().hex[:12]

    pending[token] = oferta

    titulo = oferta.get(
        "titulo",
        "Produto Mercado Livre"
    )

    preco = oferta.get(
        "preco",
        ""
    )

    preco_original = oferta.get(
        "preco_original",
        ""
    )

    desconto = oferta.get(
        "desconto",
        0
    )

    texto = (
        "🔥 OFERTA ENCONTRADA!\n\n"
        f"📦 {titulo}\n\n"
    )

    if preco:
        texto += (
            f"💰 Preço: {preco}\n"
        )

    if preco_original:
        texto += (
            f"💵 De: {preco_original}\n"
        )

    if desconto:
        texto += (
            f"📉 {desconto}% OFF\n"
        )

    texto += (
        "\n🔗 Link normal:\n"
        f"{oferta['link']}\n\n"
        "⚠️ Confira o produto antes "
        "de publicar."
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
                },
            ]
        ]
    }

    payload = {
        "chat_id":
            TELEGRAM_ADMIN_CHAT_ID,
        "reply_markup":
            teclado,
    }

    imagem = oferta.get(
        "imagem",
        ""
    )

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
        pending.pop(
            token,
            None
        )

    return resposta


# =========================================================
# APROVAR / DESCARTAR
# =========================================================

def responder_callback(
    callback
):
    callback_id = callback.get(
        "id"
    )

    data = callback.get(
        "data",
        ""
    )

    if callback_id:
        try:
            telegram_api(
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

    oferta = pending.get(
        token
    )

    if not oferta:
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
                        "Não será publicada."
                    ),
                }
            )
        except Exception:
            pass

        return

    if acao == "aprovar":
        pending.pop(
            token,
            None
        )

        aguardando_afiliado[
            chat_id
        ] = oferta

        try:
            telegram_api(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": (
                        "💰 OFERTA APROVADA!\n\n"
                        "Agora mande seu "
                        "LINK DE AFILIADO.\n\n"
                        "Depois disso eu publico "
                        "a oferta no canal."
                    ),
                }
            )
        except Exception:
            pass


# =========================================================
# PUBLICAR NO CANAL
# =========================================================

def publicar_oferta(
    oferta,
    link_afiliado
):
    titulo = oferta.get(
        "titulo",
        "Oferta Mercado Livre"
    )

    preco = oferta.get(
        "preco",
        ""
    )

    preco_original = oferta.get(
        "preco_original",
        ""
    )

    desconto = oferta.get(
        "desconto",
        0
    )

    texto = (
        "🔥 OFERTA DO DIA! 🔥\n\n"
        f"📦 {titulo}\n\n"
    )

    if preco:
        texto += (
            f"💰 Por: {preco}\n"
        )

    if preco_original:
        texto += (
            f"💵 De: {preco_original}\n"
        )

    if desconto:
        texto += (
            f"📉 {desconto}% OFF\n"
        )

    texto += (
        "\n🛒 COMPRAR AGORA:\n"
        f"{link_afiliado}"
    )

    imagem = oferta.get(
        "imagem",
        ""
    )

    if imagem:
        return telegram_api(
            "sendPhoto",
            {
                "chat_id":
                    TELEGRAM_CHANNEL_ID,
                "photo":
                    imagem,
                "caption":
                    texto,
            }
        )

    return telegram_api(
        "sendMessage",
        {
            "chat_id":
                TELEGRAM_CHANNEL_ID,
            "text":
                texto,
        }
    )


# =========================================================
# RECEBER LINK DE AFILIADO
# =========================================================

def processar_mensagem(
    message
):
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
        TELEGRAM_ADMIN_CHAT_ID
    ):
        return

    texto = (
        message.get(
            "text"
        )
        or ""
    ).strip()

    if not texto:
        return

    oferta = aguardando_afiliado.get(
        chat_id
    )

    if not oferta:
        return

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
                        "⚠️ Mande um link válido "
                        "começando com https://"
                    ),
                }
            )
        except Exception:
            pass

        return

    if not TELEGRAM_CHANNEL_ID:
        try:
            telegram_api(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": (
                        "⚠️ TELEGRAM_CHANNEL_ID "
                        "não está configurado."
                    ),
                }
            )
        except Exception:
            pass

        return

    try:
        resposta = publicar_oferta(
            oferta,
            texto
        )
    except Exception:
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
                        "A oferta foi publicada "
                        "no canal."
                    ),
                }
            )
        except Exception:
            pass

    else:
        try:
            telegram_api(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": (
                        "❌ Erro ao publicar.\n\n"
                        f"Telegram HTTP "
                        f"{resposta.status_code}"
                    ),
                }
            )
        except Exception:
            pass


# =========================================================
# PÁGINA INICIAL
# =========================================================

@app.route("/")
def home():
    return """
    <h1>🔥 Bot de Ofertas</h1>

    <p>
        <a href="/buscar-ofertas">
            🔎 Buscar ofertas
        </a>
    </p>

    <p>
        <a href="/configurar-webhook">
            ⚙️ Configurar webhook
        </a>
    </p>

    <p>
        Mercado Livre →
        Aprovação →
        Link afiliado →
        Canal
    </p>
    """


# =========================================================
# BUSCAR OFERTAS
# =========================================================

@app.route(
    "/buscar-ofertas"
)
def rota_buscar_ofertas():

    configurar_webhook()

    ofertas, status = buscar_ofertas()

    if status != "200":
        return f"""
        <h2>❌ Busca falhou</h2>
        <p>Status: {status}</p>
        """

    if not ofertas:
        return """
        <h2>❌ Nenhuma oferta válida encontrada.</h2>
        """

    enviadas = 0

    for oferta in ofertas[:3]:
        try:
            resposta = (
                enviar_oferta_para_aprovacao(
                    oferta
                )
            )

            if resposta.status_code == 200:
                enviadas += 1

        except Exception:
            pass

    return f"""
    <h2>🔥 OFERTAS ENCONTRADAS!</h2>

    <p>
        Ofertas analisadas:
        <b>{len(ofertas)}</b>
    </p>

    <p>
        Enviadas para aprovação:
        <b>{enviadas}</b>
    </p>

    <p>
        Confira seu Telegram.
    </p>

    <p>
        ✅ Aprovar ou ❌ Descartar.
    </p>
    """


# =========================================================
# WEBHOOK TELEGRAM
# =========================================================

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
        pass

    return "OK"


# =========================================================
# CONFIGURAR WEBHOOK
# =========================================================

@app.route(
    "/configurar-webhook"
)
def rota_webhook():

    if configurar_webhook():
        return """
        <h2>✅ Webhook configurado!</h2>

        <p>
            Botões de aprovação ativos.
        </p>
        """

    return """
    <h2>❌ Falha no webhook</h2>
    """


# =========================================================
# INICIAR SERVIDOR
# =========================================================

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
