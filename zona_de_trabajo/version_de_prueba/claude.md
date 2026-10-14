# CLAUDE.md - Instrucciones y Contexto para Claude Code

## 1. Visión General del Proyecto
Este proyecto implementa el receptor y sistema de procesamiento digital de señales (DSP) para un **Sondador Ionosférico Digital Vertical** basado en la placa **Red Pitaya SDRlab 122-16**, utilizando **GNU Radio Companion (GRC)**, **Python 3** y el **Método QCC (Quadrature Component Correlation)** con **Códigos Complementarios de Golay de 16 bits**.

El objetivo de este repositorio es proporcionar los diagramas de flujo de GNU Radio (`.grc`), scripts de simulación en Python y módulos de prueba en lazo cerrado (*loopback*) para capturar ecos ionosféricos, realizar la compresión de pulso y publicar matrices de datos para la generación del ionograma (Altura Virtual $h'$ vs. Frecuencia $f$).

---

## 2. Especificaciones Técnicas y Parámetros del Sistema

### Hardware y Red
- **Placa SDR:** Red Pitaya SDRlab 122-16 (SoC Xilinx Zynq-7020).
- **Conversores:** ADC de 16 bits a 122.88 MS/s (Muestreo Directo de RF en HF).
- **Interfaz de Comunicaciones:** Gigabit Ethernet (`iiod` / `libiio` en `192.168.1.100:1822`).

### Parámetros de Radar y Señal
- **Rango de Frecuencias de Sondeo:** 1.0 MHz a 20.0 MHz (Banda HF).
- **Frecuencia de Muestreo en Banda Base ($f_{s,bb}$):** $200\text{ kS/s}$ ($200.000\text{ muestras/segundo}$).
- **Código de Modulación:** Códigos Complementarios de Golay de 16 bits (BPSK).
  - `Code_A` = `[+1, +1, +1, -1, +1, +1, -1, +1, +1, +1, +1, -1, -1, -1, +1, -1]`
  - `Code_B` = `[+1, +1, +1, -1, +1, +1, -1, +1, -1, -1, -1, +1, +1, +1, -1, +1]`
- **Duración de Sub-pulso ($\tau_s$ / *chip*):** $30\ \mu\text{s}$ $\rightarrow$ Ancho de banda $B = 33.33\text{ kHz}$.
- **Resolución en Altura Virtual ($\delta h_v$):** $4.5\text{ km}$ ($\delta h_v = c \cdot \tau_s / 2$).
- **Duración Total de Pulso ($\tau_p$):** $480\ \mu\text{s}$ ($16 \times 30\ \mu\text{s}$).
- **Distancia Ciega / Altura Mínima ($h_{min}$):** $72\text{ km}$ ($h_{min} = c \cdot \tau_p / 2$).
- **Frecuencia de Repetición de Pulso (FRP / PRF):** $60\text{ Hz}$.
- **Período de Repetición (PRT):** $16.667\text{ ms}$ ($1 / 60\text{ Hz}$).
- **Alcance Máximo Sin Ambigüedad ($h_{max}$):** $2500\text{ km}$ ($h_{max} = c / (2 \cdot FRP)$).
- **Tamaño del Vector por Disparo ($N_{vec}$):** $3333\text{ muestras}$ ($200.000\text{ S/s} \times 0.016667\text{ s}$).
- **Sobremuestreo por Chip ($spb$):** $6\text{ muestras/chip}$ ($30\ \mu\text{s} \times 200\text{ kS/s}$).
- **Promedio Coherente ($K$):** $25\text{ parejas}$ ($\text{Ganancia} = 13.98\text{ dB}$).

---

## 3. Arquitectura del Flujo en GNU Radio (`ionosonde_rx.grc` y `ionosonde_rx_sim.grc`)

Los dos flowgraphs comparten la misma cadena de procesamiento; solo cambia la fuente. Cuando generes o modifiques archivos `.grc` o scripts de Python asociados, debes respetar el siguiente flujo de bloques (nombres de bloque entre paréntesis):

1. **Fuente:**
   - **Receptor real (`ionosonde_rx.grc`):** `iio_device_source` (canales `voltage0` = I y `voltage1` = Q, salida `short`) → `short_to_float` (escala 32768) ×2 → `float_to_complex`. Alternativa deshabilitada: `zmq_sub_source` en `tcp://127.0.0.1:5556`.
   - **Simulación (`ionosonde_rx_sim.grc`):** bloque Python `simulador` (Golay A/B + capas E y F2 + Doppler + ruido) → `throttle` a `samp_rate × velocidad`.
2. **`blocks_delay` (`alineacion_disparo`, solo receptor real):**
   - Retrasa la señal `alineacion` muestras para que cada vector empiece con el disparo. Se calibra llevando el pulso directo del TX a $h' \approx 0$ km.
3. **Marcado de frecuencia (tag `sondeo`):**
   - Cada frecuencia de sondeo dura $2K$ disparos ($K$ parejas A/B). En su primera muestra se agrega el tag `sondeo` = `{f_mhz, paso_mhz, paso, n_pasos, barrido, modo}`.
   - Receptor real: lo agrega el bloque Python `programador_barrido`, que además publica `{'freq': Hz}` por su puerto de mensajes `freq` (pendiente: conectarlo a la sintonía del hardware). Simulación: lo agrega el `simulador`.
4. **`blocks_deinterleave` (`selector_golay`, Selector Golay A/B):**
   - Sobre el flujo (sin vectores), en bloques de $N_{vec} = 3333$ muestras: Disparo Par $\rightarrow$ Rama A, Disparo Impar $\rightarrow$ Rama B.
   - La variable `swap_ab` intercambia los taps de A y B si el selector arranca en la fase equivocada.
5. **`fir_filter_ccc` (`correlador_A`, `correlador_B`, filtros correladores coincidentes):**
   - Taps Rama A: `np.repeat(code_A, 6)[::-1]` (Código A invertido y sobremuestreado x6).
   - Taps Rama B: `np.repeat(code_B, 6)[::-1]` (Código B invertido y sobremuestreado x6).
   - El pico de un eco a altura $h'$ sale en la muestra $h'/\delta + h_{off}$, con $h_{off} = 16 \cdot spb - 1 = 95$ y $\delta = c/(2 f_{s,bb}) = 0.7495$ km.
6. **`add` (`suma_qcc`, Suma QCC):**
   - Suma A + B sobre I y Q por separado (flujo complejo) para cancelar los lóbulos laterales.
7. **`blocks_keep_m_in_n` (`ventana_alturas`):**
   - De cada disparo conserva $n_h = 3200$ muestras a partir de $h_{off}$: el perfil queda con $h' = i \cdot \delta$, de 0 a 2398 km.
8. **Integración de $K = 25$ parejas (variable `integracion`, elegida con `blocks_selector`):**
   - **Coherente (0, por defecto):** `stream_to_vector` (complejo, $n_h$) → `integrate_cc` → `complex_to_mag`. Promedia I/Q antes de $|Z|$: es la que da la ganancia de $10 \log_{10} K = 13.98$ dB (método de AIS-INGV y Cabrera et al.).
   - **Incoherente (1):** `complex_to_mag` → `stream_to_vector` (float, $n_h$) → `integrate_ff`. Promedia $|Z| = \sqrt{I_{sum}^2 + Q_{sum}^2}$: reduce la fluctuación del ruido pero no baja su piso.
   - Luego `multiply_const_vff` por $1/K$ (`promedio`).
9. **Salidas:**
   - Bloque Python `ionograma`: ubica cada perfil integrado en la columna de su frecuencia (según el tag `sondeo`) y detecta el eco más fuerte.
   - `zeromq_pub_sink`: publica los perfiles (float, vector de $n_h = 3200$) a `tcp://127.0.0.1:5555`. Habilitado en el receptor real, deshabilitado en la simulación.
10. **Interfaz gráfica:**
    - Un bloque *Python Snippet* (`main_after_init`) llama a `ionosonde_gui.instalar(self)`. La GUI (PyQt5 + pyqtgraph) lee los bloques Python `monitor_rx`, `monitor_correl` e `ionograma`, y cambia las variables con los `set_<variable>()` que genera GRC. Solo muestra los controles cuyas variables existen en el flowgraph.

### Reglas al modificar los flowgraphs
- **No usar `qtgui_vector_sink_f`:** en GNU Radio 3.10.12 lee el vector siguiente del buffer y la gráfica queda en blanco. Graficar desde `ionosonde_gui.py`.
- **No usar vectores de 3333 elementos:** en Windows cada buffer vectorial de ese tamaño ocupa 218 MB. Los vectores van después de `ventana_alturas`, con $n_h = 3200 = 2^7 \cdot 25$.
- **$K$ es fijo durante la ejecución:** `blocks_integrate_xx` no tiene setter. Los demás parámetros (barrido, alineación, integración, swap A/B, modelo ionosférico) se cambian en ejecución y se aplican al empezar la frecuencia siguiente.
- **El transmisor debe disparar cada $N_{vec} = 3333$ muestras exactas** (PRF real = $f_{s,bb}/N_{vec}$ = 60.006 Hz). Con un PRT de 16.667 ms exactos (3333.33 muestras), el eco se corre 1/3 de muestra por disparo.
- **Código de los bloques Python embebidos y textos del `.grc` en ASCII:** GRC en Windows puede fallar con acentos.

---

## 4. Tareas Solicitadas para Claude Code

Cuando ejecutes comandos en este repositorio, debes enfocarte en:

1. **Mantener los flowgraphs `.grc` (GNU Radio Companion 3.10+):**
   - `ionosonde_rx.grc` (receptor real) e `ionosonde_rx_sim.grc` (simulación) comparten la cadena de la sección 3: un cambio en el procesamiento va en los dos.
   - Usar bloques estándar de GNU Radio y bloques Python embebidos (`epy_block`) solo donde no haya uno estándar (simulador, programador del barrido, monitores, armado del ionograma).
   - Después de cada cambio, validar compilando con `grcc` (sección 5) y, de ser posible, ejecutar la simulación.
   - Las versiones anteriores de los `.grc` se guardan en `respaldo/`.
2. **Mantener la interfaz gráfica `ionosonde_gui.py`:**
   - Es la interfaz del ionograma y de todos los gráficos; la cargan los dos `.grc`. Tiene que seguir funcionando con y sin el bloque `simulador`.
   - Cada control nuevo se agrega solo si existe la variable (`set_<variable>`) en el flowgraph, con su unidad y un texto de ayuda.
3. **Pendientes del receptor real:**
   - Confirmar el firmware de la Red Pitaya: `sdr_transceiver_122_88.xpr` es el proyecto de Pavel Demin (red-pitaya-notes), que usa su propio servidor TCP y no `iiod`. De eso dependen la fuente (`iio_device_source`) y la sintonía.
   - Conectar el mensaje `freq` del `programador_barrido` a la sintonía del hardware (DDC de RX y frecuencia del TX).
   - Asegurar que el transmisor siga el mismo plan de barrido ($2K$ disparos por frecuencia) y dispare cada $N_{vec}$ muestras exactas.
4. **Desarrollar Scripts de Prueba (pendientes, todavía no existen):**
   - `test_loopback_sim.py`: script independiente en Python/NumPy que genere ecos sintéticos con ruido aditivo y valide la cancelación perfecta de lóbulos laterales mediante Golay, sin GNU Radio.
   - `ionogram_gui.py` (opcional): cliente externo por PyZMQ, solo si se necesita ver o grabar los ionogramas fuera de la ventana de GNU Radio. Recibe vectores `float32` de $n_h = 3200$ alturas desde `tcp://127.0.0.1:5555`. Hoy el `zeromq_pub_sink` publica sin tags (`pass_tags: False`): para saber la frecuencia de cada perfil habría que activar `pass_tags` o enviarla por otro medio.
5. **Verificación de Coherencia Matemática:**
   - Verificar que todos los filtros correladores usen el sobremuestreo correcto ($spb = 6$).
   - Asegurar que la conversión de tiempo $t$ ($\mu\text{s}$) a altura virtual $h'$ ($\text{km}$) utilice $h' = \frac{c \cdot t}{2}$; en el perfil, $h' = i \cdot \delta$ con $\delta = 0.7495$ km.
   - Comprobar en la simulación (modo *Frecuencia fija*, pestaña 3) que la ganancia de integración coherente medida se acerque a $10 \log_{10} K = 13.98$ dB.

---

## 5. Comandos Útiles de Compilación y Prueba

En Windows, GNU Radio 3.10.12 está instalado con RadioConda en `D:\Programas\RadioConda` (usar su `python.exe` y `Scripts\grcc.exe`). En Linux, `grcc` y `python3`. Los scripts generados necesitan `ionosonde_gui.py` en la misma carpeta.

```bash
# Abrir un flowgraph en GNU Radio Companion (F6 para ejecutarlo)
gnuradio-companion ionosonde_rx_sim.grc

# Compilar los flowgraphs a Python
grcc -o . ionosonde_rx_sim.grc
grcc -o . ionosonde_rx.grc

# Ejecutar la simulación (no necesita hardware)
python3 ionosonde_rx_sim.py

# Ejecutar el receptor real (Red Pitaya en 192.168.1.100)
python3 ionosonde_rx.py

# Validación matemática sin GNU Radio (pendiente de crear)
python3 test_loopback_sim.py
```

Las capturas guardadas desde la interfaz (botón *Guardar captura* o Ctrl+S) quedan en `capturas/`.
