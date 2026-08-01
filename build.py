#!/usr/bin/env python3
"""
BioFuel Monitor - Raizen Novos Negocios
Google News RSS para buscar noticias + Gemini para filtrar e resumir.
"""

import html
import json
import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import quote

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

def get_gemini_url():
    return "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent"

def make_gemini_request(payload: bytes) -> urllib.request.Request:
    return urllib.request.Request(
        get_gemini_url(),
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": GEMINI_API_KEY,
        },
        method="POST"
    )

RSS_SEARCHES = [
    # SAF
    {"cat": "saf",   "query": '"sustainable aviation fuel"'},
    {"cat": "saf",   "query": '"SAF" "aviation" mandate OR production OR airline OR investment'},
    {"cat": "saf",   "query": '"SAF" "ethanol" OR "alcohol to jet" OR "ATJ" OR "sugarcane"'},
    {"cat": "saf",   "query": '"CORSIA" OR "ReFuelEU" aviation fuel'},
    {"cat": "saf",   "query": '"SAF" "HEFA" OR "Fischer-Tropsch" OR "power to liquid" OR "e-fuel"'},
    {"cat": "saf",   "query": '"sustainable aviation fuel" Brazil OR Raizen OR sugarcane OR ethanol'},
    # Biobunker
    {"cat": "bio",   "query": '"marine biofuel" OR "bio-bunker" OR "biobunker"'},
    {"cat": "bio",   "query": '"ethanol" "shipping" OR "vessel" OR "bunker fuel" OR "maritime"'},
    {"cat": "bio",   "query": '"CMA CGM" OR "Maersk" OR "MSC" OR "Hapag" biofuel OR ethanol bunker'},
    {"cat": "bio",   "query": '"IMO" "biofuel" OR "green fuel" shipping decarbonization'},
    {"cat": "bio",   "query": '"FuelEU Maritime" OR "IMO 2050" shipping biofuel'},
    {"cat": "bio",   "query": '"ethanol bunker" OR "ethanol bunkering" shipping worldwide'},
    # Blending
    {"cat": "blend", "query": '"ethanol blending mandate" ethanol gasoline worldwide'},
    {"cat": "blend", "query": '"E10" OR "E15" OR "E20" OR "E25" OR "E30" ethanol mandate 2026'},
    {"cat": "blend", "query": '"RenovaBio" OR "ANP" OR "etanol" mistura gasolina Brasil'},
    {"cat": "blend", "query": '"ethanol blend" Europe OR Africa OR "Middle East" mandate policy'},
    {"cat": "blend", "query": '"ethanol blend" Brazil OR Argentina OR Colombia OR Paraguay mandate'},
    {"cat": "blend", "query": '"ethanol blending" Indonesia OR Vietnam OR Thailand OR Philippines policy'},
    {"cat": "blend", "query": '"ethanol blending" USA OR Canada OR "United States" policy 2026'},
]

COUNTRY_RULES = [
    ("🇧🇷", "Brasil",          ["brasil", "brazil", "brazilian", "petrobras", "anp", "renovabio", "embraer", "raizen"]),
    ("🇺🇸", "EUA",             ["united states", " u.s.", "usa", "american", "faa", "epa", "washington", "california", "boeing"]),
    ("🇪🇺", "Uniao Europeia",  ["european union", "eu commission", "brussels", "refueleu"]),
    ("🇬🇧", "Reino Unido",     ["uk", "united kingdom", "britain", "british", "london", "heathrow"]),
    ("🇩🇪", "Alemanha",        ["germany", "german", "berlin", "lufthansa"]),
    ("🇫🇷", "Franca",          ["france", "french", "paris", "total energies", "airbus"]),
    ("🇳🇱", "Paises Baixos",   ["netherlands", "dutch", "rotterdam", "amsterdam", "shell"]),
    ("🇸🇬", "Singapura",       ["singapore", "singaporean", "changi"]),
    ("🇨🇳", "China",           ["china", "chinese", "beijing", "sinopec"]),
    ("🇯🇵", "Japao",           ["japan", "japanese", "tokyo"]),
    ("🇮🇳", "India",           ["india", "indian", "delhi", "mumbai"]),
    ("🇦🇺", "Australia",       ["australia", "australian", "qantas"]),
    ("🇨🇦", "Canada",          ["canada", "canadian"]),
    ("🇦🇪", "Emirados Arabes", ["uae", "emirates", "dubai", "abu dhabi"]),
    ("🇮🇩", "Indonesia",       ["indonesia", "indonesian", "jakarta"]),
    ("🇻🇳", "Vietna",          ["vietnam", "vietnamese", "hanoi"]),
    ("🇹🇭", "Tailandia",       ["thailand", "thai", "bangkok"]),
    ("🇵🇭", "Filipinas",       ["philippines", "manila"]),
    ("🇳🇴", "Noruega",         ["norway", "norwegian"]),
    ("🇿🇦", "Africa do Sul",   ["south africa", "johannesburg"]),
    ("🇦🇷", "Argentina",       ["argentina", "buenos aires"]),
    ("🇨🇴", "Colombia",        ["colombia", "bogota"]),
    ("🇲🇾", "Malasia",         ["malaysia", "kuala lumpur", "petronas"]),
    ("🇰🇷", "Coreia do Sul",   ["south korea", "korean", "seoul"]),
    ("🇳🇬", "Nigeria",         ["nigeria", "lagos"]),
    ("🇨🇱", "Chile",           ["chile", "chilean", "santiago"]),
    ("🇪🇸", "Espanha",         ["spain", "spanish", "madrid", "repsol", "iberia"]),
    ("🇮🇹", "Italia",          ["italy", "italian", "rome", "eni"]),
]


def detect_country(title, summary=""):
    text = f" {title} {summary} ".lower()
    for flag, name, keywords in COUNTRY_RULES:
        if any(k in text for k in keywords):
            return flag, name
    return "🌐", "Global"


def normalize_title(title):
    t = re.sub(r"[^\w\s]", "", title.lower().strip())
    return re.sub(r"\s+", " ", t)


def fmt_date(date_str):
    if not date_str:
        return "hoje"
    try:
        from email.utils import parsedate_to_datetime
        d = parsedate_to_datetime(date_str)
        diff = int((datetime.now(timezone.utc) - d).total_seconds() / 60)
        if diff < 60:    return f"ha {diff}min"
        if diff < 1440:  return f"ha {diff // 60}h"
        if diff < 10080: return f"ha {diff // 1440}d"
        return d.strftime("%d/%m/%Y")
    except Exception:
        return "hoje"


def parse_date_obj(date_str):
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(date_str)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def build_rss_url(query):
    return f"https://news.google.com/rss/search?q={quote(query)}&hl=en-US&gl=US&ceid=US:en"


def fetch_rss(query):
    url = build_rss_url(query)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)
        items = []
        for item in root.findall(".//item")[:5]:
            title  = item.findtext("title") or ""
            title  = re.sub(r"\s+-\s+[^-]+$", "", title).strip()
            link   = item.findtext("link") or ""
            desc   = item.findtext("description") or ""
            date   = item.findtext("pubDate") or ""
            source = ""
            m = re.search(r"<font[^>]*>([^<]+)</font>", desc)
            if m:
                source = m.group(1).strip()
            if title and link:
                items.append({"title": title, "url": link, "date": date, "source": source})
        return items
    except Exception as e:
        print(f"    AVISO RSS: {e}")
        return []


def gemini_filter(items_by_cat: dict) -> list:
    """Usa Gemini para filtrar apenas noticias realmente relevantes para o setor."""
    all_items = []
    # Coleta proporcional para garantir que Blending nao seja cortado
    for cat in ["saf", "bio", "blend"]:
        for item in items_by_cat.get(cat, []):
            item["category"] = cat
            all_items.append(item)

    if not all_items or not GEMINI_API_KEY:
        return all_items

    print(f"  Filtrando {len(all_items)} noticias coletadas com Gemini...")

    numbered = "\n".join([
        f"{i+1}. [{item['category'].upper()}] {item['title']}"
        for i, item in enumerate(all_items)
    ])

    prompt = (
        "Voce e um analista especializado em biocombustiveis para uma empresa de etanol (Raizen).\n"
        "Avalie cada noticia abaixo e retorne APENAS os numeros das RELEVANTES para o setor de novos negocios.\n\n"
        "Descarte: esportes, relatorios juridicos, precos de gasolina sem contexto de biocombustivel, "
        "agregadores, duplicatas, e qualquer coisa fora do contexto de biocombustiveis.\n\n"
        "IMPORTANTE - Diversidade geografica: para BLENDING, garanta presenca de noticias de varios paises "
        "e nao selecione mais de 3 do mesmo pais.\n\n"
        "Retorne APENAS JSON no formato exato: {\"relevantes\": [1, 3, 5, ...]}\n\n"
        f"NOTICIAS:\n{numbered}"
    )

    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1000}
    }).encode("utf-8")

    try:
        req = make_gemini_request(payload)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        text = re.sub(r"```json|```", "", text).strip()
        result = json.loads(text)
        indices = [i - 1 for i in result.get("relevantes", []) if 1 <= i <= len(all_items)]
        filtered = [all_items[i] for i in indices]
        print(f"    Gemini: {len(all_items)} -> {len(filtered)} noticias relevantes")
        return filtered
    except Exception as e:
        print(f"    AVISO Gemini filter: {e}")
        return all_items


def gemini_summarize_batch(items_batch):
    """Gera resumos para um lote de noticias em uma unica requisicao."""
    if not GEMINI_API_KEY or not items_batch:
        return {}

    prompt_items = []
    for idx, item in enumerate(items_batch):
        prompt_items.append(f"ID {idx+1}:\nTitulo: {item['title']}\nURL: {item['url']}")

    news_block = "\n\n".join(prompt_items)

    prompt = (
        "Voce e um analista especialista em biocombustiveis (SAF, Biobunker, Blending de etanol).\n"
        "Para CADA noticia abaixo, faca um resumo objetivo em portugues de 2 a 3 frases "
        "focado no mercado e nos impactos comerciais/regulatorios.\n\n"
        "Retorne APENAS um objeto JSON onde as chaves sejam os IDs (como string) "
        "e o valor seja o texto do resumo.\n"
        "Exemplo: {\"1\": \"Resumo...\", \"2\": \"Resumo...\"}\n\n"
        f"NOTICIAS:\n{news_block}"
    )

    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 2000,
            "responseMimeType": "application/json"
        }
    }).encode("utf-8")

    try:
        req = make_gemini_request(payload)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()

        # Parse robusto — limpa markdown e tenta varios formatos
        text = re.sub(r"```json|```", "", text).strip()

        # Tenta parse direto
        try:
            result = json.loads(text)
            return {str(k): str(v) for k, v in result.items()}
        except Exception:
            pass

        # Tenta extrair JSON do texto
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            result = json.loads(m.group(0))
            return {str(k): str(v) for k, v in result.items()}

        return {}

    except Exception as e:
        print(f"    AVISO resumo em lote: {e}")
        return {}


def fetch_news():
    seen_urls   = set()
    seen_titles = set()
    items_by_cat = {"saf": [], "bio": [], "blend": []}

    for search in RSS_SEARCHES:
        cat   = search["cat"]
        query = search["query"]
        print(f"  [{cat.upper()}] {query[:60]}...")
        rss_items = fetch_rss(query)
        print(f"    Retornou {len(rss_items)} itens")

        for r in rss_items:
            url   = r["url"]
            title = r["title"]
            if not url or not title:
                continue
            if url in seen_urls:
                continue
            norm = normalize_title(title)
            if norm in seen_titles:
                continue
            seen_urls.add(url)
            seen_titles.add(norm)

            flag, country = detect_country(title)
            items_by_cat[cat].append({
                "title":    title,
                "url":      url,
                "source":   r["source"],
                "date_str": fmt_date(r["date"]),
                "date_raw": r["date"],
                "flag":     flag,
                "country":  country,
                "summary":  ""
            })

    print(f"  Total coletado: SAF={len(items_by_cat['saf'])} | Bio={len(items_by_cat['bio'])} | Blend={len(items_by_cat['blend'])}")

    # 1. Filtra com Gemini
    filtered = gemini_filter(items_by_cat)

    # 2. Ordena por data
    filtered.sort(key=lambda x: parse_date_obj(x.get("date_raw", "")), reverse=True)

    # 3. Gera resumos em lotes (apenas top 20 para respeitar rate limit)
    if GEMINI_API_KEY and filtered:
        top      = min(20, len(filtered))
        to_summarize = filtered[:top]
        batch_size   = 10
        print(f"  Gerando resumos para {top} noticias em lotes de {batch_size}...")
        time.sleep(60)  # Pausa de 1 minuto para resetar rate limit apos filtro

        for i in range(0, top, batch_size):
            batch = to_summarize[i:i + batch_size]
            lote  = i // batch_size + 1
            total = (top - 1) // batch_size + 1
            print(f"    Lote {lote}/{total}...")

            resumos = gemini_summarize_batch(batch)
            for idx, item in enumerate(batch):
                item["summary"] = resumos.get(str(idx + 1), "")

            if i + batch_size < top:
                time.sleep(60)  # 1 minuto entre lotes

        for item in filtered[top:]:
            item["summary"] = ""

    return filtered


def render_html(items):
    now    = datetime.now(timezone.utc).strftime("%d/%m/%Y as %H:%M UTC")
    labels = {"saf": "SAF", "bio": "Biobunker", "blend": "Blending"}
    counts = {
        "all":   len(items),
        "saf":   sum(1 for i in items if i["category"] == "saf"),
        "bio":   sum(1 for i in items if i["category"] == "bio"),
        "blend": sum(1 for i in items if i["category"] == "blend"),
    }

    cards_html = ""
    for idx, item in enumerate(items):
        delay          = min(idx * 15, 500)
        url_esc        = html.escape(item["url"])
        title_esc      = html.escape(item["title"])
        summary_esc    = html.escape(item.get("summary", "") or "Resumo nao disponivel para esta noticia.")
        date_esc       = html.escape(item["date_str"])
        country_esc    = html.escape(item["country"])
        source_esc     = html.escape(item["source"])
        title_low      = html.escape(item["title"].lower())
        cat            = item["category"]
        label          = labels[cat]
        flag           = item["flag"]

        cards_html += f"""
    <div class="news-card" data-cat="{cat}" data-title="{title_low}" style="animation-delay:{delay}ms">
      <div class="news-top">
        <span class="news-badge {cat}">{label}</span>
        <span class="news-time">{date_esc}</span>
      </div>
      <div class="news-title">
        <a href="{url_esc}" target="_blank" rel="noopener">{title_esc}</a>
      </div>
      <div class="news-summary" id="summary-{idx}" style="display:none">{summary_esc}</div>
      <div class="news-footer">
        <span class="news-source">{flag} {country_esc} · {source_esc}</span>
        <div class="news-actions">
          <button class="btn-resumo" onclick="toggleResumo({idx})" id="btn-{idx}">📄 Resumo</button>
          <a class="news-read" href="{url_esc}" target="_blank" rel="noopener">Ler →</a>
        </div>
      </div>
    </div>"""

    if not items:
        cards_html = """
    <div class="empty">
      <div class="empty-icon">📭</div>
      <div class="empty-title">Nenhuma noticia encontrada hoje</div>
      <div class="empty-desc">Tente rodar novamente mais tarde.</div>
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>BioFuel Monitor</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
  *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
  :root{{
    --bg:#111318;--bg2:#1c1f26;--bg3:#23272f;
    --border:#2a2f3a;--border2:#363c4a;
    --text:#e8edf5;--text2:#7c8799;--text3:#404855;
    --saf:#4db8f0;--saf-bg:rgba(77,184,240,0.10);
    --bio:#3dd6a0;--bio-bg:rgba(61,214,160,0.10);
    --blend:#f0b84d;--blend-bg:rgba(240,184,77,0.10);
    --accent:#2bc4a0;--accent-l:#3dd6a0;--r:14px;
  }}
  body{{background:var(--bg);color:var(--text);font-family:'Inter',system-ui,sans-serif;min-height:100vh;padding-bottom:40px}}
  .navbar{{background:var(--bg2);border-bottom:1px solid var(--border);padding:0 20px;height:52px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:200}}
  .nav-logo{{display:flex;align-items:center;gap:8px;text-decoration:none}}
  .nav-logo-mark{{width:28px;height:28px;background:linear-gradient(135deg,#2bc4a0,#065c3d);border-radius:7px;display:flex;align-items:center;justify-content:center;font-size:14px}}
  .nav-logo-name{{font-size:13px;font-weight:700;color:var(--text);letter-spacing:-.3px}}
  .nav-tabs{{display:flex;gap:4px}}
  .nav-tab{{padding:6px 14px;border-radius:8px;font-size:13px;font-weight:500;color:var(--text2);text-decoration:none;transition:all .15s;border:1px solid transparent}}
  .nav-tab:hover{{color:var(--text);background:var(--bg3)}}
  .nav-tab.active{{background:var(--bg3);border-color:var(--border2);color:var(--text)}}
  .header{{padding:20px 20px 16px}}
  .header-title{{font-size:22px;font-weight:700;line-height:1.25;letter-spacing:-.3px}}
  .updated{{font-size:12px;color:var(--text3);margin-top:6px}}
  .search-wrap{{padding:0 20px 16px}}
  .search-box{{display:flex;align-items:center;gap:10px;background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:10px 14px}}
  .search-box input{{flex:1;background:transparent;border:none;outline:none;color:var(--text);font-size:14px}}
  .search-box input::placeholder{{color:var(--text3)}}
  .stats{{padding:0 20px;display:flex;flex-direction:column;gap:10px;margin-bottom:20px}}
  .stat-card{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--r);padding:16px 18px;cursor:pointer;transition:all .15s}}
  .stat-card:hover{{border-color:var(--border2)}}
  .stat-card.active-saf  {{border-color:var(--saf);background:var(--saf-bg)}}
  .stat-card.active-bio  {{border-color:var(--bio);background:var(--bio-bg)}}
  .stat-card.active-blend{{border-color:var(--blend);background:var(--blend-bg)}}
  .stat-card.active-all  {{border-color:var(--accent);background:rgba(43,196,160,0.08)}}
  .stat-head{{display:flex;align-items:center;gap:8px;margin-bottom:6px}}
  .stat-dot{{width:9px;height:9px;border-radius:50%;flex-shrink:0}}
  .stat-label{{font-size:12px;font-weight:600;letter-spacing:.8px;text-transform:uppercase;color:var(--text2)}}
  .stat-num{{font-size:34px;font-weight:700;line-height:1;margin-bottom:2px}}
  .stat-sub{{font-size:12px;color:var(--text3)}}
  .filters{{padding:0 20px 16px;display:flex;gap:8px;overflow-x:auto;scrollbar-width:none}}
  .filters::-webkit-scrollbar{{display:none}}
  .chip{{padding:7px 18px;border-radius:20px;font-size:13px;font-weight:500;border:1px solid var(--border);background:transparent;color:var(--text2);cursor:pointer;white-space:nowrap;transition:all .15s;flex-shrink:0}}
  .chip.a{{background:var(--accent);border-color:var(--accent);color:#fff}}
  .news-wrap{{padding:0 20px;display:flex;flex-direction:column;gap:10px}}
  @keyframes fadeUp{{from{{opacity:0;transform:translateY(6px)}}to{{opacity:1;transform:translateY(0)}}}}
  .news-card{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--r);padding:16px;transition:border-color .15s;animation:fadeUp .2s ease both}}
  .news-card:hover{{border-color:var(--border2)}}
  .news-top{{display:flex;align-items:center;gap:8px;margin-bottom:10px}}
  .news-badge{{font-size:10px;font-weight:700;letter-spacing:.6px;text-transform:uppercase;padding:3px 9px;border-radius:20px;border:1px solid}}
  .news-badge.saf  {{color:var(--saf);border-color:var(--saf);background:var(--saf-bg)}}
  .news-badge.bio  {{color:var(--bio);border-color:var(--bio);background:var(--bio-bg)}}
  .news-badge.blend{{color:var(--blend);border-color:var(--blend);background:var(--blend-bg)}}
  .news-time{{font-size:11px;color:var(--text3);margin-left:auto}}
  .news-title{{font-size:14px;font-weight:500;line-height:1.45;margin-bottom:10px}}
  .news-title a{{color:var(--text);text-decoration:none}}
  .news-title a:hover{{color:var(--accent-l);text-decoration:underline}}
  .news-summary{{font-size:13px;color:var(--text2);line-height:1.65;margin-bottom:10px;padding:12px;background:var(--bg3);border-radius:8px;border-left:3px solid var(--accent)}}
  .news-footer{{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}}
  .news-source{{font-size:11px;color:var(--text3)}}
  .news-actions{{display:flex;align-items:center;gap:8px}}
  .btn-resumo{{font-size:11px;font-weight:500;padding:4px 10px;border-radius:8px;border:1px solid var(--border2);background:var(--bg3);color:var(--text2);cursor:pointer;transition:all .15s}}
  .btn-resumo:hover{{border-color:var(--accent);color:var(--accent-l)}}
  .btn-resumo.open{{border-color:var(--accent);color:var(--accent-l);background:rgba(43,196,160,0.08)}}
  .news-read{{font-size:11px;color:var(--accent-l);text-decoration:none}}
  .news-read:hover{{text-decoration:underline}}
  .empty{{padding:60px 20px;text-align:center;color:var(--text2)}}
  .empty-icon{{font-size:40px;margin-bottom:14px;opacity:.3}}
  .empty-title{{font-size:16px;font-weight:600;color:var(--text);margin-bottom:8px}}
  .empty-desc{{font-size:13px;line-height:1.6;max-width:300px;margin:0 auto}}
  @media(max-width:640px){{
    .navbar{{padding:0 14px}}
    .header,.search-wrap,.stats,.filters,.news-wrap{{padding-left:14px;padding-right:14px}}
  }}
</style>
</head>
<body>
<nav class="navbar">
  <a class="nav-logo" href="index.html">
    <div class="nav-logo-mark">🛢️</div>
    <span class="nav-logo-name">BioFuel Monitor</span>
  </a>
  <div class="nav-tabs">
    <a class="nav-tab active" href="index.html">📰 Noticias</a>
    <a class="nav-tab" href="mapa-mandatos.html">🗺️ Blending</a>
    <a class="nav-tab" href="mapa-saf.html">✈️ SAF</a>
  </div>
</nav>
<div class="header">
  <div class="header-title">Ferramenta de monitoramento de noticias para novos mercados</div>
  <div class="updated">Atualizado em {now}</div>
</div>
<div class="search-wrap">
  <div class="search-box">
    <span>🔍</span>
    <input type="text" id="search-input" placeholder="Buscar..." oninput="renderCards()"/>
  </div>
</div>
<div class="stats">
  <div class="stat-card" id="sc-saf" onclick="setFilter('saf')">
    <div class="stat-head"><span class="stat-dot" style="background:var(--saf)"></span><span class="stat-label">SAF</span></div>
    <div class="stat-num">{counts['saf']}</div><div class="stat-sub">noticias</div>
  </div>
  <div class="stat-card" id="sc-bio" onclick="setFilter('bio')">
    <div class="stat-head"><span class="stat-dot" style="background:var(--bio)"></span><span class="stat-label">Biobunker</span></div>
    <div class="stat-num">{counts['bio']}</div><div class="stat-sub">noticias</div>
  </div>
  <div class="stat-card" id="sc-blend" onclick="setFilter('blend')">
    <div class="stat-head"><span class="stat-dot" style="background:var(--blend)"></span><span class="stat-label">Blending</span></div>
    <div class="stat-num">{counts['blend']}</div><div class="stat-sub">noticias</div>
  </div>
  <div class="stat-card active-all" id="sc-all" onclick="setFilter('all')">
    <div class="stat-head"><span class="stat-dot" style="background:var(--accent)"></span><span class="stat-label">Total</span></div>
    <div class="stat-num">{counts['all']}</div><div class="stat-sub">—</div>
  </div>
</div>
<div class="filters">
  <button class="chip a" id="chip-all"   onclick="setFilter('all')">Todos</button>
  <button class="chip"   id="chip-saf"   onclick="setFilter('saf')">SAF</button>
  <button class="chip"   id="chip-bio"   onclick="setFilter('bio')">Biobunker</button>
  <button class="chip"   id="chip-blend" onclick="setFilter('blend')">Blending</button>
</div>
<div class="news-wrap" id="news-area">{cards_html}
</div>
<script>
let activeFilter = 'all';
function toggleResumo(idx) {{
  const box = document.getElementById('summary-' + idx);
  const btn = document.getElementById('btn-' + idx);
  if (box.style.display !== 'none') {{
    box.style.display = 'none';
    btn.textContent = '📄 Resumo';
    btn.classList.remove('open');
  }} else {{
    box.style.display = 'block';
    btn.textContent = '📄 Fechar';
    btn.classList.add('open');
  }}
}}
function setFilter(f) {{
  activeFilter = f;
  ['all','saf','bio','blend'].forEach(k=>{{
    document.getElementById('chip-'+k).className = 'chip'+(k===f?' a':'');
    document.getElementById('sc-'+k).className   = 'stat-card'+(k===f?' active-'+k:'');
  }});
  renderCards();
}}
function renderCards() {{
  const q = document.getElementById('search-input').value.toLowerCase();
  document.querySelectorAll('.news-card').forEach(c => {{
    const mf = activeFilter === 'all' || c.dataset.cat === activeFilter;
    const ms = !q || c.dataset.title.includes(q);
    c.style.display = (mf && ms) ? 'block' : 'none';
  }});
}}
</script>
</body>
</html>"""


def main():
    print("BioFuel Monitor - iniciando...")
    if not GEMINI_API_KEY:
        print("AVISO: GEMINI_API_KEY nao encontrada.")
    else:
        print(f"GEMINI_API_KEY encontrada: {GEMINI_API_KEY[:8]}... (primeiros 8 chars)")

    items = fetch_news()
    print(f"Total final: {len(items)} noticias")

    output = render_html(items)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(output)
    print("index.html gerado com sucesso!")


if __name__ == "__main__":
    main()
