using System;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;

namespace ControlBiblioteca.Client.Services
{
    /// <summary>
    /// Puerta trasera de mantenimiento para recuperar el escritorio sin abrir CMD.
    ///
    /// Cómo usarla en la VM:
    ///   1. Presionar Ctrl+Alt+F7 en cualquier momento (no importa si la UI está bloqueada).
    ///   2. Ingresar el PIN de mantenimiento en el diálogo que aparece.
    ///   3. Windows Explorer se lanzará y la aplicación kiosco se cerrará.
    ///
    /// El hotkey se registra a nivel de sistema vía RegisterHotKey, por lo que funciona
    /// incluso cuando el KeyboardHook de la app está activo bloqueando otras combinaciones.
    /// </summary>
    internal sealed class MantenimientoBackdoor : IDisposable
    {
        private const int HOTKEY_ID = 0x4D41; // 'MA' de Mantenimiento
        private const int WM_HOTKEY = 0x0312;

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool RegisterHotKey(IntPtr hWnd, int id, int fsModifiers, int vk);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool UnregisterHotKey(IntPtr hWnd, int id);

        private readonly App _app;
        private readonly KioscoConfig _config;
        private HwndSource? _hwnd;
        private bool _disposed;

        public MantenimientoBackdoor(App app, KioscoConfig config)
        {
            _app = app;
            _config = config;
            // La ventana de mensajes debe crearse en el hilo de UI
            app.Dispatcher.Invoke(CrearVentanaMensajes);
        }

        private void CrearVentanaMensajes()
        {
            var param = new HwndSourceParameters("KioscoMantenimientoHotkey")
            {
                Width        = 0,
                Height       = 0,
                WindowStyle  = 0,
                // HWND_MESSAGE (-3): ventana invisible que solo recibe mensajes
                ParentWindow = new IntPtr(-3)
            };

            _hwnd = new HwndSource(param);
            _hwnd.AddHook(WndProc);

            bool ok = RegisterHotKey(_hwnd.Handle, HOTKEY_ID, _config.BackdoorModifiers, _config.BackdoorKey);
            if (!ok)
                System.Diagnostics.Debug.WriteLine($"[Backdoor] No se pudo registrar el atajo (Mod: {_config.BackdoorModifiers}, Key: {_config.BackdoorKey}).");
        }

        private IntPtr WndProc(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam, ref bool handled)
        {
            if (msg == WM_HOTKEY && wParam.ToInt32() == HOTKEY_ID)
            {
                handled = true;
                MostrarDialogoPin();
            }
            return IntPtr.Zero;
        }

        private void MostrarDialogoPin()
        {
            // Construir el diálogo programáticamente para no depender de XAML externo
            var ventana = new Window
            {
                Title                 = "Mantenimiento",
                Width                 = 340,
                Height                = 200,
                WindowStyle           = WindowStyle.None,
                ResizeMode            = ResizeMode.NoResize,
                Topmost               = true,
                ShowInTaskbar         = false,
                WindowStartupLocation = WindowStartupLocation.CenterScreen,
                Background            = new SolidColorBrush(Color.FromRgb(30, 40, 55))
            };

            var panel = new StackPanel { Margin = new Thickness(24) };

            var titulo = new TextBlock
            {
                Text       = "Acceso de Mantenimiento (Ctrl+Alt+F7)",
                Foreground = Brushes.LightSteelBlue,
                FontSize   = 11,
                Margin     = new Thickness(0, 0, 0, 14),
                TextWrapping = TextWrapping.Wrap
            };

            var etiqueta = new TextBlock
            {
                Text       = "PIN:",
                Foreground = Brushes.White,
                FontSize   = 13,
                Margin     = new Thickness(0, 0, 0, 6)
            };

            var pinBox = new PasswordBox
            {
                FontSize  = 15,
                MaxLength = 32,
                Margin    = new Thickness(0, 0, 0, 16)
            };

            var btnAcceder = new Button
            {
                Content = "Acceder al escritorio",
                FontSize = 13,
                Padding  = new Thickness(12, 6, 12, 6),
                HorizontalAlignment = HorizontalAlignment.Right
            };

            void Verificar(object? s, RoutedEventArgs ev)
            {
                if (pinBox.Password == _config.BackdoorPin)
                {
                    ventana.Close();
                    _app.EscaparAExplorer("maintenance_backdoor");
                }
                else
                {
                    pinBox.Clear();
                    pinBox.Focus();
                    etiqueta.Text       = "PIN incorrecto — intente nuevamente:";
                    etiqueta.Foreground = Brushes.Salmon;
                }
            }

            btnAcceder.Click += Verificar;

            pinBox.KeyDown += (_, e) =>
            {
                if (e.Key == Key.Enter)  btnAcceder.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                if (e.Key == Key.Escape) ventana.Close();
            };

            panel.Children.Add(titulo);
            panel.Children.Add(etiqueta);
            panel.Children.Add(pinBox);
            panel.Children.Add(btnAcceder);

            ventana.Content = panel;
            ventana.Loaded += (_, _) => pinBox.Focus();
            ventana.ShowDialog();
        }

        public void ActualizarConfig(int modifiers, int key, string pin)
        {
            _app.Dispatcher.Invoke(() =>
            {
                if (_hwnd != null)
                {
                    UnregisterHotKey(_hwnd.Handle, HOTKEY_ID);
                }

                _config.BackdoorModifiers = modifiers;
                _config.BackdoorKey = key;
                _config.BackdoorPin = pin;
                _config.Guardar();

                if (_hwnd != null)
                {
                    bool ok = RegisterHotKey(_hwnd.Handle, HOTKEY_ID, modifiers, key);
                    if (!ok)
                        System.Diagnostics.Debug.WriteLine($"[Backdoor] No se pudo re-registrar el atajo (Mod: {modifiers}, Key: {key}).");
                    else
                        System.Diagnostics.Debug.WriteLine($"[Backdoor] Atajo re-registrado exitosamente (Mod: {modifiers}, Key: {key}).");
                }
            });
        }

        public void Dispose()
        {
            if (_disposed) return;
            _disposed = true;

            if (_hwnd != null)
            {
                UnregisterHotKey(_hwnd.Handle, HOTKEY_ID);
                _hwnd.Dispose();
                _hwnd = null;
            }
        }
    }
}
