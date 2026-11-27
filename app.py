import json
import os
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from functools import wraps
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

from extrato_parser import inferir_banco, inferir_categoria, ler_extrato

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _carregar_env(caminho=os.path.join(BASE_DIR, ".env")):
    """Carrega variáveis de um arquivo .env sem sobrescrever variáveis já definidas."""
    if not os.path.isfile(caminho):
        return
    with open(caminho, encoding="utf-8") as arquivo:
        for linha in arquivo:
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, valor = linha.split("=", 1)
            chave = chave.strip()
            valor = valor.strip()
            if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in ('"', "'"):
                valor = valor[1:-1]
            if chave:
                os.environ.setdefault(chave, valor)


def _resolver_caminho_banco():
    caminho = os.environ.get("DATABASE_PATH", "gastos.db").strip() or "gastos.db"
    if os.path.isabs(caminho):
        return caminho
    return os.path.join(BASE_DIR, caminho)


def _ler_int_env(nome, padrao):
    try:
        valor = int(os.environ.get(nome, padrao))
        return valor if valor > 0 else padrao
    except (TypeError, ValueError):
        return padrao


_carregar_env()

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config["MAX_CONTENT_LENGTH"] = _ler_int_env("MAX_CONTENT_LENGTH_MB", 10) * 1024 * 1024

DB = _resolver_caminho_banco()

MESES_PT = [
    "", "Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
    "Jul", "Ago", "Set", "Out", "Nov", "Dez",
]

BANCOS_BRASILEIROS = [
    "Nubank", "Itaú Unibanco", "Bradesco", "Banco do Brasil", "Santander",
    "Caixa Econômica Federal", "Banco Inter", "C6 Bank", "XP Investimentos",
    "BTG Pactual", "Mercado Pago", "PicPay", "PagBank", "Sicredi", "Sicoob",
    "Banrisul", "Neon", "Safra", "Banco Safra", "Votorantim", "Daycoval",
    "Pan", "Bmg", "Original", "Avenue", "Nomad", "Wise", "Binance Pay",
    "Outro",
]


# ------------------------------------------------------------------ banco ----
@contextmanager
def conectar():
    """Abre conexão SQLite, garante commit/rollback e fecha ao sair."""
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _garantir_coluna(conn, tabela, coluna, definicao):
    cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({tabela})").fetchall()}
    if coluna not in cols:
        conn.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {definicao}")


def _reclassificar_legado_extrato(conn):
    """Atualiza registros antigos salvos genericamente como 'Extrato'."""
    for tabela in ("lancamentos", "cartao_gastos"):
        rows = conn.execute(
            f"SELECT id, descricao FROM {tabela} WHERE categoria = 'Extrato'"
        ).fetchall()
        for r in rows:
            nova_cat = inferir_categoria(r["descricao"])
            conn.execute(f"UPDATE {tabela} SET categoria = ? WHERE id = ?", (nova_cat, r["id"]))


def _migrar_faturas_pagas(conn):
    """Garante que faturas_pagas seja isolada por user_id com UNIQUE(user_id, banco, mes)."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(faturas_pagas)").fetchall()}
    if "user_id" in cols:
        return
    conn.execute("ALTER TABLE faturas_pagas RENAME TO faturas_pagas_legado")
    conn.execute(
        """
        CREATE TABLE faturas_pagas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL DEFAULT 0,
            banco TEXT NOT NULL DEFAULT '',
            mes TEXT NOT NULL,
            pago_em TEXT NOT NULL,
            UNIQUE(user_id, banco, mes)
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO faturas_pagas (user_id, banco, mes, pago_em)
        SELECT 0, banco, mes, pago_em FROM faturas_pagas_legado
        """
    )
    conn.execute("DROP TABLE faturas_pagas_legado")


def _semear_admin_inicial(conn):
    """Garante a existência de ao menos um administrador usando as credenciais do .env."""
    qtd_admin = conn.execute("SELECT COUNT(*) FROM usuarios WHERE role = 'admin'").fetchone()[0]
    if qtd_admin > 0:
        return
    email = (os.environ.get("ADMIN_EMAIL", "admin@controledegastos.local") or "").strip().lower()
    nome = (os.environ.get("ADMIN_NOME", "Administrador") or "").strip() or "Administrador"
    senha = (os.environ.get("ADMIN_PASSWORD", "") or "").strip()
    if not email:
        return
    agora = datetime.now().strftime("%Y-%m-%d %H:%M")
    existente = conn.execute("SELECT id FROM usuarios WHERE LOWER(email) = ?", (email,)).fetchone()
    senha_hash = generate_password_hash(senha) if senha else None
    if existente:
        conn.execute(
            "UPDATE usuarios SET role = 'admin', ativo = 1, senha_hash = COALESCE(?, senha_hash) WHERE id = ?",
            (senha_hash, existente["id"]),
        )
    else:
        conn.execute(
            """
            INSERT INTO usuarios (nome, email, senha_hash, role, provedor, ativo, criado_em)
            VALUES (?, ?, ?, 'admin', 'local', 1, ?)
            """,
            (nome, email, senha_hash, agora),
        )


def criar_banco():
    """Garante que todas as tabelas e colunas do aplicativo existam."""
    with conectar() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                google_sub TEXT UNIQUE,
                avatar_url TEXT,
                senha_hash TEXT,
                role TEXT NOT NULL DEFAULT 'user',
                provedor TEXT NOT NULL DEFAULT 'google',
                ativo INTEGER NOT NULL DEFAULT 1,
                criado_em TEXT NOT NULL,
                ultimo_login TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lancamentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                tipo TEXT NOT NULL,
                valor REAL NOT NULL,
                categoria TEXT NOT NULL,
                data TEXT NOT NULL,
                descricao TEXT,
                lote_importacao TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cartao_gastos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                descricao TEXT NOT NULL,
                categoria TEXT NOT NULL,
                valor_total REAL NOT NULL,
                parcelas INTEGER NOT NULL DEFAULT 1,
                parcelas_pagas INTEGER NOT NULL DEFAULT 0,
                data_compra TEXT NOT NULL,
                lote_importacao TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS metas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                nome TEXT NOT NULL,
                valor_alvo REAL NOT NULL,
                valor_atual REAL NOT NULL DEFAULT 0,
                prazo TEXT NOT NULL,
                criado_em TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meta_aportes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meta_id INTEGER NOT NULL,
                valor REAL NOT NULL,
                data TEXT NOT NULL,
                observacao TEXT,
                FOREIGN KEY (meta_id) REFERENCES metas(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS importacoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                lote TEXT NOT NULL UNIQUE,
                arquivo TEXT NOT NULL,
                destino TEXT NOT NULL,
                quantidade INTEGER NOT NULL,
                ignorados INTEGER NOT NULL DEFAULT 0,
                criado_em TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS faturas_pagas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL DEFAULT 0,
                banco TEXT NOT NULL DEFAULT '',
                mes TEXT NOT NULL,
                pago_em TEXT NOT NULL,
                UNIQUE(user_id, banco, mes)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bancos_brasileiros (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL UNIQUE
            )
            """
        )
        # Migrações de colunas novas
        _garantir_coluna(conn, "lancamentos", "user_id", "INTEGER")
        _garantir_coluna(conn, "lancamentos", "lote_importacao", "TEXT")
        _garantir_coluna(conn, "lancamentos", "banco", "TEXT")
        _garantir_coluna(conn, "lancamentos", "pago", "INTEGER NOT NULL DEFAULT 0")
        _garantir_coluna(conn, "cartao_gastos", "user_id", "INTEGER")
        _garantir_coluna(conn, "cartao_gastos", "lote_importacao", "TEXT")
        _garantir_coluna(conn, "cartao_gastos", "banco", "TEXT")
        _garantir_coluna(conn, "metas", "user_id", "INTEGER")
        _garantir_coluna(conn, "importacoes", "user_id", "INTEGER")
        _garantir_coluna(conn, "importacoes", "banco", "TEXT")
        _migrar_faturas_pagas(conn)

        # Semear bancos brasileiros se ainda vazio
        qtd = conn.execute("SELECT COUNT(*) FROM bancos_brasileiros").fetchone()[0]
        if qtd == 0:
            for banco in BANCOS_BRASILEIROS:
                conn.execute(
                    "INSERT OR IGNORE INTO bancos_brasileiros (nome) VALUES (?)", (banco,)
                )

        _semear_admin_inicial(conn)
        _reclassificar_legado_extrato(conn)


# ------------------------------------------------------- filtros e datas ----
@app.template_filter("brl")
def brl(valor):
    """Formata número float como moeda brasileira (R$ 1.234,56)."""
    valor = float(valor or 0)
    sinal = "-" if valor < 0 else ""
    texto = f"{abs(valor):,.2f}"
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{sinal}R$ {texto}"


@app.template_filter("data_br")
def data_br(texto):
    """Converte YYYY-MM-DD para DD/MM/YYYY."""
    ano, mes, dia = texto.split("-")
    return f"{dia}/{mes}/{ano}"


@app.template_filter("mes_br")
def mes_br(ano_mes):
    """Converte YYYY-MM para formato curto (Nov/2026)."""
    ano, mes = ano_mes.split("-")
    return f"{MESES_PT[int(mes)]}/{ano}"


def normalizar_mes(mes_str):
    """Valida YYYY-MM ou retorna o mês atual."""
    try:
        datetime.strptime(mes_str, "%Y-%m")
        return mes_str
    except (TypeError, ValueError):
        return date.today().strftime("%Y-%m")


def adicionar_meses(ano_mes, delta):
    """Soma/subtrai `delta` meses a uma string YYYY-MM."""
    ano, mes = map(int, ano_mes.split("-"))
    total = ano * 12 + (mes - 1) + delta
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def diferenca_meses(inicio, fim):
    """Retorna diferença em meses inteiros entre duas strings YYYY-MM."""
    a1, m1 = map(int, inicio.split("-"))
    a2, m2 = map(int, fim.split("-"))
    return (a2 * 12 + m2) - (a1 * 12 + m1)


def parse_valor(texto):
    """Converte texto monetário (com vírgula ou ponto) para float."""
    limpo = (texto or "").strip().replace("R$", "").replace(" ", "")
    if "," in limpo and "." in limpo:
        limpo = limpo.replace(".", "").replace(",", ".")
    else:
        limpo = limpo.replace(",", ".")
    try:
        return float(limpo)
    except ValueError:
        return None


# ------------------------------------------------------------ validações ----
def validar_lancamento(form):
    erros = []
    tipo = form.get("tipo", "")
    categoria = form.get("categoria", "").strip() or "Geral"
    data_str = form.get("data", "")
    descricao = form.get("descricao", "").strip()
    valor = parse_valor(form.get("valor", ""))

    if tipo not in ("entrada", "saida"):
        erros.append("Escolha se é entrada ou saída.")
    if valor is None or valor <= 0:
        erros.append("Informe um valor maior que zero.")
    try:
        date.fromisoformat(data_str)
    except ValueError:
        erros.append("Informe uma data válida.")

    dados = {
        "tipo": tipo,
        "valor": form.get("valor", ""),
        "categoria": form.get("categoria", "").strip(),
        "data": data_str,
        "descricao": descricao,
    }
    return erros, dados, valor, categoria


def validar_cartao(form):
    erros = []
    descricao = form.get("descricao", "").strip()
    categoria = form.get("categoria", "").strip() or "Geral"
    data_compra = form.get("data_compra", "")
    valor_total = parse_valor(form.get("valor_total", ""))

    try:
        parcelas = int(form.get("parcelas", "1"))
    except ValueError:
        parcelas = 0

    try:
        parcelas_pagas = int(form.get("parcelas_pagas", "0") or "0")
    except ValueError:
        parcelas_pagas = -1

    if not descricao:
        erros.append("Informe uma descrição para a compra.")
    if valor_total is None or valor_total <= 0:
        erros.append("Informe um valor total maior que zero.")
    if parcelas < 1 or parcelas > 48:
        erros.append("O número de parcelas deve ficar entre 1 e 48.")
    if parcelas_pagas < 0 or (parcelas >= 1 and parcelas_pagas > parcelas):
        erros.append("Parcelas pagas deve estar entre 0 e o total de parcelas.")
    try:
        date.fromisoformat(data_compra)
    except ValueError:
        erros.append("Informe uma data de compra válida.")

    dados = {
        "descricao": descricao,
        "categoria": form.get("categoria", "").strip(),
        "valor_total": form.get("valor_total", ""),
        "parcelas": form.get("parcelas", "1"),
        "parcelas_pagas": form.get("parcelas_pagas", "0"),
        "data_compra": data_compra,
    }
    return erros, dados, valor_total, parcelas, parcelas_pagas, categoria


def validar_meta(form):
    erros = []
    nome = form.get("nome", "").strip()
    valor_alvo = parse_valor(form.get("valor_alvo", ""))
    valor_atual_raw = (form.get("valor_atual", "") or "0").strip()
    valor_atual = parse_valor(valor_atual_raw)
    prazo = form.get("prazo", "")

    if not nome:
        erros.append("Informe o nome da meta.")
    if valor_alvo is None or valor_alvo <= 0:
        erros.append("Informe um valor da meta maior que zero.")
    if valor_atual is None or valor_atual < 0:
        erros.append("O valor já guardado não pode ser negativo.")
    try:
        date.fromisoformat(prazo)
    except ValueError:
        erros.append("Informe um prazo válido.")

    dados = {
        "nome": nome,
        "valor_alvo": form.get("valor_alvo", ""),
        "valor_atual": valor_atual_raw,
        "prazo": prazo,
    }
    return erros, dados, valor_alvo, valor_atual


# ---------------------------------------------------- regras de negócio ----
def valores_parcelas(valor_total, parcelas):
    """Divide valor_total em `parcelas` garantindo soma exata em centavos."""
    total_centavos = round(float(valor_total) * 100)
    base, resto = divmod(total_centavos, parcelas)
    return [
        round((base + (1 if i < resto else 0)) / 100, 2)
        for i in range(parcelas)
    ]


def enriquecer_compra_cartao(row):
    """Calcula resumo de parcelas pagas, restantes e período de uma compra."""
    item = dict(row)
    parcelas = max(int(item["parcelas"]), 1)
    pagas = min(max(int(item["parcelas_pagas"]), 0), parcelas)
    lista = valores_parcelas(item["valor_total"], parcelas)
    mes_inicio = item["data_compra"][:7]
    mes_fim = adicionar_meses(mes_inicio, parcelas - 1)

    item["parcelas"] = parcelas
    item["parcelas_pagas"] = pagas
    item["parcelas_restantes"] = parcelas - pagas
    item["valor_parcela_media"] = round(item["valor_total"] / parcelas, 2)
    item["valores_parcelas"] = lista
    item["valor_restante"] = round(sum(lista[pagas:]), 2)
    item["valor_pago"] = round(sum(lista[:pagas]), 2)
    item["mes_inicio"] = mes_inicio
    item["mes_fim"] = mes_fim
    item["quitado"] = pagas >= parcelas
    return item


def calcular_dados_cartao(compras, mes, categoria_filtro="", banco_filtro="", faturas_pagas_set=None):
    """Agrega fatura do mês, dívida total aberta e gastos por categoria livre."""
    if faturas_pagas_set is None:
        faturas_pagas_set = set()

    compras_enr = [enriquecer_compra_cartao(c) for c in compras]

    # Filtrar por banco se solicitado
    if banco_filtro:
        compras_enr = [c for c in compras_enr if (c.get("banco") or "") == banco_filtro]

    categorias_disponiveis = sorted(
        {c["categoria"] for c in compras_enr if c["categoria"]}
    )

    fatura_itens = []
    por_categoria = {}

    for c in compras_enr:
        if categoria_filtro and c["categoria"] != categoria_filtro:
            continue
        idx = diferenca_meses(c["mes_inicio"], mes)
        if 0 <= idx < c["parcelas"]:
            num_parcela = idx + 1
            valor_parc = c["valores_parcelas"][idx]
            paga = num_parcela <= c["parcelas_pagas"]
            fatura_itens.append({
                "id": c["id"],
                "descricao": c["descricao"],
                "categoria": c["categoria"],
                "data_compra": c["data_compra"],
                "numero_parcela": num_parcela,
                "total_parcelas": c["parcelas"],
                "parcelas_pagas": c["parcelas_pagas"],
                "valor": valor_parc,
                "paga": paga,
                "banco": c.get("banco") or "",
            })
            por_categoria[c["categoria"]] = round(
                por_categoria.get(c["categoria"], 0.0) + valor_parc, 2
            )

    compras_filtradas = [
        c for c in compras_enr
        if not categoria_filtro or c["categoria"] == categoria_filtro
    ]

    total_fatura = round(sum(i["valor"] for i in fatura_itens), 2)
    total_fatura_aberta = round(sum(i["valor"] for i in fatura_itens if not i["paga"]), 2)
    total_restante_geral = round(sum(c["valor_restante"] for c in compras_filtradas), 2)

    # Verificar se a fatura do mês está marcada como paga globalmente
    fatura_mes_paga = mes in faturas_pagas_set

    return {
        "fatura_itens": fatura_itens,
        "compras": compras_filtradas,
        "categorias_disponiveis": categorias_disponiveis,
        "por_categoria": por_categoria,
        "total_fatura": total_fatura,
        "total_fatura_aberta": total_fatura_aberta,
        "total_restante_geral": total_restante_geral,
        "fatura_mes_paga": fatura_mes_paga,
    }


def enriquecer_meta(row):
    """Calcula progresso, meses restantes e aporte mensal necessário da meta."""
    m = dict(row)
    alvo = float(m["valor_alvo"])
    atual = float(m["valor_atual"])
    restante = max(round(alvo - atual, 2), 0.0)
    progresso = min(round((atual / alvo) * 100, 1), 100.0) if alvo > 0 else 0.0

    hoje = date.today()
    prazo_dt = date.fromisoformat(m["prazo"])
    meses_restantes = diferenca_meses(hoje.strftime("%Y-%m"), prazo_dt.strftime("%Y-%m"))
    meses_calc = max(meses_restantes, 1) if prazo_dt >= hoje else 0

    if restante <= 0:
        status_prazo = "Meta concluída!"
        aporte_mensal = 0.0
    elif prazo_dt < hoje:
        status_prazo = "Prazo vencido"
        aporte_mensal = restante
    elif meses_restantes == 0:
        status_prazo = "Vence este mês"
        aporte_mensal = restante
    elif meses_restantes == 1:
        status_prazo = "Falta 1 mês"
        aporte_mensal = restante
    else:
        status_prazo = f"Faltam {meses_restantes} meses"
        aporte_mensal = round(restante / meses_calc, 2)

    m["restante"] = restante
    m["progresso"] = progresso
    m["concluida"] = restante <= 0
    m["vencida"] = prazo_dt < hoje and restante > 0
    m["status_prazo"] = status_prazo
    m["aporte_mensal"] = aporte_mensal
    return m


def calcular_evolucao_patrimonial(lancamentos, compras_enr, aportes, metas):
    """Monta série histórica mensal de guardado, dívida e patrimônio líquido."""
    hoje_mes = date.today().strftime("%Y-%m")
    meses_candidatos = [adicionar_meses(hoje_mes, -5), hoje_mes]

    for l in lancamentos:
        meses_candidatos.append(l["data"][:7])
    for c in compras_enr:
        meses_candidatos.append(c["mes_inicio"])
    for a in aportes:
        meses_candidatos.append(a["data"][:7])
    for m in metas:
        meses_candidatos.append(m["criado_em"][:7])

    inicio_mes = min(meses_candidatos)
    if inicio_mes > hoje_mes:
        inicio_mes = adicionar_meses(hoje_mes, -5)

    qtd_meses = diferenca_meses(inicio_mes, hoje_mes) + 1
    meses = [adicionar_meses(inicio_mes, i) for i in range(qtd_meses)]

    caixa_por_mes = {}
    for l in lancamentos:
        m = l["data"][:7]
        delta = l["valor"] if l["tipo"] == "entrada" else -l["valor"]
        caixa_por_mes[m] = caixa_por_mes.get(m, 0.0) + delta

    aportes_por_mes = {}
    for a in aportes:
        m = a["data"][:7]
        aportes_por_mes[m] = aportes_por_mes.get(m, 0.0) + a["valor"]

    divida_atual_exata = round(sum(c["valor_restante"] for c in compras_enr), 2)

    serie = []
    caixa_acum = 0.0
    metas_acum = 0.0

    for m in meses:
        caixa_acum += caixa_por_mes.get(m, 0.0)
        metas_acum += aportes_por_mes.get(m, 0.0)
        guardado = round(caixa_acum + metas_acum, 2)

        if m == hoje_mes:
            divida = divida_atual_exata
        else:
            divida = 0.0
            for c in compras_enr:
                if c["mes_inicio"] <= m:
                    idx_no_mes = diferenca_meses(c["mes_inicio"], m)
                    if idx_no_mes + 1 < c["parcelas"]:
                        divida += sum(c["valores_parcelas"][idx_no_mes + 1:])
            divida = round(divida, 2)

        patrimonio = round(guardado - divida, 2)
        serie.append({
            "mes": m,
            "label": mes_br(m),
            "guardado": guardado,
            "divida": divida,
            "patrimonio": patrimonio,
        })

    return serie


def calcular_previsao(lancamentos, compras_raw, mes_atual):
    """Calcula previsão de gastos para o próximo mês baseado no mês anterior."""
    mes_anterior = adicionar_meses(mes_atual, -1)

    # Gastos do mês atual
    lanc_mes = [l for l in lancamentos if l["data"][:7] == mes_atual]
    saidas_mes_atual = sum(l["valor"] for l in lanc_mes if l["tipo"] == "saida")
    dados_cartao_atual = calcular_dados_cartao(compras_raw, mes_atual)
    total_atual = round(saidas_mes_atual + dados_cartao_atual["total_fatura"], 2)

    # Gastos do mês anterior
    lanc_ant = [l for l in lancamentos if l["data"][:7] == mes_anterior]
    saidas_mes_ant = sum(l["valor"] for l in lanc_ant if l["tipo"] == "saida")
    dados_cartao_ant = calcular_dados_cartao(compras_raw, mes_anterior)
    total_anterior = round(saidas_mes_ant + dados_cartao_ant["total_fatura"], 2)

    # Projeção: extrapolação linear pelo dia do mês
    hoje = date.today()
    if hoje.strftime("%Y-%m") == mes_atual:
        # Estamos no mês atual: extrapolar pelo dia
        dia_atual = hoje.day
        # Dias no mês: aproximar por 30
        try:
            import calendar
            ultimo_dia = calendar.monthrange(hoje.year, hoje.month)[1]
        except Exception:
            ultimo_dia = 30
        if dia_atual > 0 and dia_atual < ultimo_dia:
            projecao_mes_atual = round(total_atual * ultimo_dia / dia_atual, 2)
        else:
            projecao_mes_atual = total_atual
    else:
        projecao_mes_atual = total_atual

    # Estimativa próximo mês: média entre projeção atual e mês anterior
    if total_anterior > 0 and projecao_mes_atual > 0:
        estimativa_proximo = round((total_anterior + projecao_mes_atual) / 2, 2)
    elif total_anterior > 0:
        estimativa_proximo = total_anterior
    else:
        estimativa_proximo = projecao_mes_atual

    # Tendência
    if total_anterior > 0:
        variacao_pct = round(((projecao_mes_atual - total_anterior) / total_anterior) * 100, 1)
    else:
        variacao_pct = 0.0

    if variacao_pct > 5:
        tendencia = "alta"
    elif variacao_pct < -5:
        tendencia = "queda"
    else:
        tendencia = "estável"

    return {
        "mes_anterior": mes_anterior,
        "total_anterior": total_anterior,
        "total_atual": total_atual,
        "projecao_mes_atual": projecao_mes_atual,
        "estimativa_proximo": estimativa_proximo,
        "variacao_pct": variacao_pct,
        "tendencia": tendencia,
        "proximo_mes": adicionar_meses(mes_atual, 1),
    }


# --------------------------------------------------- autenticação & OAuth ----
ROTAS_PUBLICAS = {"static", "login", "admin_login", "auth_google", "auth_google_callback", "logout"}


def _google_config():
    client_id = (os.environ.get("GOOGLE_CLIENT_ID", "") or "").strip()
    client_secret = (os.environ.get("GOOGLE_CLIENT_SECRET", "") or "").strip()
    redirect_uri = (os.environ.get("GOOGLE_REDIRECT_URI", "") or "").strip()
    if not redirect_uri and request:
        redirect_uri = url_for("auth_google_callback", _external=True)
    return client_id, client_secret, redirect_uri


def _google_oauth_configurado():
    client_id, client_secret, _ = _google_config()
    if not client_id or not client_secret:
        return False
    placeholders = ("seu-", "cole-", "your-", "changeme")
    return not (
        client_id.lower().startswith(placeholders)
        or client_secret.lower().startswith(placeholders)
    )


def _google_http_json(url, data=None, headers=None):
    req_headers = dict(headers or {})
    body = None
    if data is not None:
        body = urlencode(data).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    req = Request(url, data=body, headers=req_headers)
    with urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _usuario_atual_id():
    return g.usuario["id"]


def admin_required(fn):
    """Restringe o acesso da rota apenas a usuários com perfil administrador."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not getattr(g, "usuario", None) or g.usuario["role"] != "admin":
            abort(403)
        return fn(*args, **kwargs)
    return wrapper


@app.before_request
def exigir_autenticacao():
    g.usuario = None
    uid = session.get("user_id")
    if uid is not None:
        with conectar() as conn:
            user = conn.execute("SELECT * FROM usuarios WHERE id = ?", (uid,)).fetchone()
        if user and user["ativo"]:
            g.usuario = dict(user)
        else:
            session.pop("user_id", None)

    if request.endpoint in ROTAS_PUBLICAS:
        return None

    if g.usuario is None:
        if request.headers.get("X-Requested-With") == "fetch" or request.is_json:
            return jsonify({"erro": "Autenticação obrigatória."}), 401
        if request.path.startswith("/admin"):
            return redirect(url_for("admin_login"))
        return redirect(url_for("login"))
    return None


@app.context_processor
def injetar_contexto_auth():
    return {
        "usuario_atual": getattr(g, "usuario", None),
        "google_oauth_configurado": _google_oauth_configurado(),
    }


@app.route("/login", methods=["GET"])
def login():
    if g.usuario is not None:
        return redirect(url_for("admin_painel") if g.usuario["role"] == "admin" else url_for("resumo"))
    return render_template("login.html")


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if g.usuario is not None:
        return redirect(url_for("admin_painel") if g.usuario["role"] == "admin" else url_for("resumo"))

    erros = []
    email_preenchido = ""
    if request.method == "POST":
        email = (request.form.get("email", "") or "").strip().lower()
        senha = request.form.get("senha", "") or ""
        email_preenchido = email

        if not email or not senha:
            erros.append("Informe o e-mail e a senha do administrador.")
        else:
            with conectar() as conn:
                user = conn.execute(
                    "SELECT * FROM usuarios WHERE LOWER(email) = ?", (email,)
                ).fetchone()
                if user is None or not user["senha_hash"] or not check_password_hash(user["senha_hash"], senha):
                    erros.append("Credenciais inválidas.")
                elif user["role"] != "admin":
                    erros.append("Acesso restrito a contas com perfil de administrador.")
                elif not user["ativo"]:
                    erros.append("Esta conta de administrador está bloqueada.")
                else:
                    agora = datetime.now().strftime("%Y-%m-%d %H:%M")
                    conn.execute("UPDATE usuarios SET ultimo_login = ? WHERE id = ?", (agora, user["id"]))
                    session.clear()
                    session["user_id"] = user["id"]
                    flash(f"Bem-vindo(a) ao painel administrativo, {user['nome']}!")
                    return redirect(url_for("admin_painel"))

    return render_template("admin_login.html", erros=erros, email_preenchido=email_preenchido)


@app.route("/auth/google")
def auth_google():
    if not _google_oauth_configurado():
        flash(
            "Login com Google ainda não configurado. Defina GOOGLE_CLIENT_ID e "
            "GOOGLE_CLIENT_SECRET no arquivo .env."
        )
        return redirect(url_for("login"))

    client_id, _, redirect_uri = _google_config()
    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    params = urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    })
    return redirect(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")


@app.route("/auth/google/callback")
def auth_google_callback():
    if request.args.get("error"):
        flash("Autenticação com o Google cancelada ou recusada.")
        return redirect(url_for("login"))

    state_recebido = request.args.get("state", "")
    state_esperado = session.pop("oauth_state", None)
    code = request.args.get("code", "")
    if not state_esperado or state_recebido != state_esperado or not code:
        flash("Sessão de login com o Google expirada ou inválida. Tente novamente.")
        return redirect(url_for("login"))

    if not _google_oauth_configurado():
        flash("Login com Google não está configurado no servidor.")
        return redirect(url_for("login"))

    client_id, client_secret, redirect_uri = _google_config()
    try:
        token_resp = _google_http_json(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        access_token = token_resp.get("access_token")
        if not access_token:
            raise ValueError("Token de acesso não retornado pelo Google.")

        perfil = _google_http_json(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    except (HTTPError, URLError, ValueError, KeyError) as exc:
        flash(f"Não foi possível autenticar com o Google: {exc}")
        return redirect(url_for("login"))

    email = (perfil.get("email") or "").strip().lower()
    sub = (perfil.get("sub") or "").strip()
    nome = (perfil.get("name") or "").strip() or (email.split("@")[0] if email else "Usuário Google")
    avatar = (perfil.get("picture") or "").strip()
    email_verificado = bool(perfil.get("email_verified"))

    if not email or not sub or not email_verificado:
        flash("Sua conta Google precisa ter um e-mail verificado para entrar.")
        return redirect(url_for("login"))

    agora = datetime.now().strftime("%Y-%m-%d %H:%M")
    admin_env_email = (os.environ.get("ADMIN_EMAIL", "") or "").strip().lower()
    allow_signup = os.environ.get("ALLOW_GOOGLE_SIGNUP", "true").strip().lower() in ("1", "true", "sim", "yes", "on")

    with conectar() as conn:
        user = conn.execute(
            "SELECT * FROM usuarios WHERE google_sub = ? OR LOWER(email) = ?",
            (sub, email),
        ).fetchone()

        if user is None:
            if not allow_signup:
                flash("Cadastro automático via Google desativado. Solicite acesso ao administrador.")
                return redirect(url_for("login"))
            role_inicial = "admin" if admin_env_email and email == admin_env_email else "user"
            cur = conn.execute(
                """
                INSERT INTO usuarios (nome, email, google_sub, avatar_url, role, provedor, ativo, criado_em, ultimo_login)
                VALUES (?, ?, ?, ?, ?, 'google', 1, ?, ?)
                """,
                (nome, email, sub, avatar, role_inicial, agora, agora),
            )
            user_id = cur.lastrowid
            role_final = role_inicial
        else:
            if not user["ativo"]:
                flash("Sua conta está bloqueada pelo administrador.")
                return redirect(url_for("login"))
            conn.execute(
                """
                UPDATE usuarios
                SET google_sub = ?,
                    avatar_url = ?,
                    nome = COALESCE(NULLIF(?, ''), nome),
                    provedor = CASE WHEN provedor = 'local' AND senha_hash IS NULL THEN 'google' ELSE provedor END,
                    ultimo_login = ?
                WHERE id = ?
                """,
                (sub, avatar, nome, agora, user["id"]),
            )
            user_id = user["id"]
            role_final = user["role"]

    session.clear()
    session["user_id"] = user_id
    flash(f"Conectado com Google como {email}.")
    return redirect(url_for("admin_painel") if role_final == "admin" else url_for("resumo"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Você saiu da sua conta.")
    return redirect(url_for("login"))


# ------------------------------------------- painel admin (somente usuários) ----
def _salvar_env_chaves(atualizacoes):
    """Atualiza ou adiciona chaves no arquivo .env e sincroniza os.environ."""
    caminho = os.path.join(BASE_DIR, ".env")
    limpos = {}
    for chave, valor in atualizacoes.items():
        c = (chave or "").strip()
        v = (valor or "").replace("\r", "").replace("\n", "").strip()
        if c:
            limpos[c] = v

    linhas = []
    if os.path.isfile(caminho):
        with open(caminho, encoding="utf-8") as f:
            linhas = f.read().splitlines()

    encontradas = set()
    novas_linhas = []
    for linha in linhas:
        stripped = linha.strip()
        if stripped and not stripped.startswith("#") and "=" in linha:
            chave = linha.split("=", 1)[0].strip()
            if chave in limpos:
                novas_linhas.append(f"{chave}={limpos[chave]}")
                encontradas.add(chave)
                continue
        novas_linhas.append(linha)

    for chave, valor in limpos.items():
        if chave not in encontradas:
            novas_linhas.append(f"{chave}={valor}")

    with open(caminho, "w", encoding="utf-8") as f:
        f.write("\n".join(novas_linhas) + "\n")

    for chave, valor in limpos.items():
        os.environ[chave] = valor


@app.route("/admin")
@admin_required
def admin_painel():
    busca = (request.args.get("q", "") or "").strip()
    filtro_role = (request.args.get("role", "") or "").strip()
    filtro_status = (request.args.get("status", "") or "").strip()

    with conectar() as conn:
        todos = conn.execute("SELECT * FROM usuarios ORDER BY id DESC").fetchall()

    resumo_usuarios = {
        "total": len(todos),
        "ativos": sum(1 for u in todos if u["ativo"]),
        "bloqueados": sum(1 for u in todos if not u["ativo"]),
        "admins": sum(1 for u in todos if u["role"] == "admin"),
        "google": sum(1 for u in todos if u["google_sub"]),
    }

    filtrados = []
    termo = busca.lower()
    for u in todos:
        if termo and termo not in u["nome"].lower() and termo not in u["email"].lower():
            continue
        if filtro_role in ("user", "admin") and u["role"] != filtro_role:
            continue
        if filtro_status == "ativo" and not u["ativo"]:
            continue
        if filtro_status == "bloqueado" and u["ativo"]:
            continue
        filtrados.append(u)

    client_id, _, redirect_uri = _google_config()
    return render_template(
        "admin.html",
        usuarios=filtrados,
        resumo_usuarios=resumo_usuarios,
        busca=busca,
        filtro_role=filtro_role,
        filtro_status=filtro_status,
        google_client_id_atual=client_id,
        google_redirect_uri_atual=redirect_uri or url_for("auth_google_callback", _external=True),
    )


@app.route("/admin/config-google", methods=["POST"])
@admin_required
def admin_config_google():
    client_id = (request.form.get("google_client_id", "") or "").strip()
    client_secret = (request.form.get("google_client_secret", "") or "").strip()
    redirect_uri = (request.form.get("google_redirect_uri", "") or "").strip()
    if not client_id or not client_secret:
        flash("Informe tanto o Client ID quanto o Client Secret do Google OAuth.")
        return redirect(url_for("admin_painel"))

    atualizacoes = {
        "GOOGLE_CLIENT_ID": client_id,
        "GOOGLE_CLIENT_SECRET": client_secret,
    }
    if redirect_uri:
        atualizacoes["GOOGLE_REDIRECT_URI"] = redirect_uri

    _salvar_env_chaves(atualizacoes)
    flash("Credenciais do Google OAuth salvas no .env com sucesso!")
    return redirect(url_for("admin_painel"))


@app.route("/admin/usuarios/novo", methods=["GET", "POST"])
@admin_required
def admin_usuario_novo():
    erros = []
    dados = {"nome": "", "email": "", "role": "user", "ativo": True}

    if request.method == "POST":
        nome = (request.form.get("nome", "") or "").strip()
        email = (request.form.get("email", "") or "").strip().lower()
        role = request.form.get("role", "user")
        ativo = 1 if request.form.get("ativo") == "1" else 0
        senha = request.form.get("senha", "") or ""
        dados = {"nome": nome, "email": email, "role": role, "ativo": bool(ativo)}

        if not nome:
            erros.append("Informe o nome do usuário.")
        if not email or "@" not in email:
            erros.append("Informe um e-mail válido (Gmail ou corporativo).")
        if role not in ("user", "admin"):
            erros.append("Perfil inválido.")
        if role == "admin" and not senha and not email.endswith("@gmail.com"):
            erros.append("Defina uma senha para administradores locais.")

        if not erros:
            with conectar() as conn:
                existe = conn.execute(
                    "SELECT id FROM usuarios WHERE LOWER(email) = ?", (email,)
                ).fetchone()
                if existe:
                    erros.append("Já existe um usuário cadastrado com este e-mail.")
                else:
                    senha_hash = generate_password_hash(senha) if senha else None
                    provedor = "local" if senha else "google"
                    agora = datetime.now().strftime("%Y-%m-%d %H:%M")
                    conn.execute(
                        """
                        INSERT INTO usuarios (nome, email, senha_hash, role, provedor, ativo, criado_em)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (nome, email, senha_hash, role, provedor, ativo, agora),
                    )
                    flash(f"Usuário '{nome}' cadastrado com sucesso.")
                    return redirect(url_for("admin_painel"))

    return render_template(
        "admin_usuario_form.html",
        titulo="Novo usuário",
        dados=dados,
        erros=erros,
        editando=False,
    )


@app.route("/admin/usuarios/<int:id>/editar", methods=["GET", "POST"])
@admin_required
def admin_usuario_editar(id):
    with conectar() as conn:
        usuario = conn.execute("SELECT * FROM usuarios WHERE id = ?", (id,)).fetchone()
    if usuario is None:
        abort(404)

    erros = []
    dados = dict(usuario)

    if request.method == "POST":
        nome = (request.form.get("nome", "") or "").strip()
        email = (request.form.get("email", "") or "").strip().lower()
        role = request.form.get("role", "user")
        ativo = 1 if request.form.get("ativo") == "1" else 0
        nova_senha = request.form.get("senha", "") or ""
        dados = {
            **dict(usuario),
            "nome": nome,
            "email": email,
            "role": role,
            "ativo": ativo,
        }

        if not nome:
            erros.append("Informe o nome do usuário.")
        if not email or "@" not in email:
            erros.append("Informe um e-mail válido.")
        if role not in ("user", "admin"):
            erros.append("Perfil inválido.")
        if id == g.usuario["id"] and (role != "admin" or not ativo):
            erros.append("Você não pode remover seu próprio acesso de administrador ou bloquear sua própria conta.")

        if not erros:
            with conectar() as conn:
                conflito = conn.execute(
                    "SELECT id FROM usuarios WHERE LOWER(email) = ? AND id != ?", (email, id)
                ).fetchone()
                if conflito:
                    erros.append("Outro usuário já utiliza este e-mail.")
                else:
                    if nova_senha:
                        conn.execute(
                            """
                            UPDATE usuarios
                            SET nome = ?, email = ?, role = ?, ativo = ?, senha_hash = ?
                            WHERE id = ?
                            """,
                            (nome, email, role, ativo, generate_password_hash(nova_senha), id),
                        )
                    else:
                        conn.execute(
                            "UPDATE usuarios SET nome = ?, email = ?, role = ?, ativo = ? WHERE id = ?",
                            (nome, email, role, ativo, id),
                        )
                    flash(f"Usuário '{nome}' atualizado.")
                    return redirect(url_for("admin_painel"))

    return render_template(
        "admin_usuario_form.html",
        titulo="Editar usuário",
        dados=dados,
        erros=erros,
        editando=True,
    )


@app.route("/admin/usuarios/<int:id>/status", methods=["POST"])
@admin_required
def admin_usuario_status(id):
    if id == g.usuario["id"]:
        flash("Você não pode bloquear sua própria conta.")
        return redirect(url_for("admin_painel"))

    with conectar() as conn:
        usuario = conn.execute("SELECT * FROM usuarios WHERE id = ?", (id,)).fetchone()
        if usuario is None:
            abort(404)
        novo_ativo = 0 if usuario["ativo"] else 1
        conn.execute("UPDATE usuarios SET ativo = ? WHERE id = ?", (novo_ativo, id))

    flash(
        f"Usuário '{usuario['nome']}' {'ativado' if novo_ativo else 'bloqueado'}."
    )
    return redirect(url_for("admin_painel"))


@app.route("/admin/usuarios/<int:id>/excluir", methods=["POST"])
@admin_required
def admin_usuario_excluir(id):
    if id == g.usuario["id"]:
        flash("Você não pode excluir a própria conta enquanto estiver logado.")
        return redirect(url_for("admin_painel"))

    with conectar() as conn:
        usuario = conn.execute("SELECT id, nome FROM usuarios WHERE id = ?", (id,)).fetchone()
        if usuario is None:
            abort(404)
        # Remove registros individuais vinculados sem expor ou consultar conteúdos
        conn.execute("DELETE FROM lancamentos WHERE user_id = ?", (id,))
        conn.execute("DELETE FROM cartao_gastos WHERE user_id = ?", (id,))
        conn.execute("DELETE FROM metas WHERE user_id = ?", (id,))
        conn.execute("DELETE FROM importacoes WHERE user_id = ?", (id,))
        conn.execute("DELETE FROM faturas_pagas WHERE user_id = ?", (id,))
        conn.execute("DELETE FROM usuarios WHERE id = ?", (id,))

    flash(f"Usuário '{usuario['nome']}' removido do sistema.")
    return redirect(url_for("admin_painel"))


# ----------------------------------------------------------- rotas: resumo ----
@app.route("/")
def resumo():
    uid = _usuario_atual_id()
    mes_selecionado = normalizar_mes(request.args.get("mes", ""))
    with conectar() as conn:
        lancamentos = conn.execute(
            "SELECT * FROM lancamentos WHERE user_id = ? ORDER BY data ASC, id ASC", (uid,)
        ).fetchall()
        compras_raw = conn.execute(
            "SELECT * FROM cartao_gastos WHERE user_id = ? ORDER BY data_compra DESC, id DESC", (uid,)
        ).fetchall()
        metas_raw = conn.execute(
            "SELECT * FROM metas WHERE user_id = ? ORDER BY prazo ASC, id ASC", (uid,)
        ).fetchall()
        aportes = conn.execute(
            "SELECT ma.* FROM meta_aportes ma "
            "JOIN metas m ON m.id = ma.meta_id "
            "WHERE m.user_id = ? ORDER BY ma.data ASC, ma.id ASC",
            (uid,),
        ).fetchall()
        faturas_pagas_rows = conn.execute(
            "SELECT mes FROM faturas_pagas WHERE user_id = ?", (uid,)
        ).fetchall()

    faturas_pagas_set = {r["mes"] for r in faturas_pagas_rows}
    compras_enr = [enriquecer_compra_cartao(c) for c in compras_raw]
    metas_enr = [enriquecer_meta(m) for m in metas_raw]

    saldo_caixa = round(
        sum(l["valor"] if l["tipo"] == "entrada" else -l["valor"] for l in lancamentos), 2
    )
    total_metas = round(sum(m["valor_atual"] for m in metas_enr), 2)
    total_guardado = round(saldo_caixa + total_metas, 2)
    total_deve = round(sum(c["valor_restante"] for c in compras_enr), 2)
    patrimonio_liquido = round(total_guardado - total_deve, 2)

    lancamentos_mes = [l for l in lancamentos if l["data"][:7] == mes_selecionado]
    entradas_mes = round(
        sum(l["valor"] for l in lancamentos_mes if l["tipo"] == "entrada"), 2
    )
    saidas_mes = round(
        sum(l["valor"] for l in lancamentos_mes if l["tipo"] == "saida"), 2
    )
    saidas_pagas_mes = round(
        sum(l["valor"] for l in lancamentos_mes if l["tipo"] == "saida" and l["pago"]), 2
    )
    saidas_pendentes_mes = round(saidas_mes - saidas_pagas_mes, 2)
    saldo_mes = round(entradas_mes - saidas_mes, 2)

    dados_cartao_mes = calcular_dados_cartao(
        compras_raw, mes_selecionado, faturas_pagas_set=faturas_pagas_set
    )
    fatura_mes = dados_cartao_mes["total_fatura"]
    fatura_paga = dados_cartao_mes["fatura_mes_paga"]
    total_gasto_mes = round(saidas_mes + fatura_mes, 2)
    sobra_falta_mes = round(entradas_mes - total_gasto_mes, 2)

    movimentacoes_mes = []
    for l in lancamentos_mes:
        movimentacoes_mes.append({
            "id": l["id"],
            "origem": "conta",
            "origem_label": "Conta",
            "data": l["data"],
            "descricao": l["descricao"] or l["categoria"],
            "categoria": l["categoria"],
            "tipo": l["tipo"],
            "valor": l["valor"],
            "pago": bool(l["pago"]),
        })
    for item in dados_cartao_mes["fatura_itens"]:
        movimentacoes_mes.append({
            "id": item["id"],
            "origem": "cartao",
            "origem_label": f"Cartão ({item['numero_parcela']}/{item['total_parcelas']})",
            "data": item["data_compra"],
            "descricao": item["descricao"],
            "categoria": item["categoria"],
            "tipo": "saida",
            "valor": item["valor"],
            "pago": item["paga"],
        })
    movimentacoes_mes.sort(key=lambda m: (m["data"], m["id"]), reverse=True)

    serie_evolucao = calcular_evolucao_patrimonial(lancamentos, compras_enr, aportes, metas_raw)
    parcelas_abertas = sum(c["parcelas_restantes"] for c in compras_enr)
    categorias_cartao_mes = sorted({i["categoria"] for i in dados_cartao_mes["fatura_itens"]})

    previsao = calcular_previsao(lancamentos, compras_raw, mes_selecionado)

    return render_template(
        "resumo.html",
        hoje_mes=mes_selecionado,
        saldo_caixa=saldo_caixa,
        total_metas=total_metas,
        total_guardado=total_guardado,
        total_deve=total_deve,
        patrimonio_liquido=patrimonio_liquido,
        saldo_mes=saldo_mes,
        entradas_mes=entradas_mes,
        saidas_mes=saidas_mes,
        saidas_pagas_mes=saidas_pagas_mes,
        saidas_pendentes_mes=saidas_pendentes_mes,
        fatura_mes=fatura_mes,
        fatura_paga=fatura_paga,
        total_gasto_mes=total_gasto_mes,
        sobra_falta_mes=sobra_falta_mes,
        movimentacoes_mes=movimentacoes_mes,
        fatura_itens_mes=dados_cartao_mes["fatura_itens"],
        categorias_cartao_mes=categorias_cartao_mes,
        parcelas_abertas=parcelas_abertas,
        compras_abertas=sum(1 for c in compras_enr if not c["quitado"]),
        metas=metas_enr[:4],
        total_metas_qtd=len(metas_enr),
        serie_evolucao=serie_evolucao,
        por_categoria_cartao=dados_cartao_mes["por_categoria"],
        previsao=previsao,
    )


@app.route("/categoria/<origem>/<int:id>", methods=["POST"])
def atualizar_categoria(origem, id):
    if origem not in ("conta", "cartao"):
        abort(400)
    uid = _usuario_atual_id()
    nova_cat = request.form.get("categoria", "").strip() or "Outros"
    tabela = "cartao_gastos" if origem == "cartao" else "lancamentos"
    with conectar() as conn:
        cur = conn.execute(
            f"UPDATE {tabela} SET categoria = ? WHERE id = ? AND user_id = ?",
            (nova_cat, id, uid),
        )
        if cur.rowcount == 0:
            abort(404)
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"ok": True, "categoria": nova_cat})
    flash(f"Categoria atualizada para '{nova_cat}'.")
    return redirect(request.referrer or url_for("resumo"))


# ------------------------------------------------------ rotas: lançamentos ----
@app.route("/lancamentos")
def lancamentos():
    uid = _usuario_atual_id()
    mes = normalizar_mes(request.args.get("mes", ""))
    with conectar() as conn:
        lista = conn.execute(
            "SELECT * FROM lancamentos WHERE user_id = ? AND substr(data, 1, 7) = ? "
            "ORDER BY data DESC, id DESC",
            (uid, mes),
        ).fetchall()

    entradas = sum(l["valor"] for l in lista if l["tipo"] == "entrada")
    saidas = sum(l["valor"] for l in lista if l["tipo"] == "saida")
    saidas_pagas = sum(l["valor"] for l in lista if l["tipo"] == "saida" and l["pago"])
    saidas_pendentes = round(saidas - saidas_pagas, 2)

    return render_template(
        "lancamentos.html",
        lancamentos=lista,
        mes=mes,
        entradas=entradas,
        saidas=saidas,
        saidas_pagas=saidas_pagas,
        saidas_pendentes=saidas_pendentes,
        saldo=entradas - saidas,
    )


@app.route("/lancamentos/pagar/<int:id>", methods=["POST"])
def lancamento_pagar(id):
    """Alterna o status pago/pendente de um lançamento."""
    uid = _usuario_atual_id()
    with conectar() as conn:
        lanc = conn.execute(
            "SELECT * FROM lancamentos WHERE id = ? AND user_id = ?", (id, uid)
        ).fetchone()
        if lanc is None:
            abort(404)
        novo_pago = 0 if lanc["pago"] else 1
        conn.execute(
            "UPDATE lancamentos SET pago = ? WHERE id = ? AND user_id = ?",
            (novo_pago, id, uid),
        )
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"ok": True, "pago": bool(novo_pago)})
    flash("Status de pagamento atualizado.")
    return redirect(request.referrer or url_for("lancamentos"))


@app.route("/novo", methods=["GET", "POST"])
def novo():
    uid = _usuario_atual_id()
    erros = []
    dados = {
        "tipo": "saida", "valor": "", "categoria": "",
        "data": date.today().isoformat(), "descricao": "",
    }
    if request.method == "POST":
        erros, dados, valor, categoria = validar_lancamento(request.form)
        if not erros:
            with conectar() as conn:
                conn.execute(
                    "INSERT INTO lancamentos (user_id, tipo, valor, categoria, data, descricao) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (uid, dados["tipo"], valor, categoria, dados["data"], dados["descricao"]),
                )
            flash("Lançamento salvo.")
            return redirect(url_for("lancamentos", mes=dados["data"][:7]))
    return render_template("form.html", titulo="Novo lançamento", dados=dados, erros=erros)


@app.route("/editar/<int:id>", methods=["GET", "POST"])
def editar(id):
    uid = _usuario_atual_id()
    with conectar() as conn:
        lanc = conn.execute(
            "SELECT * FROM lancamentos WHERE id = ? AND user_id = ?", (id, uid)
        ).fetchone()
    if lanc is None:
        abort(404)

    erros = []
    dados = dict(lanc)
    if request.method == "POST":
        erros, dados, valor, categoria = validar_lancamento(request.form)
        if not erros:
            with conectar() as conn:
                conn.execute(
                    "UPDATE lancamentos SET tipo = ?, valor = ?, categoria = ?, "
                    "data = ?, descricao = ? WHERE id = ? AND user_id = ?",
                    (dados["tipo"], valor, categoria, dados["data"], dados["descricao"], id, uid),
                )
            flash("Lançamento atualizado.")
            return redirect(url_for("lancamentos", mes=dados["data"][:7]))
    return render_template("form.html", titulo="Editar lançamento", dados=dados, erros=erros)


@app.route("/excluir/<int:id>", methods=["POST"])
def excluir(id):
    uid = _usuario_atual_id()
    with conectar() as conn:
        conn.execute("DELETE FROM lancamentos WHERE id = ? AND user_id = ?", (id, uid))
    flash("Lançamento excluído.")
    return redirect(request.referrer or url_for("lancamentos"))


# ----------------------------------------------------------- rotas: cartão ----
@app.route("/cartao")
def cartao():
    uid = _usuario_atual_id()
    mes = normalizar_mes(request.args.get("mes", ""))
    categoria = request.args.get("categoria", "").strip()
    banco_filtro = request.args.get("banco", "").strip()

    with conectar() as conn:
        compras = conn.execute(
            "SELECT * FROM cartao_gastos WHERE user_id = ? ORDER BY data_compra DESC, id DESC",
            (uid,),
        ).fetchall()
        faturas_pagas_rows = conn.execute(
            "SELECT banco, mes FROM faturas_pagas WHERE user_id = ?", (uid,)
        ).fetchall()
        # Bancos que têm dados no cartão
        bancos_rows = conn.execute(
            "SELECT DISTINCT banco FROM cartao_gastos "
            "WHERE user_id = ? AND banco IS NOT NULL AND banco != '' ORDER BY banco",
            (uid,),
        ).fetchall()

    faturas_pagas_set = {r["mes"] for r in faturas_pagas_rows}
    # Set de (banco, mes) para verificar fatura paga por banco
    faturas_pagas_banco_mes = {(r["banco"], r["mes"]) for r in faturas_pagas_rows}
    bancos_disponiveis = [r["banco"] for r in bancos_rows]

    dados = calcular_dados_cartao(compras, mes, categoria, banco_filtro, faturas_pagas_set)

    # Verificar se fatura deste banco+mês está paga
    fatura_banco_paga = (banco_filtro, mes) in faturas_pagas_banco_mes if banco_filtro else mes in faturas_pagas_set

    return render_template(
        "cartao.html",
        mes=mes,
        categoria=categoria,
        banco_filtro=banco_filtro,
        bancos_disponiveis=bancos_disponiveis,
        categorias=dados["categorias_disponiveis"],
        fatura_itens=dados["fatura_itens"],
        compras=dados["compras"],
        por_categoria=dados["por_categoria"],
        total_fatura=dados["total_fatura"],
        total_fatura_aberta=dados["total_fatura_aberta"],
        total_restante_geral=dados["total_restante_geral"],
        fatura_mes_paga=fatura_banco_paga,
    )


@app.route("/cartao/fatura/pagar", methods=["POST"])
def cartao_fatura_pagar():
    """Marca ou desmarca toda a fatura de um mês como paga."""
    uid = _usuario_atual_id()
    mes = request.form.get("mes", "").strip()
    banco = request.form.get("banco", "").strip()
    acao = request.form.get("acao", "pagar")
    if not mes:
        flash("Mês inválido.")
        return redirect(request.referrer or url_for("cartao"))

    with conectar() as conn:
        if acao == "pagar":
            conn.execute(
                "INSERT OR REPLACE INTO faturas_pagas (user_id, banco, mes, pago_em) VALUES (?, ?, ?, ?)",
                (uid, banco, mes, datetime.now().strftime("%Y-%m-%d %H:%M")),
            )
            flash(f"Fatura de {mes_br(mes)} marcada como paga.")
        else:
            conn.execute(
                "DELETE FROM faturas_pagas WHERE user_id = ? AND banco = ? AND mes = ?",
                (uid, banco, mes),
            )
            flash(f"Fatura de {mes_br(mes)} reaberta.")

    return redirect(request.referrer or url_for("cartao", mes=mes))


@app.route("/cartao/novo", methods=["GET", "POST"])
def cartao_novo():
    uid = _usuario_atual_id()
    erros = []
    dados = {
        "descricao": "",
        "categoria": "",
        "valor_total": "",
        "parcelas": "1",
        "parcelas_pagas": "0",
        "data_compra": date.today().isoformat(),
    }
    if request.method == "POST":
        erros, dados, valor_total, parcelas, parcelas_pagas, categoria = validar_cartao(request.form)
        if not erros:
            with conectar() as conn:
                conn.execute(
                    "INSERT INTO cartao_gastos "
                    "(user_id, descricao, categoria, valor_total, parcelas, parcelas_pagas, data_compra) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        uid, dados["descricao"], categoria, valor_total,
                        parcelas, parcelas_pagas, dados["data_compra"],
                    ),
                )
            flash("Gasto no cartão registrado.")
            return redirect(url_for("cartao", mes=dados["data_compra"][:7]))
    return render_template(
        "cartao_form.html",
        titulo="Novo gasto no cartão",
        dados=dados,
        erros=erros,
    )


@app.route("/cartao/editar/<int:id>", methods=["GET", "POST"])
def cartao_editar(id):
    uid = _usuario_atual_id()
    with conectar() as conn:
        compra = conn.execute(
            "SELECT * FROM cartao_gastos WHERE id = ? AND user_id = ?", (id, uid)
        ).fetchone()
    if compra is None:
        abort(404)

    erros = []
    dados = dict(compra)
    if request.method == "POST":
        erros, dados, valor_total, parcelas, parcelas_pagas, categoria = validar_cartao(request.form)
        if not erros:
            with conectar() as conn:
                conn.execute(
                    "UPDATE cartao_gastos SET descricao = ?, categoria = ?, valor_total = ?, "
                    "parcelas = ?, parcelas_pagas = ?, data_compra = ? WHERE id = ? AND user_id = ?",
                    (
                        dados["descricao"], categoria, valor_total,
                        parcelas, parcelas_pagas, dados["data_compra"], id, uid,
                    ),
                )
            flash("Gasto no cartão atualizado.")
            return redirect(url_for("cartao", mes=dados["data_compra"][:7]))
    return render_template(
        "cartao_form.html",
        titulo="Editar gasto no cartão",
        dados=dados,
        erros=erros,
    )


@app.route("/cartao/parcela/<int:id>", methods=["POST"])
def cartao_parcela(id):
    uid = _usuario_atual_id()
    acao = request.form.get("acao", "pagar")
    with conectar() as conn:
        compra = conn.execute(
            "SELECT * FROM cartao_gastos WHERE id = ? AND user_id = ?", (id, uid)
        ).fetchone()
        if compra is None:
            abort(404)
        pagas = compra["parcelas_pagas"]
        if acao == "pagar" and pagas < compra["parcelas"]:
            pagas += 1
            flash("Parcela marcada como paga.")
        elif acao == "desfazer" and pagas > 0:
            pagas -= 1
            flash("Pagamento da última parcela desfeito.")
        conn.execute(
            "UPDATE cartao_gastos SET parcelas_pagas = ? WHERE id = ? AND user_id = ?",
            (pagas, id, uid),
        )
    return redirect(request.referrer or url_for("cartao"))


@app.route("/cartao/excluir/<int:id>", methods=["POST"])
def cartao_excluir(id):
    uid = _usuario_atual_id()
    with conectar() as conn:
        conn.execute("DELETE FROM cartao_gastos WHERE id = ? AND user_id = ?", (id, uid))
    flash("Compra no cartão excluída.")
    return redirect(request.referrer or url_for("cartao"))


# ----------------------------------------------------- rotas: planejamento ----
@app.route("/planejamento", methods=["GET", "POST"])
def planejamento():
    uid = _usuario_atual_id()
    erros = []
    dados = {
        "nome": "",
        "valor_alvo": "",
        "valor_atual": "0",
        "prazo": "",
    }
    if request.method == "POST":
        erros, dados, valor_alvo, valor_atual = validar_meta(request.form)
        if not erros:
            hoje_iso = date.today().isoformat()
            with conectar() as conn:
                cur = conn.execute(
                    "INSERT INTO metas (user_id, nome, valor_alvo, valor_atual, prazo, criado_em) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (uid, dados["nome"], valor_alvo, valor_atual, dados["prazo"], hoje_iso),
                )
                if valor_atual > 0:
                    conn.execute(
                        "INSERT INTO meta_aportes (meta_id, valor, data, observacao) "
                        "VALUES (?, ?, ?, ?)",
                        (cur.lastrowid, valor_atual, hoje_iso, "Valor inicial da meta"),
                    )
            flash("Meta criada com sucesso.")
            return redirect(url_for("planejamento"))

    with conectar() as conn:
        metas_raw = conn.execute(
            "SELECT * FROM metas WHERE user_id = ? ORDER BY prazo ASC, id DESC", (uid,)
        ).fetchall()

    metas = [enriquecer_meta(m) for m in metas_raw]
    total_alvo = round(sum(m["valor_alvo"] for m in metas), 2)
    total_guardado = round(sum(m["valor_atual"] for m in metas), 2)
    total_restante = round(sum(m["restante"] for m in metas), 2)

    return render_template(
        "planejamento.html",
        metas=metas,
        erros=erros,
        dados=dados,
        total_alvo=total_alvo,
        total_guardado=total_guardado,
        total_restante=total_restante,
    )


@app.route("/planejamento/aporte/<int:id>", methods=["POST"])
def meta_aporte(id):
    uid = _usuario_atual_id()
    valor = parse_valor(request.form.get("valor", ""))
    observacao = request.form.get("observacao", "").strip()
    if valor is None or valor <= 0:
        flash("Informe um valor de aporte maior que zero.")
        return redirect(url_for("planejamento"))

    with conectar() as conn:
        meta = conn.execute(
            "SELECT * FROM metas WHERE id = ? AND user_id = ?", (id, uid)
        ).fetchone()
        if meta is None:
            abort(404)
        novo_atual = round(meta["valor_atual"] + valor, 2)
        conn.execute(
            "UPDATE metas SET valor_atual = ? WHERE id = ? AND user_id = ?",
            (novo_atual, id, uid),
        )
        conn.execute(
            "INSERT INTO meta_aportes (meta_id, valor, data, observacao) VALUES (?, ?, ?, ?)",
            (id, valor, date.today().isoformat(), observacao or "Aporte na meta"),
        )
    flash(f"Aporte de {brl(valor)} adicionado à meta '{meta['nome']}'.")
    return redirect(url_for("planejamento"))


@app.route("/planejamento/editar/<int:id>", methods=["GET", "POST"])
def meta_editar(id):
    uid = _usuario_atual_id()
    with conectar() as conn:
        meta = conn.execute(
            "SELECT * FROM metas WHERE id = ? AND user_id = ?", (id, uid)
        ).fetchone()
    if meta is None:
        abort(404)

    erros = []
    dados = dict(meta)
    if request.method == "POST":
        erros, dados, valor_alvo, valor_atual = validar_meta(request.form)
        if not erros:
            diferenca = round(valor_atual - meta["valor_atual"], 2)
            with conectar() as conn:
                conn.execute(
                    "UPDATE metas SET nome = ?, valor_alvo = ?, valor_atual = ?, prazo = ? "
                    "WHERE id = ? AND user_id = ?",
                    (dados["nome"], valor_alvo, valor_atual, dados["prazo"], id, uid),
                )
                if diferenca != 0:
                    conn.execute(
                        "INSERT INTO meta_aportes (meta_id, valor, data, observacao) "
                        "VALUES (?, ?, ?, ?)",
                        (id, diferenca, date.today().isoformat(), "Ajuste manual da meta"),
                    )
            flash("Meta atualizada.")
            return redirect(url_for("planejamento"))
    return render_template("meta_form.html", titulo="Editar meta", dados=dados, erros=erros)


@app.route("/planejamento/excluir/<int:id>", methods=["POST"])
def meta_excluir(id):
    uid = _usuario_atual_id()
    with conectar() as conn:
        conn.execute("DELETE FROM metas WHERE id = ? AND user_id = ?", (id, uid))
    flash("Meta excluída.")
    return redirect(url_for("planejamento"))


# -------------------------------------------- rotas: leitor de extrato ----
def _importar_para_lancamentos(conn, user_id, itens, lote, ignorar_duplicados, banco=""):
    existentes = set()
    if ignorar_duplicados:
        for r in conn.execute(
            "SELECT data, tipo, valor, descricao FROM lancamentos WHERE user_id = ?", (user_id,)
        ).fetchall():
            existentes.add((r["data"], r["tipo"], round(r["valor"], 2), (r["descricao"] or "").strip()))

    lancados = []
    ignorados = 0
    for item in itens:
        chave = (item["data"], item["tipo"], round(item["valor"], 2), item["descricao"])
        if ignorar_duplicados and chave in existentes:
            ignorados += 1
            continue
        conn.execute(
            "INSERT INTO lancamentos (user_id, tipo, valor, categoria, data, descricao, lote_importacao, banco) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, item["tipo"], item["valor"], item["categoria"], item["data"], item["descricao"], lote, banco),
        )
        existentes.add(chave)
        lancados.append(item)
    return lancados, ignorados


def _importar_para_cartao(conn, user_id, itens, lote, ignorar_duplicados, banco=""):
    existentes = set()
    if ignorar_duplicados:
        for r in conn.execute(
            "SELECT data_compra, valor_total, descricao FROM cartao_gastos WHERE user_id = ?", (user_id,)
        ).fetchall():
            existentes.add((r["data_compra"], round(r["valor_total"], 2), (r["descricao"] or "").strip()))

    lancados = []
    ignorados = 0
    for item in itens:
        # Pagamentos/estornos positivos em fatura de cartão não entram como nova compra
        if item["tipo"] == "entrada" and "estorno" not in item["descricao"].lower():
            continue
        chave = (item["data"], round(item["valor"], 2), item["descricao"])
        if ignorar_duplicados and chave in existentes:
            ignorados += 1
            continue
        parcelas = item.get("parcelas", 1)
        pagas = max(item.get("parcela_atual", 1) - 1, 0)
        conn.execute(
            "INSERT INTO cartao_gastos "
            "(user_id, descricao, categoria, valor_total, parcelas, parcelas_pagas, data_compra, lote_importacao, banco) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, item["descricao"], item["categoria"], item["valor"], parcelas, pagas, item["data"], lote, banco),
        )
        existentes.add(chave)
        lancados.append(item)
    return lancados, ignorados


@app.route("/importar", methods=["GET", "POST"])
def importar_extrato():
    uid = _usuario_atual_id()
    erros = []
    resultado = None
    banco_detectado = ""

    with conectar() as conn:
        historico = conn.execute(
            "SELECT * FROM importacoes WHERE user_id = ? ORDER BY id DESC LIMIT 10", (uid,)
        ).fetchall()
        # Bancos que já tiveram importações
        bancos_importados = conn.execute(
            "SELECT DISTINCT banco FROM importacoes "
            "WHERE user_id = ? AND banco IS NOT NULL AND banco != '' ORDER BY banco",
            (uid,),
        ).fetchall()
        bancos_importados = [r["banco"] for r in bancos_importados]

    if request.method == "POST":
        arquivo = request.files.get("arquivo")
        destino = request.form.get("destino", "lancamentos")
        banco_manual = request.form.get("banco", "").strip()
        categoria_padrao = request.form.get("categoria_padrao", "").strip()
        ignorar_duplicados = request.form.get("ignorar_duplicados") == "1"

        if not arquivo or not arquivo.filename:
            erros.append("Selecione um arquivo de extrato (.ofx, .csv, .txt ou .pdf).")
        elif destino not in ("lancamentos", "cartao"):
            erros.append("Destino inválido.")
        else:
            conteudo = arquivo.read()
            if not conteudo:
                erros.append("O arquivo enviado está vazio.")
            else:
                # Detectar banco automaticamente
                banco_detectado = inferir_banco(arquivo.filename, conteudo)
                banco_final = banco_manual or banco_detectado

                try:
                    itens = ler_extrato(arquivo.filename, conteudo, categoria_padrao)
                except Exception as exc:
                    itens = []
                    erros.append(f"Não foi possível ler o arquivo: {exc}")

                if not erros and not itens:
                    erros.append(
                        "Nenhuma movimentação foi reconhecida neste arquivo. "
                        "Verifique se o extrato contém data, descrição e valor (.ofx, .csv, .txt ou .pdf)."
                    )
                elif not erros:
                    lote = uuid.uuid4().hex[:12]
                    with conectar() as conn:
                        if destino == "cartao":
                            lancados, ignorados = _importar_para_cartao(conn, uid, itens, lote, ignorar_duplicados, banco_final)
                        else:
                            lancados, ignorados = _importar_para_lancamentos(conn, uid, itens, lote, ignorar_duplicados, banco_final)

                        if lancados:
                            conn.execute(
                                "INSERT INTO importacoes "
                                "(user_id, lote, arquivo, destino, quantidade, ignorados, criado_em, banco) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                (
                                    uid,
                                    lote,
                                    arquivo.filename,
                                    destino,
                                    len(lancados),
                                    ignorados,
                                    datetime.now().strftime("%Y-%m-%d %H:%M"),
                                    banco_final,
                                ),
                            )

                    if lancados:
                        flash(
                            f"Extrato processado: {len(lancados)} lançamento(s) adicionado(s)"
                            + (f" ({ignorados} duplicado(s) ignorado(s))." if ignorados else ".")
                        )
                    else:
                        flash(f"Nenhum lançamento novo adicionado ({ignorados} duplicado(s) já existiam).")

                    resultado = {
                        "arquivo": arquivo.filename,
                        "destino": destino,
                        "banco": banco_final,
                        "lote": lote if lancados else None,
                        "lancados": lancados,
                        "ignorados": ignorados,
                        "total_entradas": round(sum(i["valor"] for i in lancados if i["tipo"] == "entrada"), 2),
                        "total_saidas": round(sum(i["valor"] for i in lancados if i["tipo"] == "saida"), 2),
                    }

        # Recarregar histórico após POST
        with conectar() as conn:
            historico = conn.execute(
                "SELECT * FROM importacoes WHERE user_id = ? ORDER BY id DESC LIMIT 10", (uid,)
            ).fetchall()
            bancos_importados = conn.execute(
                "SELECT DISTINCT banco FROM importacoes "
                "WHERE user_id = ? AND banco IS NOT NULL AND banco != '' ORDER BY banco",
                (uid,),
            ).fetchall()
            bancos_importados = [r["banco"] for r in bancos_importados]

    return render_template(
        "importar.html",
        erros=erros,
        resultado=resultado,
        historico=historico,
        bancos_brasileiros=BANCOS_BRASILEIROS,
        bancos_importados=bancos_importados,
        banco_detectado=banco_detectado,
    )


@app.route("/importar/desfazer/<lote>", methods=["POST"])
def importar_desfazer(lote):
    uid = _usuario_atual_id()
    with conectar() as conn:
        conn.execute("DELETE FROM lancamentos WHERE lote_importacao = ? AND user_id = ?", (lote, uid))
        conn.execute("DELETE FROM cartao_gastos WHERE lote_importacao = ? AND user_id = ?", (lote, uid))
        conn.execute("DELETE FROM importacoes WHERE lote = ? AND user_id = ?", (lote, uid))
    flash("Importação desfeita e lançamentos do lote removidos.")
    return redirect(url_for("importar_extrato"))


criar_banco()

if __name__ == "__main__":
    host = os.environ.get("FLASK_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = _ler_int_env("FLASK_PORT", 5000)
    debug = os.environ.get("FLASK_DEBUG", "true").strip().lower() in ("1", "true", "sim", "yes", "on")
    app.run(host=host, port=port, debug=debug)
