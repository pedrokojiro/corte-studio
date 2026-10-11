#!/usr/bin/env python3
"""Pipeline dark/IA: roteiro JSON + imagens geradas fora -> MP4 vertical com voz e legenda.

Cada roteiro fica numa pasta própria:
    projetos/<id>/roteiro.json
    projetos/<id>/img_01.png, img_02.png ...      (você gera fora; png/jpg/webp)
    projetos/<id>/audio_01.mp3 ...                 (opcional: se existir, é usado no lugar do edge-tts)

Comandos:
    python dark.py auditar projetos/<id>/roteiro.json
    python dark.py prompts projetos/<id>/roteiro.json
    python dark.py voz     projetos/<id>/roteiro.json [--refazer]
    python dark.py render  projetos/<id>/roteiro.json [--trilha musica.mp3] [--vol-trilha 0.10]
    python dark.py tudo    projetos/<id>/roteiro.json [--trilha ...]
    python dark.py lote    projetos                   [--trilha ...]

Não gera imagens e não publica nada.
"""
import argparse
import json
import shutil
import sys
import time
import uuid
from pathlib import Path

import app as A

CPS = 15.0           # caracteres falados por segundo [ESTIMATIVA]
PAUSA = 0.35         # respiro depois de cada cena (s)
CAUDA = 0.8          # respiro no fim do vídeo (s)
MIN_AUDITORIA = 65.0  # margem sobre os 61 s do Creator Rewards, porque a duração é estimada
MAX_GANCHO = 20      # palavras na 1a cena
ZOOM = 0.12          # zoom máximo (12%)
EXT_IMG = (".png", ".jpg", ".jpeg", ".webp")
EXT_AUD = (".mp3", ".wav", ".m4a")
LEMBRETE = ('Ao postar: ative o toggle "conteúdo gerado por IA" no TikTok (voz e imagens sintéticas) '
            "e deixe claro na legenda que é ficção.")


# --------------------------------------------------------------- roteiro
def carregar(caminho):
    caminho = Path(caminho)
    try:
        r = json.loads(caminho.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise A.Erro(f"Roteiro não encontrado: {caminho}")
    except json.JSONDecodeError as e:
        raise A.Erro(f"JSON inválido em {caminho.name}: linha {e.lineno}, coluna {e.colno}")
    falta = [k for k in ("id", "titulo", "cenas") if not r.get(k)]
    if falta:
        raise A.Erro(f"Campos obrigatórios faltando: {', '.join(falta)}")
    if not isinstance(r["cenas"], list) or not all(isinstance(c, dict) and (c.get("texto") or "").strip()
                                                    for c in r["cenas"]):
        raise A.Erro("'cenas' precisa ser uma lista de {texto, prompt_imagem} com texto preenchido.")
    r.setdefault("voz", "pt-BR-AntonioNeural")
    r.setdefault("taxa", "-5%")
    r.setdefault("estilo_visual", "")
    r["_pasta"] = caminho.resolve().parent
    return r


def achar_arquivo(pasta, base, exts):
    return next((pasta / f"{base}{e}" for e in exts if (pasta / f"{base}{e}").exists()), None)


def n2(i):
    return f"{i + 1:02d}"


# --------------------------------------------------------------- auditar
def auditar(r):
    """Devolve (erros, avisos, resumo). Erro impede o render; aviso não."""
    erros, avisos = [], []
    cenas, pasta = r["cenas"], r["_pasta"]
    sem_img = [f"img_{n2(i)}" for i in range(len(cenas)) if not achar_arquivo(pasta, f"img_{n2(i)}", EXT_IMG)]
    if sem_img:
        erros.append("Imagens faltando: " + ", ".join(sem_img))

    gancho = len(cenas[0]["texto"].split())
    if gancho > MAX_GANCHO:
        erros.append(f"Gancho (cena 1) com {gancho} palavras; máximo {MAX_GANCHO}.")

    chars = sum(len(c["texto"].strip()) for c in cenas)
    estimada = chars / CPS + PAUSA * len(cenas) + CAUDA - PAUSA
    reais = [achar_arquivo(pasta, f"audio_{n2(i)}", EXT_AUD) for i in range(len(cenas))]
    real = None
    if all(reais):
        real = sum(A.probe(a)["duracao"] for a in reais) + PAUSA * len(cenas) + CAUDA - PAUSA
    dur = real if real is not None else estimada
    if dur < MIN_AUDITORIA:
        avisos.append(f"Duração {'medida' if real else 'estimada'} {dur:.0f}s: abaixo de {MIN_AUDITORIA:.0f}s. "
                      "Abaixo de 61s não conta para o Creator Rewards [DADO, fonte secundária].")

    if r.get("ficcao") is not True:
        avisos.append("'ficcao' não está true. Se é lenda ou história inventada, marque como ficção; "
                      "nunca apresente como 'história real'.")
    texto_todo = " ".join(c["texto"] for c in cenas).lower()
    if r.get("ficcao") is True and any(s in texto_todo for s in ("história real", "historia real",
                                                                 "aconteceu de verdade", "caso real")):
        avisos.append("Roteiro marcado como ficção, mas o texto diz que é real. Contradição: corrija o texto.")
    sem_prompt = [n2(i) for i, c in enumerate(cenas) if not (c.get("prompt_imagem") or "").strip()]
    if sem_prompt:
        avisos.append("Cenas sem prompt_imagem: " + ", ".join(sem_prompt))

    resumo = {"cenas": len(cenas), "caracteres": chars, "duracao_estimada_s": round(estimada, 1),
              "duracao_medida_s": round(real, 1) if real is not None else None,
              "palavras_gancho": gancho}
    return erros, avisos, resumo


def cmd_auditar(r, _args):
    erros, avisos, resumo = auditar(r)
    print(f"[{r['id']}] {r['titulo']}")
    print(f"  {resumo['cenas']} cenas | {resumo['caracteres']} caracteres | estimada "
          f"{resumo['duracao_estimada_s']}s a {CPS:.0f} car/s [ESTIMATIVA]"
          + (f" | medida {resumo['duracao_medida_s']}s" if resumo["duracao_medida_s"] else "")
          + f" | gancho {resumo['palavras_gancho']} palavras")
    for e in erros:
        print("  ERRO:", e)
    for a in avisos:
        print("  AVISO:", a)
    if not erros and not avisos:
        print("  OK")
    return not erros


# --------------------------------------------------------------- prompts
def cmd_prompts(r, _args):
    estilo = (r.get("estilo_visual") or "").strip()
    linhas = []
    for i, c in enumerate(r["cenas"]):
        p = (c.get("prompt_imagem") or "").strip()
        linhas.append(f"img_{n2(i)}.png: " + ", ".join(x for x in (p, estilo) if x))
    saida = r["_pasta"] / "prompts.txt"
    saida.write_text("\n\n".join(linhas) + "\n", encoding="utf-8")
    print("\n\n".join(linhas))
    print(f"\nSalvo em {saida}")
    return True


# --------------------------------------------------------------- voz
def cmd_voz(r, args):
    pasta = r["_pasta"]
    for i, c in enumerate(r["cenas"]):
        existente = achar_arquivo(pasta, f"audio_{n2(i)}", EXT_AUD)
        if existente and not args.refazer:
            print(f"  cena {n2(i)}: usando {existente.name}")
            continue
        destino = pasta / f"audio_{n2(i)}.mp3"
        tempos = A.gerar_voz(c["texto"].strip(), r["voz"], r["taxa"], destino)
        (pasta / f"tempos_{n2(i)}.json").write_text(json.dumps(tempos, ensure_ascii=False), encoding="utf-8")
        print(f"  cena {n2(i)}: {destino.name} {A.probe(destino)['duracao']:.1f}s, {len(tempos)} palavras com tempo")
    return True


# --------------------------------------------------------------- render
def filtro_zoom(frames, entrando):
    """Zoom suave centralizado. A imagem é ampliada 2x antes para reduzir o tremido do zoompan."""
    passo = f"{ZOOM}*on/{max(frames - 1, 1)}"
    z = f"1+{passo}" if entrando else f"{1 + ZOOM}-{passo}"
    return (f"scale={A.W * 2}:{A.H * 2}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={A.W * 2}:{A.H * 2},setsar=1,"
            f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={A.W}x{A.H}:fps={A.FPS},"
            "format=yuv420p")


def tempos_cena(pasta, i, audio):
    """Tempo por palavra só vale se veio do edge-tts para este mesmo áudio."""
    tj = pasta / f"tempos_{n2(i)}.json"
    if audio.suffix == ".mp3" and tj.exists() and tj.stat().st_mtime >= audio.stat().st_mtime - 5:
        return [tuple(t) for t in json.loads(tj.read_text(encoding="utf-8"))]
    return []


def cmd_render(r, args):
    erros, avisos, _ = auditar(r)
    if erros:
        raise A.Erro("Auditoria com erro: " + " | ".join(erros))
    pasta, cenas = r["_pasta"], r["cenas"]
    audios = [achar_arquivo(pasta, f"audio_{n2(i)}", EXT_AUD) for i in range(len(cenas))]
    if not all(audios):
        faltam = [f"audio_{n2(i)}" for i, a in enumerate(audios) if not a]
        raise A.Erro(f"Áudio faltando ({', '.join(faltam)}). Rode 'voz' antes ou coloque seus audio_XX.mp3.")
    trilha = Path(args.trilha).resolve() if getattr(args, "trilha", None) else None
    if trilha and not trilha.exists():
        raise A.Erro(f"Trilha não encontrada: {trilha}")

    work = A.TMP / ("dark_" + uuid.uuid4().hex[:8])
    work.mkdir()
    try:
        clips, sync = [], []
        for i, c in enumerate(cenas):
            img = achar_arquivo(pasta, f"img_{n2(i)}", EXT_IMG)
            d_aud = A.probe(audios[i])["duracao"]
            dur = d_aud + (CAUDA if i == len(cenas) - 1 else PAUSA)
            frames = round(dur * A.FPS)
            tempos = tempos_cena(pasta, i, audios[i])
            sync.append("por_palavra" if tempos else "proporcional")
            (work / f"l{n2(i)}.ass").write_text(
                A.ass_doc(A.grupos(c["texto"].strip(), d_aud, tempos), args.fonte, True), encoding="utf-8")
            vf = filtro_zoom(frames, entrando=(i % 2 == 0)) + f",ass=l{n2(i)}.ass"
            clip = f"c{n2(i)}.mp4"
            A.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(img), "-i", str(audios[i]),
                   "-filter_complex", f"[0:v]{vf}[v];[1:a]aresample=44100,apad[a]",
                   "-map", "[v]", "-map", "[a]", "-t", f"{frames / A.FPS:.3f}",
                   "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", str(A.FPS),
                   "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2", clip], cwd=work)
            clips.append(clip)
            print(f"  cena {n2(i)}: {dur:.1f}s, zoom {'entrada' if i % 2 == 0 else 'saída'}, legenda {sync[-1]}")

        (work / "lista.txt").write_text("".join(f"file '{c}'\n" for c in clips), encoding="utf-8")
        A.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "lista.txt",
               "-c", "copy", "base.mp4"], cwd=work)
        total = A.probe(work / "base.mp4")["duracao"]

        nome = f"{A.slug(r['id'])}_{time.strftime('%Y%m%d-%H%M%S')}"
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", "base.mp4"]
        if trilha:
            cmd += ["-stream_loop", "-1", "-i", str(trilha), "-filter_complex",
                    f"[1:a]aresample=44100,volume={args.vol_trilha:.3f}[m];"
                    "[0:a][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]",
                    "-map", "0:v", "-map", "[a]"]
        else:
            cmd += ["-map", "0:v", "-map", "0:a"]
        cmd += ["-t", f"{total:.3f}", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                "-movflags", "+faststart", f"{nome}.mp4"]
        A.run(cmd, cwd=work)
        A.OUT.mkdir(exist_ok=True)
        shutil.move(str(work / f"{nome}.mp4"), A.OUT / f"{nome}.mp4")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    final = A.OUT / f"{nome}.mp4"
    dur_final = A.probe(final)["duracao"]
    registro = {
        "arquivo": final.name, "criado_em": time.strftime("%Y-%m-%d %H:%M:%S"),
        "roteiro_id": r["id"], "titulo": r["titulo"], "ficcao": r.get("ficcao"),
        "origem_declarada": "proprio",
        "prova_declarada": "Roteiro original e imagens geradas por IA pelo usuário (fora do app).",
        "pasta_roteiro": str(pasta), "voz": r["voz"], "legenda_sync": sync,
        "trilha": trilha.name if trilha else None, "duracao_s": round(dur_final, 2),
        "aviso": "Origem autodeclarada pelo usuário. O app não verifica status legal.",
    }
    (A.OUT / f"{nome}.json").write_text(json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> {final} ({dur_final:.1f}s)")
    if dur_final < A.MIN_ELEGIVEL:
        print(f"  AVISO: {dur_final:.0f}s, abaixo de 1 minuto: não conta para o Creator Rewards "
              "[DADO, fonte secundária].")
    for a in avisos:
        print("  AVISO:", a)
    print("  " + LEMBRETE)
    return True


def cmd_tudo(r, args):
    if not cmd_auditar(r, args):
        return False
    cmd_voz(r, args)
    return cmd_render(r, args)


def cmd_lote(pasta, args):
    roteiros = sorted(Path(pasta).glob("*/roteiro.json"))
    if not roteiros:
        raise A.Erro(f"Nenhum */roteiro.json em {pasta}")
    falhas = []
    for p in roteiros:
        print(f"\n=== {p.parent.name} ===")
        try:
            if not cmd_tudo(carregar(p), args):
                falhas.append(p.parent.name)
        except A.Erro as e:
            print("  ERRO:", e)
            falhas.append(p.parent.name)
    print(f"\nLote: {len(roteiros) - len(falhas)} ok, {len(falhas)} com erro"
          + (f" ({', '.join(falhas)})" if falhas else ""))
    return not falhas


COMANDOS = {"auditar": cmd_auditar, "prompts": cmd_prompts, "voz": cmd_voz,
            "render": cmd_render, "tudo": cmd_tudo}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Pipeline dark/IA do Corte Studio (sem UI).")
    ap.add_argument("comando", choices=[*COMANDOS, "lote"])
    ap.add_argument("alvo", help="roteiro.json (ou a pasta de projetos, no 'lote')")
    ap.add_argument("--trilha", help="arquivo de trilha (livre de direitos), opcional")
    ap.add_argument("--vol-trilha", type=float, default=0.10)
    ap.add_argument("--fonte", default="Arial")
    ap.add_argument("--refazer", action="store_true", help="regera audio_XX.mp3 mesmo se já existir")
    args = ap.parse_args(argv)
    try:
        if args.comando == "lote":
            ok = cmd_lote(args.alvo, args)
        else:
            ok = COMANDOS[args.comando](carregar(args.alvo), args)
    except A.Erro as e:
        print("ERRO:", e)
        return 1
    return 0 if ok else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
