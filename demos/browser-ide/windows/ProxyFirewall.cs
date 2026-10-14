// Windows WFP, no third-party driver. Persistent, package-SID-scoped block rules.
using System;
using System.Runtime.InteropServices;
using System.Security.Principal;

public static class ProxyFirewall {
    [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] struct Display { public string name,description; }
    [StructLayout(LayoutKind.Sequential)] struct Blob { public uint size; public IntPtr data; }
    [StructLayout(LayoutKind.Explicit,Size=16)] struct Value { [FieldOffset(0)] public uint type; [FieldOffset(8)] public ulong number; [FieldOffset(8)] public IntPtr pointer; }
    [StructLayout(LayoutKind.Sequential)] struct Condition { public Guid field; public uint match; public Value value; }
    [StructLayout(LayoutKind.Sequential)] struct Action { public uint type; public Guid key; }
    [StructLayout(LayoutKind.Explicit,Size=16)] struct Context { [FieldOffset(0)] public ulong raw; }
    [StructLayout(LayoutKind.Sequential)] struct Filter {
        public Guid key; public Display display; public uint flags; public IntPtr provider;
        public Blob providerData; public Guid layer,subLayer; public Value weight;
        public uint count; public IntPtr conditions; public Action action; public Context context;
        public IntPtr reserved; public ulong id; public Value effectiveWeight;
    }
    [StructLayout(LayoutKind.Sequential)] struct SubLayer { public Guid key; public Display display; public uint flags; public IntPtr provider; public Blob data; public ushort weight; }
    [DllImport("fwpuclnt.dll",CharSet=CharSet.Unicode)] static extern uint FwpmEngineOpen0(string server,uint auth,IntPtr identity,IntPtr session,out IntPtr engine);
    [DllImport("fwpuclnt.dll")] static extern uint FwpmEngineClose0(IntPtr engine);
    [DllImport("fwpuclnt.dll")] static extern uint FwpmTransactionBegin0(IntPtr engine,uint flags);
    [DllImport("fwpuclnt.dll")] static extern uint FwpmTransactionCommit0(IntPtr engine);
    [DllImport("fwpuclnt.dll")] static extern uint FwpmTransactionAbort0(IntPtr engine);
    [DllImport("fwpuclnt.dll")] static extern uint FwpmSubLayerAdd0(IntPtr engine,ref SubLayer layer,IntPtr security);
    [DllImport("fwpuclnt.dll")] static extern uint FwpmSubLayerDeleteByKey0(IntPtr engine,ref Guid key);
    [DllImport("fwpuclnt.dll")] static extern uint FwpmFilterAdd0(IntPtr engine,ref Filter filter,IntPtr security,out ulong id);
    [DllImport("fwpuclnt.dll")] static extern uint FwpmFilterDeleteByKey0(IntPtr engine,ref Guid key);
    [DllImport("fwpuclnt.dll")] static extern uint FwpmFilterGetByKey0(IntPtr engine,ref Guid key,out IntPtr filter);
    [DllImport("fwpuclnt.dll")] static extern void FwpmFreeMemory0(ref IntPtr memory);
    [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool ConvertStringSecurityDescriptorToSecurityDescriptor(string text,uint revision,out IntPtr descriptor,out uint size);
    [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr memory);
    static readonly Guid Sub=new Guid("5ae3f122-41b5-4bcb-9281-31c513c6f220");
    static readonly Guid V4=new Guid("c38d57d1-05a7-4c33-904f-7fbceee60e82");
    static readonly Guid V6=new Guid("4a72393b-319f-44bc-84c3-ba54dcb3b6b4");
    static readonly Guid Package=new Guid("71bc78fa-f17c-4997-a602-6abb261f351c");
    static readonly Guid Address=new Guid("b235ae9a-1d64-49b8-a44c-5ff3d9095045");
    static readonly Guid Port=new Guid("c35a604d-d22b-4e1a-91b4-68f674ee674b");
    static readonly Guid Protocol=new Guid("3971ef2b-623e-4f9a-8cb1-6e79b806b9a7");
    static Guid Key(int i) { return new Guid("5ae3f122-41b5-4bcb-9281-31c513c6f22"+(i+1)); }
    static void Check(uint error,string step) { if(error!=0) throw new Exception(step+": 0x"+error.ToString("x8")); }
    static IntPtr Open() { IntPtr e; Check(FwpmEngineOpen0(null,10,IntPtr.Zero,IntPtr.Zero,out e),"Open WFP"); return e; }
    static void Add(IntPtr engine,int index,Guid layer,Condition[] conditions,string name) {
        int size=Marshal.SizeOf(typeof(Condition)); IntPtr values=Marshal.AllocHGlobal(size*conditions.Length);
        try {
            for(int i=0;i<conditions.Length;i++) Marshal.StructureToPtr(conditions[i],IntPtr.Add(values,size*i),false);
            var f=new Filter { key=Key(index),display=new Display{name="Browser IDE: "+name},flags=1,layer=layer,subLayer=Sub,
                weight=new Value{type=1,number=15},count=(uint)conditions.Length,conditions=values,action=new Action{type=0x1001} };
            IntPtr sd; uint sdSize;
            string user=WindowsIdentity.GetCurrent().User.Value;
            if(!ConvertStringSecurityDescriptorToSecurityDescriptor("D:(A;;GA;;;SY)(A;;GA;;;BA)(A;;GR;;;"+user+")",1,out sd,out sdSize)) throw new System.ComponentModel.Win32Exception();
            try { ulong id; Check(FwpmFilterAdd0(engine,ref f,sd,out id),"Add "+name); }
            finally { LocalFree(sd); }
        } finally { Marshal.FreeHGlobal(values); }
    }
    public static void Install(string sidString) {
        var sid=new SecurityIdentifier(sidString); byte[] bytes=new byte[sid.BinaryLength]; sid.GetBinaryForm(bytes,0);
        IntPtr sidPtr=Marshal.AllocHGlobal(bytes.Length); Marshal.Copy(bytes,0,sidPtr,bytes.Length);
        IntPtr engine=Open(); bool transaction=false;
        try {
            Check(FwpmTransactionBegin0(engine,0),"Begin transaction"); transaction=true;
            var sub=new SubLayer{key=Sub,display=new Display{name="Browser IDE dedicated AppContainer"},flags=1,weight=65535};
            uint e=FwpmSubLayerAdd0(engine,ref sub,IntPtr.Zero); if(e!=0x80320009) Check(e,"Add sublayer");
            // Replace only this demo's four deterministic filter keys, atomically.
            for(int i=0;i<4;i++) { Guid key=Key(i); e=FwpmFilterDeleteByKey0(engine,ref key); if(e!=0x80320003) Check(e,"Replace filter"); }
            var package=new Condition{field=Package,value=new Value{type=13,pointer=sidPtr}};
            Add(engine,0,V4,new[]{package,new Condition{field=Address,match=10,value=new Value{type=3,number=0x7f000001}}},"block non-proxy IPv4 addresses");
            Add(engine,1,V4,new[]{package,new Condition{field=Port,match=10,value=new Value{type=2,number=18081}}},"block non-proxy ports");
            Add(engine,2,V4,new[]{package,new Condition{field=Protocol,match=10,value=new Value{type=1,number=6}}},"block non-TCP traffic");
            Add(engine,3,V6,new[]{package},"block IPv6");
            Check(FwpmTransactionCommit0(engine),"Commit transaction"); transaction=false;
        } finally { if(transaction) FwpmTransactionAbort0(engine); FwpmEngineClose0(engine); Marshal.FreeHGlobal(sidPtr); }
    }
    public static string Verify(string expectedSid) {
        IntPtr engine=Open();
        try {
            for(int i=0;i<4;i++) {
                Guid key=Key(i); IntPtr data; Check(FwpmFilterGetByKey0(engine,ref key,out data),"Find filter "+i);
                try {
                    var f=(Filter)Marshal.PtrToStructure(data,typeof(Filter));
                    if(f.action.type!=0x1001 || (f.flags&0x21)!=1 || f.subLayer!=Sub || f.layer!=(i==3?V6:V4) || f.count!=(i==3?1:2)) throw new Exception("Unexpected filter configuration");
                    var p=(Condition)Marshal.PtrToStructure(f.conditions,typeof(Condition));
                    if(p.field!=Package || p.match!=0 || p.value.type!=13 || new SecurityIdentifier(p.value.pointer).Value!=expectedSid) throw new Exception("Wrong package condition");
                    if(i<3) {
                        var c=(Condition)Marshal.PtrToStructure(IntPtr.Add(f.conditions,Marshal.SizeOf(typeof(Condition))),typeof(Condition));
                        Guid field=i==0?Address:(i==1?Port:Protocol);
                        uint type=i==0?3u:(i==1?2u:1u); ulong number=i==0?0x7f000001ul:(i==1?18081ul:6ul);
                        ulong mask=i==0?0xfffffffful:(i==1?0xfffful:0xfful);
                        if(c.field!=field || c.match!=10 || c.value.type!=type || (c.value.number&mask)!=number) throw new Exception("Wrong endpoint condition");
                    }
                } finally {FwpmFreeMemory0(ref data);}
            }
            return "four persistent block filters verified for "+expectedSid;
        } finally { FwpmEngineClose0(engine); }
    }
    public static void Remove() {
        IntPtr engine=Open(); try { for(int i=0;i<4;i++) {Guid key=Key(i); uint e=FwpmFilterDeleteByKey0(engine,ref key); if(e!=0x80320003) Check(e,"Delete filter");} Guid sub=Sub; uint r=FwpmSubLayerDeleteByKey0(engine,ref sub); if(r!=0x80320007) Check(r,"Delete sublayer"); } finally {FwpmEngineClose0(engine);}
    }
}
