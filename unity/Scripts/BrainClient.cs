// Connects the Unity sim to the brain over TCP (see unity/README.md for the protocol).
// Put one in the scene. Trucks and sensor cameras register themselves.
//
// Brain address: command line `-brain-host H -brain-port P`, else env BRAIN_HOST /
// BRAIN_PORT, else the inspector values.

using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using UnityEngine;

public class BrainClient : MonoBehaviour
{
    public string host = "127.0.0.1";
    public int port = 9000;
    [Tooltip("Quarry extent in world metres: xmin, zmin, xmax, zmax. Used by the website map.")]
    public Vector4 bounds = new Vector4(-100, -100, 100, 100);

    public static BrainClient Instance { get; private set; }

    static readonly List<string> trucks = new List<string>();
    static readonly List<string> cameras = new List<string>();

    readonly BlockingCollection<byte[]> outbox = new BlockingCollection<byte[]>(64);
    readonly ConcurrentDictionary<string, ActionMsg> actions = new ConcurrentDictionary<string, ActionMsg>();
    Thread worker;
    volatile bool running;
    volatile bool connected;

    public bool Connected => connected;

    public static void RegisterTruck(string id) { lock (trucks) if (!trucks.Contains(id)) trucks.Add(id); }
    public static void RegisterCamera(string name) { lock (cameras) if (!cameras.Contains(name)) cameras.Add(name); }

    [Serializable] public class ActionMsg { public string type; public string truck; public float throttle; public float steer; [NonSerialized] public float receivedAt; }
    [Serializable] class HelloMsg { public string type = "hello"; public string[] trucks; public string[] cameras; public float[] bounds; }

    void Awake()
    {
        Instance = this;
        host = Arg("-brain-host") ?? Environment.GetEnvironmentVariable("BRAIN_HOST") ?? host;
        var p = Arg("-brain-port") ?? Environment.GetEnvironmentVariable("BRAIN_PORT");
        if (p != null) port = int.Parse(p);
    }

    void OnEnable()
    {
        running = true;
        worker = new Thread(Run) { IsBackground = true, Name = "BrainClient" };
        worker.Start();
    }

    void OnDisable()
    {
        running = false;
        worker?.Join(1000);
    }

    /// Queue a fully encoded message. Drops it if the link is down or backed up.
    public void Send(byte[] message)
    {
        if (connected) outbox.TryAdd(message);
    }

    /// Latest action for a truck, or null if none arrived within maxAge seconds.
    public ActionMsg LatestAction(string truckId, float maxAge)
    {
        if (actions.TryGetValue(truckId, out var a) && Now() - a.receivedAt <= maxAge) return a;
        return null;
    }

    void Run()
    {
        while (running)
        {
            try
            {
                using (var client = new TcpClient { NoDelay = true })
                {
                    client.Connect(host, port);
                    var stream = client.GetStream();
                    Debug.Log($"[BrainClient] connected to {host}:{port}");
                    while (outbox.TryTake(out _)) { }
                    var hello = Hello();
                    stream.Write(hello, 0, hello.Length);
                    connected = true;

                    var reader = new Thread(() => Receive(stream)) { IsBackground = true, Name = "BrainClient.rx" };
                    reader.Start();
                    while (running && reader.IsAlive)
                    {
                        if (outbox.TryTake(out var msg, 100)) stream.Write(msg, 0, msg.Length);
                    }
                }
            }
            catch (Exception e) when (e is SocketException || e is System.IO.IOException || e is ObjectDisposedException)
            {
                if (connected) Debug.LogWarning($"[BrainClient] disconnected: {e.Message}");
            }
            connected = false;
            if (running) Thread.Sleep(2000);
        }
    }

    void Receive(NetworkStream stream)
    {
        try
        {
            var prefix = new byte[8];
            while (running)
            {
                ReadExactly(stream, prefix, 8);
                int hlen = BitConverter.ToInt32(prefix, 0), plen = BitConverter.ToInt32(prefix, 4);
                var header = new byte[hlen];
                ReadExactly(stream, header, hlen);
                if (plen > 0) ReadExactly(stream, new byte[plen], plen);
                var json = Encoding.UTF8.GetString(header);
                if (!json.Contains("\"action\"")) continue;
                var a = JsonUtility.FromJson<ActionMsg>(json);
                a.receivedAt = Now();
                actions[a.truck] = a;
            }
        }
        catch (Exception) { /* connection dropped; Run() reconnects */ }
    }

    // Unity's Time API is main-thread only, so both threads share this clock.
    static readonly System.Diagnostics.Stopwatch clock = System.Diagnostics.Stopwatch.StartNew();
    static float Now() => (float)clock.Elapsed.TotalSeconds;

    byte[] Hello()
    {
        var msg = new HelloMsg { bounds = new[] { bounds.x, bounds.y, bounds.z, bounds.w } };
        lock (trucks) msg.trucks = trucks.ToArray();
        lock (cameras) msg.cameras = cameras.ToArray();
        return BrainProtocol.Encode(JsonUtility.ToJson(msg), null);
    }

    static void ReadExactly(NetworkStream s, byte[] buf, int n)
    {
        int off = 0;
        while (off < n)
        {
            int r = s.Read(buf, off, n - off);
            if (r <= 0) throw new System.IO.IOException("stream closed");
            off += r;
        }
    }

    static string Arg(string name)
    {
        var args = Environment.GetCommandLineArgs();
        for (int i = 0; i < args.Length - 1; i++) if (args[i] == name) return args[i + 1];
        return null;
    }
}
