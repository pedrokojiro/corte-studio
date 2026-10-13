"""Gera o executável do Corte Studio: dist/CorteStudio/ (pasta portátil com CorteStudio.exe, ffmpeg e LEIA-ME).

Rode pelo .venv do app:  .venv\\Scripts\\python construir_exe.py   (ou construir_exe.bat)
Instala o PyInstaller fixado (requirements-build.txt) só no .venv.
"""
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
DESTINO = RAIZ / "dist" / "CorteStudio"
LEIA_ME = """CORTE STUDIO (executável para Windows)

Abrir: dê dois cliques em CorteStudio.exe. O navegador abre em http://127.0.0.1:5000/editor
Sair: feche a janela preta do Corte Studio.
Abriu de novo com ele já aberto? Só abre o navegador.

O que fica nesta pasta (seus dados; não apague se quiser manter):
  uploads\\   vídeos enviados          saida\\     vídeos exportados (com o .json do plano)
  edicoes\\   projetos salvos          cache\\     transcrições e prévias (pode apagar para liberar espaço)
  config_local.env  (opcional) chave do Gemini: crie com a linha GEMINI_API_KEY=sua-chave e abra o app de novo

Primeira transcrição: o modelo de fala (~480 MB) é baixado da internet uma vez e fica na pasta do usuário
(.cache\\huggingface). Depois funciona sem internet, exceto a narração por voz (edge-tts) e o Gemini.

A pasta precisa ficar num lugar onde você pode gravar (ex.: Documentos ou Área de Trabalho), não em
"Arquivos de Programas". Mova a pasta inteira, não só o .exe.

ffmpeg: a pasta ffmpeg\\ traz o ffmpeg e o ffprobe (build de www.gyan.dev, licença GPL). Se for repassar esta
pasta para outras pessoas, a licença do ffmpeg exige oferecer o código-fonte dele (https://ffmpeg.org).
"""


def rodar(*cmd):
    print(">", " ".join(map(str, cmd)), flush=True)
    subprocess.run(cmd, check=True, cwd=RAIZ)


def main():
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        sys.exit("[ERRO] ffmpeg/ffprobe não estão no PATH (winget install Gyan.FFmpeg).")
    rodar(sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q",
          "-r", "requirements-build.txt", "-c", "constraints.txt")
    rodar(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "corte_studio.spec")
    (DESTINO / "ffmpeg").mkdir(exist_ok=True)
    for exe in (ffmpeg, ffprobe):   # o atalho do winget é um link: copia o arquivo de verdade
        real = Path(exe).resolve()
        shutil.copy2(real, DESTINO / "ffmpeg" / real.name)
    (DESTINO / "LEIA-ME.txt").write_text(LEIA_ME, encoding="utf-8-sig")
    total = sum(f.stat().st_size for f in DESTINO.rglob("*") if f.is_file())
    print(f"\nPronto: {DESTINO / 'CorteStudio.exe'}  ({total / 1e6:.0f} MB na pasta)")


if __name__ == "__main__":
    main()
