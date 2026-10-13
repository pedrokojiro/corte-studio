"""Prévia de trecho (mesmo plano/motor da exportação), texto na tela com estilo próprio, texto da publicação separado do
vídeo, sugestões com destino e Busca Google do Gemini (SIMULADA) com confirmação pelo oEmbed do YouTube."""
import json
import subprocess

import numpy as np
import pytest

import app as A
import conteudo as C
import editor as E
import referencias as R
import sugestoes as S
import tarefas as T
from test_conteudo import FID, PAL, _fonte, plano, tarefa
from test_linha_do_tempo import fonte_com_marcas, medir_marcas
from test_referencias import Rede


# ---------------------------------------------------------------- prévia de trecho
def test_recortar_plano_preserva_o_que_aparece_naquele_trecho():
    p = plano(((30.0, 42.0), (0.0, 13.0)))                                      # saída: bolo 0-12, cobra 12-25
    p["legenda"]["frases"] = E.agrupar([tuple(w) for w in PAL], 3, fonte=FID)
    p["titulos"] = [{"id": "t1", "texto": "Topo", "ini": 10.0, "fim": 14.0, "origem": "usuario"}]
    p["audio"]["efeitos"] = [{"id": "e1", "som": "whoosh", "fonte": None, "t": 11.5, "dur": 0.6, "volume": 0.2},
                             {"id": "e2", "som": "impacto", "fonte": FID, "t": 4.5, "dur": 1.0, "volume": 0.3},
                             {"id": "e3", "som": "bipe", "fonte": None, "t": 2.0, "dur": 0.5, "volume": 0.2}]
    q = E.recortar_plano(E.validar_plano(p), 10.0, 18.0)
    assert [(s["ini"], s["fim"]) for s in q["segmentos"]] == [(40.0, 42.0), (0.0, 6.0)]
    assert q["titulos"][0]["ini"] == pytest.approx(0.0) and q["titulos"][0]["fim"] == pytest.approx(4.0)
    ef = {e["id"]: e for e in E.efeitos_saida(q)}
    assert ef["e1"]["t_saida"] == pytest.approx(1.5)                            # preso à saída: deslocado
    assert ef["e2"]["t_saida"] == pytest.approx(2.0 + 4.5)                      # preso à fonte: segue a projeção
    assert "e3" not in ef                                                       # fora do trecho
    # a legenda do trecho é exatamente a da edição completa, deslocada
    completa = [(e["palavras"][0][0], e["ini"] - 10.0) for e in E.eventos_legenda(E.validar_plano(p)) if 10.0 <= e["ini"] < 18.0]
    trecho = [(e["palavras"][0][0], e["ini"]) for e in E.eventos_legenda(q)]
    assert [w for w, _ in trecho] == [w for w, _ in completa]
    assert all(abs(a[1] - b[1]) < 0.04 for a, b in zip(trecho, completa))
    with pytest.raises(A.Erro, match="fora da linha"):
        E.recortar_plano(p, 100, 105)


def test_rota_render_trecho_valida(studio):
    (studio.UP / f"{FID}.mp4").write_bytes(b"x")
    cli = studio.app.test_client()
    p = plano()
    assert cli.post("/api/render", json={"plano": p, "qualidade": "final", "trecho": [0, 5]}).status_code == 400
    assert cli.post("/api/render", json={"plano": p, "trecho": [0, 60]}).status_code == 400   # até 30 s
    assert cli.post("/api/render", json={"plano": p, "trecho": [100, 105]}).status_code == 400


def test_render_do_trecho_bate_com_a_exportacao(studio):
    """Real (ffmpeg): marcas de bipe+flash na fonte; o trecho [2, 6) da saída tem as marcas onde a saída completa tem."""
    marcas = [1.5, 3.3, 5.6, 8.2]
    fonte_com_marcas(studio.UP / f"{FID}.mp4", marcas)
    p = E.plano_vazio()
    p["fontes"][FID] = {"nome": "m.mp4", "duracao": 10.0, "tem_audio": True}
    p["segmentos"] = [{"id": "a", "fonte": FID, "ini": 5.0, "fim": 9.0}, {"id": "b", "fonte": FID, "ini": 1.0, "fim": 4.0}]
    p["audio"]["normalizar"] = False
    r = E.render_plano(tarefa(), p, "previa", None, (2.0, 6.0))
    bipes, flashes = medir_marcas(E.CACHE / "previas" / r["arquivo"])
    vp = E.validar_plano(p)
    esperado = sorted(x - 2.0 for m in marcas for x in E.fonte_para_saida(vp, FID, m) if 2.0 <= x < 6.0)
    assert len(bipes) == len(esperado) >= 2
    assert all(abs(b - e) < 0.02 for b, e in zip(bipes, esperado)) and r["duracao"] == pytest.approx(4.0, abs=0.1)


# ---------------------------------------------------------------- texto na tela com aparência própria
def test_texto_na_tela_estilo_posicao_tamanho_e_compatibilidade():
    p = plano()
    p["titulos"] = [{"id": "t1", "texto": "Antigo", "ini": 0, "fim": 2, "origem": "usuario"},             # projeto antigo
                    {"id": "t2", "texto": "Novo", "ini": 1, "fim": 3, "estilo": "contorno", "posicao_y": 0.5, "tamanho": 90}]
    v = E.validar_plano(p)
    assert v["titulos"][0]["estilo"] == "claro" and v["titulos"][0]["posicao_y"] == 0.14
    linhas = [l for l in E.ass_plano(v).splitlines() if l.startswith("Dialogue: 1")]
    assert ",Titulo,," in linhas[0] and "\\pos(540,269)" in linhas[0] and "\\fs64" in linhas[0]
    assert ",TituloContorno,," in linhas[1] and "\\pos(540,960)" in linhas[1] and "\\fs90" in linhas[1]
    p["titulos"][1]["estilo"] = "neon"
    with pytest.raises(A.Erro, match="estilo"):
        E.validar_plano(p)


# ---------------------------------------------------------------- texto da publicação (projeto, não vídeo)
def test_publicacao_salva_reabre_e_nao_entra_no_plano(studio):
    (studio.UP / f"{FID}.mp4").write_bytes(b"x")
    cli = studio.app.test_client()
    p = plano()
    r = cli.post("/api/projetos", json={"nome": "P", "plano": p, "publicacao": {"texto": "Lenda da cobra #lenda"}})
    pid = r.get_json()["id"]
    aberto = cli.get(f"/api/projetos/{pid}").get_json()
    assert aberto["publicacao"]["texto"] == "Lenda da cobra #lenda" and aberto["plano"]["titulos"] == []
    assert "publicacao" not in aberto["plano"]
    # projeto antigo (sem o campo) abre com texto vazio
    antigo = json.loads((E.EDICOES / f"{pid}.json").read_text(encoding="utf-8"))
    antigo.pop("publicacao")
    (E.EDICOES / f"{pid}.json").write_text(json.dumps(antigo), encoding="utf-8")
    assert cli.get(f"/api/projetos/{pid}").get_json()["publicacao"] == {"texto": ""}
    assert cli.put(f"/api/projetos/{pid}", json={"nome": "P", "plano": p, "publicacao": {"texto": "x" * 4001}}).status_code == 400


def test_sugestao_de_publicacao_nao_cria_texto_no_video(studio):
    _fonte(studio)
    p = plano(((30.0, 42.0),))
    res = C.sugerir_titulos(tarefa(), p, "s0", "corte", "local", False, False, [], "publicacao")
    assert res["destino"] == "publicacao" and "NÃO aparece no vídeo" in res["destino_rotulo"]
    assert res["titulos"] and all("#" in t["texto"] for t in res["titulos"])
    assert "cobra" not in json.dumps(res, ensure_ascii=False).lower()
    assert p["titulos"] == []                                                  # nada foi aplicado ao plano
    cli = studio.app.test_client()
    assert cli.post("/api/titulos/sugerir", json={"plano": p, "segmento": "s0", "destino": "post"}).status_code == 400


def test_gemini_publicacao_usa_o_prompt_proprio(studio, monkeypatch):
    _fonte(studio)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    enviados = []

    def http(metodo, url, cab=None, corpo=None, timeout=60):
        enviados.append(json.loads(corpo)["input"][0]["text"])
        resp = {"titulos": [{"texto": "A receita do bolo de chocolate no forno #bolo #receita", "tipo": "descritivo",
                             "explicacao": "resume", "frases": [0, 1]}]}
        return 200, {}, json.dumps({"steps": [{"type": "model_output", "content": [{"type": "text", "text": json.dumps(resp)}]}]}).encode()
    monkeypatch.setattr(R, "_http", http)
    res = C.sugerir_titulos(tarefa(), plano(((30.0, 42.0),)), "s0", "corte", "auto", False, False, [], "publicacao")
    assert "TEXTO DA PUBLICAÇÃO" in enviados[0] and "NÃO aparece dentro do vídeo" in enviados[0]
    assert res["provedor"] == "gemini" and res["titulos"][0]["alerta"] is None   # hashtags não contam como invenção


# ---------------------------------------------------------------- Busca Google do Gemini (simulada) + oEmbed
def _passos(texto, citacoes, consultas):
    return {"steps": [{"type": "google_search_call", "arguments": {"queries": consultas}},
                      {"type": "google_search_result", "result": [{"search_suggestions": "<div>sugestões</div>"}]},
                      {"type": "model_output", "content": [{"type": "text", "text": texto,
                                                            "annotations": [{"type": "url_citation", **c} for c in citacoes]}]}]}


def test_padroes_sem_desempenho_viram_recomendacao_ligada_ao_corte(studio):
    _fonte(studio)
    p = plano(((0.0, 13.0),))
    p["segmentos"][0]["ini"] = 0.0
    refs = [{"id": i, "nome": i, "tipo": "youtube", "tags": {}, "metadados": {"fonte": "YouTube oEmbed", "views": None},
             "analises": {"gemini": {"caracteristicas": {"fala_nos_3s": True, "efeitos_sonoros": True}}}} for i in ("A", "B", "C")]
    comp = R.comparar(refs)
    assert comp["hipoteses"] == [] and {x["metrica"] for x in comp["padroes"]} == {"fala_nos_3s", "efeitos_sonoros"}
    p2 = S.aplicar_operacao(p, {"tipo": "cortar_inicio", "novo_ini": 0.0})
    p2["segmentos"][0]["ini"] = 0.0
    rec = S.recomendacoes(p2, comp)["recomendacoes"]
    efe = next(r for r in rec if r["titulo"] == "Efeitos sonoros com propósito")
    assert "sem dado de desempenho" in efe["forca"] and efe["operacao"] is None and efe["evidencia"][0]["padrao"]
