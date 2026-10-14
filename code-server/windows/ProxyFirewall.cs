// Restricts the dedicated AppContainer's outbound connections to the browser
// proxy (TCP 127.0.0.1:18086) with Windows Filtering Platform (WFP) filters, so
// no third-party driver is needed. Filters are persistent and match only the
// AppContainer's package SID.
//
// A connection is blocked when any one of four block filters matches, which
// leaves only TCP to 127.0.0.1:18086:
//   0: IPv4 to an address other than 127.0.0.1
//   1: IPv4 to a port other than 18086
//   2: IPv4 with a protocol other than TCP
//   3: IPv6, all of it
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Principal;

public static class ProxyFirewall {
    const uint ProxyAddress = 0x7f000001;  // 127.0.0.1
    const uint ProxyPort = 18086;
    const uint Tcp = 6;

    // fwptypes.h / fwpmtypes.h / fwpmu.h / rpcdce.h.
    const uint RPC_C_AUTHN_WINNT = 10;
    const uint FWP_UINT8 = 1, FWP_UINT16 = 2, FWP_UINT32 = 3, FWP_SID = 13;  // FWP_DATA_TYPE
    const uint FWP_MATCH_EQUAL = 0, FWP_MATCH_NOT_EQUAL = 10;                 // FWP_MATCH_TYPE
    const uint FWP_ACTION_BLOCK = 0x1001;
    const uint FWPM_FLAG_PERSISTENT = 0x01;    // Filters and sublayers alike.
    const uint FWPM_FILTER_FLAG_DISABLED = 0x20;
    const uint FWP_E_FILTER_NOT_FOUND = 0x80320003;
    const uint FWP_E_SUBLAYER_NOT_FOUND = 0x80320007;
    const uint FWP_E_ALREADY_EXISTS = 0x80320009;

    // Fixed keys, so that Install replaces and Remove deletes exactly these.
    static readonly Guid SubLayerKey = new Guid("8b834b46-6b22-43c8-8be3-88f97e99c320");
    static readonly Guid[] FilterKeys = {
        new Guid("8b834b46-6b22-43c8-8be3-88f97e99c321"),
        new Guid("8b834b46-6b22-43c8-8be3-88f97e99c322"),
        new Guid("8b834b46-6b22-43c8-8be3-88f97e99c323"),
        new Guid("8b834b46-6b22-43c8-8be3-88f97e99c324"),
    };
    // Well-known WFP layer and condition keys.
    static readonly Guid FWPM_LAYER_ALE_AUTH_CONNECT_V4 = new Guid("c38d57d1-05a7-4c33-904f-7fbceee60e82");
    static readonly Guid FWPM_LAYER_ALE_AUTH_CONNECT_V6 = new Guid("4a72393b-319f-44bc-84c3-ba54dcb3b6b4");
    static readonly Guid FWPM_CONDITION_ALE_PACKAGE_ID = new Guid("71bc78fa-f17c-4997-a602-6abb261f351c");
    static readonly Guid FWPM_CONDITION_IP_REMOTE_ADDRESS = new Guid("b235ae9a-1d64-49b8-a44c-5ff3d9095045");
    static readonly Guid FWPM_CONDITION_IP_REMOTE_PORT = new Guid("c35a604d-d22b-4e1a-91b4-68f674ee674b");
    static readonly Guid FWPM_CONDITION_IP_PROTOCOL = new Guid("3971ef2b-623e-4f9a-8cb1-6e79b806b9a7");

    // Filters 0-2 each have one condition besides the package; what it checks.
    struct Endpoint {
        public Guid field;
        public uint type;
        public ulong value, mask;
        public string name;
    }
    static readonly Endpoint[] Endpoints = {
        new Endpoint { field = FWPM_CONDITION_IP_REMOTE_ADDRESS, type = FWP_UINT32, value = ProxyAddress, mask = 0xffffffff, name = "block non-proxy IPv4 addresses" },
        new Endpoint { field = FWPM_CONDITION_IP_REMOTE_PORT, type = FWP_UINT16, value = ProxyPort, mask = 0xffff, name = "block non-proxy ports" },
        new Endpoint { field = FWPM_CONDITION_IP_PROTOCOL, type = FWP_UINT8, value = Tcp, mask = 0xff, name = "block non-TCP traffic" },
    };
    const int Ipv6Filter = 3;

    // The fwpmtypes.h structures, as far as these filters use them.
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct Display {
        public string name, description;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct Blob {
        public uint size;
        public IntPtr data;
    }

    // FWP_VALUE0 / FWP_CONDITION_VALUE0: a type tag and a union.
    [StructLayout(LayoutKind.Explicit, Size = 16)]
    struct Value {
        [FieldOffset(0)] public uint type;
        [FieldOffset(8)] public ulong number;
        [FieldOffset(8)] public IntPtr pointer;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct Condition {
        public Guid field;
        public uint match;
        public Value value;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct Action {
        public uint type;
        public Guid key;
    }

    [StructLayout(LayoutKind.Explicit, Size = 16)]
    struct Context {
        [FieldOffset(0)] public ulong raw;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct Filter {
        public Guid key;
        public Display display;
        public uint flags;
        public IntPtr provider;
        public Blob providerData;
        public Guid layer, subLayer;
        public Value weight;
        public uint count;
        public IntPtr conditions;
        public Action action;
        public Context context;
        public IntPtr reserved;
        public ulong id;
        public Value effectiveWeight;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct SubLayer {
        public Guid key;
        public Display display;
        public uint flags;
        public IntPtr provider;
        public Blob data;
        public ushort weight;
    }

    [DllImport("fwpuclnt.dll", CharSet = CharSet.Unicode)]
    static extern uint FwpmEngineOpen0(string server, uint auth, IntPtr identity, IntPtr session, out IntPtr engine);
    [DllImport("fwpuclnt.dll")]
    static extern uint FwpmEngineClose0(IntPtr engine);
    [DllImport("fwpuclnt.dll")]
    static extern uint FwpmTransactionBegin0(IntPtr engine, uint flags);
    [DllImport("fwpuclnt.dll")]
    static extern uint FwpmTransactionCommit0(IntPtr engine);
    [DllImport("fwpuclnt.dll")]
    static extern uint FwpmTransactionAbort0(IntPtr engine);
    [DllImport("fwpuclnt.dll")]
    static extern uint FwpmSubLayerAdd0(IntPtr engine, ref SubLayer layer, IntPtr security);
    [DllImport("fwpuclnt.dll")]
    static extern uint FwpmSubLayerDeleteByKey0(IntPtr engine, ref Guid key);
    [DllImport("fwpuclnt.dll")]
    static extern uint FwpmFilterAdd0(IntPtr engine, ref Filter filter, IntPtr security, out ulong id);
    [DllImport("fwpuclnt.dll")]
    static extern uint FwpmFilterDeleteByKey0(IntPtr engine, ref Guid key);
    [DllImport("fwpuclnt.dll")]
    static extern uint FwpmFilterGetByKey0(IntPtr engine, ref Guid key, out IntPtr filter);
    [DllImport("fwpuclnt.dll")]
    static extern void FwpmFreeMemory0(ref IntPtr memory);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool ConvertStringSecurityDescriptorToSecurityDescriptor(string text, uint revision, out IntPtr descriptor, out uint size);
    [DllImport("kernel32.dll")]
    static extern IntPtr LocalFree(IntPtr memory);

    static void Check(uint error, string step) {
        if (error != 0) throw new Exception(step + ": 0x" + error.ToString("x8"));
    }

    // Like Check, but also accepts the given "nothing to do" error.
    static void CheckExcept(uint error, uint ignored, string step) {
        if (error != ignored) Check(error, step);
    }

    static IntPtr Open() {
        IntPtr engine;
        Check(FwpmEngineOpen0(null, RPC_C_AUTHN_WINNT, IntPtr.Zero, IntPtr.Zero, out engine), "Open WFP");
        return engine;
    }

    static void Add(IntPtr engine, int index, Guid layer, Condition[] conditions, string name) {
        int size = Marshal.SizeOf(typeof(Condition));
        IntPtr values = Marshal.AllocHGlobal(size * conditions.Length);
        try {
            for (int i = 0; i < conditions.Length; i++) {
                Marshal.StructureToPtr(conditions[i], IntPtr.Add(values, size * i), false);
            }
            var filter = new Filter {
                key = FilterKeys[index],
                display = new Display { name = "code-server: " + name },
                flags = FWPM_FLAG_PERSISTENT,
                layer = layer,
                subLayer = SubLayerKey,
                weight = new Value { type = FWP_UINT8, number = 15 },
                count = (uint)conditions.Length,
                conditions = values,
                action = new Action { type = FWP_ACTION_BLOCK },
            };
            // SYSTEM and Administrators may do anything; the current user may only read.
            string user = WindowsIdentity.GetCurrent().User.Value;
            string sddl = "D:(A;;GA;;;SY)(A;;GA;;;BA)(A;;GR;;;" + user + ")";
            IntPtr descriptor;
            uint descriptorSize;
            if (!ConvertStringSecurityDescriptorToSecurityDescriptor(sddl, 1, out descriptor, out descriptorSize)) {
                throw new Win32Exception();
            }
            try {
                ulong id;
                Check(FwpmFilterAdd0(engine, ref filter, descriptor, out id), "Add " + name);
            } finally {
                LocalFree(descriptor);
            }
        } finally {
            Marshal.FreeHGlobal(values);
        }
    }

    // Adds or replaces the four filters in one transaction.
    public static void Install(string sidString) {
        var sid = new SecurityIdentifier(sidString);
        byte[] bytes = new byte[sid.BinaryLength];
        sid.GetBinaryForm(bytes, 0);
        IntPtr sidPointer = Marshal.AllocHGlobal(bytes.Length);
        Marshal.Copy(bytes, 0, sidPointer, bytes.Length);

        IntPtr engine = Open();
        bool inTransaction = false;
        try {
            Check(FwpmTransactionBegin0(engine, 0), "Begin transaction");
            inTransaction = true;

            var subLayer = new SubLayer {
                key = SubLayerKey,
                display = new Display { name = "code-server dedicated AppContainer" },
                flags = FWPM_FLAG_PERSISTENT,
                weight = 65535,
            };
            CheckExcept(FwpmSubLayerAdd0(engine, ref subLayer, IntPtr.Zero), FWP_E_ALREADY_EXISTS, "Add sublayer");

            for (int i = 0; i < FilterKeys.Length; i++) {
                Guid key = FilterKeys[i];
                CheckExcept(FwpmFilterDeleteByKey0(engine, ref key), FWP_E_FILTER_NOT_FOUND, "Replace filter");
            }

            var package = new Condition {
                field = FWPM_CONDITION_ALE_PACKAGE_ID,
                match = FWP_MATCH_EQUAL,
                value = new Value { type = FWP_SID, pointer = sidPointer },
            };
            for (int i = 0; i < Endpoints.Length; i++) {
                var endpoint = new Condition {
                    field = Endpoints[i].field,
                    match = FWP_MATCH_NOT_EQUAL,
                    value = new Value { type = Endpoints[i].type, number = Endpoints[i].value },
                };
                Add(engine, i, FWPM_LAYER_ALE_AUTH_CONNECT_V4, new[] { package, endpoint }, Endpoints[i].name);
            }
            Add(engine, Ipv6Filter, FWPM_LAYER_ALE_AUTH_CONNECT_V6, new[] { package }, "block IPv6");

            Check(FwpmTransactionCommit0(engine), "Commit transaction");
            inTransaction = false;
        } finally {
            if (inTransaction) FwpmTransactionAbort0(engine);
            FwpmEngineClose0(engine);
            Marshal.FreeHGlobal(sidPointer);
        }
    }

    // Throws unless all four filters exist exactly as Install adds them.
    public static string Verify(string expectedSid) {
        IntPtr engine = Open();
        try {
            for (int i = 0; i < FilterKeys.Length; i++) {
                Guid key = FilterKeys[i];
                IntPtr data;
                Check(FwpmFilterGetByKey0(engine, ref key, out data), "Find filter " + i);
                try {
                    VerifyFilter(i, (Filter)Marshal.PtrToStructure(data, typeof(Filter)), expectedSid);
                } finally {
                    FwpmFreeMemory0(ref data);
                }
            }
            return "four persistent block filters verified for " + expectedSid;
        } finally {
            FwpmEngineClose0(engine);
        }
    }

    static void VerifyFilter(int index, Filter filter, string expectedSid) {
        bool ipv6 = index == Ipv6Filter;
        bool enabledAndPersistent = (filter.flags & (FWPM_FLAG_PERSISTENT | FWPM_FILTER_FLAG_DISABLED)) == FWPM_FLAG_PERSISTENT;
        if (filter.action.type != FWP_ACTION_BLOCK
                || !enabledAndPersistent
                || filter.subLayer != SubLayerKey
                || filter.layer != (ipv6 ? FWPM_LAYER_ALE_AUTH_CONNECT_V6 : FWPM_LAYER_ALE_AUTH_CONNECT_V4)
                || filter.count != (ipv6 ? 1 : 2)) {
            throw new Exception("Unexpected filter configuration");
        }

        var package = (Condition)Marshal.PtrToStructure(filter.conditions, typeof(Condition));
        if (package.field != FWPM_CONDITION_ALE_PACKAGE_ID
                || package.match != FWP_MATCH_EQUAL
                || package.value.type != FWP_SID
                || new SecurityIdentifier(package.value.pointer).Value != expectedSid) {
            throw new Exception("Wrong package condition");
        }
        if (ipv6) return;

        // The value union is read as 64 bits; only the low bits of the type count.
        Endpoint expected = Endpoints[index];
        IntPtr second = IntPtr.Add(filter.conditions, Marshal.SizeOf(typeof(Condition)));
        var endpoint = (Condition)Marshal.PtrToStructure(second, typeof(Condition));
        if (endpoint.field != expected.field
                || endpoint.match != FWP_MATCH_NOT_EQUAL
                || endpoint.value.type != expected.type
                || (endpoint.value.number & expected.mask) != expected.value) {
            throw new Exception("Wrong endpoint condition");
        }
    }

    // Deletes the filters, then the sublayer. Missing ones are not an error.
    public static void Remove() {
        IntPtr engine = Open();
        try {
            for (int i = 0; i < FilterKeys.Length; i++) {
                Guid key = FilterKeys[i];
                CheckExcept(FwpmFilterDeleteByKey0(engine, ref key), FWP_E_FILTER_NOT_FOUND, "Delete filter");
            }
            Guid subLayerKey = SubLayerKey;
            CheckExcept(FwpmSubLayerDeleteByKey0(engine, ref subLayerKey), FWP_E_SUBLAYER_NOT_FOUND, "Delete sublayer");
        } finally {
            FwpmEngineClose0(engine);
        }
    }
}
