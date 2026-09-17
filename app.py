from flask import Flask, request, redirect
import os
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

app = Flask(__name__)

CLIENT_ID = os.environ.get("ML_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("ML_CLIENT_SECRET", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "@TNBofertasMercadoLivreBR").strip()

BASE_URL = "https://ofertas-mercado-livre-bot.onrender.com"
REDIRECT_URI = f"{BASE_URL}/oauth/callback"
WEBHOOK_URL = f"{BASE_URL}/telegram/webhook"

ML_ACCESS_TOKEN = None
ML_REFRESH_TOKEN = None

OFERTAS = {}
AGUARDANDO_LINK = {}

TERMOS_CATEGORIAS = [
    "smartphone",
    "fone bluetooth",
    "smart tv",
    "notebook",
    "tenis",
    "perfume",
    "air fryer",
    "relogio",
    "caixa de som",
    "aspirador"
]


def telegram_api(metodo, payload):
    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/{metodo}"
    )

    return requests.post(
        url,
        json=payload,
        timeout=20
    )


def formatar_preco(preco):
    if preco is None:
        return "Consulte o preco"

    try:
        preco = float(preco)
    except (TypeError, ValueError):
        return str(preco)

    texto = f"R$ {preco:,.2f}"

    return (
        texto
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
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
        <a href="/buscar-mais-vendidos">
            2 - Buscar mais vendidos 🔥
        </a>
    </p>

    <p>
        <a href="/configurar-webhook">
            3 - Configurar Webhook
        </a>
    </p>
    """


@app.route("/configurar-webhook")
def configurar_webhook():
    if not TELEGRAM_BOT_TOKEN:
        return "Token do Telegram nao configurado."

    try:
        resposta = telegram_api(
            "setWebhook",
            {
                "url": WEBHOOK_URL,
                "allowed_updates": [
                    "callback_query",
                    "message"
                ]
            }
        )

        dados = resposta.json()

    except Exception:
        return "Erro ao configurar webhook."

    if not dados.get("ok"):
        return "Telegram nao aceitou o webhook."

    return """
    <h2>WEBHOOK CONFIGURADO! ✅</h2>
    <p>Telegram conectado ao bot.</p>
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
    global ML_ACCESS_TOKEN, ML_REFRESH_TOKEN

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
            timeout=20
        )

    except requests.RequestException:
        return "Erro de conexao com Mercado Livre."

    if resposta.status_code != 200:
        return (
            "Erro ao conectar Mercado Livre. "
            f"Codigo: {resposta.status_code}"
        )

    dados = resposta.json()

    ML_ACCESS_TOKEN = dados.get("access_token")
    ML_REFRESH_TOKEN = dados.get("refresh_token")

    print(
        "OAUTH TOKEN RECEBIDO. "
        f"expires_in={dados.get('expires_in')} "
        f"user_id={dados.get('user_id')}"
    )

    if not ML_ACCESS_TOKEN:
        return "Access token nao recebido."

    return """
    <h2>Mercado Livre conectado! ✅</h2>

    <p>
        <a href="/buscar-mais-vendidos">
            Buscar mais vendidos 🔥
        </a>
    </p>
    """


def descobrir_categoria(termo, headers):
    try:
        resposta = requests.get(
            (
                "https://api.mercadolibre.com/"
                "sites/MLB/domain_discovery/search"
            ),
            headers=headers,
            params={
                "q": termo,
                "limit": 1
            },
            timeout=4
        )

    except requests.RequestException:
        return None

    if resposta.status_code != 200:
        return None

    resultados = resposta.json()

    if not resultados:
        return None

    return resultados[0].get("category_id")


def consultar_ranking(category_id, headers):
    try:
        resposta = requests.get(
            (
                "https://api.mercadolibre.com/"
                f"highlights/MLB/category/{category_id}"
            ),
            headers=headers,
            timeout=4
        )

    except requests.RequestException:
        return []

    if resposta.status_code != 200:
        return []

    dados = resposta.json()

    return dados.get("content") or []


def consultar_item(item_id, headers):
    try:
        resposta = requests.get(
            f"https://api.mercadolibre.com/items/{item_id}",
            headers=headers,
            timeout=4
        )

    except requests.RequestException:
        return None

    if resposta.status_code != 200:
        return None

    item = resposta.json()

    if item.get("status") != "active":
        return None

    permalink = item.get("permalink")

    if not permalink:
        return None

    titulo = item.get("title") or "Produto"

    preco = item.get("price")

    original_price = item.get("original_price")

    imagem = (
        item.get("secure_thumbnail")
        or item.get("thumbnail")
    )

    pictures = item.get("pictures") or []

    if pictures:
        imagem = (
            pictures[0].get("secure_url")
            or pictures[0].get("url")
            or imagem
        )

    return {
        "item_id": str(item_id),
        "nome": titulo,
        "preco_numero": preco,
        "preco": formatar_preco(preco),
        "preco_original": original_price,
        "imagem": imagem,
        "link_normal": permalink
    }


def converter_product(product_id, headers, profundidade=0):
    """Converte PRODUCT em oferta.

    Se houver ITEM vencedor, usa o anúncio real.
    Se a API não expuser o buy_box_winner, não descarta o PRODUCT:
    usa a página individual do produto do catálogo como fallback.
    """
    try:
        resposta = requests.get(
            f"https://api.mercadolibre.com/products/{product_id}",
            headers=headers,
            timeout=3,
        )
    except requests.RequestException:
        return None

    if resposta.status_code != 200:
        return None

    try:
        produto = resposta.json()
    except ValueError:
        return None

    if produto.get("status") not in (None, "active"):
        return None

    vencedor = produto.get("buy_box_winner") or {}
    item_id = vencedor.get("item_id")

    # Melhor caso: existe anúncio vencedor real.
    if item_id:
        oferta = consultar_item(item_id, headers)
        if oferta:
            preco = vencedor.get("price")
            if preco is not None:
                oferta["preco_numero"] = preco
                oferta["preco"] = formatar_preco(preco)

            original_price = vencedor.get("original_price")
            if original_price is not None:
                oferta["preco_original"] = original_price

            oferta["product_id"] = str(product_id)
            return oferta

    # Produto pai: tenta alguns filhos antes do fallback.
    if profundidade == 0:
        filhos = produto.get("children_ids") or []
        for filho in filhos[:5]:
            oferta = converter_product(filho, headers, profundidade=1)
            if oferta and oferta.get("item_real"):
                return oferta

    # FALLBACK IMPORTANTE:
    # PRODUCT é um produto específico do catálogo e pode ser aberto pela
    # página /p/{PRODUCT_ID}. Assim não dependemos do USER_PRODUCT de terceiro,
    # que no token atual retorna 403.
    nome = (
        produto.get("name")
        or produto.get("title")
        or produto.get("short_description", {}).get("content")
        or "Produto Mercado Livre"
    )

    imagem = None
    pictures = produto.get("pictures") or []
    if pictures:
        primeira = pictures[0]
        if isinstance(primeira, dict):
            imagem = primeira.get("secure_url") or primeira.get("url")

    if not imagem:
        imagem = produto.get("thumbnail")

    preco = vencedor.get("price")
    original_price = vencedor.get("original_price")

    return {
        # Mantemos uma chave única para os botões e memória da oferta.
        "item_id": str(product_id),
        "product_id": str(product_id),
        "item_real": False,
        "nome": nome,
        "preco_numero": preco,
        "preco": formatar_preco(preco),
        "preco_original": original_price,
        "imagem": imagem,
        "link_normal": f"https://www.mercadolivre.com.br/p/{product_id}",
    }

def calcular_desconto(preco, original_price):
    try:
        preco = float(preco)
        original_price = float(original_price)

        if original_price <= preco:
            return None

        desconto = (
            (original_price - preco)
            / original_price
        ) * 100

        return round(desconto)

    except (TypeError, ValueError, ZeroDivisionError):
        return None


def consultar_item_publico(item_id):
    """Consulta um item publico sem Authorization."""
    try:
        resposta = requests.get(
            f"https://api.mercadolibre.com/items/{item_id}",
            timeout=4,
        )
    except requests.RequestException as erro:
        print(f"ERRO item {item_id}: {erro}")
        return None

    print(f"ITEM PUBLICO {item_id}: {resposta.status_code}")

    if resposta.status_code != 200:
        print(
            f"ERRO item {item_id} {resposta.status_code} "
            f"{resposta.text[:300]}"
        )
        return None

    try:
        item = resposta.json()
    except ValueError:
        return None

    if item.get("status") != "active":
        return None

    permalink = item.get("permalink")
    preco = item.get("price")
    if not permalink or preco is None:
        return None

    imagem = (
        item.get("secure_thumbnail")
        or item.get("thumbnail")
    )

    pictures = item.get("pictures") or []
    if pictures:
        imagem = (
            pictures[0].get("secure_url")
            or pictures[0].get("url")
            or imagem
        )

    return {
        "item_id": str(item_id),
        "nome": item.get("title") or "Produto",
        "preco_numero": preco,
        "preco": formatar_preco(preco),
        "preco_original": item.get("original_price"),
        "imagem": imagem,
        "link_normal": permalink,
    }


def converter_user_product(user_product_id, headers):
    """Converte um USER_PRODUCT do ranking em uma publicacao compravel.

    Primeiro consulta o UP. Depois tenta a busca privada do vendedor.
    Se a API negar por o token nao pertencer ao vendedor, usa a busca
    publica de listagens do seller e confere o user_product_id dos itens.
    """
    try:
        resposta = requests.get(
            f"https://api.mercadolibre.com/user-products/{user_product_id}",
            headers=headers,
            timeout=4,
        )
    except requests.RequestException as erro:
        print(f"ERRO USER_PRODUCT {user_product_id}: {erro}")
        return None

    print(f"USER_PRODUCT {user_product_id}: {resposta.status_code}")
    if resposta.status_code != 200:
        return None

    try:
        up = resposta.json()
    except ValueError:
        return None

    seller_id = up.get("user_id") or up.get("seller_id")
    category_id = up.get("category_id")
    catalog_product_id = up.get("catalog_product_id")

    if not seller_id:
        print(f"USER_PRODUCT {user_product_id} sem user_id")
        return None

    # 1) Tenta o recurso do vendedor. Em vendedores terceiros ele pode
    # responder 401/403, porque o token nao pertence a esse seller.
    try:
        busca = requests.get(
            f"https://api.mercadolibre.com/users/{seller_id}/items/search",
            headers=headers,
            params={"user_product_id": user_product_id, "limit": 20},
            timeout=4,
        )
        print(f"ITENS PRIVADOS USER_PRODUCT {user_product_id}: {busca.status_code}")

        if busca.status_code == 200:
            try:
                item_ids = busca.json().get("results") or []
            except ValueError:
                item_ids = []

            for item_id in item_ids[:5]:
                oferta = consultar_item(item_id, headers)
                if oferta:
                    oferta["user_product_id"] = str(user_product_id)
                    return oferta
    except requests.RequestException as erro:
        print(f"ERRO busca privada USER_PRODUCT {user_product_id}: {erro}")

    # 2) Fallback para as listagens publicas do vendedor. Este recurso e
    # documentado para listar anuncios ativos por seller_id.
    params = {
        "seller_id": seller_id,
        "limit": 20,
    }
    if category_id:
        params["category"] = category_id

    try:
        publica = requests.get(
            "https://api.mercadolibre.com/sites/MLB/search",
            headers=headers,
            params=params,
            timeout=4,
        )
    except requests.RequestException as erro:
        print(f"ERRO busca publica seller {seller_id}: {erro}")
        return None

    print(
        f"LISTAGENS PUBLICAS seller={seller_id} "
        f"UP={user_product_id}: {publica.status_code}"
    )
    if publica.status_code != 200:
        return None

    try:
        resultados = publica.json().get("results") or []
    except ValueError:
        return None

    # Alguns resultados ja trazem user_product_id/catalog_product_id.
    # Se nao trouxerem, consulta apenas poucos itens para manter a rota rapida.
    candidatos = []
    for resultado in resultados[:20]:
        rid = resultado.get("id")
        if not rid:
            continue

        rup = str(resultado.get("user_product_id") or "")
        rcp = str(resultado.get("catalog_product_id") or "")

        if rup == str(user_product_id):
            candidatos.insert(0, rid)
        elif catalog_product_id and rcp == str(catalog_product_id):
            candidatos.append(rid)

    # Se a busca nao expuser os identificadores no resultado resumido,
    # testa no maximo os 5 primeiros anuncios do seller/categoria.
    if not candidatos:
        candidatos = [r.get("id") for r in resultados[:5] if r.get("id")]

    for item_id in candidatos[:5]:
        try:
            detalhe = requests.get(
                f"https://api.mercadolibre.com/items/{item_id}",
                headers=headers,
                timeout=3,
            )
        except requests.RequestException:
            continue

        if detalhe.status_code != 200:
            continue

        try:
            item = detalhe.json()
        except ValueError:
            continue

        item_up = str(item.get("user_product_id") or "")
        item_cp = str(item.get("catalog_product_id") or "")

        corresponde = item_up == str(user_product_id)
        if not corresponde and catalog_product_id:
            corresponde = item_cp == str(catalog_product_id)

        if not corresponde:
            continue

        oferta = consultar_item(item_id, headers)
        if oferta:
            oferta["user_product_id"] = str(user_product_id)
            return oferta

    return None

def encontrar_mais_vendido(headers):
    """Busca curta: no maximo ~12 segundos, sem USER_PRODUCT lento."""
    inicio = time.monotonic()
    limite_segundos = 12

    for termo in TERMOS_CATEGORIAS[:3]:
        if time.monotonic() - inicio >= limite_segundos:
            return None

        category_id = descobrir_categoria(termo, headers)
        if not category_id:
            continue

        ranking = consultar_ranking(category_id, headers)
        print(f"RANKING {termo} {category_id}: {len(ranking)} resultados")

        # Só testa caminhos que podem virar anúncio diretamente.
        candidatos = []
        for posicao, entrada in enumerate(ranking[:10], start=1):
            tipo = str(entrada.get("type") or "").upper()
            identificador = entrada.get("id")
            if identificador and tipo in {"ITEM", "PRODUCT"}:
                candidatos.append((tipo, identificador, posicao))

        if not candidatos:
            continue

        def testar(candidato):
            tipo, identificador, posicao = candidato
            if time.monotonic() - inicio >= limite_segundos:
                return None

            try:
                if tipo == "ITEM":
                    oferta = consultar_item(identificador, headers)
                else:
                    oferta = converter_product(identificador, headers)
            except Exception as erro:
                print(f"ERRO candidato {tipo} {identificador}: {erro}")
                return None

            if not oferta:
                return None

            oferta["posicao"] = posicao
            oferta["categoria_busca"] = termo
            oferta["tipo_ranking"] = tipo
            oferta["desconto"] = calcular_desconto(
                oferta.get("preco_numero"),
                oferta.get("preco_original"),
            )
            return oferta

        with ThreadPoolExecutor(max_workers=4) as executor:
            futuros = [executor.submit(testar, c) for c in candidatos[:8]]

            try:
                for futuro in as_completed(futuros, timeout=8):
                    if time.monotonic() - inicio >= limite_segundos:
                        break

                    oferta = futuro.result()
                    if oferta:
                        for f in futuros:
                            f.cancel()
                        return oferta
            except TimeoutError:
                print(f"LIMITE atingido na categoria {termo}")

            for f in futuros:
                f.cancel()

    return None


@app.route("/status")
def status():
    return {
        "mercado_livre_conectado": bool(ML_ACCESS_TOKEN),
        "client_id_configurado": bool(CLIENT_ID),
        "client_secret_configurado": bool(CLIENT_SECRET),
        "telegram_configurado": bool(TELEGRAM_BOT_TOKEN),
        "telegram_chat_configurado": bool(TELEGRAM_CHAT_ID),
        "redirect_uri": REDIRECT_URI,
    }


@app.route("/buscar-mais-vendidos")
def buscar_mais_vendidos():
    if not ML_ACCESS_TOKEN:
        return """
        <h3>Conecte o Mercado Livre primeiro.</h3>
        <p><a href="/login">Conectar Mercado Livre</a></p>
        """

    headers = {
        "Authorization": f"Bearer {ML_ACCESS_TOKEN}"
    }

    oferta = encontrar_mais_vendido(headers)

    if not oferta:
        return """
        <h3>
            Nao encontrei uma publicacao compravel
            nos rankings testados.
        </h3>

        <p>
            ITEM e PRODUCT foram testados dentro do limite rapido. USER_PRODUCT foi ignorado para evitar o bloqueio 403 e a demora.
        </p>
        """

    item_id = oferta["item_id"]

    OFERTAS[item_id] = oferta

    nome = oferta["nome"]
    preco = oferta["preco"]
    imagem = oferta["imagem"]
    link_normal = oferta["link_normal"]
    posicao = oferta.get("posicao", "?")
    desconto = oferta.get("desconto")
    categoria = oferta.get(
        "categoria_busca",
        ""
    )

    legenda = (
        "🔥 MAIS VENDIDO ENCONTRADO!\n\n"
        f"🏆 Ranking: #{posicao}\n"
        f"📂 Categoria: {categoria}\n\n"
        f"📦 {nome}\n\n"
    )

    if desconto:
        legenda += (
            f"🏷️ DESCONTO: {desconto}% OFF\n"
        )

        original = formatar_preco(
            oferta.get("preco_original")
        )

        legenda += (
            f"❌ De: {original}\n"
            f"✅ Por: {preco}\n\n"
        )

    else:
        if oferta.get("preco_numero") is None:
            legenda += (
                "💰 Confira o preco atual no link do produto.\n\n"
            )
        else:
            legenda += (
                f"💰 Preco: {preco}\n\n"
            )

    legenda += (
        "🔗 LINK DO PRODUTO:\n"
        f"{link_normal}\n\n"
        "👇 Deseja preparar essa oferta?"
    )

    botoes = {
        "inline_keyboard": [
            [
                {
                    "text": "✅ PUBLICAR",
                    "callback_data": (
                        f"publicar:{item_id}"
                    )
                },
                {
                    "text": "❌ IGNORAR",
                    "callback_data": (
                        f"ignorar:{item_id}"
                    )
                }
            ]
        ]
    }

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "reply_markup": botoes
    }

    try:
        if imagem:
            payload["photo"] = imagem
            payload["caption"] = legenda

            telegram = telegram_api(
                "sendPhoto",
                payload
            )

        else:
            payload["text"] = legenda

            telegram = telegram_api(
                "sendMessage",
                payload
            )

    except requests.RequestException:
        return "Erro ao conectar com Telegram."

    if telegram.status_code != 200:
        return (
            "Erro ao enviar para Telegram. "
            f"Codigo: {telegram.status_code}"
        )

    return """
    <h2>OFERTA ENVIADA! 🔥</h2>
    <p>
        Encontrei um produto do ranking
        com publicacao compravel.
    </p>
    <p>Confira seu Telegram.</p>
    """


@app.route(
    "/telegram/webhook",
    methods=["POST"]
)
def telegram_webhook():
    update = request.get_json(
        silent=True
    ) or {}

    callback = update.get(
        "callback_query"
    )

    if callback:
        callback_id = callback.get("id")
        callback_data = callback.get(
            "data",
            ""
        )

        mensagem_callback = callback.get(
            "message",
            {}
        )

        chat_id = (
            mensagem_callback
            .get("chat", {})
            .get("id")
        )

        if str(chat_id) != str(
            TELEGRAM_CHAT_ID
        ):
            if callback_id:
                telegram_api(
                    "answerCallbackQuery",
                    {
                        "callback_query_id":
                            callback_id,
                        "text":
                            "Acesso nao autorizado."
                    }
                )

            return "OK", 200

        if callback_data.startswith(
            "ignorar:"
        ):
            item_id = callback_data.split(
                ":",
                1
            )[1]

            OFERTAS.pop(
                item_id,
                None
            )

            if AGUARDANDO_LINK.get(
                str(chat_id)
            ) == item_id:
                AGUARDANDO_LINK.pop(
                    str(chat_id),
                    None
                )

            telegram_api(
                "answerCallbackQuery",
                {
                    "callback_query_id":
                        callback_id,
                    "text":
                        "Oferta ignorada ❌"
                }
            )

            telegram_api(
                "sendMessage",
                {
                    "chat_id":
                        TELEGRAM_CHAT_ID,
                    "text": (
                        "❌ Oferta descartada.\n\n"
                        "Vou deixar essa de fora."
                    )
                }
            )

            return "OK", 200

        if callback_data.startswith(
            "publicar:"
        ):
            item_id = callback_data.split(
                ":",
                1
            )[1]

            oferta = OFERTAS.get(
                item_id
            )

            if not oferta:
                telegram_api(
                    "answerCallbackQuery",
                    {
                        "callback_query_id":
                            callback_id,
                        "text":
                            "Oferta expirou."
                    }
                )

                telegram_api(
                    "sendMessage",
                    {
                        "chat_id":
                            TELEGRAM_CHAT_ID,
                        "text": (
                            "⚠️ Essa oferta "
                            "nao esta mais na memoria.\n\n"
                            "Busque uma nova."
                        )
                    }
                )

                return "OK", 200

            link_normal = oferta.get(
                "link_normal",
                ""
            )

            if not link_normal:
                telegram_api(
                    "answerCallbackQuery",
                    {
                        "callback_query_id":
                            callback_id,
                        "text":
                            "Link nao encontrado."
                    }
                )

                return "OK", 200

            AGUARDANDO_LINK[
                str(chat_id)
            ] = item_id

            telegram_api(
                "answerCallbackQuery",
                {
                    "callback_query_id":
                        callback_id,
                    "text":
                        "Oferta aprovada! ✅"
                }
            )

            telegram_api(
                "sendMessage",
                {
                    "chat_id":
                        TELEGRAM_CHAT_ID,
                    "text": (
                        "💰 OFERTA APROVADA!\n\n"
                        "🔗 COPIE ESTE LINK:\n\n"
                        f"{link_normal}\n\n"
                        "Coloque esse link no "
                        "Gerador de Links do "
                        "Mercado Livre.\n\n"
                        "Depois mande aqui para "
                        "o bot o link de afiliado "
                        "gerado. 👇"
                    )
                }
            )

            return "OK", 200

    mensagem = update.get("message")

    if mensagem:
        chat_id = (
            mensagem
            .get("chat", {})
            .get("id")
        )

        texto = mensagem.get(
            "text",
            ""
        ).strip()

        if str(chat_id) != str(
            TELEGRAM_CHAT_ID
        ):
            return "OK", 200

        item_id = AGUARDANDO_LINK.get(
            str(chat_id)
        )

        if not item_id:
            return "OK", 200

        if not (
            texto.startswith("https://")
            or texto.startswith("http://")
        ):
            telegram_api(
                "sendMessage",
                {
                    "chat_id":
                        TELEGRAM_CHAT_ID,
                    "text": (
                        "⚠️ Cole o link completo "
                        "gerado pelo Mercado Livre."
                    )
                }
            )

            return "OK", 200

        oferta = OFERTAS.get(
            item_id
        )

        if not oferta:
            AGUARDANDO_LINK.pop(
                str(chat_id),
                None
            )

            telegram_api(
                "sendMessage",
                {
                    "chat_id":
                        TELEGRAM_CHAT_ID,
                    "text": (
                        "⚠️ Essa oferta expirou.\n\n"
                        "Busque uma nova oferta."
                    )
                }
            )

            return "OK", 200

        # Usa exatamente o link que voce
        # enviar para o bot.
        oferta["link_afiliado"] = texto

        AGUARDANDO_LINK.pop(
            str(chat_id),
            None
        )

        # Monta a publicação final usando EXATAMENTE o link afiliado recebido.
        nome = oferta.get("nome") or "Oferta Mercado Livre"
        imagem = oferta.get("imagem")
        preco_numero = oferta.get("preco_numero")
        preco = oferta.get("preco")
        desconto = oferta.get("desconto")
        preco_original = oferta.get("preco_original")

        legenda_canal = (
            "🔥 OFERTA NO MERCADO LIVRE!\n\n"
            f"📦 {nome}\n\n"
        )

        if desconto and preco_numero is not None:
            legenda_canal += (
                f"🏷️ {desconto}% OFF\n"
                f"❌ De: {formatar_preco(preco_original)}\n"
                f"✅ Por: {preco}\n\n"
            )
        elif preco_numero is not None:
            legenda_canal += f"💰 {preco}\n\n"
        else:
            legenda_canal += "💰 Confira o preço atual no link 👇\n\n"

        legenda_canal += (
            "🛒 COMPRAR AGORA:\n"
            f"{texto}\n\n"
            "⚠️ Preço e disponibilidade podem mudar."
        )

        payload_canal = {
            "chat_id": TELEGRAM_CHANNEL_ID
        }

        try:
            if imagem:
                payload_canal["photo"] = imagem
                payload_canal["caption"] = legenda_canal
                publicacao = telegram_api("sendPhoto", payload_canal)
            else:
                payload_canal["text"] = legenda_canal
                publicacao = telegram_api("sendMessage", payload_canal)

            publicado = publicacao.status_code == 200

        except requests.RequestException as erro:
            print(f"ERRO PUBLICAR CANAL: {erro}")
            publicado = False

        if publicado:
            telegram_api(
                "sendMessage",
                {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": (
                        "🚀 PUBLICADO NO CANAL!\n\n"
                        f"📦 {nome}\n\n"
                        "✅ Seu link de afiliado foi usado na publicação."
                    )
                }
            )
            OFERTAS.pop(item_id, None)
        else:
            telegram_api(
                "sendMessage",
                {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": (
                        "⚠️ Recebi seu link de afiliado, mas o Telegram "
                        "não conseguiu publicar no canal.\n\n"
                        "Confira se o bot continua como administrador do canal "
                        "e se pode publicar mensagens."
                    )
                }
            )

        return "OK", 200

    return "OK", 200


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
