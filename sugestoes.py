"""Ganchos e recomendações que viram mudanças EDITÁVEIS e reversíveis no plano.

- Ganchos falados são trechos reais da transcrição (com tempo na fonte). Títulos sugeridos são texto
  editorial derivado dessa fala e ficam na trilha de títulos, nunca na legenda nem atribuídos a quem fala.
- Toda sugestão aplicada passa por `aplicar_operacao`, que só conhece um conjunto fechado de operações
  e devolve um plano revalidado. Nada vindo de sugestão (local ou de IA) é executado como comando.
"""
import copy
import re
import uuid

from flask import Blueprint, jsonify

import app as A
import editor as E
import referencias as R

bp = Blueprint("sugestoes", __name__)

FORTES = {"nunca", "ninguém", "ninguem", "segredo", "verdade", "medo", "morte", "morreu", "morto", "proibido", "erro",
          "perigo", "descobri", "descobriu", "sumiu", "desapareceu", "sozinho", "noite", "madrugada", "grito", "sangue",
          "estranho", "real", "impossível", "impossivel", "ninguém sabe", "último", "ultimo", "primeira vez"}
PREENCHE = {"então", "entao", "tipo", "né", "ne", "aí", "ai", "é", "e", "bom", "olha", "assim", "cara", "mano"}
SEGUNDA_PESSOA = {"você", "voce", "vocês", "voces", "te", "seu", "sua"}


# ---------------------------------------------------------------- frases da transcrição
def sentencas(palavras):
    """Quebra a transcrição em frases pela pontuação (ou por pausa > 0,8 s)."""
    out, atual = [], []
    for i, w in enumerate(palavras):
        if atual and (w[0] - atual[-1][1] > 0.8):
            out.append(atual)
            atual = []
        atual.append(w)
        if w[2][-1:] in ".?!…":
            out.append(atual)
            atual = []
    if atual:
        out.append(atual)
    return [{"ini": s[0][0], "fim": s[-1][1], "texto": " ".join(w[2] for w in s), "palavras": s} for s in out if s]


def _db(falas, ini, fim):
    vals = [f["db"] for f in falas if f["ini"] < fim and f["fim"] > ini]
    return max(vals) if vals else None


def ganchos(fonte, n=5):
    """Candidatos a gancho de abertura, pontuados por sinais simples e explicados. Heurística local: não mede retenção."""
    d = E.transcricao_em_cache(fonte)
    if not d:
        raise A.Erro("Transcreva a fonte (aba Legenda) antes de pedir ganchos.")
    sents = sentencas(d["palavras"])
    dbs = sorted(f["db"] for f in d["falas"] if f["db"] > -100)
    mediana = dbs[len(dbs) // 2] if dbs else None
    cand = []
    for s in sents:
        ws = [A._norm(w[2]) for w in s["palavras"]]
        n_pal, motivos, pontos = len(ws), [], 0.0
        if n_pal < 4 or s["fim"] - s["ini"] < 1.0:
            continue
        if s["texto"].rstrip().endswith("?"):
            pontos += 2
            motivos.append("é uma pergunta direta")
        if re.search(r"\d", s["texto"]) or any(w in ("mil", "cem", "dez", "vinte", "trinta") for w in ws):
            pontos += 1
            motivos.append("traz um número concreto")
        fortes = sorted({w for w in ws if w in {A._norm(f) for f in FORTES}})
        if fortes:
            pontos += min(2, len(fortes))
            motivos.append("palavra de impacto: " + ", ".join(fortes[:3]))
        if SEGUNDA_PESSOA & set(ws):
            pontos += 1
            motivos.append("fala direto com quem assiste")
        if 6 <= n_pal <= 22:
            pontos += 1
            motivos.append(f"frase curta ({n_pal} palavras)")
        elif n_pal > 30:
            pontos -= 1
            motivos.append(f"longa ({n_pal} palavras): talvez precise aparar")
        if ws[0] in {A._norm(p) for p in PREENCHE}:
            pontos -= 0.5
            motivos.append("começa com muleta (“" + s["palavras"][0][2] + "”)")
        db = _db(d["falas"], s["ini"], s["fim"])
        if db is not None and mediana is not None and db - mediana >= 3:
            pontos += 1
            motivos.append(f"fala mais intensa que a média (+{db - mediana:.0f} dB)")
        if s["ini"] >= 10:
            pontos += 0.5
            motivos.append(f"hoje só aparece em {int(s['ini'] // 60)}:{s['ini'] % 60:04.1f}")
        cand.append({"id": "g" + uuid.uuid4().hex[:8], "tipo": "fala", "fonte": fonte, "ini": round(s["ini"], 2),
                     "fim": round(s["fim"], 2), "texto": s["texto"], "pontos": pontos, "motivos": motivos})
    cand.sort(key=lambda c: (-c["pontos"], c["ini"]))
    escolhidos = []
    for c in cand:
        if all(c["fim"] <= e["ini"] or c["ini"] >= e["fim"] for e in escolhidos):
            escolhidos.append(c)
        if len(escolhidos) == n:
            break
    titulos = [t for t in (titulo_derivado(c) for c in escolhidos[:3]) if t]
    return {"falados": escolhidos, "titulos": titulos,
            "metodo": "Heurística local sobre a transcrição (pergunta, número, palavras de impacto, 2a pessoa, tamanho, "
                      "intensidade da voz, posição). Não prevê desempenho nem retenção.",
            "frases_analisadas": len(sents)}


def titulo_derivado(c):
    """Título editorial curto DERIVADO de uma fala real: tira muletas, corta na 1a vírgula/7 palavras.
    Fica marcado como texto do editor (trilha de títulos), sem aspas e sem atribuir fala a ninguém."""
    ws = [w[2] if isinstance(w, (list, tuple)) else w for w in c["texto"].split()]
    while ws and A._norm(ws[0]) in {A._norm(p) for p in PREENCHE}:
        ws.pop(0)
    curto = []
    for w in ws:
        curto.append(w)
        if w.endswith((",", ";", ":")) or len(curto) >= 7:
            break
    texto = " ".join(curto).strip(" ,;:")
    texto = re.sub(r"[\"“”]", "", texto)
    if len(texto) < 8:
        return None
    if texto[-1:] not in ".?!…":
        texto += "…" if len(ws) > len(curto) else ""
    texto = texto[:1].upper() + texto[1:]
    return {"id": "t" + uuid.uuid4().hex[:8], "tipo": "titulo", "texto": texto[:60], "derivado_de": c["id"],
            "fonte": c["fonte"], "t_fonte": c["ini"],
            "nota": f"Título editorial derivado da fala em {int(c['ini'] // 60)}:{c['ini'] % 60:04.1f}; não é citação."}


# ---------------------------------------------------------------- operações permitidas sobre o plano
def _remover_trecho(segs, fonte, ini, fim):
    out = []
    for s in segs:
        if s["fonte"] != fonte or s["fim"] <= ini or s["ini"] >= fim:
            out.append(s)
            continue
        if s["ini"] < ini - 0.1:
            out.append({**s, "fim": ini})
        if s["fim"] > fim + 0.1:
            out.append({**s, "id": "s" + uuid.uuid4().hex[:10], "ini": fim})
    return out


VISUAL_OK = ("estilo", "posicao_y", "tamanho", "destaque") + tuple(E.CAMPOS_VISUAIS)


def _visual(op):
    """Aparência do texto na tela vinda de uma sugestão: só chaves conhecidas; o validar_plano confere os valores."""
    v = op.get("visual") or {}
    if not isinstance(v, dict):
        raise A.Erro("op.visual precisa ser objeto.")
    desconhecidas = set(v) - set(VISUAL_OK)
    if desconhecidas:
        raise A.Erro(f"op.visual tem campo desconhecido: {sorted(desconhecidas)[0]}")
    return dict(v)


def aplicar_operacao(plano, op):
    """Aplica UMA operação do conjunto fechado e devolve um plano revalidado (o original não muda)."""
    p = E.validar_plano(copy.deepcopy(plano))
    if not isinstance(op, dict):
        raise A.Erro("Operação inválida.")
    tipo = op.get("tipo")
    num = lambda k, lo=0, hi=6 * 3600: E._num(op.get(k), f"op.{k}", lo, hi)  # noqa: E731
    if tipo == "inserir_inicio":                       # gancho falado como abertura (o trecho continua no lugar)
        if op.get("fonte") not in p["fontes"]:
            raise A.Erro("Fonte da operação não está no plano.")
        dur = p["fontes"][op["fonte"]]["duracao"]
        ini, fim = max(0.0, num("ini") - 0.12), min(dur, num("fim") + 0.25)
        p["segmentos"].insert(0, {"id": "s" + uuid.uuid4().hex[:10], "fonte": op["fonte"], "ini": ini, "fim": fim})
    elif tipo == "adicionar_titulo":
        texto = E._txt(op.get("texto"), "op.texto", 90)
        p["titulos"].append({"id": "t" + uuid.uuid4().hex[:10], "texto": texto, "ini": num("ini", 0, 600),
                             "fim": num("fim", 0.2, 600), "origem": op.get("origem", "sugestao_local"), **_visual(op)})
    elif tipo == "remover_trecho":                      # corta só o trecho; o resto do segmento fica
        if op.get("fonte") not in p["fontes"]:
            raise A.Erro("Fonte da operação não está no plano.")
        p["segmentos"] = _remover_trecho(p["segmentos"], op["fonte"], num("ini"), num("fim"))
    elif tipo == "cortar_inicio":                       # tira silêncio antes da primeira fala do 1o segmento
        if not p["segmentos"]:
            raise A.Erro("Plano sem segmentos.")
        s = p["segmentos"][0]
        novo = num("novo_ini")
        if not s["ini"] <= novo < s["fim"] - 0.3:
            raise A.Erro("O novo início precisa cair dentro do primeiro segmento.")
        s["ini"] = novo
    elif tipo == "ativar_legenda":
        p["legenda"]["ativa"] = True
        if op.get("preset") in E.PRESETS:
            est = dict(E.PRESETS[op["preset"]])
            est.pop("nome")
            p["legenda"]["estilo"] = est
    elif tipo == "ativar_zoom":
        p["efeitos"]["zoom"] = {"ativo": True, "intensidade": num("intensidade", 0.02, 0.15)}
    elif tipo == "adicionar_segmento":                  # corte sugerido entra no fim da linha do tempo
        if op.get("fonte") not in p["fontes"]:
            raise A.Erro("Fonte da operação não está no plano.")
        p["segmentos"].append({"id": "s" + uuid.uuid4().hex[:10], "fonte": op["fonte"], "ini": num("ini"),
                               "fim": num("fim")})
    elif tipo == "definir_titulo":                      # troca o texto de UM título (só por ação explícita)
        alvo = next((t for t in p["titulos"] if t["id"] == op.get("id")), None)
        if not alvo:
            raise A.Erro("Título não encontrado no plano.")
        alvo["texto"] = E._txt(op.get("texto"), "op.texto", 90)
        alvo["origem"] = op.get("origem", "sugestao_local")
        alvo.update(_visual(op))
    elif tipo == "adicionar_efeito":
        if op.get("fonte") is not None and op.get("fonte") not in p["fontes"]:
            raise A.Erro("Fonte da operação não está no plano.")
        p["audio"]["efeitos"].append({"id": "e" + uuid.uuid4().hex[:10], "som": op.get("som"),
                                      "arquivo": op.get("arquivo"), "fonte": op.get("fonte"), "t": num("t"),
                                      "dur": num("dur", 0.1, 10), "volume": num("volume", 0, 1),
                                      "objetivo": str(op.get("objetivo") or "")[:200],
                                      "origem": op.get("origem", "sugestao_local")})
    elif tipo == "remover_efeito":
        antes = len(p["audio"]["efeitos"])
        p["audio"]["efeitos"] = [e for e in p["audio"]["efeitos"] if e["id"] != op.get("id")]
        if len(p["audio"]["efeitos"]) == antes:
            raise A.Erro("Efeito não encontrado no plano.")
    elif tipo == "ajustar_audio":                       # tratamento: só chaves conhecidas, só true/false
        mapa_aud = {"realce_voz": ("realce_voz",), "normalizar": ("normalizar",),
                    "abaixar_na_fala": ("musica", "abaixar_na_fala")}
        mudou = False
        for k, caminho in mapa_aud.items():
            if k in op:
                alvo = p["audio"]
                for c in caminho[:-1]:
                    alvo = alvo[c]
                alvo[caminho[-1]] = E._bool(op[k], f"op.{k}")
                mudou = True
        if not mudou:
            raise A.Erro("ajustar_audio sem nenhum ajuste conhecido.")
    else:
        raise A.Erro(f"Operação desconhecida: {tipo!r}.")
    return E.validar_plano(p)


def aplicar_operacoes(plano, ops):
    """Uma edição completa proposta = várias operações, aplicadas em ordem; se uma falhar, nada é aplicado."""
    if not isinstance(ops, list) or not 1 <= len(ops) <= 20:
        raise A.Erro("Envie de 1 a 20 operações.")
    p = plano
    for i, op in enumerate(ops):
        try:
            p = aplicar_operacao(p, op)
        except A.Erro as e:
            raise A.Erro(f"Operação {i + 1} de {len(ops)} recusada (nada foi aplicado): {e}")
    return p


# ---------------------------------------------------------------- meu vídeo x referências
def metricas_plano(plano):
    p = E.validar_plano(plano)
    m = E.mapa(p)
    dur = m[-1]["out_fim"] if m else 0.0
    ev = E.eventos_legenda(p)
    palavras = sum(len(e["palavras"]) for e in ev)
    fala = sum(e["palavras"][-1][2] - e["palavras"][0][1] for e in ev)
    tem_trans = bool(p["legenda"]["frases"])
    return {"duracao_s": round(dur, 2), "segmentos": len(m),
            "cortes_por_min": round((len(m) - 1) / dur * 60, 2) if dur else None,
            "plano_medio_s": round(dur / len(m), 2) if m else None,
            "primeira_fala_s": round(ev[0]["palavras"][0][1], 2) if ev else None,
            "palavras_por_min": round(palavras / fala * 60, 1) if fala > 1 else None,
            "legenda_ativa": p["legenda"]["ativa"], "zoom": p["efeitos"]["zoom"]["ativo"], "tem_transcricao": tem_trans,
            "nota": ("Cortes/min do seu vídeo contam só as emendas entre segmentos; as trocas de cena dentro de cada "
                     "segmento não entram (nas referências, entram).") if m else None}


def repeticoes_no_plano(plano):
    """Repetições de 4+ palavras dentro do que está na linha do tempo (para sugerir corte da 2a ocorrência)."""
    p = E.validar_plano(plano)
    out = []
    for fonte in p["fontes"]:
        d = E.transcricao_combinada(fonte)
        if not d:
            continue
        dentro = [w for w in d["palavras"]
                  if any(s["fonte"] == fonte and s["ini"] <= (w[0] + w[1]) / 2 < s["fim"] for s in p["segmentos"])]
        for r in R._repeticoes(dentro):
            out.append({"fonte": fonte, **r})
    return out


def recomendacoes(plano, comparacao):
    p = E.validar_plano(plano)
    meu = metricas_plano(p)
    hip = {h["metrica"]: h for h in comparacao["hipoteses"]}
    refs = comparacao["referencias"]
    analisadas = [r for r in refs if r["metricas"]]
    um_so = len(analisadas) == 1
    rec = []

    def nova(titulo, explicacao, evidencia, op, quando=None, forca=None):
        rec.append({"id": "r" + uuid.uuid4().hex[:8], "titulo": titulo, "explicacao": explicacao, "evidencia": evidencia,
                    "operacao": op, "tempos_fonte": quando,
                    "forca": forca or ("inspiração de 1 referência" if um_so else "sem hipótese de desempenho")})
    # 1) abrir com fala
    vals = [r["metricas"]["primeira_fala_s"] for r in analisadas if r["metricas"].get("primeira_fala_s") is not None]
    if meu["primeira_fala_s"] is not None and vals and meu["primeira_fala_s"] > max(vals) and meu["primeira_fala_s"] > 0.8:
        s0 = p["segmentos"][0]
        d = E.transcricao_combinada(s0["fonte"])
        prim = next((w for w in (d or {}).get("palavras", []) if s0["ini"] <= w[0] < s0["fim"]), None)
        if prim and prim[0] - 0.15 > s0["ini"]:
            h = hip.get("primeira_fala_s")
            nova("Começar já na fala",
                 f"Seu vídeo leva {meu['primeira_fala_s']:.1f}s até a primeira palavra; as referências analisadas levam "
                 f"{', '.join(f'{v:.1f}s' for v in vals)}. A mudança apara o silêncio inicial do 1o segmento "
                 f"(fonte {prim[0] - 0.15:.1f}s), sem tirar fala.",
                 [{"ref": r["nome"], "valor": r["metricas"].get("primeira_fala_s")} for r in analisadas] + ([{"hipotese": h["texto"]}] if h else []),
                 {"tipo": "cortar_inicio", "novo_ini": round(prim[0] - 0.15, 2)}, [s0["ini"], prim[0]],
                 forca=f"evidência {h['evidencia']}" if h and h["direcao"] == "menor" else None)
    # 2) remover repetição exata
    for r in repeticoes_no_plano(p)[:3]:
        nova("Tirar uma repetição",
             f"“{r['texto']}” é dito de novo em {r['repeticao']:.1f}s (fonte), depois da 1a vez em {r['primeira']:.1f}s. "
             "Só a 2a ocorrência sai; confira se ela não muda o sentido.",
             [{"hipotese": hip["repeticoes"]["texto"]}] if "repeticoes" in hip else [],
             {"tipo": "remover_trecho", "fonte": r["fonte"], "ini": round(r["repeticao"] - 0.05, 2),
              "fim": round(r["repeticao_fim"] + 0.1, 2)}, [r["repeticao"], r["repeticao_fim"]],
             forca=f"evidência {hip['repeticoes']['evidencia']}" if "repeticoes" in hip else "edição de limpeza (sem relação com desempenho)")
    # 3) legenda
    com_leg = [r for r in refs if r["legenda_queimada"] is True or (r["tags"].get("legenda") or "").lower() in ("sim", "s", "yes")]
    if com_leg and not meu["legenda_ativa"]:
        nova("Ligar a legenda", f"{len(com_leg)} de {len(refs)} referências têm legenda na tela (observado pelo provedor ou informado por você).",
             [{"ref": r["nome"], "valor": "legenda na tela"} for r in com_leg], {"tipo": "ativar_legenda", "preset": "classico"})
    # 4) ritmo: zoom leve em planos longos, só se houver hipótese apontando planos mais curtos/mais cortes
    h = hip.get("plano_medio_s") if hip.get("plano_medio_s", {}).get("direcao") == "menor" else (
        hip.get("cortes_por_min") if hip.get("cortes_por_min", {}).get("direcao") == "maior" else None)
    if h and not meu["zoom"] and meu["plano_medio_s"] and meu["plano_medio_s"] > 6:
        nova("Zoom leve nos planos longos",
             f"Seus segmentos duram em média {meu['plano_medio_s']:.1f}s. Um zoom lento e contido dá movimento sem cortar conteúdo.",
             [{"hipotese": h["texto"]}], {"tipo": "ativar_zoom", "intensidade": 0.05}, forca=f"evidência {h['evidencia']}")
    # 5) características observadas pelo Gemini nas referências: com desempenho comparável vira HIPÓTESE
    #    ("as de mais views/dia tinham X"); sem métricas, só PADRÃO observado (inspiração, sem relação com desempenho).
    feitos = {r["titulo"] for r in rec}
    s0 = p["segmentos"][0] if p["segmentos"] else None
    dd = E.transcricao_combinada(s0["fonte"]) if s0 else None
    prim = next((w for w in (dd or {}).get("palavras", []) if s0["ini"] <= w[0] < s0["fim"]), None) if s0 else None
    origens = [(h["metrica"], [{"hipotese": h["texto"]}], f"evidência {h['evidencia']}", "As referências com mais views/dia")
               for h in comparacao["hipoteses"] if h.get("tipo") == "caracteristica_observada" and h["direcao"] == "presente"]
    origens += [(x["metrica"], [{"padrao": x["texto"]}], f"padrão em {x['n']} de {x['de']} referências (sem dado de desempenho)",
                 f"{x['n']} de {x['de']} referências analisadas") for x in comparacao.get("padroes") or []]
    for k, ev, forca, quem in origens:
        if k == "legenda_queimada" and not meu["legenda_ativa"] and "Ligar a legenda" not in feitos:
            nova("Ligar a legenda", f"{quem} tinham legenda na tela (observado pelo Gemini).",
                 ev, {"tipo": "ativar_legenda", "preset": "classico"}, forca=forca)
        elif k == "texto_na_tela_no_inicio" and not any(t["ini"] < 1 for t in p["titulos"]) and "Texto na tela logo no início" not in feitos:
            nova("Texto na tela logo no início",
                 f"{quem} mostravam texto na tela no começo. Use “Sugerir” na aba Texto na tela: o texto sai da fala do "
                 "seu corte; nada é inventado aqui.", ev, None, forca=forca)
        elif k == "fala_nos_3s" and meu["primeira_fala_s"] is not None and meu["primeira_fala_s"] > 3 \
                and not feitos & {"Começar já na fala", "Fala nos 3 primeiros segundos"}:
            op = ({"tipo": "cortar_inicio", "novo_ini": round(prim[0] - 0.15, 2)}
                  if prim and prim[0] - 0.15 > s0["ini"] and prim[0] - 0.15 < s0["fim"] - 0.3 else None)
            nova("Fala nos 3 primeiros segundos",
                 f"Seu vídeo leva {meu['primeira_fala_s']:.1f}s até a primeira fala; {quem} falavam logo de cara. "
                 + (f"A mudança começa o 1o corte em {prim[0] - 0.15:.1f}s da fonte (logo antes de “{prim[2]}”)."
                    if op else "Veja os ganchos (aba Cortes) para abrir com uma fala do próprio corte."),
                 ev, op, [s0["ini"], prim[0]] if op else None, forca=forca)
        elif k == "efeitos_sonoros" and not p["audio"]["efeitos"] and "Efeitos sonoros com propósito" not in feitos:
            nova("Efeitos sonoros com propósito",
                 f"{quem} usavam efeitos sonoros. Use “Sugerir áudio” (aba Áudio e efeitos): só entram efeitos com "
                 "motivo claro, sem cobrir a fala.", ev, None, forca=forca)
        elif k == "musica_de_fundo" and not p["audio"]["musica"]["id"] and "Música de fundo" not in feitos:
            nova("Música de fundo", f"{quem} tinham música de fundo. Isso exige um arquivo que você tenha direito "
                 "de usar (o app não fornece música).", ev, None, forca=forca)
        feitos = {r["titulo"] for r in rec}
    return {"meu": meu, "recomendacoes": rec}


# ---------------------------------------------------------------- rotas
@bp.post("/api/ganchos")
def rota_ganchos():
    j = A.corpo_json()
    p = E.validar_plano(j.get("plano"))
    fontes = [j["fonte"]] if j.get("fonte") in p["fontes"] else list(p["fontes"])
    if not fontes:
        raise A.Erro("Adicione uma fonte antes de pedir ganchos.")
    res = [ganchos(f) for f in fontes if E.transcricao_em_cache(f)]
    if not res:
        raise A.Erro("Transcreva a fonte (aba Legenda) antes de pedir ganchos.")
    return jsonify({"falados": sorted([c for r in res for c in r["falados"]], key=lambda c: -c["pontos"])[:6],
                    "titulos": [t for r in res for t in r["titulos"]][:4], "metodo": res[0]["metodo"]})


@bp.post("/api/operacao")
def rota_operacao():
    j = A.corpo_json()
    if j.get("operacoes") is not None:
        return jsonify({"plano": aplicar_operacoes(j.get("plano"), j["operacoes"])})
    return jsonify({"plano": aplicar_operacao(j.get("plano"), j.get("operacao"))})
