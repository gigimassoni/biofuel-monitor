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

MAX_PER_QUERY   = 15    # itens por busca no Google News
JANELA          = "when:7d"   # so noticias dos ultimos 7 dias.
                              # use "when:1d" para so hoje, ou "" para desligar.
FILTER_CHUNK    = 60    # titulos por chamada de filtro
SUMMARY_BATCH   = 10    # noticias por chamada de resumo
MIN_INTERVAL    = 7.0   # segundos minimos entre chamadas ao Gemini (limite 10/min)
RSS_PAUSA       = 1.5   # segundos entre buscas no Google News (evita bloqueio 503)
RSS_TENTATIVAS  = 3     # quantas vezes reptir uma busca que falhou
CACHE_FILE      = "resumos.json"
CACHE_DIAS      = 45    # descarta resumos mais antigos que isso
MAPA_BLEND      = "mapa-mandatos.html"
PEND_FILE       = "pendencias.json"
PEND_HTML       = "pendencias.html"
REPO            = os.environ.get("GITHUB_REPOSITORY", "gigimassoni/biofuel-monitor")
ARQUIVO_FILE    = "arquivo.json"
ARQUIVO_HTML    = "arquivo.html"
ARQUIVO_DIAS    = 90    # quanto tempo o arquivo guarda (3 meses)
ARQUIVO_MOSTRAR = 600   # quantas noticias a pagina de arquivo exibe
DESTAQUES_FILE  = "destaques.json"
MAX_DESTAQUES   = 6     # quantos destaques a semana carrega

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

    # --- rota ATJ: quem produz SAF a partir de etanol ---
    {"cat": "saf",   "query": '"LanzaJet" OR "Gevo" OR "Summit Next Gen" OR "Vertimass"'},
    {"cat": "saf",   "query": '"ethanol-to-jet" OR "alcohol-to-jet" plant OR investment OR offtake'},

    # --- etanol de milho, apenas quando ligado as tres frentes ---
    {"cat": "saf",   "query": '"corn ethanol" SAF OR "aviation fuel" OR "alcohol to jet"'},
    {"cat": "blend", "query": '"corn ethanol" blending mandate OR blend rate policy'},

    # --- barreiras comerciais e acesso a mercado ---
    {"cat": "blend", "query": 'ethanol tariff OR "import duty" OR "trade barrier" fuel market'},
    {"cat": "blend", "query": '"RED III" OR "Renewable Energy Directive" ethanol OR biofuel'},
    {"cat": "saf",   "query": 'SAF tariff OR "trade barrier" OR "import rules" aviation fuel'},

    # --- concorrentes e pares, apenas ligados as tres frentes ---
    {"cat": "saf",   "query": '"Raizen" OR "Cosan" OR "Sao Martinho" OR "BP Bunge" SAF OR "aviation fuel"'},
    {"cat": "blend", "query": '"POET" OR "Green Plains" OR "Valero" OR "ADM" ethanol blending OR SAF'},
    {"cat": "bio",   "query": '"Raizen" OR "Cosan" OR "Vertex" OR "Petrobras" marine fuel OR bunker biofuel'},

    # --- Brasil, em portugues (edicao BR do Google Noticias) ---
    {"cat": "blend", "query": 'CNPE OR MME OR ANP etanol mistura gasolina mandato', "lang": "pt"},
    {"cat": "blend", "query": '"mistura de etanol" OR "teor de etanol" gasolina aumento', "lang": "pt"},
    {"cat": "saf",   "query": '"combustivel do futuro" OR ProBioQAV OR SAF aviacao etanol', "lang": "pt"},
    {"cat": "saf",   "query": 'querosene sustentavel OR "combustivel sustentavel de aviacao" etanol', "lang": "pt"},
    {"cat": "bio",   "query": 'etanol combustivel maritimo OR bunker OR navio descarbonizacao', "lang": "pt"},
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
        return "sem data"
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
    """Converte a data do RSS. Sempre devolve datetime com fuso, para ordenar sem erro."""
    try:
        from email.utils import parsedate_to_datetime
        d = parsedate_to_datetime(date_str)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def build_rss_url(query, lang="en"):
    q = f"{query} {JANELA}".strip() if JANELA else query
    if lang == "pt":
        return f"https://news.google.com/rss/search?q={quote(q)}&hl=pt-BR&gl=BR&ceid=BR:pt"
    return f"https://news.google.com/rss/search?q={quote(q)}&hl=en-US&gl=US&ceid=US:en"


def fetch_rss(query, lang="en"):
    """Busca no Google News. Repete com espera crescente se levar bloqueio (503)."""
    url = build_rss_url(query, lang)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8" if lang == "pt" else "en-US,en;q=0.9",
    }

    for tentativa in range(1, RSS_TENTATIVAS + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as resp:
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
            bloqueio = "503" in str(e) or "429" in str(e)
            if tentativa < RSS_TENTATIVAS:
                espera = tentativa * (6 if bloqueio else 2)
                print(f"    tentativa {tentativa} falhou ({e}) - repetindo em {espera}s")
                time.sleep(espera)
            else:
                print(f"    AVISO RSS apos {RSS_TENTATIVAS} tentativas: {e}")
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
#  DETECCAO DE MUDANCA DE MANDATO (BLENDING)
# ==========================================================

def ler_mapa_blend():
    """Le os paises e o percentual atual direto do mapa de blending."""
    try:
        html_src = open(MAPA_BLEND, encoding="utf-8").read()
    except Exception as e:
        print(f"  AVISO: nao consegui ler {MAPA_BLEND}: {e}")
        return {}
    paises = {}
    for m in re.finditer(r'id:"([A-Z]{3})"\s*,\s*name:"([^"]+)"', html_src):
        cid, nome = m.group(1), m.group(2)
        trecho = html_src[m.start():m.start() + 1200]
        mb = re.search(r'blend:"([^"]*)"', trecho)
        if mb:
            paises[cid] = {"nome": nome, "blend": mb.group(1)}
    return paises


def carregar_pendencias():
    try:
        with open(PEND_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return {"pendentes": d.get("pendentes", []), "recusados": d.get("recusados", [])}
    except Exception:
        return {"pendentes": [], "recusados": []}


def salvar_pendencias(dados):
    try:
        with open(PEND_FILE, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"  AVISO ao salvar pendencias: {e}")


def detectar_mudancas_blend(items):
    """Pergunta ao Gemini se alguma noticia indica novo percentual de mistura."""
    dados = carregar_pendencias()
    if not _GEMINI_OK or not GEMINI_API_KEY:
        return dados

    mapa = ler_mapa_blend()
    if not mapa:
        return dados

    noticias = [i for i in items if i["category"] == "blend"][:40]
    if not noticias:
        return dados

    atual = "\n".join(f"{cid} = {v['nome']}: {v['blend']}" for cid, v in sorted(mapa.items()))
    lista = "\n\n".join(
        f"ID {n+1}:\nTitulo: {it['title']}\nResumo: {it.get('desc', '')[:200]}"
        for n, it in enumerate(noticias)
    )

    prompt = (
        "Voce monitora mandatos de mistura de etanol na gasolina (blending).\n\n"
        "VALORES ATUALMENTE REGISTRADOS NO MAPA:\n" + atual + "\n\n"
        "NOTICIAS DE HOJE:\n" + lista + "\n\n"
        "Identifique APENAS os casos em que uma noticia afirma explicitamente um NOVO "
        "percentual obrigatorio de mistura que DIFERE do valor registrado acima.\n"
        "Regras rigorosas:\n"
        "- So proponha se a noticia declarar o percentual de forma clara (ex: E15, E20).\n"
        "- Ignore metas futuras, estudos, propostas e discussoes sem aprovacao.\n"
        "- Ignore se o valor da noticia for igual ao que ja esta no mapa.\n"
        "- Na duvida, NAO proponha.\n\n"
        "Responda SOMENTE com JSON:\n"
        '{\"mudancas\": [{\"id\": \"VNM\", \"novo\": \"E15\", \"noticia\": 3, '
        '\"justificativa\": \"frase curta\"}]}\n'
        'Se nao houver nenhuma, responda {\"mudancas\": []}'
    )

    print("  Verificando se ha mudanca de mandato de blending...")
    r = _parse_json(gemini_call(prompt, max_tokens=1200, temperature=0.0))
    if not r or not isinstance(r.get("mudancas"), list):
        print("    Nenhuma mudanca detectada.")
        return dados

    ja_pend = {p["id"] + "|" + p["novo"] for p in dados["pendentes"]}
    achados = 0
    for mud in r["mudancas"]:
        cid  = str(mud.get("id", "")).upper().strip()
        novo = str(mud.get("novo", "")).strip()
        idx  = mud.get("noticia")
        if cid not in mapa or not novo:
            continue
        if novo.lower() == mapa[cid]["blend"].lower():
            continue
        chave = cid + "|" + novo
        if chave in ja_pend or chave in dados["recusados"]:
            continue
        if not isinstance(idx, int) or not (1 <= idx <= len(noticias)):
            continue
        fonte = noticias[idx - 1]
        dados["pendentes"].append({
            "id": cid,
            "pais": mapa[cid]["nome"],
            "atual": mapa[cid]["blend"],
            "novo": novo,
            "justificativa": str(mud.get("justificativa", ""))[:300],
            "noticia_titulo": fonte["title"],
            "noticia_url": fonte["url"],
            "noticia_fonte": fonte["source"],
            "detectado_em": datetime.now(timezone.utc).strftime("%d/%m/%Y"),
        })
        achados += 1
        print(f"    PROPOSTA: {mapa[cid]['nome']} {mapa[cid]['blend']} -> {novo}")

    if not achados:
        print("    Nenhuma mudanca nova.")
    salvar_pendencias(dados)
    return dados


def link_issue(p, acao):
    """Monta o link do GitHub que registra a decisao."""
    from urllib.parse import quote as q
    tag = "[MAPA]" if acao == "aprovar" else "[MAPA-RECUSAR]"
    titulo = f"{tag} {p['id']} blend -> {p['novo']}"
    corpo = (
        f"Pais: {p['pais']} ({p['id']})\n"
        f"De: {p['atual']}\n"
        f"Para: {p['novo']}\n\n"
        f"Noticia: {p['noticia_titulo']}\n"
        f"{p['noticia_url']}\n\n"
        "```json\n"
        + json.dumps({"id": p["id"], "novo": p["novo"]}, ensure_ascii=False)
        + "\n```\n\n"
        "Envie esta issue para confirmar. O robo aplica a alteracao e fecha sozinho."
    )
    return f"https://github.com/{REPO}/issues/new?title={q(titulo)}&body={q(corpo)}"


# ==========================================================
#  COLETA
# ==========================================================

def fetch_news():
    seen_urls, seen_titles = set(), set()
    all_items = []
    falhas = 0

    for search in RSS_SEARCHES:
        cat, query = search["cat"], search["query"]
        print(f"  [{cat.upper()}] {query[:58]}...")
        rss_items = fetch_rss(query, search.get("lang", "en"))
        print(f"    Retornou {len(rss_items)} itens")
        if not rss_items:
            falhas += 1
        time.sleep(RSS_PAUSA)

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

    if falhas:
        print(f"  ATENCAO: {falhas} de {len(RSS_SEARCHES)} buscas nao retornaram nada.")

    n_saf   = sum(1 for i in all_items if i["category"] == "saf")
    n_bio   = sum(1 for i in all_items if i["category"] == "bio")
    n_blend = sum(1 for i in all_items if i["category"] == "blend")
    print(f"  Total coletado: SAF={n_saf} | Bio={n_bio} | Blend={n_blend}")

    # ordena por data ANTES de filtrar, para que o primeiro bloco enviado ao Gemini
    # seja sempre o das noticias mais recentes
    all_items.sort(key=lambda x: parse_date_obj(x.get("date_raw", "")), reverse=True)

    filtered = gemini_filter(all_items)
    filtered.sort(key=lambda x: parse_date_obj(x.get("date_raw", "")), reverse=True)

    # quantas noticias de cada idade sobraram
    agora = datetime.now(timezone.utc)
    faixas = {"hoje": 0, "ontem": 0, "2-7 dias": 0, "mais antigas": 0}
    for it in filtered:
        d = parse_date_obj(it.get("date_raw", ""))
        if d.year < 2000:
            faixas["mais antigas"] += 1
            continue
        h = (agora - d).total_seconds() / 3600
        if h < 24:    faixas["hoje"] += 1
        elif h < 48:  faixas["ontem"] += 1
        elif h < 168: faixas["2-7 dias"] += 1
        else:         faixas["mais antigas"] += 1
    print("  Idade das noticias: " + " | ".join(f"{k}={v}" for k, v in faixas.items()))

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

# ==========================================================
#  DESTAQUES DA SEMANA
# ==========================================================

def semana_atual():
    iso = datetime.now(timezone.utc).isocalendar()
    return f"{iso[0]}-S{iso[1]:02d}"


def salvar_destaques(d):
    try:
        with open(DESTAQUES_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"  AVISO ao salvar destaques: {e}")


def carregar_destaques():
    vazio = {"semana": semana_atual(),
             "temas": {"saf": {}, "bio": {}, "blend": {}}}
    try:
        with open(DESTAQUES_FILE, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("temas"), dict):
            for t in ("saf", "bio", "blend"):
                d["temas"].setdefault(t, {})
            return d
    except Exception:
        pass
    return vazio


def atualizar_destaques(items):
    """Mantem uma sintese por frente (SAF, Biobunker, Blending), atualizada a cada dia."""
    d = carregar_destaques()

    if d.get("semana") != semana_atual():
        print(f"  Destaques: semana nova ({semana_atual()}), sinteses zeradas.")
        d = {"semana": semana_atual(), "temas": {"saf": {}, "bio": {}, "blend": {}}}

    if not _GEMINI_OK or not GEMINI_API_KEY or not items:
        salvar_destaques(d)
        return d

    hoje = datetime.now(timezone.utc).strftime("%d/%m")
    limite = datetime.now(timezone.utc) - timedelta(days=7)
    nomes = {"saf": "SAF (combustivel sustentavel de aviacao)",
             "bio": "Biobunker (combustivel maritimo sustentavel)",
             "blend": "Blending (mistura etanol+gasolina)"}

    partes = []
    for tema in ("saf", "bio", "blend"):
        noticias = [i for i in items
                    if i["category"] == tema
                    and parse_date_obj(i.get("date_raw", "")) >= limite][:25]
        if not noticias:
            continue
        atual = d["temas"].get(tema, {}).get("texto", "")
        linhas = "\n".join(
            f"- {n['title']}" + (f" | {n.get('summary','')[:150]}" if n.get("summary") else "")
            for n in noticias
        )
        partes.append(
            f"### {tema} = {nomes[tema]}\n"
            f"SINTESE ATUAL: {atual if atual else '(ainda nao existe)'}\n"
            f"NOTICIAS DA SEMANA:\n{linhas}"
        )

    if not partes:
        salvar_destaques(d)
        return d

    prompt = (
        "Voce e analista de novos negocios de etanol na Raizen.\n"
        "Para CADA uma das tres frentes abaixo, escreva a sintese do que foi mais "
        "importante NA SEMANA - de 2 a 4 frases, em portugues, texto corrido.\n\n"
        "Como escrever:\n"
        "- Fale dos fatos, nao das materias. Nao escreva 'segundo noticia' nem cite veiculo.\n"
        "- Seja concreto: cite pais, empresa, percentual, valor quando houver.\n"
        "- Priorize mudanca de regra ou mandato, contrato e investimento, movimento de "
        "concorrente, abertura ou fechamento de mercado.\n"
        "- Termine com o que isso significa para quem vende etanol, quando fizer sentido.\n"
        "- Se ja existe uma SINTESE ATUAL, atualize-a incorporando o que e novo e "
        "removendo o que perdeu importancia. Nao recomece do zero sem motivo.\n"
        "- Se a frente teve pouca coisa relevante, diga isso em uma frase, sem inventar.\n\n"
        'Responda SOMENTE com JSON: {"saf": "...", "bio": "...", "blend": "..."}\n\n'
        + "\n\n".join(partes)
    )

    r = _parse_json(gemini_call(prompt, max_tokens=1600, temperature=0.3))
    if not isinstance(r, dict):
        print("  Destaques: Gemini nao respondeu, sinteses anteriores mantidas.")
        salvar_destaques(d)
        return d

    mudou = []
    for tema in ("saf", "bio", "blend"):
        texto = str(r.get(tema, "")).strip()
        if len(texto) < 25:
            continue
        anterior = d["temas"].get(tema, {}).get("texto", "")
        d["temas"][tema] = {"texto": texto, "atualizado": hoje,
                            "desde": d["temas"].get(tema, {}).get("desde", hoje)}
        if texto != anterior:
            mudou.append(tema.upper())

    print(f"  Destaques da semana: {len(mudou)} frente(s) atualizada(s)"
          + (f" ({', '.join(mudou)})" if mudou else ""))
    salvar_destaques(d)
    return d


def render_destaques(d):
    """Tres quadros com a sintese da semana, um por frente."""
    temas = d.get("temas", {})
    if not any(temas.get(t, {}).get("texto") for t in ("saf", "bio", "blend")):
        return ""

    rotulos = {"saf": ("SAF", "Combustivel de aviacao"),
               "bio": ("Biobunker", "Combustivel maritimo"),
               "blend": ("Blending", "Mistura etanol+gasolina")}

    quadros = ""
    for tema in ("saf", "bio", "blend"):
        info = temas.get(tema, {})
        texto = info.get("texto", "")
        titulo, sub = rotulos[tema]
        corpo = (html.escape(texto) if texto
                 else '<span class="dq-sem">Sem movimentacao relevante ate agora nesta semana.</span>')
        atualizado = (f'<span class="dq-quando">atualizado em {html.escape(info["atualizado"])}</span>'
                      if info.get("atualizado") else "")
        quadros += f"""
    <div class="dq-card {tema}">
      <div class="dq-card-topo">
        <span class="dq-card-tit">{titulo}</span>
        {atualizado}
      </div>
      <div class="dq-card-sub">{sub}</div>
      <div class="dq-card-txt">{corpo}</div>
    </div>"""

    return f"""
<div class="destaques">
  <div class="dq-cab">
    <span class="dq-tit">Destaques da semana</span>
    <span class="dq-sub">{html.escape(d.get('semana',''))} &middot; a sintese vai sendo atualizada a cada dia</span>
  </div>
  <div class="dq-grade">{quadros}
  </div>
</div>
"""


def render_html(items, destaques=None):
    now    = datetime.now(timezone.utc).strftime("%d/%m/%Y as %H:%M UTC")
    bloco_destaques = render_destaques(destaques or {})
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
    --bg:#05201E;--bg2:#0A302D;--bg3:#0F3D38;
    --border:#14463F;--border2:#1E5C53;
    --text:#FFFFFF;--text2:#9FB8B2;--text3:#5F7A75;
    --saf:#5E9BE0;--saf-bg:rgba(94,155,224,0.10);
    --bio:#8FCC58;--bio-bg:rgba(46,143,143,0.10);
    --blend:#EA792B;--blend-bg:rgba(234,121,43,0.10);
    --accent:#75B73B;--accent-l:#8FCC58;--r:14px;
  }}
  body{{background:var(--bg);color:var(--text);font-family:'Inter',system-ui,sans-serif;min-height:100vh;padding-bottom:40px}}
  .navbar{{background:var(--bg2);border-bottom:1px solid var(--border);padding:0 20px;height:52px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:200}}
  .nav-logo{{display:flex;align-items:center;gap:8px;text-decoration:none}}
  .nav-logo-mark{{width:28px;height:28px;background:linear-gradient(135deg,#75B73B,#025050);border-radius:7px;display:flex;align-items:center;justify-content:center;font-size:14px}}
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
  .destaques{{margin:0 20px 22px}}
  .dq-cab{{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:12px}}
  .dq-tit{{font-size:15px;font-weight:700;color:#FFFFFF;letter-spacing:-.2px}}
  .dq-sub{{font-size:11px;color:#5F7A75}}
  .dq-grade{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}
  .dq-card{{background:linear-gradient(165deg,#0A302D,#06413D);border:1px solid #1E5C53;border-radius:14px;padding:16px;border-top:3px solid}}
  .dq-card.saf{{border-top-color:#5E9BE0}}
  .dq-card.bio{{border-top-color:#2E8F8F}}
  .dq-card.blend{{border-top-color:#EA792B}}
  .dq-card-topo{{display:flex;align-items:baseline;justify-content:space-between;gap:8px}}
  .dq-card-tit{{font-size:14px;font-weight:700;letter-spacing:.2px}}
  .dq-card.saf .dq-card-tit{{color:#5E9BE0}}
  .dq-card.bio .dq-card-tit{{color:#2E8F8F}}
  .dq-card.blend .dq-card-tit{{color:#EA792B}}
  .dq-quando{{font-size:10px;color:#5F7A75;white-space:nowrap}}
  .dq-card-sub{{font-size:10px;color:#5F7A75;text-transform:uppercase;letter-spacing:.6px;margin:3px 0 10px}}
  .dq-card-txt{{font-size:12.5px;color:#FFFFFF;line-height:1.65;opacity:.92}}
  .dq-sem{{color:#5F7A75;font-style:italic}}
  @media(max-width:860px){{.dq-grade{{grid-template-columns:1fr}}}}
  .stats{{padding:0 20px;display:flex;flex-direction:column;gap:10px;margin-bottom:20px}}
  .stat-card{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--r);padding:16px 18px;cursor:pointer;transition:all .15s}}
  .stat-card:hover{{border-color:var(--border2)}}
  .stat-card.active-saf  {{border-color:var(--saf);background:var(--saf-bg)}}
  .stat-card.active-bio  {{border-color:var(--bio);background:var(--bio-bg)}}
  .stat-card.active-blend{{border-color:var(--blend);background:var(--blend-bg)}}
  .stat-card.active-all  {{border-color:var(--accent);background:rgba(117,183,59,0.08)}}
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
  .btn-resumo.open{{border-color:var(--accent);color:var(--accent-l);background:rgba(117,183,59,0.08)}}
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
    <a class="nav-tab" href="arquivo.html">🗂️ Arquivo</a>
    <a class="nav-tab" href="mapa-mandatos.html">🗺️ Blending</a>
    <a class="nav-tab" href="mapa-saf.html">✈️ SAF</a>
    <a class="nav-tab" href="pendencias.html">✅ Aprovacoes</a>
  </div>
</nav>
<div class="header">
  <div class="header-title">Ferramenta de monitoramento de noticias para novos mercados</div>
  <div class="updated">Atualizado em {now}</div>
</div>
{bloco_destaques}
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

def render_pendencias(dados):
    """Gera a pagina de solicitacoes de alteracao do mapa."""
    pend = dados.get("pendentes", [])
    now  = datetime.now(timezone.utc).strftime("%d/%m/%Y as %H:%M UTC")

    cards = ""
    for p in pend:
        cards += f"""
    <div class="req">
      <div class="req-head">
        <span class="req-pais">{html.escape(p['pais'])}</span>
        <span class="req-data">detectado em {html.escape(p['detectado_em'])}</span>
      </div>
      <div class="req-mud">
        <span class="pill old">{html.escape(p['atual'])}</span>
        <span class="seta">passa a</span>
        <span class="pill new">{html.escape(p['novo'])}</span>
      </div>
      <div class="req-just">{html.escape(p['justificativa'])}</div>
      <div class="prova">
        <div class="prova-tag">Noticia usada como prova</div>
        <a class="prova-titulo" href="{html.escape(p['noticia_url'])}" target="_blank" rel="noopener">{html.escape(p['noticia_titulo'])}</a>
        <div class="prova-fonte">{html.escape(p['noticia_fonte'])}</div>
      </div>
      <div class="req-acoes">
        <a class="btn ok"  href="{link_issue(p, 'aprovar')}" target="_blank" rel="noopener">Aprovar e atualizar o mapa</a>
        <a class="btn no"  href="{link_issue(p, 'recusar')}" target="_blank" rel="noopener">Recusar</a>
      </div>
    </div>"""

    if not pend:
        cards = """
    <div class="vazio">
      <div class="vazio-t">Nenhuma solicitacao pendente</div>
      <div class="vazio-d">Quando uma noticia indicar mudanca de mandato de mistura,
      a solicitacao aparece aqui para a sua aprovacao.</div>
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Aprovacoes - BioFuel Monitor</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
  *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
  :root{{
    --bg:#05201E;--bg2:#0A302D;--bg3:#0F3D38;--border:#14463F;--border2:#1E5C53;
    --text:#FFFFFF;--text2:#9FB8B2;--text3:#5F7A75;--accent:#75B73B;--accent-l:#8FCC58;
    --warn:#EA792B;
  }}
  body{{background:var(--bg);color:var(--text);font-family:'Inter',system-ui,sans-serif;min-height:100vh;padding-bottom:50px}}
  .navbar{{background:var(--bg2);border-bottom:1px solid var(--border);padding:0 20px;height:52px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:200}}
  .nav-logo{{display:flex;align-items:center;gap:8px;text-decoration:none}}
  .nav-logo-mark{{width:28px;height:28px;background:linear-gradient(135deg,#75B73B,#025050);border-radius:7px;display:flex;align-items:center;justify-content:center;font-size:14px}}
  .nav-logo-name{{font-size:13px;font-weight:700;color:var(--text)}}
  .nav-tabs{{display:flex;gap:4px}}
  .nav-tab{{padding:6px 14px;border-radius:8px;font-size:13px;font-weight:500;color:var(--text2);text-decoration:none;border:1px solid transparent}}
  .nav-tab:hover{{color:var(--text);background:var(--bg3)}}
  .nav-tab.active{{background:var(--bg3);border-color:var(--border2);color:var(--text)}}
  .header{{padding:22px 20px 6px}}
  .header h1{{font-size:21px;font-weight:700;letter-spacing:-.3px}}
  .header p{{font-size:12px;color:var(--text3);margin-top:6px}}
  .aviso{{margin:16px 20px;padding:12px 14px;background:rgba(234,121,43,.07);border:1px solid rgba(234,121,43,.25);border-radius:10px;font-size:12px;color:#F5A46A;line-height:1.6}}
  .wrap{{padding:6px 20px;display:flex;flex-direction:column;gap:14px}}
  .req{{background:var(--bg2);border:1px solid var(--border);border-radius:14px;padding:18px}}
  .req-head{{display:flex;align-items:baseline;justify-content:space-between;gap:10px;margin-bottom:14px}}
  .req-pais{{font-size:17px;font-weight:700}}
  .req-data{{font-size:11px;color:var(--text3)}}
  .req-mud{{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px}}
  .pill{{padding:5px 14px;border-radius:8px;font-size:15px;font-weight:700;border:1px solid}}
  .pill.old{{background:var(--bg3);border-color:var(--border2);color:var(--text2)}}
  .pill.new{{background:rgba(117,183,59,.1);border-color:var(--accent);color:var(--accent-l)}}
  .seta{{font-size:12px;color:var(--text3)}}
  .req-just{{font-size:13px;color:var(--text2);line-height:1.6;margin-bottom:14px}}
  .prova{{background:var(--bg3);border-left:3px solid var(--accent);border-radius:8px;padding:12px 14px;margin-bottom:16px}}
  .prova-tag{{font-size:10px;letter-spacing:.7px;text-transform:uppercase;color:var(--text3);margin-bottom:7px}}
  .prova-titulo{{font-size:13px;font-weight:500;color:var(--text);text-decoration:none;line-height:1.45;display:block}}
  .prova-titulo:hover{{color:var(--accent-l);text-decoration:underline}}
  .prova-fonte{{font-size:11px;color:var(--text3);margin-top:6px}}
  .req-acoes{{display:flex;gap:10px;flex-wrap:wrap}}
  .btn{{padding:9px 18px;border-radius:9px;font-size:13px;font-weight:600;text-decoration:none;border:1px solid;transition:all .15s}}
  .btn.ok{{background:var(--accent);border-color:var(--accent);color:#06231F}}
  .btn.ok:hover{{background:var(--accent-l)}}
  .btn.no{{background:transparent;border-color:var(--border2);color:var(--text2)}}
  .btn.no:hover{{border-color:#DC2720;color:#E8635C}}
  .vazio{{padding:60px 20px;text-align:center}}
  .vazio-t{{font-size:16px;font-weight:600;margin-bottom:10px}}
  .vazio-d{{font-size:13px;color:var(--text2);line-height:1.65;max-width:420px;margin:0 auto}}
  @media(max-width:640px){{.navbar{{padding:0 14px}}.header,.wrap,.aviso{{padding-left:14px;padding-right:14px}}}}
</style>
</head>
<body>
<nav class="navbar">
  <a class="nav-logo" href="index.html">
    <div class="nav-logo-mark">B</div>
    <span class="nav-logo-name">BioFuel Monitor</span>
  </a>
  <div class="nav-tabs">
    <a class="nav-tab" href="index.html">📰 Noticias</a>
    <a class="nav-tab" href="arquivo.html">🗂️ Arquivo</a>
    <a class="nav-tab" href="mapa-mandatos.html">🗺️ Blending</a>
    <a class="nav-tab" href="mapa-saf.html">✈️ SAF</a>
    <a class="nav-tab active" href="pendencias.html">✅ Aprovacoes</a>
  </div>
</nav>
<div class="header">
  <h1>Solicitacoes de alteracao do mapa</h1>
  <p>Atualizado em {now} &middot; {len(pend)} pendente(s)</p>
</div>
<div class="aviso">
  Nada e alterado sem a sua aprovacao. Ao clicar em Aprovar, o GitHub abre com a
  solicitacao ja preenchida: basta enviar para confirmar. Esse passo existe porque o
  site e publico, e so quem tem acesso ao repositorio pode alterar os mapas.
  Apenas o campo de percentual de mistura e modificado.
</div>
<div class="wrap">{cards}
</div>
</body>
</html>"""


# ==========================================================
#  ARQUIVO (memoria de 3 meses)
# ==========================================================

def carregar_arquivo():
    try:
        with open(ARQUIVO_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def atualizar_arquivo(items):
    """Guarda as noticias do dia e descarta o que passou de ARQUIVO_DIAS."""
    arq = carregar_arquivo()
    antes = len(arq)
    hoje = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for it in items:
        chave = normalize_title(it["title"])
        reg = arq.get(chave, {})
        arq[chave] = {
            "titulo":   it["title"],
            "url":      it["url"],
            "fonte":    it.get("source", ""),
            "cat":      it["category"],
            "data":     it.get("date_raw", ""),
            "flag":     it.get("flag", ""),
            "pais":     it.get("country", ""),
            "resumo":   it.get("summary") or reg.get("resumo", ""),
            "visto_em": reg.get("visto_em", hoje),
        }

    # poda
    limite = datetime.now(timezone.utc) - timedelta(days=ARQUIVO_DIAS)
    limpo = {}
    for k, v in arq.items():
        d = parse_date_obj(v.get("data", ""))
        if d.year < 2000:
            try:
                d = datetime.strptime(v.get("visto_em", hoje), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except Exception:
                d = datetime.now(timezone.utc)
        if d >= limite:
            limpo[k] = v

    try:
        with open(ARQUIVO_FILE, "w", encoding="utf-8") as f:
            json.dump(limpo, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"  AVISO ao salvar arquivo: {e}")

    novas = len(limpo) - antes
    print(f"  Arquivo: {len(limpo)} noticias guardadas "
          f"({max(novas, 0)} novas, {max(antes + len(items) - len(limpo), 0)} podadas por idade)")
    return limpo


MESES_PT = {1: "janeiro", 2: "fevereiro", 3: "marco", 4: "abril", 5: "maio", 6: "junho",
            7: "julho", 8: "agosto", 9: "setembro", 10: "outubro", 11: "novembro", 12: "dezembro"}


def render_arquivo(arq):
    """Pagina com o historico de 3 meses."""
    now = datetime.now(timezone.utc).strftime("%d/%m/%Y as %H:%M UTC")
    labels = {"saf": "SAF", "bio": "Biobunker", "blend": "Blending"}

    regs = list(arq.values())
    regs.sort(key=lambda r: parse_date_obj(r.get("data", "")), reverse=True)
    total = len(regs)
    regs = regs[:ARQUIVO_MOSTRAR]

    meses = []
    for r in regs:
        d = parse_date_obj(r.get("data", ""))
        if d.year > 2000:
            chave = f"{d.year}-{d.month:02d}"
            rotulo = f"{MESES_PT[d.month]}/{str(d.year)[2:]}"
            if (chave, rotulo) not in meses:
                meses.append((chave, rotulo))
    meses.sort(reverse=True)

    botoes_mes = "".join(
        f'<button class="chip" data-mes="{c}" onclick="setMes(\'{c}\')" id="m-{c}">{r}</button>'
        for c, r in meses
    )

    cards = ""
    for i, r in enumerate(regs):
        d = parse_date_obj(r.get("data", ""))
        mes = f"{d.year}-{d.month:02d}" if d.year > 2000 else "sd"
        data_txt = d.strftime("%d/%m/%Y") if d.year > 2000 else "sem data"
        cat = r.get("cat", "saf")
        resumo = html.escape(r.get("resumo") or "Resumo nao disponivel para esta noticia.")
        cards += f"""
    <div class="news-card" data-cat="{cat}" data-mes="{mes}" data-title="{html.escape(r['titulo'].lower())}">
      <div class="news-top">
        <span class="news-badge {cat}">{labels.get(cat, cat)}</span>
        <span class="news-time">{data_txt}</span>
      </div>
      <div class="news-title"><a href="{html.escape(r['url'])}" target="_blank" rel="noopener">{html.escape(r['titulo'])}</a></div>
      <div class="news-summary" id="summary-{i}" style="display:none">{resumo}</div>
      <div class="news-footer">
        <span class="news-source">{r.get('flag','')} {html.escape(r.get('pais',''))} &middot; {html.escape(r.get('fonte',''))}</span>
        <div class="news-actions">
          <button class="btn-resumo" onclick="toggleResumo({i})" id="btn-{i}">Resumo</button>
          <a class="news-read" href="{html.escape(r['url'])}" target="_blank" rel="noopener">Ler</a>
        </div>
      </div>
    </div>"""

    if not regs:
        cards = """<div class="vazio">
      <div class="vazio-t">O arquivo ainda esta vazio</div>
      <div class="vazio-d">Ele vai sendo montado a cada atualizacao do monitor.
      As noticias saem do painel principal depois de 7 dias e ficam guardadas aqui por 3 meses.</div>
    </div>"""

    nota = (f"Mostrando as {len(regs)} mais recentes de {total} guardadas."
            if total > len(regs) else f"{total} noticia(s) guardada(s).")

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Arquivo - BioFuel Monitor</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
  *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
  :root{{
    --bg:#05201E;--bg2:#0A302D;--bg3:#0F3D38;--border:#14463F;--border2:#1E5C53;
    --text:#FFFFFF;--text2:#9FB8B2;--text3:#5F7A75;
    --saf:#5E9BE0;--saf-bg:rgba(94,155,224,.10);
    --bio:#8FCC58;--bio-bg:rgba(46,143,143,.10);
    --blend:#EA792B;--blend-bg:rgba(234,121,43,.10);
    --accent:#75B73B;--accent-l:#8FCC58;--r:14px;
  }}
  body{{background:var(--bg);color:var(--text);font-family:'Inter',system-ui,sans-serif;min-height:100vh;padding-bottom:40px}}
  .navbar{{background:var(--bg2);border-bottom:1px solid var(--border);padding:0 20px;height:52px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:200}}
  .nav-logo{{display:flex;align-items:center;gap:8px;text-decoration:none}}
  .nav-logo-mark{{width:28px;height:28px;background:linear-gradient(135deg,#75B73B,#025050);border-radius:7px;display:flex;align-items:center;justify-content:center;font-size:14px}}
  .nav-logo-name{{font-size:13px;font-weight:700;color:var(--text)}}
  .nav-tabs{{display:flex;gap:4px;flex-wrap:wrap}}
  .nav-tab{{padding:6px 14px;border-radius:8px;font-size:13px;font-weight:500;color:var(--text2);text-decoration:none;border:1px solid transparent}}
  .nav-tab:hover{{color:var(--text);background:var(--bg3)}}
  .nav-tab.active{{background:var(--bg3);border-color:var(--border2);color:var(--text)}}
  .header{{padding:20px 20px 10px}}
  .header h1{{font-size:21px;font-weight:700;letter-spacing:-.3px}}
  .header p{{font-size:12px;color:var(--text3);margin-top:6px}}
  .search-wrap{{padding:8px 20px 14px}}
  .search-box{{display:flex;align-items:center;gap:10px;background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:10px 14px}}
  .search-box input{{flex:1;background:transparent;border:none;outline:none;color:var(--text);font-size:14px}}
  .search-box input::placeholder{{color:var(--text3)}}
  .filtros{{padding:0 20px 8px;display:flex;gap:8px;overflow-x:auto;scrollbar-width:none}}
  .filtros::-webkit-scrollbar{{display:none}}
  .rot{{font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.8px;padding:0 20px 6px}}
  .chip{{padding:7px 16px;border-radius:20px;font-size:13px;font-weight:500;border:1px solid var(--border);background:transparent;color:var(--text2);cursor:pointer;white-space:nowrap;flex-shrink:0}}
  .chip.a{{background:var(--accent);border-color:var(--accent);color:#06231F}}
  .news-wrap{{padding:10px 20px 0;display:flex;flex-direction:column;gap:10px}}
  .news-card{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--r);padding:16px}}
  .news-card:hover{{border-color:var(--border2)}}
  .news-top{{display:flex;align-items:center;gap:8px;margin-bottom:10px}}
  .news-badge{{font-size:10px;font-weight:700;letter-spacing:.6px;text-transform:uppercase;padding:3px 9px;border-radius:20px;border:1px solid}}
  .news-badge.saf{{color:var(--saf);border-color:var(--saf);background:var(--saf-bg)}}
  .news-badge.bio{{color:var(--bio);border-color:var(--bio);background:var(--bio-bg)}}
  .news-badge.blend{{color:var(--blend);border-color:var(--blend);background:var(--blend-bg)}}
  .news-time{{font-size:11px;color:var(--text3);margin-left:auto}}
  .news-title{{font-size:14px;font-weight:500;line-height:1.45;margin-bottom:10px}}
  .news-title a{{color:var(--text);text-decoration:none}}
  .news-title a:hover{{color:var(--accent-l);text-decoration:underline}}
  .news-summary{{font-size:13px;color:var(--text2);line-height:1.65;margin-bottom:10px;padding:12px;background:var(--bg3);border-radius:8px;border-left:3px solid var(--accent)}}
  .news-footer{{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}}
  .news-source{{font-size:11px;color:var(--text3)}}
  .news-actions{{display:flex;align-items:center;gap:8px}}
  .btn-resumo{{font-size:11px;font-weight:500;padding:4px 10px;border-radius:8px;border:1px solid var(--border2);background:var(--bg3);color:var(--text2);cursor:pointer}}
  .btn-resumo:hover{{border-color:var(--accent);color:var(--accent-l)}}
  .btn-resumo.open{{border-color:var(--accent);color:var(--accent-l);background:rgba(117,183,59,.08)}}
  .news-read{{font-size:11px;color:var(--accent-l);text-decoration:none}}
  .vazio{{padding:60px 20px;text-align:center}}
  .vazio-t{{font-size:16px;font-weight:600;margin-bottom:10px}}
  .vazio-d{{font-size:13px;color:var(--text2);line-height:1.65;max-width:430px;margin:0 auto}}
  .nada{{display:none;padding:40px 20px;text-align:center;color:var(--text2);font-size:13px}}
  @media(max-width:640px){{.navbar{{padding:0 14px;height:auto;flex-direction:column;gap:8px;padding-top:10px;padding-bottom:10px}}
  .header,.search-wrap,.filtros,.news-wrap,.rot{{padding-left:14px;padding-right:14px}}}}
</style>
</head>
<body>
<nav class="navbar">
  <a class="nav-logo" href="index.html">
    <div class="nav-logo-mark">B</div><span class="nav-logo-name">BioFuel Monitor</span>
  </a>
  <div class="nav-tabs">
    <a class="nav-tab" href="index.html">📰 Noticias</a>
    <a class="nav-tab active" href="arquivo.html">🗂️ Arquivo</a>
    <a class="nav-tab" href="mapa-mandatos.html">🗺️ Blending</a>
    <a class="nav-tab" href="mapa-saf.html">✈️ SAF</a>
    <a class="nav-tab" href="pendencias.html">✅ Aprovacoes</a>
  </div>
</nav>
<div class="header">
  <h1>Arquivo de noticias</h1>
  <p>Ultimos {ARQUIVO_DIAS} dias &middot; {nota} Atualizado em {now}</p>
</div>
<div class="search-wrap">
  <div class="search-box"><span>Buscar</span>
    <input type="text" id="q" placeholder="titulo, pais, tema..." oninput="render()"/>
  </div>
</div>
<div class="rot">Tema</div>
<div class="filtros">
  <button class="chip a" id="c-all"   onclick="setCat('all')">Todos</button>
  <button class="chip"   id="c-saf"   onclick="setCat('saf')">SAF</button>
  <button class="chip"   id="c-bio"   onclick="setCat('bio')">Biobunker</button>
  <button class="chip"   id="c-blend" onclick="setCat('blend')">Blending</button>
</div>
<div class="rot">Periodo</div>
<div class="filtros">
  <button class="chip a" id="m-all" onclick="setMes('all')">Tudo</button>
  {botoes_mes}
</div>
<div class="news-wrap" id="lista">{cards}
</div>
<div class="nada" id="nada">Nenhuma noticia com esses filtros.</div>
<script>
let cat = 'all', mes = 'all';
function toggleResumo(i) {{
  const b = document.getElementById('summary-' + i), t = document.getElementById('btn-' + i);
  if (b.style.display !== 'none') {{ b.style.display = 'none'; t.textContent = 'Resumo'; t.classList.remove('open'); }}
  else {{ b.style.display = 'block'; t.textContent = 'Fechar'; t.classList.add('open'); }}
}}
function setCat(c) {{
  cat = c;
  ['all','saf','bio','blend'].forEach(k => document.getElementById('c-'+k).className = 'chip' + (k===c?' a':''));
  render();
}}
function setMes(m) {{
  mes = m;
  document.querySelectorAll('[id^="m-"]').forEach(b => b.className = 'chip');
  const el = document.getElementById('m-' + m);
  if (el) el.className = 'chip a';
  render();
}}
function render() {{
  const q = document.getElementById('q').value.toLowerCase();
  let n = 0;
  document.querySelectorAll('.news-card').forEach(c => {{
    const okc = cat === 'all' || c.dataset.cat === cat;
    const okm = mes === 'all' || c.dataset.mes === mes;
    const okq = !q || c.dataset.title.includes(q) || c.textContent.toLowerCase().includes(q);
    const ok = okc && okm && okq;
    c.style.display = ok ? 'block' : 'none';
    if (ok) n++;
  }});
  document.getElementById('nada').style.display = n ? 'none' : 'block';
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

    if not items and os.path.exists("index.html"):
        print("ATENCAO: nenhuma noticia coletada (provavel bloqueio do Google).")
        print("O painel anterior foi mantido. Nada foi sobrescrito.")
        return

    dq = atualizar_destaques(items)
    output = render_html(items, dq)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(output)
    print("index.html gerado com sucesso!")

    arq = atualizar_arquivo(items)
    with open(ARQUIVO_HTML, "w", encoding="utf-8") as f:
        f.write(render_arquivo(arq))
    print(f"{ARQUIVO_HTML} gerado.")

    dados = detectar_mudancas_blend(items)
    with open(PEND_HTML, "w", encoding="utf-8") as f:
        f.write(render_pendencias(dados))
    print(f"{PEND_HTML} gerado ({len(dados['pendentes'])} pendencia(s)).")


if __name__ == "__main__":
    main()
