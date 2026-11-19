"""Punto de entrada de la app desktop.

Arranca el mismo Flask de siempre (api/server.py) en un hilo y lo muestra en
una ventana nativa de Windows (WebView2) en vez de abrir el navegador. Así:
  - no hay consola que cerrar sin querer y matar el server con ella
  - no hay ventanas de PowerShell/UAC sueltas: el UAC salta UNA vez al abrir
    la app (si hace falta admin), después todo vive en esta sola ventana
  - "Liberar RAM" puede vaciar el standby list del sistema porque la app
    corre elevada de punta a punta
"""
import os
import sys
import ctypes
import platform
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_IS_WINDOWS = platform.system() == "Windows"
_PORT = 7433


def _is_admin() -> bool:
    if not _IS_WINDOWS:
        return hasattr(os, "geteuid") and os.geteuid() == 0
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _relaunch_as_admin():
    # ShellExecuteW con verbo "runas" dispara el UAC una sola vez al abrir la
    # app — a partir de ahí todo (server + ventana) corre ya elevado, sin
    # pedir de nuevo ni abrir ventanas extra.
    # Devuelve True solo si el relanzamiento elevado arrancó. Si el UAC se
    # cancela o la cuenta no es admin (ShellExecuteW <= 32), seguimos sin
    # elevar en vez de cerrar la app sin abrir nada.
    # En el .exe congelado sys.argv[0] ES el ejecutable: no repetirlo como arg.
    args = sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv
    params = " ".join(f'"{a}"' for a in args)
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    return rc > 32


def _log_crash(exc: BaseException) -> Path:
    import traceback
    log_path = Path(os.environ.get("TEMP", ".")) / "db-detector-crash.log"
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n--- crash ---\n")
            traceback.print_exception(type(exc), exc, exc.__traceback__, file=f)
    except Exception:
        pass
    return log_path


def _show_startup_error(log_path: Path):
    msg = (
        "No se pudo abrir la ventana de la app.\n\n"
        f"Detalle del error guardado en:\n{log_path}"
    )
    try:
        ctypes.windll.user32.MessageBoxW(None, msg, "DB-Detector — error al iniciar", 0x10)
    except Exception:
        pass


def _tray_icon_image():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, 60, 60), fill=(37, 99, 235, 255))
    draw.ellipse((20, 18, 44, 42), fill=(255, 255, 255, 255))
    draw.rectangle((28, 40, 36, 56), fill=(255, 255, 255, 255))
    return img


def _setup_tray(window):
    # Minimiza a la bandeja del sistema en vez de cerrar: al tocar la X se
    # oculta la ventana y el ícono queda en el tray (doble click / "Mostrar"
    # la recupera). "Salir" del menú sí cierra la app de verdad.
    import pystray

    state = {"icon": None, "tray_ok": False, "quitting": False}

    def restore(icon=None, item=None):
        window.show()
        try:
            window.restore()
        except Exception:
            pass

    def quit_app(icon, item):
        state["quitting"] = True
        state["stop"] = True
        try:
            icon.stop()
        except Exception:
            pass
        window.destroy()

    try:
        import tray_menu
        menu = tray_menu.build_menu(state, restore, quit_app)
    except Exception as exc:
        # Si el menú completo falla, al menos Mostrar/Salir.
        _log_crash(exc)
        tray_menu = None
        menu = pystray.Menu(
            pystray.MenuItem("Mostrar DB-Detector", restore, default=True),
            pystray.MenuItem("Salir", quit_app),
        )
    icon = pystray.Icon("db-detector", _tray_icon_image(), "DB-Detector — Orquestador", menu)
    state["icon"] = icon
    if tray_menu is not None:
        tray_menu.start_refresher(icon, state)

    def on_closing():
        # Con el ícono de bandeja vivo, la X solo oculta la ventana (el server
        # y los proyectos siguen). Si el ícono NO arrancó, ocultar dejaría el
        # proceso vivo sin ninguna forma de volver a él ni de cerrarlo —
        # entonces la X cierra de verdad.
        if state["quitting"] or not state["tray_ok"]:
            return True
        window.hide()
        return False

    def _run_tray():
        def _setup(ic):
            ic.visible = True
            state["tray_ok"] = True
        try:
            icon.run(setup=_setup)
        except Exception as exc:
            state["tray_ok"] = False
            _log_crash(exc)
        finally:
            state["tray_ok"] = False

    window.events.closing += on_closing
    threading.Thread(target=_run_tray, daemon=True).start()
    return icon


def _shutdown(icon=None):
    """Apaga todo al salir: los proyectos que arrancó la app (si no, quedan
    procesos huérfanos — ng serve, java, kafka — reteniendo puertos) y después
    termina el proceso a la fuerza. Sin os._exit, pythonnet/WinForms y algún
    hilo no-daemon dejan el .exe vivo en segundo plano sin ventana ni ícono."""
    try:
        if icon is not None:
            icon.stop()
    except Exception:
        pass
    try:
        from api.server import gestor
        t = threading.Thread(target=gestor.down_all, daemon=True)
        t.start()
        t.join(timeout=30)
    except Exception as exc:
        _log_crash(exc)
    os._exit(0)


def main():
    if _IS_WINDOWS and not _is_admin() and "--no-elevate" not in sys.argv:
        if _relaunch_as_admin():
            return

    # OJO: NO forzar PYTHONNET_RUNTIME=coreclr. El Microsoft.Web.WebView2.WinForms.dll
    # que empaqueta pywebview está compilado para .NET Framework 4.6.2 (netfx),
    # no para .NET 8. Si se fuerza CoreCLR, ese .dll referencia tipos de
    # System.Windows.Forms versión 4.0.0.0 (netfx) pero CoreCLR carga la versión
    # 8.0.0.0 (de Microsoft.WindowsDesktop.App) -> TypeLoadException al cargar
    # System.Windows.Forms.ContextMenu. pythonnet por default (sin indicar nada)
    # usa el CLR de .NET Framework que ya trae Windows, que es justo lo que este
    # .dll necesita.

    try:
        import webview
        from api.server import app, _ensure_single_instance, set_desktop_window

        _ensure_single_instance(_PORT)

        threading.Thread(
            target=lambda: app.run(host="127.0.0.1", port=_PORT, debug=False, use_reloader=False),
            daemon=True,
        ).start()

        window = webview.create_window(
            "DB-Detector — Orquestador",
            f"http://localhost:{_PORT}",
            width=1360, height=840, min_size=(960, 600),
        )
        set_desktop_window(window)
        icon = None
        try:
            icon = _setup_tray(window)
        except Exception as exc:
            _log_crash(exc)
        webview.start()
        # La ventana se cerró de verdad (Salir, o X sin bandeja): apagar todo.
        _shutdown(icon)
    except Exception as exc:
        log_path = _log_crash(exc)
        _show_startup_error(log_path)
        os._exit(1)


if __name__ == "__main__":
    main()
