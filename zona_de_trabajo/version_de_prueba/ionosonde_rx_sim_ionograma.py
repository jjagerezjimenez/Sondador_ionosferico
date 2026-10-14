"""
Armado del ionograma.

Recibe cada perfil de altura ya integrado (vector de n_h alturas,
h = i * dh_km) y, con el tag 'sondeo' que viaja junto al perfil, sabe a que
frecuencia corresponde. Guarda:
  * matriz_db[f, h]: ionograma en dB sobre el piso de ruido de cada perfil
    (piso = mediana del perfil).
  * traza_h[f]: altura del eco mas fuerte (si supera 'umbral_db').
  * ultimo: el ultimo perfil recibido (lineal y en dB) con su frecuencia.
  * historial: los ultimos perfiles en dB (para el grafico altura-tiempo).
La interfaz grafica lee estos atributos.
"""
import collections
import threading
import numpy as np
import pmt
from gnuradio import gr

TAG_SONDEO = pmt.intern('sondeo')


class blk(gr.sync_block):
    """Armado del ionograma h'(f): ubica cada perfil integrado en la columna de su
    frecuencia (leida del tag 'sondeo') y detecta el eco mas fuerte."""

    def __init__(self, n_h=3200, dh_km=0.7494811, f_min=1.0, f_max=20.0, df_grilla=0.05,
                 umbral_db=12.0, h_min_km=80.0):
        gr.sync_block.__init__(self, name='Armado del ionograma',
                               in_sig=[(np.float32, int(n_h))], out_sig=None)
        self.n_h = int(n_h)
        self.dh_km = float(dh_km)
        self.f_min, self.f_max, self.df = float(f_min), float(f_max), float(df_grilla)
        self.n_f = int(round((self.f_max - self.f_min) / self.df)) + 1
        self.umbral_db = umbral_db
        self._i_hmin = int(np.ceil(float(h_min_km) / self.dh_km))
        self.lock = threading.Lock()
        self._info = {}
        self.contador = 0
        self.ultimo = None                                 # (contador, info, perfil_lin, perfil_db)
        self.historial = collections.deque(maxlen=200)     # (info, perfil_db)
        self.limpiar()

    def limpiar(self):
        with self.lock:
            self.matriz_db = np.full((self.n_f, self.n_h), np.nan, np.float32)
            self.traza_h = np.full(self.n_f, np.nan)
            self.traza_db = np.full(self.n_f, np.nan)

    def columnas(self, f_mhz, paso_mhz):
        """Columnas de la grilla de frecuencia que cubre un paso del barrido."""
        ancho = max(float(paso_mhz), self.df)
        i0 = int(np.floor((f_mhz - ancho / 2.0 - self.f_min) / self.df + 0.5))
        i1 = int(np.floor((f_mhz + ancho / 2.0 - self.f_min) / self.df + 0.5))
        i0, i1 = max(i0, 0), min(max(i1, i0 + 1), self.n_f)
        return i0, i1

    def work(self, input_items, output_items):
        perfiles = input_items[0]
        n = len(perfiles)
        base = self.nitems_read(0)
        info_en = {t.offset - base: pmt.to_python(t.value)
                   for t in self.get_tags_in_window(0, 0, n, TAG_SONDEO)}
        for j in range(n):
            if j in info_en:
                self._info = info_en[j]
            perfil = np.array(perfiles[j], dtype=np.float32)
            piso = float(np.median(perfil)) or 1e-12
            perfil_db = 20.0 * np.log10(np.maximum(perfil, 1e-12) / piso)
            f = self._info.get('f_mhz')
            with self.lock:
                if f is not None and self.f_min - self.df <= f <= self.f_max + self.df:
                    i0, i1 = self.columnas(f, self._info.get('paso_mhz', 0.0))
                    self.matriz_db[i0:i1] = perfil_db
                    k = self._i_hmin + int(np.argmax(perfil_db[self._i_hmin:]))
                    detectado = perfil_db[k] >= float(self.umbral_db)
                    self.traza_h[i0:i1] = k * self.dh_km if detectado else np.nan
                    self.traza_db[i0:i1] = perfil_db[k] if detectado else np.nan
                self.contador += 1
                self.ultimo = (self.contador, dict(self._info), perfil, perfil_db)
                self.historial.append((dict(self._info), perfil_db))
        return n
