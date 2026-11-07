"""Minimal SOAP client for the documented BRENDA query-helper interface.

Credentials remain in the process environment. Importing this module does not
load a WSDL or contact a service. Live API behavior requires a separate check.
"""
from __future__ import annotations

import hashlib
import os


def call_brenda(action: str, parameters: list[str]):
    email = os.environ.get("BRENDA_EMAIL") or os.environ.get("BRENDA_EMIAL")
    password = os.environ.get("BRENDA_PASSWORD")
    if not email or not password:
        raise RuntimeError("BRENDA_CREDENTIALS_REQUIRED")
    from zeep import Client, Settings
    from zeep.transports import Transport

    client = Client("https://www.brenda-enzymes.org/soap/brenda_zeep.wsdl",
                    settings=Settings(strict=True), transport=Transport(timeout=30, operation_timeout=60))
    method = getattr(client.service, action)
    return method(email, hashlib.sha256(password.encode()).hexdigest(), *parameters)


def split_entries(response) -> list[str]:
    if response is None:
        return []
    if isinstance(response, (list, tuple)):
        if any(not isinstance(value, str) for value in response):
            raise ValueError("BRENDA_RESPONSE_SHAPE_UNSUPPORTED")
        return [entry for value in response for entry in split_entries(value)]
    if not isinstance(response, str):
        raise ValueError("BRENDA_RESPONSE_SHAPE_UNSUPPORTED")
    return [entry.strip() for entry in response.split("!") if entry.strip()]


def get_km_values(ec_number: str, organism: str = "*", substrate: str = "*") -> list[str]:
    parameters = ["ecNumber*" + ec_number, "organism*" + organism, "kmValue*", "kmValueMaximum*",
                  "substrate*" + substrate, "commentary*", "ligandStructureId*", "literature*"]
    return split_entries(call_brenda("getKmValue", parameters))


def get_reactions(ec_number: str, organism: str = "*", reaction: str = "*") -> list[str]:
    parameters = ["ecNumber*" + ec_number, "organism*" + organism, "reaction*" + reaction, "commentary*", "literature*"]
    return split_entries(call_brenda("getReaction", parameters))
