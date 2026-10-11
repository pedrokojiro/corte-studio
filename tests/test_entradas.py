"""Arquivos, entradas e proteções do servidor local (auditoria A4–A8)."""
import io
import json
import subprocess
import threading

import pytest

import app as A
import dark as D


def _get_com_prazo(cli, url, prazo=5.0):
    """GET numa thread: se a requisição travar, o teste falha em vez de pendurar a suíte."""
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", cli.get(url)), daemon=True)
    t.start()
    t.join(prazo)
    assert "r" in out, f"{url} travou por mais de {prazo}s"
    return out["r"]


# ---------------------------------------------------------------- A4: downloads
@pytest.mark.parametrize("nome", ["CON.mp4", "aux.mp4", "NUL.mp4", "com1.mp4", "lpt1.mp4", "x.json",
                                  "..%2Fapp.py", "ok_20261011-101010.mp4.json"])
def test_saida_recusa_nomes_invalidos_e_de_dispositivo(studio, nome):
    r = _get_com_prazo(studio.app.test_client(), f"/saida/{nome}")
    assert r.status_code == 404


def test_saida_entrega_arquivos_validos_novos_e_antigos(studio):
    for nome in ("video-um_20261011-101010_a1b2c3.mp4", "daniel_20261011-084451.mp4"):
        (studio.OUT / nome).write_bytes(b"\x00" * 10)
        assert studio.app.test_client().get(f"/saida/{nome}").status_code == 200
    (studio.OUT / "pasta_20261011-101010.mp4").mkdir()           # diretório com nome válido
    assert studio.app.test_client().get("/saida/pasta_20261011-101010.mp4").status_code == 404


# ---------------------------------------------------------------- A5: uploads inválidos
def test_upload_sem_duracao_da_erro_json_e_limpa(studio, tmp_path):
    mkv = tmp_path / "ao vivo.mkv"
    with open(mkv, "wb") as f:
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=160x120:d=1",
                        "-c:v", "libx264", "-f", "matroska", "-live", "1", "pipe:1"], stdout=f, check=True)
    with open(mkv, "rb") as f:
        r = studio.app.test_client().post("/upload", data={"arquivo": (f, mkv.name)},
                                          content_type="multipart/form-data")
    assert r.status_code == 400 and r.is_json and "duração" in r.get_json()["erro"]
    assert [p.name for p in studio.UP.iterdir()] == []


def test_upload_lixo_e_erro_inesperado_viram_json_e_limpam(studio, monkeypatch):
    cli = studio.app.test_client()
    r = cli.post("/upload", data={"arquivo": (io.BytesIO(b"nao e video"), "a.mp4")},
                 content_type="multipart/form-data")
    assert r.status_code == 400 and r.is_json
    monkeypatch.setattr(A, "probe", lambda p: (_ for _ in ()).throw(RuntimeError("boom")))
    studio.app.config["PROPAGATE_EXCEPTIONS"] = False
    r = cli.post("/upload", data={"arquivo": (io.BytesIO(b"x"), "b.mp4")}, content_type="multipart/form-data")
    assert r.status_code == 500 and r.is_json and "RuntimeError" in r.get_json()["erro"]
    assert [p.name for p in studio.UP.iterdir()] == []


def test_erros_http_tambem_sao_json(studio):
    r = studio.app.test_client().get("/nao-existe")
    assert r.status_code == 404 and r.is_json


# ---------------------------------------------------------------- A6: nomes únicos
def test_renders_simultaneos_nao_se_sobrescrevem(studio, enviar, midia):
    vid = enviar(midia / "com_audio.mp4")
    saidas, erros = [], []

    def go():
        try:
            saidas.append(A.renderizar({"nome": "mesmo", "video_id": vid, "cortes": [{"ini": 0, "fim": 0.5}]}))
        except Exception as e:  # noqa: BLE001
            erros.append(e)
    ts = [threading.Thread(target=go) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not erros
    nomes = {s["arquivo"] for s in saidas}
    assert len(nomes) == 3
    for n in nomes:  # cada vídeo com o seu .json
        reg = json.loads((studio.OUT / n).with_suffix(".json").read_text(encoding="utf-8"))
        assert reg["arquivo"] == n


# ---------------------------------------------------------------- A7: só o próprio navegador local
def test_json_exige_content_type(studio):
    r = studio.app.test_client().post("/render", data=json.dumps({"video_id": "x"}), content_type="text/plain")
    assert r.status_code == 415 and r.is_json


@pytest.mark.parametrize("cabecalhos, status", [
    ({"Origin": "https://site-qualquer.example"}, 403),
    ({"Origin": "null"}, 403),
    ({"Sec-Fetch-Site": "cross-site"}, 403),
    ({"Origin": "http://127.0.0.1:5000", "Sec-Fetch-Site": "same-origin"}, 400),  # passa; para no vídeo inválido
    ({"Origin": "http://localhost:5000"}, 400),
    ({}, 400),                                                                    # curl / scripts locais
])
def test_origem_da_requisicao(studio, cabecalhos, status):
    r = studio.app.test_client().post("/render", json={"video_id": "x"}, headers=cabecalhos)
    assert r.status_code == status, r.get_json()


@pytest.mark.parametrize("host, status", [("evil.example", 403), ("evil.example:5000", 403),
                                          ("127.0.0.1:5000", 200), ("localhost:5000", 200), ("[::1]:5000", 200)])
def test_host_local_apenas(studio, host, status):
    assert studio.app.test_client().get("/", headers={"Host": host}).status_code == status


def test_upload_de_outro_site_recusado(studio):
    r = studio.app.test_client().post("/upload", data={"arquivo": (io.BytesIO(b"x"), "a.mp4")},
                                      content_type="multipart/form-data",
                                      headers={"Origin": "https://site-qualquer.example"})
    assert r.status_code == 403 and [p.name for p in studio.UP.iterdir()] == []


# ---------------------------------------------------------------- A8: roteiro JSON
@pytest.mark.parametrize("roteiro, msg", [
    ({"id": "a", "titulo": "t", "cenas": [{"texto": 5}]}, "cena 1"),
    ({"id": "a", "titulo": "t", "cenas": [{"texto": "ok", "prompt_imagem": 3}]}, "prompt_imagem"),
    ({"id": 7, "titulo": "t", "cenas": [{"texto": "ok"}]}, "'id'"),
    ({"id": "a", "titulo": "t", "cenas": "texto"}, "cenas"),
    ({"id": "a", "titulo": "t", "cenas": [{"texto": "ok"}], "ficcao": "sim"}, "ficcao"),
    ({"id": "a", "titulo": "t", "cenas": [{"texto": "ok"}], "taxa": "rápido"}, "taxa"),
    ({"id": "a", "titulo": "t", "cenas": [{"texto": "ok"}], "voz": 1}, "voz"),
    ([1, 2], "objeto"),
])
def test_roteiro_com_tipo_errado_da_erro_claro(tmp_path, roteiro, msg):
    p = tmp_path / "roteiro.json"
    p.write_text(json.dumps(roteiro), encoding="utf-8")
    with pytest.raises(A.Erro, match=msg):
        D.carregar(p)


def test_roteiro_com_voz_e_taxa_nulas_usa_padrao(tmp_path):
    p = tmp_path / "roteiro.json"
    p.write_text(json.dumps({"id": "a", "titulo": "t", "voz": None, "taxa": None, "cenas": [{"texto": "ok"}]}),
                 encoding="utf-8")
    r = D.carregar(p)
    assert r["voz"] == "pt-BR-AntonioNeural" and r["taxa"] == "-5%"


# ---------------------------------------------------------------- interface: o JavaScript precisa ao menos compilar
def test_javascript_da_pagina_compila(tmp_path):
    import re
    import shutil
    node = shutil.which("node")
    if not node:
        pytest.skip("node não instalado: sem como checar a sintaxe do JavaScript")
    html = (A.ROOT / "index.html").read_text(encoding="utf-8")
    js = "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))
    assert js.strip()
    arq = tmp_path / "pagina.js"
    arq.write_text(js, encoding="utf-8")
    r = subprocess.run([node, "--check", str(arq)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
