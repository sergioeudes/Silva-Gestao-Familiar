"""Apresentação: CSS, cartões de indicadores, selos e estilo de tabelas.

Não importa Streamlit: só gera texto (HTML/CSS) e objetos do pandas, para poder ser testado.
As cores de fundo/texto usam as variáveis do tema do Streamlit (com valores de reserva),
então o visual acompanha o tema claro/escuro.
"""
import html

import pandas as pd

import regras as rg

# Cores semânticas (funcionam bem nos temas claro e escuro)
VERDE, VERMELHO, AMARELO, AZUL, CINZA = "#10b981", "#ef4444", "#f59e0b", "#3b82f6", "#94a3b8"
TONS = {"positivo": VERDE, "negativo": VERMELHO, "alerta": AMARELO, "info": AZUL, "neutro": CINZA}
CLASSE_TEXTO = {"positivo": "#059669", "negativo": "#dc2626", "alerta": "#b45309", "info": "#2563eb", "neutro": ""}

CSS = """
<style>
:root {
    --safe-top: env(safe-area-inset-top, 0px);
    --safe-bottom: env(safe-area-inset-bottom, 0px);
}
.block-container { padding: calc(1rem + var(--safe-top)) 1rem calc(6rem + var(--safe-bottom)) 1rem !important; }
@media (min-width: 900px) { .block-container { padding: 2rem 3rem 3rem 3rem !important; } }

/* Visual de app: some com o rodapé "Made with Streamlit" (mantém o cabeçalho, usado para
   abrir a barra lateral no celular) e dá alvos de toque maiores em todos os controles. */
footer[data-testid="stFooter"] { display: none; }
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button,
.stSelectbox [data-baseweb="select"], .stTextInput input, .stNumberInput input, .stDateInput input {
    min-height: 44px !important;
}

/* Cartões nativos do Streamlit */
div[data-testid="stMetric"], .stExpander, div[data-testid="stForm"] {
    background-color: var(--secondary-background-color, #ffffff);
    border-radius: 12px !important; padding: 14px 18px !important;
    border: 1px solid rgba(128,128,128,0.25) !important; margin-bottom: 12px;
}
div[data-testid="stMetricLabel"] { font-size: 0.9rem !important; opacity: .75; font-weight: 600; }
div[data-testid="stMetricValue"] { font-size: 1.4rem !important; font-weight: 700; }

/* Botões: azul por padrão, vermelho para ações destrutivas (chave começando com "perigo") */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {
    border-radius: 8px !important; background-color: #2563eb; color: white; font-weight: 600;
    border: none; padding: 0.5rem 1.2rem; transition: all 0.2s ease-in-out;
}
.stButton > button:hover, .stFormSubmitButton > button:hover { background-color: #1d4ed8; color: white; }
[class*="st-key-perigo"] button { background-color: #dc2626 !important; color: #ffffff !important; }
[class*="st-key-perigo"] button:hover { background-color: #b91c1c !important; }

/* Abas */
.stTabs [data-baseweb="tab-list"] { gap: 8px; background-color: rgba(128,128,128,0.12); padding: 6px; border-radius: 10px; }
.stTabs [data-baseweb="tab"] { border-radius: 6px; padding: 8px 16px; background-color: transparent;
    font-size: 14px; font-weight: 600; }
.stTabs [aria-selected="true"] { background-color: var(--background-color, #ffffff) !important;
    color: var(--primary-color, #2563eb) !important; box-shadow: 0px 1px 3px rgba(0,0,0,0.12); }

/* Cartões de indicador (KPI) */
.kpi { background-color: var(--secondary-background-color, #ffffff); border: 1px solid rgba(128,128,128,0.25);
    border-left: 5px solid #94a3b8; border-radius: 12px; padding: 12px 16px; margin-bottom: 12px; }
.kpi-rotulo { font-size: 0.82rem; font-weight: 600; opacity: .72; }
.kpi-valor { font-size: 1.35rem; font-weight: 700; line-height: 1.35; }
.kpi-detalhe { font-size: 0.78rem; opacity: .75; margin-top: 2px; }
.kpi-grande { padding: 18px 22px; }
.kpi-grande .kpi-rotulo { font-size: 0.95rem; }
.kpi-grande .kpi-valor { font-size: 2rem; }
.kpi-positivo { border-left-color: #10b981; } .kpi-negativo { border-left-color: #ef4444; }
.kpi-alerta { border-left-color: #f59e0b; } .kpi-info { border-left-color: #3b82f6; }
.delta-bom { color: #059669; font-weight: 700; } .delta-ruim { color: #dc2626; font-weight: 700; }
.delta-neutro { opacity: .8; font-weight: 700; }

/* Selos de status */
.selo { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 0.78rem; font-weight: 700;
    vertical-align: middle; margin-left: 8px; }
.selo-verde { background: rgba(16,185,129,0.18); color: #059669; }
.selo-vermelho { background: rgba(239,68,68,0.18); color: #dc2626; }
.selo-amarelo { background: rgba(245,158,11,0.20); color: #b45309; }
.selo-azul { background: rgba(59,130,246,0.18); color: #2563eb; }
.selo-cinza { background: rgba(148,163,184,0.25); color: #64748b; }

/* Barra de navegação (nav_*): o Streamlit só tem dois modos para colunas — lado a lado ou
   totalmente empilhadas (sem meio-termo) — e empilha por padrão em telas estreitas. Aqui a
   linha é forçada a ficar horizontal em QUALQUER largura, senão vira uma pilha de botões
   gigantes cobrindo a tela no celular. Cobre os dois nomes de seletor usados pelo Streamlit
   ao longo das versões (data-testid antigo e classe nova), para funcionar em mais versões. */
.st-key-barra_nav [data-testid="stHorizontalBlock"], .st-key-barra_nav .stHorizontalBlock {
    flex-direction: row !important; flex-wrap: nowrap !important; gap: 0.25rem !important;
}
.st-key-barra_nav [data-testid="column"], .st-key-barra_nav .stColumn {
    flex: 1 1 0 !important; width: auto !important; min-width: 0 !important; max-width: none !important;
}
.st-key-barra_nav button {
    padding: 0.3rem 0.15rem !important; font-size: 0.68rem !important; line-height: 1.15 !important;
    min-height: 42px !important; white-space: normal !important; word-break: keep-all !important;
}

/* Barra de limite do cartão */
.limite { background-color: var(--secondary-background-color, #ffffff); border: 1px solid rgba(128,128,128,0.25);
    border-radius: 12px; padding: 12px 16px; margin-bottom: 12px; }
.limite-topo { display: flex; justify-content: space-between; gap: 8px; font-size: 0.85rem; }
.limite-topo b { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.barra { height: 10px; border-radius: 999px; background: rgba(128,128,128,0.22); margin: 8px 0 6px 0; overflow: hidden; }
.barra-preench { height: 100%; border-radius: 999px; }
.barra-verde { background: #10b981; } .barra-amarelo { background: #f59e0b; } .barra-vermelho { background: #ef4444; }
.limite-rodape { font-size: 0.76rem; opacity: .75; }
</style>
"""

CSS = "\n".join(linha for linha in CSS.splitlines() if linha.strip())   # sem linhas em branco (Markdown)

# ---------------------------------------------------------
# Textos
# ---------------------------------------------------------
def fmt_pct(valor, casas=1):
    """12.34 -> '12,3%'"""
    return f"{valor:.{casas}f}".replace(".", ",") + "%"


def esc(texto):
    return html.escape(str(texto), quote=True)


# ---------------------------------------------------------
# HTML
# ---------------------------------------------------------
def delta_html(pct, alta_e_boa=True):
    """'▲ 12,3% vs mês anterior' colorido. alta_e_boa=False para despesas (subir é ruim).
    Sem base de comparação: mostra 'sem base de comparação'."""
    if pct is None:
        return '<span class="delta-neutro">— sem base de comparação</span>'
    if abs(pct) < 0.05:
        return '<span class="delta-neutro">= igual ao mês anterior</span>'
    subiu = pct > 0
    bom = subiu == alta_e_boa
    seta = "▲" if subiu else "▼"
    classe = "delta-bom" if bom else "delta-ruim"
    return f'<span class="{classe}">{seta} {fmt_pct(abs(pct))}</span> vs mês anterior'


def kpi(rotulo, valor, tom="neutro", grande=False, detalhe_html=""):
    """Cartão de indicador. `detalhe_html` já deve estar escapado/seguro (use esc() em textos de usuário)."""
    tom = tom if tom in TONS else "neutro"
    cor_valor = CLASSE_TEXTO[tom] if grande and tom in ("positivo", "negativo", "alerta") else ""
    estilo = f' style="color:{cor_valor}"' if cor_valor else ""
    detalhe = f'<div class="kpi-detalhe">{detalhe_html}</div>' if detalhe_html else ""
    return (f'<div class="kpi kpi-{tom}{" kpi-grande" if grande else ""}">'
            f'<div class="kpi-rotulo">{esc(rotulo)}</div>'
            f'<div class="kpi-valor"{estilo}>{esc(valor)}</div>{detalhe}</div>')


SELOS_FATURA = {  # status -> (texto, cor)
    "Paga": ("Paga", "verde"), "Vencida": ("Vencida", "vermelho"), "Parcial": ("Paga em parte", "amarelo"),
    "Aberta": ("Aberta", "azul"), "Vazia": ("Sem lançamentos", "cinza"),
}


def selo(texto, cor):
    return f'<span class="selo selo-{cor}">{esc(texto)}</span>'


def selo_fatura(status):
    texto, cor = SELOS_FATURA.get(status, (status, "cinza"))
    return selo(texto, cor)


def tom_limite(pct):
    """Cor da barra: verde até 60%, amarelo até 85%, vermelho acima."""
    if pct is None or pct < 60:
        return "verde"
    return "amarelo" if pct < 85 else "vermelho"


def limite_html(nome, usado, limite, formatar):
    """Barra do limite usado de um cartão. `formatar` converte centavos em texto (ex.: rg.formatar_brl)."""
    pct = rg.percentual_limite(usado, limite)
    if pct is None:
        return (f'<div class="limite"><div class="limite-topo"><b>{esc(nome)}</b></div>'
                f'<div class="limite-rodape">Limite não informado · em uso {esc(formatar(usado))}</div></div>')
    return (f'<div class="limite"><div class="limite-topo"><b>{esc(nome)}</b>'
            f'<span>{esc(formatar(usado))} / {esc(formatar(limite))}</span></div>'
            f'<div class="barra"><div class="barra-preench barra-{tom_limite(pct)}" style="width:{pct:.0f}%"></div></div>'
            f'<div class="limite-rodape">{fmt_pct(pct, 0)} usado · disponível {esc(formatar(limite - usado))}</div></div>')


# ---------------------------------------------------------
# Tabelas (pandas Styler): formato brasileiro e cores com significado
# ---------------------------------------------------------
def fmt_moeda(v):
    """Reais (float) -> 'R$ 1.234,56'. Vazio para NaN/None."""
    if v is None or pd.isna(v):
        return ""
    return rg.formatar_brl(rg.reais_para_centavos(v))


def fmt_data(v):
    if v is None or pd.isna(v):
        return ""
    return v.strftime("%d/%m/%Y")


def fmt_percentual(v):
    if v is None or pd.isna(v):
        return ""
    return fmt_pct(v)


def _cor_sinal(v):
    """Verde para positivo, vermelho para negativo."""
    try:
        if pd.isna(v) or v == 0:
            return ""
        return "color: #059669; font-weight: 600" if v > 0 else "color: #dc2626; font-weight: 600"
    except TypeError:
        return ""


def _cor_so_negativo(v):
    try:
        return "color: #dc2626; font-weight: 600" if (not pd.isna(v) and v < 0) else ""
    except TypeError:
        return ""


def _cor_status(v):
    t = str(v)
    if any(p in t for p in ("Vencida", "Atrasada")):
        return "color: #dc2626; font-weight: 600"
    if any(p in t for p in ("Pendente", "A vencer", "Aberta", "em parte")):
        return "color: #b45309; font-weight: 600"
    if any(p in t for p in ("Paga", "Pago")):
        return "color: #059669; font-weight: 600"
    return ""


def _mapear(styler, fn, coluna):
    """Styler.map (pandas >= 2.1) ou applymap (versões antigas)."""
    aplicar = getattr(styler, "map", None) or styler.applymap
    return aplicar(fn, subset=[coluna])


def tabela(df, moeda=(), datas=(), percentuais=(), sinal=(), so_negativo=(), status=(), realce_atraso=None):
    """Styler pronto para st.dataframe.

    moeda / datas / percentuais : colunas formatadas no padrão brasileiro
    sinal          : verde se positivo, vermelho se negativo (ex.: valor do extrato)
    so_negativo    : só destaca negativos (ex.: saldo)
    status         : colorir textos como Vencida/Atrasada, Pendente/Aberta, Paga/Pago
    realce_atraso  : nome de uma coluna de texto; linhas cujo valor contém 'Vencida' ou 'Atrasada' ganham fundo avermelhado
    """
    st_ = df.style
    formatos = {c: fmt_moeda for c in moeda}
    formatos.update({c: fmt_data for c in datas})
    formatos.update({c: fmt_percentual for c in percentuais})
    if formatos:
        st_ = st_.format(formatos, na_rep="")
    for c in sinal:
        st_ = _mapear(st_, _cor_sinal, c)
    for c in so_negativo:
        st_ = _mapear(st_, _cor_so_negativo, c)
    for c in status:
        st_ = _mapear(st_, _cor_status, c)
    if realce_atraso:
        def _linha(row):
            atrasada = any(p in str(row[realce_atraso]) for p in ("Vencida", "Atrasada"))
            return ["background-color: rgba(239,68,68,0.10)" if atrasada else "" for _ in row]
        st_ = st_.apply(_linha, axis=1)
    return st_
