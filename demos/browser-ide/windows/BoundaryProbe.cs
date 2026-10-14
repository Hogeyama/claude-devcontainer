using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Diagnostics;
using System.Web.Script.Serialization;
class BoundaryProbe {
    static string Tcp(string host,int port) {
        try { using(var client=new TcpClient(IPAddress.Parse(host).AddressFamily)) { var task=client.ConnectAsync(IPAddress.Parse(host),port); if(!task.Wait(1500)) return "timeout"; return client.Connected?"connected":"disconnected"; } }
        catch(Exception e) { return e.GetBaseException().GetType().Name+": "+e.GetBaseException().Message; }
    }
    static string Udp(string host,int port) {
        try { var ip=IPAddress.Parse(host); using(var client=new UdpClient(ip.AddressFamily)) {byte[] b=System.Text.Encoding.UTF8.GetBytes("DEMO_ONLY"); client.Send(b,b.Length,new IPEndPoint(ip,port)); return "sent";} }
        catch(Exception e) { return e.GetBaseException().GetType().Name+": "+e.GetBaseException().Message; }
    }
    static void Main(string[] args) {
        var result=new Dictionary<string,object>();
        result["proxy"]=Tcp("127.0.0.1",18081);
        result["other_loopback"]=Tcp("127.0.0.1",18082);
        result["ipv6_loopback"]=Tcp("::1",18084);
        result["udp_loopback"]=Udp("127.0.0.1",18083);
        result["udp_ipv6"]=Udp("::1",18085);
        try {result["host_sentinel"]=File.ReadAllText(args[1]);} catch(Exception e) {result["host_sentinel"]=e.GetType().Name;}
        try { File.WriteAllText(args[1]+".written","DEMO_ONLY"); result["host_write"]="written";} catch(Exception e) {result["host_write"]=e.GetType().Name;}
        try { Process.Start(new ProcessStartInfo("browseridedemo:probe") {UseShellExecute=true}); result["external_protocol"]="ShellExecute returned"; } catch(Exception e) {result["external_protocol"]=e.GetType().Name;}
        File.WriteAllText(args[0],new JavaScriptSerializer().Serialize(result));
    }
}
