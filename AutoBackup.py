import flet as ft
from flet import Icons
import os
import time
import json
import sys
import threading
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive

# Directorios base
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

BASE_APP_DATA_DIR = os.path.join(os.path.expanduser("~"), "AppData", "Local", "AutoBackup SISMED")
APP_DATA_DIR = os.path.realpath(BASE_APP_DATA_DIR)
os.makedirs(APP_DATA_DIR, exist_ok=True)
CONFIG_FILE = os.path.join(APP_DATA_DIR, "autobackup_config.json")
ERROR_LOG_FILE = os.path.join(APP_DATA_DIR, "autobackup_errors.log")
CREDENTIALS_FILE = os.path.join(APP_DATA_DIR, "credentials.json")
PENDING_UPLOAD_FILE = os.path.join(APP_DATA_DIR, "pending_upload.json")
PENDING_RETRY_SECONDS = 60
SETTINGS_FILE = os.path.join(APP_DIR, "settings.yaml")
CLIENT_SECRETS_PATH = os.path.join(APP_DIR, "client_secrets.json")


def log_error(message: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(ERROR_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")
    except Exception:
        pass


def ensure_plain_file(path: str, context: str = "archivo"):
    try:
        if os.path.exists(path) and os.path.islink(path):
            os.remove(path)
            log_error(f"{context} era un enlace simbólico y se regenerará: {path}")
            return True
    except Exception as err:
        log_error(f"No se pudo limpiar enlace simbólico ({path}): {err}")
    return False


def load_pending_upload():
    try:
        if os.path.exists(PENDING_UPLOAD_FILE):
            with open(PENDING_UPLOAD_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("path"):
                    return data
    except Exception as err:
        log_error(f"Error leyendo pending_upload.json: {err}")
    return None


def save_pending_upload(file_path, in_progress=False):
    payload = {
        "path": file_path,
        "timestamp": datetime.utcnow().isoformat(),
        "in_progress": in_progress,
        "last_attempt_at": None,
    }
    try:
        with open(PENDING_UPLOAD_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception as err:
        log_error(f"Error guardando pending_upload.json: {err}")


def mark_pending_in_progress(flag):
    data = load_pending_upload()
    if not data:
        return
    data["in_progress"] = flag
    data["last_attempt_at"] = datetime.utcnow().isoformat()
    try:
        with open(PENDING_UPLOAD_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as err:
        log_error(f"Error actualizando pending_upload.json: {err}")


def clear_pending_upload():
    try:
        if os.path.exists(PENDING_UPLOAD_FILE):
            os.remove(PENDING_UPLOAD_FILE)
    except Exception as err:
        log_error(f"Error eliminando pending_upload.json: {err}")


def clear_pending_if_matches(file_path):
    data = load_pending_upload()
    if data and data.get("path") == file_path:
        clear_pending_upload()


def normalize_pending_record(max_lock_seconds=PENDING_RETRY_SECONDS * 2):
    data = load_pending_upload()
    if not data:
        return None
    if data.get("in_progress"):
        reset_flag = False
        last_attempt = data.get("last_attempt_at")
        if not last_attempt:
            reset_flag = True
        else:
            try:
                last_dt = datetime.fromisoformat(last_attempt)
                if (datetime.utcnow() - last_dt).total_seconds() > max_lock_seconds:
                    reset_flag = True
            except ValueError:
                reset_flag = True
        if reset_flag:
            mark_pending_in_progress(False)
            data = load_pending_upload()
    return data


# -----------------------
# Google Drive Manager
# -----------------------
class DriveUploader:
    def __init__(self):
        self.gauth = GoogleAuth(settings_file=SETTINGS_FILE)
        self.drive = None
        self.folder_id = None
        self.last_deleted = 0
        self.credentials_path = CREDENTIALS_FILE
        self.gauth.settings["save_credentials_file"] = self.credentials_path

    def authenticate(self):
        credentials_loaded = False
        ensure_plain_file(self.credentials_path, "Credenciales")
        if os.path.exists(self.credentials_path):
            try:
                self.gauth.LoadCredentialsFile(self.credentials_path)
                if self.gauth.credentials:
                    credentials_loaded = True
                    if self.gauth.access_token_expired:
                        self.gauth.Refresh()
                    self.gauth.Authorize()
            except Exception as auth_err:
                log_error(f"Error cargando credenciales guardadas: {auth_err}")
                credentials_loaded = False

        if not credentials_loaded:
            self.gauth.LocalWebserverAuth()  # Abre navegador para login 1 sola vez

        self._save_credentials_safe()
        self.drive = GoogleDrive(self.gauth)

    def _save_credentials_safe(self):
        ensure_plain_file(self.credentials_path, "Credenciales")
        try:
            self.gauth.SaveCredentialsFile(self.credentials_path)
        except Exception as save_err:
            if "symbolic link" in str(save_err).lower():
                ensure_plain_file(self.credentials_path, "Credenciales")
                self.gauth.SaveCredentialsFile(self.credentials_path)
            else:
                raise

    def get_or_create_folder(self, folder_name):
        """Busca o crea una carpeta en Drive y devuelve su ID"""
        if self.drive is None:
            raise RuntimeError("Google Drive no autenticado")
        
        # Buscar si ya existe la carpeta
        query = f"title='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        file_list = self.drive.ListFile({'q': query}).GetList()
        
        if file_list:
            self.folder_id = file_list[0]['id']
            return file_list[0]['id']
        else:
            # Crear nueva carpeta
            folder_metadata = {
                'title': folder_name,
                'mimeType': 'application/vnd.google-apps.folder'
            }
            folder = self.drive.CreateFile(folder_metadata)
            folder.Upload()
            self.folder_id = folder['id']
            return folder['id']

    def upload_file(self, file_path):
        if self.drive is None:
            raise RuntimeError("Google Drive no autenticado")
        
        file_metadata = {'title': os.path.basename(file_path)}
        
        # Si hay carpeta configurada, subir ahí
        if self.folder_id:
            file_metadata['parents'] = [{'id': self.folder_id}]
        
        file_drive = self.drive.CreateFile(file_metadata)
        file_drive.SetContentFile(file_path)
        file_drive.Upload()
        self.last_deleted = 0

        if self.folder_id:
            query = f"'{self.folder_id}' in parents and trashed=false"
            existing_files = self.drive.ListFile({'q': query}).GetList()
            for existing in existing_files:
                if existing['id'] != file_drive['id']:
                    existing.Delete()
                    self.last_deleted += 1
        return True


# -----------------------
# File Watcher
# -----------------------
class ZipHandler(FileSystemEventHandler):
    def __init__(self, uploader, log_callback, success_callback=None, pending_failure_callback=None, pending_clear_callback=None):
        self.uploader = uploader
        self.log_callback = log_callback
        self.success_callback = success_callback
        self.pending_failure_callback = pending_failure_callback
        self.pending_clear_callback = pending_clear_callback

    def on_created(self, event):
        if event.is_directory:
            return

        if event.src_path.lower().endswith(".zip"):
            self.log_callback(f"Nuevo archivo detectado: {event.src_path}")

            try:
                self._wait_for_file_ready(event.src_path)
                self._upload_with_retry(event.src_path)
                self.log_callback(f"✓ Subido a Drive: {os.path.basename(event.src_path)}")
                if self.uploader.last_deleted:
                    self.log_callback(
                        f"ℹ Se eliminó {self.uploader.last_deleted} archivo(s) previo(s) en Drive para conservar solo el más reciente."
                    )
                if self.pending_clear_callback:
                    self.pending_clear_callback(event.src_path)
                if self.success_callback:
                    self.success_callback(event.src_path)
            except TimeoutError as wait_err:
                self.log_callback(f"✗ {wait_err}")
            except Exception as e:
                self.log_callback(f"✗ Error subiendo archivo: {e}")
                if self.pending_failure_callback:
                    self.pending_failure_callback(event.src_path, e)
    
    def on_modified(self, event):
        # Detectar modificaciones también (útil si el archivo se está escribiendo)
        if event.is_directory:
            return
        
        if event.src_path.lower().endswith(".zip"):
            # Solo registrar, no subir en modificación
            pass

    def _wait_for_file_ready(self, file_path, timeout=90, interval=1, stable_checks=2):
        start_time = time.time()
        last_size = -1
        stable_count = 0
        self.log_callback("Esperando que el archivo termine de generarse...")

        while time.time() - start_time <= timeout:
            try:
                current_size = os.path.getsize(file_path)
                # Intentar abrir el archivo en modo lectura para confirmar que no esté bloqueado
                with open(file_path, "rb"):
                    pass
            except (FileNotFoundError, PermissionError):
                current_size = -1

            if current_size == last_size and current_size > 0:
                stable_count += 1
                if stable_count >= stable_checks:
                    return True
            else:
                stable_count = 0

            last_size = current_size
            time.sleep(interval)

        raise TimeoutError("El archivo no se liberó a tiempo para subirlo.")

    def _upload_with_retry(self, file_path, retries=3, delay=2):
        last_error = None
        backoff = delay
        for attempt in range(1, retries + 1):
            try:
                time.sleep(0.5)
                self.uploader.upload_file(file_path)
                return
            except PermissionError as err:
                last_error = err
                self.log_callback(
                    f"Archivo en uso, nuevo intento en {backoff}s (intento {attempt}/{retries})"
                )
                time.sleep(backoff)
            except Exception as err:
                last_error = err
                msg = (
                    f"Error conectando con Drive: {err}. Reintento en {backoff}s "
                    f"(intento {attempt}/{retries})."
                )
                self.log_callback(msg)
                log_error(f"Drive upload failed for {file_path}: {err}")
                time.sleep(backoff)
            finally:
                backoff *= 2
        raise last_error


# -----------------------
# Interfaz Flet
# -----------------------
def main(page: ft.Page):
    page.title = "Monitor de backups SISMED → Google Drive"
    page.window_width = 460
    page.window_height = 500
    page.bgcolor = "#0f172a"
    page.padding = 0
    page.horizontal_alignment = "center"
    page.vertical_alignment = "center"
    
    # Iniciar oculta
    page.window_visible = False
    page.window_prevent_close = True

    def save_config(folder_path=None, drive_folder_name=None):
        try:
            config = {}
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    config = json.load(f)
            
            if folder_path is not None:
                config["last_folder"] = folder_path
            if drive_folder_name is not None:
                config["drive_folder_name"] = drive_folder_name
            
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f)
        except Exception as e:
            print(f"Error guardando config: {e}")

    def load_config():
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("last_folder"), data.get("drive_folder_name", "Backups SISMED")
        except Exception as e:
            print(f"Error cargando config: {e}")
        return None, "Backups SISMED"

    # Estado
    selected_folder = ft.Text("Carpeta no seleccionada", size=14, color="#94a3b8", italic=True)
    log_output = ft.Text("", selectable=True, size=13, color="#cbd5e1")
    monitoring = False
    observer = None
    pending_retry_timer = None
    pending_retry_lock = threading.Lock()
    pending_retry_enabled = False
    selected_folder_path, drive_folder_name = load_config()
    drive_folder_text = ft.Text(drive_folder_name or "Backups SISMED", size=13, color="#cbd5e1")
    status_icon = ft.Icon(Icons.CIRCLE, color="#94a3b8", size=10)
    status_text = ft.Text("Inactivo", size=12, weight="w600", color="#cbd5e1")
    last_upload_text = ft.Text("Última subida: --", size=12, color="#94a3b8")
    client_secret_exists = os.path.exists(CLIENT_SECRETS_PATH)
    
    drive_icon_container = ft.Container(
        content=ft.Icon(Icons.CIRCLE, color="#64748b", size=10),
        bgcolor=None,
        border_radius=50,
        padding=0,
    )
    
    drive_button = ft.ElevatedButton(
        content=ft.Row([
            drive_icon_container,
            ft.Text("Conectar Drive", size=13, weight="w500"),
        ], spacing=8, tight=True),
        on_click=None,  # Se asigna después
        height=40,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=10),
            bgcolor="#1e293b",
        ),
    )
    drive_button.disabled = not client_secret_exists
    
    start_button = ft.FilledButton(
        "Iniciar monitoreo",
        icon=Icons.PLAY_CIRCLE,
        on_click=None,  # Se asigna después
        expand=True,
        height=45,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=10),
        ),
    )
    
    stop_button = ft.OutlinedButton(
        "Detener",
        icon=Icons.STOP_CIRCLE,
        on_click=None,  # Se asigna después
        expand=True,
        height=45,
        disabled=True,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=10),
            side=ft.BorderSide(2, "#475569"),
        ),
    )
    
    status_badge = ft.Container(
        content=ft.Row([
            status_icon,
            status_text,
        ], spacing=8, tight=True),
        bgcolor="#334155",
        border_radius=20,
        padding=ft.Padding(14, 8, 14, 8),
        border=ft.border.all(2, "#475569"),
    )

    warning_banner = ft.Container(
        visible=not client_secret_exists,
        bgcolor="#b4530940",
        border_radius=12,
        padding=ft.Padding(16, 12, 16, 12),
        content=ft.Row([
            ft.Icon(Icons.WARNING_ROUNDED, color="#f97316"),
            ft.Text(
                "Falta client_secrets.json. Copia el archivo de credenciales para poder conectar Drive.",
                size=13,
                color="#fde68a",
                expand=True,
            ),
        ], spacing=12, vertical_alignment="center"),
    )

    def set_drive_button_state(connected=False):
        if connected:
            drive_icon_container.content.color = "#10b981"
            drive_icon_container.bgcolor = "#10b98130"
            drive_icon_container.padding = ft.Padding(6, 6, 6, 6)
            drive_button.content.controls[1].value = "Drive conectado"
            drive_button.style.bgcolor = "#06573320"
            drive_button.disabled = True
        page.update()
    
    def update_monitoring_buttons(is_monitoring):
        start_button.disabled = is_monitoring
        stop_button.disabled = not is_monitoring
        page.update()

    def set_status_badge(message, bg_color="#334155", icon_color="#94a3b8", border_color="#475569"):
        status_text.value = message
        status_badge.bgcolor = bg_color
        status_icon.color = icon_color
        status_badge.border = ft.border.all(2, border_color)
        page.update()

    def refresh_status_badge():
        pending = normalize_pending_record()
        if pending:
            set_status_badge("Pendiente por subir", "#4c1d95", "#c4b5fd", "#9333ea")
        elif monitoring:
            set_status_badge("Activo", "#064e3b", "#34d399", "#10b981")
        else:
            set_status_badge("Inactivo", "#334155", "#94a3b8", "#475569")

    drive_uploader = DriveUploader()

    def schedule_pending_retry(delay=PENDING_RETRY_SECONDS):
        nonlocal pending_retry_timer
        if not pending_retry_enabled:
            return
        with pending_retry_lock:
            if pending_retry_timer:
                pending_retry_timer.cancel()
            pending_retry_timer = threading.Timer(delay, process_pending_upload)
            pending_retry_timer.daemon = True
            pending_retry_timer.start()

    def stop_pending_retry_loop():
        nonlocal pending_retry_timer, pending_retry_enabled
        pending_retry_enabled = False
        with pending_retry_lock:
            if pending_retry_timer:
                pending_retry_timer.cancel()
                pending_retry_timer = None

    def process_pending_upload():
        nonlocal pending_retry_timer
        with pending_retry_lock:
            pending_retry_timer = None

        if not pending_retry_enabled:
            return

        pending = normalize_pending_record()
        if not pending:
            schedule_pending_retry()
            return

        file_path = pending.get("path")
        if not file_path or not os.path.exists(file_path):
            clear_pending_upload()
            append_log("ℹ Archivo pendiente ya no existe, se limpia la cola.")
            refresh_status_badge()
            schedule_pending_retry()
            return

        if drive_uploader.drive is None:
            append_log("ℹ Hay un archivo pendiente. Esperando conexión a Drive...")
            schedule_pending_retry()
            return

        if pending.get("in_progress"):
            schedule_pending_retry()
            return

        mark_pending_in_progress(True)
        handler = ZipHandler(drive_uploader, append_log, update_last_upload)
        append_log(f"🔁 Reintentando subir archivo pendiente: {os.path.basename(file_path)}")
        try:
            handler._wait_for_file_ready(file_path)
            handler._upload_with_retry(file_path)
            clear_pending_if_matches(file_path)
            update_last_upload(file_path)
            append_log("✓ Archivo pendiente subido correctamente.")
            refresh_status_badge()
        except Exception as err:
            append_log(f"✗ Error reintentando archivo pendiente: {err}")
            log_error(f"Reintento de archivo pendiente falló ({file_path}): {err}")
        finally:
            if load_pending_upload():
                mark_pending_in_progress(False)
            if pending_retry_enabled:
                schedule_pending_retry()

    def handle_pending_failure(file_path, error):
        save_pending_upload(file_path)
        log_error(f"Archivo pendiente por subir ({file_path}): {error}")
        append_log(
            f"⚠ Archivo en cola para reintento: {os.path.basename(file_path)}. Motivo: {error}"
        )
        refresh_status_badge()
        schedule_pending_retry()

    def handle_pending_clear(file_path):
        data = load_pending_upload()
        if data and data.get("path") == file_path:
            clear_pending_upload()
            append_log(
                f"✓ Archivo pendiente procesado: {os.path.basename(file_path)}"
            )
        refresh_status_badge()

    # Log printer
    def append_log(text):
        log_output.value += text + "\n"
        page.update()

    pending_snapshot = normalize_pending_record()
    if pending_snapshot:
        append_log(
            f"ℹ Archivo pendiente detectado: {os.path.basename(pending_snapshot['path'])}"
        )
        refresh_status_badge()

    def update_last_upload(file_path):
        ts = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        last_upload_text.value = f"Última subida: {ts} → {os.path.basename(file_path)}"
        page.update()

    if not client_secret_exists:
        append_log("⚠ client_secrets.json no encontrado. Copia el archivo antes de conectar Drive.")

    # Autenticación con Drive
    def login_drive(e=None, silent=False):
        nonlocal drive_folder_name
        if not silent:
            append_log("Autenticando con Google Drive...")
        try:
            drive_uploader.authenticate()
            if not silent:
                append_log("✓ Autenticación exitosa.")
            
            # Configurar carpeta en Drive
            try:
                folder_id = drive_uploader.get_or_create_folder(drive_folder_name)
                append_log(f"✓ Carpeta configurada: {drive_folder_name}")
            except Exception as folder_ex:
                append_log(f"⚠ Error configurando carpeta: {folder_ex}")
            
            set_drive_button_state(connected=True)
            return True
        except Exception as ex:
            if not silent:
                append_log(f"✗ Error: {ex}")
            return False
    
    # Configurar carpeta de Drive
    def configure_drive_folder(e):
        nonlocal drive_folder_name
        
        # Crear un overlay temporal con input
        def handle_close(e):
            overlay_container.visible = False
            page.update()
        
        def handle_save(e):
            nonlocal drive_folder_name
            new_name = input_field.value.strip()
            if new_name:
                drive_folder_name = new_name
                drive_folder_text.value = new_name
                save_config(drive_folder_name=new_name)
                append_log(f"Carpeta Drive actualizada: {new_name}")
                
                if drive_uploader.drive:
                    try:
                        drive_uploader.get_or_create_folder(new_name)
                        append_log(f"✓ Carpeta creada/actualizada en Drive")
                    except Exception as ex:
                        append_log(f"⚠ Error: {ex}")
            
            overlay_container.visible = False
            page.update()
        
        input_field = ft.TextField(
            value=drive_folder_name,
            width=300,
            autofocus=True,
        )
        
        overlay_container = ft.Container(
            content=ft.Container(
                content=ft.Column([
                    ft.Text("Nombre de carpeta en Drive:", size=16, weight="bold"),
                    ft.Container(height=10),
                    input_field,
                    ft.Container(height=15),
                    ft.Row([
                        ft.ElevatedButton("Cancelar", on_click=handle_close),
                        ft.FilledButton("Guardar", on_click=handle_save),
                    ], alignment=ft.MainAxisAlignment.END),
                ]),
                bgcolor="#1e293b",
                padding=20,
                border_radius=10,
                width=400,
            ),
            alignment=ft.alignment.center,
            bgcolor="#00000088",
            expand=True,
            visible=True,
        )
        
        page.overlay.append(overlay_container)
        page.update()

    # Seleccionar carpeta
    def choose_folder(e):
        def on_result(folder_picker_result):
            nonlocal selected_folder_path
            if folder_picker_result.path:
                selected_folder_path = folder_picker_result.path
                selected_folder.value = selected_folder_path
                save_config(folder_path=selected_folder_path)
                append_log(f"Carpeta seleccionada: {selected_folder_path}")
                page.update()

        dlg = ft.FilePicker(on_result=on_result)
        page.overlay.append(dlg)
        page.update()
        dlg.get_directory_path()

    # Iniciar monitoreo
    def start_monitoring(e=None, auto=False):
        nonlocal observer, monitoring

        if monitoring:
            if not auto:
                append_log("El monitoreo ya está activo.")
            return

        folder = selected_folder_path
        if not folder:
            if not auto:
                append_log("Debe seleccionar una carpeta primero.")
            return

        if drive_uploader.drive is None:
            if not auto:
                append_log("Debe autenticarse con Google Drive antes de iniciar el monitoreo.")
            return

        if not auto:
            append_log("Iniciando monitoreo en segundo plano...")
        monitoring = True

        event_handler = ZipHandler(
            drive_uploader,
            append_log,
            update_last_upload,
            handle_pending_failure,
            handle_pending_clear,
        )
        observer = Observer()
        observer.schedule(event_handler, path=folder, recursive=False)

        observer.start()

        append_log("✓ Monitoreo iniciado.")
        refresh_status_badge()
        update_monitoring_buttons(True)

    # Detener monitoreo
    def stop_monitoring(e):
        nonlocal observer, monitoring

        if not monitoring:
            append_log("El monitoreo no está activo.")
            return

        observer.stop()
        observer.join()
        observer = None
        monitoring = False

        append_log("■ Monitoreo detenido.")
        refresh_status_badge()
        update_monitoring_buttons(False)

    # Asignar callbacks después de definir todas las funciones
    drive_button.on_click = login_drive
    start_button.on_click = start_monitoring
    stop_button.on_click = stop_monitoring

    # Cargar la última carpeta si existe
    if selected_folder_path and os.path.exists(selected_folder_path):
        selected_folder.value = selected_folder_path
        append_log(f"Carpeta cargada: {selected_folder_path}")
    
    # Auto-conexión y auto-inicio
    def auto_start():
        # Intentar conectar a Drive automáticamente si hay credenciales
        if os.path.exists(CREDENTIALS_FILE):
            append_log("🔄 Conectando automáticamente a Drive...")
            if login_drive(silent=True):
                append_log("✓ Drive conectado automáticamente.")
                # Si hay carpeta configurada, iniciar monitoreo
                if selected_folder_path and os.path.exists(selected_folder_path):
                    append_log("🔄 Iniciando monitoreo automáticamente...")
                    start_monitoring(auto=True)
                else:
                    append_log("⚠ No hay carpeta configurada para monitorear.")
            else:
                append_log("⚠ No se pudo conectar a Drive automáticamente.")
        else:
            append_log("ℹ Primera ejecución: Conecta manualmente a Drive y selecciona carpeta.")
            append_log("ℹ Después de eso, el monitoreo iniciará automáticamente.")
    
    # Manejar cierre de ventana (minimizar a bandeja)
    def on_window_event(e):
        if e.data == "close":
            page.window_visible = False
            page.update()
    
    page.on_window_event = on_window_event
    
    # Ejecutar auto-inicio y programar reintentos
    def handle_connect(_):
        nonlocal pending_retry_enabled
        pending_retry_enabled = True
        schedule_pending_retry()
        auto_start()

    page.on_connect = handle_connect
    page.on_disconnect = lambda _: stop_pending_retry_loop()
    handle_connect(None)

    card = ft.Container(
        width=1220,
        height=620,
        padding=ft.Padding(24, 24, 24, 24),
        border_radius=20,
        bgcolor="#1e293b",
        shadow=ft.BoxShadow(blur_radius=40, spread_radius=2, color="#00000066", offset=ft.Offset(0, 10)),
        content=ft.Column([
            # Header
            ft.Row([
                ft.Row([
                    ft.Container(
                        content=ft.Icon(Icons.CLOUD_UPLOAD, color="#0ea5e9", size=28),
                        bgcolor="#0ea5e920",
                        border_radius=12,
                        padding=ft.Padding(10, 10, 10, 10),
                    ),
                    ft.Text("Monitor de Backups SISMED", size=24, weight="w700", color="#f1f5f9"),
                    ft.Container(width=24),
                    status_badge,
                    ft.Container(width=12),
                    last_upload_text,
                ], spacing=14, tight=True, vertical_alignment="center"),
                drive_button,
            ], alignment="spaceBetween", vertical_alignment="center"),

            ft.Container(height=12),
            warning_banner,
            ft.Container(height=warning_banner.visible and 12 or 0),

            ft.Container(height=20),
            
            # Carpeta seleccionada con botón
            ft.Container(
                bgcolor="#0f172a",
                border_radius=12,
                padding=ft.Padding(16, 12, 16, 12),
                content=ft.Row([
                    ft.Icon(Icons.FOLDER_OPEN, color="#0ea5e9", size=22),
                    ft.Container(
                        content=selected_folder,
                        expand=True,
                    ),
                    ft.ElevatedButton(
                        "Elegir carpeta",
                        icon=Icons.FOLDER_OPEN,
                        on_click=choose_folder,
                        height=40,
                        style=ft.ButtonStyle(
                            shape=ft.RoundedRectangleBorder(radius=10),
                        ),
                    ),
                ], spacing=12, vertical_alignment="center"),
            ),
            
            ft.Container(height=20),
            
            # Carpeta destino en Drive
            ft.Container(
                bgcolor="#0f172a",
                border_radius=12,
                padding=ft.Padding(16, 12, 16, 12),
                content=ft.Row([
                    ft.Icon(Icons.CLOUD_QUEUE, color="#10b981", size=22),
                    ft.Column([
                        ft.Text("Carpeta en Drive:", size=11, color="#64748b"),
                        drive_folder_text,
                    ], spacing=2, expand=True),
                    ft.ElevatedButton(
                        "Configurar",
                        icon=Icons.EDIT,
                        on_click=configure_drive_folder,
                        height=40,
                        style=ft.ButtonStyle(
                            shape=ft.RoundedRectangleBorder(radius=10),
                        ),
                    ),
                ], spacing=12, vertical_alignment="center"),
            ),
            
            ft.Container(height=16),
            
            ft.Row([
                start_button,
                stop_button,
            ], spacing=12),

            ft.Container(height=20),
            
            # Registro
            ft.Row([
                ft.Icon(Icons.ARTICLE, color="#64748b", size=20),
                ft.Text("Registro de actividad", size=16, weight="w600", color="#94a3b8"),
            ], spacing=10),
            
            ft.Container(height=8),
            
            ft.Container(
                bgcolor="#0f172a",
                border_radius=12,
                border=ft.border.all(1, "#1e293b"),
                padding=ft.Padding(16, 16, 16, 16),
                height=230,
                content=ft.ListView([log_output], auto_scroll=True),
            )
        ], spacing=0),
    )

    page.add(card)


# Ejecutar la app
ft.app(target=main)
