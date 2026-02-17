# app_min.py — Jornal Crucial (somente jornal)
# Cache forte (buckets + seção normalizada) para Render ficar rápido.

from __future__ import annotations

import html as _html
import os
import random
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, redirect, render_template_string, session, url_for

from .jornal2 import FEEDS_BY_TEMA, LIMITES_PADRAO, coletar_noticias_por_tema

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "jornal-crucial-chave-local-1234567890")

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
    "Leia mais", "Veja mais", "VÍDEOS:", "Veja os vídeos", "Siga o canal", "Clique aqui",
    "Participe do canal", "Receba as notícias", "The post", "appeared first on",
    "Leia a íntegra", "Leia a nota", "Assista", "AO VIVO",
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


def normalize_entry(entry: Any) -> Dict[str, str]:
    titulo_raw = _get(entry, "title", "titulo", "headline", default="(sem título)")
    link = _get(entry, "link", "url", "href", default="") or ""
    fonte = _get(entry, "source", "fonte", "publisher", "site", default="") or ""
    resumo_raw = _get(entry, "summary", "description", "content", "resumo", default="")

    return {
        "titulo": summarize(titulo_raw, 140) or "(sem título)",
        "link": str(link),
        "fonte": strip_html(fonte),
        "resumo": summarize(resumo_raw, 320),
    }


def normalize_list(entries: List[Any], limit: int) -> List[Dict[str, str]]:
    seen: set[Tuple[str, str]] = set()
    out: List[Dict[str, str]] = []
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
    return "👪 Família" if tema == "🌍 Geopolítica" else tema


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
    "🌍 Geopolítica": [
        "img/familia/familia1.jpg",
        "img/familia/familia2.jpg",
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


def escolher_imagem_sem_repetir(tema_label: Optional[str]) -> Optional[str]:
    if not tema_label:
        return None
    imagens = IMAGENS_POR_TEMA.get(tema_label) or []
    if not imagens:
        return None

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
# Lua
# =========================================================
def fase_da_lua(dt: datetime) -> str:
    ref = datetime(2000, 1, 6, 18, 14)
    days = (dt - ref).total_seconds() / 86400.0
    synodic = 29.53058867
    age = days % synodic

    if age < 1.84566: return "🌑 Lua Nova"
    if age < 5.53699: return "🌒 Crescente"
    if age < 9.22831: return "🌓 Quarto Crescente"
    if age < 12.91963: return "🌔 Gibosa Crescente"
    if age < 16.61096: return "🌕 Lua Cheia"
    if age < 20.30228: return "🌖 Gibosa Minguante"
    if age < 23.99361: return "🌗 Quarto Minguante"
    if age < 27.68493: return "🌘 Minguante"
    return "🌑 Lua Nova"


def entry_ts(e: Any) -> float:
    try:
        t = e.get("published_parsed") or e.get("updated_parsed")
    except Exception:
        t = None
    try:
        return time.mktime(t) if t else 0.0
    except Exception:
        return 0.0


# =========================================================
# Cache (buckets + seção normalizada)
# =========================================================
CACHE_TTL = int(os.getenv("CACHE_TTL", "180"))

_CACHE: Dict[str, Any] = {
    "ts": 0.0,
    "buckets": None,
    "sections": {},  # key -> (ts, titulo, noticias_normalizadas)
}


def _expired(ts: float) -> bool:
    return (time.time() - ts) > CACHE_TTL


def get_buckets_cached():
    if _CACHE["buckets"] is None or _expired(_CACHE["ts"]):
        buckets, _flat = coletar_noticias_por_tema(LIMITES_PADRAO)
        _CACHE["buckets"] = buckets
        _CACHE["ts"] = time.time()
        _CACHE["sections"] = {}  # invalida seções
    return _CACHE["buckets"]


def get_section_cached(tema_label: Optional[str], limit: int):
    key = f"{tema_label or '__GERAL__'}::{limit}"
    cached = _CACHE["sections"].get(key)
    if cached and not _expired(cached[0]):
        return cached[1], cached[2]

    buckets = get_buckets_cached()

    if not tema_label:
        if GERAL_LABEL and GERAL_LABEL in buckets:
            entries = list(buckets.get(GERAL_LABEL, []) or [])
        else:
            entries = []
            for k in TEMAS:
                entries = list(buckets.get(k, []) or [])
                if entries:
                    break
        entries.sort(key=entry_ts, reverse=True)
        titulo = "📰 Geral"
        noticias = normalize_list(entries, limit=limit)
    else:
        entries = list(buckets.get(tema_label, []) or [])
        entries.sort(key=entry_ts, reverse=True)
        titulo = display_label(tema_label)
        noticias = normalize_list(entries, limit=limit)

    _CACHE["sections"][key] = (time.time(), titulo, noticias)
    return titulo, noticias


# =========================================================
# HTML (seu template)
# =========================================================
HTML = r"""<!doctype html>
<html lang="pt-br">
<head>
  <meta charset="utf-8"/>
  <meta http-equiv="refresh" content="300">
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Jornal Crucial</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=IM+Fell+English:ital@0;1&family=Libre+Baskerville:wght@400;700&display=swap" rel="stylesheet">
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
    .columns{ column-count:3; column-gap:22px; } @media (max-width:980px){ .columns{ column-count:2; } } @media (max-width:740px){ .columns{ column-count:1; } }
    .item{ break-inside:avoid; margin:0 0 14px 0; padding-bottom:12px; border-bottom:1px dashed var(--rule); overflow-wrap:anywhere; word-break:break-word; }
    .item:last-child{ border-bottom:none; padding-bottom:0; margin-bottom:0; }
    .headline{ font-size:15px; font-weight:700; line-height:1.25; margin:0 0 6px 0; }
    .headline a{ color:inherit; text-decoration:none; border-bottom:none; } .headline a:hover{ text-decoration:none; border-bottom:none; }
    .byline{ font-size:12px; color:var(--muted); margin:0 0 8px 0; font-family:"IM Fell English", serif; }
    .teaser{ margin:0; font-size:12.5px; color:#2a241c; line-height:1.45; }
    .readmore{ display:inline-block; margin-top:8px; font-size:12px; color:var(--muted); text-decoration:none; border-bottom:1px dotted rgba(30,27,22,.35); }
    .readmore:hover{ color:var(--ink); border-bottom-color:rgba(30,27,22,.85); }
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
            <img src="{{ url_for('static', filename=imagem) }}" alt="Imagem do tema"/>
          </div>
        {% endif %}

        {% if noticias %}
          <div class="columns">
            {% for n in noticias %}
              <div class="item">
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
    titulo, noticias = get_section_cached(None, limit=80)

    now = datetime.now()
    agora = now.strftime("%d/%m/%Y • %H:%M")
    lua = fase_da_lua(now)
    img = escolher_imagem_sem_repetir("Geral")

    return render_template_string(
        HTML,
        temas=build_menu(),
        active_slug="geral",
        titulo_secao=titulo,
        noticias=noticias,
        agora=agora,
        fase_lua=lua,
        imagem=img,
    )


@app.get("/tema/<slug>")
def por_tema(slug: str):
    tema_label = TEMA_SLUGS.get(slug)
    if not tema_label:
        return redirect(url_for("home"))

    titulo, noticias = get_section_cached(tema_label, limit=60)

    now = datetime.now()
    agora = now.strftime("%d/%m/%Y • %H:%M")
    lua = fase_da_lua(now)
    img = escolher_imagem_sem_repetir(tema_label)

    return render_template_string(
        HTML,
        temas=build_menu(),
        active_slug=slug,
        titulo_secao=titulo,
        noticias=noticias,
        agora=agora,
        fase_lua=lua,
        imagem=img,
    )


@app.get("/favicon.ico")
def favicon():
    return "", 204


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5001"))
    app.run(host="0.0.0.0", port=port)
