"""Vídeos de referência: metadados oficiais, análise audiovisual (local ou provedor) e comparação com evidência.

Regras:
- Metadados (views, data, canal) e conteúdo audiovisual são acessos DIFERENTES e ficam registrados separados.
- Nada de baixar vídeo de terceiros nem burlar restrição: URL sem acesso audiovisual oficial pede o arquivo.
- Observação com timestamp só existe se alguém (o app local ou o provedor) realmente processou a mídia.
- Métrica informada pelo usuário fica rotulada como tal. Nada de views previstas, nota de viral ou confiança numérica.
"""
import json
import math
import mimetypes
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, send_file, abort

import app as A
import editor as E
import tarefas as T

REFS = A.ROOT / "referencias"
REFS.mkdir(exist_ok=True)
bp = Blueprint("referencias", __name__)
VERSAO_ANALISE = 1  # muda quando o método muda: invalida o cache (análise local)
VERSAO_GEMINI = 2   # v2: o prompt passou a pedir características observáveis (comparação entre referências)
CATEGORIAS = ("gancho", "contexto", "narrativa", "entrega", "ritmo", "repeticao", "legenda", "enquadramento",
              "efeitos", "fala", "audio", "final")


# ---------------------------------------------------------------- credenciais (só no backend)
def _config_local():
    arq = A.ROOT / "config_local.env"
    out = {}
    if arq.exists():
        for linha in arq.read_text(encoding="utf-8").splitlines():
            if "=" in linha and not linha.lstrip().startswith("#"):
                k, v = linha.split("=", 1)
                out[k.strip()] = v.strip().strip('"')
    return out


def credencial(nome):
    return os.environ.get(nome) or _config_local().get(nome) or None


def status_config():
    return {"youtube": bool(credencial("YOUTUBE_API_KEY")), "gemini": bool(credencial("GEMINI_API_KEY")),
            "gemini_modelo": credencial("GEMINI_MODELO") or "gemini-3.8-flash"}


# ---------------------------------------------------------------- HTTP com tentativas limitadas
class ErroHTTP(A.Erro):
    def __init__(self, msg, codigo=None):
        super().__init__(msg, 502)
        self.codigo = codigo


def _limpa(texto):
    """Nunca devolve chave de API em mensagem de erro ou log."""
    for nome in ("YOUTUBE_API_KEY", "GEMINI_API_KEY"):
        v = credencial(nome)
        if v:
            texto = texto.replace(v, "***")
    return re.sub(r"(key=)[^&\s]+", r"\1***", texto)


def _http(metodo, url, cabecalhos=None, corpo=None, timeout=60):
    """Uma requisição. Devolve (status, cabeçalhos, bytes). Separada para os testes substituírem."""
    req = urllib.request.Request(url, data=corpo, method=metodo, headers=cabecalhos or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()


def requisitar(metodo, url, cabecalhos=None, corpo=None, tarefa=None, tentativas=3, timeout=60):
    """Tenta de novo só em erro temporário (rede, 429, 5xx), com espera crescente e limite."""
    ultimo = None
    for i in range(tentativas):
        if tarefa is not None:
            tarefa.checar()
        try:
            st, cab, dados = _http(metodo, url, cabecalhos, corpo, timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            ultimo = ErroHTTP(f"Sem conexão com {urllib.parse.urlparse(url).netloc} ({type(e).__name__}).")
        else:
            if st < 400:
                return st, cab, dados
            texto = _limpa(dados[:300].decode("utf-8", "replace"))
            ultimo = ErroHTTP(f"{urllib.parse.urlparse(url).netloc} respondeu HTTP {st}: {texto}", st)
            if st not in (429, 500, 502, 503, 504):
                raise ultimo
        if i + 1 < tentativas:
            time.sleep(min(8, 1.5 * 2 ** i))
    raise ultimo


def requisitar_json(*a, **kw):
    _, _, dados = requisitar(*a, **kw)
    try:
        return json.loads(dados.decode("utf-8"))
    except ValueError:
        raise ErroHTTP("Resposta que não é JSON.")


# ---------------------------------------------------------------- URLs
def id_youtube(url):
    u = urllib.parse.urlparse(url.strip())
    host = (u.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    if host == "youtu.be":
        vid = u.path.strip("/").split("/")[0]
    elif host in ("youtube.com", "music.youtube.com"):
        if u.path == "/watch":
            vid = urllib.parse.parse_qs(u.query).get("v", [""])[0]
        else:
            m = re.match(r"/(shorts|embed|live)/([^/?#]+)", u.path)
            vid = m.group(2) if m else ""
    else:
        return None
    return vid if re.fullmatch(r"[A-Za-z0-9_-]{11}", vid) else None


def eh_tiktok(url):
    host = (urllib.parse.urlparse(url.strip()).hostname or "").lower()
    return host == "tiktok.com" or host.endswith(".tiktok.com")


# ---------------------------------------------------------------- metadados oficiais
def _iso_duracao(s):
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return None
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def metadados_youtube(vid, tarefa=None):
    chave = credencial("YOUTUBE_API_KEY")
    if not chave:
        return None, "Sem YOUTUBE_API_KEY: views, data e canal não foram consultados (configure a chave ou informe à mão)."
    base = "https://www.googleapis.com/youtube/v3/"
    j = requisitar_json("GET", base + "videos?" + urllib.parse.urlencode(
        {"part": "snippet,statistics,contentDetails", "id": vid, "key": chave}), tarefa=tarefa)
    itens = j.get("items") or []
    if not itens:
        return None, "O YouTube não devolveu esse vídeo (privado, removido ou ID errado)."
    v = itens[0]
    sn, stt, cd = v.get("snippet", {}), v.get("statistics", {}), v.get("contentDetails", {})
    meta = {"fonte": "YouTube Data API v3 (videos.list)", "titulo": sn.get("title"), "canal": sn.get("channelTitle"),
            "canal_id": sn.get("channelId"), "publicado_em": sn.get("publishedAt"),
            "duracao_s": _iso_duracao(cd.get("duration")), "idioma_audio": sn.get("defaultAudioLanguage"),
            "views": int(stt["viewCount"]) if str(stt.get("viewCount", "")).isdigit() else None,
            "likes": int(stt["likeCount"]) if str(stt.get("likeCount", "")).isdigit() else None,
            "inscritos": None, "consultado_em": agora_iso()}
    if meta["canal_id"]:
        try:
            c = requisitar_json("GET", base + "channels?" + urllib.parse.urlencode(
                {"part": "statistics", "id": meta["canal_id"], "key": chave}), tarefa=tarefa)
            cs = ((c.get("items") or [{}])[0]).get("statistics", {})
            if not cs.get("hiddenSubscriberCount") and str(cs.get("subscriberCount", "")).isdigit():
                meta["inscritos"] = int(cs["subscriberCount"])
        except ErroHTTP:
            pass  # inscritos é complemento; o resto continua valendo
    return meta, None


def gemini_busca(tarefa, prompt):
    """Gemini com a ferramenta oficial de Busca Google (grounding). Devolve (json, modelo, fontes), em que `fontes`
    traz SÓ o que a API registrou: consultas feitas, citações (url, título) e o HTML de sugestões de busca que os
    termos pedem para exibir. Cobrança: por busca executada (há cota mensal grátis; ver README)."""
    chave = credencial("GEMINI_API_KEY")
    if not chave:
        raise SemProvedor("Sem GEMINI_API_KEY: a Busca Google pelo Gemini não está disponível.")
    modelo = status_config()["gemini_modelo"]
    corpo = {"model": modelo, "input": [{"type": "text", "text": prompt}], "tools": [{"type": "google_search"}]}
    j = requisitar_json("POST", "https://generativelanguage.googleapis.com/v1beta/interactions", tarefa=tarefa,
                        timeout=300, corpo=json.dumps(corpo).encode(),
                        cabecalhos={"x-goog-api-key": chave, "Content-Type": "application/json"})
    consultas, citacoes, sugestoes = [], [], None
    for passo in j.get("steps") or []:
        if not isinstance(passo, dict):
            continue
        if passo.get("type") == "google_search_call":
            q = (passo.get("arguments") or {}).get("queries") or []
            consultas += [x for x in q if isinstance(x, str)]
        elif passo.get("type") == "google_search_result":
            for r in passo.get("result") or [passo]:
                if isinstance(r, dict) and isinstance(r.get("search_suggestions"), str):
                    sugestoes = r["search_suggestions"]
        elif passo.get("type") == "model_output":
            for c in passo.get("content") or []:
                for an in (c.get("annotations") or []) if isinstance(c, dict) else []:
                    if isinstance(an, dict) and an.get("type") == "url_citation" and isinstance(an.get("url"), str):
                        citacoes.append({"url": an["url"][:500], "titulo": str(an.get("title") or "")[:200]})
    vistos, unicas = set(), []
    for c in citacoes:
        if c["url"] not in vistos:
            vistos.add(c["url"])
            unicas.append(c)
    return json_do_texto(_texto_interacao(j)), modelo, {"consultas": consultas[:20], "citacoes": unicas[:30],
                                                        "sugestoes_html": (sugestoes or "")[:20000] or None}


def oembed_youtube(vid, tarefa=None):
    """Confirma, pelo oEmbed oficial do YouTube (sem chave), que o vídeo existe e é público/incorporável.
    Devolve título e canal; NÃO traz views nem data."""
    url = "https://www.youtube.com/oembed?" + urllib.parse.urlencode(
        {"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"})
    try:
        j = requisitar_json("GET", url, tarefa=tarefa, tentativas=2, timeout=20)
    except ErroHTTP as e:
        return None, str(e)
    if not j.get("title"):
        return None, "o oEmbed não reconheceu o vídeo"
    return {"titulo": str(j["title"])[:200], "canal": str(j.get("author_name") or "")[:120],
            "canal_url": j.get("author_url"), "fonte": "YouTube oEmbed", "consultado_em": agora_iso()}, None


def _meta_item(v, consultado):
    sn, stt, cd = v.get("snippet", {}), v.get("statistics", {}), v.get("contentDetails", {})
    dig = lambda k: int(stt[k]) if str(stt.get(k, "")).isdigit() else None  # noqa: E731
    return {"fonte": "YouTube Data API v3 (videos.list)", "titulo": sn.get("title"), "canal": sn.get("channelTitle"),
            "canal_id": sn.get("channelId"), "publicado_em": sn.get("publishedAt"),
            "duracao_s": _iso_duracao(cd.get("duration")),
            "idioma_audio": sn.get("defaultAudioLanguage") or sn.get("defaultLanguage"),
            "views": dig("viewCount"), "likes": dig("likeCount"), "comentarios": dig("commentCount"),
            "inscritos": None, "consultado_em": consultado}


def buscar_youtube(consulta, tarefa=None, maximo=15, idioma="pt", regiao="BR"):
    """search.list oficial (vídeos curtos, < 4 min). A busca NÃO traz views: só os ids."""
    chave = credencial("YOUTUBE_API_KEY")
    if not chave:
        raise SemProvedor("Sem YOUTUBE_API_KEY: a busca no YouTube não foi feita.")
    j = requisitar_json("GET", "https://www.googleapis.com/youtube/v3/search?" + urllib.parse.urlencode(
        {"part": "snippet", "q": consulta, "type": "video", "videoDuration": "short", "relevanceLanguage": idioma,
         "regionCode": regiao, "maxResults": maximo, "key": chave}), tarefa=tarefa)
    return [it["id"]["videoId"] for it in j.get("items") or []
            if isinstance(it.get("id"), dict) and re.fullmatch(r"[A-Za-z0-9_-]{11}", str(it["id"].get("videoId", "")))]


def detalhes_youtube(ids, tarefa=None):
    """videos.list + channels.list em lote (até 50 por chamada): views, data, duração e inscritos públicos."""
    chave = credencial("YOUTUBE_API_KEY")
    if not chave:
        raise SemProvedor("Sem YOUTUBE_API_KEY.")
    base, agora, metas = "https://www.googleapis.com/youtube/v3/", agora_iso(), {}
    for k in range(0, len(ids), 50):
        j = requisitar_json("GET", base + "videos?" + urllib.parse.urlencode(
            {"part": "snippet,statistics,contentDetails", "id": ",".join(ids[k:k + 50]), "key": chave}), tarefa=tarefa)
        for v in j.get("items") or []:
            metas[v["id"]] = _meta_item(v, agora)
    canais = sorted({m["canal_id"] for m in metas.values() if m.get("canal_id")})
    insc = {}
    for k in range(0, len(canais), 50):
        try:
            c = requisitar_json("GET", base + "channels?" + urllib.parse.urlencode(
                {"part": "statistics", "id": ",".join(canais[k:k + 50]), "key": chave}), tarefa=tarefa)
        except ErroHTTP:
            break  # inscritos é complemento
        for it in c.get("items") or []:
            cs = it.get("statistics", {})
            if not cs.get("hiddenSubscriberCount") and str(cs.get("subscriberCount", "")).isdigit():
                insc[it["id"]] = int(cs["subscriberCount"])
    for m in metas.values():
        m["inscritos"] = insc.get(m.get("canal_id"))
    return metas


def metadados_tiktok(url, tarefa=None):
    j = requisitar_json("GET", "https://www.tiktok.com/oembed?" + urllib.parse.urlencode({"url": url}), tarefa=tarefa)
    if not j.get("title") and not j.get("author_name"):
        return None, "O oEmbed do TikTok não reconheceu essa URL."
    return {"fonte": "TikTok oEmbed", "titulo": j.get("title"), "canal": j.get("author_name"),
            "canal_url": j.get("author_url"), "miniatura": j.get("thumbnail_url"), "views": None,
            "publicado_em": None, "inscritos": None, "consultado_em": agora_iso()}, \
        "O TikTok não expõe views nem data pela API pública (oEmbed); informe à mão se souber."


def agora_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- análise local (arquivo)
def _mmss(t):
    return f"{int(t // 60)}:{t % 60:04.1f}"


def _repeticoes(palavras, n=4):
    """Sequências de n palavras (normalizadas) ditas mais de uma vez, com o tempo de cada ocorrência."""
    norm = [A._norm(p[2]) for p in palavras]
    vistos, rep = {}, []
    for i in range(len(norm) - n + 1):
        chave = " ".join(norm[i:i + n])
        if not all(norm[i:i + n]):
            continue
        if chave in vistos and palavras[i][0] - palavras[vistos[chave]][0] > 2.0:
            rep.append({"texto": " ".join(p[2] for p in palavras[i:i + n]), "primeira": palavras[vistos[chave]][0],
                        "repeticao": palavras[i][0], "repeticao_fim": palavras[i + n - 1][1]})
        vistos.setdefault(chave, i)
    # junta janelas sobrepostas da mesma repetição
    out = []
    for r in rep:
        if out and r["repeticao"] <= out[-1]["repeticao_fim"]:
            out[-1]["repeticao_fim"] = max(out[-1]["repeticao_fim"], r["repeticao_fim"])
        else:
            out.append(r)
    return out


def analisar_local(tarefa, fid):
    src = A.achar(fid)
    info = A.probe(src)
    dur = info["duracao"]
    obs, lim = [], []
    tarefa.avancar(0.02, "lendo o arquivo")
    vertical = info["altura"] and info["largura"] and info["altura"] > info["largura"]
    obs.append({"t": 0.0, "categoria": "enquadramento",
                "texto": f"{info['largura']}x{info['altura']} ({'vertical' if vertical else 'horizontal/quadrado'}), {dur:.1f}s."})
    tarefa.avancar(0.05, "detectando trocas de cena e pausas")
    cenas, pausas = A.detectar(src, info["tem_audio"], duracao=dur)
    tarefa.avancar(0.30)
    planos = [b - a for a, b in zip([0.0] + cenas, cenas + [dur])]
    mais_longo = max(range(len(planos)), key=lambda i: planos[i]) if planos else 0
    ini_longo = ([0.0] + cenas)[mais_longo] if planos else 0.0
    obs.append({"t": cenas[0] if cenas else 0.0, "categoria": "ritmo",
                "texto": (f"{len(cenas)} trocas de cena ({len(cenas) / dur * 60:.1f}/min); 1a troca em {_mmss(cenas[0])}."
                          if cenas else "Nenhuma troca de cena detectada (plano único ou cortes muito suaves).")})
    if planos:
        obs.append({"t": ini_longo, "categoria": "ritmo",
                    "texto": f"Plano mais longo: {planos[mais_longo]:.1f}s a partir de {_mmss(ini_longo)}."})
    trans, palavras, falas = None, [], []
    if info["tem_audio"]:
        sub = T.Tarefa("sub", "transcrição")
        sub._cancelar = tarefa._cancelar
        def avancar(p=None, etapa=None):
            tarefa.avancar(0.3 + 0.55 * (p or 0), etapa and f"fala: {etapa}")
        sub.avancar = avancar
        E.transcrever_fonte(sub, fid)
        trans = E.transcricao_em_cache(fid)
        palavras, falas = trans["palavras"], trans["falas"]
    else:
        lim.append("Sem áudio: nada de fala, gancho falado ou repetição a observar.")
    if palavras:
        obs.append({"t": palavras[0][0], "categoria": "gancho",
                    "texto": f"Primeira fala em {_mmss(palavras[0][0])}: “{falas[0]['texto'][:160]}”"})
        if len(falas) > 1:
            obs.append({"t": falas[-1]["ini"], "categoria": "final",
                        "texto": f"Última fala em {_mmss(falas[-1]['ini'])}: “{falas[-1]['texto'][:160]}”"})
        for r in _repeticoes(palavras)[:5]:
            obs.append({"t": r["repeticao"], "categoria": "repeticao",
                        "texto": f"“{r['texto']}” repetido (1a vez em {_mmss(r['primeira'])})."})
    elif info["tem_audio"]:
        obs.append({"t": 0.0, "categoria": "fala", "texto": "Nenhuma fala reconhecida (música, efeitos ou idioma não PT)."})
    for a, b in pausas:
        if b - a >= 1.0:
            obs.append({"t": a, "categoria": "audio", "texto": f"Silêncio de {b - a:.1f}s em {_mmss(a)}."})
    lufs = None
    if info["tem_audio"]:
        tarefa.avancar(0.88, "medindo volume (EBU R128)")
        r = A.executar(["ffmpeg", "-hide_banner", "-nostats", "-i", str(src), "-map", "0:a:0", "-af", "ebur128",
                        "-f", "null", "-"], timeout=A.prazo_midia(dur))
        m = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
        lufs = float(m[-1]) if m else None
        if lufs is not None:
            obs.append({"t": 0.0, "categoria": "audio", "texto": f"Volume integrado {lufs:.1f} LUFS."})
    tarefa.avancar(0.93, "salvando quadros nas trocas de cena")
    quadros = []
    pasta = E.CACHE / "quadros" / E.hash_midia(src)
    pasta.mkdir(parents=True, exist_ok=True)
    for t in ([0.5] + cenas)[:12]:
        arq = pasta / f"{t:09.3f}.jpg"
        if not arq.exists():
            A.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{min(t + 0.05, dur - 0.05):.3f}", "-i", str(src),
                   "-frames:v", "1", "-vf", "scale=-2:240", str(arq)], timeout=60)
        quadros.append({"t": t, "url": f"/api/quadro/{E.hash_midia(src)}/{arq.name}"})
    lim.append("Estilo/legibilidade de legenda queimada, efeitos visuais e enquadramento de rosto NÃO são detectados "
               "localmente: veja os quadros ou use o provedor Gemini (opcional).")
    fala_s = sum(f["fim"] - f["ini"] for f in falas)
    metricas = {"duracao_s": round(dur, 2), "vertical": bool(vertical), "trocas_de_cena": len(cenas),
                "cortes_por_min": round(len(cenas) / dur * 60, 2) if dur else None,
                "plano_medio_s": round(dur / (len(cenas) + 1), 2),
                "primeira_fala_s": round(palavras[0][0], 2) if palavras else None,
                "palavras_por_min": round(len(palavras) / fala_s * 60, 1) if fala_s > 1 else None,
                "fala_proporcao": round(fala_s / dur, 2) if dur else None,
                "repeticoes": len(_repeticoes(palavras)) if palavras else 0, "lufs": lufs}
    return {"provedor": "local", "versao": VERSAO_ANALISE, "criado_em": agora_iso(), "metricas": metricas,
            "observacoes": sorted(obs, key=lambda o: o["t"]), "quadros": quadros, "limitacoes": lim,
            "metodo": "Análise local do arquivo: ffmpeg (cenas, pausas, EBU R128) + faster-whisper (fala)."}


# ---------------------------------------------------------------- provedor Gemini (opcional)
PROMPT_GEMINI = """Você analisa um vídeo curto de referência para um editor de vídeo. Descreva SOMENTE o que dá para observar no vídeo e no áudio.
Não estime views, sucesso, viralidade nem desempenho. Não invente: se não der para observar, não escreva.
Responda APENAS com um objeto JSON neste formato (sem texto fora do JSON):
{"resumo": "até 3 frases",
 "observacoes": [{"t": "MM:SS", "categoria": "gancho|contexto|narrativa|entrega|ritmo|repeticao|legenda|enquadramento|efeitos|fala|audio|final", "texto": "o que acontece nesse instante"}],
 "legenda_queimada": {"presente": true/false/null, "descricao": "fonte, cor, posição, legibilidade, se houver"},
 "promessa_e_entrega": {"promessa": "o que o início promete", "t_promessa": "MM:SS ou null", "entrega": "se e quando entrega", "t_entrega": "MM:SS ou null"},
 "caracteristicas": {"fala_nos_3s": true/false/null, "texto_na_tela_no_inicio": true/false/null, "rosto_em_cena": true/false/null,
                     "musica_de_fundo": true/false/null, "narracao_em_off": true/false/null, "efeitos_sonoros": true/false/null}}
Em "caracteristicas", use null quando não der para observar com segurança.
Cubra: o gancho de abertura e quando o contexto fica claro; a progressão e se a promessa é entregue; ritmo, repetições e cortes;
aparência, legibilidade e posição das legendas; enquadramento, efeitos visuais, fala e tratamento de áudio. De 6 a 25 observações, em ordem de tempo."""


def _t_mmss(v, dur):
    if v is None:
        return None
    if not isinstance(v, str) or not re.fullmatch(r"\d{1,3}:\d{2}(\.\d+)?", v.strip()):
        raise ValueError(f"timestamp inválido {v!r}")
    m, s = v.strip().split(":")
    t = int(m) * 60 + float(s)
    if float(s) >= 60 or (dur and t > dur + 2):
        raise ValueError(f"timestamp {v} fora do vídeo")
    return round(t, 1)


def json_do_texto(texto):
    """O JSON que o modelo devolveu (aceita bloco ```json```). Resposta vazia ou não-JSON é erro 502."""
    if not isinstance(texto, str) or not texto.strip():
        raise A.Erro("O provedor devolveu resposta vazia.", 502)
    bruto = texto.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", bruto, re.S)
    if m:
        bruto = m.group(1)
    try:
        return json.loads(bruto)
    except ValueError:
        raise A.Erro("A resposta do provedor não é JSON válido; nada foi registrado.", 502)


CARACTERISTICAS = {"fala_nos_3s": "fala nos 3 primeiros segundos", "texto_na_tela_no_inicio": "texto na tela no início",
                   "rosto_em_cena": "rosto em cena", "musica_de_fundo": "música de fundo",
                   "narracao_em_off": "narração em off", "efeitos_sonoros": "efeitos sonoros",
                   "legenda_queimada": "legenda na tela"}


def validar_resposta_gemini(texto, dur=None):
    """Valida o que o modelo devolveu. Observação inválida é descartada (e contada); estrutura inválida é erro."""
    j = json_do_texto(texto)
    if not isinstance(j, dict) or not isinstance(j.get("observacoes"), list):
        raise A.Erro("A resposta do provedor não tem a lista 'observacoes'; nada foi registrado.", 502)
    obs, descartadas = [], 0
    for o in j["observacoes"][:60]:
        try:
            if not isinstance(o, dict) or o.get("categoria") not in CATEGORIAS:
                raise ValueError("categoria")
            texto_o = o.get("texto")
            if not isinstance(texto_o, str) or not texto_o.strip():
                raise ValueError("texto")
            obs.append({"t": _t_mmss(o.get("t"), dur), "categoria": o["categoria"], "texto": texto_o.strip()[:300]})
        except ValueError:
            descartadas += 1
    if not obs:
        raise A.Erro("Nenhuma observação válida na resposta do provedor; nada foi registrado.", 502)
    leg = j.get("legenda_queimada") if isinstance(j.get("legenda_queimada"), dict) else {}
    pe = j.get("promessa_e_entrega") if isinstance(j.get("promessa_e_entrega"), dict) else {}

    def t_ou_none(v):
        try:
            return _t_mmss(v, dur)
        except ValueError:
            return None
    return {"resumo": str(j.get("resumo") or "")[:600],
            "observacoes": sorted([o for o in obs if o["t"] is not None], key=lambda o: o["t"]),
            "legenda_queimada": {"presente": leg.get("presente") if isinstance(leg.get("presente"), bool) else None,
                                 "descricao": str(leg.get("descricao") or "")[:300]},
            "promessa_e_entrega": {"promessa": str(pe.get("promessa") or "")[:300], "t_promessa": t_ou_none(pe.get("t_promessa")),
                                   "entrega": str(pe.get("entrega") or "")[:300], "t_entrega": t_ou_none(pe.get("t_entrega"))},
            "caracteristicas": {k: v for k, v in (j.get("caracteristicas") or {}).items()
                                if k in CARACTERISTICAS and isinstance(v, bool)}
            if isinstance(j.get("caracteristicas"), dict) else {},
            "descartadas": descartadas}


def _texto_interacao(j):
    partes = []
    for passo in j.get("steps") or []:
        if isinstance(passo, dict) and passo.get("type") == "model_output":
            for c in passo.get("content") or []:
                if isinstance(c, dict) and isinstance(c.get("text"), str):
                    partes.append(c["text"])
    if not partes:  # formatos alternativos documentados nos SDKs (output_text / outputs)
        if isinstance(j.get("output_text"), str):
            partes.append(j["output_text"])
        for c in j.get("outputs") or []:
            if isinstance(c, dict) and c.get("type") == "text" and isinstance(c.get("text"), str):
                partes.append(c["text"])
    return "\n".join(partes)


class SemProvedor(A.Erro):
    """Provedor não configurado: quem chamou decide se cai na heurística local (e avisa)."""
    def __init__(self, msg):
        super().__init__(msg, 503)


def gemini_json(tarefa, prompt, imagens=(), temperatura=None):
    """Uma chamada de TEXTO (e, só com consentimento, imagens JPEG inline) ao Gemini; devolve (json, modelo).
    A chave fica no backend; quem valida o conteúdo da resposta é quem chamou."""
    import base64
    chave = credencial("GEMINI_API_KEY")
    if not chave:
        raise SemProvedor("Sem GEMINI_API_KEY: análise por modelo indisponível (configure em config_local.env).")
    modelo = status_config()["gemini_modelo"]
    entrada = [{"type": "text", "text": prompt}] + [
        {"type": "image", "data": base64.b64encode(img).decode("ascii"), "mime_type": "image/jpeg"} for img in imagens]
    corpo = {"model": modelo, "input": entrada}
    if temperatura is not None:
        corpo["generation_config"] = {"temperature": temperatura}
    j = requisitar_json("POST", "https://generativelanguage.googleapis.com/v1beta/interactions", tarefa=tarefa,
                        timeout=300, corpo=json.dumps(corpo).encode(),
                        cabecalhos={"x-goog-api-key": chave, "Content-Type": "application/json"})
    return json_do_texto(_texto_interacao(j)), modelo


def analisar_gemini(tarefa, ref, permitir_envio=False):
    chave = credencial("GEMINI_API_KEY")
    if not chave:
        raise A.Erro("Provedor Gemini não configurado: defina GEMINI_API_KEY (veja o README). A análise local continua disponível para arquivos.")
    modelo = status_config()["gemini_modelo"]
    base = "https://generativelanguage.googleapis.com"
    cab = {"x-goog-api-key": chave, "Content-Type": "application/json"}
    dur = None
    if ref["tipo"] == "youtube":
        entrada_video = {"type": "video", "uri": f"https://www.youtube.com/watch?v={ref['youtube_id']}"}
        dur = (ref.get("metadados") or {}).get("duracao_s")
        envio = "A URL pública do YouTube foi enviada ao Gemini (nenhum arquivo saiu do seu PC)."
    elif ref["tipo"] == "arquivo":
        if not permitir_envio:
            raise A.Erro("Para analisar este arquivo com o Gemini, o vídeo é ENVIADO ao Google. Marque o consentimento e tente de novo.", 403)
        src = A.achar(ref["upload_id"])
        dur = A.probe(src)["duracao"]
        mime = mimetypes.guess_type(src.name)[0] or "video/mp4"
        tamanho = src.stat().st_size
        tarefa.avancar(0.05, "enviando o vídeo ao Gemini")
        _, h, _ = requisitar("POST", f"{base}/upload/v1beta/files", tarefa=tarefa, cabecalhos={
            **cab, "X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(tamanho), "X-Goog-Upload-Header-Content-Type": mime},
            corpo=json.dumps({"file": {"display_name": f"referencia-{ref['id']}"}}).encode())
        url_up = {k.lower(): v for k, v in h.items()}.get("x-goog-upload-url")
        if not url_up:
            raise A.Erro("O Gemini não devolveu o endereço de envio.", 502)
        with open(src, "rb") as f:
            _, _, dados = requisitar("POST", url_up, tarefa=tarefa, tentativas=1, timeout=600, cabecalhos={
                "Content-Length": str(tamanho), "X-Goog-Upload-Offset": "0",
                "X-Goog-Upload-Command": "upload, finalize"}, corpo=f.read())
        arquivo = json.loads(dados.decode("utf-8")).get("file") or {}
        for i in range(40):  # aguarda processar, com limite (~2 min)
            tarefa.avancar(0.1 + i * 0.01, "aguardando o Gemini processar o vídeo")
            if arquivo.get("state") == "ACTIVE":
                break
            if arquivo.get("state") == "FAILED":
                raise A.Erro("O Gemini não conseguiu processar o vídeo.", 502)
            time.sleep(3)
            arquivo = requisitar_json("GET", f"{base}/v1beta/{arquivo['name']}", cabecalhos=cab, tarefa=tarefa)
        else:
            raise A.Erro("O Gemini demorou demais para processar o vídeo; tente de novo mais tarde.", 504)
        entrada_video = {"type": "video", "uri": arquivo["uri"], "mime_type": mime}
        envio = "O arquivo foi ENVIADO ao Google (Files API do Gemini) com o seu consentimento."
    else:
        raise A.Erro("O Gemini não aceita URL do TikTok. Envie o arquivo, se você tiver direito de usá-lo.")
    tarefa.avancar(0.6, f"analisando com {modelo}")
    j = requisitar_json("POST", f"{base}/v1beta/interactions", tarefa=tarefa, timeout=300, cabecalhos=cab,
                        corpo=json.dumps({"model": modelo, "input": [entrada_video, {"type": "text", "text": PROMPT_GEMINI}]}).encode())
    r = validar_resposta_gemini(_texto_interacao(j), dur)
    lim = ["Observações geradas por modelo de IA: podem conter erros; confira nos tempos indicados.",
           "O provedor amostra o vídeo (por padrão ~1 quadro/s): cortes muito rápidos podem passar despercebidos."]
    if r["descartadas"]:
        lim.append(f"{r['descartadas']} observação(ões) da resposta foram descartadas por formato ou tempo inválido.")
    return {"provedor": "gemini", "modelo": modelo, "versao": VERSAO_GEMINI, "criado_em": agora_iso(),
            "caracteristicas": r["caracteristicas"],
            "envio": envio, "resumo": r["resumo"], "observacoes": r["observacoes"],
            "legenda_queimada": r["legenda_queimada"], "promessa_e_entrega": r["promessa_e_entrega"],
            "limitacoes": lim, "metodo": f"Gemini ({modelo}) via Interactions API."}


# ---------------------------------------------------------------- registros de referência
def _arq_ref(rid):
    if not re.fullmatch(r"[a-f0-9]{12}", rid or ""):
        raise A.Erro("Referência inválida.", 404)
    return REFS / f"{rid}.json"


def ler_ref(rid):
    arq = _arq_ref(rid)
    if not arq.exists():
        raise A.Erro("Referência não encontrada.", 404)
    return json.loads(arq.read_text(encoding="utf-8"))


def gravar_ref(ref):
    arq = _arq_ref(ref["id"])
    tmp = arq.with_suffix(".tmp")
    tmp.write_text(json.dumps(ref, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, arq)
    return ref


def nova_ref_url(url, tarefa=None):
    url = (url or "").strip()
    if not re.match(r"https?://", url) or len(url) > 500:
        raise A.Erro("Informe uma URL completa (https://...).")
    ref = {"id": uuid.uuid4().hex[:12], "criado_em": agora_iso(), "url": url, "metadados": None,
           "metricas_usuario": None, "tags": {}, "analises": {}, "avisos": []}
    vid = id_youtube(url)
    if vid:
        ref.update(tipo="youtube", youtube_id=vid, nome=f"YouTube {vid}")
        try:
            meta, aviso = metadados_youtube(vid, tarefa)
        except ErroHTTP as e:
            meta, aviso = None, f"Metadados do YouTube indisponíveis: {e}"
        ref["acesso"] = {"metadados": bool(meta), "audiovisual": "somente pelo provedor Gemini (URL pública)",
                         "motivo": "O app não baixa vídeos de terceiros; a API oficial do YouTube só dá metadados."}
    elif eh_tiktok(url):
        ref.update(tipo="tiktok", nome="TikTok")
        try:
            meta, aviso = metadados_tiktok(url, tarefa)
        except ErroHTTP as e:
            meta, aviso = None, f"oEmbed do TikTok indisponível: {e}"
        ref["acesso"] = {"metadados": bool(meta), "audiovisual": "indisponível por URL",
                         "motivo": "Não há API pública oficial que entregue o vídeo do TikTok. Envie o arquivo se tiver direito de usá-lo."}
    else:
        raise A.Erro("Só URLs do YouTube ou do TikTok. Para outros vídeos, envie o arquivo.")
    ref["metadados"] = meta
    if meta and meta.get("titulo"):
        ref["nome"] = meta["titulo"][:120]
    if aviso:
        ref["avisos"].append(aviso)
    return gravar_ref(ref)


def nova_ref_arquivo(fid, nome):
    A.achar(fid)
    ref = {"id": uuid.uuid4().hex[:12], "criado_em": agora_iso(), "tipo": "arquivo", "upload_id": fid,
           "nome": (nome or "arquivo")[:120], "metadados": None, "metricas_usuario": None, "tags": {}, "analises": {},
           "avisos": [], "acesso": {"metadados": False, "audiovisual": "local (arquivo enviado por você)",
                                    "motivo": "Views e data de um arquivo só existem se você informar."}}
    return gravar_ref(ref)


def _chave_cache(ref, provedor):
    if ref["tipo"] == "arquivo":
        ident = "sha:" + E.hash_midia(A.achar(ref["upload_id"]))
    elif ref["tipo"] == "youtube":
        ident = "yt:" + ref["youtube_id"]
    else:
        ident = "url:" + ref["url"]
    extra = status_config()["gemini_modelo"] if provedor == "gemini" else A.MODELO_WHISPER
    versao = VERSAO_GEMINI if provedor == "gemini" else VERSAO_ANALISE
    return re.sub(r"[^A-Za-z0-9]", "_", f"{provedor}_{versao}_{extra}_{ident}")[:150]


def analisar_ref(tarefa, rid, provedor, permitir_envio=False):
    ref = ler_ref(rid)
    (E.CACHE / "analises").mkdir(parents=True, exist_ok=True)
    arq = E.CACHE / "analises" / f"{_chave_cache(ref, provedor)}.json"
    if arq.exists():
        res = json.loads(arq.read_text(encoding="utf-8"))
        res["em_cache"] = True
    else:
        if provedor == "local":
            if ref["tipo"] != "arquivo":
                raise A.Erro("A análise local precisa do arquivo de vídeo. Por URL, o app só lê metadados oficiais.")
            res = analisar_local(tarefa, ref["upload_id"])
        elif provedor == "gemini":
            res = analisar_gemini(tarefa, ref, permitir_envio)
        else:
            raise A.Erro("Provedor desconhecido.")
        arq.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        res["em_cache"] = False
    ref = ler_ref(rid)  # relê: o usuário pode ter editado métricas enquanto analisava
    ref["analises"][provedor] = res
    gravar_ref(ref)
    return {"ref": rid, "provedor": provedor, "observacoes": len(res["observacoes"]), "em_cache": res["em_cache"]}


def validar_metricas_usuario(m):
    if not isinstance(m, dict):
        raise A.Erro("Métricas precisam ser um objeto.")
    out = {"informado_em": agora_iso()}
    for k in ("views", "inscritos"):
        v = m.get(k)
        if v in (None, ""):
            out[k] = None
        elif isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 and math.isfinite(v):
            out[k] = int(v)
        else:
            raise A.Erro(f"'{k}' precisa ser um número inteiro >= 0.")
    d = m.get("publicado_em")
    if d in (None, ""):
        out["publicado_em"] = None
    elif isinstance(d, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        out["publicado_em"] = d + "T00:00:00Z"
    else:
        raise A.Erro("'publicado_em' precisa ser uma data AAAA-MM-DD.")
    out["observacao"] = str(m.get("observacao") or "")[:300]
    return out


# ---------------------------------------------------------------- comparação e hipóteses
CARAC = {  # métrica: (rótulo, unidade)
    "primeira_fala_s": ("tempo até a primeira fala", "s"),
    "cortes_por_min": ("trocas de cena por minuto", "/min"),
    "plano_medio_s": ("duração média de plano", "s"),
    "palavras_por_min": ("palavras por minuto (na fala)", ""),
    "fala_proporcao": ("proporção do vídeo com fala", ""),
    "repeticoes": ("trechos repetidos", ""),
    "duracao_s": ("duração", "s"),
}


def _dt(s):
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def desempenho(ref):
    """Views e idade, dizendo de onde veio cada número. Nada é estimado."""
    meta, usu = ref.get("metadados") or {}, ref.get("metricas_usuario") or {}
    if meta.get("views") is not None:
        views, origem_v, quando = meta["views"], meta["fonte"], meta.get("consultado_em")
    elif usu.get("views") is not None:
        views, origem_v, quando = usu["views"], "informado por você", usu.get("informado_em")
    else:
        views, origem_v, quando = None, None, None
    pub = meta.get("publicado_em") or usu.get("publicado_em")
    origem_pub = (meta.get("fonte") if meta.get("publicado_em") else "informado por você") if pub else None
    inscritos = meta.get("inscritos") if meta.get("inscritos") is not None else usu.get("inscritos")
    dias = None
    if pub and quando and _dt(pub) and _dt(quando):
        dias = max(1.0, (_dt(quando) - _dt(pub)).total_seconds() / 86400)
    return {"views": views, "origem_views": origem_v, "consultado_em": quando, "publicado_em": pub,
            "origem_publicacao": origem_pub, "dias_no_ar": round(dias, 1) if dias else None,
            "views_por_dia": round(views / dias, 1) if views is not None and dias else None, "inscritos": inscritos}


def metricas_ref(ref):
    return (ref.get("analises", {}).get("local") or {}).get("metricas") or {}


def caracteristicas_ref(r):
    """Características booleanas observadas pelo provedor (Gemini) ou marcadas por você (legenda)."""
    g = (r.get("analises") or {}).get("gemini") or {}
    out = dict(g.get("caracteristicas") or {})
    leg = (g.get("legenda_queimada") or {}).get("presente")
    if isinstance(leg, bool):
        out["legenda_queimada"] = leg
    tag = (r.get("tags") or {}).get("legenda")
    if "legenda_queimada" not in out and tag in ("sim", "não"):
        out["legenda_queimada"] = tag == "sim"
    return out


def comparar(refs, meu=None):
    linhas = [{"id": r["id"], "nome": r.get("nome"), "tipo": r["tipo"], "desempenho": desempenho(r),
               "metricas": metricas_ref(r), "tags": r.get("tags") or {},
               "legenda_queimada": ((r.get("analises", {}).get("gemini") or {}).get("legenda_queimada") or {}).get("presente"),
               "caracteristicas": caracteristicas_ref(r),
               "analisado": sorted((r.get("analises") or {}).keys())} for r in refs]
    notas, hipoteses = [], []
    comp = [l for l in linhas if l["desempenho"]["views_por_dia"] is not None and (l["metricas"] or l["caracteristicas"])]
    if len(refs) == 1:
        notas.append("Análise de UMA referência só: não há comparação de desempenho; as observações servem de inspiração, não de evidência.")
    if len(comp) < 2:
        notas.append("Para formular hipóteses são precisas pelo menos 2 referências com análise (local ou Gemini) E views + data de publicação "
                     f"(hoje: {len(comp)}).")
    else:
        dias = [l["desempenho"]["dias_no_ar"] for l in comp]
        if max(dias) / min(dias) > 3:
            notas.append(f"Tempo no ar muito diferente ({min(dias):.0f} a {max(dias):.0f} dias): views totais não são comparáveis; "
                         "usei views/dia, que ainda favorece vídeos novos em alta ou antigos com cauda longa.")
        insc = [l["desempenho"]["inscritos"] for l in comp if l["desempenho"]["inscritos"]]
        if len(insc) >= 2 and max(insc) / max(1, min(insc)) > 10:
            notas.append(f"Tamanho de canal muito diferente ({min(insc):,} a {max(insc):,} inscritos): parte da diferença de views vem do público, não da edição.".replace(",", "."))
        durs = [l["metricas"].get("duracao_s") for l in comp if l["metricas"].get("duracao_s")]
        if durs and max(durs) / min(durs) > 2:
            notas.append("Durações muito diferentes: prefira referências com duração e formato parecidos com o seu vídeo.")
        if len({l["metricas"].get("vertical") for l in comp}) > 1:
            notas.append("Há referências verticais e horizontais misturadas: formatos diferentes.")
        ordem = sorted(comp, key=lambda l: l["desempenho"]["views_por_dia"], reverse=True)
        meio = len(ordem) // 2
        alto, baixo = ordem[:max(1, meio)], ordem[len(ordem) - max(1, meio):]
        for k, (rot, un) in CARAC.items():
            va = [l["metricas"].get(k) for l in alto]
            vb = [l["metricas"].get(k) for l in baixo]
            if any(v is None for v in va + vb):
                continue
            if max(va) < min(vb) or min(va) > max(vb):
                direcao = "menor" if max(va) < min(vb) else "maior"
                n = len(comp)
                hipoteses.append({
                    "metrica": k, "direcao": direcao,
                    "texto": (f"Nas referências com mais views/dia, {rot} foi {direcao} "
                              f"({', '.join(f'{l['nome']}: {l['metricas'][k]}{un}' for l in alto)}) do que nas com menos "
                              f"({', '.join(f'{l['nome']}: {l['metricas'][k]}{un}' for l in baixo)})."),
                    "evidencia": "muito fraca" if n <= 3 else "fraca",
                    "por_que": (f"Correlação em só {n} vídeos, sem dados de distribuição, promoção, público ou retenção. "
                                "Pode ser coincidência ou efeito de outra diferença (tema, canal, data).")})
        for k, rot in CARACTERISTICAS.items():
            va = [l["caracteristicas"].get(k) for l in alto]
            vb = [l["caracteristicas"].get(k) for l in baixo]
            if any(v is None for v in va + vb):
                continue
            if (all(va) and not any(vb)) or (not any(va) and all(vb)):
                direcao = "presente" if all(va) else "ausente"
                n = len(comp)
                hipoteses.append({
                    "metrica": k, "direcao": direcao, "tipo": "caracteristica_observada",
                    "texto": (f"Nas referências com mais views/dia, {rot} esteve {direcao} "
                              f"({', '.join(l['nome'] for l in alto)}); nas com menos, "
                              f"{'ausente' if direcao == 'presente' else 'presente'} ({', '.join(l['nome'] for l in baixo)})."),
                    "evidencia": "muito fraca" if n <= 3 else "fraca",
                    "por_que": (f"Observação do modelo de IA (pode errar) em só {n} vídeos, sem dados de distribuição, "
                                "público ou retenção. Pode ser coincidência ou efeito de outra diferença.")})
        if not hipoteses:
            notas.append("Nenhuma característica medida separou os vídeos de mais e menos views/dia: sem hipótese a propor.")
    notas.append("Não sabemos como cada plataforma distribuiu esses vídeos, se houve promoção paga, nem a retenção: "
                 "qualquer explicação de desempenho é hipótese, não causa.")
    # Padrões comuns entre as referências ANALISADAS (sem relação com desempenho): servem de inspiração.
    padroes = []
    for k, rot in CARACTERISTICAS.items():
        vals = [l["caracteristicas"][k] for l in linhas if l["caracteristicas"].get(k) is not None]
        if len(vals) >= 2 and sum(vals) / len(vals) >= 2 / 3:
            padroes.append({"metrica": k, "n": sum(vals), "de": len(vals),
                            "texto": f"{rot}: presente em {sum(vals)} de {len(vals)} referências analisadas pelo Gemini."})
    return {"referencias": linhas, "hipoteses": hipoteses, "padroes": padroes, "notas": notas, "meu": meu}


# ---------------------------------------------------------------- rotas
@bp.get("/api/referencias")
def rota_refs():
    out = []
    for arq in sorted(REFS.glob("*.json"), key=lambda a: a.stat().st_mtime, reverse=True):
        try:
            r = json.loads(arq.read_text(encoding="utf-8"))
            r["desempenho"] = desempenho(r)
            out.append(r)
        except (ValueError, OSError, KeyError):
            continue
    return jsonify(out)


@bp.post("/api/referencias")
def rota_nova_ref():
    j = A.corpo_json()
    if j.get("upload_id"):
        return jsonify(nova_ref_arquivo(j["upload_id"], j.get("nome"))), 201
    return jsonify(nova_ref_url(j.get("url"))), 201


@bp.put("/api/referencias/<rid>")
def rota_editar_ref(rid):
    j = A.corpo_json()
    ref = ler_ref(rid)
    if "metricas_usuario" in j:
        ref["metricas_usuario"] = validar_metricas_usuario(j["metricas_usuario"])
    if "tags" in j:
        tags = j["tags"] if isinstance(j["tags"], dict) else {}
        ref["tags"] = {k: str(v)[:60] for k, v in tags.items() if k in ("tema", "idioma", "formato", "legenda") and v}
    return jsonify(gravar_ref(ref))


@bp.delete("/api/referencias/<rid>")
def rota_apagar_ref(rid):
    arq = _arq_ref(rid)
    if arq.exists():
        arq.unlink()
    return jsonify({"ok": True})


@bp.post("/api/referencias/<rid>/analisar")
def rota_analisar_ref(rid):
    j = A.corpo_json()
    ler_ref(rid)
    prov = j.get("provedor", "local")
    t = T.iniciar("analise", f"Analisando referência ({prov})", analisar_ref, rid, prov, j.get("permitir_envio") is True)
    return jsonify(t.json()), 202


@bp.get("/api/quadro/<h>/<nome>")
def rota_quadro(h, nome):
    if not re.fullmatch(r"[a-f0-9]{32}", h) or not re.fullmatch(r"\d{5}\.\d{3}\.jpg", nome):
        abort(404)
    arq = E.CACHE / "quadros" / h / nome
    if not arq.is_file():
        abort(404)
    return send_file(arq, mimetype="image/jpeg")
