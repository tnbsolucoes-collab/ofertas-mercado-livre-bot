from flask import Flask, request
import os

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot Ofertas Mercado Livre BR - Online!"

@app.route("/oauth/callback")
def oauth_callback():
    code = request.args.get("code")

    if not code:
        return "Nenhum codigo de autorizacao recebido."

    return "Autorizacao recebida com sucesso! Podemos continuar."

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
