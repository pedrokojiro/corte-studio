"""Linha do tempo com vários cortes: cada marca (bipe + flash de 1 quadro) precisa cair onde a
transcrição/legenda espera, em todas as fronteiras de corte.

Separa as duas fontes de erro:
- deriva da linha do tempo (este arquivo): marcas sintéticas e um "Whisper falso" sem erro;
- erro do reconhecimento de fala (tests/medir_sync.py e test_transcricao_real_de_fala).
"""
import subprocess
import types

import pytest

import app as A

SR = 44100
# Cortes fora da grade de quadros e com durações irregulares (os mesmos da auditoria).
CORTES = [(0.37, 1.71), (2.05, 3.38), (4.11, 5.02), (6.33, 7.99), (8.21, 9.47), (10.1, 11.43)]
DENTRO = 0.25  # a marca fica ~0,25 s depois do início de cada corte


def marcas_da_fonte():
    """Instantes das marcas no vídeo-fonte, na grade de quadros de 30 fps."""
    return [round((a + DENTRO) * 30) / 30 for a, _ in CORTES]


def fonte_com_marcas(path, marcas, dur=12.0):
    """Vídeo preto 30 fps; em cada marca, 1 quadro branco e um bipe de 50 ms."""
    flash = "+".join(f"between(n,{round(t * 30)},{round(t * 30)})" for t in marcas)
    bipe = "+".join(f"between(t,{t:.4f},{t + 0.05:.4f})" for t in marcas)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"color=c=black:s=640x360:r=30:d={dur}",
                    "-f", "lavfi", "-i", f"aevalsrc='if({bipe},0.25*sin(2*PI*1000*t),0)':s={SR}:d={dur}",
                    "-filter_complex", f"[0:v]drawbox=c=white:t=fill:enable='{flash}'[v]",
                    "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-b:a", "192k", str(path)], check=True)


def medir_marcas(mp4):
    """(inícios dos bipes em s, quadros com flash em s) no vídeo renderizado."""
    pcm = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(mp4), "-map", "0:a:0", "-ac", "1",
                          "-ar", str(SR), "-f", "s16le", "-"], capture_output=True, check=True).stdout
    amostras = memoryview(pcm).cast("h")
    bipes, ultimo = [], -1.0
    for i in range(0, len(amostras)):
        if abs(amostras[i]) > 3000 and i / SR - ultimo > 0.2:
            bipes.append(i / SR)
            ultimo = i / SR
        elif abs(amostras[i]) > 3000:
            ultimo = i / SR
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(mp4), "-map", "0:v:0",
                          "-vf", "scale=16:16,format=gray", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    flashes = [k / 30 for k in range(len(raw) // 256) if sum(raw[k * 256:(k + 1) * 256]) / 256 > 128]
    return bipes, flashes


def esperado(cortes_q, inicios):
    """Onde cada marca deve cair: início do corte na linha do tempo + posição dentro do corte."""
    return [ini_tl + (m - a) for (a, _), ini_tl, m in zip(cortes_q, inicios, marcas_da_fonte())]


@pytest.fixture(scope="module")
def renderizado(tmp_path_factory, request):
    d = tmp_path_factory.mktemp("tl")
    fonte = d / "marcas.mp4"
    fonte_com_marcas(fonte, marcas_da_fonte())
    return fonte


def test_cada_fronteira_de_corte_cai_no_tempo_esperado(studio, enviar, renderizado):
    vid = enviar(renderizado)
    job = {"nome": "tl", "video_id": vid, "cortes": [{"ini": a, "fim": b} for a, b in CORTES]}
    r = A.renderizar(job)
    cortes_q = A.ler_cortes(job["cortes"], A.probe(renderizado)["duracao"])
    inicios = A.linha_do_tempo(cortes_q)
    alvo = esperado(cortes_q, inicios)
    bipes, flashes = medir_marcas(studio.OUT / r["arquivo"])
    assert len(bipes) == len(CORTES) and len(flashes) == len(CORTES), (bipes, flashes)
    for k, (t, b, f) in enumerate(zip(alvo, bipes, flashes)):
        assert b == pytest.approx(t, abs=0.010), f"corte {k}: bipe em {b:.4f}s, esperado {t:.4f}s"
        assert f == pytest.approx(t, abs=1 / 30 + 0.001), f"corte {k}: flash em {f:.4f}s, esperado {t:.4f}s"
    # duração total = soma exata dos cortes na grade de quadros
    assert r["duracao"] == pytest.approx(sum(b - a for a, b in cortes_q), abs=0.010)


def test_transcricao_usa_a_mesma_linha_do_tempo(studio, enviar, renderizado, monkeypatch):
    """Whisper falso: uma "palavra" a DENTRO s do início de cada trecho (erro de reconhecimento zero)."""
    pytest.importorskip("faster_whisper")

    class Falso:
        def transcribe(self, audio, **kw):
            w = types.SimpleNamespace(start=DENTRO, end=DENTRO + 0.2, word=" marca")
            return [types.SimpleNamespace(words=[w])], None

    monkeypatch.setattr(A, "_modelo", Falso())
    vid = enviar(renderizado)
    cortes = [{"ini": a, "fim": b} for a, b in CORTES]
    j = studio.app.test_client().post("/transcrever", json={"video_id": vid, "cortes": cortes}).get_json()
    cortes_q = A.ler_cortes(cortes, A.probe(renderizado)["duracao"])
    inicios = A.linha_do_tempo(cortes_q)
    assert [t[0] for t in j["tempos"]] == pytest.approx([i + DENTRO for i in inicios], abs=0.001)
    # e a linha do tempo bate com o vídeo de verdade (bipes medidos no render)
    r = A.renderizar({"nome": "tl2", "video_id": vid, "cortes": cortes})
    bipes, _ = medir_marcas(studio.OUT / r["arquivo"])
    for t_leg, t_real, (a, _), m in zip([t[0] for t in j["tempos"]], bipes, cortes_q, marcas_da_fonte()):
        # a palavra falsa está a DENTRO do início do trecho; a marca, a (m - a): compara os dois deslocamentos
        assert (t_real - (m - a)) == pytest.approx(t_leg - DENTRO, abs=0.010)
