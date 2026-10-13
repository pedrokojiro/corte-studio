# -*- mode: python ; coding: utf-8 -*-
# Especificação do PyInstaller para o Corte Studio (modo pasta: dist/CorteStudio/CorteStudio.exe).
# Gere com construir_exe.bat (ou: .venv\Scripts\python construir_exe.py).
from PyInstaller.utils.hooks import collect_all

datas = [("index.html", "."), ("editor.html", "."), ("editor.js", ".")]   # páginas: somente leitura, em _MEIPASS
binaries = []
hiddenimports = ["editor", "referencias", "sugestoes", "conteudo", "tarefas"]
# Transcrição: faster-whisper usa ctranslate2 (DLLs), onnxruntime (VAD silero), tokenizers e importa o av.
for pacote in ("faster_whisper", "ctranslate2", "onnxruntime", "tokenizers", "av", "edge_tts"):
    d, b, h = collect_all(pacote)
    datas += d
    binaries += b
    hiddenimports += h
# Partes do onnxruntime que o app não usa (só o capi roda o VAD). Além de peso, os caminhos de onnxruntime/tools
# passam de 260 caracteres quando a pasta do app fica num caminho comprido, e extrair/copiar falha no Windows.
SOBRA_ORT = ("tools", "transformers", "quantization", "datasets")


def _fora(destino):
    partes = destino.replace("\\", "/").split("/")
    return len(partes) > 1 and partes[0] == "onnxruntime" and partes[1] in SOBRA_ORT


datas = [x for x in datas if not _fora(x[1])]
binaries = [x for x in binaries if not _fora(x[1])]
hiddenimports = [h for h in hiddenimports if not any(h.startswith(f"onnxruntime.{s}") for s in SOBRA_ORT)]

a = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "matplotlib", "IPython"] + [f"onnxruntime.{s}" for s in SOBRA_ORT],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="CorteStudio", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="CorteStudio")
