using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using Microsoft.Win32;

namespace ControlBiblioteca.Client.Services
{
    /// <summary>
    /// Configura Windows para el modo "Kiosco Superpuesto".
    ///
    /// Arquitectura:
    ///   Shell de Winlogon = explorer.exe (SIEMPRE).  El kiosco se lanza
    ///   mediante la tarea programada (watchdog) al iniciar sesión.
    ///   Al arrancar, mata explorer.exe y se pone Topmost.
    ///   Al desbloquear, se oculta y relanza explorer.exe — que al ser
    ///   el Shell oficial, carga escritorio + barra de tareas completa.
    ///
    /// Tarea watchdog (Programador de Tareas "ControlBibliotecaKiosco"):
    ///   Se dispara al iniciar sesión, se repite cada 1 minuto.
    ///   El Mutex en App.xaml.cs evita duplicados si ya estamos corriendo.
    ///   Si alguien mata el proceso, la tarea lo relanza en máximo 60 s.
    /// </summary>
    public static class StartupConfigurator
    {
        private const string WinlogonKey =
            @"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon";

        private const string SerializeSubKey =
            @"Software\Microsoft\Windows\CurrentVersion\Explorer\Serialize";

        private const string ShellBackupFile = "Backups\\OriginalShell.txt";

        private const string ExplorerRegPath =
            @"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Explorer";

        private const string TaskName = "ControlBibliotecaKiosco";

        // ── API pública ───────────────────────────────────────────────────────────

        /// <summary>
        /// Solo optimizaciones HKCU (sin admin). Llamar en cada arranque normal.
        /// </summary>
        public static void AplicarOptimizacionesUsuario()
        {
            try { EliminarRetardoExplorer(); } catch { }
        }

        /// <summary>
        /// True si el sistema está correctamente configurado:
        ///   1. La tarea watchdog existe en el Programador de Tareas.
        ///   2. El Shell de Winlogon es explorer.exe (NO nuestro exe).
        /// Si el Shell todavía apunta a nuestro exe (instalación vieja),
        /// devuelve false para forzar la migración automática.
        /// </summary>
        public static bool EstaConfigurado(string exePath)
        {
            try
            {
                // ¿La tarea watchdog existe?
                bool tareaExiste = TareaWatchdogExiste();
                if (!tareaExiste) return false;

                // ¿El Shell es explorer.exe? (no debe ser nuestro exe)
                using RegistryKey? k = Registry.LocalMachine.OpenSubKey(WinlogonKey);
                string shell = (k?.GetValue("Shell")?.ToString() ?? "").Trim('"', ' ');
                bool shellEsExplorer = shell.Equals("explorer.exe", StringComparison.OrdinalIgnoreCase);

                return shellEsExplorer;
            }
            catch { return false; }
        }

        /// <summary>
        /// Verifica si la tarea watchdog ya existe en el Programador de Tareas.
        /// </summary>
        public static bool TareaWatchdogExiste()
        {
            try
            {
                using var proc = Process.Start(new ProcessStartInfo
                {
                    FileName               = "schtasks.exe",
                    Arguments              = $"/Query /TN \"{TaskName}\"",
                    UseShellExecute        = false,
                    CreateNoWindow         = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError  = true
                });
                proc?.WaitForExit(5_000);
                return proc?.ExitCode == 0;
            }
            catch { return false; }
        }

        /// <summary>
        /// Configura el sistema para arranque como Shell nativo.
        /// Requiere privilegios de administrador.
        /// Devuelve true si todo se completó correctamente.
        /// </summary>
        public static bool Configure()
        {
            try
            {
                string baseDir = AppDomain.CurrentDomain.BaseDirectory;
                string exePath = Process.GetCurrentProcess().MainModule!.FileName;

                LimpiarConfiguracionesAnteriores(exePath);
                Directory.CreateDirectory(Path.Combine(baseDir, "Backups"));
                ExportarRegistro(Path.Combine(baseDir, "Backups"));
                EliminarRetardoExplorer();
                EstablecerShell(exePath, baseDir);
                RegistrarTareaWatchdog(exePath);

                return true;
            }
            catch (Exception ex)
            {
                Debug.WriteLine($"[StartupConfigurator] Configure() falló: {ex.Message}");
                return false;
            }
        }

        /// <summary>
        /// Restaura el Shell original y elimina optimizaciones.
        /// </summary>
        public static void Rollback()
        {
            string baseDir = AppDomain.CurrentDomain.BaseDirectory;
            try { RestaurarShell(baseDir); } catch { }
            try { EliminarClaveSerialize(); } catch { }
            try { EliminarTareaWatchdog(); } catch { }
        }

        // ── Servicio de pre-calentamiento ────────────────────────────────────────

        private const string ServiceName    = "ControlBibliotecaPreWarm";
        private const string ServiceExeName = "ControlBiblioteca.Service.exe";

        /// <summary>
        /// Registra e inicia el servicio de Windows que arranca ANTES del login
        /// para pre-inicializar la red. Si el exe del servicio no existe en la ruta
        /// de instalación, omite silenciosamente (no es un error fatal).
        /// </summary>
        public static void RegistrarServicioPreWarm(string dirInstalacion)
        {
            string exeServicio = Path.Combine(dirInstalacion, ServiceExeName);
            if (!File.Exists(exeServicio)) return; // servicio no incluido en este build

            string exeXml = SecurityEscape(exeServicio);

            // Eliminar instancia anterior si existe
            RunCmd("sc.exe", $"stop {ServiceName}");
            RunCmd("sc.exe", $"delete {ServiceName}");

            // Crear servicio: auto-start, descripción legible, ejecuta como LocalSystem
            RunCmd("sc.exe",
                $"create {ServiceName} " +
                $"binPath= \"{exeServicio}\" " +
                $"start= auto " +
                $"DisplayName= \"Kiosco Biblioteca - PreWarm\"");

            RunCmd("sc.exe", $"description {ServiceName} " +
                "\"Inicia servicios de red antes del login para acelerar el arranque del kiosco.\"");

            // Iniciar inmediatamente (no esperar al próximo boot)
            RunCmd("sc.exe", $"start {ServiceName}");
        }

        private static void RunCmd(string cmd, string args)
        {
            try
            {
                Process.Start(new ProcessStartInfo(cmd, args)
                {
                    UseShellExecute        = false,
                    CreateNoWindow         = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError  = true
                })?.WaitForExit(10_000);
            }
            catch { }
        }

        // ── Watchdog: Programador de Tareas ───────────────────────────────────────

        /// <summary>
        /// Crea (o actualiza) una tarea en el Programador de Tareas que relanza el
        /// kiosco si es eliminado. Se dispara al login y se repite cada 1 minuto.
        /// </summary>
        public static void RegistrarTareaWatchdog(string exePath)
        {
            // Escapar caracteres XML en la ruta del ejecutable
            string exeXml = SecurityEscape(exePath);

            // XML de la tarea — LogonTrigger + Repetition de 1 minuto
            string xml = $"""
                <?xml version="1.0" encoding="UTF-16"?>
                <Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
                  <RegistrationInfo>
                    <Description>Watchdog del kiosco de la Biblioteca UNASAM</Description>
                  </RegistrationInfo>
                  <Triggers>
                    <LogonTrigger>
                      <Enabled>true</Enabled>
                      <Repetition>
                        <Interval>PT1M</Interval>
                        <StopAtDurationEnd>false</StopAtDurationEnd>
                      </Repetition>
                    </LogonTrigger>
                  </Triggers>
                  <Settings>
                    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
                    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
                    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
                    <AllowHardTerminate>false</AllowHardTerminate>
                    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
                    <Priority>4</Priority>
                    <Hidden>true</Hidden>
                  </Settings>
                  <Actions Context="Author">
                    <Exec>
                      <Command>{exeXml}</Command>
                    </Exec>
                  </Actions>
                  <Principals>
                    <Principal id="Author">
                      <LogonType>InteractiveToken</LogonType>
                      <RunLevel>HighestAvailable</RunLevel>
                    </Principal>
                  </Principals>
                </Task>
                """;

            string xmlPath = Path.Combine(Path.GetTempPath(), "kiosco_task.xml");
            try
            {
                // schtasks requiere UTF-16 para /XML
                File.WriteAllText(xmlPath, xml, Encoding.Unicode);

                Process.Start(new ProcessStartInfo
                {
                    FileName        = "schtasks.exe",
                    Arguments       = $"/Create /TN \"{TaskName}\" /XML \"{xmlPath}\" /F",
                    UseShellExecute = false,
                    CreateNoWindow  = true
                })?.WaitForExit(15_000);
            }
            finally
            {
                try { File.Delete(xmlPath); } catch { }
            }
        }

        private static void EliminarTareaWatchdog()
        {
            Process.Start(new ProcessStartInfo
            {
                FileName        = "schtasks.exe",
                Arguments       = $"/Delete /TN \"{TaskName}\" /F",
                UseShellExecute = false,
                CreateNoWindow  = true
            })?.WaitForExit(5_000);
        }

        // ── Limpieza de versiones anteriores ─────────────────────────────────────

        private static void LimpiarConfiguracionesAnteriores(string exePath)
        {
            try
            {
                using RegistryKey? k = Registry.LocalMachine.OpenSubKey(WinlogonKey, writable: true);
                if (k != null)
                {
                    string userinit = k.GetValue("Userinit")?.ToString() ?? "";
                    if (userinit.Contains(exePath, StringComparison.OrdinalIgnoreCase))
                    {
                        string limpio = userinit
                            .Replace($",{exePath},", ",", StringComparison.OrdinalIgnoreCase)
                            .Replace($"{exePath},",  "",  StringComparison.OrdinalIgnoreCase)
                            .Replace(exePath,        "",  StringComparison.OrdinalIgnoreCase);
                        k.SetValue("Userinit", limpio, RegistryValueKind.String);
                    }
                }
            }
            catch { }

            // Eliminar tarea de versiones anteriores (nombre antiguo)
            try
            {
                Process.Start(new ProcessStartInfo
                {
                    FileName        = "schtasks.exe",
                    Arguments       = "/Delete /TN \"ArranqueBiblioteca\" /F",
                    UseShellExecute = false,
                    CreateNoWindow  = true
                })?.WaitForExit();
            }
            catch { }

            try
            {
                using RegistryKey? run = Registry.CurrentUser.OpenSubKey(
                    @"Software\Microsoft\Windows\CurrentVersion\Run", writable: true);
                run?.DeleteValue("ControlBiblioteca", throwOnMissingValue: false);
            }
            catch { }
        }

        // ── Pasos de configuración ────────────────────────────────────────────────

        private static void ExportarRegistro(string backupFolder)
        {
            string dest = Path.Combine(backupFolder,
                $"Explorer_Backup_{DateTime.Now:yyyyMMdd_HHmmss}.reg");

            using var proc = Process.Start(new ProcessStartInfo
            {
                FileName              = "reg.exe",
                Arguments             = $"export \"{ExplorerRegPath}\" \"{dest}\" /y",
                UseShellExecute       = false,
                CreateNoWindow        = true,
                RedirectStandardError = true
            }) ?? throw new InvalidOperationException("No se pudo iniciar reg.exe.");

            proc.StandardError.ReadToEnd();
            proc.WaitForExit();
        }

        private static void EliminarRetardoExplorer()
        {
            using RegistryKey key = Registry.CurrentUser.CreateSubKey(
                SerializeSubKey, writable: true)
                ?? throw new InvalidOperationException("No se pudo crear clave Serialize.");
            key.SetValue("StartupDelayInMSec", 0, RegistryValueKind.DWord);
        }

        private static void EstablecerShell(string exePath, string baseDir)
        {
            using RegistryKey key = Registry.LocalMachine.OpenSubKey(WinlogonKey, writable: true)
                ?? throw new InvalidOperationException(
                    "No se pudo abrir HKLM\\Winlogon. ¿Corre como Administrador?");

            string original = key.GetValue("Shell")?.ToString() ?? "explorer.exe";
            File.WriteAllText(Path.Combine(baseDir, ShellBackupFile), original);

            // Siempre restaurar explorer.exe como Shell oficial.
            // El kiosco se inicia mediante la tarea programada (watchdog).
            key.SetValue("Shell", "explorer.exe", RegistryValueKind.String);
        }

        // ── Rollback ──────────────────────────────────────────────────────────────

        private static void RestaurarShell(string baseDir)
        {
            using RegistryKey key = Registry.LocalMachine.OpenSubKey(WinlogonKey, writable: true)
                ?? throw new InvalidOperationException("No se pudo abrir Winlogon.");

            string backupPath = Path.Combine(baseDir, ShellBackupFile);
            string original = File.Exists(backupPath)
                ? File.ReadAllText(backupPath).Trim()
                : "explorer.exe";

            key.SetValue("Shell", original, RegistryValueKind.String);
        }

        private static void EliminarClaveSerialize()
        {
            using RegistryKey? parent = Registry.CurrentUser.OpenSubKey(
                @"Software\Microsoft\Windows\CurrentVersion\Explorer", writable: true);
            parent?.DeleteSubKey("Serialize", throwOnMissingSubKey: false);
        }

        // ── Utilidades ────────────────────────────────────────────────────────────

        private static string SecurityEscape(string s) =>
            s.Replace("&",  "&amp;")
             .Replace("<",  "&lt;")
             .Replace(">",  "&gt;")
             .Replace("\"", "&quot;")
             .Replace("'",  "&apos;");
    }
}
