"""Mede a precisão da descoberta de cortes contra fronteiras de assunto anotadas à mão (tests/anotacoes_descoberta.json).

Uso: .venv\\Scripts\\python tests/medir_descoberta.py [min max]
Precisa das transcrições em cache/transcricoes/ (geradas pelo app ao descobrir cortes nesses vídeos); casos sem a
transcrição local são pulados. Métricas: precisão/cobertura/F1 das fronteiras (com tolerância; fronteira fraca achada
conta, perdida não pune) e pureza (fração de cada corte dentro de um único assunto anotado). Só a heurística local.
"""
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
import app  # noqa: E402,F401
import conteudo as C  # noqa: E402


def medir(caso, cortes, dur):
    tol, fortes, fracas = caso["tolerancia_s"], caso["fortes"], caso["fracas"]
    brutas = sorted({round(c["ini"], 1) for c in cortes if c["ini"] > 2} | {round(c["fim"], 1) for c in cortes if c["fim"] < dur - 2})
    previstas = []   # fim de um corte e início do seguinte, colados, são UMA fronteira
    for p in brutas:
        if not previstas or p - previstas[-1] > tol:
            previstas.append(p)
    usadas, tp = set(), 0
    for g in fortes + fracas:
        m = [p for p in previstas if abs(p - g) <= tol and p not in usadas]
        if m:
            usadas.add(min(m, key=lambda p: abs(p - g)))
            tp += 1
    prec = tp / max(1, len(previstas))
    rec = sum(1 for g in fortes if any(abs(p - g) <= tol for p in previstas)) / len(fortes)
    lim = [0.0] + sorted(fortes + fracas) + [dur]
    pur = [max(0.0, max(min(c["fim"], b) - max(c["ini"], a) for a, b in zip(lim, lim[1:]))) / max(0.1, c["fim"] - c["ini"])
           for c in cortes]
    return prec, rec, 2 * prec * rec / max(1e-9, prec + rec), sum(pur) / max(1, len(pur))


def main():
    dmin, dmax = (float(sys.argv[1]), float(sys.argv[2])) if len(sys.argv) > 2 else (20.0, 75.0)
    anot = json.loads((RAIZ / "tests" / "anotacoes_descoberta.json").read_text(encoding="utf-8"))
    for nome, caso in anot["casos"].items():
        arq = RAIZ / "cache" / "transcricoes" / f"{caso['hash']}_small_pt.json"
        if not arq.exists():
            print(f"{nome}: pulado (sem a transcrição local)")
            continue
        d = json.loads(arq.read_text(encoding="utf-8"))
        fr = C.frases_de([tuple(w) for w in d["palavras"]], "x")
        dur = fr[-1]["fim"]
        cortes = C.descobrir_local(fr, [], dur, dmin, dmax, limite=40)
        p, r, f1, pur = medir(caso, cortes, dur)
        print(f"{nome} ({caso['descricao']}): P {p:.2f} R {r:.2f} F1 {f1:.2f} | pureza {pur:.2f} | {len(cortes)} cortes")


if __name__ == "__main__":
    main()
