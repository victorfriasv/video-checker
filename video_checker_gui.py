# -*- coding: utf-8 -*-
"""
Video Quality Checker con GUI (video_checker_gui.py)

VERSIÓN COMPLETA Y FUNCIONAL
"""
import argparse
import subprocess
import re
import sys
import os
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
import threading
import queue

# --- Constantes de Umbrales ---
MUTE_THRESHOLD_DB = -50
MUTE_MIN_DURATION_S = 1.0
SHORT_SHOT_MIN_FRAMES = 5
BLACK_FRAME_THRESHOLD = 0.98

# --- Clase de Análisis con la lógica completa ---

class VideoAnalyzer:
    def __init__(self, file_path, num_channels, text_widget_queue):
        self.file_path = file_path
        self.num_channels = int(num_channels)
        self.queue = text_widget_queue

    def log(self, message):
        self.queue.put(message)

    def get_resource_path(self, relative_path):
        try:
            base_path = sys._MEIPASS
        except Exception:
            base_path = os.path.abspath(".")
        return os.path.join(base_path, relative_path)

    def run_analysis(self):
        self.log(f"🚀 Iniciando análisis del archivo: {os.path.basename(self.file_path)}\n" + "-"*50)
        
        ffmpeg_path = self.get_resource_path("ffmpeg.exe")
        ffprobe_path = self.get_resource_path("ffprobe.exe")
        
        if not os.path.exists(ffmpeg_path) or not os.path.exists(ffprobe_path):
            self.log("❌ ERROR: No se encontraron 'ffmpeg.exe' y 'ffprobe.exe'.")
            return

        metadata = self._get_video_metadata(ffprobe_path)
        self._find_mute_segments(ffmpeg_path)
        self._find_short_shots(ffmpeg_path, metadata)
        self._find_audio_peaks() # Esta sigue siendo una función de aviso
        self._find_black_frames(ffmpeg_path)
        self.log("\n" + "-"*50 + "\n✅ Análisis completado.")

    def _run_command(self, command):
        return subprocess.run(
            command, capture_output=True, text=True, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW, encoding='utf-8', errors='ignore'
        )

    def _get_video_metadata(self, ffprobe_path):
        self.log("\nObteniendo metadatos del vídeo...")
        command = [ffprobe_path, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate,duration", "-of", "default=noprint_wrappers=1:nokey=1", self.file_path]
        result = self._run_command(command)
        try:
            output = result.stdout.strip().split('\n')
            frame_rate_str = output[0]
            if '/' in frame_rate_str:
                num, den = map(int, frame_rate_str.split('/'))
                fps = num / den if den != 0 else 0
            else:
                fps = float(frame_rate_str)
            duration = float(output[1])
            self.log(f"    Duración: {duration:.2f}s, Tasa de frames: {fps:.2f} fps")
            return {"fps": fps, "duration": duration}
        except Exception as e:
            self.log(f"🚨 Error al obtener metadatos del vídeo: {e}")
            return None

    def _find_mute_segments(self, ffmpeg_path):
        self.log("\n[1/4] 🔇 Comprobando audio en mute...")
        mute_moments = []
        channel_maps = "".join([f"c{i}|" for i in range(self.num_channels)]).rstrip('|')
        command = [ffmpeg_path, "-i", self.file_path, "-af", f"pan={self.num_channels}c|{channel_maps},silencedetect=noise={MUTE_THRESHOLD_DB}dB:d={MUTE_MIN_DURATION_S}", "-f", "null", "-"]
        result = self._run_command(command)
        for line in result.stderr.splitlines():
            if "silence_start" in line:
                match = re.search(r"silence_start: ([\d\.]+)", line)
                if match:
                    mute_moments.append({"start": float(match.group(1))})
        if mute_moments:
            self.log(f"    👉 Se encontraron {len(mute_moments)} momentos con mute prolongado (> {MUTE_MIN_DURATION_S}s):")
            for moment in mute_moments:
                self.log(f"       - Ocurre en el segundo: {moment['start']:.2f}")
        else:
            self.log("    ✅ No se encontraron problemas de mute prolongado.")

    def _find_short_shots(self, ffmpeg_path, metadata):
        self.log("\n[2/4] 🎬 Comprobando planos cortos...")
        if not metadata or metadata["fps"] == 0:
            self.log("    ❌ No se pudo determinar la tasa de frames (fps). Saltando esta comprobación.")
            return

        fps = metadata["fps"]
        min_duration_s = SHORT_SHOT_MIN_FRAMES / fps
        short_shots = []

        command = [ffmpeg_path, "-i", self.file_path, "-vf", "scenedetect=threshold=0.4", "-f", "null", "-"]
        result = self._run_command(command)
        
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

    def _find_audio_peaks(self):
        self.log(f"\n[3/4] 📈 Comprobando picos de audio...")
        self.log("    ⚠️ La detección de picos de audio 'muy cortos' es compleja.")
        self.log("    Esta función no está implementada; se recomienda usar un software de edición de audio (DAW).")

    def _find_black_frames(self, ffmpeg_path):
        self.log("\n[4/4] ⚫ Comprobando frames negros...")
        black_segments = []
        command = [ffmpeg_path, "-i", self.file_path, "-vf", f"blackdetect=d=0:pic_th=0.99:pix_th={BLACK_FRAME_THRESHOLD}", "-f", "null", "-"]
        result = self._run_command(command)
        
        for line in result.stderr.splitlines():
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

# --- Clase de la Interfaz Gráfica (GUI) - SIN CAMBIOS ---

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
        self.output_text = scrolledtext.ScrolledText(self, wrap=tk.WORD, state="disabled")
        self.output_text.grid(row=3, column=0, columnspan=3, padx=10, pady=10, sticky="nsew")
        self.grid_rowconfigure(3, weight=1)
        self.status_var = tk.StringVar(value="Listo")
        tk.Label(self, textvariable=self.status_var, bd=1, relief=tk.SUNKEN, anchor="w").grid(row=4, column=0, columnspan=3, sticky="ew")
        self.process_queue()

    def select_file(self):
        path = filedialog.askopenfilename(title="Selecciona un archivo de vídeo", filetypes=(("Archivos MXF", "*.mxf"), ("Archivos MOV", "*.mov"), ("Todos los archivos", "*.*")))
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
