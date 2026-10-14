using System;
using System.Text;
using System.Runtime.InteropServices;
using System.ComponentModel;

public static class BrowserAppContainer {
    [StructLayout(LayoutKind.Sequential)] struct SecurityCapabilities {
        public IntPtr Sid, Capabilities;
        public uint Count, Reserved;
    }
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] struct StartupInfo {
        public int cb; public string reserved, desktop, title;
        public uint x,y,cx,cy,xchars,ychars,fill,flags;
        public ushort show,reservedSize; public IntPtr reserved2,input,output,error;
    }
    [StructLayout(LayoutKind.Sequential)] struct StartupEx { public StartupInfo startup; public IntPtr attributes; }
    [StructLayout(LayoutKind.Sequential)] struct ProcessInfo { public IntPtr process, thread; public uint pid, tid; }
    [DllImport("userenv.dll", CharSet=CharSet.Unicode)] static extern int CreateAppContainerProfile(string name,string display,string description,IntPtr caps,uint count,out IntPtr sid);
    [DllImport("userenv.dll", CharSet=CharSet.Unicode)] static extern int DeriveAppContainerSidFromAppContainerName(string name,out IntPtr sid);
    [DllImport("advapi32.dll")] static extern IntPtr FreeSid(IntPtr sid);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool InitializeProcThreadAttributeList(IntPtr list,int count,int flags,ref IntPtr size);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool UpdateProcThreadAttribute(IntPtr list,uint flags,IntPtr attribute,IntPtr value,IntPtr size,IntPtr previous,IntPtr returned);
    [DllImport("kernel32.dll")] static extern void DeleteProcThreadAttributeList(IntPtr list);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool CreateProcess(string app,StringBuilder command,IntPtr pa,IntPtr ta,bool inherit,uint flags,IntPtr environment,string cwd,ref StartupEx info,out ProcessInfo process);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr handle,uint timeout);
    [DllImport("kernel32.dll")] static extern bool GetExitCodeProcess(IntPtr handle,out uint code);
    [DllImport("advapi32.dll", SetLastError=true)] static extern bool OpenProcessToken(IntPtr process,uint access,out IntPtr token);
    [DllImport("advapi32.dll", SetLastError=true)] static extern bool GetTokenInformation(IntPtr token,int kind,IntPtr info,int size,out int needed);
    [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr OpenProcess(uint access,bool inherit,uint pid);
    public static string Inspect(uint pid) {
        IntPtr process=OpenProcess(0x1000,false,pid),token;
        if(process==IntPtr.Zero) throw new Win32Exception();
        try {
            if(!OpenProcessToken(process,8,out token)) throw new Win32Exception();
            try {
                int needed; GetTokenInformation(token,31,IntPtr.Zero,0,out needed);
                IntPtr data=Marshal.AllocHGlobal(needed);
                try {
                    if(!GetTokenInformation(token,31,data,needed,out needed)) throw new Win32Exception();
                    IntPtr sid=Marshal.ReadIntPtr(data);
                    return sid==IntPtr.Zero ? "not-appcontainer" : new System.Security.Principal.SecurityIdentifier(sid).Value;
                } finally { Marshal.FreeHGlobal(data); }
            } finally { CloseHandle(token); }
        } finally { CloseHandle(process); }
    }
    public static string Profile(string name) {
        IntPtr sid; int hr=CreateAppContainerProfile(name,name,"Browser IDE standard Windows isolation experiment",IntPtr.Zero,0,out sid);
        if(hr<0) { if(hr!=unchecked((int)0x800700B7)) Marshal.ThrowExceptionForHR(hr); hr=DeriveAppContainerSidFromAppContainerName(name,out sid); if(hr<0) Marshal.ThrowExceptionForHR(hr); }
        try { return new System.Security.Principal.SecurityIdentifier(sid).Value; } finally { FreeSid(sid); }
    }
    [StructLayout(LayoutKind.Sequential)] struct SecurityAttributes { public int length; public IntPtr descriptor; public int inherit; }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern IntPtr CreateFile(string path,uint access,uint share,ref SecurityAttributes attributes,uint creation,uint flags,IntPtr template);
    public static string Launch(string name,string app,string arguments,int seconds) { return LaunchLogged(name,app,arguments,seconds,null); }
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool WaitForDebugEvent(IntPtr data,uint timeout);
    [DllImport("kernel32.dll")] static extern bool ContinueDebugEvent(uint pid,uint tid,uint status);
    [DllImport("kernel32.dll")] static extern bool ReadProcessMemory(IntPtr process,IntPtr address,byte[] data,int count,out IntPtr read);
    [DllImport("kernel32.dll")] static extern bool DebugActiveProcessStop(uint pid);
    public static string LaunchLogged(string name,string app,string arguments,int seconds,string log) { return LaunchDiagnostic(name,app,arguments,seconds,log,false); }
    public static string LaunchDiagnostic(string name,string app,string arguments,int seconds,string log,bool debug) {
        IntPtr sid; int hr=DeriveAppContainerSidFromAppContainerName(name,out sid); if(hr<0) Marshal.ThrowExceptionForHR(hr);
        IntPtr size=IntPtr.Zero; InitializeProcThreadAttributeList(IntPtr.Zero,1,0,ref size);
        IntPtr list=Marshal.AllocHGlobal(size), cap=Marshal.AllocHGlobal(Marshal.SizeOf(typeof(SecurityCapabilities)));
        ProcessInfo p=new ProcessInfo(); bool initialized=false; IntPtr output=IntPtr.Zero,input=IntPtr.Zero;
        try {
            if(!InitializeProcThreadAttributeList(list,1,0,ref size)) throw new Win32Exception();
            initialized=true;
            Marshal.StructureToPtr(new SecurityCapabilities{Sid=sid},cap,false);
            if(!UpdateProcThreadAttribute(list,0,new IntPtr(0x20009),cap,new IntPtr(Marshal.SizeOf(typeof(SecurityCapabilities))),IntPtr.Zero,IntPtr.Zero)) throw new Win32Exception();
            var si=new StartupEx(); si.startup.cb=Marshal.SizeOf(typeof(StartupEx)); si.attributes=list;
            if(log!=null) {
                var attributes=new SecurityAttributes {length=Marshal.SizeOf(typeof(SecurityAttributes)),inherit=1};
                output=CreateFile(log,0x40000000,3,ref attributes,2,0,IntPtr.Zero);
                input=CreateFile("NUL",0x80000000,3,ref attributes,3,0,IntPtr.Zero);
                if(output==new IntPtr(-1) || input==new IntPtr(-1)) throw new Win32Exception();
                si.startup.flags=0x100; si.startup.output=output; si.startup.error=output; si.startup.input=input;
            }
            if(!CreateProcess(app,new StringBuilder("\""+app+"\" "+arguments),IntPtr.Zero,IntPtr.Zero,log!=null,(uint)(0x80000|(debug?2:0)),IntPtr.Zero,Environment.GetFolderPath(Environment.SpecialFolder.System),ref si,out p)) throw new Win32Exception();
            int isContainer=-1; IntPtr token;
            if(OpenProcessToken(p.process,8,out token)) {
                IntPtr value=Marshal.AllocHGlobal(4); int needed;
                try { if(GetTokenInformation(token,29,value,4,out needed)) isContainer=Marshal.ReadInt32(value); } finally { Marshal.FreeHGlobal(value); CloseHandle(token); }
            }
            if(debug) {
                IntPtr ev=Marshal.AllocHGlobal(176); var end=DateTime.UtcNow.AddSeconds(seconds); bool first=true,exited=false;
                try { while(DateTime.UtcNow<end) {
                    if(!WaitForDebugEvent(ev,500)) continue;
                    int kind=Marshal.ReadInt32(ev); uint dp=(uint)Marshal.ReadInt32(ev,4),dt=(uint)Marshal.ReadInt32(ev,8); uint status=0x10002;
                    string message="event="+kind;
                    if(kind==1) {
                        uint exception=(uint)Marshal.ReadInt32(ev,16); message+=" exception=0x"+exception.ToString("x")+" address=0x"+Marshal.ReadIntPtr(ev,32).ToInt64().ToString("x")+" firstChance="+Marshal.ReadInt32(ev,168);
                        if(exception==0x80000003 && first) first=false; else status=0x80010001;
                    }
                    if(kind==3) message+=" imageBase=0x"+Marshal.ReadIntPtr(ev,40).ToInt64().ToString("x");
                    if(kind==8) {
                        bool unicode=Marshal.ReadInt16(ev,24)!=0; int count=(ushort)Marshal.ReadInt16(ev,26)*(unicode?2:1); byte[] bytes=new byte[count]; IntPtr read;
                        if(ReadProcessMemory(p.process,Marshal.ReadIntPtr(ev,16),bytes,count,out read)) message+=" "+(unicode?Encoding.Unicode:Encoding.Default).GetString(bytes);
                    }
                    if(kind==1 || kind==3 || kind==8 || kind==5) System.IO.File.AppendAllText(log+".debug",message+Environment.NewLine);
                    ContinueDebugEvent(dp,dt,status);
                    if(kind==5) {exited=true;break;}
                } } finally {if(!exited) DebugActiveProcessStop(p.pid); Marshal.FreeHGlobal(ev);}
            }
            uint wait=WaitForSingleObject(p.process,(uint)(seconds*1000)); uint code; GetExitCodeProcess(p.process,out code);
            return "{\"pid\":"+p.pid+",\"tokenIsAppContainer\":"+isContainer+",\"waitResult\":"+wait+",\"exitCode\":"+code+"}";
        } finally {
            if(output!=IntPtr.Zero && output!=new IntPtr(-1)) CloseHandle(output);
            if(input!=IntPtr.Zero && input!=new IntPtr(-1)) CloseHandle(input);
            if(p.thread!=IntPtr.Zero) CloseHandle(p.thread);
            if(p.process!=IntPtr.Zero) CloseHandle(p.process);
            if(initialized) DeleteProcThreadAttributeList(list); Marshal.FreeHGlobal(list); Marshal.FreeHGlobal(cap); FreeSid(sid);
        }
    }
}
