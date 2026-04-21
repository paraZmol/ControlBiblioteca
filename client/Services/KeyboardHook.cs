using System;
using System.Diagnostics;
using System.Runtime.InteropServices;

namespace ControlBiblioteca.Client.Services
{
    /// <summary>
    /// Hook de teclado a nivel de sistema para bloquear combinaciones peligrosas.
    /// Bloquea: Win, Alt+Tab, Alt+F4, Ctrl+Esc, Alt+Esc.
    /// </summary>
    public class KeyboardHook
    {
        // Constantes de la API de Windows
        private const int WH_KEYBOARD_LL = 13;
        private const int WM_KEYDOWN = 0x0100;
        private const int WM_SYSKEYDOWN = 0x0104;

        // Teclas a bloquear
        private const int VK_LWIN   = 0x5B;
        private const int VK_RWIN   = 0x5C;
        private const int VK_TAB    = 0x09;
        private const int VK_ESCAPE = 0x1B;
        private const int VK_F4     = 0x73;
        private const int VK_SPACE  = 0x20;  // no se bloquea, solo se usa para detectar escape

        private IntPtr _hookId = IntPtr.Zero;
        private readonly LowLevelKeyboardProc _proc;

        // Delegado para el callback del hook
        private delegate IntPtr LowLevelKeyboardProc(int nCode, IntPtr wParam, IntPtr lParam);

        public KeyboardHook()
        {
            _proc = HookCallback;
        }

        /// <summary>
        /// Instalar el hook de teclado global.
        /// </summary>
        public void Instalar()
        {
            if (_hookId != IntPtr.Zero) return;

            using var proceso = Process.GetCurrentProcess();
            using var modulo = proceso.MainModule!;
            _hookId = SetWindowsHookEx(WH_KEYBOARD_LL, _proc, GetModuleHandle(modulo.ModuleName), 0);

            if (_hookId == IntPtr.Zero)
                Debug.WriteLine("Error al instalar hook de teclado");
        }

        /// <summary>
        /// Desinstalar el hook de teclado.
        /// </summary>
        public void Desinstalar()
        {
            if (_hookId != IntPtr.Zero)
            {
                UnhookWindowsHookEx(_hookId);
                _hookId = IntPtr.Zero;
            }
        }

        /// <summary>
        /// Callback que intercepta cada tecla presionada.
        /// </summary>
        private IntPtr HookCallback(int nCode, IntPtr wParam, IntPtr lParam)
        {
            if (nCode >= 0)
            {
                int vkCode = Marshal.ReadInt32(lParam);
                bool altPresionado   = (GetAsyncKeyState(0xA4) & 0x8000) != 0 ||
                                       (GetAsyncKeyState(0xA5) & 0x8000) != 0;
                bool ctrlPresionado  = (GetAsyncKeyState(0xA2) & 0x8000) != 0 ||
                                       (GetAsyncKeyState(0xA3) & 0x8000) != 0;
                bool shiftPresionado = (GetAsyncKeyState(0xA0) & 0x8000) != 0 ||
                                       (GetAsyncKeyState(0xA1) & 0x8000) != 0;

                // Bloquear tecla Windows
                if (vkCode == VK_LWIN || vkCode == VK_RWIN)
                    return (IntPtr)1;

                // Bloquear Alt+Tab
                if (altPresionado && vkCode == VK_TAB)
                    return (IntPtr)1;

                // Bloquear Alt+F4
                if (altPresionado && vkCode == VK_F4)
                    return (IntPtr)1;

                // Bloquear Ctrl+Esc (abre menú inicio)
                if (ctrlPresionado && vkCode == VK_ESCAPE)
                    return (IntPtr)1;

                // Bloquear Alt+Esc
                if (altPresionado && vkCode == VK_ESCAPE)
                    return (IntPtr)1;

                // Bloquear Ctrl+Shift+Esc (abre Task Manager directamente)
                if (ctrlPresionado && shiftPresionado && vkCode == VK_ESCAPE)
                    return (IntPtr)1;

                // Bloquear Ctrl+Alt+Shift+Esc (variante)
                if (ctrlPresionado && altPresionado && vkCode == VK_ESCAPE)
                    return (IntPtr)1;
            }

            return CallNextHookEx(_hookId, nCode, wParam, lParam);
        }

        // ── Imports de Win32 ───────────────────────────────────────

        [DllImport("user32.dll", CharSet = CharSet.Auto, SetLastError = true)]
        private static extern IntPtr SetWindowsHookEx(int idHook, LowLevelKeyboardProc lpfn, IntPtr hMod, uint dwThreadId);

        [DllImport("user32.dll", CharSet = CharSet.Auto, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool UnhookWindowsHookEx(IntPtr hhk);

        [DllImport("user32.dll", CharSet = CharSet.Auto, SetLastError = true)]
        private static extern IntPtr CallNextHookEx(IntPtr hhk, int nCode, IntPtr wParam, IntPtr lParam);

        [DllImport("kernel32.dll", CharSet = CharSet.Auto, SetLastError = true)]
        private static extern IntPtr GetModuleHandle(string lpModuleName);

        [DllImport("user32.dll")]
        private static extern short GetAsyncKeyState(int vKey);
    }
}
