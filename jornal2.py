# jornal2.py — Jornal (Felipe Tacconi)
# Coleta RSS por tema (rápido) + cache por feed (TTL)
# + suporte forte a ATOM (Banco Central) e RSS
# + campos extras por item: jc_link, jc_summary, jc_ts, jc_hora
# + (NOVO) filtro por palavras-chave somente no tema 🌍 Economia
#          para evitar BBB/carnaval/esporte nos feeds gerais (InfoMoney/Exame)

from __future__ import annotations

import calendar
import re
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
    Busca RSS/Atom com requests (mais compatível em servidores).
    Faz cache em memória por URL (TTL).
    """
    cached = _FEED_CACHE.get(url)
    if cached and not _cache_expired(cached[0]):
        return cached[1]

    try:
        r = _SESSION.get(url, timeout=timeout, allow_redirects=True)
        r.raise_for_status()

        content = r.content or b""
        head = content.lstrip()[:400].lower()
        parece_xml = (
                head.startswith(b"<?xml")
                or b"<rss" in head
                or b"<feed" in head
                or b"<rdf:rdf" in head
        )

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

    # 🌍 ECONOMIA = cripto + mercado + macro
    # (InfoMoney/Exame são feeds gerais -> vamos filtrar por palavras-chave)
    "🌍 Economia": [
        "https://www.infomoney.com.br/feed/",
        "https://exame.com/feed/",
        "https://g1.globo.com/rss/g1/economia",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://br.investing.com/rss/news_301.rss",
        # opcional (se funcionar no seu ambiente):
        # "https://www.bcb.gov.br/noticiablogbc/rss",
        # opcional (pode exigir assinatura em alguns casos):
        # "https://www.economist.com/finance-and-economics/rss.xml",
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
    "🌍 Economia": 20,
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
    DIAS_PT = [
        "Segunda-feira", "Terça-feira", "Quarta-feira",
        "Quinta-feira", "Sexta-feira", "Sábado", "Domingo",
    ]
    MESES_PT = [
        "", "janeiro", "fevereiro", "março", "abril", "maio", "junho",
        "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
    ]
    agora = agora_local()
    return f"{DIAS_PT[agora.weekday()]}, {agora.day} de {MESES_PT[agora.month]} de {agora.year} — {agora.strftime('%H:%M')}"


def _entry_time_struct(e: Any):
    try:
        return e.get("published_parsed") or e.get("updated_parsed")
    except Exception:
        return None


def entry_ts(e: Any) -> float:
    t = _entry_time_struct(e)
    try:
        return float(calendar.timegm(t)) if t else 0.0
    except Exception:
        return 0.0


def formatar_hora_noticia(e: Any) -> str:
    try:
        t = _entry_time_struct(e)
        if not t:
            return ""
        ts = calendar.timegm(t)  # UTC
        dt_utc = datetime.fromtimestamp(ts, tz=ZoneInfo("UTC"))
        dt_local = dt_utc.astimezone(_tz())

        agora = agora_local()
        if dt_local.date() == agora.date():
            return dt_local.strftime("%H:%M")
        return dt_local.strftime("%d/%m %H:%M")
    except Exception:
        return ""


# =========================================================
# Helpers RSS/Atom (link + resumo compatíveis)
# =========================================================
_RE_TAG = re.compile(r"<[^>]+>")
_RE_SPACE = re.compile(r"\s+")


def _strip_html(s: str) -> str:
    s = _RE_TAG.sub(" ", s)
    s = _RE_SPACE.sub(" ", s).strip()
    return s


def entry_link(entry: Any) -> str:
    # 1) dict-like links (ATOM)
    try:
        links = entry.get("links")
        if links:
            for l in links:
                if l.get("rel") == "alternate" and l.get("href"):
                    return str(l.get("href"))
            for l in links:
                if l.get("href"):
                    return str(l.get("href"))
    except Exception:
        pass

    # 2) attribute-like links
    try:
        links = getattr(entry, "links", None)
        if links:
            for l in links:
                if l.get("rel") == "alternate" and l.get("href"):
                    return str(l.get("href"))
            for l in links:
                if l.get("href"):
                    return str(l.get("href"))
    except Exception:
        pass

    # 3) RSS clássico
    try:
        return str(entry.get("link") or "")
    except Exception:
        return ""


def entry_summary(entry: Any, max_chars: int = 280) -> str:
    txt = ""
    try:
        txt = (
                entry.get("summary")
                or (entry.get("summary_detail") or {}).get("value")
                or entry.get("description")
                or (entry.get("description_detail") or {}).get("value")
                or entry.get("subtitle")
                or ""
        )
    except Exception:
        txt = ""

    if not txt:
        try:
            content = entry.get("content") or []
            if isinstance(content, list) and content:
                txt = (content[0] or {}).get("value") or ""
        except Exception:
            txt = ""

    if not txt:
        return ""

    txt = _strip_html(str(txt))
    txt = re.sub(r"(continue reading|leia mais|saiba mais)\.*$", "", txt, flags=re.I).strip()

    if len(txt) <= max_chars:
        return txt

    cut = txt[: max_chars + 1].rsplit(" ", 1)[0]
    return (cut or txt[:max_chars]).rstrip(".,;:—- ") + "…"


# =========================================================
# (NOVO) Filtro Economia: cripto + mercado + macro
# =========================================================
_ECO_KEYWORDS = [
    # macro BR
    "economia", "inflação", "ipca", "igp", "pib", "selic", "copom", "banco central",
    "juros", "câmbio", "dólar", "real", "fiscal", "déficit", "superávit", "tesouro",
    "tribut", "imposto", "reforma", "orçamento",

    # mercado
    "bolsa", "b3", "ações", "acionista", "dividend", "ibovespa", "índice",
    "empresa", "lucro", "receita", "balanço", "guidance", "m&a", "fus", "aquisi",
    "economista", "mercado", "invest", "fund", "banco", "credito", "crédito",

    # cripto
    "cripto", "criptomoeda", "bitcoin", "btc", "ethereum", "eth", "blockchain",
    "token", "defi", "stablecoin", "binance", "coinbase",
]

# coisas que queremos cortar na marra quando vier de feed geral
_ECO_BLACKLIST = [
    "bbb", "big brother", "carnaval", "rio open", "anitta", "novela",
    "campeã", "campeao", "eliminado", "paredão", "paredao",
    "futebol", "flamengo", "corinthians", "palmeiras", "santos", "são paulo",
    "oscar", "grammy",
]


def _match_economia(title: str, summary: str) -> bool:
    t = (title or "").lower()
    s = (summary or "").lower()
    blob = f"{t} {s}"

    # corta lixo óbvio
    for b in _ECO_BLACKLIST:
        if b in blob:
            return False

    # aceita se bater em keyword
    for k in _ECO_KEYWORDS:
        if k in blob:
            return True

    return False


# =========================================================
# Coleta
# =========================================================
def _coletar_de_feeds(tema: str, urls: List[str], limite_total: int | None = None) -> List[Dict[str, Any]]:
    itens: List[Dict[str, Any]] = []
    vistos: set[str] = set()

    # stats (só pra diagnóstico opcional via print)
    stats_total: Dict[str, int] = {}
    stats_ok: Dict[str, int] = {}

    for url in urls:
        feed = carregar_feed(url)
        entries = feed.get("entries", []) or []
        stats_total[url] = len(entries)
        ok_count = 0

        for entry in entries:
            try:
                e = dict(entry)
            except Exception:
                e = entry  # type: ignore

            link = entry_link(e)
            if not link:
                continue

            # Campos extras
            try:
                e["jc_link"] = link
            except Exception:
                pass

            try:
                e["jc_ts"] = entry_ts(e)
            except Exception:
                pass

            try:
                e["jc_hora"] = formatar_hora_noticia(e)
            except Exception:
                pass

            try:
                e["jc_summary"] = entry_summary(e)
            except Exception:
                pass

            # título string
            try:
                if "title" in e and e["title"] is not None:
                    e["title"] = str(e["title"])
            except Exception:
                pass

            # ✅ filtro só pra Economia (porque tem feeds gerais)
            if tema == "🌍 Economia":
                if not _match_economia(e.get("title", ""), e.get("jc_summary", "")):
                    continue

            # dedupe por link
            if link in vistos:
                continue
            vistos.add(link)

            itens.append(e)
            ok_count += 1

        stats_ok[url] = ok_count

    # se você quiser ver no terminal rapidamente:
    # if tema == "🌍 Economia":
    #     print("\n=== FILTRO ECONOMIA (por feed) ===")
    #     for u in urls:
    #         print(f"{stats_ok.get(u,0):>3}/{stats_total.get(u,0):<3}  {u}")

    itens.sort(key=lambda x: float(x.get("jc_ts") or entry_ts(x) or 0.0), reverse=True)
    return itens[:limite_total] if limite_total else itens


def coletar_noticias_por_tema(limites: Dict[str, int] | None = None):
    if limites is None:
        limites = LIMITES_PADRAO.copy()

    buckets: Dict[str, List[Dict[str, Any]]] = {}
    flat: List[Dict[str, Any]] = []

    for tema, urls in FEEDS_BY_TEMA.items():
        limite = limites.get(tema, 0)
        itens = _coletar_de_feeds(tema, urls, limite_total=limite if limite > 0 else None)
        buckets[tema] = itens
        flat.extend(itens)

    return buckets, flat


# =========================================================
# Diagnóstico (rodar: python jornal2.py)
# =========================================================
def diagnosticar_tema(nome_tema: str = "🌍 Economia"):
    urls = FEEDS_BY_TEMA.get(nome_tema, [])
    print(f"\n=== DIAGNÓSTICO {nome_tema} ===")
    for url in urls:
        f = carregar_feed(url)
        n = len((f or {}).get("entries", []) or [])
        print(f"{n:>3}  {url}")


if __name__ == "__main__":
    diagnosticar_tema("🌍 Economia")
