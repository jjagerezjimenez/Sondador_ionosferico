"""
Monitor de la senal recibida (solo visualizacion).

Cada 'cada_n_parejas' parejas copia una pareja completa de disparos
(A y luego B = 2*n_vec muestras) de la senal recibida y de los ecos sin
ruido, junto con la informacion del tag 'sondeo' (frecuencia).
La interfaz grafica lee el atributo 'capturas'.
"""
import collections
import threading
import numpy as np
import pmt
from gnuradio import gr

TAG_SONDEO = pmt.intern('sondeo')


class blk(gr.sync_block):
    """Monitor (para la GUI): guarda cada tanto una pareja de disparos A/B de la
    senal recibida (entrada 0) y de los ecos sin ruido (entrada 1)."""

    def __init__(self, n_vec=3333, cada_n_parejas=6):
        gr.sync_block.__init__(self, name='Monitor senal recibida',
                               in_sig=[np.complex64, np.complex64], out_sig=None)
        self._n = 2 * int(n_vec)
        self._cada = max(1, int(cada_n_parejas))
        self._info = {}
        self._cap = None
        self.lock = threading.Lock()
        self.capturas = collections.deque(maxlen=8)   # (n_pareja, info, rx, limpia)

    def work(self, input_items, output_items):
        rx, limpia = input_items
        n = len(rx)
        base = self.nitems_read(0)
        tags = sorted(self.get_tags_in_window(0, 0, n, TAG_SONDEO), key=lambda t: t.offset)
        it = 0
        i = 0
        while i < n:
            pos = base + i
            while it < len(tags) and tags[it].offset <= pos:
                self._info = pmt.to_python(tags[it].value)
                it += 1
            if self._cap is None:
                resto = pos % self._n
                if resto:                                   # esperar el inicio de la pareja
                    i += self._n - resto
                    continue
                pareja = pos // self._n
                if pareja % self._cada:
                    i += self._n
                    continue
                self._cap = [pareja, dict(self._info), np.empty(self._n, np.complex64),
                             np.empty(self._n, np.complex64), 0]
            cap = self._cap
            k = min(self._n - cap[4], n - i)
            cap[2][cap[4]:cap[4] + k] = rx[i:i + k]
            cap[3][cap[4]:cap[4] + k] = limpia[i:i + k]
            cap[4] += k
            i += k
            if cap[4] == self._n:
                with self.lock:
                    self.capturas.append(tuple(cap[:4]))
                self._cap = None
        while it < len(tags):
            self._info = pmt.to_python(tags[it].value)
            it += 1
        return n
