import os
import sys


def env(name: str, default: str = "", required: bool = False) -> str:
    value = os.getenv(name, default).strip()

    if required and not value:
        print(f"ERRO: variável {name} não configurada.")
        sys.exit(1)

    return value


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()

    if not value:
        raise RuntimeError(f"Variável {name} não configurada.")

    return value
