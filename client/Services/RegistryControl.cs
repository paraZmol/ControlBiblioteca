using System;
using Microsoft.Win32;

namespace ControlBiblioteca.Client.Services
{
    /// <summary>
    /// Control del Registro de Windows para deshabilitar/habilitar Task Manager.
    /// Usa doble capa: HKCU (siempre disponible) + HKLM (requiere admin, inamovible).
    /// </summary>
    public class RegistryControl
    {
        private const string HKCU_POLICY = @"Software\Microsoft\Windows\CurrentVersion\Policies\System";
        private const string HKLM_POLICY = @"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System";

        /// <summary>
        /// Deshabilitar Task Manager vía HKCU (no requiere admin).
        /// </summary>
        public void DeshabilitarTaskManager()
        {
            try
            {
                using var key = Registry.CurrentUser.CreateSubKey(HKCU_POLICY, true);
                key?.SetValue("DisableTaskMgr", 1, RegistryValueKind.DWord);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Error al deshabilitar TaskMgr (HKCU): {ex.Message}");
            }
        }

        /// <summary>
        /// Deshabilitar Task Manager vía HKLM (requiere admin). 
        /// Bloqueo a nivel máquina — persiste incluso si HKCU se borra.
        /// </summary>
        public void DeshabilitarTaskManagerMaquina()
        {
            try
            {
                using var key = Registry.LocalMachine.CreateSubKey(HKLM_POLICY, true);
                key?.SetValue("DisableTaskMgr", 1, RegistryValueKind.DWord);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Error al deshabilitar TaskMgr (HKLM): {ex.Message}");
            }
        }

        /// <summary>
        /// Habilitar Task Manager — borra AMBAS capas (HKCU y HKLM).
        /// Solo usar en escape de emergencia o mantenimiento.
        /// Si no tiene permisos para HKLM, lo intenta vía proceso elevado.
        /// </summary>
        public void HabilitarTaskManager()
        {
            // 1. HKCU — siempre funciona
            try
            {
                using var key = Registry.CurrentUser.OpenSubKey(HKCU_POLICY, true);
                key?.DeleteValue("DisableTaskMgr", false);
            }
            catch { }

            // 2. HKLM — intentar directo (funciona si ya es admin)
            try
            {
                using var key = Registry.LocalMachine.OpenSubKey(HKLM_POLICY, true);
                key?.DeleteValue("DisableTaskMgr", false);
                return; // Éxito, no necesita elevación
            }
            catch { }

            // 3. Si HKLM falló, intentar vía reg.exe elevado
            try
            {
                var psi = new System.Diagnostics.ProcessStartInfo
                {
                    FileName = "reg.exe",
                    Arguments = $"delete \"HKLM\\{HKLM_POLICY}\" /v DisableTaskMgr /f",
                    Verb = "runas",
                    UseShellExecute = true,
                    WindowStyle = System.Diagnostics.ProcessWindowStyle.Hidden
                };
                using var proc = System.Diagnostics.Process.Start(psi);
                proc?.WaitForExit(5000);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"No se pudo limpiar HKLM (sin admin): {ex.Message}");
            }
        }
    }
}
