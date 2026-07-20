#!/usr/bin/env python3
"""
BioFuel Monitor - Raizen Novos Negocios
Busca noticias sobre SAF, Biobunker e Blending usando Google News RSS
para encontrar noticias relevantes e Tavily para extrair o conteudo.
"""

import html
import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import quote

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")

# ── GOOGLE NEWS RSS QUERIES ──
# Queries com aspas para maxima precisao no nicho
RSS_SEARCHES = [

    # SAF - Geral
    {"cat": "saf", "query": '"sustainable aviation fuel"'},
    {"cat": "saf", "query": '"SAF" "aviation fuel" mandate OR production OR airline'},
    {"cat": "saf", "query": '"SAF" "ethanol" OR "alcohol to jet" OR "ATJ"'},
    {"cat": "saf", "query": '"CORSIA" OR "ReFuelEU" "aviation fuel"'},
    {"cat": "saf", "query": '"SAF" "HEFA" OR "Fischer-Tropsch" OR "power to liquid" OR "e-fuel"'},
    {"cat": "saf", "query": '"sustainable aviation fuel" Brazil OR Raizen OR sugarcane OR ethanol'},

    # Biobunker - Maritimo
    {"cat": "bio", "query": '"marine biofuel" OR "bio-bunker" OR "biobunker"'},
    {"cat": "bio", "query": '"ethanol" "shipping" OR "vessel" OR "bunker fuel"'},
    {"cat": "bio", "query": '"CMA CGM" OR "Maersk" OR "MSC" OR "Hapag" biofuel OR ethanol bunker'},
    {"cat": "bio", "query": '"IMO" "biofuel" OR "green fuel" shipping decarbonization'},
    {"cat": "bio", "query": '"FuelEU Maritime" OR "IMO 2050" shipping biofuel'},

    # Blending - Mandatos etanol+gasolina
    {"cat": "blend", "query": '"ethanol blending mandate" OR "blending mandate" ethanol gasoline'},
    {"cat": "blend", "query": '"E10" OR "E15" OR "E20" OR "E25" OR "E30" ethanol mandate'},
    {"cat": "blend", "query": '"RenovaBio" OR "ANP" etanol mistura'},
    {"cat": "blend", "query": '"ethanol" "blending" India OR Indonesia OR Vietnam OR Thailand OR Philippines'},
    {"cat": "blend", "query": '"ethanol blend" Brazil OR Argentina OR Colombia mandate 2026'},
]

MAX_PER_QUERY = 8

# Palavras que indicam ruido absoluto — nao tem nada a ver com o tema
NOISE_TITLE = [
    "golf", "golfe", "bunker shot", "british open", "ryder cup", "golf championship",
    "arbitration report", "kluwer", "law review", "legal journal",
    "week in technology", "aviation week space technology",
    "engine leasing", "capa airline leader summit",
    "football club", "futebol", "soccer club", "premier league",
    "champions league", "serie a", "bundesliga", "la liga",
    "flamengo", "corinthians", "palmeiras", "vasco", "botafogo",
    "fluminense", "gremio", "cruzeiro", "athletico",
    "midfielder", "striker", "transfer window", "manager sacked",
    "sociedade anonima do futebol",
]

NOISE_SUMMARY = [
    "sign up to read", "free email subscription", "subscribe to access",
    "start a free trial", "create an account to",
]

COUNTRY_RULES = [
    ("🇧🇷", "Brasil",          ["brasil", "brazil", "brazilian", "petrobras", "anp", "renovabio", "embraer", "raizen", "sao paulo", "rio de janeiro"]),
    ("🇺🇸", "EUA",             ["united states", " u.s.", "usa", "american", "faa", "epa", "washington", "california", "texas", "boeing", "delta", "united airlines", "american airlines"]),
    ("🇪🇺", "Uniao Europeia",  ["european union", "eu commission", "brussels", "refueleu", "eu parliament", "europe"]),
    ("🇬🇧", "Reino Unido",     ["uk", "united kingdom", "britain", "british", "london", "heathrow"]),
    ("🇩🇪", "Alemanha",        ["germany", "german", "berlin", "lufthansa", "frankfurt"]),
    ("🇫🇷", "Franca",          ["france", "french", "paris", "total energies", "airbus", "air france"]),
    ("🇳🇱", "Paises Baixos",   ["netherlands", "dutch", "rotterdam", "amsterdam", "shell"]),
    ("🇸🇬", "Singapura",       ["singapore", "singaporean", "changi"]),
    ("🇨🇳", "China",           ["china", "chinese", "beijing", "sinopec", "shanghai"]),
    ("🇯🇵", "Japao",           ["japan", "japanese", "tokyo", "ana holdings", "jal", "osaka"]),
    ("🇮🇳", "India",           ["india", "indian", "delhi", "mumbai", "indianoil", "air india"]),
    ("🇦🇺", "Australia",       ["australia", "australian", "qantas", "sydney", "melbourne"]),
    ("🇨🇦", "Canada",          ["canada", "canadian", "toronto", "air canada", "vancouver"]),
    ("🇦🇪", "Emirados Arabes", ["uae", "emirates", "dubai", "abu dhabi", "etihad"]),
    ("🇮🇩", "Indonesia",       ["indonesia", "indonesian", "jakarta", "pertamina"]),
    ("🇻🇳", "Vietna",          ["vietnam", "vietnamese", "hanoi", "ho chi minh"]),
    ("🇹🇭", "Tailandia",       ["thailand", "thai", "bangkok"]),
    ("🇵🇭", "Filipinas",       ["philippines", "filipino", "manila"]),
    ("🇰🇷", "Coreia do Sul",   ["south korea", "korean", "seoul", "korean air"]),
    ("🇳🇴", "Noruega",         ["norway", "norwegian"]),
    ("🇿🇦", "Africa do Sul",   ["south africa", "johannesburg"]),
    ("🇦🇷", "Argentina",       ["argentina", "buenos aires"]),
    ("🇨🇴", "Colombia",        ["colombia", "bogota"]),
    ("🇲🇾", "Malasia",         ["malaysia", "kuala lumpur", "petronas"]),
    ("🇳🇬", "Nigeria",         ["nigeria", "lagos", "abuja"]),
    ("🇮🇱", "Israel",          ["israel", "tel aviv"]),
    ("🇨🇱", "Chile",           ["chile", "chilean", "santiago"]),
    ("🇪🇸", "Espanha",         ["spain", "spanish", "madrid", "iberia", "repsol"]),
    ("🇮🇹", "Italia",          ["italy", "italian", "rome", "eni", "milan"]),
]


def is_noise(title: str, summary: str) -> bool:
    title_l = title.lower()
    summary_l = summary.lower()
    if any(w in title_l for w in NOISE_TITLE):
        return True
    if any(w in summary_l for w in NOISE_SUMMARY):
        return True
    if title.strip().startswith("[PDF]"):
        return True
    return False


def detect_country(title: str, summary: str):
    text = f" {title} {summary} ".lower()
    for flag, name, keywords in COUNTRY_RULES:
        if any(k in text for k in keywords):
            return flag, name
    return "🌐", "Global"


def normalize_title(title: str) -> str:
    t = re.sub(r"[^\w\s]", "", title.lower().strip())
    return re.sub(r"\s+", " ", t)


def fmt_date(date_str: str) -> str:
    if not date_str:
        return "hoje"
    try:
        # RSS date format: "Mon, 19 Jul 2026 10:30:00 GMT"
        from email.utils import parsedate_to_datetime
        d = parsedate_to_datetime(date_str)
        diff = int((datetime.now(timezone.utc) - d).total_seconds() / 60)
        if diff < 60:
            return f"ha {diff}min"
        if diff < 1440:
            return f"ha {diff // 60}h"
        if diff < 10080:
            return f"ha {diff // 1440}d"
        return d.strftime("%d/%m/%Y")
    except Exception:
        return "hoje"


def build_rss_url(query: str) -> str:
    q = quote(query)
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def fetch_rss(query: str, max_results: int = 8) -> list:
    url = build_rss_url(query)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)
        items = []
        for item in root.findall(".//item")[:max_results]:
            title = item.findtext("title") or ""
            link  = item.findtext("link") or ""
            desc  = item.findtext("description") or ""
            date  = item.findtext("pubDate") or ""
            # Clean title: remove " - Source" suffix
            title = re.sub(r"\s+-\s+[^-]+$", "", title).strip()
            # Extract source from description
            source = ""
            m = re.search(r"<font[^>]*>([^<]+)</font>", desc)
            if m:
                source = m.group(1).strip()
            items.append({
                "title": title,
                "url": link,
                "date": date,
                "source": source,
            })
        return items
    except Exception as e:
        print(f"    AVISO RSS: {e}")
        return []


def resolve_url(url: str) -> str:
    """Resolve URL de redirecionamento do Google News para a URL real do artigo."""
    if "news.google.com" not in url:
        return url
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        })
        # Segue o redirecionamento automaticamente
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.url
    except Exception:
        return url


def tavily_extract(url: str) -> str:
    """Extrai o conteudo de uma URL usando Tavily."""
    if not TAVILY_API_KEY:
        return ""
    # Resolve URL real antes de extrair
    real_url = resolve_url(url)
    payload = json.dumps({"urls": [real_url]}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.tavily.com/extract",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {TAVILY_API_KEY}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        results = data.get("results", [])
        if results:
            raw = results[0].get("raw_content", "") or ""
            clean = re.sub(r"\s+", " ", re.sub(r"[#*\[\]|]+", "", raw)).strip()
            return clean[:280]
        return ""
    except Exception:
        return ""


def fetch_news() -> list:
    saf_items, bio_items, blend_items = [], [], []
    seen_urls   = set()
    seen_titles = set()

    for search in RSS_SEARCHES:
        cat   = search["cat"]
        query = search["query"]
        print(f"  [{cat.upper()}] {query[:60]}...")

        rss_items = fetch_rss(query, MAX_PER_QUERY)
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

            if is_noise(title, ""):
                print(f"    [RUIDO] {title[:70]}")
                continue

            seen_urls.add(url)
            seen_titles.add(norm)

            # Extrai resumo via Tavily
            print(f"    [EXTRACT] {title[:60]}")
            summary = tavily_extract(url)

            if is_noise(title, summary):
                print(f"    [RUIDO pos-extract] {title[:60]}")
                seen_urls.discard(url)
                seen_titles.discard(norm)
                continue

            flag, country = detect_country(title, summary)

            item = {
                "title":    title,
                "summary":  summary,
                "url":      url,
                "source":   r["source"],
                "date_str": fmt_date(r["date"]),
                "date_raw": r["date"],
                "category": cat,
                "flag":     flag,
                "country":  country,
            }

            if cat == "saf":
                saf_items.append(item)
            elif cat == "bio":
                bio_items.append(item)
            else:
                blend_items.append(item)

    print(f"  SAF: {len(saf_items)} | Biobunker: {len(bio_items)} | Blending: {len(blend_items)}")

    def parse_date(date_str: str):
        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(date_str)
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    # Intercala: 1 SAF, 1 Bio, 1 Blend para garantir variedade
    interleaved = []
    max_len = max(len(saf_items), len(bio_items), len(blend_items), 1)
    for i in range(max_len):
        if i < len(saf_items):   interleaved.append(saf_items[i])
        if i < len(bio_items):   interleaved.append(bio_items[i])
        if i < len(blend_items): interleaved.append(blend_items[i])

    # Ordena globalmente por data — mais recente primeiro
    interleaved.sort(key=lambda x: parse_date(x.get("date_raw", "")), reverse=True)

    return interleaved


def render_html(items: list) -> str:
    now = datetime.now(timezone.utc).strftime("%d/%m/%Y as %H:%M UTC")

    counts = {
        "all":   len(items),
        "saf":   sum(1 for i in items if i["category"] == "saf"),
        "bio":   sum(1 for i in items if i["category"] == "bio"),
        "blend": sum(1 for i in items if i["category"] == "blend"),
    }

    labels = {"saf": "SAF", "bio": "Biobunker", "blend": "Blending"}

    cards_html = ""
    for idx, item in enumerate(items):
        delay = min(idx * 20, 400)
        desc = f'<div class="news-desc">{html.escape(item["summary"][:250])}...</div>' if item["summary"] else ""
        cards_html += f"""
    <a class="news-card" href="{html.escape(item['url'])}" target="_blank" rel="noopener"
       data-cat="{item['category']}" data-title="{html.escape(item['title'].lower())}"
       style="animation-delay:{delay}ms">
      <div class="news-top">
        <span class="news-badge {item['category']}">{labels[item['category']]}</span>
        <span class="news-time">{html.escape(item['date_str'])}</span>
      </div>
      <div class="news-title">{html.escape(item['title'])}</div>
      {desc}
      <div class="news-footer">
        <span class="news-source">{item['flag']} {html.escape(item['country'])} · {html.escape(item['source'])}</span>
        <span class="news-read">Ler &#8594;</span>
      </div>
    </a>"""

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
    --accent:#2bc4a0;--accent-l:#3dd6a0;
    --r:14px;
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
  .news-card{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--r);padding:16px;text-decoration:none;color:inherit;display:block;transition:border-color .15s;animation:fadeUp .2s ease both}}
  .news-card:hover{{border-color:var(--border2)}}
  .news-top{{display:flex;align-items:center;gap:8px;margin-bottom:10px}}
  .news-badge{{font-size:10px;font-weight:700;letter-spacing:.6px;text-transform:uppercase;padding:3px 9px;border-radius:20px;border:1px solid}}
  .news-badge.saf  {{color:var(--saf);border-color:var(--saf);background:var(--saf-bg)}}
  .news-badge.bio  {{color:var(--bio);border-color:var(--bio);background:var(--bio-bg)}}
  .news-badge.blend{{color:var(--blend);border-color:var(--blend);background:var(--blend-bg)}}
  .news-time{{font-size:11px;color:var(--text3);margin-left:auto}}
  .news-title{{font-size:14px;font-weight:500;line-height:1.45;margin-bottom:8px}}
  .news-desc{{font-size:12px;color:var(--text2);line-height:1.55;margin-bottom:10px}}
  .news-footer{{display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
  .news-source{{font-size:11px;color:var(--text3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:75%}}
  .news-read{{font-size:11px;color:var(--accent-l);margin-left:auto}}
  .empty{{padding:60px 20px;text-align:center;color:var(--text2)}}
  .empty-icon{{font-size:40px;margin-bottom:14px;opacity:.3}}
  .empty-title{{font-size:16px;font-weight:600;color:var(--text);margin-bottom:8px}}
  .empty-desc{{font-size:13px;line-height:1.6;max-width:300px;margin:0 auto}}
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

    if not TAVILY_API_KEY:
        print("AVISO: TAVILY_API_KEY nao encontrada. Resumos nao serao extraidos.")

    items = fetch_news()
    print(f"Total final: {len(items)} noticias")

    output = render_html(items)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(output)
    print("index.html gerado com sucesso!")


if __name__ == "__main__":
    main()
