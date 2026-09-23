"""Modelos do banco de dados e criação do engine."""
import os

from sqlalchemy import (Boolean, Column, Date, ForeignKey, Integer, String,
                        create_engine, event)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.pool import StaticPool

from regras import KIND_NORMAL

# Banco NOVO (v2): não mexe no seu arquivo antigo "financas_familia.db".
DB_PATH = os.environ.get("FINANCAS_DB", "financas_v2.db")

Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    email = Column(String(100), unique=True, nullable=False)
    cpf = Column(String(14), unique=True, nullable=False)


class BankAccount(Base):
    __tablename__ = "bank_accounts"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(50), nullable=False)
    initial_balance_cents = Column(Integer, default=0, nullable=False)


class CreditCard(Base):
    __tablename__ = "credit_cards"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(50), nullable=False)
    limit_cents = Column(Integer, default=0, nullable=False)
    due_day = Column(Integer, default=10, nullable=False)
    closing_days_before = Column(Integer, default=7, nullable=False)
    # Competência da fatura: 0 = mês do vencimento (fatura que vence em 04/10 é "Outubro");
    # 1 = mês ANTERIOR ao vencimento (fatura que vence em 04/10 é "Setembro").
    competence_offset = Column(Integer, default=0, nullable=False)


class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True)
    name = Column(String(60), unique=True, nullable=False)
    type = Column(String(20), nullable=False)  # Entrada | Saída | Sistema


class Transaction(Base):
    """Um lançamento.

    - Lançamento de CONTA: account_id preenchido; due_date = data/vencimento;
      is_paid/paid_date indicam se o dinheiro já saiu/entrou.
    - Lançamento de CARTÃO: card_id preenchido; due_date = vencimento da FATURA
      (gravado na criação, não recalculado). Quem "paga" é o InvoicePayment.
    """
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    account_id = Column(Integer, ForeignKey("bank_accounts.id"), nullable=True)
    card_id = Column(Integer, ForeignKey("credit_cards.id"), nullable=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    description = Column(String(120), nullable=False)
    amount_cents = Column(Integer, nullable=False)  # sempre positivo
    trans_type = Column(String(20), nullable=False)  # Entrada | Saída
    method = Column(String(50), nullable=False)
    kind = Column(String(30), default=KIND_NORMAL, nullable=False)
    purchase_date = Column(Date, nullable=False)
    due_date = Column(Date, nullable=False, index=True)
    paid_date = Column(Date, nullable=True)
    is_paid = Column(Boolean, default=False, nullable=False)

    # Séries (parcelas / recorrências)
    group_id = Column(String(36), nullable=True, index=True)
    installment_no = Column(Integer, nullable=True)
    installment_total = Column(Integer, nullable=True)
    series_type = Column(String(20), nullable=True)

    account = relationship("BankAccount")
    card = relationship("CreditCard")
    category = relationship("Category")


class InvoicePayment(Base):
    """Pagamento (total ou parcial) de uma fatura de cartão."""
    __tablename__ = "invoice_payments"
    id = Column(Integer, primary_key=True)
    card_id = Column(Integer, ForeignKey("credit_cards.id"), nullable=False, index=True)
    ref_year = Column(Integer, nullable=False)   # ano do vencimento da fatura
    ref_month = Column(Integer, nullable=False)  # mês do vencimento da fatura
    account_id = Column(Integer, ForeignKey("bank_accounts.id"), nullable=False)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)
    amount_cents = Column(Integer, nullable=False)
    paid_date = Column(Date, nullable=False)


class Investment(Base):
    __tablename__ = "investments"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    bank_name = Column(String(50), nullable=False)
    product_name = Column(String(100), nullable=False)
    balance_cents = Column(Integer, default=0, nullable=False)
    yield_rate = Column(String(50), nullable=False, default="")


def criar_engine(url=None):
    url = url or f"sqlite:///{DB_PATH}"
    kwargs = {"connect_args": {"check_same_thread": False}}
    if ":memory:" in url:
        kwargs["poolclass"] = StaticPool  # mesma conexão (usado nos testes)
    engine = create_engine(url, **kwargs)

    @event.listens_for(engine, "connect")
    def _ativar_fk(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def _atualizar_esquema(engine):
    """Adiciona colunas novas em bancos criados por versões anteriores (sem perder dados)."""
    with engine.begin() as conn:
        colunas = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(credit_cards)")}
        if "competence_offset" not in colunas:
            conn.exec_driver_sql("ALTER TABLE credit_cards ADD COLUMN competence_offset INTEGER NOT NULL DEFAULT 0")


def init_db(engine):
    Base.metadata.create_all(engine)
    _atualizar_esquema(engine)
    return sessionmaker(bind=engine)
