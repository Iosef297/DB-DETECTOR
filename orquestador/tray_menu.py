"""Menú del ícono de bandeja: Liberar RAM + árbol carpeta → servicio con
acciones (iniciar / parar / reiniciar / abrir), igual que la barra lateral.

El menú es dinámico: pystray re-evalúa los callables cada vez que se llama a
icon.update_menu(), así que un hilo lo refresca cada pocos segundos para que
los estados (● RUNNING / ○ STOPPED) estén al día al abrirlo.

Todas las acciones corren en un hilo aparte (arrancar un java o kafka tarda)
y avisan el resultado con una notificación de Windows.
"""
import threading
import time
import webbrowser

import pystray
from pystray import Menu, MenuItem as Item

MISC = "Miscelaneo"
_STATUS_MARK = {"RUNNING": "●", "STARTING": "◐", "ERROR": "✕"}


def _gestor():
    from api.server import gestor
    return gestor


def _bg(icon, label, fn):
    """Corre fn() en segundo plano y notifica cómo terminó."""
    def run():
        try:
            res = fn() or {}
            if isinstance(res, dict) and (res.get("error") or res.get("ok") is False):
                msg = res.get("error") or "falló"
                if res.get("port_conflict"):
                    msg = f"puerto {res['port_conflict'].get('port')} ocupado"
                _notify(icon, f"{label}: {msg}")
            elif isinstance(res, dict) and res.get("starting"):
                _notify(icon, f"{label}: arrancando…")
            else:
                _notify(icon, f"{label}: listo")
        except Exception as e:
            _notify(icon, f"{label}: {e}")
        finally:
            try:
                icon.update_menu()
            except Exception:
                pass
    threading.Thread(target=run, daemon=True).start()


def _notify(icon, msg):
    try:
        icon.notify(msg, "DB-Detector")
    except Exception:
        pass


def _free_ram(icon):
    def run():
        from core.mem_tools import free_ram
        r = free_ram()
        if not r.get("ok"):
            _notify(icon, f"Liberar RAM: {r.get('error') or 'falló'}")
        else:
            _notify(icon, f"RAM libre: {r.get('free_mb_before')} MB → {r.get('free_mb_after')} MB")
    threading.Thread(target=run, daemon=True).start()


# ── Árbol de proyectos (misma agrupación que la UI) ──────────────────────────

def _tree(projects):
    """[(root|None, {carpeta: [proyectos]})]. Sueltos → "Miscelaneo"."""
    roots, flat, loose = {}, {}, []
    for p in projects:
        rg, g = p.get("root_group"), p.get("group_name")
        if rg:
            roots.setdefault(rg, {}).setdefault(g or MISC, []).append(p)
        elif g:
            flat.setdefault(g, []).append(p)
        else:
            loose.append(p)
    out = []
    if loose or flat:
        top = dict(flat)
        if loose:
            top[MISC] = loose
        out.append((None, top))
    for rg in sorted(roots):
        out.append((rg, roots[rg]))
    return out


def _sorted_folders(folders: dict):
    # Miscelaneo primero (infra suelta: kafka, keycloak...), luego alfabético
    return sorted(folders.items(), key=lambda kv: (kv[0] != MISC, kv[0].lower()))


def _service_menu(icon, p):
    name = p["name"]
    running = p.get("status") == "RUNNING"
    port = p.get("app_port")
    g = _gestor
    items = [
        Item("Iniciar", lambda: _bg(icon, f"Iniciar {name}", lambda: g().up(name)), enabled=not running),
        Item("Parar", lambda: _bg(icon, f"Parar {name}", lambda: g().down(name)), enabled=running),
        Item("Reiniciar", lambda: _bg(icon, f"Reiniciar {name}", lambda: g().restart(name))),
    ]
    if port:
        items.append(Item(f"Abrir http://localhost:{port}",
                          lambda: webbrowser.open(f"http://localhost:{port}/"), enabled=running))
    mark = _STATUS_MARK.get(p.get("status"), "○")
    return Item(f"{mark} {name}", Menu(*items))


def _folder_actions(icon, label, members, real_group):
    """Iniciar/Parar de una carpeta. Si es un grupo real de la BD usa
    start_group/stop_group (respetan orden infra → backend → frontend); si es
    "Miscelaneo" (virtual) va proyecto por proyecto."""
    g = _gestor
    names = [p["name"] for p in sorted(
        members, key=lambda p: ((p.get("detection") or {}).get("infra_rank", 9), p["name"]))]
    if real_group:
        start = lambda: g().start_group(real_group)
        stop = lambda: g().stop_group(real_group)
    else:
        def start():
            errs = [n for n in names if not g().up(n).get("ok")]
            return {"ok": not errs, "error": f"fallaron: {', '.join(errs)}" if errs else None}

        def stop():
            for n in reversed(names):
                g().down(n)
            return {"ok": True}
    return [
        Item(f"Iniciar {label}", lambda: _bg(icon, f"Iniciar {label}", start)),
        Item(f"Parar {label}", lambda: _bg(icon, f"Parar {label}", stop)),
        Menu.SEPARATOR,
    ]


def _folder_menu(icon, folder, members, real_group):
    running = sum(1 for p in members if p.get("status") == "RUNNING")
    items = _folder_actions(icon, "carpeta", members, real_group)
    items += [_service_menu(icon, p) for p in sorted(members, key=lambda p: p["name"])]
    return Item(f"{folder}  ({running}/{len(members)})", Menu(*items))


def _projects_items(icon):
    try:
        projects = _gestor().list_projects()
    except Exception:
        return [Item("(no se pudo leer proyectos)", None, enabled=False)]
    if not projects:
        return [Item("(sin proyectos)", None, enabled=False)]
    items = []
    for root, folders in _tree(projects):
        folder_items = [
            _folder_menu(icon, f, ms, None if f == MISC else f)
            for f, ms in _sorted_folders(folders)
        ]
        if root is None:
            items += folder_items
        else:
            all_members = [p for ms in folders.values() for p in ms]
            running = sum(1 for p in all_members if p.get("status") == "RUNNING")
            sub = _folder_actions(icon, "todo", all_members, root) + folder_items
            items.append(Item(f"{root}  ({running}/{len(all_members)})", Menu(*sub)))
    return items


def build_menu(icon_ref, restore, quit_app):
    """icon_ref: dict con "icon" (el pystray.Icon se crea después del menú)."""
    def dynamic():
        icon = icon_ref["icon"]
        # Windows abre cada submenú hacia abajo si cabe y hacia arriba si no
        # (no hay API para forzar la dirección). Con el ícono anclado en la
        # barra de tareas el menú queda pegado al borde y los submenús suben.
        yield Item("Mostrar DB-Detector", restore, default=True)
        yield Item("Liberar RAM", lambda: _free_ram(icon))
        yield Menu.SEPARATOR
        yield from _projects_items(icon)
        yield Menu.SEPARATOR
        yield Item("Salir", quit_app)
    return Menu(dynamic)


def start_refresher(icon, stop_flag: dict, every: float = 5.0):
    """Refresca el menú periódicamente para que los estados estén al día."""
    def loop():
        while not stop_flag.get("stop"):
            time.sleep(every)
            try:
                icon.update_menu()
            except Exception:
                pass
    threading.Thread(target=loop, daemon=True).start()
