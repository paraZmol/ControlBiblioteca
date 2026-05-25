using System;
using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Threading.Tasks;

namespace ControlBiblioteca.Client.Services
{
    /// <summary>
    /// Verifica si hay una nueva versión del cliente en el servidor.
    /// Si la hay, la descarga, reemplaza el exe actual y reinicia.
    /// Si falla en cualquier paso, el cliente arranca normal sin errores.
    /// </summary>
    public static class AutoUpdater
    {
        // Versión actual del cliente — cambiar en cada release
        public const string VERSION_ACTUAL = "4.0";

        public static event Action<string>? OnEstado;

        public static async Task<bool> VerificarYActualizarAsync(string serverIp, int serverPort)
        {
            try
            {
                string baseUrl = $"http://{serverIp}:{serverPort}";

                // 1. Consultar versión disponible en el servidor
                using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(5) };
                var resp = await http.GetAsync($"{baseUrl}/api/version");
                if (!resp.IsSuccessStatusCode) return false;

                string json = await resp.Content.ReadAsStringAsync();
                string versionServidor = ExtraerVersion(json);

                if (string.IsNullOrWhiteSpace(versionServidor)) return false;
                if (versionServidor == VERSION_ACTUAL) return false;

                // 2. Hay versión nueva — notificar y descargar
                OnEstado?.Invoke($"Nueva versión disponible: {versionServidor}. Actualizando...");

                string dirActual  = Path.GetDirectoryName(Environment.ProcessPath)
                                    ?? AppDomain.CurrentDomain.BaseDirectory;
                string exeActual  = Environment.ProcessPath ?? Process.GetCurrentProcess().MainModule!.FileName;
                string exeNuevo   = Path.Combine(dirActual, "ControlBiblioteca.Client_new.exe");
                string batRuta    = Path.Combine(dirActual, "actualizar.bat");

                // 3. Descargar nuevo exe
                OnEstado?.Invoke("Descargando actualización...");
                using var httpDescarga = new HttpClient { Timeout = TimeSpan.FromMinutes(3) };
                var bytes = await httpDescarga.GetByteArrayAsync($"{baseUrl}/api/descargar-cliente");
                await File.WriteAllBytesAsync(exeNuevo, bytes);

                // 4. Crear script bat que reemplaza el exe y lanza el nuevo
                string bat = $@"@echo off
timeout /t 2 /nobreak > nul
move /y ""{exeNuevo}"" ""{exeActual}""
start """" ""{exeActual}""
del ""%~f0""
";
                await File.WriteAllTextAsync(batRuta, bat);

                // 5. Lanzar el bat y cerrar el proceso actual
                OnEstado?.Invoke("Instalando actualización...");
                Process.Start(new ProcessStartInfo
                {
                    FileName        = batRuta,
                    CreateNoWindow  = true,
                    UseShellExecute = true,
                    WindowStyle     = ProcessWindowStyle.Hidden,
                });

                return true; // señal para cerrar el proceso actual
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[AutoUpdater] Falló: {ex.Message}");
                return false; // arrancar normal
            }
        }

        private static string ExtraerVersion(string json)
        {
            // Parseo simple sin dependencias: {"version":"3.1"}
            try
            {
                int idx = json.IndexOf("\"version\"", StringComparison.OrdinalIgnoreCase);
                if (idx < 0) return "";
                int start = json.IndexOf('"', idx + 9) + 1;
                int end   = json.IndexOf('"', start);
                return json[start..end].Trim();
            }
            catch { return ""; }
        }
    }
}
