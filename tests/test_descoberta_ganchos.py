"""Descoberta por assunto e ranking explicável, reaproveitamento de trabalho (cache, pedido idêntico, provisório),
visual automático do gancho (texto + aparência), preferências e a pesquisa externa fora do fluxo."""
import json

import pytest

import app as A
import conteudo as C
import editor as E
import referencias as R
import sugestoes as S
import tarefas as T
from test_conteudo import FID, _fonte, _gemini, tarefa
from test_referencias import Rede


def plano(segs=((0.0, 40.0),)):
    p = E.plano_vazio()
    p["fontes"][FID] = {"nome": "f.mp4", "duracao": 120.0, "tem_audio": True}
    p["segmentos"] = [{"id": f"s{i}", "fonte": FID, "ini": a, "fim": b} for i, (a, b) in enumerate(segs)]
    return E.validar_plano(p)

# Três assuntos com vocabulário próprio, separados só por pausas de ~1,3 s (como numa fala real).
TEXTOS = [
    (0.0, "Você sabe por que ninguém entra na mata depois da meia-noite? Dizem que a cobra de fogo aparece entre as árvores. "
          "Meu avô viu a cobra de fogo quando era jovem. A mata inteira ficou vermelha naquela noite. Foi aí que ele descobriu "
          "o segredo da cobra. A cobra de fogo só aparece para quem entra sozinho na mata. Por isso os moradores atravessam a mata juntos."),
    (40.0, "Agora a receita do bolo de chocolate mais fácil. Misture três ovos com uma xícara de açúcar. Acrescente duas xícaras "
           "de farinha e o chocolate em pó. Coloque o leite morno e misture a massa do bolo de novo. Leve o bolo ao forno por "
           "quarenta minutos. O segredo é não abrir o forno antes de trinta minutos."),
    (80.0, "O jogo estava empatado e faltava um minuto para o fim. O time tinha perdido dois pênaltis no jogo. Foi aí que o goleiro "
           "subiu para a área no último escanteio. A bola bateu na trave e voltou para o zagueiro. O zagueiro chutou e marcou o gol "
           "da vitória. Foi a primeira vez em vinte anos que o time ganhou o campeonato."),
]


def palavras_de(textos=TEXTOS, passo=0.32):
    out = []
    for ini, txt in textos:
        t = ini
        for w in txt.split():
            out.append([round(t, 2), round(t + passo - 0.05, 2), w])
            t += passo + (0.45 if w[-1] in ".?!" else 0.0)
    return out


PAL3 = palavras_de()


def test_assuntos_naturais_sem_partes_iguais_e_com_ranking_explicado():
    frases = C.frases_de([tuple(w) for w in PAL3], FID)
    cortes = C.descobrir_local(frases, [], 120.0, 15, 40)
    assuntos = [(0, 40), (40, 80), (80, 120)]
    assert len(cortes) == 3
    for c in cortes:   # cada corte cai inteiro dentro de UM assunto e cobre quase todo ele
        dono = [a for a in assuntos if c["ini"] >= a[0] - 0.5 and c["fim"] <= a[1]]
        assert len(dono) == 1
        fala = [w for w in PAL3 if dono[0][0] <= w[0] < dono[0][1]]
        assert c["ini"] <= fala[0][0] + 0.1 and c["fim"] >= fala[-1][1] - 0.1
    durs = sorted(round(c["dur"]) for c in cortes)
    assert durs[0] != durs[-1]                                                   # duração segue o assunto, não é fixa
    por_assunto = {a: next(c for c in cortes if a[0] <= (c["ini"] + c["fim"]) / 2 < a[1]) for a in assuntos}
    cobra, bolo, jogo = (por_assunto[a] for a in assuntos)
    assert any(w in (cobra["titulo"] + cobra["ideia"]).lower() for w in ("cobra", "mata"))
    assert "bolo" in bolo["titulo"].lower() and bolo["titulo"].startswith("Passo a passo") and "etapas" in bolo["ideia"]
    assert any(w in (jogo["titulo"] + jogo["ideia"]).lower() for w in ("jogo", "gol", "time"))
    for c in cortes:
        assert "Trecho sobre" not in c["titulo"] and not c["ideia"].startswith("Fala de")
    for c in cortes:
        assert c["por_que"] and "views" not in c["por_que"].lower() and "%" not in c["por_que"]
    assert cortes[0]["acima_do_proximo"].startswith("Acima do #2") or "Empate" in cortes[0]["acima_do_proximo"]
    assert cortes[-1]["acima_do_proximo"] is None


def test_assunto_mais_curto_que_a_faixa_fica_inteiro_e_avisado():
    frases = C.frases_de([tuple(w) for w in PAL3], FID)
    cortes = C.descobrir_local(frases, [], 120.0, 60, 90)        # pede 60–90 s; cada assunto tem ~30 s e vocabulário próprio
    assert all(c["nota_duracao"] for c in cortes)
    assert any("abaixo dos 60s" in (c["nota_duracao"] or "") or "juntados" in (c["nota_duracao"] or "") for c in cortes)


def test_contexto_faltando_e_penalizado():
    pal = palavras_de([(0.0, "Mas isso não foi o pior. Ele voltou para a mata. A mata estava escura e fria. Ele voltou sozinho para casa.")])
    frases = C.frases_de([tuple(w) for w in pal], FID)
    crit, _ = C.avaliar(frases, frases)
    assert crit["abertura"][0] == 0 and "Mas" in crit["abertura"][1]
    assert any("contexto anterior" in x for x in C._limitacoes(frases))


def test_consolidacao_entre_blocos_remove_duplicado_e_ordena(studio, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    pal = palavras_de([(k * 4.0, f"Frase número {k} sobre o assunto {k // 100}.") for k in range(700)], passo=0.3)
    frases = C.frases_de([tuple(w) for w in pal], FID)
    assert len(frases) == 700
    pedidos = []

    def http(metodo, url, cab=None, corpo=None, timeout=60):
        texto = json.loads(corpo)["input"][0]["text"]
        pedidos.append(texto)
        if "Ordene TODOS" in texto:
            ids = [ln.split(":")[0] for ln in texto.split("Candidatos:\n")[1].splitlines() if ln.strip()]
            return 200, {}, json.dumps(_gemini(json.dumps({"ordem": list(reversed(ids)), "motivos": {ids[0]: "melhor fechamento"}}))[2]).encode()
        idx = [int(x) for x in __import__("re").findall(r"^\[(\d+)\]", texto, __import__("re").M)]
        a = idx[0] + (0 if idx[0] == 0 else 30)
        cortes = [{"posicao": 1, "titulo": f"Assunto do bloco {a}", "ideia_central": "ideia", "frase_ini": a, "frase_fim": a + 8,
                   "frase_trecho": a + 1, "por_que": "porque sim"}]
        if idx[0] > 0:   # o mesmo assunto da borda aparece nos dois blocos vizinhos (sobreposição)
            cortes.append({"posicao": 2, "titulo": "Duplicado da borda", "ideia_central": "x", "frase_ini": idx[0],
                           "frase_fim": idx[0] + 8, "frase_trecho": idx[0] + 1, "por_que": "x"})
        return 200, {}, json.dumps(_gemini(json.dumps({"cortes": cortes}))[2]).encode()
    monkeypatch.setattr(R, "_http", http)
    avisos = []
    cortes, desc, _ = C.descobrir_ia(tarefa(), frases, [], 2900.0, 20, 75, avisos)
    blocos = [p for p in pedidos if "Ordene TODOS" not in p]
    assert len(blocos) == 3 and sum("Ordene TODOS" in p for p in pedidos) == 1     # 700 frases: 3 blocos + 1 ordenação
    assert [c["rank"] for c in cortes] == list(range(1, len(cortes) + 1))
    inis = [c["frases"][0] for c in cortes]
    assert len(inis) == len(set(inis)) <= 5                                        # duplicados da borda removidos
    assert "melhor fechamento" in cortes[-1]["por_que"]                            # ordem final veio da consolidação


def _fonte_completa(studio, pal):
    (studio.UP / f"{FID}.mp4").write_bytes(b"conteudo-falso")
    E._arq_transcricao(FID).write_text(json.dumps({"versao": 1, "palavras": pal, "falas": [], "duracao": 120.0}), encoding="utf-8")
    E._gravar_json(E.CACHE / "transcricoes" / f"{E.hash_midia(A.achar(FID))}_cenas.json", {"cenas": [], "pausas": [], "duracao": 120.0})


def test_descoberta_repetida_reaproveita_e_nao_transcreve_de_novo(studio, monkeypatch):
    _fonte_completa(studio, PAL3)
    monkeypatch.setattr(E, "_whisper", lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deveria transcrever")))
    r1 = C.descobrir_cortes(tarefa(), FID, 15, 40, "local")
    r2 = C.descobrir_cortes(tarefa(), FID, 15, 40, "local")
    assert not r1["em_cache"] and r2["em_cache"] and [c["titulo"] for c in r1["cortes"]] == [c["titulo"] for c in r2["cortes"]]
    assert r1["tempos"]["total_s"] >= 0 and r1["transcricao_em_cache"]
    r3 = C.descobrir_cortes(tarefa(), FID, 20, 60, "local")                       # outra faixa: refaz só a análise
    assert not r3["em_cache"]
    # operações de corte reaproveitam a transcrição da fonte inteira (sem Whisper)
    res = C.sugerir_titulos(tarefa(), plano(((40.0, 80.0),)), "s0", "corte", "local", False, False, [])
    assert res["titulos"] and "cobra" not in json.dumps(res, ensure_ascii=False).lower()
    # mudar estilo/volume não toca na transcrição nem na descoberta
    p = plano(((40.0, 80.0),))
    p["legenda"]["estilo"]["tamanho"] = 120
    p["audio"]["volume_original"] = 0.4
    E.validar_plano(p)


def test_pedido_identico_em_andamento_reaproveita_a_tarefa():
    import threading
    pare = threading.Event()
    t1 = T.iniciar("x", "x", lambda t: pare.wait(5), chave="igual")
    t2 = T.iniciar("x", "x", lambda t: None, chave="igual")
    t3 = T.iniciar("x", "x", lambda t: None, chave="outra")
    assert t1 is t2 and t3 is not t1
    pare.set()


def test_sugestoes_provisorias_enquanto_transcreve(studio, monkeypatch):
    (studio.UP / f"{FID}.mp4").write_bytes(b"conteudo-falso")
    E._gravar_json(E.CACHE / "transcricoes" / f"{E.hash_midia(A.achar(FID))}_cenas.json", {"cenas": [], "pausas": [], "duracao": 120.0})
    vistos = []

    class Obs(T.Tarefa):
        def __setattr__(self, k, v):
            if k == "parcial" and v:
                vistos.append(v)
            super().__setattr__(k, v)

    def falso(tarefa, fid, ao_segmento=None):   # entrega as palavras aos poucos, como o Whisper
        for k in range(10, len(PAL3) + 1, 10):
            ao_segmento(PAL3[:k])
        E._arq_transcricao(fid).write_text(json.dumps({"versao": 1, "palavras": PAL3, "falas": [], "duracao": 120.0}), encoding="utf-8")
        return {"em_cache": False, "palavras": len(PAL3), "falas": 0, "duracao": 120.0}
    monkeypatch.setattr(E, "transcrever_fonte", falso)
    r = C.descobrir_cortes(Obs("t", "t"), FID, 15, 40, "local")
    assert vistos and all(v["provisorio"] for v in vistos)
    assert any(v["cortes"] for v in vistos)                                        # algo útil antes do fim
    assert all(c["fim"] <= v["ate_s"] + 0.5 for v in vistos for c in v["cortes"])   # só do que já foi transcrito
    assert r["cortes"] and r["provedor"] == "local"


# ---------------------------------------------------------------- gancho: texto + visual automático
def test_alternativas_visuais_do_gancho_por_conteudo(studio):
    _fonte_completa(studio, PAL3)
    res = C.analisar_ganchos(tarefa(), plano(((80.0, 120.0),)), "s0", "corte", "local", False, False)
    alts = res["alternativas_visuais"]
    assert len(alts) >= 3 and alts[0]["visual"]["estilo"] == "esporte" and "esporte" in alts[0]["motivo"]   # assunto de jogo
    for a in alts:
        v = a["visual"]
        assert {"estilo", "tamanho", "posicao_y", "destaque"} <= set(v) and 2.0 <= a["fim"] <= 4.5
        assert 0.1 <= v["posicao_y"] <= 0.7 and a["operacao"]["visual"] == v
    assert {a["rotulo"] for a in alts[:3]} == {"Esporte", "Impacto", "Limpo"}
    p = S.aplicar_operacao(plano(((80.0, 120.0),)), alts[0]["operacao"])
    t = p["titulos"][0]
    assert t["estilo"] == "esporte" and t["destaque"] == alts[0]["visual"]["destaque"]
    linha = E.dialogo_titulo(t)
    assert "\\fnImpact" in linha and "\\move(" in linha and "\\i1" in linha and t["texto"].split()[0].upper() in linha
    assert "cobra" not in json.dumps(res, ensure_ascii=False).lower()             # só o corte do jogo


def test_preferencia_salva_vira_meu_estilo_primeiro(studio):
    _fonte_completa(studio, PAL3)
    E.gravar_preferencias({"gancho": {"estilo": "limpo", "cor": "#00FF88", "animacao": "pop", "tamanho": 70, "posicao": "auto"}})
    assert E.ler_preferencias()["gancho"]["cor"] == "#00FF88"
    res = C.analisar_ganchos(tarefa(), plano(((0.0, 40.0),)), "s0", "corte", "local", False, False)
    meu = res["alternativas_visuais"][0]
    assert meu["rotulo"] == "Meu estilo" and meu["visual"]["cor"] == "#00FF88" and meu["visual"]["animacao"] == "pop"
    with pytest.raises(A.Erro):
        E.gravar_preferencias({"gancho": {"estilo": "neon"}})


def test_aparencia_invalida_do_texto_na_tela_e_recusada():
    p = plano()
    for ruim, msg in (({"cor": "vermelho"}, "#RRGGBB"), ({"animacao": "explodir"}, "nenhuma"), ({"fonte": "Arial{x}"}, "não permitido"),
                      ({"destaque": [99]}, "posições")):
        p["titulos"] = [{"id": "t", "texto": "Oi", "ini": 0, "fim": 2, **ruim}]
        with pytest.raises(A.Erro, match=msg):
            E.validar_plano(p)
    with pytest.raises(A.Erro, match="desconhecido"):
        S.aplicar_operacao(plano(), {"tipo": "adicionar_titulo", "texto": "x", "ini": 0, "fim": 2, "visual": {"script": "rm"}})


def test_miniatura_do_gancho_e_area_livre_reais(studio, midia):
    """Real (ffmpeg + libass): miniatura do corte com o gancho e a altura escolhida longe da legenda."""
    import shutil
    shutil.copy(midia / "com_audio.mp4", studio.UP / f"{FID}.mp4")
    p = E.plano_vazio()
    p["fontes"][FID] = {"nome": "c.mp4", "duracao": 8.0, "tem_audio": True}
    p["segmentos"] = [{"id": "s0", "fonte": FID, "ini": 1.0, "fim": 6.0}]
    p["legenda"]["frases"] = [{"id": "f", "fonte": FID, "palavras": [{"t": "oi", "ini": 1.2, "fim": 1.5}]}]
    nomes = {E.miniatura_titulo(p, {"texto": "Gol no fim", "estilo": e}, None, "s0") for e in ("esporte", "impacto", "limpo")}
    assert len(nomes) == 3 and all((E.CACHE / "miniaturas" / n).stat().st_size > 1000 for n in nomes)
    y, motivo = E.area_livre(p, "s0")
    assert abs(y - p["legenda"]["estilo"]["posicao_y"]) >= 0.14 and "legenda" in motivo


# ---------------------------------------------------------------- pesquisa externa fora do fluxo
def test_pesquisa_externa_fora_das_rotas_e_sem_chamadas(studio, monkeypatch):
    rotas = {r.rule for r in A.app.url_map.iter_rules()}
    assert not any(r.startswith(("/api/referencias", "/api/comparar", "/api/quadro")) for r in rotas) and "/api/config" in rotas
    chamadas = []
    monkeypatch.setattr(R, "_http", lambda *a, **k: chamadas.append(a) or (500, {}, b""))
    cli = studio.app.test_client()
    assert cli.get("/api/config").get_json().keys() == {"gemini", "gemini_modelo"}
    assert cli.get("/editor").status_code == 200 and chamadas == []
    html = (A.ROOT / "editor.html").read_text(encoding="utf-8")
    assert "pqBusca" not in html and "Referências" not in html and "YOUTUBE_API_KEY" not in html


def test_sem_sinal_forte_ainda_oferece_gancho_fraco_e_visual(studio):
    pal = palavras_de([(0.0, "A bola veio alta e o zagueiro cabeceou. O jogo seguiu no segundo tempo. O time voltou para o "
                             "vestiário depois do jogo.")])
    _fonte_completa(studio, pal)
    res = C.analisar_ganchos(tarefa(), plano(((0.0, 30.0),)), "s0", "corte", "local", False, False)
    falas = [g for g in res["ganchos"] if g["tipo"] == "fala"]
    assert falas and all(g.get("fraco") and "sinal fraco" in g["motivo"] for g in falas)
    assert len(res["alternativas_visuais"]) >= 3 and any("sinal forte" in a for a in res["avisos"])


# ---------------------------------------------------------------- precisão: unidades e perguntas
def test_frase_longa_sem_pontuacao_vira_unidades_nas_pausas():
    pal, t = [], 0.0
    for k in range(80):   # 80 palavras sem ponto final (~32 s), com uma pausa maior a cada 15 palavras
        pal.append([round(t, 2), round(t + 0.3, 2), f"palavra{k}" + ("," if k % 15 == 14 else "")])
        t += 0.4 + (0.5 if k % 15 == 14 else 0.0)
    us = C.unidades(pal)
    assert len(us) > 1 and all(u["fim"] - u["ini"] <= C.UNIDADE_MAX_S + 0.01 for u in us)
    assert sum(len(u["palavras"]) for u in us) == 80                                  # nenhuma palavra perdida ou partida
    for a, b in zip(us, us[1:]):
        assert b["palavras"][0][0] - a["palavras"][-1][1] >= 0.3                      # corte numa pausa, não no meio


def test_pergunta_de_apoio_nao_abre_assunto_nem_vira_titulo():
    assert C._pergunta_real("Como te selecionaram para o grupo?")
    for t in ("Como é que é?", "Ah é?", "Foi lá ontem, né?", "Isso já estava pronto, não é?"):
        assert not C._pergunta_real(t), t
    pal = palavras_de([(0.0, "Ah é? A cobra de fogo aparece na mata. A cobra só aparece para quem entra sozinho na mata. "
                             "Por isso ninguém entra sozinho na mata.")])
    fr = C.frases_de([tuple(w) for w in pal], FID)
    d = C.descrever_local(fr, fr)
    assert "Ah é" not in d["titulo"] and "cobra" in d["titulo"].lower()


def test_dois_assuntos_curtos_nao_sao_juntados():
    """Assunto mais curto que a faixa pedida fica inteiro e avisado; não é misturado com o vizinho."""
    pal = palavras_de([(0.0, "A cobra de fogo aparece na mata à noite. A cobra de fogo protege a mata dos caçadores. Ninguém entra "
                             "sozinho na mata escura. Meu avô viu a cobra de fogo uma vez na mata."),
                       (22.0, "Agora a receita do bolo de chocolate. Misture os ovos com o chocolate e a farinha. Leve o bolo ao forno "
                              "quente. O bolo de chocolate fica pronto em quarenta minutos.")])
    fr = C.frases_de([tuple(w) for w in pal], FID)
    cortes = C.descobrir_local(fr, [], pal[-1][1] + 1, 20, 75)
    assert len(cortes) == 2 and all(c["nota_duracao"] and "abaixo" in c["nota_duracao"] for c in cortes)
    assert all(not (c["ini"] < 15 and c["fim"] > 23) for c in cortes)
