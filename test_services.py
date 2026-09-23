"""Testes da camada de serviços com banco SQLite em memória.  Rodar: pytest -v"""
import datetime
from types import SimpleNamespace

import pytest

import services as sv
from models import (BankAccount, CreditCard, InvoicePayment, Transaction, User,
                    criar_engine, init_db)
from regras import (CAT_PAGTO_FATURA, ENTRADA, KIND_PAGTO_FATURA, SAIDA,
                    SERIE_PARCELADO, SERIE_RECORRENTE)

D = datetime.date


@pytest.fixture
def db():
    engine = criar_engine("sqlite:///:memory:")
    sessao = init_db(engine)()
    sv.garantir_categorias(sessao)
    yield sessao
    sessao.close()


@pytest.fixture
def cen(db):
    """Usuário com 2 contas (R$ 1.000,00 e R$ 0,00) e 1 cartão (vence dia 10, fecha 7 dias antes = dia 3)."""
    u = User(name="Teste", email="t@t.com", cpf="529.982.247-25")
    db.add(u)
    db.commit()
    conta = BankAccount(user_id=u.id, name="Banco A", initial_balance_cents=100000)
    conta2 = BankAccount(user_id=u.id, name="Banco B", initial_balance_cents=0)
    cartao = CreditCard(user_id=u.id, name="Cartão X", limit_cents=500000, due_day=10, closing_days_before=7)
    db.add_all([conta, conta2, cartao])
    db.commit()
    return SimpleNamespace(u=u, conta=conta, conta2=conta2, cartao=cartao,
                           cat=sv.obter_categoria(db, "Alimentação & Mercado"))


def compra_cartao(db, cen, valor, data, modo=SERIE_PARCELADO, qtd=1):
    return sv.criar_lancamento_cartao(db, user_id=cen.u.id, card=cen.cartao, category_id=cen.cat.id,
                                      descricao="Compra", valor_cents=valor, modo=modo,
                                      quantidade=qtd, data_compra=data)


def lanc_conta(db, cen, valor, data, tipo=SAIDA, pago=True, reps=1, conta=None):
    return sv.criar_lancamento_conta(db, user_id=cen.u.id, account_id=(conta or cen.conta).id,
                                     category_id=cen.cat.id, descricao="Lanc", valor_cents=valor,
                                     trans_type=tipo, metodo="PIX", data=data, pago=pago, repeticoes=reps)


def pagar(db, cen, valor, ano=2026, mes=1, data=D(2026, 1, 10)):
    return sv.pagar_fatura(db, user_id=cen.u.id, card=cen.cartao, ano=ano, mes=mes,
                           account_id=cen.conta.id, valor_cents=valor, data=data, metodo="PIX")


def saldo(db, cen, conta=None):
    conta = conta or cen.conta
    return next(c["atual"] for c in sv.resumo_contas(db, cen.u.id) if c["conta"].id == conta.id)


# ---------- Usuários ----------
def test_cadastrar_usuario(db):
    assert sv.cadastrar_usuario(db, "Ana", "ana@x.com", "529.982.247-25")[0]
    ok, msg = sv.cadastrar_usuario(db, "Outra", "outra@x.com", "529.982.247-25")
    assert not ok and "já cadastrado" in msg
    assert not sv.cadastrar_usuario(db, "Bob", "bob@x.com", "111.111.111-11")[0]
    assert not sv.cadastrar_usuario(db, "", "", "")[0]
    # o banco continua utilizável após o erro de integridade
    assert sv.cadastrar_usuario(db, "Bia", "bia@x.com", "52998224725")[0] is False  # mesmo CPF
    assert db.query(User).count() == 1


# ---------- Saldo (bug 1: pagamento de fatura precisa baixar o saldo) ----------
def test_saldo_desconta_pagamento_de_fatura(db, cen):
    compra_cartao(db, cen, 30000, D(2026, 1, 2))          # fatura de 10/01
    assert saldo(db, cen) == 100000                       # compra no cartão não mexe na conta
    pagar(db, cen, 30000)
    assert saldo(db, cen) == 70000
    assert sv.resumo_fatura(db, cen.cartao, 2026, 1)["restante"] == 0


# ---------- Pagamento parcial (bug 3) ----------
def test_pagamento_parcial_abate_o_valor_pago(db, cen):
    for _ in range(3):
        compra_cartao(db, cen, 10000, D(2026, 1, 2))
    assert sv.resumo_fatura(db, cen.cartao, 2026, 1)["total"] == 30000

    pagar(db, cen, 15000)
    f = sv.resumo_fatura(db, cen.cartao, 2026, 1)
    assert (f["pago"], f["restante"]) == (15000, 15000)      # nada de dinheiro "sumindo"
    assert saldo(db, cen) == 85000

    pagar(db, cen, 15000)
    assert sv.resumo_fatura(db, cen.cartao, 2026, 1)["restante"] == 0

    with pytest.raises(ValueError):
        pagar(db, cen, 1)                                    # já quitada


def test_nao_paga_mais_que_o_restante(db, cen):
    compra_cartao(db, cen, 10000, D(2026, 1, 2))
    with pytest.raises(ValueError):
        pagar(db, cen, 10001)
    with pytest.raises(ValueError):
        pagar(db, cen, 0)


# ---------- Parcelamento (bugs 2 e 4) ----------
def test_parcelamento_soma_exata_e_faturas_consecutivas(db, cen):
    criados = compra_cartao(db, cen, 10000, D(2026, 1, 15), qtd=3)
    assert [t.amount_cents for t in criados] == [3334, 3333, 3333]
    assert sum(t.amount_cents for t in criados) == 10000
    assert [t.due_date for t in criados] == [D(2026, 2, 10), D(2026, 3, 10), D(2026, 4, 10)]
    assert len({t.group_id for t in criados}) == 1
    assert criados[0].series_type == SERIE_PARCELADO
    assert [t.description for t in criados] == ["Compra (1/3)", "Compra (2/3)", "Compra (3/3)"]


def test_compra_a_vista_nao_gera_serie(db, cen):
    (t,) = compra_cartao(db, cen, 5000, D(2026, 1, 2))
    assert t.group_id is None and t.series_type is None and t.method == "Cartão (À vista)"


def test_recorrencia_em_conta_no_fim_do_mes(db, cen):
    criados = lanc_conta(db, cen, 5000, D(2026, 1, 31), reps=3)
    assert [t.due_date for t in criados] == [D(2026, 1, 31), D(2026, 2, 28), D(2026, 3, 31)]
    assert [t.is_paid for t in criados] == [True, False, False]   # só a 1ª nasce paga
    assert criados[0].paid_date == D(2026, 1, 31) and criados[1].paid_date is None


def test_valor_invalido_e_rejeitado(db, cen):
    with pytest.raises(ValueError):
        lanc_conta(db, cen, 0, D(2026, 1, 1))
    with pytest.raises(ValueError):
        compra_cartao(db, cen, -5, D(2026, 1, 1))


# ---------- Transferências ----------
def test_transferencia_move_saldo_e_fica_fora_dos_relatorios(db, cen):
    sv.criar_transferencia(db, user_id=cen.u.id, origem_id=cen.conta.id, destino_id=cen.conta2.id,
                           valor_cents=25000, data=D(2026, 1, 15))
    assert saldo(db, cen) == 75000
    assert saldo(db, cen, cen.conta2) == 25000
    assert sv.gastos_por_categoria(db, cen.u.id, 2026, 1) == []
    r = sv.resumo_mensal(db, cen.u.id, 2026, 1)
    assert r["entradas_realizadas"] == 0 and r["saidas_realizadas"] == 0

    with pytest.raises(ValueError):
        sv.criar_transferencia(db, user_id=cen.u.id, origem_id=cen.conta.id, destino_id=cen.conta.id,
                               valor_cents=100, data=D(2026, 1, 15))

    uma_ponta = db.query(Transaction).filter(Transaction.account_id == cen.conta.id).one()
    assert sv.excluir_lancamento(db, uma_ponta) == (2, 0)     # remove as duas pontas
    assert db.query(Transaction).count() == 0
    assert saldo(db, cen) == 100000


# ---------- Exclusões ----------
def test_excluir_pagamento_de_fatura_reabre_a_fatura(db, cen):
    compra_cartao(db, cen, 30000, D(2026, 1, 2))
    trans = pagar(db, cen, 30000)
    assert trans.kind == KIND_PAGTO_FATURA
    assert sv.excluir_lancamento(db, trans) == (1, 0)
    assert db.query(InvoicePayment).count() == 0
    assert sv.resumo_fatura(db, cen.cartao, 2026, 1)["restante"] == 30000
    assert saldo(db, cen) == 100000


def test_excluir_pagamento_que_abateu_duas_faturas(db, cen):
    """Dados migrados podem ter um único pagamento em conta abatendo mais de uma fatura."""
    compra_cartao(db, cen, 10000, D(2026, 1, 2))     # fatura de 10/01
    compra_cartao(db, cen, 20000, D(2026, 1, 15))    # fatura de 10/02
    cat = sv.obter_categoria(db, CAT_PAGTO_FATURA)
    pg = Transaction(user_id=cen.u.id, account_id=cen.conta.id, category_id=cat.id, description="Pagamento",
                     amount_cents=30000, trans_type=SAIDA, method="PIX", kind=KIND_PAGTO_FATURA,
                     purchase_date=D(2026, 2, 1), due_date=D(2026, 2, 1), paid_date=D(2026, 2, 1), is_paid=True)
    db.add(pg)
    db.flush()
    for mes, valor in ((1, 10000), (2, 20000)):
        db.add(InvoicePayment(card_id=cen.cartao.id, ref_year=2026, ref_month=mes, account_id=cen.conta.id,
                              transaction_id=pg.id, amount_cents=valor, paid_date=D(2026, 2, 1)))
    db.commit()
    assert sv.resumo_fatura(db, cen.cartao, 2026, 1)["restante"] == 0
    assert sv.resumo_fatura(db, cen.cartao, 2026, 2)["restante"] == 0

    assert sv.excluir_lancamento(db, pg) == (1, 0)
    assert db.query(InvoicePayment).count() == 0
    assert sv.resumo_fatura(db, cen.cartao, 2026, 1)["restante"] == 10000
    assert sv.resumo_fatura(db, cen.cartao, 2026, 2)["restante"] == 20000


def test_item_de_fatura_paga_nao_pode_ser_excluido(db, cen):
    (t,) = compra_cartao(db, cen, 30000, D(2026, 1, 2))
    pagar(db, cen, 10000)
    assert sv.excluir_lancamento(db, t) == (0, 1)
    assert db.query(Transaction).filter(Transaction.card_id == cen.cartao.id).count() == 1


def test_excluir_esta_e_proximas_da_serie(db, cen):
    criados = lanc_conta(db, cen, 1000, D(2026, 1, 10), reps=4)
    segunda = criados[1]
    assert sv.excluir_lancamento(db, segunda, "proximas") == (3, 0)
    restantes = db.query(Transaction).all()
    assert len(restantes) == 1 and restantes[0].installment_no == 1


def test_excluir_serie_inteira(db, cen):
    criados = lanc_conta(db, cen, 1000, D(2026, 1, 10), reps=4)
    assert sv.excluir_lancamento(db, criados[2], "serie") == (4, 0)
    assert db.query(Transaction).count() == 0


# ---------- Relatórios ----------
def test_gastos_por_categoria_nao_duplica_pagamento_de_fatura(db, cen):
    compra_cartao(db, cen, 30000, D(2026, 1, 2))
    pagar(db, cen, 30000)
    assert sv.gastos_por_categoria(db, cen.u.id, 2026, 1) == [("Alimentação & Mercado", 30000)]


def test_resumo_mensal_e_atraso(db, cen):
    lanc_conta(db, cen, 20000, D(2026, 1, 20), pago=False)       # conta pendente
    compra_cartao(db, cen, 30000, D(2026, 1, 2))                 # fatura de 10/01
    lanc_conta(db, cen, 7000, D(2026, 1, 5), tipo=ENTRADA, pago=True)

    r = sv.resumo_mensal(db, cen.u.id, 2026, 1)
    assert r["a_pagar"] == 50000
    assert r["entradas_realizadas"] == 7000 and r["saidas_realizadas"] == 0

    assert sv.em_atraso(db, cen.u.id, hoje=D(2026, 1, 5)) == 0
    assert sv.em_atraso(db, cen.u.id, hoje=D(2026, 1, 15)) == 30000   # só a fatura já venceu
    assert sv.em_atraso(db, cen.u.id, hoje=D(2026, 2, 15)) == 50000

    pagar(db, cen, 30000)
    r = sv.resumo_mensal(db, cen.u.id, 2026, 1)
    assert r["a_pagar"] == 20000 and r["saidas_realizadas"] == 30000


def test_limite_parcelas_consomem_e_recorrencias_futuras_nao(db, cen):
    compra_cartao(db, cen, 5000, D(2026, 1, 2), modo=SERIE_RECORRENTE, qtd=12)   # assinatura
    compra_cartao(db, cen, 30000, D(2026, 1, 2), modo=SERIE_PARCELADO, qtd=3)    # 3 x 10000
    hoje = D(2026, 1, 20)
    assert sv.limite_utilizado(db, cen.u.id, hoje)[cen.cartao.id] == 5000 + 30000
    pagar(db, cen, 15000)                                        # fatura de janeiro (5000 + 10000)
    assert sv.limite_utilizado(db, cen.u.id, hoje)[cen.cartao.id] == 20000


def test_competencia_mes_anterior_no_cartao(db, cen):
    """Cartão com competência = mês anterior ao vencimento: a fatura que vence em 10/10 é a de setembro."""
    cen.cartao.competence_offset = 1
    db.commit()
    (t,) = compra_cartao(db, cen, 30000, D(2026, 9, 1))            # fecha dia 3: entra na fatura que vence 10/09
    assert t.due_date == D(2026, 9, 10)
    lanc_conta(db, cen, 4000, D(2026, 9, 20), pago=False)          # conta: sempre pela data

    assert (("Alimentação & Mercado", 4000),) == tuple(sv.gastos_por_categoria(db, cen.u.id, 2026, 9))   # cartão só em agosto
    assert (("Alimentação & Mercado", 30000),) == tuple(sv.gastos_por_categoria(db, cen.u.id, 2026, 8))

    (f,) = sv.faturas_em_aberto(db, cen.u.id)
    assert (f["ano"], f["mes"], f["comp_ano"], f["comp_mes"]) == (2026, 9, 2026, 8)

    p = pagar(db, cen, 30000, ano=2026, mes=9, data=D(2026, 9, 10))
    assert "(08/2026)" in p.description                            # a descrição usa a competência


def test_competencia_padrao_e_o_mes_do_vencimento(db, cen):
    compra_cartao(db, cen, 30000, D(2026, 9, 1))
    assert sv.gastos_por_categoria(db, cen.u.id, 2026, 9) == [("Alimentação & Mercado", 30000)]
    assert sv.gastos_por_categoria(db, cen.u.id, 2026, 8) == []
    (f,) = sv.faturas_em_aberto(db, cen.u.id)
    assert (f["comp_ano"], f["comp_mes"]) == (2026, 9)


def test_projecao_de_faturas(db, cen):
    compra_cartao(db, cen, 30000, D(2026, 1, 15), qtd=3)          # 3 x 10000: faturas de fev, mar e abr
    compra_cartao(db, cen, 5000, D(2026, 1, 2))                   # fatura de jan
    pagar(db, cen, 4000)                                          # pagamento parcial da fatura de jan

    proj = sv.projecao_faturas(db, cen.cartao, 2026, 1, n=6)
    assert [(p["ano"], p["mes"]) for p in proj] == [(2026, m) for m in range(1, 7)]
    assert [p["total"] for p in proj] == [5000, 10000, 10000, 10000, 0, 0]
    assert (proj[0]["pago"], proj[0]["restante"]) == (4000, 1000)
    assert all(p["restante"] == p["total"] for p in proj[1:])

    cen.cartao.competence_offset = 1                              # competência = mês anterior ao vencimento
    db.commit()
    proj = sv.projecao_faturas(db, cen.cartao, 2026, 1, n=1)
    assert (proj[0]["comp_ano"], proj[0]["comp_mes"]) == (2025, 12)


def test_fluxo_anual(db, cen):
    lanc_conta(db, cen, 1000, D(2026, 3, 10), tipo=ENTRADA)
    lanc_conta(db, cen, 400, D(2026, 3, 12))
    compra_cartao(db, cen, 600, D(2026, 3, 1))                   # fatura de 10/03
    marco = sv.fluxo_anual(db, cen.u.id, 2026)[2]
    assert marco == {"mes": 3, "entradas": 1000, "saidas": 1000}


# ---------- Extrato e edição ----------
def test_extrato_ordena_por_data_de_pagamento_e_acumula_saldo(db, cen):
    lanc_conta(db, cen, 20000, D(2026, 1, 3))
    lanc_conta(db, cen, 50000, D(2026, 1, 5), tipo=ENTRADA)
    lanc_conta(db, cen, 10000, D(2026, 1, 10), pago=False)
    linhas, final = sv.extrato_conta(db, cen.conta)
    assert [l["saldo"] for l in linhas] == [80000, 130000, 130000]
    assert final == 130000


def test_atualizar_lancamento_status(db, cen):
    (t,) = lanc_conta(db, cen, 1000, D(2026, 1, 10), pago=False)
    campos = dict(purchase_date=t.purchase_date, due_date=t.due_date, category_id=t.category_id,
                  descricao="Novo nome", valor_cents=1500)
    assert sv.atualizar_lancamento(db, t, is_paid=True, **campos)
    assert t.is_paid and t.paid_date == D(2026, 1, 10) and t.amount_cents == 1500
    sv.atualizar_lancamento(db, t, is_paid=False, **campos)
    assert not t.is_paid and t.paid_date is None
    assert not sv.atualizar_lancamento(db, t, is_paid=None, **{**campos, "valor_cents": 0})

    (c,) = compra_cartao(db, cen, 1000, D(2026, 1, 2))
    sv.atualizar_lancamento(db, c, is_paid=True, **campos)       # cartão nunca ganha status próprio
    assert c.is_paid is False
