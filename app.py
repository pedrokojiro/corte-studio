#!/usr/bin/env python3
"""Corte Studio: front-end local para cortar vídeo em 9:16 com legenda, narração e trilha.

Roda no seu PC (http://127.0.0.1:5000). Nada sobe para a internet, exceto o texto da
narração, que vai ao serviço de voz gratuito (edge-tts) se você escolher "gerar com IA".
A transcrição (faster-whisper) roda local.

O app não verifica direitos autorais: a responsabilidade é sua.
"""
import asyncio
import difflib
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, jsonify, request, send_file, abort
from werkzeug.exceptions import HTTPException

ROOT = Path(__file__).resolve().parent
UP, OUT, TMP = ROOT / "uploads", ROOT / "saida", ROOT / "tmp"
for d in (UP, OUT, TMP):
    d.mkdir(exist_ok=True)

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 ** 3

W, H, FPS = 1080, 1920, 30
SR = 44100  # taxa de amostragem do áudio de trabalho
MIN_ELEGIVEL = 61.0  # Creator Rewards: vídeo acima de 1 minuto [DADO, fonte secundária]
ORIGENS = {"dominio_publico", "proprio", "licenciado"}
EXT_OK = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


class Erro(Exception):
    """Erro com mensagem para o usuário; `status` vira o código HTTP."""

    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


def _float(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return x if x == x and x > 0 else 0.0  # descarta NaN e não-positivos


def run(cmd, cwd=None):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise Erro(f"'{cmd[0]}' não encontrado. Instale o ffmpeg e coloque no PATH.")
    if r.returncode != 0:
        tail = " | ".join(r.stderr.strip().splitlines()[-6:])
        raise Erro(f"Falha no {cmd[0]}: {tail}")
    return r.stdout


def probe(path):
    out = run(["ffprobe", "-v", "error", "-show_entries",
               "format=duration:stream=codec_type,width,height,duration", "-of", "json", str(path)])
    try:
        info = json.loads(out or "{}")
    except ValueError:
        raise Erro("Não consegui ler as informações desse arquivo.")
    streams = info.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    dur = _float((info.get("format") or {}).get("duration")) or max(
        (_float(s.get("duration")) for s in streams), default=0.0)
    if not dur:
        raise Erro("Esse arquivo não informa a duração (comum em gravação 'ao vivo'). "
                   "Converta antes: ffmpeg -i entrada -c copy saida.mp4")
    return {
        "duracao": dur,
        "tem_audio": any(s.get("codec_type") == "audio" for s in streams),
        "largura": v.get("width") if v else None,
        "altura": v.get("height") if v else None,
    }


def achar(fid):
    if not re.fullmatch(r"[a-f0-9]{12}", fid or ""):
        raise Erro("Arquivo inválido")
    hits = list(UP.glob(f"{fid}.*"))
    if not hits:
        raise Erro("Arquivo não encontrado. Envie de novo.")
    return hits[0]


def num(v, lo, hi, padrao):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return padrao
    return max(lo, min(hi, x))


def slug(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s or "video").strip("-").lower()
    return s[:40] or "video"


# --------------------------------------------------------------- análise de cortes
def detectar(src, tem_audio, limiar_cena=0.30, silencio_db=-35, silencio_min=0.4):
    """Uma passada do ffmpeg: trocas de cena (vídeo reduzido a 5 fps) e pausas no áudio."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(src), "-map", "0:v:0",
           "-vf", f"fps=5,scale=320:-2,select='gt(scene,{limiar_cena})',showinfo"]
    if tem_audio:
        cmd += ["-map", "0:a:0", "-af", f"silencedetect=n={silencio_db}dB:d={silencio_min}"]
    cmd += ["-f", "null", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise Erro("'ffmpeg' não encontrado. Instale o ffmpeg e coloque no PATH.")
    if r.returncode != 0:
        raise Erro("Falha ao analisar o vídeo: " + " | ".join(r.stderr.strip().splitlines()[-3:]))
    cenas = [float(x) for x in re.findall(r"Parsed_showinfo.*?pts_time:\s*([\d.]+)", r.stderr)]
    ini = [max(0.0, float(x)) for x in re.findall(r"silence_start:\s*(-?[\d.]+)", r.stderr)]
    fim = [float(x) for x in re.findall(r"silence_end:\s*([\d.]+)", r.stderr)]
    return cenas, list(zip(ini, fim))


def sugerir(duracao, cenas, pausas, dmin, dmax, limite=60):
    """Cortes que começam e terminam em troca de cena ou pausa de fala, entre dmin e dmax segundos.

    Guloso, sem sobreposição: de cada ponto de início, pega o fim limpo mais distante dentro da faixa.
    """
    inicios = sorted({(0.0, "início do vídeo")} | {(t, "troca de cena") for t in cenas}
                     | {(round(e, 2), "fala começa após pausa") for _, e in pausas})
    fins = sorted({(duracao, "fim do vídeo")} | {(t, "troca de cena") for t in cenas}
                  | {(round(s, 2), "fala termina em pausa") for s, _ in pausas})
    out, livre = [], 0.0
    for s, ms in inicios:
        if s < livre:
            continue
        cands = [(e, me) for e, me in fins if dmin <= e - s <= dmax]
        if not cands:
            continue
        e, me = max(cands)
        out.append({"ini": round(s, 2), "fim": round(e, 2), "dur": round(e - s, 2), "motivo": f"{ms} → {me}"})
        livre = e
        if len(out) >= limite:
            break
    return out


# --------------------------------------------------------------- legenda
def ass_time(t):
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _norm(w):
    return re.sub(r"\W", "", unicodedata.normalize("NFKD", w.lower()).encode("ascii", "ignore").decode())


def alinhar(palavras, tempos):
    """Dá um tempo a cada palavra do texto (talvez corrigido à mão) casando com as palavras faladas.

    Palavras iguais herdam o tempo; trechos trocados dividem o tempo do trecho original;
    palavras inseridas ficam entre as vizinhas.
    """
    a = [_norm(w) for _, _, w in tempos]
    b = [_norm(w) for w in palavras]
    out = [None] * len(palavras)

    def espalha(j1, j2, s, e):
        n = j2 - j1
        for k in range(n):
            out[j1 + k] = (s + (e - s) * k / n, s + (e - s) * (k + 1) / n, palavras[j1 + k])

    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                out[j1 + k] = (tempos[i1 + k][0], tempos[i1 + k][1], palavras[j1 + k])
        elif op == "replace":
            espalha(j1, j2, tempos[i1][0], tempos[i2 - 1][1])
        elif op == "insert":
            s = tempos[i1 - 1][1] if i1 > 0 else tempos[0][0]
            e = tempos[i1][0] if i1 < len(tempos) else tempos[-1][1]
            espalha(j1, j2, s, max(s, e))
    return out


def grupos(texto, dur_fala, tempos=None, n=3):
    """Legenda em grupos de n palavras: [(ini_s, fim_s, texto)].

    Com `tempos` [(ini_s, fim_s, palavra)] (edge-tts ou transcrição), cada grupo começa
    quando a 1a palavra é falada. Sem `tempos`, divide a duração pelo tamanho do texto.
    """
    palavras = texto.replace("\n", " ").split()
    if tempos:
        tempos = alinhar(palavras, tempos)
        ev = []
        for k in range(0, len(palavras), n):
            fim = tempos[k + n][0] if k + n < len(tempos) else max(dur_fala, tempos[-1][1])
            ev.append((tempos[k][0], fim, " ".join(palavras[k:k + n])))
        return ev
    blocos = [palavras[k:k + n] for k in range(0, len(palavras), n)]
    pesos = [sum(len(w) + 1 for w in g) for g in blocos]
    soma = sum(pesos) or 1
    t, ini = 0.15, 0.15
    ev = []
    for g, p in zip(blocos, pesos):
        d = (dur_fala - ini) * p / soma
        ev.append((t, t + d, " ".join(g)))
        t += d
    return ev


def ass_doc(eventos, fonte, maiuscula):
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\nWrapStyle: 2\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{fonte},88,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,7,2,2,60,60,480,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    linhas = []
    for t0, t1, txt in eventos:
        if maiuscula:
            txt = txt.upper()
        txt = txt.replace("{", "(").replace("}", ")")
        linhas.append(f"Dialogue: 0,{ass_time(t0)},{ass_time(t1)},Default,,0,0,0,,{txt}")
    return head + "\n".join(linhas) + "\n"


def build_ass(texto, dur_fala, fonte, maiuscula, tempos=None):
    return ass_doc(grupos(texto, dur_fala, tempos), fonte, maiuscula)


# --------------------------------------------------------------- narração
def gerar_voz(texto, voz, taxa, destino):
    """Gera a narração em `destino` e devolve o tempo de cada palavra ([] se não vier)."""
    try:
        import edge_tts
    except ImportError:
        raise Erro("edge-tts não instalado. Rode: pip install -r requirements.txt")

    tempos = []

    async def go():
        try:
            com = edge_tts.Communicate(texto, voz, rate=taxa, boundary="WordBoundary")
        except TypeError:  # edge-tts < 7 não tem `boundary`; lá WordBoundary já é o padrão
            com = edge_tts.Communicate(texto, voz, rate=taxa)
        with open(destino, "wb") as f:
            async for ch in com.stream():
                if ch["type"] == "audio":
                    f.write(ch["data"])
                elif ch["type"] == "WordBoundary":  # offset/duração em unidades de 100 ns
                    t0 = ch["offset"] / 1e7
                    tempos.append((t0, t0 + ch["duration"] / 1e7, ch["text"]))

    try:
        asyncio.run(go())
    except Exception as e:
        raise Erro(f"Não consegui gerar a voz ({type(e).__name__}). Verifique a internet ou "
                   "use a opção 'enviar áudio'.")
    if not destino.exists() or destino.stat().st_size == 0:
        raise Erro("A voz saiu vazia. Tente de novo ou envie um áudio seu.")
    return tempos


# --------------------------------------------------------------- transcrição
MODELO_WHISPER = "small"
_modelo, _lock_modelo = None, threading.Lock()


def transcrever(src, trechos=((0.0, None),)):
    """Transcreve a fala (PT-BR) dos trechos, emendados na ordem: (texto, [(ini_s, fim_s, palavra)]).

    Os tempos ficam na linha do tempo do vídeo final (`linha_do_tempo`), não na soma nominal.
    """
    # Anaconda: numpy (MKL) e ctranslate2 trazem cada um sua cópia do OpenMP e o processo aborta sem isto.
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    try:
        from faster_whisper import WhisperModel
        import numpy as np
    except ImportError:
        raise Erro("Transcrição não instalada. Rode: pip install faster-whisper")
    global _modelo
    with _lock_modelo:
        try:
            if _modelo is None:
                _modelo = WhisperModel(MODELO_WHISPER, device="cpu", compute_type="int8")
        except Exception as e:
            raise Erro(f"Não consegui carregar o modelo de transcrição ({type(e).__name__}). Na 1a vez "
                       "ele é baixado da internet (~480 MB).")
        # Um trecho por vez: emendados, o Whisper chega a pular fala repetida entre cortes.
        tempos = []
        cortados = [t for t in trechos if t[1] is not None]
        inicios = linha_do_tempo(cortados) if len(cortados) == len(trechos) else None
        offset = 0.0
        for k, (ini, fim) in enumerate(trechos):
            if inicios is not None:
                offset = inicios[k]
            cmd = ["ffmpeg", "-loglevel", "error", "-ss", f"{ini:.3f}"]
            cmd += ["-to", f"{fim:.3f}"] if fim is not None else []
            cmd += ["-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"]
            r = subprocess.run(cmd, capture_output=True)  # decodifica com o ffmpeg; evita o módulo `av`
            if r.returncode != 0:
                raise Erro("Não consegui ler o áudio para transcrever.")
            audio = np.frombuffer(r.stdout, np.float32)
            if audio.size >= 16000 * 0.3:
                segs, _ = _modelo.transcribe(audio, language="pt", word_timestamps=True, vad_filter=True)
                tempos += [(round(offset + float(w.start), 3), round(offset + float(w.end), 3), w.word.strip())
                           for s in segs for w in (s.words or []) if w.word.strip()]
            if inicios is None:
                offset += audio.size / 16000
    return " ".join(w for _, _, w in tempos), tempos


def ler_tempos(v):
    """Tempos vindos do navegador: [[ini, fim, palavra], ...]. Qualquer coisa estranha vira []."""
    try:
        t = [(float(a), float(b), str(c)) for a, b, c in (v or [])[:50000]]
    except (TypeError, ValueError):
        return []
    return t if all(x[0] <= x[1] for x in t) else []


def quadros(t):
    """Segundos -> número inteiro de quadros (grade de 1/FPS)."""
    return int(round(t * FPS))


def ler_cortes(lista, duracao):
    """Cortes válidos, alinhados à grade de quadros: a mesma base de tempo no render e na transcrição."""
    ultimo = math.floor(duracao * FPS + 1e-6)
    cortes = []
    for c in lista or []:
        if not isinstance(c, dict):
            continue
        a = quadros(num(c.get("ini"), 0, duracao, 0))
        b = min(quadros(num(c.get("fim"), 0, duracao, 0)), ultimo)
        if (b - a) / FPS >= 0.3:
            cortes.append((a / FPS, b / FPS))
    if not cortes:
        raise Erro("Adicione pelo menos um corte (fim maior que o início).")
    return cortes


def linha_do_tempo(cortes):
    """Início de cada corte no vídeo final (s). Cada clipe tem exatamente round(dur*FPS) quadros."""
    inicios, n = [], 0
    for a, b in cortes:
        inicios.append(n / FPS)
        n += quadros(b - a)
    return inicios


def chave_transcricao(video_id=None, cortes=None, audio_id=None):
    """Identifica de onde veio uma transcrição; se mudar, o tempo das palavras ficou velho."""
    if audio_id:
        return f"a:{audio_id}"
    return f"v:{video_id}:" + ";".join(f"{quadros(a)}-{quadros(b)}" for a, b in cortes)


NOME_SAIDA = re.compile(r"[a-z0-9-]{1,40}_\d{8}-\d{6}(?:_[a-f0-9]{6})?\.mp4")


def nome_saida(nome):
    """Nome único em saida/: slug + data-hora + sufixo aleatório (renders simultâneos não colidem)."""
    base = f"{slug(nome)}_{time.strftime('%Y%m%d-%H%M%S')}"
    while True:
        n = f"{base}_{uuid.uuid4().hex[:6]}"
        if not (OUT / f"{n}.mp4").exists() and not (OUT / f"{n}.json").exists():
            return n


def salvar_par(mp4_tmp, registro):
    """Move o vídeo para saida/ e grava o .json com o mesmo nome."""
    destino = OUT / mp4_tmp.name
    shutil.move(str(mp4_tmp), destino)
    destino.with_suffix(".json").write_text(json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8")
    return destino


# --------------------------------------------------------------- render
def renderizar(job):
    origem = job.get("origem") if job.get("origem") in ORIGENS else None  # opcional
    prova = (job.get("prova") or "").strip() or None

    src = achar(job.get("video_id"))
    info = probe(src)
    cortes = ler_cortes(job.get("cortes"), info["duracao"])

    cx = num(job.get("crop_x"), 0, 1, 0.5)
    texto = (job.get("texto") or "").strip()
    modo = job.get("narracao", "nenhuma")
    narrando = modo in ("ia", "arquivo")
    if narrando and modo == "ia" and not texto:
        raise Erro("Escreva o texto da narração.")
    vol_orig = num(job.get("volume_original"), 0, 1, 0.12 if narrando else 1.0)
    vol_musica = num(job.get("volume_musica"), 0, 1, 0.10)
    fonte = (job.get("fonte") or "Arial").replace(",", " ")[:40]
    avisos = []

    work = TMP / uuid.uuid4().hex[:10]
    work.mkdir()
    try:
        # Cada clipe: exatamente n quadros e n*SR/FPS amostras (PCM, sem atraso de AAC),
        # começando em 0. Assim a emenda não acumula deriva nem desalinha áudio e vídeo.
        clips = []
        for i, (ini, fim) in enumerate(cortes):
            n = quadros(fim - ini)
            dur = n / FPS
            # Busca meio quadro antes: o quadro exato de `ini` não pode cair antes do ponto de busca
            # por arredondamento. O áudio descarta esse meio quadro e começa na amostra de `ini`.
            folga = min(ini, 0.5 / FPS)
            clip = work / f"c{i:02d}.mkv"
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{ini - folga:.6f}", "-t", f"{dur + 0.5:.4f}",
                   "-i", str(src)]
            if not info["tem_audio"]:
                cmd += ["-f", "lavfi", "-t", f"{dur + 0.5:.4f}", "-i", f"anullsrc=r={SR}:cl=stereo"]
            fa = "0:a:0" if info["tem_audio"] else "1:a:0"
            corta_a = round(folga * SR) if info["tem_audio"] else 0
            fc = (f"[0:v:0]setpts=PTS-STARTPTS,crop=w='min(iw,ih*9/16)':h=ih:x='(iw-ow)*{cx:.4f}':y=0,"
                  f"scale={W}:{H}:flags=lanczos,setsar=1,fps={FPS},tpad=stop_mode=clone:stop_duration=1,"
                  f"trim=end_frame={n},format=yuv420p[v];"
                  f"[{fa}]aresample={SR},asetpts=PTS-STARTPTS,atrim=start_sample={corta_a},asetpts=PTS-STARTPTS,"
                  f"apad,atrim=end_sample={round(dur * SR)}[a]")
            cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-c:a", "pcm_s16le", "-ar", str(SR), "-ac", "2", str(clip)]
            run(cmd)
            clips.append(clip)

        (work / "lista.txt").write_text("".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "lista.txt",
             "-c", "copy", "base.mkv"], cwd=work)
        d_video = sum(quadros(b - a) for a, b in cortes) / FPS

        narr, d_narr = None, 0.0
        tempos, sync = [], None
        quer_legenda = bool(job.get("legenda")) and bool(texto)
        tempos_cli = ler_tempos(job.get("tempos"))
        if modo == "ia":
            narr = work / "narracao.mp3"
            tempos = gerar_voz(texto, job.get("voz") or "pt-BR-AntonioNeural", job.get("taxa") or "-5%", narr)
        elif modo == "arquivo":
            narr = achar(job.get("audio_id"))
            chave = chave_transcricao(audio_id=job.get("audio_id"))
        else:
            chave = chave_transcricao(job.get("video_id"), cortes)
        if narr:
            d_narr = probe(narr)["duracao"]

        if quer_legenda and modo != "ia":
            if tempos_cli and job.get("tempos_chave") == chave:
                tempos = tempos_cli
            else:
                motivo = ("a transcrição era de outro corte ou áudio" if tempos_cli
                          else "o texto não foi transcrito")
                try:  # refaz a transcrição da fonte atual e casa com o texto (que pode ter sido editado)
                    trechos = [(0.0, None)] if modo == "arquivo" else cortes
                    _, tempos = transcrever(narr if modo == "arquivo" else src, trechos)
                    if not tempos:
                        raise Erro("não encontrei fala")
                    sync = "por_palavra_retranscrita"
                    avisos.append(f"Legenda sincronizada de novo ({motivo}). Confira se o texto ainda "
                                  "corresponde à fala.")
                except Erro as e:
                    tempos = []
                    detalhe = f"{motivo}; não deu para transcrever agora: {e}"
                    if modo == "arquivo":
                        avisos.append(f"Legenda proporcional ao texto, sem tempo por palavra ({detalhe}).")
                    else:
                        avisos.append(f"Legenda NÃO gerada ({detalhe}). Clique em 'Transcrever fala' "
                                      "e renderize de novo.")
        legenda = quer_legenda and bool(narr or tempos)
        if legenda:
            sync = sync or ("por_palavra" if tempos else "proporcional")
            (work / "legenda.ass").write_text(
                build_ass(texto, d_narr if narr else d_video, fonte, bool(job.get("maiuscula", True)), tempos),
                encoding="utf-8")
        elif bool(job.get("legenda")) and not texto and modo != "ia":
            avisos.append("Legenda marcada, mas o texto está vazio: o vídeo saiu sem legenda.")

        total = max(d_video, d_narr + 0.4) if narr else d_video
        extra = max(0.0, total - d_video)
        musica = achar(job["musica_id"]) if job.get("musica_id") else None

        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", "base.mkv"]
        idx = 1
        labels = ["[a0]"]
        fc_a = [f"[0:a]volume={vol_orig:.3f},apad[a0]"]
        if narr:
            cmd += ["-i", str(narr)]
            fc_a.append(f"[{idx}:a]aresample={SR},apad[a{idx}]")
            labels.append(f"[a{idx}]")
            idx += 1
        if musica:
            cmd += ["-stream_loop", "-1", "-i", str(musica)]
            fc_a.append(f"[{idx}:a]aresample={SR},volume={vol_musica:.3f}[a{idx}]")
            labels.append(f"[a{idx}]")
            idx += 1
        n = len(labels)
        fc_a.append("".join(labels) + f"amix=inputs={n}:duration=longest:dropout_transition=0,volume={n}[aout]")

        vchain = []
        if extra > 0:
            vchain.append(f"tpad=stop_mode=clone:stop_duration={extra:.3f}")
        if legenda:
            vchain.append("ass=legenda.ass")
        fc_v = "[0:v]" + (",".join(vchain) if vchain else "null") + "[vout]"

        nome = nome_saida(job.get("nome"))
        final = work / f"{nome}.mp4"
        cmd += ["-filter_complex", ";".join([fc_v] + fc_a), "-map", "[vout]", "-map", "[aout]",
                "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", final.name]
        run(cmd, cwd=work)

        dur_final = probe(final)["duracao"]
        if dur_final < MIN_ELEGIVEL:
            avisos.insert(0, f"{dur_final:.0f}s: abaixo de 1 minuto, não conta para o Creator Rewards "
                             "[DADO, fonte secundária].")
        registro = {
            "arquivo": final.name, "criado_em": time.strftime("%Y-%m-%d %H:%M:%S"),
            "origem_declarada": origem, "prova_declarada": prova,
            "video_fonte": job.get("nome_original"), "cortes": cortes,
            "narracao": modo, "legenda_sync": sync if legenda else None,
            "duracao_s": round(dur_final, 2), "avisos": avisos,
            "aviso": "Origem opcional, autodeclarada pelo usuário. O app não verifica status legal.",
        }
        salvar_par(final, registro)
        return {"arquivo": final.name, "duracao": dur_final, "aviso": " ".join(avisos), "avisos": avisos,
                "legenda_sync": registro["legenda_sync"]}
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --------------------------------------------------------------- rotas
HOSTS_LOCAIS = {"127.0.0.1", "localhost", "::1"}


def _host_local(valor):
    try:
        return (urlparse("//" + (valor or "")).hostname or "") in HOSTS_LOCAIS
    except ValueError:
        return False


@app.before_request
def so_o_navegador_local():
    """O app escuta só em 127.0.0.1, mas qualquer site aberto no navegador consegue mandar
    requisições para lá. Recusa Host estranho (DNS rebinding) e POST vindo de outro site."""
    if not _host_local(request.host):
        raise Erro("Acesso recusado: o Corte Studio só atende em 127.0.0.1 ou localhost.", 403)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origem = request.headers.get("Origin")
        de_fora = request.headers.get("Sec-Fetch-Site") == "cross-site"
        if de_fora or (origem is not None and not _host_local(urlparse(origem).netloc)):
            raise Erro("Requisição de outro site recusada.", 403)


def corpo_json():
    if not request.is_json:
        raise Erro("Envie os dados como JSON (Content-Type: application/json).", 415)
    j = request.get_json(silent=True)
    if not isinstance(j, dict):
        raise Erro("JSON inválido.")
    return j


@app.errorhandler(Erro)
def erro(e):
    return jsonify({"erro": str(e)}), getattr(e, "status", 400)


@app.errorhandler(HTTPException)
def erro_http(e):
    msg = {404: "Não encontrado.", 413: "Arquivo grande demais (limite de 8 GB).",
           405: "Método não permitido."}.get(e.code, e.description)
    return jsonify({"erro": msg}), e.code


@app.errorhandler(Exception)
def erro_inesperado(e):
    app.logger.exception("Erro inesperado")
    return jsonify({"erro": f"Erro interno ({type(e).__name__}): {e}. Detalhes no terminal do app."}), 500


@app.get("/")
def index():
    return send_file(ROOT / "index.html")


@app.post("/upload")
def upload():
    f = request.files.get("arquivo")
    if not f or not f.filename:
        raise Erro("Nenhum arquivo enviado.")
    ext = Path(f.filename).suffix.lower()
    if ext not in EXT_OK:
        raise Erro(f"Formato {ext or '(sem extensão)'} não suportado.")
    fid = uuid.uuid4().hex[:12]
    dest = UP / f"{fid}{ext}"
    try:
        f.save(dest)
        info = probe(dest)
    except Erro as e:
        dest.unlink(missing_ok=True)
        raise Erro(f"Não consegui ler esse arquivo de mídia. {e}")
    except BaseException:
        dest.unlink(missing_ok=True)  # nada de arquivo pela metade em uploads/
        raise
    return jsonify({"id": fid, "nome": f.filename, **info})


@app.get("/media/<fid>")
def media(fid):
    try:
        return send_file(achar(fid), conditional=True)
    except Erro:
        abort(404)


@app.post("/render")
def render():
    return jsonify(renderizar(corpo_json()))


@app.post("/transcrever")
def rota_transcrever():
    job = corpo_json()
    t0 = time.time()
    if job.get("audio_id"):  # narração enviada
        texto, tempos = transcrever(achar(job["audio_id"]))
        chave = chave_transcricao(audio_id=job["audio_id"])
    else:  # fala original do vídeo, só nos cortes, na ordem da lista
        src = achar(job.get("video_id"))
        info = probe(src)
        if not info["tem_audio"]:
            raise Erro("Esse vídeo não tem áudio para transcrever.")
        cortes = ler_cortes(job.get("cortes"), info["duracao"])
        texto, tempos = transcrever(src, cortes)
        chave = chave_transcricao(job.get("video_id"), cortes)
    if not tempos:
        raise Erro("Não encontrei fala nesse trecho.")
    # `chave` volta no /render como `tempos_chave`: se não bater, a transcrição está velha.
    return jsonify({"texto": texto, "tempos": tempos, "chave": chave, "segundos": round(time.time() - t0, 1)})


@app.post("/analisar")
def analisar():
    job = corpo_json()
    src = achar(job.get("video_id"))
    info = probe(src)
    dmin = num(job.get("min"), 1, 600, 15)
    dmax = num(job.get("max"), dmin, 600, max(dmin, 60))
    t0 = time.time()
    cenas, pausas = detectar(src, info["tem_audio"])
    return jsonify({"sugestoes": sugerir(info["duracao"], cenas, pausas, dmin, dmax),
                    "cenas": len(cenas), "pausas": len(pausas), "segundos": round(time.time() - t0, 1)})


@app.get("/saida/<nome>")
def baixar(nome):
    # Só nomes no formato que o app gera: bloqueia CON/AUX/NUL e afins (travam no Windows) e ../
    if not NOME_SAIDA.fullmatch(nome) or not (OUT / nome).is_file():
        abort(404)
    return send_file(OUT / nome, conditional=True, as_attachment=request.args.get("dl") == "1")


if __name__ == "__main__":
    print("Corte Studio em http://127.0.0.1:5000  (Ctrl+C para sair)")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
