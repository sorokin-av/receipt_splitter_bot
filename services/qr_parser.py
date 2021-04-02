import os
import sys
import json
import asyncio
import subprocess
from copy import copy
from datetime import datetime
from functools import partial
from concurrent.futures import ThreadPoolExecutor

import requests
from dotenv import load_dotenv

from utils.logger import system_log
from bot_config import FEDERAL_TAX_LOGIN, FEDERAL_TAX_PASSWORD, FEDERAL_TAX_SECRET_TOKEN, \
    CREDENTIALS_PATH, CONNECT_TIMEOUT, READ_TIMEOUT


load_dotenv(dotenv_path=CREDENTIALS_PATH)


class Methods:
    GET = "GET"
    POST = "POST"


class QRParser:
    ACCEPT = "*/*"
    DEVICE_OS = "iOS"
    CLIENT_VERSION = "2.9.0"
    DEVICE_ID = "7C82010F-16CC-446B-8F66-FC4080C66521"
    USER_AGENT = "billchecker/2.9.0 (iPhone; iOS 13.6; Scale/2.00)"
    ACCEPT_LANGUAGE = "ru-RU;q=1, en-US;q=0.9"
    FTS_HOST = "irkkt-mobile.nalog.ru:8888"
    AUTH_URL = f"https://{FTS_HOST}/v2/mobile/users/lkfl/auth"
    TICKET_URL = f"https://{FTS_HOST}/v2/ticket"
    TICKETS_URL = f"https://{FTS_HOST}/v2/tickets/"
    BACKUP_TICKETS_URL = f"https://proverkacheka.com/check/get"
    TINKOFF_FNS_NLP_URL = f"https://receiptnlp.tinkoff.ru/api/fns"

    def __init__(self):
        self.__session_id = None
        self._set_session_id()
        system_log("Init {parser}".format(parser=type(self).__name__))

    @property
    def headers(self):
        return {
            "Host": self.FTS_HOST,
            "Accept": self.ACCEPT,
            "Device-OS": self.DEVICE_OS,
            "Device-Id": self.DEVICE_ID,
            "clientVersion": self.CLIENT_VERSION,
            "Accept-Language": self.ACCEPT_LANGUAGE,
            "User-Agent": self.USER_AGENT
        }

    @property
    def headers_with_session(self):
        headers = copy(self.headers)
        headers["sessionId"] = self.__session_id
        return headers

    @property
    def __auth_payload(self):
        return {
            "inn": os.getenv(FEDERAL_TAX_LOGIN),
            "password": os.getenv(FEDERAL_TAX_PASSWORD),
            "client_secret": os.getenv(FEDERAL_TAX_SECRET_TOKEN),
        }

    @staticmethod
    def request_handling(method, url, **kwargs):
        response = {}
        try:
            if method == Methods.GET:
                response = requests.get(url=url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), **kwargs)
            elif method == Methods.POST:
                response = requests.post(url=url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), **kwargs)
        except requests.exceptions.RequestException:
            system_log("Request exception occurred", level="ERROR", exc_info=True)
        finally:
            if hasattr(response, "text") and hasattr(response, "status_code"):
                system_log("Obtained response from {url}: {response}".format(url=url, response=response.text))
                return response.json() if response.status_code == 200 else {}
            else:
                return {}

    def _set_session_id(self) -> None:
        system_log("Set session id with federal tax service")
        resp = self.request_handling(method=Methods.POST, url=self.AUTH_URL,
                                     json=self.__auth_payload, headers=self.headers)
        session_id = resp["sessionId"] if resp else None
        self.__session_id = session_id

    def _get_ticket_id(self, qr: str) -> str:
        system_log("Fetch ticket id from {url}".format(url=self.TICKET_URL))
        resp = self.request_handling(method=Methods.POST, url=self.TICKET_URL,
                                     json={"qr": qr}, headers=self.headers_with_session)
        ticket_id = resp["id"] if resp else ""
        return ticket_id

    def _get_federal_tax_ticket(self, qr: str) -> dict:
        ticket_id = self._get_ticket_id(qr)
        ticket_description_url = self.TICKETS_URL + ticket_id
        system_log("Fetch ticket description by id={id} from {url}".format(id=ticket_id, url=self.TICKET_URL))
        resp = self.request_handling(method=Methods.GET, url=ticket_description_url,
                                     headers=self.headers_with_session)
        ticket = resp.get("ticket")
        if ticket:
            receipt = ticket["document"]["receipt"]
            system_log("Successful federal tax service receipt obtaining: receipt={receipt}".format(receipt=receipt))
            return receipt
        else:
            system_log("Failed to obtain receipt from FTS")
            return {}

    def _get_backup_ofd_ticket(self, qr: str) -> dict:
        system_log("Fetch ticket description for qr code '{qr}' from backup URL: {url}".format(qr=qr, url=self.BACKUP_TICKETS_URL))
        command = 'curl --data "{qr}" {host}'.format(qr=qr, host=self.BACKUP_TICKETS_URL)
        pipe = subprocess.Popen(command.split(), stdout=subprocess.PIPE, stderr=sys.stderr)
        if not pipe.stderr:
            resp = json.loads(pipe.stdout.read().decode())
            ticket = resp.get("data")
            if isinstance(ticket, dict):
                receipt = ticket["json"]
                system_log("Successful backup OFD receipt obtaining: receipt={receipt}".format(receipt=receipt))
                return receipt
            else:
                system_log("Failed to obtain receipt from backup OFD")
                return {}
        else:
            return {}

    def _ticket_processing(self, ticket):
        def _preprocessing_payload():
            return {
                "user": ticket.get("user", ""),
                "userInn": ticket.get("userInn", "").strip(),
                "retailPlaceAddress": "",
                "kktRegId": ticket.get("kktRegId", "").strip(),
                "fiscalDocumentNumber": ticket.get("fiscalDocumentNumber", 0),
                "fiscalSign": ticket.get("fiscalSign", 0),
                "totalSum": ticket.get("totalSum", 0),
                "dateTime": ticket.get("dateTime", ""),
                "items": []
            }

        def _clean_items():
            clean_items = []
            for item in ticket["items"]:
                clean_item = {
                    "name": item["name"],
                    "price": item["price"],
                    "quantity": item["quantity"],
                    "sum": item["sum"]
                }
                clean_items.append(clean_item)
            return clean_items

        system_log("Start preprocessing receipt options")
        payload = _preprocessing_payload()
        payload["items"] = _clean_items()
        if isinstance(payload["dateTime"], int):
            payload["dateTime"] = datetime.fromtimestamp(ticket["dateTime"]).isoformat()

        system_log("Sending raw receipt data for preprocessing")
        response = self.request_handling(method=Methods.POST, url=self.TINKOFF_FNS_NLP_URL, json=payload)
        processed_items = response.get("result", {}).get("items", [])
        if processed_items:
            system_log("Successful receipt preprocessing")
            for position, item in enumerate(ticket["items"]):
                item["name"] = processed_items[position]["look"]
                item["price"] = int(item["price"]) / 100
        else:
            system_log("Failed to preprocess receipt")

    async def get_ticket_items(self, qr: str):
        loop = asyncio.get_running_loop()
        system_log("Get running loop for asynchronous ticket fetch")
        with ThreadPoolExecutor() as pool:
            futures = [
                loop.run_in_executor(pool, partial(self._get_federal_tax_ticket, qr=qr)),
                loop.run_in_executor(pool, partial(self._get_backup_ofd_ticket, qr=qr))
            ]
            tickets = await asyncio.gather(*futures)

        for ticket in tickets:
            if ticket:
                self._ticket_processing(ticket)
                return ticket.get("items")
        return []
