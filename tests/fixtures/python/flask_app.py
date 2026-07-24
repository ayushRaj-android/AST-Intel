from flask import Flask

app = Flask(__name__)


@app.route("/health")
def health():
    return "OK"


@app.route("/api/users", methods=["GET"])
def list_users():
    return "[]"


@app.route("/api/users", methods=["GET", "POST"])
def users():
    return "users"


@app.route("/api/items/<item_id>", methods=["PUT"])
def update_item(item_id):
    return "updated"


def internal_helper():
    """No route here."""
    pass
