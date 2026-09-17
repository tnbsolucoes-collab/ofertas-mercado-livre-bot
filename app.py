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
# CONFIGURAÇÕES
# =========================================================

TELEGRAM_BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()

TELEGRAM_ADMIN_CHAT_ID = os.environ.get(
    "TELEGRAM_CHAT_ID",
    ""
).strip()

TELEGRAM_CHANNEL_ID = os.environ.get(
    "TELEGRAM_CHANNEL_ID",
    ""
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

TIMEOUT = 20


# =========================================================
# MEMÓRIA TEMPORÁRIA
# =========================================================

pending = {}

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

        "Connection": "keep-alive",
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


def valor_numerico(preco):

    if not preco:
        return 0

    try:

        valor = (
            str(preco)
            .replace("R$", "")
            .replace(" ", "")
            .replace(".", "")
            .replace(",", ".")
        )

        return float(valor)

    except Exception:

        return 0


def formatar_preco(valor):

    if valor is None:
        return ""

    valor = str(valor).strip()

    if not valor:
        return ""

    if "R$" in valor:
        return valor

    try:

        numero = float(
            valor.replace(",", ".")
        )

        return (
            f"R$ {numero:,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

    except Exception:

        return f"R$ {valor}"


# =========================================================
# DESCONTO
# =========================================================

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


# =========================================================
# PREÇOS
# =========================================================

def extrair_precos(texto):

    if not texto:
        return "", ""

    encontrados = re.findall(
        r"R\$\s*[\d\.]+(?:,\d{2})?",
        texto
    )

    if not encontrados:
        return "", ""

    valores = []

    for valor in encontrados:

        valor = valor.strip()

        if valor not in valores:
            valores.append(valor)

    if len(valores) == 1:
        return valores[0], ""

    # -----------------------------------------------------
    # Tenta "De R$ X ... Por R$ Y"
    # -----------------------------------------------------

    padrao_de_por = re.search(
        r"De\s*:?\s*(R\$\s*[\d\.]+(?:,\d{2})?)"
        r".{0,150}?"
        r"(?:Por|por)\s*:?\s*"
        r"(R\$\s*[\d\.]+(?:,\d{2})?)",
        texto,
        flags=re.IGNORECASE
    )

    if padrao_de_por:

        original = (
            padrao_de_por.group(1)
            .strip()
        )

        atual = (
            padrao_de_por.group(2)
            .strip()
        )

        return atual, original

    # -----------------------------------------------------
    # Tenta "Por R$ X ... De R$ Y"
    # -----------------------------------------------------

    padrao_por_de = re.search(
        r"(?:Por|por)\s*:?\s*"
        r"(R\$\s*[\d\.]+(?:,\d{2})?)"
        r".{0,150}?"
        r"De\s*:?\s*"
        r"(R\$\s*[\d\.]+(?:,\d{2})?)",
        texto,
        flags=re.IGNORECASE
    )

    if padrao_por_de:

        atual = (
            padrao_por_de.group(1)
            .strip()
        )

        original = (
            padrao_por_de.group(2)
            .strip()
        )

        return atual, original

    # -----------------------------------------------------
    # Se houver vários preços:
    # menor = promocional
    # maior = original
    # -----------------------------------------------------

    numericos = []

    for valor in valores:

        numero = valor_numerico(
            valor
        )

        if numero > 0:

            numericos.append(
                (
                    valor,
                    numero
                )
            )

    if not numericos:
        return valores[0], ""

    numericos.sort(
        key=lambda item: item[1]
    )

    menor = numericos[0][0]
    maior = numericos[-1][0]

    if (
        valor_numerico(maior)
        > valor_numerico(menor)
    ):

        return menor, maior

    return menor, ""


def calcular_desconto(
    preco_atual,
    preco_original
):

    atual = valor_numerico(
        preco_atual
    )

    original = valor_numerico(
        preco_original
    )

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
# NOME PELO LINK
# =========================================================

def nome_pelo_link(url):

    try:

        url_limpa = unquote(
            url.split("?")[0]
        )

        partes = re.split(
            r"/p/|/MLB",
            url_limpa,
            flags=re.IGNORECASE
        )

        if not partes:
            return ""

        caminho = partes[0]

        partes_caminho = (
            caminho
            .rstrip("/")
            .split("/")
        )

        if not partes_caminho:
            return ""

        slug = partes_caminho[-1]

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

        minusculas = {
            "de",
            "da",
            "do",
            "das",
            "dos",
            "e",
            "em",
            "com",
            "para",
        }

        for palavra in slug.split():

            if palavra.lower() in minusculas:

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
# IMAGEM DO CARD
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
        "data-zoom",
        "data-srcset",
    ]

    for atributo in atributos:

        valor = img.get(
            atributo
        )

        if not valor:
            continue

        if atributo == "data-srcset":

            valor = (
                valor
                .split(",")[0]
                .strip()
                .split(" ")[0]
            )

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
# VALIDAR IMAGEM
# =========================================================

def imagem_valida(url):

    if not url:
        return False

    url_lower = url.lower()

    bloqueios = [
        "logo",
        "favicon",
        "icon",
        "sprite",
        "avatar",
    ]

    for bloqueio in bloqueios:

        if bloqueio in url_lower:
            return False

    return (
        url.startswith("http://")
        or url.startswith("https://")
    )


# =========================================================
# EXTRAIR IMAGEM DO HTML
# =========================================================

def procurar_imagem_no_html(
    soup
):

    # -----------------------------------------------------
    # 1. OG IMAGE
    # -----------------------------------------------------

    og_image = soup.find(
        "meta",
        property="og:image"
    )

    if og_image:

        valor = (
            og_image.get(
                "content"
            )
            or ""
        ).strip()

        if imagem_valida(valor):

            return valor

    # -----------------------------------------------------
    # 2. META TWITTER IMAGE
    # -----------------------------------------------------

    twitter_image = soup.find(
        "meta",
        attrs={
            "name": "twitter:image"
        }
    )

    if twitter_image:

        valor = (
            twitter_image.get(
                "content"
            )
            or ""
        ).strip()

        if imagem_valida(valor):

            return valor

    # -----------------------------------------------------
    # 3. IMG
    # -----------------------------------------------------

    for img in soup.find_all(
        "img"
    ):

        atributos = [
            "src",
            "data-src",
            "data-original",
            "data-lazy",
            "data-image",
            "data-zoom",
            "data-fallback-src",
            "data-srcset",
        ]

        for atributo in atributos:

            valor = img.get(
                atributo
            )

            if not valor:
                continue

            if atributo == "data-srcset":

                valor = (
                    valor
                    .split(",")[0]
                    .strip()
                    .split(" ")[0]
                )

            if valor.startswith("//"):

                valor = "https:" + valor

            if valor.startswith("/"):

                valor = urljoin(
                    "https://www.mercadolivre.com.br",
                    valor
                )

            if imagem_valida(valor):

                return valor

    # -----------------------------------------------------
    # 4. JSON / HTML
    # -----------------------------------------------------

    for script in soup.find_all(
        "script"
    ):

        conteudo = (
            script.string
            or script.get_text()
            or ""
        )

        if not conteudo:
            continue

        # URLs terminando em imagem
        encontrados = re.findall(
            r'https?://[^"\'\s<>]+',
            conteudo
        )

        for candidato in encontrados:

            candidato = (
                candidato
                .replace(
                    "\\u002F",
                    "/"
                )
                .replace(
                    "\\/",
                    "/"
                )
                .replace(
                    "\\u003A",
                    ":"
                )
            )

            candidato_lower = (
                candidato.lower()
            )

            if (
                "mlstatic.com"
                in candidato_lower
                or "mercadolibre"
                in candidato_lower
            ):

                if imagem_valida(
                    candidato
                ):

                    return candidato

    return ""


# =========================================================
# EXTRAIR CARDS
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

        for _ in range(6):

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

                if len(candidato) < 10:
                    continue

                if candidato.lower() in [
                    "mercado livre",
                    "ver oferta",
                    "comprar",
                ]:
                    continue

                if len(candidato) > len(
                    titulo
                ):

                    titulo = candidato

        preco, preco_original = (
            extrair_precos(
                texto_card
            )
        )

        url = urljoin(
            "https://www.mercadolivre.com.br",
            href
        )

        candidatos.append({

            "titulo_card":
                titulo,

            "desconto":
                desconto,

            "link":
                url,

            "imagem_card":
                imagem,

            "texto_card":
                texto_card[:5000],

            "preco_card":
                preco,

            "preco_original_card":
                preco_original,
        })

    # Remove duplicados
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

def enriquecer_produto(
    oferta
):

    link = oferta.get(
        "link",
        ""
    )

    # -----------------------------------------------------
    # Nome
    # -----------------------------------------------------

    titulo = nome_pelo_link(
        link
    )

    if not titulo:

        titulo = oferta.get(
            "titulo_card",
            ""
        )

    # -----------------------------------------------------
    # Imagem inicial
    # -----------------------------------------------------

    imagem = oferta.get(
        "imagem_card",
        ""
    )

    # -----------------------------------------------------
    # Preços iniciais
    # -----------------------------------------------------

    preco = oferta.get(
        "preco_card",
        ""
    )

    preco_original = oferta.get(
        "preco_original_card",
        ""
    )

    # -----------------------------------------------------
    # Página do produto
    # -----------------------------------------------------

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
        oferta["preco_original"] = (
            preco_original
        )

        return oferta

    if resposta.status_code != 200:

        oferta["titulo"] = (
            titulo
            or "Produto Mercado Livre"
        )

        oferta["imagem"] = imagem
        oferta["preco"] = preco
        oferta["preco_original"] = (
            preco_original
        )

        return oferta

    soup = BeautifulSoup(
        resposta.text,
        "html.parser"
    )

    # =====================================================
    # NOME DO PRODUTO
    # =====================================================

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

    if not titulo:

        og_title = soup.find(
            "meta",
            property="og:title"
        )

        if og_title:

            valor = (
                og_title.get(
                    "content"
                )
                or ""
            ).strip()

            if (
                len(valor) >= 10
                and valor.lower()
                not in [
                    "mercado livre",
                    "mercadolivre",
                ]
            ):

                titulo = valor

    # =====================================================
    # IMAGEM
    # =====================================================

    imagem_pagina = procurar_imagem_no_html(
        soup
    )

    if imagem_pagina:

        imagem = imagem_pagina

    # =====================================================
    # JSON-LD
    # =====================================================

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

            # -------------------------------------------------
            # Nome
            # -------------------------------------------------

            nome_json = bloco.get(
                "name"
            )

            if nome_json:

                nome_json = str(
                    nome_json
                ).strip()

                if (
                    len(nome_json) >= 10
                    and nome_json.lower()
                    not in [
                        "mercado livre",
                        "mercadolivre",
                    ]
                ):

                    titulo = nome_json

            # -------------------------------------------------
            # Imagem
            # -------------------------------------------------

            imagem_json = bloco.get(
                "image"
            )

            if isinstance(
                imagem_json,
                list
            ):

                for item in imagem_json:

                    if isinstance(
                        item,
                        str
                    ):

                        if imagem_valida(
                            item
                        ):

                            imagem = item
                            break

                    elif isinstance(
                        item,
                        dict
                    ):

                        candidato = (
                            item.get(
                                "url"
                            )
                            or ""
                        )

                        if imagem_valida(
                            candidato
                        ):

                            imagem = candidato
                            break

            elif isinstance(
                imagem_json,
                str
            ):

                if imagem_valida(
                    imagem_json
                ):

                    imagem = imagem_json

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

                if imagem_valida(
                    candidato
                ):

                    imagem = candidato

            # -------------------------------------------------
            # Preços
            # -------------------------------------------------

            offers = bloco.get(
                "offers"
            )

            if isinstance(
                offers,
                list
            ):

                if offers:

                    offers = offers[0]

                else:

                    offers = {}

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

                high_price = (
                    offers.get(
                        "highPrice"
                    )
                )

                if high_price:

                    preco_original = str(
                        high_price
                    )

    # =====================================================
    # PREÇOS VISÍVEIS NA PÁGINA
    # =====================================================

    texto_pagina = limpar_texto(
        soup.get_text(
            " ",
            strip=True
        )
    )

    atual_html, original_html = (
        extrair_precos(
            texto_pagina
        )
    )

    if (
        atual_html
        and original_html
    ):

        preco = atual_html

        preco_original = (
            original_html
        )

    # =====================================================
    # FALLBACKS
    # =====================================================

    if not titulo:

        titulo = (
            nome_pelo_link(
                link
            )
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

    if not preco_original:

        preco_original = oferta.get(
            "preco_original_card",
            ""
        )

    # =====================================================
    # RESULTADO
    # =====================================================

    oferta["titulo"] = limpar_texto(
        titulo
    )[:300]

    oferta["imagem"] = imagem

    oferta["preco"] = formatar_preco(
        preco
    )

    oferta["preco_original"] = (
        formatar_preco(
            preco_original
        )
    )

    # =====================================================
    # DESCONTO
    # =====================================================

    desconto_calculado = (
        calcular_desconto(
            oferta["preco"],
            oferta["preco_original"]
        )
    )

    if desconto_calculado > 0:

        oferta["desconto"] = (
            desconto_calculado
        )

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

        titulo = (
            oferta.get(
                "titulo",
                ""
            )
            .strip()
        )

        if (
            not titulo
            or titulo.lower()
            in [
                "mercado livre",
                "mercadolivre",
                "produto mercado livre",
            ]
        ):

            titulo_slug = nome_pelo_link(
                oferta.get(
                    "link",
                    ""
                )
            )

            if titulo_slug:

                oferta["titulo"] = (
                    titulo_slug
                )

            else:

                continue

        # Não envia sem preço
        if not oferta.get(
            "preco"
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
            f"🔥 Por: {preco}\n"
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
        "⚠️ Confira a oferta antes "
        "de publicar."
    )

    teclado = {
        "inline_keyboard": [
            [
                {
                    "text": "✅ APROVAR",
                    "callback_data":
                        f"aprovar:{token}",
                },
                {
                    "text": "❌ DESCARTAR",
                    "callback_data":
                        f"descartar:{token}",
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

    # =====================================================
    # ENVIA COM FOTO
    # =====================================================

    if imagem:

        payload["photo"] = imagem
        payload["caption"] = texto

        resposta = telegram_api(
            "sendPhoto",
            payload
        )

        # Se Telegram rejeitar a imagem,
        # manda o texto em vez de perder a oferta.
        if resposta.status_code != 200:

            print(
                "Imagem rejeitada pelo Telegram:",
                resposta.text
            )

            payload_texto = {
                "chat_id":
                    TELEGRAM_ADMIN_CHAT_ID,

                "text":
                    texto,

                "reply_markup":
                    teclado,
            }

            resposta = telegram_api(
                "sendMessage",
                payload_texto
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

        print(
            "Erro Telegram:",
            resposta.text
        )

    return resposta


# =========================================================
# BOTÕES
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

    # =====================================================
    # DESCARTAR
    # =====================================================

    if acao == "descartar":

        pending.pop(
            token,
            None
        )

        try:

            telegram_api(
                "sendMessage",
                {
                    "chat_id":
                        chat_id,

                    "text":
                        (
                            "❌ Oferta descartada.\n\n"
                            "Ela não será publicada."
                        ),
                }
            )

        except Exception:

            pass

        return

    # =====================================================
    # APROVAR
    # =====================================================

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
                    "chat_id":
                        chat_id,

                    "text":
                        (
                            "💰 OFERTA APROVADA!\n\n"
                            "Agora envie seu "
                            "LINK DE AFILIADO.\n\n"
                            "Assim que receber, "
                            "vou publicar no canal."
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
            f"🔥 Por: {preco}\n"
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

        resposta = telegram_api(
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

        # Fallback caso a imagem dê erro
        if resposta.status_code != 200:

            print(
                "Erro ao enviar imagem:",
                resposta.text
            )

            resposta = telegram_api(
                "sendMessage",
                {
                    "chat_id":
                        TELEGRAM_CHANNEL_ID,

                    "text":
                        texto,
                }
            )

        return resposta

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

    # -----------------------------------------------------
    # Verifica link
    # -----------------------------------------------------

    if not re.match(
        r"^https?://",
        texto,
        re.IGNORECASE
    ):

        try:

            telegram_api(
                "sendMessage",
                {
                    "chat_id":
                        chat_id,

                    "text":
                        (
                            "⚠️ Envie um link válido "
                            "começando com https://"
                        ),
                }
            )

        except Exception:

            pass

        return

    # -----------------------------------------------------
    # Verifica canal
    # -----------------------------------------------------

    if not TELEGRAM_CHANNEL_ID:

        try:

            telegram_api(
                "sendMessage",
                {
                    "chat_id":
                        chat_id,

                    "text":
                        (
                            "⚠️ TELEGRAM_CHANNEL_ID "
                            "não está configurado."
                        ),
                }
            )

        except Exception:

            pass

        return

    # -----------------------------------------------------
    # Publica
    # -----------------------------------------------------

    try:

        resposta = publicar_oferta(
            oferta,
            texto
        )

    except Exception as erro:

        print(
            "Erro ao publicar:",
            erro
        )

        return

    # -----------------------------------------------------
    # Sucesso
    # -----------------------------------------------------

    if resposta.status_code == 200:

        aguardando_afiliado.pop(
            chat_id,
            None
        )

        try:

            telegram_api(
                "sendMessage",
                {
                    "chat_id":
                        chat_id,

                    "text":
                        (
                            "🚀 PUBLICADO!\n\n"
                            "A oferta foi publicada "
                            "no canal."
                        ),
                }
            )

        except Exception:

            pass

    else:

        print(
            "Telegram respondeu:",
            resposta.text
        )

        try:

            telegram_api(
                "sendMessage",
                {
                    "chat_id":
                        chat_id,

                    "text":
                        (
                            "❌ Não consegui publicar "
                            "a oferta no canal.\n\n"
                            f"Telegram HTTP "
                            f"{resposta.status_code}"
                        ),
                }
            )

        except Exception:

            pass


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():

    return """
    <html>

    <head>
        <title>Bot de Ofertas</title>
    </head>

    <body>

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

        <hr>

        <p>
            Mercado Livre →
            Aprovação →
            Link afiliado →
            Canal
        </p>

    </body>

    </html>
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

        <p>
            Status:
            <b>{status}</b>
        </p>
        """

    if not ofertas:

        return """
        <h2>
            ❌ Nenhuma oferta válida encontrada.
        </h2>
        """

    enviadas = 0

    # Máximo de 3 ofertas por rodada
    for oferta in ofertas[:3]:

        try:

            resposta = (
                enviar_oferta_para_aprovacao(
                    oferta
                )
            )

            if resposta.status_code == 200:

                enviadas += 1

        except Exception as erro:

            print(
                "Erro ao enviar oferta:",
                erro
            )

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
# WEBHOOK
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

    except Exception as erro:

        print(
            "Erro webhook:",
            erro
        )

    return "OK"


# =========================================================
# CONFIGURAR WEBHOOK
# =========================================================

@app.route(
    "/configurar-webhook"
)
def rota_webhook():

    sucesso = configurar_webhook()

    if sucesso:

        return """
        <h2>✅ Webhook configurado!</h2>

        <p>
            Botões de aprovação ativos.
        </p>
        """

    return """
    <h2>❌ Falha no webhook</h2>

    <p>
        Confira o TELEGRAM_BOT_TOKEN
        no Render.
    </p>
    """


# =========================================================
# INICIAR
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
