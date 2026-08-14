#!/usr/bin/env python3
"""
Aplica (ou recusa) uma alteracao de mandato aprovada via issue.
Roda apenas pelo GitHub Actions, disparado quando uma issue [MAPA] e aberta.
Altera EXCLUSIVAMENTE o campo de percentual indicado, no mapa indicado.
"""

import json
import os
import re
import sys

ARQUIVOS  = {"blend": "mapa-mandatos.html", "saf": "mapa-saf.html"}
CAMPOS_OK = {"blend": {"blend"},
             "saf":   {"meta2025", "meta2030", "meta2040", "meta2050"}}
PEND = "pendencias.json"

PAIS_NOVO = False
NOME_PAIS = ""

titulo  = os.environ.get("ISSUE_TITLE", "")
corpo   = os.environ.get("ISSUE_BODY", "")
recusar = titulo.startswith("[MAPA-RECUSAR]")


def extrair_pedido():
    """Le o bloco json da issue; cai para o titulo se faltar."""
    m = re.search(r"```json\s*(\{.*?\})\s*```", corpo, re.DOTALL)
    if m:
        try:
            d = json.loads(m.group(1))
            if d.get("id") and d.get("novo"):
                global PAIS_NOVO, NOME_PAIS
                PAIS_NOVO = bool(d.get("pais_novo"))
                NOME_PAIS = str(d.get("pais", "")).strip()
                return (d.get("mapa", "blend"), d["id"].upper().strip(),
                        d.get("campo", "blend"), str(d["novo"]).strip())
        except Exception:
            pass
    m = re.search(r"\[MAPA(?:-RECUSAR)?\]\s+(\w+)/([A-Z]{3})\s+(\w+)\s*->\s*(\S+)", titulo)
    if m:
        return m.group(1), m.group(2), m.group(3), m.group(4)
    m = re.search(r"\[MAPA(?:-RECUSAR)?\]\s+([A-Z]{3})\s+blend\s*->\s*(\S+)", titulo)
    if m:
        return "blend", m.group(1), "blend", m.group(2)
    return None, None, None, None


PADRAO_PAIS = {
    "blend": ('  {{ id:"{cid}", name:"{pais}", flag:"{flag}", region:"{regiao}", '
              'status:"vigente", blend:"{novo}", year:{ano},\n'
              '    notes:"Registrado automaticamente a partir de noticia aprovada em {hoje}. '
              'Descricao ainda nao revisada." }},\n'),
    "saf":   ('  {{\n    id:"{cid}", name:"{pais}", flag:"{flag}", region:"{regiao}", '
              'status:"volumetrico", tipo:"volumetrico", mandato:"A revisar",\n'
              '    inicio:{ano}, meta2025:"—", meta2030:"—", meta2040:"—", meta2050:"—",\n'
              '    desc:"Registrado automaticamente a partir de noticia aprovada em {hoje}. '
              'Descricao ainda nao revisada."\n  }},\n'),
}


def inserir_pais(mapa, cid, pais, campo, novo):
    """Acrescenta um pais que ainda nao existe no mapa."""
    from datetime import datetime
    arquivo = ARQUIVOS[mapa]
    src = open(arquivo, encoding="utf-8").read()

    if re.search(r'id:"' + re.escape(cid) + r'"', src):
        return False, f"{cid} ja existe no mapa"

    m = re.search(r'(const COUNTRIES\s*=\s*\[)', src)
    if not m:
        return False, "nao encontrei a lista de paises no arquivo"

    ano = datetime.now().year
    hoje = datetime.now().strftime("%d/%m/%Y")
    linha = PADRAO_PAIS[mapa].format(cid=cid, pais=pais, flag="\U0001F310",
                                     regiao="A revisar", novo=novo, ano=ano, hoje=hoje)
    # o campo pedido pode nao ser o do padrao (ex: meta2040 no mapa de SAF)
    if mapa == "saf" and campo != "meta2030":
        linha = linha.replace(f'{campo}:"—"', f'{campo}:"{novo}"')
    elif mapa == "saf":
        linha = linha.replace('meta2030:"—"', f'meta2030:"{novo}"')

    novo_src = src[:m.end()] + "\n" + linha + src[m.end():]
    open(arquivo, "w", encoding="utf-8").write(novo_src)
    return True, "inserido"


def aplicar(mapa, cid, campo, novo):
    """Troca somente campo:"valor" do pais. Devolve (ok, info)."""
    if mapa not in ARQUIVOS:
        return False, f"mapa desconhecido: {mapa}"
    if campo not in CAMPOS_OK[mapa]:
        return False, f"campo nao permitido no mapa {mapa}: {campo}"

    arquivo = ARQUIVOS[mapa]
    src = open(arquivo, encoding="utf-8").read()

    m = re.search(r'id:"' + re.escape(cid) + r'"', src)
    if not m:
        return False, f"pais {cid} nao existe em {arquivo}"

    trecho = src[m.start():m.start() + 1400]
    mc = re.search(re.escape(campo) + r':"([^"]*)"', trecho)
    if not mc:
        return False, f"campo {campo} nao encontrado para {cid}"

    antigo = mc.group(1)
    if antigo == novo:
        return False, f"o mapa ja esta em {antigo}"

    ini = m.start() + mc.start(1)
    fim = m.start() + mc.end(1)
    novo_src = src[:ini] + novo + src[fim:]

    if len(novo_src) - len(src) != len(novo) - len(antigo):
        return False, "alteracao inesperada, abortado"

    open(arquivo, "w", encoding="utf-8").write(novo_src)
    return True, antigo


def limpar_pendencia(mapa, cid, campo, novo, foi_recusa):
    try:
        d = json.load(open(PEND, encoding="utf-8"))
    except Exception:
        return
    d["pendentes"] = [
        p for p in d.get("pendentes", [])
        if not (p.get("mapa", "blend") == mapa and p["id"] == cid
                and p.get("campo", "blend") == campo and p["novo"] == novo)
    ]
    if foi_recusa:
        d.setdefault("recusados", []).append(f"{mapa}|{cid}|{campo}|{novo}")
    json.dump(d, open(PEND, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def main():
    mapa, cid, campo, novo = extrair_pedido()
    if not cid or not novo:
        print("RESULTADO=Nao consegui entender a solicitacao.")
        sys.exit(0)

    if recusar:
        limpar_pendencia(mapa, cid, campo, novo, True)
        print(f"RESULTADO=Solicitacao recusada. {cid} nao sera alterado e a proposta nao volta.")
    elif PAIS_NOVO:
        ok, info = inserir_pais(mapa, cid, NOME_PAIS or cid, campo, novo)
        if ok:
            limpar_pendencia(mapa, cid, campo, novo, False)
            print(f"RESULTADO=Pais adicionado ao mapa de {mapa}: {NOME_PAIS or cid} "
                  f"com {campo}={novo}. Regiao, bandeira e descricao entraram como "
                  f"'A revisar' - complete no arquivo quando puder.")
        else:
            print(f"RESULTADO=Nada foi alterado ({info}).")
    else:
        ok, info = aplicar(mapa, cid, campo, novo)
        if ok:
            limpar_pendencia(mapa, cid, campo, novo, False)
            print(f"RESULTADO=Mapa de {mapa} atualizado: {cid} {campo} de {info} para {novo}.")
        else:
            print(f"RESULTADO=Nada foi alterado ({info}).")

    try:
        import build
        d = build.carregar_pendencias()
        open(build.PEND_HTML, "w", encoding="utf-8").write(build.render_pendencias(d))
    except Exception as e:
        print(f"(aviso: nao regenerou a pagina de pendencias: {e})")


if __name__ == "__main__":
    main()
