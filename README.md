<div align="center">

# 🏫 Sistema de Control de Acceso <br> Biblioteca Central UNASAM

[INSERTAR IMAGEN: Banner del Proyecto / Logo Institucional]

> **Versión:** 3.0 | **Estado:** 🚀 Producción | **Arquitectura:** Cliente-Servidor

[![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![C#](https://img.shields.io/badge/C%23-239120?style=for-the-badge&logo=c-sharp&logoColor=white)](https://learn.microsoft.com/en-us/dotnet/csharp/)
[![WPF](https://img.shields.io/badge/WPF-512BD4?style=for-the-badge&logo=.net&logoColor=white)](https://learn.microsoft.com/en-us/dotnet/desktop/wpf/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![JavaScript](https://img.shields.io/badge/JavaScript-F7DF1E?style=for-the-badge&logo=javascript&logoColor=black)](https://developer.mozilla.org/en-US/docs/Web/JavaScript)
[![MySQL](https://img.shields.io/badge/MySQL-4479A1?style=for-the-badge&logo=mysql&logoColor=white)](https://www.mysql.com/)

</div>

---

## 📖 1. Encabezado y Contexto

### 📉 Descripción del Problema Original
Históricamente, el acceso a las computadoras de la Biblioteca Central y el Centro de Cómputo de la UNASAM se gestionaba de forma manual. Esto generaba múltiples cuellos de botella:
- **Tiempos de espera prolongados** para registrar el ingreso y salida de estudiantes.
- **Falta de métricas fiables** sobre el uso real de los equipos, tiempos promedio y motivos de uso.
- **Riesgo de uso no autorizado** por parte de personas ajenas a la institución o alumnos no matriculados.
- **Dificultad en la supervisión** para identificar equipos libres u ocupados en tiempo real.

[INSERTAR IMAGEN: Fotografía del centro de cómputo o biblioteca antes de la implementación]

### 💡 La Solución Implementada
Este sistema resuelve estos problemas mediante la **automatización total del acceso**. Cada computadora de la biblioteca opera como un **Kiosco de seguridad**. El equipo permanece bloqueado hasta que un estudiante autorizado ingresa su DNI. El sistema verifica instantáneamente los datos contra el padrón de alumnos matriculados y, de ser válido, desbloquea el terminal registrando la sesión.

Todo esto está supervisado por un panel de administración en tiempo real que brinda a los encargados visibilidad completa del estado del laboratorio.

### 🛠 Metodologías Aplicadas
- **Levantamiento de Requerimientos:** Entrevistas iterativas con los stakeholders (administradores de la biblioteca, equipo de TI).
- **Desarrollo Iterativo e Incremental:** Entregas en fases (v1, v2 y v3) permitiendo ajustes tempranos.
- **Enfoque Centrado en la Seguridad:** Desarrollo orientado a evitar fugas de sesión (bloqueo de comandos del SO como `Alt+Tab`, `Ctrl+Alt+Supr` y `Tecla Windows`).

---

## 🏗️ 2. Arquitectura del Sistema y Tecnologías (Justificación Técnica)

La solución adopta una arquitectura cliente-servidor en la red local (LAN) diseñada para operar sin interrupciones y soportar alto volumen de transacciones concurrentes.

[INSERTAR IMAGEN: Diagrama General de la Arquitectura de Red y Tecnologías]

### 💻 Lenguajes y Tecnologías Seleccionadas

#### 🐍 Backend (Servidor): Python (FastAPI)
- **¿Por qué Python/FastAPI?** Elegido por su velocidad extrema de desarrollo y ejecución (gracias a Starlette y Pydantic). Su manejo nativo de asincronía (`asyncio`) es perfecto para mantener conexiones WebSocket persistentes sin agotar los recursos del servidor. Esto permite monitorear docenas de terminales simultáneamente en tiempo real.

#### 🪟 Frontend Cliente (Terminal Kiosco): C# con WPF (.NET 8)
- **¿Por qué C# y WPF?** Las computadoras del centro operan bajo Windows. WPF provee una integración profunda con la API Win32 del sistema operativo, lo cual es crítico para implementar "hooks" de teclado a bajo nivel (`GlobalKeyboardHook`) que anulen comandos de escape (`Alt+F4`, `Ctrl+Esc`, etc.). Además, genera binarios autocontenidos ultrarrápidos y seguros.

#### 🌐 Frontend Administrador (Panel Web): Vanilla JavaScript, HTML5 y CSS3
- **¿Por qué sin frameworks?** Se optó por Vanilla JS (ES2022) para mantener una interfaz extremadamente ligera y libre de dependencias pesadas. El panel se sirve de forma estática directamente desde el backend FastAPI. Emplea WebSockets nativos y fetch API con un diseño *Glassmorphism* altamente responsivo.

#### 🐬 Base de Datos: MySQL 8.0+
- **¿Por qué MySQL?** Por la necesidad de mantener una estricta **integridad relacional** entre estudiantes, sesiones, terminales, facultades y escuelas. Soporta gran concurrencia y un manejo impecable de operaciones masivas de actualización e inserción de datos históricos sin riesgo de corrupción.

[INSERTAR IMAGEN: Diagrama de Tecnologías y Flujo de Datos (WebSockets vs REST)]

---

## 🗄️ 3. Estructura de la Base de Datos

El diseño relacional está altamente normalizado para reducir la redundancia y optimizar las consultas del historial de acceso.

[INSERTAR IMAGEN: Diagrama Entidad-Relación (DER) de la Base de Datos]

### 📊 Tablas Principales

1. **`alumnos_maestro`**: Padrón central de estudiantes. Actúa como la fuente única de verdad para otorgar acceso.
2. **`facultades` y `escuelas`**: Catálogos relacionales de las dependencias académicas.
3. **`terminales`**: Inventario de las computadoras conectadas. Almacena IP, nombre de red, y su estado en tiempo real (`libre`, `ocupada`, `bloqueado`).
4. **`sesiones`**: Tabla transaccional que registra cada evento de acceso. Se enlaza al `alumno`, `terminal`, hora de inicio, hora de salida, y el `motivo` de uso.
5. **`catalogo_motivos`**: Catálogo administrable de las razones por las cuales un estudiante solicita un equipo (ej: Tareas, Tesis, Lectura).
6. **`usuarios`**: Cuentas del personal administrativo (Nivel 1 y Nivel 2) para el acceso al panel web.

### 🔄 Lógica de "Upsert" y Sincronización Masiva
Dado que el padrón de alumnos se alimenta frecuentemente mediante archivos Excel (`.xlsx`), el sistema emplea un patrón **Upsert** (Insert or Update). 
Al procesar una fila, el sistema evalúa la clave primaria (`dni`). Si el DNI ya existe, se actualizan los datos volátiles (código universitario, escuela, facultad) para mantener la información al día sin generar duplicados. Si el DNI es nuevo, se inserta como un nuevo estudiante.

[INSERTAR IMAGEN: Captura del proceso de Importación de Excel y barra de progreso]

---

## 🧩 4. Módulos del Sistema (A detalle)

El ecosistema se divide en 3 módulos interactuando en sinergia continua.

### 🖥️ 4.1 Módulo Cliente (Kiosco EXE)
Aplicación de escritorio instalada en cada computadora del centro de cómputo.
- **Flujo de Acceso:** El estudiante visualiza una pantalla a pantalla completa (TopMost). Ingresa su DNI y selecciona un motivo. El cliente envía un payload JSON vía WebSocket.
- **Validación Bidireccional:** El servidor verifica el DNI y responde. Si es denegado, el cliente muestra un modal flotante interactivo con un "Carrusel de Comunicados" indicando los pasos a seguir.
- **Seguridad Extrema:** Mientras la sesión no está activa, el cliente utiliza un `GlobalKeyboardHook` para inutilizar combinaciones como `Ctrl+Shift+Esc`, y modifica temporalmente los registros del sistema operativo para desactivar el Administrador de Tareas.

[INSERTAR IMAGEN: Pantalla principal de Ingreso del Kiosco]
[INSERTAR IMAGEN: Captura del Kiosco mostrando el Carrusel de Comunicados de error]

### ⚙️ 4.2 Módulo Servidor (Backend API)
Cerebro de las operaciones y el ruteo de datos en tiempo real.
- **Gestor de Conexiones:** Mantiene un registro en memoria de todos los WebSockets activos. Detecta caídas de red y retransmite datos a los paneles administrativos.
- **Seguridad:** Utiliza Pydantic para sanear los datos de entrada y JWT (JSON Web Tokens) para proteger los endpoints administrativos.
- **Limpieza Automática:** Tareas asíncronas (`asyncio`) detectan terminales desconectadas abruptamente (ej. cortes de luz) y "cierran" sus sesiones huérfanas de forma segura, garantizando métricas exactas.

[INSERTAR IMAGEN: Log del servidor en consola demostrando el Heartbeat y las conexiones WS]

### 🌐 4.3 Módulo Administrador (Frontend Web)
Centro de control visual responsivo para el personal.
- **Monitoreo en Tiempo Real:** Las tarjetas de terminales cambian de color instantáneamente (verde/rojo) vía eventos WebSocket cuando un estudiante inicia o termina sesión.
- **Gestión de Niveles (Roles de Seguridad):** 
  - *Nivel 1 (Asistentes):* Solo monitoreo, vista de métricas y búsqueda en el historial.
  - *Nivel 2 (Administradores):* Con verificación de PIN cifrado en SHA-256 localmente, acceden a bloqueos/desbloqueos remotos, importación de Excel, edición de catálogo, y purga de base de datos.
- **Reportes y Gestión de Datos:** Exportación con un clic a CSV/Excel y PDF listos para impresión. Creación y edición manual de alumnos.

[INSERTAR IMAGEN: Panel de Control Web - Vista de Monitoreo de Terminales en vivo]
[INSERTAR IMAGEN: Panel de Control Web - Vista de Base de Datos y Estadísticas]

---

## 🚀 5. Despliegue y Configuración (Producción)

El sistema fue diseñado para desplegarse ágilmente en infraestructuras locales (On-Premise) sin necesidad de internet externo.

### 📋 Requisitos de Infraestructura
- **Servidor Central:** SO Windows (10/11 o Server 2019+), Python 3.12+, MySQL 8.0+.
- **Terminales Clientes:** Windows 10/11 x64.
- **Red:** LAN interna con IP estática asignada al servidor (puerto `8000` expuesto en firewall).

### 🔧 Arquitectura de Configuración Centralizada
El sistema elimina la mala práctica del "código rígido" (hardcoding). Toda la red se orquesta a través de un único archivo de configuración (`config.json` en el backend), el cual rige:
- Credenciales seguras de MySQL.
- Hashes de contraseñas de Nivel 2.
- Parámetros del servidor.

El cliente (C#) posee un archivo `config.json` propio ultraligero donde únicamente se le indica la IP del WebSocket. Esto simplifica el clonado masivo de discos para 50+ computadoras.

### 📜 Automatización con Scripts `.bat`
Se han creado rutinas preconfiguradas para levantar el servicio sin tipear comandos:
- `instalar_servidor.bat`: Levanta el entorno virtual (venv) e instala librerías.
- `servidor_run.bat`: Arranca el servidor `Uvicorn` en producción, asegurando que la base de datos se inicialice y se corran migraciones automáticamente.

[INSERTAR IMAGEN: Captura del entorno de producción ejecutándose en la PC Servidor]

---

## 🔄 6. Versiones y Mantenimiento

### 📌 Historial de Versiones

- **Versión 1:** Prototipo base de control de acceso local y bloqueos nativos de Windows.
- **Versión 2:** Integración del panel administrativo web, roles asimétricos, WebSockets puros y carga masiva por Excel.
- **Versión 3 (Actual):** Consolidación de la interfaz *Glassmorphism*, creación de terminal virtual `IMPORTADO` para sanidad del historial histórico, **Carrusel interactivo** para control de errores y alumnos no matriculados, depuración de migraciones SQL (Upsert ultra-robusto), e implementación de reconexiones automáticas de clientes mediante *Backoff Exponencial*.

### 🛠️ Entornos de Desarrollo vs Producción

Para los futuros responsables de TI:
1. **Desarrollo (Modo Aislado):** Modifica en el `config.json` el parámetro `"db_tipo": "sqlite"`. El servidor generará un archivo `.db` en memoria secundaria para pruebas de regresión inmediatas sin usar MySQL.
2. **Cliente Kiosco (Debug):** Ejecutando desde Visual Studio o Rider, el cliente muestra una UI de consola pequeña adjunta para inspeccionar la salud del socket y la latencia.
3. **Paso a Producción Final:** 
   - Backend: Ajustar a `"db_tipo": "mysql"`.
   - Cliente: Compilar obligatoriamente usando el perfil de *Self-Contained* (PublishSingleFile) para no requerir `.NET Runtime` en las PCs de los alumnos:
     ```powershell
     dotnet publish client\ControlBiblioteca.Client.csproj -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true
     ```

[INSERTAR IMAGEN: Estructura de carpetas recomendada para despliegue On-Premise]

---

<div align="center">
  <i>Desarrollado para transformar la administración y seguridad de la Biblioteca Central.</i>
</div>
