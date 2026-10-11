import json

import pytest

import app as A
from conftest import dur

BASE = {"origem": "proprio", "prova": "gravado por mim", "nome": "teste"}


# ---------------------------------------------------------------- origem (opcional)
@pytest.mark.parametrize("origem, prova", [(None, None), ("", ""), ("filme", "abc"), ("proprio", "")])
def test_origem_opcional_nao_bloqueia(studio, enviar, midia, origem, prova):
    vid = enviar(midia / "com_audio.mp4")
    r = A.renderizar({"nome": "t", "origem": origem, "prova": prova, "video_id": vid,
                      "cortes": [{"ini": 0, "fim": 1}]})
    reg = json.loads((studio.OUT / r["arquivo"].replace(".mp4", ".json")).read_text(encoding="utf-8"))
    assert reg["origem_declarada"] == (origem if origem in A.ORIGENS else None)
    assert reg["prova_declarada"] == (prova or None)


def test_registro_json_grava_origem(studio, enviar, midia):
    vid = enviar(midia / "com_audio.mp4")
    r = A.renderizar({**BASE, "origem": "dominio_publico", "prova": "archive.org/details/xyz",
                      "video_id": vid, "cortes": [{"ini": 0, "fim": 1}]})
    reg = json.loads((studio.OUT / r["arquivo"].replace(".mp4", ".json")).read_text(encoding="utf-8"))
    assert reg["origem_declarada"] == "dominio_publico"
    assert reg["prova_declarada"] == "archive.org/details/xyz"
    assert "não verifica" in reg["aviso"]


def test_upload_rejeita_extensao(studio):
    import io
    r = studio.app.test_client().post("/upload", data={"arquivo": (io.BytesIO(b"x"), "a.exe")},
                                      content_type="multipart/form-data")
    assert r.status_code == 400 and "não suportado" in r.get_json()["erro"]


# ---------------------------------------------------------------- cortes
@pytest.mark.parametrize("cortes", [
    [],
    None,
    [{"ini": 3, "fim": 2}],            # fim antes do início
    [{"ini": 2, "fim": 2.2}],          # menor que 0,3 s
    [{"ini": 7.9, "fim": 100}],        # fim limitado a 8 s -> 0,1 s
    [{"ini": "a", "fim": "b"}],        # não numérico vira 0..0
    [{"ini": -5, "fim": 0.1}],         # negativo limitado a 0 -> 0,1 s
])
def test_cortes_invalidos(studio, enviar, midia, cortes):
    vid = enviar(midia / "com_audio.mp4")
    with pytest.raises(A.Erro, match="pelo menos um corte"):
        A.renderizar({**BASE, "video_id": vid, "cortes": cortes})


def test_corte_alem_do_fim_e_limitado(studio, enviar, midia):
    vid = enviar(midia / "com_audio.mp4")
    r = A.renderizar({**BASE, "video_id": vid, "cortes": [{"ini": 6, "fim": 999}]})
    assert r["duracao"] == pytest.approx(2.0, abs=0.15)


# ---------------------------------------------------------------- sem áudio
def test_video_sem_audio(studio, enviar, midia):
    vid = enviar(midia / "sem_audio.mp4")
    r = A.renderizar({**BASE, "video_id": vid, "cortes": [{"ini": 1, "fim": 3}]})
    info = A.probe(studio.OUT / r["arquivo"])
    assert info["tem_audio"]  # faixa muda criada com anullsrc
    assert (info["largura"], info["altura"]) == (1080, 1920)
    assert r["duracao"] == pytest.approx(2.0, abs=0.15)


# ---------------------------------------------------------------- narração > vídeo
def test_narracao_maior_estende_com_tpad(studio, enviar, midia):
    vid = enviar(midia / "com_audio.mp4")
    aud = enviar(midia / "narr9.mp3")
    r = A.renderizar({**BASE, "video_id": vid, "cortes": [{"ini": 0, "fim": 3}],
                      "narracao": "arquivo", "audio_id": aud, "texto": "um dois tres quatro cinco",
                      "legenda": True})
    # vídeo = narração + 0,4 s de folga
    assert r["duracao"] == pytest.approx(9.4, abs=0.15)
    assert dur(studio.OUT / r["arquivo"]) == pytest.approx(9.4, abs=0.15)


def test_narracao_menor_mantem_video(studio, enviar, midia):
    vid = enviar(midia / "com_audio.mp4")
    aud = enviar(midia / "narr9.mp3")
    r = A.renderizar({**BASE, "video_id": vid, "cortes": [{"ini": 0, "fim": 4}, {"ini": 0, "fim": 4}, {"ini": 4, "fim": 6}],
                      "narracao": "arquivo", "audio_id": aud})
    assert r["duracao"] == pytest.approx(10.0, abs=0.2)


# ---------------------------------------------------------------- duração
def test_duracao_soma_cortes_e_avisa_abaixo_de_61(studio, enviar, midia):
    vid = enviar(midia / "com_audio.mp4")
    r = A.renderizar({**BASE, "video_id": vid, "cortes": [{"ini": 0, "fim": 2}, {"ini": 5, "fim": 6.5}]})
    assert r["duracao"] == pytest.approx(3.5, abs=0.15)
    assert "abaixo de 1 minuto" in r["aviso"]


def test_sem_aviso_acima_de_61(studio, enviar, midia):
    vid = enviar(midia / "com_audio.mp4")
    aud = enviar(midia / "narr62.mp3")
    r = A.renderizar({**BASE, "video_id": vid, "cortes": [{"ini": 0, "fim": 1}],
                      "narracao": "arquivo", "audio_id": aud})
    assert r["duracao"] >= A.MIN_ELEGIVEL
    assert r["aviso"] == ""


def test_slug_mantem_letras_acentuadas():
    assert A.slug("Lenda do Boitatá: ação!") == "lenda-do-boitata-acao"
    assert A.slug("") == "video"


# ---------------------------------------------------------------- legenda
def test_legenda_proporcional_cobre_a_fala():
    ev = A.grupos("um dois tres quatro cinco seis sete", 7.0)
    assert [e[2] for e in ev] == ["um dois tres", "quatro cinco seis", "sete"]
    assert ev[0][0] == pytest.approx(0.15) and ev[-1][1] == pytest.approx(7.0)


def test_legenda_por_palavra_usa_inicio_real():
    tempos = [(0.1, 0.4, "Dizem"), (0.5, 0.7, "que"), (0.8, 1.0, "na"),
              (2.5, 2.9, "beira"), (3.0, 3.2, "do"), (3.3, 3.8, "rio")]
    ev = A.grupos("Dizem que na beira do rio.", 4.5, tempos)
    assert ev == [(0.1, 2.5, "Dizem que na"), (2.5, 4.5, "beira do rio.")]  # mantém a pontuação


def test_legenda_tokenizacao_diferente_mantem_o_texto():
    tempos = [(0.1, 0.5, "1932"), (0.6, 0.9, "foi"), (1.0, 1.4, "assim")]
    ev = A.grupos("Em 1932, foi assim", 2.0, tempos)
    assert ev == [(0.1, 1.0, "Em 1932, foi"), (1.0, 2.0, "assim")]


def test_alinhar_texto_corrigido_a_mao():
    falado = [(0.0, 0.5, "Dizem"), (0.5, 0.8, "que"), (0.8, 1.0, "na"), (1.0, 1.5, "beira"),
              (1.5, 1.7, "do"), (1.7, 2.2, "rio"), (2.2, 2.6, "Lua")]
    corrigido = "Dizem que à beira do rio a lua".split()  # troca "na"->"à", insere "a", "Lua"->"lua"
    out = A.alinhar(corrigido, falado)
    assert [w for _, _, w in out] == corrigido
    assert out[0][:2] == (0.0, 0.5) and out[3][:2] == (1.0, 1.5)   # palavras iguais herdam o tempo
    assert out[2][:2] == (0.8, 1.0)                                   # trocada ocupa o tempo da original
    assert out[7][:2] == (2.2, 2.6)                                   # maiúscula/acento não importam
    assert all(b[0] >= a[0] for a, b in zip(out, out[1:]))            # ordem preservada
    out2 = A.alinhar("Dizem que".split(), falado)                   # palavras apagadas
    assert [x[:2] for x in out2] == [(0.0, 0.5), (0.5, 0.8)]


def test_ass_escapa_chaves_e_maiuscula():
    doc = A.build_ass("olá {x}", 2.0, "Arial", True)
    assert "OLÁ (X)" in doc and "{" not in doc.split("[Events]")[1]


def test_edge_tts_real_devolve_tempo_por_palavra(tmp_path):
    destino = tmp_path / "v.mp3"
    try:
        tempos = A.gerar_voz("A lua sumiu atrás do morro.", "pt-BR-AntonioNeural", "-5%", destino)
    except A.Erro as e:
        pytest.skip(f"edge-tts indisponível: {e}")
    assert [w for _, _, w in tempos] == ["A", "lua", "sumiu", "atrás", "do", "morro"]
    assert all(b[0] >= a[0] for a, b in zip(tempos, tempos[1:]))
    assert tempos[-1][1] <= A.probe(destino)["duracao"] + 0.1


# ---------------------------------------------------------------- sugestão de cortes
def test_sugerir_corta_em_pontos_limpos_dentro_da_faixa():
    cenas = [10.0, 25.0, 70.0]
    pausas = [(40.0, 41.0), (55.0, 56.5)]
    sug = A.sugerir(90.0, cenas, pausas, 15, 30)
    assert [(c["ini"], c["fim"]) for c in sug] == [(0.0, 25.0), (25.0, 55.0), (70.0, 90.0)]
    for c in sug:
        assert 15 <= c["fim"] - c["ini"] <= 30
    assert all(a["fim"] <= b["ini"] for a, b in zip(sug, sug[1:]))  # sem sobreposição
    assert sug[0]["motivo"] == "início do vídeo → troca de cena"


def test_sugerir_sem_ponto_limpo_na_faixa():
    assert A.sugerir(100.0, [], [], 15, 30) == []
    assert A.sugerir(20.0, [], [], 15, 30) == [{"ini": 0.0, "fim": 20.0, "dur": 20.0,
                                                 "motivo": "início do vídeo → fim do vídeo"}]


def test_detectar_cenas_e_pausas_em_video_sintetico(tmp_path):
    from conftest import ff
    # 3 blocos de cor (trocas em 4 s e 8 s) e áudio com silêncio de 5 a 6,5 s.
    v = tmp_path / "v.mp4"
    ff("-f", "lavfi", "-i", "color=c=red:s=320x240:d=4:r=30", "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=4:r=30",
       "-f", "lavfi", "-i", "color=c=green:s=320x240:d=4:r=30",
       "-f", "lavfi", "-i", "sine=f=440:d=12",
       "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v];"
                          "[3:a]volume=enable='between(t,5,6.5)':volume=0[a]",
       "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-c:a", "aac", str(v))
    cenas, pausas = A.detectar(v, True)
    assert len(cenas) == 2 and cenas[0] == pytest.approx(4, abs=0.25) and cenas[1] == pytest.approx(8, abs=0.25)
    assert len(pausas) == 1
    assert pausas[0][0] == pytest.approx(5, abs=0.15) and pausas[0][1] == pytest.approx(6.5, abs=0.15)


TEMPOS_FALSOS = [[0.2, 0.6, "uma"], [0.7, 1.0, "fala"], [1.1, 1.5, "qualquer"], [1.6, 2.0, "aqui"]]


def _render_legenda(studio, vid, **extra):
    job = {"nome": "t", "video_id": vid, "cortes": [{"ini": 0, "fim": 3}], "narracao": "nenhuma",
           "texto": "uma fala qualquer aqui", "legenda": True, **extra}
    r = A.renderizar(job)
    reg = json.loads((studio.OUT / r["arquivo"].replace(".mp4", ".json")).read_text(encoding="utf-8"))
    return r, reg


def _chave(vid, cortes=({"ini": 0, "fim": 3},), dur=8.0):
    return A.chave_transcricao(vid, A.ler_cortes(list(cortes), dur))


def test_legenda_pela_fala_do_video_com_transcricao_valida(studio, enviar, midia):
    vid = enviar(midia / "com_audio.mp4")
    r, reg = _render_legenda(studio, vid, tempos=TEMPOS_FALSOS, tempos_chave=_chave(vid))
    assert reg["legenda_sync"] == "por_palavra" and r["duracao"] == pytest.approx(3.0, abs=0.05)
    assert not any("Legenda" in a for a in r["avisos"])


def _transcricao_fixa(monkeypatch, tempos=None, erro=None):
    chamadas = []

    def falso(src, trechos=((0.0, None),)):
        chamadas.append(list(trechos))
        if erro:
            raise A.Erro(erro)
        return "x", list(tempos or [])
    monkeypatch.setattr(A, "transcrever", falso)
    return chamadas


def test_transcricao_velha_e_refeita_com_aviso(studio, enviar, midia, monkeypatch):
    vid = enviar(midia / "com_audio.mp4")
    chamadas = _transcricao_fixa(monkeypatch, tempos=[(0.1, 0.4, "uma"), (0.5, 0.9, "fala")])
    velha = _chave(vid, [{"ini": 0, "fim": 2}])  # transcrita com outro corte
    r, reg = _render_legenda(studio, vid, tempos=TEMPOS_FALSOS, tempos_chave=velha)
    assert chamadas == [[(0.0, 3.0)]]                      # retranscreveu os cortes atuais
    assert reg["legenda_sync"] == "por_palavra_retranscrita"
    assert any("Legenda sincronizada de novo" in a and "outro corte" in a for a in r["avisos"])


def test_sem_transcricao_e_sem_fala_avisa_que_nao_gerou(studio, enviar, midia, monkeypatch):
    vid = enviar(midia / "com_audio.mp4")
    _transcricao_fixa(monkeypatch, tempos=[])               # nada de fala nos cortes
    r, reg = _render_legenda(studio, vid, tempos="lixo")
    assert reg["legenda_sync"] is None
    assert any(a.startswith("Legenda NÃO gerada") and "Transcrever fala" in a for a in r["avisos"])


def test_transcricao_indisponivel_avisa_e_nao_some_em_silencio(studio, enviar, midia, monkeypatch):
    vid = enviar(midia / "com_audio.mp4")
    _transcricao_fixa(monkeypatch, erro="Transcrição não instalada.")
    r, reg = _render_legenda(studio, vid)
    assert reg["legenda_sync"] is None
    assert any("Legenda NÃO gerada" in a and "não instalada" in a for a in r["avisos"])


def test_narracao_enviada_sem_transcricao_cai_no_proporcional_com_aviso(studio, enviar, midia, monkeypatch):
    vid = enviar(midia / "com_audio.mp4")
    aud = enviar(midia / "narr9.mp3")
    _transcricao_fixa(monkeypatch, erro="sem modelo")
    r = A.renderizar({"nome": "t", "video_id": vid, "cortes": [{"ini": 0, "fim": 2}], "narracao": "arquivo",
                      "audio_id": aud, "texto": "um dois tres", "legenda": True})
    assert r["legenda_sync"] == "proporcional"
    assert any("proporcional" in a for a in r["avisos"])


def test_legenda_marcada_com_texto_vazio_avisa(studio, enviar, midia):
    vid = enviar(midia / "com_audio.mp4")
    r = A.renderizar({"nome": "t", "video_id": vid, "cortes": [{"ini": 0, "fim": 1}], "texto": "", "legenda": True})
    assert any("texto está vazio" in a for a in r["avisos"])


def test_transcrever_devolve_chave_dos_cortes_atuais(studio, enviar, midia, monkeypatch):
    vid = enviar(midia / "com_audio.mp4")
    _transcricao_fixa(monkeypatch, tempos=[(0.1, 0.2, "a")])
    cortes = [{"ini": 0.37, "fim": 1.71}]
    j = studio.app.test_client().post("/transcrever", json={"video_id": vid, "cortes": cortes}).get_json()
    assert j["chave"] == _chave(vid, cortes)


def test_cortes_alinhados_a_grade_de_quadros():
    assert A.ler_cortes([{"ini": 0.37, "fim": 1.71}], 8.0) == [(11 / 30, 51 / 30)]
    assert A.ler_cortes([{"ini": 7.0, "fim": 99}], 8.0) == [(7.0, 8.0)]     # não passa do último quadro
    assert A.linha_do_tempo([(11 / 30, 51 / 30), (2.0, 3.0)]) == [0.0, 40 / 30]


def test_transcrever_video_sem_audio_da_erro(studio, enviar, midia):
    vid = enviar(midia / "sem_audio.mp4")
    r = studio.app.test_client().post("/transcrever", json={"video_id": vid, "cortes": [{"ini": 0, "fim": 3}]})
    assert r.status_code == 400 and "não tem áudio" in r.get_json()["erro"]


def test_transcricao_real_de_fala(tmp_path):
    pytest.importorskip("faster_whisper")
    fala = tmp_path / "fala.mp3"
    try:
        A.gerar_voz("A cobra de fogo atravessa o mato quando a noite cai.", "pt-BR-AntonioNeural", "+0%", fala)
    except A.Erro as e:
        pytest.skip(f"edge-tts indisponível: {e}")
    try:
        texto, tempos = A.transcrever(fala)
    except A.Erro as e:
        pytest.skip(f"modelo indisponível: {e}")
    palavras = {A._norm(w) for w in texto.split()}
    assert {"cobra", "fogo", "mato", "noite"} <= palavras
    assert all(b[0] >= a[0] for a, b in zip(tempos, tempos[1:]))
    # Do 2o corte em diante o tempo continua (trechos emendados).
    _, t2 = A.transcrever(fala, [(0.0, None), (0.0, None)])
    assert t2[-1][0] > tempos[-1][1]


def test_rota_analisar(studio, enviar, midia):
    vid = enviar(midia / "com_audio.mp4")
    r = studio.app.test_client().post("/analisar", json={"video_id": vid, "min": 2, "max": 8})
    j = r.get_json()
    assert r.status_code == 200 and {"sugestoes", "cenas", "pausas", "segundos"} <= set(j)
