from flask import Flask, request, redirect
import os
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import threading
import psycopg2

app = Flask(__name__)

CLIENT_ID = os.environ.get("ML_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("ML_CLIENT_SECRET", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "@TNBofertasMercadoLivreBR").strip()
CRON_SECRET = os.environ.get("CRON_SECRET", "").strip()
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

BASE_URL = "https://ofertas-mercado-livre-bot.onrender.com"
REDIRECT_URI = f"{BASE_URL}/oauth/callback"
WEBHOOK_URL = f"{BASE_URL}/telegram/webhook"

ML_ACCESS_TOKEN = None
ML_REFRESH_TOKEN = None
ML_TOKEN_EXPIRES_AT = 0
TOKEN_LOCK = threading.Lock()

OFERTAS = {}
AGUARDANDO_LINK = {}
PRODUTOS_JA_ENVIADOS = set()
BUSCA_AUTOMATICA_INICIADA = False

GRUPOS_ROTACAO = [
    ("casa", [
        "sofa retratil", "mesa de jantar", "cadeira escritorio", "escrivaninha",
        "cama box", "colchao", "criado mudo", "estante", "sapateira", "poltrona",
        "mesa de centro", "armario de cozinha", "rack para tv", "guarda roupa",
        "luminaria decorativa",
    ]),
    ("gamer", [
        "teclado gamer", "headset gamer", "mouse gamer", "controle gamer",
        "monitor gamer", "cadeira gamer", "microfone gamer", "ssd gamer", "video game",
    ]),
    ("beleza", [
        "perfume feminino", "maquiagem", "kit maquiagem", "skincare feminino",
        "creme facial", "hidratante corporal feminino", "secador de cabelo",
        "chapinha de cabelo", "modelador de cabelo",
    ]),
    ("moda", [
        "tenis feminino", "tenis masculino", "roupa feminina", "roupa masculina",
        "jaqueta", "bolsa feminina",
    ]),
    ("tecnologia", [
        "iphone apple", "celular motorola", "celular xiaomi", "celular samsung",
        "celular realme", "notebook asus", "notebook lenovo", "notebook acer",
        "notebook dell", "smart tv", "fone bluetooth", "caixa de som bluetooth",
        "smartwatch", "tablet",
    ]),
    ("bem_estar", [
        "produto natural", "cha natural", "oleo essencial", "vitaminas",
        "suplemento alimentar", "cuidados pessoais",
    ]),
    ("eletro", [
        "air fryer", "aspirador de po", "cafeteira", "liquidificador", "microondas",
        "ventilador", "maquina de lavar", "geladeira",
    ]),
]

# Mantida apenas por compatibilidade com qualquer trecho antigo que consulte a lista.
TERMOS_CATEGORIAS = [termo for _, termos in GRUPOS_ROTACAO for termo in termos]

# Evita que buscas consecutivas caiam sempre na mesma marca/tipo.
# E apenas memoria temporaria do processo: nao altera OAuth, banco, cron,
# aprovacao, link de afiliado ou publicacao no canal.
ULTIMAS_CATEGORIAS_BUSCADAS = []




def conectar_banco():
    if not DATABASE_URL:
        return None

    return psycopg2.connect(
        DATABASE_URL,
        connect_timeout=10,
        sslmode="require"
    )


def preparar_banco():
    if not DATABASE_URL:
        return False

    conexao = None
    try:
        conexao = conectar_banco()
        with conexao.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ml_tokens (
                    id INTEGER PRIMARY KEY,
                    access_token TEXT NOT NULL,
                    refresh_token TEXT NOT NULL,
                    expires_at DOUBLE PRECISION NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS produtos_enviados (
                    produto_id TEXT PRIMARY KEY,
                    enviado_em TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS historico_diversidade (
                    id BIGSERIAL PRIMARY KEY,
                    tipo_produto TEXT,
                    marca TEXT,
                    nome TEXT,
                    enviado_em TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cursor.execute("""
                ALTER TABLE historico_diversidade
                ADD COLUMN IF NOT EXISTS grupo_rotacao TEXT
            """)
            cursor.execute("""
                ALTER TABLE historico_diversidade
                ADD COLUMN IF NOT EXISTS termo_busca TEXT
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS estado_rotacao (
                    id INTEGER PRIMARY KEY,
                    proximo_grupo INTEGER NOT NULL DEFAULT 0,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cursor.execute("""
                INSERT INTO estado_rotacao (id, proximo_grupo)
                VALUES (1, 0)
                ON CONFLICT (id) DO NOTHING
            """)
        conexao.commit()
        return True
    except Exception as erro:
        print(f"BANCO: erro ao preparar: {type(erro).__name__}")
        return False
    finally:
        if conexao:
            conexao.close()


def salvar_tokens(access_token, refresh_token, expires_in):
    global ML_ACCESS_TOKEN, ML_REFRESH_TOKEN, ML_TOKEN_EXPIRES_AT

    if not access_token or not refresh_token:
        return False

    try:
        segundos = int(expires_in or 21600)
    except (TypeError, ValueError):
        segundos = 21600

    # Renova um pouco antes do vencimento.
    expires_at = time.time() + max(segundos - 120, 60)

    ML_ACCESS_TOKEN = access_token
    ML_REFRESH_TOKEN = refresh_token
    ML_TOKEN_EXPIRES_AT = expires_at

    if not preparar_banco():
        return False

    conexao = None
    try:
        conexao = conectar_banco()
        with conexao.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO ml_tokens
                    (id, access_token, refresh_token, expires_at, updated_at)
                VALUES
                    (1, %s, %s, %s, NOW())
                ON CONFLICT (id)
                DO UPDATE SET
                    access_token = EXCLUDED.access_token,
                    refresh_token = EXCLUDED.refresh_token,
                    expires_at = EXCLUDED.expires_at,
                    updated_at = NOW()
                """,
                (access_token, refresh_token, expires_at)
            )
        conexao.commit()
        print("TOKENS ML: salvos com seguranca no banco.")
        return True
    except Exception as erro:
        print(f"BANCO: erro ao salvar tokens: {type(erro).__name__}")
        return False
    finally:
        if conexao:
            conexao.close()


def carregar_tokens():
    global ML_ACCESS_TOKEN, ML_REFRESH_TOKEN, ML_TOKEN_EXPIRES_AT

    if not preparar_banco():
        return False

    conexao = None
    try:
        conexao = conectar_banco()
        with conexao.cursor() as cursor:
            cursor.execute(
                """
                SELECT access_token, refresh_token, expires_at
                FROM ml_tokens
                WHERE id = 1
                """
            )
            linha = cursor.fetchone()

        if not linha:
            return False

        ML_ACCESS_TOKEN = linha[0]
        ML_REFRESH_TOKEN = linha[1]
        ML_TOKEN_EXPIRES_AT = float(linha[2] or 0)
        return True
    except Exception as erro:
        print(f"BANCO: erro ao carregar tokens: {type(erro).__name__}")
        return False
    finally:
        if conexao:
            conexao.close()


def renovar_token_ml():
    global ML_ACCESS_TOKEN, ML_REFRESH_TOKEN, ML_TOKEN_EXPIRES_AT

    if not ML_REFRESH_TOKEN:
        return False

    try:
        resposta = requests.post(
            "https://api.mercadolibre.com/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "refresh_token": ML_REFRESH_TOKEN
            },
            timeout=20
        )
    except requests.RequestException:
        print("TOKEN ML: falha de conexao ao renovar.")
        return False

    if resposta.status_code != 200:
        print(f"TOKEN ML: renovacao falhou HTTP {resposta.status_code}.")
        return False

    try:
        dados = resposta.json()
    except ValueError:
        return False

    novo_access = dados.get("access_token")
    novo_refresh = dados.get("refresh_token")
    expires_in = dados.get("expires_in", 21600)

    if not novo_access or not novo_refresh:
        return False

    # O Mercado Livre entrega um NOVO refresh token a cada renovacao.
    # Ele precisa ser salvo imediatamente, pois somente o ultimo e valido.
    if not salvar_tokens(novo_access, novo_refresh, expires_in):
        print("TOKEN ML: renovado, mas nao foi possivel persistir.")
        return False

    print("TOKEN ML: renovado automaticamente.")
    return True


def garantir_token_ml():
    global ML_ACCESS_TOKEN, ML_REFRESH_TOKEN, ML_TOKEN_EXPIRES_AT

    with TOKEN_LOCK:
        if not ML_ACCESS_TOKEN or not ML_REFRESH_TOKEN:
            carregar_tokens()

        if not ML_ACCESS_TOKEN or not ML_REFRESH_TOKEN:
            return False

        if time.time() < ML_TOKEN_EXPIRES_AT:
            return True

        return renovar_token_ml()



def produto_ja_enviado(produto_id):
    produto_id = str(produto_id or "").strip()
    if not produto_id:
        return False

    if produto_ja_enviado(produto_id):
        return True

    if not preparar_banco():
        return False

    conexao = None
    try:
        conexao = conectar_banco()
        with conexao.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM produtos_enviados WHERE produto_id = %s LIMIT 1",
                (produto_id,)
            )
            existe = cursor.fetchone() is not None

        if existe:
            registrar_produto_enviado(produto_id)

        return existe
    except Exception as erro:
        print(f"HISTORICO: erro ao consultar: {type(erro).__name__}")
        return False
    finally:
        if conexao:
            conexao.close()


def registrar_produto_enviado(produto_id):
    produto_id = str(produto_id or "").strip()
    if not produto_id:
        return False

    PRODUTOS_JA_ENVIADOS.add(produto_id)

    if not preparar_banco():
        return False

    conexao = None
    try:
        conexao = conectar_banco()
        with conexao.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO produtos_enviados (produto_id, enviado_em)
                VALUES (%s, NOW())
                ON CONFLICT (produto_id) DO NOTHING
                """,
                (produto_id,)
            )
        conexao.commit()
        print(f"HISTORICO: produto {produto_id} salvo no banco.")
        return True
    except Exception as erro:
        print(f"HISTORICO: erro ao salvar: {type(erro).__name__}")
        return False
    finally:
        if conexao:
            conexao.close()


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
    global ML_ACCESS_TOKEN, ML_REFRESH_TOKEN, ML_TOKEN_EXPIRES_AT

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

    novo_access = dados.get("access_token")
    novo_refresh = dados.get("refresh_token")
    expires_in = dados.get("expires_in", 21600)

    if not novo_access or not novo_refresh:
        return "Tokens do Mercado Livre nao recebidos."

    if not salvar_tokens(novo_access, novo_refresh, expires_in):
        return (
            "Mercado Livre autorizou, mas o banco persistente ainda "
            "nao esta configurado corretamente."
        )

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

MARCAS_CONHECIDAS = [
    "samsung", "apple", "iphone", "motorola", "xiaomi", "realme", "asus",
    "lenovo", "acer", "dell", "lg", "philips", "electrolux", "brastemp",
    "consul", "mondial", "oster", "britania", "logitech", "hyperx",
    "redragon", "razer", "corsair", "jbl", "sony", "nivea", "loreal",
    "l'oréal", "maybelline", "wella", "eudora", "natura", "avon",
]

TIPOS_PRODUTO = [
    ("painel_tv", ["painel para tv", "painel de tv", "painel tv", "painel suspenso"]),
    ("rack_tv", ["rack para tv", "rack tv", "rack com"]),
    ("guarda_roupa", ["guarda roupa", "guarda-roupa"]),
    ("sofa", ["sofa", "sofá"]),
    ("mesa", ["mesa de jantar", "mesa centro", "mesa de centro"]),
    ("cadeira", ["cadeira escritorio", "cadeira de escritorio", "cadeira gamer", "cadeira"]),
    ("escrivaninha", ["escrivaninha"]),
    ("cama", ["cama box", "cama casal", "cama solteiro"]),
    ("colchao", ["colchao", "colchão"]),
    ("estante", ["estante"]),
    ("sapateira", ["sapateira"]),
    ("poltrona", ["poltrona"]),
    ("armario", ["armario", "armário"]),
    ("luminaria", ["luminaria", "luminária"]),
    ("smartphone", ["iphone", "smartphone", "celular"]),
    ("notebook", ["notebook", "laptop"]),
    ("smart_tv", ["smart tv", "televisor", " tv "] ),
    ("headset", ["headset"]),
    ("mouse", ["mouse gamer", "mouse sem fio"]),
    ("teclado", ["teclado gamer", "teclado mecanico", "teclado mecânico"]),
    ("monitor", ["monitor gamer", "monitor "] ),
    ("controle_gamer", ["controle gamer", "gamepad"]),
    ("video_game", ["playstation", "xbox", "nintendo switch", "video game"]),
    ("perfume", ["perfume"]),
    ("maquiagem", ["maquiagem", "batom", "mascara de cilios", "máscara de cílios", "base facial"]),
    ("skincare", ["skincare", "serum facial", "sérum facial", "creme facial"]),
    ("cabelo", ["secador de cabelo", "chapinha", "modelador de cabelo"]),
    ("tenis", ["tenis", "tênis"]),
    ("bolsa", ["bolsa feminina", "bolsa "] ),
    ("air_fryer", ["air fryer", "fritadeira eletrica", "fritadeira elétrica"]),
    ("aspirador", ["aspirador"]),
    ("cafeteira", ["cafeteira"]),
    ("liquidificador", ["liquidificador"]),
    ("microondas", ["microondas", "micro-ondas"]),
    ("ventilador", ["ventilador"]),
    ("geladeira", ["geladeira", "refrigerador"]),
]


def identificar_marca(nome):
    texto = f" {str(nome or '').lower()} "
    for marca in MARCAS_CONHECIDAS:
        if marca in texto:
            return "apple" if marca == "iphone" else marca.replace("l'oréal", "loreal")
    return ""


def identificar_tipo(nome, termo_busca=""):
    texto = f" {str(nome or '').lower()} "
    for tipo, palavras in TIPOS_PRODUTO:
        if any(palavra in texto for palavra in palavras):
            return tipo
    # Se o titulo nao revelar um tipo conhecido, usa o termo que originou a busca.
    termo = str(termo_busca or "").strip().lower().replace(" ", "_")
    return termo[:80]


def historico_diversidade_recente(limite=30):
    if not preparar_banco():
        return []
    conexao = None
    try:
        conexao = conectar_banco()
        with conexao.cursor() as cursor:
            cursor.execute(
                """
                SELECT tipo_produto, marca, nome, grupo_rotacao, termo_busca
                FROM historico_diversidade
                ORDER BY enviado_em DESC, id DESC
                LIMIT %s
                """,
                (limite,),
            )
            return cursor.fetchall() or []
    except Exception as erro:
        print(f"DIVERSIDADE: erro ao ler historico: {type(erro).__name__}")
        return []
    finally:
        if conexao:
            conexao.close()


def obter_proximo_grupo():
    if not preparar_banco():
        return 0
    conexao = None
    try:
        conexao = conectar_banco()
        with conexao.cursor() as cursor:
            cursor.execute("SELECT proximo_grupo FROM estado_rotacao WHERE id = 1")
            linha = cursor.fetchone()
            return int((linha or [0])[0] or 0) % len(GRUPOS_ROTACAO)
    except Exception as erro:
        print(f"ROTACAO: erro ao ler estado: {type(erro).__name__}")
        return 0
    finally:
        if conexao:
            conexao.close()


def oferta_compativel_com_grupo(oferta):
    """Valida apenas grupos que precisam de correspondencia forte com o titulo real.

    No grupo gamer, evita aceitar produtos comuns apenas porque o ranking da busca
    veio de um termo como "teclado gamer" ou "headset gamer". O titulo real
    precisa indicar que o produto e gamer/gaming ou pertencer a uma linha gamer
    reconhecivel. Nao altera o nome original do Mercado Livre.
    """
    grupo = str(oferta.get("grupo_rotacao") or "").lower()
    if grupo != "gamer":
        return True

    nome = str(oferta.get("nome") or "").lower()
    termo = str(oferta.get("categoria_busca") or "").lower()

    # Consoles/videogames sao aceitos pela propria identidade do produto.
    sinais_console = [
        "playstation", "ps4", "ps5", "xbox", "nintendo switch",
        "video game", "videogame", "gamepad", "controle gamer",
    ]
    if any(sinal in nome for sinal in sinais_console):
        return True

    # Indicacao explicita no titulo e o sinal mais seguro.
    if "gamer" in nome or "gaming" in nome:
        return True

    # Linhas/marcas muito associadas a perifericos gamer. A marca so vale quando
    # o produto tambem e um periferico do grupo, evitando classificar qualquer
    # item da marca como gamer.
    marcas_gamer = [
        "redragon", "hyperx", "razer", "corsair", "steelseries",
        "husky gaming", "pichau gaming", "mancer", "fallen",
    ]
    perifericos = [
        "teclado", "mouse", "headset", "fone", "monitor", "cadeira",
        "microfone", "controle", "gamepad", "ssd",
    ]
    if any(marca in nome for marca in marcas_gamer) and any(p in nome for p in perifericos):
        return True

    # Logitech tem linhas comuns e gamer; exige indicacao de linha G/PRO gamer
    # ou a palavra gamer/gaming (ja tratada acima).
    if "logitech" in nome and any(p in nome for p in perifericos):
        linhas_logitech_g = [
            " g203", " g305", " g403", " g502", " g703", " g903",
            " g213", " g413", " g512", " g515", " g613", " g715", " g915",
            " g332", " g335", " g432", " g435", " g535", " g733", " g935",
            "pro x", "pro 2 lightspeed", "lightspeed gaming",
        ]
        if any(linha in f" {nome}" for linha in linhas_logitech_g):
            return True

    print(f"FILTRO GAMER: rejeitado termo={termo} titulo={nome[:100]}")
    return False


def oferta_repetitiva(oferta, historico):
    nome = oferta.get("nome") or ""
    tipo = identificar_tipo(nome, oferta.get("categoria_busca"))
    marca = identificar_marca(nome)
    oferta["tipo_produto"] = tipo
    oferta["marca_detectada"] = marca

    # Dentro do grupo atual, evita repetir o mesmo tipo usado nas ultimas voltas.
    grupo = oferta.get("grupo_rotacao") or ""
    historico_grupo = [linha for linha in historico if str(linha[3] or "") == grupo]
    tipos_grupo = [str(linha[0] or "") for linha in historico_grupo[:3]]
    if tipo and tipo in tipos_grupo:
        print(f"DIVERSIDADE: pulando tipo repetido no grupo {grupo}: {tipo} | {nome[:80]}")
        return True

    # Marca tambem nao deve dominar as voltas recentes.
    marcas_recentes = [str(linha[1] or "") for linha in historico[:3]]
    if marca and marca in marcas_recentes:
        print(f"DIVERSIDADE: pulando marca repetida: {marca} | {nome[:80]}")
        return True

    return False


def registrar_diversidade(oferta):
    if not preparar_banco():
        return False
    nome = oferta.get("nome") or ""
    tipo = oferta.get("tipo_produto") or identificar_tipo(nome, oferta.get("categoria_busca"))
    marca = oferta.get("marca_detectada") or identificar_marca(nome)
    grupo = oferta.get("grupo_rotacao") or ""
    termo = oferta.get("categoria_busca") or ""
    indice_grupo = int(oferta.get("indice_grupo_rotacao", 0))
    proximo = (indice_grupo + 1) % len(GRUPOS_ROTACAO)
    conexao = None
    try:
        conexao = conectar_banco()
        with conexao.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO historico_diversidade
                    (tipo_produto, marca, nome, grupo_rotacao, termo_busca, enviado_em)
                VALUES (%s, %s, %s, %s, %s, NOW())
                """,
                (tipo, marca, nome[:500], grupo, termo),
            )
            cursor.execute(
                """
                INSERT INTO estado_rotacao (id, proximo_grupo, updated_at)
                VALUES (1, %s, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    proximo_grupo = EXCLUDED.proximo_grupo,
                    updated_at = NOW()
                """,
                (proximo,),
            )
            cursor.execute(
                """
                DELETE FROM historico_diversidade
                WHERE id NOT IN (
                    SELECT id FROM historico_diversidade
                    ORDER BY enviado_em DESC, id DESC
                    LIMIT 100
                )
                """
            )
        conexao.commit()
        print(f"ROTACAO: registrado grupo={grupo} tipo={tipo} marca={marca or 'sem_marca'}; proximo={GRUPOS_ROTACAO[proximo][0]}")
        return True
    except Exception as erro:
        print(f"DIVERSIDADE: erro ao registrar: {type(erro).__name__}")
        return False
    finally:
        if conexao:
            conexao.close()


def encontrar_mais_vendido(headers):
    """Busca uma oferta respeitando rotacao rigida de grupos e variedade interna."""
    inicio = time.monotonic()
    limite_segundos = 12
    historico = historico_diversidade_recente(30)
    inicio_grupo = obter_proximo_grupo()

    # Tenta primeiro o grupo obrigatorio. Se a API nao devolver nada utilizavel,
    # pula para o grupo seguinte para nao deixar o cron sem oferta.
    for deslocamento in range(min(3, len(GRUPOS_ROTACAO))):
        if time.monotonic() - inicio >= limite_segundos:
            return None

        indice_grupo = (inicio_grupo + deslocamento) % len(GRUPOS_ROTACAO)
        grupo, termos = GRUPOS_ROTACAO[indice_grupo]

        # Evita o termo usado mais recentemente nesse mesmo grupo.
        termos_recentes = [
            str(linha[4] or "") for linha in historico
            if str(linha[3] or "") == grupo
        ]
        termos_ordenados = [t for t in termos if t not in termos_recentes[:4]]
        termos_ordenados += [t for t in termos if t not in termos_ordenados]

        # Testa ate 3 tipos diferentes dentro do grupo atual.
        termos_busca = termos_ordenados[:3]
        print(f"ROTACAO GRUPO={grupo} TERMOS={termos_busca}")

        for termo in termos_busca:
            if time.monotonic() - inicio >= limite_segundos:
                return None

            category_id = descobrir_categoria(termo, headers)
            if not category_id:
                continue

            ranking = consultar_ranking(category_id, headers)
            print(f"RANKING {grupo}/{termo} {category_id}: {len(ranking)} resultados")

            candidatos = []
            for posicao, entrada in enumerate(ranking[:10], start=1):
                tipo_ranking = str(entrada.get("type") or "").upper()
                identificador = entrada.get("id")
                if identificador and tipo_ranking in {"ITEM", "PRODUCT"}:
                    candidatos.append((tipo_ranking, identificador, posicao))

            if not candidatos:
                continue

            def testar(candidato):
                tipo_ranking, identificador, posicao = candidato
                if time.monotonic() - inicio >= limite_segundos:
                    return None
                try:
                    if tipo_ranking == "ITEM":
                        oferta = consultar_item(identificador, headers)
                    else:
                        oferta = converter_product(identificador, headers)
                except Exception as erro:
                    print(f"ERRO candidato {tipo_ranking} {identificador}: {erro}")
                    return None
                if not oferta:
                    return None
                oferta["posicao"] = posicao
                oferta["categoria_busca"] = termo
                oferta["grupo_rotacao"] = grupo
                oferta["indice_grupo_rotacao"] = indice_grupo
                oferta["tipo_ranking"] = tipo_ranking
                oferta["desconto"] = calcular_desconto(
                    oferta.get("preco_numero"), oferta.get("preco_original")
                )
                return oferta

            with ThreadPoolExecutor(max_workers=4) as executor:
                futuros = [executor.submit(testar, c) for c in candidatos[:8]]
                try:
                    for futuro in as_completed(futuros, timeout=6):
                        if time.monotonic() - inicio >= limite_segundos:
                            break
                        oferta = futuro.result()
                        if not oferta:
                            continue
                        if not oferta_compativel_com_grupo(oferta):
                            continue
                        if oferta_repetitiva(oferta, historico):
                            continue
                        for f in futuros:
                            f.cancel()
                        return oferta
                except TimeoutError:
                    print(f"LIMITE atingido em {grupo}/{termo}")
                for f in futuros:
                    f.cancel()

    return None


@app.route("/status")
def status():
    return {
        "mercado_livre_conectado": bool(ML_ACCESS_TOKEN or ML_REFRESH_TOKEN),
        "banco_configurado": bool(DATABASE_URL),
        "client_id_configurado": bool(CLIENT_ID),
        "client_secret_configurado": bool(CLIENT_SECRET),
        "telegram_configurado": bool(TELEGRAM_BOT_TOKEN),
        "telegram_chat_configurado": bool(TELEGRAM_CHAT_ID),
        "redirect_uri": REDIRECT_URI,
    }



@app.route("/ping")
def ping():
    return "OK", 200


@app.route("/cron/buscar-oferta")
def cron_buscar_oferta():
    if not CRON_SECRET:
        return "CRON_SECRET nao configurado.", 503

    autorizacao = request.headers.get("Authorization", "")
    esperado = f"Bearer {CRON_SECRET}"

    if autorizacao != esperado:
        return "Nao autorizado.", 401

    if not garantir_token_ml():
        return "ML_AUTH_REQUIRED", 503

    # A funcao envia a oferta ao Telegram. O cron recebe somente
    # uma resposta curta para evitar "saida muito grande".
    buscar_mais_vendidos()
    return "OK", 200


@app.route("/buscar-mais-vendidos")
def buscar_mais_vendidos():
    if not garantir_token_ml():
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

    # So entra no historico de diversidade depois que realmente chegou no Telegram.
    registrar_diversidade(oferta)

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
