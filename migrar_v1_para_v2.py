#!/usr/bin/env python3
"""Migra o banco antigo (financas_familia.db) para o formato novo (financas_v2.db).

Uso:
    python migrar_v1_para_v2.py                       # origem/destino padrão
    python migrar_v1_para_v2.py ANTIGO.db NOVO.db
    python migrar_v1_para_v2.py --sobrescrever        # recria o destino se já existir

O banco antigo é aberto SOMENTE PARA LEITURA e nunca é alterado.

O que o script faz
  * Valores em reais (float) -> centavos inteiros.
  * Parcelas: recupera a data ORIGINAL da compra (no banco antigo, muitas parcelas guardam a
    data da própria parcela em purchase_date) e refaz a divisão do total em centavos exatos.
  * Faturas: cada item de cartão vai para a fatura CORRETA pela regra nova (o app antigo errava
    o mês nos cartões cujo vencimento - dias de fechamento <= 0). Itens cuja data foi editada
    à mão (fora do padrão da série) recebem a regra correta aplicada à data que você deixou.
  * Pagamentos de fatura: o vínculo é refeito ITEM A ITEM a partir do que estava marcado como
    pago, então cada item pago continua pago (agora na fatura correta).
  * Séries (parcelas/recorrências) recebem group_id, para excluir "esta e as próximas".
  * Saldo das contas: idêntico ao antigo (diferença apenas de centavos de arredondamento).
  * --competencia-anterior "NOME DO CARTÃO": cartões cuja fatura leva o nome do mês ANTERIOR ao
    vencimento (ex.: a que vence em 04/10 é a "de setembro"). Só muda o nome da fatura, não as datas.
"""
import argparse
import collections
import datetime
import re
import sqlite3
import sys
import uuid
from pathlib import Path

from dateutil.relativedelta import relativedelta

from regras import (CAT_PAGTO_FATURA, CATEGORIAS_PADRAO, KIND_NORMAL,
                    KIND_PAGTO_FATURA, MESES, SAIDA, SERIE_PARCELADO,
                    SERIE_RECORRENTE, TIPO_SISTEMA, dividir_em_parcelas,
                    gerar_vencimentos_fatura, reais_para_centavos,
                    vencimento_da_fatura, vencimento_no_mes)

D = datetime.date
PADRAO_PARCELA = re.compile(r"^(.*?)\s*\((Recorrente\s+)?(\d+)/(\d+)\)\s*$")
PADRAO_PAGAMENTO = re.compile(r"^pagamento fatura (.+) \(([^/()]+)/(\d{4})\)\s*$", re.I)
MES_NUM = {m.lower(): i + 1 for i, m in enumerate(MESES)}

DDL = """
CREATE TABLE users (
    id INTEGER NOT NULL, name VARCHAR(50) NOT NULL, email VARCHAR(100) NOT NULL, cpf VARCHAR(14) NOT NULL,
    PRIMARY KEY (id), UNIQUE (email), UNIQUE (cpf));
CREATE TABLE bank_accounts (
    id INTEGER NOT NULL, user_id INTEGER NOT NULL, name VARCHAR(50) NOT NULL, initial_balance_cents INTEGER NOT NULL,
    PRIMARY KEY (id), FOREIGN KEY(user_id) REFERENCES users (id));
CREATE TABLE credit_cards (
    id INTEGER NOT NULL, user_id INTEGER NOT NULL, name VARCHAR(50) NOT NULL, limit_cents INTEGER NOT NULL,
    due_day INTEGER NOT NULL, closing_days_before INTEGER NOT NULL, competence_offset INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (id), FOREIGN KEY(user_id) REFERENCES users (id));
CREATE TABLE categories (
    id INTEGER NOT NULL, name VARCHAR(60) NOT NULL, type VARCHAR(20) NOT NULL,
    PRIMARY KEY (id), UNIQUE (name));
CREATE TABLE transactions (
    id INTEGER NOT NULL, user_id INTEGER NOT NULL, account_id INTEGER, card_id INTEGER, category_id INTEGER NOT NULL,
    description VARCHAR(120) NOT NULL, amount_cents INTEGER NOT NULL, trans_type VARCHAR(20) NOT NULL,
    method VARCHAR(50) NOT NULL, kind VARCHAR(30) NOT NULL, purchase_date DATE NOT NULL, due_date DATE NOT NULL,
    paid_date DATE, is_paid BOOLEAN NOT NULL, group_id VARCHAR(36), installment_no INTEGER,
    installment_total INTEGER, series_type VARCHAR(20),
    PRIMARY KEY (id), FOREIGN KEY(user_id) REFERENCES users (id),
    FOREIGN KEY(account_id) REFERENCES bank_accounts (id), FOREIGN KEY(card_id) REFERENCES credit_cards (id),
    FOREIGN KEY(category_id) REFERENCES categories (id));
CREATE INDEX ix_transactions_user_id ON transactions (user_id);
CREATE INDEX ix_transactions_due_date ON transactions (due_date);
CREATE INDEX ix_transactions_group_id ON transactions (group_id);
CREATE TABLE invoice_payments (
    id INTEGER NOT NULL, card_id INTEGER NOT NULL, ref_year INTEGER NOT NULL, ref_month INTEGER NOT NULL,
    account_id INTEGER NOT NULL, transaction_id INTEGER, amount_cents INTEGER NOT NULL, paid_date DATE NOT NULL,
    PRIMARY KEY (id), FOREIGN KEY(card_id) REFERENCES credit_cards (id),
    FOREIGN KEY(account_id) REFERENCES bank_accounts (id), FOREIGN KEY(transaction_id) REFERENCES transactions (id));
CREATE INDEX ix_invoice_payments_card_id ON invoice_payments (card_id);
CREATE TABLE investments (
    id INTEGER NOT NULL, user_id INTEGER NOT NULL, bank_name VARCHAR(50) NOT NULL, product_name VARCHAR(100) NOT NULL,
    balance_cents INTEGER NOT NULL, yield_rate VARCHAR(50) NOT NULL,
    PRIMARY KEY (id), FOREIGN KEY(user_id) REFERENCES users (id));
"""


def brl(c):
    from regras import formatar_brl
    return formatar_brl(c)


def cents(v):
    return reais_para_centavos(repr(float(v)))


def data(s):
    return D.fromisoformat(str(s)[:10]) if s else None


def fatura_antiga(card, ref):
    """Réplica do cálculo do app ANTIGO (com o defeito), só para saber onde o item estava."""
    fech = card["due_day"] - card["closing_days_before"]
    if fech <= 0:
        fech += 30
    return ref + relativedelta(months=1) if ref.day >= fech else ref


# ---------------------------------------------------------
# Leitura do banco antigo
# ---------------------------------------------------------
def abrir_somente_leitura(caminho):
    con = sqlite3.connect(Path(caminho).resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def carregar(con):
    tabela = lambda n: [dict(r) for r in con.execute(f"select * from {n} order by id")]
    return {n: tabela(n) for n in ("users", "bank_accounts", "credit_cards", "categories", "investments", "transactions")}


# ---------------------------------------------------------
# Reconstrução de séries (parcelas / recorrências)
# ---------------------------------------------------------
def montar_cadeias(itens, dono):
    """Agrupa itens em cadeias (uma compra parcelada ou uma recorrência).
    `dono(t)` identifica cartão/conta. Marca t['_cadeia'], t['_i'], t['_n'], t['_rec'], t['_editado']."""
    grupos = collections.defaultdict(list)
    for t in itens:
        m = PADRAO_PARCELA.match(t["description"])
        if m:
            t["_base"], t["_rec"], t["_i"], t["_n"] = m.group(1).strip(), bool(m.group(2)), int(m.group(3)), int(m.group(4))
        else:
            t["_base"], t["_rec"], t["_i"], t["_n"] = t["description"], False, 1, 1
        t["_data"] = data(t["date"])
        grupos[(dono(t), t["category_name"], t["_base"], t["_n"], t["_rec"])].append(t)

    cadeias = []
    for grupo in grupos.values():
        locais = []
        for t in sorted(grupo, key=lambda x: (x["_i"], x["id"])):
            i, editado = t["_i"], False
            if i == 1:
                c = {"base": t["_data"], "itens": {}, "n": t["_n"], "rec": t["_rec"], "dono": dono(t)}
                locais.append(c)
            else:
                livres = [c for c in locais if i not in c["itens"]]
                exatas = [c for c in livres if c["base"] + relativedelta(months=i - 1) == t["_data"]]
                if exatas:
                    c = exatas[0]
                elif livres:
                    c, editado = livres[0], True          # data não bate: edição manual
                else:                                      # parcela sem a parcela 1
                    c = {"base": t["_data"] - relativedelta(months=i - 1), "itens": {}, "n": t["_n"],
                         "rec": t["_rec"], "dono": dono(t)}
                    locais.append(c)
            if t["_n"] == 1 and t.get("purchase_date") and data(t["purchase_date"]) != t["_data"]:
                editado = True                             # compra à vista com data de compra != data de lançamento
            c["itens"][i] = t
            t["_cadeia"], t["_editado"] = c, editado
        cadeias.extend(locais)
    return cadeias


def centavos_da_cadeia(c):
    """{i: centavos}. Parcelado: refaz a divisão do total em centavos exatos."""
    itens, n = c["itens"], max(c["n"], max(c["itens"]))
    valores = [x["amount"] for x in itens.values()]
    iguais = max(valores) - min(valores) < 1e-6
    if n > 1 and not c["rec"] and iguais:
        total = valores[0] * n * 100
        if abs(total - round(total)) <= 0.02:
            partes = dividir_em_parcelas(int(round(total)), n)
            return {i: partes[i - 1] for i in itens}
    return {i: cents(t["amount"]) for i, t in itens.items()}


# ---------------------------------------------------------
# Conversão
# ---------------------------------------------------------
def converter(antigo, hoje):
    R = {"avisos": [], "editados": [], "orfas": 0}
    contas = {a["id"]: a for a in antigo["bank_accounts"]}
    cartoes = {c["id"]: c for c in antigo["credit_cards"]}
    cat_id = {c["name"]: c["id"] for c in antigo["categories"]}
    tx = antigo["transactions"]

    # --- séries de cartão
    itens_cartao = [t for t in tx if t["card_id"]]
    cadeias_cartao = montar_cadeias(itens_cartao, lambda t: ("cartao", t["card_id"]))
    linhas_cartao = {}
    for c in cadeias_cartao:
        card = cartoes[c["dono"][1]]
        n = max(c["n"], max(c["itens"]))
        vencs = gerar_vencimentos_fatura(card["due_day"], card["closing_days_before"], c["base"], n)
        valores = centavos_da_cadeia(c)
        gid = str(uuid.uuid4()) if n > 1 else None
        for i, t in c["itens"].items():
            if t["_editado"]:
                # data editada à mão: respeita a data que você deixou e aplica a regra correta a ela
                venc = vencimento_da_fatura(card["due_day"], card["closing_days_before"], t["_data"])
                compra = data(t["purchase_date"]) or t["_data"]
                R["editados"].append(t)
            else:
                venc, compra = vencs[i - 1], c["base"]
            linhas_cartao[t["id"]] = dict(
                id=t["id"], user_id=t["user_id"], account_id=None, card_id=t["card_id"],
                category_id=cat_id[t["category_name"]], description=t["description"],
                amount_cents=valores[i], trans_type=SAIDA, method=t["method"], kind=KIND_NORMAL,
                purchase_date=compra, due_date=venc, paid_date=None, is_paid=0, group_id=gid,
                installment_no=i if gid else None, installment_total=n if gid else None,
                series_type=(SERIE_RECORRENTE if c["rec"] else SERIE_PARCELADO) if gid else None,
                _old_key=(lambda v: (v.year, v.month))(fatura_antiga(card, t["_data"])), _pago_antigo=bool(t["is_paid"]))

    # --- itens de conta
    itens_conta = [t for t in tx if t["account_id"]]
    pagamentos = [t for t in itens_conta if t["category_name"] == CAT_PAGTO_FATURA]
    normais = [t for t in itens_conta if t["category_name"] != CAT_PAGTO_FATURA]
    cadeias_conta = montar_cadeias(normais, lambda t: ("conta", t["account_id"], t["trans_type"]))
    linhas_conta = {}
    for c in cadeias_conta:
        n = max(c["n"], max(c["itens"]))
        gid = str(uuid.uuid4()) if n > 1 else None
        for i, t in c["itens"].items():
            venc = t["_data"]
            pago = bool(t["is_paid"])
            linhas_conta[t["id"]] = dict(
                id=t["id"], user_id=t["user_id"], account_id=t["account_id"], card_id=None,
                category_id=cat_id[t["category_name"]], description=t["description"],
                amount_cents=cents(t["amount"]), trans_type=t["trans_type"], method=t["method"], kind=KIND_NORMAL,
                purchase_date=data(t["purchase_date"]) or venc, due_date=venc, paid_date=venc if pago else None,
                is_paid=int(pago), group_id=gid, installment_no=i if gid else None,
                installment_total=n if gid else None, series_type=SERIE_RECORRENTE if gid else None)
    for t in pagamentos:
        d = data(t["date"])
        linhas_conta[t["id"]] = dict(
            id=t["id"], user_id=t["user_id"], account_id=t["account_id"], card_id=None,
            category_id=cat_id[t["category_name"]], description=t["description"], amount_cents=cents(t["amount"]),
            trans_type=SAIDA, method=t["method"], kind=KIND_PAGTO_FATURA, purchase_date=d, due_date=d, paid_date=d,
            is_paid=1, group_id=None, installment_no=None, installment_total=None, series_type=None)

    # --- ajuste de centavos: os pagamentos de fatura viraram centavos arredondados um a um;
    #     compensa o resíduo no maior pagamento da conta para o saldo bater com o antigo.
    R["ajustes"] = []
    for a in antigo["bank_accounts"]:
        alvo = cents((a["initial_balance"] or 0) + sum(
            t["amount"] if t["trans_type"] == "Entrada" else -t["amount"]
            for t in tx if t["account_id"] == a["id"] and t["is_paid"]))
        proprias = [l for l in linhas_conta.values() if l["account_id"] == a["id"] and l["is_paid"]]
        atual = cents(a["initial_balance"] or 0) + sum(
            l["amount_cents"] if l["trans_type"] == "Entrada" else -l["amount_cents"] for l in proprias)
        dif = alvo - atual
        pagtos = [l for l in proprias if l["kind"] == KIND_PAGTO_FATURA]
        if dif and pagtos:
            maior = max(pagtos, key=lambda l: l["amount_cents"])
            maior["amount_cents"] -= dif
            R["ajustes"].append((a["bank_name"], dif, maior["id"]))

    # --- vínculo dos pagamentos de fatura (item a item)
    nome2card = {c["card_name"].strip().upper(): c["id"] for c in antigo["credit_cards"]}
    pagos_por_rotulo = collections.defaultdict(list)
    for l in linhas_cartao.values():
        if l["_pago_antigo"]:
            pagos_por_rotulo[(l["card_id"],) + l["_old_key"]].append(l)
    pecas, sem_vinculo, usados = [], [], set()
    for p in sorted(pagamentos, key=lambda x: (x["date"], x["id"])):
        m = PADRAO_PAGAMENTO.match(p["description"])
        card_id = nome2card.get(m.group(1).strip().upper()) if m else None
        mes = MES_NUM.get(m.group(2).strip().lower()) if m else None
        rotulo = (card_id, int(m.group(3)), mes) if card_id and mes else None
        if rotulo is None or rotulo not in pagos_por_rotulo or rotulo in usados:
            sem_vinculo.append(p)
            continue
        usados.add(rotulo)
        por_fatura = collections.defaultdict(int)
        for l in pagos_por_rotulo[rotulo]:
            por_fatura[(l["due_date"].year, l["due_date"].month)] += l["amount_cents"]
        for (ano, mes_f), valor in sorted(por_fatura.items()):
            pecas.append(dict(card_id=card_id, ref_year=ano, ref_month=mes_f, account_id=p["account_id"],
                              transaction_id=p["id"], amount_cents=valor, paid_date=data(p["date"])))
    R["itens_pagos_sem_pagamento"] = [l for k, ls in pagos_por_rotulo.items() if k not in usados for l in ls]
    R["pagamentos_sem_vinculo"] = sem_vinculo

    todas = {**linhas_cartao, **linhas_conta}
    R.update(linhas=[todas[k] for k in sorted(todas)], pecas=pecas, cadeias_cartao=cadeias_cartao)
    return R


# ---------------------------------------------------------
# Escrita do banco novo
# ---------------------------------------------------------
def escrever(destino, antigo, R):
    con = sqlite3.connect(destino)
    con.executescript(DDL)
    ins = lambda sql, rows: con.executemany(sql, rows)
    ins("insert into users values (?,?,?,?)", [(u["id"], u["name"], u["email"], u["cpf"]) for u in antigo["users"]])
    ins("insert into bank_accounts values (?,?,?,?)",
        [(a["id"], a["user_id"], a["bank_name"], cents(a["initial_balance"] or 0)) for a in antigo["bank_accounts"]])
    anteriores = R.get("competencia_anterior", set())
    ins("insert into credit_cards values (?,?,?,?,?,?,?)",
        [(c["id"], c["user_id"], c["card_name"], cents(c["credit_limit"] or 0), c["due_day"], c["closing_days_before"],
          1 if c["card_name"].strip().upper() in anteriores else 0)
         for c in antigo["credit_cards"]])

    categorias = [(c["id"], c["name"], TIPO_SISTEMA if c["name"] == CAT_PAGTO_FATURA else c["type"])
                  for c in antigo["categories"]]
    existentes = {c[1] for c in categorias}
    proximo = max(c[0] for c in categorias) + 1
    for nome, tipo in CATEGORIAS_PADRAO:
        if nome not in existentes:
            categorias.append((proximo, nome, tipo))
            proximo += 1
    ins("insert into categories values (?,?,?)", categorias)

    cols = ["id", "user_id", "account_id", "card_id", "category_id", "description", "amount_cents", "trans_type",
            "method", "kind", "purchase_date", "due_date", "paid_date", "is_paid", "group_id", "installment_no",
            "installment_total", "series_type"]
    linhas = [tuple(l[c].isoformat() if isinstance(l[c], D) else l[c] for c in cols) for l in R["linhas"]]
    ins(f"insert into transactions ({','.join(cols)}) values ({','.join('?' * len(cols))})", linhas)
    ins("insert into invoice_payments (card_id,ref_year,ref_month,account_id,transaction_id,amount_cents,paid_date) "
        "values (?,?,?,?,?,?,?)",
        [(p["card_id"], p["ref_year"], p["ref_month"], p["account_id"], p["transaction_id"], p["amount_cents"],
          p["paid_date"].isoformat()) for p in R["pecas"]])
    ins("insert into investments values (?,?,?,?,?,?)",
        [(i["id"], i["user_id"], i["bank_name"], i["product_name"], cents(i["balance"]), i["yield_rate"] or "")
         for i in antigo["investments"]])
    con.commit()
    con.execute("PRAGMA foreign_keys=ON")
    violacoes = con.execute("PRAGMA foreign_key_check").fetchall()
    con.close()
    return violacoes


# ---------------------------------------------------------
# Verificação e relatório
# ---------------------------------------------------------
def verificar(antigo, destino, R, hoje):
    L = []
    p = L.append
    con = sqlite3.connect(destino)
    q = lambda s, *a: con.execute(s, a).fetchall()
    falhas = []

    p("=" * 78 + "\nRELATÓRIO DE MIGRAÇÃO\n" + "=" * 78)
    p(f"\nRegistros: usuários {len(antigo['users'])}, contas {len(antigo['bank_accounts'])}, "
      f"cartões {len(antigo['credit_cards'])}, investimentos {len(antigo['investments'])}, "
      f"lançamentos {len(antigo['transactions'])}")
    n_novo = q("select count(*) from transactions")[0][0]
    p(f"Lançamentos no banco novo: {n_novo}  -> {'OK' if n_novo == len(antigo['transactions']) else 'DIVERGE'}")
    if n_novo != len(antigo['transactions']):
        falhas.append("contagem de lançamentos")

    # 1) saldos por conta
    p("\n--- 1) SALDO POR CONTA (antigo x novo) ---")
    for a in antigo["bank_accounts"]:
        old = (a["initial_balance"] or 0) + sum(t["amount"] if t["trans_type"] == "Entrada" else -t["amount"]
                                               for t in antigo["transactions"] if t["account_id"] == a["id"] and t["is_paid"])
        ini = q("select initial_balance_cents from bank_accounts where id=?", a["id"])[0][0]
        mov = q("""select coalesce(sum(case when trans_type='Entrada' then amount_cents else -amount_cents end),0)
                   from transactions where account_id=? and is_paid=1""", a["id"])[0][0]
        novo = ini + mov
        dif = novo - cents(old)
        ok = abs(dif) <= 10
        p(f"  {a['bank_name'][:30]:<30} antigo {brl(cents(old)):>16}  novo {brl(novo):>16}  dif {dif:>4} cent  {'OK' if ok else 'DIVERGE'}")
        if not ok:
            falhas.append(f"saldo {a['bank_name']}")

    # 2) cartões: total de itens e pagamentos
    p("\n--- 2) CARTÕES: itens x pagamentos ---")
    for c in antigo["credit_cards"]:
        old_tot = sum(t["amount"] for t in antigo["transactions"] if t["card_id"] == c["id"])
        old_pago = sum(t["amount"] for t in antigo["transactions"] if t["card_id"] == c["id"] and t["is_paid"])
        tot = q("select coalesce(sum(amount_cents),0) from transactions where card_id=?", c["id"])[0][0]
        pago = q("select coalesce(sum(amount_cents),0) from invoice_payments where card_id=?", c["id"])[0][0]
        d1, d2 = tot - cents(old_tot), pago - cents(old_pago)
        ok = abs(d1) <= 50 and abs(d2) <= 50
        p(f"  {c['card_name'][:24]:<24} itens {brl(tot):>14} (dif {d1:>4}c)   pago {brl(pago):>14} (dif {d2:>4}c)  {'OK' if ok else 'DIVERGE'}")
        if not ok:
            falhas.append(f"cartão {c['card_name']}")

    # 3) pagamento (conta) x peças (fatura)
    dif_pg = q("""select p.id, p.amount_cents, coalesce(sum(i.amount_cents),0) from transactions p
                  left join invoice_payments i on i.transaction_id=p.id
                  where p.kind='pagamento_fatura' group by p.id""")
    maior = max((abs(a - b) for _, a, b in dif_pg if b), default=0)
    p(f"\n--- 3) Pagamentos de fatura: {len(dif_pg)} lançamentos; maior diferença entre o valor debitado na conta "
      f"e o valor abatido nas faturas: {maior} centavo(s)")

    # 4) itens
    p("\n--- 4) INVESTIMENTOS ---")
    old_inv = sum(i["balance"] for i in antigo["investments"])
    novo_inv = q("select coalesce(sum(balance_cents),0) from investments")[0][0]
    p(f"  antigo {brl(cents(old_inv))}  novo {brl(novo_inv)}  {'OK' if abs(novo_inv - cents(old_inv)) <= 10 else 'DIVERGE'}")

    # 5) séries
    grupos = q("select count(distinct group_id) from transactions where group_id is not null")[0][0]
    p(f"\n--- 5) SÉRIES: {grupos} séries (parcelas/recorrências) reconstruídas")

    # 6) impacto da correção de faturas
    p("\n--- 6) FATURAS: itens que MUDARAM de mês em relação ao app antigo (regra corrigida) ---")
    muda = collections.Counter(); total = collections.Counter()
    for l in R["linhas"]:
        if l["card_id"]:
            total[l["card_id"]] += 1
            if (l["due_date"].year, l["due_date"].month) != l["_old_key"]:
                muda[l["card_id"]] += 1
    nomes = {c["id"]: c["card_name"] for c in antigo["credit_cards"]}
    for cid in nomes:
        p(f"  {nomes[cid][:26]:<26} {muda[cid]:>4} de {total[cid]:>4} itens")

    p(f"\n--- 7) ITENS COM DATA EDITADA À MÃO (regra aplicada à data que você deixou; vale conferir): {len(R['editados'])} ---")
    for t in R["editados"][:15]:
        p(f"  id {t['id']}: {t['description'][:40]} (data {t['date']})")

    # 8) situação das faturas no app novo
    p(f"\n--- 8) FATURAS EM ABERTO NO APP NOVO (hoje {hoje:%d/%m/%Y}) ---")
    rows = q("""select t.card_id, t.due_date, sum(t.amount_cents) from transactions t where t.card_id is not null
                group by t.card_id, substr(t.due_date,1,7)""")
    pag = collections.defaultdict(int)
    for cid, ano, mes, v in q("select card_id, ref_year, ref_month, amount_cents from invoice_payments"):
        pag[(cid, ano, mes)] += v
    tot = collections.defaultdict(int)
    for cid, due, v in q("select card_id, due_date, amount_cents from transactions where card_id is not null"):
        d = data(due); tot[(cid, d.year, d.month)] += v
    abertas = collections.defaultdict(lambda: [0, 0, 0, 0])
    for (cid, ano, mes), v in tot.items():
        rest = v - pag.get((cid, ano, mes), 0)
        if rest > 0:
            a = abertas[cid]
            venc_c = next(c for c in antigo["credit_cards"] if c["id"] == cid)
            vencida = vencimento_no_mes(venc_c["due_day"], ano, mes) < hoje
            a[0] += 1; a[1] += rest
            if vencida:
                a[2] += 1; a[3] += rest
    p(f"  {'cartão':<26} {'faturas abertas':>15} {'restante':>15} {'já vencidas':>12} {'valor vencido':>16}")
    for cid in nomes:
        a = abertas[cid]
        p(f"  {nomes[cid][:26]:<26} {a[0]:>15} {brl(a[1]):>15} {a[2]:>12} {brl(a[3]):>16}")
    vencido_total = sum(a[3] for a in abertas.values())
    p(f"  TOTAL VENCIDO E NÃO PAGO EM CARTÕES: {brl(vencido_total)}")

    p("\n--- 9) PONTOS QUE PRECISAM DA SUA ATENÇÃO ---")
    if R.get("competencia_anterior"):
        p("  * Competência = mês ANTERIOR ao vencimento nos cartões: " + ", ".join(sorted(R["competencia_anterior"])))
    for nome, dif, tid in R["ajustes"]:
        p(f"  * Ajuste de {dif:+d} centavo(s) no pagamento de fatura #{tid} da conta {nome} (compensa arredondamentos).")
    if R["pagamentos_sem_vinculo"]:
        for t in R["pagamentos_sem_vinculo"]:
            p(f"  * Pagamento sem vínculo automático (id {t['id']}, {t['date']}, {brl(cents(t['amount']))}): "
              f"'{t['description']}'. Mantido como saída na conta (saldo correto), mas NÃO abate nenhuma fatura.")
    else:
        p("  * Todos os pagamentos de fatura foram vinculados.")
    p(f"  * Itens marcados como pagos sem pagamento correspondente: {len(R['itens_pagos_sem_pagamento'])}")
    if falhas:
        p("\n!!! FALHAS NA VERIFICAÇÃO: " + ", ".join(falhas))
    else:
        p("\nVerificação numérica: TUDO OK")
    con.close()
    return "\n".join(L), falhas


def verificar_com_sqlalchemy(destino, hoje):
    """Se SQLAlchemy estiver instalado, confere se o app novo lê o banco migrado e mostra os mesmos números."""
    try:
        import services as sv
        from models import User, criar_engine, init_db
    except Exception as e:  # noqa: BLE001
        return f"(verificação via SQLAlchemy ignorada: {type(e).__name__})"
    try:
        engine = criar_engine(f"sqlite:///{Path(destino).resolve().as_posix()}")
        with init_db(engine)() as db:
            linhas = ["\n--- 10) VERIFICAÇÃO PELO CÓDIGO DO APP NOVO (SQLAlchemy) ---"]
            for u in db.query(User).order_by(User.id):
                total = sum(c["atual"] for c in sv.resumo_contas(db, u.id))
                fat = sv.faturas_em_aberto(db, u.id)
                linhas.append(f"  {u.name[:30]:<30} saldo contas {brl(total):>16} | faturas em aberto {len(fat)} "
                              f"({brl(sum(f['restante'] for f in fat))}) | em atraso {brl(sv.em_atraso(db, u.id, hoje))}")
        return "\n".join(linhas)
    except Exception as e:  # noqa: BLE001
        return f"\n!!! O app novo NÃO conseguiu ler o banco migrado: {type(e).__name__}: {e}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("origem", nargs="?", default="financas_familia.db")
    ap.add_argument("destino", nargs="?", default="financas_v2.db")
    ap.add_argument("--sobrescrever", action="store_true")
    ap.add_argument("--competencia-anterior", action="append", default=[], metavar="CARTAO",
                    help="nome EXATO de um cartão cuja competência é o mês ANTERIOR ao vencimento "
                         "(a fatura que vence em 04/10 é a de setembro). Pode repetir a opção.")
    ap.add_argument("--hoje", help="data de referência AAAA-MM-DD (padrão: hoje)")
    args = ap.parse_args()

    hoje = data(args.hoje) if args.hoje else D.today()
    origem, destino = Path(args.origem), Path(args.destino)
    if not origem.exists():
        sys.exit(f"Banco antigo não encontrado: {origem}")
    if destino.exists():
        if not args.sobrescrever:
            sys.exit(f"{destino} já existe. Use --sobrescrever para recriá-lo (o banco antigo nunca é alterado).")
        destino.unlink()

    antigo = carregar(abrir_somente_leitura(origem))
    R = converter(antigo, hoje)
    nomes_cartoes = {c["card_name"].strip().upper() for c in antigo["credit_cards"]}
    anteriores = {n.strip().upper() for n in args.competencia_anterior}
    if anteriores - nomes_cartoes:
        sys.exit(f"Cartão não encontrado: {sorted(anteriores - nomes_cartoes)}. Nomes existentes: {sorted(nomes_cartoes)}")
    R["competencia_anterior"] = anteriores
    violacoes = escrever(str(destino), antigo, R)
    if violacoes:
        sys.exit(f"Chaves estrangeiras inválidas no banco novo: {violacoes[:5]}")
    relatorio, falhas = verificar(antigo, str(destino), R, hoje)
    relatorio += verificar_com_sqlalchemy(str(destino), hoje)
    print(relatorio)
    (destino.parent / "relatorio_migracao.txt").write_text(relatorio, encoding="utf-8")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
