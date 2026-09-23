"""Regras puras do sistema: constantes, dinheiro, CPF, parcelas e datas de fatura.

Este módulo NÃO depende de banco de dados nem de Streamlit, para poder ser
testado de forma isolada e rápida.
"""
import calendar
import datetime
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from dateutil.relativedelta import relativedelta

# ---------------------------------------------------------
# Constantes
# ---------------------------------------------------------
ENTRADA = "Entrada"
SAIDA = "Saída"
TIPO_SISTEMA = "Sistema"  # categorias internas (não aparecem nos formulários)

KIND_NORMAL = "normal"
KIND_PAGTO_FATURA = "pagamento_fatura"
KIND_TRANSFERENCIA = "transferencia"

SERIE_PARCELADO = "parcelado"
SERIE_RECORRENTE = "recorrente"

MAX_USUARIOS = 6
MAX_CONTAS = 10
MAX_CARTOES = 10

CAT_PAGTO_FATURA = "Pagamento de Fatura de Cartão"
CAT_TRANSFERENCIA = "Transferência entre Contas"

CATEGORIAS_PADRAO = [
    ("Alimentação & Mercado", SAIDA),
    ("Moradia (Aluguel/Condomínio/IPTU)", SAIDA),
    ("Serviços Públicos (Água/Luz/Gás)", SAIDA),
    ("Transporte & Combustível", SAIDA),
    ("Sergio Pessoal", SAIDA),
    ("Patricia Pessoal", SAIDA),
    ("Lazer & Restaurantes", SAIDA),
    ("Saúde & Farmácia", SAIDA),
    ("Assinaturas & Streaming", SAIDA),
    ("Despesas Veiculo", SAIDA),
    ("Despesas com PETs", SAIDA),
    ("Vestuários/Calçados", SAIDA),
    ("Educação & Cursos", SAIDA),
    ("Moveis/Eletro/Eletronicos", SAIDA),
    ("Manutenção Residencial", SAIDA),
    ("Obras e Serviços", SAIDA),
    ("Férias & Viagens", SAIDA),
    ("Festas e Presentes", SAIDA),
    ("Empréstimos & Recebíveis", SAIDA),
    ("Outras Despesas", SAIDA),
    ("Salário / Pró-labore", ENTRADA),
    ("Investimentos & Rendimentos", ENTRADA),
    ("Vendas & Extra", ENTRADA),
    ("Outras Receitas", ENTRADA),
    # Categorias de sistema: usadas automaticamente pelo programa
    (CAT_PAGTO_FATURA, TIPO_SISTEMA),
    (CAT_TRANSFERENCIA, TIPO_SISTEMA),
]

MESES = [
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]


# ---------------------------------------------------------
# CPF
# ---------------------------------------------------------
def validar_cpf(cpf: str) -> bool:
    cpf = re.sub(r"\D", "", cpf or "")
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False

    soma = sum(int(cpf[i]) * (10 - i) for i in range(9))
    resto = (soma * 10) % 11
    digito1 = 0 if resto == 10 else resto
    if int(cpf[9]) != digito1:
        return False

    soma = sum(int(cpf[i]) * (11 - i) for i in range(10))
    resto = (soma * 10) % 11
    digito2 = 0 if resto == 10 else resto
    return int(cpf[10]) == digito2


def formatar_cpf(cpf: str) -> str:
    n = re.sub(r"\D", "", cpf)
    return f"{n[:3]}.{n[3:6]}.{n[6:9]}-{n[9:]}"


def mascarar_cpf(cpf: str) -> str:
    """123.456.789-09 -> ***.456.789-**"""
    n = re.sub(r"\D", "", cpf)
    if len(n) != 11:
        return "***"
    return f"***.{n[3:6]}.{n[6:9]}-**"


# ---------------------------------------------------------
# Dinheiro (sempre em centavos inteiros)
# ---------------------------------------------------------
def reais_para_centavos(valor) -> int:
    """Converte reais (float, str ou Decimal) em centavos inteiros, com arredondamento comercial."""
    try:
        d = Decimal(str(valor).strip().replace(",", "."))
    except InvalidOperation:
        raise ValueError(f"Valor monetário inválido: {valor!r}")
    if not d.is_finite():
        raise ValueError(f"Valor monetário inválido: {valor!r}")
    return int((d * 100).to_integral_value(rounding=ROUND_HALF_UP))


def centavos_para_reais(centavos: int) -> float:
    """Apenas para exibição em gráficos/tabelas. Nunca use o resultado em cálculos."""
    return centavos / 100


def formatar_brl(centavos: int) -> str:
    """123456 -> 'R$ 1.234,56'"""
    sinal = "-" if centavos < 0 else ""
    reais, cent = divmod(abs(int(centavos)), 100)
    return f"{sinal}R$ {reais:,}".replace(",", ".") + f",{cent:02d}"


def dividir_em_parcelas(total_centavos: int, n: int) -> list:
    """Divide o total em n parcelas cuja soma é EXATAMENTE o total.
    O resto (centavos) vai para as primeiras parcelas: 10000 em 3x -> [3334, 3333, 3333]."""
    if n < 1:
        raise ValueError("O número de parcelas deve ser pelo menos 1.")
    base, resto = divmod(total_centavos, n)
    return [base + (1 if i < resto else 0) for i in range(n)]


# ---------------------------------------------------------
# Datas de fatura de cartão
# ---------------------------------------------------------
def limites_mes(ano: int, mes: int):
    """Retorna (primeiro dia do mês, primeiro dia do mês seguinte)."""
    ini = datetime.date(ano, mes, 1)
    return ini, ini + relativedelta(months=1)


def vencimento_no_mes(due_day: int, ano: int, mes: int) -> datetime.date:
    """Vencimento no mês, ajustando para meses curtos (dia 31 em fevereiro -> 28/29)."""
    ultimo = calendar.monthrange(ano, mes)[1]
    return datetime.date(ano, mes, min(due_day, ultimo))


def vencimento_da_fatura(due_day: int, closing_days_before: int, data_compra: datetime.date) -> datetime.date:
    """Vencimento da primeira fatura que recebe a compra.

    O fechamento é `closing_days_before` dias antes do vencimento. Compras feitas
    ATÉ o dia anterior ao fechamento entram na fatura; compras no dia do fechamento
    ou depois vão para a fatura seguinte.
    """
    base = data_compra.replace(day=1)
    for i in range(4):
        m = base + relativedelta(months=i)
        venc = vencimento_no_mes(due_day, m.year, m.month)
        fechamento = venc - datetime.timedelta(days=closing_days_before)
        if data_compra < fechamento:
            return venc
    raise ValueError("Não foi possível determinar a fatura da compra.")  # não deve ocorrer


def gerar_vencimentos_fatura(due_day: int, closing_days_before: int,
                             data_compra: datetime.date, quantidade: int) -> list:
    """Lista de vencimentos de fatura para cada parcela/recorrência.

    A primeira fatura é calculada UMA vez; as demais são os meses seguintes.
    (Somar meses à data da compra e recalcular a fatura causava parcelas
    caindo duas vezes no mesmo mês em fechamentos próximos ao fim do mês.)
    """
    primeiro = vencimento_da_fatura(due_day, closing_days_before, data_compra)
    base = primeiro.replace(day=1)
    vencimentos = []
    for i in range(quantidade):
        m = base + relativedelta(months=i)
        vencimentos.append(vencimento_no_mes(due_day, m.year, m.month))
    return vencimentos


# ---------------------------------------------------------
# Competência da fatura (por cartão)
# ---------------------------------------------------------
OPCOES_COMPETENCIA = {0: "Mês do vencimento", 1: "Mês anterior ao vencimento"}


def vencimento_da_competencia(ano: int, mes: int, offset: int) -> tuple:
    """Competência (ano, mês) -> mês em que a fatura VENCE. offset=1: 'Setembro' vence em outubro."""
    d = datetime.date(ano, mes, 1) + relativedelta(months=offset)
    return d.year, d.month


def competencia_do_vencimento(ano: int, mes: int, offset: int) -> tuple:
    """Mês de vencimento (ano, mês) -> competência da fatura (o inverso da função acima)."""
    d = datetime.date(ano, mes, 1) - relativedelta(months=offset)
    return d.year, d.month


# ---------------------------------------------------------
# Apoio à apresentação (lógica pura, testável)
# ---------------------------------------------------------
def agrupar_top_n(pares, n=8, rotulo_outras="Outras"):
    """[(nome, centavos)] -> as n maiores + uma linha 'Outras (k)' somando o resto.
    Ex.: 12 categorias, n=8 -> 8 maiores + ('Outras (4)', soma das 4 menores)."""
    ordenados = sorted(pares, key=lambda p: -p[1])
    topo, resto = ordenados[:n], ordenados[n:]
    if resto:
        topo.append((f"{rotulo_outras} ({len(resto)})", sum(v for _, v in resto)))
    return topo


def variacao_percentual(atual, anterior):
    """Variação % em relação ao período anterior. None quando não há base de comparação (anterior = 0)."""
    if not anterior:
        return None
    return (atual - anterior) / abs(anterior) * 100


def status_fatura(total, pago, vencimento, hoje):
    """Vazia | Paga | Vencida | Parcial | Aberta  (valores em centavos)."""
    restante = max(total - pago, 0)
    if total <= 0:
        return "Vazia"
    if restante == 0:
        return "Paga"
    if vencimento < hoje:
        return "Vencida"
    if pago > 0:
        return "Parcial"
    return "Aberta"


def percentual_limite(usado, limite):
    """% do limite usado, entre 0 e 100 (None se o limite não foi informado)."""
    if limite <= 0:
        return None
    return max(0.0, min(100.0, usado / limite * 100))
