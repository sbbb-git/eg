#!/usr/bin/env python3
"""escape_proxy.py — couche proxy centrale (résidentiel) pour le scraping.

Certaines plateformes (Bookeo notamment) bloquent les IP de datacenter
("Access from unauthorized IP address detected"). Pour les scraper il faut
sortir par un proxy résidentiel (Bright Data, Oxylabs, Smartproxy…).

Activation = définir les variables d'environnement / secrets CI :

    PROXY_SERVER    ex. http://gate.smartproxy.com:7000
                    ou   http://brd.superproxy.io:22225
    PROXY_USERNAME  identifiant fourni par le provider
    PROXY_PASSWORD  mot de passe fourni par le provider

Tant qu'elles ne sont pas définies, tout fonctionne SANS proxy (no-op) :
les scrapers 4escape (pas de blocage IP) continuent normalement, et les
engines protégés (Bookeo) se contentent de logguer "proxy requis".
"""
from __future__ import annotations

import os
from urllib.parse import quote


def enabled() -> bool:
    return bool(os.environ.get("PROXY_SERVER"))


def playwright_proxy() -> dict | None:
    """Dict prêt pour Playwright launch(proxy=...) / new_context(proxy=...)."""
    server = os.environ.get("PROXY_SERVER")
    if not server:
        return None
    proxy = {"server": server}
    user = os.environ.get("PROXY_USERNAME")
    pwd = os.environ.get("PROXY_PASSWORD")
    if user:
        proxy["username"] = user
    if pwd:
        proxy["password"] = pwd
    return proxy


def requests_proxies() -> dict | None:
    """Dict {http,https} pour urllib/requests, avec auth encodée dans l'URL."""
    server = os.environ.get("PROXY_SERVER")
    if not server:
        return None
    user = os.environ.get("PROXY_USERNAME", "")
    pwd = os.environ.get("PROXY_PASSWORD", "")
    if user:
        scheme, _, host = server.partition("://")
        host = host or scheme
        scheme = scheme if _ else "http"
        url = f"{scheme}://{quote(user)}:{quote(pwd)}@{host}"
    else:
        url = server
    return {"http": url, "https": url}


def describe() -> str:
    return f"proxy ON ({os.environ.get('PROXY_SERVER')})" if enabled() else "proxy OFF (IP directe)"
