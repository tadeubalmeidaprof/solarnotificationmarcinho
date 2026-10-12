import logging
import math
import os
from collections.abc import Callable

from solisclient import SolisAPIError


logger = logging.getLogger(__name__)


class SolisLocationError(RuntimeError):
    pass


def coordinates_from_station(station: dict) -> tuple[float, float] | None:
    if not isinstance(station, dict):
        return None
    try:
        latitude = float(str(station.get("latitude", "")).strip().replace(",", "."))
        longitude = float(str(station.get("longitude", "")).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    if not (
        math.isfinite(latitude)
        and math.isfinite(longitude)
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    ):
        return None
    return latitude, longitude


def resolve_coordinates(
    station: dict | None = None,
    detail_loader: Callable[[], dict] | None = None,
) -> tuple[float, float]:
    latitude = os.getenv("STATION_LATITUDE", "").strip()
    longitude = os.getenv("STATION_LONGITUDE", "").strip()
    if latitude or longitude:
        coordinates = coordinates_from_station({
            "latitude": latitude,
            "longitude": longitude,
        })
        if coordinates is None:
            raise SolisLocationError(
                "Configure STATION_LATITUDE e STATION_LONGITUDE juntas, "
                "com valores numéricos válidos."
            )
        return coordinates

    coordinates = coordinates_from_station(station)
    if coordinates is not None:
        logger.info("Localização obtida da lista de usinas SolisCloud.")
        return coordinates

    if detail_loader is not None:
        try:
            response = detail_loader()
        except SolisAPIError as exc:
            raise SolisLocationError(
                "Não foi possível consultar a localização em stationDetail. "
                "Tente novamente ou configure STATION_LATITUDE e STATION_LONGITUDE."
            ) from exc
        data = response.get("data") if isinstance(response, dict) else None
        coordinates = coordinates_from_station(data)
        if coordinates is not None:
            logger.info("Localização obtida dos detalhes da usina SolisCloud.")
            return coordinates

    raise SolisLocationError(
        "A SolisCloud não forneceu coordenadas válidas na lista ou nos detalhes "
        "da usina. Verifique a localização cadastrada na SolisCloud ou configure "
        "STATION_LATITUDE e STATION_LONGITUDE."
    )
