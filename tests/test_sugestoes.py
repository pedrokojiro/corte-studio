"""Ganchos (fala real x título derivado) e operações reversíveis sobre o plano."""
import copy
import json

import pytest

import app as A
import editor as E
import referencias as R
import sugestoes as S

FID = "aaaaaaaaaaaa"


def _transcricao(studio, palavras, falas):
    (studio.UP / f"{FID}.mp4").write_bytes(b"conteudo-falso-so-para-o-hash")
    E._arq_transcricao(FID).write_text(json.dumps({"versao": 1, "palavras": palavras, "falas": falas, "duracao": 60}),
                                       encoding="utf-8")


PAL = [[0.5, 0.8, "Então"], [0.8, 1.0, "tipo"], [1.0, 1.4, "começou"], [1.4, 1.8, "aqui."],
       [20.0, 20.3, "Você"], [20.3, 20.6, "sabe"], [20.6, 21.0, "quem"], [21.0, 21.4, "morreu"], [21.4, 21.8, "naquela"],
       [21.8, 22.3, "noite?"],
       [30.0, 30.4, "Foram"], [30.4, 30.8, "três"], [30.8, 31.2, "dias"], [31.2, 31.6, "sem"], [31.6, 32.0, "ninguém"],
       [32.0, 32.5, "aparecer."]]
FALAS = [{"ini": 0.5, "fim": 1.8, "texto": "x", "db": -30.0}, {"ini": 20.0, "fim": 22.3, "texto": "x", "db": -20.0},
         {"ini": 30.0, "fim": 32.5, "texto": "x", "db": -29.0}]


def plano(segs=((0.0, 40.0),)):
    p = E.plano_vazio()
    p["fontes"][FID] = {"nome": "f", "duracao": 60.0, "tem_audio": True}
    p["segmentos"] = [{"id": f"s{i}", "fonte": FID, "ini": a, "fim": b} for i, (a, b) in enumerate(segs)]
    return p


def test_ganchos_vem_da_fala_real_com_tempo_e_motivo(studio):
    _transcricao(studio, PAL, FALAS)
    g = S.ganchos(FID)
    top = g["falados"][0]
    assert top["texto"] == "Você sabe quem morreu naquela noite?" and (top["ini"], top["fim"]) == (20.0, 22.3)
    assert any("pergunta" in m for m in top["motivos"]) and any("+" in m and "dB" in m for m in top["motivos"])
    assert all(c["tipo"] == "fala" for c in g["falados"])
    textos_reais = {" ".join(w[2] for w in s["palavras"]) for s in S.sentencas(PAL)}
    assert all(c["texto"] in textos_reais for c in g["falados"])           # nada inventado
    assert "Não prevê desempenho" in g["metodo"]


def test_titulos_sao_separados_e_marcados_como_editoriais(studio):
    _transcricao(studio, PAL, FALAS)
    t = S.ganchos(FID)["titulos"]
    assert t and all(x["tipo"] == "titulo" and "não é citação" in x["nota"] and '"' not in x["texto"] for x in t)
    assert not any(x["texto"].lower().startswith(("então", "tipo")) for x in t)


def test_aplicar_gancho_e_titulo_nao_toca_na_legenda(studio):
    p = E.validar_plano(plano())
    p["legenda"]["frases"] = [{"id": "f1", "fonte": FID, "palavras": [{"t": "Você", "ini": 20.0, "fim": 20.3}]}]
    p1 = S.aplicar_operacao(p, {"tipo": "inserir_inicio", "fonte": FID, "ini": 20.0, "fim": 22.3})
    assert p1["segmentos"][0]["ini"] == pytest.approx(19.867, abs=0.02) and len(p1["segmentos"]) == 2
    assert p1["legenda"]["frases"] == p["legenda"]["frases"]            # legenda intacta…
    assert len(E.fonte_para_saida(p1, FID, 20.1)) == 2                  # …e a fala aparece nos dois lugares
    p2 = S.aplicar_operacao(p1, {"tipo": "adicionar_titulo", "texto": "Quem morreu naquela noite?", "ini": 0, "fim": 2.4})
    assert p2["titulos"][0]["origem"] == "sugestao_local" and p["titulos"] == []   # original não mudou


@pytest.mark.parametrize("op, msg", [
    ({"tipo": "rm -rf"}, "desconhecida"), ("texto", "inválida"),
    ({"tipo": "inserir_inicio", "fonte": "bbbbbbbbbbbb", "ini": 1, "fim": 2}, "Fonte"),
    ({"tipo": "cortar_inicio", "novo_ini": 39.9}, "dentro do primeiro"),
    ({"tipo": "ativar_zoom", "intensidade": 5}, "fora da faixa"),
    ({"tipo": "adicionar_titulo", "texto": "", "ini": 0, "fim": 2}, "vazio"),
])
def test_operacao_invalida_e_recusada(studio, op, msg):
    with pytest.raises(A.Erro, match=msg):
        S.aplicar_operacao(plano(), op)


def test_remover_trecho_divide_o_segmento(studio):
    p = S.aplicar_operacao(plano(), {"tipo": "remover_trecho", "fonte": FID, "ini": 10.0, "fim": 12.0})
    assert [(s["ini"], s["fim"]) for s in p["segmentos"]] == [(0.0, 10.0), (12.0, 40.0)]


def test_recomendacoes_ligadas_a_evidencia_e_aplicaveis(studio):
    pal = [[3.0, 3.4, "Ninguém"], [3.4, 3.8, "volta"], [3.8, 4.1, "da"], [4.1, 4.5, "mata."],
           [9.0, 9.4, "Ninguém"], [9.4, 9.8, "volta"], [9.8, 10.1, "da"], [10.1, 10.5, "mata."]]
    _transcricao(studio, pal, [{"ini": 3.0, "fim": 4.5, "texto": "x", "db": -20}])
    p = plano(((0.0, 12.0),))
    p["legenda"]["frases"] = E.agrupar([tuple(w) for w in pal], 4, fonte=FID)
    refs = [{"id": "A", "nome": "A", "tipo": "youtube", "tags": {}, "metadados": {"fonte": "YouTube", "views": 90000,
             "publicado_em": "2026-10-01T00:00:00Z", "consultado_em": "2026-10-11T00:00:00Z"},
             "analises": {"local": {"metricas": {"primeira_fala_s": 0.4, "repeticoes": 0}}}},
            {"id": "B", "nome": "B", "tipo": "youtube", "tags": {}, "metadados": {"fonte": "YouTube", "views": 900,
             "publicado_em": "2026-10-01T00:00:00Z", "consultado_em": "2026-10-11T00:00:00Z"},
             "analises": {"local": {"metricas": {"primeira_fala_s": 2.6, "repeticoes": 2}}}}]
    comp = R.comparar(refs)
    rec = S.recomendacoes(p, comp)
    titulos = [r["titulo"] for r in rec["recomendacoes"]]
    assert "Começar já na fala" in titulos and "Tirar uma repetição" in titulos
    abrir = next(r for r in rec["recomendacoes"] if r["titulo"] == "Começar já na fala")
    assert abrir["forca"].startswith("evidência muito fraca") and abrir["evidencia"]
    novo = S.aplicar_operacao(p, abrir["operacao"])
    assert novo["segmentos"][0]["ini"] == pytest.approx(2.85, abs=0.04)
    rep = next(r for r in rec["recomendacoes"] if r["titulo"] == "Tirar uma repetição")
    sem_rep = S.aplicar_operacao(p, rep["operacao"])
    falas = [w[0] for e in E.eventos_legenda(sem_rep) for w in e["palavras"]]
    assert falas.count("Ninguém") == 1                                  # a 2a ocorrência saiu da saída


def test_rota_ganchos_exige_transcricao(studio):
    (studio.UP / f"{FID}.mp4").write_bytes(b"x")
    r = studio.app.test_client().post("/api/ganchos", json={"plano": plano()})
    assert r.status_code == 400 and "Transcreva" in r.get_json()["erro"]
