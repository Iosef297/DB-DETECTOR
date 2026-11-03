"""Arranque de proyectos Python (Django / Flask / FastAPI / script).

Se resuelve EN CADA ARRANQUE (no al detectar), porque la detección queda
guardada en la BD y la PC puede cambiar entre medio (instalaste Python, se
agregó un requirements.txt, etc.).

Qué hace:
  1. Busca el venv propio del proyecto (.venv / venv / env). Si existe, lo usa.
  2. Si no, busca un Python 3 REAL del sistema (py -3, python, python3). Ojo:
     en Windows "python.exe" puede ser el alias de la Microsoft Store, que
     existe aunque Python no esté instalado — por eso se verifica ejecutándolo.
     Nunca se usa sys.executable: dentro del .exe ese es la app misma.
  3. Sin venv propio crea uno en ~/.orquestador/venvs/<proyecto> (no ensucia
     la carpeta del proyecto).
  4. Instala dependencias (requirements.txt, o pyproject.toml / setup.py)
     solo cuando cambiaron: guarda un hash en el venv.
  5. Arma el comando según el framework, pasando el puerto asignado.

Todo se encadena en UN comando de shell, así pip/venv corren en segundo plano
y su salida aparece en los logs del proyecto igual que la de la app.
"""
import os
import re
import hashlib
import platform
import subprocess
from pathlib import Path

from core.paths import data_root

_IS_WINDOWS = platform.system() == "Windows"

PYTHON_FIX = ("Instala Python 3 desde https://www.python.org/downloads/ "
              "(marca \"Add python.exe to PATH\") o con: winget install Python.Python.3.12 "
              "— y vuelve a iniciar el proyecto.")

_ENTRY_FILES = ("manage.py", "app.py", "main.py", "run.py", "server.py",
                "wsgi.py", "asgi.py", "app/main.py", "src/main.py")
_VENV_NAMES = (".venv", "venv", "env")

_system_python_cache: list | None = None


def _q(path: str) -> str:
    return f'"{path}"'


def _venv_python(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if _IS_WINDOWS else "bin/python")


def _clean_env() -> dict:
    env = os.environ.copy()
    for k in ("PYTHONHOME", "PYTHONPATH", "_MEIPASS2"):
        env.pop(k, None)
    return env


def _works(cmd: list) -> bool:
    try:
        r = subprocess.run(cmd + ["-c", "import sys; assert sys.version_info >= (3, 8); print('ok')"],
                           capture_output=True, text=True, timeout=15, env=_clean_env())
        return r.returncode == 0 and "ok" in r.stdout
    except Exception:
        return False


def find_system_python() -> list | None:
    """Devuelve el comando (lista) de un Python 3.8+ que funcione, o None."""
    global _system_python_cache
    if _system_python_cache and _works(_system_python_cache):
        return _system_python_cache
    candidates = []
    if _IS_WINDOWS:
        candidates.append(["py", "-3"])
        # Instalación típica por usuario de python.org (aunque no esté en PATH)
        base = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python"
        if base.is_dir():
            for d in sorted(base.glob("Python3*"), reverse=True):
                exe = d / "python.exe"
                if exe.is_file():
                    candidates.append([str(exe)])
    candidates += [["python"], ["python3"]]
    for c in candidates:
        if _works(c):
            _system_python_cache = c
            return c
    _system_python_cache = None
    return None


def _project_venv(root: Path) -> Path | None:
    for n in _VENV_NAMES:
        if _venv_python(root / n).is_file():
            return root / n
    return None


def _deps_file(root: Path) -> Path | None:
    for n in ("requirements.txt", "pyproject.toml", "setup.py"):
        if (root / n).is_file():
            return root / n
    return None


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _module_of(rel: str) -> str:
    return rel[:-3].replace("/", ".")


def _detect_entry(root: Path, port: int | None) -> tuple[str, str] | None:
    """(label, args-para-python) según el framework detectado."""
    host_port = f"127.0.0.1:{port}" if port else ""

    if (root / "manage.py").is_file():
        return "Django", f"manage.py runserver {host_port}".strip()

    for rel in _ENTRY_FILES[1:]:
        f = root / rel
        if not f.is_file():
            continue
        src = _read(f)
        mod = _module_of(rel)

        m = re.search(r"^(\w+)\s*(?::\s*\w+\s*)?=\s*FastAPI\(", src, re.M)
        if m and "uvicorn.run" not in src:
            args = f"-m uvicorn {mod}:{m.group(1)} --host 127.0.0.1"
            return "FastAPI (uvicorn)", args + (f" --port {port}" if port else "")

        m = re.search(r"^(\w+)\s*=\s*Flask\(", src, re.M)
        if m and not re.search(rf"{m.group(1)}\.run\(", src):
            args = f"-m flask --app {mod}:{m.group(1)} run --host 127.0.0.1"
            return "Flask", args + (f" --port {port}" if port else "")

        # Script con su propio arranque (app.run / uvicorn.run / etc.)
        return f"Python ({rel})", rel.replace("/", os.sep)
    return None


def plan(project_name: str, project_root: str, port: int | None) -> dict:
    """Devuelve {"ok": True, "cmd", "label", "python"} o {"ok": False, "error", "fix"}."""
    root = Path(project_root)
    entry = _detect_entry(root, port)
    if not entry:
        return {"ok": False, "error": "No se encontró cómo arrancar este proyecto Python "
                                      "(manage.py, app.py, main.py, ...)", "fix": ""}
    label, run_args = entry

    steps = []
    venv = _project_venv(root)
    if venv:
        py = _venv_python(venv)
    else:
        sys_py = find_system_python()
        if not sys_py:
            return {"ok": False, "error": "Python 3 no está instalado en esta PC", "fix": PYTHON_FIX}
        venv = Path(data_root()) / "venvs" / project_name
        py = _venv_python(venv)
        if not py.is_file():
            steps.append(f'echo [orquestador] Creando entorno virtual en {venv} ...')
            steps.append(" ".join(_q(c) if " " in c else c for c in sys_py) + f" -m venv {_q(str(venv))}")

    deps = _deps_file(root)
    if deps:
        digest = hashlib.sha256(deps.read_bytes()).hexdigest()[:16]
        marker = venv / ".orq_deps_hash"
        current = marker.read_text().strip() if marker.is_file() else ""
        if current != digest:
            target = f"-r {_q(deps.name)}" if deps.name == "requirements.txt" else "."
            steps.append(f"echo [orquestador] Instalando dependencias ({deps.name}) ...")
            steps.append(f"{_q(str(py))} -m pip install --disable-pip-version-check {target}")
            steps.append(f"{_q(str(py))} -c \"open(r'{marker}','w').write('{digest}')\"")

    # Flask/uvicorn se lanzan como módulo: si no venían en las dependencias,
    # instalarlos para que el arranque no muera con "No module named ...".
    # Entre paréntesis: sin ellos el "||" se mezcla con los "&&" del resto
    # de la cadena y el arranque seguiría aunque pip hubiera fallado antes.
    if run_args.startswith("-m uvicorn"):
        null = "nul" if _IS_WINDOWS else "/dev/null"
        steps.append(f"({_q(str(py))} -c \"import uvicorn\" 2>{null} || "
                     f"{_q(str(py))} -m pip install --disable-pip-version-check uvicorn)")

    steps.append(f"{_q(str(py))} -u {run_args}")
    return {"ok": True, "cmd": " && ".join(steps), "label": label, "python": str(py)}
