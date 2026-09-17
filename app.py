from flask import Flask
import os
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN", ""
).strip()

TELEGRAM_CHAT_ID = os.environ.get(
    "TELEGRAM_CHAT_ID", ""
).strip()

TIMEOUT = 12

URL_OFERTAS = "https://www.mercadolivre.com.br/ofertas"


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


def baixar_ofertas():
    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/152.0 Safari/537.36"
        ),
        "Accept-Language": "pt-BR,pt;q=0.9"
    }

    try:
        resposta = requests.get(
            URL_OFERTAS,
            headers=headers,
            timeout=TIMEOUT
        )

        return resposta

    except requests.RequestException:
        return None


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


def extrair_ofertas(html):
    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    candidatos = []

    # Procuramos links de produtos dentro
    # da pagina oficial de ofertas.
    for link_tag in soup.find_all(
        "a",
        href=True
    ):
        href = link_tag.get("href", "")

        if not href:
            continue

        # Ignora links que claramente nao
        # sao paginas de produto.
        if (
            "/p/" not in href
            and "produto.mercadolivre.com.br" not in href
            and "/MLB-" not in href
        ):
            continue

        container = link_tag

        # Sobe alguns niveis para capturar
        # titulo, preco e desconto do card.
        for _ in range(4):
            if container.parent:
                container = container.parent

        texto = limpar_texto(
            container.get_text(
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
            # Tenta encontrar um titulo
            # maior dentro do card.
            titulos = container.find_all(
                ["h2", "h3", "p"]
            )

            for tag in titulos:
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

        imagem = ""

        img = container.find("img")

        if img:
            imagem = (
                img.get("data-src")
                or img.get("src")
                or ""
            )

        candidatos.append({
            "titulo": titulo[:250],
            "desconto": desconto,
            "link": url,
            "imagem": imagem,
            "texto": texto[:600]
        })

    # Remove links repetidos.
    unicos = {}

    for oferta in candidatos:
        link = oferta["link"]

        if link not in unicos:
            unicos[link] = oferta

    ofertas = list(
        unicos.values()
    )

    ofertas.sort(
        key=lambda x: x["desconto"],
        reverse=True
    )

    return ofertas


def enviar_telegram(oferta):
    texto = (
        "🔥 OFERTA ENCONTRADA!\n\n"
        f"📦 {oferta['titulo']}\n\n"
        f"📉 {oferta['desconto']}% OFF\n\n"
        "🛒 VER OFERTA:\n"
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


@app.route("/")
def home():
    return """
    <h2>Bot Ofertas Mercado Livre BR 🤖</h2>

    <p>
        Agora sem /sites/MLB/search 😎
    </p>

    <p>
        <a href="/buscar-ofertas">
            🔥 Buscar ofertas reais
        </a>
    </p>
    """


@app.route("/buscar-ofertas")
def buscar_ofertas():
    resposta = baixar_ofertas()

    if resposta is None:
        return """
        <h2>Erro de conexao</h2>

        <p>
            Render nao conseguiu acessar
            a pagina de ofertas.
        </p>
        """

    if resposta.status_code != 200:
        return f"""
        <h2>Pagina respondeu HTTP {resposta.status_code}</h2>

        <p>
            Nao enviamos nenhum produto.
        </p>
        """

    ofertas = extrair_ofertas(
        resposta.text
    )

    if not ofertas:
        return f"""
        <h2>Mercado Livre respondeu HTTP 200 ✅</h2>

        <p>
            Pagina carregada:
            {len(resposta.text)} caracteres.
        </p>

        <p>
            Mas o extrator ainda nao encontrou
            cards de oferta no HTML recebido.
        </p>

        <p>
            Isso significa que ajustaremos
            somente o extrator.
        </p>
        """

    melhor = ofertas[0]

    try:
        telegram = enviar_telegram(
            melhor
        )

    except requests.RequestException:
        return f"""
        <h2>OFERTAS ENCONTRADAS! 🔥</h2>

        <p>
            Quantidade:
            {len(ofertas)}
        </p>

        <p>
            Maior desconto:
            {melhor['desconto']}%
        </p>

        <p>
            Mas o Telegram falhou.
        </p>
        """

    if telegram.status_code != 200:
        return f"""
        <h2>OFERTAS ENCONTRADAS! 🔥</h2>

        <p>
            Quantidade:
            {len(ofertas)}
        </p>

        <p>
            Telegram HTTP:
            {telegram.status_code}
        </p>
        """

    return f"""
    <h2>DEU BOM! 🔥🔥🔥</h2>

    <p>
        Ofertas encontradas:
        <b>{len(ofertas)}</b>
    </p>

    <p>
        Maior desconto encontrado:
        <b>{melhor['desconto']}% OFF</b>
    </p>

    <p>
        Produto enviado para seu Telegram.
    </p>

    <p>
        E sem usar aquele /sites/MLB/search
        do 403 😂
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
