import os

#Obtener directorio raiz.
BASE_DIR = os.path.abspath('.')

#Generar rutas a carpetas de entrada y salida.
OUTPUT_DIR = os.path.join(BASE_DIR,'outputs')
INPUT_DIR = os.path.join(BASE_DIR,'inputs')
WATERMARK_FOLDER = os.path.join(BASE_DIR,"watermarks",)
WATERMARK_IMG = os.path.join(WATERMARK_FOLDER,'LogoMarvel.png')
#
print('Directorio actual ->' + WATERMARK_IMG)