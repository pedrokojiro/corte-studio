"""Conteúdo do corte: legenda por trecho, correções preservadas, títulos/ganchos/áudio/cortes por conteúdo,
provedor de IA simulado (válido, malformado, inventando fala), pesquisa de referências simulada e efeitos no render.

Real (ffmpeg/Whisper/edge-tts) só onde está escrito; o Gemini e as APIs do YouTube são SEMPRE simulados aqui."""
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
from test_referencias import Rede

FID = "aaaaaaaaaaaa"
# Dois assuntos em intervalos separados da MESMA fonte: 0-12 s (cobra na mata), 30-42 s (bolo de chocolate).
COBRA = [(0.5, "Você"), (0.8, "sabe"), (1.1, "por"), (1.3, "que"), (1.5, "ninguém"), (1.9, "volta"), (2.2, "da"), (2.4, "mata?"),
         (4.0, "A"), (4.2, "cobra"), (4.6, "de"), (4.8, "fogo"), (5.1, "aparece"), (5.6, "na"), (5.8, "mata."),
         (8.0, "Foi"), (8.2, "aí"), (8.4, "que"), (8.6, "ele"), (8.8, "descobriu"), (9.3, "o"), (9.5, "segredo."),
         (11.0, "A"), (11.2, "cobra"), (11.6, "protege"), (12.0, "a"), (12.2, "mata.")]
BOLO = [(30.5, "Agora"), (30.9, "a"), (31.1, "receita"), (31.6, "do"), (31.8, "bolo"), (32.2, "de"), (32.4, "chocolate."),
        (34.0, "Misture"), (34.5, "três"), (34.9, "ovos"), (35.3, "e"), (35.5, "farinha."),
        (37.0, "Leve"), (37.3, "o"), (37.5, "bolo"), (37.8, "ao"), (38.0, "forno."),
        (40.0, "O"), (40.2, "bolo"), (40.5, "de"), (40.7, "chocolate"), (41.2, "fica"), (41.5, "fofo.")]
PAL = [[t, round(t + 0.18, 2), w] for t, w in COBRA + BOLO]


def tarefa():
    return T.Tarefa("t", "t")


def _fonte(studio, palavras=PAL, completa=True, cobertura=None):
    (studio.UP / f"{FID}.mp4").write_bytes(b"conteudo-falso-so-para-o-hash")
    falas = [{"ini": 0.5, "fim": 2.6, "texto": "x", "db": -30.0}, {"ini": 8.0, "fim": 9.7, "texto": "x", "db": -18.0}]
    if completa:
        E._arq_transcricao(FID).write_text(json.dumps({"versao": 1, "palavras": palavras, "falas": falas, "duracao": 60.0}),
                                           encoding="utf-8")
    else:
        E._gravar_json(E._arq_trechos(FID), {"versao": 1, "palavras": palavras, "falas": falas, "duracao": 60.0,
                                             "cobertura": cobertura})


def plano(segs=((0.0, 13.0),)):
    p = E.plano_vazio()
    p["fontes"][FID] = {"nome": "f.mp4", "duracao": 60.0, "tem_audio": True}
    p["segmentos"] = [{"id": f"s{i}", "fonte": FID, "ini": a, "fim": b} for i, (a, b) in enumerate(segs)]
    return E.validar_plano(p)


# ---------------------------------------------------------------- 1) legenda do corte
def test_legenda_do_corte_nao_traz_fala_de_fora(studio):
    _fonte(studio)
    p = plano(((29.5, 42.0),))
    frases, r = E.mesclar_frases([], FID, [(29.5, 42.0)], E.palavras_no_intervalo(FID, 29.5, 42.0))
    texto = " ".join(w["t"] for f in frases for w in f["palavras"])
    assert "bolo" in texto and "cobra" not in texto and "mata" not in texto
    p["legenda"]["frases"] = frases
    assert all(29.5 <= w["ini"] < 42.0 for f in frases for w in f["palavras"]) and r["novas"] == len(frases)


def test_regerar_mantem_correcao_e_avisa_quando_o_corte_a_parte(studio):
    _fonte(studio)
    pal = E.palavras_no_intervalo(FID, 29.5, 42.0)
    frases, _ = E.mesclar_frases([], FID, [(29.5, 42.0)], pal, por_frase=3)
    corrigida = E.editar_texto_frase(frases[0], "Agora a RECEITA")
    frases[0] = corrigida
    assert corrigida["editada"] is True
    novas, r = E.mesclar_frases(frases, FID, [(29.5, 42.0)], pal, por_frase=3)
    assert r["mantidas_editadas"] == 1 and any(f.get("editada") and f["palavras"][2]["t"] == "RECEITA" for f in novas)
    assert sum("Agora" in [w["t"] for w in f["palavras"]] for f in novas) == 1   # a fala nova não duplica a corrigida
    p = plano(((31.0, 42.0),))                                                   # aparar o início corta a correção
    p["legenda"]["frases"] = novas
    av = E.avisos_legenda(E.validar_plano(p))
    assert av and av[0]["tipo"] == "cortada" and av[0]["dentro"] == 1 and av[0]["total"] == 3
    p["segmentos"] = [{"id": "x", "fonte": FID, "ini": 0.0, "fim": 13.0}]          # fora da linha: guardada, avisada
    av = E.avisos_legenda(E.validar_plano(p))
    assert av[0]["tipo"] == "fora" and any(f.get("editada") for f in E.validar_plano(p)["legenda"]["frases"])


def test_frase_automatica_na_borda_perde_so_as_palavras_de_dentro():
    fr = [{"id": "f1", "fonte": FID, "palavras": [{"t": "a", "ini": 9.0, "fim": 9.2}, {"t": "b", "ini": 10.5, "fim": 10.7}]}]
    out, r = E.mesclar_frases(fr, FID, [(10.0, 20.0)], [[10.5, 10.7, "b"]])
    assert [[w["t"] for w in f["palavras"]] for f in out] == [["a"], ["b"]] and r["substituidas"] == 1


def test_tres_relogios_e_reordenar(studio):
    p = plano(((30.0, 42.0), (0.0, 13.0)))
    p["legenda"]["frases"] = E.agrupar([tuple(w) for w in PAL], 3, fonte=FID)
    ev = E.eventos_legenda(E.validar_plano(p))
    bolo = next(e for e in ev if e["palavras"][0][0] == "Agora")
    assert bolo["seg"] == "s0" and bolo["src"] == pytest.approx(30.5) and bolo["rel"] == pytest.approx(0.5)
    assert bolo["ini"] == pytest.approx(0.5)                                     # saída: o bolo vem primeiro
    p["segmentos"].reverse()
    bolo = next(e for e in E.eventos_legenda(E.validar_plano(p)) if e["palavras"][0][0] == "Agora")
    assert bolo["ini"] == pytest.approx(13.0 + 0.5) and bolo["rel"] == pytest.approx(0.5)


def test_transcrever_trechos_so_transcreve_o_que_falta(studio, monkeypatch):
    (studio.UP / f"{FID}.mp4").write_bytes(b"x")
    chamadas = []
    monkeypatch.setattr(A, "probe", lambda src: {"duracao": 60.0, "tem_audio": True, "largura": 1, "altura": 1})
    monkeypatch.setattr(E, "_decodificar", lambda src, ini=None, fim=None, timeout=0: (ini, fim))

    def whisper(t, audio, offset, *a):
        chamadas.append(audio)
        ini, fim = audio
        return [w for w in PAL if ini <= w[0] < fim], []
    monkeypatch.setattr(E, "_whisper", whisper)
    r = E.transcrever_trechos(tarefa(), FID, [[30.0, 42.0]])
    assert not r["em_cache"] and r["transcrito_s"] == pytest.approx(12.0)
    assert not E.transcricao_em_cache(FID)                                       # a fonte inteira NÃO foi transcrita
    assert all(30.0 <= (w[0] + w[1]) / 2 < 42.0 for w in E.transcricao_combinada(FID)["palavras"])
    r = E.transcrever_trechos(tarefa(), FID, [[31.0, 40.0]])                      # dentro do que já foi feito
    assert r["em_cache"] and len(chamadas) == 1
    r = E.transcrever_trechos(tarefa(), FID, [[0.0, 13.0], [30.0, 42.0]])         # só a parte nova
    assert r["transcrito_s"] == pytest.approx(13.0) and len(chamadas) == 2
    assert E.transcricao_combinada(FID)["cobertura"] == [[0.0, 13.0], [30.0, 42.0]]


def test_rota_gerar_legenda_exige_trecho_transcrito(studio):
    _fonte(studio, completa=False, cobertura=[[30.0, 42.0]])
    cli = studio.app.test_client()
    p = plano(((0.0, 13.0),))
    r = cli.post("/api/legenda/gerar", json={"plano": p, "trechos": [{"fonte": FID, "ini": 0.0, "fim": 13.0}]})
    assert r.status_code == 400 and "transcrito" in r.get_json()["erro"]
    r = cli.post("/api/legenda/gerar", json={"plano": p, "trechos": [{"fonte": FID, "ini": 30.0, "fim": 42.0}]})
    assert r.status_code == 200 and r.get_json()["resumo"]["novas"] > 0


# ---------------------------------------------------------------- 2/3) títulos e ganchos (local)
def test_titulos_locais_falam_so_do_corte(studio):
    _fonte(studio)
    for segs, proibido in (((30.0, 42.0),), ("cobra", "mata")), (((0.0, 13.0),), ("bolo", "chocolate")):
        res = C.sugerir_titulos(tarefa(), plano(segs), "s0", "corte", "local", False, False, [])
        assert res["provedor"] == "local" and res["titulos"]
        blob = json.dumps(res, ensure_ascii=False).lower()
        assert not any(x in blob for x in proibido)
        assert all(t["base"] for t in res["titulos"])                            # cada título aponta a fala que o sustenta
    # gerar outras: não repete
    r1 = C.sugerir_titulos(tarefa(), plano(), "s0", "corte", "local", False, False, [])
    r2 = C.sugerir_titulos(tarefa(), plano(), "s0", "corte", "local", False, False, [t["texto"] for t in r1["titulos"]])
    assert not {t["texto"] for t in r1["titulos"]} & {t["texto"] for t in r2["titulos"]}


def test_ganchos_locais_tem_tipo_entrega_e_proposta(studio):
    _fonte(studio)
    res = C.analisar_ganchos(tarefa(), plano(), "s0", "corte", "local", False, False)
    falas = [g for g in res["ganchos"] if g["tipo"] == "fala"]
    assert falas and all(g["operacao"]["tipo"] == "inserir_inicio" for g in falas)
    pergunta = next(g for g in falas if g["categoria"] == "pergunta")
    assert pergunta["texto"] == "Você sabe por que ninguém volta da mata?"
    assert pergunta["entrega"]["status"] in ("provavel", "parcial") and pergunta["entrega"]["trecho"]
    assert any(g["tipo"] == "titulo" for g in res["ganchos"])
    assert "bolo" not in json.dumps(res, ensure_ascii=False)
    novo = S.aplicar_operacoes(plano(), res["proposta"]["operacoes"])
    assert len(novo["segmentos"]) == 2 and len(novo["titulos"]) == 1


def test_sinais_nao_escolhe_primeira_frase_so_por_ser_primeira(studio):
    pal = [[0.5, 0.8, "Então"], [0.8, 1.0, "tipo"], [1.0, 1.4, "começou"], [1.4, 1.8, "aqui."],
           [5.0, 5.3, "Mas"], [5.3, 5.6, "ninguém"], [5.6, 6.0, "sabia"], [6.0, 6.4, "o"], [6.4, 6.8, "segredo."]]
    _fonte(studio, pal)
    res = C.analisar_ganchos(tarefa(), plano(((0.0, 10.0),)), "s0", "corte", "local", False, False)
    textos = [g["texto"] for g in res["ganchos"] if g["tipo"] == "fala"]
    assert "Então tipo começou aqui." not in textos


# ---------------------------------------------------------------- 7) cortes pelo conteúdo
def test_descobrir_local_separa_os_assuntos_e_descreve():
    frases = C.frases_de([tuple(w) for w in PAL], FID)
    cortes = C.descobrir_local(frases, [12.5, 29.0], 60.0, 8, 15)
    assert len(cortes) >= 2
    for c in cortes:
        assert {"rank", "titulo", "ideia", "por_que", "trecho", "ini", "fim", "dur", "gancho", "limitacoes", "criterios"} <= set(c)
        assert not (c["ini"] < 13 and c["fim"] > 29)                           # nenhum mistura os dois assuntos
        assert len(c["criterios"]) == 6 and all(x["texto"] for x in c["criterios"])
    assert [c["rank"] for c in cortes] == list(range(1, len(cortes) + 1))
    tit = " ".join(c["titulo"] + " " + c["ideia"] for c in cortes).lower()
    assert ("cobra" in tit or "mata" in tit) and "bolo" in tit


# ---------------------------------------------------------------- provedor simulado (Gemini)
def _bytes(texto):
    st, cab, corpo = _gemini(texto)
    return st, cab, json.dumps(corpo).encode()


def _gemini(texto):
    return (200, {}, {"steps": [{"type": "model_output", "content": [{"type": "text", "text": texto}]}]})


def test_gemini_ganchos_cita_fala_por_indice_e_descarta_invencao(studio, monkeypatch):
    _fonte(studio)
    monkeypatch.setenv("GEMINI_API_KEY", "chave-teste")
    resp = {"ganchos": [
        {"tipo": "fala", "categoria": "pergunta", "frase_ini": 0, "frase_fim": 0, "motivo": "pergunta direta",
         "promessa": "explicar o sumiço", "entrega": {"status": "entrega", "frase": 3, "explicacao": "a cobra protege"}},
        {"tipo": "fala", "frase_ini": 99, "frase_fim": 99, "motivo": "inventado"},           # fora do escopo
        {"tipo": "visual", "quadro": 0, "motivo": "sem quadro nenhum"},                     # não viu imagem
        {"tipo": "titulo", "categoria": "revelacao", "texto": "O segredo da mata", "motivo": "curiosidade",
         "entrega": {"status": "entrega", "frase": 2}},
        {"tipo": "narracao", "texto": "Esta história foi contada pelo meu avô.", "motivo": "contexto",
         "entrega": {"status": "parcial"}}]}
    rede = Rede(monkeypatch, {"https://generativelanguage.googleapis.com/v1beta/interactions": _gemini(json.dumps(resp))})
    res = C.analisar_ganchos(tarefa(), plano(), "s0", "corte", "auto", True, False)
    assert res["provedor"] == "gemini"
    tipos = [g["tipo"] for g in res["ganchos"]]
    assert tipos == ["fala", "titulo", "narracao"]
    assert res["ganchos"][0]["texto"] == "Você sabe por que ninguém volta da mata?"     # texto vem da transcrição
    assert res["ganchos"][2]["operacao"] is None                                          # narração nova não é aplicada
    assert any("descartados" in a for a in res["avisos"]) and any("autorização" in a for a in res["avisos"])
    enviado = [c for c in rede.chamadas if "interactions" in c[1]]
    assert len(enviado) == 1 and "chave-teste" not in enviado[0][1]                        # chave só no cabeçalho


def test_gemini_sem_consentimento_nao_envia_imagem(studio, monkeypatch):
    _fonte(studio)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    corpos = []

    def http(metodo, url, cab=None, corpo=None, timeout=60):
        corpos.append(json.loads(corpo))
        return _bytes(json.dumps({"titulos": [{"texto": "A cobra de fogo protege a mata", "tipo": "descritivo",
                                                "explicacao": "resume", "frases": [1, 3]},
                                               {"texto": "Dragões no céu de Marte", "tipo": "curiosidade",
                                                "explicacao": "inventado", "frases": [0]},
                                               {"texto": "sem apoio", "explicacao": "x", "frases": []}]}))
    monkeypatch.setattr(R, "_http", http)
    res = C.sugerir_titulos(tarefa(), plano(), "s0", "corte", "auto", True, False, [])
    assert all(p["type"] == "text" for p in corpos[0]["input"])
    assert [t["texto"] for t in res["titulos"]] == ["A cobra de fogo protege a mata", "Dragões no céu de Marte"]
    assert res["titulos"][0]["alerta"] is None and "Dragões" in res["titulos"][1]["alerta"]   # invenção sinalizada
    assert "bolo" not in corpos[0]["input"][0]["text"]                                        # só o corte foi enviado


@pytest.mark.parametrize("resposta", ["isto não é json", json.dumps({"outra": []}), json.dumps({"titulos": [{"texto": 3}]})])
def test_gemini_malformado_cai_na_heuristica_e_avisa(studio, monkeypatch, resposta):
    _fonte(studio)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    Rede(monkeypatch, {"https://generativelanguage.googleapis.com/": _gemini(resposta)})
    res = C.sugerir_titulos(tarefa(), plano(), "s0", "corte", "auto", False, False, [])
    assert res["provedor"] == "local" and res["titulos"] and any("Gemini falhou" in a for a in res["avisos"])


def test_sem_credencial_e_provedor_fora_do_ar(studio, monkeypatch):
    _fonte(studio)
    res = C.sugerir_titulos(tarefa(), plano(), "s0", "corte", "gemini", False, False, [])
    assert res["provedor"] == "local" and any("GEMINI_API_KEY" in a for a in res["avisos"])
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    Rede(monkeypatch, {"https://generativelanguage.googleapis.com/": (503, {}, b"indisponivel")})
    res = C.analisar_ganchos(tarefa(), plano(), "s0", "corte", "auto", False, False)
    assert res["provedor"] == "local" and any("503" in a for a in res["avisos"])


def test_gemini_cortes_validados_e_bordas_pela_fala(studio, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    frases = C.frases_de([tuple(w) for w in PAL], FID)
    resp = {"cortes": [{"posicao": 1, "titulo": "A cobra de fogo", "ideia_central": "Lenda: a cobra só aparece a quem entra sozinho.",
                        "tipo": "conta", "frase_ini": 0, "frase_fim": 3, "frase_trecho": 1, "por_que": "história completa",
                        "criterios": {"abertura": "pergunta direta", "fechamento": "explica o segredo"},
                        "gancho": {"tipo": "fala", "frase": 0}, "limitacoes": None},
                       {"posicao": 2, "titulo": "Índice inventado", "ideia_central": "x", "frase_ini": 0, "frase_fim": 99,
                        "frase_trecho": 1, "por_que": "x"},
                       {"posicao": 3, "titulo": "Trecho fora", "ideia_central": "x", "frase_ini": 4, "frase_fim": 2,
                        "frase_trecho": 3, "por_que": "x"}]}
    Rede(monkeypatch, {"https://generativelanguage.googleapis.com/": _gemini(json.dumps(resp))})
    cortes, desc, _ = C.descobrir_ia(tarefa(), frases, [], 60.0, 8, 20)
    assert len(cortes) == 1 and desc == 2
    c = cortes[0]
    assert c["trecho"]["texto"] == frases[1]["texto"] and c["gancho"]["texto"] == frases[0]["texto"]
    assert c["ini"] <= frases[0]["ini"] and c["fim"] >= frases[3]["fim"]
    assert c["rank"] == 1 and c["ideia"].startswith("Lenda") and {x["chave"] for x in c["criterios"]} == {"abertura", "fechamento"}


# ---------------------------------------------------------------- 4) áudio
def test_audio_local_so_com_proposito_e_sem_cobrir_fala(studio, monkeypatch):
    pal = [[0.5, 0.8, "Ninguém"], [0.8, 1.2, "sabia"], [1.2, 1.5, "a"], [1.5, 2.0, "verdade."],
           [4.0, 4.3, "Foi"], [4.3, 4.5, "aí"], [4.5, 4.7, "que"], [4.7, 5.0, "descobri"], [5.0, 5.2, "o"], [5.2, 5.6, "segredo"],
           [5.6, 5.8, "da"], [5.8, 6.2, "mentira."], [8.0, 8.3, "Depois"], [8.3, 8.6, "disso"], [8.6, 9.0, "voltei"], [9.0, 9.4, "segredo."]]
    _fonte(studio, pal)
    monkeypatch.setattr(C, "medir_ruido", lambda p, s: 12.0)
    res = C.sugerir_audio(tarefa(), plano(((0.0, 10.0),)), "s0", "corte", "local")
    assert [t["operacao"] for t in res["tratamento"]] == [{"tipo": "ajustar_audio", "realce_voz": True}]
    assert res["efeitos"] and all(e["pronto"] for e in res["efeitos"])
    frases = C.frases_de([tuple(w) for w in pal], FID)
    for e in res["efeitos"]:
        assert not C._sobre_fala(frases, e["t"], e["t"] + e["dur"])            # nenhum efeito cobre a fala
    p = S.aplicar_operacoes(plano(((0.0, 10.0),)), res["proposta"]["operacoes"])
    assert p["audio"]["realce_voz"] and p["audio"]["efeitos"]
    # corte sem virada nem pausa: nada de efeito à toa
    _fonte(studio, [[0.5, 0.8, "Misture"], [0.8, 1.2, "os"], [1.2, 1.5, "ovos."]])
    monkeypatch.setattr(C, "medir_ruido", lambda p, s: 40.0)
    res = C.sugerir_audio(tarefa(), plano(((0.0, 3.0),)), "s0", "corte", "local")
    assert res["efeitos"] == [] and any("Nenhum efeito" in a for a in res["avisos"]) and res["tratamento"] == []


def test_audio_gemini_externo_precisa_de_arquivo_e_volume_sob_fala(studio, monkeypatch):
    _fonte(studio)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(C, "medir_ruido", lambda p, s: 30.0)
    resp = {"efeitos": [{"som": "impacto", "apos_frase": 2, "dur": 1.0, "volume": 0.5, "objetivo": "pontuar a revelação"},
                        {"som": "externo", "descricao_externa": "passos na mata", "antes_da_frase": 1, "dur": 1.5,
                         "volume": 0.4, "objetivo": "ambientar"},
                        {"som": "explosao_nuclear", "apos_frase": 0, "dur": 1, "volume": 0.2, "objetivo": "x"}]}
    Rede(monkeypatch, {"https://generativelanguage.googleapis.com/": _gemini(json.dumps(resp))})
    res = C.sugerir_audio(tarefa(), plano(), "s0", "corte", "auto")
    pronto, externo = res["efeitos"]
    assert pronto["pronto"] and pronto["operacao"]["som"] == "impacto" and pronto["operacao"]["origem"] == "sugestao_ia"
    assert not externo["pronto"] and externo["operacao"] is None and "direito de usar" in externo["motivo"]
    assert any("descartados" in a for a in res["avisos"])


# ---------------------------------------------------------------- operações novas
def test_operacoes_novas_e_lote_atomico(studio):
    p = plano()
    p = S.aplicar_operacao(p, {"tipo": "adicionar_titulo", "texto": "Original", "ini": 0, "fim": 2})
    tid = p["titulos"][0]["id"]
    p2 = S.aplicar_operacao(p, {"tipo": "definir_titulo", "id": tid, "texto": "Novo texto", "origem": "sugestao_ia"})
    assert p2["titulos"][0]["texto"] == "Novo texto" and p["titulos"][0]["texto"] == "Original"
    p3 = S.aplicar_operacoes(p2, [{"tipo": "adicionar_segmento", "fonte": FID, "ini": 30, "fim": 40},
                                  {"tipo": "adicionar_efeito", "som": "whoosh", "fonte": None, "t": 12.5, "dur": 0.6, "volume": 0.2},
                                  {"tipo": "ajustar_audio", "realce_voz": True}])
    assert len(p3["segmentos"]) == 2 and p3["audio"]["efeitos"][0]["som"] == "whoosh" and p3["audio"]["realce_voz"]
    eid = p3["audio"]["efeitos"][0]["id"]
    assert S.aplicar_operacao(p3, {"tipo": "remover_efeito", "id": eid})["audio"]["efeitos"] == []
    with pytest.raises(A.Erro, match="Operação 2 de 2"):
        S.aplicar_operacoes(p3, [{"tipo": "ativar_zoom", "intensidade": 0.05}, {"tipo": "adicionar_efeito", "som": "rm -rf", "t": 1}])
    with pytest.raises(A.Erro, match="ajuste conhecido"):
        S.aplicar_operacao(p3, {"tipo": "ajustar_audio", "comando": "x"})


def test_efeito_preso_a_fonte_acompanha_o_corte(studio):
    p = plano(((0.0, 13.0), (30.0, 42.0)))
    p = S.aplicar_operacao(p, {"tipo": "adicionar_efeito", "som": "impacto", "fonte": FID, "t": 31.0, "dur": 1, "volume": 0.3})
    assert E.efeitos_saida(p)[0]["t_saida"] == pytest.approx(14.0)
    p["segmentos"].reverse()
    assert E.efeitos_saida(E.validar_plano(p))[0]["t_saida"] == pytest.approx(1.0)
    p["segmentos"] = [p["segmentos"][1]]
    assert E.efeitos_saida(E.validar_plano(p))[0]["t_saida"] is None


def test_render_mixa_o_efeito_na_posicao_certa(studio, midia):
    """Real (ffmpeg): fonte sem áudio + bipe preso à fonte em 4,0 s; o corte começa em 3,0 -> bipe em 1,0 s da saída."""
    import shutil
    shutil.copy(midia / "sem_audio.mp4", studio.UP / f"{FID}.mp4")
    p = E.plano_vazio()
    p["fontes"][FID] = {"nome": "s.mp4", "duracao": 6.0, "tem_audio": False}
    p["segmentos"] = [{"id": "s0", "fonte": FID, "ini": 3.0, "fim": 6.0}]
    p["audio"]["normalizar"] = False
    p["audio"]["efeitos"] = [{"id": "e1", "som": "bipe", "fonte": FID, "t": 4.0, "dur": 0.5, "volume": 0.8}]
    r = E.render_plano(tarefa(), p, "previa")
    pcm = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(E.CACHE / "previas" / r["arquivo"]), "-ac", "1",
                          "-ar", "8000", "-f", "f32le", "-"], capture_output=True).stdout
    a = np.frombuffer(pcm, np.float32)
    rms = lambda t0, t1: float(np.sqrt(np.mean(a[int(t0 * 8000):int(t1 * 8000)] ** 2)))  # noqa: E731
    assert rms(1.05, 1.4) > 0.1 and rms(0.1, 0.95) < 0.005 and rms(1.7, 2.8) < 0.005


def test_rota_som_de_efeito(studio):
    cli = studio.app.test_client()
    r = cli.get("/api/efeitos/som/impacto?dur=0.8")
    assert r.status_code == 200 and r.data[:4] == b"RIFF"
    assert cli.get("/api/efeitos/som/../../app").status_code == 404
    assert set(cli.get("/api/efeitos").get_json()) == set(E.SONS)


# ---------------------------------------------------------------- 5) pesquisa de referências (APIs simuladas)
def _yt(vid, views, pub, dur="PT45S", canal="C1"):
    return {"id": vid, "snippet": {"title": f"Vídeo {vid}", "channelTitle": "Canal", "channelId": canal, "publishedAt": pub,
                                   "defaultAudioLanguage": "pt-BR"},
            "statistics": {"viewCount": str(views), "likeCount": "10"}, "contentDetails": {"duration": dur}}


def test_comparar_caracteristicas_observadas_vira_hipotese_rotulada():
    def ref(i, views, carac):
        return {"id": i, "nome": i, "tipo": "youtube", "tags": {},
                "metadados": {"fonte": "YouTube", "views": views, "publicado_em": "2026-10-01T00:00:00Z",
                              "consultado_em": "2026-10-11T00:00:00Z"},
                "analises": {"gemini": {"caracteristicas": carac, "legenda_queimada": {"presente": carac["fala_nos_3s"]}}}}
    comp = R.comparar([ref("A", 90000, {"fala_nos_3s": True}), ref("B", 500, {"fala_nos_3s": False})])
    h = [x for x in comp["hipoteses"] if x.get("tipo") == "caracteristica_observada"]
    assert {x["metrica"] for x in h} == {"fala_nos_3s", "legenda_queimada"}
    assert all(x["evidencia"] == "muito fraca" and "pode errar" in x["por_que"] for x in h)


# ---------------------------------------------------------------- real: fala de verdade, transcrição só do corte
def test_real_transcricao_do_corte_exclui_o_outro_assunto(studio, tmp_path):
    pytest.importorskip("faster_whisper")
    a, b = tmp_path / "a.mp3", tmp_path / "b.mp3"
    try:
        A.gerar_voz("A cobra de fogo atravessa a mata quando a noite cai.", "pt-BR-AntonioNeural", "+0%", a)
        A.gerar_voz("Misture os ovos com a farinha e leve o bolo ao forno.", "pt-BR-FranciscaNeural", "+0%", b)
    except A.Erro as e:
        pytest.skip(f"edge-tts indisponível: {e}")
    da = A.probe(a)["duracao"]
    src = studio.UP / f"{FID}.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(a), "-i", str(b), "-filter_complex",
                    "[0:a]apad=pad_dur=2[x];[x][1:a]concat=n=2:v=0:a=1[o]", "-map", "[o]", str(src)], check=True)
    try:
        r = E.transcrever_trechos(tarefa(), FID, [[da + 1.5, A.probe(src)["duracao"]]])
    except A.Erro as e:
        pytest.skip(f"modelo indisponível: {e}")
    texto = " ".join(w[2] for w in E.transcricao_combinada(FID)["palavras"]).lower()
    assert r["palavras"] > 3 and "bolo" in texto and "cobra" not in texto and "mata" not in texto
    assert not E.transcricao_em_cache(FID)
