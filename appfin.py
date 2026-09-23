"""Gestão Financeira Familiar — interface Streamlit.

Rodar:  streamlit run appfin.py
"""
import datetime
import os

import pandas as pd
import plotly.express as px
import streamlit as st
from dateutil.relativedelta import relativedelta

import regras as rg
import services as sv
import visual
from models import (DB_PATH, BankAccount, Category, CreditCard, Investment,
                    Transaction, User, criar_engine, init_db)

st.set_page_config(page_title="Gestão Financeira Familiar", page_icon="💻", layout="wide",
                  initial_sidebar_state="collapsed")

st.markdown(visual.CSS, unsafe_allow_html=True)

# ---------------------------------------------------------
# Infra: banco, mensagens e conversões
# ---------------------------------------------------------
@st.cache_resource
def get_session_factory():
    engine = criar_engine()
    factory = init_db(engine)
    with factory() as db:
        sv.garantir_categorias(db)
    return factory


def flash(msg, tipo="success"):
    """Mensagem que sobrevive ao st.rerun() (tipo: success | warning | error | info)."""
    st.session_state["_flash"] = (tipo, msg)


def mostrar_flash():
    f = st.session_state.pop("_flash", None)
    if f:
        tipo, msg = f
        if tipo == "success":
            st.toast(msg, icon="✅")          # some sozinho; erros e avisos continuam fixos na tela
        else:
            getattr(st, tipo)(msg)


COMPETENCIA_POR_ROTULO = {rotulo: off for off, rotulo in rg.OPCOES_COMPETENCIA.items()}


def brl(centavos):
    return rg.formatar_brl(centavos)


def reais(centavos):
    return rg.centavos_para_reais(centavos)


def cents(valor):
    """Valor de célula/campo (reais) -> centavos. Célula vazia vira 0."""
    return 0 if valor is None or pd.isna(valor) else rg.reais_para_centavos(valor)


def to_date(v, fallback):
    if v is None or pd.isna(v):
        return fallback
    if isinstance(v, pd.Timestamp):
        return v.date()
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    return pd.to_datetime(v).date()


def fmt_data(d):
    return d.strftime("%d/%m/%Y") if d else "-"


def kpi_em(container, *args, **kwargs):
    """Desenha um cartão de indicador (visual.kpi) em `container` (st, uma coluna, etc.)."""
    container.markdown(visual.kpi(*args, **kwargs), unsafe_allow_html=True)


def figura_base(fig, altura=340):
    """Padrão dos gráficos: margens enxutas e separadores brasileiros (1.234,56)."""
    fig.update_layout(margin=dict(l=10, r=10, t=20, b=10), legend_title_text="", separators=",.", height=altura)
    return fig


def init_state():
    hoje = datetime.date.today()
    st.session_state.setdefault("ultima_data_lancamento", hoje)
    st.session_state.setdefault("ultimo_cartao_id", None)
    st.session_state.setdefault("ultima_conta_id", None)


def indice_de(ids, alvo):
    return ids.index(alvo) if alvo in ids else 0


# ---------------------------------------------------------
# Barra lateral
# ---------------------------------------------------------
def sidebar_usuarios(db):
    st.sidebar.title("👨‍👩‍👧‍👦 Família & Acesso")
    users = db.query(User).order_by(User.id).all()
    st.sidebar.write(f"**Usuários cadastrados:** {len(users)} / {rg.MAX_USUARIOS}")

    user = None
    if users:
        ids = [u.id for u in users]
        rotulos = {u.id: f"{u.name} (CPF {rg.mascarar_cpf(u.cpf)})" for u in users}
        if st.session_state.get("active_user_id") not in ids:
            st.session_state["active_user_id"] = ids[0]
        uid = st.sidebar.selectbox("Alternar usuário ativo:", ids, format_func=rotulos.get, key="active_user_id")
        user = next(u for u in users if u.id == uid)
    else:
        st.sidebar.info("Nenhum usuário cadastrado.")

    st.sidebar.markdown("---")
    st.sidebar.subheader("➕ Adicionar Integrante")
    if len(users) < rg.MAX_USUARIOS:
        with st.sidebar.form("form_add_user", clear_on_submit=True):
            nome = st.text_input("Nome:")
            email = st.text_input("E-mail:")
            cpf = st.text_input("CPF:", max_chars=14, placeholder="000.000.000-00")
            enviar = st.form_submit_button("Cadastrar Usuário")
        if enviar:
            ok, msg = sv.cadastrar_usuario(db, nome, email, cpf)
            if ok:
                flash(msg)
                st.rerun()
            else:
                st.sidebar.error(msg)
    else:
        st.sidebar.warning("⚠️ Limite de usuários atingido!")

    st.sidebar.markdown("---")
    st.sidebar.subheader("💾 Backup")
    if os.path.exists(DB_PATH):
        with open(DB_PATH, "rb") as f:
            st.sidebar.download_button(
                "Baixar backup do banco", f.read(),
                file_name=f"backup_financas_{datetime.date.today():%Y%m%d}.db",
                mime="application/octet-stream")
    return user


# ---------------------------------------------------------
# Aba: Perfil & Configurações
# ---------------------------------------------------------
def render_perfil(db, user):
    st.markdown("### 📋 Resumo do Perfil")
    col_inv, col_cfg = st.columns(2, gap="large")
    with col_inv:
        render_investimentos(db, user)
    with col_cfg:
        render_contas_cartoes(db, user)


def render_investimentos(db, user):
    st.markdown("### 📈 Aplicações & Investimentos")
    invs = db.query(Investment).filter(Investment.user_id == user.id).order_by(Investment.id).all()
    if invs:
        kpi_em(st, "💰 Total investido", brl(sum(i.balance_cents for i in invs)), tom="info")
        df = pd.DataFrame([{
            "ID": i.id, "Banco / Corretora": i.bank_name, "Produto": i.product_name,
            "Saldo Atual (R$)": reais(i.balance_cents), "Rentabilidade": i.yield_rate,
        } for i in invs])
        editado = st.data_editor(
            df, hide_index=True, key="editor_investimentos",
            column_config={
                "ID": st.column_config.NumberColumn("ID", disabled=True),
                "Banco / Corretora": st.column_config.TextColumn("Banco"),
                "Produto": st.column_config.TextColumn("Produto"),
                "Saldo Atual (R$)": st.column_config.NumberColumn("Saldo", format="R$ %.2f", min_value=0.0),
                "Rentabilidade": st.column_config.TextColumn("Rentab."),
            })
        if st.button("💾 Salvar Aplicações"):
            for _, row in editado.iterrows():
                inv = db.get(Investment, int(row["ID"]))
                if inv:
                    inv.bank_name = str(row["Banco / Corretora"])
                    inv.product_name = str(row["Produto"])
                    inv.balance_cents = cents(row["Saldo Atual (R$)"])
                    inv.yield_rate = "" if pd.isna(row["Rentabilidade"]) else str(row["Rentabilidade"])
            db.commit()
            flash("Aplicações atualizadas!")
            st.rerun()
    else:
        st.info("Nenhuma aplicação cadastrada.")

    with st.expander("➕ Cadastrar / Excluir Aplicação"):
        with st.form("form_add_inv", clear_on_submit=True):
            c1, c2 = st.columns(2)
            banco = c1.text_input("Banco / Corretora:", placeholder="Ex: XP, Nubank")
            produto = c2.text_input("Produto:", placeholder="Ex: CDB, Tesouro")
            saldo = c1.number_input("Saldo Aplicado (R$):", min_value=0.0, step=100.0, format="%.2f")
            rentab = c2.text_input("Rentabilidade:", placeholder="Ex: 100% CDI")
            enviar = st.form_submit_button("Salvar Aplicação")
        if enviar:
            if banco and produto:
                db.add(Investment(user_id=user.id, bank_name=banco, product_name=produto,
                                  balance_cents=cents(saldo), yield_rate=rentab))
                db.commit()
                flash("Aplicação cadastrada!")
                st.rerun()
            else:
                st.error("Preencha Banco e Produto.")

        if invs:
            st.markdown("---")
            rotulos = {i.id: f"#{i.id} - {i.bank_name} ({i.product_name})" for i in invs}
            sel = st.selectbox("Selecione para excluir:", list(rotulos), format_func=rotulos.get, key="sel_del_inv")
            if st.button("🗑️ Excluir Aplicação", key="perigo_del_inv"):
                db.delete(db.get(Investment, sel))
                db.commit()
                flash("Aplicação removida!")
                st.rerun()


def render_contas_cartoes(db, user):
    st.markdown("### ⚙️ Contas e Cartões")

    # ---- Contas
    st.markdown("#### 🏦 Contas Correntes")
    contas = db.query(BankAccount).filter(BankAccount.user_id == user.id).order_by(BankAccount.id).all()
    st.write(f"**Contas:** {len(contas)} / {rg.MAX_CONTAS}")
    if contas:
        df = pd.DataFrame([{"ID": a.id, "Banco / Conta": a.name,
                            "Saldo Inicial (R$)": reais(a.initial_balance_cents)} for a in contas])
        editado = st.data_editor(
            df, hide_index=True, key="editor_contas",
            column_config={
                "ID": st.column_config.NumberColumn("ID", disabled=True),
                "Banco / Conta": st.column_config.TextColumn("Conta"),
                "Saldo Inicial (R$)": st.column_config.NumberColumn("Saldo Inicial", format="R$ %.2f"),
            })
        if st.button("💾 Salvar Contas"):
            for _, row in editado.iterrows():
                a = db.get(BankAccount, int(row["ID"]))
                if a and str(row["Banco / Conta"]).strip():
                    a.name = str(row["Banco / Conta"]).strip()
                    a.initial_balance_cents = cents(row["Saldo Inicial (R$)"])
            db.commit()
            flash("Contas atualizadas!")
            st.rerun()

    if len(contas) < rg.MAX_CONTAS:
        with st.expander("➕ Nova Conta"):
            with st.form("form_add_acc", clear_on_submit=True):
                c1, c2 = st.columns(2)
                nome = c1.text_input("Nome do Banco / Conta:")
                saldo = c2.number_input("Saldo Inicial (R$):", value=0.0, step=50.0, format="%.2f")
                enviar = st.form_submit_button("Salvar Conta")
            if enviar:
                if nome.strip():
                    db.add(BankAccount(user_id=user.id, name=nome.strip(), initial_balance_cents=cents(saldo)))
                    db.commit()
                    flash("Conta adicionada!")
                    st.rerun()
                else:
                    st.error("Informe o nome da conta.")

    # ---- Cartões
    st.markdown("---")
    st.markdown("#### 💳 Cartões de Crédito")
    cartoes = db.query(CreditCard).filter(CreditCard.user_id == user.id).order_by(CreditCard.id).all()
    st.write(f"**Cartões:** {len(cartoes)} / {rg.MAX_CARTOES}")
    if cartoes:
        df = pd.DataFrame([{"ID": c.id, "Cartão": c.name, "Limite Total (R$)": reais(c.limit_cents),
                            "Dia Venc.": int(c.due_day), "Dias Fech.": int(c.closing_days_before),
                            "Competência": rg.OPCOES_COMPETENCIA.get(c.competence_offset, rg.OPCOES_COMPETENCIA[0])}
                           for c in cartoes])
        editado = st.data_editor(
            df, hide_index=True, key="editor_cartoes",
            column_config={
                "ID": st.column_config.NumberColumn("ID", disabled=True),
                "Cartão": st.column_config.TextColumn("Cartão"),
                "Limite Total (R$)": st.column_config.NumberColumn("Limite", format="R$ %.2f", min_value=0.0),
                "Dia Venc.": st.column_config.NumberColumn("Venc.", min_value=1, max_value=31, step=1),
                "Dias Fech.": st.column_config.NumberColumn("Fech.", min_value=1, max_value=25, step=1),
                "Competência": st.column_config.SelectboxColumn(
                    "Competência da fatura", options=list(rg.OPCOES_COMPETENCIA.values()), required=True,
                    help="Mês anterior ao vencimento: a fatura que vence em 04/10 é chamada de Setembro."),
            })
        st.caption("ℹ️ Mudar vencimento/fechamento vale só para NOVOS lançamentos; faturas já geradas não se movem. "
                   "A competência muda apenas o NOME (mês) da fatura, nunca as datas.")
        if st.button("💾 Salvar Cartões"):
            for _, row in editado.iterrows():
                c = db.get(CreditCard, int(row["ID"]))
                if c and str(row["Cartão"]).strip() and not pd.isna(row["Dia Venc."]) and not pd.isna(row["Dias Fech."]):
                    c.name = str(row["Cartão"]).strip()
                    c.limit_cents = cents(row["Limite Total (R$)"])
                    c.due_day = int(row["Dia Venc."])
                    c.closing_days_before = int(row["Dias Fech."])
                    c.competence_offset = COMPETENCIA_POR_ROTULO.get(row["Competência"], c.competence_offset)
            db.commit()
            flash("Cartões atualizados!")
            st.rerun()

    if len(cartoes) < rg.MAX_CARTOES:
        with st.expander("➕ Novo Cartão"):
            with st.form("form_add_card", clear_on_submit=True):
                c1, c2 = st.columns(2)
                nome = c1.text_input("Nome/Bandeira:")
                limite = c2.number_input("Limite Total (R$):", value=1000.0, step=100.0, format="%.2f")
                c3, c4 = st.columns(2)
                venc = c3.number_input("Dia Venc.:", min_value=1, max_value=31, value=10)
                fech = c4.number_input("Dias antes do venc. p/ fechar:", min_value=1, max_value=25, value=7)
                comp = st.selectbox("Competência da fatura:", list(rg.OPCOES_COMPETENCIA.values()))
                enviar = st.form_submit_button("Salvar Cartão")
            if enviar:
                if nome.strip():
                    db.add(CreditCard(user_id=user.id, name=nome.strip(), limit_cents=cents(limite),
                                      due_day=int(venc), closing_days_before=int(fech),
                                      competence_offset=COMPETENCIA_POR_ROTULO[comp]))
                    db.commit()
                    flash("Cartão adicionado!")
                    st.rerun()
                else:
                    st.error("Informe o nome do cartão.")


# ---------------------------------------------------------
# Aba: Dashboard
# ---------------------------------------------------------
def render_dashboard(db, user):
    st.markdown("### 📊 Indicadores Financeiros")
    hoje = datetime.date.today()
    c1, c2, _ = st.columns([1, 1, 3])
    mes_nome = c1.selectbox("Mês:", rg.MESES, index=hoje.month - 1, key="dash_mes")
    ano = int(c2.number_input("Ano:", min_value=2020, max_value=2050, value=hoje.year, step=1, key="dash_ano"))
    mes = rg.MESES.index(mes_nome) + 1
    anterior = datetime.date(ano, mes, 1) - relativedelta(months=1)

    contas = sv.resumo_contas(db, user.id)
    saldo_contas = sum(c["atual"] for c in contas)
    investimentos = sum(i.balance_cents for i in db.query(Investment).filter(Investment.user_id == user.id))
    faturas = sv.faturas_em_aberto(db, user.id)
    resumo = sv.resumo_mensal(db, user.id, ano, mes)
    resumo_ant = sv.resumo_mensal(db, user.id, anterior.year, anterior.month)
    atraso = sv.em_atraso(db, user.id, hoje)

    # Destaques: o que mais importa olhar primeiro
    g1, g2, g3 = st.columns(3)
    kpi_em(g1, "🏦 Saldo em contas", brl(saldo_contas), tom="negativo" if saldo_contas < 0 else "info", grande=True,
           detalhe_html=f"Com aplicações: {visual.esc(brl(saldo_contas + investimentos))}")
    kpi_em(g2, f"📤 A pagar em {mes_nome}", brl(resumo["a_pagar"]), tom="alerta" if resumo["a_pagar"] else "positivo",
           grande=True, detalhe_html="Contas e faturas que vencem no mês")
    kpi_em(g3, "⏰ Em atraso", brl(atraso), tom="negativo" if atraso else "positivo", grande=True,
           detalhe_html="Contas e faturas vencidas e não pagas" if atraso else "Tudo em dia ✓")

    st.caption("Situação atual")
    s1, s2, s3 = st.columns(3)
    kpi_em(s1, "💎 Patrimônio (contas + aplicações)", brl(saldo_contas + investimentos), tom="info")
    kpi_em(s2, "📈 Aplicações", brl(investimentos), tom="info")
    kpi_em(s3, "💳 Faturas em aberto (total)", brl(sum(f["restante"] for f in faturas)),
           tom="alerta" if faturas else "positivo")

    st.caption(f"Movimento de {mes_nome}/{ano}")
    e1, e2, e3 = st.columns(3)
    var_ent = rg.variacao_percentual(resumo["entradas_realizadas"], resumo_ant["entradas_realizadas"])
    var_sai = rg.variacao_percentual(resumo["saidas_realizadas"], resumo_ant["saidas_realizadas"])
    kpi_em(e1, "🟢 Entradas realizadas", brl(resumo["entradas_realizadas"]), tom="positivo",
           detalhe_html=visual.delta_html(var_ent, alta_e_boa=True))
    kpi_em(e2, "🔴 Saídas pagas", brl(resumo["saidas_realizadas"]), tom="negativo",
           detalhe_html=visual.delta_html(var_sai, alta_e_boa=False))
    kpi_em(e3, "📥 A receber no mês", brl(resumo["a_receber"]), tom="info")

    st.markdown("---")
    col_esq, col_dir = st.columns(2, gap="large")

    with col_esq:
        st.markdown(f"### 📈 Fluxo de Caixa {ano} (por vencimento)")
        fluxo = sv.fluxo_anual(db, user.id, ano)
        df_fluxo = pd.DataFrame(
            [{"Mês": rg.MESES[m["mes"] - 1][:3], "Tipo": "Entradas", "Valor (R$)": reais(m["entradas"])} for m in fluxo]
            + [{"Mês": rg.MESES[m["mes"] - 1][:3], "Tipo": "Saídas", "Valor (R$)": reais(m["saidas"])} for m in fluxo])
        fig = px.bar(df_fluxo, x="Mês", y="Valor (R$)", color="Tipo", barmode="group",
                     category_orders={"Mês": [m[:3] for m in rg.MESES]},
                     color_discrete_map={"Entradas": "#10b981", "Saídas": "#ef4444"})
        fig.update_traces(hovertemplate="%{x}: R$ %{y:,.2f}<extra>%{fullData.name}</extra>")
        fig.update_yaxes(tickprefix="R$ ", tickformat=",.0f", title=None)
        fig.update_xaxes(title=None)
        st.plotly_chart(figura_base(fig, altura=380))

    with col_dir:
        st.markdown(f"### 🏷️ Gastos por Categoria ({mes_nome}/{ano})")
        gastos = sv.gastos_por_categoria(db, user.id, ano, mes)
        if gastos:
            total = sum(v for _, v in gastos)
            topo = rg.agrupar_top_n(gastos, 8)          # 8 maiores + "Outras (n)"
            df_top = pd.DataFrame({
                "Categoria": [n for n, _ in topo], "Valor (R$)": [reais(v) for _, v in topo],
                "Rótulo": [f"{brl(v)} · {visual.fmt_pct(v / total * 100, 0)}" for _, v in topo]})
            fig = px.bar(df_top, x="Valor (R$)", y="Categoria", orientation="h", text="Rótulo",
                         color_discrete_sequence=["#6366f1"])
            fig.update_traces(textposition="outside", cliponaxis=False, hovertemplate="%{y}: R$ %{x:,.2f}<extra></extra>")
            fig.update_yaxes(autorange="reversed", title=None)
            fig.update_xaxes(visible=False)
            figura_base(fig, altura=max(300, 42 * len(topo) + 40))
            fig.update_layout(margin=dict(l=10, r=150, t=10, b=10))
            st.plotly_chart(fig)
            with st.expander("Ver todas as categorias"):
                df_cat = pd.DataFrame([{"Categoria": n, "Valor (R$)": reais(v), "%": v / total * 100} for n, v in gastos])
                st.dataframe(visual.tabela(df_cat, moeda=("Valor (R$)",), percentuais=("%",)), hide_index=True)
            st.caption("Por mês de competência: contas pela data; cartões pelo mês da fatura (veja a configuração "
                       "de competência de cada cartão). Pago + previsto. "
                       "Pagamentos de fatura e transferências não entram, para não duplicar.")
        else:
            st.info("Nenhuma despesa neste mês.")

    pend = sv.lancamentos_pendentes(db, user.id, ano, mes)
    if pend:
        st.markdown("#### 📋 Contas pendentes (até o fim do mês selecionado)")
        df_pend = pd.DataFrame([{
            "Vencimento": t.due_date, "Situação": "🔴 Atrasada" if t.due_date < hoje else "🟡 A vencer",
            "Conta": t.account.name, "Tipo": t.trans_type, "Descrição": t.description,
            "Valor (R$)": reais(t.amount_cents)} for t in pend])
        st.dataframe(visual.tabela(df_pend, moeda=("Valor (R$)",), datas=("Vencimento",), status=("Situação",),
                                   realce_atraso="Situação"), hide_index=True)

    if faturas:
        st.markdown("#### 💳 Faturas em aberto")
        df_fat = pd.DataFrame([{
            "Cartão": f["card"].name, "Fatura": f"{rg.MESES[f['comp_mes'] - 1][:3]}/{f['comp_ano']}",
            "Vencimento": f["vencimento"],
            "Total (R$)": reais(f["total"]), "Pago (R$)": reais(f["pago"]), "Restante (R$)": reais(f["restante"]),
            "Situação": "🔴 Vencida" if f["vencimento"] < hoje else "🟡 A vencer"} for f in faturas])
        st.dataframe(visual.tabela(df_fat, moeda=("Total (R$)", "Pago (R$)", "Restante (R$)"), datas=("Vencimento",),
                                   status=("Situação",), realce_atraso="Situação"), hide_index=True)


# ---------------------------------------------------------
# Aba: Saldos
# ---------------------------------------------------------
def render_saldos(db, user):
    contas = sv.resumo_contas(db, user.id)
    if not contas:
        st.info("Nenhuma conta corrente cadastrada.")
        return

    aba_saldos, aba_extrato = st.tabs(["💰 Saldos", "📄 Extrato"])
    with aba_saldos:
        st.markdown("### 🏦 Saldos das Contas")
        total = sum(c["atual"] for c in contas)
        kpi_em(st, "💰 Saldo total consolidado", brl(total), tom="negativo" if total < 0 else "info", grande=True)
        st.dataframe(visual.tabela(pd.DataFrame([{
            "Conta": c["conta"].name, "Inicial": reais(c["inicial"]), "Entradas": reais(c["entradas"]),
            "Saídas": reais(c["saidas"]), "Atual": reais(c["atual"])} for c in contas]),
            moeda=("Inicial", "Entradas", "Saídas", "Atual"), so_negativo=("Atual",)), hide_index=True)
        st.caption("Só lançamentos marcados como pagos entram no saldo (inclui pagamentos de fatura e transferências).")

    with aba_extrato:
        st.markdown("### 📄 Extrato da Conta Corrente")
        rotulos = {c["conta"].id: c["conta"].name for c in contas}
        conta_id = st.selectbox("Conta:", list(rotulos), format_func=rotulos.get, key="filtro_conta_extrato")
        conta = next(c["conta"] for c in contas if c["conta"].id == conta_id)
        linhas, saldo_final = sv.extrato_conta(db, conta)
        if not linhas:
            st.info("Nenhum lançamento registrado nesta conta.")
            return
        ent = sum(l["valor"] for l in linhas if l["transacao"].is_paid and l["valor"] > 0)
        sai = -sum(l["valor"] for l in linhas if l["transacao"].is_paid and l["valor"] < 0)
        a, b, c = st.columns(3)
        kpi_em(a, "Saldo atual", brl(saldo_final), tom="negativo" if saldo_final < 0 else "info")
        kpi_em(b, "Entradas pagas", brl(ent), tom="positivo")
        kpi_em(c, "Saídas pagas", brl(sai), tom="negativo")
        df_ext = pd.DataFrame([{
            "ID": l["transacao"].id,
            "Data": (l["transacao"].paid_date or l["transacao"].due_date) if l["transacao"].is_paid else l["transacao"].due_date,
            "Status": "Pago" if l["transacao"].is_paid else "Pendente",
            "Tipo": l["transacao"].trans_type, "Descrição": l["transacao"].description,
            "Categoria": l["transacao"].category.name, "Método": l["transacao"].method,
            "Valor (R$)": reais(l["valor"]), "Saldo (R$)": reais(l["saldo"])} for l in reversed(linhas)])
        st.dataframe(visual.tabela(df_ext, moeda=("Valor (R$)", "Saldo (R$)"), datas=("Data",), sinal=("Valor (R$)",),
                                   so_negativo=("Saldo (R$)",), status=("Status",)), hide_index=True)


# ---------------------------------------------------------
# Aba: Faturas
# ---------------------------------------------------------
def render_faturas(db, user):
    st.markdown("### 💳 Faturas de Cartão")
    cartoes = db.query(CreditCard).filter(CreditCard.user_id == user.id).order_by(CreditCard.id).all()
    contas = db.query(BankAccount).filter(BankAccount.user_id == user.id).order_by(BankAccount.id).all()
    if not cartoes:
        st.info("Nenhum cartão cadastrado.")
        return

    hoje = datetime.date.today()
    usados = sv.limite_utilizado(db, user.id, hoje)

    st.caption("Limite utilizado por cartão (parcelas futuras consomem limite; assinaturas futuras não)")
    colunas = st.columns(min(len(cartoes), 4))
    for n, c in enumerate(cartoes):
        colunas[n % len(colunas)].markdown(
            visual.limite_html(c.name, usados.get(c.id, 0), c.limit_cents, brl), unsafe_allow_html=True)

    st.markdown("---")
    c1, c2, c3 = st.columns([2, 1, 1])
    nomes_cartoes = {c.id: c.name for c in cartoes}
    card_id = c1.selectbox("Cartão:", list(nomes_cartoes), format_func=nomes_cartoes.get, key="fat_cartao")
    card = next(c for c in cartoes if c.id == card_id)
    # O usuário escolhe a COMPETÊNCIA (nome da fatura); ano/mes abaixo são o mês em que ela VENCE.
    mes_nome = c2.selectbox("Fatura (competência):", rg.MESES, index=hoje.month - 1, key="fat_mes")
    comp_ano = int(c3.number_input("Ano:", min_value=2020, max_value=2050, value=hoje.year, step=1, key="fat_ano"))
    ano, mes = rg.vencimento_da_competencia(comp_ano, rg.MESES.index(mes_nome) + 1, card.competence_offset)

    fat = sv.resumo_fatura(db, card, ano, mes)
    status = rg.status_fatura(fat["total"], fat["pago"], fat["vencimento"], hoje)
    disponivel = card.limit_cents - usados.get(card.id, 0)

    st.markdown(f"#### Fatura {mes_nome}/{comp_ano} {visual.selo_fatura(status)}", unsafe_allow_html=True)
    m1, m2, m3, m4 = st.columns(4)
    kpi_em(m1, "Total da fatura", brl(fat["total"]), tom="info")
    kpi_em(m2, "Restante a pagar", brl(fat["restante"]),
           tom={"Vencida": "negativo", "Paga": "positivo", "Vazia": "neutro"}.get(status, "alerta"))
    kpi_em(m3, "Vencimento · Fechamento", f"{fat['vencimento']:%d/%m/%y} · {fat['fechamento']:%d/%m}", tom="neutro")
    kpi_em(m4, "Limite disponível", brl(disponivel), tom="negativo" if disponivel < 0 else "neutro")
    if card.competence_offset:
        st.caption(f"ℹ️ Neste cartão a competência é o mês anterior ao vencimento: "
                   f"a fatura de {mes_nome}/{comp_ano} vence em {fmt_data(fat['vencimento'])}.")

    proj = sv.projecao_faturas(db, card, ano, mes, n=6)
    if any(p["total"] for p in proj):
        st.markdown("##### Próximas faturas (a partir da selecionada)")
        rotulos = [f"{rg.MESES[p['comp_mes'] - 1][:3]}/{str(p['comp_ano'])[2:]}" for p in proj]
        df_proj = pd.DataFrame([{"Fatura": rot, "Situação": sit, "Valor (R$)": reais(v)}
                                for rot, p in zip(rotulos, proj)
                                for sit, v in (("Pago", p["pago"]), ("A pagar", p["restante"]))])
        fig = px.bar(df_proj, x="Fatura", y="Valor (R$)", color="Situação", barmode="stack",
                     category_orders={"Fatura": rotulos},
                     color_discrete_map={"Pago": "#10b981", "A pagar": "#3b82f6"})
        fig.update_traces(hovertemplate="%{x}: R$ %{y:,.2f}<extra>%{fullData.name}</extra>")
        fig.update_yaxes(tickprefix="R$ ", tickformat=",.0f", title=None)
        fig.update_xaxes(title=None)
        st.plotly_chart(figura_base(fig, altura=280))

    with st.expander("💵 Pagar Fatura (total ou parcial)"):
        if not contas:
            st.warning("Cadastre uma Conta Corrente para registrar o pagamento.")
        elif fat["restante"] <= 0:
            st.info("Esta fatura não possui valor a pagar.")
        else:
            with st.form("form_pagar_fatura", clear_on_submit=True):
                p1, p2, p3, p4 = st.columns(4)
                nomes_contas = {a.id: a.name for a in contas}
                conta_id = p1.selectbox("Conta de origem:", list(nomes_contas), format_func=nomes_contas.get)
                valor = p2.number_input("Valor a pagar (R$):", value=reais(fat["restante"]), min_value=0.01,
                                        max_value=reais(fat["restante"]), step=10.0, format="%.2f")
                data_pg = p3.date_input("Data do pagamento:", value=st.session_state["ultima_data_lancamento"])
                metodo = p4.selectbox("Forma:", ["PIX", "Débito Automático", "Boleto / TED"])
                enviar = st.form_submit_button("Confirmar Pagamento")
            if enviar:
                try:
                    sv.pagar_fatura(db, user_id=user.id, card=card, ano=ano, mes=mes, account_id=conta_id,
                                    valor_cents=cents(valor), data=data_pg, metodo=metodo)
                except ValueError as e:
                    db.rollback()
                    st.error(str(e))
                else:
                    st.session_state["ultima_data_lancamento"] = data_pg
                    flash(f"Pagamento de {brl(cents(valor))} registrado.")
                    st.rerun()

    if fat["pagamentos"]:
        st.markdown("#### Pagamentos desta fatura")
        nomes_contas = {a.id: a.name for a in contas}
        df_pg = pd.DataFrame([{"Data": p.paid_date, "Conta": nomes_contas.get(p.account_id, "-"),
                               "Valor (R$)": reais(p.amount_cents)} for p in fat["pagamentos"]])
        st.dataframe(visual.tabela(df_pg, moeda=("Valor (R$)",), datas=("Data",)), hide_index=True)
        st.caption("Para estornar um pagamento, exclua-o na aba Lançamentos (categoria Pagamento de Fatura).")

    st.markdown("#### Lançamentos da fatura")
    if fat["itens"]:
        df_it = pd.DataFrame([{"Data Compra": t.purchase_date, "Descrição": t.description,
                               "Categoria": t.category.name, "Valor (R$)": reais(t.amount_cents)} for t in fat["itens"]])
        st.dataframe(visual.tabela(df_it, moeda=("Valor (R$)",), datas=("Data Compra",)), hide_index=True)
    else:
        st.info("Nenhum lançamento nesta fatura.")


# ---------------------------------------------------------
# Aba: Lançamentos
# ---------------------------------------------------------
def opcoes_categoria(db, tipo):
    cats = db.query(Category).filter(Category.type == tipo).order_by(Category.name).all()
    return {c.id: c.name for c in cats}


def render_lancamentos(db, user):
    st.markdown("### 📝 Novo Lançamento")
    contas = db.query(BankAccount).filter(BankAccount.user_id == user.id).order_by(BankAccount.id).all()
    cartoes = db.query(CreditCard).filter(CreditCard.user_id == user.id).order_by(CreditCard.id).all()

    if not contas and not cartoes:
        st.info("Cadastre uma Conta ou Cartão de Crédito para realizar lançamentos.")
        return

    origem = st.radio("Origem do Lançamento:", ["Conta Corrente", "Cartão de Crédito", "Transferência entre Contas"],
                      horizontal=True)
    if origem == "Conta Corrente":
        form_conta(db, user, contas)
    elif origem == "Cartão de Crédito":
        form_cartao(db, user, cartoes)
    else:
        form_transferencia(db, user, contas)

    st.markdown("---")
    render_historico(db, user)


def form_conta(db, user, contas):
    if not contas:
        st.warning("Nenhuma conta cadastrada.")
        return
    ids = [a.id for a in contas]
    nomes = {a.id: a.name for a in contas}
    tipo = st.selectbox("Tipo:", [rg.SAIDA, rg.ENTRADA], key="acc_trans_type_sel")
    cats = opcoes_categoria(db, tipo)

    with st.form("form_trans_acc", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns(4)
        conta_id = c1.selectbox("Conta:", ids, format_func=nomes.get,
                                index=indice_de(ids, st.session_state["ultima_conta_id"]))
        cat_id = c2.selectbox("Categoria:", list(cats), format_func=cats.get)
        metodo = c3.selectbox("Método:", ["PIX", "Boleto", "TED", "Saque", "Débito Automático", "Outro"])
        valor = c4.number_input("Valor (R$):", min_value=0.01, step=10.0, format="%.2f")

        c5, c6, c7 = st.columns([2, 1, 1])
        desc = c5.text_input("Descrição:", placeholder="Ex: Aluguel, Mercado")
        data = c6.date_input("Data:", value=st.session_state["ultima_data_lancamento"])
        pago = c7.checkbox("Lançamento pago?", value=True)

        meses = st.number_input("Repetir por quantos meses? (1 = lançamento único)",
                                min_value=1, max_value=60, value=1, step=1)
        enviar = st.form_submit_button("💾 Salvar Lançamento")

    if enviar:
        if not desc.strip():
            st.error("Preencha a descrição.")
            return
        try:
            criados = sv.criar_lancamento_conta(
                db, user_id=user.id, account_id=conta_id, category_id=cat_id, descricao=desc.strip(),
                valor_cents=cents(valor), trans_type=tipo, metodo=metodo, data=data, pago=pago, repeticoes=int(meses))
        except ValueError as e:
            db.rollback()
            st.error(str(e))
            return
        st.session_state["ultima_data_lancamento"] = data
        st.session_state["ultima_conta_id"] = conta_id
        flash(f"{len(criados)} lançamento(s) salvo(s)!")
        st.rerun()


def form_cartao(db, user, cartoes):
    if not cartoes:
        st.warning("Nenhum cartão cadastrado.")
        return
    ids = [c.id for c in cartoes]
    nomes = {c.id: c.name for c in cartoes}
    cats = opcoes_categoria(db, rg.SAIDA)

    modo_rotulo = st.radio("Tipo de lançamento:", ["Parcelado", "Recorrente (Mensal)"], horizontal=True)
    parcelado = modo_rotulo == "Parcelado"

    with st.form("form_trans_card", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        card_id = c1.selectbox("Cartão:", ids, format_func=nomes.get,
                               index=indice_de(ids, st.session_state["ultimo_cartao_id"]))
        cat_id = c2.selectbox("Categoria:", list(cats), format_func=cats.get)
        valor = c3.number_input("Valor TOTAL da compra (R$):" if parcelado else "Valor cobrado por mês (R$):",
                                min_value=0.01, step=10.0, format="%.2f")
        c4, c5, c6 = st.columns([2, 1, 1])
        desc = c4.text_input("Descrição:", placeholder="Ex: Assinatura Netflix, Mercado")
        data = c5.date_input("Data da compra:", value=st.session_state["ultima_data_lancamento"])
        qtd = c6.number_input("Nº de parcelas:" if parcelado else "Repetir por quantos meses?",
                              min_value=1, max_value=48 if parcelado else 60, value=1 if parcelado else 12, step=1)
        enviar = st.form_submit_button("💾 Salvar no Cartão")

    if enviar:
        if not desc.strip():
            st.error("Preencha a descrição.")
            return
        card = next(c for c in cartoes if c.id == card_id)
        try:
            criados = sv.criar_lancamento_cartao(
                db, user_id=user.id, card=card, category_id=cat_id, descricao=desc.strip(),
                valor_cents=cents(valor), modo=rg.SERIE_PARCELADO if parcelado else rg.SERIE_RECORRENTE,
                quantidade=int(qtd), data_compra=data)
        except ValueError as e:
            db.rollback()
            st.error(str(e))
            return
        st.session_state["ultima_data_lancamento"] = data
        st.session_state["ultimo_cartao_id"] = card_id
        primeira, ultima = criados[0], criados[-1]
        flash(f"{len(criados)} lançamento(s) de {brl(primeira.amount_cents)} registrados. "
              f"Primeira fatura vence em {fmt_data(primeira.due_date)}; última em {fmt_data(ultima.due_date)}.")
        st.rerun()


def form_transferencia(db, user, contas):
    if len(contas) < 2:
        st.warning("Cadastre pelo menos duas contas para fazer transferências.")
        return
    ids = [a.id for a in contas]
    nomes = {a.id: a.name for a in contas}
    with st.form("form_transferencia", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        origem = c1.selectbox("Conta de origem:", ids, format_func=nomes.get)
        destino = c2.selectbox("Conta de destino:", ids, format_func=nomes.get, index=1)
        valor = c3.number_input("Valor (R$):", min_value=0.01, step=10.0, format="%.2f")
        c4, c5 = st.columns([2, 1])
        desc = c4.text_input("Descrição (opcional):", placeholder="Ex: Reserva do mês")
        data = c5.date_input("Data:", value=st.session_state["ultima_data_lancamento"])
        enviar = st.form_submit_button("💾 Transferir")
    if enviar:
        try:
            sv.criar_transferencia(db, user_id=user.id, origem_id=origem, destino_id=destino,
                                   valor_cents=cents(valor), data=data, descricao=desc)
        except ValueError as e:
            db.rollback()
            st.error(str(e))
            return
        st.session_state["ultima_data_lancamento"] = data
        flash("Transferência registrada!")
        st.rerun()


STATUS_PAGO, STATUS_PEND, STATUS_FATURA = "🟢 Pago", "🟡 Pendente", "💳 Fatura"


def render_historico(db, user):
    st.markdown("### 📋 Histórico")
    todas = (db.query(Transaction).filter(Transaction.user_id == user.id)
             .order_by(Transaction.due_date.desc(), Transaction.id.desc()).all())
    if not todas:
        st.info("Nenhum lançamento registrado.")
        return

    with st.expander("🔍 Filtros de Busca", expanded=False):
        f1, f2, f3, f4 = st.columns(4)
        tipo_f = f1.selectbox("Tipo:", ["Todos", rg.ENTRADA, rg.SAIDA])
        status_f = f2.selectbox("Status:", ["Todos", "Apenas Pagos", "Apenas Pendentes"])
        meses_disp = sorted({f"{t.due_date:%Y-%m}" for t in todas}, reverse=True)
        opcoes_mes = ["Todos"] + meses_disp
        mes_atual = f"{datetime.date.today():%Y-%m}"
        mes_f = f3.selectbox("Mês de vencimento:", opcoes_mes, key="hist_mes",
                             index=opcoes_mes.index(mes_atual) if mes_atual in opcoes_mes else 0)
        busca = f4.text_input("Buscar descrição:", placeholder="Ex: Aluguel")

    filtradas = todas
    if tipo_f != "Todos":
        filtradas = [t for t in filtradas if t.trans_type == tipo_f]
    if status_f == "Apenas Pagos":
        filtradas = [t for t in filtradas if t.is_paid]
    elif status_f == "Apenas Pendentes":
        filtradas = [t for t in filtradas if not t.is_paid]
    if mes_f != "Todos":
        filtradas = [t for t in filtradas if f"{t.due_date:%Y-%m}" == mes_f]
    if busca:
        filtradas = [t for t in filtradas if busca.lower() in t.description.lower()]

    st.caption(f"Mostrando {len(filtradas)} de {len(todas)} lançamentos "
               f"({'todos os meses' if mes_f == 'Todos' else 'vencimento em ' + mes_f}). "
               "Use 'Filtros de Busca' para mudar o mês ou procurar por texto.")
    if not filtradas:
        st.warning("Nenhum lançamento encontrado com esses filtros.")
        return

    categorias = {c.id: c.name for c in db.query(Category).all()}
    cat_por_nome = {n: i for i, n in categorias.items()}
    contas = {a.id: a.name for a in db.query(BankAccount).filter(BankAccount.user_id == user.id)}
    cartoes = {c.id: c.name for c in db.query(CreditCard).filter(CreditCard.user_id == user.id)}

    def origem_txt(t):
        if t.card_id:
            return f"💳 {cartoes.get(t.card_id, '?')}"
        return f"🏦 {contas.get(t.account_id, '?')}"

    def status_txt(t):
        if t.card_id:
            return STATUS_FATURA
        return STATUS_PAGO if t.is_paid else STATUS_PEND

    df = pd.DataFrame([{
        "ID": t.id, "Origem": origem_txt(t), "Data Compra": t.purchase_date, "Vencimento": t.due_date,
        "Status": status_txt(t), "Categoria": categorias[t.category_id], "Descrição": t.description,
        "Valor (R$)": reais(t.amount_cents)} for t in filtradas])

    editado = st.data_editor(
        df, hide_index=True, key="editor_lancamentos",
        column_config={
            "ID": st.column_config.NumberColumn("ID", disabled=True),
            "Origem": st.column_config.TextColumn("Origem", disabled=True),
            "Data Compra": st.column_config.DateColumn("Data Compra", format="DD/MM/YYYY"),
            "Vencimento": st.column_config.DateColumn("Vencimento / Fatura", format="DD/MM/YYYY"),
            "Status": st.column_config.SelectboxColumn("Status", options=[STATUS_PAGO, STATUS_PEND, STATUS_FATURA]),
            "Categoria": st.column_config.SelectboxColumn("Categoria", options=list(categorias.values())),
            "Descrição": st.column_config.TextColumn("Descrição"),
            "Valor (R$)": st.column_config.NumberColumn("Valor", format="R$ %.2f", min_value=0.01),
        })
    st.caption("O status de lançamentos de cartão é definido pelo pagamento da fatura. "
               "Pagamentos de fatura e transferências não são editáveis: exclua e refaça.")

    if st.button("💾 Salvar Tabela"):
        alterados = ignorados = 0
        for _, row in editado.iterrows():
            t = db.get(Transaction, int(row["ID"]))
            if t is None:
                continue
            if t.kind != rg.KIND_NORMAL:
                ignorados += 1
                continue
            status = str(row["Status"])
            is_paid = True if status == STATUS_PAGO else False if status == STATUS_PEND else None
            ok = sv.atualizar_lancamento(
                db, t,
                purchase_date=to_date(row["Data Compra"], t.purchase_date),
                due_date=to_date(row["Vencimento"], t.due_date),
                is_paid=is_paid,
                category_id=cat_por_nome.get(row["Categoria"], t.category_id),
                descricao="" if pd.isna(row["Descrição"]) else str(row["Descrição"]),
                valor_cents=cents(row["Valor (R$)"]))
            alterados += 1 if ok else 0
        db.commit()
        flash(f"Histórico atualizado ({alterados} linha(s) processada(s)).")
        st.rerun()

    with st.expander("🗑️ Excluir Lançamento"):
        rotulos = {t.id: f"#{t.id} · {fmt_data(t.due_date)} · {t.description} ({brl(t.amount_cents)})" for t in filtradas}
        sel_id = st.selectbox("Selecione para excluir:", list(rotulos), format_func=rotulos.get, key="sel_del_trans")
        sel = next(t for t in filtradas if t.id == sel_id)

        escopo = "apenas"
        if sel.group_id and sel.kind == rg.KIND_NORMAL:
            opcoes = {"apenas": "Somente este lançamento",
                      "proximas": "Este e os próximos da série",
                      "serie": "Toda a série (parcelas/recorrências)"}
            escopo = st.radio("Escopo:", list(opcoes), format_func=opcoes.get, key="escopo_exclusao")
        if sel.kind == rg.KIND_TRANSFERENCIA:
            st.caption("As duas pontas da transferência serão removidas.")
        if sel.kind == rg.KIND_PAGTO_FATURA:
            st.caption("A fatura voltará a ficar em aberto pelo valor deste pagamento.")

        if st.button(f"Confirmar exclusão do lançamento #{sel.id}", key="perigo_del_trans"):
            excluidos, bloqueados = sv.excluir_lancamento(db, sel, escopo)
            msg = f"{excluidos} lançamento(s) excluído(s)."
            if bloqueados:
                flash(msg + f" {bloqueados} item(ns) de cartão em faturas já pagas foram mantidos "
                            "(exclua antes o pagamento da fatura).", "warning")
            else:
                flash(msg)
            st.rerun()


ABAS = [
    ("perfil", "🏠", "Perfil", render_perfil),
    ("dashboard", "📊", "Início", render_dashboard),
    ("saldos", "🏦", "Saldos", render_saldos),
    ("faturas", "💳", "Faturas", render_faturas),
    ("lancamentos", "💸", "Lançar", render_lancamentos),
]


def _botoes_navegacao():
    """As colunas do Streamlit empilham na vertical em telas estreitas (não há meio-termo
    entre lado a lado e empilhado). Para a barra continuar horizontal no celular, o
    CSS (visual.py) força flex-direction: row só dentro do contêiner 'barra_nav'."""
    ativa = st.session_state["aba_ativa"]
    with st.container(key="barra_nav"):
        cols = st.columns(len(ABAS))
        for col, (chave, icone, rotulo, _) in zip(cols, ABAS):
            tipo = "primary" if chave == ativa else "secondary"
            if col.button(f"{icone} {rotulo}", key=f"nav_{chave}", type=tipo, width="stretch") and chave != ativa:
                st.session_state["aba_ativa"] = chave
                st.rerun()


def navegacao(db, user):
    """Barra de navegação. Usa st.bottom (Streamlit recente) para fixá-la no rodapé,
    como um app de celular; em versões sem st.bottom, cai para uma barra no topo."""
    st.session_state.setdefault("aba_ativa", "dashboard")
    if hasattr(st, "bottom"):
        with st.bottom:
            _botoes_navegacao()
    else:
        _botoes_navegacao()
        st.markdown("---")
    for chave, _, _, fn in ABAS:
        if st.session_state["aba_ativa"] == chave:
            fn(db, user)


# ---------------------------------------------------------
# Principal
# ---------------------------------------------------------
def main():
    init_state()
    db = get_session_factory()()
    try:
        user = sidebar_usuarios(db)
        st.title("💻 Dashboard Financeiro")
        mostrar_flash()
        if user is None:
            st.warning("Cadastre ou selecione um usuário na barra lateral para começar.")
            return

        st.caption(f"👤 Usuário ativo: **{user.name}**")
        navegacao(db, user)
    finally:
        db.close()


main()
