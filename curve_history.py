import time
import argparse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from solar_queries import collect_curve_analysis_for_date


REPORT_TIMEZONE = ZoneInfo("America/Bahia")


def _dates_to_process(days: int):
    safe_days = max(1, min(int(days), 45))
    today = datetime.now(REPORT_TIMEZONE).date()
    start = today - timedelta(days=safe_days - 1)

    current = start
    while current <= today:
        yield current
        current += timedelta(days=1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Persiste curvas solares e calcula o baseline histórico "
            "da própria usina."
        )
    )
    parser.add_argument(
        "--days",
        type=int,
        default=1,
        help="Quantidade de dias até hoje para processar. Máximo: 45.",
    )
    args = parser.parse_args()

    failures = []

    for report_date in _dates_to_process(args.days):
        result = None
        last_error = None

        for attempt in range(1, 4):
            try:
                result = collect_curve_analysis_for_date(
                    report_date.isoformat(),
                    start_hour=7,
                    end_hour=17,
                    persist=True,
                )
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                print(
                    "Tentativa de curva falhou:",
                    {
                        "date": report_date.isoformat(),
                        "attempt": attempt,
                        "error": type(exc).__name__,
                    },
                )
                if attempt < 3:
                    time.sleep(2 * attempt)

        if last_error is not None or result is None:
            failures.append(
                {
                    "date": report_date.isoformat(),
                    "error": (
                        type(last_error).__name__
                        if last_error is not None
                        else "UnknownError"
                    ),
                }
            )
            continue

        analysis = result.get("analysis") or {}
        print(
            "Curva processada:",
            {
                "date": report_date.isoformat(),
                "available": bool(analysis.get("available")),
                "status": analysis.get("status"),
                "anomaly_score": analysis.get("anomaly_score"),
                "confidence": analysis.get("confidence"),
                "baseline_days_used": analysis.get(
                    "baseline_days_used"
                ),
                "persisted": (
                    result.get("storage", {}).get("persisted")
                ),
            },
        )

    if failures:
        raise SystemExit(
            f"{len(failures)} dia(s) não puderam ser processados."
        )


if __name__ == "__main__":
    main()
