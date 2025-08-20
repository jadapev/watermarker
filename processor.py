import ffmpeg

def agregar_marca_de_agua_transparente(video_entrada, marca_de_agua, video_salida, transparencia=0.5, x="10", y="10", output_format="mp4"):
    """
    Agrega una marca de agua con transparencia y convierte al formato deseado.

    Args:
        video_entrada: Ruta al video de entrada.
        marca_de_agua: Ruta a la imagen de marca de agua (convertida a PNG).
        video_salida: Ruta de salida (incluye extensión).
        transparencia: Opacidad (0.0 = transparente, 1.0 = opaco).
        x, y: Posición de la marca de agua.
        output_format: Formato de salida ('mp4', 'webm', 'avi', 'mov').
    """
    try:
        video_stream = ffmpeg.input(video_entrada)
        watermark_stream = ffmpeg.input(marca_de_agua)
        watermark_with_alpha = watermark_stream.filter('colorchannelmixer', aa=transparencia)

        overlay_stream = ffmpeg.overlay(video_stream, watermark_with_alpha, x=x, y=y)

        output_args = {
            'overwrite_output': True
        }

        # Configuración específica por formato
        if output_format == 'webm':
            output_args['vcodec'] = 'libvpx-vp9'
            output_args['crf'] = 30
            output_args['pix_fmt'] = 'yuv420p'
        elif output_format == 'avi':
            output_args['vcodec'] = 'mpeg4'
            output_args['acodec'] = 'ac3'
        elif output_format == 'mov':
            output_args['vcodec'] = 'libx264'
            output_args['pix_fmt'] = 'yuv420p'
        else:  # mp4 u otros
            output_args['vcodec'] = 'libx264'
            output_args['pix_fmt'] = 'yuv420p'

        (
            overlay_stream
            .output(video_salida, **output_args)
            .run(overwrite_output=True)
        )
        print(f"Video procesado y guardado en {video_salida} (formato: {output_format})")
        return True
    except ffmpeg.Error as e:
        error_msg = e.stderr.decode() if e.stderr else str(e)
        print(f"Error al procesar el video {video_entrada}: {error_msg}")
        return False