# Sondador Ionosférico Digital con Red Pitaya y GNU Radio

Este repositorio contiene la implementación en software y los diagramas de flujo de **GNU Radio Companion (GRC)** para el procesamiento digital de señales de un **Sondador Ionosférico Digital Vertical (Ionosonda HF)**. 

El sistema utiliza la placa **Red Pitaya SDRlab 122-16**, la técnica de **compresión de pulso por Códigos Complementarios de Golay de 16 bits** y el **Método QCC (Quadrature Component Correlation)** para obtener ionogramas de alta resolución con bajo nivel de potencia emitida.

---

## 📋 Características Principales

- **Muestreo Directo de RF:** Operación nativa en la banda HF (1 a 20 MHz) utilizando el ADC de 16 bits a 122.88 MS/s de la Red Pitaya.
- **Compresión de Pulso Golay (16 bits):** Ganancia de procesamiento de $12.04\text{ dB}$ y cancelación matemática absoluta de lóbulos laterales de tiempo (0 dB sidelobes).
- **Resolución Espacial Elevada:** Sub-pulsos (*chips*) de $30\ \mu\text{s}$, otorgando una resolución en altura virtual de $\delta h_v = 4.5\text{ km}$.
- **Integración Coherente ($K=25$):** Promedio de ráfagas que aporta $13.98\text{ dB}$ adicionales de ganancia contra el ruido atmosférico.
- **Arquitectura Distribuida:** Procesamiento previo en la FPGA Zynq-7020 / ARM de la Red Pitaya, transferencia por Gigabit Ethernet vía `iiod` / `libiio` y procesamiento de matriz en GNU Radio / Python.

---

## 🛠️ Especificaciones del Sistema

| Parámetro | Valor Nomimal | Explicación / Fórmulas |
| :--- | :--- | :--- |
| **Banda de Frecuencia** | $1.0\text{ MHz} - 20.0\text{ MHz}$ | Sondeo vertical de las capas D, E y F |
| **Frecuencia de Muestreo (Banda Base)** | $200\text{ kS/s}$ | Decimado digital en FPGA (DDC) |
| **Duración del Sub-pulso ($\tau_s$)** | $30\ \mu\text{s}$ | Ancho de banda $B = 33.33\text{ kHz}$ |
| **Resolución en Altura ($\delta h_v$)** | $\mathbf{4.5\text{ km}}$ | $\delta h_v = \frac{c \cdot \tau_s}{2}$ |
| **Duración del Pulso ($\tau_p$)** | $480\ \mu\text{s}$ | $16\text{ bits} \times 30\ \mu\text{s}$ |
| **Distancia Ciega ($h_{min}$)** | $\mathbf{72\text{ km}}$ | $h_{min} = \frac{c \cdot \tau_p}{2}$ |
| **FRP / PRF** | $60\text{ Hz}$ | Período $PRT = 16.667\text{ ms}$ |
| **Alcance Máximo ($h_{max}$)** | $\mathbf{2500\text{ km}}$ | $h_{max} = \frac{c}{2 \cdot FRP}$ |
| **Muestras por PRT ($N_{vec}$)** | $3333\text{ muestras}$ | $200.000\text{ S/s} \times 16.667\text{ ms}$ |
| **Promedio Coherente ($K$)** | $25\text{ parejas}$ | Ganancia total de procesamiento = $26.02\text{ dB}$ |

---

## 📂 Estructura del Repositorio

```text
.
├── CLAUDE.md               # Guía de contexto e instrucciones para Claude Code
├── README.md               # Documentación general del proyecto
├── ionosonde_rx.grc        # Diagrama de flujo principal de GNU Radio Companion
├── ionosonde_rx.py         # Script compilado del receptor GNU Radio
├── ionosonde_rx_sim.grc    # Simulación completa del receptor (sin hardware)
├── ionosonde_gui.py        # Interfaz gráfica común (la cargan los dos .grc)
├── capturas/               # PNG guardados desde la interfaz (Ctrl+S)
├── respaldo/               # Versiones anteriores de los .grc
├── test_loopback_sim.py    # Script de prueba independiente (Simulación Golay QCC)
└── ionogram_gui.py         # Interfaz de visualización en tiempo real (PyZMQ + Matplotlib)
```

---

## 🚀 Requisitos e Instalación

### Requisitos de Hardware
1. Placa **Red Pitaya SDRlab 122-16**.
2. Computadora Anfitriona (Host PC) con **Ubuntu 22.04 LTS** o superior.
3. Cable de red Ethernet Gigabit (RJ45).

### Requisitos de Software
```bash
sudo apt update
sudo apt install gnuradio gr-iio libiio-utils python3-pip python3-numpy python3-scipy python3-matplotlib python3-zmq
```

---

## 🏃 Modo de Uso y Pruebas

### 1. Validación Matemática (Simulación en Lazo Cerrado)
Antes de conectar la Red Pitaya, se puede ejecutar la simulación en Python para verificar la cancelación de lóbulos laterales y el comportamiento del método QCC frente al ruido:
```bash
python3 test_loopback_sim.py
```

### 1b. Simulación del receptor en GNU Radio (con interfaz gráfica)
Abrir `ionosonde_rx_sim.grc` en GNU Radio Companion y ejecutarlo (F6). `ionosonde_gui.py`
tiene que estar en la misma carpeta. Un bloque simulador reemplaza a la Red Pitaya: transmite los
códigos Golay A/B, los refleja en una ionosfera con capas E y F2 (con Doppler y ruido) y el resto
de la cadena es el receptor real. La ventana tiene un panel de control y cinco pestañas: señal
recibida, compresión Golay, perfil de altura (con la medición de la ganancia de integración),
ionograma h'(f) y ayuda. **Ctrl+S** guarda la pestaña visible en `capturas/`.

### 2. Compilación del Diagrama de Flujo en GNU Radio
Para generar el script ejecutable de Python a partir del diagrama `.grc`:
```bash
grcc -o . ionosonde_rx.grc
```

### 3. Ejecución del Receptor e Interfaz del Ionograma
1. Asegurarse de que la Red Pitaya esté encendida y accesible en la red (`ping 192.168.1.100`).
2. Abrir `ionosonde_rx.grc` en GNU Radio Companion y ejecutarlo (F6). Usa la misma interfaz que la
   simulación (`ionosonde_gui.py`), con los controles del receptor real. Para la puesta en marcha:
   - En *Frecuencia fija*, mover **Alineación del disparo** hasta que el pulso directo del
     transmisor quede en h' ≈ 0 km (pestaña 3).
   - Si |A + B| no muestra un pico limpio, marcar **Intercambiar correladores A ↔ B** (pestaña 2).
   - El bloque *Programador del barrido* marca cada frecuencia (2K disparos) y publica
     `{'freq': Hz}` por su puerto de mensajes `freq`: **falta conectarlo a la sintonía del hardware**,
     y el transmisor tiene que seguir el mismo plan de barrido.
   - El transmisor debe disparar cada `n_vec` = 3333 muestras exactas (PRF = 60.006 Hz), no cada
     16.667 ms: si no, el eco se corre 1/3 de muestra por disparo.
3. Opcional: el receptor también publica los perfiles integrados (3200 alturas, 0–2398 km) por ZMQ
   en `tcp://127.0.0.1:5555` para una interfaz externa:
```bash
python3 ionogram_gui.py
```
4. O ejecutar el receptor ya compilado:
```bash
python3 ionosonde_rx.py
```

---

## 📜 Licencia y Créditos
Desarrollado como parte del proyecto de Tesis de Grado en Ingeniería en Telecomunicaciones (FACET - UNT). Basado en los desarrollos y publicaciones del Dr. Miguel A. Cabrera y la arquitectura de radar ionosférico AIS-INGV.
