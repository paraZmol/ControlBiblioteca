using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Management;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace ControlBiblioteca.Client.Services
{
    /// <summary>
    /// Monitorea actividad del alumno usando lista blanca:
    /// solo registra procesos y archivos que realmente importan.
    /// Todo lo demás se ignora silenciosamente.
    /// </summary>
    public class ActivityMonitor : IDisposable
    {
        private readonly WebSocketService _ws;
        private FileSystemWatcher?        _downloadsWatcher;
        private CancellationTokenSource   _cts = new();
        private bool _activo = false;

        // Log diagnóstico a archivo (junto al exe), para depurar en campo la
        // captura de aperturas/cierres/reaperturas sin necesidad de un debugger.
        private static readonly string _logDiagPath = Path.Combine(
            AppContext.BaseDirectory, "actividad_diag.log");
        private static readonly object _logLock = new();
        private static void LogDiag(string msg)
        {
            try
            {
                lock (_logLock)
                    File.AppendAllText(_logDiagPath,
                        $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} | {msg}{Environment.NewLine}");
            }
            catch { /* el log nunca debe romper el monitoreo */ }
        }

        // Intervalo de sondeo de procesos. Usamos polling en vez de
        // Win32_ProcessStartTrace porque esa clase WMI exige privilegios de
        // administrador, y el cliente corre como asInvoker (usuario normal).
        private const int POLL_MS = 1500;

        // ── Sondeo de VENTANA EN FOCO (programa que el alumno mira AHORA) ──
        // Distinto del monitoreo de procesos: aquel detecta APERTURAS (para
        // sospechas/evidencia); esto detecta QUÉ está usando en este momento,
        // para pintarlo en vivo en la tarjeta de la PC del panel. Es estado
        // efímero: no se guarda en la base, no genera sospechas.
        private const int FOCO_MS = 30000;               // cada 30 s
        // Último exe en foco REPORTADO. Solo enviamos si cambió, para no
        // inundar el WS: un alumno 10 min en Word = un solo envío.
        private string _ultimoFocoReportado = "";

        [System.Runtime.InteropServices.DllImport("user32.dll")]
        private static extern IntPtr GetForegroundWindow();
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);

        // Conjunto de PIDs vistos en el sondeo anterior. Un proceso es "nuevo"
        // si su PID no estaba aquí. Se reemplaza completo en cada vuelta.
        private HashSet<int> _pidsVistos = new();

        // Apps NORMALES (navegadores, académicos) ya reportadas en esta sesión.
        // Word/Chrome lanzan varios procesos con el mismo nombre; sin esto la
        // base se llenaría de filas idénticas. Solo registramos la PRIMERA vez
        // que aparece cada app por sesión. Se vacía en Iniciar()/Detener().
        // Los SOSPECHOSOS (cmd, powershell...) NO usan esto: cada apertura cuenta.
        private readonly HashSet<string> _appsReportadas = new(StringComparer.OrdinalIgnoreCase);

        // Archivos de Downloads ya reportados en esta sesión. Evita duplicar
        // cuando el navegador crea un temporal (.crdownload) y luego lo renombra
        // al nombre final — ambos disparan el evento Created. Se vacía con la sesión.
        private readonly HashSet<string> _descargasReportadas = new(StringComparer.OrdinalIgnoreCase);

        // Extensiones temporales de descarga en progreso — se ignoran; solo nos
        // interesa el archivo final ya renombrado.
        private static readonly HashSet<string> _extTemporales = new(StringComparer.OrdinalIgnoreCase)
        {
            ".crdownload", ".part", ".download", ".tmp", ".partial", ".opdownload",
        };

        // Navegadores conocidos para etiquetar el origen de la descarga.
        private static readonly Dictionary<string, string> _navegadorNombre = new(StringComparer.OrdinalIgnoreCase)
        {
            { "chrome.exe", "Chrome" }, { "firefox.exe", "Firefox" },
            { "msedge.exe", "Edge" },   { "opera.exe", "Opera" },
            { "brave.exe", "Brave" },   { "iexplore.exe", "Internet Explorer" },
        };

        // ── ENFOQUE LISTA NEGRA: se registra TODO lo que abra el alumno ──
        // excepto el ruido del sistema operativo (_procesosIgnorados, abajo).
        //
        // Navegadores: solo para etiquetar descargas (ver _navegadorNombre).

        // Herramientas peligrosas — se marcan como nivel "sospechoso".
        // Esta es la lista BASE compilada; el servidor puede ampliarla en
        // caliente vía el mensaje 'config_sospechosos' (tabla en MySQL).
        // Se compara por el nombre REAL del ejecutable de Windows (ej. Word es
        // WINWORD.EXE), no por el nombre comercial.
        private static readonly HashSet<string> _sospechososBase = new(StringComparer.OrdinalIgnoreCase)
        {
            "cmd.exe", "powershell.exe", "pwsh.exe", "powershell_ise.exe",
            "regedit.exe", "taskmgr.exe", "msconfig.exe",
            "wscript.exe", "cscript.exe", "regsvr32.exe", "mshta.exe", "rundll32.exe",
            "net.exe", "net1.exe", "netstat.exe", "nbtstat.exe", "arp.exe",
            "diskpart.exe", "format.exe", "taskkill.exe", "sc.exe", "reg.exe",
            "bcdedit.exe", "vssadmin.exe", "wmic.exe", "psexec.exe", "ftp.exe",
            "telnet.exe", "ncat.exe", "nc.exe", "nmap.exe", "putty.exe",
            "gpedit.msc", "secpol.msc", "lusrmgr.msc", "compmgmt.msc",
        };

        // Lista efectiva de sospechosos: base + lo que añada el servidor.
        // Se inicializa con la base y se reemplaza al recibir config del server.
        private static HashSet<string> _sospechosos = new(_sospechososBase, StringComparer.OrdinalIgnoreCase);

        // ── ARCHIVOS: extensiones que se registran en Downloads ───────

        // Sospechosas — ejecutables e instaladores
        private static readonly HashSet<string> _extSospechosas = new(StringComparer.OrdinalIgnoreCase)
        {
            ".exe", ".msi", ".bat", ".cmd", ".ps1",
            ".vbs", ".js", ".jar", ".reg",
        };

        // Normales — documentos y archivos académicos
        private static readonly HashSet<string> _extNormales = new(StringComparer.OrdinalIgnoreCase)
        {
            ".pdf", ".docx", ".doc", ".xlsx", ".xls",
            ".pptx", ".ppt", ".txt", ".csv",
            ".zip", ".rar", ".7z",
            ".jpg", ".png", ".mp4", ".mp3",
        };

        public ActivityMonitor(WebSocketService ws)
        {
            _ws = ws;
        }

        public void Iniciar()
        {
            if (_activo) return;
            _activo = true;
            _cts    = new CancellationTokenSource();
            _appsReportadas.Clear();      // sesión nueva → lista de apps limpia
            _descargasReportadas.Clear(); // y de descargas
            _ultimoFocoReportado = "";    // sesión nueva → sin foco previo
            IniciarWatcherProcesos();
            IniciarWatcherDescargas();
            _ = Task.Run(() => LoopFocoAsync(_cts.Token));
        }

        public void Detener()
        {
            _activo = false;
            _cts.Cancel();
            _downloadsWatcher?.Dispose();
            _downloadsWatcher = null;
            _pidsVistos = new();
            _appsReportadas.Clear();
            _descargasReportadas.Clear();
            _ultimoFocoReportado = "";
        }

        // ── Monitoreo de procesos via polling (sin admin) ─────────────

        private void IniciarWatcherProcesos()
        {
            // Snapshot inicial: marcar todo lo que YA está corriendo como visto,
            // para no reportar procesos preexistentes como "recién abiertos".
            try
            {
                _pidsVistos = new HashSet<int>(Process.GetProcesses().Select(p => p.Id));
            }
            catch (Exception ex)
            {
                Debug.WriteLine($"[ActivityMonitor] Snapshot inicial falló: {ex.Message}");
                _pidsVistos = new HashSet<int>();
            }

            _ = Task.Run(() => LoopProcesosAsync(_cts.Token));
        }

        private async Task LoopProcesosAsync(CancellationToken token)
        {
            while (_activo && !token.IsCancellationRequested)
            {
                try { await Task.Delay(POLL_MS, token); }
                catch (OperationCanceledException) { break; }

                if (!_activo || token.IsCancellationRequested) break;

                try
                {
                    var procesosActuales = Process.GetProcesses();
                    var nuevosPids = new HashSet<int>(procesosActuales.Length);
                    // Nombres de exe con AL MENOS una instancia viva en esta vuelta.
                    // Sirve para purgar de _appsReportadas las apps que el alumno
                    // cerró, y así poder volver a registrarlas si las reabre.
                    var nombresVivos = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

                    foreach (var p in procesosActuales)
                    {
                        int pid;
                        string nombre;
                        try
                        {
                            pid    = p.Id;
                            nombre = p.ProcessName + ".exe"; // ProcessName no incluye extensión
                        }
                        catch { continue; }
                        finally { p.Dispose(); }

                        nuevosPids.Add(pid);
                        nombresVivos.Add(nombre);

                        // Solo procesar los que NO estaban en el sondeo anterior.
                        if (_pidsVistos.Contains(pid)) continue;

                        EvaluarProceso(nombre, pid);
                    }

                    // Purga: una app reportada que ya no tiene NINGÚN proceso vivo
                    // fue cerrada. La quitamos del registro para que, si el alumno
                    // la reabre, vuelva a contar como una apertura nueva. Esto
                    // distingue "Chrome abrió 5 subprocesos de una" (un solo evento
                    // mientras siga vivo) de "abrió y cerró Excel 5 veces".
                    var cerradas = _appsReportadas.Where(n => !nombresVivos.Contains(n)).ToList();
                    foreach (var cerrada in cerradas)
                    {
                        _appsReportadas.Remove(cerrada);
                        LogDiag($"PURGA (cerrada, ya re-registrable): {cerrada}");
                    }

                    _pidsVistos = nuevosPids;
                }
                catch (Exception ex)
                {
                    Debug.WriteLine($"[ActivityMonitor] Loop procesos error: {ex.Message}");
                }
            }
        }

        private void EvaluarProceso(string nombre, int pid)
        {
            if (string.IsNullOrWhiteSpace(nombre)) return;

            // 1. Ignorar el ruido del sistema operativo (svchost, dwm, etc.).
            //    Todo lo demás SÍ se registra — es actividad real del alumno.
            if (_procesosIgnorados.Contains(nombre)) return;

            // 2. ¿Es una herramienta peligrosa? → nivel sospechoso.
            //    NO filtramos por proceso padre: si el alumno abre cmd desde el
            //    explorador, su padre es explorer.exe pero IGUAL nos interesa.
            //    Los sospechosos NO se deduplican: cada apertura cuenta.
            if (_sospechosos.Contains(nombre))
            {
                string amigableS = NombreAmigable(pid, nombre);
                _ = EnviarEventoAsync(
                    "comando",
                    $"Herramienta del sistema: {amigableS}",
                    ObtenerCommandLine(pid),
                    "sospechoso",
                    nombre);
                return;
            }

            // 3. Cualquier otro programa de usuario → nivel normal.
            //    Dedup por instancia viva: una app abierta = un evento mientras
            //    siga corriendo. Al cerrarse se purga (ver LoopProcesosAsync) y
            //    reabrirla vuelve a contar.
            if (!_appsReportadas.Add(nombre))
            {
                LogDiag($"YA REPORTADA (sigue viva, no re-registra): {nombre} pid={pid}");
                return;
            }

            string amigable = NombreAmigable(pid, nombre);
            LogDiag($"REGISTRA apertura: {nombre} ({amigable}) pid={pid}");
            _ = EnviarEventoAsync(
                "proceso",
                $"Abrió: {amigable}",
                "",
                "normal",
                nombre);
        }

        // Devuelve el nombre comercial del programa (ej. "Microsoft Word") leído
        // de los metadatos del .exe, con el nombre interno entre paréntesis para
        // referencia (ej. "Microsoft Word (WINWORD.EXE)"). Si no hay metadatos,
        // devuelve solo el nombre del ejecutable.
        private static string NombreAmigable(int pid, string nombreExe)
        {
            try
            {
                using var p = Process.GetProcessById(pid);
                string? ruta = p.MainModule?.FileName;
                if (!string.IsNullOrEmpty(ruta))
                {
                    var info = System.Diagnostics.FileVersionInfo.GetVersionInfo(ruta);
                    string desc = (info.FileDescription ?? "").Trim();
                    if (desc.Length > 0 && !desc.Equals(nombreExe, StringComparison.OrdinalIgnoreCase))
                        return $"{desc} ({nombreExe})";
                }
            }
            catch { /* MainModule puede fallar sin permisos — usar solo el exe */ }
            return nombreExe;
        }

        // Intenta leer la línea de comando vía WMI (consulta Win32_Process,
        // que SÍ funciona sin admin, a diferencia de Win32_ProcessStartTrace).
        private static string ObtenerCommandLine(int pid)
        {
            try
            {
                using var searcher = new ManagementObjectSearcher(
                    $"SELECT CommandLine FROM Win32_Process WHERE ProcessId = {pid}"
                );
                foreach (ManagementObject obj in searcher.Get())
                {
                    string cmd = obj["CommandLine"]?.ToString() ?? "";
                    if (cmd.Length > 0)
                        return cmd[..Math.Min(cmd.Length, 300)];
                }
            }
            catch { }
            return "";
        }

        // ── LISTA NEGRA: ruido del SO que NO se registra ──────────────
        // Procesos internos de Windows que arrancan solos y no representan
        // actividad del alumno. TODO lo que no esté aquí (ni en sospechosos)
        // se registra como app normal usada por el alumno.
        private static readonly HashSet<string> _procesosIgnorados = new(StringComparer.OrdinalIgnoreCase)
        {
            "svchost.exe", "lsass.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
            "services.exe", "smss.exe", "dwm.exe", "explorer.exe", "taskhostw.exe",
            "RuntimeBroker.exe", "SearchIndexer.exe", "audiodg.exe", "conhost.exe",
            "fontdrvhost.exe", "spoolsv.exe", "WmiPrvSE.exe", "dllhost.exe",
            "ctfmon.exe", "sihost.exe", "ShellExperienceHost.exe",
            "SearchHost.exe", "SearchApp.exe", "StartMenuExperienceHost.exe",
            "SystemSettings.exe", "TextInputHost.exe", "LockApp.exe",
            "MsMpEng.exe", "NisSrv.exe", "SecurityHealthService.exe",
            "SecurityHealthSystray.exe", "smartscreen.exe",
            "TiWorker.exe", "TrustedInstaller.exe", "wuauclt.exe", "usocoreworker.exe",
            "backgroundTaskHost.exe", "ApplicationFrameHost.exe", "WidgetService.exe",
            "Widgets.exe", "PhoneExperienceHost.exe", "GameBar.exe", "GameBarFTServer.exe",
            "SgrmBroker.exe", "spoolsv.exe", "dasHost.exe", "WUDFHost.exe",
            "wlanext.exe", "unsecapp.exe", "taskhost.exe", "wmpnetwk.exe",
            "OfficeClickToRun.exe", "SDXHelper.exe", "MsoSync.exe", "msosync.exe",
            "splwow64.exe", "OfficeC2RClient.exe", "AppVShNotify.exe",
            "MicrosoftEdgeUpdate.exe", "msedgewebview2.exe",
            // Ruido adicional observado en campo: actualizaciones, activación,
            // notificaciones, indexado y updaters de drivers — no son acciones
            // del alumno.
            "MoUsoCoreWorker.exe", "MoNotificationUx.exe", "sppsvc.exe",
            "SearchProtocolHost.exe", "SearchFilterHost.exe",
            "NvProfileUpdater64.exe", "NvProfileUpdater.exe", "nvcontainer.exe",
            "nvsphelper64.exe", "NVDisplay.Container.exe",
            "FileCoAuth.exe", "UserOOBEBroker.exe", "OneDrive.exe",
            "OneDriveStandaloneUpdater.exe", "WidgetBoard.exe",
            "CompPkgSrv.exe", "SystemSettingsBroker.exe", "ShellHost.exe",
            "uhssvc.exe", "wermgr.exe", "WerFault.exe", "mscorsvw.exe",
            "ngentask.exe", "ngen.exe", "AggregatorHost.exe", "CallButler.exe",
            "ControlBiblioteca.Client.exe",
        };

        // ── Monitoreo de archivos en Downloads ────────────────────────

        private void IniciarWatcherDescargas()
        {
            try
            {
                string downloads = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                    "Downloads"
                );
                if (!Directory.Exists(downloads)) return;

                _downloadsWatcher = new FileSystemWatcher(downloads)
                {
                    NotifyFilter          = NotifyFilters.FileName,
                    IncludeSubdirectories = false,
                    EnableRaisingEvents   = true,
                };
                _downloadsWatcher.Created += OnArchivoCreado;
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[ActivityMonitor] FileSystemWatcher error: {ex.Message}");
            }
        }

        private void OnArchivoCreado(object sender, FileSystemEventArgs e)
        {
            if (!_activo || _cts.Token.IsCancellationRequested) return;

            try
            {
                string nombre = e.Name ?? "";
                string ext    = Path.GetExtension(nombre).ToLowerInvariant();
                if (string.IsNullOrWhiteSpace(ext)) return;

                // Ignorar archivos temporales de descarga en progreso. El final
                // ya renombrado disparará su propio evento Created.
                if (_extTemporales.Contains(ext)) return;

                // Dedup por nombre de archivo: evita registrar el mismo dos veces
                // (renombrados, reescrituras del navegador).
                if (!_descargasReportadas.Add(nombre)) return;

                string nivel;
                string accion;

                if (_extSospechosas.Contains(ext))
                {
                    nivel  = "sospechoso";
                    accion = "Descargó ejecutable";
                }
                else if (_extNormales.Contains(ext))
                {
                    nivel  = "normal";
                    accion = "Descargó archivo";
                }
                else
                {
                    return; // extensión no relevante — ignorar
                }

                // Etiquetar el navegador de origen si hay uno corriendo.
                string origen = NavegadorActivo();
                string desc   = origen.Length > 0
                    ? $"{accion} desde {origen}: {nombre}"
                    : $"{accion}: {nombre}";

                _ = EnviarEventoAsync("archivo", desc, $"Downloads\\{nombre}", nivel);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[ActivityMonitor] Error en archivo: {ex.Message}");
            }
        }

        // Devuelve el nombre amigable del navegador en ejecución (Chrome, Firefox...),
        // o "" si no hay ninguno. Sirve para etiquetar el origen de una descarga.
        private static string NavegadorActivo()
        {
            try
            {
                foreach (var p in Process.GetProcesses())
                {
                    string nombre = p.ProcessName + ".exe";
                    p.Dispose();
                    if (_navegadorNombre.TryGetValue(nombre, out var amigable))
                        return amigable;
                }
            }
            catch { }
            return "";
        }

        // ── Sondeo de ventana en foco ─────────────────────────────────

        // Nombre del .exe de la ventana que está en primer plano, o "" si no
        // se puede determinar. No lee el TÍTULO de la ventana a propósito:
        // el título puede exponer datos privados del alumno (nombres, búsquedas).
        // Solo el nombre del programa sale de la PC.
        private static string ExeEnFoco()
        {
            try
            {
                IntPtr hwnd = GetForegroundWindow();
                if (hwnd == IntPtr.Zero) return "";
                GetWindowThreadProcessId(hwnd, out uint pid);
                if (pid == 0) return "";
                using var p = Process.GetProcessById((int)pid);
                return p.ProcessName + ".exe";   // ProcessName no incluye extensión
            }
            catch { return ""; }
        }

        private async Task LoopFocoAsync(CancellationToken token)
        {
            while (_activo && !token.IsCancellationRequested)
            {
                try { await Task.Delay(FOCO_MS, token); }
                catch (OperationCanceledException) { break; }
                if (!_activo || token.IsCancellationRequested) break;

                try
                {
                    string exe = ExeEnFoco();
                    if (string.IsNullOrEmpty(exe)) continue;
                    // Solo enviamos si el foco CAMBIÓ desde el último reporte.
                    if (string.Equals(exe, _ultimoFocoReportado, StringComparison.OrdinalIgnoreCase))
                        continue;
                    _ultimoFocoReportado = exe;
                    await EnviarFocoAsync(exe);
                }
                catch (Exception ex)
                {
                    Debug.WriteLine($"[ActivityMonitor] Error en foco: {ex.Message}");
                }
            }
        }

        // Evento de foco: tipo propio "foco", separado de "actividad" para que
        // el servidor no lo trate como evento a registrar ni a evaluar por banco
        // de sospechas. Es solo estado en vivo del mapa de terminales.
        private async Task EnviarFocoAsync(string procesoExe)
        {
            try
            {
                if (!_ws.EstaConectado) return;
                await _ws.EnviarAsync(JsonSerializer.Serialize(new
                {
                    tipo        = "foco",
                    proceso_exe = procesoExe,
                }));
            }
            catch (Exception ex)
            {
                Debug.WriteLine($"[ActivityMonitor] Error enviando foco: {ex.Message}");
            }
        }

        // ── Envío al servidor ─────────────────────────────────────────

        private async Task EnviarEventoAsync(string evento, string descripcion, string detalle, string nivel, string procesoExe = "")
        {
            try
            {
                if (!_ws.EstaConectado) return;
                await _ws.EnviarAsync(JsonSerializer.Serialize(new
                {
                    tipo = "actividad",
                    evento,
                    descripcion,
                    detalle,
                    nivel,
                    proceso_exe = procesoExe,   // nombre del .exe para filtrado server-side
                }));
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[ActivityMonitor] Error enviando: {ex.Message}");
            }
        }

        public void Dispose()
        {
            Detener();
            _cts.Dispose();
        }
    }
}
