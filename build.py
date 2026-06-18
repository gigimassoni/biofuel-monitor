#!/usr/bin/env python3
"""
BioFuel Monitor — busca notícias sobre SAF, Biobunker e Blending
no Google News e gera um arquivo index.html estático.

Roda sozinho via GitHub Actions todo dia, sem precisar de chave de API.
"""

import feedparser
import html
import re
import urllib.request
from datetime import datetime, timezone
from urllib.parse import quote

# Google News blocks requests without a browser-like User-Agent
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

# ── Configuração dos temas e queries de busca ──
# Cada tema tem buscas separadas em inglês (mundo) e português (Brasil),
# para cobrir tanto fontes internacionais quanto nacionais.
FEEDS = [
    # ───────── SAF (Sustainable Aviation Fuel) ─────────
    {
        "cat": "saf",
        "label": "SAF",
        "lang": "en",
        "query": (
            '"sustainable aviation fuel" OR "SAF mandate" OR "aviation biofuel" '
            'OR "SAF ATJ" OR "alcohol to jet" OR "SAF production" OR "jet biofuel"'
        ),
    },
    {
        "cat": "saf",
        "label": "SAF",
        "lang": "pt",
        "query": (
            '"combustível sustentável de aviação" OR "SAF" OR "querosene sustentável" '
            'OR "etanol para aviação" OR "biocombustível de aviação"'
        ),
    },

    # ───────── Biobunker (combustível marítimo sustentável) ─────────
    {
        "cat": "bio",
        "label": "Biobunker",
        "lang": "en",
        "query": (
            '"biobunker" OR "marine biofuel" OR "bio-bunker" OR "green shipping fuel" '
            'OR "ethanol shipping fuel" OR "IMO biofuel" OR "IMO decarbonization" '
            'OR "maritime ethanol fuel" OR "shipping ethanol"'
        ),
    },
    {
        "cat": "bio",
        "label": "Biobunker",
        "lang": "pt",
        "query": (
            '"biobunker" OR "combustível marítimo sustentável" OR "etanol marítimo" '
            'OR "etanol para navios" OR "descarbonização marítima" OR "IMO biocombustível"'
        ),
    },

    # ───────── Blending (mandatos de mistura — etanol na gasolina) ─────────
    {
        "cat": "blend",
        "label": "Blending",
        "lang": "en",
        "query": (
            '"ethanol blending mandate" OR "ethanol gasoline blend" OR "E10" OR "E15" OR "E20" OR "E25" '
            'OR "ethanol blending requirement" OR "gasoline ethanol mandate"'
        ),
    },
    {
        "cat": "blend",
        "label": "Blending",
        "lang": "pt",
        "query": (
            '"mistura de etanol na gasolina" OR "mandato de mistura etanol" OR "mistura obrigatória etanol" '
            'OR "RenovaBio mistura etanol" OR "percentual de etanol na gasolina" '
            'OR "ANP mistura etanol"'
        ),
    },

    # ───────── Regulação internacional transversal (CORSIA / mandatos por país) ─────────
    {
        "cat": "saf",
        "label": "SAF",
        "lang": "en",
        "query": '"CORSIA" OR "ReFuelEU aviation" OR "SAF mandate" country OR "national SAF mandate"',
    },
]

MAX_PER_FEED = 12


def build_feed_url(query: str, lang: str = "en") -> str:
    q = quote(query)
    if lang == "pt":
        return f"https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def clean_title(title: str) -> str:
    # Google News titles often end with " - Source Name"
    return re.sub(r" - [^-]{2,60}$", "", title).strip()


def extract_source(title: str, entry) -> str:
    if hasattr(entry, "source") and getattr(entry.source, "title", None):
        return entry.source.title
    m = re.search(r" - ([^-]+)$", title)
    return m.group(1).strip() if m else "Fonte desconhecida"


def fmt_date(parsed_time) -> str:
    if not parsed_time:
        return ""
    dt = datetime(*parsed_time[:6], tzinfo=timezone.utc)
    return dt.strftime("%d/%m %H:%M UTC")


def fetch_news():
    all_items = []
    seen_urls = set()

    for feed_cfg in FEEDS:
        url = build_feed_url(feed_cfg["query"], feed_cfg.get("lang", "en"))
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw_data = resp.read()
            parsed = feedparser.parse(raw_data)
        except Exception as e:
            print(f"  Aviso: falha ao buscar feed '{feed_cfg['label']}': {e}")
            continue

        for entry in parsed.entries[:MAX_PER_FEED]:
            link = entry.get("link", "")
            if not link or link in seen_urls:
                continue
            seen_urls.add(link)

            raw_title = entry.get("title", "Sem título")
            title = clean_title(raw_title)
            source = extract_source(raw_title, entry)
            summary = entry.get("summary", "")
            summary = re.sub(r"<[^>]+>", "", summary).strip()

            all_items.append({
                "title": title,
                "summary": summary[:220],
                "url": link,
                "source": source,
                "date_str": fmt_date(entry.get("published_parsed")),
                "date_sort": entry.get("published_parsed") or (0,) * 9,
                "category": feed_cfg["cat"],
            })

    # Sort newest first
    all_items.sort(key=lambda x: x["date_sort"], reverse=True)
    return all_items


def render_html(items):
    now = datetime.now(timezone.utc).strftime("%d/%m/%Y às %H:%M UTC")

    counts = {
        "all": len(items),
        "saf": sum(1 for i in items if i["category"] == "saf"),
        "bio": sum(1 for i in items if i["category"] == "bio"),
        "blend": sum(1 for i in items if i["category"] == "blend"),
    }

    labels = {"saf": "SAF", "bio": "Biobunker", "blend": "Blending"}

    cards_html = ""
    for i, item in enumerate(items):
        cards_html += f"""
    <a class="news-card" href="{html.escape(item['url'])}" target="_blank" rel="noopener"
       data-cat="{item['category']}" data-title="{html.escape(item['title'].lower())}"
       style="animation-delay:{min(i*20,400)}ms">
      <div class="news-top">
        <span class="news-badge {item['category']}">{labels[item['category']]}</span>
        <span class="news-time">{html.escape(item['date_str'])}</span>
      </div>
      <div class="news-title">{html.escape(item['title'])}</div>
      {f'<div class="news-desc">{html.escape(item["summary"])}…</div>' if item['summary'] else ''}
      <div class="news-footer">
        <span class="news-source">{html.escape(item['source'])}</span>
        <span class="news-read">Ler →</span>
      </div>
    </a>"""

    if not items:
        cards_html = """
    <div class="empty">
      <div class="empty-icon">📭</div>
      <div class="empty-title">Nenhuma notícia encontrada</div>
      <div class="empty-desc">A próxima atualização automática roda em breve.</div>
    </div>"""

    template = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>BioFuel Monitor — SAF · Biobunker · Blending</title>
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

  .header{{padding:28px 20px 16px;display:flex;align-items:flex-start;justify-content:space-between;gap:12px}}
  .header-title{{font-size:32px;font-weight:700;line-height:1.1;letter-spacing:-.5px}}
  .updated{{font-size:12px;color:var(--text3);margin-top:6px}}

  .search-wrap{{padding:0 20px 16px}}
  .search-box{{display:flex;align-items:center;gap:10px;background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:10px 14px}}
  .search-box input{{flex:1;background:transparent;border:none;outline:none;color:var(--text);font-size:14px}}
  .search-box input::placeholder{{color:var(--text3)}}

  .stats{{padding:0 20px;display:flex;flex-direction:column;gap:10px;margin-bottom:20px}}
  .stat-card{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--r);padding:16px 18px;cursor:pointer;transition:all .15s}}
  .stat-card:hover{{border-color:var(--border2)}}
  .stat-card.active-saf  {{border-color:var(--saf);  background:var(--saf-bg)}}
  .stat-card.active-bio  {{border-color:var(--bio);  background:var(--bio-bg)}}
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
  .news-badge.saf  {{color:var(--saf);  border-color:var(--saf);  background:var(--saf-bg)}}
  .news-badge.bio  {{color:var(--bio);  border-color:var(--bio);  background:var(--bio-bg)}}
  .news-badge.blend{{color:var(--blend);border-color:var(--blend);background:var(--blend-bg)}}
  .news-time{{font-size:11px;color:var(--text3);margin-left:auto}}
  .news-title{{font-size:14px;font-weight:500;line-height:1.45;margin-bottom:8px}}
  .news-desc{{font-size:12px;color:var(--text2);line-height:1.55;margin-bottom:10px}}
  .news-footer{{display:flex;align-items:center}}
  .news-source{{font-size:11px;color:var(--text3)}}
  .news-read{{font-size:11px;color:var(--accent-l);margin-left:auto}}

  .empty{{padding:60px 20px;text-align:center;color:var(--text2)}}
  .empty-icon{{font-size:40px;margin-bottom:14px;opacity:.3}}
  .empty-title{{font-size:16px;font-weight:600;color:var(--text);margin-bottom:8px}}
  .empty-desc{{font-size:13px;line-height:1.6;max-width:300px;margin:0 auto}}
</style>
</head>
<body>

<div class="header">
  <div>
    <div class="header-title">Todas<br>as<br>Notícias</div>
    <div class="updated">Atualizado em {now}</div>
  </div>
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
    <div class="stat-num">{counts['saf']}</div><div class="stat-sub">notícias</div>
  </div>
  <div class="stat-card" id="sc-bio" onclick="setFilter('bio')">
    <div class="stat-head"><span class="stat-dot" style="background:var(--blend)"></span><span class="stat-label">Biobunker</span></div>
    <div class="stat-num">{counts['bio']}</div><div class="stat-sub">notícias</div>
  </div>
  <div class="stat-card" id="sc-blend" onclick="setFilter('blend')">
    <div class="stat-head"><span class="stat-dot" style="background:var(--saf)"></span><span class="stat-label">Blending</span></div>
    <div class="stat-num">{counts['blend']}</div><div class="stat-sub">notícias</div>
  </div>
  <div class="stat-card active-all" id="sc-all" onclick="setFilter('all')">
    <div class="stat-head"><span class="stat-dot" style="background:var(--bio)"></span><span class="stat-label">Total</span></div>
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
  const cards = document.querySelectorAll('.news-card');
  cards.forEach(c => {{
    const matchesFilter = activeFilter === 'all' || c.dataset.cat === activeFilter;
    const matchesSearch = !q || c.dataset.title.includes(q);
    c.style.display = (matchesFilter && matchesSearch) ? 'block' : 'none';
  }});
}}
</script>
</body>
</html>"""

    return template


def main():
    print("Buscando notícias...")
    items = fetch_news()
    print(f"Encontradas {len(items)} notícias.")

    output = render_html(items)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(output)
    print("Arquivo index.html gerado com sucesso.")


if __name__ == "__main__":
    main()
