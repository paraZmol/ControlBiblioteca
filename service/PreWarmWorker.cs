using System.Diagnostics;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace ControlBiblioteca.Service
{
    /// <summary>
    /// Servicio de pre-calentamiento que arranca con Windows (como SYSTEM, antes del login).
    ///
    /// Hace en segundo plano todo lo que normalmente retrasa la aparición del kiosco:
    ///   1. Inicia los servicios de red para que la red esté disponible al hacer login.
    ///   2. Verifica periódicamente que esos servicios sigan activos.
    ///
    /// Resultado: cuando el usuario introduce sus credenciales de Windows y la sesión
    /// arranca, la red ya está lista → el kiosco aparece casi al instante.
    /// </summary>
    public sealed class PreWarmWorker : BackgroundService
    {
        private readonly ILogger<PreWarmWorker> _log;

        // Servicios de red críticos — el worker lee la lista guardada por el instalador
        // y cae back a estos defaults si el archivo no existe.
        private static readonly string[] ServiciosDefault =
            { "Dhcp", "Dnscache", "NlaSvc", "netprofm", "LanmanWorkstation" };

        private const string PERFIL_SERVICIOS = @"C:\SistemaBiblioteca\network_services.txt";

        public PreWarmWorker(ILogger<PreWarmWorker> logger) => _log = logger;

        protected override async Task ExecuteAsync(CancellationToken ct)
        {
            // Esperar a que Windows termine de arrancar antes del primer ciclo
            await Task.Delay(TimeSpan.FromSeconds(25), ct);

            string[] servicios = CargarPerfilServicios();
            _log.LogInformation("PreWarm iniciado. Servicios a vigilar: {s}", string.Join(", ", servicios));

            while (!ct.IsCancellationRequested)
            {
                foreach (string svc in servicios)
                    AsegurarServicio(svc);

                // Ciclo de verificación cada 5 minutos
                await Task.Delay(TimeSpan.FromMinutes(5), ct);
            }
        }

        private static string[] CargarPerfilServicios()
        {
            try
            {
                if (File.Exists(PERFIL_SERVICIOS))
                {
                    string[] lineas = File.ReadAllLines(PERFIL_SERVICIOS);
                    if (lineas.Length > 0) return lineas;
                }
            }
            catch { }
            return ServiciosDefault;
        }

        private void AsegurarServicio(string nombre)
        {
            try
            {
                var psi = new ProcessStartInfo("sc.exe", $"start {nombre}")
                {
                    UseShellExecute        = false,
                    CreateNoWindow         = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError  = true
                };
                using var p = Process.Start(psi);
                p?.WaitForExit(8_000);
            }
            catch (Exception ex)
            {
                _log.LogWarning("No se pudo iniciar {svc}: {err}", nombre, ex.Message);
            }
        }
    }
}
