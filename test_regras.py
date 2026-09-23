import datetime

import pytest

from regras import (agrupar_top_n, competencia_do_vencimento, dividir_em_parcelas, formatar_brl, gerar_vencimentos_fatura,
                    limites_mes, mascarar_cpf, percentual_limite,
                    reais_para_centavos, status_fatura, validar_cpf, variacao_percentual, vencimento_da_competencia, vencimento_da_fatura,
                    vencimento_no_mes)

D = datetime.date


# ---------- CPF ----------
def test_cpf_valido():
    assert validar_cpf("529.982.247-25")
    assert validar_cpf("52998224725")


@pytest.mark.parametrize("cpf", ["", "111.111.111-11", "529.982.247-24", "123", "abc"])
def test_cpf_invalido(cpf):
    assert not validar_cpf(cpf)


def test_mascara_cpf():
    assert mascarar_cpf("529.982.247-25") == "***.982.247-**"


# ---------- Dinheiro ----------
def test_reais_para_centavos_sem_erro_de_float():
    assert reais_para_centavos(19.99) == 1999
    assert reais_para_centavos(0.1 + 0.2) == 30      # 0.30000000000000004 em float
    assert reais_para_centavos("1234,56") == 123456
    assert reais_para_centavos(1.005) == 101         # arredondamento comercial
    assert reais_para_centavos(0) == 0


def test_reais_para_centavos_invalido():
    with pytest.raises(ValueError):
        reais_para_centavos("abc")
    with pytest.raises(ValueError):
        reais_para_centavos(float("nan"))


def test_formatar_brl():
    assert formatar_brl(123456) == "R$ 1.234,56"
    assert formatar_brl(0) == "R$ 0,00"
    assert formatar_brl(5) == "R$ 0,05"
    assert formatar_brl(-5) == "-R$ 0,05"
    assert formatar_brl(100000000) == "R$ 1.000.000,00"


# ---------- Parcelas ----------
def test_parcelas_somam_exatamente_o_total():
    assert dividir_em_parcelas(10000, 3) == [3334, 3333, 3333]
    for total in (1, 99, 10000, 123457):
        for n in (1, 2, 3, 7, 12, 48):
            partes = dividir_em_parcelas(total, n)
            assert sum(partes) == total
            assert len(partes) == n
            assert max(partes) - min(partes) <= 1


def test_parcelas_invalidas():
    with pytest.raises(ValueError):
        dividir_em_parcelas(1000, 0)


# ---------- Datas de fatura ----------
def test_vencimento_no_mes_ajusta_meses_curtos():
    assert vencimento_no_mes(31, 2026, 2) == D(2026, 2, 28)
    assert vencimento_no_mes(31, 2028, 2) == D(2028, 2, 29)   # bissexto
    assert vencimento_no_mes(31, 2026, 4) == D(2026, 4, 30)
    assert vencimento_no_mes(10, 2026, 1) == D(2026, 1, 10)


def test_fatura_venc_10_fecha_dia_3():
    # vence dia 10, fecha 7 dias antes (dia 3)
    assert vencimento_da_fatura(10, 7, D(2026, 1, 2)) == D(2026, 1, 10)
    assert vencimento_da_fatura(10, 7, D(2026, 1, 3)) == D(2026, 2, 10)   # no dia do fechamento -> próxima
    assert vencimento_da_fatura(10, 7, D(2026, 1, 15)) == D(2026, 2, 10)


def test_fatura_vencimento_no_inicio_do_mes_bug_original():
    # Bug original: venc. dia 5 e fechamento 7 dias antes virava "dia 28" e errava o mês.
    # Fecha em 29/jan para a fatura de 05/fev.
    assert vencimento_da_fatura(5, 7, D(2026, 1, 10)) == D(2026, 2, 5)
    assert vencimento_da_fatura(5, 7, D(2026, 1, 28)) == D(2026, 2, 5)
    assert vencimento_da_fatura(5, 7, D(2026, 1, 29)) == D(2026, 3, 5)
    assert vencimento_da_fatura(5, 7, D(2026, 1, 30)) == D(2026, 3, 5)


def test_fatura_virada_de_ano():
    assert vencimento_da_fatura(5, 7, D(2025, 12, 20)) == D(2026, 1, 5)   # fecha 29/12
    assert vencimento_da_fatura(5, 7, D(2025, 12, 31)) == D(2026, 2, 5)


def test_fatura_vencimento_dia_31_em_mes_curto():
    # vence dia 31, fecha 5 dias antes
    assert vencimento_da_fatura(31, 5, D(2026, 2, 10)) == D(2026, 2, 28)  # fecha 23/fev
    assert vencimento_da_fatura(31, 5, D(2026, 2, 25)) == D(2026, 3, 31)


def test_parcelas_nunca_caem_duas_vezes_no_mesmo_mes():
    # Bug original: fechamento perto do fim do mês fazia parcelas colidirem em fevereiro.
    vencs = gerar_vencimentos_fatura(10, 7, D(2026, 1, 31), 4)
    assert vencs == [D(2026, 2, 10), D(2026, 3, 10), D(2026, 4, 10), D(2026, 5, 10)]

    vencs = gerar_vencimentos_fatura(31, 5, D(2026, 1, 10), 4)
    assert vencs == [D(2026, 1, 31), D(2026, 2, 28), D(2026, 3, 31), D(2026, 4, 30)]
    meses = [(v.year, v.month) for v in vencs]
    assert len(set(meses)) == len(meses)


def test_gerar_vencimentos_atravessa_o_ano():
    vencs = gerar_vencimentos_fatura(10, 7, D(2026, 11, 20), 3)
    assert vencs == [D(2026, 12, 10), D(2027, 1, 10), D(2027, 2, 10)]


def test_limites_mes():
    assert limites_mes(2026, 12) == (D(2026, 12, 1), D(2027, 1, 1))
    assert limites_mes(2026, 2) == (D(2026, 2, 1), D(2026, 3, 1))


# ---------- Competência da fatura ----------
def test_competencia_mes_anterior_ao_vencimento():
    # fatura que vence em 04/10 é a de setembro
    assert vencimento_da_competencia(2026, 9, 1) == (2026, 10)
    assert competencia_do_vencimento(2026, 10, 1) == (2026, 9)
    # virada de ano
    assert vencimento_da_competencia(2026, 12, 1) == (2027, 1)
    assert competencia_do_vencimento(2027, 1, 1) == (2026, 12)


def test_competencia_mes_do_vencimento_nao_muda_nada():
    assert vencimento_da_competencia(2026, 9, 0) == (2026, 9)
    assert competencia_do_vencimento(2026, 9, 0) == (2026, 9)
    for ano, mes in ((2026, 1), (2026, 12), (2027, 6)):
        assert competencia_do_vencimento(*vencimento_da_competencia(ano, mes, 1), 1) == (ano, mes)


# ---------- Apoio à apresentação ----------
def test_agrupar_top_n_soma_o_resto():
    pares = [(f"C{i}", 100 * i) for i in range(1, 13)]           # 12 categorias, do menor ao maior
    topo = agrupar_top_n(pares, 8)
    assert len(topo) == 9
    assert [n for n, _ in topo[:8]] == [f"C{i}" for i in range(12, 4, -1)]   # ordena do maior ao menor
    assert topo[8] == ("Outras (4)", 100 + 200 + 300 + 400)
    assert sum(v for _, v in topo) == sum(v for _, v in pares)               # nada se perde


def test_agrupar_top_n_sem_resto_nao_cria_outras():
    pares = [("A", 5), ("B", 9), ("C", 1)]
    assert agrupar_top_n(pares, 8) == [("B", 9), ("A", 5), ("C", 1)]
    assert agrupar_top_n([], 8) == []


def test_variacao_percentual():
    assert variacao_percentual(150, 100) == 50
    assert variacao_percentual(50, 100) == -50
    assert variacao_percentual(100, 100) == 0
    assert variacao_percentual(10, 0) is None and variacao_percentual(0, 0) is None
    assert variacao_percentual(-50, -100) == 50                  # base negativa: usa o módulo


def test_status_fatura():
    hoje = D(2026, 9, 19)
    assert status_fatura(0, 0, D(2026, 9, 1), hoje) == "Vazia"
    assert status_fatura(1000, 1000, D(2026, 9, 1), hoje) == "Paga"
    assert status_fatura(1000, 0, D(2026, 9, 1), hoje) == "Vencida"
    assert status_fatura(1000, 400, D(2026, 9, 1), hoje) == "Vencida"    # vencida tem prioridade sobre parcial
    assert status_fatura(1000, 400, D(2026, 10, 4), hoje) == "Parcial"
    assert status_fatura(1000, 0, D(2026, 10, 4), hoje) == "Aberta"
    assert status_fatura(1000, 0, D(2026, 9, 19), hoje) == "Aberta"      # vence hoje ainda não venceu


def test_percentual_limite():
    assert percentual_limite(250, 1000) == 25
    assert percentual_limite(2000, 1000) == 100                  # nunca passa de 100 na barra
    assert percentual_limite(-50, 1000) == 0
    assert percentual_limite(100, 0) is None
