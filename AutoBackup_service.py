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

# Resolver rutas tanto en modo script como empaquetado
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

os.chdir(APP_DIR)

BASE_APP_DATA_DIR = os.path.join(os.path.expanduser("~"), "AppData", "Local", "AutoBackup SISMED")
APP_DATA_DIR = os.path.realpath(BASE_APP_DATA_DIR)
os.makedirs(APP_DATA_DIR, exist_ok=True)

CONFIG_FILE = os.path.join(APP_DATA_DIR, "autobackup_config.json")
CREDENTIALS_FILE = os.path.join(APP_DATA_DIR, "credentials.json")
SETTINGS_FILE = os.path.join(APP_DIR, "settings.yaml")
PENDING_UPLOAD_FILE = os.path.join(APP_DATA_DIR, "pending_upload.json")
PENDING_RETRY_SECONDS = 60

LOG_FILE = os.path.join(APP_DATA_DIR, "autobackup_service.log")

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


def ensure_plain_file(path: str, context: str = "archivo"):
    try:
        if os.path.exists(path) and os.path.islink(path):
            os.remove(path)
            logging.warning(f"{context} era un enlace simbólico y se regenerará: {path}")
            return True
    except Exception as err:
        logging.error(f"No se pudo limpiar enlace simbólico ({path}): {err}")
    return False


def load_pending_upload():
    try:
        if os.path.exists(PENDING_UPLOAD_FILE):
            with open(PENDING_UPLOAD_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("path"):
                    return data
    except Exception as err:
        logging.error(f"Error leyendo pending_upload.json: {err}")
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
        logging.warning(f"Archivo en cola para reintento: {file_path}")
    except Exception as err:
        logging.error(f"Error guardando pending_upload.json: {err}")


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
        logging.error(f"Error actualizando pending_upload.json: {err}")


def clear_pending_upload():
    try:
        if os.path.exists(PENDING_UPLOAD_FILE):
            os.remove(PENDING_UPLOAD_FILE)
            logging.info("Registro de pendiente eliminado.")
    except Exception as err:
        logging.error(f"Error eliminando pending_upload.json: {err}")


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


class DriveUploader:
    def __init__(self):
        self.gauth = GoogleAuth(settings_file=SETTINGS_FILE)
        self.drive = None
        self.folder_id = None
        self.credentials_path = CREDENTIALS_FILE
        self.gauth.settings["save_credentials_file"] = self.credentials_path
        self.last_deleted = 0

    def authenticate(self):
        ensure_plain_file(self.credentials_path, "Credenciales")
        if not os.path.exists(self.credentials_path):
            raise RuntimeError("No hay credenciales guardadas. Ejecuta la interfaz y conecta Drive.")

        self.gauth.LoadCredentialsFile(self.credentials_path)
        if self.gauth.credentials is None:
            raise RuntimeError("Credenciales inválidas. Conecta nuevamente desde la interfaz GUI.")

        if self.gauth.access_token_expired:
            try:
                self.gauth.Refresh()
            except Exception as refresh_err:
                raise RuntimeError(f"No se pudo refrescar el token: {refresh_err}")

        self.gauth.Authorize()
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
        self.last_deleted = 0

        if self.folder_id:
            query = f"'{self.folder_id}' in parents and trashed=false"
            existing_files = self.drive.ListFile({'q': query}).GetList()
            for existing in existing_files:
                if existing['id'] != file_drive['id']:
                    existing.Delete()
                    self.last_deleted += 1
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
                self._wait_for_file_ready(event.src_path)
                self._upload_with_retry(event.src_path)
                logging.info(f"✓ Subido a Drive: {os.path.basename(event.src_path)}")
                if self.uploader.last_deleted:
                    logging.info(
                        f"ℹ Se eliminó {self.uploader.last_deleted} archivo(s) previo(s) en Drive para conservar solo el más reciente."
                    )
                clear_pending_if_matches(event.src_path)
            except TimeoutError as wait_err:
                logging.error(f"✗ {wait_err}")
            except Exception as e:
                logging.error(f"✗ Error subiendo archivo: {e}")
                save_pending_upload(event.src_path)

    def _wait_for_file_ready(self, file_path, timeout=90, interval=1, stable_checks=2):
        start_time = time.time()
        last_size = -1
        stable_count = 0
        logging.info("Esperando que el archivo termine de generarse...")

        while time.time() - start_time <= timeout:
            try:
                current_size = os.path.getsize(file_path)
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

    def _upload_with_retry(self, file_path, retries=5, delay=1):
        last_error = None
        for attempt in range(retries):
            try:
                time.sleep(0.5)
                self.uploader.upload_file(file_path)
                return
            except PermissionError as err:
                last_error = err
                logging.warning(
                    f"Archivo en uso, reintentando en {delay}s... (intento {attempt + 1})"
                )
                time.sleep(delay)
        raise last_error


def retry_pending_upload(drive_uploader):
    pending = normalize_pending_record()
    if not pending:
        return

    file_path = pending.get("path")
    if not file_path or not os.path.exists(file_path):
        clear_pending_upload()
        logging.info("Archivo pendiente inexistente. Registro limpiado.")
        return

    if drive_uploader.drive is None:
        logging.info("Hay un archivo pendiente pero Drive no está autenticado. Se reintentará luego.")
        return

    if pending.get("in_progress"):
        return

    mark_pending_in_progress(True)
    handler = ZipHandler(drive_uploader)
    logging.info(f"🔁 Reintentando subida pendiente: {file_path}")
    try:
        handler._wait_for_file_ready(file_path)
        handler._upload_with_retry(file_path)
        clear_pending_if_matches(file_path)
        logging.info("✓ Archivo pendiente subido correctamente.")
    except Exception as err:
        logging.error(f"✗ Error reintentando archivo pendiente: {err}")
    finally:
        if load_pending_upload():
            mark_pending_in_progress(False)


def load_config():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("last_folder"), data.get("drive_folder_name", "Backups SISMED")
    except Exception as e:
        logging.error(f"Error cargando config: {e}")
    return None, "Backups SISMED"


def create_icon():
    width = 64
    height = 64
    image = Image.new('RGB', (width, height), color='#0ea5e9')
    dc = ImageDraw.Draw(image)

    dc.ellipse([10, 20, 30, 35], fill='white')
    dc.ellipse([20, 15, 44, 35], fill='white')
    dc.ellipse([35, 20, 54, 35], fill='white')
    dc.rectangle([15, 27, 50, 35], fill='white')

    return image


def open_gui():
    try:
        gui_exe = os.path.join(APP_DIR, "AutoBackup Config.exe")
        if os.path.exists(gui_exe):
            subprocess.Popen([gui_exe])
        else:
            python_exe = os.path.join(os.path.dirname(sys.executable), "python.exe")
            gui_path = os.path.join(APP_DIR, "AutoBackup.py")
            subprocess.Popen([python_exe, gui_path])
        logging.info("Interfaz gráfica abierta")
    except Exception as e:
        logging.error(f"Error abriendo GUI: {e}")


def view_logs():
    try:
        os.startfile(LOG_FILE)
        logging.info("Logs abiertos")
    except Exception as e:
        logging.error(f"Error abriendo logs: {e}")


def quit_app(icon, observer):
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

    selected_folder_path, drive_folder_name = load_config()

    if not selected_folder_path or not os.path.exists(selected_folder_path):
        logging.error("No hay carpeta configurada o no existe. Ejecuta la versión GUI primero.")
        return

    logging.info(f"Carpeta a monitorear: {selected_folder_path}")

    drive_uploader = DriveUploader()

    try:
        logging.info("Conectando a Google Drive...")
        drive_uploader.authenticate()
        logging.info("✓ Conectado a Drive")

        drive_uploader.get_or_create_folder(drive_folder_name)
        logging.info(f"✓ Carpeta configurada: {drive_folder_name}")

    except Exception as e:
        logging.error(f"✗ Error conectando a Drive: {e}")
        return

    logging.info("Iniciando monitoreo...")
    event_handler = ZipHandler(drive_uploader)
    observer = Observer()
    observer.schedule(event_handler, path=selected_folder_path, recursive=False)
    observer.start()

    logging.info("✓ Monitoreo activo")

    pending_snapshot = normalize_pending_record()
    if pending_snapshot:
        logging.info(f"Archivo pendiente detectado: {pending_snapshot['path']}")

    icon_image = create_icon()
    menu = pystray.Menu(
        pystray.MenuItem("Abrir interfaz", lambda: open_gui()),
        pystray.MenuItem("Ver logs", lambda: view_logs()),
        pystray.MenuItem("Salir", lambda: quit_app(icon, observer))
    )

    icon = pystray.Icon("AutoBackup SISMED", icon_image, "AutoBackup SISMED - Activo", menu)

    def run_icon():
        icon.run()

    icon_thread = threading.Thread(target=run_icon, daemon=True)
    icon_thread.start()

    last_retry_attempt = time.time()

    try:
        while True:
            time.sleep(60)
            if time.time() - last_retry_attempt >= PENDING_RETRY_SECONDS:
                last_retry_attempt = time.time()
                retry_pending_upload(drive_uploader)
    except KeyboardInterrupt:
        logging.info("Deteniendo monitoreo...")
        icon.stop()
        observer.stop()
        observer.join()
        logging.info("✓ Monitoreo detenido")


if __name__ == "__main__":
    main()
