import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as A  # noqa: E402


def ff(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


@pytest.fixture(scope="session")
def midia(tmp_path_factory):
    """Mídia sintética gerada pelo ffmpeg (nada baixado)."""
    d = tmp_path_factory.mktemp("midia")
    ff("-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=8", "-f", "lavfi", "-i", "sine=f=440:d=8",
       "-c:v", "libx264", "-c:a", "aac", "-shortest", str(d / "com_audio.mp4"))
    ff("-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=6", "-c:v", "libx264", str(d / "sem_audio.mp4"))
    ff("-f", "lavfi", "-i", "sine=f=220:d=9", str(d / "narr9.mp3"))
    ff("-f", "lavfi", "-i", "sine=f=220:d=62", str(d / "narr62.mp3"))
    return d


@pytest.fixture
def studio(tmp_path, monkeypatch):
    """Aponta uploads/saida/tmp do app para uma pasta temporária."""
    for nome, sub in (("UP", "uploads"), ("OUT", "saida"), ("TMP", "tmp")):
        p = tmp_path / sub
        p.mkdir()
        monkeypatch.setattr(A, nome, p)
    return A


@pytest.fixture
def enviar(studio):
    cli = studio.app.test_client()

    def _enviar(caminho):
        with open(caminho, "rb") as f:
            r = cli.post("/upload", data={"arquivo": (f, Path(caminho).name)},
                         content_type="multipart/form-data")
        assert r.status_code == 200, r.get_json()
        return r.get_json()["id"]

    return _enviar


def dur(path):
    return A.probe(path)["duracao"]
