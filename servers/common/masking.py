"""Masking of sensitive values before they leave a server."""

import re


def compact_iban(iban: str) -> str:
    return re.sub(r"\s+", "", iban).upper()


def mask_iban(iban: str | None) -> str | None:
    """Show only the last four characters: NL91ABNA0417164300 -> ****4300."""
    if not iban:
        return None
    iban = compact_iban(iban)
    return "****" + iban[-4:]


def iban_last4(iban: str | None) -> str | None:
    return compact_iban(iban)[-4:] if iban else None
