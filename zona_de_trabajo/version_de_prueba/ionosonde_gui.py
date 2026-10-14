"""
ionosonde_gui.py
================
Interfaz gráfica del sondador ionosférico. La usan los dos flowgraphs:

  * ionosonde_rx_sim.grc : simulación (bloque 'simulador' en lugar de la Red Pitaya).
  * ionosonde_rx.grc     : receptor real (Red Pitaya + 'programador_barrido').

GNU Radio Companion procesa la señal (fuente -> correladores Golay A/B ->
suma QCC -> integración -> armado del ionograma) y este módulo arma la ventana:

  * Panel izquierdo: todos los controles, agrupados y con una explicación.
  * Pestañas con los gráficos:
        1 · Señal recibida        (lo que entra al receptor)
        2 · Compresión Golay      (correladores A, B y la suma A + B)
        3 · Perfil de altura      (después de integrar K parejas)
        4 · Ionograma h'(f)       (barrido de 1 a 20 MHz)
        5 · Ayuda y parámetros
  * Botones para guardar capturas en PNG (carpeta ./capturas, Ctrl+S).

Lo invoca el bloque "Python Snippet" del .grc (sección main_after_init):

    import ionosonde_gui
    self._gui = ionosonde_gui.instalar(self)

No reemplaza ningún bloque de GNU Radio: solo lee los bloques de monitoreo
(monitor_rx, monitor_correl, ionograma y, si existe, simulador) y llama a los
set_<variable>() que genera GRC. Solo muestra los controles cuyas variables
existen en el flowgraph.
"""
import datetime
import os
import sys
import time
import traceback

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

try:
    from scipy.signal import welch
except ImportError:  # pragma: no cover
    welch = None

pg.setConfigOptions(antialias=True, background='w', foreground='k', imageAxisOrder='row-major')

C_LUZ = 299792458.0
AZUL, NARANJA, VERDE = '#1f5fa8', '#e07b10', '#2a9d4b'
ROJO, GRIS, NEGRO = '#d62728', '#9a9a9a', '#222222'
FONDO_ZONA_CIEGA = (120, 120, 120, 45)
PERIODO_MS = 150                     # refresco de los gráficos


# ----------------------------------------------------------------------------
# Utilidades de estilo
# ----------------------------------------------------------------------------
def _grafico(titulo, eje_x, eje_y):
    """PlotWidget con el estilo común: fondo blanco, grilla, sin botones extra."""
    p = pg.PlotWidget()
    p.setTitle(titulo, size='11pt', bold=True)
    p.setLabel('bottom', eje_x, **{'font-size': '10pt'})
    p.setLabel('left', eje_y, **{'font-size': '10pt'})
    p.showGrid(x=True, y=True, alpha=0.25)
    p.hideButtons()                      # oculta el botón "A" de autoescala
    p.setMouseEnabled(x=False, y=False)  # evita mover el gráfico por accidente
    fuente = QtGui.QFont()
    fuente.setPointSize(9)
    for eje in ('bottom', 'left'):
        p.getAxis(eje).setStyle(tickFont=fuente)
    return p


def _lapiz(color, ancho=1.5, estilo=QtCore.Qt.SolidLine):
    return pg.mkPen(color=color, width=ancho, style=estilo)


def _texto_ayuda(texto):
    lbl = QtWidgets.QLabel(texto)
    lbl.setWordWrap(True)
    lbl.setStyleSheet('color: #5a5a5a; font-size: 8pt;')
    return lbl


def _leyenda(plot):
    ley = plot.addLegend(offset=(-10, 10), labelTextSize='9pt')
    ley.setBrush(pg.mkBrush(255, 255, 255, 220))
    ley.setPen(pg.mkPen('#bbbbbb'))
    return ley


def _db(x):
    return 20.0 * np.log10(np.maximum(np.abs(x), 1e-12))


# ----------------------------------------------------------------------------
# Controles con explicación
# ----------------------------------------------------------------------------
class Deslizador(QtWidgets.QWidget):
    """Título + valor (editable) + barra deslizante + texto de ayuda."""

    def __init__(self, titulo, ayuda, minimo, maximo, paso, valor, unidad, al_cambiar, decimales=1):
        super().__init__()
        self._min, self._paso, self._al_cambiar = minimo, paso, al_cambiar
        caja = QtWidgets.QVBoxLayout(self)
        caja.setContentsMargins(0, 4, 0, 4)
        caja.setSpacing(2)
        fila = QtWidgets.QHBoxLayout()
        etiqueta = QtWidgets.QLabel(titulo)
        etiqueta.setStyleSheet('font-weight: bold;')
        self.numero = QtWidgets.QDoubleSpinBox()
        self.numero.setRange(minimo, maximo)
        self.numero.setSingleStep(paso)
        self.numero.setDecimals(decimales)
        self.numero.setSuffix(' ' + unidad if unidad else '')
        self.numero.setKeyboardTracking(False)
        self.numero.setMinimumWidth(92)
        fila.addWidget(etiqueta)
        fila.addStretch(1)
        fila.addWidget(self.numero)
        caja.addLayout(fila)
        self.barra = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.barra.setRange(0, int(round((maximo - minimo) / paso)))
        caja.addWidget(self.barra)
        if ayuda:
            caja.addWidget(_texto_ayuda(ayuda))
        self.barra.valueChanged.connect(lambda v: self.numero.setValue(self._min + v * self._paso))
        self.numero.valueChanged.connect(self._numero_cambio)
        self.setValue(valor)

    def _numero_cambio(self, v):
        self.barra.blockSignals(True)
        self.barra.setValue(int(round((v - self._min) / self._paso)))
        self.barra.blockSignals(False)
        self._al_cambiar(v)

    def setValue(self, v):
        self.numero.setValue(v)
        self._numero_cambio(self.numero.value())

    def value(self):
        return self.numero.value()


class Casilla(QtWidgets.QWidget):
    """Casilla de verificación con texto de ayuda debajo."""

    def __init__(self, titulo, ayuda, valor, al_cambiar):
        super().__init__()
        caja = QtWidgets.QVBoxLayout(self)
        caja.setContentsMargins(0, 4, 0, 4)
        caja.setSpacing(1)
        self.casilla = QtWidgets.QCheckBox(titulo)
        self.casilla.setStyleSheet('font-weight: bold;')
        self.casilla.setChecked(bool(valor))
        self.casilla.toggled.connect(lambda c: al_cambiar(int(c)))
        caja.addWidget(self.casilla)
        if ayuda:
            ayuda_lbl = _texto_ayuda(ayuda)
            ayuda_lbl.setContentsMargins(20, 0, 0, 0)
            caja.addWidget(ayuda_lbl)


class Opciones(QtWidgets.QWidget):
    """Grupo de botones de opción excluyentes, cada uno con su ayuda."""

    def __init__(self, titulo, opciones, valor, al_cambiar):
        super().__init__()
        caja = QtWidgets.QVBoxLayout(self)
        caja.setContentsMargins(0, 4, 0, 4)
        caja.setSpacing(1)
        if titulo:
            etiqueta = QtWidgets.QLabel(titulo)
            etiqueta.setStyleSheet('font-weight: bold;')
            caja.addWidget(etiqueta)
        self.grupo = QtWidgets.QButtonGroup(self)
        for valor_op, texto, ayuda in opciones:
            boton = QtWidgets.QRadioButton(texto)
            self.grupo.addButton(boton, valor_op)
            caja.addWidget(boton)
            if ayuda:
                lbl = _texto_ayuda(ayuda)
                lbl.setContentsMargins(20, 0, 0, 2)
                caja.addWidget(lbl)
            if valor_op == valor:
                boton.setChecked(True)
        self.grupo.idClicked.connect(al_cambiar)


class Desplegable(QtWidgets.QWidget):
    """Lista desplegable con título y ayuda."""

    def __init__(self, titulo, ayuda, opciones, valor, al_cambiar):
        super().__init__()
        caja = QtWidgets.QVBoxLayout(self)
        caja.setContentsMargins(0, 4, 0, 4)
        caja.setSpacing(2)
        fila = QtWidgets.QHBoxLayout()
        etiqueta = QtWidgets.QLabel(titulo)
        etiqueta.setStyleSheet('font-weight: bold;')
        self.lista = QtWidgets.QComboBox()
        for texto, dato in opciones:
            self.lista.addItem(texto, dato)
        idx = max(0, [d for _, d in opciones].index(valor) if valor in [d for _, d in opciones] else 0)
        self.lista.setCurrentIndex(idx)
        fila.addWidget(etiqueta)
        fila.addStretch(1)
        fila.addWidget(self.lista)
        caja.addLayout(fila)
        self.ayuda = _texto_ayuda(ayuda) if ayuda else None
        if self.ayuda:
            caja.addWidget(self.ayuda)
        self.lista.currentIndexChanged.connect(lambda i: al_cambiar(self.lista.itemData(i)))

    def value(self):
        return self.lista.currentData()


# ----------------------------------------------------------------------------
# Ventana principal
# ----------------------------------------------------------------------------
class InterfazSondador(QtCore.QObject):

    def __init__(self, tb):
        super().__init__(tb)
        self.tb = tb
        self.sim = getattr(tb, 'simulador', None)
        self.mon_rx = getattr(tb, 'monitor_rx', None)
        self.mon_c = getattr(tb, 'monitor_correl', None)
        self.iono = getattr(tb, 'ionograma', None)
        self.es_sim = self.sim is not None

        # Parámetros del sistema (variables del .grc)
        self.fs = float(self._var('samp_rate', 200000))
        self.n_vec = int(self._var('n_vec', 3333))
        self.spb = int(self._var('spb', 6))
        self.K = int(self._var('K', 25))
        self.dh = float(self._var('dh_km', C_LUZ / (2 * self.fs) / 1000))
        self.h_off = int(self._var('h_off', 16 * self.spb - 1))
        self.n_h = int(self._var('n_h', 3200))
        self.code_A = np.asarray(self._var('code_A', []), float)
        self.code_B = np.asarray(self._var('code_B', []), float)
        self.n_chips = len(self.code_A)
        self.tau_us = self.spb / self.fs * 1e6
        self.h_ciega = C_LUZ * self.n_chips * self.spb / self.fs / 2 / 1000
        self.i_80km = int(np.ceil(80.0 / self.dh))
        self.umbral_db = float(getattr(self.iono, 'umbral_db', 14.0)) if self.iono else 14.0

        self.h_max = 800.0
        self.escala_db = [6.0, 25.0]          # escala de color de los mapas [dB sobre el ruido]
        self.congelado = False
        self._psd = None
        self._ultima = {}
        self._snr = {}
        self._tasa = (time.time(), 0, 0.0)
        self._clave_teoria = None
        self._avisos_barrido = []
        self._carpeta_capturas = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'capturas')

        self._construir_ventana()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._actualizar)
        self.timer.start(PERIODO_MS)

    # ---------------------------------------------------------------- helpers
    def _var(self, nombre, defecto):
        return getattr(self.tb, nombre, defecto)

    def _setter(self, nombre):
        """Devuelve una función que cambia la variable 'nombre' del flowgraph."""
        metodo = getattr(self.tb, 'set_' + nombre, None)

        def aplicar(valor):
            if metodo is not None:
                metodo(valor)
            self._actualizar_info_barrido()
        return aplicar

    # ------------------------------------------------------------ construcción
    def _construir_ventana(self):
        tb = self.tb
        tb.setWindowTitle('Sondador ionosférico — ' + (
            'Simulación del receptor Golay / QCC' if self.es_sim else
            'Receptor Golay / QCC (Red Pitaya)'))
        principal = QtWidgets.QWidget()
        caja = QtWidgets.QVBoxLayout(principal)
        caja.setContentsMargins(0, 0, 0, 0)
        divisor = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        divisor.addWidget(self._panel_controles())
        self.pestanas = QtWidgets.QTabWidget()
        fuente = QtGui.QFont()
        fuente.setPointSize(10)
        self.pestanas.tabBar().setFont(fuente)
        self.pestanas.tabBar().setElideMode(QtCore.Qt.ElideNone)
        self.pestanas.setCornerWidget(self._botones_captura(), QtCore.Qt.TopRightCorner)
        self.pestanas.addTab(self._pestana_senal(), '1 · Señal recibida')
        self.pestanas.addTab(self._pestana_compresion(), '2 · Compresión Golay (QCC)')
        self.pestanas.addTab(self._pestana_perfil(), '3 · Perfil de altura')
        self.pestanas.addTab(self._pestana_ionograma(), "4 · Ionograma h'(f)")
        self.pestanas.addTab(self._pestana_ayuda(), '5 · Ayuda y parámetros')
        self.pestanas.setCurrentIndex(3)
        self.pestanas.currentChanged.connect(lambda _: self._actualizar(forzar=True))
        divisor.addWidget(self.pestanas)
        divisor.setStretchFactor(0, 0)
        divisor.setStretchFactor(1, 1)
        divisor.setSizes([330, 1170])
        caja.addWidget(divisor, 1)
        self.estado = QtWidgets.QLabel()
        self.estado.setStyleSheet('background: #eef2f7; border-top: 1px solid #c8d0da; '
                                  'padding: 4px 8px; font-size: 9pt;')
        caja.addWidget(self.estado)
        tb.top_layout.addWidget(principal)
        QtWidgets.QShortcut(QtGui.QKeySequence('Ctrl+S'), tb, activated=self._capturar_pestana)
        if tb.width() < 1400 or tb.height() < 850:
            pantalla = QtWidgets.QApplication.primaryScreen().availableGeometry()
            tb.resize(min(1500, pantalla.width() - 40), min(920, pantalla.height() - 60))

    def _grupo(self, titulo, widgets):
        g = QtWidgets.QGroupBox(titulo)
        g.setStyleSheet('QGroupBox { font-weight: bold; font-size: 10pt; margin-top: 10px; } '
                        'QGroupBox::title { subcontrol-origin: margin; left: 6px; padding: 0 3px; }')
        caja = QtWidgets.QVBoxLayout(g)
        caja.setSpacing(2)
        for w in widgets:
            caja.addWidget(w)
        return g

    def _tiene(self, nombre):
        return hasattr(self.tb, 'set_' + nombre)

    def _panel_controles(self):
        v = self._var
        contenido = QtWidgets.QWidget()
        caja = QtWidgets.QVBoxLayout(contenido)
        titulo = QtWidgets.QLabel('Panel de control')
        titulo.setStyleSheet('font-size: 13pt; font-weight: bold;')
        caja.addWidget(titulo)
        if not self.es_sim:
            caja.addWidget(_texto_ayuda(
                f'<b>Receptor real</b>: Red Pitaya en {v("rp_uri", "?")}.'))
        caja.addWidget(_texto_ayuda(
            'Los cambios se aplican al empezar la próxima frecuencia de sondeo '
            f'(cada {2 * self.K} disparos = {2 * self.K * self.n_vec / self.fs:.2f} s en tiempo real).'))
        grupos = []

        if self._tiene('foE'):
            grupos.append(('Ionosfera simulada', [
                Deslizador('foE: crítica de la capa E', 'Frecuencia máxima que refleja la capa E '
                           '(~110 km). Por debajo de foE el eco viene de E; por encima, la atraviesa.',
                           1.5, 4.5, 0.1, v('foE', 3.0), 'MHz', self._setter('foE')),
                Deslizador('foF2: crítica de la capa F2', 'Frecuencia máxima que refleja la '
                           'ionosfera. Por encima de foF2 la onda escapa y no hay eco.',
                           3.0, 15.0, 0.1, v('foF2', 8.0), 'MHz', self._setter('foF2')),
                Deslizador('hmF2: altura del máximo de F2', 'Sube o baja toda la traza F2 del '
                           'ionograma.', 220, 450, 5, v('hmF2', 300.0), 'km', self._setter('hmF2'),
                           decimales=0),
                Casilla('Eco de segundo salto (2F)', 'Rebote ionosfera → suelo → ionosfera: '
                        'aparece al doble de altura y más débil.', v('segundo_salto', 1),
                        self._setter('segundo_salto')),
                Casilla('Acople directo TX → RX', 'Pulso fuerte en h ≈ 0 km. Muestra la zona ciega '
                        f'(h < {self.h_ciega:.0f} km) donde el receptor no puede ver ecos.',
                        v('pulso_directo', 0), self._setter('pulso_directo')),
            ]))

        modo = int(v('modo_barrido', 1))
        if self._tiene('modo_barrido'):
            self.ctrl_f_fija = Deslizador('Frecuencia fija', 'Solo se usa en modo "Frecuencia fija".',
                                          1.0, 20.0, 0.1, v('f_fija', 5.0), 'MHz',
                                          self._setter('f_fija'))
            self.ctrl_paso = Desplegable('Paso del barrido', None,
                                         [('0.05 MHz', 0.05), ('0.1 MHz', 0.1), ('0.2 MHz', 0.2),
                                          ('0.5 MHz', 0.5)], v('paso_mhz', 0.1),
                                         self._setter('paso_mhz'))
            self.lbl_info_barrido = _texto_ayuda('')
            self.ctrl_modo = Opciones(None, [
                (1, 'Barrido 1 → 20 MHz', 'Genera el ionograma (pestaña 4).'),
                (0, 'Frecuencia fija', 'Sondeo continuo en una frecuencia: ideal para las '
                 'pestañas 1, 2 y 3.')], modo, self._cambiar_modo)
            controles = [self.ctrl_modo, self.ctrl_f_fija, self.ctrl_paso, self.lbl_info_barrido]
            if not self.es_sim:
                controles.append(_texto_ayuda(
                    '<b>Ojo:</b> el receptor supone que el transmisor sigue este mismo plan. '
                    'El bloque "Programador del barrido" publica la frecuencia por su puerto '
                    '"freq" para sintonizar el hardware.'))
            grupos.append(('Barrido de frecuencia', controles))

        if self._tiene('snr_db'):
            grupos.append(('Canal: ruido y movimiento', [
                Deslizador('SNR de entrada', 'Potencia del eco / potencia del ruido, por muestra, '
                           'ANTES de procesar. Con valores negativos el eco queda oculto en el ruido: '
                           'la compresión (+22.8 dB) y la integración (+14 dB) lo recuperan.',
                           -40, 10, 1, v('snr_db', -15.0), 'dB', self._setter('snr_db'),
                           decimales=0),
                Deslizador('Doppler del eco', 'Velocidad de la capa: la fase del eco gira de un '
                           'disparo al siguiente. Con Doppler alto la integración coherente pierde '
                           'ganancia.', 0.0, 5.0, 0.05, v('doppler_hz', 0.2), 'Hz',
                           self._setter('doppler_hz'), decimales=2),
            ]))

        receptor = []
        if self._tiene('integracion'):
            receptor.append(Opciones(f'Integración de K = {self.K} parejas', [
                (0, 'Coherente (promedia I/Q y luego |·|)',
                 f'Baja el piso de ruido: ganancia 10·log10(K) = {10 * np.log10(self.K):.2f} dB. '
                 'Es la que usan AIS-INGV y Cabrera et al.'),
                (1, 'Incoherente (|·| y luego promedia)',
                 'Solo suaviza el ruido; no baja el piso. Útil si la fase del eco cambia mucho.')],
                int(v('integracion', 0)), self._setter('integracion')))
        if self._tiene('swap_ab'):
            receptor.append(Casilla(
                'Intercambiar correladores A ↔ B',
                'Simula el selector A/B desfasado (la rama A recibe los disparos B): los lóbulos '
                'laterales ya no se cancelan.' if self.es_sim else
                'Usar si la rama A recibe los disparos B (el selector arrancó en la fase '
                'equivocada): sin esto no hay pico limpio ni cancelación de lóbulos.',
                v('swap_ab', 0), self._setter('swap_ab')))
        if self._tiene('alineacion'):
            receptor.append(Deslizador(
                'Alineación del disparo', 'Retrasa la señal N muestras para que cada vector '
                f'empiece con el disparo (1 muestra = {self.dh:.2f} km: los ecos suben N × '
                f'{self.dh:.2f} km). Ajustar hasta que el pulso directo del TX quede en '
                "h' ≈ 0 km.", 0, self.n_vec - 1, 1, v('alineacion', 0), 'muestras',
                self._setter('alineacion'), decimales=0))
        if receptor:
            grupos.append(('Receptor', receptor))

        vista = []
        if self._tiene('velocidad'):
            vista.append(Desplegable('Velocidad', 'Acelera la simulación (el procesamiento es '
                                     'idéntico).', [('×1 (tiempo real)', 1), ('×5', 5),
                                                    ('×10', 10), ('×20', 20)],
                                     v('velocidad', 10), self._setter('velocidad')))
        vista.append(Desplegable('Altura máxima en gráficos', None,
                                 [('400 km', 400.0), ('600 km', 600.0), ('800 km', 800.0),
                                  ('1200 km', 1200.0), ('2400 km', 2400.0)], self.h_max,
                                 self._cambiar_h_max))
        grupos.append(('Simulación y vista' if self.es_sim else 'Vista', vista))

        for i, (nombre, widgets) in enumerate(grupos, 1):
            caja.addWidget(self._grupo(f'{i} · {nombre}', widgets))
        caja.addWidget(_texto_ayuda('Capturas: botones arriba a la derecha de las pestañas, '
                                    'o Ctrl+S para guardar la pestaña visible.'))
        caja.addStretch(1)
        if self._tiene('modo_barrido'):
            self._cambiar_modo(modo)

        desplazable = QtWidgets.QScrollArea()
        desplazable.setWidget(contenido)
        desplazable.setWidgetResizable(True)
        desplazable.setMinimumWidth(310)
        desplazable.setFrameShape(QtWidgets.QFrame.NoFrame)
        return desplazable

    def _botones_captura(self):
        """Botones a la derecha de las pestañas: congelar y guardar capturas."""
        w = QtWidgets.QWidget()
        fila = QtWidgets.QHBoxLayout(w)
        fila.setContentsMargins(0, 0, 4, 2)
        self.btn_congelar = QtWidgets.QPushButton('Congelar gráficos')
        self.btn_congelar.setCheckable(True)
        self.btn_congelar.setToolTip('Detiene el refresco de los gráficos (la simulación sigue) '
                                     'para elegir el instante de la captura.')
        self.btn_congelar.toggled.connect(self._congelar)
        btn_pestana = QtWidgets.QPushButton('Guardar captura (Ctrl+S)')
        btn_pestana.setToolTip('Guarda la pestaña visible en PNG, al doble de resolución, '
                               'en la carpeta "capturas".')
        btn_pestana.clicked.connect(self._capturar_pestana)
        btn_ventana = QtWidgets.QPushButton('Captura de toda la ventana')
        btn_ventana.setToolTip('Incluye el panel de control.')
        btn_ventana.clicked.connect(lambda: self._guardar_png(self.tb, 'ventana'))
        for b in (self.btn_congelar, btn_pestana, btn_ventana):
            fila.addWidget(b)
        return w

    def _cambiar_modo(self, m):
        """Cambia entre barrido (1) y frecuencia fija (0), manteniendo el panel sincronizado."""
        self.ctrl_modo.grupo.button(m).setChecked(True)
        self._setter('modo_barrido')(m)
        self.ctrl_f_fija.setEnabled(m == 0)
        self.ctrl_paso.setEnabled(m == 1)
        for aviso in self._avisos_barrido:
            aviso.setVisible(m == 1)

    def _aviso_barrido(self, texto):
        """Franja visible solo en modo barrido, con un botón para pasar a frecuencia fija."""
        marco = QtWidgets.QFrame()
        marco.setStyleSheet('QFrame { background: #fff6e0; border: 1px solid #f0c36d; '
                            'border-radius: 4px; }')
        fila = QtWidgets.QHBoxLayout(marco)
        fila.setContentsMargins(8, 3, 8, 3)
        lbl = QtWidgets.QLabel(texto)
        lbl.setWordWrap(True)
        lbl.setStyleSheet('border: none; font-size: 9pt;')
        boton = QtWidgets.QPushButton('Pasar a frecuencia fija')
        boton.clicked.connect(lambda: self._cambiar_modo(0))
        fila.addWidget(lbl, 1)
        fila.addWidget(boton)
        marco.setVisible(int(self._var('modo_barrido', 1)) == 1)
        self._avisos_barrido.append(marco)
        return marco

    def _contenedor(self, leyenda, aviso=False):
        """Pestaña = [aviso de modo barrido] + leyenda explicativa + grilla de gráficos."""
        w = QtWidgets.QWidget()
        caja = QtWidgets.QVBoxLayout(w)
        if aviso:
            caja.addWidget(self._aviso_barrido(
            '<b>Modo barrido:</b> la frecuencia cambia cada '
            f'{2 * self.K * self.n_vec / self.fs:.2f} s' + (' (÷ velocidad)' if self.es_sim else '') +
            ' y por encima de foF2 no hay eco. '
            'Para estudiar esta pestaña conviene una frecuencia fija.'))
        caja.addWidget(leyenda)
        grilla = QtWidgets.QGridLayout()
        caja.addLayout(grilla, 1)
        return w, grilla

    def _leyenda_pestana(self, texto):
        lbl = QtWidgets.QLabel(texto)
        lbl.setWordWrap(True)
        lbl.setStyleSheet('background: #f6f8fb; border: 1px solid #dde3ea; border-radius: 4px; '
                          'padding: 6px; font-size: 9pt;')
        return lbl

    # ----- Pestaña 1
    def _pestana_senal(self):
        w, g = self._contenedor(self._leyenda_pestana(
            '<b>Qué muestra:</b> la señal tal como llega al receptor (banda base, '
            f'{self.fs / 1e3:.0f} kS/s). Arriba, una pareja de disparos: con SNR negativa los ecos '
            'quedan ocultos en el ruido. ' + (
                'Abajo a la izquierda, los mismos ecos sin ruido (solo existe en simulación): cada eco '
                'es una copia del pulso codificado de '
                f'{self.n_chips} chips × {self.tau_us:.0f} µs = {self.h_ciega:.0f} km de largo. '
                if self.es_sim else
                'Abajo a la izquierda, la magnitud del disparo A sin procesar en función de la altura '
                'equivalente: solo se ven ecos muy fuertes y el pulso directo del transmisor. ') +
            'Abajo a la derecha, el espectro.'), aviso=True)

        self.p_rx = _grafico('Pareja de disparos recibida: disparo A y luego disparo B',
                             'Tiempo desde el disparo A [ms]', 'Amplitud')
        _leyenda(self.p_rx)
        self.c_rx_i = self.p_rx.plot(pen=_lapiz(AZUL, 1), name='I (real)')
        self.c_rx_q = self.p_rx.plot(pen=_lapiz(NARANJA, 1), name='Q (imaginaria)')
        prt_ms = self.n_vec / self.fs * 1e3
        self.p_rx.addItem(pg.InfiniteLine(prt_ms, angle=90, pen=_lapiz(NEGRO, 1.5, QtCore.Qt.DashLine),
                                          label='inicio del disparo B',
                                          labelOpts={'position': 0.93, 'color': NEGRO}))
        self.p_rx.setXRange(0, 2 * prt_ms, padding=0.01)
        for c in (self.c_rx_i, self.c_rx_q):
            c.setClipToView(True)
            c.setDownsampling(auto=True, method='peak')

        if self.es_sim:
            self.p_limpia = _grafico('Ecos sin ruido del disparo A (referencia de la simulación)',
                                     'Altura equivalente  h = c·t / 2  [km]', 'Amplitud')
            _leyenda(self.p_limpia)
            self.c_l_i = self.p_limpia.plot(pen=_lapiz(AZUL, 1.2), name='I')
            self.c_l_q = self.p_limpia.plot(pen=_lapiz(NARANJA, 1.2), name='Q')
        else:
            self.p_limpia = _grafico('Disparo A sin procesar: |señal| vs altura equivalente',
                                     'Altura equivalente  h = c·t / 2  [km]', 'Magnitud')
            self.c_l_i = self.p_limpia.plot(pen=_lapiz(AZUL, 1.2))
            self.c_l_q = self.p_limpia.plot(pen=None)
        self.marcas_eco = []

        self.p_psd = _grafico('Espectro de la señal recibida (promedio)', 'Frecuencia [kHz]',
                              'Densidad espectral [dB]')
        _leyenda(self.p_psd)
        self.c_psd = self.p_psd.plot(pen=_lapiz(AZUL, 1.5), name='recibida (ecos + ruido)')
        self.c_psd_l = self.p_psd.plot(pen=_lapiz(NEGRO, 1.2, QtCore.Qt.DashLine),
                                       name='ecos sin ruido' if self.es_sim else None)
        nulo = 1e3 / self.tau_us
        for s in (-1, 1):
            self.p_psd.addItem(pg.InfiniteLine(s * nulo, angle=90, pen=_lapiz(ROJO, 1, QtCore.Qt.DotLine),
                                               label=f'{s * nulo:+.1f} kHz (1/τ)',
                                               labelOpts={'position': 0.12, 'color': ROJO}))
        self.p_psd.setXRange(-self.fs / 2e3, self.fs / 2e3, padding=0)

        g.addWidget(self.p_rx, 0, 0, 1, 2)
        g.addWidget(self.p_limpia, 1, 0)
        g.addWidget(self.p_psd, 1, 1)
        return w

    # ----- Pestaña 2
    def _pestana_compresion(self):
        self.lbl_compresion = self._leyenda_pestana(
            '<b>Qué muestra:</b> la compresión de pulso. Cada código Golay por separado deja '
            '<b>lóbulos laterales</b> (falsos ecos alrededor del verdadero). Los de A y los de B son '
            'iguales y de signo opuesto, así que en la suma <b>A + B</b> se cancelan y queda un pico '
            'limpio del doble de alto. Fila superior: teoría a nivel de chip. Fila inferior: lo que '
            'sale de los correladores del flowgraph alrededor del eco más fuerte' +
            (' (línea punteada = el mismo cálculo sin ruido).' if self.es_sim else '.'))
        w, g = self._contenedor(self.lbl_compresion, aviso=True)

        RA = np.correlate(self.code_A, self.code_A, 'full')
        RB = np.correlate(self.code_B, self.code_B, 'full')
        lags = np.arange(-(self.n_chips - 1), self.n_chips)
        datos = [('Autocorrelación del código A', RA, AZUL),
                 ('Autocorrelación del código B', RB, NARANJA),
                 ('Suma: R_A + R_B', RA + RB, VERDE)]
        for col, (titulo, R, color) in enumerate(datos):
            lob = np.max(np.abs(np.delete(R, self.n_chips - 1)))
            sub = f'lóbulo máx = {lob:.0f} de {R.max():.0f}' if lob > 0 else 'lóbulos = 0 en todos los retardos'
            p = _grafico(f'{titulo}<br><span style="font-size:9pt; font-weight:normal">{sub}</span>',
                         'Retardo [chips]', 'R[k]')
            p.addItem(pg.BarGraphItem(x=lags, height=R, width=0.25, brush=color, pen=pg.mkPen(color)))
            p.plot(lags, R, pen=None, symbol='o', symbolSize=6, symbolBrush=color, symbolPen=None)
            p.addLine(y=0, pen=_lapiz(NEGRO, 1))
            p.setYRange(-12, 34)
            g.addWidget(p, 0, col)

        self.p_corr = []
        self.c_corr = []
        self.c_corr_ideal = []
        nombres = [('Correlador A  |A|', AZUL), ('Correlador B  |B|', NARANJA),
                   ('Suma QCC  |A + B|', VERDE)]
        for col, (titulo, color) in enumerate(nombres):
            p = _grafico(titulo, "Altura virtual h' [km]", 'Amplitud')
            _leyenda(p)
            self.c_corr.append(p.plot(pen=_lapiz(color, 2), name='medido (flowgraph)'))
            self.c_corr_ideal.append(p.plot(pen=_lapiz(NEGRO, 1.2, QtCore.Qt.DashLine),
                                            name='sin ruido' if self.es_sim else None))
            self.p_corr.append(p)
            g.addWidget(p, 1, col)
        self.p_corr[1].setYLink(self.p_corr[0])
        self.p_corr[2].setYLink(self.p_corr[0])
        return w

    # ----- Pestaña 3
    def _pestana_perfil(self):
        w, g = self._contenedor(self._leyenda_pestana(
            '<b>Qué muestra:</b> el perfil de altura virtual = |A + B| promediado sobre '
            f'K = {self.K} parejas, en dB sobre su piso de ruido. En gris, una sola pareja sin '
            'integrar: la integración coherente hace que el eco sobresalga ~14 dB más. La franja gris '
            f'es la zona ciega (h &lt; {self.h_ciega:.0f} km: el receptor todavía está transmitiendo). '
            'Abajo, la historia de perfiles: cada columna es un perfil.'), aviso=True)

        self.p_perfil = _grafico("Perfil de altura virtual", "Altura virtual h' [km]",
                                 'Nivel [dB sobre el ruido]')
        _leyenda(self.p_perfil)
        self.c_par = self.p_perfil.plot(pen=_lapiz(GRIS, 1), name='1 pareja (sin integrar)')
        self.c_int = self.p_perfil.plot(pen=_lapiz(AZUL, 2), name=f'K = {self.K} parejas integradas')
        self.p_perfil.addItem(pg.InfiniteLine(self.umbral_db, angle=0,
                                              pen=_lapiz(ROJO, 1, QtCore.Qt.DashLine),
                                              label=f'umbral de detección ({self.umbral_db:.0f} dB)',
                                              labelOpts={'position': 0.85, 'color': ROJO}))
        self._zona_ciega(self.p_perfil, vertical=True)
        self.etiquetas_pico = []
        self.p_perfil.setYRange(-10, 45)

        self.p_rti = _grafico('Historia de perfiles (el más reciente a la derecha)',
                              'Perfiles anteriores', "h' [km]")
        self.img_rti = pg.ImageItem()
        self.p_rti.addItem(self.img_rti)
        self._barra_color(self.p_rti, self.img_rti)

        self.lbl_snr = QtWidgets.QLabel()
        self.lbl_snr.setTextFormat(QtCore.Qt.RichText)
        self.lbl_snr.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        self.lbl_snr.setWordWrap(True)
        self.lbl_snr.setMinimumWidth(270)
        self.lbl_snr.setMaximumWidth(320)
        self.lbl_snr.setStyleSheet('background: white; border: 1px solid #dde3ea; padding: 8px; '
                                   'font-size: 10pt;')

        g.addWidget(self.p_perfil, 0, 0)
        g.addWidget(self.lbl_snr, 0, 1, 2, 1)
        g.addWidget(self.p_rti, 1, 0)
        g.setRowStretch(0, 3)
        g.setRowStretch(1, 2)
        g.setColumnStretch(0, 1)
        return w

    # ----- Pestaña 4
    def _pestana_ionograma(self):
        w, g = self._contenedor(self._leyenda_pestana(
            "<b>Qué muestra:</b> el ionograma: altura virtual h' de los ecos en función de la "
            'frecuencia de sondeo. Cada columna es un perfil integrado; el color indica cuántos dB '
            'sobresale del ruido. La línea naranja marca la frecuencia que se está sondeando. La traza '
            'se corta en foF2: por encima, la onda atraviesa la ionosfera. Las zonas blancas todavía '
            'no se sondearon.'))

        barra = QtWidgets.QHBoxLayout()
        barra.addWidget(QtWidgets.QLabel('<b>Escala de color</b> (dB sobre el ruido): desde'))
        self.spin_escala = []
        for i, (lo, hi) in enumerate(((0, 20), (10, 60))):
            spin = QtWidgets.QSpinBox()
            spin.setRange(lo, hi)
            spin.setValue(int(self.escala_db[i]))
            spin.setSuffix(' dB')
            spin.valueChanged.connect(lambda v, i=i: self._cambiar_escala(i, v))
            self.spin_escala.append(spin)
        barra.addWidget(self.spin_escala[0])
        barra.addWidget(QtWidgets.QLabel('hasta'))
        barra.addWidget(self.spin_escala[1])
        barra.addSpacing(20)
        self.chk_teoria = QtWidgets.QCheckBox('Curva teórica del modelo (línea blanca)')
        self.chk_teoria.setChecked(False)
        self.chk_teoria.toggled.connect(lambda _: self._actualizar(forzar=True))
        self.chk_traza = QtWidgets.QCheckBox(f'Trazo detectado (rojo, > {self.umbral_db:.0f} dB)')
        self.chk_traza.setChecked(False)
        barra.addWidget(self.chk_teoria)
        self.chk_teoria.setVisible(self.es_sim)
        barra.addWidget(self.chk_traza)
        barra.addStretch(1)
        btn_limpiar = QtWidgets.QPushButton('Limpiar ionograma')
        btn_limpiar.clicked.connect(lambda: self.iono.limpiar() if self.iono else None)
        barra.addWidget(btn_limpiar)
        g.addLayout(barra, 0, 0, 1, 2)

        self.p_iono = _grafico("Ionograma h'(f)", 'Frecuencia de sondeo [MHz]', "Altura virtual h' [km]")
        self.p_iono.showGrid(x=True, y=True, alpha=0.15)
        self.img_iono = pg.ImageItem()
        self.p_iono.addItem(self.img_iono)
        self._barra_color(self.p_iono, self.img_iono)
        self._zona_ciega(self.p_iono, vertical=False)
        self.c_teoria = [self.p_iono.plot(pen=_lapiz('#ffffff', 1.2, QtCore.Qt.DashLine),
                                          connect='finite') for _ in range(3)]
        self.c_traza = pg.ScatterPlotItem(size=5, brush=pg.mkBrush(ROJO), pen=None)
        self.p_iono.addItem(self.c_traza)
        self.linea_f = pg.InfiniteLine(5.0, angle=90, pen=_lapiz(NARANJA, 2))
        self.p_iono.addItem(self.linea_f)
        f_min = getattr(self.iono, 'f_min', 1.0)
        f_max = getattr(self.iono, 'f_max', 20.0)
        self.p_iono.setXRange(f_min - 0.1, f_max + 0.1, padding=0)

        self.p_ascan = _grafico('Perfil actual', 'dB sobre el ruido', "h' [km]")
        self.p_ascan.setMaximumWidth(260)
        self.c_ascan = self.p_ascan.plot(pen=_lapiz(AZUL, 1.5))
        self.p_ascan.addItem(pg.InfiniteLine(self.umbral_db, angle=90,
                                             pen=_lapiz(ROJO, 1, QtCore.Qt.DashLine)))
        self.p_ascan.setYLink(self.p_iono)
        self.p_ascan.setXRange(-10, 45)

        self.lbl_iono = QtWidgets.QLabel()
        self.lbl_iono.setStyleSheet('font-size: 10pt; padding: 2px 4px;')
        g.addWidget(self.p_iono, 1, 0)
        g.addWidget(self.p_ascan, 1, 1)
        g.addWidget(self.lbl_iono, 2, 0, 1, 2)
        g.setRowStretch(1, 1)
        g.setColumnStretch(0, 1)
        return w

    # ----- Pestaña 5
    def _pestana_ayuda(self):
        fs, K, n_vec, spb = self.fs, self.K, self.n_vec, self.spb
        prf = float(self._var('prf', fs / n_vec))
        filas = [
            ('Frecuencia de muestreo (banda base)', f'{fs / 1e3:.0f} kS/s', 'DDC en la FPGA'),
            ('Duración de chip τ', f'{self.tau_us:.0f} µs', f'{spb} muestras/chip'),
            ('Ancho de banda B', f'{1e3 / self.tau_us:.2f} kHz', 'B = 1/τ'),
            ("Resolución en altura δh'", f'{C_LUZ * self.tau_us * 1e-6 / 2 / 1e3:.1f} km', 'c·τ/2'),
            ('Duración del pulso', f'{self.n_chips * self.tau_us:.0f} µs', f'{self.n_chips} chips'),
            ('Altura mínima (zona ciega)', f'{self.h_ciega:.0f} km', 'c·τp/2'),
            ('PRF / PRT', f'{prf:.0f} Hz / {1e3 / prf:.3f} ms', ''),
            ('Alcance sin ambigüedad', f'{C_LUZ / (2 * prf) / 1e3:.0f} km', 'c/(2·PRF)'),
            ('Muestras por disparo N_vec', f'{n_vec}', 'samp_rate // PRF'),
            ('Alturas por perfil', f'{self.n_h}  (0 a {(self.n_h - 1) * self.dh:.0f} km)',
             f'δ = {self.dh:.4f} km por muestra'),
            ('Integración', f'K = {K} parejas', f'{2 * K} disparos = {2 * K * n_vec / fs:.3f} s'),
            ('Ganancia compresión (1 código)', f'{10 * np.log10(self.n_chips):.2f} dB',
             f'10·log10({self.n_chips}) a nivel de chip'),
            ('Ganancia suma A + B', '3.01 dB', 'dos códigos complementarios'),
            ('Ganancia integración coherente', f'{10 * np.log10(K):.2f} dB', '10·log10(K)'),
        ]
        tabla = ''.join(f'<tr><td>{a}</td><td><b>{b}</b></td><td style="color:#666">{c}</td></tr>'
                        for a, b, c in filas)
        if self.es_sim:
            intro = """
        <h2>Simulación del receptor del sondador ionosférico</h2>
        <p>El flowgraph de GNU Radio (<i>ionosonde_rx_sim.grc</i>) reemplaza la Red Pitaya por un
        <b>simulador</b> que transmite los códigos Golay A y B alternados, los refleja en una ionosfera
        con capas E y F2, y les suma ruido. El resto de la cadena es el receptor real:
        selector A/B → correladores (filtros adaptados) → suma QCC → integración de K parejas →
        ionograma.</p>"""
            experimentos = """
        <h3>Experimentos sugeridos</h3>
        <ul>
        <li>Bajar la SNR de entrada hasta que el ionograma se pierda (≈ −35 dB) y comparar
        integración coherente e incoherente.</li>
        <li>Marcar <i>Intercambiar correladores A ↔ B</i> y mirar la pestaña 2: desaparece la cancelación.</li>
        <li>Subir el Doppler a 2–3 Hz: la integración coherente pierde ganancia (pestaña 3).</li>
        <li>Mover foF2 y hmF2 y ver cómo cambia la traza del ionograma.</li>
        </ul>"""
        else:
            intro = f"""
        <h2>Receptor del sondador ionosférico (Red Pitaya)</h2>
        <p>El flowgraph <i>ionosonde_rx.grc</i> recibe I/Q de la Red Pitaya
        ({self._var('rp_uri', '?')}) a {fs / 1e3:.0f} kS/s y aplica: alineación del disparo →
        programador del barrido → selector A/B → correladores (filtros adaptados) → suma QCC →
        integración de K parejas → ionograma.</p>"""
            experimentos = f"""
        <h3>Puesta en marcha con hardware</h3>
        <ol>
        <li>Modo <i>Frecuencia fija</i>, en una frecuencia con buen eco (de día, 4–6 MHz).</li>
        <li>Pestaña 3: mover <i>Alineación del disparo</i> hasta que el pulso directo del
        transmisor quede en h' ≈ 0 km (cada muestra = {self.dh:.2f} km).</li>
        <li>Pestaña 2: si no hay un pico limpio en |A + B| pero sí en |A| y |B| corridos, marcar
        <i>Intercambiar correladores A ↔ B</i>.</li>
        <li>Recién entonces pasar a <i>Barrido</i>. El transmisor tiene que seguir el mismo plan de
        frecuencias que el <i>Programador del barrido</i> (2K disparos por frecuencia).</li>
        </ol>
        <p><b>Importante:</b> {fs:.0f} / {prf:.0f} Hz = {fs / prf:.2f} muestras por disparo, pero
        los vectores usan {n_vec}. Si el transmisor dispara cada {1e3 / prf:.4f} ms exactos, el eco se
        corre {abs(fs / prf - n_vec):.2f} muestras por disparo. El PRT del transmisor tiene que ser
        exactamente {n_vec} muestras (PRF = {fs / n_vec:.4f} Hz).</p>"""
        html = intro + f"""
        <h3>Pestañas</h3>
        <ol>
        <li><b>Señal recibida</b>: la entrada del receptor. Con SNR negativa no se ve nada: es normal.</li>
        <li><b>Compresión Golay</b>: por qué se usan dos códigos. Teoría (arriba) y {'simulación' if self.es_sim else 'medición'} (abajo).</li>
        <li><b>Perfil de altura</b>: el resultado de una frecuencia, con la medición de SNR y de la
        ganancia de integración (compararla con la teórica).</li>
        <li><b>Ionograma</b>: el producto final h'(f).""" + (
            " Con la curva teórica se verifica que el receptor ubica los ecos en la altura correcta."
            if self.es_sim else "") + """</li>
        </ol>""" + experimentos + f"""
        <h3>Capturas para la tesis</h3>
        <p><b>Ctrl+S</b> o el botón <i>Guardar captura de la pestaña</i> guarda un PNG al doble de
        resolución en <i>{self._carpeta_capturas}</i>. Para exportar un solo gráfico (también en SVG),
        hacer clic derecho sobre él → <i>Export…</i></p>
        <h3>Parámetros del sistema</h3>
        <table cellpadding="4" cellspacing="0" border="1" style="border-collapse: collapse;">
        <tr style="background:#eef2f7"><th>Parámetro</th><th>Valor</th><th>Nota</th></tr>{tabla}</table>
        """
        texto = QtWidgets.QTextBrowser()
        texto.setOpenExternalLinks(True)
        texto.setHtml(html)
        texto.setStyleSheet('font-size: 10pt; padding: 8px;')
        return texto

    def _zona_ciega(self, plot, vertical):
        region = pg.LinearRegionItem(values=(0, self.h_ciega), movable=False,
                                     orientation='vertical' if vertical else 'horizontal',
                                     brush=pg.mkBrush(*FONDO_ZONA_CIEGA), pen=pg.mkPen(None))
        region.setZValue(-10)
        plot.addItem(region)
        texto = pg.TextItem('zona ciega', color='#555555' if vertical else '#ffffff',
                            anchor=(0, 1) if vertical else (0, 0.5))
        texto.setPos(2, 44) if vertical else texto.setPos(1.05, self.h_ciega / 2)
        plot.addItem(texto)

    def _barra_color(self, plot, imagen):
        cmap = pg.colormap.get('viridis')
        imagen.setColorMap(cmap)
        barra = pg.ColorBarItem(values=tuple(self.escala_db), colorMap=cmap, interactive=False,
                                label='dB sobre el ruido', width=14)
        barra.setImageItem(imagen, insert_in=plot.getPlotItem())
        setattr(self, 'barra_' + str(id(imagen)), barra)
        return barra

    # ------------------------------------------------------------- acciones
    def _cambiar_h_max(self, h):
        self.h_max = float(h)
        self._actualizar(forzar=True)

    def _cambiar_escala(self, i, valor):
        self.escala_db[i] = float(valor)
        if self.escala_db[1] <= self.escala_db[0] + 3:
            self.escala_db[1 - i] = self.escala_db[i] + (3 if i == 0 else -3)
        for img in (self.img_iono, self.img_rti):
            getattr(self, 'barra_' + str(id(img))).setLevels(tuple(self.escala_db))
        self._actualizar(forzar=True)

    def _congelar(self, activo):
        self.congelado = activo
        self.btn_congelar.setText('Reanudar gráficos' if activo else 'Congelar gráficos')

    def _capturar_pestana(self):
        nombres = ['senal_recibida', 'compresion_golay', 'perfil_altura', 'ionograma', 'ayuda']
        self._guardar_png(self.pestanas.currentWidget(), nombres[self.pestanas.currentIndex()])

    def _guardar_png(self, widget, nombre, escala=2):
        os.makedirs(self._carpeta_capturas, exist_ok=True)
        marca = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        ruta = os.path.join(self._carpeta_capturas, f'{marca}_{nombre}.png')
        imagen = QtGui.QImage(widget.size() * escala, QtGui.QImage.Format_ARGB32)
        imagen.setDevicePixelRatio(escala)
        imagen.fill(QtCore.Qt.white)
        pintor = QtGui.QPainter(imagen)
        widget.render(pintor)
        pintor.end()
        imagen.save(ruta)
        self._mensaje = (time.time(), f'Captura guardada: {ruta}')
        print('Captura guardada:', ruta)
        return ruta

    def _actualizar_info_barrido(self):
        if not hasattr(self, 'lbl_info_barrido'):
            return
        paso = float(self._var('paso_mhz', 0.1))
        f_ini, f_fin = float(self._var('f_ini', 1.0)), float(self._var('f_fin', 20.0))
        n_f = len(np.arange(f_ini, f_fin + paso / 2, paso))
        t_real = n_f * 2 * self.K * self.n_vec / self.fs
        texto = (f'Un ionograma = {n_f} frecuencias × {2 * self.K * self.n_vec / self.fs:.2f} s = '
                 f'{t_real / 60:.1f} min en tiempo real')
        if self._tiene('velocidad'):
            vel = float(self._var('velocidad', 1))
            texto += f' → {t_real / vel:.0f} s a ×{vel:.0f}'
        self.lbl_info_barrido.setText(texto + '.')

    # -------------------------------------------------------- actualización
    def _actualizar(self, forzar=False):
        if self.congelado and not forzar:
            return
        if forzar:
            self._ultima.clear()
        try:
            i = self.pestanas.currentIndex()
            (self._act_senal, self._act_compresion, self._act_perfil, self._act_ionograma,
             lambda: None)[i]()
            self._act_estado()
        except Exception:                                    # no frenar el timer por un error
            traceback.print_exc()

    @staticmethod
    def _capturas(monitor):
        if monitor is None:
            return []
        with monitor.lock:
            return list(monitor.capturas)

    def _nueva(self, clave, valor):
        """True si 'valor' cambió desde la última vez (evita redibujar lo mismo)."""
        if self._ultima.get(clave) == valor:
            return False
        self._ultima[clave] = valor
        return True

    def _ecos_modelo(self, f):
        """Ecos ionosféricos del modelo a la frecuencia f (sin el pulso directo TX)."""
        if self.sim is None or f is None:
            return []
        return [e for e in self.sim.ecos(f) if e[2] != 'TX' and e[0] < self.n_h * self.dh]

    @staticmethod
    def _max_alturas(mat, n_px):
        """Reduce el eje de alturas (filas) tomando el máximo de cada grupo de filas.
        Si se dibujan más filas que píxeles, el gráfico saltea filas y los ecos (de pocas
        muestras de alto) se ven más débiles o cortados; con el máximo no se pierden."""
        factor = max(1, int(np.ceil(mat.shape[0] / max(n_px, 1))))
        if factor > 1:
            n = (mat.shape[0] // factor) * factor
            mat = mat[:n].reshape(n // factor, factor, -1).max(axis=1)
        return mat, factor

    def _act_senal(self):
        caps = self._capturas(self.mon_rx)
        if not caps or not self._nueva('senal', (caps[-1][0], self.h_max)):
            return
        _, info, rx, limpia = caps[-1]
        n = self.n_vec
        t_ms = np.arange(2 * n) / self.fs * 1e3
        self.c_rx_i.setData(t_ms, rx.real)
        self.c_rx_q.setData(t_ms, rx.imag)
        h = np.arange(n) * self.dh
        m = h <= self.h_max
        if limpia is not None:
            self.c_l_i.setData(h[m], limpia[:n][m].real)
            self.c_l_q.setData(h[m], limpia[:n][m].imag)
        else:
            self.c_l_i.setData(h[m], np.abs(rx[:n][m]))
        self.p_limpia.setXRange(0, self.h_max, padding=0.01)
        for item in self.marcas_eco:
            self.p_limpia.removeItem(item)
        self.marcas_eco = []
        f = info.get('f_mhz')
        ecos = self.sim.ecos(f) if (self.sim is not None and f is not None) else []
        for h_eco, _, nombre in ecos:
            if h_eco <= self.h_max:
                linea = pg.InfiniteLine(h_eco, angle=90, pen=_lapiz(GRIS, 1, QtCore.Qt.DashLine),
                                        label=f'{nombre}: {h_eco:.0f} km',
                                        labelOpts={'position': 0.92, 'color': NEGRO})
                self.p_limpia.addItem(linea)
                self.marcas_eco.append(linea)
        titulo = 'Ecos sin ruido del disparo A' if limpia is not None else 'Disparo A sin procesar'
        if f is not None:
            titulo += f' — f = {f:.2f} MHz'
            if self.es_sim and not ecos:
                titulo += '  (sin ecos: f > foF2)'
        self.p_limpia.setTitle(titulo, size='11pt', bold=True)

        if welch is None:
            return
        fr, p = welch(rx, fs=self.fs, nperseg=1024, detrend=False, return_onesided=False,
                      scaling='density')
        if limpia is not None:
            _, pl = welch(limpia, fs=self.fs, nperseg=1024, detrend=False, return_onesided=False,
                          scaling='density')
        else:
            pl = np.zeros_like(p)
        orden = np.argsort(fr)
        fr, p, pl = fr[orden] / 1e3, p[orden], pl[orden]
        if self._psd is None or len(self._psd[0]) != len(p):
            self._psd = (p, pl)
        else:
            self._psd = (0.85 * self._psd[0] + 0.15 * p, 0.85 * self._psd[1] + 0.15 * pl)
        p_db = 10 * np.log10(self._psd[0] + 1e-20)
        l_db = 10 * np.log10(self._psd[1] + 1e-20)
        self.c_psd.setData(fr, p_db)
        piso = float(np.median(p_db))
        if pl.max() > 0:
            self.c_psd_l.setData(fr, l_db)
            abajo = min(float(l_db.max()) - 40, piso - 5)
            arriba = max(float(l_db.max()), float(p_db.max())) + 8
        else:
            self.c_psd_l.setData([], [])
            abajo, arriba = piso - 15, float(p_db.max()) + 8
        self.p_psd.setYRange(abajo, arriba, padding=0)

    def _correlacion_ideal(self, limpia):
        """Mismo cálculo que los correladores del flowgraph, sobre los ecos sin ruido."""
        n = self.n_vec
        tA = np.asarray(self._var('taps_A', []), complex)
        tB = np.asarray(self._var('taps_B', []), complex)
        yA = np.convolve(limpia[:n], tA)[:n]
        yB = np.convolve(limpia[n:2 * n], tB)[:n]
        w = slice(self.h_off, self.h_off + self.n_h)
        return yA[w], yB[w], (yA + yB)[w]

    def _act_compresion(self):
        caps_c = self._capturas(self.mon_c)
        if not caps_c or not self._nueva('compresion', caps_c[-1][0]):
            return
        caps_rx = {c[0]: c for c in self._capturas(self.mon_rx)}
        comunes = [c for c in caps_c if c[0] in caps_rx and caps_rx[c[0]][3] is not None]
        pareja, info, A, B, S = comunes[-1] if comunes else caps_c[-1]
        w = slice(self.h_off, self.h_off + self.n_h)
        medidos = [np.abs(A[w]), np.abs(B[w]), np.abs(S[w])]
        ideales = None
        if comunes:
            ideales = [np.abs(x) for x in self._correlacion_ideal(caps_rx[pareja][3])]
        f = info.get('f_mhz')
        ref = medidos[2]
        k_int = None                         # eco detectado en el perfil integrado
        if ideales is not None:              # simulación: el modelo dice si hay eco
            hay_eco = bool(self._ecos_modelo(f)) and ideales[0].max() > 0
            if hay_eco:
                ref = ideales[0] + ideales[1]
        if self.iono is not None and self.iono.ultimo is not None and \
                self.iono.ultimo[1].get('f_mhz') == f:
            pdb = self.iono.ultimo[3]
            kk = self.i_80km + int(np.argmax(pdb[self.i_80km:]))
            if pdb[kk] >= self.umbral_db:
                k_int = kk
        # el eco ionosférico más fuerte (sin la zona ciega)
        if ideales is None and k_int is not None:
            k = k_int
        else:
            k = self.i_80km + int(np.argmax(ref[self.i_80km:]))
        if ideales is None:                  # receptor real: ¿el eco sobresale en UNA pareja?
            pico = float(np.max(medidos[2][max(k - 2, 0):k + 3]))
            hay_eco = 20 * np.log10(pico / max(float(np.median(medidos[2])), 1e-12)) >= self.umbral_db
        mitad = 2 * self.n_chips * self.spb
        i0, i1 = max(k - mitad, 0), min(k + mitad + 1, self.n_h)
        h = np.arange(i0, i1) * self.dh
        tope = 0.0
        titulos = ['Correlador A  |A|', 'Correlador B  |B|', 'Suma QCC  |A + B|']
        for j in range(3):
            self.c_corr[j].setData(h, medidos[j][i0:i1])
            if hay_eco and ideales is not None:
                self.c_corr_ideal[j].setData(h, ideales[j][i0:i1])
            else:
                self.c_corr_ideal[j].setData([], [])
            if hay_eco:
                base = ideales[j] if ideales is not None else medidos[j]
                lejos = np.r_[max(k - 95, 0):max(k - self.spb, 0),
                              min(k + self.spb + 1, self.n_h):min(k + 96, self.n_h)]
                psl = 20 * np.log10(max(base[lejos].max(), 1e-12) / max(base[k], 1e-12))
                sub = (f'pico {base[k]:.1f} · lóbulo lateral máx: {psl:.1f} dB' if psl > -60 else
                       f'pico {base[k]:.1f} · lóbulos laterales: 0 (cancelados)')
                tope = max(tope, float(base[i0:i1].max()))
            elif self.es_sim:
                sub = f'sin eco a {f:.2f} MHz: solo ruido' if f is not None else 'sin eco'
            elif k_int is not None:
                sub = f'eco integrado en {k_int * self.dh:.0f} km · en 1 pareja: bajo el ruido'
            else:
                sub = 'sin eco visible en 1 pareja: solo ruido'
            tope = max(tope, float(medidos[j][i0:i1].max()))
            self.p_corr[j].setTitle(f'{titulos[j]}<br><span style="font-size:9pt; font-weight:normal">'
                                    f'{sub}</span>', size='11pt', bold=True)
        self.p_corr[0].setYRange(0, tope * 1.08, padding=0)
        for p in self.p_corr:
            p.setXRange(float(h[0]), float(h[-1]), padding=0)
        aviso = ('<span style="color:#d62728"><b>Correladores intercambiados:</b> la rama A recibe '
                 'los disparos B. No hay pico limpio ni cancelación.</span><br>'
                 if int(self._var('swap_ab', 0)) else '')
        donde = f'pareja #{pareja}, f = {f:.2f} MHz' if f is not None else f'pareja #{pareja}'
        detalle = ('; punteada = mismo cálculo sin ruido). Con SNR de entrada ≥ +10 dB la '
                   'cancelación también se ve en la curva medida.' if self.es_sim else
                   '). Con ecos débiles una sola pareja queda dominada por el ruido: usar frecuencia '
                   'fija y un eco fuerte para ver la cancelación.')
        self.lbl_compresion.setText(
            aviso + '<b>Qué muestra:</b> cada código Golay por separado deja <b>lóbulos laterales</b> '
            '(falsos ecos alrededor del verdadero). Los de A y B son iguales y de signo opuesto: en la '
            'suma <b>A + B</b> se cancelan y queda un pico limpio del doble de alto. Arriba: teoría a '
            'nivel de chip. Abajo: salida de los correladores del flowgraph alrededor del eco más '
            f'fuerte ({donde}{detalle}')

    def _act_perfil(self):
        if self.iono is None or self.iono.ultimo is None:
            return
        cont, info, perfil, perfil_db = self.iono.ultimo
        if not self._nueva('perfil', (cont, self.h_max)):
            return
        h = np.arange(self.n_h) * self.dh
        m = h <= self.h_max
        self.c_int.setData(h[m], perfil_db[m])
        self.p_perfil.setXRange(0, self.h_max, padding=0.01)
        f = info.get('f_mhz')
        modo = 'coherente' if int(self._var('integracion', 0)) == 0 else 'incoherente'

        # Una pareja sin integrar de la misma frecuencia (misma pasada del barrido)
        par = None
        caps_rx = {c[0]: c for c in self._capturas(self.mon_rx)}
        for c in reversed(self._capturas(self.mon_c)):
            if c[1].get('f_mhz') == f and c[1].get('barrido') == info.get('barrido'):
                par = c
                break
        if par is not None:
            S = np.abs(par[4][self.h_off:self.h_off + self.n_h])
            self.c_par.setData(h[m], _db(S / np.median(S))[m])
        else:
            self.c_par.setData([], [])

        lineas = [f'<b>Frecuencia:</b> {f:.2f} MHz' if f is not None else '<b>Frecuencia:</b> —',
                  f'<b>Integración:</b> {modo}, K = {self.K}']
        det = perfil_db.copy()
        det[:self.i_80km] = -np.inf
        k_det = int(np.argmax(det))
        if det[k_det] >= self.umbral_db:
            lineas.append(f"<b>Eco más fuerte:</b> h' = {k_det * self.dh:.1f} km, "
                          f'{det[k_det]:.1f} dB sobre el ruido')
        else:
            lineas.append(f'<b>Ningún eco supera el umbral</b> de {self.umbral_db:.0f} dB')
        ecos = self._ecos_modelo(f)
        if self.es_sim:
            if ecos:
                lineas.append('<b>Modelo:</b> ' + ', '.join(f'{nom} a {he:.0f} km'
                                                            for he, _, nom in ecos))
            elif f is not None:
                lineas.append('<b>Modelo:</b> sin ecos a esta frecuencia (f > foF2)')

        # SNR en la altura del eco: 1 pareja vs. K parejas integradas (misma frecuencia)
        if par is not None and f is not None:
            ruido_par = np.median(S ** 2) / np.log(2)
            ruido_int = np.median(perfil ** 2) / np.log(2)
            medir = None
            if self.es_sim and ecos and par[0] in caps_rx and caps_rx[par[0]][3] is not None:
                _, _, ideal = self._correlacion_ideal(caps_rx[par[0]][3])
                k = self.i_80km + int(np.argmax(np.abs(ideal[self.i_80km:])))
                medir = (k, np.abs(ideal[k]) ** 2)          # potencia exacta del eco
            elif not self.es_sim and det[k_det] >= self.umbral_db:
                medir = (k_det, S[k_det] ** 2 - ruido_par)  # estimada en una sola pareja
            if medir is not None:
                k, p_eco = medir
                snr_par = 10 * np.log10(max(p_eco, 1e-3 * ruido_par) / ruido_par)
                snr_int = 10 * np.log10(max(perfil[k] ** 2 - ruido_int, 1e-3 * ruido_int) / ruido_int)
                clave = (f, info.get('barrido'), modo, int(self._var('swap_ab', 0)),
                         float(self._var('snr_db', 0)), float(self._var('doppler_hz', 0)),
                         int(self._var('alineacion', 0)))
                a = 0.3 if self.es_sim else 0.1
                if self._snr.get('clave') != clave:
                    self._snr = {'clave': clave, 'par': snr_par, 'int': snr_int, 'k': k}
                else:
                    self._snr.update(par=(1 - a) * self._snr['par'] + a * snr_par,
                                     int=(1 - a) * self._snr['int'] + a * snr_int, k=k)
        if 'par' in self._snr and self._snr['clave'][0] == f and f is not None and \
                (ecos or not self.es_sim):
            s_par, s_int = self._snr['par'], self._snr['int']
            lineas += ['', f"<b>SNR medida en h' = {self._snr['k'] * self.dh:.0f} km</b>",
                       f'1 pareja (A + B): <b>{s_par:.1f} dB</b>' +
                       ('' if self.es_sim else ' (estimada)'),
                       f'{self.K} parejas integradas: <b>{s_int:.1f} dB</b>',
                       f'<b>Ganancia de integración: {s_int - s_par:.1f} dB</b>',
                       f'(teórica coherente: 10·log10({self.K}) = {10 * np.log10(self.K):.2f} dB)']
            if modo == 'incoherente':
                lineas.append('<span style="color:#666">La integración incoherente no baja el piso '
                              'de ruido: solo reduce su fluctuación. El eco se distingue mejor que en '
                              '1 pareja, pero su SNR (pico sobre el piso) casi no mejora.</span>')
        elif int(self._var('modo_barrido', 1)) == 1:
            lineas += ['', '<span style="color:#666">La ganancia de integración se mide en modo '
                       '"Frecuencia fija".</span>']
        g_comp = 10 * np.log10(self.n_chips * self.spb)
        g_int = 10 * np.log10(self.K)
        if self.es_sim:
            snr_in = float(self._var('snr_db', 0))
            lineas += ['', '<b>Cadena de ganancias (teoría)</b>',
                       f'SNR de entrada: {snr_in:.0f} dB por muestra',
                       f'+ compresión ({self.n_chips * self.spb} muestras): {g_comp:.1f} dB',
                       '+ suma A + B: 3.0 dB',
                       f'+ integración coherente: {g_int:.1f} dB',
                       f'= {snr_in + g_comp + 3.0103 + g_int:.1f} dB (menos la atenuación del eco)']
        else:
            lineas += ['', '<b>Ganancia de procesamiento (teoría)</b>',
                       f'compresión ({self.n_chips * self.spb} muestras): {g_comp:.1f} dB',
                       '+ suma A + B: 3.0 dB',
                       f'+ integración coherente: {g_int:.1f} dB',
                       f'= {g_comp + 3.0103 + g_int:.1f} dB sobre la SNR por muestra de entrada']
        self.lbl_snr.setText('<br>'.join(lineas))

        # Etiquetas sobre los picos detectados
        for t in self.etiquetas_pico:
            self.p_perfil.removeItem(t)
        self.etiquetas_pico = []
        resto = perfil_db.copy()
        resto[:self.i_80km] = -np.inf
        resto[~m] = -np.inf
        for _ in range(3):
            j = int(np.argmax(resto))
            if resto[j] < self.umbral_db:
                break
            t = pg.TextItem(f"{j * self.dh:.0f} km\n{resto[j]:.0f} dB", color=AZUL, anchor=(0.5, 1.1))
            t.setPos(j * self.dh, float(resto[j]))
            self.p_perfil.addItem(t)
            self.etiquetas_pico.append(t)
            resto[max(j - 60, 0):j + 60] = -np.inf
        self.p_perfil.setYRange(-10, max(45.0, float(perfil_db[m].max()) + 8), padding=0)

        hist = list(self.iono.historial)
        if hist:
            n_vis = int(np.searchsorted(h, self.h_max)) + 1
            mat = np.array([p[:n_vis] for _, p in hist], dtype=np.float32).T
            mat, factor = self._max_alturas(mat, int(self.p_rti.getViewBox().height()))
            self.img_rti.setImage(mat, autoLevels=False, levels=tuple(self.escala_db))
            self.img_rti.setRect(QtCore.QRectF(-len(hist) + 0.5, 0, len(hist),
                                               mat.shape[0] * factor * self.dh))
            self.p_rti.setXRange(-len(hist) + 0.5, 0.5, padding=0)
            self.p_rti.setYRange(0, self.h_max, padding=0)

    def _teoria(self):
        clave = tuple(float(self._var(v, 0)) for v in ('foE', 'foF2', 'hmF2', 'segundo_salto'))
        if clave == self._clave_teoria or self.sim is None:
            return
        self._clave_teoria = clave
        fs_mhz = np.arange(1.0, 20.0001, 0.01)
        curvas = [np.full(fs_mhz.size, np.nan) for _ in range(3)]
        for i, f in enumerate(fs_mhz):
            for h, _, nombre in self.sim.ecos(f):
                if nombre in ('E', 'F2'):
                    curvas[0 if nombre == 'E' else 1][i] = h
                elif nombre.startswith('2'):
                    curvas[2][i] = h
        for linea, y in zip(self.c_teoria, curvas):
            linea.setData(fs_mhz, y)

    def _act_ionograma(self):
        if self.iono is None:
            return
        self._teoria()
        for linea in self.c_teoria:
            linea.setVisible(self.chk_teoria.isChecked())
        cont = self.iono.contador
        if not self._nueva('ionograma', (cont, self.h_max, self.chk_traza.isChecked())):
            return
        n_vis = min(int(np.ceil(self.h_max / self.dh)) + 1, self.iono.n_h)
        with self.iono.lock:
            mat = self.iono.matriz_db[:, :n_vis].T.copy()
            traza = self.iono.traza_h.copy()
        mat, factor = self._max_alturas(mat, int(self.p_iono.getViewBox().height()))
        df, f_min = self.iono.df, self.iono.f_min
        self.img_iono.setImage(mat, autoLevels=False, levels=tuple(self.escala_db))
        self.img_iono.setRect(QtCore.QRectF(f_min - df / 2, 0, self.iono.n_f * df,
                                            mat.shape[0] * factor * self.dh))
        self.p_iono.setYRange(0, self.h_max, padding=0)
        f_grilla = f_min + np.arange(self.iono.n_f) * df
        if self.chk_traza.isChecked():
            ok = np.isfinite(traza)
            self.c_traza.setData(f_grilla[ok], traza[ok])
        else:
            self.c_traza.setData([], [])
        ultimo = self.iono.ultimo
        texto = []
        if ultimo is not None:
            _, info, _, perfil_db = ultimo
            f = info.get('f_mhz')
            h = np.arange(self.n_h) * self.dh
            m = h <= self.h_max
            self.c_ascan.setData(perfil_db[m], h[m])
            if f is not None:
                self.linea_f.setValue(f)
                self.p_ascan.setTitle(f'Perfil actual ({f:.2f} MHz)', size='11pt', bold=True)
                if info.get('modo') == 'barrido':
                    avance = 100.0 * (info.get('paso', 0) + 1) / max(info.get('n_pasos', 1), 1)
                    texto.append(f'<b>Barrido #{info.get("barrido")}</b>: {avance:.0f} %')
                else:
                    texto.append('<b>Frecuencia fija</b> (solo se actualiza esa columna)')
                texto.append(f'f actual = <b>{f:.2f} MHz</b>')
        if self.es_sim:
            texto.append(f'Modelo: foE = {float(self._var("foE", 0)):.1f} MHz, '
                         f'foF2 = {float(self._var("foF2", 0)):.1f} MHz, '
                         f'hmF2 = {float(self._var("hmF2", 0)):.0f} km')
        f_traza_F = f_grilla[np.isfinite(traza) & (traza > 150)]
        if f_traza_F.size:
            texto.append(f'foF2 estimada (máx. f con eco F detectado) ≈ <b>{f_traza_F.max():.2f} MHz</b>')
        self.lbl_iono.setText('  ·  '.join(texto))

    def _act_estado(self):
        partes = []
        if self.iono is not None:
            ahora, cont = time.time(), self.iono.contador
            t0, c0, tasa = self._tasa
            if ahora - t0 > 2.0:
                tasa = (cont - c0) / (ahora - t0)
                self._tasa = (ahora, cont, tasa)
            if self.iono.ultimo is not None:
                info = self.iono.ultimo[1]
                if info.get('f_mhz') is not None:
                    partes.append(f'f = {info["f_mhz"]:.2f} MHz')
                if info.get('modo') == 'barrido':
                    partes.append(f'barrido #{info.get("barrido")}: '
                                  f'{100.0 * (info.get("paso", 0) + 1) / max(info.get("n_pasos", 1), 1):.0f} %')
            partes.append(f'{tasa:.1f} perfiles/s')
        partes.append('integración ' + ('coherente' if int(self._var('integracion', 0)) == 0
                                       else 'incoherente') + f' (K = {self.K})')
        if self._tiene('snr_db'):
            partes.append(f'SNR entrada {float(self._var("snr_db", 0)):.0f} dB')
        if self._tiene('velocidad'):
            partes.append(f'velocidad ×{float(self._var("velocidad", 1)):.0f}')
        if self._tiene('alineacion') and int(self._var('alineacion', 0)):
            partes.append(f'alineación {int(self._var("alineacion", 0))} muestras')
        if int(self._var('swap_ab', 0)):
            partes.append('<span style="color:#d62728"><b>A ↔ B intercambiados</b></span>')
        if self.congelado:
            partes.append('<b>GRÁFICOS CONGELADOS</b>')
        mensaje = getattr(self, '_mensaje', None)
        if mensaje and time.time() - mensaje[0] < 6:
            partes.append(f'<span style="color:#2a7a3b">{mensaje[1]}</span>')
        self.estado.setText('   |   '.join(partes))


def instalar(tb):
    """Arma la interfaz dentro de la ventana Qt que genera GRC. Devuelve el objeto
    (hay que guardar la referencia: self._gui = instalar(self))."""
    return InterfazSondador(tb)
