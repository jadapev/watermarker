# app.py

import os
import zipfile
import shutil
import time
from flask import Flask, render_template, request, url_for, send_from_directory
import ffmpeg
from werkzeug.utils import secure_filename

app = Flask(__name__)

# ========================
# Configuración de carpetas
# ========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Cambia esto si tienes un disco montado con más espacio
DATA_DIR = BASE_DIR  # Ej: '/mnt/data' en Render o AWS

app.config['UPLOAD_FOLDER'] = os.path.join(DATA_DIR, 'static', 'uploads')
app.config['WATERMARK_FOLDER'] = os.path.join(DATA_DIR, 'static', 'watermarks')
app.config['OUTPUT_FOLDER'] = os.path.join(DATA_DIR, 'static', 'outputs')
app.config['TEMP_DIR'] = os.path.join(DATA_DIR, 'temp_processing')

# ========================
# Tamaño máximo de subida: 20 GB
# ========================
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024 * 1024  # 20 GB

# ========================
# Extensiones permitidas
# ========================
# Permitimos cualquier formato de imagen que FFmpeg pueda leer
app.config['ALLOWED_EXTENSIONS_IMG'] = None  # No validamos extensión
app.config['ALLOWED_EXTENSIONS_VIDEO'] = {
    'mp4', 'avi', 'mov', 'wmv', 'mkv', 'flv', 'webm', 'm4v', '3gp'
}

# ========================
# Crear carpetas si no existen
# ========================
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['WATERMARK_FOLDER'], exist_ok=True)
os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)
os.makedirs(app.config['TEMP_DIR'], exist_ok=True)

# ========================
# Posiciones de marca de agua
# ========================
POSITION_MAP = {
    'top_left': {'x': '10', 'y': '10'},
    'top_right': {'x': 'main_w-overlay_w-10', 'y': '10'},
    'bottom_left': {'x': '10', 'y': 'main_h-overlay_h-10'},
    'bottom_right': {'x': 'main_w-overlay_w-10', 'y': 'main_h-overlay_h-10'},
    'center': {'x': '(main_w-overlay_w)/2', 'y': '(main_h-overlay_h)/2'},
}

# ========================
# Funciones auxiliares
# ========================
def allowed_file_img(filename):
    """Permite cualquier archivo con extensión (FFmpeg decidirá si es válido)"""
    return '.' in filename

def allowed_file_video(filename):
    """Valida formatos de video permitidos"""
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    return ext in app.config['ALLOWED_EXTENSIONS_VIDEO']

def check_disk_space(required_gb=30):
    """Verifica si hay al menos X GB libres en el disco"""
    try:
        total, used, free = shutil.disk_usage(DATA_DIR)
        free_gb = free / (1024**3)
        return free_gb > required_gb, free_gb
    except Exception as e:
        print(f"❌ Error al verificar espacio en disco: {e}")
        return False, 0

def agregar_marca_de_agua_transparente(video_entrada, marca_de_agua, video_salida, transparencia=0.5, x="10", y="10"):
    """
    Aplica marca de agua usando FFmpeg.
    Acepta cualquier imagen que FFmpeg pueda leer (PNG, JPG, WEBP, BMP, TIFF, etc.)
    """
    try:
        video_stream = ffmpeg.input(video_entrada, **{'fflags': '+genpts'})
        watermark_stream = ffmpeg.input(marca_de_agua)

        # Convertir a RGBA y aplicar transparencia
        watermark_stream = watermark_stream.filter('format', 'rgba')
        watermark_with_alpha = watermark_stream.filter('colorchannelmixer', aa=transparencia)

        # Superponer
        overlayed = ffmpeg.overlay(video_stream, watermark_with_alpha, x=x, y=y)

        # Codificar con compatibilidad máxima
        overlayed.output(
            video_salida,
            vcodec='libx264',
            crf=23,
            preset='fast',
            pix_fmt='yuv420p',
            **{'movflags': '+faststart'}  # Para web
        ).run(overwrite_output=True, quiet=True)
        return True
    except ffmpeg.Error as e:
        stderr = e.stderr.decode() if e.stderr else str(e)
        print(f"❌ FFmpeg Error al procesar {video_entrada}: {stderr}")
        return False
    except Exception as e:
        print(f"❌ Error inesperado en FFmpeg: {str(e)}")
        return False

# ========================
# Rutas de la aplicación
# ========================
@app.route('/', methods=['GET', 'POST'])
def upload_files():
    if request.method == 'POST':
        # Verificar espacio en disco (mínimo 30 GB recomendado)
        has_space, free_gb = check_disk_space(30)
        if not has_space:
            return f'❌ Espacio en disco insuficiente: {free_gb:.1f} GB libres. Necesitas al menos 30 GB.', 500

        # Validar marca de agua
        if 'watermark_file' not in request.files or request.files['watermark_file'].filename.strip() == '':
            return '❌ Debes subir una marca de agua.', 400

        watermark_file = request.files['watermark_file']
        if not allowed_file_img(watermark_file.filename):
            return '❌ La marca de agua debe tener una extensión válida.', 400

        # Guardar marca de agua
        watermark_filename = secure_filename(watermark_file.filename)
        watermark_input_path = os.path.join(app.config['WATERMARK_FOLDER'], watermark_filename)
        watermark_final_path = os.path.join(app.config['WATERMARK_FOLDER'], 'current_watermark.png')
        watermark_file.save(watermark_input_path)

        # Convertir a PNG (FFmpeg intentará abrir cualquier imagen compatible)
        try:
            ffmpeg.input(watermark_input_path).output(watermark_final_path, vframes=1).run(
                overwrite_output=True, quiet=True
            )
            os.remove(watermark_input_path)  # Limpiar original
        except ffmpeg.Error as e:
            print(f"❌ Error al convertir marca de agua: {e}")
            if os.path.exists(watermark_input_path):
                os.remove(watermark_input_path)
            return '❌ No se pudo procesar la imagen de marca de agua. Formato no soportado.', 500
        except Exception as e:
            print(f"❌ Error inesperado al procesar la imagen: {e}")
            if os.path.exists(watermark_input_path):
                os.remove(watermark_input_path)
            return '❌ Error crítico al procesar la marca de agua.', 500

        # Opacidad y posición
        try:
            transparency = max(0.0, min(1.0, float(request.form.get('transparency', 0.5))))
        except (ValueError, TypeError):
            transparency = 0.5

        position = request.form.get('position', 'top_left')
        coords = POSITION_MAP.get(position, POSITION_MAP['top_left'])

        # =====================================
        # OPCIÓN 1: Procesar un solo video
        # =====================================
        if 'video_file' in request.files and request.files['video_file'].filename.strip() != '':
            video_file = request.files['video_file']
            if not allowed_file_video(video_file.filename):
                return '❌ Formato de video no permitido.', 400

            video_filename = secure_filename(video_file.filename)
            video_path = os.path.join(app.config['UPLOAD_FOLDER'], video_filename)
            output_filename = f"watermarked_{video_filename}"
            output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)

            video_file.save(video_path)

            if agregar_marca_de_agua_transparente(
                video_path, watermark_final_path, output_path,
                transparencia=transparency, x=coords['x'], y=coords['y']
            ):
                os.remove(video_path)
                os.remove(watermark_final_path)
                return render_template('index.html', download_link=url_for('download_file_single', filename=output_filename))
            else:
                os.remove(video_path)
                os.remove(watermark_final_path)
                return '❌ Error al procesar el video.', 500

        # =====================================
        # OPCIÓN 2: Procesar un archivo ZIP
        # =====================================
        elif 'zip_file' in request.files and request.files['zip_file'].filename.strip() != '':
            zip_file = request.files['zip_file']
            if not zip_file.filename.lower().endswith('.zip'):
                return '❌ El archivo debe ser un .zip.', 400

            zip_filename = secure_filename(zip_file.filename)
            zip_path = os.path.join(app.config['UPLOAD_FOLDER'], zip_filename)
            zip_file.save(zip_path)

            # Carpetas temporales únicas
            timestamp = str(int(time.time()))
            temp_unzip = os.path.join(app.config['TEMP_DIR'], f'unzip_{timestamp}')
            temp_output = os.path.join(app.config['TEMP_DIR'], f'output_{timestamp}')
            os.makedirs(temp_unzip, exist_ok=True)
            os.makedirs(temp_output, exist_ok=True)

            try:
                processed_any = False
                with zipfile.ZipFile(zip_path, 'r') as zf:
                    for file_info in zf.infolist():
                        if file_info.is_dir():
                            continue

                        base_name = os.path.basename(file_info.filename)
                        if not base_name:
                            continue

                        if allowed_file_video(base_name):
                            # Rutas temporales
                            extracted_path = os.path.join(temp_unzip, base_name)
                            output_path = os.path.join(temp_output, f'watermarked_{base_name}')

                            # Extraer solo este archivo (sin usar RAM)
                            with zf.open(file_info) as src, open(extracted_path, 'wb') as dst:
                                shutil.copyfileobj(src, dst)

                            # Procesar con marca de agua
                            if agregar_marca_de_agua_transparente(
                                extracted_path, watermark_final_path, output_path,
                                transparencia=transparency, x=coords['x'], y=coords['y']
                            ):
                                print(f"✅ Procesado: {base_name}")
                                processed_any = True
                            else:
                                print(f"❌ Falló: {base_name}")

                            # Borrar inmediatamente el video temporal
                            os.remove(extracted_path)

                if not processed_any:
                    raise Exception("No se encontraron videos válidos en el ZIP.")

                # Crear ZIP de salida
                final_zip_name = f"processed_{os.path.splitext(zip_filename)[0]}.zip"
                final_zip_path = os.path.join(app.config['OUTPUT_FOLDER'], final_zip_name)
                shutil.make_archive(final_zip_path.replace('.zip', ''), 'zip', temp_output)

                # Limpiar
                shutil.rmtree(temp_unzip, ignore_errors=True)
                shutil.rmtree(temp_output, ignore_errors=True)
                os.remove(zip_path)
                os.remove(watermark_final_path)

                return render_template(
                    'index.html',
                    download_link=url_for('download_file_single', filename=os.path.basename(final_zip_path)),
                    is_zip=True
                )

            except Exception as e:
                print(f"❌ Error procesando ZIP: {e}")
                # Limpieza segura
                if os.path.exists(temp_unzip): shutil.rmtree(temp_unzip, ignore_errors=True)
                if os.path.exists(temp_output): shutil.rmtree(temp_output, ignore_errors=True)
                if os.path.exists(zip_path): os.remove(zip_path)
                if os.path.exists(watermark_final_path): os.remove(watermark_final_path)
                return f'❌ Error procesando el archivo ZIP: {str(e)}', 500

        return '❌ Debes subir un video o un archivo ZIP.', 400

    return render_template('index.html')

# ========================
# Rutas de descarga
# ========================
@app.route('/download/<filename>')
def download_file_single(filename):
    return send_from_directory(app.config['OUTPUT_FOLDER'], filename, as_attachment=True)

# ========================
# Manejo de errores
# ========================
@app.errorhandler(404)
def not_found(e):
    return 'Página no encontrada', 404

@app.errorhandler(500)
def server_error(e):
    return 'Error interno del servidor', 500

# ========================
# Punto de entrada
# ========================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, threaded=False)
