"""Entender o conteúdo do corte e transformar isso em sugestões EDITÁVEIS (cortes, textos, ganchos, áudio).

Dois métodos, sempre rotulados no resultado (`provedor`):
- "gemini": modelo de IA. Recebe o TEXTO da transcrição do escopo pedido (e quadros do corte só com consentimento
  explícito). Tudo o que aponta para a fala referencia frases por ÍNDICE: o texto citado sai da transcrição, não do
  modelo, então nenhuma fala é inventada e nada fora do escopo entra. Resposta fora do formato é descartada.
- "local": heurística sem IA (coesão de vocabulário, pontuação, pausas, cenas, marcadores de pergunta/conflito/
  revelação). Não entende o conteúdo; é o plano B quando não há provedor, e a interface diz isso.

Escopo: "corte" = só o segmento ativo; "linha" = todos os segmentos incluídos, na ordem de saída. A transcrição
necessária é feita automaticamente, só desses trechos (cache reaproveitado). Descobrir cortes no vídeo inteiro é
outra coisa: transcreve a fonte toda, mas não gera legenda.
"""
import collections
import json
import math
import re
import time
import uuid

from flask import Blueprint, jsonify

import app as A
import editor as E
import referencias as R
import sugestoes as S
import tarefas as T

bp = Blueprint("conteudo", __name__)

STOP = set("""a o as os um uma uns umas de do da dos das no na nos nas em por para pra pro pelo pela pelos pelas com sem
que se e ou mas nem como quando onde quem qual quais porque pois entao então ja já nao não sim tambem também mais menos
muito muita muitos muitas pouco tudo nada isso isto aquilo esse essa esses essas este esta estes estas aquele aquela ele
ela eles elas eu tu voce você voces vocês nos nós me te lhe lhes meu minha meus minhas seu sua seus suas nosso nossa ser
foi era sao são é estar esta está estava ter tem tinha ha há vai vou fazer fez faz ai aí aqui la lá so só bem tipo ne né
cara mano entao assim agora depois antes ainda até ate sobre entre cada outro outra outros outras mesmo mesma coisa
coisas gente dele dela deles delas num numa pra pras pros fui vamos vai iam ia sera será seria pode podia tá ta tô to
""".split())
CONECTIVOS = ("e", "mas", "entao", "ai", "dai", "porque", "pois", "so que", "por isso", "alem disso", "tambem", "ou seja")
ANAFORAS = ("isso", "isto", "ele", "ela", "eles", "elas", "esse", "essa", "aquilo", "dele", "dela", "la", "ali")
REVELA = ("descobri", "descobriu", "descobrimos", "na verdade", "a verdade", "o segredo", "ninguem sabia", "acontece que",
          "foi ai que", "percebi", "percebeu", "revelou", "confessou", "o que ninguem", "so depois", "no fim", "era ele",
          "era ela", "nunca mais")
CONFLITO = ("mas", "so que", "porem", "contra", "briga", "brigou", "problema", "erro", "nunca", "proibido", "perigo",
            "medo", "ameaca", "culpa", "mentira", "mentiu", "morreu", "morte", "sumiu", "desapareceu", "grito")
FORTE = ("o maior", "o pior", "o mais", "unico", "unica", "primeira vez", "ultima vez", "todo mundo", "ninguem",
         "jamais", "impossivel")


def mmss(t):
    return f"{int(t // 60)}:{t % 60:04.1f}"


def _raiz(w):
    n = A._norm(w)
    if len(n) > 4 and n.endswith("s"):
        n = n[:-1]
    return n[:6]


def raizes(texto):
    return [_raiz(w) for w in texto.split() if A._norm(w) not in STOP and len(A._norm(w)) >= 3]


def _norm_txt(texto):
    return " " + " ".join(A._norm(w) for w in texto.split()) + " "


def _tem(txt_norm, marcadores):
    return [m for m in marcadores if f" {m} " in txt_norm]


def _cos(a, b):
    if not a or not b:
        return 0.0
    num = sum(a[k] * b.get(k, 0) for k in a)
    return num / math.sqrt(sum(v * v for v in a.values()) * sum(v * v for v in b.values()))


# ---------------------------------------------------------------- escopo e frases
def segs_escopo(plano, seg_id=None, escopo="corte"):
    m = E.mapa(plano)
    if not m:
        raise A.Erro("Adicione pelo menos um corte à linha do tempo.")
    if escopo == "linha":
        return m
    s = next((x for x in m if x["id"] == seg_id), None)
    if seg_id and not s:
        raise A.Erro("O corte selecionado não está mais na linha do tempo.")
    return [s or m[0]]


UNIDADE_MAX_S = 10.0   # fala coloquial quase não tem ponto final na transcrição: “frase” de 30–90 s é comum


def _dividir_longa(s):
    """Quebra uma frase longa em unidades de até ~10 s nas melhores emendas internas: pausa e/ou vírgula
    (nunca no meio de palavra). Sem emenda boa, a frase fica inteira."""
    ws = s["palavras"]
    if ws[-1][1] - ws[0][0] <= UNIDADE_MAX_S or len(ws) < 6:
        return [s]

    def nota(k):   # emenda entre ws[k-1] e ws[k]
        pausa = ws[k][0] - ws[k - 1][1]
        return pausa + (0.4 if ws[k - 1][2][-1:] in ",;:" else 0.0)
    ops = [k for k in range(2, len(ws) - 1) if nota(k) >= 0.3]
    if not ops:
        return [s]
    meio = (ws[0][0] + ws[-1][1]) / 2   # a melhor emenda perto do meio; repete em cada metade
    k = max(ops, key=lambda k: nota(k) - abs(ws[k][0] - meio) / 20)
    partes = []
    for p in (ws[:k], ws[k:]):
        partes += _dividir_longa({"ini": p[0][0], "fim": p[-1][1], "texto": " ".join(w[2] for w in p), "palavras": p})
    return partes


def unidades(palavras):
    """Frases da transcrição, com as longas divididas em unidades (base das fronteiras de assunto)."""
    return [u for s in S.sentencas(palavras) for u in _dividir_longa(s)]


def frases_de(palavras, fonte, seg=None, out_ini=None, base=0):
    out = []
    for k, s in enumerate(unidades(palavras)):
        out.append({"i": base + k, "fonte": fonte, "seg": seg["id"] if seg else None, "ini": s["ini"], "fim": s["fim"],
                    "out": (out_ini + s["ini"] - seg["ini"]) if seg else None, "texto": s["texto"],
                    "raizes": raizes(s["texto"]), "palavras": s["palavras"]})
    return out


def frases_escopo(plano, segs):
    out = []
    for s in segs:
        pal = E.palavras_no_intervalo(s["fonte"], s["ini"], s["fim"])
        out += frases_de(pal, s["fonte"], s, s["out_ini"], len(out))
    return out


def _texto_indexado(frases, pausas=None):
    linhas = []
    for f in frases:
        linhas.append(f"[{f['i']}] {mmss(f['ini'])}–{mmss(f['fim'])} {f['texto']}")
    return "\n".join(linhas)


def _resumo_escopo(plano, segs):
    return [{"id": s["id"], "fonte": s["fonte"], "nome": plano["fontes"][s["fonte"]]["nome"], "ini": s["ini"],
             "fim": s["fim"], "out_ini": s["out_ini"], "dur": round(s["fim"] - s["ini"], 2)} for s in segs]


def preparar(tarefa, plano, segs, peso=0.4):
    """Pré-requisito automático: transcrição só dos trechos do escopo (com cache)."""
    tem_audio = [s for s in segs if plano["fontes"][s["fonte"]]["tem_audio"]]
    if not tem_audio:
        raise A.Erro("O corte não tem áudio: sem fala para analisar.")
    tr = E.transcrever_plano(T.Faixa(tarefa, 0.0, peso, "fala: "), plano, segs)
    fr = frases_escopo(plano, segs)
    return tr, fr


def quadros_escopo(plano, segs, n=6):
    """Quadros JPEG pequenos, espalhados pelo escopo (só são enviados com consentimento)."""
    out = []
    total = sum(s["fim"] - s["ini"] for s in segs)
    for k in range(n):
        alvo, acum = total * (k + 0.5) / n, 0.0
        for s in segs:
            if acum + (s["fim"] - s["ini"]) >= alvo:
                t = s["ini"] + (alvo - acum)
                r = A.executar(["ffmpeg", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(A.achar(s["fonte"])),
                                "-frames:v", "1", "-vf", "scale=-2:360", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                               timeout=60, texto=False)
                if r.returncode == 0 and r.stdout:
                    out.append({"k": len(out), "fonte": s["fonte"], "seg": s["id"], "t": round(t, 2), "jpg": r.stdout})
                break
            acum += s["fim"] - s["ini"]
    return out


# ---------------------------------------------------------------- provedor: Gemini com plano B local
def _quer_ia(provedor):
    if provedor == "local":
        return False
    return provedor == "gemini" or R.status_config()["gemini"]


def com_ia(tarefa, provedor, fn_ia, fn_local, avisos):
    """Roda a análise por modelo; se o provedor não existe ou falha, cai na heurística local e AVISA."""
    if _quer_ia(provedor):
        try:
            return fn_ia()
        except R.SemProvedor as e:
            avisos.append(f"{e} Mostrando a heurística local (sem IA).")
        except A.Erro as e:
            avisos.append(f"O Gemini falhou ({e}). Mostrando a heurística local (sem IA); nada da resposta foi usado.")
    return fn_local()


METODO_LOCAL = ("Heurística local, SEM IA: pontuação, pausas, trocas de cena, vocabulário repetido e marcadores de "
                "pergunta/conflito/revelação. Não entende o conteúdo: confira cada sugestão.")


def _idx(v, frases, campo):
    validos = {f["i"] for f in frases}
    if isinstance(v, bool) or not isinstance(v, int) or v not in validos:
        raise ValueError(f"{campo}: índice de frase inválido {v!r}")
    return v


def _str(v, campo, maximo, vazio=False):
    if not isinstance(v, str) or (not v.strip() and not vazio):
        raise ValueError(f"{campo}: texto ausente")
    return v.strip()[:maximo]


def _lista(j, chave):
    if not isinstance(j, dict) or not isinstance(j.get(chave), list):
        raise A.Erro(f"A resposta do provedor não tem a lista '{chave}'; nada foi usado.", 502)
    return j[chave]


def termos_sem_apoio(texto, frases):
    """Palavras de conteúdo do texto sugerido que NÃO aparecem na fala do escopo (sinal de invenção)."""
    base = {r for f in frases for r in f["raizes"]}
    return [w for w in texto.split() if A._norm(w) not in STOP and len(A._norm(w)) >= 4 and _raiz(w) not in base]


# ---------------------------------------------------------------- sinais locais (pergunta, conflito, revelação, entrega)
def sinais(f, frases):
    """Categoria, pontos e motivos de UMA frase como gancho + checagem de entrega dentro do escopo."""
    txt = _norm_txt(f["texto"])
    n_pal = len(f["texto"].split())
    motivos, pontos, cat = [], 0.0, "afirmacao"
    if f["texto"].rstrip().endswith("?"):
        pontos, cat = pontos + 2, "pergunta"
        motivos.append("é uma pergunta")
    rev = _tem(txt, REVELA)
    if rev:
        pontos += 2
        cat = "revelacao" if cat == "afirmacao" else cat
        motivos.append("marca de revelação (“" + rev[0] + "”)")
    conf = _tem(txt, CONFLITO)
    if conf:
        pontos += 1.5
        cat = "conflito" if cat == "afirmacao" else cat
        motivos.append("marca de conflito/tensão (“" + ", ".join(conf[:2]) + "”)")
    forte = _tem(txt, FORTE)
    if forte or re.search(r"\d", f["texto"]):
        pontos += 1
        motivos.append("afirmação forte ou número concreto")
    if n_pal < 4:
        pontos -= 2
        motivos.append("curta demais para se sustentar sozinha")
    elif n_pal > 28:
        pontos -= 1
        motivos.append(f"longa ({n_pal} palavras)")
    primeira = A._norm(f["texto"].split()[0]) if f["texto"].split() else ""
    if primeira in CONECTIVOS or primeira in ANAFORAS:
        pontos -= 1
        motivos.append(f"começa com “{f['texto'].split()[0]}”: pode depender do que veio antes")
    # entrega: o resto do escopo retoma o assunto?
    depois = [g for g in frases if g["i"] > f["i"]][:10]
    alvo = set(f["raizes"])
    precisa = 1 if len(alvo) <= 3 else 2
    afirmativas = [g for g in depois if not g["texto"].rstrip().endswith("?")]
    melhor = max(afirmativas, key=lambda g: len(alvo & set(g["raizes"])), default=None)
    comum = sorted(alvo & set(melhor["raizes"])) if melhor else []
    if not depois:
        entrega = {"status": "nao_entrega", "frase": None,
                   "explicacao": "É a última frase do escopo: nada depois dela desenvolve ou responde."}
        pontos -= 1
    elif len(comum) >= precisa:
        entrega = {"status": "provavel", "frase": melhor["i"],
                   "explicacao": f"Termos retomados em {mmss(melhor['ini'])}: “{melhor['texto'][:90]}” "
                                 "(sobreposição de vocabulário, não compreensão)."}
        pontos += 1
    elif cat == "revelacao" and afirmativas:
        entrega = {"status": "parcial", "frase": afirmativas[0]["i"],
                   "explicacao": f"Logo depois vem: “{afirmativas[0]['texto'][:90]}”. Confira se é a revelação prometida."}
    elif comum:
        entrega = {"status": "parcial", "frase": melhor["i"],
                   "explicacao": f"O assunto (“{comum[0]}…”) volta em {mmss(melhor['ini'])}: “{melhor['texto'][:90]}”. "
                                 "Confira se isso responde ao que o gancho promete."}
    else:
        entrega = {"status": "incerta", "frase": None,
                   "explicacao": "Nenhuma frase seguinte retoma os mesmos termos; confira se o corte entrega o que promete."}
    return {"categoria": cat, "pontos": pontos, "motivos": motivos, "entrega": entrega}


# ---------------------------------------------------------------- 7) cortes com conteúdo (fonte inteira)
VERSAO_DESCOBERTA = 3   # muda quando o método muda: invalida o cache de descobertas (v3: unidades, janela 50 s)
MUDA_ASSUNTO = ("agora", "outro ponto", "outra coisa", "outra dica", "falando em", "mudando de assunto", "na semana passada",
                "certa vez", "uma vez", "por que", "voce sabe", "sabe por que", "vamos a", "vamos falar", "o jogo", "a historia")
IMPERATIVOS = {"misture", "coloque", "leve", "acrescente", "adicione", "tire", "use", "faca", "pegue", "corte", "deixe",
               "evite", "troque", "desligue", "encurte", "reduza", "mexa", "bata", "asse", "separe", "lave", "ligue", "abra",
               "feche", "experimente", "comece", "despeje", "aqueca", "junte", "retire", "sirva", "anote", "guarde"}
PASSADO = {"foi", "era", "estava", "tinha", "contou", "disse", "fez", "viu", "ficou", "caiu", "chegou", "voltou", "apareceu"}
EXPLICA = ("porque", "por isso", "por que", "e formada", "acontece", "significa", "isso faz", "o motivo", "a razao")
ARGUMENTA = ("acha que", "na verdade", "faz sim", "a dica", "o maior", "vilao", "ou seja", "nao faz", "vale a pena", "o certo")
CONCLUI = ("por isso", "entao a", "ate hoje", "no fim", "no final", "o segredo", "foi a primeira vez", "resultado", "a dica",
           "ou seja", "finalmente", "por fim", "quando a luz voltou", "fez uma festa", "so o")
CRITERIOS = (("abertura", "Abertura"), ("interesse", "Interesse"), ("clareza", "Clareza"), ("autonomia", "Funciona sozinho"),
             ("ritmo", "Ritmo"), ("fechamento", "Fechamento"))


def _cenas_cache(fid):
    arq = E.CACHE / "transcricoes" / f"{E.hash_midia(A.achar(fid))}_cenas.json"
    if arq.exists():
        return json.loads(arq.read_text(encoding="utf-8"))
    src = A.achar(fid)
    info = A.probe(src)
    cenas, pausas = A.detectar(src, info["tem_audio"], duracao=info["duracao"])
    d = {"cenas": cenas, "pausas": pausas, "duracao": info["duracao"]}
    E._gravar_json(arq, d)
    return d


def refinar_bordas(ini_fala, fim_fala, frases, k_ini, k_fim, cenas, dur):
    """Bordas a partir das frases (conteúdo) e só AJUSTADAS por pausa e troca de cena."""
    ant = frases[k_ini - 1]["fim"] if k_ini > 0 else 0.0
    prox = frases[k_fim + 1]["ini"] if k_fim + 1 < len(frases) else dur
    ini = max(ant + 0.05, ini_fala - 0.25, 0.0)
    fim = min(prox - 0.05, fim_fala + 0.35, dur)
    notas = []
    c = [x for x in cenas if max(ant, ini_fala - 0.7) <= x <= ini_fala]
    if c:
        ini = c[-1]
        notas.append(f"início alinhado à troca de cena em {mmss(ini)}")
    c = [x for x in cenas if fim_fala <= x <= min(prox, fim_fala + 0.7)]
    if c:
        fim = c[0]
        notas.append(f"fim alinhado à troca de cena em {mmss(fim)}")
    return round(max(0.0, ini), 2), round(min(dur, max(fim, ini + 0.5)), 2), notas


CONVERSA = set("""gente ne ta to aqui ai cara rapaziada galera mano tipo assim entao bom olha ve ver vai vou vamos fica pode
consegue conseguir consegui tem ter tinha teve faz fazer fala falar falou coisa coisas negocio jeito vez vezes hoje agora acho
acredito sei sabe sabia claro certo verdade exatamente inclusive enfim basicamente realmente simplesmente sempre nunca parece
dizer disse digo quer queria olhar""".split())
VERBAL = re.compile(r"(ou|eu|iu|ava|avam|ia|iam|ar|er|ir|ando|endo|indo|ado|ido|amos|emos|aram|eram)$")


def _bom_para_titulo(w):
    n = A._norm(w)
    return len(n) >= 4 and n not in STOP and n not in CONVERSA and not VERBAL.search(n)


def _palavras_chave(frases_sel, todas, n=3):
    df = collections.Counter(r for f in todas for r in set(f["raizes"]))
    tf = collections.Counter(r for f in frases_sel for r in f["raizes"])
    sup = collections.defaultdict(collections.Counter)
    for f in frases_sel:
        for w in f["texto"].split():
            if A._norm(w) not in STOP and len(A._norm(w)) >= 3:
                sup[_raiz(w)][re.sub(r"[^\wÀ-ú-]", "", w.lower())] += 1
    nt = max(1, len(todas))
    total = collections.Counter(r for f in todas for r in f["raizes"])
    # concentração: termo espalhado pela fonte inteira (ex.: palavra que o reconhecimento erra em todo lugar) não
    # representa ESTE trecho; precisa aparecer aqui pelo menos 40% das vezes em que aparece na fonte
    conc = {r: c / max(1, total[r]) for r, c in tf.items()}
    pont = {r: c * math.log(1 + nt / (1 + df[r])) * conc[r] ** 2 for r, c in tf.items() if conc[r] >= 0.4 or len(todas) == len(frases_sel)}
    ordem = sorted(pont, key=lambda r: -pont[r])
    bons = [r for r in ordem if sup[r] and _bom_para_titulo(sup[r].most_common(1)[0][0])]
    top = (bons + [r for r in ordem if r not in bons])[:n]   # termos “de assunto” primeiro; verbos/muletas só se faltar
    return [sup[r].most_common(1)[0][0] for r in top if sup[r]]


def _expressoes(fs, n=2, todas=None):
    """Expressões repetidas de 2–3 palavras (ex.: “cobra de fogo”, “conta de luz”), as mais frequentes primeiro."""
    cont, forma = collections.Counter(), {}
    for f in fs:
        ws = [re.sub(r"[^\wÀ-ú-]", "", w) for w in f["texto"].split()]
        nn = [A._norm(w) for w in ws]
        for k in range(len(ws)):
            for tam in (3, 2):
                seg = nn[k:k + tam]
                if len(seg) < tam or seg[0] in STOP or seg[-1] in STOP or len(seg[0]) < 3 or len(seg[-1]) < 3:
                    continue
                if (tam == 3 and seg[1] not in ("de", "do", "da", "dos", "das")) or (tam == 2 and seg[1] in STOP):
                    continue
                if seg[0] in CONVERSA or seg[-1] in CONVERSA or not any(_bom_para_titulo(w) for w in (seg[0], seg[-1])):
                    continue   # “sempre teve”, “brighton teve”
                cont[" ".join(seg)] += 1
                forma.setdefault(" ".join(seg), " ".join(ws[k:k + tam]).lower())
    if todas is not None and todas is not fs:   # mesma regra de concentração dos termos-chave
        glob = collections.Counter()
        for f in todas:
            nn = [A._norm(re.sub(r"[^\wÀ-ú-]", "", w)) for w in f["texto"].split()]
            for c in cont:
                k = c.split()
                glob[c] += sum(1 for i in range(len(nn) - len(k) + 1) if nn[i:i + len(k)] == k)
        cont = collections.Counter({c: v for c, v in cont.items() if v / max(1, glob[c]) >= 0.5})
    rep = [forma[c] for c, v in cont.most_common() if v >= 2]
    if len(rep) < n:   # expressão de 3 palavras com o termo mais distintivo vale mesmo dita uma vez (“bolo de chocolate”)
        chave = _palavras_chave(fs, todas or fs, 1)
        if chave:
            r0 = _raiz(chave[0])
            rep += [forma[c] for c in cont if len(c.split()) == 3 and forma[c] not in rep
                    and any(_raiz(w) == r0 for w in c.split())][:n - len(rep)]
    return rep[:n]


def _expressao_de_abertura(fs, todas):
    """Expressão das 2 primeiras frases (“bolo de chocolate”, “conta de luz”) feita de termos-chave deste trecho."""
    chaves = {_raiz(k) for k in _palavras_chave(fs, todas, 6)}
    melhor, nota = None, 0
    for f in fs[:2]:
        ws = [re.sub(r"[^\wÀ-ú]", "", w) for w in f["texto"].split()]
        nn = [A._norm(w) for w in ws]
        for k in range(len(ws)):
            for tam in (3, 2):
                seg = nn[k:k + tam]
                if len(seg) < tam or not all(w.isalpha() and len(w) >= 3 for w in (seg[0], seg[-1])):
                    continue
                if (tam == 3 and seg[1] not in ("de", "do", "da", "dos", "das")) or (tam == 2 and (seg[1] in STOP or seg[0] in STOP)):
                    continue
                if seg[0] in STOP or seg[-1] in STOP or seg[0] in CONVERSA or seg[-1] in CONVERSA:
                    continue
                pts = sum(1 for w in (seg[0], seg[-1]) if _raiz(w) in chaves) + (0.5 if tam == 3 else 0)
                if pts > nota:
                    melhor, nota = " ".join(ws[k:k + tam]).lower(), pts
    return melhor if nota >= 1.5 else None


def _limitacoes(fs):
    lim = []
    prim = A._norm(fs[0]["texto"].split()[0]) if fs[0]["texto"].split() else ""
    if prim in CONECTIVOS:
        lim.append(f"Começa com “{fs[0]['texto'].split()[0]}”: pode faltar o contexto anterior.")
    elif prim in ANAFORAS:
        lim.append(f"Começa com “{fs[0]['texto'].split()[0]}”: refere-se a algo dito antes do corte.")
    if fs[-1]["texto"].rstrip()[-1:] not in ".!?…":
        lim.append("Termina sem pontuação final: a ideia pode continuar depois do corte.")
    if fs[-1]["texto"].rstrip().endswith("?"):
        lim.append("Termina numa pergunta: a resposta fica fora do corte.")
    return lim


def tipo_conteudo(fs):
    """Que tipo de fala é (por regras): passo a passo, relato, explicação ou argumento."""
    ws = [A._norm(w) for f in fs for w in f["texto"].split()]
    inicio = [A._norm(w) for f in fs for w in f["texto"].replace(",", " ").split()[:3]]
    txt = " " + " ".join(ws) + " "
    pts = {"instrucao": 2 * sum(1 for w in inicio if w in IMPERATIVOS),
           "relato": sum(1 for w in ws if w in PASSADO or (len(w) > 4 and w.endswith(("ou", "eu", "iu", "aram", "eram")))) / 2,
           "explicacao": 2 * sum(txt.count(f" {m} ") for m in EXPLICA) + (2 if _pergunta_real(fs[0]["texto"]) else 0),
           "argumento": 2 * sum(txt.count(f" {m} ") for m in ARGUMENTA)}
    melhor = max(pts, key=pts.get)
    return melhor if pts[melhor] >= 2 else "fala"


ROTULO_TIPO = {"instrucao": "Passo a passo", "relato": "Relato", "explicacao": "Explicação", "argumento": "Argumento", "fala": "Trecho"}


def descrever_local(fs, todas):
    """Título e ideia central montados por REGRAS a partir da estrutura da fala (sem IA, e dito assim)."""
    tipo = tipo_conteudo(fs)
    exprs = _expressoes(fs, 2, todas) or _palavras_chave(fs, todas, 2)
    intro = _expressao_de_abertura(fs, todas)
    if intro:   # o assunto costuma ser apresentado no começo (“vamos à receita do bolo de chocolate”)
        exprs = [intro] + [e for e in exprs if e != intro]
    assunto = exprs[0] if exprs else "assunto sem termo dominante"
    pergunta = next((f for f in fs[:2] if _pergunta_real(f["texto"]) and len(f["texto"].split()) >= 5), None)
    exprs = [e for e in exprs if len({_raiz(w) for w in e.split()}) == len(e.split())]   # “shopping shopping”
    if len(exprs) > 1 and {_raiz(w) for w in exprs[1].split()} & {_raiz(w) for w in exprs[0].split()}:
        exprs = exprs[:1]   # “derrama sabedoria e sabedoria”
    assunto = exprs[0] if exprs else assunto
    if pergunta and tipo in ("explicacao", "relato", "fala"):
        ws = pergunta["texto"].rstrip("?").split()
        while ws and A._norm(ws[0].strip(",")) in CONECTIVOS + ("ah", "agora", "olha", "bom", "entao", "e", "mas"):
            ws.pop(0)
        q, cortou = "", False
        for w in ws:
            if len(q) + len(w) + 1 > 70:
                cortou = True
                break
            q = (q + " " + w).strip()
        q = q.strip(" ,")
        titulo = f"{ROTULO_TIPO[tipo]}: {q[:1].upper() + q[1:]}{'…' if cortou else ''}?"
    else:
        titulo = f"{ROTULO_TIPO[tipo]}: {assunto}" + (f" e {exprs[1]}" if len(exprs) > 1 and tipo != "instrucao" else "")
    fecho = any(_tem(_norm_txt(f["texto"]), CONCLUI) for f in fs[-2:])
    virada = next((f for f in fs if _tem(_norm_txt(f["texto"]), REVELA)), None)
    if tipo == "instrucao":
        verbos = []
        for f in fs:
            for w in f["texto"].replace(",", " ").split()[:3]:
                if A._norm(w) in IMPERATIVOS and w.lower() not in verbos:
                    verbos.append(w.lower())
        ideia = (f"Ensina a fazer {assunto} em {len(verbos)} etapas ({', '.join(verbos[:5])})"
                 + (" e fecha com uma dica prática." if fecho else "."))
    elif tipo == "relato":
        ideia = (f"Conta um acontecimento sobre {assunto} em ordem" + (", com uma virada no meio" if virada else "")
                 + (" e um desfecho." if fecho else ", sem um desfecho claro."))
    elif tipo == "explicacao":
        ideia = (f"Responde à pergunta do início com uma explicação sobre {assunto}" if pergunta
                 else f"Explica como funciona {assunto}") + (" e conclui." if fecho else ".")
    elif tipo == "argumento":
        ideia = f"Defende um ponto de vista sobre {assunto}" + (" e termina com uma recomendação." if fecho else ".")
    else:
        ideia = f"Fala de {assunto}."
    return {"tipo": tipo, "titulo": titulo[:90], "ideia": ideia,
            "nota_metodo": "Título e ideia montados por regras a partir da estrutura da fala (sem IA)."}


def avaliar(fs, todas):
    """Seis critérios editoriais, cada um com nível (0 fraco, 1 médio, 2 forte) e motivo concreto. Não prevê views."""
    c = {}
    s0 = sinais(fs[0], fs)
    w0 = fs[0]["texto"].split()[0] if fs[0]["texto"].split() else ""
    if A._norm(w0) in CONECTIVOS or A._norm(w0) in ANAFORAS:
        c["abertura"] = (0, f"abre com “{w0}”, que depende do que veio antes")
    elif s0["pontos"] >= 2:
        c["abertura"] = (2, f"abre com {s0['categoria']}: “{fs[0]['texto'][:70]}”")
    else:
        c["abertura"] = (1, "abertura direta, sem pergunta nem conflito")
    ms = max(fs, key=lambda f: sinais(f, fs)["pontos"])
    sm = sinais(ms, fs)
    c["interesse"] = ((2, f"{sm['categoria']} em {mmss(ms['ini'])}: “{ms['texto'][:60]}”") if sm["pontos"] >= 3 else
                      (1, f"algum gancho ({sm['categoria']})") if sm["pontos"] >= 1.5 else
                      (0, "sem pergunta, conflito ou revelação marcados"))
    bags = [collections.Counter(f["raizes"]) for f in fs]
    cen = sum(bags, collections.Counter())
    coesao = sum(_cos(b, cen) for b in bags) / len(bags)
    kws = _palavras_chave(fs, todas, 3)
    c["clareza"] = ((2, f"assunto concentrado ({', '.join(kws)})") if coesao >= 0.3 else
                    (1, f"assunto identificável ({', '.join(kws[:2])})") if coesao >= 0.18 else
                    (0, "mistura assuntos diferentes"))
    lim = _limitacoes(fs)
    c["autonomia"] = ((2, "começa e termina sem depender de outro trecho") if not lim else
                      (1, lim[0].rstrip(".").lower()) if len(lim) == 1 else (0, "; ".join(x.rstrip(".").lower() for x in lim)))
    pal = [(w[0], w[1], w[2]) for f in fs for w in (f.get("palavras") or [])]
    reps = len(R._repeticoes(pal)) if pal else 0
    longas = sum(1 for a, b in zip(fs, fs[1:]) if b["ini"] - a["fim"] > 2.0)
    c["ritmo"] = ((2, "sem repetição nem pausa longa") if not reps and not longas else
                  (1, f"{reps} repetição(ões)" if reps else f"{longas} pausa(s) longa(s)") if reps + longas <= 1 else
                  (0, f"{reps} repetição(ões) e {longas} pausa(s) longa(s)"))
    fim_txt = " ".join(f["texto"] for f in fs[-2:])
    concl = _tem(_norm_txt(fim_txt), CONCLUI)
    resp = s0["categoria"] == "pergunta" and s0["entrega"]["status"] in ("provavel", "parcial")
    c["fechamento"] = ((2, f"fecha com conclusão (“{concl[0]}…”)") if concl else
                       (2, "responde à pergunta do início") if resp else
                       (1, "termina numa revelação") if _tem(_norm_txt(fim_txt), REVELA) else
                       (0, "termina sem fechamento claro"))
    peso = {"abertura": 1.5, "interesse": 1.0, "clareza": 1.0, "autonomia": 1.5, "ritmo": 0.5, "fechamento": 1.5}
    return c, sum(peso[k] * v[0] for k, v in c.items())


# Segmentação por assunto: parâmetros escolhidos medindo contra fronteiras anotadas à mão em 2 vídeos reais (gameplay
# narrado de 13 min e entrevista de 18 min) em 2026-10-12. F1 das fronteiras (tolerância 8–10 s): 0,31 com a janela
# antiga de 3 frases e bônus de pausa -> 0,62 com janela de 50 s, sem bônus de pausa (na fala natural pausa não marca
# troca de assunto) e com bônus para pergunta de entrevista. Valores do meio da faixa boa, não o pico (evita decorar).
JANELA_S, LIMIAR_K, FRONTEIRA_MIN_S, PESO_PERGUNTA, PESO_MARCADOR = 50.0, 0.6, 15.0, 0.4, 0.2
PERGUNTA_DE_APOIO = ("ne", "ta", "viu", "certo", "entendeu", "sabe", "nao", "ok", "e")


def _pergunta_real(texto):
    """Pergunta que abre assunto (“Como te selecionaram?”), não a muleta “..., né?”."""
    t = texto.rstrip()
    return (t.endswith("?") and len(t.split()) >= 4 and A._norm(t.split()[-1]) not in PERGUNTA_DE_APOIO
            and len(set(raizes(t))) >= 2)   # “Como é que é?” / “Ah é?” não abrem assunto


def blocos_de_assunto(frases):
    """Fronteiras de assunto por profundidade de coesão (estilo TextTiling): onde o vocabulário dos ~50 s antes e dos
    ~50 s depois menos se parece. Pergunta de entrevista e marcador de mudança (“agora”, “outro ponto”) só REFORÇAM."""
    n = len(frases)
    if n < 2:
        return [(0, n - 1)] if n else [], [0.0] * n
    bags = [collections.Counter(f["raizes"]) for f in frases]

    def janela(i, lado):
        c, k, t0 = collections.Counter(), (i - 1 if lado < 0 else i), frases[i]["ini"]
        while 0 <= k < n and abs(frases[k]["ini"] - t0) <= JANELA_S:
            c.update(bags[k])
            k += lado
        return c
    sims = [0.0] + [_cos(janela(i, -1), janela(i, 1)) for i in range(1, n)]
    prof = [0.0] * n
    for i in range(1, n):
        esq, k = sims[i], i
        while k - 1 >= 1 and sims[k - 1] >= esq:
            k -= 1
            esq = sims[k]
        dir_, k = sims[i], i
        while k + 1 < n and sims[k + 1] >= dir_:
            k += 1
            dir_ = sims[k]
        prof[i] = (esq - sims[i]) + (dir_ - sims[i])
        if _pergunta_real(frases[i]["texto"]):
            prof[i] += PESO_PERGUNTA
        if _tem(_norm_txt(" ".join(frases[i]["texto"].split()[:4])), MUDA_ASSUNTO):
            prof[i] += PESO_MARCADOR
    vals = prof[1:]
    media = sum(vals) / len(vals)
    dp = math.sqrt(sum((v - media) ** 2 for v in vals) / len(vals))
    fortes = sorted((i for i in range(1, n) if prof[i] > media + LIMIAR_K * dp), key=lambda i: -prof[i])
    fr = []
    for i in fortes:   # as mais fortes primeiro; fronteiras a pelo menos 15 s umas das outras
        if all(abs(frases[i]["ini"] - frases[j]["ini"]) >= FRONTEIRA_MIN_S for j in fr) and i >= 2 and n - i >= 2:
            fr.append(i)
    fr.sort()
    blocos, a = [], 0
    for b in fr + [n]:
        blocos.append((a, b - 1))
        a = b
    return blocos, prof


def descobrir_local(frases, cenas, dur, dmin, dmax, limite=20):
    """Cortes que seguem a estrutura natural dos assuntos (sem número fixo nem duração igual) + ranking explicável."""
    if not frases:
        return []
    blocos, prof = blocos_de_assunto(frases)
    durf = lambda a, b: frases[b]["fim"] - frases[a]["ini"]  # noqa: E731
    minimo = max(8.0, dmin * 0.5)

    def dividir(a, b):
        if durf(a, b) <= dmax * 1.25 or b - a < 5:
            return [(a, b)]
        ops = [k for k in range(a + 2, b - 1) if durf(a, k - 1) >= minimo and durf(k, b) >= minimo]
        if not ops:
            return [(a, b)]
        k = max(ops, key=lambda k: prof[k])
        return dividir(a, k - 1) + dividir(k, b)

    pecas = []
    for a, b in blocos:
        partes = dividir(a, b)
        for p in partes:
            nota = None
            if len(partes) > 1:
                nota = (f"Parte de um assunto mais longo ({durf(a, b):.0f}s, fonte {mmss(frases[a]['ini'])}–{mmss(frases[b]['fim'])}), "
                        "dividido na transição mais natural.")
            elif durf(*p) > dmax * 1.25:
                nota = (f"O assunto completo dura {durf(*p):.0f}s, acima dos {dmax:.0f}s pedidos: cortar mais quebraria a ideia.")
            pecas.append([p[0], p[1], nota])
    # Fragmento minúsculo (< 8 s) junta com o vizinho de fronteira mais fraca. Assunto curto mas completo NÃO é
    # juntado (isso misturava assuntos diferentes): fica inteiro, com o aviso de que é mais curto que a faixa.
    k = 0
    while k < len(pecas):
        a, b, nota = pecas[k]
        if durf(a, b) < dmin * 0.8 and (durf(a, b) < 8.0 or b - a < 1):
            viz = [j for j in (k - 1, k + 1) if 0 <= j < len(pecas)]
            viz = [j for j in viz if durf(min(a, pecas[j][0]), max(b, pecas[j][1])) <= dmax * 1.25]
            if viz:
                j = min(viz, key=lambda j: prof[max(pecas[j][0], a)] if pecas[j][0] > a else prof[a])
                na, nb = min(a, pecas[j][0]), max(b, pecas[j][1])
                pecas[min(j, k)] = [na, nb, "Inclui um trecho muito curto vizinho (menos de 8 s), juntado para não sobrar solto."]
                del pecas[max(j, k)]
                k = 0 if j < k else k
                continue
        if durf(a, b) < dmin * 0.8 and not pecas[k][2]:
            pecas[k][2] = (f"O assunto termina em {durf(a, b):.0f}s, abaixo dos {dmin:.0f}s pedidos: esticar traria outro assunto.")
        k += 1
    out = []
    for a, b, nota in pecas:
        fs = frases[a:b + 1]
        if durf(a, b) < 5 or len(fs) < 2:
            continue
        ini, fim, notas = refinar_bordas(fs[0]["ini"], fs[-1]["fim"], frases, a, b, cenas, dur)
        desc = descrever_local(fs, frases)
        crit, nota_total = avaliar(fs, frases)
        bags = [collections.Counter(f["raizes"]) for f in fs]
        cen = sum(bags, collections.Counter())
        central = max(fs, key=lambda f: _cos(collections.Counter(f["raizes"]), cen))
        gs = sorted(((sinais(f, fs), f) for f in fs), key=lambda x: -x[0]["pontos"])[0]
        out.append({"id": "c" + uuid.uuid4().hex[:8], "provedor": "local", "frases": [a, b],
                    "titulo": desc["titulo"], "ideia": desc["ideia"], "tipo": desc["tipo"],
                    "trecho": {"texto": central["texto"], "ini": central["ini"], "fim": central["fim"]},
                    "ini": ini, "fim": fim, "dur": round(fim - ini, 2), "nota_duracao": nota,
                    "bordas": notas, "criterios": [{"chave": kk, "nome": nn, "nivel": crit[kk][0], "texto": crit[kk][1]}
                                                   for kk, nn in CRITERIOS],
                    "gancho": {"tipo": "fala", "texto": gs[1]["texto"], "ini": gs[1]["ini"], "fim": gs[1]["fim"],
                               "categoria": gs[0]["categoria"]} if gs[0]["pontos"] >= 1.5 else None,
                    "limitacoes": _limitacoes(fs), "_nota": nota_total,
                    "analise": "só pela fala (transcrição); imagem não analisada"})
    out.sort(key=lambda c: (-c["_nota"], c["ini"]))
    out = out[:limite]
    for i, c in enumerate(out):
        c["rank"] = i + 1
        fortes = [x["texto"] for x in c["criterios"] if x["nivel"] == 2]
        fracos = [x["nome"].lower() for x in c["criterios"] if x["nivel"] == 0]
        c["por_que"] = ("Pontos fortes: " + "; ".join(fortes[:3]) + "." if fortes else "Sem ponto forte marcado.") + (
            f" Pesa contra: {', '.join(fracos)}." if fracos else "")
        if i + 1 < len(out):
            prox = out[i + 1]
            melhores = [x["nome"].lower() for x, y in zip(c["criterios"], prox["criterios"]) if x["nivel"] > y["nivel"]]
            c["acima_do_proximo"] = (f"Acima do #{i + 2} (“{prox['titulo'][:40]}”) por {', '.join(melhores)}." if melhores
                                     else f"Empate técnico com o #{i + 2}: ordem pela combinação dos critérios.")
        else:
            c["acima_do_proximo"] = None
        del c["_nota"]
    return out


PROMPT_CORTES = """Você é editor de vídeos curtos verticais em português. Abaixo está a transcrição de um vídeo, frase por frase,
no formato [índice] início–fim texto. Trocas de cena (s): {cenas}.
1) Encontre os ASSUNTOS, argumentos, histórias ou acontecimentos completos (ideias que se sustentam sozinhas), seguindo a
estrutura natural do vídeo. Não divida em partes iguais e não force uma quantidade. Não corte no meio de frase nem tire
ressalvas essenciais. Preferência de duração: {dmin:.0f}s a {dmax:.0f}s; se a ideia coerente precisar de outra duração,
mantenha-a e explique em "nota_duracao".
2) Para cada um: título ESPECÍFICO (nada genérico como "o falante fala sobre o tema"), a ideia central (o que é explicado,
defendido, revelado ou mostrado, com suas palavras, sem copiar a transcrição), por que vale selecionar, o gancho de abertura e
o contexto que falta. Você só tem a FALA: não descreva imagens.
3) Ordene do maior para o menor potencial editorial: abertura forte, interesse vindo do conteúdo, clareza da ideia, se funciona
sozinho, ritmo sem repetição e se tem fechamento/revelação. Não estime views, chance de viralizar nem dê notas numéricas.
Responda APENAS com JSON:
{{"cortes": [{{"posicao": 1, "titulo": "...", "ideia_central": "...", "tipo": "explica|argumenta|revela|conta|mostra|ensina",
  "frase_ini": int, "frase_fim": int, "frase_trecho": int, "por_que": "por que vale selecionar",
  "criterios": {{"abertura": "...", "interesse": "...", "clareza": "...", "autonomia": "...", "ritmo": "...", "fechamento": "..."}},
  "acima_do_proximo": "por que fica acima do próximo da lista, ou null no último",
  "gancho": {{"tipo": "fala" ou "titulo", "frase": int ou null, "texto": "..."}},
  "limitacoes": "contexto que falta, ou null", "nota_duracao": "ou null"}}]}}
Transcrição:
{texto}"""

PROMPT_ORDEM = """Estes são trechos candidatos de um mesmo vídeo, achados em partes diferentes da transcrição. Ordene TODOS do maior
para o menor potencial editorial como vídeo curto (abertura, interesse vindo do conteúdo, clareza, funcionar sozinho, ritmo,
fechamento). Não estime views nem dê notas. Responda APENAS com JSON:
{{"ordem": [ids na nova ordem], "motivos": {{"id": "por que está nessa posição"}}}}
Candidatos:
{lista}"""

TIPOS_IA = {"explica": "explicacao", "argumenta": "argumento", "revela": "revelacao", "conta": "relato", "mostra": "mostra",
            "ensina": "instrucao"}


def _cortes_do_bloco(tarefa, bloco, frases, cenas, dur, dmin, dmax):
    j, modelo = R.gemini_json(tarefa, PROMPT_CORTES.format(
        cenas=", ".join(f"{c:.1f}" for c in cenas if bloco[0]["ini"] - 1 <= c <= bloco[-1]["fim"] + 1)[:3000] or "nenhuma",
        dmin=dmin, dmax=dmax, texto=_texto_indexado(bloco)))
    cortes, desc = [], 0
    for c in _lista(j, "cortes")[:15]:
        try:
            a, b = _idx(c.get("frase_ini"), bloco, "frase_ini"), _idx(c.get("frase_fim"), bloco, "frase_fim")
            t = _idx(c.get("frase_trecho"), bloco, "frase_trecho")
            if not a <= t <= b or frases[b]["fim"] - frases[a]["ini"] < 4:
                raise ValueError("intervalo")
            g = c.get("gancho") if isinstance(c.get("gancho"), dict) else {}
            gancho = None
            if g.get("tipo") == "fala" and isinstance(g.get("frase"), int) and a <= g["frase"] <= b:
                fg = frases[g["frase"]]
                gancho = {"tipo": "fala", "texto": fg["texto"], "ini": fg["ini"], "fim": fg["fim"], "categoria": "fala"}
            elif g.get("tipo") == "titulo" and isinstance(g.get("texto"), str) and g["texto"].strip():
                gancho = {"tipo": "titulo", "texto": g["texto"].strip()[:90]}
            ini, fim, notas = refinar_bordas(frases[a]["ini"], frases[b]["fim"], frases, a, b, cenas, dur)
            crit = c.get("criterios") if isinstance(c.get("criterios"), dict) else {}
            lim = _limitacoes(frases[a:b + 1])
            if isinstance(c.get("limitacoes"), str) and c["limitacoes"].strip():
                lim.insert(0, c["limitacoes"].strip()[:300])
            cortes.append({"id": "c" + uuid.uuid4().hex[:8], "provedor": "gemini", "frases": [a, b],
                           "posicao": c.get("posicao") if isinstance(c.get("posicao"), int) else 999,
                           "titulo": _str(c.get("titulo"), "titulo", 90), "ideia": _str(c.get("ideia_central"), "ideia", 400),
                           "tipo": TIPOS_IA.get(c.get("tipo"), "fala"), "por_que": _str(c.get("por_que"), "por_que", 400),
                           "criterios": [{"chave": k, "nome": n, "nivel": None, "texto": str(crit.get(k) or "")[:200]}
                                         for k, n in CRITERIOS if crit.get(k)],
                           "acima_do_proximo": str(c.get("acima_do_proximo") or "")[:300] or None,
                           "trecho": {"texto": frases[t]["texto"], "ini": frases[t]["ini"], "fim": frases[t]["fim"]},
                           "ini": ini, "fim": fim, "dur": round(fim - ini, 2), "bordas": notas,
                           "nota_duracao": str(c.get("nota_duracao") or "")[:300] or None, "gancho": gancho,
                           "limitacoes": lim, "analise": "só pela fala (transcrição); imagem não analisada"})
        except (ValueError, TypeError):
            desc += 1
    cortes.sort(key=lambda x: x["posicao"])
    return cortes, desc, modelo


def descobrir_ia(tarefa, frases, cenas, dur, dmin, dmax, avisos=None):
    """Transcrição em blocos coerentes (com sobreposição), no máximo 2 pedidos simultâneos; depois consolida: tira
    duplicados (sobreposição > 50%) e, se houve mais de um bloco, pede UMA ordenação final só com títulos e ideias."""
    from concurrent.futures import ThreadPoolExecutor
    avisos = [] if avisos is None else avisos
    tam, sobra = 320, 30
    blocos = [frases[max(0, k - sobra):k + tam] for k in range(0, len(frases), tam)]
    feitos = []
    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = [ex.submit(_cortes_do_bloco, tarefa, b, frases, cenas, dur, dmin, dmax) for b in blocos]
        for k, f in enumerate(futs):
            feitos.append(f.result())
            tarefa.avancar(0.7 + 0.2 * (k + 1) / len(futs), f"modelo leu {k + 1} de {len(futs)} parte(s) da transcrição")
    modelo = feitos[0][2]
    descartados = sum(x[1] for x in feitos)
    if descartados:
        avisos.append(f"{descartados} corte(s) da resposta do modelo foram descartados (índice, intervalo ou formato inválido).")
    todos, unicos = [c for x in feitos for c in x[0]], []
    for c in todos:   # duplicados entre blocos vizinhos: fica o primeiro (melhor posição dentro do seu bloco)
        dup = next((u for u in unicos if min(u["fim"], c["fim"]) - max(u["ini"], c["ini"]) > 0.5 * min(u["dur"], c["dur"])), None)
        if not dup:
            unicos.append(c)
    if not unicos:
        raise A.Erro("Nenhum corte válido na resposta do provedor.", 502)
    if len(blocos) > 1 and len(unicos) > 1:
        lista = "\n".join(f'{c["id"]}: {c["titulo"]} — {c["ideia"][:200]} ({c["dur"]:.0f}s)' for c in unicos)
        try:
            j, _ = R.gemini_json(tarefa, PROMPT_ORDEM.format(lista=lista))
            ordem = j.get("ordem") if isinstance(j, dict) else None
            ids = {c["id"] for c in unicos}
            if not isinstance(ordem, list) or set(ordem) != ids or len(ordem) != len(ids):
                raise ValueError("ordem inválida")
            pos = {cid: i for i, cid in enumerate(ordem)}
            motivos = j.get("motivos") if isinstance(j.get("motivos"), dict) else {}
            unicos.sort(key=lambda c: pos[c["id"]])
            for c in unicos:
                if isinstance(motivos.get(c["id"]), str):
                    c["por_que"] = motivos[c["id"]][:400] + " " + c["por_que"]
        except (ValueError, TypeError, A.Erro):
            avisos.append("A ordenação final entre partes do vídeo falhou: a ordem mistura os rankings de cada parte (provisória).")
            unicos.sort(key=lambda c: (c["posicao"], c["ini"]))
    for i, c in enumerate(unicos):
        c["rank"] = i + 1
        c.pop("posicao", None)
    return unicos, descartados, modelo


def _chave_descoberta(fid, dmin, dmax, provedor):
    extra = R.status_config()["gemini_modelo"] if provedor == "gemini" else "regras"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", f"{E.hash_midia(A.achar(fid))}_{A.MODELO_WHISPER}_v{VERSAO_DESCOBERTA}_"
                                          f"{provedor}_{extra}_{dmin:g}_{dmax:g}")


def descobrir_cortes(tarefa, fid, dmin, dmax, provedor="auto"):
    """Descoberta na fonte inteira (só quando pedida). Reaproveita transcrição, cenas e o resultado anterior; detecta cenas
    EM PARALELO com a transcrição; mostra cortes provisórios enquanto a fala ainda está sendo transcrita."""
    import threading
    avisos, tempos = [], {}
    usa = "gemini" if _quer_ia(provedor) and R.status_config()["gemini"] else "local"
    if _quer_ia(provedor) and usa == "local":
        avisos.append("Sem GEMINI_API_KEY: descoberta feita pela heurística local (sem IA).")
    pasta = E.CACHE / "descobertas"
    pasta.mkdir(parents=True, exist_ok=True)
    arq = pasta / f"{_chave_descoberta(fid, dmin, dmax, usa)}.json"
    if arq.exists():   # mesma mídia, método, modelo e faixa: nada a refazer
        r = json.loads(arq.read_text(encoding="utf-8"))
        return {**r, "em_cache": True, "avisos": r.get("avisos", []) + ["Resultado reaproveitado (mesma mídia, método e faixa)."]}
    t0 = time.perf_counter()
    res_cenas = {}

    def cenas_em_paralelo():
        t = time.perf_counter()
        try:
            res_cenas["d"] = _cenas_cache(fid)
        except Exception as e:  # noqa: BLE001 - vira aviso; cenas só refinam bordas
            res_cenas["erro"] = e
        res_cenas["s"] = time.perf_counter() - t
    th = threading.Thread(target=cenas_em_paralelo, daemon=True)
    th.start()
    marca = {"ate": 0.0}

    def provisorio(palavras):   # a cada ~45 s de fala nova, cortes provisórios do que já foi transcrito
        if not palavras or palavras[-1][1] - marca["ate"] < 45:
            return
        marca["ate"] = palavras[-1][1]
        fr = frases_de(palavras, fid)
        if len(fr) < 8:
            return
        cs = [c for c in descobrir_local(fr[:-3], (res_cenas.get("d") or {}).get("cenas", []), palavras[-1][1], dmin, dmax)
              if c["frases"][1] < len(fr) - 6]   # o último assunto ainda pode continuar: fica de fora
        tarefa.parcial = {"cortes": cs, "provisorio": True, "ate_s": round(palavras[-1][1], 1), "provedor": "local",
                          "metodo": "Provisório (heurística local) enquanto a fala é transcrita."}
    tarefa.avancar(0.01, "transcrevendo a fonte inteira (descoberta de cortes)")
    t = time.perf_counter()
    tr = E.transcrever_fonte(T.Faixa(tarefa, 0.0, 0.6, "fala: "), fid, provisorio)
    tempos["transcricao_s"] = round(time.perf_counter() - t, 2)
    tarefa.avancar(0.61, "aguardando a detecção de cenas")
    th.join()
    tempos["cenas_s_em_paralelo"] = round(res_cenas.get("s", 0.0), 2)
    if "erro" in res_cenas:
        avisos.append(f"Trocas de cena não detectadas ({res_cenas['erro']}); as bordas seguem só a fala e as pausas.")
    cp = res_cenas.get("d") or {"cenas": [], "duracao": E.transcricao_em_cache(fid)["duracao"]}
    d = E.transcricao_em_cache(fid)
    frases = frases_de(d["palavras"], fid)
    if not frases:
        raise A.Erro("Nenhuma fala reconhecida nessa fonte: a descoberta de cortes por conteúdo precisa de fala.")
    tarefa.avancar(0.65, "procurando assuntos completos")
    t = time.perf_counter()

    def ia():
        cortes, _, modelo = descobrir_ia(tarefa, frases, cp["cenas"], cp["duracao"], dmin, dmax, avisos)
        return {"provedor": "gemini", "modelo": modelo, "cortes": cortes,
                "metodo": f"Gemini ({modelo}) leu a transcrição com tempos e ordenou por potencial editorial; bordas ajustadas "
                          "localmente por pausa e cena.", "enviado": "Texto da transcrição da fonte (nenhuma imagem ou áudio)."}

    def local():
        return {"provedor": "local", "cortes": descobrir_local(frases, cp["cenas"], cp["duracao"], dmin, dmax),
                "metodo": "Heurística local, SEM IA: assuntos por coesão de vocabulário, pausas e marcadores; título, ideia e "
                          "ranking montados por regras. Não entende o conteúdo como um modelo.", "enviado": None}
    res = com_ia(tarefa, usa, ia, local, avisos)
    tempos["analise_s"] = round(time.perf_counter() - t, 2)
    tempos["total_s"] = round(time.perf_counter() - t0, 2)
    out = {**res, "avisos": avisos, "fonte": fid, "frases": len(frases), "transcricao_em_cache": tr["em_cache"],
           "cenas": len(cp["cenas"]), "faixa": [dmin, dmax], "tempos": tempos,
           "ranking": "Avaliação editorial (abertura, interesse, clareza, autonomia, ritmo, fechamento), não previsão de views.",
           "nota": "A descoberta usa a transcrição da fonte inteira, mas NÃO cria legenda; a legenda dos cortes reaproveita essa transcrição."}
    if res["provedor"] == usa:   # só guarda o que veio do método pedido (uma falha do provedor não vira cache)
        E._gravar_json(arq, out)
    return {**out, "em_cache": False}


# ---------------------------------------------------------------- 2) títulos
PROMPT_TITULOS = """Você sugere títulos curtos (texto na tela) para um vídeo vertical em português. O vídeo é SÓ o trecho abaixo
(frases [índice] tempo texto). {visual}
Regras: descreva o que o trecho realmente mostra/diz; não invente fatos, nomes, números ou eventos; não prometa o que o trecho
não entrega; sem aspas atribuindo fala a alguém; até 60 caracteres. Evite repetir: {excluir}.
Responda APENAS com JSON: {{"titulos": [{{"texto": "...", "tipo": "descritivo|curiosidade|pergunta",
 "explicacao": "por que funciona e em que parte do trecho se apoia", "frases": [índices das frases que sustentam o título]}}]}}
Dê 5 títulos diferentes entre si.
Trecho:
{texto}"""


PROMPT_PUBLICACAO = """Você escreve o TEXTO DA PUBLICAÇÃO (a legenda do post no TikTok, que NÃO aparece dentro do vídeo) para
um vídeo vertical em português. O vídeo é SÓ o trecho abaixo (frases [índice] tempo texto). {visual}
Regras: diga do que o vídeo trata com base no que é dito/mostrado; não invente fatos, nomes, números, eventos ou citações;
não prometa o que o trecho não entrega; 1 ou 2 frases curtas e de 2 a 4 hashtags relacionadas ao assunto; até 220 caracteres.
Evite repetir: {excluir}.
Responda APENAS com JSON: {{"titulos": [{{"texto": "texto completo da publicação, com as hashtags",
 "tipo": "descritivo|curiosidade|pergunta", "explicacao": "por que funciona e em que parte do trecho se apoia",
 "frases": [índices das frases que sustentam o texto]}}]}}
Dê 4 opções diferentes entre si.
Trecho:
{texto}"""
LIMITE = {"video": 70, "publicacao": 300}


def _publicacao_local(frases, excluir):
    """Texto de publicação montado com a própria fala (sem IA): frase mais forte + hashtags dos termos distintivos."""
    kws = _palavras_chave(frases, frases, 4)
    tags = " ".join("#" + re.sub(r"[^\wÀ-ú]", "", k) for k in kws if len(k) >= 3)
    out = []
    for f in sorted(frases, key=lambda f: -sinais(f, frases)["pontos"])[:6]:
        base = f["texto"].strip()
        if len(base.split()) < 4:
            continue
        texto = (base[:200] + (" " + tags if tags else "")).strip()
        out.append({"texto": texto[:300], "tipo": "pergunta" if base.endswith("?") else "descritivo",
                    "explicacao": f"Usa a fala de {mmss(f['ini'])} e hashtags com os termos mais distintivos do corte.",
                    "frases": [f["i"]]})
    exc = {A._norm(x) for x in excluir}
    return [o for o in out if A._norm(o["texto"]) not in exc][:4]


def _titulos_local(frases, excluir):
    cands = []
    ordem = sorted(frases, key=lambda f: -sinais(f, frases)["pontos"])
    for f in ordem:
        sg = sinais(f, frases)
        t = S.titulo_derivado({"texto": f["texto"], "id": "x", "fonte": f["fonte"], "ini": f["ini"]})
        if t:
            cands.append({"texto": t["texto"], "tipo": "pergunta" if t["texto"].endswith("?") else "curiosidade",
                          "explicacao": f"Derivado da fala em {mmss(f['ini'])} ({'; '.join(sg['motivos'][:2]) or 'frase completa'}).",
                          "frases": [f["i"]]})
    kws = _palavras_chave(frases, frases, 2)
    if kws:
        cands.insert(1, {"texto": ("Sobre " + " e ".join(kws))[:60], "tipo": "descritivo",
                         "explicacao": "Termos mais frequentes e distintivos da fala do trecho.",
                         "frases": [f["i"] for f in frases if set(f["raizes"]) & {_raiz(k) for k in kws}][:4]})
    exc = {A._norm(x) for x in excluir}
    vistos, out = set(exc), []
    for c in cands:
        k = A._norm(c["texto"])
        if k not in vistos and len(c["texto"]) >= 8:
            vistos.add(k)
            out.append(c)
    return out[:5]


def sugerir_titulos(tarefa, plano, seg_id, escopo, provedor, visual, permitir_envio, excluir, destino="video"):
    p = E.validar_plano(plano)
    segs = segs_escopo(p, seg_id, escopo)
    avisos = []
    tr, frases = preparar(tarefa, p, segs)
    if not frases:
        raise A.Erro("Nenhuma fala reconhecida no corte: não há base para sugerir títulos sem inventar.")
    tarefa.avancar(0.5, "pensando nos títulos")

    def ia():
        quadros = []
        vis = "Sem imagens: baseie-se só na fala."
        if visual and permitir_envio:
            quadros = quadros_escopo(p, segs, 4)
            vis = f"Há {len(quadros)} quadros do trecho anexados, na ordem do tempo; use-os só para o que dá para ver."
        elif visual:
            avisos.append("Contexto visual pedido, mas sem autorização para enviar quadros ao Google: análise só pela fala.")
        modelo_prompt = PROMPT_PUBLICACAO if destino == "publicacao" else PROMPT_TITULOS
        j, modelo = R.gemini_json(tarefa, modelo_prompt.format(visual=vis, excluir="; ".join(excluir[:20]) or "nada",
                                                              texto=_texto_indexado(frases)),
                                  [q["jpg"] for q in quadros], temperatura=0.9 if excluir else None)
        out, desc = [], 0
        for t in _lista(j, "titulos")[:8]:
            try:
                texto = _str(t.get("texto"), "texto", LIMITE[destino]).strip('"“” ')
                base = [_idx(i, frases, "frases") for i in (t.get("frases") or [])][:6]
                if not base:
                    raise ValueError("sem frase de apoio")
                if A._norm(texto) in {A._norm(x) for x in excluir}:
                    raise ValueError("repetido")
                sem = termos_sem_apoio(re.sub(r"#\S+", "", texto), frases)   # hashtags não contam
                out.append({"texto": texto, "tipo": t.get("tipo") if t.get("tipo") in ("descritivo", "curiosidade", "pergunta") else "descritivo",
                            "explicacao": _str(t.get("explicacao"), "explicacao", 300), "frases": base,
                            "alerta": (f"Termos que não aparecem na fala: {', '.join(sem)}"
                                       + (" (podem vir dos quadros)" if quadros else " — confira se não é invenção.")) if sem else None})
            except (ValueError, TypeError):
                desc += 1
        if not out:
            raise A.Erro("Nenhum título válido na resposta do provedor.", 502)
        if desc:
            avisos.append(f"{desc} título(s) da resposta descartados (sem frase de apoio, repetidos ou fora do formato).")
        return {"provedor": "gemini", "modelo": modelo, "titulos": out,
                "metodo": f"Gemini ({modelo}) leu a fala do trecho" + (f" e {len(quadros)} quadros." if quadros else "."),
                "enviado": "Texto da transcrição do trecho" + (f" + {len(quadros)} quadros (com sua autorização)." if quadros else ".")}

    def local():
        ts = _publicacao_local(frases, excluir) if destino == "publicacao" else _titulos_local(frases, excluir)
        if not ts:
            avisos.append("A heurística local não achou mais títulos diferentes: edite um dos anteriores ou configure o Gemini.")
        return {"provedor": "local", "titulos": [{**t, "alerta": None} for t in ts], "metodo": METODO_LOCAL, "enviado": None}
    res = com_ia(tarefa, provedor, ia, local, avisos)
    por_i = {f["i"]: f for f in frases}
    for t in res["titulos"]:
        t["id"] = "ts" + uuid.uuid4().hex[:8]
        t["base"] = [{"texto": por_i[i]["texto"], "ini": por_i[i]["ini"], "fim": por_i[i]["fim"], "fonte": por_i[i]["fonte"]}
                     for i in t["frases"]]
    return {**res, "avisos": avisos, "escopo": _resumo_escopo(p, segs), "transcricao": tr, "destino": destino,
            "destino_rotulo": "Texto da publicação (legenda do post; NÃO aparece no vídeo)" if destino == "publicacao"
            else "Texto na tela (queimado no vídeo, com tempo e posição)"}


# ---------------------------------------------------------------- 3) ganchos analisados
PROMPT_GANCHOS = """Você analisa um vídeo vertical em português para escolher GANCHOS de abertura (primeiros 3 segundos).
O vídeo é SÓ o trecho abaixo (frases [índice] tempo texto). Trocas de cena no trecho (s da fonte): {cenas}. {visual}
Procure falas marcantes, perguntas, conflitos, revelações{e_visual}. Para cada gancho, diga se o trecho ENTREGA o que o gancho
promete (e em qual frase). Não escolha a primeira frase só por ser a primeira. Não invente fala: fala existente é citada por índice.
Tipos: "fala" (trecho falado existente, usar frase_ini..frase_fim), "visual" (momento visual: índice do quadro anexado),
"titulo" (texto novo sobreposto na tela), "narracao" (fala nova que precisaria ser gravada).
Responda APENAS com JSON: {{"ganchos": [{{"tipo": "fala|visual|titulo|narracao",
 "categoria": "afirmacao|pergunta|conflito|revelacao|visual", "frase_ini": int|null, "frase_fim": int|null, "quadro": int|null,
 "texto": "texto do título ou da narração (null para fala/visual)", "motivo": "por que prende",
 "promessa": "o que o gancho promete a quem assiste",
 "entrega": {{"status": "entrega|parcial|nao_entrega", "frase": int|null, "explicacao": "..."}}}}]}}
De 3 a 6 ganchos, pelo menos 2 de fala existente se houver.
Trecho:
{texto}"""


def _proposta_fala(fonte, ini, fim):
    return {"descricao": f"Repetir a fala da fonte {mmss(ini)}–{mmss(fim)} no início do vídeo (o trecho continua no lugar).",
            "operacao": {"tipo": "inserir_inicio", "fonte": fonte, "ini": round(ini, 2), "fim": round(fim, 2)}}


def _proposta_titulo(texto, origem):
    return {"descricao": "Título sobreposto nos 3 primeiros segundos (texto novo, não é fala).",
            "operacao": {"tipo": "adicionar_titulo", "texto": texto[:90], "ini": 0, "fim": 3, "origem": origem}}


def _ganchos_local(frases, segs):
    out = []
    for f in frases:
        sg = sinais(f, frases)
        if sg["pontos"] < 1.5 or not sg["motivos"]:
            continue
        out.append({"tipo": "fala", "categoria": sg["categoria"], "texto": f["texto"], "fonte": f["fonte"],
                    "ini": round(f["ini"], 2), "fim": round(f["fim"], 2), "motivo": "; ".join(sg["motivos"]),
                    "promessa": ("a resposta da pergunta" if sg["categoria"] == "pergunta" else
                                 "o desdobramento do que é dito"),
                    "entrega": sg["entrega"], "pontos": sg["pontos"], **_proposta_fala(f["fonte"], f["ini"], f["fim"])})
    out.sort(key=lambda g: (-g["pontos"], g["ini"]))
    if not out:   # sem sinal forte: oferece as melhores frases disponíveis, dizendo que o sinal é fraco
        fracas = sorted((f for f in frases if len(f["texto"].split()) >= 5), key=lambda f: (-sinais(f, frases)["pontos"], f["ini"]))
        for f in fracas[:2]:
            sg = sinais(f, frases)
            out.append({"tipo": "fala", "categoria": sg["categoria"], "texto": f["texto"], "fonte": f["fonte"],
                        "ini": round(f["ini"], 2), "fim": round(f["fim"], 2), "fraco": True,
                        "motivo": "sinal fraco: nenhuma frase tem pergunta, conflito ou revelação marcados; esta é a frase "
                                  "completa mais forte do trecho" + (f" ({'; '.join(sg['motivos'])})" if sg["motivos"] else ""),
                        "promessa": "o desdobramento do que é dito", "entrega": sg["entrega"], "pontos": sg["pontos"],
                        **_proposta_fala(f["fonte"], f["ini"], f["fim"])})
    falados = out[:4]
    tits = []
    for g in falados[:2]:
        t = S.titulo_derivado({"texto": g["texto"], "id": "x", "fonte": g["fonte"], "ini": g["ini"]})
        if t:
            tits.append({"tipo": "titulo", "categoria": g["categoria"], "texto": t["texto"], "fonte": None,
                         "motivo": f"Texto editorial derivado da fala em {mmss(g['ini'])} (não é citação).",
                         "promessa": g["promessa"], "entrega": g["entrega"], **_proposta_titulo(t["texto"], "sugestao_local")})
    return falados + tits


def analisar_ganchos(tarefa, plano, seg_id, escopo, provedor, visual, permitir_envio):
    p = E.validar_plano(plano)
    segs = segs_escopo(p, seg_id, escopo)
    avisos = []
    tr, frases = preparar(tarefa, p, segs)
    if not frases:
        raise A.Erro("Nenhuma fala reconhecida no escopo: sem base para ganchos falados.")
    tarefa.avancar(0.5, "analisando ganchos")
    cenas = []
    for s in segs:
        try:
            cenas += [c for c in _cenas_cache(s["fonte"])["cenas"] if s["ini"] <= c < s["fim"]]
        except A.Erro:
            pass

    def ia():
        quadros = []
        vis = "Sem imagens anexadas: NÃO proponha ganchos do tipo visual."
        if visual and permitir_envio:
            quadros = quadros_escopo(p, segs, 6)
            vis = "Quadros anexados, na ordem: " + "; ".join(f"quadro {q['k']} = {mmss(q['t'])} da fonte" for q in quadros) + "."
        elif visual:
            avisos.append("Momentos visuais pedidos, mas sem autorização para enviar quadros ao Google: só a fala foi analisada.")
        j, modelo = R.gemini_json(tarefa, PROMPT_GANCHOS.format(
            cenas=", ".join(f"{c:.1f}" for c in cenas) or "nenhuma", visual=vis,
            e_visual=" e momentos visuais" if quadros else "", texto=_texto_indexado(frases)), [q["jpg"] for q in quadros])
        out, desc = [], 0
        por_i = {f["i"]: f for f in frases}
        for g in _lista(j, "ganchos")[:8]:
            try:
                tipo = g.get("tipo")
                cat = g.get("categoria") if g.get("categoria") in ("afirmacao", "pergunta", "conflito", "revelacao", "visual") else "afirmacao"
                ent = g.get("entrega") if isinstance(g.get("entrega"), dict) else {}
                st_ = ent.get("status") if ent.get("status") in ("entrega", "parcial", "nao_entrega") else "incerta"
                ef = ent.get("frase")
                entrega = {"status": st_, "frase": ef if isinstance(ef, int) and ef in por_i else None,
                           "explicacao": str(ent.get("explicacao") or "")[:300]}
                base = {"categoria": cat, "motivo": _str(g.get("motivo"), "motivo", 300),
                        "promessa": str(g.get("promessa") or "")[:200], "entrega": entrega}
                if tipo == "fala":
                    a, b = _idx(g.get("frase_ini"), frases, "frase_ini"), _idx(g.get("frase_fim"), frases, "frase_fim")
                    if b < a or b - a > 3 or por_i[a]["seg"] != por_i[b]["seg"]:
                        raise ValueError("intervalo de fala inválido")
                    fa, fb = por_i[a], por_i[b]
                    texto = " ".join(por_i[k]["texto"] for k in range(a, b + 1))
                    out.append({"tipo": "fala", "texto": texto, "fonte": fa["fonte"], "ini": round(fa["ini"], 2),
                                "fim": round(fb["fim"], 2), **base, **_proposta_fala(fa["fonte"], fa["ini"], fb["fim"])})
                elif tipo == "visual":
                    k = g.get("quadro")
                    if not quadros or not isinstance(k, int) or not 0 <= k < len(quadros):
                        raise ValueError("visual sem quadro")
                    q = quadros[k]
                    s = next(x for x in segs if x["id"] == q["seg"])
                    ini, fim = max(s["ini"], q["t"] - 1.0), min(s["fim"], q["t"] + 1.5)
                    out.append({"tipo": "visual", "texto": f"Momento visual em {mmss(q['t'])}", "fonte": q["fonte"],
                                "ini": round(ini, 2), "fim": round(fim, 2), **base, **_proposta_fala(q["fonte"], ini, fim)})
                elif tipo == "titulo":
                    texto = _str(g.get("texto"), "texto", 90)
                    out.append({"tipo": "titulo", "texto": texto, "fonte": None, **base,
                                "alerta": ("Termos que não aparecem na fala: " + ", ".join(termos_sem_apoio(texto, frases)))
                                if termos_sem_apoio(texto, frases) else None,
                                **_proposta_titulo(texto, "sugestao_ia")})
                elif tipo == "narracao":
                    texto = _str(g.get("texto"), "texto", 300)
                    out.append({"tipo": "narracao", "texto": texto, "fonte": None, **base,
                                "descricao": "Narração NOVA: precisa ser gravada ou gerada (modo simples). Não é aplicada aqui; "
                                             "dá para usar o texto como título.", "operacao": None})
                else:
                    raise ValueError("tipo")
            except (ValueError, TypeError, StopIteration):
                desc += 1
        if not out:
            raise A.Erro("Nenhum gancho válido na resposta do provedor.", 502)
        if desc:
            avisos.append(f"{desc} gancho(s) da resposta descartados (índice inválido, fala inventada ou formato).")
        return {"provedor": "gemini", "modelo": modelo, "ganchos": out,
                "metodo": f"Gemini ({modelo}) leu a fala do escopo" + (f" e {len(quadros)} quadros." if quadros else "."),
                "enviado": "Texto da transcrição do escopo" + (f" + {len(quadros)} quadros (com sua autorização)." if quadros else ".")}

    def local():
        avisos.append("Sem análise por modelo, momentos VISUAIS não são detectados.")
        gs = _ganchos_local(frases, segs)
        if not gs or all(g.get("fraco") for g in gs if g["tipo"] == "fala"):
            avisos.append("Nenhuma frase do escopo tem sinal forte de gancho (pergunta, conflito, revelação, afirmação forte): "
                          "as sugestões abaixo são as frases mais fortes disponíveis. Isso não quer dizer que o corte seja "
                          "fraco: a heurística só enxerga esses sinais.")
        return {"provedor": "local", "ganchos": gs, "metodo": METODO_LOCAL, "enviado": None}
    res = com_ia(tarefa, provedor, ia, local, avisos)
    por_i = {f["i"]: f for f in frases}
    for g in res["ganchos"]:
        g["id"] = "g" + uuid.uuid4().hex[:8]
        fe = g["entrega"].get("frase")
        if fe is not None and fe in por_i:
            g["entrega"]["trecho"] = {"texto": por_i[fe]["texto"], "ini": por_i[fe]["ini"], "fonte": por_i[fe]["fonte"]}
    falas = [g for g in res["ganchos"] if g["tipo"] in ("fala", "visual") and g["entrega"]["status"] != "nao_entrega"]
    tits = [g for g in res["ganchos"] if g["tipo"] == "titulo"]
    # aparência automática: para os 2 melhores textos de gancho, alternativas completas (texto + visual)
    tarefa.avancar(0.9, "escolhendo o visual do gancho")
    alternativas = []
    if tits:
        y, motivo_pos = E.area_livre(p, segs[0]["id"])
        pref = (E.ler_preferencias() or {}).get("gancho") or {}
        for g in tits[:2]:
            alternativas += estilizar_gancho(g["texto"], g["categoria"], frases, y, motivo_pos, pref, origemde(res))
    melhor_tit = [dict(alternativas[0]["operacao"])] if alternativas else [g["operacao"] for g in tits[:1]]
    proposta = [g["operacao"] for g in falas[:1] if g.get("operacao")] + melhor_tit
    return {**res, "avisos": avisos, "escopo": _resumo_escopo(p, segs), "transcricao": tr,
            "alternativas_visuais": alternativas,
            "proposta": {"operacoes": proposta, "descricao": "Abrir com o melhor gancho falado que o corte entrega + o melhor "
                                                             "gancho na tela, já com o visual sugerido."} if proposta else None}


def origemde(res):
    return "sugestao_ia" if res.get("provedor") == "gemini" else "sugestao_local"


ESPORTE = {"gol", "jogo", "time", "partida", "campeonato", "torcida", "goleiro", "zagueiro", "penalti", "escanteio",
           "estadio", "treino", "corrida", "luta", "atleta", "placar", "vitoria", "final"}


def estilizar_gancho(texto, categoria, frases, y, motivo_pos, pref, origem):
    """Três (ou quatro) alternativas de gancho NA TELA para o mesmo texto, cada uma com preset, cor, palavra em destaque,
    tamanho, posição, animação e duração. A ordem e os motivos vêm do conteúdo, do espaço livre na imagem e da preferência."""
    vocab = {A._norm(w) for f in frases for w in f["texto"].split()}
    esporte = sorted(ESPORTE & vocab)
    if esporte:
        ordem = ["esporte", "impacto", "limpo"]
        motivo_base = {"esporte": f"o corte fala de esporte (“{', '.join(esporte[:3])}”)"}
    elif categoria in ("revelacao", "conflito"):
        ordem = ["impacto", "esporte", "limpo"]
        motivo_base = {"impacto": f"gancho de {categoria}: destaque forte e entrada rápida"}
    else:
        ordem = ["limpo", "impacto", "esporte"]
        motivo_base = {"limpo": "pergunta ou afirmação direta: visual discreto não compete com a legenda"}
    ws = texto.split()
    pesos = [(len(A._norm(w)) + (3 if re.search(r"\d", w) else 0) + (4 if _tem(f" {A._norm(w)} ", REVELA + CONFLITO + FORTE) else 0)
              - (10 if A._norm(w) in STOP else 0), k) for k, w in enumerate(ws)]
    destaque = [max(pesos)[1]] if pesos and max(pesos)[0] > 3 else []
    dur = round(max(2.0, min(4.5, 0.8 + len(texto) / 15)), 1)
    base_tam = 84 if len(texto) <= 18 else 72 if len(texto) <= 32 else 60
    alts = []
    variantes = ([("meu", pref)] if pref.get("estilo") else []) + [(k, {}) for k in ordem]
    for chave, extra in variantes:
        preset = extra.get("estilo") if chave == "meu" else chave
        if preset not in E.ESTILOS_TITULO:
            continue
        tam = extra.get("tamanho") or (base_tam + (6 if preset == "impacto" else -8 if preset == "limpo" else 0))
        pos_y = extra.get("posicao_y") if extra.get("posicao") == "fixa" and extra.get("posicao_y") else y
        visual = {"estilo": preset, "tamanho": int(tam), "posicao_y": round(pos_y, 2), "destaque": destaque,
                  **{k: extra[k] for k in E.CAMPOS_VISUAIS if k in extra}}
        nome = "Meu estilo" if chave == "meu" else E.ESTILOS_TITULO[preset]["nome"]
        motivo = ("seu estilo salvo" if chave == "meu" else motivo_base.get(preset, "alternativa de contraste"))
        alts.append({"id": "v" + uuid.uuid4().hex[:8], "texto": texto, "rotulo": nome, "visual": visual, "ini": 0.0,
                     "fim": dur, "motivo": f"{motivo}; posição: {motivo_pos}; dura {dur}s pelo tamanho do texto"
                                           + (f"; destaque em “{ws[destaque[0]]}”" if destaque else ""),
                     "operacao": {"tipo": "adicionar_titulo", "texto": texto, "ini": 0, "fim": dur, "origem": origem,
                                  "visual": visual}})
    return alts


# ---------------------------------------------------------------- 4) áudio
def medir_ruido(plano, segs):
    """Diferença (dB) entre o volume típico da fala e o do fundo nas pausas, nos trechos do escopo [DADO medido]."""
    import numpy as np
    fala_db, fundo_db = [], []
    for s in segs:
        if not plano["fontes"][s["fonte"]]["tem_audio"]:
            continue
        audio = E._decodificar(A.achar(s["fonte"]), s["ini"], s["fim"], timeout=A.prazo_midia(s["fim"] - s["ini"]))
        if audio.size < 1600:
            continue
        janela = 800  # 50 ms
        n = audio.size // janela
        rms = np.sqrt(np.mean(audio[:n * janela].astype(np.float64).reshape(n, janela) ** 2, axis=1)) + 1e-9
        db = 20 * np.log10(rms)
        mascara = np.zeros(n, bool)
        for w in E.palavras_no_intervalo(s["fonte"], s["ini"], s["fim"]):
            mascara[max(0, int((w[0] - s["ini"]) * 20)):int((w[1] - s["ini"]) * 20) + 1] = True
        fala_db += list(db[mascara])
        fundo_db += list(db[~mascara])
    if len(fala_db) < 10 or len(fundo_db) < 10:
        return None
    return round(float(np.median(fala_db) - np.percentile(fundo_db, 50)), 1)


def _sobre_fala(frases, a, b):
    return any(f["ini"] < b and f["fim"] > a for f in frases)


def _efeito(som, fonte, t, dur, volume, objetivo, motivo, origem="sugestao_local"):
    return {"id": "a" + uuid.uuid4().hex[:8], "tipo": "efeito", "som": som, "pronto": True,
            "nome": E.SONS[som]["nome"], "fonte": fonte, "t": round(t, 2), "dur": round(dur, 2), "volume": round(volume, 2),
            "objetivo": objetivo, "motivo": motivo,
            "operacao": {"tipo": "adicionar_efeito", "som": som, "fonte": fonte, "t": round(t, 2), "dur": round(dur, 2),
                         "volume": round(volume, 2), "objetivo": objetivo[:200], "origem": origem}}


def _efeitos_local(p, segs, frases):
    out = []
    for i, f in enumerate(frases):
        sg = sinais(f, frases)
        prox = frases[i + 1] if i + 1 < len(frases) and frases[i + 1]["seg"] == f["seg"] else None
        seg = next(s for s in segs if s["id"] == f["seg"])
        pausa = (prox["ini"] if prox else seg["fim"]) - f["fim"]
        if sg["categoria"] in ("revelacao", "conflito") and sg["pontos"] >= 3 and pausa >= 0.6:
            out.append((sg["pontos"], _efeito("impacto", f["fonte"], f["fim"] + 0.05, min(1.2, pausa - 0.1), 0.35,
                                              "pontuar a virada logo depois da frase, na pausa que já existe",
                                              f"Depois de “{f['texto'][:70]}” ({sg['categoria']}) há {pausa:.1f}s de pausa: "
                                              "o impacto cabe sem cobrir fala.")))
        ant_fim = frases[i - 1]["fim"] if i > 0 and frases[i - 1]["seg"] == f["seg"] else seg["ini"]
        if sg["categoria"] in ("pergunta", "revelacao") and sg["pontos"] >= 3 and f["ini"] - ant_fim >= 1.5:
            d = min(2.0, f["ini"] - ant_fim - 0.1)
            out.append((sg["pontos"] - 0.5, _efeito("subida", f["fonte"], f["ini"] - d, d, 0.25,
                                                    "criar expectativa antes da frase forte",
                                                    f"Há {f['ini'] - ant_fim:.1f}s de silêncio antes de “{f['texto'][:70]}”.")))
    if len(segs) > 1 and p["efeitos"]["transicao"]["tipo"] == "corte":
        for a, b in zip(segs, segs[1:]):
            contiguo = a["fonte"] == b["fonte"] and abs(a["fim"] - b["ini"]) < 0.1
            if contiguo or _sobre_fala(frases, a["fim"] - 0.6, a["fim"]):
                continue
            # termina exatamente na emenda: não cobre a primeira palavra do trecho seguinte
            out.append((1.0, _efeito("whoosh", None, max(0.0, b["out_ini"] - 0.6), 0.6, 0.2,
                                     "sinalizar a mudança de trecho na emenda",
                                     f"Emenda em {mmss(b['out_ini'])} da saída entre trechos não contíguos; o som "
                                     "termina na emenda, sem cobrir fala.")))
    out.sort(key=lambda x: -x[0])
    return [e for _, e in out[:3]]


PROMPT_AUDIO = """Você sugere efeitos sonoros para um vídeo vertical em português, SEM atrapalhar a fala. Só sugira efeito
quando ele tiver um propósito claro (marcar virada, revelação, mudança de assunto, tensão); se nada justificar, devolva lista vazia.
Sons prontos (sintetizados): {catalogo}. Se o momento pedir um som que não está na lista (ex.: passos, porta), use
"som": "externo" e descreva-o em "descricao_externa".
Frases [índice] tempo(fonte) texto:
{texto}
Responda APENAS com JSON: {{"efeitos": [{{"som": "chave do catálogo ou externo", "descricao_externa": "..." ou null,
 "apos_frase": índice da frase depois da qual o efeito entra (ou null), "antes_da_frase": índice (ou null),
 "dur": segundos, "volume": 0.05 a 0.6, "objetivo": "propósito em uma frase"}}]}}. No máximo 4 efeitos."""


def sugerir_audio(tarefa, plano, seg_id, escopo, provedor):
    p = E.validar_plano(plano)
    segs = segs_escopo(p, seg_id, escopo)
    avisos = []
    tr, frases = preparar(tarefa, p, segs)
    tarefa.avancar(0.45, "medindo fala e ruído de fundo")
    snr = medir_ruido(p, segs)
    tratamento = []
    au = p["audio"]
    if snr is not None and snr < 18 and not au["realce_voz"]:
        tratamento.append({"id": "a" + uuid.uuid4().hex[:8], "tipo": "tratamento", "pronto": True, "nome": "Realce de voz",
                           "objetivo": "deixar a fala mais clara",
                           "motivo": f"A fala está só {snr:.0f} dB acima do fundo nas pausas [DADO medido neste corte]; "
                                     "o realce corta grave e reduz o ruído.",
                           "operacao": {"tipo": "ajustar_audio", "realce_voz": True}})
    if not au["normalizar"]:
        tratamento.append({"id": "a" + uuid.uuid4().hex[:8], "tipo": "tratamento", "pronto": True, "nome": "Normalizar volume",
                           "objetivo": "volume consistente (−14 LUFS)", "motivo": "A normalização está desligada.",
                           "operacao": {"tipo": "ajustar_audio", "normalizar": True}})
    if au["musica"]["id"] and not au["musica"]["abaixar_na_fala"] and frases:
        tratamento.append({"id": "a" + uuid.uuid4().hex[:8], "tipo": "tratamento", "pronto": True,
                           "nome": "Abaixar a música na fala", "objetivo": "a música não competir com a voz",
                           "motivo": "Há música e fala ao mesmo tempo, sem abaixamento automático.",
                           "operacao": {"tipo": "ajustar_audio", "abaixar_na_fala": True}})
    tarefa.avancar(0.55, "procurando onde um efeito tem propósito")

    def ia():
        cat = "; ".join(f"{k} ({v['nome']}: {v['uso']}, ~{v['dur']}s)" for k, v in E.SONS.items())
        j, modelo = R.gemini_json(tarefa, PROMPT_AUDIO.format(catalogo=cat, texto=_texto_indexado(frases)))
        por_i = {f["i"]: f for f in frases}
        out, desc = [], 0
        for e in _lista(j, "efeitos")[:6]:
            try:
                som = e.get("som")
                dur = float(e.get("dur"))
                vol = float(e.get("volume"))
                if not (0.1 <= dur <= 10 and 0 < vol <= 1):
                    raise ValueError("dur/volume")
                if isinstance(e.get("apos_frase"), int) and e["apos_frase"] in por_i:
                    f = por_i[e["apos_frase"]]
                    t = f["fim"] + 0.05
                elif isinstance(e.get("antes_da_frase"), int) and e["antes_da_frase"] in por_i:
                    f = por_i[e["antes_da_frase"]]
                    t = max(0.0, f["ini"] - dur)
                else:
                    raise ValueError("posição")
                seg = next(s for s in segs if s["id"] == f["seg"])
                t = min(max(t, seg["ini"]), seg["fim"] - 0.1)
                objetivo = _str(e.get("objetivo"), "objetivo", 200)
                nota = ""
                if _sobre_fala(frases, t, t + dur):
                    vol, nota = min(vol, 0.12), " Cai sobre fala: volume limitado a 12% para preservar a inteligibilidade."
                if som in E.SONS:
                    out.append(_efeito(som, f["fonte"], t, dur, vol, objetivo, f"Sugerido pelo modelo junto de “{f['texto'][:70]}”.{nota}",
                                       origem="sugestao_ia"))
                elif som == "externo":
                    desc_ext = _str(e.get("descricao_externa"), "descricao_externa", 120)
                    out.append({"id": "a" + uuid.uuid4().hex[:8], "tipo": "efeito", "som": "arquivo", "pronto": False,
                                "nome": desc_ext, "fonte": f["fonte"], "t": round(t, 2), "dur": round(dur, 2),
                                "volume": round(vol, 2), "objetivo": objetivo,
                                "motivo": f"Precisa de um arquivo de som (“{desc_ext}”) que você tenha direito de usar "
                                          "(ex.: gravado por você ou CC0). Envie o arquivo para aplicar." + nota,
                                "operacao": None})
                else:
                    raise ValueError("som")
            except (ValueError, TypeError, StopIteration):
                desc += 1
        if desc:
            avisos.append(f"{desc} efeito(s) da resposta descartados (som, posição ou faixa inválidos).")
        return {"provedor": "gemini", "modelo": modelo, "efeitos": out,
                "metodo": f"Gemini ({modelo}) leu a fala; posições presas às frases; ruído medido localmente.",
                "enviado": "Texto da transcrição do escopo."}

    def local():
        return {"provedor": "local", "efeitos": _efeitos_local(p, segs, frases), "metodo": METODO_LOCAL, "enviado": None}
    res = com_ia(tarefa, provedor, ia, local, avisos) if frases else {
        "provedor": "local", "efeitos": [], "metodo": METODO_LOCAL, "enviado": None}
    if not res["efeitos"]:
        avisos.append("Nenhum efeito recomendado: não há virada ou pausa que justifique. Efeito sem propósito só polui a edição.")
    prontos = [x["operacao"] for x in tratamento + res["efeitos"] if x.get("pronto") and x.get("operacao")]
    return {**res, "tratamento": tratamento, "snr_db": snr, "avisos": avisos, "escopo": _resumo_escopo(p, segs),
            "transcricao": tr, "proposta": {"operacoes": prontos[:20]} if prontos else None}


# ---------------------------------------------------------------- rotas
@bp.get("/api/config")
def rota_config():
    """Só diz SE o provedor está configurado (e o modelo); nunca devolve a chave."""
    c = R.status_config()
    return jsonify({"gemini": c["gemini"], "gemini_modelo": c["gemini_modelo"]})


def _comum(j):
    p = E.validar_plano(j.get("plano"))
    escopo = j.get("escopo", "corte")
    if escopo not in ("corte", "linha"):
        raise A.Erro("escopo precisa ser 'corte' ou 'linha'.")
    provedor = j.get("provedor", "auto")
    if provedor not in ("auto", "gemini", "local"):
        raise A.Erro("provedor precisa ser 'auto', 'gemini' ou 'local'.")
    segs_escopo(p, j.get("segmento"), escopo)  # erro de escopo aparece já
    return p, j.get("segmento"), escopo, provedor


@bp.post("/api/cortes/descobrir")
def rota_descobrir():
    j = A.corpo_json()
    fid = j.get("fonte")
    A.achar(fid)
    dmin = A.num(j.get("min"), 5, 600, 20)
    dmax = A.num(j.get("max"), dmin + 1, 900, max(dmin + 1, 75))
    provedor = j.get("provedor", "auto") if j.get("provedor") in ("auto", "gemini", "local") else "auto"
    # pedido idêntico já em andamento (mesma mídia, faixa e método): devolve a MESMA tarefa
    t = T.iniciar("analise", "Descobrindo cortes no vídeo inteiro", descobrir_cortes, fid, dmin, dmax, provedor,
                  chave=f"descobrir:{fid}:{dmin:g}:{dmax:g}:{provedor}")
    return jsonify(t.json()), 202


@bp.post("/api/titulos/sugerir")
def rota_titulos():
    j = A.corpo_json()
    p, seg, escopo, prov = _comum(j)
    destino = j.get("destino", "video")
    if destino not in ("video", "publicacao"):
        raise A.Erro("destino precisa ser 'video' (texto na tela) ou 'publicacao' (texto do post).")
    excluir = [str(x)[:300] for x in (j.get("excluir") or []) if isinstance(x, str)][:40]
    t = T.iniciar("analise", "Sugerindo texto da publicação" if destino == "publicacao" else "Sugerindo texto na tela",
                  sugerir_titulos, p, seg, escopo, prov, j.get("visual") is True, j.get("permitir_envio") is True,
                  excluir, destino)
    return jsonify(t.json()), 202


@bp.post("/api/ganchos/analisar")
def rota_ganchos_analisar():
    j = A.corpo_json()
    p, seg, escopo, prov = _comum(j)
    t = T.iniciar("analise", "Analisando ganchos", analisar_ganchos, p, seg, escopo, prov, j.get("visual") is True,
                  j.get("permitir_envio") is True)
    return jsonify(t.json()), 202


@bp.post("/api/audio/sugerir")
def rota_audio():
    j = A.corpo_json()
    p, seg, escopo, prov = _comum(j)
    t = T.iniciar("analise", "Sugerindo áudio", sugerir_audio, p, seg, escopo, prov)
    return jsonify(t.json()), 202
