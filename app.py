from flask import Flask, request, redirect
import os
import json
import time
from urllib.parse import urlencode
import requests

app = Flask(__name__)

# =========================
# CONFIGURAÇÕES
# =========================
ML_CLIENT_ID = os.getenv("ML_CLIENT_ID", "").strip()
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "").strip()
ML_ACCESS_TOKEN = os.getenv("ML_ACCESS_TOKEN", "").strip() or None
ML_REFRESH_TOKEN = os.getenv("ML_REFRESH_TOKEN", "").strip() or None

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
# Chat privado onde você aprova/descarta as ofertas
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
# Canal público onde o produto aprovado será publicado
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()

BASE_URL = "https://ofertas-mercado-livre-bot.onrender.com"
REDIRECT_URI = f"{BASE_URL}/oauth/callback"
WEBHOOK_URL = f"{BASE_URL}/telegram/webhook"
TOKEN_FILE = "ml_tokens.json"

TOKEN_EXPIRES_AT = 0
OFERTAS = {}
AGUARDANDO_LINK = {}

TERMOS = [
    "smartphone", "notebook", "fone bluetooth", "smart tv",
    "mochila", "creatina", "monitor", "teclado", "mouse",
    "air fryer", "perfume", "tenis", "caixa de som"
]


def fmt_preco(valor):
    if valor is None:
        return "Consulte"
    try:
        valor = float(valor)
    except (TypeError, ValueError):
        return str(valor)
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def desconto(preco, original):
    try:
        preco = float(preco)
        original = float(original)
        if original <= 0 or original <= preco:
            return None
        return round((original - preco) / original * 100)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def tg(method, payload):
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN não configurado")
    return requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}",
        json=payload,
        timeout=30,
    )


# =========================
# TOKEN MERCADO LIVRE
# =========================

def save_tokens(data):
    global ML_ACCESS_TOKEN, ML_REFRESH_TOKEN, TOKEN_EXPIRES_AT
    ML_ACCESS_TOKEN = data.get("access_token") or ML_ACCESS_TOKEN
    ML_REFRESH_TOKEN = data.get("refresh_token") or ML_REFRESH_TOKEN
    expires = int(data.get("expires_in") or 21600)
    TOKEN_EXPIRES_AT = int(time.time()) + expires - 60
    try:
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "access_token": ML_ACCESS_TOKEN,
                "refresh_token": ML_REFRESH_TOKEN,
                "expires_at": TOKEN_EXPIRES_AT,
            }, f)
    except Exception as e:
        print("AVISO: não salvou tokens no arquivo:", e)


def load_tokens():
    global ML_ACCESS_TOKEN, ML_REFRESH_TOKEN, TOKEN_EXPIRES_AT
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not ML_ACCESS_TOKEN:
            ML_ACCESS_TOKEN = data.get("access_token")
        if not ML_REFRESH_TOKEN:
            ML_REFRESH_TOKEN = data.get("refresh_token")
        TOKEN_EXPIRES_AT = int(data.get("expires_at") or 0)
    except Exception:
        pass


def refresh_token():
    global ML_ACCESS_TOKEN, ML_REFRESH_TOKEN
    if not ML_REFRESH_TOKEN or not ML_CLIENT_ID or not ML_CLIENT_SECRET:
        return False
    try:
        r = requests.post(
            "https://api.mercadolibre.com/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": ML_CLIENT_ID,
                "client_secret": ML_CLIENT_SECRET,
                "refresh_token": ML_REFRESH_TOKEN,
            },
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
    except requests.RequestException as e:
        print("ERRO refresh:", e)
        return False
    print("REFRESH STATUS:", r.status_code)
    if r.status_code != 200:
        print("REFRESH ERRO:", r.text)
        return False
    try:
        save_tokens(r.json())
        return bool(ML_ACCESS_TOKEN)
    except Exception:
        return False


def get_token():
    load_tokens()
    if ML_ACCESS_TOKEN and (not TOKEN_EXPIRES_AT or time.time() < TOKEN_EXPIRES_AT):
        return ML_ACCESS_TOKEN
    if refresh_token():
        return ML_ACCESS_TOKEN
    return None


def ml_headers():
    token = get_token()
    if not token:
        return None
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


# =========================
# PÁGINA / OAUTH
# =========================

@app.route("/")
def home():
    return """
    <h2>🔥 Bot Ofertas Mercado Livre BR</h2>
    <p><a href="/login">1 - Conectar Mercado Livre</a></p>
    <p><a href="/buscar-mais-vendidos">2 - Buscar oferta</a></p>
    <p><a href="/configurar-webhook">3 - Configurar webhook Telegram</a></p>
    <p><a href="/status">4 - Status</a></p>
    """


@app.route("/status")
def status():
    return {
        "mercado_livre": bool(get_token()),
        "telegram": bool(TELEGRAM_BOT_TOKEN),
        "chat_aprovacao": bool(TELEGRAM_CHAT_ID),
        "canal": bool(TELEGRAM_CHANNEL_ID),
        "redirect_uri": REDIRECT_URI,
    }


@app.route("/login")
def login():
    if not ML_CLIENT_ID:
        return "ML_CLIENT_ID não configurado no Render.", 500
    url = "https://auth.mercadolivre.com.br/authorization?" + urlencode({
        "response_type": "code",
        "client_id": ML_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
    })
    return redirect(url)


@app.route("/oauth/callback")
def oauth_callback():
    code = request.args.get("code")
    error = request.args.get("error")
    error_description = request.args.get("error_description", "")

    if error:
        return f"<h2>❌ Mercado Livre recusou</h2><p>{error}</p><p>{error_description}</p>", 400

    if not code:
        return "Nenhum code recebido do Mercado Livre.", 400

    if not ML_CLIENT_ID or not ML_CLIENT_SECRET:
        return "ML_CLIENT_ID ou ML_CLIENT_SECRET não configurado.", 500

    try:
        r = requests.post(
            "https://api.mercadolibre.com/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": ML_CLIENT_ID,
                "client_secret": ML_CLIENT_SECRET,
                "code": code,
                "redirect_uri": REDIRECT_URI,
            },
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
    except requests.RequestException as e:
        return f"Erro de conexão: {e}", 500

    print("OAUTH STATUS:", r.status_code)
    print("OAUTH RESPOSTA:", r.text)

    if r.status_code != 200:
        return f"<h2>❌ Erro ao obter token</h2><pre>{r.text}</pre>", r.status_code

    try:
        data = r.json()
    except ValueError:
        return "Resposta inválida do Mercado Livre.", 500

    save_tokens(data)
    return "<h2>🔥 Mercado Livre conectado!</h2><p><a href='/buscar-mais-vendidos'>Buscar oferta agora</a></p>"


# =========================
# TELEGRAM WEBHOOK
# =========================

@app.route("/configurar-webhook")
def configurar_webhook():
    if not TELEGRAM_BOT_TOKEN:
        return "TELEGRAM_BOT_TOKEN não configurado.", 500
    try:
        r = tg("setWebhook", {
            "url": WEBHOOK_URL,
            "allowed_updates": ["callback_query", "message"],
        })
        data = r.json()
    except Exception as e:
        return f"Erro: {e}", 500
    return f"<pre>{json.dumps(data, ensure_ascii=False, indent=2)}</pre>"


# =========================
# MERCADO LIVRE - PREÇO/ITEM
# =========================

def get_prices(item_id, headers):
    """Usa /items/{id}/prices para obter o preço promocional atual."""
    try:
        r = requests.get(
            f"https://api.mercadolibre.com/items/{item_id}/prices",
            headers=headers,
            timeout=30,
        )
    except requests.RequestException as e:
        print("ERRO /prices:", e)
        return None, None

    if r.status_code != 200:
        print("ERRO /prices", item_id, r.status_code, r.text[:500])
        return None, None

    try:
        prices = (r.json() or {}).get("prices") or []
    except ValueError:
        return None, None

    promotions = []
    standards = []

    for p in prices:
        try:
            amount = float(p.get("amount"))
        except (TypeError, ValueError):
            continue
        regular = p.get("regular_amount")
        try:
            regular = float(regular) if regular is not None else None
        except (TypeError, ValueError):
            regular = None
        kind = str(p.get("type", "")).lower()

        if kind == "promotion" and regular is not None and regular > amount:
            promotions.append((amount, regular))
        elif kind == "standard":
            standards.append((amount, None))

    # Se existir promoção válida, ela vence.
    if promotions:
        promotions.sort(key=lambda x: x[0])
        return promotions[0]
    if standards:
        standards.sort(key=lambda x: x[0])
        return standards[0]
    return None, None


def get_item(item_id, headers):
    try:
        r = requests.get(
            f"https://api.mercadolibre.com/items/{item_id}",
            headers=headers,
            timeout=30,
        )
    except requests.RequestException as e:
        print("ERRO item:", e)
        return None

    if r.status_code != 200:
        print("ERRO item", item_id, r.status_code, r.text[:500])
        return None

    try:
        item = r.json()
    except ValueError:
        return None

    if item.get("status") != "active":
        return None

    title = item.get("title") or "Produto"
    permalink = item.get("permalink")
    if not permalink:
        return None

    image = item.get("secure_thumbnail") or item.get("thumbnail")
    pictures = item.get("pictures") or []
    if pictures:
        image = pictures[0].get("secure_url") or pictures[0].get("url") or image

    price = item.get("price")
    original = item.get("original_price")

    # Corrige preço promocional usando o endpoint oficial /prices.
    current, regular = get_prices(str(item_id), headers)
    if current is not None:
        price = current
    if regular is not None:
        original = regular

    return {
        "item_id": str(item_id),
        "nome": title,
        "preco_numero": price,
        "preco": fmt_preco(price),
        "preco_original": original,
        "desconto": desconto(price, original),
        "imagem": image,
        "link_normal": permalink,
    }


# =========================
# BUSCA DE OFERTAS
# =========================

def buscar_itens(termo, headers):
    """Fallback robusto: usa busca de itens caso o endpoint de highlights dê 403."""
    try:
        r = requests.get(
            "https://api.mercadolibre.com/sites/MLB/search",
            params={"q": termo, "limit": 20},
            headers=headers,
            timeout=30,
        )
    except requests.RequestException as e:
        print("ERRO busca:", e)
        return []

    if r.status_code != 200:
        print("ERRO busca", termo, r.status_code, r.text[:500])
        return []

    try:
        return (r.json() or {}).get("results") or []
    except ValueError:
        return []


def encontrar_oferta(headers):
    candidatos = []

    for termo in TERMOS:
        resultados = buscar_itens(termo, headers)
        for result in resultados:
            item_id = result.get("id")
            if not item_id:
                continue
            oferta = get_item(item_id, headers)
            if not oferta:
                continue
            candidatos.append(oferta)

        # Prioriza logo que encontra desconto real.
        com_desconto = [x for x in candidatos if x.get("desconto")]
        if com_desconto:
            com_desconto.sort(key=lambda x: x.get("desconto") or 0, reverse=True)
            return com_desconto[0]

    if candidatos:
        return candidatos[0]
    return None


@app.route("/buscar-mais-vendidos")
def buscar_mais_vendidos():
    headers = ml_headers()
    if not headers:
        return "<h3>Conecte o Mercado Livre primeiro.</h3><p><a href='/login'>Conectar</a></p>", 401

    oferta = encontrar_oferta(headers)
    if not oferta:
        return "<h3>Não encontrei ofertas agora.</h3><p>Veja os logs do Render para o erro da API.</p>", 404

    item_id = oferta["item_id"]
    OFERTAS[item_id] = oferta

    legenda = f"🔥 OFERTA ENCONTRADA!\n\n📦 {oferta['nome']}\n\n"
    if oferta.get("desconto") and oferta.get("preco_original"):
        legenda += (
            f"🔥 Por: {oferta['preco']}\n"
            f"💵 De: {fmt_preco(oferta['preco_original'])}\n"
            f"📉 {oferta['desconto']}% OFF\n\n"
        )
    else:
        legenda += f"💰 Preço: {oferta['preco']}\n\n"
    legenda += f"🔗 Link normal:\n{oferta['link_normal']}\n\n⚠️ Confira o produto antes de publicar."

    buttons = {"inline_keyboard": [[
        {"text": "✅ APROVAR", "callback_data": f"publicar:{item_id}"},
        {"text": "❌ DESCARTAR", "callback_data": f"ignorar:{item_id}"},
    ]]}

    payload = {"chat_id": TELEGRAM_CHAT_ID, "reply_markup": buttons}
    if oferta.get("imagem"):
        payload["photo"] = oferta["imagem"]
        payload["caption"] = legenda
        r = tg("sendPhoto", payload)
    else:
        payload["text"] = legenda
        r = tg("sendMessage", payload)

    if r.status_code != 200:
        return f"Erro Telegram {r.status_code}: {r.text}", 500

    return "<h3>🔥 Busca concluída!</h3><p>Oferta enviada para aprovação no Telegram.</p>"


# =========================
# TELEGRAM: APROVAR / DESCARTAR / LINK DE AFILIADO
# =========================

@app.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    update = request.get_json(silent=True) or {}

    callback = update.get("callback_query")
    if callback:
        callback_id = callback.get("id")
        data = callback.get("data", "")
        message = callback.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")

        if str(chat_id) != str(TELEGRAM_CHAT_ID):
            tg("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Acesso não autorizado."})
            return "OK", 200

        if data.startswith("ignorar:"):
            item_id = data.split(":", 1)[1]
            OFERTAS.pop(item_id, None)
            AGUARDANDO_LINK.pop(str(chat_id), None)
            tg("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Oferta descartada ❌"})
            return "OK", 200

        if data.startswith("publicar:"):
            item_id = data.split(":", 1)[1]
            oferta = OFERTAS.get(item_id)
            if not oferta:
                tg("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Oferta expirou."})
                return "OK", 200

            AGUARDANDO_LINK[str(chat_id)] = item_id
            tg("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Oferta aprovada! ✅"})
            tg("sendMessage", {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": (
                    "💰 OFERTA APROVADA!\n\n"
                    "🔗 COPIE ESTE LINK:\n\n"
                    f"{oferta['link_normal']}\n\n"
                    "Gere o seu link de afiliado do Mercado Livre e mande o link aqui no bot. 👇"
                ),
            })
            return "OK", 200

    message = update.get("message")
    if message:
        chat_id = (message.get("chat") or {}).get("id")
        text = (message.get("text") or "").strip()

        if str(chat_id) != str(TELEGRAM_CHAT_ID):
            return "OK", 200

        item_id = AGUARDANDO_LINK.get(str(chat_id))
        if not item_id:
            return "OK", 200

        if not text.startswith(("https://", "http://")):
            tg("sendMessage", {"chat_id": TELEGRAM_CHAT_ID, "text": "⚠️ Cole o link completo gerado pelo Mercado Livre."})
            return "OK", 200

        oferta = OFERTAS.get(item_id)
        if not oferta:
            AGUARDANDO_LINK.pop(str(chat_id), None)
            tg("sendMessage", {"chat_id": TELEGRAM_CHAT_ID, "text": "⚠️ Essa oferta expirou. Busque outra."})
            return "OK", 200

        oferta["link_afiliado"] = text
        AGUARDANDO_LINK.pop(str(chat_id), None)

        if not TELEGRAM_CHANNEL_ID:
            tg("sendMessage", {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": "✅ Link recebido! Configure TELEGRAM_CHANNEL_ID no Render para eu publicar automaticamente no canal.",
            })
            return "OK", 200

        legenda = f"🔥 OFERTA ENCONTRADA!\n\n📦 {oferta['nome']}\n\n"
        if oferta.get("desconto") and oferta.get("preco_original"):
            legenda += (
                f"🔥 Por: {oferta['preco']}\n"
                f"💵 De: {fmt_preco(oferta['preco_original'])}\n"
                f"📉 {oferta['desconto']}% OFF\n\n"
            )
        else:
            legenda += f"💰 Preço: {oferta['preco']}\n\n"
        legenda += f"🛒 COMPRE AQUI:\n{oferta['link_afiliado']}\n\n⚠️ Confira preço e disponibilidade antes da compra."

        if oferta.get("imagem"):
            pub = tg("sendPhoto", {"chat_id": TELEGRAM_CHANNEL_ID, "photo": oferta["imagem"], "caption": legenda})
        else:
            pub = tg("sendMessage", {"chat_id": TELEGRAM_CHANNEL_ID, "text": legenda})

        if pub.status_code != 200:
            print("ERRO PUBLICAÇÃO:", pub.status_code, pub.text)
            tg("sendMessage", {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": f"❌ Não consegui publicar no canal. HTTP {pub.status_code}\n{pub.text[:1000]}",
            })
            return "OK", 200

        tg("sendMessage", {"chat_id": TELEGRAM_CHAT_ID, "text": "🚀 PUBLICADO NO CANAL! ✅"})
        OFERTAS.pop(item_id, None)
        return "OK", 200

    return "OK", 200


load_tokens()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
