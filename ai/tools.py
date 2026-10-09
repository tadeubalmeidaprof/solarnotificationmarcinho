from solar_queries import (
    compare_months,
    get_active_faults_summary,
    get_comprehensive_analysis,
    get_curve_anomaly_analysis,
    get_fault_code_info,
    get_generation_period,
    get_maintenance_status,
    get_maintenance_impact,
    get_performance_diagnostic,
    get_plant_status,
    get_recent_generation,
    get_real_maintenance_history,
    get_savings_summary,
    get_solar_generation_hours,
    get_slow_degradation_analysis,
    get_weather_window_summary,
    record_maintenance_event,
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

    {
        "type": "function",
        "function": {
            "name": "analisar_curva_geracao",
            "description": (
                "Analisa a curva de potência contra o histórico da própria usina. "
                "Retorna score de anomalia 0-100, componentes de forma, energia, "
                "pico, interrupções, janela produtiva, volatilidade e telemetria, "
                "além de persistência e confiança. Usa recência e clima para "
                "selecionar dias comparáveis."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "report_date": {
                        "type": "string",
                        "description": "Data no formato YYYY-MM-DD.",
                    },
                    "start_hour": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 23,
                        "description": "Hora inicial. Padrão: 7.",
                    },
                    "end_hour": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 24,
                        "description": "Hora final exclusiva. Padrão: 17.",
                    },
                },
                "required": ["report_date"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_historico_manutencao",
            "description": (
                "Consulta manutenções reais já realizadas e registradas pelo "
                "usuário, como limpeza, inspeção, reparo ou troca de componente."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 30,
                        "description": "Quantidade máxima de eventos. Padrão: 10.",
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Data inicial opcional no formato YYYY-MM-DD.",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "Data final opcional no formato YYYY-MM-DD.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "registrar_manutencao_real",
            "description": (
                "Registra uma manutenção que o usuário afirmou que realmente "
                "foi realizada. Não use para manutenção planejada, hipótese, "
                "recomendação ou algo ainda não confirmado."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "event_date": {
                        "type": "string",
                        "description": "Data da manutenção no formato YYYY-MM-DD.",
                    },
                    "event_type": {
                        "type": "string",
                        "enum": [
                            "cleaning",
                            "inspection",
                            "preventive",
                            "corrective",
                            "repair",
                            "replacement",
                            "electrical",
                            "inverter",
                            "panel",
                            "other"
                        ],
                        "description": "Categoria da manutenção realizada.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Descrição objetiva do que foi realizado.",
                    },
                    "performed_by": {
                        "type": "string",
                        "description": "Quem realizou, se informado.",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Observações adicionais, se houver.",
                    },
                },
                "required": ["event_date", "event_type", "description"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "avaliar_impacto_manutencao",
            "description": (
                "Compara o desempenho antes e depois da última manutenção real "
                "registrada, preferindo dias com clima semelhante. Use quando "
                "o usuário perguntar se limpeza, reparo ou outra intervenção "
                "melhorou ou piorou o desempenho."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "event_type": {
                        "type": "string",
                        "description": (
                            "Tipo opcional de manutenção registrada."
                        ),
                    },
                    "days_before": {
                        "type": "integer",
                        "minimum": 3,
                        "maximum": 30,
                        "description": "Janela antes da manutenção. Padrão: 7.",
                    },
                    "days_after": {
                        "type": "integer",
                        "minimum": 3,
                        "maximum": 30,
                        "description": "Janela depois da manutenção. Padrão: 7.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_degradacao_lenta",
            "description": (
                "Analisa perda operacional lenta/progressiva no histórico da "
                "usina. Combina Theil-Sen, Mann-Kendall, EWMA/CUSUM, "
                "persistência, qualidade dos dados, clima e histórico de "
                "manutenção. Use para perguntas sobre degradação, perda "
                "gradual de rendimento ou piora ao longo de semanas/meses."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "window_days": {
                        "type": "integer",
                        "minimum": 45,
                        "maximum": 366,
                        "description": (
                            "Janela histórica em dias. Padrão: 180."
                        ),
                    },
                },
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
    "analisar_curva_geracao": (
        "get_curve_anomaly_analysis",
        {"report_date"},
        {"report_date", "start_hour", "end_hour"},
    ),
    "consultar_historico_manutencao": (
        "get_real_maintenance_history",
        set(),
        {"limit", "start_date", "end_date"},
    ),
    "registrar_manutencao_real": (
        "record_maintenance_event",
        {"event_date", "event_type", "description"},
        {"event_date", "event_type", "description", "performed_by", "notes"},
    ),
    "avaliar_impacto_manutencao": (
        "get_maintenance_impact",
        set(),
        {"event_type", "days_before", "days_after"},
    ),
    "consultar_degradacao_lenta": (
        "get_slow_degradation_analysis",
        set(),
        {"window_days"},
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
