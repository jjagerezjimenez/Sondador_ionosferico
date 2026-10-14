"""
Simulador del sondador (reemplaza a la Red Pitaya en la simulacion).

Genera, disparo a disparo, la senal que llegaria al receptor:
  * Transmisor: pulso BPSK de 16 chips (spb muestras por chip) con el
    codigo Golay A en los disparos pares y el B en los impares.
  * Ionosfera: capas parabolicas E y F2. Para cada frecuencia se calcula
    la altura virtual h'(f) del eco (retardo de grupo) y su atenuacion
    (absorcion en la capa D y cerca de la frecuencia critica).
  * Canal: Doppler (la fase del eco gira de un disparo al siguiente) y
    ruido gaussiano complejo de potencia 1.

Salida 0: senal recibida = ecos + ruido (banda base compleja).
Salida 1: los mismos ecos SIN ruido (solo para graficar).

Cada frecuencia dura 2*K disparos (K parejas A/B). En su primera muestra
se agrega el tag 'sondeo' = {f_mhz, paso_mhz, paso, n_pasos, barrido, modo}.
Los parametros de la ionosfera y del canal se pueden cambiar en ejecucion;
se aplican al comenzar la siguiente frecuencia.
"""
import numpy as np
import pmt
from gnuradio import gr

C_LUZ = 299792458.0
HM_E, YM_E = 110.0, 20.0        # capa E: altura del maximo y semiespesor [km]
TAG_SONDEO = pmt.intern('sondeo')


def retardo_reflexion(x):
    """Altura virtual extra, en unidades del semiespesor ym, al reflejarse
    en una capa parabolica. x = f / fc (< 1)."""
    x = min(max(x, 0.0), 0.999)
    return 0.5 * x * np.log((1.0 + x) / (1.0 - x))


def retardo_cruce(a):
    """Altura virtual extra, en unidades de ym, al atravesar completa una
    capa parabolica (descontado su espesor real 2*ym). a = fc / f (< 1)."""
    a = min(max(a, 1e-6), 0.999)
    return np.log((1.0 + a) / (1.0 - a)) / a - 2.0


class blk(gr.sync_block):
    """Simulador: transmisor Golay A/B + ionosfera (capas E y F2) + Doppler + ruido.
    Salida 0 = senal recibida (ecos + ruido). Salida 1 = ecos sin ruido (solo para graficar)."""

    def __init__(self, samp_rate=200000.0, n_vec=3333, spb=6, K=25,
                 code_A=(1, 1, 1, -1, 1, 1, -1, 1, 1, 1, 1, -1, -1, -1, 1, -1),
                 code_B=(1, 1, 1, -1, 1, 1, -1, 1, -1, -1, -1, 1, 1, 1, -1, 1),
                 foE=3.0, foF2=8.0, hmF2=300.0, snr_db=-20.0, doppler_hz=0.2,
                 modo_barrido=1, f_fija=5.0, f_ini=1.0, f_fin=20.0, paso_mhz=0.1,
                 segundo_salto=1, pulso_directo=0):
        gr.sync_block.__init__(self, name='Simulador TX Golay + ionosfera',
                               in_sig=None, out_sig=[np.complex64, np.complex64])
        # Parametros fijos (no cambian en ejecucion)
        self._fs = float(samp_rate)
        self._n_vec = int(n_vec)
        self._K = int(K)
        self._dh_km = C_LUZ / (2.0 * self._fs) / 1000.0
        self._prt = self._n_vec / self._fs
        pulso_A = np.repeat(np.asarray(code_A, dtype=np.float32), int(spb))
        pulso_B = np.repeat(np.asarray(code_B, dtype=np.float32), int(spb))
        self._pulsos = np.stack([pulso_A, pulso_B]).astype(np.complex64)
        self._n_pulso = len(pulso_A)
        # Parametros que se pueden cambiar en ejecucion (GRC genera callbacks
        # porque los atributos se llaman igual que los parametros)
        self.foE, self.foF2, self.hmF2 = foE, foF2, hmF2
        self.snr_db, self.doppler_hz = snr_db, doppler_hz
        self.modo_barrido, self.f_fija = modo_barrido, f_fija
        self.f_ini, self.f_fin, self.paso_mhz = f_ini, f_fin, paso_mhz
        self.segundo_salto, self.pulso_directo = segundo_salto, pulso_directo
        # Estado interno
        self._rng = np.random.default_rng()
        self._rx = np.zeros(0, np.complex64)
        self._limpia = np.zeros(0, np.complex64)
        self._pos = 0
        self._disparo = 0           # disparos generados (0 = primer A)
        self._barrido = 0
        self._i_paso = 0
        self._frecuencias = None
        self._clave_barrido = None

    # ------------------------------------------------------------------
    # Modelo ionosferico
    # ------------------------------------------------------------------
    def ecos(self, f_mhz):
        """Ecos a la frecuencia f_mhz: lista de (h_virtual_km, nivel_dB, nombre).
        nivel_dB es relativo al eco de referencia (el que define la SNR)."""
        f = float(f_mhz)
        foE, foF2, hmF2 = float(self.foE), float(self.foF2), float(self.hmF2)
        ymF = 0.35 * hmF2                 # semiespesor de F2 [km]
        h0E, h0F = HM_E - YM_E, hmF2 - ymF
        absorcion = 40.0 / (f + 1.0) ** 2     # capa D, ~1/f^2 [dB]
        ecos = []
        if f < foE:
            x = f / foE
            h = h0E + YM_E * retardo_reflexion(x)
            perdida = absorcion + 5.0 * np.log10(1.0 / (1.0 - min(x, 0.999) ** 2))
            ecos.append((h, -perdida, 'E'))
        elif f < foF2:
            x = f / foF2
            h = h0F + YM_E * retardo_cruce(foE / f) + ymF * retardo_reflexion(x)
            perdida = absorcion + 5.0 * np.log10(1.0 / (1.0 - min(x, 0.999) ** 2))
            ecos.append((h, -perdida, 'F2'))
        if int(self.segundo_salto) and ecos:
            h1, nivel1, _ = ecos[0]
            # doble recorrido: el doble de absorcion, mas dispersion y reflexion en el suelo
            ecos.append((2.0 * h1, 2.0 * nivel1 - 9.0, '2F' if ecos[0][2] == 'F2' else '2E'))
        if int(self.pulso_directo):
            ecos.append((0.0, 30.0, 'TX'))
        return ecos

    # ------------------------------------------------------------------
    # Planificacion del barrido
    # ------------------------------------------------------------------
    def _siguiente_frecuencia(self):
        if int(self.modo_barrido) == 0:
            self._clave_barrido = None
            f = float(self.f_fija)
            return f, {'f_mhz': f, 'paso_mhz': 0.0, 'paso': 0, 'n_pasos': 1,
                       'barrido': self._barrido, 'modo': 'fija'}
        clave = (float(self.f_ini), float(self.f_fin), float(self.paso_mhz))
        if clave != self._clave_barrido:          # cambio de barrido: empezar de nuevo
            f_ini, f_fin, paso = clave
            self._frecuencias = np.round(np.arange(f_ini, f_fin + paso / 2.0, paso), 4)
            self._clave_barrido = clave
            self._i_paso = 0
            self._barrido += 1
        elif self._i_paso >= len(self._frecuencias):
            self._i_paso = 0
            self._barrido += 1
        f = float(self._frecuencias[self._i_paso])
        info = {'f_mhz': f, 'paso_mhz': float(self.paso_mhz), 'paso': self._i_paso,
                'n_pasos': len(self._frecuencias), 'barrido': self._barrido, 'modo': 'barrido'}
        self._i_paso += 1
        return f, info

    def _generar_frecuencia(self):
        """Genera los 2K disparos de una frecuencia (ecos y ecos + ruido)."""
        f, info = self._siguiente_frecuencia()
        n, n_disp = self._n_vec, 2 * self._K
        a_ref = 10.0 ** (float(self.snr_db) / 20.0)
        t = (self._disparo + np.arange(n_disp)) * self._prt         # instante de cada disparo [s]
        pulsos = self._pulsos[np.arange(n_disp) % 2]                 # A, B, A, B, ...
        limpia = np.zeros((n_disp, n), np.complex64)
        for h, nivel_db, nombre in self.ecos(f):
            retardo = int(round(h / self._dh_km))                     # h' = c t / 2
            if retardo >= n:
                continue                                              # fuera de la ventana del PRT
            fd = {'TX': 0.0, '2F': 2.0, '2E': 2.0}.get(nombre, 1.0) * float(self.doppler_hz)
            fase = np.exp(1j * (self._rng.uniform(0, 2 * np.pi) + 2 * np.pi * fd * t))
            amp = a_ref * 10.0 ** (nivel_db / 20.0)
            cols = (retardo + np.arange(self._n_pulso)) % n
            limpia[:, cols] += (amp * fase[:, None] * pulsos).astype(np.complex64)
        ruido = self._rng.standard_normal((n_disp * n, 2)).astype(np.float32)
        ruido = (ruido[:, 0] + 1j * ruido[:, 1]) * np.float32(np.sqrt(0.5))
        self._limpia = limpia.ravel()
        self._rx = (self._limpia + ruido).astype(np.complex64)
        self._pos = 0
        self._disparo += n_disp
        return info

    def work(self, input_items, output_items):
        rx, limpia = output_items
        n = len(rx)
        hecho = 0
        while hecho < n:
            if self._pos >= len(self._rx):
                info = self._generar_frecuencia()
                valor = pmt.to_pmt(info)
                for puerto in (0, 1):
                    self.add_item_tag(puerto, self.nitems_written(puerto) + hecho, TAG_SONDEO, valor)
            k = min(n - hecho, len(self._rx) - self._pos)
            rx[hecho:hecho + k] = self._rx[self._pos:self._pos + k]
            limpia[hecho:hecho + k] = self._limpia[self._pos:self._pos + k]
            self._pos += k
            hecho += k
        return n
