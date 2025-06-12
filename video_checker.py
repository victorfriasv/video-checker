# -*- coding: utf-8 -*-
"""
Video Quality Checker (video_checker.py)

Analiza un archivo de vídeo (.mxf u otros formatos compatibles con FFmpeg)
para detectar problemas comunes de control de calidad:
- Segmentos de audio en silencio prolongado.
- Planos (tomas) de vídeo excesivamente cortos.
- Picos de audio (función simplificada).
- Frames o segmentos de vídeo completamente negros.

Esta versión está preparada para ser empaquetada en un ejecutable (.exe)
con PyInstaller, ya que busca los binarios de FFmpeg y FFprobe en su
misma ruta en lugar de depender del PATH del sistema.
"""
import argparse
import subprocess
import re
import sys
import os

# --- Constantes de Umbrales (ajustables) ---
MUTE_THRESHOLD_DB = -50      # Nivel de dB considerado "mute"
MUTE_MIN_DURATION_S = 1.0    # Duración mínima para reportar mute (1 segundo)
PEAK_THRESHOLD_DBFS = -1.0   # Umbral para picos de audio (dBFS, 0 es el máximo)
PEAK_MAX_DURATION_S = 0.2    # Duración máxima de un "pico corto"
SHORT_SHOT_MIN_FRAMES = 5    # Mínimo de frames para que un plano no sea "corto"
BLACK_FRAME_THRESHOLD = 0.98 # Porcentaje de píxeles negros para considerar un frame "negro"

def get_resource_path(relative_path):
    """
    Obtiene la ruta absoluta al recurso. Funciona tanto en desarrollo
    como cuando está empaquetado con PyInstaller.
    """
    try:
        # PyInstaller crea una carpeta temporal y guarda su ruta en `_MEIPASS`.
        base_path = sys._MEIPASS
    except Exception:
        # Si no está empaquetado, la ruta base es el directorio del script.
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

def check_ffmpeg_available():
    """
    Comprueba si ffmpeg.exe y ffprobe.exe están disponibles junto al script/ejecutable.
    """
    ffmpeg_path = get_resource_path("ffmpeg.exe")
    ffprobe_path = get_resource_path("ffprobe.exe")
    try:
        if not os.path.exists(ffmpeg_path) or not os.path.exists(ffprobe_path):
            raise FileNotFoundError
        # Ejecuta un comando silencioso para verificar que los binarios son válidos.
        # CREATE_NO_WINDOW evita que se abra una ventana de consola en Windows.
        subprocess.run(
            [ffmpeg_path, "-version"],
            capture_output=True, check=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("❌ ERROR: No se encontraron 'ffmpeg.exe' y 'ffprobe.exe'.")
        print("Por favor, asegúrate de que ambos archivos estén en la misma carpeta que este programa.")
        return False

def get_video_metadata(file_path):
    """Obtiene metadatos básicos del vídeo como duración y tasa de frames."""
    ffprobe_path = get_resource_path("ffprobe.exe")
    try:
        command = [
            ffprobe_path, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate,duration",
            "-of", "default=noprint_wrappers=1:nokey=1", file_path
        ]
        result = subprocess.run(
            command, capture_output=True, text=True, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        output = result.stdout.strip().split('\n')
        
        frame_rate_str = output[0]
        if '/' in frame_rate_str:
            num, den = map(int, frame_rate_str.split('/'))
            fps = num / den if den != 0 else 0
        else:
            fps = float(frame_rate_str)
            
        duration = float(output[1])
        return {"fps": fps, "duration": duration}
    except (subprocess.CalledProcessError, IndexError, ValueError) as e:
        print(f"🚨 Error al obtener metadatos del vídeo: {e}")
        return None

def find_mute_segments(file_path, num_channels):
    """Encuentra segmentos en mute que duren más de MUTE_MIN_DURATION_S."""
    ffmpeg_path = get_resource_path("ffmpeg.exe")
    print("\n[1/4] 🔇 Comprobando audio en mute...")
    mute_moments = []
    
    channel_maps = "".join([f"c{i}|" for i in range(num_channels)]).rstrip('|')
    command = [
        ffmpeg_path, "-i", file_path, "-af",
        f"pan={num_channels}c|{channel_maps},silencedetect=noise={MUTE_THRESHOLD_DB}dB:d={MUTE_MIN_DURATION_S}",
        "-f", "null", "-"
    ]
    
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        for line in result.stderr.splitlines():
            if "silence_start" in line:
                match = re.search(r"silence_start: ([\d\.]+)", line)
                if match:
                    mute_moments.append({"start": float(match.group(1))})
    except Exception as e:
        print(f"Ocurrió un error inesperado al analizar el audio: {e}")
        return None

    if mute_moments:
        print(f"    👉 Se encontraron {len(mute_moments)} momentos con mute prolongado (> {MUTE_MIN_DURATION_S}s):")
        for moment in mute_moments:
            print(f"       - Ocurre en el segundo: {moment['start']:.2f}")
    else:
        print("    ✅ No se encontraron problemas de mute prolongado.")
    return mute_moments

def find_short_shots(file_path, metadata):
    """Encuentra planos (shots) con una duración inferior a SHORT_SHOT_MIN_FRAMES."""
    ffmpeg_path = get_resource_path("ffmpeg.exe")
    print("\n[2/4] 🎬 Comprobando planos cortos...")
    if not metadata or metadata["fps"] == 0:
        print("    ❌ No se pudo determinar la tasa de frames (fps). Saltando esta comprobación.")
        return None

    fps = metadata["fps"]
    min_duration_s = SHORT_SHOT_MIN_FRAMES / fps
    short_shots = []

    command = [
        ffmpeg_path, "-i", file_path, "-vf", 
        f"scenedetect=threshold=0.4",
        "-f", "null", "-"
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        scene_cut_times = [0.0]
        for line in result.stderr.splitlines():
            if "pts_time" in line:
                match = re.search(r"pts_time:([\d\.]+)", line)
                if match:
                    scene_cut_times.append(float(match.group(1)))
        
        scene_cut_times.append(metadata["duration"])

        for i in range(len(scene_cut_times) - 1):
            start_time = scene_cut_times[i]
            duration = scene_cut_times[i+1] - start_time
            if 0 < duration < min_duration_s:
                short_shots.append({"start": start_time, "duration_frames": duration * fps})

    except Exception as e:
        print(f"Ocurrió un error inesperado al detectar planos: {e}")
        return None
        
    if short_shots:
        print(f"    👉 Se encontraron {len(short_shots)} planos con menos de {SHORT_SHOT_MIN_FRAMES} frames:")
        for shot in short_shots:
            print(f"       - Plano corto en el segundo: {shot['start']:.2f} (dura ~{shot['duration_frames']:.1f} frames)")
    else:
        print("    ✅ No se encontraron planos demasiado cortos.")
    return short_shots

def find_audio_peaks(file_path, num_channels):
    """Encuentra picos de audio muy cortos y por encima del umbral (función simplificada)."""
    print(f"\n[3/4] 📈 Comprobando picos de audio (por encima de {PEAK_THRESHOLD_DBFS} dBFS)...")
    print("    ⚠️ La detección de picos de audio 'muy cortos' es compleja y requeriría un análisis de la onda de audio.")
    print("    Por simplicidad, esta función no está implementada en este script básico.")
    print("    Se recomienda usar un software de edición de audio (DAW) para este tipo de análisis detallado.")
    return []

def find_black_frames(file_path):
    """Encuentra segmentos de frames negros en el vídeo."""
    ffmpeg_path = get_resource_path("ffmpeg.exe")
    print("\n[4/4] ⚫ Comprobando frames negros...")
    black_segments = []

    command = [
        ffmpeg_path, "-i", file_path, "-vf",
        f"blackdetect=d=0:pic_th=0.99:pix_th={BLACK_FRAME_THRESHOLD}",
        "-f", "null", "-"
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        for line in result.stderr.splitlines():
            if "black_start" in line:
                start_match = re.search(r"black_start:([\d\.]+)", line)
                end_match = re.search(r"black_end:([\d\.]+)", line)
                duration_match = re.search(r"black_duration:([\d\.]+)", line)
                if start_match and end_match and duration_match:
                    black_segments.append({
                        "start": float(start_match.group(1)),
                        "end": float(end_match.group(1)),
                        "duration": float(duration_match.group(1))
                    })
    except Exception as e:
        print(f"Ocurrió un error inesperado al detectar frames negros: {e}")
        return None

    if black_segments:
        print(f"    👉 Se encontraron {len(black_segments)} segmentos con frames negros:")
        for segment in black_segments:
            print(f"       - Inicio: {segment['start']:.2f}s, Fin: {segment['end']:.2f}s (Duración: {segment['duration']:.2f}s)")
    else:
        print("    ✅ No se encontraron frames negros.")
    return black_segments

def main():
    """Función principal del script."""
    parser = argparse.ArgumentParser(
        description="Analizador de archivos de vídeo para control de calidad.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--file", required=True, help="Ruta al archivo de vídeo a analizar.")
    parser.add_argument("--channels", required=True, type=int, help="Número de canales de audio a revisar.")
    
    args = parser.parse_args()
    
    if not os.path.exists(args.file):
        print(f"❌ ERROR: El archivo especificado no existe: {args.file}")
        sys.exit(1)
        
    if not check_ffmpeg_available():
        sys.exit(1)

    print(f"🚀 Iniciando análisis del archivo: {os.path.basename(args.file)}")
    print("-" * 50)

    video_metadata = get_video_metadata(args.file)

    find_mute_segments(args.file, args.channels)
    find_short_shots(args.file, video_metadata)
    find_audio_peaks(args.file, args.channels)
    find_black_frames(args.file)

    print("-" * 50)
    print("✅ Análisis completado.")

if __name__ == "__main__":
    main()
