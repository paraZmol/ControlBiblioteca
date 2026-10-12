using System;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Diagnostics;
using System.Security.Cryptography;

namespace ControlBiblioteca.Client.Services
{
    internal sealed class KioscoConfig
    {
        public string TerminalName { get; set; } = "";
        public string ServerIp     { get; set; } = "127.0.0.1";
        public int    ServerPort   { get; set; } = 8000;
        
        public int    BackdoorModifiers { get; set; } = 0x0003; // MOD_CONTROL | MOD_ALT
        public int    BackdoorKey       { get; set; } = 0x76;   // VK_F7
        public string BackdoorPin       { get; set; } = "UNASAM2025";

        public int    OfflineModifiers  { get; set; } = 0x0003; // MOD_CONTROL | MOD_ALT
        public int    OfflineKey        { get; set; } = 0x7A;   // VK_F11
        public string OfflinePin        { get; set; } = "UNASAM";

        // Hash PBKDF2 que manda el servidor (pbkdf2_sha256$iter$sal$hash). Si
        // existe, el PIN en claro se borra del config y se valida contra el hash:
        // ni la red ni kiosco.config.json exponen el PIN real.
        public string BackdoorPinHash   { get; set; } = "";
        public string OfflinePinHash    { get; set; } = "";

        /// <summary>Valida un PIN tipeado contra el hash (si hay) o el PIN en claro.</summary>
        // Freno a la fuerza bruta: tras 5 fallos seguidos, 60 s sin aceptar ningun PIN.
        private static int _fallosPin;
        private static DateTime _pinBloqueadoHasta = DateTime.MinValue;
        internal static bool PinBloqueado => DateTime.Now < _pinBloqueadoHasta;

        internal static bool PinValido(string ingresado, string hash, string plano)
        {
            if (PinBloqueado) return false;
            bool ok = PinValidoSinFreno(ingresado, hash, plano);
            if (ok) { _fallosPin = 0; return true; }
            if (++_fallosPin >= 5) { _fallosPin = 0; _pinBloqueadoHasta = DateTime.Now.AddSeconds(60); }
            return false;
        }

        private static bool PinValidoSinFreno(string ingresado, string hash, string plano)
        {
            ingresado ??= "";
            if (!string.IsNullOrEmpty(hash))
            {
                try
                {
                    var p = hash.Split('$');
                    if (p.Length != 4 || p[0] != "pbkdf2_sha256") return false;
                    int iter = int.Parse(p[1]);
                    byte[] sal = Convert.FromBase64String(p[2]);
                    byte[] esperado = Convert.FromBase64String(p[3]);
                    byte[] calc = Rfc2898DeriveBytes.Pbkdf2(Encoding.UTF8.GetBytes(ingresado), sal, iter,
                                                            HashAlgorithmName.SHA256, esperado.Length);
                    return CryptographicOperations.FixedTimeEquals(calc, esperado);
                }
                catch { return false; }
            }
            return !string.IsNullOrEmpty(plano) && plano.Length >= 4 && ingresado == plano;
        }

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
