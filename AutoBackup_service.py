import os
import time
import json
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive
import logging
from datetime import datetime
import pystray
from PIL import Image, ImageDraw
import threading
import subprocess
import sys

# Configurar ruta del log en la carpeta del usuario
log_dir = os.path.join(os.path.expanduser("~"), "AppData", "Local", "AutoBackup SISMED")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "autobackup_service.log")

# Configurar logging
logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

class DriveUploader:
    def __init__(self):
        self.gauth = GoogleAuth(settings_file="settings.yaml")
        self.drive = None
        self.folder_id = None

    def authenticate(self):
        self.gauth.LocalWebserverAuth()
        self.drive = GoogleDrive(self.gauth)

    def get_or_create_folder(self, folder_name):
        if self.drive is None:
            raise RuntimeError("Google Drive no autenticado")
        
        query = f"title='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        file_list = self.drive.ListFile({'q': query}).GetList()
        
        if file_list:
            self.folder_id = file_list[0]['id']
            return file_list[0]['id']
        else:
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
        
        if self.folder_id:
            file_metadata['parents'] = [{'id': self.folder_id}]
        
        file_drive = self.drive.CreateFile(file_metadata)
        file_drive.SetContentFile(file_path)
        file_drive.Upload()
        return True


class ZipHandler(FileSystemEventHandler):
    def __init__(self, uploader):
        self.uploader = uploader

    def on_created(self, event):
        if event.is_directory:
            return

        if event.src_path.lower().endswith(".zip"):
            logging.info(f"Nuevo archivo detectado: {event.src_path}")

            try:
                self._upload_with_retry(event.src_path)
                logging.info(f"✓ Subido a Drive: {os.path.basename(event.src_path)}")
            except Exception as e:
                logging.error(f"✗ Error subiendo archivo: {e}")

    def _upload_with_retry(self, file_path, retries=5, delay=1):
        last_error = None
        for attempt in range(retries):
            try:
                time.sleep(0.5)
                self.uploader.upload_file(file_path)
                return
            except PermissionError as err:
                last_error = err
                logging.warning(f"Archivo en uso, reintentando en {delay}s... (intento {attempt + 1})")
                time.sleep(delay)
        raise last_error


def load_config():
    try:
        if os.path.exists("autobackup_config.json"):
            with open("autobackup_config.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("last_folder"), data.get("drive_folder_name", "Backups SISMED")
    except Exception as e:
        logging.error(f"Error cargando config: {e}")
    return None, "Backups SISMED"


def create_icon():
    """Crea un icono simple para la bandeja del sistema"""
    width = 64
    height = 64
    image = Image.new('RGB', (width, height), color='#0ea5e9')
    dc = ImageDraw.Draw(image)
    
    # Dibujar una nube simple
    dc.ellipse([10, 20, 30, 35], fill='white')
    dc.ellipse([20, 15, 44, 35], fill='white')
    dc.ellipse([35, 20, 54, 35], fill='white')
    dc.rectangle([15, 27, 50, 35], fill='white')
    
    return image


def open_gui():
    """Abre la interfaz gráfica"""
    try:
        python_exe = os.path.join(os.path.dirname(sys.executable), "python.exe")
        gui_path = os.path.join(os.path.dirname(__file__), "AutoBackup.py")
        subprocess.Popen([python_exe, gui_path])
        logging.info("Interfaz gráfica abierta")
    except Exception as e:
        logging.error(f"Error abriendo GUI: {e}")


def view_logs():
    """Abre el archivo de logs"""
    try:
        log_dir = os.path.join(os.path.expanduser("~"), "AppData", "Local", "AutoBackup SISMED")
        log_path = os.path.join(log_dir, "autobackup_service.log")
        os.startfile(log_path)
        logging.info("Logs abiertos")
    except Exception as e:
        logging.error(f"Error abriendo logs: {e}")


def quit_app(icon, observer):
    """Detiene el servicio y cierra"""
    logging.info("Deteniendo servicio...")
    icon.stop()
    if observer:
        observer.stop()
        observer.join()
    logging.info("✓ Servicio detenido")


def main():
    logging.info("=" * 50)
    logging.info("AutoBackup SISMED - Servicio iniciado")
    logging.info("=" * 50)
    
    # Cargar configuración
    selected_folder_path, drive_folder_name = load_config()
    
    if not selected_folder_path or not os.path.exists(selected_folder_path):
        logging.error("No hay carpeta configurada o no existe. Ejecuta la versión GUI primero.")
        return
    
    logging.info(f"Carpeta a monitorear: {selected_folder_path}")
    
    # Autenticar con Drive
    if not os.path.exists("credentials.json"):
        logging.error("No hay credenciales de Drive. Ejecuta la versión GUI primero para autenticarte.")
        return
    
    drive_uploader = DriveUploader()
    
    try:
        logging.info("Conectando a Google Drive...")
        drive_uploader.authenticate()
        logging.info("✓ Conectado a Drive")
        
        # Configurar carpeta en Drive
        drive_uploader.get_or_create_folder(drive_folder_name)
        logging.info(f"✓ Carpeta configurada: {drive_folder_name}")
        
    except Exception as e:
        logging.error(f"✗ Error conectando a Drive: {e}")
        return
    
    # Iniciar monitoreo
    logging.info("Iniciando monitoreo...")
    event_handler = ZipHandler(drive_uploader)
    observer = Observer()
    observer.schedule(event_handler, path=selected_folder_path, recursive=False)
    observer.start()
    
    logging.info("✓ Monitoreo activo")
    
    # Crear icono en bandeja del sistema
    icon_image = create_icon()
    menu = pystray.Menu(
        pystray.MenuItem("Abrir interfaz", lambda: open_gui()),
        pystray.MenuItem("Ver logs", lambda: view_logs()),
        pystray.MenuItem("Salir", lambda: quit_app(icon, observer))
    )
    
    icon = pystray.Icon("AutoBackup SISMED", icon_image, "AutoBackup SISMED - Activo", menu)
    
    # Ejecutar en thread separado para mantener el monitoreo
    def run_icon():
        icon.run()
    
    icon_thread = threading.Thread(target=run_icon, daemon=True)
    icon_thread.start()
    
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logging.info("Deteniendo monitoreo...")
        icon.stop()
        observer.stop()
        observer.join()
        logging.info("✓ Monitoreo detenido")


if __name__ == "__main__":
    main()
