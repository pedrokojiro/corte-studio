"""Referências: URLs, metadados oficiais (simulados), provedor Gemini (respostas simuladas e malformadas),
análise local real e comparação que não inventa números."""
import json
import subprocess

import pytest

import app as A
import editor as E
import referencias as R
import tarefas as T


class Rede:
    """Substitui R._http: responde por prefixo de URL e registra as chamadas."""
    def __init__(self, monkeypatch, rotas):
        self.rotas, self.chamadas = rotas, []
        monkeypatch.setattr(R, "_http", self)
        monkeypatch.setattr(R.time, "sleep", lambda s: None)

    def __call__(self, metodo, url, cab=None, corpo=None, timeout=60):
        self.chamadas.append((metodo, url, cab or {}))
        for prefixo, resp in self.rotas.items():
            if url.startswith(prefixo):
                r = resp.pop(0) if isinstance(resp, list) else resp
                if isinstance(r, Exception):
                    raise r
                st, cab_r, corpo_r = r
                return st, cab_r, corpo_r if isinstance(corpo_r, bytes) else json.dumps(corpo_r).encode()
        raise AssertionError(f"URL inesperada: {url}")


def tarefa():
    return T.Tarefa("t", "t")


# ---------------------------------------------------------------- URLs
@pytest.mark.parametrize("url, vid", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"), ("https://youtu.be/dQw4w9WgXcQ?t=3", "dQw4w9WgXcQ"),
    ("https://youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"), ("https://m.youtube.com/watch?v=dQw4w9WgXcQ&x=1", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?v=curto", None), ("https://evil.example/watch?v=dQw4w9WgXcQ", None),
])
def test_id_youtube(url, vid):
    assert R.id_youtube(url) == vid


def test_url_desconhecida_e_invalida(studio):
    for url in ("ftp://x", "nada", "https://vimeo.com/123"):
        with pytest.raises(A.Erro):
            R.nova_ref_url(url)


# ---------------------------------------------------------------- metadados
YT = {"items": [{"snippet": {"title": "Lenda do Boitatá", "channelTitle": "Canal X", "channelId": "UC1",
                             "publishedAt": "2026-09-01T12:00:00Z", "defaultAudioLanguage": "pt"},
                 "statistics": {"viewCount": "120000", "likeCount": "5000"}, "contentDetails": {"duration": "PT1M12S"}}]}


def test_youtube_sem_chave_registra_limite_sem_inventar(studio, monkeypatch):
    Rede(monkeypatch, {})
    ref = R.nova_ref_url("https://youtu.be/dQw4w9WgXcQ")
    assert ref["metadados"] is None and any("YOUTUBE_API_KEY" in a for a in ref["avisos"])
    assert R.desempenho(ref)["views"] is None and R.desempenho(ref)["views_por_dia"] is None


def test_youtube_com_chave_e_chave_fora_de_erro(studio, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "CHAVE-SECRETA-123")
    rede = Rede(monkeypatch, {"https://www.googleapis.com/youtube/v3/videos": (200, {}, YT),
                              "https://www.googleapis.com/youtube/v3/channels": (200, {}, {"items": [{"statistics": {"subscriberCount": "8000"}}]})})
    ref = R.nova_ref_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    m = ref["metadados"]
    assert (m["views"], m["duracao_s"], m["inscritos"], m["fonte"]) == (120000, 72, 8000, "YouTube Data API v3 (videos.list)")
    assert ref["acesso"]["audiovisual"].startswith("somente pelo provedor")
    # erro de cota: mensagem sem a chave
    monkeypatch.setattr(R, "_http", lambda *a, **k: (403, {}, b'{"error":"quotaExceeded key=CHAVE-SECRETA-123"}'))
    ref2 = R.nova_ref_url("https://youtu.be/dQw4w9WgXcQ")
    assert ref2["metadados"] is None and "CHAVE-SECRETA-123" not in json.dumps(ref2) and "403" in ref2["avisos"][0]
    assert all("CHAVE-SECRETA-123" not in c[1] or "key=" in c[1] for c in rede.chamadas)


def test_tentativas_limitadas_so_em_erro_temporario(studio, monkeypatch):
    rede = Rede(monkeypatch, {"https://x.example/": [(503, {}, {}), (429, {}, {}), (200, {}, {"ok": 1})]})
    assert R.requisitar_json("GET", "https://x.example/a") == {"ok": 1} and len(rede.chamadas) == 3
    rede = Rede(monkeypatch, {"https://x.example/": [(500, {}, {})] * 5})
    with pytest.raises(R.ErroHTTP, match="HTTP 500"):
        R.requisitar_json("GET", "https://x.example/a")
    assert len(rede.chamadas) == 3                    # para no limite
    rede = Rede(monkeypatch, {"https://x.example/": [(404, {}, {})] * 5})
    with pytest.raises(R.ErroHTTP, match="404"):
        R.requisitar_json("GET", "https://x.example/a")
    assert len(rede.chamadas) == 1                    # erro definitivo: não insiste


def test_tiktok_oembed_sem_views(studio, monkeypatch):
    Rede(monkeypatch, {"https://www.tiktok.com/oembed": (200, {}, {"title": "Lenda", "author_name": "@perfil",
                                                                   "thumbnail_url": "https://p.x/a.jpg"})})
    ref = R.nova_ref_url("https://www.tiktok.com/@perfil/video/123")
    assert ref["tipo"] == "tiktok" and ref["metadados"]["views"] is None
    assert ref["acesso"]["audiovisual"] == "indisponível por URL" and any("não expõe views" in a for a in ref["avisos"])


# ---------------------------------------------------------------- provedor Gemini: respostas simuladas
def _interacao(texto):
    return {"id": "v1", "status": "completed", "steps": [{"type": "model_output", "content": [{"type": "text", "text": texto}]}]}


BOA = json.dumps({"resumo": "Abre com pergunta.",
                  "observacoes": [{"t": "00:01", "categoria": "gancho", "texto": "Pergunta direta ao público."},
                                  {"t": "00:12.5", "categoria": "legenda", "texto": "Legenda amarela no centro."},
                                  {"t": "09:99", "categoria": "ritmo", "texto": "tempo inválido"},
                                  {"t": "00:30", "categoria": "viralidade", "texto": "vai viralizar"},
                                  {"t": "05:00", "categoria": "final", "texto": "depois do fim do vídeo"}],
                  "legenda_queimada": {"presente": True, "descricao": "amarela"},
                  "promessa_e_entrega": {"promessa": "contar o caso", "t_promessa": "00:02", "entrega": "sim", "t_entrega": "00:50"}})


def test_gemini_sem_chave_e_explicito(studio, monkeypatch):
    Rede(monkeypatch, {})
    ref = R.nova_ref_url("https://youtu.be/dQw4w9WgXcQ")
    with pytest.raises(A.Erro, match="GEMINI_API_KEY"):
        R.analisar_ref(tarefa(), ref["id"], "gemini")


def test_gemini_youtube_valida_e_descarta_o_que_nao_presta(studio, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "G-SECRETA")
    rede = Rede(monkeypatch, {"https://generativelanguage.googleapis.com/v1beta/interactions": (200, {}, _interacao("```json\n" + BOA + "\n```"))})
    ref = R.nova_ref_url("https://youtu.be/dQw4w9WgXcQ")
    ref["metadados"] = {"duracao_s": 60}
    R.gravar_ref(ref)
    R.analisar_ref(tarefa(), ref["id"], "gemini")
    an = R.ler_ref(ref["id"])["analises"]["gemini"]
    assert [(o["t"], o["categoria"]) for o in an["observacoes"]] == [(1.0, "gancho"), (12.5, "legenda")]
    assert any("3 observação" in l for l in an["limitacoes"]) and an["legenda_queimada"]["presente"] is True
    # a chave vai no cabeçalho, nunca na URL
    assert "G-SECRETA" not in rede.chamadas[-1][1] and rede.chamadas[-1][2]["x-goog-api-key"] == "G-SECRETA"


@pytest.mark.parametrize("resposta, msg", [
    ("isto não é JSON", "não é JSON"),
    (json.dumps({"resumo": "x"}), "observacoes"),
    (json.dumps({"observacoes": [{"t": "99:99", "categoria": "gancho", "texto": "x"}]}), "Nenhuma observação válida"),
    (json.dumps({"observacoes": [{"t": "00:01", "categoria": "rm -rf", "texto": "x"}]}), "Nenhuma observação válida"),
    ("", "vazia"),
])
def test_gemini_resposta_malformada_nao_vira_resultado(studio, monkeypatch, resposta, msg):
    monkeypatch.setenv("GEMINI_API_KEY", "G")
    Rede(monkeypatch, {"https://generativelanguage.googleapis.com/v1beta/interactions": (200, {}, _interacao(resposta))})
    ref = R.nova_ref_url("https://youtu.be/dQw4w9WgXcQ")
    with pytest.raises(A.Erro, match=msg):
        R.analisar_ref(tarefa(), ref["id"], "gemini")
    assert R.ler_ref(ref["id"])["analises"] == {}


def test_gemini_arquivo_exige_consentimento_e_tiktok_recusa(studio, monkeypatch, enviar, midia):
    monkeypatch.setenv("GEMINI_API_KEY", "G")
    rede = Rede(monkeypatch, {"https://www.tiktok.com/oembed": (200, {}, {"title": "t"})})
    ref = R.nova_ref_arquivo(enviar(midia / "com_audio.mp4"), "ref")
    with pytest.raises(A.Erro, match="ENVIADO ao Google"):
        R.analisar_ref(tarefa(), ref["id"], "gemini")
    assert not [c for c in rede.chamadas if "googleapis" in c[1]]   # nada saiu sem consentimento
    tk = R.nova_ref_url("https://www.tiktok.com/@a/video/1")
    with pytest.raises(A.Erro, match="TikTok"):
        R.analisar_ref(tarefa(), tk["id"], "gemini")


def test_local_recusa_url_sem_arquivo(studio, monkeypatch):
    Rede(monkeypatch, {})
    ref = R.nova_ref_url("https://youtu.be/dQw4w9WgXcQ")
    with pytest.raises(A.Erro, match="precisa do arquivo"):
        R.analisar_ref(tarefa(), ref["id"], "local")


# ---------------------------------------------------------------- análise local de verdade
@pytest.fixture(scope="module")
def video_ref(tmp_path_factory):
    d = tmp_path_factory.mktemp("ref")
    fala = d / "fala.mp3"
    try:
        A.gerar_voz("Você sabia que ninguém volta da mata? Ninguém volta da mata. E foi assim que tudo começou.",
                    "pt-BR-AntonioNeural", "+0%", fala)
    except A.Erro as e:
        pytest.skip(f"edge-tts indisponível: {e}")
    dur = A.probe(fala)["duracao"] + 1.0
    v = d / "ref.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"color=c=red:s=360x640:d={dur / 3:.2f}:r=30",
                    "-f", "lavfi", "-i", f"color=c=blue:s=360x640:d={dur / 3:.2f}:r=30",
                    "-f", "lavfi", "-i", f"color=c=green:s=360x640:d={dur / 3:.2f}:r=30", "-i", str(fala),
                    "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v];[3:a]adelay=1000|1000,apad[a]",
                    "-map", "[v]", "-map", "[a]", "-t", f"{dur:.2f}", "-c:v", "libx264", "-c:a", "aac", str(v)], check=True)
    return v


def test_analise_local_gera_observacoes_com_tempo(studio, enviar, video_ref):
    pytest.importorskip("faster_whisper")
    ref = R.nova_ref_arquivo(enviar(video_ref), "ref sintética")
    t = tarefa()
    R.analisar_ref(t, ref["id"], "local")
    an = R.ler_ref(ref["id"])["analises"]["local"]
    m = an["metricas"]
    # a fala começa ~1,1 s; o Whisper costuma marcar a 1a palavra algumas centenas de ms antes
    # (erro de reconhecimento, separado da linha do tempo; medido em tests/medir_sync.py)
    assert m["vertical"] and m["trocas_de_cena"] == 2 and 0.5 <= m["primeira_fala_s"] <= 1.5
    cats = {o["categoria"] for o in an["observacoes"]}
    assert {"gancho", "ritmo", "enquadramento", "audio"} <= cats
    assert any(o["categoria"] == "repeticao" and "volta da mata" in o["texto"].lower() for o in an["observacoes"])
    assert all(isinstance(o["t"], float) for o in an["observacoes"]) and len(an["quadros"]) == 3
    assert any("NÃO são detectados" in l for l in an["limitacoes"])
    # cache por conteúdo: a 2a análise não reprocessa
    R.analisar_ref(tarefa(), ref["id"], "local")
    assert R.ler_ref(ref["id"])["analises"]["local"]["em_cache"] is True
    cli = studio.app.test_client()
    assert cli.get(an["quadros"][0]["url"]).status_code == 200 and cli.get("/api/quadro/x/../../app.py").status_code == 404


# ---------------------------------------------------------------- comparação
def _ref(nome, views, pub, met, inscritos=None, fonte="YouTube Data API v3 (videos.list)"):
    return {"id": nome, "nome": nome, "tipo": "youtube", "tags": {},
            "metadados": {"fonte": fonte, "views": views, "publicado_em": pub, "consultado_em": "2026-10-11T00:00:00Z",
                          "inscritos": inscritos},
            "analises": {"local": {"metricas": met}}}


def test_uma_referencia_e_rotulada_e_sem_hipotese():
    c = R.comparar([_ref("A", 1000, "2026-10-01T00:00:00Z", {"primeira_fala_s": 0.4})])
    assert not c["hipoteses"] and any("UMA referência" in n for n in c["notas"])


def test_hipotese_so_com_separacao_e_rotulo_de_evidencia():
    refs = [_ref("A", 100000, "2026-10-01T00:00:00Z", {"primeira_fala_s": 0.3, "cortes_por_min": 20, "duracao_s": 60}, 5000),
            _ref("B", 80000, "2026-10-01T00:00:00Z", {"primeira_fala_s": 0.5, "cortes_por_min": 8, "duracao_s": 70}, 6000),
            _ref("C", 2000, "2026-10-01T00:00:00Z", {"primeira_fala_s": 2.5, "cortes_por_min": 12, "duracao_s": 65}, 5500),
            _ref("D", 1000, "2026-10-01T00:00:00Z", {"primeira_fala_s": 3.0, "cortes_por_min": 30, "duracao_s": 62}, 4000)]
    c = R.comparar(refs)
    assert [h["metrica"] for h in c["hipoteses"]] == ["primeira_fala_s"]       # cortes/min se sobrepõe: sem hipótese
    h = c["hipoteses"][0]
    assert h["direcao"] == "menor" and h["evidencia"] == "fraca" and "Correlação" in h["por_que"]
    assert any("distribuiu" in n for n in c["notas"])
    assert all(isinstance(l["desempenho"]["views_por_dia"], float) for l in c["referencias"])
    assert "previs" not in json.dumps(c).lower() and "score" not in json.dumps(c).lower()


def test_exposicao_e_publico_diferentes_sao_avisados():
    refs = [_ref("A", 100000, "2024-01-01T00:00:00Z", {"primeira_fala_s": 0.3}, 1_000_000),
            _ref("B", 1000, "2026-10-05T00:00:00Z", {"primeira_fala_s": 2.0}, 2000)]
    notas = " ".join(R.comparar(refs)["notas"])
    assert "Tempo no ar muito diferente" in notas and "Tamanho de canal muito diferente" in notas


def test_metrica_do_usuario_e_rotulada(studio, monkeypatch, enviar, midia):
    ref = R.nova_ref_arquivo(enviar(midia / "com_audio.mp4"), "meu arquivo")
    cli = studio.app.test_client()
    r = cli.put(f"/api/referencias/{ref['id']}", json={"metricas_usuario": {"views": 5000, "publicado_em": "2026-10-01"}})
    d = R.desempenho(r.get_json())
    assert d["views"] == 5000 and d["origem_views"] == "informado por você" and d["origem_publicacao"] == "informado por você"
    assert cli.put(f"/api/referencias/{ref['id']}", json={"metricas_usuario": {"views": "muitas"}}).status_code == 400
