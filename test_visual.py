import datetime

import pandas as pd

import visual as vz

D = datetime.date


def test_fmt_pct():
    assert vz.fmt_pct(12.34) == "12,3%"
    assert vz.fmt_pct(60, 0) == "60%"


def test_delta_html_cores_dependem_do_tipo_de_indicador():
    assert "delta-bom" in vz.delta_html(10, alta_e_boa=True) and "▲ 10,0%" in vz.delta_html(10)
    assert "delta-ruim" in vz.delta_html(10, alta_e_boa=False)        # despesa subindo é ruim
    assert "delta-bom" in vz.delta_html(-10, alta_e_boa=False)        # despesa caindo é bom
    assert "▼ 10,0%" in vz.delta_html(-10)
    assert "sem base" in vz.delta_html(None)
    assert "igual" in vz.delta_html(0.01)


def test_kpi_escapa_html_e_aplica_tom():
    h = vz.kpi("<b>Meu</b> cartão", "R$ 1,00", tom="negativo", grande=True, detalhe_html="ok")
    assert "&lt;b&gt;Meu&lt;/b&gt;" in h and "<b>Meu</b>" not in h
    assert "kpi-negativo" in h and "kpi-grande" in h and "kpi-detalhe" in h
    assert "color:#dc2626" in h                                          # valor grande colorido
    pequeno = vz.kpi("x", "y", tom="positivo")
    assert "kpi-grande" not in pequeno and "color:" not in pequeno
    assert "kpi-neutro" in vz.kpi("x", "y", tom="inexistente")            # tom inválido cai em neutro


def test_barra_de_limite_muda_de_cor():
    f = lambda c: f"R$ {c / 100:.2f}"
    assert "barra-verde" in vz.limite_html("A", 25000, 100000, f) and "width:25%" in vz.limite_html("A", 25000, 100000, f)
    assert "barra-amarelo" in vz.limite_html("A", 70000, 100000, f)
    assert "barra-vermelho" in vz.limite_html("A", 90000, 100000, f)
    assert "width:100%" in vz.limite_html("A", 250000, 100000, f)          # acima do limite: barra cheia
    assert "Limite não informado" in vz.limite_html("A", 5000, 0, f)
    assert "&lt;script&gt;" in vz.limite_html("<script>", 1, 100, f)


def test_selos_de_fatura():
    assert "selo-verde" in vz.selo_fatura("Paga")
    assert "selo-vermelho" in vz.selo_fatura("Vencida")
    assert "Paga em parte" in vz.selo_fatura("Parcial")
    assert "selo-cinza" in vz.selo_fatura("qualquer")


def test_tabela_formata_em_padrao_brasileiro():
    df = pd.DataFrame({"Data": [D(2026, 9, 1), None], "Valor": [1234.56, -5.0], "Saldo": [-10.0, 20.0],
                       "%": [12.34, None], "Status": ["🔴 Vencida", "🟢 Paga"]})
    html = vz.tabela(df, moeda=("Valor", "Saldo"), datas=("Data",), percentuais=("%",), sinal=("Valor",),
                     so_negativo=("Saldo",), status=("Status",)).to_html()
    assert "R$ 1.234,56" in html and "-R$ 5,00" in html          # milhar com ponto, decimal com vírgula
    assert "01/09/2026" in html
    assert "12,3%" in html
    assert "color: #dc2626" in html and "color: #059669" in html  # vermelho/verde aplicados
    assert "R$ 1234.56" not in html                               # formato do Streamlit não vaza


def test_tabela_realca_linhas_atrasadas():
    df = pd.DataFrame({"Situação": ["🔴 Atrasada", "🟡 A vencer"], "Valor": [10.0, 20.0]})
    html = vz.tabela(df, moeda=("Valor",), realce_atraso="Situação").to_html()
    css = html.split("</style>")[0]                               # regras CSS geradas pelo Styler
    assert "rgba(239,68,68,0.10)" in css
    assert "row0_col0" in css and "row0_col1" in css              # a linha atrasada inteira (2 células)
    assert "row1" not in css                                      # e somente ela


def test_tabela_sem_formatacao_nao_quebra():
    assert "a" in vz.tabela(pd.DataFrame({"a": [1]})).to_html()
    assert vz.tabela(pd.DataFrame({"Valor": []}), moeda=("Valor",)).to_html()   # tabela vazia
