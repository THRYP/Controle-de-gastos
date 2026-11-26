import csv
import io
import re
import unicodedata
from datetime import datetime

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None


IGNORAR_DESCRICOES = (
    "saldo anterior",
    "saldo do dia",
    "saldo atual",
    "saldo final",
    "saldo disponivel",
    "saldo bloqueado",
    "total de entradas",
    "total de saidas",
)

REGRAS_CATEGORIA = [
    ("Assinaturas", (
        "netflix", "spotify", "disney", "amazon prime", "prime video",
        "youtube", "apple.com", "icloud", "google ", "fiber net", "internet",
        "telecom", "vivo ", "claro ", "tim ", "oi ", "streaming", "globoplay",
    )),
    ("Compras", (
        "mercado livre", "mercadolivre", "amazon", "shopee", "shein",
        "magalu", "magazine", "americanas", "casas bahia", "renner",
        "riachuelo", "c&a", "zara", "shopping",
    )),
    ("Alimentação", (
        "aliment", "comper", "atacad", "supermercado", "mercado", "padaria",
        "acougue", "hortifruti", "restaurante", "lanchonete", "pizzaria",
        "burger", "sushi", "ifood", "rappi", "cafe", "sorvete", "bebida",
        "sdb comercio", "carrefour", "assai", "extra", "pao de acucar",
    )),
    ("Juros", (
        "juros", "mora", "multa", "iof", "encargo", "rotativo", "anuidade",
        "tarifa bancaria", "taxa",
    )),
    ("Viagem", (
        "viagem", "hotel", "pousada", "airbnb", "passagem", "aerea", "latam",
        "gol ", "azul ", "decolar", "booking", "rodoviaria", "hospedagem",
    )),
    ("Educação", (
        "educac", "anhanguera", "faculdade", "universidade", "escola",
        "colegio", "curso", "livro", "livraria", "papelaria", "estacio",
        "unopar", "alura", "udemy",
    )),
    ("Saúde", (
        "farmacia", "drogaria", "raia", "drogasil", "pague menos", "panvel",
        "medico", "clinica", "hospital", "odonto", "dentista", "laboratorio",
        "exame", "saude", "consulta",
    )),
    ("Transporte", (
        "uber", "99app", "99pop", "posto ", "combustivel", "gasolina",
        "etanol", "diesel", "estacionamento", "pedagio", "sem parar",
        "onibus", "metro", "ipva", "detran",
    )),
    ("Moradia", (
        "aluguel", "condominio", "energia", "energisa", "enel", "cemig",
        "copel", "sabesp", "sanepar", "saneamento", "agua ", "iptu", "gas ",
    )),
    ("Lazer", (
        "cinema", "ingresso", "teatro", "show", "steam", "playstation",
        "xbox", "nintendo", "bar ", "pub ", "choperia", "lazer", "parque",
    )),
    ("Dívida", (
        "fatura", "emprestimo", "financiamento", "parcela", "vuoncard",
        "cartao", "consignado", "divida", "acordo", "boleto",
    )),
    ("Compras", (
        "compra no debito", "compra ", "loja",
    )),
    ("Transferência / Pix", (
        "transferencia", "pix", "ted ", "doc ", "instituicao de pagamento",
    )),
]


REGRAS_BANCO = [
    ("Nubank", ("nubank", "nu pagamentos", "nuconta", "roxinho")),
    ("Itaú Unibanco", ("itau", "itaú", "uniclass", "personnalite")),
    ("Bradesco", ("bradesco", "bco bradesco")),
    ("Banco do Brasil", ("banco do brasil", "bb ", "_bb_", "bb.csv", "bb.ofx")),
    ("Santander", ("santander", "bco santander")),
    ("Caixa Econômica Federal", ("caixa economica", "cef", "caixa.ofx", "caixa.csv")),
    ("Banco Inter", ("banco inter", "intermedium", "inter.csv", "inter.ofx")),
    ("C6 Bank", ("c6 bank", "c6bank", "c6.csv")),
    ("XP Investimentos", ("banco xp", "xp investimentos", "xp.csv")),
    ("BTG Pactual", ("btg pactual", "btg.csv")),
    ("Mercado Pago", ("mercado pago", "mercadopago")),
    ("PicPay", ("picpay",)),
    ("PagBank", ("pagbank", "pagseguro")),
    ("Sicredi", ("sicredi",)),
    ("Sicoob", ("sicoob",)),
    ("Banrisul", ("banrisul",)),
    ("Neon", ("neon",)),
    ("Safra", ("safra",)),
]


def inferir_banco(nome_arquivo, conteudo_bytes=b""):
    """Tenta identificar o banco pelo nome do arquivo ou cabeçalho do extrato."""
    amostra_texto = _decodificar(conteudo_bytes[:4096]) if conteudo_bytes else ""
    alvo = _normalizar_texto(f"{nome_arquivo or ''} {amostra_texto}")
    for banco, palavras in REGRAS_BANCO:
        if any(p in alvo for p in palavras):
            return banco
    return ""


def inferir_categoria(descricao, padrao="Outros"):
    """Classifica automaticamente uma descrição bancária em categoria útil."""
    norm = _normalizar_texto(descricao)
    if not norm:
        return padrao
    for categoria, palavras in REGRAS_CATEGORIA:
        if any(p in norm for p in palavras):
            return categoria
    return padrao


def _decodificar(conteudo_bytes):
    """Decodifica bytes de arquivos bancários brasileiros (UTF-8, BOM, CP1252, Latin-1)."""
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return conteudo_bytes.decode(enc)
        except UnicodeDecodeError:
            continue
    return conteudo_bytes.decode("latin-1", errors="replace")


def _normalizar_texto(texto):
    """Remove acentos e converte para minúsculas para casar nomes de colunas."""
    sem_acento = "".join(
        ch for ch in unicodedata.normalize("NFKD", texto or "")
        if not unicodedata.combining(ch)
    )
    return re.sub(r"\s+", " ", sem_acento).strip().lower()


def _parse_data(texto):
    """Converte datas em formatos comuns (DD/MM/YYYY, YYYY-MM-DD, YYYYMMDD) para ISO."""
    s = (texto or "").strip()
    if not s:
        return None
    s = s.split(" ")[0].split("T")[0]
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    m = re.match(r"^(\d{4})(\d{2})(\d{2})", s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date().isoformat()
        except ValueError:
            return None
    return None


def _parse_numero(texto):
    """Converte valor monetário BR/US, com sinal, parênteses ou sufixo D/C, para float."""
    if texto is None:
        return None
    s = str(texto).strip()
    if not s:
        return None

    negativo = False
    upper = s.upper()
    if upper.endswith("D") and re.search(r"\d", s):
        negativo = True
        s = s[:-1].strip()
    elif upper.endswith("C") and re.search(r"\d", s):
        s = s[:-1].strip()

    if s.startswith("(") and s.endswith(")"):
        negativo = True
        s = s[1:-1].strip()

    s = s.replace("R$", "").replace("BRL", "").replace(" ", "")
    if s.startswith("-"):
        negativo = True
        s = s[1:]
    elif s.startswith("+"):
        s = s[1:]

    if not re.match(r"^[\d.,]+$", s):
        return None

    if "," in s and "." in s:
        # Decide pelo último separador decimal
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        partes = s.split(",")
        if len(partes[-1]) <= 2:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "." in s:
        partes = s.split(".")
        if len(partes) > 2 or (len(partes) == 2 and len(partes[1]) == 3):
            s = s.replace(".", "")

    try:
        val = float(s)
    except ValueError:
        return None
    return -val if negativo else val


def _eh_resumo_saldo(descricao):
    norm = _normalizar_texto(descricao)
    return any(norm.startswith(p) or norm == p for p in IGNORAR_DESCRICOES)


def _detectar_parcela(descricao):
    """Detecta padrão de parcela como '02/10' ou 'PARC 2/6' na descrição."""
    m = re.search(r"(?:parc(?:ela)?\s*)?(\d{1,2})\s*/\s*(\d{1,2})\b", descricao, re.IGNORECASE)
    if not m:
        return 1, 1
    atual, total = int(m.group(1)), int(m.group(2))
    if 1 <= atual <= total <= 48 and total >= 2:
        return atual, total
    return 1, 1


def _montar_item(data_iso, valor_assinado, descricao, categoria_padrao=""):
    if not data_iso or valor_assinado is None or round(valor_assinado, 2) == 0:
        return None
    desc = re.sub(r"\s+", " ", (descricao or "")).strip() or "Movimentação bancária"
    if _eh_resumo_saldo(desc):
        return None
    tipo = "entrada" if valor_assinado > 0 else "saida"
    parcela_atual, total_parcelas = _detectar_parcela(desc)
    categoria = (categoria_padrao or "").strip() or inferir_categoria(desc)
    return {
        "data": data_iso,
        "tipo": tipo,
        "valor": round(abs(valor_assinado), 2),
        "descricao": desc[:140],
        "categoria": categoria,
        "parcela_atual": parcela_atual,
        "parcelas": total_parcelas,
    }


# ------------------------------------------------------------------- OFX ----
def _extrair_tag_ofx(bloco, tag):
    m = re.search(rf"<{tag}>([^\r\n<]+)", bloco, re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _parse_ofx(texto, categoria_padrao):
    blocos = re.findall(r"<STMTTRN>(.*?)(?:</STMTTRN>|(?=<STMTTRN>)|</BANKTRANLIST>)", texto, re.IGNORECASE | re.DOTALL)
    itens = []
    for bloco in blocos:
        data_iso = _parse_data(_extrair_tag_ofx(bloco, "DTPOSTED"))
        valor = _parse_numero(_extrair_tag_ofx(bloco, "TRNAMT"))
        trntype = _extrair_tag_ofx(bloco, "TRNTYPE").upper()
        nome = _extrair_tag_ofx(bloco, "NAME")
        memo = _extrair_tag_ofx(bloco, "MEMO")
        desc = memo if not nome else (nome if not memo or memo == nome else f"{nome} - {memo}")

        if valor is not None and valor > 0 and trntype in ("DEBIT", "PAYMENT", "FEE", "ATM", "POS", "CHECK"):
            valor = -valor

        item = _montar_item(data_iso, valor, desc, categoria_padrao)
        if item:
            itens.append(item)
    return itens


# ------------------------------------------------------------------- CSV ----
def _detectar_delimitador(linhas):
    amostra = "\n".join(linhas[:10])
    contagens = {d: amostra.count(d) for d in (";", ",", "\t")}
    melhor = max(contagens, key=contagens.get)
    return melhor if contagens[melhor] > 0 else ","


def _mapear_colunas(cabecalho):
    norm = [_normalizar_texto(c) for c in cabecalho]
    idx = {"data": None, "desc": None, "valor": None, "credito": None, "debito": None, "tipo": None, "cat": None}

    for i, col in enumerate(norm):
        if idx["data"] is None and any(k in col for k in ("data", "date", "dt ", "vencimento")):
            idx["data"] = i
        elif idx["desc"] is None and any(k in col for k in ("descri", "historic", "title", "lancamento", "estabelecimento", "memo", "detalhe", "nome", "favorecido")):
            idx["desc"] = i
        elif idx["credito"] is None and any(k in col for k in ("credito", "entrada", "receita")):
            idx["credito"] = i
        elif idx["debito"] is None and any(k in col for k in ("debito", "saida", "despesa")):
            idx["debito"] = i
        elif idx["valor"] is None and any(k in col for k in ("valor", "amount", "quantia", "vlr", "total")):
            idx["valor"] = i
        elif idx["tipo"] is None and any(k in col for k in ("tipo", "natureza", "operacao", "d/c")):
            idx["tipo"] = i
        elif idx["cat"] is None and any(k in col for k in ("categori", "tag", "etiqueta")):
            idx["cat"] = i

    return idx


def _parse_csv(texto, categoria_padrao):
    linhas = [l for l in texto.splitlines() if l.strip()]
    if not linhas:
        return []

    delim = _detectar_delimitador(linhas)
    leitor = list(csv.reader(io.StringIO("\n".join(linhas)), delimiter=delim))
    if not leitor:
        return []

    # Procura a linha de cabeçalho nas primeiras 15 linhas (alguns bancos colocam metadados no topo)
    inicio_dados = 0
    mapa = None
    for pos, row in enumerate(leitor[:15]):
        candidato = _mapear_colunas(row)
        if candidato["data"] is not None and (
            candidato["valor"] is not None or candidato["credito"] is not None or candidato["debito"] is not None
        ):
            mapa = candidato
            inicio_dados = pos + 1
            break

    itens = []
    if mapa is not None:
        for row in leitor[inicio_dados:]:
            if len(row) <= mapa["data"]:
                continue
            data_iso = _parse_data(row[mapa["data"]])
            if not data_iso:
                continue

            desc = row[mapa["desc"]] if (mapa["desc"] is not None and mapa["desc"] < len(row)) else ""
            cat = (
                row[mapa["cat"]].strip()
                if (mapa["cat"] is not None and mapa["cat"] < len(row) and row[mapa["cat"]].strip())
                else categoria_padrao
            )

            valor = None
            if mapa["valor"] is not None and mapa["valor"] < len(row):
                valor = _parse_numero(row[mapa["valor"]])
                if valor is not None and mapa["tipo"] is not None and mapa["tipo"] < len(row):
                    t_norm = _normalizar_texto(row[mapa["tipo"]])
                    if t_norm in ("d", "debito", "saida", "despesa") and valor > 0:
                        valor = -valor
                    elif t_norm in ("c", "credito", "entrada", "receita") and valor < 0:
                        valor = abs(valor)
            else:
                cred = _parse_numero(row[mapa["credito"]]) if (mapa["credito"] is not None and mapa["credito"] < len(row)) else None
                deb = _parse_numero(row[mapa["debito"]]) if (mapa["debito"] is not None and mapa["debito"] < len(row)) else None
                if cred and abs(cred) > 0:
                    valor = abs(cred)
                elif deb and abs(deb) > 0:
                    valor = -abs(deb)

            item = _montar_item(data_iso, valor, desc, cat)
            if item:
                itens.append(item)
        return itens

    # Fallback para CSV sem cabeçalho: procura coluna de data e coluna numérica em cada linha
    for row in leitor:
        data_iso = None
        valor = None
        textos = []
        for cel in row:
            if data_iso is None:
                d = _parse_data(cel)
                if d:
                    data_iso = d
                    continue
            num = _parse_numero(cel)
            if num is not None and valor is None:
                valor = num
                continue
            if cel.strip():
                textos.append(cel.strip())
        item = _montar_item(data_iso, valor, " - ".join(textos), categoria_padrao)
        if item:
            itens.append(item)
    return itens


# ------------------------------------------------------------ TXT e PDF ----
PADRAO_LINHA_EXTRATO = re.compile(
    r"^(?P<data>\d{2}[/-]\d{2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})\s+"
    r"(?P<desc>.+?)\s+"
    r"(?P<valor>[+-]?\s*(?:R\$\s*)?\(?[\d.,]+\)?\s*[DCdc]?)$"
)


def _parse_txt(texto, categoria_padrao):
    linhas = [l.strip() for l in texto.splitlines() if l.strip()]
    if not linhas:
        return []

    # Se parecer CSV/TSV estruturado, tenta primeiro como CSV
    delims_encontrados = sum(1 for l in linhas[:10] if l.count(";") >= 2 or l.count("\t") >= 2)
    if delims_encontrados >= 2:
        itens_csv = _parse_csv(texto, categoria_padrao)
        if itens_csv:
            return itens_csv

    itens = []
    for linha in linhas:
        m = PADRAO_LINHA_EXTRATO.match(linha)
        if not m:
            continue
        data_iso = _parse_data(m.group("data"))
        valor = _parse_numero(m.group("valor"))
        item = _montar_item(data_iso, valor, m.group("desc"), categoria_padrao)
        if item:
            itens.append(item)
    return itens


def _parse_pdf(conteudo_bytes, categoria_padrao):
    if PdfReader is None:
        raise RuntimeError("Leitura de PDF requer o pacote pypdf instalado no ambiente.")
    reader = PdfReader(io.BytesIO(conteudo_bytes))
    paginas = [page.extract_text() or "" for page in reader.pages]
    return _parse_txt("\n".join(paginas), categoria_padrao)


# ------------------------------------------------------------- entrada -----
def ler_extrato(nome_arquivo, conteudo_bytes, categoria_padrao=""):
    """Lê extrato (.ofx, .qfx, .csv, .txt, .pdf) e devolve lista de lançamentos."""
    nome = (nome_arquivo or "").lower().strip()
    cat = (categoria_padrao or "").strip()

    if nome.endswith(".pdf"):
        return _parse_pdf(conteudo_bytes, cat)

    texto = _decodificar(conteudo_bytes)
    if nome.endswith((".ofx", ".qfx")) or "<OFX>" in texto.upper() or "<STMTTRN>" in texto.upper():
        return _parse_ofx(texto, cat)
    if nome.endswith(".csv"):
        return _parse_csv(texto, cat)
    return _parse_txt(texto, cat)
