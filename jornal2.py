# jornal2.py — Jornal (Felipe Tacconi)
# Coleta RSS por tema (rápido) + cache por feed (TTL)

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo

import feedparser
import requests

# =========================================================
# Config
# =========================================================
DEFAULT_TIMEOUT = int(__import__("os").environ.get("JC_TIMEOUT", "8"))
CACHE_TTL = int(__import__("os").environ.get("JC_CACHE_TTL", "180"))  # 3 min
TIMEZONE = __import__("os").environ.get("JC_TIMEZONE", "America/Sao_Paulo")

DEFAULT_HEADERS = {
    "User-Agent": "Jornal/1.0 (+https://jornal-j5jf.onrender.com)",
    "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.7",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# =========================================================
# Session + cache por feed
# =========================================================
_SESSION = requests.Session()
_SESSION.headers.update(DEFAULT_HEADERS)

# _FEED_CACHE[url] = (ts, parsed_dict)
_FEED_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def _cache_expired(ts: float) -> bool:
    return (time.time() - ts) > CACHE_TTL


def carregar_feed(url: str, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """
    Busca RSS com requests (mais compatível em servidores).
    Faz cache em memória por URL (TTL).
    """
    cached = _FEED_CACHE.get(url)
    if cached and not _cache_expired(cached[0]):
        return cached[1]

    try:
        r = _SESSION.get(url, timeout=timeout, allow_redirects=True)
        r.raise_for_status()

        content = r.content or b""
        head = content.lstrip()[:300].lower()
        parece_xml = head.startswith(b"<?xml") or b"<rss" in head or b"<feed" in head

        if not parece_xml:
            parsed = feedparser.parse(url)  # fallback
        else:
            parsed = feedparser.parse(content)

        out = dict(parsed)  # garante dict-like
        _FEED_CACHE[url] = (time.time(), out)
        return out

    except Exception:
        out = {"entries": []}
        _FEED_CACHE[url] = (time.time(), out)
        return out


# =========================================================
# Feeds por tema
# =========================================================
FEEDS_BY_TEMA: Dict[str, List[str]] = {
    "⚽ Esporte": [
        "https://rss.uol.com.br/feed/esporte.xml",
        "https://www.espn.com.br/espn/rss/news",
        "https://feeds.bbci.co.uk/sport/rss.xml",
    ],
    "🎭 Cultura": [
        "https://g1.globo.com/dynamo/pop-arte/rss2.xml",
        "https://www1.folha.uol.com.br/ilustrada/rss091.xml",
    ],
    "🏛️ Política Brasil": [
        "https://g1.globo.com/dynamo/politica/rss2.xml",
        "https://feeds.folha.uol.com.br/poder/rss091.xml",
    ],
    "🌍 Economia": [
        "https://g1.globo.com/dynamo/mundo/rss2.xml",
        "https://feeds.bbci.co.uk/portuguese/rss.xml",
    ],
    "📰 Últimas": [
        "https://g1.globo.com/rss/g1/",
        "https://rss.uol.com.br/feed/noticias.xml",
        "https://www.infomoney.com.br/feed/",
        "https://exame.com/feed/",
        "https://tecnoblog.net/feed/",
    ],
}

LIMITES_PADRAO: Dict[str, int] = {
    "⚽ Esporte": 9,
    "🎭 Cultura": 9,
    "🏛️ Política Brasil": 9,
    "🌍 Economia": 9,
    "📰 Últimas": 100,
}


# =========================================================
# Helpers de tempo
# =========================================================
def _tz() -> ZoneInfo:
    """Retorna o fuso horário configurado, com fallback para UTC."""
    try:
        return ZoneInfo(TIMEZONE)
    except Exception:
        return ZoneInfo("UTC")


def agora_local() -> datetime:
    """Retorna o datetime atual no fuso horário local configurado."""
    return datetime.now(tz=_tz())


def formatar_hora_cabecalho() -> str:
    """
    Retorna string formatada para o cabeçalho do jornal.
    Ex.: 'Terça-feira, 17 de fevereiro de 2025 — 14:32'
    """
    DIAS_PT = [
        "Segunda-feira", "Terça-feira", "Quarta-feira",
        "Quinta-feira", "Sexta-feira", "Sábado", "Domingo",
    ]
    MESES_PT = [
        "", "janeiro", "fevereiro", "março", "abril", "maio", "junho",
        "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
    ]
    agora = agora_local()
    dia_semana = DIAS_PT[agora.weekday()]
    mes = MESES_PT[agora.month]
    return f"{dia_semana}, {agora.day} de {mes} de {agora.year} — {agora.strftime('%H:%M')}"


def entry_ts(e: Any) -> float:
    """Retorna o timestamp Unix da entrada RSS."""
    try:
        t = e.get("published_parsed") or e.get("updated_parsed")
    except Exception:
        t = None
    try:
        return time.mktime(t) if t else 0.0
    except Exception:
        return 0.0


def formatar_hora_noticia(e: Any) -> str:
    """
    Retorna a hora de publicação da notícia formatada como 'HH:MM'
    no fuso horário local. Retorna '' se não disponível.
    """
    try:
        t = e.get("published_parsed") or e.get("updated_parsed")
        if not t:
            return ""
        # time.struct_time do feedparser é em UTC
        ts = time.mktime(t)
        dt_utc = datetime.fromtimestamp(ts, tz=ZoneInfo("UTC"))
        dt_local = dt_utc.astimezone(_tz())

        agora = agora_local()
        # Se for hoje, mostra só a hora; se for outro dia, mostra "DD/MM HH:MM"
        if dt_local.date() == agora.date():
            return dt_local.strftime("%H:%M")
        else:
            return dt_local.strftime("%d/%m %H:%M")
    except Exception:
        return ""


# =========================================================
# Coleta
# =========================================================
def _coletar_de_feeds(urls: List[str], limite_total: int | None = None) -> List[Dict[str, Any]]:
    itens: List[Dict[str, Any]] = []
    vistos: set[str] = set()

    for url in urls:
        feed = carregar_feed(url)
        for entry in feed.get("entries", []) or []:
            try:
                link = entry.get("link") or ""
            except Exception:
                link = ""
            if not link or link in vistos:
                continue
            vistos.add(link)
            itens.append(entry)

    itens.sort(key=entry_ts, reverse=True)
    return itens[:limite_total] if limite_total else itens


def coletar_noticias_por_tema(limites: Dict[str, int] | None = None):
    if limites is None:
        limites = LIMITES_PADRAO.copy()

    buckets: Dict[str, List[Dict[str, Any]]] = {}
    flat: List[Dict[str, Any]] = []

    for tema, urls in FEEDS_BY_TEMA.items():
        limite = limites.get(tema, 0)
        itens = _coletar_de_feeds(urls, limite_total=limite if limite > 0 else None)
        buckets[tema] = itens
        flat.extend(itens)

    return buckets, flat# alteração direta git teste
