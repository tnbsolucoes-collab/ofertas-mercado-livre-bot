from flask import Flask, request
import os
import re
import uuid
import json
import io
import requests

from bs4 import BeautifulSoup
from urllib.parse import urljoin, unquote
from PIL import Image


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


def telegram_api_com_arquivo(
    metodo,
    data,
    arquivo_nome,
    arquivo_bytes,
    mime_type="image/jpeg"
):

    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN não configurado."
        )

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/{metodo}"
    )

    files = {
        "photo": (
            arquivo_nome,
            arquivo_bytes,
            mime_type
        )
    }

    return requests.post(
        url,
        data=data,
        files=files,
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

    except Exception as erro:

        print(
            "Erro webhook:",
            erro
        )

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

    percentual = (
        (original - atual)
        / original
    ) * 100

    return round(percentual)


def desconto_do_texto(texto):

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
            texto,
            re.IGNORECASE
        )

        if resultado:

            try:

                numero = int(
                    resultado.group(1)
                )

                if 1 <= numero <= 99:
                    return numero

            except Exception:
                pass

    return 0


# =========================================================
# PREÇOS
# =========================================================

def extrair_valores_monetarios(texto):

    if not texto:
        return []

    resultados = re.findall(
        r"R\$\s*[\d\.]+(?:,\d{2})?",
        texto
    )

    valores = []

    for valor in resultados:

        valor = valor.strip()

        numero = valor_numerico(
            valor
        )

        if numero <= 0:
            continue

        # Evita duplicados
        if numero not in [
            item[1]
            for item in valores
        ]:

            valores.append(
                (
                    valor,
                    numero
                )
            )

    return valores


def extrair_preco_principal(
    soup,
    texto
):

    # =====================================================
    # 1. META PRODUCT PRICE
    # =====================================================

    metas = [
        ("meta", {
            "property":
                "product:price:amount"
        }),
        ("meta", {
            "property":
                "product:sale_price:amount"
        }),
    ]

    for tag_name, attrs in metas:

        tag = soup.find(
            tag_name,
            attrs=attrs
        )

        if tag:

            valor = (
                tag.get("content")
                or ""
            ).strip()

            if valor:

                numero = valor_numerico(
                    valor
                )

                if numero > 0:

                    return numero

    # =====================================================
    # 2. JSON-LD
    # =====================================================

    scripts = soup.find_all(
        "script",
        type="application/ld+json"
    )

    for script in scripts:

        conteudo = (
            script.string
            or script.get_text()
            or ""
        ).strip()

        if not conteudo:
            continue

        try:

            dados = json.loads(
                conteudo
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

            if not isinstance(
                offers,
                dict
            ):
                continue

            price = offers.get(
                "price"
            )

            if price is not None:

                numero = valor_numerico(
                    price
                )

                if numero > 0:

                    return numero

    # =====================================================
    # 3. PREÇO PRÓXIMO DO "OFF"
    #
    # Exemplo:
    # R$ 269 R$ 86 68% OFF
    #
    # Aqui pegamos R$ 86,
    # não R$ 28,67 das parcelas.
    # =====================================================

    padrao_off = re.search(
        r"(R\$\s*[\d\.]+(?:,\d{2})?)"
        r".{0,100}?"
        r"(\d{1,2})%\s*OFF",
        texto,
        re.IGNORECASE
    )

    if padrao_off:

        candidatos = re.findall(
            r"R\$\s*[\d\.]+(?:,\d{2})?",
            padrao_off.group(0)
        )

        if candidatos:

            # O último preço antes do OFF
            # normalmente é o preço atual.
            ultimo = candidatos[-1]

            numero = valor_numerico(
                ultimo
            )

            if numero > 0:
                return numero

    return 0


def extrair_preco_original(
    soup,
    texto,
    preco_atual
):

    # =====================================================
    # 1. JSON-LD
    # =====================================================

    scripts = soup.find_all(
        "script",
        type="application/ld+json"
    )

    for script in scripts:

        conteudo = (
            script.string
            or script.get_text()
            or ""
        ).strip()

        if not conteudo:
            continue

        try:

            dados = json.loads(
                conteudo
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

            # Alguns JSONs possuem preço anterior
            for chave in [
                "highPrice",
                "priceBeforeDiscount",
                "originalPrice",
                "listPrice",
            ]:

                valor = bloco.get(
                    chave
                )

                if valor:

                    numero = valor_numerico(
                        valor
                    )

                    if (
                        numero
                        > preco_atual
                    ):

                        return numero

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

                for chave in [
                    "highPrice",
                    "priceBeforeDiscount",
                    "originalPrice",
                    "listPrice",
                ]:

                    valor = offers.get(
                        chave
                    )

                    if valor:

                        numero = valor_numerico(
                            valor
                        )

                        if (
                            numero
                            > preco_atual
                        ):

                            return numero

    # =====================================================
    # 2. PROCURA PADRÃO:
    #
    # R$ 269 ... R$ 86 ... 68% OFF
    # =====================================================

    desconto_site = (
        desconto_do_texto(
            texto
        )
    )

    if desconto_site:

        padrao = re.search(
            r"(R\$\s*[\d\.]+(?:,\d{2})?)"
            r".{0,250}?"
            r"(R\$\s*[\d\.]+(?:,\d{2})?)"
            r".{0,100}?"
            r"(\d{1,2})%\s*OFF",
            texto,
            re.IGNORECASE
        )

        if padrao:

            primeiro = valor_numerico(
                padrao.group(1)
            )

            segundo = valor_numerico(
                padrao.group(2)
            )

            if (
                primeiro > preco_atual
                and abs(
                    calcular_desconto(
                        preco_atual,
                        primeiro
                    )
                    - desconto_site
                ) <= 2
            ):

                return primeiro

            if (
                segundo > preco_atual
                and abs(
                    calcular_desconto(
                        preco_atual,
                        segundo
                    )
                    - desconto_site
                ) <= 2
            ):

                return segundo

    # =====================================================
    # 3. USA O DESCONTO PUBLICADO
    #
    # Se sabemos preço atual + % OFF,
    # podemos recuperar o preço original.
    # =====================================================

    if (
        preco_atual > 0
        and desconto_site > 0
        and desconto_site < 100
    ):

        original = (
            preco_atual
            / (
                1
                - (
                    desconto_site
                    / 100
                )
            )
        )

        # Só aceita valores razoáveis
        if original > preco_atual:

            return round(
                original,
                2
            )

    # =====================================================
    # 4. FALLBACK PELO TEXTO
    #
    # Ignora explicitamente parcelas.
    # =====================================================

    valores = extrair_valores_monetarios(
        texto
    )

    candidatos = []

    for texto_valor, numero in valores:

        if numero <= preco_atual:
            continue

        candidatos.append(
            (
                texto_valor,
                numero
            )
        )

    if candidatos:

        # Se houver vários, prefere o menor
        # acima do preço atual.
        candidatos.sort(
            key=lambda item: item[1]
        )

        return candidatos[0][1]

    return 0


# =========================================================
# NOME
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
# IMAGEM
# =========================================================

def imagem_valida(url):

    if not url:
        return False

    url_lower = url.lower()

    bloqueios = [
        "favicon",
        "sprite",
        "avatar",
        "logo",
    ]

    for bloqueio in bloqueios:

        if bloqueio in url_lower:
            return False

    return (
        url.startswith("http://")
        or url.startswith("https://")
    )


def imagem_do_link(link_tag):

    if not link_tag:
        return ""

    img = link_tag.find(
        "img"
    )

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

        if imagem_valida(valor):
            return valor

    return ""


def procurar_imagem_no_html(
    soup
):

    # OG IMAGE
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

    # TWITTER
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

    # IMG
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

    # SCRIPTS
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
# BAIXAR IMAGEM
# =========================================================

def baixar_imagem(url):

    if not url:
        return None

    try:

        resposta = requests.get(
            url,
            headers=headers_navegador(),
            timeout=TIMEOUT
        )

        if resposta.status_code != 200:

            print(
                "Imagem HTTP:",
                resposta.status_code
            )

            return None

        if not resposta.content:
            return None

        imagem = Image.open(
            io.BytesIO(
                resposta.content
            )
        )

        if imagem.mode != "RGB":

            imagem = imagem.convert(
                "RGB"
            )

        largura, altura = (
            imagem.size
        )

        limite = 1600

        if (
            largura > limite
            or altura > limite
        ):

            imagem.thumbnail(
                (
                    limite,
                    limite
                )
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
            "Erro baixando imagem:",
            erro
        )

        return None


# =========================================================
# EXTRAIR CARDS DAS OFERTAS
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

            desconto = desconto_do_texto(
                texto_card
            )

            if desconto > 0:

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

        desconto = desconto_do_texto(
            texto_card
        )

        if desconto <= 0:
            continue

        titulo = limpar_texto(
            link_tag.get_text(
                " ",
                strip=True
            )
        )

        if (
            not titulo
            or titulo.lower()
            in [
                "mercado livre",
                "ver oferta",
                "comprar",
                "oferta",
            ]
        ):

            titulo = ""

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

        url = urljoin(
            "https://www.mercadolivre.com.br",
            href
        )

        candidatos.append({

            "titulo_card":
                titulo,

            "desconto_card":
                desconto,

            "link":
                url,

            "imagem_card":
                imagem,

            "texto_card":
                texto_card[:5000],
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
            "desconto_card",
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

    titulo = nome_pelo_link(
        link
    )

    if not titulo:

        titulo = oferta.get(
            "titulo_card",
            ""
        )

    imagem = oferta.get(
        "imagem_card",
        ""
    )

    # -----------------------------------------------------
    # ABRE PÁGINA DO PRODUTO
    # -----------------------------------------------------

    try:

        resposta = requests.get(
            link,
            headers=headers_navegador(),
            timeout=TIMEOUT,
            allow_redirects=True
        )

    except Exception as erro:

        print(
            "Erro abrindo produto:",
            erro
        )

        oferta["titulo"] = (
            titulo
            or "Produto Mercado Livre"
        )

        oferta["imagem"] = imagem
        oferta["preco"] = ""
        oferta["preco_original"] = ""
        oferta["desconto"] = 0

        return oferta

    if resposta.status_code != 200:

        print(
            "Produto HTTP:",
            resposta.status_code
        )

        oferta["titulo"] = (
            titulo
            or "Produto Mercado Livre"
        )

        oferta["imagem"] = imagem
        oferta["preco"] = ""
        oferta["preco_original"] = ""
        oferta["desconto"] = 0

        return oferta

    soup = BeautifulSoup(
        resposta.text,
        "html.parser"
    )

    texto_pagina = limpar_texto(
        soup.get_text(
            " ",
            strip=True
        )
    )

    # =====================================================
    # NOME
    # =====================================================

    h1 = soup.find(
        "h1"
    )

    if h1:

        nome = limpar_texto(
            h1.get_text(
                " ",
                strip=True
            )
        )

        if len(nome) >= 10:

            titulo = nome

    if not titulo:

        og_title = soup.find(
            "meta",
            property="og:title"
        )

        if og_title:

            titulo = (
                og_title.get(
                    "content"
                )
                or ""
            ).strip()

    # =====================================================
    # IMAGEM
    # =====================================================

    imagem_pagina = (
        procurar_imagem_no_html(
            soup
        )
    )

    if imagem_pagina:

        imagem = imagem_pagina

    # =====================================================
    # PREÇO ATUAL
    # =====================================================

    preco_atual = (
        extrair_preco_principal(
            soup,
            texto_pagina
        )
    )

    # =====================================================
    # PREÇO ORIGINAL
    # =====================================================

    preco_original = (
        extrair_preco_original(
            soup,
            texto_pagina,
            preco_atual
        )
    )

    # =====================================================
    # DESCONTO
    # =====================================================

    desconto_calculado = (
        calcular_desconto(
            preco_atual,
            preco_original
        )
    )

    desconto_site = (
        desconto_do_texto(
            texto_pagina
        )
    )

    # Se o cálculo bate com o desconto
    # mostrado pelo Mercado Livre,
    # usa o calculado.
    if desconto_calculado > 0:

        desconto = desconto_calculado

    else:

        desconto = desconto_site

    # =====================================================
    # FALLBACK DO CARD
    # =====================================================

    if preco_atual <= 0:

        # Tenta encontrar um preço no card,
        # mas NUNCA usa parcelas como preço
        texto_card = oferta.get(
            "texto_card",
            ""
        )

        preco_atual = (
            extrair_preco_principal(
                BeautifulSoup(
                    "",
                    "html.parser"
                ),
                texto_card
            )
        )

    # =====================================================
    # RESULTADO
    # =====================================================

    oferta["titulo"] = (
        limpar_texto(
            titulo
        )[:300]
        or "Produto Mercado Livre"
    )

    oferta["imagem"] = imagem

    oferta["preco"] = (
        formatar_preco(
            preco_atual
        )
    )

    oferta["preco_original"] = (
        formatar_preco(
            preco_original
        )
    )

    oferta["desconto"] = desconto

    # Debug útil no Render
    print(
        "========================================"
    )

    print(
        "PRODUTO:",
        oferta["titulo"]
    )

    print(
        "PREÇO ATUAL:",
        oferta["preco"]
    )

    print(
        "PREÇO ORIGINAL:",
        oferta["preco_original"]
    )

    print(
        "DESCONTO:",
        oferta["desconto"],
        "%"
    )

    print(
        "IMAGEM:",
        oferta["imagem"]
    )

    print(
        "========================================"
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

    except Exception as erro:

        print(
            "Erro busca:",
            erro
        )

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

        if not oferta.get(
            "titulo"
        ):
            continue

        if not oferta.get(
            "preco"
        ):
            continue

        finais.append(
            oferta
        )

    return finais, "200"


# =========================================================
# TEXTO DE APROVAÇÃO
# =========================================================

def montar_texto_aprovacao(
    oferta
):

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
        "⚠️ Confira a oferta antes de publicar."
    )

    return texto


# =========================================================
# ENVIAR PARA APROVAÇÃO
# =========================================================

def enviar_oferta_para_aprovacao(
    oferta
):

    token = uuid.uuid4().hex[:12]

    pending[token] = oferta

    texto = montar_texto_aprovacao(
        oferta
    )

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

    imagem_url = oferta.get(
        "imagem",
        ""
    )

    imagem_bytes = None

    if imagem_url:

        imagem_bytes = baixar_imagem(
            imagem_url
        )

    # =====================================================
    # ENVIA FOTO
    # =====================================================

    if imagem_bytes:

        try:

            resposta = (
                telegram_api_com_arquivo(
                    "sendPhoto",

                    {
                        "chat_id":
                            TELEGRAM_ADMIN_CHAT_ID,

                        "caption":
                            texto,

                        "reply_markup":
                            json.dumps(
                                teclado,
                                ensure_ascii=False
                            )
                    },

                    "produto.jpg",
                    imagem_bytes,
                    "image/jpeg"
                )
            )

            if resposta.status_code == 200:

                return resposta

            print(
                "Telegram rejeitou foto:",
                resposta.text
            )

        except Exception as erro:

            print(
                "Erro enviando foto:",
                erro
            )

    # =====================================================
    # FALLBACK TEXTO
    # =====================================================

    resposta = telegram_api(
        "sendMessage",
        {
            "chat_id":
                TELEGRAM_ADMIN_CHAT_ID,

            "text":
                texto,

            "reply_markup":
                teclado
        }
    )

    return resposta


# =========================================================
# CALLBACK
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

        telegram_api(
            "sendMessage",
            {
                "chat_id":
                    chat_id,

                "text":
                    (
                        "❌ Oferta descartada.\n\n"
                        "Ela não será publicada."
                    )
            }
        )

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
                    )
            }
        )


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

    imagem_url = oferta.get(
        "imagem",
        ""
    )

    imagem_bytes = None

    if imagem_url:

        imagem_bytes = baixar_imagem(
            imagem_url
        )

    # =====================================================
    # PUBLICAR COM FOTO
    # =====================================================

    if imagem_bytes:

        try:

            resposta = (
                telegram_api_com_arquivo(
                    "sendPhoto",

                    {
                        "chat_id":
                            TELEGRAM_CHANNEL_ID,

                        "caption":
                            texto
                    },

                    "produto.jpg",
                    imagem_bytes,
                    "image/jpeg"
                )
            )

            if resposta.status_code == 200:

                return resposta

            print(
                "Erro foto canal:",
                resposta.text
            )

        except Exception as erro:

            print(
                "Erro foto canal:",
                erro
            )

    # =====================================================
    # FALLBACK TEXTO
    # =====================================================

    return telegram_api(
        "sendMessage",
        {
            "chat_id":
                TELEGRAM_CHANNEL_ID,

            "text":
                texto
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

        telegram_api(
            "sendMessage",
            {
                "chat_id":
                    chat_id,

                "text":
                    (
                        "⚠️ Envie um link válido "
                        "começando com https://"
                    )
            }
        )

        return

    if not TELEGRAM_CHANNEL_ID:

        telegram_api(
            "sendMessage",
            {
                "chat_id":
                    chat_id,

                "text":
                    (
                        "⚠️ TELEGRAM_CHANNEL_ID "
                        "não está configurado."
                    )
            }
        )

        return

    try:

        resposta = publicar_oferta(
            oferta,
            texto
        )

    except Exception as erro:

        print(
            "Erro publicação:",
            erro
        )

        return

    if resposta.status_code == 200:

        aguardando_afiliado.pop(
            chat_id,
            None
        )

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
                    )
            }
        )

    else:

        print(
            "Telegram respondeu:",
            resposta.text
        )

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
                    )
            }
        )


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

    # Apenas 3 por rodada
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
                "Erro oferta:",
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
