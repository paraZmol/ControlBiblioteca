# Sistema de Control de Acceso - Biblioteca Central UNASAM

[INSERTAR IMAGEN: Banner del Proyecto / Logo Institucional]

> **Versión:** 3.0
> **Estado:** Producción
> **Arquitectura:** Cliente-Servidor (WPF + FastAPI + MySQL)

---

## 1. Encabezado y Contexto

### Descripción del Problema Original
Históricamente, el acceso a las computadoras de la Biblioteca Central y el Centro de Cómputo de la UNASAM se gestionaba de forma manual. Esto generaba múltiples cuellos de botella:
- **Tiempos de espera prolongados** para registrar el ingreso y salida de estudiantes.
- **Falta de métricas fiables** sobre el uso real de los equipos, tiempos promedio y motivos de uso.
- **Riesgo de uso no autorizado** por parte de personas ajenas a la institución o alumnos no matriculados.
- **Dificultad en la supervisión** para identificar equipos libres u ocupados en tiempo real.

### La Solución Implementada
Este sistema resuelve estos problemas mediante la **automatización total del acceso**. Cada computadora de la biblioteca opera como un **Kiosco de seguridad**. El equipo permanece bloqueado hasta que un estudiante autorizado ingresa su DNI. El sistema verifica instantáneamente los datos contra el padrón de alumnos matriculados y, de ser válido, desbloquea el terminal registrando la sesión.

Todo esto está supervisado por un panel de administración en tiempo real que brinda a los encargados visibilidad completa del estado del laboratorio.

### Metodologías Aplicadas
- **Levantamiento de Requerimientos:** Entrevistas iterativas con los stakeholders (administradores de la biblioteca, equipo de TI).
- **Desarrollo Iterativo e Incremental:** Entregas en fases (v1, v2 y v3) permitiendo ajustes tempranos.
- **Enfoque Centrado en la Seguridad:** Desarrollo orientado a evitar fugas de sesión (bloqueo de comandos del SO como `Alt+Tab`, `Ctrl+Alt+Supr` y `Tecla Windows`).

---

## 2. Arquitectura del Sistema y Tecnologías (Justificación Técnica)

La solución adopta una arquitectura cliente-servidor en la red local (LAN) diseñada para operar sin interrupciones y soportar alto volumen de transacciones concurrentes.

### Tecnologías Seleccionadas

- **Backend (API + WebSockets): FastAPI (Python 3.12)**
  *Justificación:* Elegido por su velocidad extrema (basado en Starlette y Pydantic) y su manejo nativo de asincronía (`asyncio`). Soporta eficientemente conexiones WebSocket persistentes para decenas de computadoras simultáneamente sin bloquear hilos.
- **Frontend Cliente (Terminales): C# y WPF (.NET 8)**
  *Justificación:* WPF provee integración profunda con la API Win32 de Windows, lo cual es crítico para implementar hooks de teclado de bajo nivel que bloqueen atajos de sistema (`Alt+F4`, `Ctrl+Esc`, etc.). Al ser un entorno 100% Windows, C# garantiza robustez, rendimiento y binarios autocontenidos.
- **Frontend Administrador: Vanilla JavaScript, HTML5 y CSS3**
  *Justificación:* Se diseñó sin frameworks (como React o Angular) para mantener extrema ligereza y facilitar el despliegue integrado dentro de los archivos estáticos de FastAPI. Usa arquitectura SPA (Single Page Application) e implementa un diseño *Glassmorphism* moderno y responsivo.
- **Base de Datos: MySQL 8.0+**
  *Justificación:* Garantiza integridad referencial compleja (claves foráneas entre alumnos, facultades, escuelas y sesiones). Provee un manejo robusto de transacciones para la concurrencia de sesiones.
- **Comunicación: REST API + WebSockets**
  *Justificación:* REST se utiliza para operaciones CRUD y carga masiva. WebSockets se emplean para mantener canales bidireccionales persistentes, permitiendo conocer en tiempo real el estado en línea/fuera de línea de los Kioscos y empujar eventos (como el "desbloqueo remoto") de manera instantánea.

[INSERTAR IMAGEN: Diagrama de Arquitectura de Red y Tecnologías]

---

## 3. Estructura de la Base de Datos

El diseño relacional está altamente normalizado para reducir la redundancia y optimizar las consultas del historial de acceso.

### Esquema y Tablas Principales

1. **`alumnos_maestro`**: Padrón central de estudiantes. Actúa como la fuente única de verdad para otorgar acceso.
2. **`facultades` y `escuelas`**: Catálogos relacionales de las dependencias académicas.
3. **`terminales`**: Inventario de las computadoras conectadas. Almacena IP, nombre de red, y su estado en tiempo real (`libre`, `ocupada`, `bloqueado`).
4. **`sesiones`**: Tabla transaccional que registra cada evento de acceso. Se enlaza al `alumno`, `terminal`, hora de inicio, hora de salida, y el `motivo` de uso.
5. **`catalogo_motivos`**: Catálogo administrable de las razones por las cuales un estudiante solicita un equipo (ej: Tareas, Tesis, Lectura).
6. **`usuarios`**: Cuentas del personal administrativo (Nivel 1 y Nivel 2) para el acceso al panel web.

### Lógica de "Upsert" y Sincronización Masiva
Dado que el padrón de alumnos se alimenta frecuentemente mediante archivos Excel (`.xlsx`), el sistema emplea un patrón **Upsert** (Insert or Update). 
Al procesar una fila, el sistema evalúa la clave primaria (`dni`). Si el DNI ya existe, se actualizan los datos volátiles (código universitario, escuela, facultad) para mantener la información al día sin generar duplicados. Si el DNI es nuevo, se inserta como un nuevo estudiante.

[INSERTAR IMAGEN: Diagrama Entidad-Relación (DER)]

---

## 4. Módulos del Sistema (A detalle)

El ecosistema se divide en 3 módulos interactuando en sinergia.

### 4.1 Módulo Cliente (Kiosco EXE)
Aplicación de escritorio instalada en cada computadora del centro de cómputo.
- **Flujo de Acceso:** El estudiante visualiza una pantalla a pantalla completa (TopMost). Ingresa su DNI y selecciona un motivo. El cliente envía un payload JSON vía WebSocket.
- **Validación Bidireccional:** El servidor verifica el DNI y responde. Si es denegado, el cliente muestra un modal flotante con un "Carrusel de Comunicados" informativos indicando los pasos a seguir.
- **Seguridad Extrema:** Mientras la sesión no está activa, el cliente utiliza un `GlobalKeyboardHook` para inutilizar combinaciones como `Ctrl+Shift+Esc`, y modifica temporalmente los registros del sistema operativo para desactivar el Administrador de Tareas.

[INSERTAR IMAGEN: Interfaz de Terminal de Ingreso]

### 4.2 Módulo Servidor (Backend API)
Cerebro de las operaciones y el ruteo de datos.
- **Gestor de Conexiones:** Mantiene un registro en memoria de todos los WebSockets activos.
- **Seguridad:** Utiliza Pydantic para sanear los datos de entrada y JWT (JSON Web Tokens) para proteger los endpoints administrativos.
- **Limpieza Automática:** Dispone de tareas en segundo plano (`asyncio tasks`) que detectan terminales desconectadas abruptamente (ej. cortes de luz) y cierran sus sesiones huérfanas automáticamente para mantener la consistencia de la base de datos.

### 4.3 Módulo Administrador (Frontend Web)
Centro de control visual para el personal.
- **Monitoreo en Tiempo Real:** Las tarjetas de terminales cambian de color instantáneamente vía eventos WebSocket.
- **Gestión de Niveles (Roles):** 
  - *Nivel 1 (Asistentes):* Solo monitoreo, estadísticas y búsqueda en el historial.
  - *Nivel 2 (Administradores):* Pueden realizar bloqueos/desbloqueos remotos, importar Excel, y limpiar la base de datos (doble factor de autenticación y hash SHA-256 local en JavaScript).
- **Reportes:** Exportación con un solo clic a formatos Excel y PDF listos para impresión.

[INSERTAR IMAGEN: Panel de Control del Administrador]

---

## 5. Despliegue y Configuración (Producción)

El sistema fue diseñado para desplegarse ágilmente en infraestructuras locales (On-Premise).

### Requisitos de Infraestructura
- **Servidor:** SO Windows (10/11 o Server 2019+), Python 3.12+, MySQL 8.0+.
- **Terminales Clientes:** Windows 10/11 x64.
- **Red:** Conexión LAN con IP estática asignada al servidor (puerto 8000 expuesto en el firewall).

### Arquitectura de Configuración Centralizada
El sistema elimina la configuración en código rígido. Toda la red se orquesta a través de un único archivo `config.json` en el backend, el cual rige:
- Credenciales de MySQL.
- Contraseñas cifradas maestras.
- IP y puerto del Host.

El cliente (C#) posee su propio `config.json` ligero donde únicamente se le instruye a qué IP conectarse. Esto facilita la escalabilidad y despliegue masivo (ej. clonación de discos).

### Scripts de Despliegue Automatizado
No se requiere conocimiento profundo de Python para montar el servidor gracias a los scripts `.bat`:
- `instalar_servidor.bat`: Levanta el entorno virtual (venv), instala las dependencias de `requirements.txt` de manera automatizada.
- `servidor_run.bat`: Ejecuta Uvicorn en modo producción, validando previamente que exista la base de datos y ejecutando migraciones SQLAlchemy automáticamente en tiempo de arranque.

---

## 6. Versiones y Mantenimiento

### Historial de Versiones

- **Versión 1:** Prototipo base de control de acceso y bloqueos de Windows.
- **Versión 2:** Integración del panel administrativo, roles de usuario, soporte para WebSockets y carga por Excel.
- **Versión 3 (Actual):** Consolidación visual Glassmorphism, terminales virtuales `IMPORTADO` para sanidad de datos, Carrusel interactivo para alumnos no matriculados, depuración de migraciones SQL (Upsert robusto) y optimización de reconexiones automáticas mediante backoff exponencial.

### Entornos de Desarrollo vs Producción

Para los futuros responsables del mantenimiento del código:
1. **Desarrollo (Local):** Modifica en el `config.json` el parámetro `"db_tipo": "sqlite"`. El servidor generará un archivo `.db` autónomo sin necesidad de arrancar el servicio de MySQL, ideal para pruebas de regresión.
2. **Cliente C#:** En el IDE (Visual Studio / Rider), el cliente levantará con una UI de consola adjunta (Debug log) para inspeccionar la trama de red.
3. **Paso a Producción:** 
   - Backend: Cambiar `"db_tipo": "mysql"`.
   - Cliente: Compilar usando el perfil de *Self-Contained* (PublishSingleFile):
     ```powershell
     dotnet publish client\ControlBiblioteca.Client.csproj -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true
     ```

---
*Documentación técnica elaborada para la Biblioteca Central de la Universidad Nacional Santiago Antúnez de Mayolo (UNASAM).*
