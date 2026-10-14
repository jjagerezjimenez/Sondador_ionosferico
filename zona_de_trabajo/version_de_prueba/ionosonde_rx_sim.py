#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: Sondador ionosferico - Simulacion del receptor Golay/QCC
# Author: Juanjo
# Description: Simulacion del receptor del sondador ionosferico: Golay 16 bits + QCC + integracion coherente + ionograma. GUI en ionosonde_gui.py
# GNU Radio version: 3.10.12.0

from PyQt5 import Qt
from gnuradio import qtgui
from gnuradio import blocks
from gnuradio import filter
from gnuradio.filter import firdes
from gnuradio import gr
from gnuradio.fft import window
import sys
import signal
from PyQt5 import Qt
from argparse import ArgumentParser
from gnuradio.eng_arg import eng_float, intx
from gnuradio import eng_notation
import ionosonde_rx_sim_ionograma as ionograma  # embedded python block
import ionosonde_rx_sim_monitor_correl as monitor_correl  # embedded python block
import ionosonde_rx_sim_monitor_rx as monitor_rx  # embedded python block
import ionosonde_rx_sim_simulador as simulador  # embedded python block
import numpy as np
import threading


def snipfcn_interfaz_grafica(self):
    # Interfaz grafica: panel de control + pestanas de graficos.
    # El codigo esta en ionosonde_gui.py (misma carpeta que este .grc).
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import ionosonde_gui
    self._gui = ionosonde_gui.instalar(self)


def snippets_main_after_init(tb):
    snipfcn_interfaz_grafica(tb)

class ionosonde_rx_sim(gr.top_block, Qt.QWidget):

    def __init__(self):
        gr.top_block.__init__(self, "Sondador ionosferico - Simulacion del receptor Golay/QCC", catch_exceptions=True)
        Qt.QWidget.__init__(self)
        self.setWindowTitle("Sondador ionosferico - Simulacion del receptor Golay/QCC")
        qtgui.util.check_set_qss()
        try:
            self.setWindowIcon(Qt.QIcon.fromTheme('gnuradio-grc'))
        except BaseException as exc:
            print(f"Qt GUI: Could not set Icon: {str(exc)}", file=sys.stderr)
        self.top_scroll_layout = Qt.QVBoxLayout()
        self.setLayout(self.top_scroll_layout)
        self.top_scroll = Qt.QScrollArea()
        self.top_scroll.setFrameStyle(Qt.QFrame.NoFrame)
        self.top_scroll_layout.addWidget(self.top_scroll)
        self.top_scroll.setWidgetResizable(True)
        self.top_widget = Qt.QWidget()
        self.top_scroll.setWidget(self.top_widget)
        self.top_layout = Qt.QVBoxLayout(self.top_widget)
        self.top_grid_layout = Qt.QGridLayout()
        self.top_layout.addLayout(self.top_grid_layout)

        self.settings = Qt.QSettings("gnuradio/flowgraphs", "ionosonde_rx_sim")

        try:
            geometry = self.settings.value("geometry")
            if geometry:
                self.restoreGeometry(geometry)
        except BaseException as exc:
            print(f"Qt GUI: Could not restore geometry: {str(exc)}", file=sys.stderr)
        self.flowgraph_started = threading.Event()

        ##################################################
        # Variables
        ##################################################
        self.swap_ab = swap_ab = 0
        self.spb = spb = 6
        self.samp_rate = samp_rate = 200000
        self.prf = prf = 60
        self.n_chips = n_chips = 16
        self.code_B = code_B = [1, 1, 1, -1, 1, 1, -1, 1, -1, -1, -1, 1, 1, 1, -1, 1]
        self.code_A = code_A = [1, 1, 1, -1, 1, 1, -1, 1, 1, 1, 1, -1, -1, -1, 1, -1]
        self.c_luz = c_luz = 299792458.0
        self.zmq_addr = zmq_addr = 'tcp://127.0.0.1:5555'
        self.velocidad = velocidad = 10
        self.taps_B = taps_B = np.repeat(code_A if swap_ab else code_B, spb)[::-1].astype(complex).tolist()
        self.taps_A = taps_A = np.repeat(code_B if swap_ab else code_A, spb)[::-1].astype(complex).tolist()
        self.snr_db = snr_db = -15.0
        self.segundo_salto = segundo_salto = 1
        self.pulso_directo = pulso_directo = 0
        self.paso_mhz = paso_mhz = 0.1
        self.n_vec = n_vec = int(samp_rate // prf)
        self.n_h = n_h = 3200
        self.modo_barrido = modo_barrido = 1
        self.integracion = integracion = 0
        self.hmF2 = hmF2 = 300.0
        self.h_off = h_off = n_chips * spb - 1
        self.foF2 = foF2 = 8.0
        self.foE = foE = 3.0
        self.f_ini = f_ini = 1.0
        self.f_fin = f_fin = 20.0
        self.f_fija = f_fija = 5.0
        self.doppler_hz = doppler_hz = 0.2
        self.dh_km = dh_km = c_luz / (2.0 * samp_rate) / 1000.0
        self.K = K = 25

        ##################################################
        # Blocks
        ##################################################

        self.ventana_alturas = blocks.keep_m_in_n(gr.sizeof_gr_complex, n_h, n_vec, h_off)
        self.tipo_integracion = blocks.selector(gr.sizeof_float*n_h,integracion,0)
        self.tipo_integracion.set_enabled(True)
        self.suma_qcc = blocks.add_vcc(1)
        self.simulador = simulador.blk(samp_rate=samp_rate, n_vec=n_vec, spb=spb, K=K, code_A=code_A, code_B=code_B, foE=foE, foF2=foF2, hmF2=hmF2, snr_db=snr_db, doppler_hz=doppler_hz, modo_barrido=modo_barrido, f_fija=f_fija, f_ini=f_ini, f_fin=f_fin, paso_mhz=paso_mhz, segundo_salto=segundo_salto, pulso_directo=pulso_directo)
        self.selector_golay = blocks.deinterleave(gr.sizeof_gr_complex*1, n_vec)
        self.promedio = blocks.multiply_const_vff([1.0 / K] * n_h)
        self.perfil_i = blocks.stream_to_vector(gr.sizeof_float*1, n_h)
        self.perfil_c = blocks.stream_to_vector(gr.sizeof_gr_complex*1, n_h)
        self.monitor_rx = monitor_rx.blk(n_vec=n_vec, cada_n_parejas=6)
        self.monitor_correl = monitor_correl.blk(n_vec=n_vec, cada_n_parejas=6)
        self.ionograma = ionograma.blk(n_h=n_h, dh_km=dh_km, f_min=f_ini, f_max=f_fin, df_grilla=0.05, umbral_db=14.0, h_min_km=80.0)
        self.integrador_incoherente = blocks.integrate_ff(K, n_h)
        self.integrador_coherente = blocks.integrate_cc(K, n_h)
        self.envolvente_i = blocks.complex_to_mag(1)
        self.envolvente_c = blocks.complex_to_mag(n_h)
        self.correlador_B = filter.fir_filter_ccc(1, taps_B)
        self.correlador_B.declare_sample_delay(0)
        self.correlador_A = filter.fir_filter_ccc(1, taps_A)
        self.correlador_A.declare_sample_delay(0)
        self.blocks_throttle = blocks.throttle( gr.sizeof_gr_complex*1, (samp_rate * velocidad), True, 0 if "auto" == "auto" else max( int(float(0.1) * (samp_rate * velocidad)) if "auto" == "time" else int(0.1), 1) )


        ##################################################
        # Connections
        ##################################################
        self.connect((self.blocks_throttle, 0), (self.selector_golay, 0))
        self.connect((self.correlador_A, 0), (self.monitor_correl, 0))
        self.connect((self.correlador_A, 0), (self.suma_qcc, 0))
        self.connect((self.correlador_B, 0), (self.monitor_correl, 1))
        self.connect((self.correlador_B, 0), (self.suma_qcc, 1))
        self.connect((self.envolvente_c, 0), (self.tipo_integracion, 0))
        self.connect((self.envolvente_i, 0), (self.perfil_i, 0))
        self.connect((self.integrador_coherente, 0), (self.envolvente_c, 0))
        self.connect((self.integrador_incoherente, 0), (self.tipo_integracion, 1))
        self.connect((self.perfil_c, 0), (self.integrador_coherente, 0))
        self.connect((self.perfil_i, 0), (self.integrador_incoherente, 0))
        self.connect((self.promedio, 0), (self.ionograma, 0))
        self.connect((self.selector_golay, 0), (self.correlador_A, 0))
        self.connect((self.selector_golay, 1), (self.correlador_B, 0))
        self.connect((self.simulador, 0), (self.blocks_throttle, 0))
        self.connect((self.simulador, 1), (self.monitor_rx, 1))
        self.connect((self.simulador, 0), (self.monitor_rx, 0))
        self.connect((self.suma_qcc, 0), (self.monitor_correl, 2))
        self.connect((self.suma_qcc, 0), (self.ventana_alturas, 0))
        self.connect((self.tipo_integracion, 0), (self.promedio, 0))
        self.connect((self.ventana_alturas, 0), (self.envolvente_i, 0))
        self.connect((self.ventana_alturas, 0), (self.perfil_c, 0))


    def closeEvent(self, event):
        self.settings = Qt.QSettings("gnuradio/flowgraphs", "ionosonde_rx_sim")
        self.settings.setValue("geometry", self.saveGeometry())
        self.stop()
        self.wait()

        event.accept()

    def get_swap_ab(self):
        return self.swap_ab

    def set_swap_ab(self, swap_ab):
        self.swap_ab = swap_ab
        self.set_taps_A(np.repeat(self.code_B if self.swap_ab else self.code_A, self.spb)[::-1].astype(complex).tolist())
        self.set_taps_B(np.repeat(self.code_A if self.swap_ab else self.code_B, self.spb)[::-1].astype(complex).tolist())

    def get_spb(self):
        return self.spb

    def set_spb(self, spb):
        self.spb = spb
        self.set_h_off(self.n_chips * self.spb - 1)
        self.set_taps_A(np.repeat(self.code_B if self.swap_ab else self.code_A, self.spb)[::-1].astype(complex).tolist())
        self.set_taps_B(np.repeat(self.code_A if self.swap_ab else self.code_B, self.spb)[::-1].astype(complex).tolist())

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.set_dh_km(self.c_luz / (2.0 * self.samp_rate) / 1000.0)
        self.set_n_vec(int(self.samp_rate // self.prf))
        self.blocks_throttle.set_sample_rate((self.samp_rate * self.velocidad))

    def get_prf(self):
        return self.prf

    def set_prf(self, prf):
        self.prf = prf
        self.set_n_vec(int(self.samp_rate // self.prf))

    def get_n_chips(self):
        return self.n_chips

    def set_n_chips(self, n_chips):
        self.n_chips = n_chips
        self.set_h_off(self.n_chips * self.spb - 1)

    def get_code_B(self):
        return self.code_B

    def set_code_B(self, code_B):
        self.code_B = code_B
        self.set_taps_A(np.repeat(self.code_B if self.swap_ab else self.code_A, self.spb)[::-1].astype(complex).tolist())
        self.set_taps_B(np.repeat(self.code_A if self.swap_ab else self.code_B, self.spb)[::-1].astype(complex).tolist())

    def get_code_A(self):
        return self.code_A

    def set_code_A(self, code_A):
        self.code_A = code_A
        self.set_taps_A(np.repeat(self.code_B if self.swap_ab else self.code_A, self.spb)[::-1].astype(complex).tolist())
        self.set_taps_B(np.repeat(self.code_A if self.swap_ab else self.code_B, self.spb)[::-1].astype(complex).tolist())

    def get_c_luz(self):
        return self.c_luz

    def set_c_luz(self, c_luz):
        self.c_luz = c_luz
        self.set_dh_km(self.c_luz / (2.0 * self.samp_rate) / 1000.0)

    def get_zmq_addr(self):
        return self.zmq_addr

    def set_zmq_addr(self, zmq_addr):
        self.zmq_addr = zmq_addr

    def get_velocidad(self):
        return self.velocidad

    def set_velocidad(self, velocidad):
        self.velocidad = velocidad
        self.blocks_throttle.set_sample_rate((self.samp_rate * self.velocidad))

    def get_taps_B(self):
        return self.taps_B

    def set_taps_B(self, taps_B):
        self.taps_B = taps_B
        self.correlador_B.set_taps(self.taps_B)

    def get_taps_A(self):
        return self.taps_A

    def set_taps_A(self, taps_A):
        self.taps_A = taps_A
        self.correlador_A.set_taps(self.taps_A)

    def get_snr_db(self):
        return self.snr_db

    def set_snr_db(self, snr_db):
        self.snr_db = snr_db
        self.simulador.snr_db = self.snr_db

    def get_segundo_salto(self):
        return self.segundo_salto

    def set_segundo_salto(self, segundo_salto):
        self.segundo_salto = segundo_salto
        self.simulador.segundo_salto = self.segundo_salto

    def get_pulso_directo(self):
        return self.pulso_directo

    def set_pulso_directo(self, pulso_directo):
        self.pulso_directo = pulso_directo
        self.simulador.pulso_directo = self.pulso_directo

    def get_paso_mhz(self):
        return self.paso_mhz

    def set_paso_mhz(self, paso_mhz):
        self.paso_mhz = paso_mhz
        self.simulador.paso_mhz = self.paso_mhz

    def get_n_vec(self):
        return self.n_vec

    def set_n_vec(self, n_vec):
        self.n_vec = n_vec
        self.ventana_alturas.set_n(self.n_vec)

    def get_n_h(self):
        return self.n_h

    def set_n_h(self, n_h):
        self.n_h = n_h
        self.ionograma.n_h = self.n_h
        self.promedio.set_k([1.0 / self.K] * self.n_h)
        self.ventana_alturas.set_m(self.n_h)

    def get_modo_barrido(self):
        return self.modo_barrido

    def set_modo_barrido(self, modo_barrido):
        self.modo_barrido = modo_barrido
        self.simulador.modo_barrido = self.modo_barrido

    def get_integracion(self):
        return self.integracion

    def set_integracion(self, integracion):
        self.integracion = integracion
        self.tipo_integracion.set_input_index(self.integracion)

    def get_hmF2(self):
        return self.hmF2

    def set_hmF2(self, hmF2):
        self.hmF2 = hmF2
        self.simulador.hmF2 = self.hmF2

    def get_h_off(self):
        return self.h_off

    def set_h_off(self, h_off):
        self.h_off = h_off
        self.ventana_alturas.set_offset(self.h_off)

    def get_foF2(self):
        return self.foF2

    def set_foF2(self, foF2):
        self.foF2 = foF2
        self.simulador.foF2 = self.foF2

    def get_foE(self):
        return self.foE

    def set_foE(self, foE):
        self.foE = foE
        self.simulador.foE = self.foE

    def get_f_ini(self):
        return self.f_ini

    def set_f_ini(self, f_ini):
        self.f_ini = f_ini
        self.ionograma.f_min = self.f_ini
        self.simulador.f_ini = self.f_ini

    def get_f_fin(self):
        return self.f_fin

    def set_f_fin(self, f_fin):
        self.f_fin = f_fin
        self.ionograma.f_max = self.f_fin
        self.simulador.f_fin = self.f_fin

    def get_f_fija(self):
        return self.f_fija

    def set_f_fija(self, f_fija):
        self.f_fija = f_fija
        self.simulador.f_fija = self.f_fija

    def get_doppler_hz(self):
        return self.doppler_hz

    def set_doppler_hz(self, doppler_hz):
        self.doppler_hz = doppler_hz
        self.simulador.doppler_hz = self.doppler_hz

    def get_dh_km(self):
        return self.dh_km

    def set_dh_km(self, dh_km):
        self.dh_km = dh_km
        self.ionograma.dh_km = self.dh_km

    def get_K(self):
        return self.K

    def set_K(self, K):
        self.K = K
        self.promedio.set_k([1.0 / self.K] * self.n_h)




def main(top_block_cls=ionosonde_rx_sim, options=None):

    qapp = Qt.QApplication(sys.argv)

    tb = top_block_cls()
    snippets_main_after_init(tb)
    tb.start()
    tb.flowgraph_started.set()

    tb.show()

    def sig_handler(sig=None, frame=None):
        tb.stop()
        tb.wait()

        Qt.QApplication.quit()

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    timer = Qt.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)

    qapp.exec_()

if __name__ == '__main__':
    main()
