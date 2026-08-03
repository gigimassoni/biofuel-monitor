#!/usr/bin/env python3
"""
BioFuel Monitor - Raizen Novos Negocios
Google News RSS + Gemini (modelo descoberto automaticamente via ListModels)
"""

import html
import json
import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

MAX_PER_QUERY   = 15    # itens por busca no Google News (antes: 5)
FILTER_CHUNK    = 60    # titulos por chamada de filtro
SUMMARY_BATCH   = 10    # noticias por chamada de resumo
MIN_INTERVAL    = 7.0   # segundos minimos entre chamadas ao Gemini (limite 10/min)
CACHE_FILE      = "resumos.json"
CACHE_DIAS      = 45    # descarta resumos mais antigos que isso

# Descobertos automaticamente em tempo de execucao
_MODEL = None
_API_VERSION = None
_CANDIDATES = []    # modelos alternativos, em ordem de preferencia
_GEMINI_OK = True   # vira False se a descoberta falhar (evita repetir chamadas)


# ==========================================================
#  DESCOBERTA AUTOMATICA DO MODELO (ListModels)
# ==========================================================

def _score_model(name: str) -> int:
    """Pontua um modelo: preferimos flash/lite recentes e de texto."""
    low = name.lower()
    score = 0
    # tipos que NAO servem para resumo de texto
    for bad in ("embedding", "aqa", "imagen", "veo", "tts", "audio",
                "image", "vision", "live", "learnlm", "gemma"):
        if bad in low:
            return -999
    if "flash" in low:
        score += 100
    if "lite" in low:
        score += 10
    if "pro" in low:
        score += 40
    if "preview" in low or "-exp" in low or "experimental" in low:
        score -= 8
    if "thinking" in low:
        score -= 15
    # versao: gemini-3.1-... ou gemini-3-...
    mv = re.search(r"gemini-(\d+)(?:\.(\d+))?", low)
    if mv:
        score += int(mv.group(1)) * 40
        if mv.group(2):
            score += int(mv.group(2)) * 4
    return score


def _list_models(version: str):
    """Chama ListModels e devolve nomes que suportam generateContent."""
    usable, page_token, pages = [], None, 0
    while pages < 5:
        url = f"https://generativelanguage.googleapis.com/{version}/models?pageSize=200"
        if page_token:
            url += f"&pageToken={page_token}"
        req = urllib.request.Request(url, headers={"x-goog-api-key": GEMINI_API_KEY})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for m in data.get("models", []):
            name = m.get("name", "").replace("models/", "")
            methods = m.get("supportedGenerationMethods") or m.get("supportedActions") or []
            if "generateContent" in methods:
                usable.append(name)
        page_token = data.get("nextPageToken")
        pages += 1
        if not page_token:
            break
    return usable


def discover_model():
    """Descobre qual modelo a SUA chave pode usar. Roda uma unica vez."""
    global _MODEL, _API_VERSION, _GEMINI_OK
    if _MODEL or not _GEMINI_OK:
        return _MODEL, _API_VERSION
    if not GEMINI_API_KEY:
        print("  [GEMINI] Nenhuma chave encontrada.")
        _GEMINI_OK = False
        return None, None

    print("  [GEMINI] Descobrindo modelos disponiveis para a sua chave...")
    for version in ("v1beta", "v1"):
        try:
            usable = _list_models(version)
        except Exception as e:
            print(f"    [{version}] ListModels falhou: {e}")
            continue

        if not usable:
            print(f"    [{version}] nenhum modelo com generateContent.")
            continue

        print(f"    [{version}] modelos disponiveis ({len(usable)}):")
        for n in usable:
            print(f"        - {n}")

        ranked = sorted(usable, key=_score_model, reverse=True)
        best = [n for n in ranked if _score_model(n) > -999]
        if not best:
            continue
        globals()["_CANDIDATES"] = best[1:6]
        _MODEL, _API_VERSION = best[0], version
        print(f"    >>> MODELO ESCOLHIDO: {_MODEL}  (API {version})")
        return _MODEL, _API_VERSION

    print("    [GEMINI] Nao foi possivel descobrir nenhum modelo utilizavel.")
    _GEMINI_OK = False
    return None, None


_ULTIMA_CHAMADA = 0.0


def gemini_call(prompt: str, max_tokens: int = 2000, temperature: float = 0.2) -> str:
    """Chama o Gemini com o modelo descoberto. Devolve texto ou string vazia."""
    global _GEMINI_OK, _ULTIMA_CHAMADA
    if not _GEMINI_OK:
        return ""
    # respeita o limite de requisicoes por minuto
    espera = MIN_INTERVAL - (time.time() - _ULTIMA_CHAMADA)
    if espera > 0:
        time.sleep(espera)
    _ULTIMA_CHAMADA = time.time()
    model, version = discover_model()
    if not model:
        return ""

    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
    }).encode("utf-8")

    url = f"https://generativelanguage.googleapis.com/{version}/models/{model}:generateContent"
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print(f"    [GEMINI] {model} falhou: {e}")
        # troca para o proximo modelo da lista e tenta de novo (uma vez)
        if _CANDIDATES:
            proximo = _CANDIDATES.pop(0)
            print(f"    [GEMINI] trocando para: {proximo}")
            globals()["_MODEL"] = proximo
            return gemini_call(prompt, max_tokens, temperature)
        print("    [GEMINI] sem modelos alternativos - seguindo sem IA.")
        globals()["_GEMINI_OK"] = False
        return ""


def _parse_json(text: str):
    """Extrai JSON de uma resposta do modelo, tolerando markdown em volta."""
    if not text:
        return None
    t = re.sub(r"```json|```", "", text).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    m = re.search(r"[\{\[].*[\}\]]", t, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


# ==========================================================
#  QUERIES E REGRAS
# ==========================================================

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
    # cobertura adicional
    {"cat": "saf",   "query": '"SAF" offtake OR agreement OR contract airline supply'},
    {"cat": "saf",   "query": '"SAF" plant OR refinery construction capacity announcement'},
    {"cat": "saf",   "query": '"SAF" ICAO OR ISCC OR RSB certification aviation fuel'},
    {"cat": "bio",   "query": '"IMO" "Net-Zero Framework" OR "carbon intensity" shipping fuel'},
    {"cat": "bio",   "query": '"green corridor" OR "green shipping" biofuel port bunkering'},
    {"cat": "blend", "query": '"ethanol" export OR import market demand mandate country'},
    {"cat": "blend", "query": '"flex fuel" OR "flex-fuel" ethanol gasoline vehicle policy'},
]

NOISE = [
    "golf", "golfe", "bunker shot", "british open", "ryder cup",
    "football club", "futebol", "soccer", "premier league", "champions league",
    "flamengo", "corinthians", "palmeiras", "sociedade anonima do futebol",
    "arbitration report", "kluwer", "law review", "legal journal",
    "week in technology", "capa airline leader summit",
    "sign up to read", "start a free trial", "access newswire", "tradingview",
]

COUNTRY_RULES = [
    ("BR", "Brasil",          ["brasil", "brazil", "brazilian", "petrobras", "anp", "renovabio", "embraer", "raizen"]),
    ("US", "EUA",             ["united states", " u.s.", "usa", "american", "faa", "epa", "washington", "california", "boeing"]),
    ("EU", "Uniao Europeia",  ["european union", "eu commission", "brussels", "refueleu"]),
    ("GB", "Reino Unido",     ["uk", "united kingdom", "britain", "british", "london", "heathrow"]),
    ("DE", "Alemanha",        ["germany", "german", "berlin", "lufthansa"]),
    ("FR", "Franca",          ["france", "french", "paris", "total energies", "airbus", "cma cgm"]),
    ("NL", "Paises Baixos",   ["netherlands", "dutch", "rotterdam", "amsterdam", "shell"]),
    ("SG", "Singapura",       ["singapore", "singaporean", "changi"]),
    ("CN", "China",           ["china", "chinese", "beijing", "sinopec"]),
    ("JP", "Japao",           ["japan", "japanese", "tokyo"]),
    ("IN", "India",           ["india", "indian", "delhi", "mumbai"]),
    ("AU", "Australia",       ["australia", "australian", "qantas"]),
    ("CA", "Canada",          ["canada", "canadian"]),
    ("AE", "Emirados Arabes", ["uae", "emirates", "dubai", "abu dhabi"]),
    ("ID", "Indonesia",       ["indonesia", "indonesian", "jakarta"]),
    ("VN", "Vietna",          ["vietnam", "vietnamese", "hanoi"]),
    ("TH", "Tailandia",       ["thailand", "thai", "bangkok"]),
    ("PH", "Filipinas",       ["philippines", "manila"]),
    ("NO", "Noruega",         ["norway", "norwegian"]),
    ("ZA", "Africa do Sul",   ["south africa", "johannesburg"]),
    ("AR", "Argentina",       ["argentina", "buenos aires"]),
    ("CO", "Colombia",        ["colombia", "bogota"]),
    ("MY", "Malasia",         ["malaysia", "kuala lumpur", "petronas"]),
    ("KR", "Coreia do Sul",   ["south korea", "korean", "seoul"]),
    ("NG", "Nigeria",         ["nigeria", "lagos"]),
    ("CL", "Chile",           ["chile", "chilean", "santiago"]),
    ("ES", "Espanha",         ["spain", "spanish", "madrid", "repsol", "iberia"]),
    ("IT", "Italia",          ["italy", "italian", "rome", "eni"]),
]

FLAGS = {
    "BR": "\U0001F1E7\U0001F1F7", "US": "\U0001F1FA\U0001F1F8", "EU": "\U0001F1EA\U0001F1FA",
    "GB": "\U0001F1EC\U0001F1E7", "DE": "\U0001F1E9\U0001F1EA", "FR": "\U0001F1EB\U0001F1F7",
    "NL": "\U0001F1F3\U0001F1F1", "SG": "\U0001F1F8\U0001F1EC", "CN": "\U0001F1E8\U0001F1F3",
    "JP": "\U0001F1EF\U0001F1F5", "IN": "\U0001F1EE\U0001F1F3", "AU": "\U0001F1E6\U0001F1FA",
    "CA": "\U0001F1E8\U0001F1E6", "AE": "\U0001F1E6\U0001F1EA", "ID": "\U0001F1EE\U0001F1E9",
    "VN": "\U0001F1FB\U0001F1F3", "TH": "\U0001F1F9\U0001F1ED", "PH": "\U0001F1F5\U0001F1ED",
    "NO": "\U0001F1F3\U0001F1F4", "ZA": "\U0001F1FF\U0001F1E6", "AR": "\U0001F1E6\U0001F1F7",
    "CO": "\U0001F1E8\U0001F1F4", "MY": "\U0001F1F2\U0001F1FE", "KR": "\U0001F1F0\U0001F1F7",
    "NG": "\U0001F1F3\U0001F1EC", "CL": "\U0001F1E8\U0001F1F1", "ES": "\U0001F1EA\U0001F1F8",
    "IT": "\U0001F1EE\U0001F1F9", "XX": "\U0001F310",
}


# ==========================================================
#  HELPERS
# ==========================================================

def detect_country(title, desc=""):
    text = f" {title} {desc} ".lower()
    for code, name, keywords in COUNTRY_RULES:
        if any(k in text for k in keywords):
            return FLAGS.get(code, FLAGS["XX"]), name
    return FLAGS["XX"], "Global"


def is_noise(title, desc=""):
    text = f"{title} {desc}".lower()
    if title.strip().startswith("[PDF]"):
        return True
    return any(n in text for n in NOISE)


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
        for item in root.findall(".//item")[:MAX_PER_QUERY]:
            title = item.findtext("title") or ""
            title = re.sub(r"\s+-\s+[^-]+$", "", title).strip()
            link  = item.findtext("link") or ""
            desc  = item.findtext("description") or ""
            date  = item.findtext("pubDate") or ""
            source = ""
            ms = re.search(r"<font[^>]*>([^<]+)</font>", desc)
            if ms:
                source = ms.group(1).strip()
            desc_clean = re.sub(r"<[^>]+>", " ", desc)
            desc_clean = re.sub(r"\s+", " ", desc_clean).strip()[:300]
            if title and link:
                items.append({"title": title, "url": link, "date": date,
                              "source": source, "desc": desc_clean})
        return items
    except Exception as e:
        print(f"    AVISO RSS: {e}")
        return []


# ==========================================================
#  FILTRO E RESUMO
# ==========================================================

def simple_filter(all_items):
    """Fallback sem IA: descarta ruido obvio."""
    kept = [i for i in all_items if not is_noise(i["title"], i.get("desc", ""))]
    print(f"    Filtro por palavras-chave: {len(all_items)} -> {len(kept)}")
    return kept


def gemini_filter(all_items):
    """Seleciona as noticias relevantes usando o Gemini, em blocos."""
    if not all_items:
        return []
    if not _GEMINI_OK or not GEMINI_API_KEY:
        return simple_filter(all_items)

    # tira o ruido obvio antes de gastar chamada de IA
    base = [i for i in all_items if not is_noise(i["title"], i.get("desc", ""))]
    print(f"  Pre-filtro por palavras-chave: {len(all_items)} -> {len(base)}")

    total_blocos = (len(base) - 1) // FILTER_CHUNK + 1
    print(f"  Filtrando {len(base)} noticias com Gemini em {total_blocos} bloco(s)...")

    escolhidas = []
    for b in range(total_blocos):
        bloco = base[b * FILTER_CHUNK:(b + 1) * FILTER_CHUNK]
        numbered = "\n".join(
            f"{i+1}. [{it['category'].upper()}] {it['title']}"
            for i, it in enumerate(bloco)
        )
        prompt = (
            "Voce e analista de novos negocios de etanol na Raizen.\n"
            "Abaixo ha titulos de noticias sobre SAF (combustivel de aviacao), "
            "Biobunker (combustivel maritimo) e Blending (mistura etanol+gasolina).\n\n"
            "Retorne os numeros de TODAS as noticias relevantes para o setor. "
            "Seja INCLUSIVO: na duvida, mantenha a noticia. Politicas, mandatos, "
            "producao, contratos, investimentos, tecnologia e mercado sao relevantes.\n"
            "Descarte apenas: esporte, juridico sem ligacao com o setor, preco de "
            "gasolina sem contexto de biocombustivel, agregadores, paginas de indice "
            "e duplicatas.\n"
            "Em BLENDING, no maximo 5 noticias do mesmo pais.\n\n"
            "Responda SOMENTE com JSON: {\"relevantes\": [1, 3, 5]}\n\n"
            f"NOTICIAS:\n{numbered}"
        )
        result = _parse_json(gemini_call(prompt, max_tokens=2000, temperature=0.1))
        if not result or "relevantes" not in result:
            print(f"    Bloco {b+1}: sem resposta valida - mantendo o bloco inteiro.")
            escolhidas.extend(bloco)
            continue
        idxs = [i - 1 for i in result["relevantes"]
                if isinstance(i, int) and 1 <= i <= len(bloco)]
        sel = [bloco[i] for i in idxs]
        print(f"    Bloco {b+1}/{total_blocos}: {len(bloco)} -> {len(sel)}")
        escolhidas.extend(sel)

    if not escolhidas:
        return simple_filter(all_items)
    print(f"    Gemini selecionou {len(escolhidas)} noticias relevantes")
    return escolhidas


def carregar_cache():
    """Le resumos ja gerados em execucoes anteriores."""
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)
        print(f"  Cache: {len(cache)} resumos guardados de execucoes anteriores.")
        return cache
    except Exception:
        print("  Cache: nenhum arquivo anterior (primeira execucao).")
        return {}


def salvar_cache(cache):
    """Grava o cache, descartando resumos antigos."""
    limite = (datetime.now(timezone.utc) - timedelta(days=CACHE_DIAS)).strftime("%Y-%m-%d")
    limpo = {k: v for k, v in cache.items()
             if isinstance(v, dict) and v.get("data", "9999") >= limite}
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(limpo, f, ensure_ascii=False, indent=1)
        print(f"  Cache salvo: {len(limpo)} resumos "
              f"({len(cache) - len(limpo)} antigos descartados).")
    except Exception as e:
        print(f"  AVISO ao salvar cache: {e}")


def gemini_summarize_batch(batch):
    """Resume um lote de noticias em UMA chamada. Usa titulo + descricao do RSS."""
    if not batch or not _GEMINI_OK or not GEMINI_API_KEY:
        return {}

    blocks = []
    for idx, it in enumerate(batch):
        entry = f"ID {idx+1}:\nTitulo: {it['title']}"
        if it.get("desc"):
            entry += f"\nResumo da fonte: {it['desc']}"
        blocks.append(entry)

    prompt = (
        "Voce e analista do mercado de biocombustiveis (SAF, biobunker maritimo e "
        "mistura etanol+gasolina).\n"
        "Para CADA noticia abaixo, escreva 2 a 3 frases em portugues explicando o que "
        "aconteceu e por que importa para o mercado de etanol. Baseie-se apenas nas "
        "informacoes fornecidas, sem inventar dados.\n\n"
        "Responda SOMENTE com JSON, chaves iguais aos IDs:\n"
        "{\"1\": \"resumo...\", \"2\": \"resumo...\"}\n\n"
        f"NOTICIAS:\n\n" + "\n\n".join(blocks)
    )

    result = _parse_json(gemini_call(prompt, max_tokens=4000, temperature=0.25))
    if not isinstance(result, dict):
        return {}
    return {str(k).strip(): str(v).strip() for k, v in result.items()}


# ==========================================================
#  COLETA
# ==========================================================

def fetch_news():
    seen_urls, seen_titles = set(), set()
    all_items = []

    for search in RSS_SEARCHES:
        cat, query = search["cat"], search["query"]
        print(f"  [{cat.upper()}] {query[:58]}...")
        rss_items = fetch_rss(query)
        print(f"    Retornou {len(rss_items)} itens")

        for r in rss_items:
            url, title = r["url"], r["title"]
            if not url or not title or url in seen_urls:
                continue
            norm = normalize_title(title)
            if norm in seen_titles:
                continue
            seen_urls.add(url)
            seen_titles.add(norm)

            flag, country = detect_country(title, r.get("desc", ""))
            all_items.append({
                "title": title, "url": url, "source": r["source"],
                "desc": r.get("desc", ""),
                "date_str": fmt_date(r["date"]), "date_raw": r["date"],
                "flag": flag, "country": country,
                "category": cat, "summary": "",
            })

    n_saf   = sum(1 for i in all_items if i["category"] == "saf")
    n_bio   = sum(1 for i in all_items if i["category"] == "bio")
    n_blend = sum(1 for i in all_items if i["category"] == "blend")
    print(f"  Total coletado: SAF={n_saf} | Bio={n_bio} | Blend={n_blend}")

    filtered = gemini_filter(all_items)
    filtered.sort(key=lambda x: parse_date_obj(x.get("date_raw", "")), reverse=True)

    if _GEMINI_OK and GEMINI_API_KEY and filtered:
        cache = carregar_cache()
        hoje  = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # aproveita o que ja foi resumido antes
        novas = []
        reaproveitados = 0
        for item in filtered:
            chave = normalize_title(item["title"])
            guardado = cache.get(chave)
            if isinstance(guardado, dict) and guardado.get("resumo"):
                item["summary"] = guardado["resumo"]
                guardado["data"] = hoje          # renova a validade
                reaproveitados += 1
            else:
                novas.append(item)

        print(f"  Resumos reaproveitados do cache: {reaproveitados}")
        print(f"  Noticias novas para resumir: {len(novas)}")

        if novas:
            total_lotes = (len(novas) - 1) // SUMMARY_BATCH + 1
            ok = 0
            for i in range(0, len(novas), SUMMARY_BATCH):
                lote = novas[i:i + SUMMARY_BATCH]
                print(f"    Lote {i // SUMMARY_BATCH + 1}/{total_lotes}...")
                resumos = gemini_summarize_batch(lote)
                for idx, item in enumerate(lote):
                    texto = resumos.get(str(idx + 1), "")
                    item["summary"] = texto
                    if texto:
                        cache[normalize_title(item["title"])] = {
                            "resumo": texto, "data": hoje,
                        }
                        ok += 1
            print(f"  Resumos novos gerados: {ok}/{len(novas)}")

        salvar_cache(cache)

    return filtered


# ==========================================================
#  HTML
# ==========================================================

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
        delay       = min(idx * 15, 500)
        url_esc     = html.escape(item["url"])
        title_esc   = html.escape(item["title"])
        summary_esc = html.escape(item.get("summary") or "Resumo nao disponivel para esta noticia.")
        date_esc    = html.escape(item["date_str"])
        country_esc = html.escape(item["country"])
        source_esc  = html.escape(item["source"])
        title_low   = html.escape(item["title"].lower())
        cat         = item["category"]
        label       = labels[cat]
        flag        = item["flag"]

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
        <span class="news-source">{flag} {country_esc} &middot; {source_esc}</span>
        <div class="news-actions">
          <button class="btn-resumo" onclick="toggleResumo({idx})" id="btn-{idx}">Resumo</button>
          <a class="news-read" href="{url_esc}" target="_blank" rel="noopener">Ler</a>
        </div>
      </div>
    </div>"""

    if not items:
        cards_html = """
    <div class="empty">
      <div class="empty-icon">-</div>
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
    print("=" * 60)
    print("BioFuel Monitor - iniciando...")
    if GEMINI_API_KEY:
        print(f"GEMINI_API_KEY presente (inicio: {GEMINI_API_KEY[:6]}..., "
              f"{len(GEMINI_API_KEY)} caracteres)")
        discover_model()
    else:
        print("GEMINI_API_KEY AUSENTE - rodando sem IA (filtro por palavras-chave).")
    print("=" * 60)

    items = fetch_news()
    print(f"Total final: {len(items)} noticias")

    output = render_html(items)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(output)
    print("index.html gerado com sucesso!")


if __name__ == "__main__":
    main()
