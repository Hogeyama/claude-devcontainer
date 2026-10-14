using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

public class BrowserHost : Form {
    [DllImport("user32.dll",SetLastError=true)] static extern bool SetProcessDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")] static extern uint GetDpiForWindow(IntPtr hwnd);
    [DllImport("user32.dll")] static extern IntPtr GetWindowDpiAwarenessContext(IntPtr hwnd);
    [DllImport("user32.dll")] static extern bool AreDpiAwarenessContextsEqual(IntPtr first,IntPtr second);
    readonly WebView2 view=new WebView2();
    readonly string root,url;
    readonly CoreWebView2Environment sharedEnvironment;
    readonly TaskCompletionSource<bool> ready=new TaskCompletionSource<bool>();
    static readonly List<BrowserHost> windows=new List<BrowserHost>();
    public BrowserHost(string data,string address,CoreWebView2Environment environment=null) {
        root=data; url=address; sharedEnvironment=environment;
        Text="code-server";
        AutoScaleMode=AutoScaleMode.Dpi; Width=1280; Height=900;
        view.Dock=DockStyle.Fill; Controls.Add(view); windows.Add(this);
        Shown+=async (s,e)=>await Initialize();
    }
    async Task Initialize() {
        try {
            Log("dpi="+GetDpiForWindow(Handle)+" perMonitorV2="+AreDpiAwarenessContextsEqual(GetWindowDpiAwarenessContext(Handle),new IntPtr(-4)));
            var options=new CoreWebView2EnvironmentOptions("--proxy-server=http://127.0.0.1:18086 --proxy-bypass-list=<-loopback> --disable-background-networking --disable-quic");
            var environment=sharedEnvironment??await CoreWebView2Environment.CreateAsync(null,Path.Combine(root,"WebViewProfile"),options);
            await view.EnsureCoreWebView2Async(environment);
            var core=view.CoreWebView2;
            core.Settings.AreHostObjectsAllowed=false;
            core.Settings.IsWebMessageEnabled=false;
            core.Settings.IsPasswordAutosaveEnabled=false;
            core.Settings.IsGeneralAutofillEnabled=false;
            core.Settings.AreDevToolsEnabled=false;
            core.LaunchingExternalUriScheme+=(sender,args)=> { args.Cancel=true; Log("external-scheme-blocked "+new Uri(args.Uri).Scheme); };
            core.DownloadStarting+=(sender,args)=> { args.Cancel=true; Log("download-blocked"); };
            core.PermissionRequested+=(sender,args)=> { args.State=CoreWebView2PermissionState.Deny; };
            core.NewWindowRequested+=async (sender,args)=> {
                args.Handled=true;
                if(!Allowed(args.Uri)) {Log("new-window-blocked");return;}
                var deferral=args.GetDeferral();
                try {
                    var child=new BrowserHost(root,null,environment); child.Show();
                    await child.ready.Task;
                    // Reuse the requested browsing context, preserving form POST bodies.
                    args.NewWindow=child.view.CoreWebView2;
                    Log("new-window-contained");
                } catch(Exception ex) {Log("new-window-failed "+ex.GetType().Name);}
                finally {deferral.Complete();}
            };
            core.NavigationStarting+=(sender,args)=> { if(!Allowed(args.Uri)) { args.Cancel=true; Log("navigation-blocked"); } };
            core.FrameNavigationStarting+=(sender,args)=> { if(!Allowed(args.Uri)) args.Cancel=true; };
            core.NavigationCompleted+= (sender,args)=> {
                Log("navigation-completed success="+args.IsSuccess+" status="+args.WebErrorStatus);
            };
            Log("ready runtime="+environment.BrowserVersionString+" browserPid="+core.BrowserProcessId);
            ready.SetResult(true);
            if(url!=null) core.Navigate(url);
        } catch(Exception ex) { Log(ex.ToString()); ready.TrySetException(ex); Text="code-server initialization failed — see host.log"; }
    }
    static bool Allowed(string address) { Uri u; return Uri.TryCreate(address,UriKind.Absolute,out u) && (u.Scheme=="http" || u.Scheme=="https" || address=="about:blank"); }
    void Log(string message) {
        File.AppendAllText(Path.Combine(root,"host.log"),DateTime.UtcNow.ToString("o")+" "+message+Environment.NewLine);
    }
    [STAThread] public static void Main(string[] args) {
        if(BrowserAppContainer.Inspect((uint)System.Diagnostics.Process.GetCurrentProcess().Id)!=CodeServerIdentity.Sid) {
            MessageBox.Show("Launch this browser through launch.ps1; the dedicated AppContainer is required.");
            Environment.ExitCode=1; return;
        }
        bool dpi=SetProcessDpiAwarenessContext(new IntPtr(-4));
        File.AppendAllText(Path.Combine(args[0],"host.log"),"SetProcessDpiAwarenessContext="+dpi+" error="+Marshal.GetLastWin32Error()+Environment.NewLine);
        Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new BrowserHost(args[0],args.Length>1?args[1]:"about:blank"));
    }
}
