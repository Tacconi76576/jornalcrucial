#!/usr/bin/env python3
# app_min.py — Jornal Crucial (versão melhorada)
# Cache forte (JSON em disco + atualização em background) para Render ficar rápido.
#
# ✅ Nesta versão:
# - Corrige horário "no futuro" (usa calendar.timegm para struct_time UTC do feedparser)
# - Tema "📰 Últimas": 100 itens, lista 1 coluna, com HORÁRIO antes da manchete (igual aos outros temas)
# - Outros temas continuam no layout normal (com hora + título + fonte + resumo)
# - Corrige NameError: display_label não definido
# - Renomeia/expõe "🌍 Economia" (se existir no jornal2.py)

from __future__ import annotations

import calendar
import html as _html
import json
import logging
import os
import random
import re
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from flask import (
    Flask,
    abort,
    render_template_string,
    request,
    session,
)

from jornal2 import FEEDS_BY_TEMA, LIMITES_PADRAO, coletar_noticias_por_tema

# =========================================================
# Configuração inicial
# =========================================================
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "jornal-crucial-chave-local-1234567890")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("jornal-crucial")

TZ_BR = ZoneInfo("America/Sao_Paulo")

# =========================================================
# Compressão gzip + headers de performance
# =========================================================
import gzip as _gzip
import io as _io


@app.after_request
def compress_response(response):
    """Comprime respostas textuais se o cliente aceitar gzip e o payload for grande."""
    accept = request.headers.get("Accept-Encoding", "")
    if "gzip" not in accept:
        return response
    ct = response.content_type or ""
    if not any(t in ct for t in ("text/", "application/json", "application/javascript")):
        return response
    if response.direct_passthrough:
        return response
    data = response.get_data()
    if len(data) < 500:
        return response
    buf = _io.BytesIO()
    with _gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6) as gz:
        gz.write(data)
    response.set_data(buf.getvalue())
    response.headers["Content-Encoding"] = "gzip"
    response.headers["Content-Length"] = len(response.get_data())
    response.headers["Vary"] = "Accept-Encoding"
    return response


# =========================================================
# Sanitização rápida
# =========================================================
RE_TAG = re.compile(r"<[^>]+>")
RE_URL = re.compile(r"https?://\S+")
RE_IMG = re.compile(r"<img[^>]*>", flags=re.IGNORECASE)
RE_BR = re.compile(r"<br\s*/?>", flags=re.IGNORECASE)
RE_P_OPEN = re.compile(r"<p[^>]*>", flags=re.IGNORECASE)
RE_P_CLOSE = re.compile(r"</p\s*>", flags=re.IGNORECASE)
RE_WS = re.compile(r"\s+")

BLACKLIST = (
    "Leia mais",
    "Veja mais",
    "VÍDEOS:",
    "Veja os vídeos",
    "Siga o canal",
    "Clique aqui",
    "Participe do canal",
    "Receba as notícias",
    "The post",
    "appeared first on",
    "Leia a íntegra",
    "Leia a nota",
    "Assista",
    "AO VIVO",
)


def strip_html(text: Any) -> str:
    if not text:
        return ""
    t = str(text)
    t = RE_IMG.sub(" ", t)
    t = RE_BR.sub(" ", t)
    t = RE_P_CLOSE.sub(" ", t)
    t = RE_P_OPEN.sub(" ", t)
    t = RE_TAG.sub(" ", t)
    t = _html.unescape(t)
    t = RE_URL.sub(" ", t)
    for b in BLACKLIST:
        if b in t:
            t = t.replace(b, " ")
    return RE_WS.sub(" ", t).strip()


def summarize(text: Any, max_chars: int = 320) -> str:
    t = strip_html(text)
    if len(t) <= max_chars:
        return t
    cut = t[:max_chars].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:") + "…"


def _get(entry: Any, *keys: str, default: str = "") -> Any:
    for k in keys:
        try:
            v = entry.get(k)
        except Exception:
            v = None
        if v not in (None, "", [], {}):
            return v
    return default


# =========================================================
# Horários (CORRIGIDO: struct_time do feedparser é UTC)
# =========================================================
def entry_ts(e: Any) -> float:
    """Timestamp correto: usa calendar.timegm (UTC)."""
    try:
        t = e.get("published_parsed") or e.get("updated_parsed")
    except Exception:
        t = None
    try:
        return float(calendar.timegm(t)) if t else 0.0
    except Exception:
        return 0.0


def formatar_hora_noticia(entry: Any) -> str:
    """
    Converte o horário UTC da notícia para o fuso de Brasília.
    Retorna "HH:MM" se for hoje, "DD/MM HH:MM" caso contrário.
    """
    try:
        t = entry.get("published_parsed") or entry.get("updated_parsed")
        if not t:
            return ""
        ts = calendar.timegm(t)  # ✅ UTC correto
        dt_utc = datetime.fromtimestamp(ts, tz=ZoneInfo("UTC"))
        dt_local = dt_utc.astimezone(TZ_BR)

        agora_local = datetime.now(tz=TZ_BR)
        if dt_local.date() == agora_local.date():
            return dt_local.strftime("%H:%M")
        return dt_local.strftime("%d/%m %H:%M")
    except Exception as e:
        logger.warning(f"Erro ao formatar hora da notícia: {e}")
        return ""


def normalize_entry(entry: Any) -> Dict[str, Any]:
    titulo_raw = _get(entry, "title", "titulo", "headline", default="(sem título)")
    link = _get(entry, "link", "url", "href", default="") or ""
    fonte = _get(entry, "source", "fonte", "publisher", "site", default="") or ""
    resumo_raw = _get(entry, "summary", "description", "content", "resumo", default="")

    return {
        "titulo": summarize(titulo_raw, 140) or "(sem título)",
        "link": str(link),
        "fonte": strip_html(fonte),
        "resumo": summarize(resumo_raw, 320),
        "ts": float(entry_ts(entry)),
        "hora": formatar_hora_noticia(entry),
    }


def normalize_list(entries: List[Any], limit: int) -> List[Dict[str, Any]]:
    seen: set[Tuple[str, str]] = set()
    out: List[Dict[str, Any]] = []
    for e in entries:
        n = normalize_entry(e)
        key = (n.get("titulo", ""), n.get("link", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(n)
        if len(out) >= limit:
            break
    return out


# =========================================================
# Slugs / menu
# =========================================================
def slugify_tema(tema: str) -> str:
    base = re.sub(r"[^\w\s-]", "", tema, flags=re.UNICODE).strip().lower()
    base = re.sub(r"\s+", "-", base)
    base = re.sub(r"-+", "-", base)
    return base or "tema"


TEMAS = list(FEEDS_BY_TEMA.keys())
TEMA_SLUGS = {slugify_tema(t): t for t in TEMAS}


def display_label(tema: str) -> str:
    """
    Rótulo exibido no menu/título.
    Se você trocou o tema no jornal2.py para "🌍 Economia", ele aparece como economia aqui.
    """
    mapping = {
        "🌍 Economia": "🌍 Economia",
        # se ainda existir geopolítica em algum lugar, você pode mapear:
        "🌍 Geopolítica": "🌍 Economia",
    }
    return mapping.get(tema, tema)


def build_menu():
    return [{"label": display_label(t), "slug": slugify_tema(t)} for t in TEMAS]


def _tema_geral_label() -> Optional[str]:
    for candidato in ("📰 Últimas", "📰 Ultimas", "📰 Gerais", "📰 Geral"):
        if candidato in FEEDS_BY_TEMA:
            return candidato
    return None


GERAL_LABEL = _tema_geral_label()

# =========================================================
# Imagens
# =========================================================
IMAGENS_POR_TEMA: Dict[str, List[str]] = {
    "🏛️ Política Brasil": [
        "img/politica/politica1.jpg",
        "img/politica/politica2.jpg",
        "img/politica/politica3.jpg",
        "img/politica/politica4.jpg",
    ],
    # ✅ aqui: economia
    "🌍 Economia": [
        "img/economia/economia1.jpg",
        "img/economia/economia2.jpg",
        "img/economia/economia3.jpg",
    ],
    # caso ainda chame geopolítica em algum lugar:
    "🌍 Geopolítica": [
        "img/economia/economia1.jpg",
        "img/economia/economia2.jpg",
    ],
    "⚽ Esporte": [
        "img/esporte/esporte1.jpg",
        "img/esporte/esporte2.jpg",
        "img/esporte/esporte3.jpg",
    ],
    "🎭 Cultura": [
        "img/cultura/cultura1.jpg",
        "img/cultura/cultura2.jpg",
        "img/cultura/cultura3.jpg",
    ],
    "Geral": [
        "img/tudo/tudo1.jpg",
        "img/tudo/tudo2.jpg",
        "img/tudo/tudo3.jpg",
        "img/tudo/tudo4.jpg",
    ],
}
IMAGEM_FALLBACK = "img/fallback.jpg"


def escolher_imagem_sem_repetir(tema_label: Optional[str]) -> Optional[str]:
    if not tema_label:
        return None
    imagens = IMAGENS_POR_TEMA.get(tema_label)
    if not imagens:
        return IMAGEM_FALLBACK

    key = f"last_img::{tema_label}"
    last = session.get(key)

    if len(imagens) == 1:
        chosen = imagens[0]
    else:
        pool = [x for x in imagens if x != last]
        chosen = random.choice(pool) if pool else random.choice(imagens)

    session[key] = chosen
    return chosen


# =========================================================
# Fase da Lua (cálculo em UTC)
# =========================================================
def fase_da_lua(dt: Optional[datetime] = None) -> str:
    if dt is None:
        dt = datetime.now(timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    ref = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    days = (dt - ref).total_seconds() / 86400.0
    synodic = 29.53058867
    age = days % synodic

    if age < 1.84566:
        return "🌑 Lua Nova"
    if age < 5.53699:
        return "🌒 Crescente"
    if age < 9.22831:
        return "🌓 Quarto Crescente"
    if age < 12.91963:
        return "🌔 Gibosa Crescente"
    if age < 16.61096:
        return "🌕 Lua Cheia"
    if age < 20.30228:
        return "🌖 Gibosa Minguante"
    if age < 23.99361:
        return "🌗 Quarto Minguante"
    if age < 27.68493:
        return "🌘 Minguante"
    return "🌑 Lua Nova"


# =========================================================
# Cache em JSON
# =========================================================
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))
BASE_DIR = os.path.dirname(__file__)
CACHE_DIR = os.getenv("CACHE_DIR", os.path.join(BASE_DIR, "cache"))
CACHE_FILE = os.path.join(CACHE_DIR, "feeds_cache.json")

_CACHE_LOCK = threading.Lock()
_REFRESH_LOCK = threading.Lock()
_REFRESHING = False


def _ensure_cache_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z"


def _read_cache_file() -> Dict[str, Any]:
    _ensure_cache_dir()
    if not os.path.exists(CACHE_FILE):
        return {"updated_at": None, "buckets": {}}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Erro ao ler cache: {e}")
        return {"updated_at": None, "buckets": {}}


def _write_cache_file(data: Dict[str, Any]) -> None:
    _ensure_cache_dir()
    tmp = CACHE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CACHE_FILE)
        logger.info("Cache atualizado com sucesso.")
    except Exception as e:
        logger.error(f"Erro ao escrever cache: {e}")
        try:
            os.remove(tmp)
        except OSError:
            pass


def _cache_age_seconds(updated_at: Optional[str]) -> float:
    if not updated_at:
        return 1e18
    try:
        if updated_at.endswith("Z"):
            updated_at = updated_at[:-1] + "+00:00"
        last = datetime.fromisoformat(updated_at)
        now = datetime.now(timezone.utc)
        return (now - last).total_seconds()
    except Exception as e:
        logger.warning(f"Erro ao calcular idade do cache: {e}")
        return 1e18


def _build_cache_from_feeds() -> Dict[str, Any]:
    logger.info("Iniciando construção do cache a partir dos feeds...")

    limites = dict(LIMITES_PADRAO or {})
    if "📰 Últimas" in FEEDS_BY_TEMA:
        limites["📰 Últimas"] = max(int(limites.get("📰 Últimas", 0) or 0), 100)

    buckets_raw, _flat = coletar_noticias_por_tema(limites)

    buckets_norm: Dict[str, List[Dict[str, Any]]] = {}
    for tema, entries in (buckets_raw or {}).items():
        try:
            entries = list(entries or [])
        except Exception:
            entries = []

        entries.sort(key=entry_ts, reverse=True)

        keep = 140 if tema == "📰 Últimas" else 120
        buckets_norm[tema] = normalize_list(entries, limit=keep)

    logger.info("Cache construído com sucesso.")
    return {"updated_at": _now_iso(), "buckets": buckets_norm}


def refresh_cache_sync() -> None:
    data = _build_cache_from_feeds()
    with _CACHE_LOCK:
        _write_cache_file(data)


def refresh_cache_background() -> None:
    global _REFRESHING
    if not _REFRESH_LOCK.acquire(blocking=False):
        return
    try:
        if _REFRESHING:
            return
        _REFRESHING = True
        try:
            refresh_cache_sync()
        finally:
            _REFRESHING = False
    finally:
        _REFRESH_LOCK.release()


def get_buckets_cached() -> Tuple[Dict[str, List[Dict[str, Any]]], Optional[str]]:
    with _CACHE_LOCK:
        data = _read_cache_file()

    updated_at = data.get("updated_at")
    buckets = data.get("buckets") or {}

    if not updated_at or not buckets:
        refresh_cache_sync()
        with _CACHE_LOCK:
            data = _read_cache_file()
        return (data.get("buckets") or {}), data.get("updated_at")

    if _cache_age_seconds(updated_at) > CACHE_TTL:
        threading.Thread(target=refresh_cache_background, daemon=True).start()

    return buckets, updated_at


def get_section_cached(tema_label: Optional[str], limit: int) -> Tuple[str, List[Dict[str, Any]]]:
    buckets, _updated_at = get_buckets_cached()

    if not tema_label:
        if GERAL_LABEL and GERAL_LABEL in buckets and buckets.get(GERAL_LABEL):
            entries = list(buckets.get(GERAL_LABEL, []) or [])
        else:
            entries = []
            for k in TEMAS:
                entries = list(buckets.get(k, []) or [])
                if entries:
                    break
        entries.sort(key=lambda x: float(x.get("ts", 0.0)), reverse=True)
        titulo = "📰 Geral"
        noticias = entries[:limit]
    else:
        entries = list(buckets.get(tema_label, []) or [])
        entries.sort(key=lambda x: float(x.get("ts", 0.0)), reverse=True)
        titulo = display_label(tema_label)
        noticias = entries[:limit]

    for n in noticias:
        n.pop("ts", None)

    return titulo, noticias


# =========================================================
# Template HTML
# =========================================================
HTML_TEMPLATE = r"""<!doctype html>
<html lang="pt-br">
<head>
  <meta charset="utf-8"/>
  <link rel="dns-prefetch" href="//fonts.googleapis.com">
  <link rel="dns-prefetch" href="//fonts.gstatic.com">
  <meta http-equiv="refresh" content="600">
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Jornal Crucial</title>
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="preload" as="style" href="https://fonts.googleapis.com/css2?family=IM+Fell+English:ital@0;1&family=Libre+Baskerville:wght@400;700&display=swap" onload="this.onload=null;this.rel='stylesheet'">
  <noscript><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IM+Fell+English:ital@0;1&family=Libre+Baskerville:wght@400;700&display=swap"></noscript>
  <style>
    :root{ --paper:#f4f0e4; --ink:#1e1b16; --muted:#5a5146; --rule:#2b241b33; --shadow: 0 10px 30px rgba(0,0,0,.08); }
    body{ margin:0; color:var(--ink); background: radial-gradient(1200px 500px at 50% -100px, rgba(0,0,0,.06), transparent 60%), linear-gradient(180deg, #efe7d4 0%, var(--paper) 40%, #efe7d4 100%); font-family:"Libre Baskerville", serif; }
    .wrap{ max-width:1040px; margin:28px auto 60px; padding:22px; }
    .paper{ background: linear-gradient(0deg, rgba(0,0,0,.03), rgba(0,0,0,0) 40%), repeating-linear-gradient(90deg, rgba(0,0,0,.012) 0, rgba(0,0,0,.012) 2px, transparent 2px, transparent 10px), var(--paper); border:1px solid var(--rule); border-radius:18px; box-shadow:var(--shadow); padding:18px 18px 22px; overflow:hidden; }
    .masthead{ text-align:center; padding:8px 10px 14px; border-bottom:2px solid var(--ink); }
    .masthead .kicker{ font-family:"IM Fell English", serif; letter-spacing:.06em; text-transform:uppercase; font-size:12px; color:var(--muted); margin-bottom:6px; }
    .masthead h1{ margin:0; font-size:42px; letter-spacing:.02em; }
    .meta{ display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap; font-size:12px; color:var(--muted); margin-top:10px; align-items:flex-start; }
    .badge{ display:inline-block; font-size:11px; padding:3px 8px; border:1px solid var(--rule); border-radius:999px; color:var(--muted); background:rgba(255,255,255,.35); }
    .when{ text-align:right; line-height:1.2; } .when .dt{ font-size:12px; } .when .moon{ font-size:12px; margin-top:4px; font-family:"IM Fell English", serif; letter-spacing:.02em; }
    .menu{ display:flex; gap:10px; flex-wrap:wrap; justify-content:center; padding:14px 10px 2px; border-bottom:1px solid var(--rule); margin-bottom:14px; }
    .btn{ appearance:none; border:1px solid var(--rule); background:rgba(255,255,255,.30); color:var(--ink); padding:8px 12px; border-radius:999px; font-size:12.5px; text-decoration:none; line-height:1; box-shadow:0 1px 0 rgba(0,0,0,.04); }
    .btn:hover{ background:rgba(255,255,255,.55); border-color:rgba(30,27,22,.35); }
    .btn.active{ background:rgba(30,27,22,.10); border-color:rgba(30,27,22,.45); font-weight:700; }
    .section{ border:1px solid var(--rule); border-radius:14px; padding:14px 14px 16px; background:rgba(255,255,255,.25); margin-top:14px; }
    .section h2{ margin:0 0 10px 0; font-size:16px; letter-spacing:.04em; text-transform:uppercase; border-bottom:1px solid var(--rule); padding-bottom:8px; }
    .hero{ margin:12px 0 14px; border-radius:14px; overflow:hidden; border:1px solid rgba(0,0,0,.18); box-shadow:0 10px 18px rgba(0,0,0,.06); background:rgba(255,255,255,.25); }
    .hero img{ width:100%; display:block; max-height:260px; object-fit:cover; filter:saturate(.95) contrast(.98); }
    .columns{ column-count:3; column-gap:22px; } @media (max-width:980px){ .columns{ column-count:2; } } @media (max-width:740px){ .columns{ column-count:1; } } @media (max-width:600px){ .wrap{margin:8px auto 32px;padding:10px;} .paper{padding:12px 12px 16px;border-radius:12px;} .masthead h1{font-size:28px;} .masthead .kicker{font-size:10px;} .btn{padding:7px 10px;font-size:12px;} .menu{gap:7px;padding:10px 6px 2px;} .section{padding:10px 10px 14px;} .headline{font-size:14px;} .teaser{font-size:12px;} }
    .item{ break-inside:avoid; margin:0 0 14px 0; padding-bottom:12px; border-bottom:1px dashed var(--rule); overflow-wrap:anywhere; word-break:break-word; }
    .item:last-child{ border-bottom:none; padding-bottom:0; margin-bottom:0; }
    .pub-time{ display:inline-block; font-size:13px; font-family:"IM Fell English", serif; font-style:italic; color:var(--muted); letter-spacing:.04em; margin-bottom:5px; }
    .headline{ font-size:15px; font-weight:700; line-height:1.25; margin:0 0 6px 0; }
    .headline a{ color:inherit; text-decoration:none; border-bottom:none; } .headline a:hover{ text-decoration:none; border-bottom:none; }
    .byline{ font-size:12px; color:var(--muted); margin:0 0 8px 0; font-family:"IM Fell English", serif; }
    .teaser{ margin:0; font-size:12.5px; color:#2a241c; line-height:1.45; }
    .readmore{ display:inline-block; margin-top:8px; font-size:12px; color:var(--muted); text-decoration:none; border-bottom:1px dotted rgba(30,27,22,.35); }
    .readmore:hover{ color:var(--ink); border-bottom-color:rgba(30,27,22,.85); }

    /* Últimas: 1 coluna, com o MESMO horário (pub-time) antes da manchete */
    .ultimas-list{ list-style:none; padding:0; margin:0; }
    .ultimas-item{ break-inside:avoid; margin:0 0 10px 0; padding:0 0 10px 0; border-bottom:1px dashed var(--rule); }
    .ultimas-item:last-child{ border-bottom:none; padding-bottom:0; margin-bottom:0; }
    .ultimas-a{ color:inherit; text-decoration:none; font-weight:700; line-height:1.25; display:block; }
    .ultimas-a:hover{ text-decoration:underline; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="paper">
      <div class="masthead">
        <div class="kicker">Edição local • papel & tinta • sem login</div>
        <h1>Jornal Crucial</h1>
        <div class="meta">
          <div><span class="badge">Manchetes por tema</span></div>
          <div class="when">
            <div class="dt">{{ agora }}</div>
            <div class="moon">{{ fase_lua }}</div>
          </div>
        </div>
      </div>

      <div class="menu">
        <a class="btn {{ 'active' if active_slug == 'geral' else '' }}" href="/">📰 Geral</a>
        {% for t in temas %}
          <a class="btn {{ 'active' if active_slug == t.slug else '' }}" href="/tema/{{ t.slug }}">{{ t.label }}</a>
        {% endfor %}
      </div>

      <div class="section">
        <h2>{{ titulo_secao }}</h2>

        {% if imagem %}
          <div class="hero">
            <img src="{{ url_for('static', filename=imagem) }}" alt="Imagem do tema" loading="lazy" decoding="async"/>
          </div>
        {% endif %}

        {% if noticias %}
          {% if so_manchetes %}
            <ul class="ultimas-list">
              {% for n in noticias %}
                <li class="ultimas-item">
                  {% if n.hora %}
                    <span class="pub-time">{{ n.hora }}</span>
                  {% endif %}
                  {% if n.link %}
                    <a class="ultimas-a" href="{{ n.link }}" target="_blank" rel="noopener">{{ n.titulo }}</a>
                  {% else %}
                    <span class="ultimas-a">{{ n.titulo }}</span>
                  {% endif %}
                </li>
              {% endfor %}
            </ul>
          {% else %}
            <div class="columns">
              {% for n in noticias %}
                <div class="item">
                  {% if n.hora %}
                    <span class="pub-time">{{ n.hora }}</span>
                  {% endif %}
                  <div class="headline">
                    {% if n.link %}
                      <a href="{{ n.link }}" target="_blank" rel="noopener">{{ n.titulo }}</a>
                    {% else %}
                      {{ n.titulo }}
                    {% endif %}
                  </div>

                  {% if n.fonte %}
                    <div class="byline">{{ n.fonte }}</div>
                  {% endif %}

                  {% if n.resumo %}
                    <p class="teaser">{{ n.resumo }}</p>
                  {% endif %}

                  {% if n.link %}
                    <a class="readmore" href="{{ n.link }}" target="_blank" rel="noopener">Ler completa</a>
                  {% endif %}
                </div>
              {% endfor %}
            </div>
          {% endif %}
        {% else %}
          <p class="teaser">Sem notícias agora.</p>
        {% endif %}
      </div>
    </div>
  </div>
</body>
</html>
"""

# =========================================================
# Rotas
# =========================================================
@app.get("/")
def home():
    titulo, noticias = get_section_cached(None, limit=40)

    now = datetime.now(tz=TZ_BR)
    agora = now.strftime("%d/%m/%Y • %H:%M")
    lua = fase_da_lua(now)
    img = escolher_imagem_sem_repetir("Geral")

    resp = app.make_response(
        render_template_string(
            HTML_TEMPLATE,
            temas=build_menu(),
            active_slug="geral",
            titulo_secao=titulo,
            noticias=noticias,
            agora=agora,
            fase_lua=lua,
            imagem=img,
            so_manchetes=False,
        )
    )
    resp.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=300"
    return resp


@app.get("/tema/<slug>")
def por_tema(slug: str):
    tema_label = TEMA_SLUGS.get(slug)
    if not tema_label:
        abort(404, description="Tema não encontrado")

    if tema_label == "📰 Últimas":
        limit = 100
        so_manchetes = True
    else:
        limit = 60
        so_manchetes = False

    titulo, noticias = get_section_cached(tema_label, limit=limit)

    now = datetime.now(tz=TZ_BR)
    agora = now.strftime("%d/%m/%Y • %H:%M")
    lua = fase_da_lua(now)
    img = escolher_imagem_sem_repetir(tema_label)

    resp = app.make_response(
        render_template_string(
            HTML_TEMPLATE,
            temas=build_menu(),
            active_slug=slug,
            titulo_secao=titulo,
            noticias=noticias,
            agora=agora,
            fase_lua=lua,
            imagem=img,
            so_manchetes=so_manchetes,
        )
    )
    resp.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=300"
    return resp


@app.get("/refresh")
def refresh():
    refresh_cache_sync()
    return {"ok": True, "updated_at": _now_iso()}


@app.get("/favicon.ico")
def favicon():
    return "", 204


@app.get("/health")
def health():
    return {"status": "ok"}


# =========================================================
# Inicialização
# =========================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5001"))
    threading.Thread(target=refresh_cache_background, daemon=True).start()
    app.run(host="0.0.0.0", port=port)
