"""Editor assistido: plano de edição versionado, linha do tempo, legendas estilizadas, prévia e exportação.

Princípios:
- O PLANO (JSON, versão 1) é a única especificação: a prévia e a exportação usam a mesma função.
- Tempos da FONTE (segmentos, palavras da legenda) ficam separados dos tempos de SAÍDA (títulos);
  a legenda é projetada na saída pelos segmentos, então cortar, reordenar ou repetir um trecho
  mantém a sincronia.
- A legenda é fala transcrita (fiel); títulos são texto editorial, com estilo e trilha próprios.
- A mídia de origem nunca é alterada.
"""
import copy
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
import uuid

from flask import Blueprint, jsonify, request, send_file, abort

import app as A
import tarefas as T

VERSAO_PLANO = 1
EDICOES = A.ROOT / "edicoes"
CACHE = A.ROOT / "cache"
for _d in (EDICOES, CACHE / "transcricoes", CACHE / "previas", CACHE / "miniaturas"):
    _d.mkdir(parents=True, exist_ok=True)

bp = Blueprint("editor", __name__)

# ---------------------------------------------------------------- estilos
COR = re.compile(r"#[0-9A-Fa-f]{6}")
ESTILO_PADRAO = {
    "preset": "classico", "fonte": "Arial", "tamanho": 80, "negrito": True, "maiusculas": True,
    "cor": "#FFFFFF", "contorno": 6, "contorno_cor": "#000000",
    "fundo": False, "fundo_cor": "#000000", "fundo_opacidade": 0.6,
    "posicao_y": 0.72, "destaque": False, "destaque_cor": "#FFD400", "palavras_por_frase": 3,
}
PRESETS = {
    "classico": {"nome": "Clássico", **ESTILO_PADRAO},
    "caixa": {"nome": "Caixa", **ESTILO_PADRAO, "preset": "caixa", "tamanho": 66, "maiusculas": False,
              "contorno": 0, "fundo": True, "fundo_opacidade": 0.7, "posicao_y": 0.78, "palavras_por_frase": 4},
    "destaque": {"nome": "Palavra em destaque", **ESTILO_PADRAO, "preset": "destaque", "tamanho": 100,
                 "contorno": 8, "posicao_y": 0.55, "destaque": True, "palavras_por_frase": 2},
    "discreto": {"nome": "Discreto", **ESTILO_PADRAO, "preset": "discreto", "tamanho": 56, "negrito": False,
                 "maiusculas": False, "contorno": 3, "posicao_y": 0.86, "palavras_por_frase": 5},
}
# Títulos editoriais: visual propositalmente diferente da legenda de fala (caixa clara, no alto).
ESTILO_TITULO = {"fonte": "Arial", "tamanho": 64, "cor": "#111111", "fundo_cor": "#FFFFFF", "posicao_y": 0.14}
# Aparências do texto na tela (todas diferentes da legenda de fala). "claro" é o visual original.
_BASE_T = {"fonte": "Arial", "cor": "#111111", "fundo_cor": "#FFFFFF", "caixa": True, "maiusculas": False, "italico": False,
           "destaque_cor": "#E03A2F", "animacao": "nenhuma", "borda": 14}
ESTILOS_TITULO = {
    "claro": {**_BASE_T, "nome": "Caixa clara", "estilo_ass": "Titulo"},
    "escuro": {**_BASE_T, "nome": "Caixa escura", "estilo_ass": "TituloEscuro", "cor": "#FFFFFF", "fundo_cor": "#111111",
               "destaque_cor": "#FFD400"},
    "contorno": {**_BASE_T, "nome": "Contorno amarelo", "estilo_ass": "TituloContorno", "cor": "#FFD400",
                 "fundo_cor": "#000000", "caixa": False, "destaque_cor": "#FFFFFF", "borda": 7},
    # estilos de gancho (nomes da interface em português)
    "esporte": {**_BASE_T, "nome": "Esporte", "estilo_ass": "TituloContorno", "fonte": "Impact", "cor": "#FFFFFF",
                "fundo_cor": "#000000", "caixa": False, "maiusculas": True, "italico": True, "destaque_cor": "#FFD400",
                "animacao": "deslizar", "borda": 8},
    "impacto": {**_BASE_T, "nome": "Impacto", "estilo_ass": "Titulo", "fonte": "Arial", "cor": "#FFFFFF",
                "fundo_cor": "#E0242B", "caixa": True, "maiusculas": True, "destaque_cor": "#FFE14D", "animacao": "pop",
                "borda": 16},
    "limpo": {**_BASE_T, "nome": "Limpo", "estilo_ass": "TituloContorno", "fonte": "Segoe UI", "cor": "#FFFFFF",
              "fundo_cor": "#000000", "caixa": False, "destaque_cor": "#9BE7FF", "animacao": "fade", "borda": 3},
}
ANIMACOES = ("nenhuma", "fade", "pop", "deslizar")
CAMPOS_VISUAIS = ("fonte", "cor", "fundo_cor", "caixa", "maiusculas", "italico", "destaque_cor", "animacao")


def visual_titulo(t):
    """Propriedades visuais efetivas de um texto na tela: as do preset, sobrescritas pelas do próprio item."""
    v = dict(ESTILOS_TITULO[t.get("estilo", "claro")])
    v.update({k: t[k] for k in CAMPOS_VISUAIS if t.get(k) is not None})
    return v

QUALIDADES = {
    "previa": {"w": 540, "h": 960, "preset": "ultrafast", "crf": 30, "abr": "96k", "rotulo": "Prévia em baixa resolução (540×960)"},
    "final": {"w": 1080, "h": 1920, "preset": "medium", "crf": 20, "abr": "160k", "rotulo": "Exportação 1080×1920"},
}


# ---------------------------------------------------------------- validação do plano
def _erro(caminho, msg):
    raise A.Erro(f"Plano inválido em '{caminho}': {msg}")


def _num(v, caminho, lo=None, hi=None):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        _erro(caminho, f"precisa ser número, veio {v!r}")
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        _erro(caminho, f"fora da faixa [{lo}, {hi}]: {v}")
    return float(v)


def _txt(v, caminho, maximo, vazio=False):
    if not isinstance(v, str):
        _erro(caminho, f"precisa ser texto, veio {type(v).__name__}")
    v = v.strip()
    if not v and not vazio:
        _erro(caminho, "está vazio")
    if len(v) > maximo:
        _erro(caminho, f"passa de {maximo} caracteres")
    return v


def _bool(v, caminho):
    if not isinstance(v, bool):
        _erro(caminho, f"precisa ser true/false, veio {v!r}")
    return v


def _id(v, caminho):
    if not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", v):
        _erro(caminho, f"id inválido: {v!r}")
    return v


def validar_estilo(e, caminho="legenda.estilo"):
    if not isinstance(e, dict):
        _erro(caminho, "precisa ser objeto")
    out = dict(ESTILO_PADRAO)
    for k, v in e.items():
        c = f"{caminho}.{k}"
        if k == "preset":
            out[k] = v if v in PRESETS or v == "personalizado" else _erro(c, "preset desconhecido")
        elif k == "nome":
            continue
        elif k == "fonte":
            out[k] = _txt(v, c, 40)
            if re.search(r"[,{}\\\n]", out[k]):
                _erro(c, "caractere não permitido no nome da fonte")
        elif k in ("cor", "contorno_cor", "fundo_cor", "destaque_cor"):
            if not isinstance(v, str) or not COR.fullmatch(v):
                _erro(c, "cor precisa ser #RRGGBB")
            out[k] = v.upper()
        elif k == "tamanho":
            out[k] = int(_num(v, c, 24, 180))
        elif k == "contorno":
            out[k] = _num(v, c, 0, 16)
        elif k == "fundo_opacidade":
            out[k] = _num(v, c, 0, 1)
        elif k == "posicao_y":
            out[k] = _num(v, c, 0.05, 0.95)
        elif k == "palavras_por_frase":
            out[k] = int(_num(v, c, 1, 12))
        elif k in ("negrito", "maiusculas", "fundo", "destaque"):
            out[k] = _bool(v, c)
        else:
            _erro(c, "campo desconhecido")
    return out


def plano_vazio():
    return {"versao": VERSAO_PLANO, "fontes": {}, "segmentos": [], "quadro": {"crop_x": 0.5},
            "legenda": {"ativa": True, "estilo": dict(ESTILO_PADRAO), "frases": []}, "titulos": [],
            "efeitos": {"zoom": {"ativo": False, "intensidade": 0.06}, "transicao": {"tipo": "corte", "duracao": 0.2}},
            "audio": {"volume_original": 1.0, "normalizar": True, "realce_voz": False, "efeitos": [],
                      "musica": {"id": None, "volume": 0.12, "abaixar_na_fala": True}}}


# ---------------------------------------------------------------- efeitos sonoros
# Todos sintetizados pelo próprio ffmpeg (lavfi): nada de arquivo de terceiros, nada de licença a conferir.
# {d} é a duração; {d1}..{d9} são frações dela (d*0.1 .. d*0.9).
SONS = {
    "whoosh": {"nome": "Passagem (whoosh)", "dur": 0.6, "uso": "marcar uma troca de assunto ou de cena",
               "expr": "anoisesrc=d={d}:c=pink:r=44100:a=0.7,bandpass=f=1400:width_type=o:w=1.6,"
                       "afade=t=in:d={d4},afade=t=out:st={d4}:d={d6}"},
    "impacto": {"nome": "Impacto grave", "dur": 1.2, "uso": "pontuar uma revelação, logo depois da frase",
                "expr": "aevalsrc='0.9*sin(2*PI*(70-30*t)*t)*exp(-3.5*t)':s=44100:d={d}"},
    "subida": {"nome": "Subida de tensão", "dur": 2.0, "uso": "preparar uma revelação (termina quando ela começa)",
               "expr": "aevalsrc='0.35*(t/{d})*sin(2*PI*(150+300*t/{d})*t)':s=44100:d={d},afade=t=out:st={d9}:d={d1}"},
    "drone": {"nome": "Ambiência grave", "dur": 4.0, "uso": "tensão contínua por baixo da fala, bem baixa",
              "expr": "aevalsrc='0.25*sin(2*PI*55*t)+0.18*sin(2*PI*82.5*t)+0.08*sin(2*PI*110.7*t)':s=44100:d={d},"
                      "lowpass=f=400,afade=t=in:d={d3},afade=t=out:st={d7}:d={d3}"},
    "bipe": {"nome": "Bipe (censura)", "dur": 0.5, "uso": "cobrir uma palavra", "expr": "sine=f=1000:sample_rate=44100:d={d},volume=4"},
}


def expr_som(som, dur):
    d = {"d": f"{dur:.3f}", **{f"d{k}": f"{dur * k / 10:.3f}" for k in range(1, 10)}}
    return SONS[som]["expr"].format(**d)


def validar_efeito(e, caminho, fontes, exigir_arquivos=False):
    if not isinstance(e, dict):
        _erro(caminho, "precisa ser objeto")
    som = e.get("som")
    if som not in SONS and som != "arquivo":
        _erro(caminho + ".som", f"som desconhecido: {som!r}")
    arq = e.get("arquivo")
    if som == "arquivo":
        if not isinstance(arq, str) or not re.fullmatch(r"[a-f0-9]{12}", arq):
            _erro(caminho + ".arquivo", "id de upload inválido")
        if exigir_arquivos:
            A.achar(arq)
    fonte = e.get("fonte")
    if fonte is not None and fonte not in fontes:
        _erro(caminho + ".fonte", "fonte não declarada em 'fontes'")
    origem = e.get("origem", "usuario")
    if origem not in ("usuario", "sugestao_local", "sugestao_ia"):
        _erro(caminho + ".origem", "origem desconhecida")
    return {"id": _id(e.get("id"), caminho + ".id"), "som": som, "arquivo": arq if som == "arquivo" else None,
            "fonte": fonte, "t": _num(e.get("t"), caminho + ".t", 0, 6 * 3600),
            "dur": _num(e.get("dur", 1.0), caminho + ".dur", 0.1, 10),
            "volume": _num(e.get("volume", 0.3), caminho + ".volume", 0, 1),
            "objetivo": _txt(e.get("objetivo", ""), caminho + ".objetivo", 200, vazio=True), "origem": origem}


def validar_plano(p, exigir_arquivos=False):
    """Confere tipos, faixas e referências; devolve uma cópia normalizada. Vale também para o que vier
    de um modelo de IA: nada fora deste formato é aplicado."""
    if not isinstance(p, dict):
        _erro("(raiz)", "precisa ser objeto JSON")
    if p.get("versao") != VERSAO_PLANO:
        _erro("versao", f"versão {p.get('versao')!r} desconhecida (esperado {VERSAO_PLANO})")
    out = plano_vazio()
    desconhecidos = set(p) - set(out)
    if desconhecidos:
        _erro(sorted(desconhecidos)[0], "campo desconhecido")

    fontes = p.get("fontes", {})
    if not isinstance(fontes, dict) or len(fontes) > 20:
        _erro("fontes", "precisa ser objeto com até 20 fontes")
    for fid, f in fontes.items():
        c = f"fontes.{fid}"
        if not re.fullmatch(r"[a-f0-9]{12}", str(fid)):
            _erro(c, "id de upload inválido")
        if not isinstance(f, dict):
            _erro(c, "precisa ser objeto")
        out["fontes"][fid] = {"nome": _txt(f.get("nome", "video"), c + ".nome", 200),
                              "duracao": _num(f.get("duracao"), c + ".duracao", 0.1, 6 * 3600),
                              "tem_audio": _bool(f.get("tem_audio", True), c + ".tem_audio"),
                              "largura": f.get("largura") if isinstance(f.get("largura"), int) else None,
                              "altura": f.get("altura") if isinstance(f.get("altura"), int) else None}
        if exigir_arquivos:
            A.achar(fid)

    segs = p.get("segmentos", [])
    if not isinstance(segs, list) or len(segs) > 500:
        _erro("segmentos", "precisa ser lista com até 500 segmentos")
    vistos = set()
    for i, s in enumerate(segs):
        c = f"segmentos[{i}]"
        if not isinstance(s, dict):
            _erro(c, "precisa ser objeto")
        sid = _id(s.get("id"), c + ".id")
        if sid in vistos:
            _erro(c + ".id", "repetido")
        vistos.add(sid)
        fonte = s.get("fonte")
        if fonte not in out["fontes"]:
            _erro(c + ".fonte", "fonte não declarada em 'fontes'")
        dur = out["fontes"][fonte]["duracao"]
        ini = _num(s.get("ini"), c + ".ini", 0, dur)
        fim = _num(s.get("fim"), c + ".fim", 0, dur + 0.05)
        a, b = A.quadros(ini), min(A.quadros(fim), math.floor(dur * A.FPS + 1e-6))
        if b - a < 3:
            _erro(c, "segmento curto demais (mínimo 3 quadros) ou com fim antes do início")
        out["segmentos"].append({"id": sid, "fonte": fonte, "ini": a / A.FPS, "fim": b / A.FPS})

    q = p.get("quadro", {})
    if not isinstance(q, dict):
        _erro("quadro", "precisa ser objeto")
    out["quadro"]["crop_x"] = _num(q.get("crop_x", 0.5), "quadro.crop_x", 0, 1)

    leg = p.get("legenda", {})
    if not isinstance(leg, dict):
        _erro("legenda", "precisa ser objeto")
    out["legenda"]["ativa"] = _bool(leg.get("ativa", True), "legenda.ativa")
    out["legenda"]["estilo"] = validar_estilo(leg.get("estilo", {}))
    frases = leg.get("frases", [])
    if not isinstance(frases, list) or len(frases) > 20000:
        _erro("legenda.frases", "precisa ser lista com até 20000 frases")
    ids = set()
    for i, fr in enumerate(frases):
        c = f"legenda.frases[{i}]"
        if not isinstance(fr, dict):
            _erro(c, "precisa ser objeto")
        fid = _id(fr.get("id"), c + ".id")
        if fid in ids:
            _erro(c + ".id", "repetido")
        ids.add(fid)
        fonte = fr.get("fonte")
        if fonte not in out["fontes"]:
            _erro(c + ".fonte", "fonte não declarada em 'fontes'")
        dur = out["fontes"][fonte]["duracao"]
        pals = fr.get("palavras")
        if not isinstance(pals, list) or not 1 <= len(pals) <= 60:
            _erro(c + ".palavras", "precisa ter de 1 a 60 palavras")
        lista, ant = [], -1.0
        for j, w in enumerate(pals):
            cw = f"{c}.palavras[{j}]"
            if not isinstance(w, dict):
                _erro(cw, "precisa ser objeto {t, ini, fim}")
            t = _txt(w.get("t"), cw + ".t", 60)
            ini = _num(w.get("ini"), cw + ".ini", 0, dur + 1)
            fim = _num(w.get("fim"), cw + ".fim", ini, dur + 1)
            if ini < ant - 1e-6:
                _erro(cw + ".ini", "palavras fora de ordem")
            ant = ini
            lista.append({"t": t, "ini": round(ini, 3), "fim": round(fim, 3)})
        item = {"id": fid, "fonte": fonte, "palavras": lista}
        if _bool(fr.get("editada", False), c + ".editada"):
            item["editada"] = True   # corrigida à mão: regerar a legenda do trecho não apaga
        out["legenda"]["frases"].append(item)

    tits = p.get("titulos", [])
    if not isinstance(tits, list) or len(tits) > 20:
        _erro("titulos", "precisa ser lista com até 20 títulos")
    for i, t in enumerate(tits):
        c = f"titulos[{i}]"
        if not isinstance(t, dict):
            _erro(c, "precisa ser objeto")
        ini = _num(t.get("ini"), c + ".ini", 0, 6 * 3600)
        fim = _num(t.get("fim"), c + ".fim", ini + 0.2, 6 * 3600)
        origem = t.get("origem", "usuario")
        if origem not in ("usuario", "sugestao_local", "sugestao_ia"):
            _erro(c + ".origem", "origem desconhecida")
        estilo = t.get("estilo", "claro")
        if estilo not in ESTILOS_TITULO:
            _erro(c + ".estilo", f"use {', '.join(ESTILOS_TITULO)}")
        item = {"id": _id(t.get("id"), c + ".id"), "texto": _txt(t.get("texto"), c + ".texto", 90),
                "ini": ini, "fim": fim, "origem": origem, "estilo": estilo,
                "posicao_y": _num(t.get("posicao_y", ESTILO_TITULO["posicao_y"]), c + ".posicao_y", 0.05, 0.95),
                "tamanho": int(_num(t.get("tamanho", ESTILO_TITULO["tamanho"]), c + ".tamanho", 24, 160))}
        for k in CAMPOS_VISUAIS:   # opcionais: sobrescrevem o preset
            v = t.get(k)
            if v is None:
                continue
            ck = f"{c}.{k}"
            if k == "fonte":
                v = _txt(v, ck, 40)
                if re.search(r"[,{}\\\n]", v):
                    _erro(ck, "caractere não permitido no nome da fonte")
            elif k in ("cor", "fundo_cor", "destaque_cor"):
                if not isinstance(v, str) or not COR.fullmatch(v):
                    _erro(ck, "cor precisa ser #RRGGBB")
                v = v.upper()
            elif k == "animacao":
                if v not in ANIMACOES:
                    _erro(ck, f"use {', '.join(ANIMACOES)}")
            else:
                v = _bool(v, ck)
            item[k] = v
        dest = t.get("destaque")
        if dest is not None:
            if not isinstance(dest, list) or len(dest) > 20 or not all(isinstance(x, int) and not isinstance(x, bool)
                                                                       and 0 <= x < 60 for x in dest):
                _erro(c + ".destaque", "lista de posições de palavras (0 a 59)")
            item["destaque"] = sorted(set(dest))
        out["titulos"].append(item)

    ef = p.get("efeitos", {})
    if not isinstance(ef, dict):
        _erro("efeitos", "precisa ser objeto")
    z = ef.get("zoom", {}) or {}
    out["efeitos"]["zoom"] = {"ativo": _bool(z.get("ativo", False), "efeitos.zoom.ativo"),
                              "intensidade": _num(z.get("intensidade", 0.06), "efeitos.zoom.intensidade", 0, 0.15)}
    tr = ef.get("transicao", {}) or {}
    if tr.get("tipo", "corte") not in ("corte", "fade"):
        _erro("efeitos.transicao.tipo", "use 'corte' ou 'fade'")
    out["efeitos"]["transicao"] = {"tipo": tr.get("tipo", "corte"),
                                   "duracao": _num(tr.get("duracao", 0.2), "efeitos.transicao.duracao", 0.05, 0.5)}

    au = p.get("audio", {})
    if not isinstance(au, dict):
        _erro("audio", "precisa ser objeto")
    mu = au.get("musica", {}) or {}
    mid = mu.get("id")
    if mid is not None and not re.fullmatch(r"[a-f0-9]{12}", str(mid)):
        _erro("audio.musica.id", "id de upload inválido")
    if mid is not None and exigir_arquivos:
        A.achar(mid)
    efs = au.get("efeitos", [])
    if not isinstance(efs, list) or len(efs) > 40:
        _erro("audio.efeitos", "precisa ser lista com até 40 efeitos")
    efeitos = [validar_efeito(e, f"audio.efeitos[{i}]", out["fontes"], exigir_arquivos) for i, e in enumerate(efs)]
    if len({e["id"] for e in efeitos}) != len(efeitos):
        _erro("audio.efeitos", "id repetido")
    out["audio"] = {"volume_original": _num(au.get("volume_original", 1.0), "audio.volume_original", 0, 2),
                    "normalizar": _bool(au.get("normalizar", True), "audio.normalizar"),
                    "realce_voz": _bool(au.get("realce_voz", False), "audio.realce_voz"), "efeitos": efeitos,
                    "musica": {"id": mid, "volume": _num(mu.get("volume", 0.12), "audio.musica.volume", 0, 1),
                               "abaixar_na_fala": _bool(mu.get("abaixar_na_fala", True), "audio.musica.abaixar_na_fala")}}
    return out


# ---------------------------------------------------------------- linha do tempo
def mapa(plano):
    """Segmentos na ordem de saída, com início/fim de SAÍDA (grade de quadros, mesma base do render)."""
    cortes = [(s["ini"], s["fim"]) for s in plano["segmentos"]]
    inicios = A.linha_do_tempo(cortes)
    out = []
    for s, ini_out in zip(plano["segmentos"], inicios):
        n = A.quadros(s["fim"] - s["ini"])
        out.append({**s, "out_ini": ini_out, "out_fim": ini_out + n / A.FPS, "quadros": n})
    return out


def duracao_saida(plano):
    m = mapa(plano)
    return m[-1]["out_fim"] if m else 0.0


def fonte_para_saida(plano, fonte, t):
    """Todos os instantes de saída em que o instante `t` da fonte aparece (um trecho pode se repetir)."""
    return [s["out_ini"] + (t - s["ini"]) for s in mapa(plano) if s["fonte"] == fonte and s["ini"] <= t < s["fim"]]


def saida_para_fonte(plano, t):
    for s in mapa(plano):
        if s["out_ini"] <= t < s["out_fim"]:
            return s["fonte"], s["ini"] + (t - s["out_ini"])
    return None


def eventos_legenda(plano, hold=0.25, minimo=0.6):
    """Frases da legenda projetadas na saída. Cada ocorrência de um trecho gera o seu evento."""
    segs = mapa(plano)
    ev = []
    for fr in plano["legenda"]["frases"]:
        for s in segs:
            if s["fonte"] != fr["fonte"]:
                continue
            ws = [w for w in fr["palavras"] if s["ini"] <= (w["ini"] + w["fim"]) / 2 < s["fim"]]
            if not ws:
                continue
            def proj(t):
                return min(max(s["out_ini"] + (t - s["ini"]), s["out_ini"]), s["out_fim"])
            pals = [(w["t"], proj(w["ini"]), proj(w["fim"])) for w in ws]
            # três relógios separados: fonte (src), relativo ao corte (rel) e saída (ini/fim)
            ev.append({"frase": fr["id"], "ini": pals[0][1], "fim": pals[-1][2], "palavras": pals,
                       "limite": s["out_fim"], "seg": s["id"], "src": ws[0]["ini"],
                       "rel": max(0.0, ws[0]["ini"] - s["ini"])})
    ev.sort(key=lambda e: e["ini"])
    for i, e in enumerate(ev):
        prox = ev[i + 1]["ini"] if i + 1 < len(ev) else float("inf")
        e["fim"] = min(max(e["fim"] + hold, e["ini"] + minimo), prox, e["limite"])
        e["fim"] = max(e["fim"], e["ini"] + 0.04)
    return ev


def _dentro(segs, fonte, w):
    meio = (w["ini"] + w["fim"]) / 2
    return any(s["fonte"] == fonte and s["ini"] <= meio < s["fim"] for s in segs)


def avisos_legenda(plano):
    """Correções feitas à mão que o corte atual não mostra por inteiro. Nada é apagado do plano: se o trecho
    voltar para a linha do tempo, a frase corrigida volta junto. Só avisa para o usuário decidir."""
    segs = plano["segmentos"]
    out = []
    for fr in plano["legenda"]["frases"]:
        if not fr.get("editada"):
            continue
        dentro = sum(1 for w in fr["palavras"] if _dentro(segs, fr["fonte"], w))
        total = len(fr["palavras"])
        if dentro == total:
            continue
        texto = " ".join(w["t"] for w in fr["palavras"])
        if dentro:
            out.append({"frase": fr["id"], "tipo": "cortada", "texto": texto, "dentro": dentro, "total": total,
                        "fonte": fr["fonte"], "ini": fr["palavras"][0]["ini"], "fim": fr["palavras"][-1]["fim"],
                        "msg": f"Sua correção “{texto[:80]}” ficou pela metade: {total - dentro} de {total} palavras "
                               "caíram fora do corte. Ajuste o corte ou edite a frase."})
        else:
            out.append({"frase": fr["id"], "tipo": "fora", "texto": texto, "dentro": 0, "total": total,
                        "fonte": fr["fonte"], "ini": fr["palavras"][0]["ini"], "fim": fr["palavras"][-1]["fim"],
                        "msg": f"Sua correção “{texto[:80]}” está fora dos cortes atuais (guardada; volta se o trecho voltar)."})
    return out


def efeitos_saida(plano):
    """Efeitos sonoros na linha de SAÍDA. Efeito ancorado na fonte acompanha o trecho (cortar/reordenar);
    se o trecho saiu da linha do tempo, o efeito fica guardado mas não toca (e é avisado)."""
    total = duracao_saida(plano)
    out = []
    for e in plano["audio"]["efeitos"]:
        if e["fonte"]:
            ts = fonte_para_saida(plano, e["fonte"], e["t"])
            t = ts[0] if ts else None
        else:
            t = e["t"] if e["t"] < total else None
        out.append({**e, "t_saida": t, "dur_saida": min(e["dur"], total - t) if t is not None else 0.0})
    return out


# ---------------------------------------------------------------- legenda: palavras -> frases
def agrupar(palavras, por_frase=3, max_chars=26, pausa=0.6, fonte=None):
    """Palavras [(ini, fim, t)] -> frases editáveis. Quebra por contagem, tamanho, pausa e pontuação."""
    frases, atual = [], []

    def fecha():
        if atual:
            frases.append({"id": "f" + uuid.uuid4().hex[:10], "fonte": fonte,
                           "palavras": [{"t": t, "ini": round(a, 3), "fim": round(b, 3)} for a, b, t in atual]})
            atual.clear()
    for a, b, t in palavras:
        if atual:
            chars = sum(len(x[2]) + 1 for x in atual) + len(t)
            if (len(atual) >= por_frase or chars > max_chars or a - atual[-1][1] > pausa
                    or atual[-1][2][-1:] in ".?!…"):
                fecha()
        atual.append((a, b, t))
    fecha()
    return frases


def editar_texto_frase(frase, texto):
    """Troca o texto mantendo o tempo: casa as palavras novas com as antigas (alinhar)."""
    novas = texto.split()
    if not novas:
        raise A.Erro("O texto da frase não pode ficar vazio (use 'remover frase').")
    if len(novas) > 60:
        raise A.Erro("Frase longa demais (máximo 60 palavras): divida em duas.")
    tempos = [(w["ini"], w["fim"], w["t"]) for w in frase["palavras"]]
    al = A.alinhar(novas, tempos)
    return {**frase, "editada": True,
            "palavras": [{"t": t, "ini": round(a, 3), "fim": round(max(a, b), 3)} for a, b, t in al]}


def mesclar_frases(frases, fonte, trechos, palavras, por_frase=3):
    """Regera a legenda SÓ dentro de `trechos` [(ini, fim)] da `fonte`, a partir de `palavras` (tempo da fonte).

    - Fala fora dos trechos não entra (o resto da fonte não vira legenda).
    - Frase corrigida à mão (`editada`) é mantida; a fala nova que cairia por cima dela é descartada.
    - Frase automática que cruza a borda do trecho perde só as palavras de dentro (as de fora continuam).
    Devolve (frases, resumo)."""
    def dentro(w):
        meio = (w["ini"] + w["fim"]) / 2 if isinstance(w, dict) else (w[0] + w[1]) / 2
        return any(a <= meio < b for a, b in trechos)

    mantidas, out, trocadas = [], [], 0
    for fr in frases:
        if fr["fonte"] != fonte or not any(dentro(w) for w in fr["palavras"]):
            out.append(fr)
        elif fr.get("editada"):
            mantidas.append(fr)
            out.append(fr)
        else:
            trocadas += 1
            resto = [w for w in fr["palavras"] if not dentro(w)]
            if resto:
                out.append({**fr, "palavras": resto})
    protegido = [(f["palavras"][0]["ini"], f["palavras"][-1]["fim"]) for f in mantidas]
    novas_pal = [tuple(w) for w in palavras if dentro(w)
                 and not any(a - 0.05 <= (w[0] + w[1]) / 2 <= b + 0.05 for a, b in protegido)]
    novas = []
    for a, b in trechos:  # agrupa por trecho: uma frase nunca atravessa dois cortes
        novas += agrupar([w for w in novas_pal if a <= (w[0] + w[1]) / 2 < b], por_frase, fonte=fonte)
    out += novas
    out.sort(key=lambda f: (f["fonte"], f["palavras"][0]["ini"]))
    return out, {"novas": len(novas), "mantidas_editadas": len(mantidas), "substituidas": trocadas,
                 "palavras": len(novas_pal)}


# ---------------------------------------------------------------- ASS
def _cor_ass(hexcor, opacidade=1.0):
    r, g, b = hexcor[1:3], hexcor[3:5], hexcor[5:7]
    alfa = round((1 - opacidade) * 255)
    return f"&H{alfa:02X}{b}{g}{r}".upper()


def _esc(t):
    return t.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def ass_plano(plano, eventos=None, titulos=None):
    """ASS com dois estilos: Legenda (fala) e Titulo (editorial). PlayRes fixo em 1080x1920: a prévia
    em resolução menor fica com o mesmo layout."""
    e = plano["legenda"]["estilo"]
    eventos = eventos_legenda(plano) if eventos is None else eventos
    titulos = plano["titulos"] if titulos is None else titulos
    if e["fundo"]:
        borda, contorno, outline_cor = 3, 10, _cor_ass(e["fundo_cor"], e["fundo_opacidade"])
    else:
        borda, contorno, outline_cor = 1, e["contorno"], _cor_ass(e["contorno_cor"])
    st_leg = (f"Style: Legenda,{e['fonte']},{e['tamanho']},{_cor_ass(e['cor'])},{_cor_ass(e['cor'])},{outline_cor},"
              f"&H80000000,{-1 if e['negrito'] else 0},0,0,0,100,100,0,0,{borda},{contorno:g},0,5,60,60,0,1")
    t = ESTILO_TITULO
    st_tit = "\n".join(
        f"Style: {v['estilo_ass']},{t['fonte']},{t['tamanho']},{_cor_ass(v['cor'])},{_cor_ass(v['cor'])},"
        f"{_cor_ass(v['fundo_cor'], 0.92 if v['caixa'] else 1.0)},&H80000000,-1,0,0,0,100,100,0,0,"
        f"{'3,14' if v['caixa'] else '1,7'},0,5,80,80,0,1" for v in ESTILOS_TITULO.values()
        if v["estilo_ass"] in ("Titulo", "TituloEscuro", "TituloContorno") and v is ESTILOS_TITULO[
            {"Titulo": "claro", "TituloEscuro": "escuro", "TituloContorno": "contorno"}[v["estilo_ass"]]])
    linhas = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {A.W}", f"PlayResY: {A.H}", "WrapStyle: 0", "",
              "[V4+ Styles]",
              "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
              "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
              "Alignment, MarginL, MarginR, MarginV, Encoding", st_leg, st_tit, "",
              "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    pos_leg = f"{{\\an5\\pos({A.W // 2},{round(e['posicao_y'] * A.H)})}}"
    up = (lambda s: s.upper()) if e["maiusculas"] else (lambda s: s)
    destaque = _cor_ass(e["destaque_cor"])
    base = _cor_ass(e["cor"])
    if plano["legenda"]["ativa"]:
        for ev in eventos:
            pals = [up(_esc(p[0])) for p in ev["palavras"]]
            if not e["destaque"]:
                linhas.append(f"Dialogue: 0,{A.ass_time(ev['ini'])},{A.ass_time(ev['fim'])},Legenda,,0,0,0,,"
                              f"{pos_leg}{' '.join(pals)}")
                continue
            # destaque da palavra falada: um evento por palavra, cobrindo até a próxima
            for k, (_, a, _b) in enumerate(ev["palavras"]):
                ini = ev["ini"] if k == 0 else a
                fim = ev["palavras"][k + 1][1] if k + 1 < len(ev["palavras"]) else ev["fim"]
                if fim - ini < 0.01:
                    continue
                txt = " ".join(f"{{\\c{destaque}}}{w}{{\\c{base}}}" if j == k else w for j, w in enumerate(pals))
                linhas.append(f"Dialogue: 0,{A.ass_time(ini)},{A.ass_time(fim)},Legenda,,0,0,0,,{pos_leg}{txt}")
    for tt in titulos:
        linhas.append(dialogo_titulo(tt))
    return "\n".join(linhas) + "\n"


def _cor_tag(hexcor):
    """Cor para tag de sobrescrita do ASS (&HBBGGRR&, sem alfa)."""
    return f"&H{hexcor[5:7]}{hexcor[3:5]}{hexcor[1:3]}&".upper()


def dialogo_titulo(tt):
    """Uma linha Dialogue do texto na tela, com as propriedades do item (preset + sobrescritas)."""
    v = visual_titulo(tt)
    estilo_ass = "Titulo" if v["caixa"] else "TituloContorno"   # caixa x contorno é do estilo (BorderStyle)
    y = round(tt.get("posicao_y", ESTILO_TITULO["posicao_y"]) * A.H)
    tags = ["\\an5", f"\\fs{tt.get('tamanho', ESTILO_TITULO['tamanho'])}", f"\\fn{v['fonte']}", f"\\c{_cor_tag(v['cor'])}",
            f"\\3c{_cor_tag(v['fundo_cor'])}", "\\3a&H14&" if v["caixa"] else "\\3a&H00&", f"\\bord{v['borda']}", "\\b1",
            "\\i1" if v["italico"] else "\\i0"]
    an = v["animacao"]
    if an == "deslizar":
        tags.append(f"\\move({A.W // 2 - 260},{y},{A.W // 2},{y},0,220)\\fad(120,150)")
    else:
        tags.append(f"\\pos({A.W // 2},{y})")
        if an == "fade":
            tags.append("\\fad(250,200)")
        elif an == "pop":
            tags.append("\\fscx130\\fscy130\\t(0,180,\\fscx100\\fscy100)\\fad(60,150)")
    ws = [_esc(w.upper() if v["maiusculas"] else w) for w in tt["texto"].split()]
    dest, base = set(tt.get("destaque") or []), _cor_tag(v["cor"])
    txt = " ".join(f"{{\\c{_cor_tag(v['destaque_cor'])}}}{w}{{\\c{base}}}" if k in dest else w for k, w in enumerate(ws))
    return (f"Dialogue: 1,{A.ass_time(tt['ini'])},{A.ass_time(tt['fim'])},{estilo_ass},,0,0,0,,"
            f"{{{''.join(tags)}}}{txt}")


# ---------------------------------------------------------------- identidade da mídia e transcrição com cache
_hashes = {}


def hash_midia(path):
    st = path.stat()
    chave = (str(path), st.st_size, st.st_mtime_ns)
    if chave not in _hashes:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for bloco in iter(lambda: f.read(1 << 20), b""):
                h.update(bloco)
        _hashes[chave] = h.hexdigest()[:32]
    return _hashes[chave]


def _arq_transcricao(fid):
    return CACHE / "transcricoes" / f"{hash_midia(A.achar(fid))}_{A.MODELO_WHISPER}_pt.json"


def transcricao_em_cache(fid):
    arq = _arq_transcricao(fid)
    return json.loads(arq.read_text(encoding="utf-8")) if arq.exists() else None


def _decodificar(src, ini=None, fim=None, timeout=3600):
    """Áudio mono 16 kHz em float32 (numpy), do trecho [ini, fim) ou da fonte inteira."""
    import numpy as np
    cmd = ["ffmpeg", "-loglevel", "error"] + (["-ss", f"{ini:.3f}"] if ini else []) + (
        ["-to", f"{fim:.3f}"] if fim is not None else []) + ["-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
                                                              "-f", "f32le", "-"]
    r = A.executar(cmd, timeout=timeout, texto=False)
    if r.returncode != 0:
        raise A.Erro("Não consegui ler o áudio para transcrever.")
    return np.frombuffer(r.stdout, np.float32)


def _whisper(tarefa, audio, offset, prog_ini, prog_peso, total_s, rotulo, ao_segmento=None):
    """Roda o Whisper num trecho já decodificado. Devolve (palavras, falas) em tempo da FONTE (offset somado)."""
    A.precisa_contorno_openmp()
    try:
        import numpy as np
        from faster_whisper import WhisperModel
    except ImportError:
        raise A.Erro("Transcrição não instalada (faster-whisper). Rode o iniciar.bat de novo.")
    palavras, falas = [], []
    if audio.size < 16000 * 0.3:
        return palavras, falas
    with A._lock_modelo:
        if A._modelo is None:
            tarefa.avancar(prog_ini, "carregando o modelo de transcrição")
            try:
                A._modelo = WhisperModel(A.MODELO_WHISPER, device="cpu", compute_type="int8")
            except Exception as e:  # noqa: BLE001
                raise A.Erro(f"Não consegui carregar o modelo ({type(e).__name__}). Na 1a vez ele é baixado (~480 MB).")
        # Pipeline em lote (8 trechos, beam 1): medido em 2026-10-12 num áudio de 275 s nesta máquina: 54,7 s contra
        # 142,0 s do modo anterior (beam 5), erro de palavras 0,327 x 0,325 e início das palavras com mediana de 10 ms
        # de diferença (p90 55 ms). O VAD vem embutido no modo em lote.
        if getattr(A, "_lote", None) is None or A._lote.model is not A._modelo:
            from faster_whisper import BatchedInferencePipeline
            A._lote = BatchedInferencePipeline(A._modelo)
        segs, _ = A._lote.transcribe(audio, language="pt", word_timestamps=True, batch_size=8, beam_size=1)
        dur = max(audio.size / 16000, 0.1)
        for s in segs:  # gerador: dá para acompanhar e cancelar entre segmentos
            tarefa.avancar(prog_ini + prog_peso * min(1.0, s.end / dur),
                           f"{rotulo} {int((offset + s.end) // 60)}:{int((offset + s.end) % 60):02d} de "
                           f"{int(total_s // 60)}:{int(total_s % 60):02d}")
            ws = [(round(offset + float(w.start), 3), round(offset + float(w.end), 3), w.word.strip())
                  for w in (s.words or []) if w.word.strip()]
            palavras += ws
            if ao_segmento and ws:
                ao_segmento(palavras)
            if ws:
                trecho = audio[int((ws[0][0] - offset) * 16000):int((ws[-1][1] - offset) * 16000)]
                rms = float(np.sqrt(np.mean(trecho.astype(np.float64) ** 2))) if trecho.size else 0.0
                falas.append({"ini": ws[0][0], "fim": ws[-1][1], "texto": " ".join(w[2] for w in ws),
                              "db": round(20 * math.log10(rms), 1) if rms > 0 else -120.0})
    return palavras, falas


def _gravar_json(arq, dados):
    tmp = arq.with_suffix(".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, arq)


def transcrever_fonte(tarefa, fid, ao_segmento=None):
    """Transcrição da fonte INTEIRA (só quando pedida: descobrir cortes no vídeo todo, análise de referência).
    Tempo da FONTE, com progresso e cancelamento. Cache por conteúdo (sha256), modelo e idioma."""
    arq = _arq_transcricao(fid)
    if arq.exists():
        return {"em_cache": True, **_resumo_transcricao(json.loads(arq.read_text(encoding="utf-8")))}
    src = A.achar(fid)
    info = A.probe(src)
    if not info["tem_audio"]:
        raise A.Erro("Essa fonte não tem áudio para transcrever.")
    tarefa.avancar(0.01, "decodificando o áudio")
    audio = _decodificar(src, timeout=A.prazo_midia(info["duracao"]))
    palavras, falas = _whisper(tarefa, audio, 0.0, 0.05, 0.93, info["duracao"], "transcrevendo", ao_segmento)
    dados = {"versao": 1, "modelo": A.MODELO_WHISPER, "idioma": "pt", "hash": hash_midia(src),
             "duracao": info["duracao"], "palavras": palavras, "falas": falas, "criado_em": time.time()}
    _gravar_json(arq, dados)
    return {"em_cache": False, **_resumo_transcricao(dados)}


# ---------------------------------------------------------------- transcrição por TRECHO (legenda do corte)
def _arq_trechos(fid):
    return CACHE / "transcricoes" / f"{hash_midia(A.achar(fid))}_{A.MODELO_WHISPER}_pt_trechos.json"


def _unir(intervalos):
    out = []
    for a, b in sorted(intervalos):
        if out and a <= out[-1][1] + 0.01:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def _faltando(intervalos, cobertura):
    """Partes de `intervalos` que a `cobertura` não cobre (tolerância de meio quadro)."""
    falta = []
    for a, b in _unir(intervalos):
        cur = a
        for c, d in cobertura:
            if d <= cur or c >= b:
                continue
            if c > cur + 0.02:
                falta.append([cur, c])
            cur = max(cur, d)
        if cur < b - 0.02:
            falta.append([cur, b])
    return [f for f in falta if f[1] - f[0] >= 0.05]


def transcricao_combinada(fid):
    """O que já se sabe da fala da fonte: transcrição completa, ou a soma dos trechos já transcritos.
    {"palavras", "falas", "cobertura": [[ini, fim]], "completa": bool} ou None."""
    d = transcricao_em_cache(fid)
    if d:
        return {**d, "cobertura": [[0.0, d["duracao"]]], "completa": True}
    arq = _arq_trechos(fid)
    if not arq.exists():
        return None
    t = json.loads(arq.read_text(encoding="utf-8"))
    return {**t, "completa": False}


def palavras_no_intervalo(fid, ini, fim):
    d = transcricao_combinada(fid) or {"palavras": []}
    return [w for w in d["palavras"] if ini <= (w[0] + w[1]) / 2 < fim]


def falas_no_intervalo(fid, ini, fim):
    d = transcricao_combinada(fid) or {"falas": []}
    return [f for f in d["falas"] if f["fim"] > ini and f["ini"] < fim]


def cobre(fid, intervalos):
    d = transcricao_combinada(fid)
    return bool(d) and not _faltando(intervalos, d["cobertura"])


def transcrever_trechos(tarefa, fid, intervalos):
    """Transcreve SÓ os trechos pedidos da fonte (tempo da fonte). Reaproveita a transcrição completa ou os
    trechos já feitos; transcreve apenas o que falta. Cada trecho é decodificado com 0,4 s de folga dos dois
    lados (o Whisper erra a palavra cortada na borda) e só ficam as palavras cujo meio cai dentro do trecho."""
    src = A.achar(fid)
    d = transcricao_combinada(fid)
    dur = d["duracao"] if d else None
    if dur is None:
        info = A.probe(src)
        if not info["tem_audio"]:
            raise A.Erro("Essa fonte não tem áudio para transcrever.")
        dur = info["duracao"]
    intervalos = [[max(0.0, a), min(dur, b)] for a, b in intervalos if min(dur, b) - max(0.0, a) > 0.05]
    pedido_s = sum(b - a for a, b in _unir(intervalos))
    falta = _faltando(intervalos, d["cobertura"]) if d else _unir(intervalos)
    if not falta:   # tudo já transcrito (fonte inteira ou trechos anteriores): nada a fazer
        return {"em_cache": True, "transcrito_s": 0.0, "pedido_s": round(pedido_s, 2),
                "palavras": sum(len(palavras_no_intervalo(fid, a, b)) for a, b in _unir(intervalos))}
    info = {"duracao": dur}
    base = d if d and not d["completa"] else {"palavras": [], "falas": [], "cobertura": []}
    total = sum(b - a for a, b in falta)
    feito = 0.0
    for a, b in falta:
        tarefa.checar()
        p0 = 0.02 + 0.96 * feito / total
        tarefa.avancar(p0, f"decodificando {int(a // 60)}:{int(a % 60):02d}")
        ini = max(0.0, a - 0.4)
        audio = _decodificar(src, ini, min(info["duracao"], b + 0.4), timeout=A.prazo_midia(b - a))
        pal, falas = _whisper(tarefa, audio, ini, p0, 0.96 * (b - a) / total, b, "transcrevendo o trecho até")
        pal = [w for w in pal if a <= (w[0] + w[1]) / 2 < b]
        falas = [f for f in falas if f["fim"] > a and f["ini"] < b]
        base["palavras"] = sorted([w for w in base["palavras"] if not a <= (w[0] + w[1]) / 2 < b] + pal)
        base["falas"] = sorted([f for f in base["falas"] if not (a <= f["ini"] < b)] + falas, key=lambda f: f["ini"])
        base["cobertura"] = _unir(base["cobertura"] + [[a, b]])
        feito += b - a
        _gravar_json(_arq_trechos(fid), {"versao": 1, "modelo": A.MODELO_WHISPER, "idioma": "pt",
                                         "hash": hash_midia(src), "duracao": info["duracao"],
                                         "palavras": base["palavras"], "falas": base["falas"],
                                         "cobertura": base["cobertura"], "atualizado_em": time.time()})
    return {"em_cache": False, "transcrito_s": round(total, 2), "pedido_s": round(pedido_s, 2),
            "palavras": sum(len(palavras_no_intervalo(fid, a, b)) for a, b in _unir(intervalos))}


def transcrever_plano(tarefa, plano, segs):
    """Pré-requisito automático das análises: garante a fala de cada segmento em `segs` (e só deles)."""
    por_fonte = {}
    for s in segs:
        if plano["fontes"][s["fonte"]]["tem_audio"]:
            por_fonte.setdefault(s["fonte"], []).append([s["ini"], s["fim"]])
    res = {}
    for k, (fid, ints) in enumerate(por_fonte.items()):
        sub = T.Faixa(tarefa, k / max(1, len(por_fonte)), 1 / max(1, len(por_fonte)))
        res[fid] = transcrever_trechos(sub, fid, ints)
    return res


def _resumo_transcricao(d):
    return {"palavras": len(d["palavras"]), "falas": len(d["falas"]), "duracao": d["duracao"]}


# ---------------------------------------------------------------- render (prévia e exportação usam a mesma função)
def hash_plano(plano, qualidade):
    extras = [e["arquivo"] for e in plano["audio"]["efeitos"] if e["arquivo"]] + (
        [plano["audio"]["musica"]["id"]] if plano["audio"]["musica"]["id"] else [])
    bruto = json.dumps({"p": plano, "q": qualidade,
                        "fontes": {f: hash_midia(A.achar(f)) for f in list(plano["fontes"]) + extras}},
                       sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()[:20]


def _filtro_zoom(n, intensidade, entrando, w, h):
    z = f"(1+{intensidade}*on/{max(n - 1, 1)})" if entrando else f"({1 + intensidade}-{intensidade}*on/{max(n - 1, 1)})"
    x0, x1, y0, y1 = f"(W/2-W/(2*{z}))", f"(W/2+W/(2*{z}))", f"(H/2-H/(2*{z}))", f"(H/2+H/(2*{z}))"
    return (f"perspective=x0='{x0}':y0='{y0}':x1='{x1}':y1='{y0}':x2='{x0}':y2='{y1}':x3='{x1}':y3='{y1}':"
            "interpolation=cubic:eval=frame")


def recortar_plano(plano, a, b):
    """O pedaço [a, b) da SAÍDA como um plano válido: segmentos aparados, texto na tela e efeitos presos à saída
    deslocados. Legenda e efeitos presos à fonte seguem pela projeção. Serve para a prévia curta de um trecho."""
    p = validar_plano(plano)
    out = copy.deepcopy(p)
    segs = []
    for s in mapa(p):
        x0, x1 = max(a, s["out_ini"]), min(b, s["out_fim"])
        if x1 - x0 >= 3 / A.FPS:
            segs.append({"id": s["id"], "fonte": s["fonte"], "ini": s["ini"] + x0 - s["out_ini"],
                         "fim": s["ini"] + x1 - s["out_ini"]})
    if not segs:
        raise A.Erro("Esse trecho está fora da linha do tempo.")
    out["segmentos"] = segs
    out["titulos"] = [{**t, "ini": max(t["ini"], a) - a, "fim": min(t["fim"], b) - a} for t in p["titulos"]
                      if min(t["fim"], b) - max(t["ini"], a) >= 0.2]
    out["audio"]["efeitos"] = [e if e["fonte"] else {**e, "t": e["t"] - a} for e in p["audio"]["efeitos"]
                               if e["fonte"] or a <= e["t"] < b]
    return validar_plano(out)


def render_plano(tarefa, plano, qualidade="previa", nome=None, trecho=None):
    p = validar_plano(plano, exigir_arquivos=True)
    if trecho and qualidade == "previa":
        p = recortar_plano(p, trecho[0], trecho[1])
    if not p["segmentos"]:
        raise A.Erro("Adicione pelo menos um segmento à linha do tempo.")
    q = QUALIDADES[qualidade]
    chave = hash_plano(p, qualidade)
    if qualidade == "previa":
        pronto = CACHE / "previas" / f"{chave}.mp4"
        if pronto.exists():
            return {"arquivo": pronto.name, "url": f"/api/previa/{pronto.name}", "duracao": A.probe(pronto)["duracao"],
                    "avisos": [], "rotulo": q["rotulo"], "em_cache": True}
    segs = mapa(p)
    total = segs[-1]["out_fim"]
    avisos = []
    ef, au = p["efeitos"], p["audio"]
    tem_leg = p["legenda"]["ativa"] and bool(eventos_legenda(p))
    for fonte_usada in ({p["legenda"]["estilo"]["fonte"]} if tem_leg else set()) | {
            visual_titulo(t)["fonte"] for t in p["titulos"]}:
        if troca := A.verificar_fonte(fonte_usada):
            avisos.append(f"A fonte '{fonte_usada}' não está instalada; o texto saiu com '{troca}'.")
    ids = list(p["fontes"]) + ([au["musica"]["id"]] if au["musica"]["id"] else []) + [
        e["arquivo"] for e in au["efeitos"] if e["arquivo"]]
    with A.em_uso(*ids), A.pasta_de_trabalho("ed_") as work:
        clips, feito = [], 0.0
        for k, s in enumerate(segs):
            tarefa.checar()
            src = A.achar(s["fonte"])
            info = p["fontes"][s["fonte"]]
            n, dur = s["quadros"], s["quadros"] / A.FPS
            folga = min(s["ini"], 0.5 / A.FPS)
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{s['ini'] - folga:.6f}", "-t", f"{dur + 0.5:.4f}",
                   "-i", str(src)]
            if not info["tem_audio"]:
                cmd += ["-f", "lavfi", "-t", f"{dur + 0.5:.4f}", "-i", f"anullsrc=r={A.SR}:cl=stereo"]
            fa = "0:a:0" if info["tem_audio"] else "1:a:0"
            corta = round(folga * A.SR) if info["tem_audio"] else 0
            v = [f"setpts=PTS-STARTPTS,crop=w='min(iw,ih*9/16)':h=ih:x='(iw-ow)*{p['quadro']['crop_x']:.4f}':y=0",
                 f"scale={q['w']}:{q['h']}:flags=lanczos,setsar=1,fps={A.FPS}",
                 "tpad=stop_mode=clone:stop_duration=1", f"trim=end_frame={n}"]
            if ef["zoom"]["ativo"] and ef["zoom"]["intensidade"] > 0:
                v.append(_filtro_zoom(n, ef["zoom"]["intensidade"], k % 2 == 0, q["w"], q["h"]))
            a = [f"aresample={A.SR}", "asetpts=PTS-STARTPTS", f"atrim=start_sample={corta}", "asetpts=PTS-STARTPTS",
                 "apad", f"atrim=end_sample={n * A.SR // A.FPS}", "afade=t=in:d=0.01",
                 f"afade=t=out:st={max(0.0, dur - 0.01):.4f}:d=0.01"]  # sem estalo na emenda
            if ef["transicao"]["tipo"] == "fade":
                d = min(ef["transicao"]["duracao"], dur / 3)
                if k > 0:
                    v.append(f"fade=t=in:st=0:d={d:.3f}")
                    a.append(f"afade=t=in:d={d:.3f}")
                if k < len(segs) - 1:
                    v.append(f"fade=t=out:st={dur - d:.3f}:d={d:.3f}")
                    a.append(f"afade=t=out:st={dur - d:.3f}:d={d:.3f}")
            v.append("format=yuv420p")
            cmd += ["-filter_complex", f"[0:v:0]{','.join(v)}[v];[{fa}]{','.join(a)}[a]", "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-preset", "veryfast" if qualidade == "final" else "ultrafast",
                    "-crf", "18" if qualidade == "final" else "28", "-c:a", "pcm_s16le", "-ar", str(A.SR), "-ac", "2",
                    f"c{k:03d}.mkv"]
            T.ffmpeg(cmd, tarefa, dur, ini=0.75 * feito / total, peso=0.75 * dur / total, cwd=work,
                     etapa=f"preparando segmento {k + 1} de {len(segs)}")
            clips.append(f"c{k:03d}.mkv")
            feito += dur
        (work / "lista.txt").write_text("".join(f"file '{c}'\n" for c in clips), encoding="utf-8")
        A.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "lista.txt", "-c", "copy",
               "base.mkv"], cwd=work, timeout=A.prazo_midia(len(clips)))

        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", "base.mkv"]
        # realce de voz: corta o grave abaixo da voz, reduz ruído de fundo e comprime de leve
        realce = "highpass=f=80,afftdn=nf=-25,acompressor=threshold=0.125:ratio=3:attack=10:release=200," \
            if au["realce_voz"] else ""
        fa = [f"[0:a]{realce}volume={au['volume_original']:.3f}[orig]"]
        mix = "[orig]"
        if au["musica"]["id"]:
            cmd += ["-stream_loop", "-1", "-i", str(A.achar(au["musica"]["id"]))]
            fa.append(f"[1:a]aresample={A.SR},volume={au['musica']['volume']:.3f}[mus]")
            if au["musica"]["abaixar_na_fala"]:
                fa.append("[orig]asplit=2[o1][o2]")
                fa.append("[mus][o2]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[musd]")
                fa.append("[o1][musd]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]")
            else:
                fa.append("[orig][mus]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]")
            mix = "[mix]"
        efs = [e for e in efeitos_saida(p) if e["t_saida"] is not None and e["dur_saida"] > 0.05]
        sumidos = len(p["audio"]["efeitos"]) - len(efs)
        if sumidos:
            avisos.append(f"{sumidos} efeito(s) sonoro(s) ficaram fora da linha do tempo e não tocam.")
        idx = 2 if au["musica"]["id"] else 1
        rot = []
        for k, e in enumerate(efs):
            d = e["dur_saida"]
            if e["som"] == "arquivo":
                cmd += ["-i", str(A.achar(e["arquivo"]))]
            else:
                cmd += ["-f", "lavfi", "-i", expr_som(e["som"], e["dur"])]
            ms = round(e["t_saida"] * 1000)
            fa.append(f"[{idx}:a]aresample={A.SR},aformat=channel_layouts=stereo,atrim=0:{d:.3f},"
                      f"afade=t=out:st={max(0.0, d - min(0.15, d / 3)):.3f}:d={min(0.15, d / 3):.3f},"
                      f"volume={e['volume']:.3f},adelay={ms}|{ms}[ef{k}]")
            rot.append(f"[ef{k}]")
            idx += 1
        if rot:
            fa.append(f"{mix}{''.join(rot)}amix=inputs={len(rot) + 1}:duration=first:dropout_transition=0:normalize=0[mixe]")
            mix = "[mixe]"
        fa.append(f"{mix}loudnorm=I=-14:TP=-1.5:LRA=11[aout]" if au["normalizar"] else f"{mix}anull[aout]")
        if tem_leg or p["titulos"]:
            (work / "leg.ass").write_text(ass_plano(p), encoding="utf-8")
            fv = "[0:v]ass=leg.ass[vout]"
        else:
            fv = "[0:v]null[vout]"
        saida = f"{(nome or 'edicao')}.mp4"
        cmd += ["-filter_complex", ";".join([fv] + fa), "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.3f}",
                "-c:v", "libx264", "-preset", q["preset"], "-crf", str(q["crf"]), "-c:a", "aac", "-b:a", q["abr"],
                "-ar", str(A.SR), "-movflags", "+faststart", saida]
        T.ffmpeg(cmd, tarefa, total, ini=0.75, peso=0.24, cwd=work, etapa="montando o vídeo final")
        dur_final = A.probe(work / saida)["duracao"]
        if qualidade == "previa":
            destino = CACHE / "previas" / f"{chave}.mp4"
            os.replace(work / saida, destino)
            return {"arquivo": destino.name, "url": f"/api/previa/{destino.name}", "duracao": dur_final,
                    "avisos": avisos, "rotulo": q["rotulo"], "em_cache": False}
        if dur_final < A.MIN_ELEGIVEL:
            avisos.insert(0, f"{dur_final:.0f}s: abaixo de 1 minuto, não conta para o Creator Rewards "
                             "[DADO, fonte secundária].")
        nome_final = A.nome_saida(nome or "edicao")
        (work / saida).rename(work / f"{nome_final}.mp4")
        registro = {"arquivo": f"{nome_final}.mp4", "criado_em": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "plano_hash": chave, "plano": p, "duracao_s": round(dur_final, 2), "avisos": avisos,
                    "aviso": "O app não verifica direitos autorais da mídia de origem."}
        A.salvar_par(work / f"{nome_final}.mp4", registro)
    return {"arquivo": f"{nome_final}.mp4", "url": f"/saida/{nome_final}.mp4", "duracao": dur_final,
            "avisos": avisos, "rotulo": q["rotulo"], "em_cache": False}


# ---------------------------------------------------------------- miniaturas de estilo (renderizadas pelo libass)
def miniatura(plano, estilo, texto=None):
    p = validar_plano(plano, exigir_arquivos=True)
    p["legenda"]["estilo"] = validar_estilo(estilo)
    p["legenda"]["ativa"] = True
    evs = eventos_legenda(p)
    palavras = [w for w, _, _ in evs[0]["palavras"]] if evs else (texto or "Legenda de exemplo aqui").split()
    t_out = evs[0]["ini"] + 0.01 if evs else 0.0
    origem = saida_para_fonte(p, t_out) if p["segmentos"] else None
    # Quadro estático: a frase aparece no instante 0; com destaque, a 2a palavra é a "falada".
    ativa = 1 if p["legenda"]["estilo"]["destaque"] and len(palavras) > 1 else 0
    ev_ass = [{"ini": 0.0, "fim": 10.0,
               "palavras": [(w, 0.0 if j <= ativa else 5.0 + j, 0.0 if j <= ativa else 5.5 + j)
                            for j, w in enumerate(palavras)]}]
    bruto = json.dumps([origem and (hash_midia(A.achar(origem[0])), round(origem[1], 2)), p["legenda"]["estilo"],
                        palavras, p["quadro"]], sort_keys=True, ensure_ascii=False)
    arq = CACHE / "miniaturas" / f"{hashlib.sha256(bruto.encode()).hexdigest()[:20]}.png"
    if arq.exists():
        return arq.name
    with A.pasta_de_trabalho("mini_") as work:
        (work / "m.ass").write_text(ass_plano(p, eventos=ev_ass, titulos=[]), encoding="utf-8")
        if origem:
            entrada = ["-ss", f"{origem[1]:.3f}", "-i", str(A.achar(origem[0]))]
            vf = (f"crop=w='min(iw,ih*9/16)':h=ih:x='(iw-ow)*{p['quadro']['crop_x']:.4f}':y=0,"
                  "scale=360:640:flags=lanczos,setsar=1,ass=m.ass")
        else:
            entrada = ["-f", "lavfi", "-i", "color=c=0x202630:s=360x640:d=1"]
            vf = "ass=m.ass"
        A.run(["ffmpeg", "-y", "-loglevel", "error", *entrada, "-frames:v", "1", "-vf", vf, "m.png"], cwd=work, timeout=60)
        os.replace(work / "m.png", arq)
    return arq.name


def miniatura_titulo(plano, titulo, t_fonte=None, seg_id=None):
    """PNG 360x640 de um quadro do corte com a legenda daquele instante e o texto na tela aplicado (libass)."""
    p = validar_plano(plano, exigir_arquivos=True)
    tt = validar_plano({**p, "titulos": [{**titulo, "id": "previa", "ini": 0, "fim": 10}]})["titulos"][0]
    segs = mapa(p)
    s = next((x for x in segs if x["id"] == seg_id), segs[0] if segs else None)
    if not s:
        raise A.Erro("Adicione um corte para ver a miniatura.")
    t = min(max(s["ini"], t_fonte if t_fonte is not None else s["ini"] + 0.6), s["fim"] - 0.05)
    t_out = s["out_ini"] + (t - s["ini"])
    evs = [{**e, "ini": 0.0, "fim": 10.0} for e in eventos_legenda(p) if e["ini"] <= t_out < e["fim"]][:1]
    tt = {**tt, "animacao": "nenhuma", "ini": 0.0, "fim": 10.0}
    bruto = json.dumps([hash_midia(A.achar(s["fonte"])), round(t, 2), tt, visual_titulo(tt), p["quadro"],
                        p["legenda"]["estilo"], [e["palavras"] for e in evs]], sort_keys=True, ensure_ascii=False)
    arq = CACHE / "miniaturas" / f"{hashlib.sha256(bruto.encode()).hexdigest()[:20]}.png"
    if arq.exists():
        return arq.name
    ev_ass = [{"ini": 0.0, "fim": 10.0, "palavras": [(w, 0.0, 0.0) for w, _, _ in e["palavras"]]} for e in evs]
    q = {**p, "legenda": {**p["legenda"], "estilo": {**p["legenda"]["estilo"], "destaque": False}}}
    with A.pasta_de_trabalho("minit_") as work:
        (work / "m.ass").write_text(ass_plano(q, eventos=ev_ass, titulos=[tt]), encoding="utf-8")
        vf = (f"crop=w='min(iw,ih*9/16)':h=ih:x='(iw-ow)*{p['quadro']['crop_x']:.4f}':y=0,"
              "scale=360:640:flags=lanczos,setsar=1,ass=m.ass")
        A.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(A.achar(s["fonte"])), "-frames:v", "1",
               "-vf", vf, "m.png"], cwd=work, timeout=60)
        os.replace(work / "m.png", arq)
    return arq.name


def area_livre(plano, seg_id=None):
    """Altura (0..1) para o texto na tela: a faixa com MENOS detalhe na imagem do corte (menos chance de cobrir ação),
    longe da legenda e fora das áreas do TikTok que costumam ter interface (topo e rodapé). Rostos não são detectados."""
    import numpy as np
    p = validar_plano(plano, exigir_arquivos=True)
    segs = mapa(p)
    s = next((x for x in segs if x["id"] == seg_id), segs[0] if segs else None)
    if not s:
        return 0.2, "sem corte: posição padrão"
    energia = np.zeros(160)
    for f in (0.2, 0.5, 0.8):
        t = s["ini"] + (s["fim"] - s["ini"]) * f
        r = A.executar(["ffmpeg", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(A.achar(s["fonte"])), "-frames:v", "1",
                        "-vf", f"crop=w='min(iw,ih*9/16)':h=ih:x='(iw-ow)*{p['quadro']['crop_x']:.4f}':y=0,scale=90:160,format=gray",
                        "-f", "rawvideo", "-"], timeout=60, texto=False)
        if r.returncode == 0 and len(r.stdout) == 90 * 160:
            img = np.frombuffer(r.stdout, np.uint8).reshape(160, 90).astype(np.float32)
            energia += np.abs(np.diff(img, axis=1)).sum(axis=1) + np.r_[np.abs(np.diff(img, axis=0)).sum(axis=1), 0]
    leg_y = p["legenda"]["estilo"]["posicao_y"] if p["legenda"]["ativa"] and p["legenda"]["frases"] else None
    opcoes = [y for y in (0.14, 0.2, 0.26, 0.32, 0.4, 0.6, 0.66) if leg_y is None or abs(y - leg_y) >= 0.14]
    if not energia.any():
        y = opcoes[0]
        return y, "sem quadro legível: posição padrão no alto, longe da legenda"
    custo = {y: float(energia[int((y - 0.05) * 160):int((y + 0.05) * 160)].mean()) for y in opcoes}
    y = min(custo, key=custo.get)
    return y, (f"faixa com menos detalhe na imagem do corte (altura {round(y * 100)}%)"
               + (f", longe da legenda ({round(leg_y * 100)}%)" if leg_y is not None else "")
               + ", fora do topo e do rodapé onde o TikTok põe interface; rostos e ação não são detectados")


# ---------------------------------------------------------------- preferências de estilo (salvas no PC)
def _arq_pref():
    return EDICOES / "preferencias.json"


def ler_preferencias():
    try:
        d = json.loads(_arq_pref().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def gravar_preferencias(d):
    if not isinstance(d, dict):
        raise A.Erro("Preferências precisam ser um objeto.")
    out = {}
    g = d.get("gancho")
    if g is not None:   # valida como um texto na tela de verdade: só propriedades conhecidas
        t = validar_plano({**plano_vazio(), "titulos": [{"id": "p", "texto": "x", "ini": 0, "fim": 1, **{
            k: g[k] for k in ("estilo", "posicao_y", "tamanho", "destaque_cor", *CAMPOS_VISUAIS) if k in g}}]})["titulos"][0]
        out["gancho"] = {k: t[k] for k in ("estilo", "tamanho", *CAMPOS_VISUAIS) if k in t}
        if g.get("posicao") in ("auto", "fixa"):
            out["gancho"]["posicao"] = g["posicao"]
            if g["posicao"] == "fixa":
                out["gancho"]["posicao_y"] = t["posicao_y"]
    _gravar_json(_arq_pref(), out)
    return out


# ---------------------------------------------------------------- projetos
def _arq_projeto(pid):
    if not re.fullmatch(r"[a-f0-9]{12}", pid or ""):
        raise A.Erro("Projeto inválido.", 404)
    return EDICOES / f"{pid}.json"


def validar_projeto(pr):
    if not isinstance(pr, dict):
        raise A.Erro("Projeto precisa ser um objeto JSON.")
    nome = _txt(pr.get("nome", "Sem nome"), "nome", 80)
    alts = pr.get("alternativas", [])
    if not isinstance(alts, list) or len(alts) > 20:
        raise A.Erro("Até 20 versões alternativas por projeto.")
    versoes = []
    for i, a in enumerate(alts):
        if not isinstance(a, dict):
            _erro(f"alternativas[{i}]", "precisa ser objeto")
        versoes.append({"id": _id(a.get("id"), f"alternativas[{i}].id"),
                        "nome": _txt(a.get("nome", "Alternativa"), f"alternativas[{i}].nome", 80),
                        "origem": _txt(a.get("origem", "usuario"), f"alternativas[{i}].origem", 300),
                        "plano": validar_plano(a.get("plano"))})
    pub = pr.get("publicacao") or {}
    if not isinstance(pub, dict):
        raise A.Erro("publicacao precisa ser um objeto.")
    publicacao = {"texto": _txt(pub.get("texto", ""), "publicacao.texto", 4000, vazio=True)}
    return {"nome": nome, "plano": validar_plano(pr.get("plano")), "alternativas": versoes, "publicacao": publicacao}


def salvar_projeto(pid, pr):
    dados = validar_projeto(pr)
    dados.update({"versao": 1, "id": pid, "atualizado_em": time.strftime("%Y-%m-%d %H:%M:%S")})
    arq = _arq_projeto(pid)
    tmp = arq.with_suffix(".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, arq)  # gravação atômica: um projeto nunca fica pela metade
    return dados


def carregar_projeto(pid):
    arq = _arq_projeto(pid)
    if not arq.exists():
        raise A.Erro("Projeto não encontrado.", 404)
    try:
        dados = json.loads(arq.read_text(encoding="utf-8"))
    except ValueError:
        raise A.Erro("O arquivo do projeto está corrompido (JSON inválido).")
    validado = validar_projeto(dados)
    faltando = [f for f in validado["plano"]["fontes"] if not list(A.UP.glob(f"{f}.*"))]
    return {**dados, **validado, "id": pid, "fontes_faltando": faltando}


# ---------------------------------------------------------------- rotas
@bp.get("/editor")
def pagina_editor():
    return send_file(A.ROOT / "editor.html")


@bp.get("/editor.js")
def script_editor():
    return send_file(A.ROOT / "editor.js", mimetype="text/javascript")


@bp.get("/api/presets")
def rota_presets():
    return jsonify({"presets": PRESETS, "titulo": ESTILO_TITULO, "estilos_titulo": ESTILOS_TITULO, "qualidades": {k: v["rotulo"] for k, v in QUALIDADES.items()}})


@bp.post("/api/plano/validar")
def rota_validar():
    p = validar_plano(A.corpo_json().get("plano"))
    return jsonify({"plano": p, "duracao_saida": duracao_saida(p), "segmentos": mapa(p),
                    "eventos": [{**{k: e[k] for k in ("frase", "ini", "fim", "seg", "src", "rel")},
                                "palavras": [[w, round(a, 3), round(b, 3)] for w, a, b in e["palavras"]]}
                               for e in eventos_legenda(p)],
                    "avisos_legenda": avisos_legenda(p),
                    "efeitos": [{k: e[k] for k in ("id", "t_saida", "dur_saida")} for e in efeitos_saida(p)]})


def ler_trechos(v, plano=None):
    """[{fonte, ini, fim}] vindos do navegador. Com `plano`, a fonte precisa estar nele."""
    if not isinstance(v, list) or not 1 <= len(v) <= 500:
        raise A.Erro("Informe de 1 a 500 trechos {fonte, ini, fim}.")
    out = []
    for i, t in enumerate(v):
        if not isinstance(t, dict):
            _erro(f"trechos[{i}]", "precisa ser objeto")
        fonte = t.get("fonte")
        A.achar(fonte)
        if plano is not None and fonte not in plano["fontes"]:
            _erro(f"trechos[{i}].fonte", "fonte não está no plano")
        ini = _num(t.get("ini"), f"trechos[{i}].ini", 0, 6 * 3600)
        fim = _num(t.get("fim"), f"trechos[{i}].fim", ini + 0.05, 6 * 3600 + 1)
        out.append({"fonte": fonte, "ini": ini, "fim": fim})
    return out


def _transcrever_trechos_tarefa(tarefa, trechos):
    por_fonte = {}
    for t in trechos:
        por_fonte.setdefault(t["fonte"], []).append([t["ini"], t["fim"]])
    res = {}
    for k, (fid, ints) in enumerate(por_fonte.items()):
        res[fid] = transcrever_trechos(T.Faixa(tarefa, k / len(por_fonte), 1 / len(por_fonte)), fid, ints)
    return {"fontes": res, "em_cache": all(r["em_cache"] for r in res.values()),
            "palavras": sum(r["palavras"] for r in res.values()),
            "transcrito_s": round(sum(r["transcrito_s"] for r in res.values()), 2),
            "pedido_s": round(sum(r["pedido_s"] for r in res.values()), 2)}


@bp.post("/api/transcricao")
def rota_transcrever_fonte():
    """Com `trechos`: transcreve só esses intervalos (legenda do corte). Sem `trechos`: a fonte inteira
    (só para quem pediu explicitamente, como 'descobrir cortes no vídeo inteiro')."""
    j = A.corpo_json()
    if j.get("trechos") is not None:
        trechos = ler_trechos(j["trechos"])
        n = len(trechos)
        t = T.iniciar("transcricao", f"Transcrevendo {n} trecho(s)", _transcrever_trechos_tarefa, trechos)
        return jsonify(t.json()), 202
    fid = j.get("fonte")
    A.achar(fid)
    t = T.iniciar("transcricao", "Transcrevendo a fonte inteira", transcrever_fonte, fid)
    return jsonify(t.json()), 202


@bp.post("/api/legenda/gerar")
def rota_legenda_gerar():
    """Gera as frases da legenda SÓ dos trechos pedidos (corte ativo ou todos os cortes incluídos), a partir da
    transcrição em cache. Frases corrigidas à mão ficam; o resto da fonte não vira legenda."""
    j = A.corpo_json()
    p = validar_plano(j.get("plano"))
    trechos = ler_trechos(j.get("trechos"), p)
    n = int(_num(j.get("palavras_por_frase", p["legenda"]["estilo"]["palavras_por_frase"]), "palavras_por_frase", 1, 12))
    frases, resumo = p["legenda"]["frases"], {"novas": 0, "mantidas_editadas": 0, "substituidas": 0, "palavras": 0}
    for fid in dict.fromkeys(t["fonte"] for t in trechos):
        ints = [(t["ini"], t["fim"]) for t in trechos if t["fonte"] == fid]
        if not cobre(fid, ints):
            raise A.Erro("Esse trecho ainda não foi transcrito. Transcreva o corte primeiro.")
        pal = [w for a, b in ints for w in palavras_no_intervalo(fid, a, b)]
        frases, r = mesclar_frases(frases, fid, ints, pal, n)
        resumo = {k: resumo[k] + r[k] for k in resumo}
    p["legenda"]["frases"] = frases
    p = validar_plano(p)
    return jsonify({"frases": p["legenda"]["frases"], "resumo": resumo})


@bp.get("/api/efeitos")
def rota_efeitos():
    return jsonify({k: {"nome": v["nome"], "dur": v["dur"], "uso": v["uso"]} for k, v in SONS.items()})


@bp.get("/api/efeitos/som/<som>")
def rota_som(som):
    """Prévia do som sozinho (para ouvir antes de aplicar). Gerado na hora pelo ffmpeg e guardado em cache."""
    if som not in SONS:
        abort(404)
    dur = A.num(request.args.get("dur"), 0.1, 10, SONS[som]["dur"])
    pasta = CACHE / "sons"
    pasta.mkdir(parents=True, exist_ok=True)
    arq = pasta / f"{som}_{round(dur * 1000)}.wav"
    if not arq.exists():
        tmp = pasta / f"{som}_{uuid.uuid4().hex[:6]}.tmp.wav"
        A.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", expr_som(som, dur), "-ac", "2", str(tmp)],
              timeout=60)
        os.replace(tmp, arq)
    return send_file(arq, mimetype="audio/wav")


@bp.get("/api/transcricao/<fid>")
def rota_transcricao(fid):
    d = transcricao_em_cache(fid)
    if not d:
        raise A.Erro("Essa fonte ainda não foi transcrita.", 404)
    return jsonify(d)


@bp.post("/api/legenda/frases")
def rota_frases():
    j = A.corpo_json()
    d = transcricao_em_cache(j.get("fonte"))
    if not d:
        raise A.Erro("Transcreva a fonte antes de gerar as frases.")
    n = int(_num(j.get("palavras_por_frase", 3), "palavras_por_frase", 1, 12))
    return jsonify({"frases": agrupar(d["palavras"], n, fonte=j.get("fonte"))})


@bp.post("/api/legenda/editar")
def rota_editar_frase():
    j = A.corpo_json()
    fr = j.get("frase")
    if not isinstance(fr, dict) or not isinstance(fr.get("palavras"), list) or not fr["palavras"]:
        raise A.Erro("Frase inválida.")
    return jsonify({"frase": editar_texto_frase(fr, _txt(j.get("texto"), "texto", 600))})


@bp.post("/api/render")
def rota_render():
    j = A.corpo_json()
    qualidade = j.get("qualidade", "previa")
    if qualidade not in QUALIDADES:
        raise A.Erro("qualidade precisa ser 'previa' ou 'final'.")
    p = validar_plano(j.get("plano"), exigir_arquivos=True)  # erro de plano aparece já, não dentro da tarefa
    nome = A.slug(j.get("nome") or "edicao")
    trecho = None
    if j.get("trecho") is not None:   # prévia curta de um trecho da saída [a, b], até 30 s
        tr = j["trecho"]
        if qualidade != "previa" or not isinstance(tr, list) or len(tr) != 2:
            raise A.Erro("trecho precisa ser [inicio, fim] em segundos da saída, só na prévia.")
        a = _num(tr[0], "trecho[0]", 0, 6 * 3600)
        b = _num(tr[1], "trecho[1]", a + 0.5, a + 30)
        recortar_plano(p, a, b)
        trecho = (a, b)
    rot = QUALIDADES[qualidade]["rotulo"] + (f" · trecho {trecho[0]:.1f}–{trecho[1]:.1f}s" if trecho else "")
    t = T.iniciar("render", rot, render_plano, j["plano"], qualidade, nome, trecho)
    return jsonify(t.json()), 202


@bp.post("/api/miniaturas")
def rota_miniaturas():
    j = A.corpo_json()
    estilos = j.get("estilos") or {k: v for k, v in PRESETS.items()}
    if not isinstance(estilos, dict) or len(estilos) > 8:
        raise A.Erro("estilos precisa ser objeto com até 8 estilos.")
    return jsonify({k: f"/api/miniatura/{miniatura(j.get('plano'), v)}" for k, v in estilos.items()})


@bp.post("/api/titulos/miniatura")
def rota_miniatura_titulo():
    j = A.corpo_json()
    if not isinstance(j.get("titulo"), dict):
        raise A.Erro("Informe o texto na tela (titulo).")
    t = j.get("t_fonte")
    return jsonify({"url": f"/api/miniatura/{miniatura_titulo(j.get('plano'), j['titulo'], t if isinstance(t, (int, float)) else None, j.get('segmento'))}"})


@bp.get("/api/preferencias")
def rota_pref():
    return jsonify(ler_preferencias())


@bp.put("/api/preferencias")
def rota_pref_salvar():
    return jsonify(gravar_preferencias(A.corpo_json()))


@bp.get("/api/miniatura/<nome>")
def rota_miniatura(nome):
    if not re.fullmatch(r"[a-f0-9]{20}\.png", nome) or not (CACHE / "miniaturas" / nome).is_file():
        abort(404)
    return send_file(CACHE / "miniaturas" / nome, mimetype="image/png")


@bp.get("/api/previa/<nome>")
def rota_previa(nome):
    if not re.fullmatch(r"[a-f0-9]{20}\.mp4", nome) or not (CACHE / "previas" / nome).is_file():
        abort(404)
    return send_file(CACHE / "previas" / nome, conditional=True)


@bp.get("/api/tarefas/<tid>")
def rota_tarefa(tid):
    return jsonify(T.obter(tid).json())


@bp.post("/api/tarefas/<tid>/cancelar")
def rota_cancelar(tid):
    t = T.obter(tid)
    t.cancelar()
    return jsonify(t.json())


@bp.get("/api/projetos")
def rota_projetos():
    out = []
    for arq in sorted(EDICOES.glob("*.json"), key=lambda a: a.stat().st_mtime, reverse=True):
        try:
            d = json.loads(arq.read_text(encoding="utf-8"))
            out.append({"id": arq.stem, "nome": d.get("nome"), "atualizado_em": d.get("atualizado_em"),
                        "segmentos": len(d.get("plano", {}).get("segmentos", []))})
        except (ValueError, OSError):
            out.append({"id": arq.stem, "nome": "(arquivo corrompido)", "atualizado_em": None, "segmentos": 0})
    return jsonify(out)


@bp.post("/api/projetos")
def rota_novo_projeto():
    j = A.corpo_json()
    pid = uuid.uuid4().hex[:12]
    return jsonify(salvar_projeto(pid, {"nome": j.get("nome") or "Novo projeto", "plano": j.get("plano") or plano_vazio(),
                                        "alternativas": j.get("alternativas", []),
                                        "publicacao": j.get("publicacao") or {}})), 201


@bp.get("/api/projetos/<pid>")
def rota_abrir_projeto(pid):
    return jsonify(carregar_projeto(pid))


@bp.put("/api/projetos/<pid>")
def rota_salvar_projeto(pid):
    if not _arq_projeto(pid).exists():
        raise A.Erro("Projeto não encontrado.", 404)
    return jsonify(salvar_projeto(pid, A.corpo_json()))
