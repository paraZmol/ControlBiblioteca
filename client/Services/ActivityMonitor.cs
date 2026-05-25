using System;
using System.Collections.Generic;
using System.IO;
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
        private ManagementEventWatcher?   _processWatcher;
        private FileSystemWatcher?        _downloadsWatcher;
        private CancellationTokenSource   _cts = new();
        private bool _activo = false;

        // ── LISTA BLANCA: solo estos procesos se registran ────────────

        // Navegadores — nivel normal
        private static readonly HashSet<string> _navegadores = new(StringComparer.OrdinalIgnoreCase)
        {
            "chrome.exe", "firefox.exe", "msedge.exe",
            "opera.exe", "brave.exe", "iexplore.exe",
        };

        // Herramientas del sistema — nivel sospechoso
        private static readonly HashSet<string> _sospechosos = new(StringComparer.OrdinalIgnoreCase)
        {
            "cmd.exe", "powershell.exe", "pwsh.exe",
            "regedit.exe", "taskmgr.exe", "msiexec.exe",
            "wscript.exe", "cscript.exe", "regsvr32.exe",
            "net.exe", "net1.exe", "netstat.exe",
            "diskpart.exe", "format.exe", "taskkill.exe",
        };

        // Office y herramientas académicas — nivel normal
        private static readonly HashSet<string> _academicos = new(StringComparer.OrdinalIgnoreCase)
        {
            "WINWORD.EXE", "EXCEL.EXE", "POWERPNT.EXE", "ONENOTE.EXE",
            "OUTLOOK.EXE", "MSPUB.EXE", "VISIO.EXE",
            "notepad.exe", "notepad++.exe",
            "AcroRd32.exe", "Acrobat.exe", "FoxitReader.exe",
            "vlc.exe", "wmplayer.exe",
        };

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
            IniciarWatcherProcesos();
            IniciarWatcherDescargas();
        }

        public void Detener()
        {
            _activo = false;
            _cts.Cancel();
            _processWatcher?.Stop();
            _processWatcher?.Dispose();
            _processWatcher = null;
            _downloadsWatcher?.Dispose();
            _downloadsWatcher = null;
        }

        // ── Monitoreo de procesos via WMI ─────────────────────────────

        private void IniciarWatcherProcesos()
        {
            try
            {
                _processWatcher = new ManagementEventWatcher(
                    new WqlEventQuery("SELECT * FROM Win32_ProcessStartTrace")
                );
                _processWatcher.EventArrived += OnProcesoIniciado;
                _processWatcher.Start();
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[ActivityMonitor] WMI no disponible: {ex.Message}");
            }
        }

        // Procesos del sistema que pueden lanzar hijos sospechosos legítimamente
        private static readonly HashSet<string> _procesosIgnorados = new(StringComparer.OrdinalIgnoreCase)
        {
            "svchost.exe", "lsass.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
            "services.exe", "smss.exe", "dwm.exe", "explorer.exe", "taskhostw.exe",
            "RuntimeBroker.exe", "SearchIndexer.exe", "audiodg.exe", "conhost.exe",
            "fontdrvhost.exe", "spoolsv.exe", "WmiPrvSE.exe", "dllhost.exe",
            "ctfmon.exe", "sihost.exe", "ShellExperienceHost.exe",
            "SearchHost.exe", "SystemSettings.exe", "TextInputHost.exe",
            "MsMpEng.exe", "NisSrv.exe", "SecurityHealthService.exe",
            "TiWorker.exe", "TrustedInstaller.exe", "wuauclt.exe",
            "ControlBiblioteca.Client.exe",
        };

        // Procesos que pueden lanzar cmd/powershell como hijos — ignorar en ese caso
        private static readonly HashSet<string> _procesosOffice = new(StringComparer.OrdinalIgnoreCase)
        {
            "WINWORD.EXE", "EXCEL.EXE", "POWERPNT.EXE", "ONENOTE.EXE",
            "OUTLOOK.EXE", "MSPUB.EXE", "VISIO.EXE",
            "OfficeClickToRun.exe", "SDXHelper.exe", "MsoSync.exe",
            "msosync.exe", "splwow64.exe", "MicrosoftEdgeUpdate.exe",
            "OfficeC2RClient.exe", "AppVShNotify.exe",
        };

        private void OnProcesoIniciado(object sender, EventArrivedEventArgs e)
        {
            if (!_activo || _cts.Token.IsCancellationRequested) return;

            try
            {
                string nombre = e.NewEvent.Properties["ProcessName"]?.Value?.ToString() ?? "";
                if (string.IsNullOrWhiteSpace(nombre)) return;

                string tipo    = "proceso";
                string nivel   = "normal";
                string desc    = "";
                string detalle = "";

                if (_navegadores.Contains(nombre))
                {
                    tipo  = "navegador";
                    nivel = "normal";
                    desc  = $"Abrió navegador: {Path.GetFileNameWithoutExtension(nombre)}";
                }
                else if (_sospechosos.Contains(nombre))
                {
                    // Verificar proceso padre — si lo lanzó Office u otro proceso del sistema, ignorar
                    if (EsHijoDeProcesoConocido(e))
                        return;

                    tipo  = "comando";
                    nivel = "sospechoso";
                    desc  = $"Abrió herramienta del sistema: {nombre}";
                    try
                    {
                        string cmd = e.NewEvent.Properties["CommandLine"]?.Value?.ToString() ?? "";
                        if (cmd.Length > 0)
                            detalle = cmd[..Math.Min(cmd.Length, 300)];
                    }
                    catch { }
                }
                else if (_academicos.Contains(nombre))
                {
                    tipo  = "proceso";
                    nivel = "normal";
                    desc  = $"Abrió aplicación: {Path.GetFileNameWithoutExtension(nombre)}";
                }
                else
                {
                    return; // no está en lista blanca — ignorar
                }

                _ = EnviarEventoAsync(tipo, desc, detalle, nivel);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[ActivityMonitor] Error en proceso: {ex.Message}");
            }
        }

        private static bool EsHijoDeProcesoConocido(EventArrivedEventArgs e)
        {
            try
            {
                // Obtener PID del proceso padre desde el evento WMI
                uint parentPid = Convert.ToUInt32(e.NewEvent.Properties["ParentProcessID"]?.Value ?? 0);
                if (parentPid == 0) return false;

                using var searcher = new ManagementObjectSearcher(
                    $"SELECT Name FROM Win32_Process WHERE ProcessId = {parentPid}"
                );
                foreach (ManagementObject obj in searcher.Get())
                {
                    string parentName = obj["Name"]?.ToString() ?? "";
                    if (_procesosOffice.Contains(parentName) ||
                        _procesosIgnorados.Contains(parentName) ||
                        parentName.StartsWith("Microsoft.", StringComparison.OrdinalIgnoreCase))
                        return true;
                }
            }
            catch { /* si falla la consulta, asumir que NO es hijo */ }
            return false;
        }

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
                string ext    = Path.GetExtension(e.Name ?? "").ToLowerInvariant();
                string nombre = e.Name ?? "";
                if (string.IsNullOrWhiteSpace(ext)) return;

                string nivel;
                string desc;

                if (_extSospechosas.Contains(ext))
                {
                    nivel = "sospechoso";
                    desc  = $"Descargó archivo ejecutable: {nombre}";
                }
                else if (_extNormales.Contains(ext))
                {
                    nivel = "normal";
                    desc  = $"Descargó archivo: {nombre}";
                }
                else
                {
                    return; // extensión no relevante — ignorar
                }

                _ = EnviarEventoAsync("archivo", desc, $"Downloads\\{nombre}", nivel);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[ActivityMonitor] Error en archivo: {ex.Message}");
            }
        }

        // ── Envío al servidor ─────────────────────────────────────────

        private async Task EnviarEventoAsync(string evento, string descripcion, string detalle, string nivel)
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
