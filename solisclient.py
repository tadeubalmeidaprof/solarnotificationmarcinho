import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from email.utils import formatdate
from typing import Any

import requests


class SolisAPIError(Exception):
    pass


@dataclass
class SolisCredentials:
    api_id: str
    api_secret: str
    base_url: str
    station_id: str


class SolisClient:
    def __init__(self, credentials: SolisCredentials):
        self.credentials = credentials
        self.base_url = credentials.base_url.rstrip("/")

    def _content_md5(self, body: str) -> str:
        md5_bytes = hashlib.md5(body.encode("utf-8")).digest()
        return base64.b64encode(md5_bytes).decode("utf-8")

    def _gmt_date(self) -> str:
        return formatdate(timeval=None, localtime=False, usegmt=True)

    def _sign(
        self,
        method: str,
        content_md5: str,
        content_type: str,
        date: str,
        path: str,
    ) -> str:
        string_to_sign = (
            method + "\n" +
            content_md5 + "\n" +
            content_type + "\n" +
            date + "\n" +
            path
        )

        digest = hmac.new(
            self.credentials.api_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).digest()

        return base64.b64encode(digest).decode("utf-8")

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

        # No seu teste local, esse Content-Type foi o que funcionou.
        content_type = "application/json"

        content_md5 = self._content_md5(body)
        date = self._gmt_date()

        signature = self._sign(
            method="POST",
            content_md5=content_md5,
            content_type=content_type,
            date=date,
            path=path,
        )

        headers = {
            "Content-MD5": content_md5,
            "Content-Type": content_type,
            "Date": date,
            "Authorization": f"API {self.credentials.api_id}:{signature}",
        }

        url = self.base_url + path

        try:
            response = requests.post(
                url,
                headers=headers,
                data=body.encode("utf-8"),
                timeout=30,
            )
        except requests.exceptions.RequestException as exc:
            raise SolisAPIError(f"Erro de conexão com a SolisCloud: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise SolisAPIError(
                f"Resposta inválida da SolisCloud. "
                f"Status: {response.status_code}. Texto: {response.text}"
            ) from exc

        if response.status_code >= 400:
            raise SolisAPIError(f"Erro HTTP {response.status_code}: {data}")

        code = str(data.get("code"))

        if code not in ("0", "200"):
            raise SolisAPIError(f"Erro retornado pela SolisCloud: {data}")

        return data

    def list_stations(self) -> dict[str, Any]:
        # Não usar nmiCode: "", porque no seu teste isso retornou zero usinas.
        return self.post(
            "/v1/api/userStationList",
            {
                "pageNo": 1,
                "pageSize": 10,
            },
        )
