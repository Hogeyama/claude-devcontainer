// The WebView2 window that shows code-server. launch.ps1 starts it in the
// dedicated AppContainer; it refuses to run anywhere else.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

public class BrowserHost : Form {
    static readonly IntPtr DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = new IntPtr(-4);
    // Every connection goes to the browser proxy, loopback included; WFP
    // blocks anything else anyway (ProxyFirewall.cs).
    const string BrowserArguments = "--proxy-server=http://127.0.0.1:18086 --proxy-bypass-list=<-loopback>"
        + " --disable-background-networking --disable-quic";

    [DllImport("user32.dll", SetLastError = true)]
    static extern bool SetProcessDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")]
    static extern uint GetDpiForWindow(IntPtr hwnd);
    [DllImport("user32.dll")]
    static extern IntPtr GetWindowDpiAwarenessContext(IntPtr hwnd);
    [DllImport("user32.dll")]
    static extern bool AreDpiAwarenessContextsEqual(IntPtr first, IntPtr second);

    // Keeps every window referenced while it is open.
    static readonly List<BrowserHost> windows = new List<BrowserHost>();

    readonly WebView2 view = new WebView2();
    readonly string root;  // Data directory: WebView2 profile and host.log.
    readonly string url;   // null for a window that a page opens.
    // A window that a page opens must share its opener's environment.
    readonly CoreWebView2Environment sharedEnvironment;
    readonly TaskCompletionSource<bool> ready = new TaskCompletionSource<bool>();

    public BrowserHost(string data, string address, CoreWebView2Environment environment = null) {
        root = data;
        url = address;
        sharedEnvironment = environment;
        Text = "code-server";
        AutoScaleMode = AutoScaleMode.Dpi;
        Width = 1280;
        Height = 900;
        view.Dock = DockStyle.Fill;
        Controls.Add(view);
        windows.Add(this);
        Shown += async (sender, args) => await Initialize();
    }

    async Task Initialize() {
        try {
            bool perMonitorV2 = AreDpiAwarenessContextsEqual(GetWindowDpiAwarenessContext(Handle), DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
            Log("dpi=" + GetDpiForWindow(Handle) + " perMonitorV2=" + perMonitorV2);
            var environment = sharedEnvironment ?? await CoreWebView2Environment.CreateAsync(
                null, Path.Combine(root, "WebViewProfile"), new CoreWebView2EnvironmentOptions(BrowserArguments));
            await view.EnsureCoreWebView2Async(environment);
            var core = view.CoreWebView2;

            // Pages get no bridge to this process and nothing is remembered.
            core.Settings.AreHostObjectsAllowed = false;
            core.Settings.IsWebMessageEnabled = false;
            core.Settings.IsPasswordAutosaveEnabled = false;
            core.Settings.IsGeneralAutofillEnabled = false;
            core.Settings.AreDevToolsEnabled = false;

            // Nothing leaves the window: no other apps, files or permissions.
            core.LaunchingExternalUriScheme += (sender, args) => {
                args.Cancel = true;
                Log("external-scheme-blocked " + new Uri(args.Uri).Scheme);
            };
            core.DownloadStarting += (sender, args) => {
                args.Cancel = true;
                Log("download-blocked");
            };
            core.PermissionRequested += (sender, args) => {
                args.State = CoreWebView2PermissionState.Deny;
            };

            // A new window is another BrowserHost in this process, so it stays
            // in the AppContainer, instead of one that WebView2 would open.
            core.NewWindowRequested += async (sender, args) => {
                args.Handled = true;
                if (!Allowed(args.Uri)) {
                    Log("new-window-blocked");
                    return;
                }
                var deferral = args.GetDeferral();
                try {
                    var child = new BrowserHost(root, null, environment);
                    child.Show();
                    await child.ready.Task;
                    // Reuse the requested browsing context, preserving form POST bodies.
                    args.NewWindow = child.view.CoreWebView2;
                    Log("new-window-contained");
                } catch (Exception ex) {
                    Log("new-window-failed " + ex.GetType().Name);
                } finally {
                    deferral.Complete();
                }
            };

            core.NavigationStarting += (sender, args) => {
                if (!Allowed(args.Uri)) {
                    args.Cancel = true;
                    Log("navigation-blocked");
                }
            };
            core.FrameNavigationStarting += (sender, args) => {
                if (!Allowed(args.Uri)) args.Cancel = true;
            };
            core.NavigationCompleted += (sender, args) => {
                Log("navigation-completed success=" + args.IsSuccess + " status=" + args.WebErrorStatus);
            };

            Log("ready runtime=" + environment.BrowserVersionString + " browserPid=" + core.BrowserProcessId);
            ready.SetResult(true);
            if (url != null) core.Navigate(url);
        } catch (Exception ex) {
            Log(ex.ToString());
            ready.TrySetException(ex);
            Text = "code-server initialization failed — see host.log";
        }
    }

    // http, https and about:blank only; the proxy decides which hosts.
    static bool Allowed(string address) {
        Uri uri;
        if (!Uri.TryCreate(address, UriKind.Absolute, out uri)) return false;
        return uri.Scheme == "http" || uri.Scheme == "https" || address == "about:blank";
    }

    void Log(string message) {
        File.AppendAllText(Path.Combine(root, "host.log"), DateTime.UtcNow.ToString("o") + " " + message + Environment.NewLine);
    }

    // args: the data directory, then the URL to open (about:blank if none).
    [STAThread]
    public static void Main(string[] args) {
        // CodeServerIdentity is generated by build-webview.ps1.
        if (BrowserAppContainer.Inspect((uint)Process.GetCurrentProcess().Id) != CodeServerIdentity.Sid) {
            MessageBox.Show("Launch this browser through launch.ps1; the dedicated AppContainer is required.");
            Environment.ExitCode = 1;
            return;
        }
        bool dpi = SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
        File.AppendAllText(Path.Combine(args[0], "host.log"),
            "SetProcessDpiAwarenessContext=" + dpi + " error=" + Marshal.GetLastWin32Error() + Environment.NewLine);
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new BrowserHost(args[0], args.Length > 1 ? args[1] : "about:blank"));
    }
}
