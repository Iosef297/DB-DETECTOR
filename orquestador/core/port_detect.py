"""Detección del puerto "natural" de un proyecto: el que el propio proyecto
declara en sus archivos (application.yml, config-repo del Config Server,
.env, package.json, vite.config, app.listen(...), uvicorn.run(...), etc.).

Por qué importa: todo lo que rodea a una app (redirect URIs de Keycloak,
environment.ts del frontend, rutas del gateway, CORS) suele traer ese puerto
hardcodeado. Si el orquestador le asigna otro del pool genérico, la app puede
incluso arrancar bien, pero el resto del sistema la busca en otro lado.

Prioridad general (la primera que encuentre gana):
  1. flag explícito en el comando de arranque (--port / -p)
  2. config del framework (application-<perfil>.yml, vite.config, código...)
  3. .env del proyecto (PORT, SERVER_PORT, APP_PORT...)
  4. puerto por defecto, solo para dev servers que IGNORAN la variable PORT
     (Angular 4200, Vite 5173): a los que sí la leen el pool les sirve.
"""
import json
import os
import re

# ── .env ──────────────────────────────────────────────────────────────────────

_ENV_FILES = (".env.local", ".env.development.local", ".env.development",
              ".env.dev", ".env")
_ENV_PORT_KEYS = ("SERVER_PORT", "PORT", "APP_PORT", "HTTP_PORT", "VITE_PORT",
                  "DEV_PORT", "API_PORT", "NG_PORT")


def read_env_vars(path: str) -> dict:
    """Variables de los .env del proyecto. Los más específicos (.env.local,
    .env.development) pisan al .env genérico."""
    merged = {}
    for fname in reversed(_ENV_FILES):
        fpath = os.path.join(path, fname)
        if not os.path.isfile(fpath):
            continue
        try:
            with open(fpath, encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = re.match(r'^\s*(?:export\s+)?([A-Za-z_][\w.]*)\s*=\s*(.*?)\s*$', line)
                    if m:
                        val = m.group(2).split(" #", 1)[0].strip().strip('"\'')
                        merged[m.group(1)] = val
        except Exception:
            continue
    return merged


def _env_port(env: dict, keys=_ENV_PORT_KEYS) -> int | None:
    for k in keys:
        v = env.get(k, "")
        if v.isdigit() and 1 <= int(v) <= 65535:
            return int(v)
    return None


def _resolve_placeholder(var: str, default: str | None, env: dict) -> int | None:
    """${VAR:default} — si el .env define VAR, manda; si no, el default."""
    v = env.get(var)
    if v and v.isdigit():
        return int(v)
    if default and default.isdigit():
        return int(default)
    return None


def _read(fpath: str) -> str:
    try:
        with open(fpath, encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


# ── Spring Boot ───────────────────────────────────────────────────────────────

_SPRING_DIRS = ("", os.path.join("src", "main", "resources"), "config")


def spring_port_in_text(text: str, env: dict | None = None) -> int | None:
    """server.port de un application.properties / .yml (con o sin ${VAR:def})."""
    env = env or {}
    m = re.search(r'^\s*server\.port\s*[=:]\s*\$\{([^:}]+)(?::(\d+))?\}', text, re.MULTILINE)
    if m:
        return _resolve_placeholder(m.group(1), m.group(2), env)
    m = re.search(r'^\s*server\.port\s*[=:]\s*["\']?(\d+)["\']?\s*$', text, re.MULTILINE)
    if m:
        return int(m.group(1))
    m = re.search(r'^server:[ \t]*\n((?:[ \t]+.*\n?|[ \t]*\n)*)', text, re.MULTILINE)
    if m:
        block = m.group(1)
        pm = re.search(r'^[ \t]+port:\s*["\']?\$\{([^:}]+)(?::(\d+))?\}', block, re.MULTILINE)
        if pm:
            return _resolve_placeholder(pm.group(1), pm.group(2), env)
        pm = re.search(r'^[ \t]+port:\s*["\']?(\d+)["\']?\s*$', block, re.MULTILINE)
        if pm:
            return int(pm.group(1))
    return None


def spring_app_name_profile(text: str):
    """(spring.application.name, perfil activo) de un application.yml/properties."""
    name = profile = None
    m = re.search(r'^\s*spring\.application\.name\s*[=:]\s*(\S+)', text, re.MULTILINE)
    if m:
        name = m.group(1)
    else:
        m = re.search(r'^\s+application:\s*\n\s+name:\s*["\']?([\w.-]+)', text, re.MULTILINE)
        if m:
            name = m.group(1)
    m = re.search(r'^\s*spring\.profiles\.active\s*[=:]\s*["\']?\$?\{?(?:[^:}]+:)?([\w-]+)', text, re.MULTILINE)
    if not m:
        m = re.search(r'^\s+profiles:\s*\n\s+active:\s*["\']?\$?\{?(?:[^:}]+:)?([\w-]+)', text, re.MULTILINE)
    if m:
        profile = m.group(1)
    return name, profile


def _spring_files(path: str, base: str, profile: str | None):
    """Rutas de <base>-<perfil>.* y <base>.* en las carpetas estándar de Spring,
    el del perfil primero (pisa al genérico, igual que hace Spring)."""
    out = []
    names = []
    if profile:
        names += [f"{base}-{profile}.{e}" for e in ("yml", "yaml", "properties")]
    names += [f"{base}.{e}" for e in ("yml", "yaml", "properties")]
    for n in names:
        for d in _SPRING_DIRS:
            fpath = os.path.join(path, d, n)
            if os.path.isfile(fpath):
                out.append(fpath)
    return out


_CONFIG_REPO_SKIP = {"node_modules", "target", "build", ".git", ".idea", ".claude",
                     ".mvn", "src", "dist", ".venv", "venv", "__pycache__", ".gradle"}


def config_repo_port(project_path: str, app_name: str, profile: str | None,
                     env: dict | None = None) -> int | None:
    """Spring Cloud Config Server (backend native o un clon del repo git):
    busca <app>-<perfil>.yml / <app>.yml en carpetas del monorepo (subiendo
    hasta 2 niveles: monorepo/services/<ms> → monorepo/) y saca server.port."""
    names = []
    if profile:
        names += [f"{app_name}-{profile}.{e}" for e in ("yml", "yaml", "properties")]
    names += [f"{app_name}.{e}" for e in ("yml", "yaml", "properties")]
    project_abs = os.path.abspath(project_path)
    root = os.path.dirname(os.path.dirname(project_abs))
    base_depth = root.rstrip(os.sep).count(os.sep)
    found = {}
    for cur, dirs, files in os.walk(root):
        if cur.rstrip(os.sep).count(os.sep) - base_depth >= 4:
            dirs[:] = []
        dirs[:] = [d for d in dirs if d not in _CONFIG_REPO_SKIP]
        # El propio proyecto ya se revisó (src/main/resources se salta igual).
        if os.path.abspath(cur) == project_abs:
            continue
        for n in names:
            if n in files and n not in found:
                found[n] = os.path.join(cur, n)
    for n in names:
        if n in found:
            port = spring_port_in_text(_read(found[n]), env)
            if port:
                return port
    return None


def _spring_port(path: str, env: dict) -> int | None:
    base_texts = [_read(f) for f in _spring_files(path, "application", None)]
    base_texts += [_read(f) for f in _spring_files(path, "bootstrap", None)]
    name = profile = None
    for t in base_texts:
        n, p = spring_app_name_profile(t)
        name = name or n
        profile = profile or p
    # SPRING_PROFILES_ACTIVE del .env pisa al del yml
    profile = env.get("SPRING_PROFILES_ACTIVE", "").split(",")[0].strip() or profile

    # 1. application-<perfil>.yml local, luego application.yml / bootstrap.yml
    for fpath in _spring_files(path, "application", profile) + _spring_files(path, "bootstrap", profile):
        port = spring_port_in_text(_read(fpath), env)
        if port:
            return port
    # 2. Config Server: el puerto vive en el config-repo
    if name and any("configserver" in t or "spring.cloud.config" in t or "cloud:" in t
                    for t in base_texts):
        port = config_repo_port(path, name, profile, env)
        if port:
            return port
    # 3. .env (spring-dotenv / docker env_file)
    return _env_port(env, ("SERVER_PORT",))


# ── Node / JS ─────────────────────────────────────────────────────────────────

# Dev servers que NO leen la variable de entorno PORT: hay que pasarles --port
# explícito, y si no lo traen, su puerto por defecto es fijo.
JS_DEV_SERVER_DEFAULTS = (("ng serve", 4200), ("vite", 5173), ("vue-cli-service serve", 8080),
                          ("astro dev", 4321), ("webpack serve", 8080),
                          ("webpack-dev-server", 8080))
_FLAG_PORT_RE = re.compile(r'(?:--port|-p)[=\s]+(\d{2,5})\b')


def npm_script(path: str, cmd: str | None = None) -> str:
    """Contenido del script de package.json que corre `cmd` ("npm run start",
    "npm start", "npm run dev"...). Sin cmd: start, o dev si no hay start."""
    try:
        with open(os.path.join(path, "package.json"), encoding="utf-8") as f:
            scripts = (json.load(f).get("scripts") or {})
    except Exception:
        return ""
    if cmd:
        m = re.match(r'^\s*(?:npm|pnpm|yarn|bun)(?:\s+run)?\s+([\w:.-]+)', cmd)
        if not m:
            return ""
        name = m.group(1)
    else:
        name = "dev" if "dev" in scripts and "start" not in scripts else "start"
    return scripts.get(name) or ""


def js_dev_server_kind(script: str):
    """(nombre, puerto_por_defecto) si el script es un dev server que ignora PORT."""
    for needle, default in JS_DEV_SERVER_DEFAULTS:
        if needle in script:
            return needle, default
    return None


def _vite_config_port(path: str, env: dict) -> int | None:
    for n in ("vite.config.ts", "vite.config.js", "vite.config.mjs", "vite.config.mts"):
        text = _read(os.path.join(path, n))
        if not text:
            continue
        m = re.search(r'server\s*:\s*\{[^}]*?\bport\s*:\s*(\d{2,5})', text, re.DOTALL)
        if m:
            return int(m.group(1))
        # port: Number(process.env.VITE_PORT) || 3000  /  env.PORT ?? 3000
        m = re.search(r'server\s*:\s*\{[^}]*?\bport\s*:[^,}\n]*?(?:\|\||\?\?)\s*(\d{2,5})', text, re.DOTALL)
        if m:
            return _env_port(env) or int(m.group(1))
    return None


def _angular_json_port(path: str) -> int | None:
    text = _read(os.path.join(path, "angular.json"))
    m = re.search(r'"port"\s*:\s*(\d{2,5})', text)
    return int(m.group(1)) if m else None


_NODE_ENTRY_CANDIDATES = ("server.js", "app.js", "index.js", "main.js", "server.ts", "app.ts",
                          "index.ts", "main.ts",
                          os.path.join("src", "server.js"), os.path.join("src", "app.js"),
                          os.path.join("src", "index.js"), os.path.join("src", "main.ts"),
                          os.path.join("src", "server.ts"), os.path.join("src", "index.ts"),
                          os.path.join("src", "app.ts"),
                          os.path.join("bin", "www"))


def _node_code_port(path: str, env: dict) -> tuple[int | None, bool]:
    """(puerto, respeta_PORT) leído del código del servidor Node.
    respeta_PORT=False si es un .listen(3000) literal — ahí PORT no sirve."""
    for rel in _NODE_ENTRY_CANDIDATES:
        text = _read(os.path.join(path, rel))
        if not text:
            continue
        # const PORT = process.env.PORT || 3000  /  ?? 3000
        m = re.search(r'process\.env\.(\w+)\s*(?:\|\||\?\?)\s*["\']?(\d{2,5})', text)
        if m:
            v = env.get(m.group(1), "")
            return (int(v) if v.isdigit() else int(m.group(2))), True
        # app.listen(3000)  /  server.listen(3000, ...)  /  listen({ port: 3000 })
        m = re.search(r'\.listen\(\s*(\d{2,5})\b', text) or \
            re.search(r'\.listen\(\s*\{[^}]*port\s*:\s*(\d{2,5})', text)
        if m:
            return int(m.group(1)), False
        # const PORT = 3000; ... listen(PORT)
        m = re.search(r'(?:const|let|var)\s+(\w*port\w*)\s*=\s*(\d{2,5})\b', text, re.IGNORECASE)
        if m and re.search(r'listen\(\s*' + re.escape(m.group(1)) + r'\b', text):
            return int(m.group(2)), False
    return None, True


def node_port(path: str, cmd: str | None = None, env: dict | None = None) -> int | None:
    env = env if env is not None else read_env_vars(path)
    script = npm_script(path, cmd)
    m = _FLAG_PORT_RE.search(script or "")
    if m:
        return int(m.group(1))
    kind = js_dev_server_kind(script)
    if kind and kind[0] == "ng serve":
        return _angular_json_port(path) or _env_port(env, ("NG_PORT", "PORT")) or kind[1]
    if kind and kind[0] == "vite":
        return _vite_config_port(path, env) or _env_port(env, ("VITE_PORT", "PORT")) or kind[1]
    if kind:
        return _env_port(env) or kind[1]
    port, _ = _node_code_port(path, env)
    return port or _env_port(env)


# ── Python ────────────────────────────────────────────────────────────────────

def _python_port(path: str, env: dict) -> int | None:
    for rel in ("main.py", "app.py", "run.py", "server.py", "wsgi.py", "asgi.py",
                os.path.join("app", "main.py"), os.path.join("src", "main.py")):
        text = _read(os.path.join(path, rel))
        if not text:
            continue
        # uvicorn.run(app, port=8001) / app.run(port=5001)
        m = re.search(r'\.run\([^)]*\bport\s*=\s*(\d{2,5})', text, re.DOTALL)
        if m:
            return int(m.group(1))
        # port=int(os.getenv("PORT", 8001)) / os.environ.get("PORT", "8001")
        m = re.search(r'(?:getenv|environ\.get)\(\s*["\'](\w+)["\']\s*,\s*["\']?(\d{2,5})', text)
        if m:
            v = env.get(m.group(1), "")
            return int(v) if v.isdigit() else int(m.group(2))
    for rel in ("Procfile", "start.sh", "run.sh"):
        m = _FLAG_PORT_RE.search(_read(os.path.join(path, rel)))
        if m:
            return int(m.group(1))
    return _env_port(env)


# ── Go / otros ────────────────────────────────────────────────────────────────

def _go_port(path: str, env: dict) -> int | None:
    for rel in ("main.go", os.path.join("cmd", "main.go"), os.path.join("cmd", "server", "main.go")):
        text = _read(os.path.join(path, rel))
        m = re.search(r'(?:ListenAndServe|\.Run|\.Start|\.Listen)\(\s*"[\w.]*:(\d{2,5})"', text)
        if m:
            return int(m.group(1))
    return _env_port(env)


# ── Entrada principal ─────────────────────────────────────────────────────────

def detect_natural_port(path: str, framework: str | None, cmd: str | None = None) -> int | None:
    """Puerto que el propio proyecto declara. None si no hay forma de saberlo
    (y entonces el orquestador usa uno del pool, pasándolo por PORT/--port)."""
    if not path or not os.path.isdir(path):
        return None
    env = read_env_vars(path)
    if cmd:
        m = _FLAG_PORT_RE.search(cmd)
        if m:
            return int(m.group(1))
    if framework == "springboot":
        return _spring_port(path, env)
    if framework in ("django", "python"):
        return _python_port(path, env)
    if framework == "go":
        return _go_port(path, env)
    if os.path.isfile(os.path.join(path, "package.json")):
        return node_port(path, cmd, env)
    if framework in ("laravel", "rails"):
        return _env_port(env, ("APP_PORT", "PORT", "SERVER_PORT"))
    return _env_port(env)


def node_ignores_port_env(path: str, cmd: str | None) -> bool:
    """True si el proceso Node NO va a leer la variable PORT: dev servers tipo
    ng/vite (hay que pasarles --port) o un .listen(3000) literal en el código
    (ahí no hay forma de cambiarlo: el orquestador debe usar ese puerto)."""
    script = npm_script(path, cmd)
    if js_dev_server_kind(script):
        return True
    _, respects = _node_code_port(path, read_env_vars(path))
    return not respects
