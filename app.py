import os
import zipfile
import shutil
import time
import json
import psutil
import subprocess
from flask import Flask, render_template, request, url_for, send_from_directory, jsonify
import ffmpeg
from werkzeug.utils import secure_filename
from celery import Celery
from celery.result import AsyncResult
from config import Config

# Configuración de Celery
def make_celery(app):
    celery = Celery(
        app.import_name,
        backend=app.config['CELERY_RESULT_BACKEND'],
        broker=app.config['CELERY_BROKER_URL']
    )
    celery.conf.update(app.config)
    
    # Configuración de Celery para tareas largas
    celery.conf.update(
        task_time_limit=86400,  # 24 horas de timeout
        task_soft_time_limit=86400,
        worker_max_tasks_per_child=10,
        worker_prefetch_multiplier=1
    )
    
    return celery

app = Flask(__name__)
app.config.from_object(Config)
celery = make_celery(app)

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
# Funciones auxiliares OPTIMIZADAS
# ========================
def allowed_file_img(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'webp', 'bmp'}

def allowed_file_video(filename):
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    return ext in app.config['ALLOWED_EXTENSIONS_VIDEO']

def check_disk_space(required_gb=40):
    """Verifica espacio en disco con margen para archivos grandes"""
    try:
        total, used, free = shutil.disk_usage(app.config['DATA_DIR'])
        free_gb = free / (1024**3)
        return free_gb > required_gb, free_gb
    except Exception as e:
        print(f"❌ Error al verificar espacio en disco: {e}")
        return False, 0

def check_system_resources():
    """Verifica recursos del sistema optimizado para VPS"""
    try:
        # Espacio en disco
        total, used, free = shutil.disk_usage(app.config['DATA_DIR'])
        free_gb = free / (1024**3)
        
        # Memoria disponible (más estricto para VPS)
        mem = psutil.virtual_memory()
        mem_available_gb = mem.available / (1024**3)
        
        # Uso de CPU
        cpu_percent = psutil.cpu_percent(interval=2)
        
        # Límites ajustados para tu VPS de 8GB RAM
        return free_gb > 10, mem_available_gb > 1.5, cpu_percent < 85, free_gb, mem_available_gb, cpu_percent
    except Exception as e:
        print(f"❌ Error al verificar recursos del sistema: {e}")
        return False, False, False, 0, 0, 100

def stream_save(file_obj, save_path, chunk_size=2*1024*1024):  # 2MB chunks para mejor rendimiento
    """Guarda archivos grandes optimizado para NVMe"""
    with open(save_path, 'wb') as f:
        while True:
            chunk = file_obj.read(chunk_size)
            if not chunk:
                break
            f.write(chunk)

def optimizar_ffmpeg_para_vps():
    """Devuelve parámetros optimizados de FFmpeg para tu VPS"""
    return {
        'vcodec': 'libx264',
        'crf': '23',
        'preset': app.config['FFMPEG_PRESET'],
        'pix_fmt': 'yuv420p',
        'threads': str(app.config['FFMPEG_THREADS']),
        'movflags': '+faststart',
        'max_muxing_queue_size': '1024'  # Important para archivos grandes
    }

def agregar_marca_de_agua_transparente(video_entrada, marca_de_agua, video_salida, transparencia=0.5, x="10", y="10"):
    """
    Aplica marca de agua usando FFmpeg OPTIMIZADO para archivos grandes
    """
    try:
        # Configuración optimizada
        ffmpeg_params = optimizar_ffmpeg_para_vps()
        
        video_stream = ffmpeg.input(video_entrada, **{'fflags': '+genpts', 'max_delay': '5000000'})
        watermark_stream = ffmpeg.input(marca_de_agua)

        # Convertir a RGBA y aplicar transparencia
        watermark_stream = watermark_stream.filter('format', 'rgba')
        watermark_with_alpha = watermark_stream.filter('colorchannelmixer', aa=transparencia)

        # Superponer con parámetros optimizados
        overlayed = ffmpeg.overlay(video_stream, watermark_with_alpha, x=x, y=y)

        # Usar parámetros optimizados
        output_kwargs = ffmpeg_params.copy()
        output_kwargs['y'] = None  # Overwrite output
        
        overlayed.output(video_salida, **output_kwargs).run(
            overwrite_output=True, 
            quiet=True,
            capture_stderr=True
        )
        return True
        
    except ffmpeg.Error as e:
        stderr = e.stderr.decode() if e.stderr else str(e)
        print(f"❌ FFmpeg Error al procesar {video_entrada}: {stderr}")
        return False
    except Exception as e:
        print(f"❌ Error inesperado en FFmpeg: {str(e)}")
        return False

# ========================
# Tareas de Celery OPTIMIZADAS para archivos grandes
# ========================
@celery.task(bind=True, name='tasks.process_video')
def process_video_task(self, video_path, watermark_path, output_path, transparency, x, y):
    """Tarea optimizada para procesar videos grandes"""
    try:
        # Verificar recursos con límites ajustados
        disk_ok, mem_ok, cpu_ok, free_gb, mem_avail, cpu_percent = check_system_resources()
        if not all([disk_ok, mem_ok, cpu_ok]):
            error_msg = f"Recursos insuficientes. Disco: {free_gb:.1f}GB, Memoria: {mem_avail:.1f}GB, CPU: {cpu_percent}%"
            raise Exception(error_msg)
        
        # Procesar el video
        success = agregar_marca_de_agua_transparente(
            video_path, watermark_path, output_path,
            transparencia=transparency, x=x, y=y
        )
        
        # Limpiar archivos temporales
        for file_path in [video_path, watermark_path]:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except:
                    pass
        
        if success:
            return output_path
        else:
            raise Exception("Error al procesar el video con FFmpeg")
            
    except Exception as e:
        # Limpieza exhaustiva en caso de error
        for file_path in [video_path, watermark_path]:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except:
                    pass
        raise self.retry(exc=e, countdown=300, max_retries=2)  # Reintentos más espaciados

@celery.task(bind=True, name='tasks.process_zip')
def process_zip_task(self, zip_path, watermark_path, output_zip_path, transparency, x, y):
    """Tarea optimizada para procesar ZIPs grandes"""
    temp_unzip = None
    temp_output = None
    
    try:
        # Verificar recursos
        disk_ok, mem_ok, cpu_ok, free_gb, mem_avail, cpu_percent = check_system_resources()
        if not all([disk_ok, mem_ok, cpu_ok]):
            error_msg = f"Recursos insuficientes. Disco: {free_gb:.1f}GB, Memoria: {mem_avail:.1f}GB, CPU: {cpu_percent}%"
            raise Exception(error_msg)
        
        # Crear carpetas temporales únicas
        timestamp = str(int(time.time()))
        temp_unzip = os.path.join(app.config['TEMP_DIR'], f'unzip_{timestamp}')
        temp_output = os.path.join(app.config['TEMP_DIR'], f'output_{timestamp}')
        os.makedirs(temp_unzip, exist_ok=True)
        os.makedirs(temp_output, exist_ok=True)
        
        processed_any = False
        processed_count = 0
        
        with zipfile.ZipFile(zip_path, 'r') as zf:
            video_files = [f for f in zf.infolist() if not f.is_dir() and allowed_file_video(os.path.basename(f.filename))]
            total_files = len(video_files)
            
            for file_info in video_files:
                base_name = os.path.basename(file_info.filename)
                if not base_name:
                    continue

                # Actualizar progreso
                self.update_state(
                    state='PROGRESS',
                    meta={
                        'current': processed_count,
                        'total': total_files,
                        'status': f'Procesando {base_name}',
                        'percent': int((processed_count / total_files) * 100) if total_files > 0 else 0
                    }
                )

                extracted_path = os.path.join(temp_unzip, base_name)
                output_path = os.path.join(temp_output, f'watermarked_{base_name}')

                # Extraer archivo
                with zf.open(file_info) as src, open(extracted_path, 'wb') as dst:
                    shutil.copyfileobj(src, dst)

                # Procesar video
                if agregar_marca_de_agua_transparente(extracted_path, watermark_path, output_path, transparencia=transparency, x=x, y=y):
                    processed_any = True
                    processed_count += 1
                else:
                    print(f"❌ Falló: {base_name}")

                # Limpiar inmediatamente
                if os.path.exists(extracted_path):
                    os.remove(extracted_path)

        if not processed_any:
            raise Exception("No se encontraron videos válidos en el ZIP.")

        # Crear ZIP de salida
        shutil.make_archive(output_zip_path.replace('.zip', ''), 'zip', temp_output)

        return output_zip_path

    except Exception as e:
        raise self.retry(exc=e, countdown=300, max_retries=2)
    finally:
        # LIMPIEZA EXHAUSTIVA
        try:
            if zip_path and os.path.exists(zip_path):
                os.remove(zip_path)
            if watermark_path and os.path.exists(watermark_path):
                os.remove(watermark_path)
            if temp_unzip and os.path.exists(temp_unzip):
                shutil.rmtree(temp_unzip, ignore_errors=True)
            if temp_output and os.path.exists(temp_output):
                shutil.rmtree(temp_output, ignore_errors=True)
        except Exception as cleanup_error:
            print(f"⚠️ Error en limpieza: {cleanup_error}")

# ========================
# Rutas de la aplicación (optimizadas)
# ========================
@app.route('/', methods=['GET'])
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_files():
    try:
        # Verificación de espacio más estricta
        has_space, free_gb = check_disk_space(40)  # 40GB mínimo
        if not has_space:
            return jsonify({'error': f'Espacio insuficiente: {free_gb:.1f}GB libres. Necesitas 40GB.'}), 500

        # Validaciones de archivos
        if 'watermark_file' not in request.files:
            return jsonify({'error': 'Debes subir una marca de agua.'}), 400

        watermark_file = request.files['watermark_file']
        if not watermark_file or not allowed_file_img(watermark_file.filename):
            return jsonify({'error': 'Marca de agua no válida.'}), 400

        # Guardar marca de agua
        watermark_filename = f"watermark_{int(time.time())}_{secure_filename(watermark_file.filename)}"
        watermark_path = os.path.join(app.config['WATERMARK_FOLDER'], watermark_filename)
        stream_save(watermark_file, watermark_path)

        # Convertir a PNG si es necesario
        if not watermark_path.lower().endswith('.png'):
            png_path = os.path.splitext(watermark_path)[0] + '.png'
            try:
                ffmpeg.input(watermark_path).output(png_path, vframes=1).run(overwrite_output=True, quiet=True)
                os.remove(watermark_path)
                watermark_path = png_path
            except:
                pass

        # Obtener parámetros
        try:
            transparency = max(0.1, min(1.0, float(request.form.get('transparency', 0.5))))
        except:
            transparency = 0.5

        position = request.form.get('position', 'bottom_right')
        coords = POSITION_MAP.get(position, POSITION_MAP['bottom_right'])

        # Procesar según el tipo de archivo
        if 'video_file' in request.files and request.files['video_file'].filename:
            return process_video_file(request, watermark_path, transparency, coords)
        elif 'zip_file' in request.files and request.files['zip_file'].filename:
            return process_zip_file(request, watermark_path, transparency, coords)
        else:
            if os.path.exists(watermark_path):
                os.remove(watermark_path)
            return jsonify({'error': 'Debes subir un video o ZIP.'}), 400

    except Exception as e:
        return jsonify({'error': f'Error interno: {str(e)}'}), 500

def process_video_file(request, watermark_path, transparency, coords):
    """Procesar archivo de video individual"""
    video_file = request.files['video_file']
    if not allowed_file_video(video_file.filename):
        if os.path.exists(watermark_path):
            os.remove(watermark_path)
        return jsonify({'error': 'Formato de video no permitido.'}), 400

    video_filename = f"video_{int(time.time())}_{secure_filename(video_file.filename)}"
    video_path = os.path.join(app.config['UPLOAD_FOLDER'], video_filename)
    output_filename = f"watermarked_{secure_filename(video_file.filename)}"
    output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)

    stream_save(video_file, video_path)

    task = process_video_task.apply_async(
        args=[video_path, watermark_path, output_path, transparency, coords['x'], coords['y']]
    )

    return jsonify({'task_id': task.id, 'type': 'video'}), 202

def process_zip_file(request, watermark_path, transparency, coords):
    """Procesar archivo ZIP"""
    zip_file = request.files['zip_file']
    if not zip_file.filename.lower().endswith('.zip'):
        if os.path.exists(watermark_path):
            os.remove(watermark_path)
        return jsonify({'error': 'El archivo debe ser un .zip.'}), 400

    zip_filename = f"zip_{int(time.time())}_{secure_filename(zip_file.filename)}"
    zip_path = os.path.join(app.config['UPLOAD_FOLDER'], zip_filename)
    stream_save(zip_file, zip_path)

    output_zip_name = f"processed_{os.path.splitext(secure_filename(zip_file.filename))[0]}.zip"
    output_zip_path = os.path.join(app.config['OUTPUT_FOLDER'], output_zip_name)

    task = process_zip_task.apply_async(
        args=[zip_path, watermark_path, output_zip_path, transparency, coords['x'], coords['y']]
    )

    return jsonify({'task_id': task.id, 'type': 'zip'}), 202

@app.route('/status/<task_id>')
def task_status(task_id):
    try:
        task = AsyncResult(task_id, app=celery)
        
        if task.state == 'PENDING':
            return jsonify({'state': 'PENDING', 'status': 'En cola...'})
        elif task.state == 'PROGRESS':
            return jsonify({
                'state': 'PROGRESS',
                'status': task.info.get('status', 'Procesando...'),
                'progress': task.info.get('percent', 0),
                'current': task.info.get('current', 0),
                'total': task.info.get('total', 1)
            })
        elif task.state == 'SUCCESS':
            return jsonify({
                'state': 'SUCCESS',
                'result': task.result,
                'status': 'Completado'
            })
        else:
            return jsonify({
                'state': 'FAILURE',
                'status': str(task.info) if task.info else 'Error desconocido'
            })
    except Exception as e:
        return jsonify({'state': 'ERROR', 'status': str(e)})

@app.route('/download/<filename>')
def download_file_single(filename):
    return send_from_directory(app.config['OUTPUT_FOLDER'], filename, as_attachment=True)

# ========================
# Manejo de errores
# ========================
@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Página no encontrada'}), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({'error': 'Error interno del servidor'}), 500

@app.errorhandler(413)
def too_large(e):
    return jsonify({'error': 'El archivo es demasiado grande (máximo 25GB)'}), 413

# ========================
# Punto de entrada
# ========================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, threaded=True)
