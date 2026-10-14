"""
Monitor de la compresion de pulso (solo visualizacion).

Entradas (una muestra por muestra de disparo, n_vec por pareja):
  0: salida del correlador A, 1: salida del correlador B, 2: suma A + B.
Cada 'cada_n_parejas' parejas copia los n_vec valores de cada entrada.
Usa la misma regla que el monitor de la senal recibida, asi la GUI puede
comparar la misma pareja en los dos monitores.
"""
import collections
import threading
import numpy as np
import pmt
from gnuradio import gr

TAG_SONDEO = pmt.intern('sondeo')


class blk(gr.sync_block):
    """Monitor (para la GUI): guarda cada tanto la salida de los correladores A y B
    y de la suma A + B para una misma pareja de disparos."""

    def __init__(self, n_vec=3333, cada_n_parejas=6):
        gr.sync_block.__init__(self, name='Monitor compresion de pulso',
                               in_sig=[np.complex64, np.complex64, np.complex64], out_sig=None)
        self._n = int(n_vec)
        self._cada = max(1, int(cada_n_parejas))
        self._info = {}
        self._cap = None
        self.lock = threading.Lock()
        self.capturas = collections.deque(maxlen=8)   # (n_pareja, info, A, B, A+B)

    def work(self, input_items, output_items):
        n = len(input_items[0])
        base = self.nitems_read(2)
        tags = sorted(self.get_tags_in_window(2, 0, n, TAG_SONDEO), key=lambda t: t.offset)
        it = 0
        i = 0
        while i < n:
            pos = base + i
            while it < len(tags) and tags[it].offset <= pos:
                self._info = pmt.to_python(tags[it].value)
                it += 1
            if self._cap is None:
                resto = pos % self._n
                if resto:
                    i += self._n - resto
                    continue
                pareja = pos // self._n
                if pareja % self._cada:
                    i += self._n
                    continue
                self._cap = [pareja, dict(self._info)] + \
                    [np.empty(self._n, np.complex64) for _ in range(3)] + [0]
            cap = self._cap
            k = min(self._n - cap[5], n - i)
            for j in range(3):
                cap[2 + j][cap[5]:cap[5] + k] = input_items[j][i:i + k]
            cap[5] += k
            i += k
            if cap[5] == self._n:
                with self.lock:
                    self.capturas.append(tuple(cap[:5]))
                self._cap = None
        while it < len(tags):
            self._info = pmt.to_python(tags[it].value)
            it += 1
        return n
