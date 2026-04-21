using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using Microsoft.Win32;

namespace ControlBiblioteca.Client.Services
{
    /// <summary>
    /// Analiza la máquina antes de instalar y devuelve un perfil con
    /// todo lo que InstaladorKiosco necesita saber para adaptarse.
    /// No escribe nada — solo lee.
    /// </summary>
    public sealed class DiagnosticoSistema
    {
        // ── Ruta de instalación ───────────────────────────────────────────────────

        /// <summary>Letra de la unidad del sistema (ej. "C:\").</summary>
        public string UnidadSistema { get; init; } = "C:\\";

        /// <summary>Ruta definitiva donde se instalará el programa.</summary>
        public string RutaInstalacion { get; init; } = "";

        // ── Sistema operativo ─────────────────────────────────────────────────────

        public string VersionWindows  { get; init; } = "";
        public int    BuildWindows    { get; init; }
        public bool   Es64Bits        { get; init; }

        // ── Configuración actual ──────────────────────────────────────────────────

        /// <summary>Valor actual de Winlogon\Shell (lo que Windows lanza al login).</summary>
        public string ShellActual { get; init; } = "explorer.exe";

        /// <summary>El Shell ya apunta al exe instalado.</summary>
        public bool ShellYaConfigurado { get; init; }

        /// <summary>La tarea watchdog "ControlBibliotecaKiosco" ya existe.</summary>
        public bool TareaWatchdogExiste { get; init; }

        // ── Recursos disponibles ──────────────────────────────────────────────────

        /// <summary>Espacio libre en la unidad de destino, en MB.</summary>
        public long EspacioLibreMB { get; init; }

        /// <summary>powercfg.exe existe y responde en esta máquina.</summary>
        public bool PowerCfgDisponible { get; init; }

        /// <summary>
        /// Servicios de red que EXISTEN en esta instalación de Windows
        /// (un servicio puede no existir en ediciones Home o Server).
        /// </summary>
        public IReadOnlyList<string> ServiciosRedDisponibles { get; init; }
            = Array.Empty<string>();

        /// <summary>
        /// Nivel de UAC configurado:
        ///   0 = desactivado, 1 = bajo, 2 = medio (defecto), 3 = alto (siempre notificar).
        /// </summary>
        public int NivelUAC { get; init; }

        // ── Posibles problemas detectados ─────────────────────────────────────────

        /// <summary>
        /// Lista de advertencias generadas durante el análisis.
        /// No son errores fatales, pero el instalador las muestra al usuario.
        /// </summary>
        public IReadOnlyList<string> Advertencias { get; init; }
            = Array.Empty<string>();

        // ── Constructor interno — usar Analizar() ────────────────────────────────

        internal DiagnosticoSistema() { }

        // ── Análisis ─────────────────────────────────────────────────────────────

        public static DiagnosticoSistema Analizar()
        {
            var avisos = new List<string>();
            var d      = new DiagnosticoSistema();

            // ── Unidad del sistema ────────────────────────────────────────────────
            string sysDir = Environment.SystemDirectory;           // ej. C:\Windows\System32
            string unidad = Path.GetPathRoot(sysDir) ?? "C:\\";   // ej. C:\

            // ── Ruta de instalación (siempre en la misma unidad que Windows) ──────
            string rutaBase = Path.Combine(unidad, "SistemaBiblioteca");

            // ── Versión de Windows ────────────────────────────────────────────────
            string version = "";
            int    build   = 0;
            try
            {
                version = Registry.GetValue(
                    @"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion",
                    "DisplayVersion", "") as string ?? "";

                string? buildStr = Registry.GetValue(
                    @"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion",
                    "CurrentBuildNumber", "0") as string;
                int.TryParse(buildStr, out build);

                // Windows 11 empieza en build 22000
                if (build == 0)
                    version = Environment.OSVersion.VersionString;
            }
            catch { version = Environment.OSVersion.VersionString; }

            // ── Shell actual ──────────────────────────────────────────────────────
            string shellActual = "explorer.exe";
            try
            {
                using var k = Registry.LocalMachine.OpenSubKey(
                    @"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon");
                shellActual = k?.GetValue("Shell")?.ToString() ?? "explorer.exe";
            }
            catch { }

            string exeInstalado = Path.Combine(rutaBase, "ControlBiblioteca.Client.exe");
            bool   yaConfigurado = shellActual
                .Trim('"', ' ')
                .Equals(exeInstalado, StringComparison.OrdinalIgnoreCase);

            // ── Tarea watchdog ────────────────────────────────────────────────────
            bool tareaExiste = false;
            try
            {
                var psi = new ProcessStartInfo("schtasks.exe",
                    "/Query /TN \"ControlBibliotecaKiosco\" /FO LIST")
                {
                    UseShellExecute        = false,
                    CreateNoWindow         = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError  = true
                };
                using var p = Process.Start(psi);
                p?.WaitForExit(5_000);
                tareaExiste = p?.ExitCode == 0;
            }
            catch { }

            // ── Espacio libre ─────────────────────────────────────────────────────
            long espacioMB = 0;
            try
            {
                var drive = new DriveInfo(unidad);
                espacioMB = drive.AvailableFreeSpace / 1_048_576;
                if (espacioMB < 200)
                    avisos.Add($"Espacio libre bajo: {espacioMB} MB (recomendado ≥ 200 MB).");
            }
            catch { }

            // ── Servicios de red presentes ────────────────────────────────────────
            string[] candidatos = { "Dhcp", "Dnscache", "NlaSvc", "netprofm", "LanmanWorkstation" };
            var serviciosPresentes = new List<string>();
            foreach (string svc in candidatos)
            {
                try
                {
                    using var k = Registry.LocalMachine.OpenSubKey(
                        $@"SYSTEM\CurrentControlSet\Services\{svc}");
                    if (k != null) serviciosPresentes.Add(svc);
                }
                catch { }
            }

            // ── PowerCfg ──────────────────────────────────────────────────────────
            bool powerCfgOk = false;
            try
            {
                string powercfgPath = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.System),
                    "powercfg.exe");
                powerCfgOk = File.Exists(powercfgPath);
            }
            catch { }

            // ── Nivel de UAC ──────────────────────────────────────────────────────
            int nivelUac = 2;
            try
            {
                object? v = Registry.GetValue(
                    @"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System",
                    "ConsentPromptBehaviorAdmin", 2);
                nivelUac = Convert.ToInt32(v);
            }
            catch { }

            // ── Avisos adicionales ────────────────────────────────────────────────
            if (!Environment.Is64BitOperatingSystem)
                avisos.Add("Sistema operativo de 32 bits detectado.");

            if (yaConfigurado)
                avisos.Add("El Shell ya apunta al exe instalado — solo se actualizará la configuración.");

            // ── Construir resultado ───────────────────────────────────────────────
            return new DiagnosticoSistema
            {
                UnidadSistema           = unidad,
                RutaInstalacion         = rutaBase,
                VersionWindows          = version,
                BuildWindows            = build,
                Es64Bits                = Environment.Is64BitOperatingSystem,
                ShellActual             = shellActual,
                ShellYaConfigurado      = yaConfigurado,
                TareaWatchdogExiste     = tareaExiste,
                EspacioLibreMB          = espacioMB,
                PowerCfgDisponible      = powerCfgOk,
                ServiciosRedDisponibles = serviciosPresentes,
                NivelUAC                = nivelUac,
                Advertencias            = avisos
            };
        }
    }
}
