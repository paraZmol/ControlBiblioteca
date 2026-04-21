using System;
using Microsoft.Win32;

namespace ControlBiblioteca.Client.Services
{
    /// <summary>
    /// Gestiona el bloqueo de seguridad del kiosco como una unidad atómica.
    ///
    /// Capas activas al bloquear:
    ///   1. Hook de teclado (Win, Alt+Tab, Alt+F4, Ctrl+Esc, Ctrl+Shift+Esc…)
    ///   2. DisableTaskMgr en HKCU (siempre aplica)
    ///   3. DisableTaskMgr en HKLM (si el proceso tiene admin — Task Scheduler elevado)
    ///   4. IFEO: redirige taskmgr.exe a un proceso nulo — imposible abrirlo por cualquier vía
    ///
    /// Al desbloquear, TODAS las capas se revierten para que el alumno tenga
    /// acceso normal al escritorio.
    ///
    /// Es seguro llamar a Bloquear() varias veces (idempotente).
    /// </summary>
    public sealed class SecurityManager : IDisposable
    {
        private readonly KeyboardHook _hook = new();
        private bool _bloqueado;
        private bool _disposed;

        private const string HKCU_POLICIES =
            @"Software\Microsoft\Windows\CurrentVersion\Policies\System";
        private const string HKLM_POLICIES =
            @"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System";
        private const string IFEO_TASKMGR =
            @"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options\Taskmgr.exe";

        // ── API pública ───────────────────────────────────────────────────────────

        public bool EstaBloqueado => _bloqueado;

        /// <summary>
        /// Aplica las capas de registro sin necesidad de un message loop.
        /// Llamar desde el constructor estático de App para el bloqueo más temprano posible.
        /// </summary>
        public static void BloquearRegistroEstatico()
        {
            SetDword(Registry.CurrentUser,  HKCU_POLICIES, "DisableTaskMgr", 1);
            SetDword(Registry.LocalMachine, HKLM_POLICIES, "DisableTaskMgr", 1);
            BloquearIFEO();
        }

        /// <summary>
        /// Activa todas las capas de bloqueo. Llamar antes de mostrar cualquier ventana.
        /// </summary>
        public void Bloquear()
        {
            if (_bloqueado) return;
            _bloqueado = true;

            _hook.Instalar();
            SetDword(Registry.CurrentUser,  HKCU_POLICIES, "DisableTaskMgr", 1);
            SetDword(Registry.LocalMachine, HKLM_POLICIES, "DisableTaskMgr", 1); // silencioso si no admin
            BloquearIFEO();
        }

        /// <summary>
        /// Revierte todas las capas. Llamar cuando el alumno se autentica.
        /// </summary>
        public void Desbloquear()
        {
            if (!_bloqueado) return;
            _bloqueado = false;

            _hook.Desinstalar();
            DeleteValue(Registry.CurrentUser,  HKCU_POLICIES, "DisableTaskMgr");
            DeleteValue(Registry.LocalMachine, HKLM_POLICIES, "DisableTaskMgr");
            DesbloquearIFEO();
        }

        public void Dispose()
        {
            if (_disposed) return;
            _disposed = true;
            Desbloquear();
        }

        // ── IFEO — Image File Execution Options ──────────────────────────────────

        private static void BloquearIFEO()
        {
            // Cuando Windows intenta abrir taskmgr.exe, busca primero este "debugger".
            // Al apuntar a un exe que no existe, el proceso falla silenciosamente:
            // ni taskmgr ni el "debugger" se abren.
            try
            {
                using var k = Registry.LocalMachine.CreateSubKey(IFEO_TASKMGR, writable: true);
                k.SetValue("Debugger",
                    @"%SystemRoot%\System32\rundll32.exe",  // existe pero sin argumentos → no hace nada
                    RegistryValueKind.String);
            }
            catch { /* no admin: silencioso, las otras capas siguen activas */ }
        }

        private static void DesbloquearIFEO()
        {
            try
            {
                Registry.LocalMachine.DeleteSubKeyTree(IFEO_TASKMGR, throwOnMissingSubKey: false);
            }
            catch { }
        }

        // ── Helpers de registro ──────────────────────────────────────────────────

        private static void SetDword(RegistryKey root, string path, string name, int value)
        {
            try
            {
                using var k = root.CreateSubKey(path, writable: true);
                k.SetValue(name, value, RegistryValueKind.DWord);
            }
            catch { }
        }

        private static void DeleteValue(RegistryKey root, string path, string name)
        {
            try
            {
                using var k = root.OpenSubKey(path, writable: true);
                k?.DeleteValue(name, throwOnMissingValue: false);
            }
            catch { }
        }
    }
}
