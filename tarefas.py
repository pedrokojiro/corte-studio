"""Tarefas em segundo plano com progresso, cancelamento e fila (sem dependências externas).

Uma tarefa pesada por vez (render, transcrição, análise): o resto espera "na fila".
"""
import subprocess
import threading
import time
import uuid

import app as A


class Cancelado(Exception):
    pass


class Tarefa:
    def __init__(self, tipo, descricao):
        self.id = uuid.uuid4().hex[:12]
        self.tipo, self.descricao = tipo, descricao
        self.estado = "na_fila"          # na_fila | rodando | concluida | erro | cancelada
        self.progresso, self.etapa = 0.0, "aguardando"
        self.resultado, self.erro = None, None
        self.parcial = None              # resultado provisório (ex.: cortes enquanto a fala é transcrita)
        self.chave = None
        self.criada, self.terminada = time.time(), None
        self._cancelar = threading.Event()

    def avancar(self, progresso=None, etapa=None):
        self.checar()
        if progresso is not None:
            self.progresso = max(0.0, min(1.0, float(progresso)))
        if etapa:
            self.etapa = etapa

    def cancelar(self):
        self._cancelar.set()

    @property
    def cancelada(self):
        return self._cancelar.is_set()

    def checar(self):
        if self._cancelar.is_set():
            raise Cancelado()

    def json(self):
        return {"id": self.id, "tipo": self.tipo, "descricao": self.descricao, "estado": self.estado,
                "progresso": round(self.progresso, 3), "etapa": self.etapa, "erro": self.erro,
                "resultado": self.resultado if self.estado == "concluida" else None,
                "parcial": self.parcial if self.estado in ("na_fila", "rodando") else None}


class Faixa:
    """Uma etapa de uma tarefa maior: o progresso 0..1 da etapa vira ini..ini+peso da tarefa; cancelar vale igual."""

    def __init__(self, tarefa, ini, peso, prefixo=""):
        self.tarefa, self.ini, self.peso, self.prefixo = tarefa, ini, peso, prefixo

    def avancar(self, progresso=None, etapa=None):
        self.tarefa.avancar(None if progresso is None else self.ini + self.peso * max(0.0, min(1.0, progresso)),
                            etapa and self.prefixo + etapa)

    def checar(self):
        self.tarefa.checar()

    @property
    def cancelada(self):
        return self.tarefa.cancelada

    @property
    def _cancelar(self):
        return self.tarefa._cancelar


_tarefas = {}
_lock = threading.Lock()
_pesada = threading.Semaphore(1)


def iniciar(tipo, descricao, fn, *args, chave=None):
    """`chave`: pedidos idênticos ainda em andamento devolvem a MESMA tarefa (não repete trabalho)."""
    t = Tarefa(tipo, descricao)
    t.chave = chave
    with _lock:
        agora = time.time()
        for k in [k for k, v in _tarefas.items() if v.terminada and agora - v.terminada > 3600]:
            del _tarefas[k]
        if chave:
            igual = next((v for v in _tarefas.values() if v.chave == chave and v.estado in ("na_fila", "rodando")
                          and not v.cancelada), None)
            if igual:
                return igual
        _tarefas[t.id] = t

    def rodar():
        try:
            while not _pesada.acquire(timeout=0.5):
                if t.cancelada:
                    raise Cancelado()
            try:
                t.estado = "rodando"
                t.resultado = fn(t, *args)
                t.progresso, t.etapa, t.estado = 1.0, "pronto", "concluida"
            finally:
                _pesada.release()
        except Cancelado:
            t.estado, t.etapa = "cancelada", "cancelada pelo usuário"
        except A.Erro as e:
            t.estado, t.erro = "erro", str(e)
        except Exception as e:  # noqa: BLE001 - vira mensagem para o usuário, detalhe no terminal
            A.app.logger.exception("Erro na tarefa %s", t.id)
            t.estado, t.erro = "erro", f"Erro interno ({type(e).__name__}): {e}"
        finally:
            t.terminada = time.time()

    threading.Thread(target=rodar, daemon=True, name=f"tarefa-{t.id}").start()
    return t


def obter(tid):
    t = _tarefas.get(tid)
    if not t:
        raise A.Erro("Tarefa não encontrada (pode ter expirado).", 404)
    return t


def ffmpeg(cmd, tarefa, duracao, ini=0.0, peso=1.0, cwd=None, timeout=None, etapa=None):
    """Roda o ffmpeg acompanhando `-progress`. Cancelar ou estourar o prazo mata o processo."""
    cmd = [cmd[0], "-progress", "pipe:1", "-nostats"] + cmd[1:]
    prazo = timeout or A.prazo_midia(duracao)
    try:
        p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise A.Erro("'ffmpeg' não encontrado. Instale o ffmpeg e coloque no PATH.")
    erros, motivo = [], {}

    def ler_stderr():
        for linha in p.stderr:
            erros.append(linha)
            del erros[:-20]

    def vigia():
        limite = time.time() + prazo
        while p.poll() is None:
            if tarefa is not None and tarefa.cancelada:
                motivo["x"] = "cancelado"
                p.kill()
            elif time.time() > limite:
                motivo["x"] = "prazo"
                p.kill()
            time.sleep(0.2)

    threading.Thread(target=ler_stderr, daemon=True).start()
    threading.Thread(target=vigia, daemon=True).start()
    if etapa and tarefa is not None:
        tarefa.etapa = etapa
    for linha in p.stdout:
        if linha.startswith("out_time_us=") and tarefa is not None and duracao > 0:
            try:
                t = int(linha.split("=", 1)[1]) / 1e6
            except ValueError:
                continue
            tarefa.progresso = max(tarefa.progresso, min(1.0, ini + peso * min(1.0, t / duracao)))
    p.wait()
    if motivo.get("x") == "cancelado":
        raise Cancelado()
    if motivo.get("x") == "prazo":
        raise A.Erro(f"O ffmpeg passou de {prazo:.0f}s e foi interrompido. Tente um trecho menor.", 504)
    if p.returncode != 0:
        raise A.Erro("Falha no ffmpeg: " + " | ".join(l.strip() for l in erros[-4:]))
    if tarefa is not None:
        tarefa.progresso = max(tarefa.progresso, ini + peso)
