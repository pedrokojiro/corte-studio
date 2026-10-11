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
            "audio": {"volume_original": 1.0, "normalizar": True, "musica": {"id": None, "volume": 0.12, "abaixar_na_fala": True}}}


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
        out["legenda"]["frases"].append({"id": fid, "fonte": fonte, "palavras": lista})

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
        out["titulos"].append({"id": _id(t.get("id"), c + ".id"), "texto": _txt(t.get("texto"), c + ".texto", 90),
                               "ini": ini, "fim": fim, "origem": origem})

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
    out["audio"] = {"volume_original": _num(au.get("volume_original", 1.0), "audio.volume_original", 0, 2),
                    "normalizar": _bool(au.get("normalizar", True), "audio.normalizar"),
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
            ev.append({"frase": fr["id"], "ini": pals[0][1], "fim": pals[-1][2], "palavras": pals,
                       "limite": s["out_fim"]})
    ev.sort(key=lambda e: e["ini"])
    for i, e in enumerate(ev):
        prox = ev[i + 1]["ini"] if i + 1 < len(ev) else float("inf")
        e["fim"] = min(max(e["fim"] + hold, e["ini"] + minimo), prox, e["limite"])
        e["fim"] = max(e["fim"], e["ini"] + 0.04)
    return ev


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
    return {**frase, "palavras": [{"t": t, "ini": round(a, 3), "fim": round(max(a, b), 3)} for a, b, t in al]}


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
    st_tit = (f"Style: Titulo,{t['fonte']},{t['tamanho']},{_cor_ass(t['cor'])},{_cor_ass(t['cor'])},"
              f"{_cor_ass(t['fundo_cor'], 0.92)},&H80000000,-1,0,0,0,100,100,0,0,3,14,0,5,80,80,0,1")
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
    pos_tit = f"{{\\an5\\pos({A.W // 2},{round(ESTILO_TITULO['posicao_y'] * A.H)})}}"
    for tt in titulos:
        linhas.append(f"Dialogue: 1,{A.ass_time(tt['ini'])},{A.ass_time(tt['fim'])},Titulo,,0,0,0,,"
                      f"{pos_tit}{_esc(tt['texto'])}")
    return "\n".join(linhas) + "\n"


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


def transcrever_fonte(tarefa, fid):
    """Transcrição da fonte inteira, em tempo da FONTE, com progresso e cancelamento. Cache por conteúdo
    (sha256), modelo e idioma: o mesmo arquivo enviado de novo não é transcrito outra vez."""
    arq = _arq_transcricao(fid)
    if arq.exists():
        return {"em_cache": True, **_resumo_transcricao(json.loads(arq.read_text(encoding="utf-8")))}
    src = A.achar(fid)
    info = A.probe(src)
    if not info["tem_audio"]:
        raise A.Erro("Essa fonte não tem áudio para transcrever.")
    A.precisa_contorno_openmp()
    try:
        import numpy as np
        from faster_whisper import WhisperModel
    except ImportError:
        raise A.Erro("Transcrição não instalada (faster-whisper). Rode o iniciar.bat de novo.")
    tarefa.avancar(0.01, "decodificando o áudio")
    r = A.executar(["ffmpeg", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
                    "-f", "f32le", "-"], timeout=A.prazo_midia(info["duracao"]), texto=False)
    if r.returncode != 0:
        raise A.Erro("Não consegui ler o áudio para transcrever.")
    audio = np.frombuffer(r.stdout, np.float32)
    tarefa.avancar(0.05, "carregando o modelo de transcrição")
    with A._lock_modelo:
        if A._modelo is None:
            try:
                A._modelo = WhisperModel(A.MODELO_WHISPER, device="cpu", compute_type="int8")
            except Exception as e:  # noqa: BLE001
                raise A.Erro(f"Não consegui carregar o modelo ({type(e).__name__}). Na 1a vez ele é baixado (~480 MB).")
        segs, _ = A._modelo.transcribe(audio, language="pt", word_timestamps=True, vad_filter=True)
        palavras, falas = [], []
        for s in segs:  # gerador: dá para acompanhar e cancelar entre segmentos
            tarefa.avancar(0.05 + 0.93 * min(1.0, s.end / max(info["duracao"], 0.1)),
                           f"transcrevendo {int(s.end // 60)}:{int(s.end % 60):02d} de "
                           f"{int(info['duracao'] // 60)}:{int(info['duracao'] % 60):02d}")
            ws = [(round(float(w.start), 3), round(float(w.end), 3), w.word.strip()) for w in (s.words or [])
                  if w.word.strip()]
            palavras += ws
            if ws:
                trecho = audio[int(ws[0][0] * 16000):int(ws[-1][1] * 16000)]
                rms = float(np.sqrt(np.mean(trecho.astype(np.float64) ** 2))) if trecho.size else 0.0
                falas.append({"ini": ws[0][0], "fim": ws[-1][1], "texto": " ".join(w[2] for w in ws),
                              "db": round(20 * math.log10(rms), 1) if rms > 0 else -120.0})
    dados = {"versao": 1, "modelo": A.MODELO_WHISPER, "idioma": "pt", "hash": hash_midia(src),
             "duracao": info["duracao"], "palavras": palavras, "falas": falas, "criado_em": time.time()}
    tmp = arq.with_suffix(".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, arq)
    return {"em_cache": False, **_resumo_transcricao(dados)}


def _resumo_transcricao(d):
    return {"palavras": len(d["palavras"]), "falas": len(d["falas"]), "duracao": d["duracao"]}


# ---------------------------------------------------------------- render (prévia e exportação usam a mesma função)
def hash_plano(plano, qualidade):
    bruto = json.dumps({"p": plano, "q": qualidade, "fontes": {f: hash_midia(A.achar(f)) for f in plano["fontes"]}},
                       sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()[:20]


def _filtro_zoom(n, intensidade, entrando, w, h):
    z = f"(1+{intensidade}*on/{max(n - 1, 1)})" if entrando else f"({1 + intensidade}-{intensidade}*on/{max(n - 1, 1)})"
    x0, x1, y0, y1 = f"(W/2-W/(2*{z}))", f"(W/2+W/(2*{z}))", f"(H/2-H/(2*{z}))", f"(H/2+H/(2*{z}))"
    return (f"perspective=x0='{x0}':y0='{y0}':x1='{x1}':y1='{y0}':x2='{x0}':y2='{y1}':x3='{x1}':y3='{y1}':"
            "interpolation=cubic:eval=frame")


def render_plano(tarefa, plano, qualidade="previa", nome=None):
    p = validar_plano(plano, exigir_arquivos=True)
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
    for fonte_usada in ({p["legenda"]["estilo"]["fonte"]} if tem_leg else set()) | (
            {ESTILO_TITULO["fonte"]} if p["titulos"] else set()):
        if troca := A.verificar_fonte(fonte_usada):
            avisos.append(f"A fonte '{fonte_usada}' não está instalada; o texto saiu com '{troca}'.")
    ids = list(p["fontes"]) + ([au["musica"]["id"]] if au["musica"]["id"] else [])
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
        fa = [f"[0:a]volume={au['volume_original']:.3f}[orig]"]
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
    return {"nome": nome, "plano": validar_plano(pr.get("plano")), "alternativas": versoes}


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
    return jsonify({"presets": PRESETS, "titulo": ESTILO_TITULO, "qualidades": {k: v["rotulo"] for k, v in QUALIDADES.items()}})


@bp.post("/api/plano/validar")
def rota_validar():
    p = validar_plano(A.corpo_json().get("plano"))
    return jsonify({"plano": p, "duracao_saida": duracao_saida(p), "segmentos": mapa(p),
                    "eventos": [{k: e[k] for k in ("frase", "ini", "fim")} for e in eventos_legenda(p)]})


@bp.post("/api/transcricao")
def rota_transcrever_fonte():
    fid = A.corpo_json().get("fonte")
    A.achar(fid)
    t = T.iniciar("transcricao", "Transcrevendo a fonte", transcrever_fonte, fid)
    return jsonify(t.json()), 202


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
    validar_plano(j.get("plano"), exigir_arquivos=True)  # erro de plano aparece já, não dentro da tarefa
    nome = A.slug(j.get("nome") or "edicao")
    t = T.iniciar("render", QUALIDADES[qualidade]["rotulo"], render_plano, j["plano"], qualidade, nome)
    return jsonify(t.json()), 202


@bp.post("/api/miniaturas")
def rota_miniaturas():
    j = A.corpo_json()
    estilos = j.get("estilos") or {k: v for k, v in PRESETS.items()}
    if not isinstance(estilos, dict) or len(estilos) > 8:
        raise A.Erro("estilos precisa ser objeto com até 8 estilos.")
    return jsonify({k: f"/api/miniatura/{miniatura(j.get('plano'), v)}" for k, v in estilos.items()})


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
                                        "alternativas": j.get("alternativas", [])})), 201


@bp.get("/api/projetos/<pid>")
def rota_abrir_projeto(pid):
    return jsonify(carregar_projeto(pid))


@bp.put("/api/projetos/<pid>")
def rota_salvar_projeto(pid):
    if not _arq_projeto(pid).exists():
        raise A.Erro("Projeto não encontrado.", 404)
    return jsonify(salvar_projeto(pid, A.corpo_json()))
