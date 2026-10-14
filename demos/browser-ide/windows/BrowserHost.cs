using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

public partial class BrowserHost : Form {
    [DllImport("user32.dll",SetLastError=true)] static extern bool SetProcessDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")] static extern uint GetDpiForWindow(IntPtr hwnd);
    [DllImport("user32.dll")] static extern IntPtr GetWindowDpiAwarenessContext(IntPtr hwnd);
    [DllImport("user32.dll")] static extern bool AreDpiAwarenessContextsEqual(IntPtr first,IntPtr second);
    readonly WebView2 view=new WebView2();
    readonly string root,url,mode;
    readonly CoreWebView2Environment sharedEnvironment;
    readonly TaskCompletionSource<bool> ready=new TaskCompletionSource<bool>();
    static readonly List<BrowserHost> windows=new List<BrowserHost>();
    static readonly List<string> events=new List<string>();
    static readonly JavaScriptSerializer json=new JavaScriptSerializer();
    bool probeStarted;
    public BrowserHost(string data,string address,string testMode=null,CoreWebView2Environment environment=null) {
        root=data; url=address; mode=testMode; sharedEnvironment=environment;
        Text="Browser IDE — AppContainer / native DPI";
        AutoScaleMode=AutoScaleMode.Dpi; Width=1280; Height=900;
        view.Dock=DockStyle.Fill; Controls.Add(view); windows.Add(this);
        Shown+=async (s,e)=>await Initialize();
    }
    async Task Initialize() {
        try {
            Log("dpi="+GetDpiForWindow(Handle)+" perMonitorV2="+AreDpiAwarenessContextsEqual(GetWindowDpiAwarenessContext(Handle),new IntPtr(-4)));
            var options=new CoreWebView2EnvironmentOptions("--proxy-server=http://127.0.0.1:18081 --proxy-bypass-list=<-loopback> --disable-background-networking --disable-quic");
            var environment=sharedEnvironment??await CoreWebView2Environment.CreateAsync(null,Path.Combine(root,mode==null?"WebViewProfile":"ProbeProfile-"+Guid.NewGuid().ToString("N")),options);
            await view.EnsureCoreWebView2Async(environment);
            var core=view.CoreWebView2;
            core.FrameCreated+=(sender,args)=>TrackFrame(args.Frame);
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
                    var child=new BrowserHost(root,null,null,environment); child.Show();
                    await child.ready.Task;
                    // Reuse the requested browsing context, preserving form POST bodies.
                    args.NewWindow=child.view.CoreWebView2;
                    Log("new-window-contained");
                } catch(Exception ex) {Log("new-window-failed "+ex.GetType().Name);}
                finally {deferral.Complete();}
            };
            core.NavigationStarting+=(sender,args)=> { if(!Allowed(args.Uri)) { args.Cancel=true; Log("navigation-blocked"); } };
            core.FrameNavigationStarting+=(sender,args)=> { if(!Allowed(args.Uri)) args.Cancel=true; };
            core.NavigationCompleted+=async (sender,args)=> {
                Log("navigation-completed success="+args.IsSuccess+" status="+args.WebErrorStatus);
                if(mode!=null && !probeStarted) {probeStarted=true; await Probe();}
            };
            Log("ready runtime="+environment.BrowserVersionString+" browserPid="+core.BrowserProcessId);
            ready.SetResult(true);
            if(url!=null) core.Navigate(url);
        } catch(Exception ex) { Log(ex.ToString()); ready.TrySetException(ex); Text="Browser IDE initialization failed — see host.log"; }
    }
    async Task Probe() {
        bool success=false; string error=null;
        try {
            if(mode.StartsWith("ide-")) {await ProbeIde();success=true;return;}
            await Task.Delay(1000);
            var core=view.CoreWebView2;
            foreach(string id in new[]{"form","link","redirect","fetch","beacon"}) {
                string expression="document.getElementById("+json.Serialize(id)+").click()";
                await core.CallDevToolsProtocolMethodAsync("Runtime.evaluate",json.Serialize(new{expression=expression,userGesture=true}));
                await Task.Delay(400);
            }
            await core.ExecuteScriptAsync("location.href='/auto'");
            await Task.Delay(1500);
            await core.CallDevToolsProtocolMethodAsync("Runtime.evaluate",json.Serialize(new{expression="var a=document.createElement('a');a.href='browseridedemo:probe';document.body.appendChild(a);a.click()",userGesture=true}));
            await Task.Delay(500);
            if(!events.Contains("external-scheme-blocked browseridedemo") && !events.Contains("navigation-blocked")) throw new Exception("External scheme was not rejected");
            int contained=events.FindAll(x=>x=="new-window-contained").Count;
            if(contained!=3) throw new Exception("Expected three contained windows; got "+contained);
            foreach(var window in windows) {
                var c=window.view.CoreWebView2;
                if(c==null || !c.Source.StartsWith("http://receiver.localhost")) continue;
                string body=await c.ExecuteScriptAsync("document.body.innerText");
                if(mode=="restricted" && !body.Contains("Blocked by demo proxy")) throw new Exception("Popup did not show proxy rejection");
            }
            using(var file=File.Create(Path.Combine(root,"preview.png"))) await core.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,file);
            success=true;
        } catch(Exception ex) { error=ex.ToString(); }
        finally {
            File.WriteAllText(Path.Combine(root,"browser-probe.json"),json.Serialize(new {passed=success,error=error,mode=mode,events=events,dpi=GetDpiForWindow(Handle),perMonitorV2=AreDpiAwarenessContextsEqual(GetWindowDpiAwarenessContext(Handle),new IntPtr(-4))}));
            Environment.ExitCode=success?0:1;
            Application.Exit();
        }
    }
    static bool Allowed(string address) { Uri u; return Uri.TryCreate(address,UriKind.Absolute,out u) && (u.Scheme=="http" || u.Scheme=="https" || address=="about:blank"); }
    void Log(string message) {
        events.Add(message);
        File.AppendAllText(Path.Combine(root,"host.log"),DateTime.UtcNow.ToString("o")+" "+message+Environment.NewLine);
    }
    [STAThread] public static void Main(string[] args) {
        if(BrowserAppContainer.Inspect((uint)System.Diagnostics.Process.GetCurrentProcess().Id)!=BrowserDemoIdentity.Sid) {
            MessageBox.Show("Launch this browser through launch.ps1; the dedicated AppContainer is required.");
            Environment.ExitCode=1; return;
        }
        bool dpi=SetProcessDpiAwarenessContext(new IntPtr(-4));
        File.AppendAllText(Path.Combine(args[0],"host.log"),"SetProcessDpiAwarenessContext="+dpi+" error="+Marshal.GetLastWin32Error()+Environment.NewLine);
        Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new BrowserHost(args[0],args.Length>1?args[1]:"about:blank",args.Length>2?args[2]:null));
    }
}
