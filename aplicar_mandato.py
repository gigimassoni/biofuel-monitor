#!/usr/bin/env python3
"""
Aplica (ou recusa) uma alteracao de mandato de blending aprovada via issue.
Roda apenas pelo GitHub Actions, disparado quando uma issue [MAPA] e aberta.
Altera EXCLUSIVAMENTE o campo blend do pais indicado.
"""

import json
import os
import re
import sys

MAPA = "mapa-mandatos.html"
PEND = "pendencias.json"

titulo = os.environ.get("ISSUE_TITLE", "")
corpo  = os.environ.get("ISSUE_BODY", "")

recusar = titulo.startswith("[MAPA-RECUSAR]")


def extrair_pedido():
    """Le o bloco json da issue. Se faltar, tenta pelo titulo."""
    m = re.search(r"```json\s*(\{.*?\})\s*```", corpo, re.DOTALL)
    if m:
        try:
            d = json.loads(m.group(1))
            if d.get("id") and d.get("novo"):
                return d["id"].upper().strip(), str(d["novo"]).strip()
        except Exception:
            pass
    m = re.search(r"\[MAPA(?:-RECUSAR)?\]\s+([A-Z]{3})\s+blend\s*->\s*(\S+)", titulo)
    if m:
        return m.group(1), m.group(2)
    return None, None


def aplicar_no_mapa(cid, novo):
    """Troca somente blend:"..." do pais cid. Devolve (ok, valor_antigo)."""
    src = open(MAPA, encoding="utf-8").read()

    m = re.search(r'id:"' + re.escape(cid) + r'"', src)
    if not m:
        return False, f"pais {cid} nao existe no mapa"

    trecho = src[m.start():m.start() + 1200]
    mb = re.search(r'blend:"([^"]*)"', trecho)
    if not mb:
        return False, f"campo blend nao encontrado para {cid}"

    antigo = mb.group(1)
    if antigo == novo:
        return False, f"o mapa ja esta em {antigo}"

    ini = m.start() + mb.start(1)
    fim = m.start() + mb.end(1)
    novo_src = src[:ini] + novo + src[fim:]

    # trava de seguranca: so o valor pode ter mudado
    if len(novo_src) - len(src) != len(novo) - len(antigo):
        return False, "alteracao inesperada, abortado"

    open(MAPA, "w", encoding="utf-8").write(novo_src)
    return True, antigo


def limpar_pendencia(cid, novo, foi_recusa):
    try:
        d = json.load(open(PEND, encoding="utf-8"))
    except Exception:
        return
    d["pendentes"] = [p for p in d.get("pendentes", [])
                      if not (p["id"] == cid and p["novo"] == novo)]
    if foi_recusa:
        d.setdefault("recusados", []).append(f"{cid}|{novo}")
    json.dump(d, open(PEND, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def main():
    cid, novo = extrair_pedido()
    if not cid or not novo:
        print("RESULTADO=Nao consegui entender a solicitacao.")
        sys.exit(0)

    if recusar:
        limpar_pendencia(cid, novo, True)
        print(f"RESULTADO=Solicitacao recusada. {cid} nao sera alterado e a proposta nao volta.")
        return

    ok, info = aplicar_no_mapa(cid, novo)
    if ok:
        limpar_pendencia(cid, novo, False)
        print(f"RESULTADO=Mapa atualizado: {cid} de {info} para {novo}.")
    else:
        print(f"RESULTADO=Nada foi alterado ({info}).")

    # regenera a pagina de pendencias
    try:
        import build
        d = build.carregar_pendencias()
        open(build.PEND_HTML, "w", encoding="utf-8").write(build.render_pendencias(d))
    except Exception as e:
        print(f"(aviso: nao regenerou a pagina de pendencias: {e})")


if __name__ == "__main__":
    main()
