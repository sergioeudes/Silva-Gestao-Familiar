"""Regras de negócio que dependem do banco de dados. Nenhuma linha de Streamlit aqui."""
import datetime
import uuid
from collections import defaultdict

from dateutil.relativedelta import relativedelta
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError

from models import (BankAccount, Category, CreditCard, InvoicePayment,
                    Transaction, User)
from regras import (CAT_PAGTO_FATURA, CAT_TRANSFERENCIA, CATEGORIAS_PADRAO,
                    ENTRADA, KIND_NORMAL, KIND_PAGTO_FATURA,
                    KIND_TRANSFERENCIA, MAX_USUARIOS, SAIDA,
                    SERIE_PARCELADO, SERIE_RECORRENTE, competencia_do_vencimento,
                    formatar_cpf, gerar_vencimentos_fatura, limites_mes,
                    dividir_em_parcelas, validar_cpf, vencimento_no_mes)

T = Transaction


# ---------------------------------------------------------
# Categorias e usuários
# ---------------------------------------------------------
def garantir_categorias(db):
    existentes = {c.name for c in db.query(Category).all()}
    for nome, tipo in CATEGORIAS_PADRAO:
        if nome not in existentes:
            db.add(Category(name=nome, type=tipo))
    db.commit()


def obter_categoria(db, nome):
    cat = db.query(Category).filter(Category.name == nome).first()
    if cat is None:
        raise ValueError(f"Categoria não encontrada: {nome}")
    return cat


def cadastrar_usuario(db, nome, email, cpf):
    """Retorna (ok, mensagem)."""
    nome, email, cpf = (nome or "").strip(), (email or "").strip().lower(), (cpf or "").strip()
    if not (nome and email and cpf):
        return False, "Preencha todos os campos."
    if "@" not in email:
        return False, "E-mail inválido."
    if not validar_cpf(cpf):
        return False, "CPF inválido!"
    if db.query(User).count() >= MAX_USUARIOS:
        return False, f"Limite de {MAX_USUARIOS} usuários atingido."
    try:
        db.add(User(name=nome, email=email, cpf=formatar_cpf(cpf)))
        db.commit()
    except IntegrityError:
        db.rollback()
        return False, "E-mail ou CPF já cadastrado!"
    return True, f"Usuário {nome} criado!"


# ---------------------------------------------------------
# Criação de lançamentos
# ---------------------------------------------------------
def criar_lancamento_conta(db, *, user_id, account_id, category_id, descricao, valor_cents,
                           trans_type, metodo, data, pago, repeticoes=1):
    """Cria um lançamento em conta (único ou recorrente mensal).
    Somente a 1ª ocorrência pode nascer paga; as futuras ficam pendentes."""
    if valor_cents <= 0:
        raise ValueError("O valor deve ser maior que zero.")
    if repeticoes < 1:
        raise ValueError("A quantidade de meses deve ser pelo menos 1.")

    gid = str(uuid.uuid4()) if repeticoes > 1 else None
    criados = []
    for i in range(repeticoes):
        venc = data + relativedelta(months=i)  # sempre a partir da data original (31/jan -> 28/fev -> 31/mar)
        quitado = bool(pago) if i == 0 else False
        t = T(
            user_id=user_id, account_id=account_id, category_id=category_id,
            description=f"{descricao} ({i + 1}/{repeticoes})" if gid else descricao,
            amount_cents=valor_cents, trans_type=trans_type, method=metodo,
            kind=KIND_NORMAL, purchase_date=venc, due_date=venc,
            paid_date=venc if quitado else None, is_paid=quitado,
            group_id=gid, installment_no=i + 1 if gid else None,
            installment_total=repeticoes if gid else None,
            series_type=SERIE_RECORRENTE if gid else None,
        )
        db.add(t)
        criados.append(t)
    db.commit()
    return criados


def criar_lancamento_cartao(db, *, user_id, card, category_id, descricao, valor_cents,
                            modo, quantidade, data_compra):
    """modo = SERIE_PARCELADO (valor_cents é o TOTAL, dividido em parcelas exatas)
    ou SERIE_RECORRENTE (valor_cents é cobrado inteiro todo mês)."""
    if valor_cents <= 0:
        raise ValueError("O valor deve ser maior que zero.")
    if quantidade < 1:
        raise ValueError("A quantidade deve ser pelo menos 1.")

    vencimentos = gerar_vencimentos_fatura(card.due_day, card.closing_days_before, data_compra, quantidade)
    valores = dividir_em_parcelas(valor_cents, quantidade) if modo == SERIE_PARCELADO else [valor_cents] * quantidade
    gid = str(uuid.uuid4()) if quantidade > 1 else None

    criados = []
    for i, (venc, valor) in enumerate(zip(vencimentos, valores)):
        if modo == SERIE_PARCELADO:
            desc = f"{descricao} ({i + 1}/{quantidade})" if quantidade > 1 else descricao
            metodo = f"Cartão ({quantidade}x)" if quantidade > 1 else "Cartão (À vista)"
        else:
            desc = f"{descricao} (Recorrente {i + 1}/{quantidade})"
            metodo = "Cartão (Recorrente)"
        t = T(
            user_id=user_id, card_id=card.id, category_id=category_id,
            description=desc, amount_cents=valor, trans_type=SAIDA, method=metodo,
            kind=KIND_NORMAL, purchase_date=data_compra, due_date=venc,
            paid_date=None, is_paid=False,
            group_id=gid, installment_no=i + 1 if gid else None,
            installment_total=quantidade if gid else None,
            series_type=modo if gid else None,
        )
        db.add(t)
        criados.append(t)
    db.commit()
    return criados


def criar_transferencia(db, *, user_id, origem_id, destino_id, valor_cents, data, descricao=""):
    """Transferência entre contas do usuário: uma saída + uma entrada ligadas por group_id.
    Não entra em relatórios de receitas/despesas."""
    if valor_cents <= 0:
        raise ValueError("O valor deve ser maior que zero.")
    if origem_id == destino_id:
        raise ValueError("Origem e destino devem ser contas diferentes.")
    origem = db.get(BankAccount, origem_id)
    destino = db.get(BankAccount, destino_id)
    if origem is None or destino is None or origem.user_id != user_id or destino.user_id != user_id:
        raise ValueError("Conta inválida.")

    cat = obter_categoria(db, CAT_TRANSFERENCIA)
    gid = str(uuid.uuid4())
    base = descricao.strip() or "Transferência"
    comuns = dict(user_id=user_id, category_id=cat.id, amount_cents=valor_cents, method="Transferência",
                  kind=KIND_TRANSFERENCIA, purchase_date=data, due_date=data, paid_date=data,
                  is_paid=True, group_id=gid)
    db.add(T(account_id=origem.id, trans_type=SAIDA, description=f"{base} → {destino.name}", **comuns))
    db.add(T(account_id=destino.id, trans_type=ENTRADA, description=f"{base} ← {origem.name}", **comuns))
    db.commit()


# ---------------------------------------------------------
# Saldos e extrato
# ---------------------------------------------------------
def resumo_contas(db, user_id):
    """Lista de dicts: conta, inicial, entradas, saidas, atual (em centavos).
    Só lançamentos PAGOS afetam o saldo (inclui pagamentos de fatura e transferências)."""
    contas = db.query(BankAccount).filter(BankAccount.user_id == user_id).order_by(BankAccount.id).all()
    soma = defaultdict(int)
    rows = (
        db.query(T.account_id, T.trans_type, func.sum(T.amount_cents))
        .filter(T.user_id == user_id, T.account_id.isnot(None), T.is_paid.is_(True))
        .group_by(T.account_id, T.trans_type)
        .all()
    )
    for account_id, tipo, total in rows:
        soma[(account_id, tipo)] = int(total or 0)

    resultado = []
    for a in contas:
        ent, sai = soma[(a.id, ENTRADA)], soma[(a.id, SAIDA)]
        resultado.append({
            "conta": a, "inicial": a.initial_balance_cents,
            "entradas": ent, "saidas": sai,
            "atual": a.initial_balance_cents + ent - sai,
        })
    return resultado


def extrato_conta(db, conta):
    """Retorna (linhas, saldo_final) em ordem cronológica.
    Ordena pela data em que o dinheiro se movimentou (pagamento) ou, se pendente, pelo vencimento."""
    itens = db.query(T).filter(T.account_id == conta.id).all()

    def chave(t):
        return ((t.paid_date or t.due_date) if t.is_paid else t.due_date, t.id)

    itens.sort(key=chave)
    saldo = conta.initial_balance_cents
    linhas = []
    for t in itens:
        valor = t.amount_cents if t.trans_type == ENTRADA else -t.amount_cents
        if t.is_paid:
            saldo += valor
        linhas.append({"transacao": t, "valor": valor, "saldo": saldo})
    return linhas, saldo


# ---------------------------------------------------------
# Faturas de cartão
# ---------------------------------------------------------
def itens_fatura(db, card_id, ano, mes):
    ini, fim = limites_mes(ano, mes)
    return (db.query(T)
            .filter(T.card_id == card_id, T.due_date >= ini, T.due_date < fim)
            .order_by(T.purchase_date, T.id).all())


def pagamentos_da_fatura(db, card_id, ano, mes):
    return (db.query(InvoicePayment)
            .filter(InvoicePayment.card_id == card_id,
                    InvoicePayment.ref_year == ano, InvoicePayment.ref_month == mes)
            .order_by(InvoicePayment.paid_date, InvoicePayment.id).all())


def resumo_fatura(db, card, ano, mes):
    itens = itens_fatura(db, card.id, ano, mes)
    pagamentos = pagamentos_da_fatura(db, card.id, ano, mes)
    total = sum(i.amount_cents for i in itens)
    pago = sum(p.amount_cents for p in pagamentos)
    venc = itens[0].due_date if itens else vencimento_no_mes(card.due_day, ano, mes)
    return {
        "itens": itens, "pagamentos": pagamentos, "total": total, "pago": pago,
        "restante": max(total - pago, 0), "vencimento": venc,
        "fechamento": venc - datetime.timedelta(days=card.closing_days_before),
    }


def pagar_fatura(db, *, user_id, card, ano, mes, account_id, valor_cents, data, metodo):
    """Registra pagamento total ou parcial: cria a saída na conta (baixa o saldo)
    e o InvoicePayment que abate o valor devido da fatura."""
    resumo = resumo_fatura(db, card, ano, mes)
    conta = db.get(BankAccount, account_id)
    if conta is None or conta.user_id != user_id:
        raise ValueError("Conta inválida.")
    if valor_cents <= 0:
        raise ValueError("O valor deve ser maior que zero.")
    if valor_cents > resumo["restante"]:
        raise ValueError("O valor é maior que o saldo restante da fatura.")

    cat = obter_categoria(db, CAT_PAGTO_FATURA)
    comp_ano, comp_mes = competencia_do_vencimento(ano, mes, card.competence_offset)
    trans = T(
        user_id=user_id, account_id=account_id, category_id=cat.id,
        description=f"Pagamento fatura {card.name} ({comp_mes:02d}/{comp_ano})",
        amount_cents=valor_cents, trans_type=SAIDA, method=metodo,
        kind=KIND_PAGTO_FATURA, purchase_date=data, due_date=data,
        paid_date=data, is_paid=True,
    )
    db.add(trans)
    db.flush()
    db.add(InvoicePayment(card_id=card.id, ref_year=ano, ref_month=mes, account_id=account_id,
                          transaction_id=trans.id, amount_cents=valor_cents, paid_date=data))
    db.commit()
    return trans


def projecao_faturas(db, card, ano, mes, n=6):
    """Totais das próximas n faturas do cartão a partir do mês de VENCIMENTO (ano, mes).
    Retorna [{ano, mes, comp_ano, comp_mes, total, pago, restante}] (centavos)."""
    ini = datetime.date(ano, mes, 1)
    fim = ini + relativedelta(months=n)
    tot = defaultdict(int)
    for due, valor in (db.query(T.due_date, T.amount_cents)
                       .filter(T.card_id == card.id, T.due_date >= ini, T.due_date < fim).all()):
        tot[(due.year, due.month)] += valor
    pag = defaultdict(int)
    for y, m, valor in (db.query(InvoicePayment.ref_year, InvoicePayment.ref_month, InvoicePayment.amount_cents)
                        .filter(InvoicePayment.card_id == card.id).all()):
        pag[(y, m)] += valor

    saida = []
    for i in range(n):
        d = ini + relativedelta(months=i)
        total, pago = tot.get((d.year, d.month), 0), pag.get((d.year, d.month), 0)
        comp_ano, comp_mes = competencia_do_vencimento(d.year, d.month, card.competence_offset)
        saida.append({"ano": d.year, "mes": d.month, "comp_ano": comp_ano, "comp_mes": comp_mes,
                      "total": total, "pago": min(pago, total), "restante": max(total - pago, 0)})
    return saida


def _totais_por_fatura(db, user_id, ignorar_recorrencias_a_partir_de=None):
    """{(card_id, ano, mes): total_centavos}"""
    tot = defaultdict(int)
    rows = (db.query(T.card_id, T.due_date, T.amount_cents, T.series_type)
            .filter(T.user_id == user_id, T.card_id.isnot(None)).all())
    for card_id, due, valor, serie in rows:
        if (ignorar_recorrencias_a_partir_de and serie == SERIE_RECORRENTE
                and due >= ignorar_recorrencias_a_partir_de):
            continue
        tot[(card_id, due.year, due.month)] += valor
    return tot


def _pagamentos_por_fatura(db, user_id):
    pag = defaultdict(int)
    rows = (db.query(InvoicePayment.card_id, InvoicePayment.ref_year,
                     InvoicePayment.ref_month, InvoicePayment.amount_cents)
            .join(CreditCard, CreditCard.id == InvoicePayment.card_id)
            .filter(CreditCard.user_id == user_id).all())
    for card_id, ano, mes, valor in rows:
        pag[(card_id, ano, mes)] += valor
    return pag


def faturas_em_aberto(db, user_id):
    """Faturas com valor restante > 0, ordenadas por vencimento.
    ano/mes = mês do VENCIMENTO; comp_ano/comp_mes = mês de COMPETÊNCIA (nome da fatura)."""
    cartoes = {c.id: c for c in db.query(CreditCard).filter(CreditCard.user_id == user_id).all()}
    tot = _totais_por_fatura(db, user_id)
    pag = _pagamentos_por_fatura(db, user_id)
    resultado = []
    for (card_id, ano, mes), total in tot.items():
        restante = total - pag.get((card_id, ano, mes), 0)
        if restante > 0 and card_id in cartoes:
            card = cartoes[card_id]
            comp_ano, comp_mes = competencia_do_vencimento(ano, mes, card.competence_offset)
            resultado.append({
                "card": card, "ano": ano, "mes": mes, "comp_ano": comp_ano, "comp_mes": comp_mes, "total": total,
                "pago": pag.get((card_id, ano, mes), 0), "restante": restante,
                "vencimento": vencimento_no_mes(card.due_day, ano, mes),
            })
    resultado.sort(key=lambda f: (f["ano"], f["mes"], f["card"].id))
    return resultado


def limite_utilizado(db, user_id, hoje=None):
    """{card_id: centavos usados do limite}.
    Parcelas futuras consomem limite; recorrências futuras (assinaturas) não."""
    hoje = hoje or datetime.date.today()
    inicio_prox_mes = limites_mes(hoje.year, hoje.month)[1]
    tot = _totais_por_fatura(db, user_id, ignorar_recorrencias_a_partir_de=inicio_prox_mes)
    pag = _pagamentos_por_fatura(db, user_id)
    usado = defaultdict(int)
    for (card_id, ano, mes), total in tot.items():
        usado[card_id] += max(total - pag.get((card_id, ano, mes), 0), 0)
    return usado


# ---------------------------------------------------------
# Edição e exclusão
# ---------------------------------------------------------
def atualizar_lancamento(db, t, *, purchase_date, due_date, is_paid, category_id, descricao, valor_cents):
    """Atualiza campos editáveis (sem commit; o chamador faz o commit).
    is_paid=None mantém o status. Lançamentos de cartão nunca têm status próprio."""
    if valor_cents <= 0 or not descricao.strip():
        return False
    t.purchase_date = purchase_date
    t.due_date = due_date
    t.category_id = category_id
    t.description = descricao.strip()
    t.amount_cents = valor_cents
    if t.account_id is not None and is_paid is not None:
        if is_paid:
            t.paid_date = t.paid_date or due_date
            t.is_paid = True
        else:
            t.is_paid = False
            t.paid_date = None
    return True


def _fatura_tem_pagamento(db, t):
    return (db.query(InvoicePayment.id)
            .filter(InvoicePayment.card_id == t.card_id,
                    InvoicePayment.ref_year == t.due_date.year,
                    InvoicePayment.ref_month == t.due_date.month).first()) is not None


def excluir_lancamento(db, t, escopo="apenas"):
    """escopo: 'apenas' | 'proximas' | 'serie'. Retorna (excluidos, bloqueados).

    - Pagamento de fatura: remove também o InvoicePayment (a fatura volta a ficar em aberto).
    - Transferência: remove as duas pontas.
    - Item de cartão em fatura que já recebeu pagamento: bloqueado (estorne o pagamento antes).
    """
    alvos = [t]
    if t.kind == KIND_TRANSFERENCIA and t.group_id:
        alvos = db.query(T).filter(T.group_id == t.group_id, T.kind == KIND_TRANSFERENCIA).all()
    elif t.kind == KIND_NORMAL and t.group_id and escopo != "apenas":
        q = db.query(T).filter(T.group_id == t.group_id)
        if escopo == "proximas":
            q = q.filter(T.installment_no >= t.installment_no)
        alvos = q.all()

    excluidos = bloqueados = 0
    for alvo in alvos:
        if alvo.kind == KIND_PAGTO_FATURA:
            # um pagamento pode ter abatido mais de uma fatura (dados migrados): remove todos os abatimentos
            for pg in db.query(InvoicePayment).filter(InvoicePayment.transaction_id == alvo.id).all():
                db.delete(pg)
            db.flush()
        elif alvo.card_id is not None and _fatura_tem_pagamento(db, alvo):
            bloqueados += 1
            continue
        db.delete(alvo)
        excluidos += 1
    db.commit()
    return excluidos, bloqueados


# ---------------------------------------------------------
# Relatórios (dashboard)
# ---------------------------------------------------------
def resumo_mensal(db, user_id, ano, mes):
    """Indicadores do mês (em centavos).

    Realizado = pago com data de pagamento dentro do mês (fluxo de caixa; sem transferências).
    A pagar/receber = pendente com vencimento dentro do mês (+ faturas de cartão do mês)."""
    ini, fim = limites_mes(ano, mes)
    base = db.query(T).filter(T.user_id == user_id, T.account_id.isnot(None))

    def soma(*filtros):
        v = base.filter(*filtros).with_entities(func.coalesce(func.sum(T.amount_cents), 0)).scalar()
        return int(v or 0)

    realizado = [T.is_paid.is_(True), T.paid_date >= ini, T.paid_date < fim, T.kind != KIND_TRANSFERENCIA]
    pendente = [T.is_paid.is_(False), T.due_date >= ini, T.due_date < fim]

    a_pagar_faturas = sum(f["restante"] for f in faturas_em_aberto(db, user_id)
                          if (f["ano"], f["mes"]) == (ano, mes))
    return {
        "entradas_realizadas": soma(T.trans_type == ENTRADA, *realizado),
        "saidas_realizadas": soma(T.trans_type == SAIDA, *realizado),
        "a_receber": soma(T.trans_type == ENTRADA, *pendente),
        "a_pagar": soma(T.trans_type == SAIDA, *pendente) + a_pagar_faturas,
    }


def em_atraso(db, user_id, hoje=None):
    """Total de contas pendentes vencidas + faturas em aberto já vencidas (centavos)."""
    hoje = hoje or datetime.date.today()
    contas = (db.query(func.coalesce(func.sum(T.amount_cents), 0))
              .filter(T.user_id == user_id, T.account_id.isnot(None), T.is_paid.is_(False),
                      T.trans_type == SAIDA, T.due_date < hoje).scalar())
    faturas = sum(f["restante"] for f in faturas_em_aberto(db, user_id) if f["vencimento"] < hoje)
    return int(contas or 0) + faturas


def lancamentos_pendentes(db, user_id, ano, mes):
    """Lançamentos de conta ainda não pagos com vencimento até o fim do mês (inclui atrasados)."""
    _, fim = limites_mes(ano, mes)
    return (db.query(T)
            .filter(T.user_id == user_id, T.account_id.isnot(None), T.is_paid.is_(False), T.due_date < fim)
            .order_by(T.due_date, T.id).all())


def gastos_por_categoria(db, user_id, ano, mes):
    """[(categoria, centavos)] por mês de COMPETÊNCIA (contas e faturas; pago + previsto).
    Contas: mês do vencimento/data. Cartões: mês da competência da fatura, que depende da configuração
    do cartão (0 = mês do vencimento; 1 = mês anterior ao vencimento).
    Exclui pagamentos de fatura e transferências para não contar em duplicidade."""
    ini, fim = limites_mes(ano, mes)
    ini1, fim1 = ini + relativedelta(months=1), fim + relativedelta(months=1)
    offset = func.coalesce(CreditCard.competence_offset, 0)
    rows = (db.query(Category.name, func.sum(T.amount_cents))
            .select_from(T)
            .join(Category, Category.id == T.category_id)
            .outerjoin(CreditCard, CreditCard.id == T.card_id)
            .filter(T.user_id == user_id, T.trans_type == SAIDA, T.kind == KIND_NORMAL,
                    or_(and_(offset == 0, T.due_date >= ini, T.due_date < fim),
                        and_(offset == 1, T.due_date >= ini1, T.due_date < fim1)))
            .group_by(Category.name).all())
    return sorted(((nome, int(total)) for nome, total in rows), key=lambda x: -x[1])


def fluxo_anual(db, user_id, ano):
    """[{mes, entradas, saidas}] (centavos) por mês de vencimento, sem transferências nem pagamentos de fatura."""
    ini, fim = datetime.date(ano, 1, 1), datetime.date(ano + 1, 1, 1)
    meses = [{"mes": m, "entradas": 0, "saidas": 0} for m in range(1, 13)]
    rows = (db.query(T.due_date, T.trans_type, T.amount_cents)
            .filter(T.user_id == user_id, T.kind == KIND_NORMAL, T.due_date >= ini, T.due_date < fim).all())
    for due, tipo, valor in rows:
        meses[due.month - 1]["entradas" if tipo == ENTRADA else "saidas"] += valor
    return meses
