using System;
using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Text;
using System.Threading.Tasks;

namespace ControlBiblioteca.Client.Services
{
    public static class AutoUpdater
    {
        // Versión de fallback — solo se usa la primera vez, antes de que exista version.local.
        // En producción, la versión real se lee desde el archivo version.local que el updater
        // escribe después de cada update exitoso, rompiendo el bucle de re-actualización.
        private const string VERSION_FALLBACK = "4.0";

        public static event Action<string>? OnEstado;

        private static readonly string _exeDir = Path.GetDirectoryName(
            Environment.ProcessPath ?? AppDomain.CurrentDomain.BaseDirectory)!;

        private static readonly string _logPath      = Path.Combine(_exeDir, "autoupdate.log");
        private static readonly string _versionLocal = Path.Combine(_exeDir, "version.local");

        // Lee la versión instalada desde version.local.
        // El updater escribe este archivo al finalizar cada actualización exitosa.
        // Fallback a VERSION_FALLBACK en el primer arranque (antes de cualquier update).
        public static string LeerVersionInstalada()
        {
            try
            {
                if (File.Exists(_versionLocal))
                {
                    string v = File.ReadAllText(_versionLocal, Encoding.UTF8).Trim();
                    if (!string.IsNullOrWhiteSpace(v)) return v;
                }
            }
            catch { }
            return VERSION_FALLBACK;
        }

        private static void Log(string msg)
        {
            string linea = $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {msg}";
            Debug.WriteLine($"[AutoUpdater] {linea}");
            try { File.AppendAllText(_logPath, linea + Environment.NewLine); } catch { }
        }

        public static async Task<bool> VerificarYActualizarAsync(string serverIp, int serverPort)
        {
            string exeActual = Environment.ProcessPath ?? Process.GetCurrentProcess().MainModule!.FileName;
            string exeDir    = Path.GetDirectoryName(exeActual)!;
            string exeNuevo  = Path.Combine(exeDir, "ControlBiblioteca.Client.exe.new");
            string exeBackup = Path.Combine(exeDir, "ControlBiblioteca.Client.exe.bak");

            string versionInstalada = LeerVersionInstalada();
            Log($"Iniciando. versionInstalada={versionInstalada} exe={exeActual}");

            // Limpieza de restos de intentos anteriores
            try { if (File.Exists(exeBackup)) File.Delete(exeBackup); Log("bak previo eliminado."); } catch { }
            try { if (File.Exists(exeNuevo))  File.Delete(exeNuevo);  Log("new previo eliminado."); } catch { }

            try
            {
                string baseUrl = $"http://{serverIp}:{serverPort}";

                // 1. Consultar versión del servidor
                using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(8) };
                HttpResponseMessage resp;
                try
                {
                    resp = await http.GetAsync($"{baseUrl}/api/version");
                }
                catch (Exception ex)
                {
                    Log($"No se pudo contactar el servidor: {ex.Message}");
                    return false;
                }

                if (!resp.IsSuccessStatusCode)
                {
                    Log($"Servidor respondió {(int)resp.StatusCode}");
                    return false;
                }

                string json = await resp.Content.ReadAsStringAsync();
                string versionServidor = ExtraerVersion(json);
                Log($"Versión servidor='{versionServidor}' | instalada='{versionInstalada}'");

                if (string.IsNullOrWhiteSpace(versionServidor)) return false;
                if (!EsVersionMayor(versionServidor, versionInstalada))
                {
                    Log("Sin actualización necesaria (servidor no es mayor).");
                    // Anclar version.local para que el fallback compilado deje de mandar
                    try
                    {
                        if (!File.Exists(_versionLocal) || File.ReadAllText(_versionLocal, Encoding.UTF8).Trim() != versionInstalada)
                            File.WriteAllText(_versionLocal, versionInstalada, Encoding.UTF8);
                    }
                    catch { }
                    return false;
                }

                // 2. Verificar permisos ANTES de descargar
                if (!TienePermisosEscritura(exeDir))
                {
                    Log($"ERROR: sin permisos de escritura en {exeDir} — abortando.");
                    OnEstado?.Invoke("Sin permisos para actualizar. Contacte al administrador.");
                    return false;
                }

                OnEstado?.Invoke($"Nueva versión {versionServidor}. Descargando...");
                Log($"Descargando desde {baseUrl}/api/descargar-cliente");

                // 3. Descargar a .new (streaming — no carga todo en RAM)
                using (var httpDescarga = new HttpClient { Timeout = TimeSpan.FromMinutes(5) })
                using (var streamServer = await httpDescarga.GetStreamAsync($"{baseUrl}/api/descargar-cliente"))
                using (var fileOut      = File.Create(exeNuevo))
                {
                    await streamServer.CopyToAsync(fileOut);
                }

                long tamano = new FileInfo(exeNuevo).Length;
                Log($"Descarga completa. Tamaño={tamano / 1024 / 1024} MB");

                // 4. Validar que sea un EXE real (firma MZ + tamaño mínimo)
                if (!EsExeValido(exeNuevo))
                {
                    Log("Validación fallida — descarga inválida o incompleta.");
                    OnEstado?.Invoke("Descarga inválida — omitiendo actualización.");
                    try { File.Delete(exeNuevo); } catch { }
                    return false;
                }

                // 5. Reemplazo en caliente — truco NTFS:
                //    Un exe en ejecución NO se puede borrar, pero SÍ renombrar.
                //    Al renombrar el exe vivo liberamos el nombre, luego movemos el nuevo.
                OnEstado?.Invoke("Instalando actualización...");
                Log("Reemplazo NTFS...");

                File.Move(exeActual, exeBackup); // exe vivo → .bak (renombrado, no borrado)
                Log($"1/2: {exeActual} → {exeBackup}");

                File.Move(exeNuevo, exeActual);  // .new → nombre original (nombre libre)
                Log($"2/2: {exeNuevo} → {exeActual}");

                // 6. Escribir version.local ANTES de lanzar el nuevo exe.
                //    El nuevo exe leerá este archivo y sabrá que ya está en versionServidor,
                //    sin importar qué constante tenga compilada. Esto rompe el bucle.
                try
                {
                    File.WriteAllText(_versionLocal, versionServidor, Encoding.UTF8);
                    Log($"version.local escrito: {versionServidor}");
                }
                catch (Exception ex)
                {
                    Log($"Advertencia: no se pudo escribir version.local: {ex.Message}");
                }

                // 7. Bat mínimo: espera 3s y lanza el nuevo exe. Usa ping en vez de
                //    timeout porque timeout falla en consolas sin TTY (modo kiosco).
                string batRuta = Path.Combine(Path.GetTempPath(), "lanzar_nueva_version.bat");
                string bat = $@"@echo off
ping -n 4 127.0.0.1 > nul
start """" ""{exeActual}""
del ""%~f0""
";
                await File.WriteAllTextAsync(batRuta, bat);
                Process.Start(new ProcessStartInfo
                {
                    FileName        = batRuta,
                    UseShellExecute = true,
                    WindowStyle     = ProcessWindowStyle.Hidden,
                    CreateNoWindow  = true,
                });
                Log("Bat lanzado. Cerrando proceso actual.");

                return true; // señal para que App.xaml.cs llame Environment.Exit(0)
            }
            catch (UnauthorizedAccessException ex)
            {
                Log($"ERROR PERMISOS: {ex.Message}");
                OnEstado?.Invoke("Sin permisos para actualizar. Reinstale con el administrador.");
                RollbackSiNecesario(exeActual, exeBackup, exeNuevo);
                return false;
            }
            catch (Exception ex)
            {
                Log($"ERROR: {ex.GetType().Name}: {ex.Message}");
                RollbackSiNecesario(exeActual, exeBackup, exeNuevo);
                return false;
            }
        }

        private static void RollbackSiNecesario(string exeActual, string exeBackup, string exeNuevo)
        {
            try
            {
                if (!File.Exists(exeActual) && File.Exists(exeBackup))
                {
                    File.Move(exeBackup, exeActual);
                    Log("Rollback: exe original restaurado desde .bak");
                }
            }
            catch (Exception rex) { Log($"Rollback fallido: {rex.Message}"); }
            try { if (File.Exists(exeNuevo)) File.Delete(exeNuevo); } catch { }
        }

        private static bool TienePermisosEscritura(string directorio)
        {
            string prueba = Path.Combine(directorio, $".perm_{Guid.NewGuid():N}");
            try
            {
                File.WriteAllText(prueba, "x");
                File.Delete(prueba);
                return true;
            }
            catch { return false; }
        }

        private static bool EsExeValido(string ruta)
        {
            try
            {
                var info = new FileInfo(ruta);
                if (info.Length < 1024 * 1024) return false; // < 1 MB → claramente inválido
                using var fs = File.OpenRead(ruta);
                return fs.ReadByte() == 'M' && fs.ReadByte() == 'Z'; // firma PE de Windows
            }
            catch { return false; }
        }

        /// <summary>
        /// Retorna true si 'nueva' es estrictamente mayor que 'actual'.
        /// Soporta formatos: "4.0", "4.1.2", "4.10" (comparación numérica, no léxica).
        /// </summary>
        private static bool EsVersionMayor(string nueva, string actual)
        {
            try
            {
                static string Normalizar(string v)
                {
                    v = v.Trim();
                    if (!v.Contains('.')) v += ".0";
                    return v;
                }
                var vNueva  = new Version(Normalizar(nueva));
                var vActual = new Version(Normalizar(actual));
                return vNueva > vActual;
            }
            catch
            {
                return nueva.Trim() != actual.Trim();
            }
        }

        private static string ExtraerVersion(string json)
        {
            try
            {
                int idx = json.IndexOf("\"version\"", StringComparison.OrdinalIgnoreCase);
                if (idx < 0) return "";
                int start = json.IndexOf('"', idx + 9) + 1;
                int end   = json.IndexOf('"', start);
                return json[start..end].Trim().TrimStart('\uFEFF'); // elimina BOM si Notepad lo agrega
            }
            catch { return ""; }
        }
    }
}