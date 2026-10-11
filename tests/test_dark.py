import json
import types

import pytest

import app as A
import dark as D
from conftest import ff


def projeto(pasta, cenas, imagens=True, audios=None, **extra):
    pasta.mkdir(parents=True, exist_ok=True)
    r = {"id": pasta.name, "titulo": "Teste", "estilo_visual": "granulado, tons frios", "ficcao": True,
         "cenas": [{"texto": t, "prompt_imagem": f"cena {i}"} for i, t in enumerate(cenas)], **extra}
    (pasta / "roteiro.json").write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
    for i in range(len(cenas)):
        if imagens:
            ff("-f", "lavfi", "-i", "testsrc2=s=800x600", "-frames:v", "1", str(pasta / f"img_{i + 1:02d}.png"))
        if audios:
            ff("-f", "lavfi", "-i", f"sine=f=300:d={audios[i]}", str(pasta / f"audio_{i + 1:02d}.mp3"))
    return pasta / "roteiro.json"


def args(**kw):
    return types.SimpleNamespace(**{"trilha": None, "vol_trilha": 0.1, "fonte": "Arial", "refazer": False, **kw})


# ---------------------------------------------------------------- auditar
def test_auditar_imagens_faltando_e_gancho_longo(tmp_path):
    p = projeto(tmp_path / "p", [" ".join(["palavra"] * 21), "fim"], imagens=False)
    erros, avisos, resumo = D.auditar(D.carregar(p))
    assert any("img_01, img_02" in e for e in erros)
    assert any("21 palavras" in e for e in erros)
    assert resumo["palavras_gancho"] == 21


def test_auditar_duracao_estimada_15_cps(tmp_path):
    p = projeto(tmp_path / "p", ["a" * 150, "b" * 150])  # 300 car / 15 = 20 s
    erros, avisos, resumo = D.auditar(D.carregar(p))
    assert not erros
    assert resumo["duracao_estimada_s"] == pytest.approx(20 + 2 * D.PAUSA + D.CAUDA - D.PAUSA, abs=0.1)
    assert any("abaixo de 65s" in a for a in avisos)


def test_auditar_sem_aviso_de_duracao_acima_de_65(tmp_path):
    p = projeto(tmp_path / "p", ["a" * 600, "b" * 450])  # 70 s estimados
    _, avisos, _ = D.auditar(D.carregar(p))
    assert not any("abaixo de 65s" in a for a in avisos)


def test_auditar_contradicao_ficcao_x_historia_real(tmp_path):
    p = projeto(tmp_path / "p", ["Esta é uma história real.", "fim"])
    _, avisos, _ = D.auditar(D.carregar(p))
    assert any("Contradição" in a for a in avisos)
    p2 = projeto(tmp_path / "q", ["Era uma vez.", "fim"], ficcao=False)
    _, avisos2, _ = D.auditar(D.carregar(p2))
    assert any("'ficcao' não está true" in a for a in avisos2)


@pytest.mark.parametrize("conteudo, msg", [
    ("{", "JSON inválido"),
    ('{"id": "x", "titulo": "t"}', "cenas"),
    ('{"id": "x", "titulo": "t", "cenas": [{"texto": ""}]}', "cena 1 obrigatório"),
])
def test_carregar_roteiro_invalido(tmp_path, conteudo, msg):
    p = tmp_path / "roteiro.json"
    p.write_text(conteudo, encoding="utf-8")
    with pytest.raises(A.Erro, match=msg):
        D.carregar(p)


# ---------------------------------------------------------------- prompts
def test_prompts_junta_estilo_visual(tmp_path):
    p = projeto(tmp_path / "p", ["um", "dois"], imagens=False)
    D.cmd_prompts(D.carregar(p), args())
    txt = (tmp_path / "p" / "prompts.txt").read_text(encoding="utf-8")
    assert "img_01.png: cena 0, granulado, tons frios" in txt
    assert "img_02.png: cena 1, granulado, tons frios" in txt


# ---------------------------------------------------------------- render (áudio próprio, sem rede)
def test_render_com_audio_proprio(studio, tmp_path):
    p = projeto(tmp_path / "p", ["primeira cena aqui", "segunda cena com mais palavras"], audios=[2, 3])
    D.cmd_render(D.carregar(p), args())
    mp4 = next(studio.OUT.glob("p_*.mp4"))
    info = A.probe(mp4)
    assert (info["largura"], info["altura"]) == (1080, 1920) and info["tem_audio"]
    esperado = 2 + D.PAUSA + 3 + D.CAUDA
    assert info["duracao"] == pytest.approx(esperado, abs=0.15)
    reg = json.loads(mp4.with_suffix(".json").read_text(encoding="utf-8"))
    assert reg["legenda_sync"] == ["proporcional", "proporcional"]  # sem tempos_XX.json
    assert reg["ficcao"] is True and reg["origem_declarada"] == "proprio"


def test_render_com_trilha(studio, tmp_path, midia):
    p = projeto(tmp_path / "p", ["uma cena"], audios=[2])
    D.cmd_render(D.carregar(p), args(trilha=str(midia / "narr9.mp3")))
    mp4 = next(studio.OUT.glob("p_*.mp4"))
    assert A.probe(mp4)["duracao"] == pytest.approx(2 + D.CAUDA, abs=0.15)  # trilha não estica o vídeo


def test_render_bloqueia_sem_audio_ou_imagem(studio, tmp_path):
    p = projeto(tmp_path / "p", ["uma cena"])
    with pytest.raises(A.Erro, match="Áudio faltando"):
        D.cmd_render(D.carregar(p), args())
    p2 = projeto(tmp_path / "q", ["uma cena"], imagens=False, audios=[1])
    with pytest.raises(A.Erro, match="Imagens faltando"):
        D.cmd_render(D.carregar(p2), args())


def test_tempos_ignorados_se_audio_foi_trocado(tmp_path):
    p = projeto(tmp_path / "p", ["uma cena"], audios=[1])
    pasta = p.parent
    tj = pasta / "tempos_01.json"
    tj.write_text("[[0.1, 0.3, \"uma\"]]", encoding="utf-8")
    import os
    os.utime(tj, (1, 1))  # tempos muito mais antigos que o áudio
    assert D.tempos_cena(pasta, 0, pasta / "audio_01.mp3") == []


def test_lote_continua_depois_de_erro(studio, tmp_path, capsys):
    projeto(tmp_path / "lote" / "a_ok", ["cena boa"], audios=[1])
    projeto(tmp_path / "lote" / "b_ruim", ["cena sem imagem"], imagens=False, audios=[1])
    assert D.main(["lote", str(tmp_path / "lote")]) == 1
    out = capsys.readouterr().out
    assert "1 ok, 1 com erro (b_ruim)" in out
    assert len(list(studio.OUT.glob("a-ok_*.mp4"))) == 1
