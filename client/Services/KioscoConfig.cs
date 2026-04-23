using System;
using System.IO;
using System.Text.Json;
using System.Diagnostics;

namespace ControlBiblioteca.Client.Services
{
    internal sealed class KioscoConfig
    {
        public string TerminalName { get; set; } = "";
        public string ServerIp     { get; set; } = "127.0.0.1";
        public int    ServerPort   { get; set; } = 8000;

        internal string WsBaseUrl => $"ws://{ServerIp}:{ServerPort}";

        private static readonly string _ruta = Path.Combine(
            Path.GetDirectoryName(Environment.ProcessPath)
                ?? AppDomain.CurrentDomain.BaseDirectory,
            "kiosco.config.json");

        internal void Guardar()
        {
            try
            {
                var options = new JsonSerializerOptions { WriteIndented = true };
                string json = JsonSerializer.Serialize(this, options);
                File.WriteAllText(_ruta, json);
            }
            catch (Exception ex)
            {
                Debug.WriteLine($"Error al guardar config: {ex.Message}");
            }
        }

        /// <summary>
        /// Lee kiosco.config.json y devuelve también un mensaje de diagnóstico
        /// que describe qué ruta buscó, si lo encontró, y qué cargó.
        /// Nunca lanza excepción.
        /// </summary>
        internal static (KioscoConfig Config, string Diagnostico) LeerConDiagnostico()
        {
            if (!File.Exists(_ruta))
                return (new KioscoConfig(),
                    $"AVISO: kiosco.config.json no encontrado en:\n{_ruta}\n" +
                    $"Usando defaults → 127.0.0.1:8000");

            try
            {
                string json = File.ReadAllText(_ruta);
                var cfg = JsonSerializer.Deserialize<KioscoConfig>(
                    json,
                    new JsonSerializerOptions { PropertyNameCaseInsensitive = true }
                );

                if (cfg is null || string.IsNullOrWhiteSpace(cfg.ServerIp) ||
                    cfg.ServerPort is <= 0 or >= 65536)
                    return (new KioscoConfig(),
                        $"AVISO: config inválido en:\n{_ruta}\nContenido: {json}\n" +
                        $"Usando defaults → 127.0.0.1:8000");

                return (cfg, $"Config OK → {cfg.ServerIp}:{cfg.ServerPort}\n(leído de {_ruta})");
            }
            catch (Exception ex)
            {
                return (new KioscoConfig(),
                    $"ERROR al leer {_ruta}:\n{ex.Message}\nUsando defaults → 127.0.0.1:8000");
            }
        }

        internal static KioscoConfig Leer() => LeerConDiagnostico().Config;
    }
}
