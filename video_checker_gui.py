# -*- coding: utf-8 -*-
"""
Video Quality Checker con GUI (video_checker_gui.py)

VERSIÓN 3.0 - COMPLETA Y FUNCIONAL
Lógica de análisis por canal y detección de picos con NumPy.
"""
import subprocess
import re
import sys
import os
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
import threading
import queue
import numpy as np

# --- Constantes de Umbrales ---
MUTE_THRESHOLD_DB = -50
MUTE_MIN_DURATION_S = 1.0
SHORT_SHOT_MIN_FRAMES = 5
BLACK_FRAME_THRESHOLD = 0.98
PEAK_DBFS_THRESHOLD = -1.5  # Umbral en dBFS para considerar un "pico"
PEAK_MAX_DURATION_S = 0.2    # Duración máxima para que un pico sea "corto"

# --- Clase de Análisis ---
class VideoAnalyzer:
    def __init__(self, file_path, num_channels, text_widget_queue):
        self.file_path = file_path
        self.num_channels = int(num_channels)
        self.queue = text_widget_queue
        # Convertir umbral de pico de dBFS a amplitud lineal para NumPy
        self.peak_amplitude_threshold = 10 ** (PEAK_DBFS_THRESHOLD / 20)

    def log(self, message):
        """ Envía mensajes a la GUI de forma segura desde el hilo. """
        self.queue.put(message)

    def get_resource_path(self, relative_path):
        """ Obtiene la ruta absoluta al recurso, funciona para dev y para PyInstaller. """
        try:
            base_path = sys._MEIPASS
        except Exception:
            base_path = os.path.abspath(".")
        return os.path.join(base_path, relative_path)

    def run_analysis(self):
        """ Orquesta toda la secuencia de análisis. """
        self.log(f"🚀 Iniciando análisis del archivo: {os.path.basename(self.file_path)}\n" + "-"*50)
        
        ffmpeg_path = self.get_resource_path("ffmpeg.exe")
        ffprobe_path = self.get_resource_path("ffprobe.exe")
        
        if not os.path.exists(ffmpeg_path) or not os.path.exists(ffprobe_path):
            self.log("❌ ERROR: No se encontraron 'ffmpeg.exe' y 'ffprobe.exe'.")
            self.log("Asegúrate de que estén en la misma carpeta que el programa.")
            return

        metadata = self._get_video_metadata(ffprobe_path)
        if metadata is None:
            self.log("\n❌ No se pudo continuar sin los metadatos del vídeo.")
            return
            
        self._find_mute_segments_per_channel(ffmpeg_path)
        self._find_short_shots(ffmpeg_path, metadata)
        self._find_audio_peaks(ffmpeg_path, metadata)
        self._find_black_frames(ffmpeg_path)
        self.log("\n" + "-"*50 + "\n✅ Análisis completado.")

    def _run_command(self, command, capture_stdout=True):
        """ Ejecuta un comando de FFmpeg/FFprobe como un subproceso. """
        return subprocess.Popen(
            command, 
            stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL, 
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW
        )

    def _get_video_metadata(self, ffprobe_path):
        self.log("\nObteniendo metadatos del vídeo...")
        command = [ffprobe_path, "-v", "error", "-select_streams", "a:0,v:0", "-show_entries", "stream=r_frame_rate,duration,sample_rate,channels", "-of", "default=noprint_wrappers=1:nokey=1", self.file_path]
        proc = self._run_command(command)
        stdout, stderr = proc.communicate()
        stdout = stdout.decode('utf-8', errors='ignore')
        try:
            lines = stdout.strip().split('\n')
            data = {'fps': 25, 'duration': 0, 'sample_rate': 48000, 'channels': self.num_channels}
            data['sample_rate'] = int(lines[0])
            data['channels'] = int(lines[1])
            data['fps'] = eval(lines[2])
            data['duration'] = float(lines[3])
            self.log(f"    Duración: {data['duration']:.2f}s, Tasa de frames: {data['fps']:.2f} fps, Audio: {data['sample_rate']} Hz")
            return data
        except Exception as e:
            self.log(f"🚨 Error al obtener metadatos: {e}\nSalida de FFprobe:\n{stdout}")
            return None

    def _find_mute_segments_per_channel(self, ffmpeg_path):
        self.log("\n[1/4] 🔇 Comprobando mute POR CANAL (puede ser lento)...")
        found_any_mute = False
        for i in range(self.num_channels):
            channel_num = i + 1
            self.log(f"    Analizando Canal {channel_num}/{self.num_channels}...")
            command = [ffmpeg_path, "-i", self.file_path, "-af", f"channelsplit=channel_layout=mono:channels=c{i}[out];[out]silencedetect=noise={MUTE_THRESHOLD_DB}dB:d={MUTE_MIN_DURATION_S}", "-f", "null", "-"]
            proc = self._run_command(command, capture_stdout=False)
            _, stderr = proc.communicate()
            stderr = stderr.decode('utf-8', errors='ignore')
            
            mute_moments = []
            for line in stderr.splitlines():
                if "silence_start" in line:
                    match = re.search(r"silence_start: ([\d\.]+)", line)
                    if match:
                        mute_moments.append({"start": float(match.group(1))})
            
            if mute_moments:
                found_any_mute = True
                self.log(f"    👉 Se encontró mute prolongado en el CANAL {channel_num}:")
                for moment in mute_moments:
                    self.log(f"       - Ocurre en el segundo: {moment['start']:.2f}")

        if not found_any_mute:
            self.log("    ✅ No se encontraron problemas de mute prolongado en ningún canal.")

    def _find_short_shots(self, ffmpeg_path, metadata):
        self.log("\n[2/4] 🎬 Comprobando planos cortos...")
        if not metadata or metadata.get("fps") == 0:
            self.log("    ❌ No se pudo determinar la tasa de frames (fps). Saltando esta comprobación.")
            return

        fps = metadata["fps"]
        min_duration_s = SHORT_SHOT_MIN_FRAMES / fps
        short_shots = []

        command = [ffmpeg_path, "-i", self.file_path, "-vf", "scenedetect=threshold=0.4", "-f", "null", "-"]
        proc = self._run_command(command, capture_stdout=False)
        _, stderr = proc.communicate()
        stderr = stderr.decode('utf-8', errors='ignore')
        
        scene_cut_times = [0.0]
        for line in stderr.splitlines():
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

        if short_shots:
            self.log(f"    👉 Se encontraron {len(short_shots)} planos con menos de {SHORT_SHOT_MIN_FRAMES} frames:")
            for shot in short_shots:
                self.log(f"       - Plano corto en el segundo: {shot['start']:.2f} (dura ~{shot['duration_frames']:.1f} frames)")
        else:
            self.log("    ✅ No se encontraron planos demasiado cortos.")

    def _find_audio_peaks(self, ffmpeg_path, metadata):
        self.log("\n[3/4] 📈 Comprobando picos de audio cortos con NumPy...")
        if not metadata or not metadata.get("sample_rate"):
            self.log("    ❌ No hay metadatos de audio para analizar.")
            return

        sample_rate = metadata['sample_rate']
        command = [ffmpeg_path, "-i", self.file_path, "-f", "f32le", "-acodec", "pcm_f32le", "-ar", str(sample_rate), "-"]
        proc = self._run_command(command)

        audio_data_raw = proc.stdout.read()
        proc.wait()

        if not audio_data_raw:
            self.log("    ❌ No se pudo decodificar el audio.")
            return

        audio_data = np.frombuffer(audio_data_raw, dtype=np.float32)
        audio_data = audio_data.reshape(-1, self.num_channels)
        num_samples = audio_data.shape[0]
        found_any_peak = False

        for i in range(self.num_channels):
            channel_num = i + 1
            self.log(f"    Analizando picos en Canal {channel_num}/{self.num_channels}...")
            channel_data = audio_data[:, i]
            
            is_peak = np.abs(channel_data) > self.peak_amplitude_threshold
            if not np.any(is_peak):
                continue

            diff = np.diff(is_peak.astype(int), prepend=0, append=0)
            starts = np.where(diff == 1)[0]
            ends = np.where(diff == -1)[0]
            
            short_peaks = []
            for start, end in zip(starts, ends):
                duration_s = (end - start) / sample_rate
                if 0 < duration_s < PEAK_MAX_DURATION_S:
                    short_peaks.append(start / sample_rate)
            
            if short_peaks:
                found_any_peak = True
                self.log(f"    👉 Se encontraron {len(short_peaks)} picos cortos en el CANAL {channel_num}:")
                for t in short_peaks:
                    self.log(f"       - Ocurre en el segundo: {t:.2f}")

        if not found_any_peak:
            self.log("    ✅ No se encontraron picos de audio cortos y fuertes.")

    def _find_black_frames(self, ffmpeg_path):
        self.log("\n[4/4] ⚫ Comprobando frames negros...")
        black_segments = []
        command = [ffmpeg_path, "-i", self.file_path, "-vf", f"blackdetect=d=0:pic_th=0.99:pix_th={BLACK_FRAME_THRESHOLD}", "-f", "null", "-"]
        proc = self._run_command(command, capture_stdout=False)
        _, stderr = proc.communicate()
        stderr = stderr.decode('utf-8', errors='ignore')
        
        for line in stderr.splitlines():
            if "black_start" in line:
                start_match = re.search(r"black_start:([\d\.]+)", line)
                end_match = re.search(r"black_end:([\d\.]+)", line)
                duration_match = re.search(r"black_duration:([\d\.]+)", line)
                if start_match and end_match and duration_match:
                    black_segments.append({"start": float(start_match.group(1)), "end": float(end_match.group(1)), "duration": float(duration_match.group(1))})
        
        if black_segments:
            self.log(f"    👉 Se encontraron {len(black_segments)} segmentos con frames negros:")
            for segment in black_segments:
                self.log(f"       - Inicio: {segment['start']:.2f}s, Fin: {segment['end']:.2f}s (Duración: {segment['duration']:.2f}s)")
        else:
            self.log("    ✅ No se encontraron frames negros.")

# --- Clase de la Interfaz Gráfica (GUI) ---

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Analizador de Vídeo QC")
        self.geometry("700x550")

        self.file_path = tk.StringVar()
        self.num_channels = tk.StringVar(value="8")
        self.queue = queue.Queue()

        self.grid_columnconfigure(1, weight=1)

        tk.Label(self, text="Archivo de Vídeo:").grid(row=0, column=0, padx=10, pady=10, sticky="w")
        tk.Entry(self, textvariable=self.file_path, state="readonly").grid(row=0, column=1, padx=10, pady=10, sticky="ew")
        tk.Button(self, text="Seleccionar...", command=self.select_file).grid(row=0, column=2, padx=10, pady=10)

        tk.Label(self, text="Nº de Canales de Audio:").grid(row=1, column=0, padx=10, pady=5, sticky="w")
        tk.Entry(self, textvariable=self.num_channels).grid(row=1, column=1, padx=10, pady=5, sticky="w")

        self.analyze_button = tk.Button(self, text="Analizar Vídeo", command=self.start_analysis)
        self.analyze_button.grid(row=2, column=0, columnspan=3, pady=10)

        self.output_text = scrolledtext.ScrolledText(self, wrap=tk.WORD, state="disabled", bg="#1e1e1e", fg="white")
        self.output_text.grid(row=3, column=0, columnspan=3, padx=10, pady=10, sticky="nsew")
        self.grid_rowconfigure(3, weight=1)

        self.status_var = tk.StringVar(value="Listo")
        tk.Label(self, textvariable=self.status_var, bd=1, relief=tk.SUNKEN, anchor="w").grid(row=4, column=0, columnspan=3, sticky="ew")

        self.process_queue()

    def select_file(self):
        path = filedialog.askopenfilename(
            title="Selecciona un archivo de vídeo",
            filetypes=(("Archivos MXF", "*.mxf"), ("Archivos MOV", "*.mov"), ("Todos los archivos", "*.*"))
        )
        if path:
            self.file_path.set(path)

    def start_analysis(self):
        if not self.file_path.get():
            messagebox.showerror("Error", "Por favor, selecciona un archivo de vídeo primero.")
            return
        try:
            channels = int(self.num_channels.get())
            if channels <= 0: raise ValueError
        except ValueError:
            messagebox.showerror("Error", "El número de canales debe ser un entero positivo.")
            return

        self.analyze_button.config(state="disabled")
        self.output_text.config(state="normal")
        self.output_text.delete(1.0, tk.END)
        self.output_text.config(state="disabled")
        self.status_var.set("Analizando, por favor espera...")

        analyzer = VideoAnalyzer(self.file_path.get(), channels, self.queue)
        self.analysis_thread = threading.Thread(target=analyzer.run_analysis, daemon=True)
        self.analysis_thread.start()

    def process_queue(self):
        try:
            while True:
                message = self.queue.get_nowait()
                self.output_text.config(state="normal")
                self.output_text.insert(tk.END, message + "\n")
                self.output_text.config(state="disabled")
                self.output_text.see(tk.END)
        except queue.Empty:
            pass
        
        if hasattr(self, 'analysis_thread') and not self.analysis_thread.is_alive() and self.analyze_button['state'] == 'disabled':
            self.analyze_button.config(state="normal")
            if "Análisis completado" in self.output_text.get(1.0, tk.END):
                self.status_var.set("Análisis completado con éxito.")
            else:
                 self.status_var.set("Análisis finalizado con errores.")

        self.after(100, self.process_queue)

if __name__ == "__main__":
    app = App()
    app.mainloop()
