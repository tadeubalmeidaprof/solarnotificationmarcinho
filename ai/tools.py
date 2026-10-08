from solar_queries import (
    compare_months,
    get_active_faults_summary,
    get_comprehensive_analysis,
    get_fault_code_info,
    get_generation_period,
    get_maintenance_status,
    get_performance_diagnostic,
    get_plant_status,
    get_recent_generation,
    get_savings_summary,
    get_solar_generation_hours,
    get_weather_window_summary,
)


def _no_arguments_tool(name: str, description: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    }


TOOL_DEFINITIONS = [
    _no_arguments_tool(
        "consultar_resumo_usina",
        (
            "Consulta o estado atual da usina Solis, incluindo status, "
            "potência atual, capacidade instalada e geração de hoje, mês, ano e total."
        ),
    ),
    _no_arguments_tool(
        "consultar_falhas_ativas",
        (
            "Consulta alarmes Solis que ainda estão ativos. "
            "Tenta a SolisCloud ao vivo e usa o banco como fallback."
        ),
    ),
    _no_arguments_tool(
        "consultar_manutencao",
        (
            "Consulta alertas abertos da análise preventiva de desempenho, "
            "como possível sujeira, sombreamento ou inversor offline."
        ),
    ),
    {
        "type": "function",
        "function": {
            "name": "consultar_geracao_periodo",
            "description": (
                "Consulta a geração diária acumulada em um intervalo de até 93 dias. "
                "Use para ontem, última semana ou datas específicas."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "Data inicial YYYY-MM-DD."},
                    "end_date": {"type": "string", "description": "Data final YYYY-MM-DD."},
                },
                "required": ["start_date", "end_date"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_ultimos_dias",
            "description": (
                "Consulta a geração dos últimos N dias incluindo hoje. "
                "Use para frases como 'últimos 7 dias'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 31,
                        "description": "Quantidade de dias, incluindo hoje.",
                    },
                },
                "required": ["days"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "comparar_meses",
            "description": (
                "Compara a geração de dois meses. Se um deles for o mês atual, "
                "retorna uma comparação equivalente até o mesmo dia do mês."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "first_year_month": {"type": "string", "description": "Primeiro mês YYYY-MM."},
                    "second_year_month": {"type": "string", "description": "Segundo mês YYYY-MM."},
                },
                "required": ["first_year_month", "second_year_month"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_economia_mes",
            "description": (
                "Calcula a economia estimada de um mês com base na geração "
                "e nas tarifas configuradas no SolCare."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "year_month": {"type": "string", "description": "Mês YYYY-MM."},
                },
                "required": ["year_month"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_clima",
            "description": (
                "Consulta o clima em uma faixa horária do dia. "
                "Padrão: 07:00 até 17:00."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "report_date": {"type": "string", "description": "Data YYYY-MM-DD."},
                    "start_hour": {"type": "integer", "minimum": 0, "maximum": 23},
                    "end_hour": {"type": "integer", "minimum": 1, "maximum": 24},
                },
                "required": ["report_date"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_horas_solares_usina",
            "description": (
                "Calcula horas de geração relevante e horas equivalentes em potência plena "
                "usando a curva real da Solis. Padrão: 07:00-17:00."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "report_date": {"type": "string", "description": "Data YYYY-MM-DD."},
                    "start_hour": {"type": "integer", "minimum": 0, "maximum": 23},
                    "end_hour": {"type": "integer", "minimum": 1, "maximum": 24},
                },
                "required": ["report_date"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diagnosticar_desempenho_diario",
            "description": (
                "Avalia se a geração diária faz sentido para o padrão da própria usina, "
                "cruzando curva Solis, geração, histórico, clima, temperatura, falhas "
                "e alertas. Use para perguntas sobre baixa geração e manutenção."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "report_date": {"type": "string", "description": "Data YYYY-MM-DD."},
                    "start_hour": {"type": "integer", "minimum": 0, "maximum": 23},
                    "end_hour": {"type": "integer", "minimum": 1, "maximum": 24},
                },
                "required": ["report_date"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explicar_codigo_falha",
            "description": (
                "Explica um código de alarme Solis já observado no monitoramento, "
                "incluindo mensagem e orientação fornecidas pela SolisCloud."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Código de alarme Solis."},
                },
                "required": ["code"],
                "additionalProperties": False,
            },
        },
    },
    _no_arguments_tool(
        "analisar_usina",
        (
            "Executa uma visão consolidada da usina: estado atual, falhas, "
            "manutenção, desempenho, clima e economia quando disponíveis."
        ),
    ),
]


class AIToolError(RuntimeError):
    pass


_TOOL_HANDLERS = {
    "consultar_resumo_usina": ("get_plant_status", set(), set()),
    "consultar_falhas_ativas": ("get_active_faults_summary", set(), set()),
    "consultar_manutencao": ("get_maintenance_status", set(), set()),
    "consultar_geracao_periodo": (
        "get_generation_period", {"start_date", "end_date"}, {"start_date", "end_date"}
    ),
    "consultar_ultimos_dias": ("get_recent_generation", {"days"}, {"days"}),
    "comparar_meses": (
        "compare_months",
        {"first_year_month", "second_year_month"},
        {"first_year_month", "second_year_month"},
    ),
    "consultar_economia_mes": ("get_savings_summary", {"year_month"}, {"year_month"}),
    "consultar_clima": (
        "get_weather_window_summary",
        {"report_date"},
        {"report_date", "start_hour", "end_hour"},
    ),
    "consultar_horas_solares_usina": (
        "get_solar_generation_hours",
        {"report_date"},
        {"report_date", "start_hour", "end_hour"},
    ),
    "diagnosticar_desempenho_diario": (
        "get_performance_diagnostic",
        {"report_date"},
        {"report_date", "start_hour", "end_hour"},
    ),
    "explicar_codigo_falha": ("get_fault_code_info", {"code"}, {"code"}),
    "analisar_usina": ("get_comprehensive_analysis", set(), set()),
}


def execute_tool(name: str, arguments: dict) -> dict:
    if not isinstance(arguments, dict):
        raise AIToolError("Argumentos da ferramenta devem ser um objeto.")

    spec = _TOOL_HANDLERS.get(name)
    if spec is None:
        raise AIToolError("Ferramenta não reconhecida.")

    handler_name, required, allowed = spec
    received = set(arguments)

    if received - allowed:
        raise AIToolError("A ferramenta recebeu argumentos não permitidos.")

    if required - received:
        raise AIToolError("A ferramenta não recebeu todos os argumentos obrigatórios.")

    handler = globals()[handler_name]
    try:
        return handler(**arguments)
    except (TypeError, ValueError) as exc:
        raise AIToolError(str(exc)) from exc
