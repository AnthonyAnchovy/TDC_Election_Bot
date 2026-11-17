"""Certificates the bot needs for particular results sites, and only for them (no Discord, no network here).

Florida's results server (flelectionfiles.floridados.gov) sends a certificate chain that ends at "GoDaddy TLS Root CA -
R1", a root from 2025 that browsers trust but Python's certificate list (certifi, and the system lists on GitHub's
machines) doesn't yet. So every request from the bot failed with CERTIFICATE_VERIFY_FAILED (the weekly feed check
found it in Nov 2026).

The fix adds no new trusted root. certs/godaddy_tls_root_r1_cross_g2.pem is GoDaddy's own cross-certificate for that
root, signed by its older "Go Daddy Root Certificate Authority - G2", which every list already trusts. Downloaded from
https://certs.godaddy.com/repository/gd_tls_root-r1-cross-g2.crt.pem on 2026-11-17; its key matches the root Florida's
server sends. With it, Florida's chain leads back to G2 and is checked as usual. Partial chains are refused, so the
cross-certificate is only ever a link in the chain, never trusted on its own.

It is used for the hosts listed in EXTRA and no others. Once Python's lists carry the R1 root (or Florida sends the
cross-certificate itself), this can go.
"""
from __future__ import annotations

import ssl
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
EXTRA = {"flelectionfiles.floridados.gov": "godaddy_tls_root_r1_cross_g2.pem"}


@lru_cache(maxsize=None)
def _context(name: str) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    try:
        import certifi                  # aiohttp's own default list, where it's installed
        ctx.load_verify_locations(certifi.where())
    except ImportError:
        pass
    ctx.load_verify_locations(HERE / "certs" / name)
    ctx.verify_flags &= ~getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)
    return ctx


def ssl_for(url: str) -> ssl.SSLContext | bool:
    """What to hand aiohttp as `ssl=` for this URL: a context with the extra certificate for a listed host, else True
    (aiohttp's normal checking)."""
    name = EXTRA.get((urlsplit(url).hostname or "").lower())
    return _context(name) if name else True
