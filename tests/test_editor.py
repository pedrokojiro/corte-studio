"""Editor: plano versionado, linha do tempo fonte->saída, legenda que sobrevive à edição, prévia = exportação."""
import copy
import json
import subprocess
import threading
import time

import pytest

import app as A
import editor as E
import tarefas as T
from test_linha_do_tempo import fonte_com_marcas, medir_marcas

FID = "aaaaaaaaaaaa"


def plano_base(dur=12.0, segs=((1.0, 3.0), (5.0, 7.0))):
    p = E.plano_vazio()
    p["fontes"][FID] = {"nome": "teste.mp4", "duracao": dur, "tem_audio": True, "largura": 640, "altura": 360}
    p["segmentos"] = [{"id": f"s{i}", "fonte": FID, "ini": a, "fim": b} for i, (a, b) in enumerate(segs)]
    return p


def frase(fid, palavras):
    return {"id": fid, "fonte": FID, "palavras": [{"t": t, "ini": a, "fim": b} for t, a, b in palavras]}


# ---------------------------------------------------------------- validação
def test_plano_valido_e_normalizado():
    p = E.validar_plano(plano_base(segs=((0.37, 1.71),)))
    assert p["segmentos"][0]["ini"] == pytest.approx(11 / 30) and p["segmentos"][0]["fim"] == pytest.approx(51 / 30)
    assert p["legenda"]["estilo"]["preset"] == "classico" and p["versao"] == 1


@pytest.mark.parametrize("mexer, msg", [
    (lambda p: p.update(versao=2), "versao"),
    (lambda p: p.update(extra=1), "extra"),
    (lambda p: p["segmentos"].append({"id": "x", "fonte": "bbbbbbbbbbbb", "ini": 0, "fim": 1}), "fonte não declarada"),
    (lambda p: p["segmentos"].append({"id": "x", "fonte": FID, "ini": 4, "fim": 3}), "segmento curto"),
    (lambda p: p["segmentos"].append({"id": "s0", "fonte": FID, "ini": 8, "fim": 9}), "repetido"),
    (lambda p: p["segmentos"].append({"id": "x", "fonte": FID, "ini": "0", "fim": 9}), "número"),
    (lambda p: p["segmentos"].append({"id": "x", "fonte": FID, "ini": 0, "fim": 99}), "fora da faixa"),
    (lambda p: p["legenda"]["estilo"].update(cor="branco"), "#RRGGBB"),
    (lambda p: p["legenda"]["estilo"].update(fonte="Arial,Bold"), "não permitido"),
    (lambda p: p["legenda"]["estilo"].update(script="rm -rf /"), "campo desconhecido"),
    (lambda p: p["legenda"]["frases"].append(frase("f1", [("b", 2.0, 2.1), ("a", 1.0, 1.1)])), "fora de ordem"),
    (lambda p: p["titulos"].append({"id": "t", "texto": "x", "ini": 0, "fim": 2, "origem": "orador"}), "origem"),
    (lambda p: p["efeitos"]["zoom"].update(intensidade=0.9), "fora da faixa"),
    (lambda p: p["efeitos"]["transicao"].update(tipo="explosao"), "corte"),
    (lambda p: p["audio"]["musica"].update(id="../../x"), "id de upload"),
])
def test_plano_invalido_da_erro_com_caminho(mexer, msg):
    p = plano_base()
    mexer(p)
    with pytest.raises(A.Erro, match=msg):
        E.validar_plano(p)


@pytest.mark.parametrize("lixo", [None, [], "texto", 42, {"versao": 1, "segmentos": "x"}])
def test_plano_lixo(lixo):
    with pytest.raises(A.Erro):
        E.validar_plano(lixo)


# ---------------------------------------------------------------- mapeamento fonte -> saída
def test_mapa_reordenar_e_repetir_trecho():
    p = E.validar_plano(plano_base(segs=((5.0, 7.0), (1.0, 3.0), (5.0, 6.0))))
    m = E.mapa(p)
    assert [(s["out_ini"], s["out_fim"]) for s in m] == [(0.0, 2.0), (2.0, 4.0), (4.0, 5.0)]
    assert E.fonte_para_saida(p, FID, 5.5) == [0.5, 4.5]      # trecho repetido aparece duas vezes
    assert E.fonte_para_saida(p, FID, 2.0) == [3.0]
    assert E.fonte_para_saida(p, FID, 4.0) == []               # cortado fora
    assert E.saida_para_fonte(p, 2.5) == (FID, 1.5)


def test_legenda_segue_a_fala_depois_de_reordenar_dividir_e_cortar():
    p = plano_base(segs=((1.0, 3.0), (5.0, 7.0)))
    p["legenda"]["frases"] = [frase("f1", [("um", 1.2, 1.5), ("dois", 1.6, 1.9)]),
                             frase("f2", [("tres", 5.2, 5.5), ("quatro", 6.5, 6.8)]),
                             frase("f3", [("fora", 4.0, 4.3)])]
    antes = {e["frase"]: e for e in E.eventos_legenda(E.validar_plano(p))}
    assert set(antes) == {"f1", "f2"}                          # f3 está fora dos cortes
    assert antes["f1"]["palavras"][0][1] == pytest.approx(0.2) and antes["f2"]["ini"] == pytest.approx(2.2)
    # reordena, divide o 2o segmento em 6.0 e remove a 1a metade dele
    p["segmentos"] = [{"id": "b2", "fonte": FID, "ini": 6.0, "fim": 7.0}, {"id": "a", "fonte": FID, "ini": 1.0, "fim": 3.0}]
    ev = {e["frase"]: e for e in E.eventos_legenda(E.validar_plano(p))}
    assert [w[0] for w in ev["f2"]["palavras"]] == ["quatro"]  # "tres" foi cortado junto com a fala
    assert ev["f2"]["palavras"][0][1] == pytest.approx(0.5)     # 6.5 na fonte -> 0.5 na saída
    assert ev["f1"]["palavras"][0][1] == pytest.approx(1.2)     # 1.2 na fonte -> 1.0 + 0.2
    assert all(e["fim"] <= e["limite"] + 1e-9 for e in ev.values())  # não vaza para o próximo corte


def test_legenda_ass_bate_com_o_mapeamento_e_separa_titulo():
    p = plano_base()
    p["legenda"]["frases"] = [frase("f1", [("ola", 1.5, 1.8), ("mundo", 1.9, 2.2)])]
    p["titulos"] = [{"id": "t1", "texto": "Título editorial", "ini": 0, "fim": 2, "origem": "sugestao_local"}]
    ass = E.ass_plano(E.validar_plano(p))
    fala = [l for l in ass.splitlines() if l.startswith("Dialogue") and ",Legenda," in l]
    tit = [l for l in ass.splitlines() if l.startswith("Dialogue") and ",Titulo," in l]
    assert fala == ["Dialogue: 0,0:00:00.50,0:00:01.45,Legenda,,0,0,0,,{\\an5\\pos(540,1382)}OLA MUNDO"]
    assert len(tit) == 1 and "Título editorial" in tit[0] and "Titulo" not in fala[0]


def test_destaque_da_palavra_ativa():
    p = plano_base()
    p["legenda"]["estilo"] = dict(E.PRESETS["destaque"])
    p["legenda"]["frases"] = [frase("f1", [("ola", 1.5, 1.8), ("mundo", 1.9, 2.2)])]
    linhas = [l for l in E.ass_plano(E.validar_plano(p)).splitlines() if l.startswith("Dialogue")]
    assert len(linhas) == 2
    assert "{\\c&H0000D4FF}OLA{\\c&H00FFFFFF} MUNDO" in linhas[0] and linhas[1].endswith("OLA {\\c&H0000D4FF}MUNDO{\\c&H00FFFFFF}")
    assert "0:00:00.90" in linhas[1]                             # a 2a palavra acende quando é falada


# ---------------------------------------------------------------- frases editáveis
def test_agrupar_e_editar_texto_mantendo_tempo():
    pal = [(0.0, 0.3, "Era"), (0.3, 0.6, "uma"), (0.6, 0.9, "vez."), (2.0, 2.3, "Depois"), (2.3, 2.6, "veio")]
    fr = E.agrupar(pal, por_frase=3, fonte=FID)
    assert [[w["t"] for w in f["palavras"]] for f in fr] == [["Era", "uma", "vez."], ["Depois", "veio"]]
    novo = E.editar_texto_frase(fr[0], "Era uma vez,")
    assert [(w["t"], w["ini"]) for w in novo["palavras"]] == [("Era", 0.0), ("uma", 0.3), ("vez,", 0.6)]


# ---------------------------------------------------------------- render: prévia e exportação do MESMO plano
@pytest.fixture
def com_marcas(studio, tmp_path):
    marcas = [1.5, 3.3, 5.6, 8.2]
    fonte_com_marcas(studio.UP / f"{FID}.mp4", marcas)
    return marcas


def _rodar(fn, *args):
    t = T.Tarefa("teste", "teste")
    return fn(t, *args), t


def test_render_reordenado_coloca_cada_marca_onde_o_mapa_diz(studio, com_marcas):
    p = plano_base(segs=((5.0, 6.5), (1.0, 2.0), (7.9, 8.6), (3.0, 3.6)))
    r, t = _rodar(E.render_plano, p, "previa")
    assert t.progresso == pytest.approx(0.99, abs=0.02)
    bipes, flashes = medir_marcas(E.CACHE / "previas" / r["arquivo"])
    vp = E.validar_plano(p)
    esperado = sorted(x for m in com_marcas for x in E.fonte_para_saida(vp, FID, m))
    assert len(bipes) == len(esperado) == len(flashes) == 4
    for b, f, e in zip(bipes, flashes, esperado):
        assert b == pytest.approx(e, abs=0.012) and f == pytest.approx(e, abs=1 / 30 + 0.002)


def test_previa_e_exportacao_tem_a_mesma_composicao(studio, com_marcas):
    p = plano_base(segs=((1.0, 2.0), (5.0, 6.0)))
    p["legenda"]["frases"] = [frase("f1", [("FALA", 1.2, 1.9)])]
    p["titulos"] = [{"id": "t", "texto": "TITULO", "ini": 0, "fim": 2, "origem": "usuario"}]
    p["efeitos"]["zoom"]["ativo"] = True
    prev, _ = _rodar(E.render_plano, p, "previa")
    fin, _ = _rodar(E.render_plano, p, "final", "teste")
    a, b = E.CACHE / "previas" / prev["arquivo"], studio.OUT / fin["arquivo"]
    assert A.probe(b)["largura"] == 1080 and A.probe(a)["largura"] == 540
    assert abs(prev["duracao"] - fin["duracao"]) < 0.05
    # mesmo quadro (legenda + título + zoom) nas duas, comparado em 540x960
    def quadro(arq):
        return subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", "0.5", "-i", str(arq), "-frames:v", "1",
                               "-vf", "scale=540:960,format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    qa, qb = quadro(a), quadro(b)
    dif = sum(abs(x - y) for x, y in zip(qa, qb)) / len(qa)
    assert len(qa) == len(qb) == 540 * 960 and dif < 6, dif
    reg = json.loads(b.with_suffix(".json").read_text(encoding="utf-8"))
    assert reg["plano"]["titulos"][0]["texto"] == "TITULO"


def test_previa_usa_cache_por_plano(studio, com_marcas):
    p = plano_base(segs=((1.0, 2.0),))
    r1, _ = _rodar(E.render_plano, p, "previa")
    r2, _ = _rodar(E.render_plano, p, "previa")
    assert r1["arquivo"] == r2["arquivo"] and r2["em_cache"]
    p["quadro"]["crop_x"] = 0.1
    r3, _ = _rodar(E.render_plano, p, "previa")
    assert r3["arquivo"] != r1["arquivo"]


def test_cancelar_render_para_e_limpa(studio, com_marcas):
    p = plano_base(segs=tuple((i * 0.5, i * 0.5 + 0.4) for i in range(20)))
    t = T.iniciar("render", "teste", E.render_plano, p, "final", "x")
    while t.estado in ("na_fila",) or (t.estado == "rodando" and t.progresso < 0.05):
        time.sleep(0.05)
    t.cancelar()
    for _ in range(200):
        if t.estado not in ("na_fila", "rodando"):
            break
        time.sleep(0.05)
    assert t.estado == "cancelada"
    assert not [d for d in studio.TMP.iterdir() if d.name != ".keep"] and not list(studio.OUT.glob("*.mp4"))


def test_miniaturas_dos_presets_sao_diferentes(studio, com_marcas):
    p = plano_base()
    p["legenda"]["frases"] = [frase("f1", [("ola", 1.5, 1.8), ("mundo", 1.9, 2.2)])]
    nomes = {k: E.miniatura(p, v) for k, v in E.PRESETS.items()}
    assert len(E.PRESETS) >= 3 and len(set(nomes.values())) == len(nomes)
    dados = {(E.CACHE / "miniaturas" / n).read_bytes() for n in nomes.values()}
    assert len(dados) == len(nomes)


# ---------------------------------------------------------------- projetos
def test_projeto_salvar_reabrir_e_validar(studio, com_marcas):
    cli = studio.app.test_client()
    p = plano_base()
    r = cli.post("/api/projetos", json={"nome": "Meu corte", "plano": p})
    assert r.status_code == 201
    pid = r.get_json()["id"]
    p["segmentos"].reverse()
    assert cli.put(f"/api/projetos/{pid}", json={"nome": "Meu corte", "plano": p}).status_code == 200
    aberto = cli.get(f"/api/projetos/{pid}").get_json()
    assert [s["id"] for s in aberto["plano"]["segmentos"]] == ["s1", "s0"] and aberto["fontes_faltando"] == []
    ruim = copy.deepcopy(p)
    ruim["segmentos"][0]["fim"] = "x"
    r = cli.put(f"/api/projetos/{pid}", json={"nome": "x", "plano": ruim})
    assert r.status_code == 400 and "segmentos[0].fim" in r.get_json()["erro"]
    assert cli.get(f"/api/projetos/{pid}").get_json()["nome"] == "Meu corte"   # o salvo continua intacto
    (E.EDICOES / f"{pid}.json").write_text("{quebrado", encoding="utf-8")
    assert "corrompido" in cli.get(f"/api/projetos/{pid}").get_json()["erro"]
    assert cli.get("/api/projetos/../../app").status_code == 404
