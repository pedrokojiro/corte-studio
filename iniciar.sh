#!/usr/bin/env bash
# Corte Studio - Linux/macOS (também roda no Git Bash do Windows).
# Usa um ambiente virtual próprio (.venv): NÃO instala nem atualiza nada no Python do sistema.
set -u
cd "$(dirname "$0")" || exit 1
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8

falha() { echo "[ERRO] $*" >&2; exit 1; }

command -v ffmpeg >/dev/null 2>&1 || falha "ffmpeg não encontrado. Instale (Debian/Ubuntu: sudo apt install ffmpeg; macOS: brew install ffmpeg)."
command -v ffprobe >/dev/null 2>&1 || falha "ffprobe não encontrado (vem junto com o ffmpeg)."

python_do_venv() {
  if [ -x .venv/bin/python ]; then echo .venv/bin/python
  elif [ -x .venv/Scripts/python.exe ]; then echo .venv/Scripts/python.exe   # Git Bash no Windows
  fi
}

VPY="$(python_do_venv)"
if [ -z "$VPY" ]; then
  PY=""
  for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then PY="$c"; break; fi
  done
  [ -n "$PY" ] || falha "Python 3.12 ou mais novo não encontrado."
  echo "Criando o ambiente do app em .venv (só na primeira vez)..."
  "$PY" -m venv .venv || falha "Não consegui criar o .venv. No Debian/Ubuntu: sudo apt install python3-venv"
  VPY="$(python_do_venv)"
  [ -n "$VPY" ] || falha "O .venv foi criado sem Python."
fi

if ! cmp -s requirements.txt .venv/requirements.ok || ! cmp -s constraints.txt .venv/constraints.ok; then
  echo "Instalando dependências no .venv..."
  "$VPY" -m pip install --disable-pip-version-check -q -r requirements.txt -c constraints.txt \
    || falha "Falha ao instalar as dependências. Verifique a internet e rode de novo."
  cp requirements.txt .venv/requirements.ok && cp constraints.txt .venv/constraints.ok
fi

(sleep 3; xdg-open http://127.0.0.1:5000 || open http://127.0.0.1:5000 || cmd.exe /c start http://127.0.0.1:5000) >/dev/null 2>&1 &
exec "$VPY" app.py
