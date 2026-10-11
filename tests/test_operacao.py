"""Qualidade do zoom e confiabilidade operacional (auditoria A1, A13, A14)."""
import os
import statistics
import subprocess
import sys
import time
import uuid

import pytest

import app as A
import dark as D


# ---------------------------------------------------------------- A1: zoom sem tremido
def _borda(linha):
    x = next(i for i in range(1, len(linha)) if linha[i] >= 128 > linha[i - 1])
    return x - 1 + (128 - linha[x - 1]) / max(1, linha[x] - linha[x - 1])


@pytest.mark.parametrize("entrando", [True, False])
def test_zoom_move_a_borda_sem_voltar(tmp_path, entrando):
    img = tmp_path / "borda.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=black:s=1080x1920,drawbox=x=300:y=0:w=1080:h=1920:c=white:t=fill",
                    "-frames:v", "1", str(img)], check=True)
    n = 60
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-loop", "1", "-framerate", "30", "-t", str(n / 30),
                          "-i", str(img), "-vf", D.filtro_zoom(n, entrando) + ",format=gray,crop=1080:2:0:960",
                          "-frames:v", str(n), "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    pos = [_borda(raw[k * 2160:k * 2160 + 1080]) for k in range(len(raw) // 2160)]
    assert len(pos) == n
    passos = [b - a for a, b in zip(pos, pos[1:])]
    sentido = -1 if entrando else 1                    # zoom de entrada empurra a borda para fora (esquerda)
    assert sum(1 for s in passos if s * sentido < -0.05) == 0, "a borda voltou em algum quadro (tremido)"
    assert statistics.pstdev(passos) < 0.2             # zoompan antigo: 0,52 px nesta medida
    ini, fim = (300, 540 - 240 * 1.12) if entrando else (540 - 240 * 1.12, 300)
    assert pos[0] == pytest.approx(ini, abs=1.5) and pos[-1] == pytest.approx(fim, abs=1.5)


# ---------------------------------------------------------------- A13: prazos sem processo órfão
def _processos_com(marca):
    if os.name == "nt":
        cmd = ["powershell", "-NoProfile", "-Command",
               f"(Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -like '*{marca}*' }}).Count"]
        out = subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
        # conta também o próprio powershell, cuja linha de comando contém a marca
        return max(0, int(out or 0) - 1)
    out = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
    return sum(1 for l in out.splitlines() if marca in l and "ps -eo" not in l)


def test_prazo_estourado_da_erro_claro_e_mata_o_ffmpeg(tmp_path):
    marca = f"prazo_{uuid.uuid4().hex[:8]}"
    saida = tmp_path / f"{marca}.mkv"
    t = time.time()
    with pytest.raises(A.Erro, match="passou de 1s e foi interrompido") as e:
        A.run(["ffmpeg", "-y", "-loglevel", "error", "-re", "-f", "lavfi", "-i", "testsrc2=d=120",
               "-c:v", "libx264", str(saida)], timeout=1)
    assert e.value.status == 504 and time.time() - t < 10
    assert _processos_com(marca) == 0


def test_ffmpeg_ausente_da_erro_claro(monkeypatch):
    with pytest.raises(A.Erro, match="não encontrado"):
        A.run(["programa-que-nao-existe-xyz", "-version"])


# ---------------------------------------------------------------- A13: limpeza explícita
def test_limpeza_preserva_o_que_esta_em_uso_e_nunca_toca_saida(studio):
    cli = studio.app.test_client()
    ids = [uuid.uuid4().hex[:12] for _ in range(3)]
    for i in ids:
        (studio.UP / f"{i}.mp4").write_bytes(b"x" * 1000)
    (studio.OUT / "pronto_20261011-101010.mp4").write_bytes(b"v")
    morto = subprocess.Popen([sys.executable, "-c", "pass"])
    morto.wait()
    for nome, pid in (("vivo", os.getpid()), ("morto", morto.pid)):
        (studio.TMP / nome).mkdir()
        (studio.TMP / nome / ".ativo").write_text(str(pid), encoding="ascii")
    (studio.TMP / "sem_marca").mkdir()

    resumo = cli.get(f"/limpeza?manter={ids[0]}").get_json()
    assert resumo["uploads"]["removiveis"]["arquivos"] == 2 and resumo["tmp"]["preservados"]["arquivos"] == 1

    with A.em_uso(ids[1]):  # um trabalho em andamento usando o 2o arquivo
        r = cli.post("/limpeza", json={"alvo": "uploads", "manter": [ids[0], "../../app"]}).get_json()
    assert r["removidos"] == 1 and r["preservados"] == 2
    assert sorted(p.stem for p in studio.UP.iterdir()) == sorted(ids[:2])
    r = cli.post("/limpeza", json={"alvo": "tmp"}).get_json()
    assert r["removidos"] == 2 and [p.name for p in studio.TMP.iterdir()] == ["vivo"]
    assert (studio.OUT / "pronto_20261011-101010.mp4").exists()
    assert cli.post("/limpeza", json={"alvo": "saida"}).status_code == 400


def test_pasta_de_trabalho_marca_e_some(studio):
    with A.pasta_de_trabalho("x_") as w:
        assert (w / ".ativo").read_text(encoding="ascii") == str(os.getpid())
        assert A._pasta_ativa(w)
    assert not w.exists()


# ---------------------------------------------------------------- A14: fonte ausente
def test_fonte_ausente_e_detectada():
    if A.verificar_fonte("FonteQueNaoExiste123") is None and A.verificar_fonte("Arial") is None:
        pytest.skip("o libass desta máquina não registra a escolha de fonte")
    assert A.verificar_fonte("Arial") is None
    assert A.verificar_fonte("FonteQueNaoExiste123")  # devolve a fonte usada no lugar


def test_render_avisa_fonte_ausente(studio, enviar, midia):
    if A.verificar_fonte("FonteQueNaoExiste123") is None:
        pytest.skip("o libass desta máquina não registra a escolha de fonte")
    vid = enviar(midia / "com_audio.mp4")
    aud = enviar(midia / "narr9.mp3")
    r = A.renderizar({"nome": "f", "video_id": vid, "cortes": [{"ini": 0, "fim": 1}], "narracao": "arquivo",
                      "audio_id": aud, "texto": "um dois", "legenda": True, "fonte": "FonteQueNaoExiste123",
                      "tempos": [[0.1, 0.4, "um"], [0.5, 0.9, "dois"]], "tempos_chave": f"a:{aud}"})
    assert any("FonteQueNaoExiste123" in a and "não está instalada" in a for a in r["avisos"])
