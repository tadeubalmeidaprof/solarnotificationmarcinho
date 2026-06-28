import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

STATION_DETAIL_RESOURCE = "/v1/api/stationDetail"
DEFAULT_TIMEOUT = 15
MAX_RETRIES = 3
BACKOFF_FACTOR = 2


class SolisAPIError(Exception):
    pass


@dataclass(frozen=True)
class SolisCredentials:
    api_id: str
    api_secret: str
    base_url: str
    station_id: str


class SolisClient:
    def __init__(self, credentials: SolisCredentials, timeout: int = DEFAULT_TIMEOUT):
        self._credentials = credentials
        self._base_url = credentials.base_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()

    def get_station_detail(self) -> dict[str, Any]:
        payload = {"id": self._credentials.station_id}
        return self._post(STATION_DETAIL_RESOURCE, payload)

    def _post(self, resource: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        headers = self._build_headers("POST", resource, body)
        url = f"{self._base_url}{resource}"

        last_error: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self._session.post(url, data=body, headers=headers, timeout=self._timeout)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as exc:
                last_error = exc
                logger.warning("Tentativa %d/%d falhou ao chamar %s: %s", attempt, MAX_RETRIES, resource, exc)
                if attempt < MAX_RETRIES:
                    time.sleep(BACKOFF_FACTOR ** attempt)

        raise SolisAPIError(f"Falha ao consultar {resource} após {MAX_RETRIES} tentativas") from last_error

    def _build_headers(self, verb: str, resource: str, body: bytes) -> dict[str, str]:
        content_type = "application/json"
        content_md5 = base64.b64encode(hashlib.md5(body).digest()).decode()
        date = format_datetime(datetime.now(timezone.utc), usegmt=True)
        signature = self._sign(verb, content_md5, content_type, date, resource)

        return {
            "Content-MD5": content_md5,
            "Content-Type": content_type,
            "Date": date,
            "Authorization": f"API {self._credentials.api_id}:{signature}",
        }

    def _sign(self, verb: str, content_md5: str, content_type: str, date: str, resource: str) -> str:
        string_to_sign = f"{verb}\n{content_md5}\n{content_type}\n{date}\n{resource}"
        digest = hmac.new(
            self._credentials.api_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        return base64.b64encode(digest).decode()
