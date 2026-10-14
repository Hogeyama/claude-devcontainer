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
    public static string Launch(string name,string app,string arguments,int seconds) {
        IntPtr sid; int hr=DeriveAppContainerSidFromAppContainerName(name,out sid); if(hr<0) Marshal.ThrowExceptionForHR(hr);
        IntPtr size=IntPtr.Zero; InitializeProcThreadAttributeList(IntPtr.Zero,1,0,ref size);
        IntPtr list=Marshal.AllocHGlobal(size), cap=Marshal.AllocHGlobal(Marshal.SizeOf(typeof(SecurityCapabilities)));
        ProcessInfo p=new ProcessInfo(); bool initialized=false;
        try {
            if(!InitializeProcThreadAttributeList(list,1,0,ref size)) throw new Win32Exception();
            initialized=true;
            Marshal.StructureToPtr(new SecurityCapabilities{Sid=sid},cap,false);
            if(!UpdateProcThreadAttribute(list,0,new IntPtr(0x20009),cap,new IntPtr(Marshal.SizeOf(typeof(SecurityCapabilities))),IntPtr.Zero,IntPtr.Zero)) throw new Win32Exception();
            var si=new StartupEx(); si.startup.cb=Marshal.SizeOf(typeof(StartupEx)); si.attributes=list;
            if(!CreateProcess(app,new StringBuilder("\""+app+"\" "+arguments),IntPtr.Zero,IntPtr.Zero,false,0x80000,IntPtr.Zero,Environment.GetFolderPath(Environment.SpecialFolder.System),ref si,out p)) throw new Win32Exception();
            int isContainer=-1; IntPtr token;
            if(OpenProcessToken(p.process,8,out token)) {
                IntPtr value=Marshal.AllocHGlobal(4); int needed;
                try { if(GetTokenInformation(token,29,value,4,out needed)) isContainer=Marshal.ReadInt32(value); } finally { Marshal.FreeHGlobal(value); CloseHandle(token); }
            }
            uint wait=WaitForSingleObject(p.process,(uint)(seconds*1000)); uint code; GetExitCodeProcess(p.process,out code);
            return "{\"pid\":"+p.pid+",\"tokenIsAppContainer\":"+isContainer+",\"waitResult\":"+wait+",\"exitCode\":"+code+"}";
        } finally {
            if(p.thread!=IntPtr.Zero) CloseHandle(p.thread);
            if(p.process!=IntPtr.Zero) CloseHandle(p.process);
            if(initialized) DeleteProcThreadAttributeList(list); Marshal.FreeHGlobal(list); Marshal.FreeHGlobal(cap); FreeSid(sid);
        }
    }
}
