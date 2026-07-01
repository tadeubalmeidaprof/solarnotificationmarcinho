import os
import json
import hmac
import base64
import hashlib
import requests
from email.utils import formatdate


BASE_URL = "https://www.soliscloud.com:13333"


class SolisAPIError(Exception):
    pass


def get_value(env_name, label):
    value = os.getenv(env_name)
    if value:
        return value.strip()
    return input(f"Digite {label}: ").strip()


def pretty(data):
    print(json.dumps(data, indent=2, ensure_ascii=False))


class SolisClient:
    def __init__(self, api_id, api_secret, base_url=BASE_URL):
        self.api_id = api_id.strip()
        self.api_secret = api_secret.strip()
        self.base_url = base_url.rstrip("/")

    def _content_md5(self, body):
        return base64.b64encode(hashlib.md5(body.encode("utf-8")).digest()).decode("utf-8")

    def _date_gmt(self):
        return formatdate(timeval=None, localtime=False, usegmt=True)

    def _signature(self, method, content_md5, content_type, date, path):
        string_to_sign = method + "\n" + content_md5 + "\n" + content_type + "\n" + date + "\n" + path
        digest = hmac.new(
            self.api_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1
        ).digest()
        return base64.b64encode(digest).decode("utf-8"), string_to_sign

    def post(self, path, payload, content_type="application/json"):
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        content_md5 = self._content_md5(body)
        date = self._date_gmt()
        signature, string_to_sign = self._signature("POST", content_md5, content_type, date, path)

        headers = {
            "Content-MD5": content_md5,
            "Content-Type": content_type,
            "Date": date,
            "Authorization": f"API {self.api_id}:{signature}",
        }

        url = self.base_url + path

        print("\n================================================")
        print(f"Endpoint: {path}")
        print(f"Payload: {body}")
        print(f"Content-Type: {content_type}")

        response = requests.post(url, headers=headers, data=body.encode("utf-8"), timeout=30)

        try:
            data = response.json()
        except Exception:
            data = {"raw_text": response.text}

        print(f"HTTP: {response.status_code}")
        pretty(data)

        return response.status_code, data


def get_records(resp):
    data = resp.get("data", {})

    # Formato comum da Solis:
    # data.page.records
    if isinstance(data, dict):
        page = data.get("page")
        if isinstance(page, dict):
            records = page.get("records")
            if isinstance(records, list):
                return records

        records = data.get("records")
        if isinstance(records, list):
            return records

        list_data = data.get("list")
        if isinstance(list_data, list):
            return list_data

    return []


def main():
    print("=== Teste Solis userStationList V3 ===")
    print("Este teste confirma se a API está autenticando e tenta listar plantas com variações de payload.\n")

    api_id = get_value("SOLIS_API_ID", "API ID / Key ID")
    api_secret = get_value("SOLIS_API_SECRET", "API Secret / Key Secret")

    client = SolisClient(api_id, api_secret)

    tests = [
        {"pageNo": 1, "pageSize": 10},
        {"pageNo": "1", "pageSize": "10"},
        {"pageNo": 1, "pageSize": 10, "nmiCode": ""},
        {"pageNo": "1", "pageSize": "10", "nmiCode": ""},
    ]

    any_records = False

    for payload in tests:
        status, resp = client.post("/v1/api/userStationList", payload)
        records = get_records(resp)

        if records:
            any_records = True
            print("\n✅ USINAS ENCONTRADAS NESTE TESTE:")
            for i, item in enumerate(records, start=1):
                print(f"\nUsina {i}:")
                for key, value in item.items():
                    if key.lower() in ["id", "stationid", "plantid", "name", "stationname", "powerstationname", "sn"]:
                        print(f"  {key}: {value}")

    if not any_records:
        print("\n⚠️ Nenhuma usina apareceu em nenhum payload.")
        print("Isso indica que a assinatura e as chaves estão OK, mas a conta API não tem plantas vinculadas/visíveis.")
        print("Peça à Solis LATAM para vincular/habilitar a planta nessa conta API.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelado.")
    except requests.RequestException as e:
        print(f"\nErro de conexão: {e}")
    except Exception as e:
        print(f"\nErro: {e}")
