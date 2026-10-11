"""Mede a defasagem da legenda: proporcional (antes) x tempo por palavra (depois).

Uso (precisa de internet para o edge-tts):  python tests/medir_sync.py
Referência independente: retomadas de fala detectadas pelo silencedetect do ffmpeg.
"""
import re
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as A  # noqa: E402

TEXTO = (
    "Em mil novecentos e trinta e dois, numa vila do interior de Minas, as crianças pararam de dormir. "
    "Não era doença. Elas ficavam sentadas na cama, olhando para a janela, esperando alguém. "
    "Os pais pregaram tábuas. Penduraram terço. Chamaram o padre. Nada adiantou. "
    "Na sétima noite, a filha do ferreiro falou pela primeira vez. Disse que a mulher de branco estava cansada de esperar do lado de fora. "
    "O ferreiro pegou a espingarda e abriu a porta. Não havia ninguém no terreiro. Só pegadas descalças, molhadas, que entravam na casa e não saíam. "
    "Naquela madrugada, todas as crianças da vila finalmente dormiram. Menos uma. "
    "E é sobre ela que eu vou te contar na parte dois."
)


def retomadas(mp3):
    """Instantes em que a fala recomeça depois de uma pausa (fim de silêncio)."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(mp3), "-af", "silencedetect=n=-35dB:d=0.18",
                        "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]


def perto(refs, tempos):
    """Distância de cada referência até o tempo mais próximo da lista."""
    return [min(abs(r - t) for t in tempos) for r in refs]


def main():
    d = Path(tempfile.mkdtemp())
    mp3 = d / "voz.mp3"
    tempos = A.gerar_voz(TEXTO, "pt-BR-AntonioNeural", "-5%", mp3)
    dur = A.probe(mp3)["duracao"]
    refs = [r for r in retomadas(mp3) if 0.3 < r < dur - 0.3]
    prop = A.grupos(TEXTO, dur)
    real = A.grupos(TEXTO, dur, tempos)
    # 1) Validação independente: início das palavras (WordBoundary) x retomadas de fala (silêncio).
    wb = perto(refs, [t0 for t0, _, _ in tempos])
    # 2) Defasagem por grupo de 3 palavras: método proporcional x início real (WordBoundary).
    antes = [abs(a[0] - b[0]) for a, b in zip(prop, real)]
    print(f"fala {dur:.1f}s | {len(TEXTO)} caracteres ({len(TEXTO) / dur:.1f} car/s) | "
          f"{len(tempos)} palavras com tempo | {len(prop)} grupos | {len(refs)} retomadas de fala")
    # DEPOIS = início real de cada grupo; o erro dele é o da linha "WordBoundary x silêncio".
    linhas = [("WordBoundary x silêncio", wb), ("ANTES por grupo x real", antes)]
    # 3) Erro do RECONHECIMENTO (Whisper) isolado: mesmo áudio, sem cortes, contra o WordBoundary.
    #    A deriva da LINHA DO TEMPO entre cortes é medida à parte em tests/test_linha_do_tempo.py.
    try:
        _, whisper = A.transcrever(mp3)
        ref = A.alinhar([w for _, _, w in whisper], tempos)
        linhas.append(("Whisper x WordBoundary", [abs(a[0] - b[0]) for a, b in zip(whisper, ref)]))
    except A.Erro as e:
        print(f"(Whisper indisponível: {e})")
    for nome, e in linhas:
        print(f"{nome:24s} média {statistics.mean(e) * 1000:5.0f} ms | mediana "
              f"{statistics.median(e) * 1000:5.0f} ms | máx {max(e) * 1000:5.0f} ms")


if __name__ == "__main__":
    main()
