import sqlite3
from datetime import date, datetime

from flask import Flask, abort, flash, redirect, render_template, request, url_for

app = Flask(__name__)
app.secret_key = "e1e2e5506c26ba16341053d136334801fbc6ba1afe8d4add63c1c3a76c513870"

DB = "gastos.db"
CATEGORIAS = [
    "Alimentação", "Transporte", "Moradia", "Saúde",
    "Lazer", "Educação", "Salário", "Outros",
]


def conectar():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def criar_banco():
    with conectar() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lancamentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tipo TEXT NOT NULL,
                valor REAL NOT NULL,
                categoria TEXT NOT NULL,
                data TEXT NOT NULL,
                descricao TEXT
            )
            """
        )
    conn.close()


@app.template_filter("brl")
def brl(valor):
    texto = f"{valor:,.2f}"
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


@app.template_filter("data_br")
def data_br(texto):
    ano, mes, dia = texto.split("-")
    return f"{dia}/{mes}/{ano}"


def validar(form):
    erros = []
    tipo = form.get("tipo", "")
    categoria = form.get("categoria", "").strip()
    data = form.get("data", "")
    descricao = form.get("descricao", "").strip()

    try:
        valor = float(form.get("valor", "").replace(",", "."))
    except ValueError:
        valor = None

    if tipo not in ("entrada", "saida"):
        erros.append("Escolha se é entrada ou saída.")
    if valor is None or valor <= 0:
        erros.append("Informe um valor maior que zero.")
    if not categoria:
        erros.append("Informe a categoria.")
    try:
        date.fromisoformat(data)
    except ValueError:
        erros.append("Informe uma data válida.")

    dados = {
        "tipo": tipo,
        "valor": form.get("valor", ""),
        "categoria": categoria,
        "data": data,
        "descricao": descricao,
    }
    return erros, dados, valor


@app.route("/")
def index():
    mes = request.args.get("mes", "")
    try:
        datetime.strptime(mes, "%Y-%m")
    except ValueError:
        mes = date.today().strftime("%Y-%m")

    with conectar() as conn:
        lancamentos = conn.execute(
            "SELECT * FROM lancamentos WHERE substr(data, 1, 7) = ? "
            "ORDER BY data DESC, id DESC",
            (mes,),
        ).fetchall()
    conn.close()

    entradas = sum(l["valor"] for l in lancamentos if l["tipo"] == "entrada")
    saidas = sum(l["valor"] for l in lancamentos if l["tipo"] == "saida")

    return render_template(
        "index.html",
        lancamentos=lancamentos,
        mes=mes,
        entradas=entradas,
        saidas=saidas,
        saldo=entradas - saidas,
    )


@app.route("/novo", methods=["GET", "POST"])
def novo():
    erros = []
    dados = {
        "tipo": "saida", "valor": "", "categoria": "",
        "data": date.today().isoformat(), "descricao": "",
    }
    if request.method == "POST":
        erros, dados, valor = validar(request.form)
        if not erros:
            with conectar() as conn:
                conn.execute(
                    "INSERT INTO lancamentos (tipo, valor, categoria, data, descricao) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (dados["tipo"], valor, dados["categoria"], dados["data"], dados["descricao"]),
                )
            conn.close()
            flash("Lançamento salvo.")
            return redirect(url_for("index", mes=dados["data"][:7]))
    return render_template(
        "form.html", titulo="Novo lançamento", dados=dados,
        erros=erros, categorias=CATEGORIAS,
    )


@app.route("/editar/<int:id>", methods=["GET", "POST"])
def editar(id):
    with conectar() as conn:
        lanc = conn.execute("SELECT * FROM lancamentos WHERE id = ?", (id,)).fetchone()
    conn.close()
    if lanc is None:
        abort(404)

    erros = []
    dados = dict(lanc)
    if request.method == "POST":
        erros, dados, valor = validar(request.form)
        if not erros:
            with conectar() as conn:
                conn.execute(
                    "UPDATE lancamentos SET tipo = ?, valor = ?, categoria = ?, "
                    "data = ?, descricao = ? WHERE id = ?",
                    (dados["tipo"], valor, dados["categoria"], dados["data"], dados["descricao"], id),
                )
            conn.close()
            flash("Lançamento atualizado.")
            return redirect(url_for("index", mes=dados["data"][:7]))
    return render_template(
        "form.html", titulo="Editar lançamento", dados=dados,
        erros=erros, categorias=CATEGORIAS,
    )


@app.route("/excluir/<int:id>", methods=["POST"])
def excluir(id):
    with conectar() as conn:
        conn.execute("DELETE FROM lancamentos WHERE id = ?", (id,))
    conn.close()
    flash("Lançamento excluído.")
    return redirect(request.referrer or url_for("index"))


criar_banco()

if __name__ == "__main__":
    app.run(debug=True)
