import flet as ft
from flet import Icons
import os
import time
import json
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive


# -----------------------
# Google Drive Manager
# -----------------------
class DriveUploader:
    def __init__(self):
        self.gauth = GoogleAuth(settings_file="settings.yaml")
        self.drive = None
        self.folder_id = None

    def authenticate(self):
        self.gauth.LocalWebserverAuth()  # Abre navegador para login 1 sola vez
        self.drive = GoogleDrive(self.gauth)

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
        return True


# -----------------------
# File Watcher
# -----------------------
class ZipHandler(FileSystemEventHandler):
    def __init__(self, uploader, log_callback):
        self.uploader = uploader
        self.log_callback = log_callback

    def on_created(self, event):
        if event.is_directory:
            return

        if event.src_path.lower().endswith(".zip"):
            self.log_callback(f"Nuevo archivo detectado: {event.src_path}")

            try:
                self._upload_with_retry(event.src_path)
                self.log_callback(f"✓ Subido a Drive: {os.path.basename(event.src_path)}")
            except Exception as e:
                self.log_callback(f"✗ Error subiendo archivo: {e}")
    
    def on_modified(self, event):
        # Detectar modificaciones también (útil si el archivo se está escribiendo)
        if event.is_directory:
            return
        
        if event.src_path.lower().endswith(".zip"):
            # Solo registrar, no subir en modificación
            pass

    def _upload_with_retry(self, file_path, retries=5, delay=1):
        last_error = None
        for attempt in range(retries):
            try:
                # Esperar un momento para asegurar que el archivo esté completamente escrito
                time.sleep(0.5)
                self.uploader.upload_file(file_path)
                return
            except PermissionError as err:
                last_error = err
                self.log_callback(
                    f"Archivo en uso, reintentando en {delay}s... (intento {attempt + 1})"
                )
                time.sleep(delay)
        raise last_error


# -----------------------
# Interfaz Flet
# -----------------------
def main(page: ft.Page):
    page.title = "Monitor de backups SISMED → Google Drive"
    page.window_width = 560
    page.window_height = 600
    page.bgcolor = "#0f172a"
    page.padding = 0
    page.horizontal_alignment = "center"
    page.vertical_alignment = "center"
    
    # Iniciar oculta
    page.window_visible = False
    page.window_prevent_close = True

    CONFIG_FILE = "autobackup_config.json"

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
    selected_folder_path, drive_folder_name = load_config()
    drive_folder_text = ft.Text(drive_folder_name or "Backups SISMED", size=13, color="#cbd5e1")
    status_icon = ft.Icon(Icons.CIRCLE, color="#94a3b8", size=10)
    status_text = ft.Text("Inactivo", size=12, weight="w600", color="#cbd5e1")
    
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

    drive_uploader = DriveUploader()

    # Log printer
    def append_log(text):
        log_output.value += text + "\n"
        page.update()

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

        event_handler = ZipHandler(drive_uploader, append_log)
        observer = Observer()
        observer.schedule(event_handler, path=folder, recursive=False)

        observer.start()

        append_log("✓ Monitoreo iniciado.")
        set_status_badge("Activo", "#064e3b", "#34d399", "#10b981")
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
        set_status_badge("Inactivo", "#334155", "#94a3b8", "#475569")
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
        if os.path.exists("credentials.json"):
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
    
    # Ejecutar auto-inicio después de construir la UI
    page.on_connect = lambda _: auto_start()

    card = ft.Container(
        width=1220,
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
                ], spacing=14),
                drive_button,
            ], alignment="spaceBetween", vertical_alignment="center"),
            
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
                height=340,
                content=ft.ListView([log_output], auto_scroll=True),
            )
        ], spacing=0),
    )

    page.add(card)


# Ejecutar la app
ft.app(target=main)
